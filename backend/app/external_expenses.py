from __future__ import annotations

import json
import hashlib
import os
import re
from collections import Counter, defaultdict
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, Optional
from zoneinfo import ZoneInfo

import psycopg
from dotenv import dotenv_values
from psycopg.rows import dict_row

from .db import ROOT_DIR


ALLOWED_SOURCE_TYPES = ("operation", "purchase")
ALLOWED_APPROVAL_STATUSES = ("COMPLETED", "RUNNING")
SOURCE_LABELS = {"operation": "运营支出", "purchase": "采购支出"}
SOURCE_TABLES = {
    "operation": "approval_expense_operation",
    "purchase": "approval_expense_purchase",
}
SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
FINANCE_STAGE_RE = re.compile(r"(财务|出纳|会计|付款|financ|cajer|tesorer|contab)", re.IGNORECASE)
PAID_PHRASE_RE = re.compile(r"(已支付|已经支付|已付款|已经付款|付款完成|支付完成|打款完成|款已付|款项已付)")
PAYMENT_EXCLUSION_RE = re.compile(
    r"(未支付|未付款|未付|待支付|待付款|待付|尚未|剩余|部分|合并|"
    r"客户.{0,8}(?:已支付|已付款)|无需.{0,8}(?:再次)?支付|不需要.{0,8}支付)"
)
APPROVAL_REFERENCE_RE = re.compile(r"(?<!\d)\d{15,}(?!\d)")
PAYMENT_AMOUNT_RE = re.compile(
    r"(?:[¥￥]\s*([0-9][0-9,]*(?:\.\d{1,2})?)\s*(万|千)?|"
    r"([0-9][0-9,]*(?:\.\d{1,2})?)\s*(万|千)?\s*元)"
)
GENERIC_COMMENT_STAGE_RE = re.compile(r"^(评论|备注|comment|remark|comentario)$", re.IGNORECASE)


class ExternalExpenseError(RuntimeError):
    pass


@dataclass(frozen=True)
class SourceDatabaseConfig:
    host: str
    port: int
    dbname: str
    user_dbname: str
    user: str
    password: str


SOURCE_ROWS_CTE = """
WITH source_rows AS (
    SELECT
        'operation'::text AS source_type,
        id::text AS source_id,
        COALESCE(request_date, source_created_at::date) AS effective_date,
        approval_no,
        creator_name,
        applicant_department,
        raw_data->>'title' AS approval_title,
        approval_status,
        raw_data->>'result' AS approval_result,
        execution_region,
        beneficiary,
        expense_type,
        matter_description AS summary,
        NULL::text AS project,
        payment_date::text AS needed_payment_date,
        currency AS source_currency,
        amount AS source_amount,
        base_currency_amount,
        NULL::text AS order_name,
        NULL::text AS product_name,
        source_created_at,
        source_updated_at,
        raw_data
    FROM public.approval_expense_operation

    UNION ALL

    SELECT
        'purchase'::text AS source_type,
        id::text AS source_id,
        COALESCE(request_date, source_created_at::date) AS effective_date,
        approval_no,
        creator_name,
        applicant_department,
        raw_data->>'title' AS approval_title,
        approval_status,
        raw_data->>'result' AS approval_result,
        execution_region,
        NULL::text AS beneficiary,
        purchase_expense AS expense_type,
        NULL::text AS summary,
        project_name AS project,
        NULL::text AS needed_payment_date,
        NULL::text AS source_currency,
        detail_summary_amount AS source_amount,
        base_currency_amount,
        order_name,
        product_name,
        source_created_at,
        source_updated_at,
        raw_data
    FROM public.approval_expense_purchase
)
"""


def _source_env_values() -> Dict[str, str]:
    requested = os.environ.get("PAYMENT_SOURCE_ENV_FILE", "").strip()
    candidates = [Path(requested).expanduser()] if requested else [ROOT_DIR / ".env", ROOT_DIR / "env"]
    file_values: Dict[str, str] = {}
    for candidate in candidates:
        if candidate.exists() and candidate.is_file():
            file_values = {
                str(key): str(value)
                for key, value in dotenv_values(candidate).items()
                if value is not None
            }
            break
    values: Dict[str, str] = {}
    for key in (
        "DB_HOST",
        "DB_PORT",
        "DB_NAME",
        "DB_USER",
        "DB_PASSWORD",
        "DINGTALK_USER_DB_NAME",
        "DINGTALK_AUTO_PAYMENT_MODE",
    ):
        value = os.environ.get(key)
        if value is None:
            value = file_values.get(key)
        if value is not None:
            values[key] = value.strip()
    return values


def dingtalk_auto_payment_mode() -> str:
    mode = _source_env_values().get("DINGTALK_AUTO_PAYMENT_MODE", "preview").strip().lower()
    return mode if mode in {"off", "preview", "apply"} else "preview"


def source_database_config() -> SourceDatabaseConfig:
    values = _source_env_values()
    missing = [key for key in ("DB_HOST", "DB_PORT", "DB_NAME", "DB_USER", "DB_PASSWORD") if not values.get(key)]
    if missing:
        raise ExternalExpenseError("支出中间表数据库配置不完整")
    try:
        port = int(values["DB_PORT"])
    except ValueError as exc:
        raise ExternalExpenseError("支出中间表数据库端口配置无效") from exc
    return SourceDatabaseConfig(
        host=values["DB_HOST"],
        port=port,
        dbname=values["DB_NAME"],
        user_dbname=values.get("DINGTALK_USER_DB_NAME") or "dingtalk_oa",
        user=values["DB_USER"],
        password=values["DB_PASSWORD"],
    )


@contextmanager
def source_connection(dbname: Optional[str] = None) -> Iterator[psycopg.Connection]:
    config = source_database_config()
    try:
        with psycopg.connect(
            host=config.host,
            port=config.port,
            dbname=dbname or config.dbname,
            user=config.user,
            password=config.password,
            connect_timeout=5,
            options="-c default_transaction_read_only=on -c statement_timeout=10000",
            row_factory=dict_row,
        ) as conn:
            yield conn
    except ExternalExpenseError:
        raise
    except Exception as exc:
        raise ExternalExpenseError("无法读取支出中间表，请检查数据库配置或网络连接") from exc


def _preview_conditions(
    date_from: Optional[date],
    date_to: Optional[date],
    source_types: Iterable[str],
    approval_no: str = "",
    applicant_ids: Optional[Iterable[str]] = None,
) -> tuple[str, list[Any]]:
    normalized_sources = [source for source in source_types if source in ALLOWED_SOURCE_TYPES]
    conditions = [
        "source_type = ANY(%s)",
        "approval_status = ANY(%s)",
        "(execution_region ILIKE '%%中国%%' OR execution_region ILIKE '%%china%%')",
        "LOWER(COALESCE(approval_result, '')) <> 'refuse'",
        "(base_currency_amount IS NULL OR base_currency_amount <> 0)",
    ]
    params: list[Any] = [normalized_sources, list(ALLOWED_APPROVAL_STATUSES)]
    normalized_approval_no = approval_no.strip()
    if normalized_approval_no:
        conditions.append("BTRIM(approval_no) = %s")
        params.append(normalized_approval_no)
    else:
        if date_from is None or date_to is None:
            raise ExternalExpenseError("查询条件缺少申请日期")
        conditions.insert(0, "effective_date BETWEEN %s AND %s")
        params[0:0] = [date_from, date_to]
    normalized_applicant_ids = sorted({str(value).strip() for value in applicant_ids or [] if str(value).strip()})
    if normalized_applicant_ids:
        conditions.append("creator_name = ANY(%s)")
        params.append(normalized_applicant_ids)
    return " AND ".join(conditions), params


def preview_external_expenses(
    *,
    date_from: Optional[date],
    date_to: Optional[date],
    source_types: Iterable[str],
    approval_no: str = "",
    applicant_ids: Optional[Iterable[str]] = None,
) -> Dict[str, Any]:
    condition_sql, params = _preview_conditions(date_from, date_to, source_types, approval_no, applicant_ids)
    option_sql, option_params = _preview_conditions(date_from, date_to, source_types, approval_no, [])
    query = f"""
        {SOURCE_ROWS_CTE}
        SELECT * FROM source_rows
        WHERE {condition_sql}
        ORDER BY effective_date DESC, approval_no DESC, source_type, source_id
    """
    option_query = f"""
        {SOURCE_ROWS_CTE}
        SELECT creator_name, applicant_department, approval_title, COUNT(*) AS count FROM source_rows
        WHERE {option_sql} AND creator_name IS NOT NULL AND BTRIM(creator_name) <> ''
        GROUP BY creator_name, applicant_department, approval_title
        ORDER BY creator_name, count DESC
    """
    with source_connection() as conn:
        rows = conn.execute(query, params).fetchall()
        applicant_rows = conn.execute(option_query, option_params).fetchall()
    user_names = fetch_dingtalk_user_names(
        [row.get("creator_name") for row in [*rows, *applicant_rows]]
    )
    return {
        "rows": [map_external_expense(row, user_names) for row in rows],
        "applicant_options": _applicant_options(applicant_rows, user_names),
    }


def fetch_external_expense_metadata(approval_nos: Iterable[str]) -> list[Dict[str, Any]]:
    normalized = sorted({str(value or "").strip() for value in approval_nos if str(value or "").strip()})
    if not normalized:
        return []
    source_rows: list[Dict[str, Any]] = []
    with source_connection() as conn:
        for start in range(0, len(normalized), 500):
            chunk = normalized[start : start + 500]
            query = f"""
                {SOURCE_ROWS_CTE}
                SELECT * FROM source_rows
                WHERE BTRIM(approval_no) = ANY(%s)
                ORDER BY approval_no, source_updated_at DESC NULLS LAST, source_type, source_id
            """
            source_rows.extend(conn.execute(query, [chunk]).fetchall())
    user_names = fetch_dingtalk_user_names(row.get("creator_name") for row in source_rows)
    metadata: list[Dict[str, Any]] = []
    for source_row in source_rows:
        mapped = map_external_expense(source_row, user_names)
        external_source = mapped["request_data"]["raw_extra"]["external_source"]
        metadata.append({
            "approval_no": mapped["approval_no"],
            "source_type": mapped["source_type"],
            "source_label": mapped["source_label"],
            "source_id": mapped["source_id"],
            **external_source,
        })
    return metadata


def _workflow_event_time(value: Any) -> Optional[str]:
    text = _text(value)
    if not text:
        return None
    normalized = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return text
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(SHANGHAI_TZ).replace(microsecond=0).isoformat()


def _json_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
            return decoded if isinstance(decoded, list) else []
        except json.JSONDecodeError:
            return []
    return []


def _workflow_event_key(process_instance_id: str, operation: Dict[str, Any]) -> str:
    stable_parts = [
        process_instance_id,
        _text(operation.get("userId")) or "",
        _text(operation.get("date")) or "",
        _text(operation.get("type")) or "",
        _text(operation.get("showName")) or "",
        _text(operation.get("remark")) or "",
    ]
    return hashlib.sha256("|".join(stable_parts).encode("utf-8")).hexdigest()


def _parse_workflow_events(
    process_instance_id: str,
    operations: list[Dict[str, Any]],
    current_activity_ids: set[str],
    user_names: Dict[str, str],
) -> list[Dict[str, Any]]:
    stage_by_activity: Dict[str, str] = {}
    normalized_operations: list[tuple[int, Dict[str, Any], Optional[str]]] = []
    finance_agree_times: Dict[str, list[str]] = {}
    for sequence_index, operation in enumerate(operations):
        activity_id = _text(operation.get("activityId")) or ""
        stage_name = _text(operation.get("showName")) or ""
        event_type = (_text(operation.get("type")) or "").upper()
        result = (_text(operation.get("result")) or "").upper()
        event_time = _workflow_event_time(operation.get("date"))
        normalized_operations.append((sequence_index, operation, event_time))
        if activity_id and stage_name and not GENERIC_COMMENT_STAGE_RE.fullmatch(stage_name):
            stage_by_activity.setdefault(activity_id, stage_name)
        operator_id = _text(operation.get("userId")) or ""
        if (
            operator_id
            and event_type == "EXECUTE_TASK_NORMAL"
            and result == "AGREE"
            and FINANCE_STAGE_RE.search(stage_name)
            and event_time
        ):
            finance_agree_times.setdefault(operator_id, []).append(event_time)

    stage_contexts = sorted(
        [
            (event_time, sequence_index, _text(operation.get("showName")) or "")
            for sequence_index, operation, event_time in normalized_operations
            if event_time
            and _text(operation.get("showName"))
            and not GENERIC_COMMENT_STAGE_RE.fullmatch(_text(operation.get("showName")) or "")
        ],
        key=lambda item: (item[0], item[1]),
    )

    events: list[Dict[str, Any]] = []
    for sequence_index, operation, event_time in normalized_operations:
        operator_id = _text(operation.get("userId")) or ""
        activity_id = _text(operation.get("activityId")) or ""
        raw_stage_name = _text(operation.get("showName")) or ""
        is_generic_comment_stage = bool(GENERIC_COMMENT_STAGE_RE.fullmatch(raw_stage_name))
        mapped_stage_name = (
            stage_by_activity.get(activity_id)
            if is_generic_comment_stage
            else raw_stage_name
        ) or stage_by_activity.get(activity_id)
        if is_generic_comment_stage and not mapped_stage_name and event_time:
            position = (event_time, sequence_index)
            previous_stage = next(
                (stage for time, index, stage in reversed(stage_contexts) if (time, index) < position),
                None,
            )
            next_stage = next(
                (stage for time, index, stage in stage_contexts if (time, index) > position),
                None,
            )
            if previous_stage and next_stage:
                mapped_stage_name = (
                    f"{previous_stage}节点评论"
                    if previous_stage == next_stage
                    else f"{previous_stage} → {next_stage} 之间的评论"
                )
            elif previous_stage:
                mapped_stage_name = f"{previous_stage}后评论"
            elif next_stage:
                mapped_stage_name = f"{next_stage}前评论"
        stage_name = mapped_stage_name or "流程评论"
        event_type = (_text(operation.get("type")) or "").upper()
        result = (_text(operation.get("result")) or "").upper()
        is_finance_agree = bool(
            operator_id
            and event_type == "EXECUTE_TASK_NORMAL"
            and result == "AGREE"
            and FINANCE_STAGE_RE.search(stage_name)
        )
        trusted_after_finance_agree = bool(
            operator_id
            and event_time
            and any(
                approval_time <= event_time
                for approval_time in finance_agree_times.get(operator_id, [])
            )
        )
        events.append({
            "event_key": _workflow_event_key(process_instance_id, operation),
            "process_instance_id": process_instance_id,
            "activity_id": activity_id or None,
            "event_type": event_type,
            "stage_name": stage_name,
            "result": result or None,
            "operator_id": operator_id,
            "operator_name": user_names.get(operator_id) or "未识别人员",
            "event_time": event_time,
            "sequence_index": sequence_index,
            "comment": _text(operation.get("remark")),
            "images": _json_list(operation.get("images")),
            "attachments": _json_list(operation.get("attachments")),
            "trusted_finance": bool(is_finance_agree or trusted_after_finance_agree),
            "current": bool(activity_id and activity_id in current_activity_ids),
        })
    return sorted(
        events,
        key=lambda event: (
            event.get("event_time") is None,
            event.get("event_time") or "",
            int(event.get("sequence_index") or 0),
        ),
    )


def fetch_dingtalk_workflows(approval_nos: Iterable[str]) -> list[Dict[str, Any]]:
    normalized = sorted({str(value or "").strip() for value in approval_nos if str(value or "").strip()})
    if not normalized:
        return []
    config = source_database_config()
    instances: list[Dict[str, Any]] = []
    with source_connection(config.user_dbname) as conn:
        for start in range(0, len(normalized), 500):
            chunk = normalized[start : start + 500]
            instances.extend(
                conn.execute(
                    """
                    SELECT process_instance_id,
                           BTRIM(raw_payload->>'businessId') AS approval_no,
                           status,
                           result,
                           title,
                           raw_payload->'operationRecords' AS operation_records,
                           raw_payload->'tasks' AS tasks,
                           updated_at
                    FROM public.ding_approval_instance
                    WHERE deleted_at IS NULL
                      AND BTRIM(raw_payload->>'businessId') = ANY(%s)
                    ORDER BY BTRIM(raw_payload->>'businessId'),
                             updated_at DESC NULLS LAST,
                             id DESC
                    """,
                    [chunk],
                ).fetchall()
            )
        user_ids = sorted({
            str(operation.get("userId") or "").strip()
            for instance in instances
            for operation in _json_list(instance.get("operation_records"))
            if isinstance(operation, dict) and str(operation.get("userId") or "").strip()
        })
        user_names: Dict[str, str] = {}
        for start in range(0, len(user_ids), 500):
            chunk = user_ids[start : start + 500]
            rows = conn.execute(
                """
                SELECT DISTINCT ON (BTRIM(user_id))
                       BTRIM(user_id) AS user_id,
                       BTRIM(name) AS name
                FROM public.ding_user_snapshot
                WHERE BTRIM(user_id) = ANY(%s)
                  AND name IS NOT NULL
                  AND BTRIM(name) <> ''
                ORDER BY BTRIM(user_id),
                         is_current DESC NULLS LAST,
                         valid_from DESC NULLS LAST,
                         updated_at DESC NULLS LAST,
                         id DESC
                """,
                [chunk],
            ).fetchall()
            user_names.update({
                str(row["user_id"]): str(row["name"])
                for row in rows
                if valid_applicant_name(row.get("name"))
            })

    workflows: list[Dict[str, Any]] = []
    for instance in instances:
        process_instance_id = _text(instance.get("process_instance_id")) or ""
        operations = [
            dict(operation)
            for operation in _json_list(instance.get("operation_records"))
            if isinstance(operation, dict)
        ]
        current_activity_ids = {
            _text(task.get("activityId")) or ""
            for task in _json_list(instance.get("tasks"))
            if isinstance(task, dict)
            and (_text(task.get("status")) or "").upper() in {"RUNNING", "PROCESSING"}
            and _text(task.get("activityId"))
        }
        events = _parse_workflow_events(
            process_instance_id,
            operations,
            current_activity_ids,
            user_names,
        )
        workflows.append({
            "approval_no": _text(instance.get("approval_no")) or "",
            "process_instance_id": process_instance_id,
            "status": (_text(instance.get("status")) or "").upper(),
            "result": (_text(instance.get("result")) or "").lower(),
            "title": _text(instance.get("title")),
            "updated_at": _workflow_event_time(instance.get("updated_at")),
            "events": events,
        })
    return workflows


def classify_dingtalk_payment_event(
    event: Dict[str, Any],
    *,
    approval_no: str,
    pending_amount: float,
    workflow_status: str,
    workflow_result: str,
    paid_amount: float = 0,
) -> tuple[str, str]:
    comment = _text(event.get("comment")) or ""
    if not comment:
        return "ignored", "无评论"
    if not event.get("trusted_finance"):
        return "ignored", "评论人未通过财务节点可信校验"
    if workflow_status.upper() not in ALLOWED_APPROVAL_STATUSES or workflow_result.lower() == "refuse":
        return "review_required", "审批流程当前状态不允许自动付款"
    if PAYMENT_EXCLUSION_RE.search(comment):
        return "review_required", "评论包含部分、未付、合并或其他排除语义"
    if not PAID_PHRASE_RE.search(comment):
        return "ignored", "未识别到明确的付款完成语义"
    referenced_approval_nos = {
        match
        for match in APPROVAL_REFERENCE_RE.findall(comment)
        if match != str(approval_no or "").strip()
    }
    if referenced_approval_nos:
        return "review_required", "评论引用了其他钉钉审批单号"
    amounts = set()
    for symbol_amount, symbol_unit, yuan_amount, yuan_unit in PAYMENT_AMOUNT_RE.findall(comment):
        raw_amount = symbol_amount or yuan_amount
        if not raw_amount:
            continue
        unit = symbol_unit or yuan_unit
        multiplier = 10000 if unit == "万" else 1000 if unit == "千" else 1
        amounts.add(round(float(raw_amount.replace(",", "")) * multiplier, 2))
    if len(amounts) > 1:
        return "review_required", "评论包含多笔付款金额"
    if amounts:
        comment_amount = next(iter(amounts))
        if float(paid_amount) > 0 and comment_amount <= round(float(paid_amount), 2) + 0.01:
            return "review_required", "评论金额可能已包含在当前累计已付金额中"
        if abs(comment_amount - round(float(pending_amount), 2)) > 0.01:
            return "review_required", "评论金额与当前待付款金额不一致"
    return "eligible", "可信财务人员明确确认全额付款"


def fetch_external_expenses(items: Iterable[Dict[str, str]]) -> list[Dict[str, Any]]:
    keys = {(str(item.get("source_type") or ""), str(item.get("source_id") or "")) for item in items}
    operation_ids = sorted(source_id for source_type, source_id in keys if source_type == "operation" and source_id.isdigit())
    purchase_ids = sorted(source_id for source_type, source_id in keys if source_type == "purchase" and source_id.isdigit())
    if not operation_ids and not purchase_ids:
        return []
    key_conditions: list[str] = []
    params: list[Any] = []
    if operation_ids:
        key_conditions.append("(source_type = 'operation' AND source_id = ANY(%s))")
        params.append(operation_ids)
    if purchase_ids:
        key_conditions.append("(source_type = 'purchase' AND source_id = ANY(%s))")
        params.append(purchase_ids)
    query = f"""
        {SOURCE_ROWS_CTE}
        SELECT * FROM source_rows
        WHERE {' OR '.join(key_conditions)}
        ORDER BY source_type, source_id
    """
    with source_connection() as conn:
        source_rows = conn.execute(query, params).fetchall()
        approval_nos = sorted({str(row.get("approval_no") or "").strip() for row in source_rows if str(row.get("approval_no") or "").strip()})
        conflicts: set[str] = set()
        if approval_nos:
            conflict_query = f"""
                {SOURCE_ROWS_CTE}
                SELECT BTRIM(approval_no) AS approval_no, COUNT(*) AS count
                FROM source_rows
                WHERE BTRIM(approval_no) = ANY(%s)
                  AND approval_status = ANY(%s)
                  AND (execution_region ILIKE '%%中国%%' OR execution_region ILIKE '%%china%%')
                  AND LOWER(COALESCE(approval_result, '')) <> 'refuse'
                GROUP BY BTRIM(approval_no)
                HAVING COUNT(*) > 1
            """
            conflicts = {
                str(row["approval_no"])
                for row in conn.execute(conflict_query, [approval_nos, list(ALLOWED_APPROVAL_STATUSES)]).fetchall()
            }
    user_names = fetch_dingtalk_user_names(row.get("creator_name") for row in source_rows)
    rows = [map_external_expense(row, user_names) for row in source_rows]
    for row in rows:
        if row.get("approval_no") in conflicts:
            row["errors"].append("同一钉钉单号存在多条来源记录")
            row["source_conflict"] = True
    return rows


def map_external_expense(raw_row: Dict[str, Any], user_names: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    row = dict(raw_row)
    source_type = str(row.get("source_type") or "")
    raw_data = _json_object(row.get("raw_data"))
    form_values = _form_values(raw_data)
    beneficiary_values: list[str]
    if source_type == "purchase":
        beneficiary_values = _component_values(form_values, "收款人")
        beneficiary = " / ".join(beneficiary_values)
        detail_values = _component_values(form_values, "规格明细需求说明") or _component_values(form_values, "需求明细")
        summary = detail_values[0] if detail_values else _first_text(row.get("product_name"), row.get("order_name"), row.get("project"), row.get("expense_type"))
        payment_date_values = _component_values(form_values, "付款日期")
        needed_payment_date = _date_text(payment_date_values[0]) if payment_date_values else None
    else:
        beneficiary = _text(row.get("beneficiary")) or ""
        beneficiary_values = [beneficiary] if beneficiary else []
        summary = _text(row.get("summary"))
        needed_payment_date = _date_text(row.get("needed_payment_date"))

    approval_no = _text(row.get("approval_no")) or ""
    approval_status = (_text(row.get("approval_status")) or "").upper()
    approval_result = (_text(row.get("approval_result")) or "").lower()
    execution_region = _text(row.get("execution_region")) or ""
    amount = _number(row.get("base_currency_amount"))
    warnings: list[str] = []
    errors: list[str] = []
    if not beneficiary:
        warnings.append("缺少收款信息")
    if len(beneficiary_values) > 1:
        warnings.append("存在多个收款人，请确认")
    if amount == 0:
        errors.append("应付金额为 0，暂不导入")
    if not approval_no:
        errors.append("缺少钉钉单号")
    if amount is None:
        errors.append("缺少应付金额")
    elif amount < 0:
        errors.append("应付金额不能为负数")
    if "中国" not in execution_region and "china" not in execution_region.lower():
        errors.append("执行地区不是中国")
    if approval_status not in ALLOWED_APPROVAL_STATUSES:
        errors.append("审批状态不允许导入")
    if approval_result == "refuse":
        errors.append("审批结果为拒绝")

    application_date = _date_text(row.get("effective_date"))
    applicant_id = _text(row.get("creator_name")) or ""
    snapshot_applicant = (user_names or {}).get(applicant_id, "")
    applicant = snapshot_applicant if valid_applicant_name(snapshot_applicant) else ""
    applicant_name_source = "ding_user_snapshot" if applicant else ""
    if not applicant:
        applicant = applicant_name_from_title(row.get("approval_title"))
        applicant_name_source = "approval_title" if applicant else "unresolved"
    if not applicant:
        applicant = "未识别人员"
        if applicant_id:
            warnings.append("申请人姓名未解析")
    applicant_department = _text(row.get("applicant_department")) or ""
    source_id = str(row.get("source_id") or "")
    source_label = SOURCE_LABELS.get(source_type, source_type)
    request_data = {
        "dingding_id": approval_no or None,
        "expense_type": _text(row.get("expense_type")),
        "summary": summary,
        "amount": amount,
        "currency": "CNY",
        "project": _text(row.get("project")),
        "payee_account": beneficiary or None,
        "needed_payment_date": needed_payment_date,
        "source_sheet": applicant_department or "未归属部门",
        "raw_extra": {
            "external_source": {
                "system": "dingtalk_expense_database",
                "table": SOURCE_TABLES.get(source_type, source_type),
                "record_id": source_id,
                "approval_no": approval_no,
                "approval_status": approval_status,
                "approval_result": approval_result or None,
                "applicant_id": applicant_id,
                "applicant": applicant,
                "applicant_name_source": applicant_name_source,
                "applicant_department": applicant_department,
                "application_date": application_date,
                "source_created_at": _datetime_text(row.get("source_created_at")),
                "source_updated_at": _datetime_text(row.get("source_updated_at")),
                "source_currency": _text(row.get("source_currency")),
                "source_amount": _number(row.get("source_amount")),
            }
        },
    }
    return {
        "source_type": source_type,
        "source_label": source_label,
        "source_id": source_id,
        "application_date": application_date,
        "approval_no": approval_no,
        "applicant_id": applicant_id,
        "applicant": applicant,
        "applicant_department": applicant_department,
        "approval_status": approval_status,
        "approval_result": approval_result or None,
        "summary": summary or "",
        "amount": amount,
        "beneficiary": beneficiary,
        "needed_payment_date": needed_payment_date,
        "warnings": warnings,
        "errors": errors,
        "source_conflict": False,
        "request_data": request_data,
    }


def applicant_name_from_title(value: Any) -> str:
    title = _text(value) or ""
    patterns = (
        re.compile(r"^(.+?)提交的"),
        re.compile(r"\benviado\s+por\s+(.+?)\s*$", re.IGNORECASE),
        re.compile(r"\bsubmitted\s+by\s+(.+?)\s*$", re.IGNORECASE),
        re.compile(r"^(.+?)[’']s\s+", re.IGNORECASE),
    )
    for pattern in patterns:
        match = pattern.search(title)
        if match:
            candidate = match.group(1).strip()
            return candidate if valid_applicant_name(candidate) else ""
    return ""


INVALID_APPLICANT_NAMES = {
    "-",
    "--",
    "n/a",
    "na",
    "none",
    "null",
    "unknown",
    "unknown user",
    "未知",
    "未识别",
    "未识别人员",
}


def valid_applicant_name(value: Any) -> bool:
    text = str(value or "").strip()
    return bool(text) and text.lower() not in INVALID_APPLICANT_NAMES


def fetch_dingtalk_user_names(user_ids: Iterable[Any]) -> Dict[str, str]:
    normalized = sorted({str(value or "").strip() for value in user_ids if str(value or "").strip()})
    if not normalized:
        return {}
    config = source_database_config()
    names: Dict[str, str] = {}
    try:
        with source_connection(config.user_dbname) as conn:
            for start in range(0, len(normalized), 500):
                chunk = normalized[start : start + 500]
                rows = conn.execute(
                    """
                    SELECT DISTINCT ON (BTRIM(user_id))
                           BTRIM(user_id) AS user_id,
                           BTRIM(name) AS name
                    FROM public.ding_user_snapshot
                    WHERE BTRIM(user_id) = ANY(%s)
                      AND name IS NOT NULL
                      AND BTRIM(name) <> ''
                      AND NOT (LOWER(BTRIM(name)) = ANY(%s))
                    ORDER BY BTRIM(user_id),
                             is_current DESC NULLS LAST,
                             valid_from DESC NULLS LAST,
                             updated_at DESC NULLS LAST,
                             id DESC
                    """,
                    [chunk, sorted(INVALID_APPLICANT_NAMES)],
                ).fetchall()
                names.update({str(row["user_id"]): str(row["name"]) for row in rows if valid_applicant_name(row["name"])})
    except ExternalExpenseError:
        return {}
    return names


def _applicant_options(rows: Iterable[Dict[str, Any]], user_names: Optional[Dict[str, str]] = None) -> list[Dict[str, Any]]:
    grouped: Dict[str, Dict[str, Any]] = defaultdict(lambda: {
        "count": 0,
        "names": Counter(),
        "departments": Counter(),
    })
    for row in rows:
        applicant_id = _text(row.get("creator_name")) or ""
        if not applicant_id:
            continue
        count = int(row.get("count") or 0)
        snapshot_name = (user_names or {}).get(applicant_id)
        name = (snapshot_name if valid_applicant_name(snapshot_name) else "") or applicant_name_from_title(row.get("approval_title")) or "未识别人员"
        department = _text(row.get("applicant_department")) or "未归属部门"
        grouped[applicant_id]["count"] += count
        grouped[applicant_id]["names"][name] += count
        grouped[applicant_id]["departments"][department] += count

    options = []
    for applicant_id, values in grouped.items():
        name = sorted(values["names"].items(), key=lambda item: (-item[1], item[0]))[0][0]
        department = sorted(values["departments"].items(), key=lambda item: (-item[1], item[0]))[0][0]
        options.append({
            "id": applicant_id,
            "name": name,
            "department": department,
            "count": values["count"],
        })
    return sorted(options, key=lambda option: (option["name"], option["department"], option["id"]))


def _json_object(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
            return decoded if isinstance(decoded, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def _form_values(raw_data: Dict[str, Any]) -> list[Dict[str, Any]]:
    values = raw_data.get("formComponentValues")
    return [item for item in values if isinstance(item, dict)] if isinstance(values, list) else []


def _component_values(form_values: Iterable[Dict[str, Any]], name_prefix: str) -> list[str]:
    result: list[str] = []
    for item in form_values:
        name = _text(item.get("name")) or ""
        if not name.startswith(name_prefix):
            continue
        value = _text(item.get("value"))
        if value and value not in result:
            result.append(value)
    return result


def _text(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _first_text(*values: Any) -> Optional[str]:
    for value in values:
        text = _text(value)
        if text:
            return text
    return None


def _number(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    if isinstance(value, Decimal):
        return round(float(value), 2)
    try:
        return round(float(value), 2)
    except (TypeError, ValueError):
        return None


def _date_text(value: Any) -> Optional[str]:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = _text(value)
    if not text:
        return None
    match = re.search(r"(20\d{2})[-/.](\d{1,2})[-/.](\d{1,2})", text)
    if not match:
        return None
    try:
        return date(int(match.group(1)), int(match.group(2)), int(match.group(3))).isoformat()
    except ValueError:
        return None


def _datetime_text(value: Any) -> Optional[str]:
    if isinstance(value, datetime):
        return value.replace(microsecond=0).isoformat(sep=" ")
    if isinstance(value, date):
        return value.isoformat()
    return _text(value)
