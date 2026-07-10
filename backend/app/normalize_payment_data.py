from __future__ import annotations

from typing import Any, Dict, Optional

from .db import connect, init_db, now_iso, row_to_dict, write_audit
from .excel_io import content_hash, normalize_request_business_fields


NORMALIZED_FIELDS = (
    "finance_review",
    "general_manager_approval",
    "general_manager_approval_date",
    "general_manager_opinion",
    "actual_payment_date",
    "remark",
    "payment_status",
)


def raw_extra_value(row: Dict[str, Any], names: tuple[str, ...], fallback: Any) -> Any:
    raw_extra = row.get("raw_extra")
    if not isinstance(raw_extra, dict):
        return fallback
    for name in names:
        matched_keys = [key for key in raw_extra if key == name or key.startswith(f"{name}#")]
        if not matched_keys:
            continue
        matched_keys.sort(key=lambda key: 1 if key == name else int(key.rsplit("#", 1)[-1]))
        for key in matched_keys:
            value = raw_extra.get(key)
            if value not in (None, ""):
                return value
        return None
    return fallback


def clean_remark_text(remark: Any, *texts_to_remove: Any) -> Optional[str]:
    text = str(remark or "").strip()
    if not text:
        return None
    for value in texts_to_remove:
        removal = str(value or "").strip()
        if not removal:
            continue
        text = text.replace(removal, "")
    lines = [line.strip() for line in text.split("\n") if line.strip()]
    return "\n".join(lines) if lines else None


def source_row_for_normalization(row: Dict[str, Any]) -> Dict[str, Any]:
    source = dict(row)
    source["finance_review"] = raw_extra_value(source, ("财务审核", "财务审批"), source.get("finance_review"))
    source["general_manager_approval"] = raw_extra_value(
        source,
        ("总经理批复", "总经理确认", "总经理审批"),
        source.get("general_manager_approval"),
    )
    source["general_manager_opinion"] = raw_extra_value(
        source,
        ("总经理意见", "总经理审批意见", "总经理批复意见"),
        source.get("general_manager_opinion"),
    )
    source["actual_payment_date"] = raw_extra_value(
        source,
        ("实际付款日期", "财务付款时间", "财务付款日期"),
        source.get("actual_payment_date"),
    )
    source["payment_status"] = raw_extra_value(source, ("付款情况", "支付状态"), source.get("payment_status"))
    return source


def normalize_finance_review_dictionary(conn) -> None:
    conn.execute(
        """
        UPDATE dictionaries
        SET active = 0
        WHERE kind = 'payment_status'
        """
    )
    conn.execute(
        """
        UPDATE dictionaries
        SET active = 0
        WHERE kind = 'finance_review'
          AND value NOT IN ('未付款', '部分付款', '已付款')
        """
    )
    timestamp = now_iso()
    for value in ("未付款", "部分付款", "已付款"):
        conn.execute(
            """
            INSERT INTO dictionaries (kind, value, active, created_at)
            VALUES ('finance_review', ?, 1, ?)
            ON CONFLICT(kind, value) DO UPDATE SET active = 1
            """,
            (value, timestamp),
        )


def normalize_payment_requests(conn) -> int:
    rows = conn.execute("SELECT * FROM payment_requests ORDER BY id").fetchall()
    changed_count = 0
    for db_row in rows:
        original = row_to_dict(db_row)
        raw_manager_text = raw_extra_value(
            original,
            ("总经理批复", "总经理确认", "总经理审批"),
            original.get("general_manager_approval"),
        )
        normalized = source_row_for_normalization(original)
        normalize_request_business_fields(normalized)
        remark_removals = [normalized.get("general_manager_opinion")]
        manager_opinion = str(normalized.get("general_manager_opinion") or "")
        raw_manager = str(raw_manager_text or "").strip()
        if raw_manager and raw_manager in manager_opinion:
            remark_removals.append(raw_manager)
        normalized["remark"] = clean_remark_text(normalized.get("remark"), *remark_removals)
        updates = {
            field: normalized.get(field)
            for field in NORMALIZED_FIELDS
            if normalized.get(field) != original.get(field)
        }
        row_for_hash = {**original, **updates}
        next_hash = content_hash(row_for_hash)
        if next_hash != original.get("content_hash"):
            updates["content_hash"] = next_hash
        if not updates:
            continue
        updates["updated_at"] = now_iso()
        columns = list(updates.keys())
        conn.execute(
            f"UPDATE payment_requests SET {', '.join(f'{column} = ?' for column in columns)} WHERE id = ?",
            [updates[column] for column in columns] + [original["id"]],
        )
        changed_count += 1
    return changed_count


def normalize_payment_data() -> Dict[str, int]:
    init_db()
    with connect() as conn:
        changed_rows = normalize_payment_requests(conn)
        normalize_finance_review_dictionary(conn)
        write_audit(
            conn,
            None,
            "maintenance.normalize_payment_data",
            "system",
            new_value={"changed_rows": changed_rows},
            reason="财务审批状态归一",
        )
    return {"changed_rows": changed_rows}


def main() -> None:
    result = normalize_payment_data()
    print(f"normalized payment data: changed_rows={result['changed_rows']}")


if __name__ == "__main__":
    main()
