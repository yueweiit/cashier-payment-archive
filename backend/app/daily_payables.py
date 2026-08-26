from __future__ import annotations

import sqlite3
from collections import defaultdict
from datetime import date, datetime, time, timedelta
from typing import Any, Dict, Iterable, Optional

from .db import get_daily_payables_history_start_date
from .sheet_names import canonical_sheet_name


MAX_TREND_DAYS = 93


class DailyPayablesError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _day_end(value: date) -> str:
    return datetime.combine(value, time.max).isoformat(timespec="microseconds")


def _history_start(conn: sqlite3.Connection) -> date:
    raw = get_daily_payables_history_start_date(conn)
    if not raw:
        raise DailyPayablesError("HISTORY_NOT_INITIALIZED", "每日应付历史尚未初始化")
    return date.fromisoformat(raw)


def _validate_date(conn: sqlite3.Connection, selected: date) -> date:
    history_start = _history_start(conn)
    if selected < history_start:
        raise DailyPayablesError(
            "HISTORY_NOT_AVAILABLE",
            f"每日应付历史从 {history_start.isoformat()} 开始记录",
        )
    return history_start


def _row_dict(row: sqlite3.Row) -> Dict[str, Any]:
    return dict(row)


def _load_events(conn: sqlite3.Connection, end: date) -> list[Dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT *
        FROM payable_history_versions
        WHERE effective_at <= ?
        ORDER BY effective_at, id
        """,
        (_day_end(end),),
    ).fetchall()
    return [_row_dict(row) for row in rows]


def _currency(row: Dict[str, Any]) -> str:
    return str(row.get("currency") or "CNY").strip().upper() or "CNY"


def _number(row: Dict[str, Any], key: str) -> float:
    return float(row.get(key) or 0)


def _base_number(row: Dict[str, Any], base_key: str, raw_key: str) -> float:
    stored = row.get(base_key)
    if stored is not None:
        return float(stored)
    raw = _number(row, raw_key)
    if _currency(row) == "CNY":
        return raw
    rate = float(row.get("fx_rate_cny_per_unit") or 0)
    return raw * rate if rate > 0 else 0.0


def _visible(
    row: Dict[str, Any],
    allowed_sheets: Optional[set[str]],
    *,
    china_only: bool = False,
) -> bool:
    if not int(row.get("included") or 0) or int(row.get("deleted") or 0):
        return False
    if china_only and (
        str(row.get("resolved_region") or "").strip().lower() != "china"
        or str(row.get("region_review_status") or "").strip().lower() != "resolved"
    ):
        return False
    if allowed_sheets is None:
        return True
    return canonical_sheet_name(row.get("source_sheet")) in allowed_sheets


def _empty_totals() -> Dict[str, float]:
    return {
        "due_today": 0.0,
        "paid_today": 0.0,
        "end_pending": 0.0,
        "overdue_pending": 0.0,
    }


def _round_totals(values: Dict[str, float]) -> Dict[str, float]:
    return {key: round(float(value), 2) for key, value in values.items()}


def _payment_event(event_type: Any) -> bool:
    normalized = str(event_type or "").strip().lower()
    return normalized.startswith("payment.") or normalized == "import.rollback_payment"


def _dingtalk_id(value: Any) -> str:
    return str(value or "").strip()


def _state_order(state: Dict[str, Any]) -> tuple[str, int]:
    return str(state.get("effective_at") or ""), int(state.get("id") or 0)


def _deduplicated_states(
    states: Dict[int, Dict[str, Any]],
) -> list[tuple[int, Dict[str, Any]]]:
    grouped: Dict[str, tuple[int, Dict[str, Any]]] = {}
    for logical_id, state in states.items():
        dingding_id = _dingtalk_id(state.get("dingding_id"))
        identity = f"dingtalk:{dingding_id}" if dingding_id else f"logical:{logical_id}"
        current = grouped.get(identity)
        if current is None or _state_order(state) > _state_order(current[1]):
            grouped[identity] = (logical_id, state)
    return list(grouped.values())


def _states_and_payment_deltas(
    events: Iterable[Dict[str, Any]],
) -> tuple[Dict[int, Dict[str, Any]], Dict[tuple[str, int], Dict[str, float]]]:
    states: Dict[int, Dict[str, Any]] = {}
    deltas: Dict[tuple[str, int], Dict[str, float]] = defaultdict(
        lambda: {"paid": 0.0, "base_paid": 0.0}
    )
    for event in events:
        logical_id = int(event["logical_request_id"])
        previous = states.get(logical_id)
        if _payment_event(event.get("event_type")):
            previous_paid = _number(previous, "paid_amount") if previous else 0.0
            previous_base_paid = (
                _base_number(previous, "base_paid_amount_cny", "paid_amount") if previous else 0.0
            )
            paid_delta = _number(event, "paid_amount") - previous_paid
            base_delta = _base_number(event, "base_paid_amount_cny", "paid_amount") - previous_base_paid
            event_date = str(event["effective_at"])[:10]
            deltas[(event_date, logical_id)]["paid"] += paid_delta
            deltas[(event_date, logical_id)]["base_paid"] += base_delta
        states[logical_id] = event
    return states, dict(deltas)


def _snapshot_payload(
    selected: date,
    states: Dict[int, Dict[str, Any]],
    payment_deltas: Dict[tuple[str, int], Dict[str, float]],
    allowed_sheets: Optional[set[str]],
    *,
    include_details: bool,
    china_only: bool = False,
) -> Dict[str, Any]:
    selected_iso = selected.isoformat()
    currency_totals: Dict[str, Dict[str, float]] = defaultdict(_empty_totals)
    base_totals = _empty_totals()
    details: list[Dict[str, Any]] = []
    due_count = 0
    pending_count = 0
    overdue_count = 0

    for logical_id, state in _deduplicated_states(states):
        if not _visible(state, allowed_sheets, china_only=china_only):
            continue
        due_date = str(state.get("needed_payment_date") or "").strip()[:10]
        if not due_date or due_date > selected_iso:
            continue

        currency = _currency(state)
        amount = _number(state, "amount")
        paid = _number(state, "paid_amount")
        pending = max(0.0, _number(state, "pending_amount"))
        base_amount = _base_number(state, "base_amount_cny", "amount")
        base_paid = _base_number(state, "base_paid_amount_cny", "paid_amount")
        base_pending = max(0.0, _base_number(state, "base_pending_amount_cny", "pending_amount"))
        day_delta = payment_deltas.get((selected_iso, logical_id), {})
        paid_today = float(day_delta.get("paid") or 0)
        base_paid_today = float(day_delta.get("base_paid") or 0)

        if due_date == selected_iso:
            due_count += 1
            currency_totals[currency]["due_today"] += amount
            base_totals["due_today"] += base_amount
        if pending > 0:
            pending_count += 1
            currency_totals[currency]["end_pending"] += pending
            base_totals["end_pending"] += base_pending
            if due_date < selected_iso:
                overdue_count += 1
                currency_totals[currency]["overdue_pending"] += pending
                base_totals["overdue_pending"] += base_pending
        if paid_today:
            currency_totals[currency]["paid_today"] += paid_today
            base_totals["paid_today"] += base_paid_today

        if include_details and (due_date == selected_iso or pending > 0):
            details.append(
                {
                    "logical_request_id": logical_id,
                    "source_request_id": state.get("source_request_id"),
                    "source_batch_id": state.get("source_batch_id"),
                    "dingding_id": state.get("dingding_id"),
                    "source_sheet": canonical_sheet_name(state.get("source_sheet")),
                    "applicant": state.get("applicant"),
                    "summary": state.get("summary"),
                    "needed_payment_date": due_date,
                    "amount": round(amount, 2),
                    "paid_amount": round(paid, 2),
                    "paid_today": round(paid_today, 2),
                    "pending_amount": round(pending, 2),
                    "currency": currency,
                    "base_amount_cny": round(base_amount, 2),
                    "base_paid_amount_cny": round(base_paid, 2),
                    "base_pending_amount_cny": round(base_pending, 2),
                    "approval_status": state.get("approval_status"),
                    "approval_result": state.get("approval_result"),
                    "is_due_today": due_date == selected_iso,
                    "is_overdue": due_date < selected_iso and pending > 0,
                }
            )

    details.sort(
        key=lambda item: (
            0 if item["is_overdue"] else 1,
            item["needed_payment_date"],
            item["source_sheet"],
            item["logical_request_id"],
        )
    )
    ordered_currencies = sorted(
        currency_totals,
        key=lambda value: ({"CNY": 0, "USD": 1, "MXN": 2}.get(value, 99), value),
    )
    return {
        "date": selected_iso,
        "totals_cny": _round_totals(base_totals),
        "currency_totals": [
            {"currency": currency, **_round_totals(currency_totals[currency])}
            for currency in ordered_currencies
        ],
        "counts": {
            "due_today": due_count,
            "end_pending": pending_count,
            "overdue_pending": overdue_count,
        },
        "items": details,
    }


def daily_snapshot(
    conn: sqlite3.Connection,
    selected: date,
    *,
    allowed_sheets: Optional[set[str]] = None,
    include_details: bool = False,
    china_only: bool = False,
) -> Dict[str, Any]:
    history_start = _validate_date(conn, selected)
    events = _load_events(conn, selected)
    states, deltas = _states_and_payment_deltas(events)
    result = _snapshot_payload(
        selected,
        states,
        deltas,
        allowed_sheets,
        include_details=include_details,
        china_only=china_only,
    )
    result["history_start_date"] = history_start.isoformat()
    return result


def daily_trend(
    conn: sqlite3.Connection,
    start: date,
    end: date,
    *,
    allowed_sheets: Optional[set[str]] = None,
    china_only: bool = False,
) -> Dict[str, Any]:
    history_start = _validate_date(conn, start)
    if end < start:
        raise DailyPayablesError("INVALID_TREND_RANGE", "趋势结束日期不能早于开始日期")
    day_count = (end - start).days + 1
    if day_count > MAX_TREND_DAYS:
        raise DailyPayablesError(
            "TREND_RANGE_TOO_LARGE",
            f"单次趋势最多查询 {MAX_TREND_DAYS} 天",
        )

    events = _load_events(conn, end)
    states: Dict[int, Dict[str, Any]] = {}
    deltas: Dict[tuple[str, int], Dict[str, float]] = defaultdict(
        lambda: {"paid": 0.0, "base_paid": 0.0}
    )
    cursor = 0
    points: list[Dict[str, Any]] = []
    selected = start
    while selected <= end:
        end_iso = _day_end(selected)
        while cursor < len(events) and str(events[cursor]["effective_at"]) <= end_iso:
            event = events[cursor]
            logical_id = int(event["logical_request_id"])
            previous = states.get(logical_id)
            if _payment_event(event.get("event_type")):
                previous_paid = _number(previous, "paid_amount") if previous else 0.0
                previous_base_paid = (
                    _base_number(previous, "base_paid_amount_cny", "paid_amount") if previous else 0.0
                )
                event_day = str(event["effective_at"])[:10]
                deltas[(event_day, logical_id)]["paid"] += _number(event, "paid_amount") - previous_paid
                deltas[(event_day, logical_id)]["base_paid"] += (
                    _base_number(event, "base_paid_amount_cny", "paid_amount") - previous_base_paid
                )
            states[logical_id] = event
            cursor += 1
        point = _snapshot_payload(
            selected,
            states,
            deltas,
            allowed_sheets,
            include_details=False,
            china_only=china_only,
        )
        points.append(point)
        selected += timedelta(days=1)

    return {
        "history_start_date": history_start.isoformat(),
        "start": start.isoformat(),
        "end": end.isoformat(),
        "points": points,
    }
