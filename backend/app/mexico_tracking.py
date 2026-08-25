from __future__ import annotations

import sqlite3
import json
import unicodedata
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Dict, Iterable, Optional
from zoneinfo import ZoneInfo


SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")

MEXICO_TRACKING_SETTING_DEFAULTS = {
    "mexico_warning_yellow_days": "2",
    "mexico_warning_red_days": "5",
    "mexico_tracking_cache_stale_seconds": "300",
    "china_region_isolation_enabled": "true",
}

CHINA_SHEETS = frozenset(
    {
        "悦为智能 YW Tech_Ai",
        "拉丁购",
        "凌翔产品&开发",
        "凌翔供应链及采购执行单元",
        "星铭HR人力资源中心",
        "星铭FC财务中心",
        "凌翔/星铭供应链及职能中心",
    }
)

MEXICO_SHEETS = frozenset(
    {
        "YW MOLDES MX模具",
        "YUEWEI MX核心制造",
        "LEMOS MX供应链开发及管理",
        "LEMOS MX 销售",
        "UV IMPRESION MX彩印",
        "FC 财务中心 Centro Financiero (FC)",
    }
)


def _now_iso() -> str:
    return datetime.now(tz=SHANGHAI_TZ).isoformat(timespec="microseconds")


def _json_object(value: Optional[str]) -> Dict[str, Any]:
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _sync_run_payload(row: sqlite3.Row, *, fresh: bool = False) -> Dict[str, Any]:
    payload = dict(row)
    payload["source_cursors"] = _json_object(row["source_cursors_json"])
    payload["stage_timings"] = _json_object(row["stage_timings_json"])
    payload["result"] = _json_object(row["result_json"])
    payload["fresh"] = fresh
    return payload


def _lease_until(seconds: int) -> str:
    return (
        datetime.now(tz=SHANGHAI_TZ) + timedelta(seconds=seconds)
    ).isoformat(timespec="microseconds")


def acquire_or_reuse_mexico_sync_run(
    conn: sqlite3.Connection,
    *,
    actor_id: Optional[int],
    trigger_type: str,
    kind: str = "mexico-tracking",
    only_if_stale_seconds: int = 0,
    lease_seconds: int = 1800,
) -> tuple[Dict[str, Any], bool]:
    """Acquire the single global Mexico sync lease or reuse current work.

    Acquisition is serialized with ``BEGIN IMMEDIATE`` but the transaction is
    committed before any PostgreSQL or attachment I/O starts.  This keeps the
    page writable while the external systems are being queried.
    """

    timestamp = _now_iso()
    run_kind = str(kind or "mexico-tracking").strip()
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute(
            """
            UPDATE mexico_sync_runs
            SET status = 'interrupted', phase = 'interrupted', completed_at = ?,
                error_message = COALESCE(error_message, '任务租约过期，已由后续同步接管'),
                updated_at = ?
            WHERE kind = ?
              AND status IN ('queued', 'running')
              AND lease_until IS NOT NULL AND lease_until <= ?
            """,
            (timestamp, timestamp, run_kind, timestamp),
        )
        active = conn.execute(
            """
            SELECT * FROM mexico_sync_runs
            WHERE kind = ? AND status IN ('queued', 'running')
            ORDER BY created_at DESC LIMIT 1
            """,
            (run_kind,),
        ).fetchone()
        if active is not None:
            conn.commit()
            return _sync_run_payload(active), True

        if only_if_stale_seconds > 0:
            latest = conn.execute(
                """
                SELECT * FROM mexico_sync_runs
                WHERE kind = ? AND status = 'completed'
                ORDER BY completed_at DESC LIMIT 1
                """,
                (run_kind,),
            ).fetchone()
            if latest is not None and latest["completed_at"]:
                completed_at = datetime.fromisoformat(str(latest["completed_at"]))
                if datetime.now(tz=SHANGHAI_TZ) - completed_at < timedelta(
                    seconds=only_if_stale_seconds
                ):
                    conn.commit()
                    return _sync_run_payload(latest, fresh=True), True

        run_id = uuid.uuid4().hex
        conn.execute(
            """
            INSERT INTO mexico_sync_runs (
                id, kind, trigger_type, triggered_by, status, phase,
                lease_owner, lease_until, created_at, updated_at
            ) VALUES (?, ?, ?, ?, 'queued', 'queued', ?, ?, ?, ?)
            """,
            (
                run_id,
                run_kind,
                str(trigger_type or "manual"),
                actor_id,
                run_id,
                _lease_until(lease_seconds),
                timestamp,
                timestamp,
            ),
        )
        row = conn.execute(
            "SELECT * FROM mexico_sync_runs WHERE id = ?", (run_id,)
        ).fetchone()
        conn.commit()
        return _sync_run_payload(row), False
    except Exception:
        conn.rollback()
        raise


def get_mexico_sync_run(conn: sqlite3.Connection, run_id: str) -> Dict[str, Any]:
    row = conn.execute(
        "SELECT * FROM mexico_sync_runs WHERE id = ?", (str(run_id),)
    ).fetchone()
    if row is None:
        raise KeyError(f"Mexico sync run {run_id} does not exist")
    return _sync_run_payload(row)


def update_mexico_sync_run(
    conn: sqlite3.Connection,
    run_id: str,
    *,
    phase: str,
    processed_count: int = 0,
    total_count: int = 0,
    attachment_processed_count: int = 0,
    attachment_total_count: int = 0,
    stage_timings: Optional[Dict[str, Any]] = None,
    state_committed: bool = False,
    lease_seconds: int = 1800,
) -> Dict[str, Any]:
    timestamp = _now_iso()
    cursor = conn.execute(
        """
        UPDATE mexico_sync_runs
        SET status = 'running', phase = ?, processed_count = ?, total_count = ?,
            attachment_processed_count = ?, attachment_total_count = ?,
            stage_timings_json = COALESCE(?, stage_timings_json),
            state_committed_at = CASE
                WHEN ? THEN COALESCE(state_committed_at, ?)
                ELSE state_committed_at
            END,
            started_at = COALESCE(started_at, ?), heartbeat_at = ?,
            lease_until = ?, updated_at = ?
        WHERE id = ? AND status IN ('queued', 'running')
        """,
        (
            str(phase),
            max(0, int(processed_count)),
            max(0, int(total_count)),
            max(0, int(attachment_processed_count)),
            max(0, int(attachment_total_count)),
            json.dumps(stage_timings, ensure_ascii=False, default=str)
            if stage_timings is not None
            else None,
            int(bool(state_committed)),
            timestamp,
            timestamp,
            timestamp,
            _lease_until(lease_seconds),
            timestamp,
            str(run_id),
        ),
    )
    if cursor.rowcount != 1:
        raise RuntimeError("墨西哥同步任务已结束或租约已失效")
    conn.commit()
    return get_mexico_sync_run(conn, run_id)


def complete_mexico_sync_run(
    conn: sqlite3.Connection,
    run_id: str,
    *,
    source_cursors: Dict[str, Any],
    result: Dict[str, Any],
    stage_timings: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    timestamp = _now_iso()
    cursor = conn.execute(
        """
        UPDATE mexico_sync_runs
        SET status = 'completed', phase = 'complete', source_cursors_json = ?,
            result_json = ?, stage_timings_json = COALESCE(?, stage_timings_json),
            completed_at = ?, heartbeat_at = ?, lease_until = NULL, updated_at = ?
        WHERE id = ? AND status IN ('queued', 'running')
        """,
        (
            json.dumps(source_cursors, ensure_ascii=False, default=str),
            json.dumps(result, ensure_ascii=False, default=str),
            json.dumps(stage_timings, ensure_ascii=False, default=str)
            if stage_timings is not None
            else None,
            timestamp,
            timestamp,
            timestamp,
            str(run_id),
        ),
    )
    if cursor.rowcount != 1:
        raise RuntimeError("墨西哥同步任务无法完成")
    conn.commit()
    return get_mexico_sync_run(conn, run_id)


def fail_mexico_sync_run(
    conn: sqlite3.Connection, run_id: str, error_message: str
) -> Dict[str, Any]:
    timestamp = _now_iso()
    conn.execute(
        """
        UPDATE mexico_sync_runs
        SET status = 'failed', phase = 'failed', error_message = ?,
            completed_at = ?, heartbeat_at = ?, lease_until = NULL, updated_at = ?
        WHERE id = ? AND status IN ('queued', 'running')
        """,
        (str(error_message)[:2000], timestamp, timestamp, timestamp, str(run_id)),
    )
    conn.commit()
    return get_mexico_sync_run(conn, run_id)


def _ensure_column(
    conn: sqlite3.Connection, table: str, column: str, definition: str
) -> None:
    existing = {
        row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
    }
    if column not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def ensure_mexico_tracking_schema(conn: sqlite3.Connection) -> None:
    """Create the Mexico approval cache without changing current China views."""

    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS mexico_approval_tracking (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            approval_no TEXT NOT NULL UNIQUE,
            source_type TEXT NOT NULL,
            source_record_id TEXT,
            process_code TEXT,
            process_instance_id TEXT,
            raw_execution_region TEXT,
            resolved_region TEXT NOT NULL DEFAULT 'review'
                CHECK (resolved_region IN ('china', 'mexico', 'review')),
            region_resolution_source TEXT NOT NULL DEFAULT 'unknown',
            region_review_status TEXT NOT NULL DEFAULT 'pending'
                CHECK (region_review_status IN ('pending', 'resolved')),
            region_reviewed_by INTEGER REFERENCES users(id),
            region_reviewed_at TEXT,
            region_conflict_reason TEXT,
            request_date TEXT,
            applicant_id TEXT,
            applicant_name TEXT,
            applicant_department TEXT,
            company_name TEXT,
            source_sheet TEXT,
            summary TEXT,
            amount REAL,
            currency TEXT,
            workflow_status TEXT,
            workflow_result TEXT,
            current_node_name TEXT,
            current_approver_id TEXT,
            current_approver_name TEXT,
            current_node_entered_at TEXT,
            workflow_url TEXT,
            linked_request_id INTEGER REFERENCES payment_requests(id) ON DELETE SET NULL,
            source_updated_at TEXT,
            last_state_synced_at TEXT,
            last_attachment_synced_at TEXT,
            last_synced_at TEXT,
            raw_summary_json TEXT,
            version INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_mexico_tracking_region_status
        ON mexico_approval_tracking(resolved_region, region_review_status, workflow_status);
        CREATE INDEX IF NOT EXISTS idx_mexico_tracking_sheet
        ON mexico_approval_tracking(source_sheet);
        CREATE INDEX IF NOT EXISTS idx_mexico_tracking_applicant
        ON mexico_approval_tracking(applicant_id, applicant_name);
        CREATE INDEX IF NOT EXISTS idx_mexico_tracking_approver
        ON mexico_approval_tracking(current_approver_id, current_approver_name);
        CREATE INDEX IF NOT EXISTS idx_mexico_tracking_node
        ON mexico_approval_tracking(current_node_name, current_node_entered_at);
        CREATE INDEX IF NOT EXISTS idx_mexico_tracking_request_date
        ON mexico_approval_tracking(request_date DESC);
        CREATE INDEX IF NOT EXISTS idx_mexico_tracking_last_synced
        ON mexico_approval_tracking(last_synced_at);

        CREATE TABLE IF NOT EXISTS mexico_approval_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            approval_no TEXT NOT NULL REFERENCES mexico_approval_tracking(approval_no)
                ON DELETE CASCADE,
            event_key TEXT NOT NULL,
            process_instance_id TEXT,
            sequence_index INTEGER NOT NULL DEFAULT 0,
            activity_id TEXT,
            event_type TEXT,
            node_name TEXT,
            result TEXT,
            operator_id TEXT,
            operator_name TEXT,
            event_time TEXT,
            comment TEXT,
            images_json TEXT,
            attachments_json TEXT,
            is_current INTEGER NOT NULL DEFAULT 0,
            synced_at TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(approval_no, event_key)
        );
        CREATE INDEX IF NOT EXISTS idx_mexico_events_approval_time
        ON mexico_approval_events(approval_no, event_time, sequence_index, id);
        CREATE INDEX IF NOT EXISTS idx_mexico_events_operator
        ON mexico_approval_events(operator_name, approval_no);

        CREATE TABLE IF NOT EXISTS mexico_approval_current_tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            approval_no TEXT NOT NULL REFERENCES mexico_approval_tracking(approval_no)
                ON DELETE CASCADE,
            task_key TEXT NOT NULL,
            task_id TEXT,
            activity_id TEXT,
            node_name TEXT,
            approver_id TEXT,
            approver_name TEXT,
            entered_at TEXT,
            synced_at TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(approval_no, task_key)
        );
        CREATE INDEX IF NOT EXISTS idx_mexico_current_tasks_approver
        ON mexico_approval_current_tasks(approver_id, approver_name);
        CREATE INDEX IF NOT EXISTS idx_mexico_current_tasks_node
        ON mexico_approval_current_tasks(node_name, entered_at);

        CREATE TABLE IF NOT EXISTS mexico_approval_request_links (
            approval_no TEXT NOT NULL REFERENCES mexico_approval_tracking(approval_no)
                ON DELETE CASCADE,
            request_id INTEGER NOT NULL REFERENCES payment_requests(id) ON DELETE CASCADE,
            is_primary INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            UNIQUE(approval_no, request_id)
        );
        CREATE INDEX IF NOT EXISTS idx_mexico_links_request
        ON mexico_approval_request_links(request_id);

        CREATE TABLE IF NOT EXISTS mexico_approval_attachments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            approval_no TEXT NOT NULL REFERENCES mexico_approval_tracking(approval_no)
                ON DELETE CASCADE,
            event_key TEXT,
            source_file_id TEXT NOT NULL,
            file_name TEXT,
            mime_type TEXT,
            size_bytes INTEGER,
            source_url TEXT,
            file_object_id INTEGER REFERENCES file_objects(id) ON DELETE SET NULL,
            status TEXT NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending', 'downloading', 'ready', 'failed')),
            attempts INTEGER NOT NULL DEFAULT 0,
            priority INTEGER NOT NULL DEFAULT 0,
            requested_at TEXT,
            claim_token TEXT,
            claimed_at TEXT,
            last_error TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(approval_no, source_file_id)
        );
        CREATE INDEX IF NOT EXISTS idx_mexico_attachments_status
        ON mexico_approval_attachments(status, updated_at);

        CREATE TABLE IF NOT EXISTS mexico_sync_runs (
            id TEXT PRIMARY KEY,
            kind TEXT NOT NULL,
            trigger_type TEXT,
            triggered_by INTEGER REFERENCES users(id),
            status TEXT NOT NULL DEFAULT 'queued'
                CHECK (status IN ('queued', 'running', 'completed', 'failed', 'interrupted')),
            phase TEXT,
            processed_count INTEGER NOT NULL DEFAULT 0,
            total_count INTEGER NOT NULL DEFAULT 0,
            attachment_processed_count INTEGER NOT NULL DEFAULT 0,
            attachment_total_count INTEGER NOT NULL DEFAULT 0,
            source_cursors_json TEXT,
            stage_timings_json TEXT,
            result_json TEXT,
            error_message TEXT,
            lease_owner TEXT,
            lease_until TEXT,
            started_at TEXT,
            heartbeat_at TEXT,
            state_committed_at TEXT,
            completed_at TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE UNIQUE INDEX IF NOT EXISTS idx_mexico_sync_one_running
        ON mexico_sync_runs(kind) WHERE status = 'running';
        CREATE INDEX IF NOT EXISTS idx_mexico_sync_created
        ON mexico_sync_runs(created_at DESC);
        """
    )

    for column, definition in (
        ("resolved_region", "TEXT NOT NULL DEFAULT 'review'"),
        ("region_resolution_source", "TEXT NOT NULL DEFAULT 'unknown'"),
        ("region_review_status", "TEXT NOT NULL DEFAULT 'pending'"),
        ("region_reviewed_by", "INTEGER REFERENCES users(id)"),
        ("region_reviewed_at", "TEXT"),
    ):
        _ensure_column(conn, "payment_requests", column, definition)

    _ensure_column(conn, "payable_history_versions", "resolved_region", "TEXT")
    _ensure_column(conn, "payable_history_versions", "region_review_status", "TEXT")
    _ensure_column(conn, "mexico_sync_runs", "state_committed_at", "TEXT")
    for column, definition in (
        ("priority", "INTEGER NOT NULL DEFAULT 0"),
        ("requested_at", "TEXT"),
        ("claim_token", "TEXT"),
        ("claimed_at", "TEXT"),
    ):
        _ensure_column(conn, "mexico_approval_attachments", column, definition)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_mexico_attachments_queue "
        "ON mexico_approval_attachments(status, priority DESC, requested_at, id)"
    )

    timestamp = _now_iso()
    for key, value in MEXICO_TRACKING_SETTING_DEFAULTS.items():
        conn.execute(
            "INSERT OR IGNORE INTO app_settings (key, value, updated_at) VALUES (?, ?, ?)",
            (key, value, timestamp),
        )


def _setting_value(conn: sqlite3.Connection, key: str, default: str) -> str:
    row = conn.execute("SELECT value FROM app_settings WHERE key = ?", (key,)).fetchone()
    return str(row["value"]) if row is not None else default


def get_mexico_tracking_settings(conn: sqlite3.Connection) -> Dict[str, Any]:
    defaults = MEXICO_TRACKING_SETTING_DEFAULTS
    return {
        "yellow_days": int(
            _setting_value(conn, "mexico_warning_yellow_days", defaults["mexico_warning_yellow_days"])
        ),
        "red_days": int(
            _setting_value(conn, "mexico_warning_red_days", defaults["mexico_warning_red_days"])
        ),
        "cache_stale_seconds": int(
            _setting_value(
                conn,
                "mexico_tracking_cache_stale_seconds",
                defaults["mexico_tracking_cache_stale_seconds"],
            )
        ),
        "china_region_isolation_enabled": True,
    }


def update_mexico_tracking_settings(
    conn: sqlite3.Connection,
    *,
    yellow_days: Optional[int] = None,
    red_days: Optional[int] = None,
    cache_stale_seconds: Optional[int] = None,
    china_region_isolation_enabled: Optional[bool] = None,
) -> Dict[str, Any]:
    current = get_mexico_tracking_settings(conn)
    yellow = current["yellow_days"] if yellow_days is None else yellow_days
    red = current["red_days"] if red_days is None else red_days
    stale = current["cache_stale_seconds"] if cache_stale_seconds is None else cache_stale_seconds
    isolation = True
    if isinstance(yellow, bool) or isinstance(red, bool) or not isinstance(yellow, int) or not isinstance(red, int):
        raise ValueError("warning days must be integers")
    if not 0 <= yellow < red <= 365:
        raise ValueError("warning days must satisfy 0 <= yellow < red <= 365")
    if isinstance(stale, bool) or not isinstance(stale, int) or stale < 0:
        raise ValueError("cache_stale_seconds must be a non-negative integer")
    if (
        china_region_isolation_enabled is not None
        and not isinstance(china_region_isolation_enabled, bool)
    ):
        raise ValueError("china_region_isolation_enabled must be boolean")

    values = {
        "mexico_warning_yellow_days": str(yellow),
        "mexico_warning_red_days": str(red),
        "mexico_tracking_cache_stale_seconds": str(stale),
        "china_region_isolation_enabled": "true" if isolation else "false",
    }
    timestamp = _now_iso()
    for key, value in values.items():
        conn.execute(
            """
            INSERT INTO app_settings (key, value, updated_at) VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
            """,
            (key, value, timestamp),
        )
    return get_mexico_tracking_settings(conn)


@dataclass(frozen=True)
class RegionDecision:
    region: str
    source: str
    execution_region_raw: Optional[str] = None
    sheet_region: Optional[str] = None
    conflict_reason: Optional[str] = None


def _normalized_token(value: Optional[str]) -> str:
    if value is None:
        return ""
    normalized = unicodedata.normalize("NFKD", str(value).strip()).casefold()
    return "".join(char for char in normalized if not unicodedata.combining(char))


def _execution_region(value: Optional[str]) -> Optional[str]:
    normalized = _normalized_token(value)
    if not normalized:
        return None
    if "墨西哥" in normalized or "mexico" in normalized:
        return "mexico"
    if "中国" in normalized or "china" in normalized:
        return "china"
    return None


def sheet_region(source_sheet: Optional[str]) -> Optional[str]:
    name = str(source_sheet or "").strip()
    if name in CHINA_SHEETS:
        return "china"
    if name in MEXICO_SHEETS:
        return "mexico"
    return None


def resolve_region(
    *,
    execution_region: Optional[str] = None,
    source_sheet: Optional[str] = None,
    currency: Optional[str] = None,
    admin_region: Optional[str] = None,
) -> RegionDecision:
    """Resolve the application region without treating currency as proof.

    The explicit execution region from the external workflow is authoritative.
    An administrator's prior resolution is only used when that field is absent.
    """

    del currency  # A currency is only a review hint and never decides a region.
    raw_region = str(execution_region).strip() if execution_region is not None else None
    explicit_region = _execution_region(execution_region)
    mapped_sheet_region = sheet_region(source_sheet)
    override = _normalized_token(admin_region)

    if explicit_region:
        return RegionDecision(
            region=explicit_region,
            source="execution_region",
            execution_region_raw=raw_region,
            sheet_region=mapped_sheet_region,
            conflict_reason=(
                f"execution_region={raw_region} 覆盖 Sheet 判定={mapped_sheet_region}"
                if mapped_sheet_region and mapped_sheet_region != explicit_region
                else None
            ),
        )

    if override in {"china", "mexico"}:
        return RegionDecision(
            region=override,
            source="admin_override",
            execution_region_raw=raw_region,
            sheet_region=mapped_sheet_region,
            conflict_reason=(
                f"管理员结论与 Sheet 判定={mapped_sheet_region} 不一致"
                if mapped_sheet_region and mapped_sheet_region != override
                else None
            ),
        )

    if mapped_sheet_region:
        return RegionDecision(
            region=mapped_sheet_region,
            source="sheet_mapping",
            execution_region_raw=raw_region,
            sheet_region=mapped_sheet_region,
        )

    return RegionDecision(
        region="review",
        source="unknown",
        execution_region_raw=raw_region,
        sheet_region=None,
        conflict_reason="缺少可确认的执行地区和 Sheet 映射",
    )


def _request_external_source(row: sqlite3.Row) -> Dict[str, Any]:
    try:
        raw_extra = json.loads(row["raw_extra_json"] or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}
    if not isinstance(raw_extra, dict):
        return {}
    external = raw_extra.get("external_source")
    return external if isinstance(external, dict) else {}


def persist_request_region(
    conn: sqlite3.Connection,
    request_id: int,
    actor_id: Optional[int],
    preserve_admin_override: bool = True,
) -> RegionDecision:
    """Recalculate and persist one request's derived region classification.

    The operation intentionally does not increment the request version: callers
    invoke it inside the same transaction as the business write that already
    owns versioning.  A prior explicit administrator decision remains stable
    unless a future review endpoint deliberately opts out of preservation.
    """

    del actor_id  # Reserved for the explicit review endpoint and its audit log.
    row = conn.execute(
        "SELECT * FROM payment_requests WHERE id = ?",
        (request_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"payment request {request_id} does not exist")

    external = _request_external_source(row)
    existing_source = str(row["region_resolution_source"] or "").strip()
    existing_region = str(row["resolved_region"] or "").strip().lower()
    existing_review = str(row["region_review_status"] or "").strip().lower()
    admin_region = None
    if (
        preserve_admin_override
        and existing_source == "admin_override"
        and existing_review == "resolved"
        and existing_region in {"china", "mexico"}
    ):
        admin_region = existing_region

    decision = resolve_region(
        execution_region=external.get("execution_region"),
        source_sheet=row["source_sheet"],
        currency=row["currency"],
        admin_region=admin_region,
    )
    review_status = "pending" if decision.region == "review" else "resolved"
    if decision.source == "admin_override":
        conn.execute(
            """
            UPDATE payment_requests
            SET resolved_region = ?, region_resolution_source = ?, region_review_status = ?
            WHERE id = ?
            """,
            (decision.region, decision.source, review_status, request_id),
        )
    else:
        conn.execute(
            """
            UPDATE payment_requests
            SET resolved_region = ?, region_resolution_source = ?, region_review_status = ?,
                region_reviewed_by = NULL, region_reviewed_at = NULL
            WHERE id = ?
            """,
            (decision.region, decision.source, review_status, request_id),
        )
    return decision


def backfill_request_regions(
    conn: sqlite3.Connection,
    *,
    append_history: bool = False,
    event_key_prefix: str = "mexico-request-region-backfill",
) -> Dict[str, int]:
    """Classify every existing request and report the migration outcome."""

    from .payable_history import record_request_state

    counts = {
        "china": 0,
        "mexico": 0,
        "review": 0,
        "preserved_override": 0,
        "reclassified": 0,
    }
    rows = conn.execute(
        """
        SELECT id, resolved_region, region_resolution_source, region_review_status
        FROM payment_requests
        ORDER BY id
        """
    ).fetchall()
    for row in rows:
        previous = (
            str(row["resolved_region"] or ""),
            str(row["region_review_status"] or ""),
        )
        was_override = (
            str(row["region_resolution_source"] or "") == "admin_override"
            and str(row["region_review_status"] or "") == "resolved"
            and str(row["resolved_region"] or "") in {"china", "mexico"}
        )
        decision = persist_request_region(conn, int(row["id"]), actor_id=None)
        counts[decision.region] += 1
        if was_override and decision.source == "admin_override":
            counts["preserved_override"] += 1
        current = (
            decision.region,
            "pending" if decision.region == "review" else "resolved",
        )
        if current != previous:
            counts["reclassified"] += 1
            if append_history:
                record_request_state(
                    conn,
                    int(row["id"]),
                    event_type="request.region_reclassified",
                    event_key=f"{event_key_prefix}:{row['id']}:{decision.region}",
                )
    return counts


def _stable_json(value: Any) -> str:
    return json.dumps(value if value is not None else [], ensure_ascii=False, sort_keys=True)


def cache_mexico_discovery_candidates(
    conn: sqlite3.Connection,
    candidates: list[Dict[str, Any]],
    *,
    synced_at: Optional[str] = None,
    manage_transaction: bool = True,
) -> Dict[str, int]:
    """Upsert source discoveries without overwriting an administrator decision.

    Discovery happens against PostgreSQL before this function is called.  The
    caller can combine this write with workflow caching by passing
    ``manage_transaction=False`` inside one short ``BEGIN IMMEDIATE`` block.
    """

    timestamp = synced_at or _now_iso()
    summary = {"inserted": 0, "updated": 0, "unchanged": 0}
    if manage_transaction:
        conn.execute("BEGIN IMMEDIATE")
    try:
        for candidate in candidates:
            approval_no = str(candidate.get("approval_no") or "").strip()
            if not approval_no:
                continue
            existing = conn.execute(
                "SELECT * FROM mexico_approval_tracking WHERE approval_no = ?",
                (approval_no,),
            ).fetchone()
            preserve_override = bool(
                existing is not None
                and str(existing["region_resolution_source"] or "") == "admin_override"
                and str(existing["region_review_status"] or "") == "resolved"
                and str(existing["resolved_region"] or "") in {"china", "mexico"}
            )
            resolved_region = (
                str(existing["resolved_region"])
                if preserve_override
                else str(candidate.get("resolved_region") or "review")
            )
            resolution_source = (
                "admin_override"
                if preserve_override
                else str(candidate.get("region_resolution_source") or "unknown")
            )
            review_status = (
                "resolved"
                if preserve_override
                else ("pending" if resolved_region == "review" else "resolved")
            )
            raw_summary = {
                "source_conflict": bool(candidate.get("source_conflict")),
                "warnings": list(candidate.get("warnings") or []),
                "errors": list(candidate.get("errors") or []),
                "raw_summary": candidate.get("raw_summary") or {},
                "raw_candidates": candidate.get("raw_candidates") or [],
            }
            values = {
                "source_type": str(candidate.get("source_type") or "unknown"),
                "source_record_id": candidate.get("source_record_id")
                or candidate.get("source_id"),
                "process_code": candidate.get("process_code"),
                "process_instance_id": candidate.get("process_instance_id"),
                "raw_execution_region": candidate.get("raw_execution_region"),
                "resolved_region": resolved_region,
                "region_resolution_source": resolution_source,
                "region_review_status": review_status,
                "region_conflict_reason": candidate.get("region_conflict_reason"),
                "request_date": candidate.get("request_date"),
                "applicant_id": candidate.get("applicant_id"),
                "applicant_name": candidate.get("applicant_name"),
                "applicant_department": candidate.get("applicant_department"),
                "company_name": candidate.get("company_name"),
                "source_sheet": candidate.get("source_sheet"),
                "summary": candidate.get("summary"),
                "amount": candidate.get("amount"),
                "currency": candidate.get("currency"),
                "workflow_status": str(candidate.get("workflow_status") or "").upper(),
                "workflow_result": str(candidate.get("workflow_result") or "").lower(),
                "source_updated_at": candidate.get("source_updated_at"),
                "raw_summary_json": json.dumps(
                    raw_summary, ensure_ascii=False, sort_keys=True, default=str
                ),
            }
            if existing is None:
                conn.execute(
                    """
                    INSERT INTO mexico_approval_tracking (
                        approval_no, source_type, source_record_id, process_code,
                        process_instance_id, raw_execution_region, resolved_region,
                        region_resolution_source, region_review_status,
                        region_conflict_reason, request_date, applicant_id,
                        applicant_name, applicant_department, company_name,
                        source_sheet, summary, amount, currency, workflow_status,
                        workflow_result, source_updated_at, last_synced_at,
                        raw_summary_json, created_at, updated_at
                    ) VALUES (
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                        ?, ?, ?, ?, ?, ?
                    )
                    """,
                    (
                        approval_no,
                        *[
                            values[key]
                            for key in values
                            if key != "raw_summary_json"
                        ],
                        timestamp,
                        values["raw_summary_json"],
                        timestamp,
                        timestamp,
                    ),
                )
                summary["inserted"] += 1
                continue

            changed = any(existing[key] != value for key, value in values.items())
            if not changed:
                conn.execute(
                    "UPDATE mexico_approval_tracking SET last_synced_at = ? WHERE approval_no = ?",
                    (timestamp, approval_no),
                )
                summary["unchanged"] += 1
                continue
            conn.execute(
                """
                UPDATE mexico_approval_tracking
                SET source_type = ?, source_record_id = ?, process_code = ?,
                    process_instance_id = ?, raw_execution_region = ?,
                    resolved_region = ?, region_resolution_source = ?,
                    region_review_status = ?, region_conflict_reason = ?,
                    request_date = ?, applicant_id = ?, applicant_name = ?,
                    applicant_department = ?, company_name = ?, source_sheet = ?,
                    summary = ?, amount = ?, currency = ?, workflow_status = ?,
                    workflow_result = ?, source_updated_at = ?, raw_summary_json = ?,
                    last_synced_at = ?, version = version + 1, updated_at = ?
                WHERE approval_no = ?
                """,
                (*values.values(), timestamp, timestamp, approval_no),
            )
            summary["updated"] += 1
        if manage_transaction:
            conn.commit()
    except Exception:
        if manage_transaction:
            conn.rollback()
        raise
    return summary


def cache_mexico_workflow_snapshots(
    conn: sqlite3.Connection,
    workflows: list[Dict[str, Any]],
    *,
    synced_at: Optional[str] = None,
    manage_transaction: bool = True,
) -> Dict[str, int]:
    """Persist externally fetched workflow state in one short SQLite transaction.

    PostgreSQL reads must finish before calling this function. Events remain in
    the local history even when DingTalk later omits them; only their current
    marker is refreshed. Replaying an identical snapshot does not increment the
    tracking version or duplicate links/events.
    """

    timestamp = synced_at or _now_iso()
    summary = {
        "workflows_changed": 0,
        "events_added": 0,
        "events_updated": 0,
        "links_added": 0,
        "links_removed": 0,
    }
    try:
        if manage_transaction:
            conn.execute("BEGIN IMMEDIATE")
        for workflow in workflows:
            approval_no = str(workflow.get("approval_no") or "").strip()
            if not approval_no:
                continue
            tracking = conn.execute(
                "SELECT * FROM mexico_approval_tracking WHERE approval_no = ?",
                (approval_no,),
            ).fetchone()
            if tracking is None:
                continue

            request_ids = [
                int(row["id"])
                for row in conn.execute(
                    """
                    SELECT id
                    FROM payment_requests
                    WHERE TRIM(COALESCE(dingding_id, '')) = ?
                    ORDER BY id
                    """,
                    (approval_no,),
                ).fetchall()
            ]
            desired_links = set(request_ids)
            existing_links = {
                int(row["request_id"])
                for row in conn.execute(
                    "SELECT request_id FROM mexico_approval_request_links WHERE approval_no = ?",
                    (approval_no,),
                ).fetchall()
            }
            removed_links = existing_links - desired_links
            added_links = desired_links - existing_links
            if removed_links:
                conn.executemany(
                    "DELETE FROM mexico_approval_request_links WHERE approval_no = ? AND request_id = ?",
                    [(approval_no, request_id) for request_id in sorted(removed_links)],
                )
            if added_links:
                conn.executemany(
                    """
                    INSERT INTO mexico_approval_request_links (
                        approval_no, request_id, is_primary, created_at
                    ) VALUES (?, ?, ?, ?)
                    """,
                    [
                        (
                            approval_no,
                            request_id,
                            int(bool(request_ids) and request_id == request_ids[0]),
                            timestamp,
                        )
                        for request_id in sorted(added_links)
                    ],
                )
            if request_ids:
                conn.execute(
                    """
                    UPDATE mexico_approval_request_links
                    SET is_primary = CASE WHEN request_id = ? THEN 1 ELSE 0 END
                    WHERE approval_no = ?
                    """,
                    (request_ids[0], approval_no),
                )
            summary["links_added"] += len(added_links)
            summary["links_removed"] += len(removed_links)

            desired_tasks: Dict[str, Dict[str, Any]] = {}
            for task in workflow.get("current_tasks") or []:
                if not isinstance(task, dict):
                    continue
                task_key = str(task.get("task_key") or "").strip()
                if not task_key:
                    continue
                desired_tasks[task_key] = {
                    "task_id": task.get("task_id"),
                    "activity_id": task.get("activity_id"),
                    "node_name": task.get("node_name"),
                    "approver_id": task.get("approver_id"),
                    "approver_name": task.get("approver_name"),
                    "entered_at": task.get("entered_at"),
                }
            existing_tasks = {
                str(row["task_key"]): row
                for row in conn.execute(
                    "SELECT * FROM mexico_approval_current_tasks WHERE approval_no = ?",
                    (approval_no,),
                ).fetchall()
            }
            stale_task_keys = set(existing_tasks) - set(desired_tasks)
            task_changed = bool(stale_task_keys or set(desired_tasks) - set(existing_tasks))
            if not task_changed:
                task_changed = any(
                    any(existing_tasks[task_key][key] != value for key, value in values.items())
                    for task_key, values in desired_tasks.items()
                )
            if stale_task_keys:
                conn.executemany(
                    "DELETE FROM mexico_approval_current_tasks "
                    "WHERE approval_no = ? AND task_key = ?",
                    [(approval_no, task_key) for task_key in sorted(stale_task_keys)],
                )
            for task_key, values in desired_tasks.items():
                conn.execute(
                    """
                    INSERT INTO mexico_approval_current_tasks (
                        approval_no, task_key, task_id, activity_id, node_name,
                        approver_id, approver_name, entered_at, synced_at,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(approval_no, task_key) DO UPDATE SET
                        task_id = excluded.task_id,
                        activity_id = excluded.activity_id,
                        node_name = excluded.node_name,
                        approver_id = excluded.approver_id,
                        approver_name = excluded.approver_name,
                        entered_at = excluded.entered_at,
                        synced_at = excluded.synced_at,
                        updated_at = excluded.updated_at
                    """,
                    (
                        approval_no,
                        task_key,
                        *values.values(),
                        timestamp,
                        timestamp,
                        timestamp,
                    ),
                )

            workflow_values = {
                "process_instance_id": workflow.get("process_instance_id"),
                "workflow_status": str(workflow.get("status") or "").upper(),
                "workflow_result": str(workflow.get("result") or "").lower(),
                "current_node_name": workflow.get("current_node_name"),
                "current_approver_id": workflow.get("current_approver_id"),
                "current_approver_name": workflow.get("current_approver_name"),
                "current_node_entered_at": workflow.get("current_node_entered_at"),
                "workflow_url": workflow.get("workflow_url"),
                "linked_request_id": request_ids[0] if request_ids else None,
                "source_updated_at": workflow.get("updated_at"),
            }
            workflow_changed = any(
                tracking[key] != value for key, value in workflow_values.items()
            ) or bool(added_links or removed_links or task_changed)
            if workflow_changed:
                conn.execute(
                    """
                    UPDATE mexico_approval_tracking
                    SET process_instance_id = ?, workflow_status = ?, workflow_result = ?,
                        current_node_name = ?, current_approver_id = ?, current_approver_name = ?,
                        current_node_entered_at = ?, workflow_url = ?, linked_request_id = ?,
                        source_updated_at = ?, last_state_synced_at = ?, last_synced_at = ?,
                        version = version + 1, updated_at = ?
                    WHERE approval_no = ?
                    """,
                    (
                        workflow_values["process_instance_id"],
                        workflow_values["workflow_status"],
                        workflow_values["workflow_result"],
                        workflow_values["current_node_name"],
                        workflow_values["current_approver_id"],
                        workflow_values["current_approver_name"],
                        workflow_values["current_node_entered_at"],
                        workflow_values["workflow_url"],
                        workflow_values["linked_request_id"],
                        workflow_values["source_updated_at"],
                        timestamp,
                        timestamp,
                        timestamp,
                        approval_no,
                    ),
                )
                summary["workflows_changed"] += 1
            else:
                conn.execute(
                    """
                    UPDATE mexico_approval_tracking
                    SET last_state_synced_at = ?, last_synced_at = ?
                    WHERE approval_no = ?
                    """,
                    (timestamp, timestamp, approval_no),
                )

            events = [
                event
                for event in workflow.get("events") or []
                if str(event.get("event_key") or "").strip()
            ]
            current_keys = {
                str(event["event_key"])
                for event in events
                if bool(event.get("current"))
            }
            if current_keys:
                placeholders = ",".join("?" for _ in current_keys)
                stale_current = conn.execute(
                    f"""
                    SELECT event_key FROM mexico_approval_events
                    WHERE approval_no = ? AND is_current = 1
                      AND event_key NOT IN ({placeholders})
                    """,
                    (approval_no, *sorted(current_keys)),
                ).fetchall()
            else:
                stale_current = conn.execute(
                    """
                    SELECT event_key FROM mexico_approval_events
                    WHERE approval_no = ? AND is_current = 1
                    """,
                    (approval_no,),
                ).fetchall()
            if stale_current:
                conn.execute(
                    "UPDATE mexico_approval_events SET is_current = 0, updated_at = ? "
                    "WHERE approval_no = ? AND is_current = 1",
                    (timestamp, approval_no),
                )
                summary["events_updated"] += len(stale_current)

            for event in events:
                event_key = str(event["event_key"])
                values = {
                    "process_instance_id": event.get("process_instance_id"),
                    "sequence_index": int(event.get("sequence_index") or 0),
                    "activity_id": event.get("activity_id"),
                    "event_type": event.get("event_type"),
                    "node_name": event.get("stage_name") or event.get("node_name"),
                    "result": event.get("result"),
                    "operator_id": event.get("operator_id"),
                    "operator_name": event.get("operator_name"),
                    "event_time": event.get("event_time"),
                    "comment": event.get("comment"),
                    "images_json": _stable_json(event.get("images")),
                    "attachments_json": _stable_json(event.get("attachments")),
                    "is_current": int(bool(event.get("current"))),
                }
                existing = conn.execute(
                    """
                    SELECT * FROM mexico_approval_events
                    WHERE approval_no = ? AND event_key = ?
                    """,
                    (approval_no, event_key),
                ).fetchone()
                if existing is None:
                    conn.execute(
                        """
                        INSERT INTO mexico_approval_events (
                            approval_no, event_key, process_instance_id, sequence_index,
                            activity_id, event_type, node_name, result, operator_id,
                            operator_name, event_time, comment, images_json,
                            attachments_json, is_current, synced_at, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            approval_no,
                            event_key,
                            *values.values(),
                            timestamp,
                            timestamp,
                            timestamp,
                        ),
                    )
                    summary["events_added"] += 1
                elif any(existing[key] != value for key, value in values.items()):
                    conn.execute(
                        """
                        UPDATE mexico_approval_events
                        SET process_instance_id = ?, sequence_index = ?, activity_id = ?,
                            event_type = ?, node_name = ?, result = ?, operator_id = ?,
                            operator_name = ?, event_time = ?, comment = ?, images_json = ?,
                            attachments_json = ?, is_current = ?, synced_at = ?, updated_at = ?
                        WHERE approval_no = ? AND event_key = ?
                        """,
                        (
                            *values.values(),
                            timestamp,
                            timestamp,
                            approval_no,
                            event_key,
                        ),
                    )
                    summary["events_updated"] += 1
        if manage_transaction:
            conn.commit()
    except Exception:
        if manage_transaction:
            conn.rollback()
        raise
    return summary


def _attachment_items(value: Any) -> list[Dict[str, Any]]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, json.JSONDecodeError):
            return []
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _attachment_value(item: Dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = item.get(key)
        if value not in (None, ""):
            return value
    return None


def _attachment_size(value: Any) -> Optional[int]:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def collect_mexico_attachment_candidates(
    workflows: list[Dict[str, Any]],
    source_attachments: list[Dict[str, Any]],
) -> list[Dict[str, Any]]:
    """Merge workflow and source-table attachments by DingTalk file identity."""

    process_instances = {
        str(workflow.get("approval_no") or "").strip(): str(
            workflow.get("process_instance_id") or ""
        ).strip()
        for workflow in workflows
        if str(workflow.get("approval_no") or "").strip()
    }
    merged: Dict[tuple[str, str], Dict[str, Any]] = {}

    def add(item: Dict[str, Any], *, approval_no: str, event_key: Optional[str] = None) -> None:
        normalized_approval = str(approval_no or "").strip()
        source_file_id = str(
            _attachment_value(
                item,
                "source_file_id",
                "file_id",
                "fileId",
                "spaceId",
                "attachment_id",
                "id",
            )
            or ""
        ).strip()
        if not normalized_approval or not source_file_id:
            return
        key = (normalized_approval, source_file_id)
        candidate = merged.setdefault(
            key,
            {
                "approval_no": normalized_approval,
                "process_instance_id": process_instances.get(normalized_approval, ""),
                "source_file_id": source_file_id,
                "file_id": source_file_id,
                "event_key": event_key,
                "file_name": None,
                "mime_type": None,
                "size_bytes": None,
                "source_url": None,
            },
        )
        values = {
            "event_key": event_key or item.get("event_key"),
            "process_instance_id": item.get("process_instance_id")
            or process_instances.get(normalized_approval),
            "file_name": _attachment_value(
                item, "file_name", "fileName", "name", "title"
            ),
            "mime_type": _attachment_value(
                item, "mime_type", "contentType", "file_type", "fileType", "type"
            ),
            "size_bytes": _attachment_size(
                _attachment_value(item, "size_bytes", "file_size", "fileSize", "size")
            ),
            "source_url": _attachment_value(item, "source_url", "downloadUrl", "url"),
        }
        for field, value in values.items():
            if value not in (None, "") and candidate.get(field) in (None, ""):
                candidate[field] = value

    for item in source_attachments:
        add(item, approval_no=str(item.get("approval_no") or ""))
    for workflow in workflows:
        approval_no = str(workflow.get("approval_no") or "").strip()
        for event in workflow.get("events") or []:
            if not isinstance(event, dict):
                continue
            event_key = str(event.get("event_key") or "").strip() or None
            for field in ("images", "attachments"):
                for item in _attachment_items(event.get(field)):
                    add(item, approval_no=approval_no, event_key=event_key)

    return [merged[key] for key in sorted(merged)]


def upsert_mexico_attachment_candidates(
    conn: sqlite3.Connection,
    candidates: list[Dict[str, Any]],
    *,
    synced_at: Optional[str] = None,
    manage_transaction: bool = True,
) -> Dict[str, int]:
    """Register candidates without resetting ready or retryable attachment state."""

    timestamp = synced_at or _now_iso()
    summary = {"inserted": 0, "updated": 0, "existing": 0}
    try:
        if manage_transaction:
            conn.execute("BEGIN IMMEDIATE")
        for candidate in candidates:
            approval_no = str(candidate.get("approval_no") or "").strip()
            source_file_id = str(
                candidate.get("source_file_id") or candidate.get("file_id") or ""
            ).strip()
            if not approval_no or not source_file_id:
                continue
            existing = conn.execute(
                """
                SELECT * FROM mexico_approval_attachments
                WHERE approval_no = ? AND source_file_id = ?
                """,
                (approval_no, source_file_id),
            ).fetchone()
            values = {
                "event_key": candidate.get("event_key"),
                "file_name": candidate.get("file_name"),
                "mime_type": candidate.get("mime_type"),
                "size_bytes": _attachment_size(candidate.get("size_bytes")),
                "source_url": candidate.get("source_url"),
            }
            if existing is None:
                conn.execute(
                    """
                    INSERT INTO mexico_approval_attachments (
                        approval_no, event_key, source_file_id, file_name,
                        mime_type, size_bytes, source_url, status,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)
                    """,
                    (
                        approval_no,
                        values["event_key"],
                        source_file_id,
                        values["file_name"],
                        values["mime_type"],
                        values["size_bytes"],
                        values["source_url"],
                        timestamp,
                        timestamp,
                    ),
                )
                summary["inserted"] += 1
                continue
            # A later inventory source can know less than an earlier one. Keep
            # the richer metadata instead of clearing it during an idempotent
            # rescan of the same DingTalk file.
            values = {
                field: existing[field] if value in (None, "") else value
                for field, value in values.items()
            }
            changed = any(existing[field] != value for field, value in values.items())
            if changed:
                conn.execute(
                    """
                    UPDATE mexico_approval_attachments
                    SET event_key = ?, file_name = ?, mime_type = ?, size_bytes = ?,
                        source_url = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (*values.values(), timestamp, existing["id"]),
                )
                summary["updated"] += 1
            else:
                summary["existing"] += 1
        if manage_transaction:
            conn.commit()
    except Exception:
        if manage_transaction:
            conn.rollback()
        raise
    return summary


def _mexico_attachment_status_summary(
    conn: sqlite3.Connection,
    approval_no: Optional[str] = None,
) -> Dict[str, int | bool]:
    where = ""
    params: tuple[Any, ...] = ()
    if approval_no is not None:
        where = "WHERE approval_no = ?"
        params = (str(approval_no),)
    row = conn.execute(
        f"""
        SELECT COUNT(*) AS total,
               SUM(CASE WHEN status = 'ready' THEN 1 ELSE 0 END) AS ready,
               SUM(CASE WHEN status = 'pending' THEN 1 ELSE 0 END) AS queued,
               SUM(CASE WHEN status = 'downloading' THEN 1 ELSE 0 END) AS downloading,
               SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS failed
        FROM mexico_approval_attachments
        {where}
        """,
        params,
    ).fetchone()
    summary: Dict[str, int | bool] = {
        "total": int(row["total"] or 0),
        "ready": int(row["ready"] or 0),
        "queued": int(row["queued"] or 0),
        "downloading": int(row["downloading"] or 0),
        "failed": int(row["failed"] or 0),
    }
    summary["complete"] = summary["total"] == summary["ready"]
    return summary


def summarize_mexico_attachments(
    conn: sqlite3.Connection,
    approval_no: str,
) -> Dict[str, int | bool]:
    return _mexico_attachment_status_summary(conn, str(approval_no))


def summarize_mexico_attachment_queue(
    conn: sqlite3.Connection,
) -> Dict[str, int]:
    summary = _mexico_attachment_status_summary(conn)
    return {
        key: int(summary[key])
        for key in ("total", "ready", "queued", "downloading", "failed")
    }


def complete_mexico_attachment_run_if_empty(
    conn: sqlite3.Connection,
    run_id: str,
    *,
    now: Optional[str] = None,
    stale_after_seconds: int = 1800,
) -> bool:
    current = datetime.fromisoformat(now) if now else datetime.now(tz=SHANGHAI_TZ)
    if current.tzinfo is None:
        current = current.replace(tzinfo=SHANGHAI_TZ)
    timestamp = current.isoformat(timespec="microseconds")
    stale_before = (current - timedelta(seconds=max(1, stale_after_seconds))).isoformat(
        timespec="microseconds"
    )
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute(
            """
            UPDATE mexico_approval_attachments
            SET status = 'pending', claim_token = NULL, claimed_at = NULL,
                updated_at = ?
            WHERE status = 'downloading'
              AND COALESCE(claimed_at, updated_at) <= ?
            """,
            (timestamp, stale_before),
        )
        outstanding = int(
            conn.execute(
                "SELECT COUNT(*) FROM mexico_approval_attachments "
                "WHERE status IN ('pending', 'downloading')"
            ).fetchone()[0]
        )
        if outstanding:
            conn.commit()
            return False
        result = {"attachments": summarize_mexico_attachment_queue(conn)}
        changed = conn.execute(
            """
            UPDATE mexico_sync_runs
            SET status = 'completed', phase = 'complete', result_json = ?,
                completed_at = ?, heartbeat_at = ?, lease_until = NULL,
                updated_at = ?
            WHERE id = ? AND kind = 'mexico-attachments'
              AND status IN ('queued', 'running')
            """,
            (
                json.dumps(result, ensure_ascii=False, default=str),
                timestamp,
                timestamp,
                timestamp,
                str(run_id),
            ),
        )
        conn.commit()
        return changed.rowcount == 1
    except Exception:
        conn.rollback()
        raise


def prioritize_mexico_attachments(
    conn: sqlite3.Connection,
    approval_no: str,
    *,
    priority: int = 100,
    requested_at: Optional[str] = None,
) -> Dict[str, int | bool]:
    timestamp = requested_at or _now_iso()
    conn.execute(
        """
        UPDATE mexico_approval_attachments
        SET priority = MAX(priority, ?), requested_at = ?,
            status = CASE WHEN status = 'failed' THEN 'pending' ELSE status END,
            last_error = CASE WHEN status = 'failed' THEN NULL ELSE last_error END,
            claim_token = CASE WHEN status = 'failed' THEN NULL ELSE claim_token END,
            claimed_at = CASE WHEN status = 'failed' THEN NULL ELSE claimed_at END,
            updated_at = ?
        WHERE approval_no = ? AND status <> 'ready'
        """,
        (max(0, int(priority)), timestamp, timestamp, str(approval_no)),
    )
    conn.commit()
    return summarize_mexico_attachments(conn, str(approval_no))


def claim_next_mexico_attachment(
    conn: sqlite3.Connection,
    *,
    claim_token: str,
    approval_nos: Optional[Iterable[str]] = None,
    now: Optional[str] = None,
    stale_after_seconds: int = 1800,
) -> Optional[Dict[str, Any]]:
    token = str(claim_token or "").strip()
    if not token:
        raise ValueError("claim_token is required")
    current = datetime.fromisoformat(now) if now else datetime.now(tz=SHANGHAI_TZ)
    if current.tzinfo is None:
        current = current.replace(tzinfo=SHANGHAI_TZ)
    timestamp = current.isoformat(timespec="microseconds")
    stale_before = (current - timedelta(seconds=max(1, stale_after_seconds))).isoformat(
        timespec="microseconds"
    )
    normalized = None
    if approval_nos is not None:
        normalized = sorted(
            {
                str(value or "").strip()
                for value in approval_nos
                if str(value or "").strip()
            }
        )
        if not normalized:
            return None

    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute(
            """
            UPDATE mexico_approval_attachments
            SET status = 'pending', claim_token = NULL, claimed_at = NULL,
                updated_at = ?
            WHERE status = 'downloading'
              AND COALESCE(claimed_at, updated_at) <= ?
            """,
            (timestamp, stale_before),
        )
        filter_sql = ""
        params: list[Any] = []
        if normalized is not None:
            placeholders = ", ".join("?" for _ in normalized)
            filter_sql = f"AND a.approval_no IN ({placeholders})"
            params.extend(normalized)
        row = conn.execute(
            f"""
            SELECT a.id
            FROM mexico_approval_attachments AS a
            JOIN mexico_approval_tracking AS t ON t.approval_no = a.approval_no
            WHERE a.status = 'pending'
              {filter_sql}
            ORDER BY a.priority DESC,
                     CASE WHEN a.requested_at IS NULL OR TRIM(a.requested_at) = '' THEN 1 ELSE 0 END,
                     a.requested_at, a.id
            LIMIT 1
            """,
            params,
        ).fetchone()
        if row is None:
            conn.commit()
            return None
        attachment_id = int(row["id"])
        changed = conn.execute(
            """
            UPDATE mexico_approval_attachments
            SET status = 'downloading', attempts = attempts + 1,
                claim_token = ?, claimed_at = ?, last_error = NULL, updated_at = ?
            WHERE id = ? AND status = 'pending'
            """,
            (token, timestamp, timestamp, attachment_id),
        )
        if changed.rowcount != 1:
            conn.rollback()
            return None
        claimed = conn.execute(
            """
            SELECT a.id AS attachment_id, a.approval_no, a.event_key,
                   a.source_file_id, a.source_file_id AS file_id,
                   a.file_name, a.mime_type, a.size_bytes, a.source_url,
                   a.status, a.attempts, a.priority, a.requested_at,
                   a.claim_token, a.claimed_at, t.process_instance_id
            FROM mexico_approval_attachments AS a
            JOIN mexico_approval_tracking AS t ON t.approval_no = a.approval_no
            WHERE a.id = ?
            """,
            (attachment_id,),
        ).fetchone()
        conn.commit()
        return dict(claimed) if claimed is not None else None
    except Exception:
        conn.rollback()
        raise


def list_mexico_attachment_download_candidates(
    conn: sqlite3.Connection,
    approval_nos: Iterable[str],
    *,
    now: Optional[str] = None,
    stale_after_seconds: int = 1800,
) -> list[Dict[str, Any]]:
    normalized = sorted(
        {str(value or "").strip() for value in approval_nos if str(value or "").strip()}
    )
    if not normalized:
        return []
    current = datetime.fromisoformat(now) if now else datetime.now(tz=SHANGHAI_TZ)
    if current.tzinfo is None:
        current = current.replace(tzinfo=SHANGHAI_TZ)
    stale_before = (current - timedelta(seconds=max(1, stale_after_seconds))).isoformat(
        timespec="microseconds"
    )
    placeholders = ",".join("?" for _ in normalized)
    rows = conn.execute(
        f"""
        SELECT a.id AS attachment_id, a.approval_no, a.event_key,
               a.source_file_id, a.source_file_id AS file_id,
               a.file_name, a.mime_type, a.size_bytes, a.source_url,
               a.status, a.attempts, t.process_instance_id
        FROM mexico_approval_attachments AS a
        JOIN mexico_approval_tracking AS t ON t.approval_no = a.approval_no
        WHERE a.approval_no IN ({placeholders})
          AND (
              a.status IN ('pending', 'failed')
              OR (a.status = 'downloading' AND a.updated_at <= ?)
          )
          AND TRIM(COALESCE(t.process_instance_id, '')) <> ''
        ORDER BY a.approval_no, a.id
        """,
        (*normalized, stale_before),
    ).fetchall()
    return [dict(row) for row in rows]


def mark_mexico_attachments_downloading(
    conn: sqlite3.Connection,
    attachment_ids: list[int],
    *,
    timestamp: Optional[str] = None,
    manage_transaction: bool = True,
) -> None:
    if not attachment_ids:
        return
    changed_at = timestamp or _now_iso()
    try:
        if manage_transaction:
            conn.execute("BEGIN IMMEDIATE")
        conn.executemany(
            """
            UPDATE mexico_approval_attachments
            SET status = 'downloading', attempts = attempts + 1,
                last_error = NULL, updated_at = ?
            WHERE id = ? AND status IN ('pending', 'failed', 'downloading')
            """,
            [(changed_at, int(attachment_id)) for attachment_id in attachment_ids],
        )
        if manage_transaction:
            conn.commit()
    except Exception:
        if manage_transaction:
            conn.rollback()
        raise


def mark_mexico_attachment_failed(
    conn: sqlite3.Connection,
    attachment_id: int,
    error_message: str,
    *,
    claim_token: Optional[str] = None,
    timestamp: Optional[str] = None,
    manage_transaction: bool = True,
) -> None:
    changed_at = timestamp or _now_iso()
    where = "id = ?"
    params: list[Any] = [str(error_message)[:2000], changed_at, int(attachment_id)]
    if claim_token is not None:
        where += " AND claim_token = ?"
        params.append(str(claim_token))
    conn.execute(
        f"""
        UPDATE mexico_approval_attachments
        SET status = 'failed', last_error = ?, claim_token = NULL,
            claimed_at = NULL, updated_at = ?
        WHERE {where}
        """,
        params,
    )
    if manage_transaction:
        conn.commit()


def mark_mexico_attachment_ready(
    conn: sqlite3.Connection,
    attachment_id: int,
    *,
    file_object_id: int,
    claim_token: Optional[str] = None,
    timestamp: Optional[str] = None,
    manage_transaction: bool = True,
) -> None:
    changed_at = timestamp or _now_iso()
    where = "id = ?"
    params: list[Any] = [int(file_object_id), changed_at, int(attachment_id)]
    if claim_token is not None:
        where += " AND claim_token = ?"
        params.append(str(claim_token))
    conn.execute(
        f"""
        UPDATE mexico_approval_attachments
        SET status = 'ready', file_object_id = ?, last_error = NULL,
            claim_token = NULL, claimed_at = NULL, updated_at = ?
        WHERE {where}
        """,
        params,
    )
    if manage_transaction:
        conn.commit()


def _as_shanghai(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=SHANGHAI_TZ)
    return value.astimezone(SHANGHAI_TZ)


def node_age_days(entered_at: datetime, *, now: Optional[datetime] = None) -> int:
    current = _as_shanghai(now or datetime.now(tz=SHANGHAI_TZ))
    entered = _as_shanghai(entered_at)
    return max(0, (current.date() - entered.date()).days)


def warning_level(age_days: int, *, yellow_days: int, red_days: int) -> str:
    if yellow_days < 0 or red_days < 0 or red_days <= yellow_days:
        raise ValueError("red_days must be greater than yellow_days and both must be non-negative")
    if age_days > red_days:
        return "red"
    if age_days > yellow_days:
        return "yellow"
    return "normal"


def build_bilingual_reminder(
    *,
    approval_no: str,
    applicant: str,
    current_node: str,
    current_approver: str,
    age_days: int,
    workflow_url: str,
) -> Dict[str, str]:
    return {
        "zh": (
            f"请协助跟进钉钉审批 {approval_no}。申请人：{applicant}；"
            f"当前节点：{current_node}；当前审批人：{current_approver}；"
            f"已停留 {age_days} 天。流程链接：{workflow_url}"
        ),
        "es": (
            f"Por favor, ayude a dar seguimiento a la solicitud de DingTalk {approval_no}. "
            f"Solicitante: {applicant}; etapa actual: {current_node}; "
            f"responsable actual: {current_approver}; lleva {age_days} días en esta etapa. "
            f"Enlace del flujo: {workflow_url}"
        ),
    }


def _parse_datetime(value: Any) -> Optional[datetime]:
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return _as_shanghai(parsed)


def _mexico_tracking_view_clause(view: str) -> tuple[str, list[Any]]:
    normalized = str(view or "pending").strip().lower()
    if normalized == "pending":
        return (
            "resolved_region = 'mexico' AND region_review_status = 'resolved' "
            "AND UPPER(COALESCE(workflow_status, '')) = 'RUNNING' "
            "AND LOWER(COALESCE(workflow_result, '')) NOT IN ('refuse', 'rejected')",
            [],
        )
    if normalized == "history":
        return (
            "resolved_region = 'mexico' AND region_review_status = 'resolved' "
            "AND (UPPER(COALESCE(workflow_status, '')) <> 'RUNNING' "
            "OR LOWER(COALESCE(workflow_result, '')) IN ('refuse', 'rejected'))",
            [],
        )
    if normalized == "review":
        return (
            "(resolved_region = 'review' OR region_review_status = 'pending')",
            [],
        )
    raise ValueError("view must be pending, history or review")


def _mexico_tracking_where(
    *,
    view: str,
    allowed_sheets: Optional[set[str]] = None,
    participant_name: Optional[str] = None,
    keyword: Optional[str] = None,
    company: Optional[str] = None,
    source_type: Optional[str] = None,
    applicant: Optional[str] = None,
    approver: Optional[str] = None,
    node: Optional[str] = None,
    request_date_from: Optional[str] = None,
    request_date_to: Optional[str] = None,
) -> tuple[str, list[Any]]:
    view_clause, params = _mexico_tracking_view_clause(view)
    clauses = [view_clause]
    if allowed_sheets is not None:
        sheets = sorted(str(item).strip() for item in allowed_sheets if str(item).strip())
        if not sheets:
            clauses.append("0 = 1")
        else:
            placeholders = ", ".join("?" for _ in sheets)
            clauses.append(
                f"COALESCE(NULLIF(TRIM(source_sheet), ''), '未分 Sheet') IN ({placeholders})"
            )
            params.extend(sheets)
    if participant_name is not None:
        identity = str(participant_name).strip()
        if not identity:
            clauses.append("0 = 1")
        else:
            clauses.append(
                "(TRIM(COALESCE(applicant_name, '')) = ? "
                "OR TRIM(COALESCE(current_approver_name, '')) = ? "
                "OR EXISTS ("
                "SELECT 1 FROM mexico_approval_current_tasks participant_task "
                "WHERE participant_task.approval_no = mexico_approval_tracking.approval_no "
                "AND TRIM(COALESCE(participant_task.approver_name, '')) = ?"
                ") OR EXISTS ("
                "SELECT 1 FROM mexico_approval_events participant_event "
                "WHERE participant_event.approval_no = mexico_approval_tracking.approval_no "
                "AND TRIM(COALESCE(participant_event.operator_name, '')) = ?"
                "))"
            )
            params.extend([identity] * 4)
    if keyword and str(keyword).strip():
        token = f"%{str(keyword).strip()}%"
        clauses.append(
            "(" + " OR ".join(
                f"COALESCE({column}, '') LIKE ?"
                for column in (
                    "approval_no",
                    "applicant_name",
                    "summary",
                    "company_name",
                    "source_sheet",
                )
            )
            + " OR EXISTS ("
            "SELECT 1 FROM mexico_approval_current_tasks current_task "
            "WHERE current_task.approval_no = mexico_approval_tracking.approval_no "
            "AND (COALESCE(current_task.approver_name, '') LIKE ? "
            "OR COALESCE(current_task.node_name, '') LIKE ?)"
            "))"
        )
        params.extend([token] * 7)
    for value, column in (
        (company, "company_name"),
        (source_type, "source_type"),
        (applicant, "applicant_name"),
    ):
        if value and str(value).strip():
            clauses.append(f"COALESCE({column}, '') = ?")
            params.append(str(value).strip())
    if approver and str(approver).strip():
        clauses.append(
            "(EXISTS (SELECT 1 FROM mexico_approval_current_tasks current_task "
            "WHERE current_task.approval_no = mexico_approval_tracking.approval_no "
            "AND TRIM(COALESCE(current_task.approver_name, '')) = ?) "
            "OR (NOT EXISTS (SELECT 1 FROM mexico_approval_current_tasks any_task "
            "WHERE any_task.approval_no = mexico_approval_tracking.approval_no) "
            "AND TRIM(COALESCE(mexico_approval_tracking.current_approver_name, '')) = ?))"
        )
        params.extend([str(approver).strip()] * 2)
    if node and str(node).strip():
        clauses.append(
            "(EXISTS (SELECT 1 FROM mexico_approval_current_tasks current_task "
            "WHERE current_task.approval_no = mexico_approval_tracking.approval_no "
            "AND TRIM(COALESCE(current_task.node_name, '')) = ?) "
            "OR (NOT EXISTS (SELECT 1 FROM mexico_approval_current_tasks any_task "
            "WHERE any_task.approval_no = mexico_approval_tracking.approval_no) "
            "AND TRIM(COALESCE(mexico_approval_tracking.current_node_name, '')) = ?))"
        )
        params.extend([str(node).strip()] * 2)
    if request_date_from and str(request_date_from).strip():
        clauses.append("request_date >= ?")
        params.append(str(request_date_from).strip())
    if request_date_to and str(request_date_to).strip():
        clauses.append("request_date <= ?")
        params.append(str(request_date_to).strip())
    return " AND ".join(f"({clause})" for clause in clauses), params


def _mexico_tracking_public(
    row: sqlite3.Row | Dict[str, Any],
    *,
    settings: Dict[str, Any],
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    payload = dict(row)
    entered_at = _parse_datetime(payload.get("current_node_entered_at"))
    age_days = node_age_days(entered_at, now=now) if entered_at is not None else 0
    payload["age_days"] = age_days
    payload["warning_level"] = warning_level(
        age_days,
        yellow_days=int(settings["yellow_days"]),
        red_days=int(settings["red_days"]),
    )
    if (
        str(payload.get("resolved_region") or "") == "mexico"
        and str(payload.get("workflow_status") or "").upper() == "RUNNING"
    ):
        payload["reminder"] = build_bilingual_reminder(
            approval_no=str(payload.get("approval_no") or ""),
            applicant=str(payload.get("applicant_name") or "-"),
            current_node=str(payload.get("current_node_name") or "-"),
            current_approver=str(payload.get("current_approver_name") or "-"),
            age_days=age_days,
            workflow_url=str(payload.get("workflow_url") or ""),
        )
    else:
        payload["reminder"] = None
    payload.pop("raw_summary_json", None)
    return payload


def _load_mexico_current_tasks(
    conn: sqlite3.Connection,
    approval_nos: Iterable[str],
) -> Dict[str, list[Dict[str, Any]]]:
    normalized = list(
        dict.fromkeys(
            str(approval_no).strip()
            for approval_no in approval_nos
            if str(approval_no).strip()
        )
    )
    if not normalized:
        return {}
    placeholders = ", ".join("?" for _ in normalized)
    rows = conn.execute(
        f"""
        SELECT approval_no, task_key, task_id, activity_id, node_name,
               approver_id, approver_name, entered_at
        FROM mexico_approval_current_tasks
        WHERE approval_no IN ({placeholders})
        ORDER BY approval_no,
                 CASE WHEN entered_at IS NULL OR TRIM(entered_at) = '' THEN 1 ELSE 0 END,
                 entered_at, node_name, approver_name, id
        """,
        normalized,
    ).fetchall()
    tasks_by_approval: Dict[str, list[Dict[str, Any]]] = {}
    for row in rows:
        item = dict(row)
        approval_no = str(item.pop("approval_no"))
        tasks_by_approval.setdefault(approval_no, []).append(item)
    return tasks_by_approval


def _attach_mexico_current_tasks(
    payloads: Iterable[Dict[str, Any]],
    tasks_by_approval: Dict[str, list[Dict[str, Any]]],
) -> None:
    for payload in payloads:
        tasks = tasks_by_approval.get(str(payload.get("approval_no") or ""), [])
        payload["current_tasks"] = tasks
        payload["current_approvers"] = list(
            dict.fromkeys(
                str(task["approver_name"])
                for task in tasks
                if task.get("approver_name")
            )
        )
        payload["current_nodes"] = list(
            dict.fromkeys(
                str(task["node_name"])
                for task in tasks
                if task.get("node_name")
            )
        )


def list_mexico_tracking(
    conn: sqlite3.Connection,
    *,
    view: str = "pending",
    page: int = 1,
    page_size: int = 50,
    allowed_sheets: Optional[set[str]] = None,
    participant_name: Optional[str] = None,
    keyword: Optional[str] = None,
    company: Optional[str] = None,
    source_type: Optional[str] = None,
    applicant: Optional[str] = None,
    approver: Optional[str] = None,
    node: Optional[str] = None,
    warning: Optional[str] = None,
    request_date_from: Optional[str] = None,
    request_date_to: Optional[str] = None,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Return a lightweight, permission-filtered Mexico tracking page."""

    current_page = max(1, int(page))
    limit = min(100, max(1, int(page_size)))
    where, params = _mexico_tracking_where(
        view=view,
        allowed_sheets=allowed_sheets,
        participant_name=participant_name,
        keyword=keyword,
        company=company,
        source_type=source_type,
        applicant=applicant,
        approver=approver,
        node=node,
        request_date_from=request_date_from,
        request_date_to=request_date_to,
    )
    settings = get_mexico_tracking_settings(conn)
    rows = conn.execute(
        f"""
        SELECT id, approval_no, source_type, resolved_region,
               region_resolution_source, region_review_status,
               region_conflict_reason, request_date, applicant_name,
               applicant_department, company_name, source_sheet, summary,
               amount, currency, workflow_status, workflow_result,
               current_node_name, current_approver_name,
               current_node_entered_at, workflow_url, last_state_synced_at,
               last_attachment_synced_at, last_synced_at, version, updated_at
        FROM mexico_approval_tracking
        WHERE {where}
        ORDER BY
            CASE WHEN current_node_entered_at IS NULL OR TRIM(current_node_entered_at) = '' THEN 1 ELSE 0 END,
            current_node_entered_at ASC,
            COALESCE(request_date, '') DESC,
            id DESC
        """,
        params,
    ).fetchall()
    items = [
        _mexico_tracking_public(row, settings=settings, now=now)
        for row in rows
    ]
    _attach_mexico_current_tasks(
        items,
        _load_mexico_current_tasks(
            conn,
            (str(item.get("approval_no") or "") for item in items),
        ),
    )
    requested_warning = str(warning or "").strip().lower()
    if requested_warning:
        if requested_warning not in {"normal", "yellow", "red"}:
            raise ValueError("warning must be normal, yellow or red")
        items = [item for item in items if item["warning_level"] == requested_warning]
    total = len(items)
    start = (current_page - 1) * limit
    return {
        "items": items[start : start + limit],
        "total": total,
        "page": current_page,
        "page_size": limit,
        "pages": max(1, (total + limit - 1) // limit),
    }


def summarize_mexico_tracking(
    conn: sqlite3.Connection,
    *,
    allowed_sheets: Optional[set[str]] = None,
    participant_name: Optional[str] = None,
    include_review: bool = True,
    now: Optional[datetime] = None,
) -> Dict[str, int]:
    pending = list_mexico_tracking(
        conn,
        view="pending",
        page=1,
        page_size=100,
        allowed_sheets=allowed_sheets,
        participant_name=participant_name,
        now=now,
    )
    # Summary must not be truncated by the public page-size ceiling.
    pending_items = pending["items"]
    if pending["total"] > len(pending_items):
        where, params = _mexico_tracking_where(
            view="pending",
            allowed_sheets=allowed_sheets,
            participant_name=participant_name,
        )
        settings = get_mexico_tracking_settings(conn)
        rows = conn.execute(
            f"SELECT * FROM mexico_approval_tracking WHERE {where}", params
        ).fetchall()
        pending_items = [
            _mexico_tracking_public(row, settings=settings, now=now) for row in rows
        ]
    history_where, history_params = _mexico_tracking_where(
        view="history",
        allowed_sheets=allowed_sheets,
        participant_name=participant_name,
    )
    review_where, review_params = _mexico_tracking_where(
        view="review",
        allowed_sheets=allowed_sheets,
        participant_name=participant_name,
    )
    counts = {"normal": 0, "yellow": 0, "red": 0}
    for item in pending_items:
        counts[item["warning_level"]] += 1
    counts.update(
        {
            "pending": len(pending_items),
            "history": int(
                conn.execute(
                    f"SELECT COUNT(*) FROM mexico_approval_tracking WHERE {history_where}",
                    history_params,
                ).fetchone()[0]
            ),
            "review": (
                int(
                    conn.execute(
                        f"SELECT COUNT(*) FROM mexico_approval_tracking WHERE {review_where}",
                        review_params,
                    ).fetchone()[0]
                )
                if include_review
                else 0
            ),
        }
    )
    return counts


def summarize_mexico_approvers(
    conn: sqlite3.Connection,
    *,
    allowed_sheets: Optional[set[str]] = None,
    participant_name: Optional[str] = None,
    now: Optional[datetime] = None,
) -> list[Dict[str, Any]]:
    """Aggregate visible pending approvals once per current approver."""

    where, params = _mexico_tracking_where(
        view="pending",
        allowed_sheets=allowed_sheets,
        participant_name=participant_name,
    )
    rows = conn.execute(
        f"""
        SELECT approval_no, current_approver_name, current_node_entered_at
        FROM mexico_approval_tracking
        WHERE {where}
        """,
        params,
    ).fetchall()
    tasks_by_approval = _load_mexico_current_tasks(
        conn,
        (str(row["approval_no"]) for row in rows),
    )
    settings = get_mexico_tracking_settings(conn)
    totals: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        approval_no = str(row["approval_no"])
        tasks = tasks_by_approval.get(approval_no, [])
        approvers = {
            str(task.get("approver_name") or "").strip()
            for task in tasks
            if str(task.get("approver_name") or "").strip()
        }
        if not approvers:
            fallback = str(row["current_approver_name"] or "").strip()
            if fallback:
                approvers.add(fallback)
        entered_at = _parse_datetime(row["current_node_entered_at"])
        age_days = node_age_days(entered_at, now=now) if entered_at is not None else 0
        level = warning_level(
            age_days,
            yellow_days=int(settings["yellow_days"]),
            red_days=int(settings["red_days"]),
        )
        for approver_name in approvers:
            item = totals.setdefault(
                approver_name,
                {
                    "approver_name": approver_name,
                    "pending": 0,
                    "overdue": 0,
                    "severe": 0,
                },
            )
            item["pending"] += 1
            if level in {"yellow", "red"}:
                item["overdue"] += 1
            if level == "red":
                item["severe"] += 1
    return sorted(
        totals.values(),
        key=lambda item: (
            -int(item["severe"]),
            -int(item["overdue"]),
            -int(item["pending"]),
            str(item["approver_name"]),
        ),
    )


def mexico_tracking_filter_options(
    conn: sqlite3.Connection,
    *,
    allowed_sheets: Optional[set[str]] = None,
    participant_name: Optional[str] = None,
) -> Dict[str, list[str]]:
    where, params = _mexico_tracking_where(
        view="pending",
        allowed_sheets=allowed_sheets,
        participant_name=participant_name,
    )
    rows = conn.execute(
        f"""
        SELECT approval_no, company_name, source_sheet, source_type, applicant_name,
               current_approver_name, current_node_name
        FROM mexico_approval_tracking
        WHERE {where}
        """,
        params,
    ).fetchall()

    def distinct(key: str) -> list[str]:
        return sorted(
            {
                str(row[key]).strip()
                for row in rows
                if row[key] is not None and str(row[key]).strip()
            }
        )

    tasks_by_approval = _load_mexico_current_tasks(
        conn,
        (str(row["approval_no"]) for row in rows),
    )
    approvers: set[str] = set()
    nodes: set[str] = set()
    for row in rows:
        approval_no = str(row["approval_no"])
        tasks = tasks_by_approval.get(approval_no, [])
        if tasks:
            approvers.update(
                str(task["approver_name"]).strip()
                for task in tasks
                if task.get("approver_name") and str(task["approver_name"]).strip()
            )
            nodes.update(
                str(task["node_name"]).strip()
                for task in tasks
                if task.get("node_name") and str(task["node_name"]).strip()
            )
        else:
            if row["current_approver_name"] and str(row["current_approver_name"]).strip():
                approvers.add(str(row["current_approver_name"]).strip())
            if row["current_node_name"] and str(row["current_node_name"]).strip():
                nodes.add(str(row["current_node_name"]).strip())

    return {
        "companies": distinct("company_name"),
        "sheets": distinct("source_sheet"),
        "source_types": distinct("source_type"),
        "applicants": distinct("applicant_name"),
        "approvers": sorted(approvers),
        "nodes": sorted(nodes),
    }


def get_mexico_tracking_detail(
    conn: sqlite3.Connection,
    tracking_id: int,
    *,
    allowed_sheets: Optional[set[str]] = None,
    participant_name: Optional[str] = None,
    allow_review: bool = True,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    row = conn.execute(
        "SELECT * FROM mexico_approval_tracking WHERE id = ?", (int(tracking_id),)
    ).fetchone()
    if row is None:
        raise KeyError(f"Mexico tracking item {tracking_id} does not exist")
    if (
        not allow_review
        and (
            str(row["resolved_region"] or "") == "review"
            or str(row["region_review_status"] or "") == "pending"
        )
    ):
        raise PermissionError("review item is restricted")
    if allowed_sheets is not None and str(row["source_sheet"] or "").strip() not in allowed_sheets:
        raise PermissionError("sheet access denied")
    if participant_name is not None:
        identity = str(participant_name).strip()
        is_participant = bool(identity) and (
            str(row["applicant_name"] or "").strip() == identity
            or str(row["current_approver_name"] or "").strip() == identity
            or conn.execute(
                """
                SELECT 1 FROM mexico_approval_current_tasks
                WHERE approval_no = ? AND TRIM(COALESCE(approver_name, '')) = ?
                LIMIT 1
                """,
                (row["approval_no"], identity),
            ).fetchone()
            is not None
            or conn.execute(
                """
                SELECT 1 FROM mexico_approval_events
                WHERE approval_no = ? AND TRIM(COALESCE(operator_name, '')) = ?
                LIMIT 1
                """,
                (row["approval_no"], identity),
            ).fetchone()
            is not None
        )
        if not is_participant:
            raise PermissionError("participant access denied")
    settings = get_mexico_tracking_settings(conn)
    payload = _mexico_tracking_public(row, settings=settings, now=now)
    _attach_mexico_current_tasks(
        [payload],
        _load_mexico_current_tasks(conn, [str(row["approval_no"])]),
    )
    events = conn.execute(
        """
        SELECT * FROM mexico_approval_events
        WHERE approval_no = ?
        ORDER BY CASE WHEN event_time IS NULL OR TRIM(event_time) = '' THEN 1 ELSE 0 END,
                 event_time, sequence_index, id
        """,
        (row["approval_no"],),
    ).fetchall()
    payload["events"] = []
    for event in events:
        item = dict(event)
        for key in ("images_json", "attachments_json"):
            try:
                item[key.removesuffix("_json")] = json.loads(item.get(key) or "[]")
            except (TypeError, json.JSONDecodeError):
                item[key.removesuffix("_json")] = []
            item.pop(key, None)
        payload["events"].append(item)
    attachment_rows = conn.execute(
        """
        SELECT id, event_key, source_file_id, file_name, mime_type, size_bytes,
               status, attempts, last_error, file_object_id, created_at, updated_at
        FROM mexico_approval_attachments
        WHERE approval_no = ? ORDER BY id
        """,
        (row["approval_no"],),
    ).fetchall()
    payload["attachments"] = []
    for attachment in attachment_rows:
        item = dict(attachment)
        item["content_url"] = (
            f"/api/mexico-tracking/{tracking_id}/attachments/{item['id']}/content"
            if item["status"] == "ready" and item["file_object_id"]
            else None
        )
        payload["attachments"].append(item)
    payload["attachment_status"] = summarize_mexico_attachments(
        conn,
        str(row["approval_no"]),
    )
    payload["linked_requests"] = [
        dict(linked)
        for linked in conn.execute(
            """
            SELECT payment_requests.id, payment_requests.batch_id,
                   request_batches.name AS batch_name, payment_requests.dingding_id,
                   payment_requests.source_sheet, payment_requests.summary,
                   payment_requests.payment_status, payment_requests.amount,
                   payment_requests.paid_amount, payment_requests.pending_amount,
                   payment_requests.currency, mexico_approval_request_links.is_primary
            FROM mexico_approval_request_links
            JOIN payment_requests
              ON payment_requests.id = mexico_approval_request_links.request_id
            JOIN request_batches ON request_batches.id = payment_requests.batch_id
            WHERE mexico_approval_request_links.approval_no = ?
            ORDER BY mexico_approval_request_links.is_primary DESC, payment_requests.id
            """,
            (row["approval_no"],),
        ).fetchall()
    ]
    return payload


def resolve_mexico_tracking_region(
    conn: sqlite3.Connection,
    tracking_id: int,
    *,
    region: str,
    expected_version: int,
    actor_id: Optional[int],
) -> Dict[str, Any]:
    target = str(region or "").strip().lower()
    if target not in {"china", "mexico"}:
        raise ValueError("region must be china or mexico")
    timestamp = _now_iso()
    conn.execute("BEGIN IMMEDIATE")
    try:
        current = conn.execute(
            "SELECT * FROM mexico_approval_tracking WHERE id = ?", (int(tracking_id),)
        ).fetchone()
        if current is None:
            raise KeyError(f"Mexico tracking item {tracking_id} does not exist")
        if int(current["version"] or 1) != int(expected_version):
            raise ValueError(
                f"VERSION_CONFLICT: current={int(current['version'] or 1)} expected={int(expected_version)}"
            )
        changed = conn.execute(
            """
            UPDATE mexico_approval_tracking
            SET resolved_region = ?, region_resolution_source = 'admin_override',
                region_review_status = 'resolved', region_reviewed_by = ?,
                region_reviewed_at = ?, version = version + 1, updated_at = ?
            WHERE id = ? AND version = ?
            """,
            (target, actor_id, timestamp, timestamp, int(tracking_id), int(expected_version)),
        )
        if changed.rowcount != 1:
            raise ValueError("VERSION_CONFLICT")
        from .db import write_audit

        write_audit(
            conn,
            actor_id,
            "mexico.region_resolve",
            "mexico_approval_tracking",
            entity_id=int(tracking_id),
            old_value={
                "resolved_region": current["resolved_region"],
                "region_review_status": current["region_review_status"],
                "version": current["version"],
            },
            new_value={
                "resolved_region": target,
                "region_review_status": "resolved",
                "version": int(expected_version) + 1,
            },
        )
        updated = conn.execute(
            "SELECT * FROM mexico_approval_tracking WHERE id = ?", (int(tracking_id),)
        ).fetchone()
        conn.commit()
        return _mexico_tracking_public(
            updated, settings=get_mexico_tracking_settings(conn)
        )
    except Exception:
        conn.rollback()
        raise
