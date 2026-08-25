from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from backend.app.external_expenses import (
    discover_expense_workflows,
    parse_dingtalk_workflow_instance,
)
from backend.app.mexico_tracking import (
    acquire_or_reuse_mexico_sync_run,
    cache_mexico_discovery_candidates,
    cache_mexico_workflow_snapshots,
    backfill_request_regions,
    build_bilingual_reminder,
    collect_mexico_attachment_candidates,
    complete_mexico_sync_run,
    fail_mexico_sync_run,
    get_mexico_sync_run,
    get_mexico_tracking_detail,
    get_mexico_tracking_settings,
    list_mexico_tracking,
    mexico_tracking_filter_options,
    resolve_mexico_tracking_region,
    summarize_mexico_tracking,
    list_mexico_attachment_download_candidates,
    mark_mexico_attachment_failed,
    mark_mexico_attachment_ready,
    mark_mexico_attachments_downloading,
    node_age_days,
    persist_request_region,
    resolve_region,
    upsert_mexico_attachment_candidates,
    update_mexico_sync_run,
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


def test_explicit_execution_region_overrides_conflicting_sheet_mapping() -> None:
    mexico = resolve_region(
        execution_region="Mexico",
        source_sheet="悦为智能 YW Tech_Ai",
    )
    china = resolve_region(
        execution_region="中国 China",
        source_sheet="YW MOLDES MX模具",
    )

    assert (mexico.region, mexico.source) == ("mexico", "execution_region")
    assert mexico.sheet_region == "china"
    assert (china.region, china.source) == ("china", "execution_region")
    assert china.sheet_region == "mexico"


def test_explicit_execution_region_supersedes_stale_admin_override() -> None:
    decision = resolve_region(
        execution_region="Mexico",
        source_sheet="悦为智能 YW Tech_Ai",
        admin_region="china",
    )

    assert (decision.region, decision.source) == ("mexico", "execution_region")


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
            "mexico_approval_current_tasks",
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
        assert {
            "idx_mexico_current_tasks_approver",
            "idx_mexico_current_tasks_node",
        } <= _indexes(conn, "mexico_approval_current_tasks")

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
        unique_current_tasks = {
            row["name"]
            for row in conn.execute("PRAGMA index_list(mexico_approval_current_tasks)")
            if row["unique"]
        }
        assert unique_tracking
        assert unique_events
        assert unique_links
        assert unique_attachments
        assert unique_current_tasks

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
            "china_region_isolation_enabled": True,
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
        assert update_mexico_tracking_settings(
            conn,
            china_region_isolation_enabled=False,
        )["china_region_isolation_enabled"] is True
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


def test_region_persist_uses_explicit_execution_region_over_sheet(isolated_db) -> None:
    with isolated_db.connect() as conn:
        request_id = _insert_region_request(
            conn,
            source_sheet="悦为智能 YW Tech_Ai",
            execution_region="Mexico",
        )

        decision = persist_request_region(conn, request_id, actor_id=None)

        assert decision.region == "mexico"
        stored = conn.execute(
            "SELECT resolved_region, region_resolution_source, region_review_status "
            "FROM payment_requests WHERE id = ?",
            (request_id,),
        ).fetchone()
        assert tuple(stored) == ("mexico", "execution_region", "resolved")


def test_region_persist_replaces_stale_admin_override_with_explicit_region(isolated_db) -> None:
    with isolated_db.connect() as conn:
        request_id = _insert_region_request(
            conn,
            source_sheet="悦为智能 YW Tech_Ai",
            execution_region="Mexico",
            resolved_region="china",
            resolution_source="admin_override",
            review_status="resolved",
        )

        decision = persist_request_region(conn, request_id, actor_id=1)

        assert (decision.region, decision.source) == ("mexico", "execution_region")
        stored = conn.execute(
            "SELECT resolved_region, region_resolution_source, region_review_status "
            "FROM payment_requests WHERE id = ?",
            (request_id,),
        ).fetchone()
        assert tuple(stored) == ("mexico", "execution_region", "resolved")


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
            "reclassified": 2,
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


def test_region_v2_backfill_reclassifies_yuewei_mexico_and_appends_history(
    isolated_db,
) -> None:
    with isolated_db.connect() as conn:
        request_id = _insert_region_request(
            conn,
            source_sheet="悦为智能 YW Tech_Ai",
            execution_region="Mexico",
            resolved_region="review",
            resolution_source="conflict",
            review_status="pending",
        )
        record_request_state(
            conn,
            request_id,
            event_type="baseline",
            event_key=f"baseline:{request_id}",
        )

        result = backfill_request_regions(
            conn,
            append_history=True,
            event_key_prefix="mexico-request-region-v2",
        )

        stored = conn.execute(
            "SELECT resolved_region, region_review_status "
            "FROM payment_requests WHERE id = ?",
            (request_id,),
        ).fetchone()
        history = conn.execute(
            "SELECT event_type, resolved_region FROM payable_history_versions "
            "WHERE source_request_id = ? ORDER BY id",
            (request_id,),
        ).fetchall()
        assert tuple(stored) == ("mexico", "resolved")
        assert [tuple(row) for row in history][-1] == (
            "request.region_reclassified",
            "mexico",
        )
        assert result["reclassified"] == 1


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


class FakeDiscoveryGateway:
    def __init__(self, rows_by_source):
        self.rows_by_source = rows_by_source
        self.calls = []

    def fetch_source_changes(self, source_type, cursor, running_approval_nos):
        self.calls.append((source_type, cursor, tuple(running_approval_nos)))
        return list(self.rows_by_source.get(source_type, []))

    def fetch_user_names(self, user_ids):
        return {
            "mx-user": "María López",
            "cn-user": "张三",
        }


def _standard_discovery_row(
    *,
    source_type: str,
    source_id: str,
    approval_no: str,
    execution_region: str,
    source_updated_at: str,
    amount: float = 100,
    applicant_id: str = "mx-user",
    applicant_department: str = "YUEWEI MX核心制造",
):
    return {
        "source_type": source_type,
        "source_id": source_id,
        "effective_date": "2026-08-24",
        "approval_no": approval_no,
        "creator_name": applicant_id,
        "applicant_department": applicant_department,
        "approval_title": "María López enviado por",
        "approval_status": "RUNNING",
        "approval_result": "",
        "execution_region": execution_region,
        "beneficiary": "Proveedor MX",
        "expense_type": "Gastos",
        "summary": "Compra local",
        "project": "Proyecto MX",
        "needed_payment_date": "2026-08-28",
        "source_currency": "MXN",
        "source_amount": amount,
        "base_currency_amount": amount * 0.4,
        "order_name": None,
        "product_name": None,
        "source_created_at": "2026-08-24T09:00:00+08:00",
        "source_updated_at": source_updated_at,
        "raw_data": {
            "processInstanceId": f"process-{source_type}-{source_id}",
        },
    }


def _monthly_discovery_row(*, source_id: str, approval_no: str, updated_at: str):
    return {
        "source_id": source_id,
        "process_instance_id": f"monthly-process-{source_id}",
        "process_code": "PROC-EE85EDD4-5CF2-4C08-B948-1690A6ACC51C",
        "create_time": "2026-08-24T08:00:00+08:00",
        "updated_at": updated_at,
        "status": "RUNNING",
        "result": "",
        "title": "María López提交的月结付款",
        "raw_payload": {
            "businessId": approval_no,
            "originatorUserId": "mx-user",
            "originatorDeptName": "LEMOS MX供应链开发及管理",
            "formComponentValues": [
                {"name": "执行地区", "value": "墨西哥 Mexico"},
                {"name": "币种", "value": "MXN"},
                {"name": "合计总额", "value": "500"},
                {"name": "申请事由", "value": "Pago mensual"},
                {"name": "收款账户信息", "value": "Proveedor MX"},
            ],
        },
    }


def test_discovery_first_sync_finds_all_three_sources_without_history_cutoff(monkeypatch) -> None:
    monkeypatch.setattr(
        "backend.app.external_expenses.fetch_rates",
        lambda requested_date, currencies: {
            "MXN": {"cny_per_unit": 0.4, "actual_date": requested_date.isoformat()}
        },
    )
    gateway = FakeDiscoveryGateway(
        {
            "operation": [
                _standard_discovery_row(
                    source_type="operation",
                    source_id="11",
                    approval_no=" 202608240001 ",
                    execution_region="Mexico",
                    source_updated_at="2026-08-24T10:00:00+08:00",
                )
            ],
            "purchase": [
                _standard_discovery_row(
                    source_type="purchase",
                    source_id="22",
                    approval_no="202608240002",
                    execution_region="中国 China",
                    source_updated_at="2026-08-24T10:01:00+08:00",
                    applicant_id="cn-user",
                    applicant_department="凌翔产品&开发",
                )
            ],
            "monthly": [
                _monthly_discovery_row(
                    source_id="33",
                    approval_no="202608240003",
                    updated_at="2026-08-24T10:02:00+08:00",
                )
            ],
        }
    )

    result = discover_expense_workflows({}, [], gateway=gateway)

    assert {candidate["source_type"] for candidate in result.candidates} == {
        "operation",
        "purchase",
        "monthly",
    }
    assert {candidate["approval_no"] for candidate in result.candidates} == {
        "202608240001",
        "202608240002",
        "202608240003",
    }
    assert all(call[1] is None for call in gateway.calls)
    monthly = next(row for row in result.candidates if row["source_type"] == "monthly")
    assert monthly["process_instance_id"] == "monthly-process-33"
    assert monthly["applicant_name"] == "María López"
    assert monthly["company_name"] == "LEMOS MX供应链开发及管理"
    assert monthly["raw_execution_region"] == "墨西哥 Mexico"
    assert monthly["amount"] == 500
    assert monthly["currency"] == "MXN"
    assert result.next_cursors["monthly"] == {
        "updated_at": "2026-08-24T10:02:00+08:00",
        "source_id": "33",
    }


def test_discovery_incremental_passes_each_cursor_and_rechecks_running() -> None:
    gateway = FakeDiscoveryGateway({})
    cursors = {
        "operation": {"updated_at": "2026-08-20T10:00:00+08:00", "source_id": "10"},
        "purchase": {"updated_at": "2026-08-21T10:00:00+08:00", "source_id": "20"},
        "monthly": {"updated_at": "2026-08-22T10:00:00+08:00", "source_id": "30"},
    }

    result = discover_expense_workflows(
        cursors,
        [" 202608010001 ", "202608010002", "202608010001"],
        gateway=gateway,
    )

    assert result.next_cursors == cursors
    assert gateway.calls == [
        ("operation", cursors["operation"], ("202608010001", "202608010002")),
        ("purchase", cursors["purchase"], ("202608010001", "202608010002")),
        ("monthly", cursors["monthly"], ("202608010001", "202608010002")),
    ]


def test_discovery_deduplicates_same_source_row_and_surfaces_cross_source_conflict() -> None:
    operation = _standard_discovery_row(
        source_type="operation",
        source_id="11",
        approval_no="202608240099",
        execution_region="Mexico",
        source_updated_at="2026-08-24T10:00:00+08:00",
        amount=100,
    )
    purchase = _standard_discovery_row(
        source_type="purchase",
        source_id="22",
        approval_no="202608240099",
        execution_region="China",
        source_updated_at="2026-08-24T10:01:00+08:00",
        amount=900,
        applicant_id="cn-user",
        applicant_department="凌翔产品&开发",
    )
    gateway = FakeDiscoveryGateway(
        {
            "operation": [operation, dict(operation)],
            "purchase": [purchase],
            "monthly": [],
        }
    )

    result = discover_expense_workflows({}, [], gateway=gateway)

    assert result.source_conflicts == ["202608240099"]
    assert len(result.candidates) == 1
    conflict = result.candidates[0]
    assert conflict["approval_no"] == "202608240099"
    assert conflict["source_type"] == "conflict"
    assert conflict["source_conflict"] is True
    assert conflict["resolved_region"] == "review"
    assert len(conflict["raw_candidates"]) == 2


def _workflow_operation(
    *,
    activity_id: str,
    event_type: str,
    user_id: str,
    event_time: str,
    show_name: str,
    remark: str = "",
    result: str = "",
):
    return {
        "activityId": activity_id,
        "type": event_type,
        "userId": user_id,
        "date": event_time,
        "showName": show_name,
        "remark": remark,
        "result": result,
    }


def test_workflow_snapshot_keeps_stable_distinct_event_keys_when_comments_reorder() -> None:
    operations = [
        _workflow_operation(
            activity_id="finance",
            event_type="ADD_REMARK",
            user_id="finance-user",
            event_time="2026-08-24T01:00:00Z",
            show_name="财务",
            remark="请核对收款账户",
        ),
        _workflow_operation(
            activity_id="finance",
            event_type="ADD_REMARK",
            user_id="finance-user",
            event_time="2026-08-24T01:01:00Z",
            show_name="财务",
            remark="账户已核对",
        ),
    ]
    instance = {
        "approval_no": "202608240099",
        "process_instance_id": "process-99",
        "status": "RUNNING",
        "result": "",
        "title": "María López提交的采购支出",
        "operation_records": operations,
        "tasks": [],
        "updated_at": "2026-08-24T01:02:00Z",
    }

    first = parse_dingtalk_workflow_instance(
        instance,
        {"finance-user": "吴嘉洪"},
    )
    second = parse_dingtalk_workflow_instance(
        {**instance, "operation_records": list(reversed(operations))},
        {"finance-user": "吴嘉洪"},
    )

    first_keys = {event["comment"]: event["event_key"] for event in first["events"]}
    second_keys = {event["comment"]: event["event_key"] for event in second["events"]}
    assert first_keys == second_keys
    assert len(set(first_keys.values())) == 2


def test_workflow_snapshot_keeps_all_current_tasks_and_assignees() -> None:
    instance = {
        "approval_no": "202608240100",
        "process_instance_id": "process-100",
        "status": "RUNNING",
        "result": "",
        "title": "María López提交的采购支出",
        "operation_records": [],
        "tasks": [
            {
                "taskId": "finance-task",
                "activityId": "finance-node",
                "activityName": "财务审批",
                "assigneeUserIds": ["finance-a", "finance-b"],
                "status": "RUNNING",
                "createTime": "2026-08-24T01:00:00Z",
            },
            {
                "taskId": "legal-task",
                "activityId": "legal-node",
                "activityName": "法务会签",
                "userId": "legal-a",
                "status": "PENDING",
                "createTime": "2026-08-24T00:30:00Z",
            },
        ],
        "updated_at": "2026-08-24T01:02:00Z",
    }

    snapshot = parse_dingtalk_workflow_instance(
        instance,
        {"finance-a": "Ana", "finance-b": "Bruno", "legal-a": "Carla"},
    )

    assert [
        (task["node_name"], task["approver_name"])
        for task in snapshot["current_tasks"]
    ] == [
        ("法务会签", "Carla"),
        ("财务审批", "Ana"),
        ("财务审批", "Bruno"),
    ]
    assert snapshot["current_node_name"] == "法务会签、财务审批"
    assert snapshot["current_approver_name"] == "Carla、Ana、Bruno"
    assert snapshot["current_node_entered_at"] == "2026-08-24T08:30:00+08:00"


def test_workflow_snapshot_keeps_unknown_current_approver_id() -> None:
    snapshot = parse_dingtalk_workflow_instance(
        {
            "approval_no": "202608240101",
            "process_instance_id": "process-101",
            "status": "RUNNING",
            "result": "",
            "title": "测试流程",
            "operation_records": [],
            "tasks": [
                {
                    "activityId": "ceo-node",
                    "showName": "CEO 审批",
                    "assigneeUserId": "unknown-user-id",
                    "status": "PROCESSING",
                    "startTime": "2026-08-24T02:00:00Z",
                }
            ],
        },
        {},
    )

    assert snapshot["current_tasks"][0]["approver_id"] == "unknown-user-id"
    assert snapshot["current_tasks"][0]["approver_name"] == "未识别人员（unknown-user-id）"


def test_workflow_cache_is_idempotent_tracks_history_and_rebuilds_request_links(
    isolated_db,
) -> None:
    approval_no = "202608240102"
    workflow = parse_dingtalk_workflow_instance(
        {
            "approval_no": approval_no,
            "process_instance_id": "process-102",
            "status": "COMPLETED",
            "result": "agree",
            "title": "María López提交的采购支出",
            "operation_records": [
                _workflow_operation(
                    activity_id="ceo-node",
                    event_type="EXECUTE_TASK_NORMAL",
                    user_id="ceo-user",
                    event_time="2026-08-24T03:00:00Z",
                    show_name="CEO 审批",
                    result="AGREE",
                    remark="同意",
                )
            ],
            "tasks": [],
            "updated_at": "2026-08-24T03:01:00Z",
        },
        {"ceo-user": "Eduardo Gómez"},
    )
    workflow["current_tasks"] = [
        {
            "task_key": "t1:a",
            "task_id": "t1",
            "activity_id": "n1",
            "node_name": "Finance",
            "approver_id": "a",
            "approver_name": "Ana",
            "entered_at": "2026-08-24T08:00:00+08:00",
        },
        {
            "task_key": "t1:b",
            "task_id": "t1",
            "activity_id": "n1",
            "node_name": "Finance",
            "approver_id": "b",
            "approver_name": "Bruno",
            "entered_at": "2026-08-24T08:00:00+08:00",
        },
        {
            "task_key": "t2:c",
            "task_id": "t2",
            "activity_id": "n2",
            "node_name": "Legal",
            "approver_id": "c",
            "approver_name": "Carla",
            "entered_at": "2026-08-24T07:00:00+08:00",
        },
    ]
    timestamp = "2026-08-24T12:00:00.000000+08:00"

    with isolated_db.connect() as conn:
        first_request = _insert_region_request(
            conn,
            source_sheet="YUEWEI MX核心制造",
            execution_region="Mexico",
        )
        second_request = _insert_region_request(
            conn,
            source_sheet="YUEWEI MX核心制造",
            execution_region="Mexico",
        )
        conn.execute(
            "UPDATE payment_requests SET dingding_id = ? WHERE id IN (?, ?)",
            (approval_no, first_request, second_request),
        )
        conn.execute(
            """
            INSERT INTO mexico_approval_tracking (
                approval_no, source_type, resolved_region, region_resolution_source,
                region_review_status, workflow_status, created_at, updated_at
            ) VALUES (?, 'purchase', 'mexico', 'execution_region', 'resolved',
                      'RUNNING', ?, ?)
            """,
            (approval_no, timestamp, timestamp),
        )
        conn.commit()

        first_result = cache_mexico_workflow_snapshots(
            conn,
            [workflow],
            synced_at=timestamp,
        )
        stored_after_first = conn.execute(
            "SELECT workflow_status, workflow_result, version FROM mexico_approval_tracking "
            "WHERE approval_no = ?",
            (approval_no,),
        ).fetchone()
        second_result = cache_mexico_workflow_snapshots(
            conn,
            [workflow],
            synced_at="2026-08-24T12:05:00.000000+08:00",
        )
        stored_after_second = conn.execute(
            "SELECT workflow_status, workflow_result, version FROM mexico_approval_tracking "
            "WHERE approval_no = ?",
            (approval_no,),
        ).fetchone()

        assert first_result == {
            "workflows_changed": 1,
            "events_added": 1,
            "events_updated": 0,
            "links_added": 2,
            "links_removed": 0,
        }
        assert second_result == {
            "workflows_changed": 0,
            "events_added": 0,
            "events_updated": 0,
            "links_added": 0,
            "links_removed": 0,
        }
        assert tuple(stored_after_first) == ("COMPLETED", "agree", 2)
        assert tuple(stored_after_second) == ("COMPLETED", "agree", 2)
        assert conn.execute(
            "SELECT COUNT(*) AS count FROM mexico_approval_events WHERE approval_no = ?",
            (approval_no,),
        ).fetchone()["count"] == 1
        assert conn.execute(
            "SELECT COUNT(*) AS count FROM mexico_approval_request_links WHERE approval_no = ?",
            (approval_no,),
        ).fetchone()["count"] == 2
        assert conn.execute(
            "SELECT COUNT(*) AS count FROM mexico_approval_current_tasks WHERE approval_no = ?",
            (approval_no,),
        ).fetchone()["count"] == 3
        bruno_items = list_mexico_tracking(
            conn,
            view="history",
            approver="Bruno",
        )["items"]
        assert [item["approval_no"] for item in bruno_items] == [approval_no]
        assert len(bruno_items[0]["current_tasks"]) == 3
        assert bruno_items[0]["current_approvers"] == ["Carla", "Ana", "Bruno"]
        reduced = {
            **workflow,
            "current_tasks": [workflow["current_tasks"][0], workflow["current_tasks"][2]],
        }
        reduced_result = cache_mexico_workflow_snapshots(
            conn,
            [reduced],
            synced_at="2026-08-24T12:08:00.000000+08:00",
        )
        assert reduced_result["workflows_changed"] == 1
        assert conn.execute(
            "SELECT COUNT(*) AS count FROM mexico_approval_current_tasks WHERE approval_no = ?",
            (approval_no,),
        ).fetchone()["count"] == 2

        resumed = {**reduced, "status": "RUNNING", "result": ""}
        cache_mexico_workflow_snapshots(
            conn,
            [resumed],
            synced_at="2026-08-24T12:10:00.000000+08:00",
        )
        restored = conn.execute(
            "SELECT workflow_status, workflow_result, version FROM mexico_approval_tracking "
            "WHERE approval_no = ?",
            (approval_no,),
        ).fetchone()
        assert tuple(restored) == ("RUNNING", "", 4)


def test_mexico_sync_run_reuses_active_task_and_recent_completion(isolated_db) -> None:
    with isolated_db.connect() as conn:
        first, reused = acquire_or_reuse_mexico_sync_run(
            conn,
            actor_id=1,
            trigger_type="automatic",
            only_if_stale_seconds=0,
        )
        second, second_reused = acquire_or_reuse_mexico_sync_run(
            conn,
            actor_id=1,
            trigger_type="manual",
            only_if_stale_seconds=0,
        )

        assert reused is False
        assert second_reused is True
        assert second["id"] == first["id"]

        completed = complete_mexico_sync_run(
            conn,
            first["id"],
            source_cursors={
                "purchase": {
                    "updated_at": "2026-08-24T12:00:00+08:00",
                    "source_id": "9",
                }
            },
            result={"tracked": 12},
        )
        fresh, fresh_reused = acquire_or_reuse_mexico_sync_run(
            conn,
            actor_id=1,
            trigger_type="automatic",
            only_if_stale_seconds=300,
        )

        assert completed["status"] == "completed"
        assert fresh_reused is True
        assert fresh["id"] == first["id"]
        assert fresh["fresh"] is True


def test_mexico_sync_run_records_state_commit_before_completion(isolated_db) -> None:
    with isolated_db.connect() as conn:
        run, _ = acquire_or_reuse_mexico_sync_run(
            conn,
            actor_id=1,
            trigger_type="manual",
        )
        committed = update_mexico_sync_run(
            conn,
            run["id"],
            phase="querying_attachments",
            processed_count=2,
            total_count=2,
            state_committed=True,
        )
        assert committed["status"] == "running"
        assert committed["state_committed_at"]
        same_marker = update_mexico_sync_run(
            conn,
            run["id"],
            phase="syncing_attachments",
            state_committed=True,
        )
        assert same_marker["state_committed_at"] == committed["state_committed_at"]


def test_mexico_sync_run_takes_over_expired_lease(isolated_db) -> None:
    with isolated_db.connect() as conn:
        expired, _ = acquire_or_reuse_mexico_sync_run(
            conn,
            actor_id=1,
            trigger_type="automatic",
            only_if_stale_seconds=0,
            lease_seconds=-1,
        )
        replacement, reused = acquire_or_reuse_mexico_sync_run(
            conn,
            actor_id=1,
            trigger_type="manual",
            only_if_stale_seconds=0,
        )

        assert reused is False
        assert replacement["id"] != expired["id"]
        assert get_mexico_sync_run(conn, expired["id"])["status"] == "interrupted"


def test_failed_mexico_sync_does_not_advance_successful_cursor(isolated_db) -> None:
    old_cursor = {
        "operation": {
            "updated_at": "2026-08-20T10:00:00+08:00",
            "source_id": "10",
        }
    }
    with isolated_db.connect() as conn:
        successful, _ = acquire_or_reuse_mexico_sync_run(
            conn,
            actor_id=1,
            trigger_type="manual",
            only_if_stale_seconds=0,
        )
        complete_mexico_sync_run(
            conn,
            successful["id"],
            source_cursors=old_cursor,
            result={"tracked": 3},
        )
        failed, _ = acquire_or_reuse_mexico_sync_run(
            conn,
            actor_id=1,
            trigger_type="manual",
            only_if_stale_seconds=0,
        )
        fail_mexico_sync_run(conn, failed["id"], "external database unavailable")

        assert get_mexico_sync_run(conn, failed["id"])["source_cursors"] == {}
        latest_success = conn.execute(
            "SELECT id FROM mexico_sync_runs WHERE kind = 'mexico-tracking' "
            "AND status = 'completed' ORDER BY completed_at DESC LIMIT 1"
        ).fetchone()
        assert get_mexico_sync_run(conn, latest_success["id"])["source_cursors"] == old_cursor


def test_mexico_discovery_cache_preserves_admin_region_override(isolated_db) -> None:
    candidate = {
        "approval_no": "202608241200000000001",
        "source_type": "purchase",
        "source_record_id": "88",
        "process_code": "PROC-PURCHASE",
        "process_instance_id": "instance-88",
        "raw_execution_region": "Mexico",
        "resolved_region": "mexico",
        "region_resolution_source": "execution_region",
        "region_conflict_reason": None,
        "request_date": "2026-08-24",
        "applicant_id": "user-1",
        "applicant_name": "Nelly Mendez",
        "applicant_department": "YUEWEI MX",
        "company_name": "YUEWEI MX核心制造",
        "source_sheet": "YUEWEI MX核心制造",
        "summary": "Compra de materiales",
        "amount": 1200.5,
        "currency": "MXN",
        "workflow_status": "RUNNING",
        "workflow_result": "",
        "source_updated_at": "2026-08-24T11:00:00+08:00",
        "source_conflict": False,
        "warnings": [],
        "errors": [],
        "raw_summary": {"execution_region": "Mexico"},
    }
    with isolated_db.connect() as conn:
        first = cache_mexico_discovery_candidates(conn, [candidate])
        assert first == {"inserted": 1, "updated": 0, "unchanged": 0}

        conn.execute(
            """
            UPDATE mexico_approval_tracking
            SET resolved_region = 'china', region_resolution_source = 'admin_override',
                region_review_status = 'resolved', region_reviewed_by = 1,
                region_reviewed_at = '2026-08-24T12:05:00+08:00'
            WHERE approval_no = ?
            """,
            (candidate["approval_no"],),
        )
        conn.commit()

        changed = {**candidate, "summary": "Compra de materiales actualizada"}
        second = cache_mexico_discovery_candidates(conn, [changed])
        stored = conn.execute(
            """
            SELECT summary, resolved_region, region_resolution_source,
                   region_review_status, region_reviewed_by
            FROM mexico_approval_tracking WHERE approval_no = ?
            """,
            (candidate["approval_no"],),
        ).fetchone()

        assert second == {"inserted": 0, "updated": 1, "unchanged": 0}
        assert tuple(stored) == (
            "Compra de materiales actualizada",
            "china",
            "admin_override",
            "resolved",
            1,
        )


def _insert_mexico_tracking_row(conn, approval_no: str = "202608241200000000888") -> None:
    timestamp = "2026-08-24T12:00:00.000000+08:00"
    conn.execute(
        """
        INSERT INTO mexico_approval_tracking (
            approval_no, source_type, process_instance_id, resolved_region,
            region_resolution_source, region_review_status, workflow_status,
            created_at, updated_at
        ) VALUES (?, 'purchase', 'process-888', 'mexico',
                  'execution_region', 'resolved', 'RUNNING', ?, ?)
        """,
        (approval_no, timestamp, timestamp),
    )
    conn.commit()


def _insert_tracking_case(
    conn,
    *,
    approval_no: str,
    sheet: str,
    status: str = "RUNNING",
    result: str = "",
    region: str = "mexico",
    review_status: str = "resolved",
    node_entered_at: str = "2026-08-20T09:00:00.000000+08:00",
    applicant: str = "Nelly Mendez",
    approver: str = "Eduardo Gomez",
) -> int:
    timestamp = "2026-08-24T12:00:00.000000+08:00"
    cursor = conn.execute(
        """
        INSERT INTO mexico_approval_tracking (
            approval_no, source_type, process_instance_id, resolved_region,
            region_resolution_source, region_review_status, request_date,
            applicant_name, applicant_department, company_name, source_sheet,
            summary, amount, currency, workflow_status, workflow_result,
            current_node_name, current_approver_name, current_node_entered_at,
            workflow_url, created_at, updated_at
        ) VALUES (?, 'purchase', ?, ?, 'execution_region', ?, '2026-08-20',
                  ?, ?, ?, ?, 'Compra de materiales', 1200, 'MXN', ?, ?,
                  'Director approval', ?, ?, 'https://oa.example/approval', ?, ?)
        """,
        (
            approval_no,
            f"process-{approval_no}",
            region,
            review_status,
            applicant,
            sheet,
            sheet,
            sheet,
            status,
            result,
            approver,
            node_entered_at,
            timestamp,
            timestamp,
        ),
    )
    conn.commit()
    return int(cursor.lastrowid)


def test_mexico_tracking_list_separates_pending_history_and_review(isolated_db) -> None:
    now = datetime.fromisoformat("2026-08-24T12:00:00+08:00")
    with isolated_db.connect() as conn:
        pending_id = _insert_tracking_case(
            conn,
            approval_no="MX-PENDING",
            sheet="YUEWEI MX核心制造",
        )
        _insert_tracking_case(
            conn,
            approval_no="MX-DONE",
            sheet="YUEWEI MX核心制造",
            status="COMPLETED",
            result="agree",
        )
        _insert_tracking_case(
            conn,
            approval_no="MX-REVIEW",
            sheet="未登记公司",
            region="review",
            review_status="pending",
        )

        pending = list_mexico_tracking(conn, view="pending", now=now)
        history = list_mexico_tracking(conn, view="history", now=now)
        review = list_mexico_tracking(conn, view="review", now=now)

        assert [item["id"] for item in pending["items"]] == [pending_id]
        assert [item["approval_no"] for item in history["items"]] == ["MX-DONE"]
        assert [item["approval_no"] for item in review["items"]] == ["MX-REVIEW"]
        assert pending["items"][0]["age_days"] == 4
        assert pending["items"][0]["warning_level"] == "yellow"
        assert "MX-PENDING" in pending["items"][0]["reminder"]["zh"]


def test_mexico_tracking_list_respects_business_sheet_permissions(isolated_db) -> None:
    with isolated_db.connect() as conn:
        _insert_tracking_case(
            conn,
            approval_no="MX-MOLDES",
            sheet="YW MOLDES MX模具",
        )
        _insert_tracking_case(
            conn,
            approval_no="MX-LEMOS",
            sheet="LEMOS MX 销售",
        )

        result = list_mexico_tracking(
            conn,
            view="pending",
            allowed_sheets={"YW MOLDES MX模具"},
        )
        options = mexico_tracking_filter_options(
            conn,
            allowed_sheets={"YW MOLDES MX模具"},
        )

        assert [item["approval_no"] for item in result["items"]] == ["MX-MOLDES"]
        assert options["companies"] == ["YW MOLDES MX模具"]


def test_mexico_tracking_summary_and_warning_filter_use_configured_thresholds(
    isolated_db,
) -> None:
    now = datetime.fromisoformat("2026-08-24T12:00:00+08:00")
    with isolated_db.connect() as conn:
        update_mexico_tracking_settings(conn, yellow_days=1, red_days=3)
        _insert_tracking_case(
            conn,
            approval_no="MX-YELLOW",
            sheet="YUEWEI MX核心制造",
            node_entered_at="2026-08-22T09:00:00+08:00",
        )
        _insert_tracking_case(
            conn,
            approval_no="MX-RED",
            sheet="YUEWEI MX核心制造",
            node_entered_at="2026-08-19T09:00:00+08:00",
        )

        summary = summarize_mexico_tracking(conn, now=now)
        red = list_mexico_tracking(conn, view="pending", warning="red", now=now)

        assert summary["pending"] == 2
        assert summary["yellow"] == 1
        assert summary["red"] == 1
        assert [item["approval_no"] for item in red["items"]] == ["MX-RED"]


def test_mexico_tracking_detail_returns_timeline_attachments_and_links(isolated_db) -> None:
    with isolated_db.connect() as conn:
        tracking_id = _insert_tracking_case(
            conn,
            approval_no="MX-DETAIL",
            sheet="YUEWEI MX核心制造",
        )
        timestamp = "2026-08-24T12:00:00.000000+08:00"
        conn.execute(
            """
            INSERT INTO mexico_approval_events (
                approval_no, event_key, sequence_index, event_type, node_name,
                result, operator_name, event_time, comment, images_json,
                attachments_json, is_current, created_at, updated_at
            ) VALUES ('MX-DETAIL', 'event-1', 1, 'TASK', 'Finance', 'AGREE',
                      'Keira', '2026-08-23T10:00:00+08:00', 'Reviewed', '[]',
                      '[]', 0, ?, ?)
            """,
            (timestamp, timestamp),
        )
        conn.execute(
            """
            INSERT INTO mexico_approval_attachments (
                approval_no, source_file_id, file_name, mime_type, status,
                created_at, updated_at
            ) VALUES ('MX-DETAIL', 'file-1', 'invoice.pdf', 'application/pdf',
                      'ready', ?, ?)
            """,
            (timestamp, timestamp),
        )
        batch_id = int(
            conn.execute(
                "INSERT INTO request_batches (name, status, created_at, updated_at) VALUES ('B', 'draft', ?, ?)",
                (timestamp, timestamp),
            ).lastrowid
        )
        request_id = int(
            conn.execute(
                """
                INSERT INTO payment_requests (
                    batch_id, dingding_id, source_sheet, summary, amount,
                    paid_amount, pending_amount, currency, payment_status,
                    created_at, updated_at
                ) VALUES (?, 'MX-DETAIL', 'YUEWEI MX核心制造', 'Compra', 1200,
                          0, 1200, 'MXN', '未付款', ?, ?)
                """,
                (batch_id, timestamp, timestamp),
            ).lastrowid
        )
        conn.execute(
            "INSERT INTO mexico_approval_request_links (approval_no, request_id, is_primary, created_at) VALUES ('MX-DETAIL', ?, 1, ?)",
            (request_id, timestamp),
        )
        conn.executemany(
            """
            INSERT INTO mexico_approval_current_tasks (
                approval_no, task_key, task_id, activity_id, node_name,
                approver_id, approver_name, entered_at, synced_at, created_at, updated_at
            ) VALUES ('MX-DETAIL', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    "finance:ana",
                    "finance",
                    "finance-node",
                    "Finance",
                    "ana",
                    "Ana",
                    "2026-08-24T08:00:00+08:00",
                    timestamp,
                    timestamp,
                    timestamp,
                ),
                (
                    "legal:carla",
                    "legal",
                    "legal-node",
                    "Legal",
                    "carla",
                    "Carla",
                    "2026-08-24T07:00:00+08:00",
                    timestamp,
                    timestamp,
                    timestamp,
                ),
            ],
        )
        conn.commit()

        detail = get_mexico_tracking_detail(conn, tracking_id)

        assert detail["approval_no"] == "MX-DETAIL"
        assert detail["events"][0]["comment"] == "Reviewed"
        assert detail["attachments"][0]["file_name"] == "invoice.pdf"
        assert detail["linked_requests"][0]["id"] == request_id
        assert [task["approver_name"] for task in detail["current_tasks"]] == [
            "Carla",
            "Ana",
        ]
        assert detail["current_approvers"] == ["Carla", "Ana"]
        assert detail["current_nodes"] == ["Legal", "Finance"]
        options = mexico_tracking_filter_options(conn)
        assert options["approvers"] == ["Ana", "Carla"]
        assert options["nodes"] == ["Finance", "Legal"]


def test_admin_region_resolution_is_versioned_and_auditable(isolated_db) -> None:
    with isolated_db.connect() as conn:
        tracking_id = _insert_tracking_case(
            conn,
            approval_no="MX-REVIEW-RESOLVE",
            sheet="未登记公司",
            region="review",
            review_status="pending",
        )

        resolved = resolve_mexico_tracking_region(
            conn,
            tracking_id,
            region="mexico",
            expected_version=1,
            actor_id=1,
        )

        assert resolved["resolved_region"] == "mexico"
        assert resolved["region_resolution_source"] == "admin_override"
        assert resolved["version"] == 2
        with pytest.raises(ValueError, match="VERSION_CONFLICT"):
            resolve_mexico_tracking_region(
                conn,
                tracking_id,
                region="china",
                expected_version=1,
                actor_id=1,
            )


def test_mexico_attachment_candidates_deduplicate_workflow_and_source_rows() -> None:
    workflows = [
        {
            "approval_no": "202608241200000000888",
            "process_instance_id": "process-888",
            "events": [
                {
                    "event_key": "event-1",
                    "images": [
                        {"fileId": "file-1", "fileName": "pago.png", "fileSize": 12}
                    ],
                    "attachments": [
                        {"fileId": "file-1", "fileName": "pago.png", "fileType": "png"}
                    ],
                }
            ],
        }
    ]
    source_rows = [
        {
            "approval_no": "202608241200000000888",
            "attachment_id": "source-row-1",
            "file_id": "file-1",
            "file_name": "pago.png",
            "file_type": "image/png",
            "file_size": 12,
        }
    ]

    candidates = collect_mexico_attachment_candidates(workflows, source_rows)

    assert len(candidates) == 1
    assert candidates[0]["approval_no"] == "202608241200000000888"
    assert candidates[0]["process_instance_id"] == "process-888"
    assert candidates[0]["source_file_id"] == "file-1"
    assert candidates[0]["event_key"] == "event-1"
    assert candidates[0]["file_name"] == "pago.png"


def test_mexico_attachment_inventory_is_idempotent_and_failed_rows_retry(isolated_db) -> None:
    approval_no = "202608241200000000888"
    candidate = {
        "approval_no": approval_no,
        "process_instance_id": "process-888",
        "source_file_id": "file-1",
        "file_id": "file-1",
        "event_key": "event-1",
        "file_name": "pago.pdf",
        "mime_type": "application/pdf",
        "size_bytes": 123,
    }
    with isolated_db.connect() as conn:
        _insert_mexico_tracking_row(conn, approval_no)

        first = upsert_mexico_attachment_candidates(conn, [candidate])
        second = upsert_mexico_attachment_candidates(conn, [candidate])
        assert first == {"inserted": 1, "updated": 0, "existing": 0}
        assert second == {"inserted": 0, "updated": 0, "existing": 1}
        assert conn.execute(
            "SELECT COUNT(*) FROM mexico_approval_attachments"
        ).fetchone()[0] == 1

        pending = list_mexico_attachment_download_candidates(conn, [approval_no])
        assert len(pending) == 1
        attachment_id = pending[0]["attachment_id"]
        mark_mexico_attachments_downloading(conn, [attachment_id])
        mark_mexico_attachment_failed(conn, attachment_id, "network timeout")

        failed = list_mexico_attachment_download_candidates(conn, [approval_no])
        assert len(failed) == 1
        assert failed[0]["attempts"] == 1
        assert failed[0]["status"] == "failed"

        mark_mexico_attachments_downloading(conn, [attachment_id])
        timestamp = isolated_db.now_iso()
        file_object_id = int(
            conn.execute(
                """
                INSERT INTO file_objects (
                    sha256, size_bytes, mime_type, storage_backend, storage_path,
                    status, created_at, verified_at
                ) VALUES (?, 1, 'application/pdf', 'local', ?, 'ready', ?, ?)
                """,
                ("f" * 64, "attachments/sha256/ff/" + "f" * 64, timestamp, timestamp),
            ).lastrowid
        )
        mark_mexico_attachment_ready(
            conn, attachment_id, file_object_id=file_object_id
        )
        upsert_mexico_attachment_candidates(conn, [candidate])
        stored = conn.execute(
            "SELECT status, attempts, file_object_id FROM mexico_approval_attachments WHERE id = ?",
            (attachment_id,),
        ).fetchone()
        assert tuple(stored) == ("ready", 2, file_object_id)
        assert list_mexico_attachment_download_candidates(conn, [approval_no]) == []


def test_stale_mexico_attachment_download_is_retried_after_interruption(isolated_db) -> None:
    approval_no = "202608241200000000889"
    candidate = {
        "approval_no": approval_no,
        "source_file_id": "file-stale",
        "file_name": "stale.pdf",
    }
    with isolated_db.connect() as conn:
        _insert_mexico_tracking_row(conn, approval_no)
        upsert_mexico_attachment_candidates(conn, [candidate])
        attachment_id = conn.execute(
            "SELECT id FROM mexico_approval_attachments WHERE approval_no = ?",
            (approval_no,),
        ).fetchone()[0]
        mark_mexico_attachments_downloading(
            conn,
            [attachment_id],
            timestamp="2026-08-24T10:00:00.000000+08:00",
        )

        retryable = list_mexico_attachment_download_candidates(
            conn,
            [approval_no],
            now="2026-08-24T11:00:00.000000+08:00",
            stale_after_seconds=1800,
        )

        assert len(retryable) == 1
        assert retryable[0]["attachment_id"] == attachment_id
        assert retryable[0]["status"] == "downloading"

        mark_mexico_attachments_downloading(conn, [attachment_id])
        assert conn.execute(
            "SELECT attempts FROM mexico_approval_attachments WHERE id = ?",
            (attachment_id,),
        ).fetchone()[0] == 2
