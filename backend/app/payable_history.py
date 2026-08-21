from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime
from typing import Any, Dict, Optional

from .db import now_iso
from .sheet_names import canonical_sheet_name


def ensure_logical_request_id(conn: sqlite3.Connection, request_id: int) -> int:
    row = conn.execute(
        "SELECT id, copied_from_request_id, logical_request_id FROM payment_requests WHERE id = ?",
        (request_id,),
    ).fetchone()
    if not row:
        raise ValueError(f"payment request {request_id} does not exist")
    if row["logical_request_id"] is not None:
        return int(row["logical_request_id"])

    trail: list[int] = []
    current = row
    root_id = int(row["id"])
    while current:
        current_id = int(current["id"])
        if current_id in trail:
            root_id = min(trail)
            break
        trail.append(current_id)
        if current["logical_request_id"] is not None:
            root_id = int(current["logical_request_id"])
            break
        parent_id = current["copied_from_request_id"]
        if parent_id is None:
            root_id = current_id
            break
        parent = conn.execute(
            "SELECT id, copied_from_request_id, logical_request_id FROM payment_requests WHERE id = ?",
            (parent_id,),
        ).fetchone()
        if not parent:
            root_id = current_id
            break
        current = parent

    for item_id in trail:
        conn.execute(
            "UPDATE payment_requests SET logical_request_id = ? WHERE id = ?",
            (root_id, item_id),
        )
    return root_id


def payment_effective_at(payment_date: Optional[str], *, recorded_at: Optional[str] = None) -> str:
    recorded = recorded_at or now_iso()
    normalized = str(payment_date or "").strip()[:10]
    if not normalized:
        return recorded
    try:
        business_date = date.fromisoformat(normalized)
        recorded_date = datetime.fromisoformat(recorded).date()
    except ValueError:
        return recorded
    if business_date >= recorded_date:
        return recorded
    return f"{business_date.isoformat()}T23:59:59.999999"


def _external_status(row: sqlite3.Row) -> tuple[Optional[str], Optional[str], int]:
    try:
        raw_extra = json.loads(row["raw_extra_json"] or "{}")
    except (TypeError, json.JSONDecodeError):
        raw_extra = {}
    external = raw_extra.get("external_source") or {}
    approval_status = str(
        external.get("approval_status") or external.get("status") or ""
    ).strip() or None
    approval_result = str(
        external.get("approval_result") or external.get("result") or ""
    ).strip() or None
    included = int(
        str(approval_status or "").upper() != "TERMINATED"
        and str(approval_result or "").lower() not in {"refuse", "rejected"}
    )
    return approval_status, approval_result, included


def _state_values(row: sqlite3.Row) -> Dict[str, Any]:
    amount = float(row["amount"] or 0)
    paid_amount = float(row["paid_amount"] or 0)
    pending_amount = float(
        row["pending_amount"] if row["pending_amount"] is not None else amount - paid_amount
    )
    currency = str(row["currency"] or "CNY").upper()
    stored_rate = float(row["fx_rate_cny_per_unit"] or 0)
    if stored_rate <= 0 and amount and row["base_amount_cny"] is not None:
        stored_rate = float(row["base_amount_cny"]) / amount
    if stored_rate <= 0 and currency == "CNY":
        stored_rate = 1.0
    base_amount = (
        float(row["base_amount_cny"])
        if row["base_amount_cny"] is not None
        else amount * stored_rate if stored_rate else None
    )
    approval_status, approval_result, included = _external_status(row)
    return {
        "amount": amount,
        "paid_amount": paid_amount,
        "pending_amount": pending_amount,
        "currency": currency,
        "base_amount_cny": base_amount,
        "base_paid_amount_cny": paid_amount * stored_rate if stored_rate else None,
        "base_pending_amount_cny": pending_amount * stored_rate if stored_rate else None,
        "fx_rate_cny_per_unit": stored_rate or None,
        "approval_status": approval_status,
        "approval_result": approval_result,
        "included": included,
    }


def record_request_state(
    conn: sqlite3.Connection,
    request_id: int,
    *,
    event_type: str,
    event_key: str,
    effective_at: Optional[str] = None,
    actor_id: Optional[int] = None,
    deleted: bool = False,
) -> bool:
    row = conn.execute(
        "SELECT * FROM payment_requests WHERE id = ?",
        (request_id,),
    ).fetchone()
    if not row:
        raise ValueError(f"payment request {request_id} does not exist")
    logical_request_id = ensure_logical_request_id(conn, request_id)
    row = conn.execute("SELECT * FROM payment_requests WHERE id = ?", (request_id,)).fetchone()
    state = _state_values(row)
    timestamp = now_iso()
    cursor = conn.execute(
        """
        INSERT OR IGNORE INTO payable_history_versions (
            logical_request_id, source_request_id, source_batch_id, dingding_id,
            effective_at, recorded_at,
            event_type, event_key, needed_payment_date,
            amount, paid_amount, pending_amount, currency,
            base_amount_cny, base_paid_amount_cny, base_pending_amount_cny,
            fx_rate_cny_per_unit, fx_rate_date, fx_rate_actual_date,
            source_sheet, summary, applicant, approval_status, approval_result,
            included, deleted, created_by
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            logical_request_id,
            request_id,
            row["batch_id"],
            row["dingding_id"],
            effective_at or timestamp,
            timestamp,
            event_type,
            event_key,
            row["needed_payment_date"],
            state["amount"],
            state["paid_amount"],
            state["pending_amount"],
            state["currency"],
            state["base_amount_cny"],
            state["base_paid_amount_cny"],
            state["base_pending_amount_cny"],
            state["fx_rate_cny_per_unit"],
            row["fx_rate_date"],
            row["fx_rate_actual_date"],
            canonical_sheet_name(row["source_sheet"]),
            row["summary"],
            row["applicant"],
            state["approval_status"],
            state["approval_result"],
            0 if deleted else state["included"],
            int(deleted),
            actor_id,
        ),
    )
    return bool(cursor.rowcount)


def seed_history_baseline(conn: sqlite3.Connection, *, start_date: str) -> int:
    before = conn.execute("SELECT COUNT(*) FROM payable_history_versions").fetchone()[0]
    from .db import ensure_daily_payable_history_schema

    conn.execute(
        """
        INSERT INTO app_settings (key, value, updated_at)
        VALUES ('daily_payables_history_start_date', ?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
        """,
        (start_date, now_iso()),
    )
    ensure_daily_payable_history_schema(conn)
    after = conn.execute("SELECT COUNT(*) FROM payable_history_versions").fetchone()[0]
    return int(after - before)
