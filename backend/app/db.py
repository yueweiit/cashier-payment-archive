from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, Optional, Union

from .security import hash_password
from .sheet_names import canonical_sheet_name, canonical_sheet_order


ROOT_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = Path(os.environ.get("PAYMENT_APP_DATA_DIR", ROOT_DIR / "data"))
DB_PATH = Path(os.environ.get("PAYMENT_APP_DB", DATA_DIR / "app.db"))
USER_ROLES = ("business", "finance", "general_manager", "admin")
LEGACY_ROLE_MAP = {
    "cashier": "finance",
    "finance": "finance",
    "manager": "general_manager",
    "admin": "admin",
}


def now_iso() -> str:
    return datetime.now().isoformat(timespec="microseconds")


def connect() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=15.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 15000")
    conn.execute("PRAGMA synchronous = FULL")
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def backup_database(destination: Union[Path, str]) -> Dict[str, Any]:
    """Create and verify a consistent SQLite backup, including committed WAL pages."""
    target = Path(destination).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
    try:
        with connect() as source, sqlite3.connect(temporary) as backup:
            source.backup(backup)
            backup.execute("PRAGMA journal_mode = DELETE")
            backup.execute("PRAGMA synchronous = FULL")
            result = backup.execute("PRAGMA integrity_check").fetchone()
            if not result or str(result[0]).lower() != "ok":
                raise RuntimeError(f"SQLite 备份完整性校验失败: {result[0] if result else '无结果'}")
        digest = hashlib.sha256()
        with temporary.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        os.replace(temporary, target)
        return {
            "path": str(target),
            "sha256": digest.hexdigest(),
            "size": target.stat().st_size,
            "created_at": now_iso(),
        }
    finally:
        temporary.unlink(missing_ok=True)


def row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
    data = dict(row)
    for key in ("raw_extra_json", "sheet_order_json", "errors_json", "mapping_json", "old_value_json", "new_value_json"):
        if key in data and data[key]:
            try:
                data[key.replace("_json", "")] = json.loads(data[key])
            except json.JSONDecodeError:
                data[key.replace("_json", "")] = data[key]
    return data


def rows_to_dicts(rows: Iterable[sqlite3.Row]) -> list[Dict[str, Any]]:
    return [row_to_dict(row) for row in rows]


def init_db() -> None:
    with connect() as conn:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL CHECK(role IN ('business','finance','general_manager','admin')),
                display_name TEXT NOT NULL,
                active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                deleted_at TEXT,
                deleted_by INTEGER REFERENCES users(id)
            );

            CREATE TABLE IF NOT EXISTS sessions (
                token TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                created_at TEXT NOT NULL,
                expires_at TEXT
            );

            CREATE TABLE IF NOT EXISTS user_sheet_permissions (
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                sheet_name TEXT NOT NULL,
                created_by INTEGER REFERENCES users(id),
                created_at TEXT NOT NULL,
                PRIMARY KEY (user_id, sheet_name)
            );

            CREATE INDEX IF NOT EXISTS idx_user_sheet_permissions_sheet
            ON user_sheet_permissions(sheet_name, user_id);

            CREATE TABLE IF NOT EXISTS user_ui_preferences (
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                preference_key TEXT NOT NULL,
                value_json TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (user_id, preference_key)
            );

            CREATE TABLE IF NOT EXISTS employee_department_mappings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT,
                employee_name TEXT NOT NULL,
                second_level_department TEXT NOT NULL,
                third_level_department TEXT,
                source_file TEXT,
                source_file_hash TEXT,
                imported_by INTEGER REFERENCES users(id),
                imported_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_employee_department_user_id
            ON employee_department_mappings(user_id);

            CREATE INDEX IF NOT EXISTS idx_employee_department_name
            ON employee_department_mappings(employee_name);

            CREATE TABLE IF NOT EXISTS request_batches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                parent_batch_id INTEGER REFERENCES request_batches(id) ON DELETE SET NULL,
                name TEXT NOT NULL,
                start_date TEXT,
                end_date TEXT,
                status TEXT NOT NULL DEFAULT 'draft' CHECK(status IN ('draft','archived')),
                source_file TEXT,
                sheet_order_json TEXT,
                created_by INTEGER REFERENCES users(id),
                archived_by INTEGER REFERENCES users(id),
                archived_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                version INTEGER NOT NULL DEFAULT 1
            );

            CREATE TABLE IF NOT EXISTS payment_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                batch_id INTEGER NOT NULL REFERENCES request_batches(id) ON DELETE CASCADE,
                copied_from_request_id INTEGER REFERENCES payment_requests(id) ON DELETE SET NULL,
                dingding_id TEXT,
                applicant TEXT,
                payment_account TEXT,
                expense_type TEXT,
                summary TEXT,
                style_name TEXT,
                amount REAL,
                paid_amount REAL,
                pending_amount REAL,
                currency TEXT DEFAULT 'CNY',
                base_amount_cny REAL,
                fx_rate_cny_per_unit REAL,
                fx_rate_date TEXT,
                fx_rate_actual_date TEXT,
                project TEXT,
                bu TEXT,
                payee_account TEXT,
                payee_name TEXT,
                bank_name TEXT,
                invoice_status TEXT,
                needed_payment_date TEXT,
                owner_confirmation TEXT,
                finance_review TEXT,
                finance_manager_approval TEXT,
                general_manager_approval TEXT,
                general_manager_approval_date TEXT,
                general_manager_opinion TEXT,
                actual_payment_date TEXT,
                remark TEXT,
                payment_status TEXT,
                overdue_status TEXT,
                payer TEXT,
                source_sheet TEXT,
                source_row INTEGER,
                content_hash TEXT,
                raw_extra_json TEXT,
                created_by INTEGER REFERENCES users(id),
                updated_by INTEGER REFERENCES users(id),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                version INTEGER NOT NULL DEFAULT 1
            );

            CREATE INDEX IF NOT EXISTS idx_payment_batch ON payment_requests(batch_id);
            CREATE INDEX IF NOT EXISTS idx_payment_hash ON payment_requests(content_hash);
            CREATE INDEX IF NOT EXISTS idx_payment_dingding ON payment_requests(dingding_id);

            CREATE TABLE IF NOT EXISTS payment_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                request_id INTEGER NOT NULL REFERENCES payment_requests(id) ON DELETE CASCADE,
                copied_from_payment_id INTEGER REFERENCES payment_records(id) ON DELETE SET NULL,
                root_payment_id INTEGER,
                amount REAL NOT NULL,
                base_amount_cny REAL,
                fx_rate_cny_per_unit REAL,
                fx_rate_date TEXT,
                fx_rate_actual_date TEXT,
                payment_date TEXT,
                payer TEXT,
                payment_account TEXT,
                bank_reference TEXT,
                remark TEXT,
                source_type TEXT NOT NULL DEFAULT 'manual',
                content_hash TEXT,
                created_by INTEGER REFERENCES users(id),
                updated_by INTEGER REFERENCES users(id),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                version INTEGER NOT NULL DEFAULT 1
            );

            CREATE INDEX IF NOT EXISTS idx_payment_records_request ON payment_records(request_id);
            CREATE INDEX IF NOT EXISTS idx_payment_records_root ON payment_records(root_payment_id);
            CREATE INDEX IF NOT EXISTS idx_payment_records_hash ON payment_records(request_id, content_hash);

            CREATE TABLE IF NOT EXISTS dingtalk_workflow_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                request_id INTEGER NOT NULL REFERENCES payment_requests(id) ON DELETE CASCADE,
                event_key TEXT NOT NULL,
                process_instance_id TEXT,
                activity_id TEXT,
                event_type TEXT,
                stage_name TEXT,
                result TEXT,
                operator_id TEXT,
                operator_name TEXT,
                event_time TEXT,
                sequence_index INTEGER NOT NULL DEFAULT 0,
                comment TEXT,
                images_json TEXT,
                attachments_json TEXT,
                trusted_finance INTEGER NOT NULL DEFAULT 0,
                classification TEXT NOT NULL DEFAULT 'ignored',
                classification_reason TEXT,
                payment_record_id INTEGER REFERENCES payment_records(id) ON DELETE SET NULL,
                is_current INTEGER NOT NULL DEFAULT 0,
                active INTEGER NOT NULL DEFAULT 1,
                synced_at TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(request_id, event_key)
            );

            CREATE INDEX IF NOT EXISTS idx_dingtalk_workflow_request_time
                ON dingtalk_workflow_events(request_id, event_time, id);

            CREATE TABLE IF NOT EXISTS payment_vouchers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                payment_id INTEGER NOT NULL REFERENCES payment_records(id) ON DELETE CASCADE,
                label TEXT,
                file_path TEXT NOT NULL,
                original_filename TEXT,
                mime_type TEXT,
                file_size INTEGER,
                created_by INTEGER REFERENCES users(id),
                created_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_payment_vouchers_payment ON payment_vouchers(payment_id);

            CREATE TABLE IF NOT EXISTS attachment_links (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                request_id INTEGER NOT NULL REFERENCES payment_requests(id) ON DELETE CASCADE,
                label TEXT,
                url_path TEXT NOT NULL,
                attachment_type TEXT NOT NULL DEFAULT 'link',
                file_path TEXT,
                original_filename TEXT,
                mime_type TEXT,
                file_size INTEGER,
                created_by INTEGER REFERENCES users(id),
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS audit_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                actor_id INTEGER REFERENCES users(id),
                operation_id TEXT,
                action TEXT NOT NULL,
                entity_type TEXT NOT NULL,
                entity_id INTEGER,
                batch_id INTEGER REFERENCES request_batches(id) ON DELETE SET NULL,
                old_value_json TEXT,
                new_value_json TEXT,
                reason TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS import_jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                kind TEXT NOT NULL,
                filename TEXT NOT NULL,
                status TEXT NOT NULL,
                total_rows INTEGER NOT NULL DEFAULT 0,
                imported_rows INTEGER NOT NULL DEFAULT 0,
                duplicate_rows INTEGER NOT NULL DEFAULT 0,
                errors_json TEXT,
                mapping_json TEXT,
                batch_id INTEGER REFERENCES request_batches(id) ON DELETE SET NULL,
                created_by INTEGER REFERENCES users(id),
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS batch_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                batch_id INTEGER NOT NULL REFERENCES request_batches(id) ON DELETE CASCADE,
                snapshot_type TEXT NOT NULL CHECK(snapshot_type IN ('baseline','pre_restore')),
                token TEXT NOT NULL UNIQUE,
                payload_json TEXT NOT NULL,
                created_by INTEGER REFERENCES users(id),
                created_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_batch_snapshots_batch_type
                ON batch_snapshots(batch_id, snapshot_type, created_at);

            CREATE TABLE IF NOT EXISTS dictionaries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                kind TEXT NOT NULL,
                value TEXT NOT NULL,
                active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                UNIQUE(kind, value)
            );

            CREATE TABLE IF NOT EXISTS import_mappings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                mapping_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS schema_migrations (
                key TEXT PRIMARY KEY,
                applied_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS batch_operations (
                id TEXT PRIMARY KEY,
                batch_id INTEGER NOT NULL REFERENCES request_batches(id) ON DELETE CASCADE,
                operation_type TEXT NOT NULL,
                actor_id INTEGER REFERENCES users(id),
                status TEXT NOT NULL CHECK(status IN ('running','succeeded','failed','interrupted')),
                lease_expires_at TEXT NOT NULL,
                started_at TEXT NOT NULL,
                heartbeat_at TEXT NOT NULL,
                finished_at TEXT,
                result_json TEXT,
                failure_reason TEXT,
                import_job_id INTEGER REFERENCES import_jobs(id) ON DELETE SET NULL
            );

            CREATE UNIQUE INDEX IF NOT EXISTS idx_batch_operations_one_running
            ON batch_operations(batch_id)
            WHERE status = 'running';

            CREATE INDEX IF NOT EXISTS idx_batch_operations_batch_started
            ON batch_operations(batch_id, started_at DESC);
            """
        )
        migrate_schema(conn)
        seed_admin(conn)
        seed_dictionaries(conn)


def migrate_schema(conn: sqlite3.Connection) -> None:
    migrate_user_roles(conn)
    ensure_column(conn, "users", "deleted_at", "TEXT")
    ensure_column(conn, "users", "deleted_by", "INTEGER REFERENCES users(id)")
    ensure_column(conn, "employee_department_mappings", "third_level_department", "TEXT")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS user_ui_preferences (
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            preference_key TEXT NOT NULL,
            value_json TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (user_id, preference_key)
        )
        """
    )
    ensure_column(conn, "request_batches", "parent_batch_id", "INTEGER REFERENCES request_batches(id) ON DELETE SET NULL")
    ensure_column(conn, "request_batches", "sheet_order_json", "TEXT")
    ensure_column(conn, "request_batches", "version", "INTEGER NOT NULL DEFAULT 1")
    ensure_column(conn, "payment_requests", "copied_from_request_id", "INTEGER REFERENCES payment_requests(id) ON DELETE SET NULL")
    ensure_column(conn, "payment_requests", "version", "INTEGER NOT NULL DEFAULT 1")
    ensure_column(conn, "payment_requests", "applicant", "TEXT")
    ensure_column(conn, "payment_requests", "general_manager_approval_date", "TEXT")
    ensure_column(conn, "payment_requests", "general_manager_opinion", "TEXT")
    ensure_column(conn, "payment_requests", "base_amount_cny", "REAL")
    ensure_column(conn, "payment_requests", "fx_rate_cny_per_unit", "REAL")
    ensure_column(conn, "payment_requests", "fx_rate_date", "TEXT")
    ensure_column(conn, "payment_requests", "fx_rate_actual_date", "TEXT")
    migrate_payment_amounts(conn)
    ensure_column(conn, "audit_logs", "operation_id", "TEXT")
    ensure_column(conn, "attachment_links", "attachment_type", "TEXT NOT NULL DEFAULT 'link'")
    ensure_column(conn, "attachment_links", "file_path", "TEXT")
    ensure_column(conn, "attachment_links", "original_filename", "TEXT")
    ensure_column(conn, "attachment_links", "mime_type", "TEXT")
    ensure_column(conn, "attachment_links", "file_size", "INTEGER")
    ensure_column(conn, "attachment_links", "source_system", "TEXT")
    ensure_column(conn, "attachment_links", "source_attachment_id", "TEXT")
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_attachment_links_external_source
        ON attachment_links(request_id, source_system, source_attachment_id)
        WHERE source_system IS NOT NULL AND source_attachment_id IS NOT NULL
        """
    )
    ensure_batch_snapshots_table(conn)
    migrate_approval_date_values(conn)
    ensure_payment_detail_tables(conn)
    ensure_column(conn, "payment_records", "version", "INTEGER NOT NULL DEFAULT 1")
    ensure_batch_operations_table(conn)
    migrate_currency_amount_anchors(conn)
    ensure_dingtalk_workflow_events_table(conn)
    migrate_payment_summaries_to_details(conn)
    refresh_payment_summaries(conn)
    migrate_role_dictionary(conn)
    migrate_external_department_sheets(conn)
    migrate_sheet_registry_and_names(conn)


def migrate_sheet_registry_and_names(conn: sqlite3.Connection) -> None:
    migration_key = "sheet_registry_and_mould_names_v1"
    if conn.execute("SELECT 1 FROM schema_migrations WHERE key = ?", (migration_key,)).fetchone():
        return

    changed_request_ids: list[int] = []
    request_rows = conn.execute("SELECT id, source_sheet FROM payment_requests ORDER BY id").fetchall()
    for row in request_rows:
        current_name = str(row["source_sheet"] or "").strip()
        canonical_name = canonical_sheet_name(current_name)
        if current_name and canonical_name != current_name:
            conn.execute(
                "UPDATE payment_requests SET source_sheet = ? WHERE id = ?",
                (canonical_name, row["id"]),
            )
            changed_request_ids.append(int(row["id"]))

    if changed_request_ids:
        from .excel_io import content_hash

        for request_id in changed_request_ids:
            request = conn.execute(
                "SELECT * FROM payment_requests WHERE id = ?",
                (request_id,),
            ).fetchone()
            conn.execute(
                "UPDATE payment_requests SET content_hash = ? WHERE id = ?",
                (content_hash(dict(request)), request_id),
            )

    batches = conn.execute(
        "SELECT id, sheet_order_json FROM request_batches ORDER BY id"
    ).fetchall()
    for batch in batches:
        try:
            stored_order = json.loads(batch["sheet_order_json"] or "[]")
        except (TypeError, json.JSONDecodeError):
            stored_order = []
        if not isinstance(stored_order, list):
            stored_order = []
        normalized_order = canonical_sheet_order(stored_order)
        registered = set(normalized_order)
        row_sheets = conn.execute(
            """
            SELECT source_sheet, MIN(id) AS first_id
            FROM payment_requests
            WHERE batch_id = ?
            GROUP BY source_sheet
            ORDER BY first_id
            """,
            (batch["id"],),
        ).fetchall()
        for row in row_sheets:
            sheet_name = canonical_sheet_name(row["source_sheet"])
            if sheet_name not in registered:
                normalized_order.append(sheet_name)
                registered.add(sheet_name)
        conn.execute(
            "UPDATE request_batches SET sheet_order_json = ? WHERE id = ?",
            (json.dumps(normalized_order, ensure_ascii=False), batch["id"]),
        )

    permissions = conn.execute(
        """
        SELECT user_id, sheet_name, created_by, created_at
        FROM user_sheet_permissions
        ORDER BY user_id, sheet_name
        """
    ).fetchall()
    for row in permissions:
        canonical_name = canonical_sheet_name(row["sheet_name"])
        if canonical_name == row["sheet_name"]:
            continue
        conn.execute(
            """
            INSERT OR IGNORE INTO user_sheet_permissions
                (user_id, sheet_name, created_by, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (row["user_id"], canonical_name, row["created_by"], row["created_at"]),
        )
        conn.execute(
            "DELETE FROM user_sheet_permissions WHERE user_id = ? AND sheet_name = ?",
            (row["user_id"], row["sheet_name"]),
        )

    conn.execute(
        "INSERT INTO schema_migrations (key, applied_at) VALUES (?, ?)",
        (migration_key, now_iso()),
    )


def migrate_external_department_sheets(conn: sqlite3.Connection) -> None:
    migration_key = "external_department_sheets_v1"
    if conn.execute("SELECT 1 FROM schema_migrations WHERE key = ?", (migration_key,)).fetchone():
        return
    rows = conn.execute(
        """
        SELECT id, source_sheet, raw_extra_json
        FROM payment_requests
        WHERE source_sheet IN ('运营支出', '采购支出')
        """
    ).fetchall()
    for row in rows:
        try:
            raw_extra = json.loads(row["raw_extra_json"] or "{}")
        except (TypeError, json.JSONDecodeError):
            continue
        external_source = raw_extra.get("external_source") or {}
        if external_source.get("system") != "dingtalk_expense_database":
            continue
        department = str(external_source.get("applicant_department") or "").strip() or "未归属部门"
        conn.execute("UPDATE payment_requests SET source_sheet = ? WHERE id = ?", (department, row["id"]))
    conn.execute(
        "INSERT INTO schema_migrations (key, applied_at) VALUES (?, ?)",
        (migration_key, now_iso()),
    )


def migrate_payment_amounts(conn: sqlite3.Connection) -> None:
    existing = {row["name"] for row in conn.execute("PRAGMA table_info(payment_requests)").fetchall()}
    had_paid_amount = "paid_amount" in existing
    ensure_column(conn, "payment_requests", "paid_amount", "REAL")
    ensure_column(conn, "payment_requests", "pending_amount", "REAL")
    if not had_paid_amount:
        conn.execute(
            """
            UPDATE payment_requests
            SET paid_amount = CASE
                WHEN finance_review = '已付款' AND amount IS NOT NULL THEN amount
                ELSE 0
            END
            """
        )
    else:
        conn.execute("UPDATE payment_requests SET paid_amount = 0 WHERE paid_amount IS NULL")
    conn.execute(
        """
        UPDATE payment_requests
        SET pending_amount = CASE
            WHEN amount IS NULL THEN NULL
            ELSE ROUND(amount - COALESCE(paid_amount, 0), 2)
        END
        """
    )


def ensure_batch_snapshots_table(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS batch_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            batch_id INTEGER NOT NULL REFERENCES request_batches(id) ON DELETE CASCADE,
            snapshot_type TEXT NOT NULL CHECK(snapshot_type IN ('baseline','pre_restore')),
            token TEXT NOT NULL UNIQUE,
            payload_json TEXT NOT NULL,
            created_by INTEGER REFERENCES users(id),
            created_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_batch_snapshots_batch_type
            ON batch_snapshots(batch_id, snapshot_type, created_at);
        """
    )


def ensure_payment_detail_tables(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS payment_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            request_id INTEGER NOT NULL REFERENCES payment_requests(id) ON DELETE CASCADE,
            copied_from_payment_id INTEGER REFERENCES payment_records(id) ON DELETE SET NULL,
            root_payment_id INTEGER,
            amount REAL NOT NULL,
            base_amount_cny REAL,
            fx_rate_cny_per_unit REAL,
            fx_rate_date TEXT,
            fx_rate_actual_date TEXT,
            payment_date TEXT,
            payer TEXT,
            payment_account TEXT,
            bank_reference TEXT,
            remark TEXT,
            source_type TEXT NOT NULL DEFAULT 'manual',
            content_hash TEXT,
            created_by INTEGER REFERENCES users(id),
            updated_by INTEGER REFERENCES users(id),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            version INTEGER NOT NULL DEFAULT 1
        );

        CREATE INDEX IF NOT EXISTS idx_payment_records_request ON payment_records(request_id);
        CREATE INDEX IF NOT EXISTS idx_payment_records_root ON payment_records(root_payment_id);
        CREATE INDEX IF NOT EXISTS idx_payment_records_hash ON payment_records(request_id, content_hash);

        CREATE TABLE IF NOT EXISTS payment_vouchers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            payment_id INTEGER NOT NULL REFERENCES payment_records(id) ON DELETE CASCADE,
            label TEXT,
            file_path TEXT NOT NULL,
            original_filename TEXT,
            mime_type TEXT,
            file_size INTEGER,
            created_by INTEGER REFERENCES users(id),
            created_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_payment_vouchers_payment ON payment_vouchers(payment_id);

        CREATE TABLE IF NOT EXISTS schema_migrations (
            key TEXT PRIMARY KEY,
            applied_at TEXT NOT NULL
        );
        """
    )
    ensure_column(conn, "payment_records", "base_amount_cny", "REAL")
    ensure_column(conn, "payment_records", "fx_rate_cny_per_unit", "REAL")
    ensure_column(conn, "payment_records", "fx_rate_date", "TEXT")
    ensure_column(conn, "payment_records", "fx_rate_actual_date", "TEXT")
    ensure_column(conn, "payment_records", "version", "INTEGER NOT NULL DEFAULT 1")


def ensure_batch_operations_table(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS batch_operations (
            id TEXT PRIMARY KEY,
            batch_id INTEGER NOT NULL REFERENCES request_batches(id) ON DELETE CASCADE,
            operation_type TEXT NOT NULL,
            actor_id INTEGER REFERENCES users(id),
            status TEXT NOT NULL CHECK(status IN ('running','succeeded','failed','interrupted')),
            lease_expires_at TEXT NOT NULL,
            started_at TEXT NOT NULL,
            heartbeat_at TEXT NOT NULL,
            finished_at TEXT,
            result_json TEXT,
            failure_reason TEXT,
            import_job_id INTEGER REFERENCES import_jobs(id) ON DELETE SET NULL
        );

        CREATE UNIQUE INDEX IF NOT EXISTS idx_batch_operations_one_running
        ON batch_operations(batch_id)
        WHERE status = 'running';

        CREATE INDEX IF NOT EXISTS idx_batch_operations_batch_started
        ON batch_operations(batch_id, started_at DESC);
        """
    )


def migrate_currency_amount_anchors(conn: sqlite3.Connection) -> None:
    """Backfill safe CNY anchors without guessing historical foreign currencies."""
    conn.execute(
        """
        UPDATE payment_requests
        SET currency = 'CNY'
        WHERE UPPER(TRIM(COALESCE(currency, ''))) NOT IN ('CNY', 'USD', 'MXN')
        """
    )
    conn.execute(
        """
        UPDATE payment_requests
        SET base_amount_cny = CASE
                WHEN base_amount_cny IS NOT NULL THEN base_amount_cny
                WHEN UPPER(TRIM(COALESCE(currency, 'CNY'))) = 'CNY' THEN amount
                WHEN fx_rate_cny_per_unit IS NOT NULL THEN ROUND(amount * fx_rate_cny_per_unit, 2)
                ELSE NULL
            END,
            fx_rate_cny_per_unit = CASE
                WHEN UPPER(TRIM(COALESCE(currency, 'CNY'))) = 'CNY' THEN 1
                ELSE fx_rate_cny_per_unit
            END
        """
    )
    conn.execute(
        """
        UPDATE payment_records
        SET base_amount_cny = CASE
                WHEN base_amount_cny IS NOT NULL THEN base_amount_cny
                WHEN COALESCE((
                    SELECT UPPER(TRIM(COALESCE(currency, 'CNY')))
                    FROM payment_requests WHERE payment_requests.id = payment_records.request_id
                ), 'CNY') = 'CNY' THEN amount
                WHEN COALESCE(fx_rate_cny_per_unit, (
                    SELECT fx_rate_cny_per_unit
                    FROM payment_requests WHERE payment_requests.id = payment_records.request_id
                )) IS NOT NULL THEN ROUND(amount * COALESCE(fx_rate_cny_per_unit, (
                    SELECT fx_rate_cny_per_unit
                    FROM payment_requests WHERE payment_requests.id = payment_records.request_id
                )), 2)
                ELSE NULL
            END,
            fx_rate_cny_per_unit = COALESCE(fx_rate_cny_per_unit, (
                SELECT fx_rate_cny_per_unit
                FROM payment_requests WHERE payment_requests.id = payment_records.request_id
            )),
            fx_rate_date = COALESCE(fx_rate_date, (
                SELECT fx_rate_date FROM payment_requests WHERE payment_requests.id = payment_records.request_id
            )),
            fx_rate_actual_date = COALESCE(fx_rate_actual_date, (
                SELECT fx_rate_actual_date FROM payment_requests WHERE payment_requests.id = payment_records.request_id
            ))
        """
    )


def ensure_dingtalk_workflow_events_table(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS dingtalk_workflow_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            request_id INTEGER NOT NULL REFERENCES payment_requests(id) ON DELETE CASCADE,
            event_key TEXT NOT NULL,
            process_instance_id TEXT,
            activity_id TEXT,
            event_type TEXT,
            stage_name TEXT,
            result TEXT,
            operator_id TEXT,
            operator_name TEXT,
            event_time TEXT,
            sequence_index INTEGER NOT NULL DEFAULT 0,
            comment TEXT,
            images_json TEXT,
            attachments_json TEXT,
            trusted_finance INTEGER NOT NULL DEFAULT 0,
            classification TEXT NOT NULL DEFAULT 'ignored',
            classification_reason TEXT,
            payment_record_id INTEGER REFERENCES payment_records(id) ON DELETE SET NULL,
            is_current INTEGER NOT NULL DEFAULT 0,
            active INTEGER NOT NULL DEFAULT 1,
            synced_at TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(request_id, event_key)
        );

        CREATE INDEX IF NOT EXISTS idx_dingtalk_workflow_request_time
            ON dingtalk_workflow_events(request_id, event_time, id);
        """
    )
    ensure_column(conn, "dingtalk_workflow_events", "is_current", "INTEGER NOT NULL DEFAULT 0")
    ensure_column(conn, "dingtalk_workflow_events", "sequence_index", "INTEGER NOT NULL DEFAULT 0")


def payment_record_hash(
    request_id: int,
    amount: Any,
    payment_date: Any,
    payer: Any,
    bank_reference: Any,
) -> str:
    parts = [str(request_id), str(amount or ""), str(payment_date or ""), str(payer or ""), str(bank_reference or "")]
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


def migrate_payment_summaries_to_details(conn: sqlite3.Connection) -> None:
    migration_key = "payment_records_from_summary_v1"
    if conn.execute("SELECT 1 FROM schema_migrations WHERE key = ?", (migration_key,)).fetchone():
        return
    rows = conn.execute(
        """
        SELECT id, paid_amount, actual_payment_date, payer, payment_account, created_by, updated_by, created_at, updated_at
        FROM payment_requests
        WHERE COALESCE(paid_amount, 0) > 0
          AND NOT EXISTS (
              SELECT 1 FROM payment_records WHERE payment_records.request_id = payment_requests.id
          )
        ORDER BY id
        """
    ).fetchall()
    for row in rows:
        timestamp = row["updated_at"] or row["created_at"] or now_iso()
        cursor = conn.execute(
            """
            INSERT INTO payment_records (
                request_id, amount, payment_date, payer, payment_account,
                remark, source_type, content_hash, created_by, updated_by, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, 'legacy_migration', ?, ?, ?, ?, ?)
            """,
            (
                row["id"],
                round(float(row["paid_amount"]), 2),
                row["actual_payment_date"],
                row["payer"],
                row["payment_account"],
                "由历史累计已支付金额迁移",
                payment_record_hash(row["id"], row["paid_amount"], row["actual_payment_date"], row["payer"], None),
                row["created_by"],
                row["updated_by"] or row["created_by"],
                row["created_at"] or timestamp,
                timestamp,
            ),
        )
        conn.execute("UPDATE payment_records SET root_payment_id = ? WHERE id = ?", (cursor.lastrowid, cursor.lastrowid))
    conn.execute("INSERT INTO schema_migrations (key, applied_at) VALUES (?, ?)", (migration_key, now_iso()))


def refresh_payment_summaries(
    conn: sqlite3.Connection,
    request_id: Optional[int] = None,
    *,
    bump_version: bool = True,
) -> None:
    if request_id is None:
        requests = conn.execute(
            """
            SELECT id, amount, paid_amount, pending_amount, finance_review, payment_status,
                   actual_payment_date, payer, general_manager_approval, raw_extra_json
            FROM payment_requests
            ORDER BY id
            """
        ).fetchall()
    else:
        requests = conn.execute(
            """
            SELECT id, amount, paid_amount, pending_amount, finance_review, payment_status,
                   actual_payment_date, payer, general_manager_approval, raw_extra_json
            FROM payment_requests
            WHERE id = ?
            """,
            (request_id,),
        ).fetchall()
    for request in requests:
        aggregate = conn.execute(
            "SELECT COALESCE(SUM(amount), 0) AS paid_amount, COUNT(*) AS payment_count FROM payment_records WHERE request_id = ?",
            (request["id"],),
        ).fetchone()
        latest = conn.execute(
            """
            SELECT payment_date, payer FROM payment_records
            WHERE request_id = ?
            ORDER BY CASE WHEN payment_date IS NULL OR TRIM(payment_date) = '' THEN 1 ELSE 0 END,
                     payment_date DESC, id DESC
            LIMIT 1
            """,
            (request["id"],),
        ).fetchone()
        payable = float(request["amount"] or 0)
        paid = round(float(aggregate["paid_amount"] or 0), 2)
        pending = round(payable - paid, 2) if request["amount"] is not None else None
        if paid <= 0:
            finance_review = "未付款"
        elif pending is not None and pending <= 0:
            finance_review = "已付款"
        else:
            finance_review = "部分付款"
        try:
            raw_extra = json.loads(request["raw_extra_json"] or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            raw_extra = {}
        external_source = raw_extra.get("external_source") if isinstance(raw_extra, dict) else {}
        external_status = str((external_source or {}).get("approval_status") or "").strip().upper()
        manager_approval = str(request["general_manager_approval"] or "").strip() or None
        if external_status == "TERMINATED":
            manager_approval = "无需审批"
        else:
            if manager_approval == "无需审批":
                manager_approval = None
            if finance_review == "已付款" and not manager_approval:
                manager_approval = "同意付款"
        latest_date = latest["payment_date"] if latest else None
        latest_payer = latest["payer"] if latest else None
        next_values = (paid, pending, finance_review, finance_review, latest_date, latest_payer, manager_approval)
        current_values = (
            request["paid_amount"], request["pending_amount"], request["finance_review"],
            request["payment_status"], request["actual_payment_date"], request["payer"],
            request["general_manager_approval"],
        )
        if current_values == next_values:
            continue
        version_sql = ", version = version + 1, updated_at = ?" if bump_version else ""
        values: list[Any] = [*next_values]
        if bump_version:
            values.append(now_iso())
        values.append(request["id"])
        conn.execute(
            f"""
            UPDATE payment_requests
            SET paid_amount = ?, pending_amount = ?, finance_review = ?,
                payment_status = ?, actual_payment_date = ?, payer = ?,
                general_manager_approval = ?{version_sql}
            WHERE id = ?
            """,
            values,
        )


def normalize_user_role(role: Optional[str]) -> str:
    return LEGACY_ROLE_MAP.get(str(role or "").strip(), str(role or "").strip() or "business")


def migrate_user_roles(conn: sqlite3.Connection) -> None:
    table_row = conn.execute("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'users'").fetchone()
    if not table_row:
        return
    table_sql = table_row["sql"] or ""
    existing_roles = {row["role"] for row in conn.execute("SELECT DISTINCT role FROM users").fetchall()}
    needs_rebuild = (
        any(role not in USER_ROLES for role in existing_roles)
        or "'cashier'" in table_sql
        or "'manager'" in table_sql
        or any(f"'{role}'" not in table_sql for role in USER_ROLES)
    )
    if not needs_rebuild:
        conn.execute("UPDATE users SET role = 'admin' WHERE username = 'admin'")
        return
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute(
        """
        CREATE TABLE users_new (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL CHECK(role IN ('business','finance','general_manager','admin')),
            display_name TEXT NOT NULL,
            active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            deleted_at TEXT,
            deleted_by INTEGER REFERENCES users(id)
        )
        """
    )
    conn.execute(
        """
        INSERT INTO users_new (id, username, password_hash, role, display_name, active, created_at)
        SELECT id, username, password_hash,
            CASE
                WHEN username = 'admin' THEN 'admin'
                WHEN role = 'cashier' THEN 'finance'
                WHEN role = 'finance' THEN 'finance'
                WHEN role = 'manager' THEN 'general_manager'
                WHEN role = 'admin' THEN 'admin'
                WHEN role = 'business' THEN 'business'
                WHEN role = 'general_manager' THEN 'general_manager'
                ELSE 'business'
            END,
            display_name, active, created_at
        FROM users
        """
    )
    conn.execute("DROP TABLE users")
    conn.execute("ALTER TABLE users_new RENAME TO users")
    conn.execute("UPDATE users SET role = 'admin' WHERE username = 'admin'")
    conn.execute("PRAGMA foreign_keys = ON")


def migrate_role_dictionary(conn: sqlite3.Connection) -> None:
    timestamp = now_iso()
    for role in USER_ROLES:
        conn.execute(
            "INSERT OR IGNORE INTO dictionaries (kind, value, active, created_at) VALUES ('role', ?, 1, ?)",
            (role, timestamp),
        )
        conn.execute("UPDATE dictionaries SET active = 1 WHERE kind = 'role' AND value = ?", (role,))
    placeholders = ", ".join("?" for _ in USER_ROLES)
    conn.execute(f"UPDATE dictionaries SET active = 0 WHERE kind = 'role' AND value NOT IN ({placeholders})", USER_ROLES)


def migrate_approval_date_values(conn: sqlite3.Connection) -> None:
    rows = conn.execute(
        """
        SELECT id, finance_review, actual_payment_date, general_manager_approval, general_manager_approval_date
        FROM payment_requests
        """
    ).fetchall()
    for row in rows:
        updates: Dict[str, Any] = {}
        finance_date = normalize_date_text(row["finance_review"])
        if finance_date:
            if not row["actual_payment_date"]:
                updates["actual_payment_date"] = finance_date
            updates["finance_review"] = None
        manager_date = normalize_date_text(row["general_manager_approval"])
        if manager_date:
            if not row["general_manager_approval_date"]:
                updates["general_manager_approval_date"] = manager_date
            updates["general_manager_approval"] = None
        if not updates:
            continue
        columns = list(updates)
        conn.execute(
            f"UPDATE payment_requests SET {', '.join(f'{column} = ?' for column in columns)} WHERE id = ?",
            [updates[column] for column in columns] + [row["id"]],
        )


def normalize_date_text(value: Optional[str]) -> Optional[str]:
    text = str(value or "").strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d", "%m/%d/%Y"):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            pass
    return None


def ensure_column(conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def seed_admin(conn: sqlite3.Connection) -> None:
    count = conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"]
    if count:
        return
    password = os.environ.get("PAYMENT_APP_ADMIN_PASSWORD", "admin123")
    conn.execute(
        """
        INSERT INTO users (username, password_hash, role, display_name, active, created_at)
        VALUES (?, ?, 'admin', '管理员', 1, ?)
        """,
        ("admin", hash_password(password), now_iso()),
    )


def seed_dictionaries(conn: sqlite3.Connection) -> None:
    defaults = {
        "role": list(USER_ROLES),
        "payment_account": ["私户", "公户", "美金户", "个人卡", "悦为公户", "凌翔公户", "星铭公户"],
        "invoice_status": ["无票", "无发票", "有发票", "专票", "普票", "个人收据"],
        "finance_review": ["未付款", "部分付款", "已付款"],
        "expense_type": ["材料款", "材料款-美金", "辅助材料", "物流", "管理费用", "销售费用", "油漆款", "模具款"],
    }
    for kind, values in defaults.items():
        for value in values:
            conn.execute(
                "INSERT OR IGNORE INTO dictionaries (kind, value, active, created_at) VALUES (?, ?, 1, ?)",
                (kind, value, now_iso()),
            )


def write_audit(
    conn: sqlite3.Connection,
    actor_id: Optional[int],
    action: str,
    entity_type: str,
    entity_id: Optional[int] = None,
    batch_id: Optional[int] = None,
    old_value: Optional[Dict[str, Any]] = None,
    new_value: Optional[Dict[str, Any]] = None,
    reason: Optional[str] = None,
    operation_id: Optional[str] = None,
) -> None:
    conn.execute(
        """
        INSERT INTO audit_logs (
            actor_id, operation_id, action, entity_type, entity_id, batch_id,
            old_value_json, new_value_json, reason, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            actor_id,
            operation_id,
            action,
            entity_type,
            entity_id,
            batch_id,
            json.dumps(old_value, ensure_ascii=False, default=str) if old_value is not None else None,
            json.dumps(new_value, ensure_ascii=False, default=str) if new_value is not None else None,
            reason,
            now_iso(),
        ),
    )
