from __future__ import annotations

import json
import os
import re
from collections import Counter, defaultdict
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, Optional

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
    for key in ("DB_HOST", "DB_PORT", "DB_NAME", "DB_USER", "DB_PASSWORD", "DINGTALK_USER_DB_NAME"):
        value = os.environ.get(key)
        if value is None:
            value = file_values.get(key)
        if value is not None:
            values[key] = value.strip()
    return values


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
