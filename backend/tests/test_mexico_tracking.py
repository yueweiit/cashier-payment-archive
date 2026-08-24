from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from backend.app.mexico_tracking import (
    backfill_request_regions,
    build_bilingual_reminder,
    get_mexico_tracking_settings,
    node_age_days,
    persist_request_region,
    resolve_region,
    update_mexico_tracking_settings,
    warning_level,
)
from backend.app.payable_history import record_request_state


@pytest.fixture()
def isolated_db(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    import backend.app.db as db_module

    original_paths = (
        db_module.DATA_DIR,
        db_module.DB_PATH,
        db_module.ATTACHMENT_STORAGE_DIR,
    )
    monkeypatch.setattr(db_module, "DATA_DIR", tmp_path / "app-data")
    monkeypatch.setattr(db_module, "DB_PATH", tmp_path / "app-data" / "app.db")
    monkeypatch.setattr(db_module, "ATTACHMENT_STORAGE_DIR", tmp_path / "attachment-data")
    db_module.init_db()
    try:
        yield db_module
    finally:
        (
            db_module.DATA_DIR,
            db_module.DB_PATH,
            db_module.ATTACHMENT_STORAGE_DIR,
        ) = original_paths


@pytest.mark.parametrize(
    ("source_sheet", "expected_region"),
    [
        ("悦为智能 YW Tech_Ai", "china"),
        ("拉丁购", "china"),
        ("凌翔产品&开发", "china"),
        ("凌翔供应链及采购执行单元", "china"),
        ("星铭HR人力资源中心", "china"),
        ("星铭FC财务中心", "china"),
        ("凌翔/星铭供应链及职能中心", "china"),
        ("YW MOLDES MX模具", "mexico"),
        ("YUEWEI MX核心制造", "mexico"),
        ("LEMOS MX供应链开发及管理", "mexico"),
        ("LEMOS MX 销售", "mexico"),
        ("UV IMPRESION MX彩印", "mexico"),
        ("FC 财务中心 Centro Financiero (FC)", "mexico"),
    ],
)
def test_region_falls_back_to_exact_confirmed_sheet_mapping(
    source_sheet: str, expected_region: str
) -> None:
    decision = resolve_region(source_sheet=source_sheet)

    assert decision.region == expected_region
    assert decision.source == "sheet_mapping"


def test_explicit_execution_region_takes_priority_without_conflict() -> None:
    decision = resolve_region(
        execution_region="墨西哥 Mexico",
        source_sheet="未登记的新公司",
    )

    assert decision.region == "mexico"
    assert decision.source == "execution_region"
    assert decision.execution_region_raw == "墨西哥 Mexico"


def test_execution_region_and_sheet_conflict_requires_review() -> None:
    decision = resolve_region(
        execution_region="中国 China",
        source_sheet="YW MOLDES MX模具",
    )

    assert decision.region == "review"
    assert decision.source == "conflict"
    assert decision.sheet_region == "mexico"
    assert decision.conflict_reason


def test_admin_resolution_is_kept_when_new_raw_fact_conflicts() -> None:
    decision = resolve_region(
        execution_region="Mexico",
        source_sheet="YW MOLDES MX模具",
        admin_region="china",
    )

    assert decision.region == "china"
    assert decision.source == "admin_override"
    assert decision.conflict_reason
    assert "Mexico" in decision.conflict_reason


def test_currency_alone_never_decides_region() -> None:
    decision = resolve_region(currency="MXN", source_sheet="新公司")

    assert decision.region == "review"
    assert decision.source == "unknown"


def test_similar_finance_sheet_names_are_not_confused() -> None:
    assert resolve_region(source_sheet="星铭FC财务中心").region == "china"
    assert (
        resolve_region(source_sheet="FC 财务中心 Centro Financiero (FC)").region
        == "mexico"
    )
    assert resolve_region(source_sheet="FC财务中心").region == "review"


def test_node_age_uses_shanghai_calendar_days() -> None:
    shanghai = ZoneInfo("Asia/Shanghai")

    assert node_age_days(
        datetime(2026, 8, 24, 0, 1, tzinfo=shanghai),
        now=datetime(2026, 8, 24, 23, 59, tzinfo=shanghai),
    ) == 0
    assert node_age_days(
        datetime(2026, 8, 21, 23, 59, tzinfo=shanghai),
        now=datetime(2026, 8, 24, 0, 1, tzinfo=shanghai),
    ) == 3


@pytest.mark.parametrize(
    ("age", "expected"),
    [
        (0, "normal"),
        (2, "normal"),
        (3, "yellow"),
        (5, "yellow"),
        (6, "red"),
    ],
)
def test_warning_thresholds_are_strictly_greater_than_config(age: int, expected: str) -> None:
    assert warning_level(age, yellow_days=2, red_days=5) == expected


def test_warning_thresholds_must_be_valid() -> None:
    with pytest.raises(ValueError):
        warning_level(3, yellow_days=5, red_days=2)


def test_bilingual_reminder_contains_operational_context() -> None:
    reminder = build_bilingual_reminder(
        approval_no="2026082412340001",
        applicant="María López",
        current_node="CEO 审批",
        current_approver="Eduardo Gómez",
        age_days=4,
        workflow_url="https://oa.dingtalk.com/example",
    )

    for value in (
        "2026082412340001",
        "María López",
        "CEO 审批",
        "Eduardo Gómez",
        "4",
        "https://oa.dingtalk.com/example",
    ):
        assert value in reminder["zh"]
        assert value in reminder["es"]
    assert "请协助" in reminder["zh"]
    assert "favor" in reminder["es"].lower()


def _columns(conn, table: str) -> set[str]:
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _indexes(conn, table: str) -> set[str]:
    return {row["name"] for row in conn.execute(f"PRAGMA index_list({table})").fetchall()}


def test_mexico_tracking_schema_and_migration_are_idempotent(isolated_db) -> None:
    isolated_db.init_db()

    with isolated_db.connect() as conn:
        tables = {
            row["name"]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        assert {
            "mexico_approval_tracking",
            "mexico_approval_events",
            "mexico_approval_request_links",
            "mexico_approval_attachments",
            "mexico_sync_runs",
        } <= tables

        assert {
            "approval_no",
            "resolved_region",
            "region_resolution_source",
            "region_review_status",
            "workflow_status",
            "current_node_name",
            "current_approver_id",
            "current_node_entered_at",
            "last_synced_at",
            "version",
        } <= _columns(conn, "mexico_approval_tracking")
        assert {
            "resolved_region",
            "region_resolution_source",
            "region_review_status",
            "region_reviewed_by",
            "region_reviewed_at",
        } <= _columns(conn, "payment_requests")
        assert {"resolved_region", "region_review_status"} <= _columns(
            conn, "payable_history_versions"
        )


def test_mexico_tracking_schema_has_required_unique_constraints_and_indexes(isolated_db) -> None:
    with isolated_db.connect() as conn:
        tracking_indexes = _indexes(conn, "mexico_approval_tracking")
        assert {
            "idx_mexico_tracking_region_status",
            "idx_mexico_tracking_sheet",
            "idx_mexico_tracking_applicant",
            "idx_mexico_tracking_approver",
            "idx_mexico_tracking_node",
            "idx_mexico_tracking_request_date",
            "idx_mexico_tracking_last_synced",
        } <= tracking_indexes
        assert "idx_mexico_events_approval_time" in _indexes(conn, "mexico_approval_events")

        unique_tracking = {
            row["name"]
            for row in conn.execute("PRAGMA index_list(mexico_approval_tracking)")
            if row["unique"]
        }
        unique_events = {
            row["name"]
            for row in conn.execute("PRAGMA index_list(mexico_approval_events)")
            if row["unique"]
        }
        unique_links = {
            row["name"]
            for row in conn.execute("PRAGMA index_list(mexico_approval_request_links)")
            if row["unique"]
        }
        unique_attachments = {
            row["name"]
            for row in conn.execute("PRAGMA index_list(mexico_approval_attachments)")
            if row["unique"]
        }
        assert unique_tracking
        assert unique_events
        assert unique_links
        assert unique_attachments

        attachment_sql = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'mexico_approval_attachments'"
        ).fetchone()["sql"]
        for status in ("pending", "downloading", "ready", "failed"):
            assert status in attachment_sql


def test_mexico_tracking_settings_have_safe_defaults_and_validation(isolated_db) -> None:
    with isolated_db.connect() as conn:
        assert get_mexico_tracking_settings(conn) == {
            "yellow_days": 2,
            "red_days": 5,
            "cache_stale_seconds": 300,
            "china_region_isolation_enabled": False,
        }

        updated = update_mexico_tracking_settings(
            conn,
            yellow_days=4,
            red_days=9,
            cache_stale_seconds=600,
            china_region_isolation_enabled=True,
        )
        assert updated == {
            "yellow_days": 4,
            "red_days": 9,
            "cache_stale_seconds": 600,
            "china_region_isolation_enabled": True,
        }
        conn.commit()

        with pytest.raises(ValueError):
            update_mexico_tracking_settings(conn, yellow_days=10, red_days=5)
        with pytest.raises(ValueError):
            update_mexico_tracking_settings(conn, yellow_days=-1, red_days=5)
        with pytest.raises(ValueError):
            update_mexico_tracking_settings(conn, yellow_days=2, red_days=366)
        with pytest.raises(ValueError):
            update_mexico_tracking_settings(conn, cache_stale_seconds=-1)
        with pytest.raises(ValueError):
            update_mexico_tracking_settings(conn, china_region_isolation_enabled="yes")


def _insert_region_request(
    conn,
    *,
    source_sheet: str,
    execution_region: str | None = None,
    resolved_region: str = "review",
    resolution_source: str = "unknown",
    review_status: str = "pending",
) -> int:
    timestamp = "2026-08-24T10:00:00.000000+08:00"
    user_id = int(conn.execute("SELECT id FROM users WHERE username = 'admin'").fetchone()["id"])
    batch = conn.execute(
        """
        INSERT INTO request_batches (name, status, created_by, created_at, updated_at)
        VALUES ('地区测试批次', 'draft', ?, ?, ?)
        """,
        (user_id, timestamp, timestamp),
    )
    raw_extra = {
        "external_source": {
            "execution_region": execution_region,
            "approval_status": "RUNNING",
        }
    }
    request = conn.execute(
        """
        INSERT INTO payment_requests (
            batch_id, dingding_id, amount, paid_amount, pending_amount, currency,
            source_sheet, raw_extra_json, resolved_region, region_resolution_source,
            region_review_status, created_by, updated_by, created_at, updated_at
        ) VALUES (?, ?, 100, 0, 100, 'CNY', ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            int(batch.lastrowid),
            f"region-{batch.lastrowid}",
            source_sheet,
            json.dumps(raw_extra, ensure_ascii=False),
            resolved_region,
            resolution_source,
            review_status,
            user_id,
            user_id,
            timestamp,
            timestamp,
        ),
    )
    return int(request.lastrowid)


def test_region_persist_prefers_external_execution_region_and_falls_back_to_sheet(
    isolated_db,
) -> None:
    with isolated_db.connect() as conn:
        explicit_id = _insert_region_request(
            conn,
            source_sheet="未登记的新公司",
            execution_region="墨西哥 Mexico",
        )
        fallback_id = _insert_region_request(
            conn,
            source_sheet="星铭FC财务中心",
        )

        explicit = persist_request_region(conn, explicit_id, actor_id=None)
        fallback = persist_request_region(conn, fallback_id, actor_id=None)

        assert (explicit.region, explicit.source) == ("mexico", "execution_region")
        assert (fallback.region, fallback.source) == ("china", "sheet_mapping")
        stored = conn.execute(
            "SELECT resolved_region, region_resolution_source, region_review_status "
            "FROM payment_requests WHERE id = ?",
            (explicit_id,),
        ).fetchone()
        assert tuple(stored) == ("mexico", "execution_region", "resolved")


def test_region_persist_marks_conflict_for_review(isolated_db) -> None:
    with isolated_db.connect() as conn:
        request_id = _insert_region_request(
            conn,
            source_sheet="YW MOLDES MX模具",
            execution_region="中国 China",
        )

        decision = persist_request_region(conn, request_id, actor_id=None)

        assert decision.region == "review"
        stored = conn.execute(
            "SELECT resolved_region, region_resolution_source, region_review_status "
            "FROM payment_requests WHERE id = ?",
            (request_id,),
        ).fetchone()
        assert tuple(stored) == ("review", "conflict", "pending")


def test_region_persist_preserves_admin_override(isolated_db) -> None:
    with isolated_db.connect() as conn:
        request_id = _insert_region_request(
            conn,
            source_sheet="YW MOLDES MX模具",
            execution_region="Mexico",
            resolved_region="china",
            resolution_source="admin_override",
            review_status="resolved",
        )

        decision = persist_request_region(conn, request_id, actor_id=1)

        assert (decision.region, decision.source) == ("china", "admin_override")
        stored = conn.execute(
            "SELECT resolved_region, region_resolution_source, region_review_status "
            "FROM payment_requests WHERE id = ?",
            (request_id,),
        ).fetchone()
        assert tuple(stored) == ("china", "admin_override", "resolved")


def test_region_backfill_counts_and_history_snapshot(isolated_db) -> None:
    with isolated_db.connect() as conn:
        china_id = _insert_region_request(conn, source_sheet="拉丁购")
        mexico_id = _insert_region_request(conn, source_sheet="YUEWEI MX核心制造")
        review_id = _insert_region_request(conn, source_sheet="未登记 Sheet")

        counts = backfill_request_regions(conn)

        assert counts == {
            "china": 1,
            "mexico": 1,
            "review": 1,
            "preserved_override": 0,
        }
        assert record_request_state(
            conn,
            mexico_id,
            event_type="request.update",
            event_key="region-history-test",
        )
        history = conn.execute(
            "SELECT resolved_region, region_review_status FROM payable_history_versions "
            "WHERE event_key = 'region-history-test'"
        ).fetchone()
        assert tuple(history) == ("mexico", "resolved")
        assert china_id != review_id


def test_request_write_helpers_persist_region_and_history(isolated_db) -> None:
    from backend.app.main import insert_request, update_request_row

    with isolated_db.connect() as conn:
        user_id = int(
            conn.execute("SELECT id FROM users WHERE username = 'admin'").fetchone()["id"]
        )
        timestamp = "2026-08-24T10:00:00.000000+08:00"
        batch_id = int(
            conn.execute(
                """
                INSERT INTO request_batches
                    (name, status, created_by, created_at, updated_at)
                VALUES ('写入路径地区测试', 'draft', ?, ?, ?)
                """,
                (user_id, timestamp, timestamp),
            ).lastrowid
        )

        request_id = insert_request(
            conn,
            batch_id,
            {
                "dingding_id": "region-write-path-1",
                "source_sheet": "星铭FC财务中心",
                "amount": 100,
                "currency": "CNY",
            },
            user_id,
        )
        created = conn.execute(
            "SELECT * FROM payment_requests WHERE id = ?",
            (request_id,),
        ).fetchone()
        assert (
            created["resolved_region"],
            created["region_resolution_source"],
            created["region_review_status"],
        ) == ("china", "sheet_mapping", "resolved")

        assert update_request_row(
            conn,
            request_id,
            {"source_sheet": "YW MOLDES MX模具"},
            user_id,
            expected_version=int(created["version"]),
        )
        updated = conn.execute(
            "SELECT * FROM payment_requests WHERE id = ?",
            (request_id,),
        ).fetchone()
        assert (
            updated["resolved_region"],
            updated["region_resolution_source"],
            updated["region_review_status"],
        ) == ("mexico", "sheet_mapping", "resolved")
        history = conn.execute(
            """
            SELECT resolved_region, region_review_status
            FROM payable_history_versions
            WHERE source_request_id = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (request_id,),
        ).fetchone()
        assert tuple(history) == ("mexico", "resolved")
