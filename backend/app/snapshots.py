from __future__ import annotations

import json
import shutil
import sqlite3
import uuid
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

from .db import DATA_DIR, now_iso, payment_record_hash, refresh_payment_summaries


SNAPSHOT_BASELINE = "baseline"
SNAPSHOT_PRE_RESTORE = "pre_restore"
SNAPSHOT_TYPES = {SNAPSHOT_BASELINE, SNAPSHOT_PRE_RESTORE}


def ensure_draft_baselines(conn: sqlite3.Connection) -> int:
    rows = conn.execute(
        """
        SELECT * FROM request_batches
        WHERE status = 'draft'
          AND NOT EXISTS (
              SELECT 1 FROM batch_snapshots
              WHERE batch_snapshots.batch_id = request_batches.id
                AND batch_snapshots.snapshot_type = 'baseline'
          )
        ORDER BY id
        """
    ).fetchall()
    for row in rows:
        create_batch_snapshot(conn, int(row["id"]), SNAPSHOT_BASELINE, row["created_by"], replace_existing=False)
    return len(rows)


def create_batch_snapshot(
    conn: sqlite3.Connection,
    batch_id: int,
    snapshot_type: str,
    actor_id: Optional[int],
    *,
    replace_existing: bool = False,
) -> Dict[str, Any]:
    if snapshot_type not in SNAPSHOT_TYPES:
        raise ValueError("快照类型无效")
    batch = conn.execute("SELECT * FROM request_batches WHERE id = ?", (batch_id,)).fetchone()
    if not batch:
        raise ValueError("批次不存在")
    token = uuid.uuid4().hex
    payload = snapshot_payload(conn, batch_id, token)
    cursor = conn.execute(
        """
        INSERT INTO batch_snapshots (
            batch_id, snapshot_type, token, payload_json, created_by, created_at
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            batch_id,
            snapshot_type,
            token,
            json.dumps(payload, ensure_ascii=False, default=str),
            actor_id,
            now_iso(),
        ),
    )
    snapshot_id = int(cursor.lastrowid)
    if replace_existing:
        old_rows = conn.execute(
            """
            SELECT * FROM batch_snapshots
            WHERE batch_id = ? AND snapshot_type = ? AND id != ?
            """,
            (batch_id, snapshot_type, snapshot_id),
        ).fetchall()
        for old in old_rows:
            cleanup_snapshot_files(old)
        conn.execute(
            "DELETE FROM batch_snapshots WHERE batch_id = ? AND snapshot_type = ? AND id != ?",
            (batch_id, snapshot_type, snapshot_id),
        )
    return snapshot_public(conn.execute("SELECT * FROM batch_snapshots WHERE id = ?", (snapshot_id,)).fetchone())


def latest_baseline_snapshot(conn: sqlite3.Connection, batch_id: int) -> Optional[sqlite3.Row]:
    return conn.execute(
        """
        SELECT * FROM batch_snapshots
        WHERE batch_id = ? AND snapshot_type = 'baseline'
        ORDER BY created_at DESC, id DESC
        LIMIT 1
        """,
        (batch_id,),
    ).fetchone()


def restore_batch_from_baseline(conn: sqlite3.Connection, batch_id: int, actor_id: int) -> Dict[str, Any]:
    batch = conn.execute("SELECT * FROM request_batches WHERE id = ?", (batch_id,)).fetchone()
    if not batch:
        raise ValueError("批次不存在")
    if batch["status"] != "draft":
        raise ValueError("只有草稿批次可以还原到初始状态")
    snapshot = latest_baseline_snapshot(conn, batch_id)
    if not snapshot:
        raise LookupError("当前草稿还没有可用的还原点")

    before_counts = batch_counts(conn, batch_id)
    pre_restore = create_batch_snapshot(conn, batch_id, SNAPSHOT_PRE_RESTORE, actor_id, replace_existing=False)
    payload = json.loads(snapshot["payload_json"])

    current_attachment_rows = conn.execute(
        """
        SELECT attachment_links.* FROM attachment_links
        JOIN payment_requests ON payment_requests.id = attachment_links.request_id
        WHERE payment_requests.batch_id = ?
        """,
        (batch_id,),
    ).fetchall()
    old_file_paths = {row["file_path"] for row in current_attachment_rows if row["file_path"]}
    current_attachment_ids = [int(row["id"]) for row in current_attachment_rows]
    delete_ids(conn, "attachment_links", current_attachment_ids)

    current_voucher_rows = conn.execute(
        """
        SELECT payment_vouchers.* FROM payment_vouchers
        JOIN payment_records ON payment_records.id = payment_vouchers.payment_id
        JOIN payment_requests ON payment_requests.id = payment_records.request_id
        WHERE payment_requests.batch_id = ?
        """,
        (batch_id,),
    ).fetchall()
    old_file_paths.update(row["file_path"] for row in current_voucher_rows if row["file_path"])
    delete_ids(conn, "payment_vouchers", [int(row["id"]) for row in current_voucher_rows])

    baseline_payments = payload.get("payments")
    current_payment_ids = {
        int(row["id"])
        for row in conn.execute(
            """
            SELECT payment_records.id FROM payment_records
            JOIN payment_requests ON payment_requests.id = payment_records.request_id
            WHERE payment_requests.batch_id = ?
            """,
            (batch_id,),
        ).fetchall()
    }
    baseline_payment_ids = {
        int(row["id"])
        for row in (baseline_payments or [])
        if row.get("id") is not None
    }
    delete_ids(conn, "payment_records", sorted(current_payment_ids - baseline_payment_ids))

    request_columns = table_columns(conn, "payment_requests")
    current_request_ids = {
        int(row["id"])
        for row in conn.execute("SELECT id FROM payment_requests WHERE batch_id = ?", (batch_id,)).fetchall()
    }
    baseline_requests = payload.get("requests", [])
    baseline_request_ids = {int(row["id"]) for row in baseline_requests if row.get("id") is not None}
    delete_ids(conn, "payment_requests", sorted(current_request_ids - baseline_request_ids))

    for request in baseline_requests:
        request_id = int(request["id"])
        values = {column: request.get(column) for column in request_columns if column in request}
        if "paid_amount" in request_columns and "paid_amount" not in request:
            values["paid_amount"] = request.get("amount") if request.get("finance_review") == "已付款" else 0
        if "pending_amount" in request_columns and "pending_amount" not in request:
            amount = request.get("amount")
            paid_amount = values.get("paid_amount") or 0
            values["pending_amount"] = round(amount - paid_amount, 2) if amount is not None else None
        values["id"] = request_id
        values["batch_id"] = batch_id
        if request_id in current_request_ids:
            update_columns = [column for column in request_columns if column != "id" and column in values]
            conn.execute(
                f"UPDATE payment_requests SET {', '.join(f'{column} = ?' for column in update_columns)} WHERE id = ?",
                [values[column] for column in update_columns] + [request_id],
            )
        else:
            insert_columns = [column for column in request_columns if column in values]
            placeholders = ", ".join("?" for _ in insert_columns)
            conn.execute(
                f"INSERT INTO payment_requests ({', '.join(insert_columns)}) VALUES ({placeholders})",
                [values[column] for column in insert_columns],
            )

    payment_columns = table_columns(conn, "payment_records")
    for payment in baseline_payments or []:
        payment_id = int(payment["id"])
        values = {column: payment.get(column) for column in payment_columns if column in payment}
        if payment_id in current_payment_ids:
            update_columns = [column for column in payment_columns if column != "id" and column in values]
            conn.execute(
                f"UPDATE payment_records SET {', '.join(f'{column} = ?' for column in update_columns)} WHERE id = ?",
                [values[column] for column in update_columns] + [payment_id],
            )
        else:
            insert_columns = [column for column in payment_columns if column in values]
            placeholders = ", ".join("?" for _ in insert_columns)
            conn.execute(
                f"INSERT INTO payment_records ({', '.join(insert_columns)}) VALUES ({placeholders})",
                [values[column] for column in insert_columns],
            )

    # 兼容付款明细功能上线前创建的草稿快照：把旧累计金额还原为一笔汇总付款。
    if baseline_payments is None:
        restore_legacy_snapshot_payments(conn, baseline_requests, actor_id)

    attachment_columns = table_columns(conn, "attachment_links")
    for attachment in payload.get("attachments", []):
        restore_attachment_file(attachment)
        values = {column: attachment.get(column) for column in attachment_columns if column in attachment}
        insert_columns = [column for column in attachment_columns if column in values]
        placeholders = ", ".join("?" for _ in insert_columns)
        conn.execute(
            f"INSERT INTO attachment_links ({', '.join(insert_columns)}) VALUES ({placeholders})",
            [values[column] for column in insert_columns],
        )

    voucher_columns = table_columns(conn, "payment_vouchers")
    for voucher in payload.get("payment_vouchers", []):
        restore_attachment_file(voucher)
        values = {column: voucher.get(column) for column in voucher_columns if column in voucher}
        insert_columns = [column for column in voucher_columns if column in values]
        placeholders = ", ".join("?" for _ in insert_columns)
        conn.execute(
            f"INSERT INTO payment_vouchers ({', '.join(insert_columns)}) VALUES ({placeholders})",
            [values[column] for column in insert_columns],
        )

    for request_id in baseline_request_ids:
        refresh_payment_summaries(conn, request_id)

    restore_batch_fields(conn, batch_id, payload.get("batch", {}))
    for file_path in old_file_paths:
        delete_data_file_if_unreferenced(conn, file_path)

    return {
        "snapshot": snapshot_public(snapshot),
        "pre_restore_snapshot": pre_restore,
        "before": before_counts,
        "after": batch_counts(conn, batch_id),
    }


def snapshot_payload(conn: sqlite3.Connection, batch_id: int, token: str) -> Dict[str, Any]:
    batch = row_plain(conn.execute("SELECT * FROM request_batches WHERE id = ?", (batch_id,)).fetchone())
    requests = [
        row_plain(row)
        for row in conn.execute(
            "SELECT * FROM payment_requests WHERE batch_id = ? ORDER BY id",
            (batch_id,),
        ).fetchall()
    ]
    attachments = [
        row_plain(row)
        for row in conn.execute(
            """
            SELECT attachment_links.* FROM attachment_links
            JOIN payment_requests ON payment_requests.id = attachment_links.request_id
            WHERE payment_requests.batch_id = ?
            ORDER BY attachment_links.id
            """,
            (batch_id,),
        ).fetchall()
    ]
    payments = [
        row_plain(row)
        for row in conn.execute(
            """
            SELECT payment_records.* FROM payment_records
            JOIN payment_requests ON payment_requests.id = payment_records.request_id
            WHERE payment_requests.batch_id = ?
            ORDER BY payment_records.id
            """,
            (batch_id,),
        ).fetchall()
    ]
    payment_vouchers = [
        row_plain(row)
        for row in conn.execute(
            """
            SELECT payment_vouchers.* FROM payment_vouchers
            JOIN payment_records ON payment_records.id = payment_vouchers.payment_id
            JOIN payment_requests ON payment_requests.id = payment_records.request_id
            WHERE payment_requests.batch_id = ?
            ORDER BY payment_vouchers.id
            """,
            (batch_id,),
        ).fetchall()
    ]
    copy_attachment_files_for_snapshot(batch_id, token, attachments, "attachment")
    copy_attachment_files_for_snapshot(batch_id, token, payment_vouchers, "payment-voucher")
    return {
        "batch": batch,
        "requests": requests,
        "attachments": attachments,
        "payments": payments,
        "payment_vouchers": payment_vouchers,
    }


def copy_attachment_files_for_snapshot(
    batch_id: int,
    token: str,
    attachments: list[Dict[str, Any]],
    prefix: str = "attachment",
) -> None:
    files_dir = DATA_DIR / "snapshots" / str(batch_id) / token / "files"
    for attachment in attachments:
        file_path = attachment.get("file_path")
        if not file_path:
            continue
        source = resolve_data_file(file_path)
        if not source.exists():
            continue
        files_dir.mkdir(parents=True, exist_ok=True)
        target = files_dir / f"{prefix}-{attachment['id']}-{source.name}"
        shutil.copy2(source, target)
        attachment["_snapshot_file_path"] = str(target.relative_to(DATA_DIR))


def restore_attachment_file(attachment: Dict[str, Any]) -> None:
    file_path = attachment.get("file_path")
    snapshot_file_path = attachment.get("_snapshot_file_path")
    if not file_path or not snapshot_file_path:
        return
    source = resolve_data_file(snapshot_file_path)
    if not source.exists():
        raise FileNotFoundError(f"快照附件文件不存在: {snapshot_file_path}")
    target = resolve_data_file(file_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)


def restore_batch_fields(conn: sqlite3.Connection, batch_id: int, snapshot_batch: Dict[str, Any]) -> None:
    batch_columns = table_columns(conn, "request_batches")
    values = {column: snapshot_batch.get(column) for column in batch_columns if column in snapshot_batch and column != "id"}
    values["status"] = "draft"
    values["archived_by"] = None
    values["archived_at"] = None
    values["updated_at"] = now_iso()
    columns = [column for column in batch_columns if column in values and column != "id"]
    conn.execute(
        f"UPDATE request_batches SET {', '.join(f'{column} = ?' for column in columns)} WHERE id = ?",
        [values[column] for column in columns] + [batch_id],
    )


def batch_counts(conn: sqlite3.Connection, batch_id: int) -> Dict[str, Any]:
    request_row = conn.execute(
        "SELECT COUNT(*) AS requests, COALESCE(SUM(amount), 0) AS amount FROM payment_requests WHERE batch_id = ?",
        (batch_id,),
    ).fetchone()
    attachment_row = conn.execute(
        """
        SELECT COUNT(*) AS attachments FROM attachment_links
        JOIN payment_requests ON payment_requests.id = attachment_links.request_id
        WHERE payment_requests.batch_id = ?
        """,
        (batch_id,),
    ).fetchone()
    payment_row = conn.execute(
        """
        SELECT COUNT(DISTINCT payment_records.id) AS payments,
               COUNT(payment_vouchers.id) AS payment_vouchers
        FROM payment_records
        JOIN payment_requests ON payment_requests.id = payment_records.request_id
        LEFT JOIN payment_vouchers ON payment_vouchers.payment_id = payment_records.id
        WHERE payment_requests.batch_id = ?
        """,
        (batch_id,),
    ).fetchone()
    return {
        "requests": int(request_row["requests"]),
        "attachments": int(attachment_row["attachments"]),
        "payments": int(payment_row["payments"]),
        "payment_vouchers": int(payment_row["payment_vouchers"]),
        "amount": request_row["amount"],
    }


def snapshot_public(row: sqlite3.Row | Dict[str, Any]) -> Dict[str, Any]:
    data = row_plain(row)
    try:
        payload = json.loads(data.get("payload_json") or "{}")
    except json.JSONDecodeError:
        payload = {}
    return {
        "id": data["id"],
        "batch_id": data["batch_id"],
        "snapshot_type": data["snapshot_type"],
        "created_by": data.get("created_by"),
        "created_at": data["created_at"],
        "request_count": len(payload.get("requests", [])),
        "attachment_count": len(payload.get("attachments", [])),
        "payment_count": len(payload.get("payments", [])),
        "payment_voucher_count": len(payload.get("payment_vouchers", [])),
    }


def cleanup_batch_snapshot_files(batch_id: int) -> None:
    shutil.rmtree(DATA_DIR / "snapshots" / str(batch_id), ignore_errors=True)


def cleanup_snapshot_files(snapshot: sqlite3.Row | Dict[str, Any]) -> None:
    data = row_plain(snapshot)
    token = data.get("token")
    batch_id = data.get("batch_id")
    if token and batch_id:
        shutil.rmtree(DATA_DIR / "snapshots" / str(batch_id) / str(token), ignore_errors=True)


def delete_ids(conn: sqlite3.Connection, table: str, ids: Iterable[int]) -> None:
    ids = [int(item) for item in ids]
    if not ids:
        return
    placeholders = ", ".join("?" for _ in ids)
    conn.execute(f"DELETE FROM {table} WHERE id IN ({placeholders})", ids)


def delete_data_file_if_unreferenced(conn: sqlite3.Connection, file_path: str) -> None:
    remaining = conn.execute(
        """
        SELECT
            (SELECT COUNT(*) FROM attachment_links WHERE file_path = ?)
          + (SELECT COUNT(*) FROM payment_vouchers WHERE file_path = ?) AS count
        """,
        (file_path, file_path),
    ).fetchone()["count"]
    if remaining:
        return
    path = resolve_data_file(file_path)
    if path.exists():
        path.unlink()


def restore_legacy_snapshot_payments(
    conn: sqlite3.Connection,
    requests: list[Dict[str, Any]],
    actor_id: int,
) -> None:
    for request in requests:
        request_id = int(request["id"])
        amount = round(float(request.get("paid_amount") or 0), 2)
        if amount <= 0:
            continue
        payment_date = request.get("actual_payment_date") or None
        payer = request.get("payer") or None
        bank_reference = None
        cursor = conn.execute(
            """
            INSERT INTO payment_records (
                request_id, amount, payment_date, payer, payment_account,
                bank_reference, remark, source_type, content_hash,
                created_by, created_at, updated_by, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 'snapshot_legacy', ?, ?, ?, ?, ?)
            """,
            (
                request_id,
                amount,
                payment_date,
                payer,
                request.get("payment_account") or None,
                bank_reference,
                "由旧版草稿快照的累计已付金额恢复",
                payment_record_hash(request_id, amount, payment_date, payer, bank_reference),
                actor_id,
                now_iso(),
                actor_id,
                now_iso(),
            ),
        )
        conn.execute(
            "UPDATE payment_records SET root_payment_id = ? WHERE id = ?",
            (cursor.lastrowid, cursor.lastrowid),
        )


def resolve_data_file(relative_path: str) -> Path:
    root = DATA_DIR.resolve()
    path = Path(str(relative_path))
    target = path.resolve() if path.is_absolute() else (DATA_DIR / path).resolve()
    if target != root and root not in target.parents:
        raise ValueError("附件路径无效")
    return target


def table_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()]


def row_plain(row: sqlite3.Row | Dict[str, Any] | None) -> Dict[str, Any]:
    return dict(row or {})
