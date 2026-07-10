from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

from .security import hash_password


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
    return datetime.now().replace(microsecond=0).isoformat()


def connect() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
    data = dict(row)
    for key in ("raw_extra_json", "errors_json", "mapping_json", "old_value_json", "new_value_json"):
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

            CREATE TABLE IF NOT EXISTS request_batches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                parent_batch_id INTEGER REFERENCES request_batches(id) ON DELETE SET NULL,
                name TEXT NOT NULL,
                start_date TEXT,
                end_date TEXT,
                status TEXT NOT NULL DEFAULT 'draft' CHECK(status IN ('draft','archived')),
                source_file TEXT,
                created_by INTEGER REFERENCES users(id),
                archived_by INTEGER REFERENCES users(id),
                archived_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS payment_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                batch_id INTEGER NOT NULL REFERENCES request_batches(id) ON DELETE CASCADE,
                copied_from_request_id INTEGER REFERENCES payment_requests(id) ON DELETE SET NULL,
                dingding_id TEXT,
                payment_account TEXT,
                expense_type TEXT,
                summary TEXT,
                style_name TEXT,
                amount REAL,
                currency TEXT DEFAULT 'CNY',
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
                updated_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_payment_batch ON payment_requests(batch_id);
            CREATE INDEX IF NOT EXISTS idx_payment_hash ON payment_requests(content_hash);
            CREATE INDEX IF NOT EXISTS idx_payment_dingding ON payment_requests(dingding_id);

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
            """
        )
        migrate_schema(conn)
        seed_admin(conn)
        seed_dictionaries(conn)


def migrate_schema(conn: sqlite3.Connection) -> None:
    migrate_user_roles(conn)
    ensure_column(conn, "users", "deleted_at", "TEXT")
    ensure_column(conn, "users", "deleted_by", "INTEGER REFERENCES users(id)")
    ensure_column(conn, "request_batches", "parent_batch_id", "INTEGER REFERENCES request_batches(id) ON DELETE SET NULL")
    ensure_column(conn, "payment_requests", "copied_from_request_id", "INTEGER REFERENCES payment_requests(id) ON DELETE SET NULL")
    ensure_column(conn, "payment_requests", "general_manager_approval_date", "TEXT")
    ensure_column(conn, "payment_requests", "general_manager_opinion", "TEXT")
    ensure_column(conn, "audit_logs", "operation_id", "TEXT")
    ensure_column(conn, "attachment_links", "attachment_type", "TEXT NOT NULL DEFAULT 'link'")
    ensure_column(conn, "attachment_links", "file_path", "TEXT")
    ensure_column(conn, "attachment_links", "original_filename", "TEXT")
    ensure_column(conn, "attachment_links", "mime_type", "TEXT")
    ensure_column(conn, "attachment_links", "file_size", "INTEGER")
    migrate_approval_date_values(conn)
    migrate_role_dictionary(conn)


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
