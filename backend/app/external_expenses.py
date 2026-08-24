from __future__ import annotations

import json
import hashlib
import os
import re
import time
import unicodedata
from collections import Counter, defaultdict
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, Optional
from zoneinfo import ZoneInfo

import httpx
import psycopg
from dotenv import dotenv_values
from psycopg.rows import dict_row

from .db import ROOT_DIR
from .fx_rates import FxRateError, fetch_rates, multiply_money, normalize_currency
from .mexico_tracking import resolve_region


ALLOWED_SOURCE_TYPES = ("operation", "purchase", "monthly")
STANDARD_SOURCE_TYPES = ("operation", "purchase")
MONTHLY_PAYMENT_PROCESS_CODE = "PROC-EE85EDD4-5CF2-4C08-B948-1690A6ACC51C"
ALLOWED_APPROVAL_STATUSES = ("COMPLETED", "RUNNING")
ALLOWED_EXECUTION_REGION_PATTERN = r"(中国|china|墨西哥|m[eé]xico)"
CHINA_EXECUTION_REGION_RE = re.compile(r"(中国|china)", re.IGNORECASE)
MEXICO_EXECUTION_REGION_RE = re.compile(r"(墨西哥|m[eé]xico)", re.IGNORECASE)
CURRENCY_COMPONENT_PREFIXES = ("币种", "货币", "currency", "moneda", "tipo de moneda")
SUMMARY_MXN_CURRENCY_RE = re.compile(
    r"(?:\bMXN\b|MX\$|墨西哥比索|比索|(?<![A-Za-z])PESOS?(?![A-Za-z]))",
    re.IGNORECASE,
)
SUMMARY_USD_CURRENCY_RE = re.compile(
    r"(?:\bUSD\b|US\$|美元|美金|(?<![A-Za-z])D[ÓO]LARES?(?![A-Za-z]))",
    re.IGNORECASE,
)
SUMMARY_CNY_CURRENCY_RE = re.compile(
    r"(?:\bCNY\b|\bRMB\b|人民币(?:元)?)",
    re.IGNORECASE,
)
EXECUTION_REGION_COMPONENT_PREFIXES = (
    "执行地区",
    "执行区域",
    "地区",
    "国家",
    "execution region",
    "region",
    "región",
    "país",
    "pais",
)
DISALLOWED_APPROVAL_RESULT_PATTERN = r"(refus|reject|cancel|terminat|revok|void|abort|作废|拒绝|撤销|撤回|取消|终止)"
DISALLOWED_APPROVAL_RESULT_RE = re.compile(DISALLOWED_APPROVAL_RESULT_PATTERN, re.IGNORECASE)
SOURCE_LABELS = {"operation": "运营支出", "purchase": "采购支出", "monthly": "月结付款"}
SOURCE_TABLES = {
    "operation": "approval_expense_operation",
    "purchase": "approval_expense_purchase",
    "monthly": "ding_approval_instance",
}
SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
FINANCE_STAGE_RE = re.compile(r"(财务|出纳|会计|付款|financ|cajer|tesorer|contab)", re.IGNORECASE)
PAID_PHRASE_RE = re.compile(r"(已支付|已经支付|已付款|已经付款|付款完成|支付完成|打款完成|款已付|款项已付)")
YUEWEI_PAYMENT_CONFIRM_RE = re.compile(
    r"(?:^|[\s,，。；;：:])悦为支付(?:$|[\s,，。；;！!])",
    re.IGNORECASE,
)
PAYMENT_EXCLUSION_RE = re.compile(
    r"(未支付|未付款|未付|待支付|待付款|待付|尚未|剩余|部分|合并|"
    r"(?:待|请|将|计划|预计|需要|需由).{0,8}悦为.{0,4}(?:支付|付款)|"
    r"客户.{0,8}(?:已支付|已付款)|无需.{0,8}(?:再次)?支付|不需要.{0,8}支付)"
)
# DingTalk approval business IDs begin with an application date (YYYYMMDD).
# Do not treat numeric user IDs in mentions such as ``[name](275014...)`` as
# references to another approval.
APPROVAL_REFERENCE_RE = re.compile(
    r"(?<!\d)20\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{10,}(?!\d)"
)
PAYMENT_AMOUNT_RE = re.compile(
    r"(?:[¥￥]\s*([0-9][0-9,]*(?:\.\d{1,2})?)\s*(万|千)?|"
    r"([0-9][0-9,]*(?:\.\d{1,2})?)\s*(万|千)?\s*元)"
)
GENERIC_COMMENT_STAGE_RE = re.compile(r"^(评论|备注|comment|remark|comentario)$", re.IGNORECASE)
MAX_DINGTALK_ATTACHMENT_BYTES = 50 * 1024 * 1024


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


@dataclass(frozen=True)
class DiscoveryResult:
    candidates: list[Dict[str, Any]]
    next_cursors: Dict[str, Dict[str, str]]
    source_conflicts: list[str]
    query_timings: Dict[str, float]


class DingtalkAttachmentClient:
    def __init__(self) -> None:
        values = _source_env_values()
        app_key = values.get("DINGTALK_APPKEY")
        app_secret = values.get("DINGTALK_APPSECRET")
        if not app_key or not app_secret:
            raise ExternalExpenseError("钉钉附件下载配置不完整")
        self.client = httpx.Client(
            timeout=httpx.Timeout(60.0, connect=10.0),
            follow_redirects=True,
        )
        try:
            response = self.client.post(
                "https://api.dingtalk.com/v1.0/oauth2/accessToken",
                json={"appKey": app_key, "appSecret": app_secret},
            )
            response.raise_for_status()
            payload = response.json()
            self.access_token = str(payload.get("accessToken") or "").strip()
            if not self.access_token:
                raise ExternalExpenseError(
                    str(payload.get("message") or payload.get("errmsg") or "无法获取钉钉访问凭证")
                )
        except ExternalExpenseError:
            self.client.close()
            raise
        except Exception as exc:
            self.client.close()
            raise ExternalExpenseError("无法获取钉钉附件下载凭证") from exc

    def __enter__(self) -> "DingtalkAttachmentClient":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.client.close()

    def download(self, process_instance_id: str, file_id: str) -> tuple[bytes, Optional[str]]:
        if not process_instance_id or not file_id:
            raise ExternalExpenseError("附件缺少流程实例或文件标识")
        try:
            response = self.client.post(
                "https://api.dingtalk.com/v1.0/workflow/processInstances/spaces/files/urls/download",
                headers={
                    "x-acs-dingtalk-access-token": self.access_token,
                    "Content-Type": "application/json",
                },
                json={
                    "processInstanceId": process_instance_id,
                    "fileId": file_id,
                },
            )
            response.raise_for_status()
            payload = response.json()
            result = payload.get("result") or {}
            download_uri = str(result.get("downloadUri") or "").strip()
            if not payload.get("success") or not download_uri:
                raise ExternalExpenseError(
                    str(payload.get("message") or payload.get("errmsg") or "钉钉未返回附件下载地址")
                )
            content = bytearray()
            content_type: Optional[str] = None
            with self.client.stream("GET", download_uri) as file_response:
                file_response.raise_for_status()
                content_type = file_response.headers.get("content-type")
                declared_size = file_response.headers.get("content-length")
                if declared_size and int(declared_size) > MAX_DINGTALK_ATTACHMENT_BYTES:
                    raise ExternalExpenseError("钉钉附件超过 50MB")
                for chunk in file_response.iter_bytes():
                    content.extend(chunk)
                    if len(content) > MAX_DINGTALK_ATTACHMENT_BYTES:
                        raise ExternalExpenseError("钉钉附件超过 50MB")
            if not content:
                raise ExternalExpenseError("钉钉附件内容为空")
            return bytes(content), content_type
        except ExternalExpenseError:
            raise
        except Exception as exc:
            raise ExternalExpenseError("钉钉附件下载失败") from exc


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
        "DINGTALK_APPKEY",
        "DINGTALK_APPSECRET",
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


class PostgresDiscoveryGateway:
    """Read approval changes without binding Mexico tracking to local requests."""

    def fetch_source_changes(
        self,
        source_type: str,
        cursor: Optional[Dict[str, str]],
        running_approval_nos: Iterable[str],
    ) -> list[Dict[str, Any]]:
        if source_type not in ALLOWED_SOURCE_TYPES:
            raise ExternalExpenseError("不支持的支出来源")
        running = list(running_approval_nos)
        if source_type == "monthly":
            return self._fetch_monthly_changes(cursor, running)
        conditions = ["source_type = %s"]
        params: list[Any] = [source_type]
        if cursor:
            updated_at = str(cursor.get("updated_at") or "").strip()
            source_id = str(cursor.get("source_id") or "").strip()
            if updated_at:
                conditions.append(
                    "("
                    "COALESCE(source_updated_at, source_created_at) > %s::timestamptz "
                    "OR (COALESCE(source_updated_at, source_created_at) = %s::timestamptz "
                    "AND source_id > %s) "
                    "OR BTRIM(COALESCE(approval_no, '')) = ANY(%s)"
                    ")"
                )
                params.extend([updated_at, updated_at, source_id, running])
        query = f"""
            {SOURCE_ROWS_CTE}
            SELECT *
            FROM source_rows
            WHERE {' AND '.join(conditions)}
            ORDER BY COALESCE(source_updated_at, source_created_at), source_id
        """
        with source_connection() as conn:
            return conn.execute(query, params).fetchall()

    def _fetch_monthly_changes(
        self,
        cursor: Optional[Dict[str, str]],
        running_approval_nos: list[str],
    ) -> list[Dict[str, Any]]:
        conditions = ["deleted_at IS NULL", "process_code = %s"]
        params: list[Any] = [MONTHLY_PAYMENT_PROCESS_CODE]
        if cursor:
            updated_at = str(cursor.get("updated_at") or "").strip()
            source_id = str(cursor.get("source_id") or "").strip()
            if updated_at:
                conditions.append(
                    "("
                    "COALESCE(updated_at, create_time) > %s::timestamptz "
                    "OR (COALESCE(updated_at, create_time) = %s::timestamptz "
                    "AND id::text > %s) "
                    "OR BTRIM(COALESCE(raw_payload->>'businessId', '')) = ANY(%s)"
                    ")"
                )
                params.extend([updated_at, updated_at, source_id, running_approval_nos])
        config = source_database_config()
        with source_connection(config.user_dbname) as conn:
            return conn.execute(
                f"""
                SELECT id::text AS source_id,
                       process_instance_id,
                       process_code,
                       create_time,
                       updated_at,
                       status,
                       result,
                       title,
                       raw_payload
                FROM public.ding_approval_instance
                WHERE {' AND '.join(conditions)}
                ORDER BY COALESCE(updated_at, create_time), id
                """,
                params,
            ).fetchall()

    def fetch_user_names(self, user_ids: Iterable[Any]) -> Dict[str, str]:
        return fetch_dingtalk_user_names(user_ids)


def _discovery_cursor_value(source_type: str, row: Dict[str, Any]) -> tuple[str, str]:
    updated = row.get("updated_at") if source_type == "monthly" else row.get("source_updated_at")
    if updated is None:
        updated = row.get("create_time") if source_type == "monthly" else row.get("source_created_at")
    return _datetime_text(updated) or "", str(row.get("source_id") or "").strip()


def _candidate_process_instance(source_type: str, row: Dict[str, Any]) -> Optional[str]:
    if source_type == "monthly":
        return _text(row.get("process_instance_id"))
    raw_data = _json_object(row.get("raw_data"))
    return _first_text(
        raw_data.get("processInstanceId"),
        raw_data.get("process_instance_id"),
        raw_data.get("processInstanceID"),
    )


def _candidate_process_code(source_type: str, row: Dict[str, Any]) -> Optional[str]:
    if source_type == "monthly":
        return _text(row.get("process_code")) or MONTHLY_PAYMENT_PROCESS_CODE
    raw_data = _json_object(row.get("raw_data"))
    return _first_text(raw_data.get("processCode"), raw_data.get("process_code"))


def _map_discovery_candidate(
    source_type: str,
    row: Dict[str, Any],
    user_names: Dict[str, str],
) -> Dict[str, Any]:
    mapped = (
        map_monthly_payment(row, user_names)
        if source_type == "monthly"
        else map_external_expense(row, user_names)
    )
    external_source = (
        mapped.get("request_data", {}).get("raw_extra", {}).get("external_source", {})
    )
    raw_execution_region = _text(external_source.get("execution_region"))
    company_name = _text(mapped.get("applicant_department")) or "未归属公司"
    decision = resolve_region(
        execution_region=raw_execution_region,
        source_sheet=company_name,
    )
    status = _text(mapped.get("approval_status")) or ""
    result = _text(mapped.get("approval_result"))
    return {
        "approval_no": str(mapped.get("approval_no") or "").strip(),
        "source_type": source_type,
        "source_record_id": str(row.get("source_id") or "").strip(),
        "source_id": str(row.get("source_id") or "").strip(),
        "process_code": _candidate_process_code(source_type, row),
        "process_instance_id": _candidate_process_instance(source_type, row),
        "raw_execution_region": raw_execution_region,
        "resolved_region": decision.region,
        "region_resolution_source": decision.source,
        "region_conflict_reason": decision.conflict_reason,
        "request_date": mapped.get("application_date"),
        "applicant_id": mapped.get("applicant_id") or None,
        "applicant_name": mapped.get("applicant") or "未识别人员",
        "applicant_department": mapped.get("applicant_department") or None,
        "company_name": company_name,
        "source_sheet": company_name,
        "summary": mapped.get("summary") or "",
        "amount": mapped.get("amount"),
        "currency": mapped.get("currency"),
        "workflow_status": status.upper(),
        "workflow_result": result.lower() if result else None,
        "source_updated_at": _discovery_cursor_value(source_type, row)[0] or None,
        "source_conflict": False,
        "warnings": list(mapped.get("warnings") or []),
        "errors": list(mapped.get("errors") or []),
        "raw_summary": external_source,
    }


def _conflict_candidate(approval_no: str, candidates: list[Dict[str, Any]]) -> Dict[str, Any]:
    return {
        "approval_no": approval_no,
        "source_type": "conflict",
        "source_record_id": None,
        "source_id": None,
        "process_code": None,
        "process_instance_id": None,
        "raw_execution_region": None,
        "resolved_region": "review",
        "region_resolution_source": "source_conflict",
        "region_conflict_reason": "同一钉钉单号存在多个来源记录",
        "request_date": None,
        "applicant_id": None,
        "applicant_name": "待核对",
        "applicant_department": None,
        "company_name": "未归属公司",
        "source_sheet": "未归属公司",
        "summary": "同一钉钉单号存在多个来源记录",
        "amount": None,
        "currency": None,
        "workflow_status": None,
        "workflow_result": None,
        "source_updated_at": max(
            (str(candidate.get("source_updated_at") or "") for candidate in candidates),
            default="",
        ) or None,
        "source_conflict": True,
        "warnings": [],
        "errors": ["同一钉钉单号存在多个来源记录"],
        "raw_summary": {},
        "raw_candidates": candidates,
    }


def discover_expense_workflows(
    cursors: Dict[str, Dict[str, str]],
    running_approval_nos: Iterable[str],
    *,
    gateway: Optional[Any] = None,
) -> DiscoveryResult:
    """Discover all three DingTalk sources independently of imported requests.

    The first call intentionally has no historical date cutoff.  Later calls
    read changes after each source cursor while also re-reading every cached
    RUNNING approval, so a terminal transition cannot be missed.
    """

    reader = gateway or PostgresDiscoveryGateway()
    running = tuple(
        sorted({str(value).strip() for value in running_approval_nos if str(value).strip()})
    )
    next_cursors = {
        source_type: dict(cursor)
        for source_type, cursor in cursors.items()
        if source_type in ALLOWED_SOURCE_TYPES and cursor
    }
    query_timings: Dict[str, float] = {}
    rows_by_identity: Dict[tuple[str, str], Dict[str, Any]] = {}
    source_rows: list[tuple[str, Dict[str, Any]]] = []
    for source_type in ALLOWED_SOURCE_TYPES:
        started = time.perf_counter()
        rows = reader.fetch_source_changes(source_type, cursors.get(source_type), running)
        query_timings[source_type] = round(time.perf_counter() - started, 6)
        for raw_row in rows:
            row = dict(raw_row)
            source_id = str(row.get("source_id") or "").strip()
            if not source_id:
                continue
            identity = (source_type, source_id)
            prior = rows_by_identity.get(identity)
            if prior is None or _discovery_cursor_value(source_type, row) >= _discovery_cursor_value(source_type, prior):
                rows_by_identity[identity] = row
        cursor_values = [
            _discovery_cursor_value(source_type, dict(row))
            for row in rows
            if str(row.get("source_id") or "").strip()
        ]
        if cursor_values:
            updated_at, source_id = max(cursor_values)
            next_cursors[source_type] = {
                "updated_at": updated_at,
                "source_id": source_id,
            }

    source_rows.extend(
        (source_type, row)
        for (source_type, _), row in rows_by_identity.items()
    )
    applicant_ids: list[Any] = []
    for source_type, row in source_rows:
        if source_type == "monthly":
            applicant_ids.append(_json_object(row.get("raw_payload")).get("originatorUserId"))
        else:
            applicant_ids.append(row.get("creator_name"))
    user_names = reader.fetch_user_names(applicant_ids)

    grouped: Dict[str, list[Dict[str, Any]]] = defaultdict(list)
    for source_type, row in source_rows:
        candidate = _map_discovery_candidate(source_type, row, user_names)
        approval_no = str(candidate.get("approval_no") or "").strip()
        if approval_no:
            grouped[approval_no].append(candidate)

    candidates: list[Dict[str, Any]] = []
    conflicts: list[str] = []
    for approval_no in sorted(grouped):
        matches = grouped[approval_no]
        if len(matches) > 1:
            conflicts.append(approval_no)
            candidates.append(_conflict_candidate(approval_no, matches))
        else:
            candidates.append(matches[0])
    return DiscoveryResult(
        candidates=candidates,
        next_cursors=next_cursors,
        source_conflicts=conflicts,
        query_timings=query_timings,
    )


def _monthly_payment_query(
    *,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    approval_no: str = "",
    applicant_ids: Optional[Iterable[str]] = None,
    source_ids: Optional[Iterable[str]] = None,
    include_inactive: bool = False,
) -> list[Dict[str, Any]]:
    conditions = ["deleted_at IS NULL", "process_code = %s"]
    params: list[Any] = [MONTHLY_PAYMENT_PROCESS_CODE]
    normalized_approval_no = approval_no.strip()
    normalized_source_ids = sorted({str(value).strip() for value in source_ids or [] if str(value).strip()})
    if normalized_source_ids:
        conditions.append("id::text = ANY(%s)")
        params.append(normalized_source_ids)
    elif normalized_approval_no:
        conditions.append("BTRIM(raw_payload->>'businessId') = %s")
        params.append(normalized_approval_no)
    elif date_from is not None and date_to is not None:
        conditions.append("(create_time AT TIME ZONE 'Asia/Shanghai')::date BETWEEN %s AND %s")
        params.extend([date_from, date_to])
    else:
        raise ExternalExpenseError("查询条件缺少申请日期")
    normalized_applicant_ids = sorted({str(value).strip() for value in applicant_ids or [] if str(value).strip()})
    if normalized_applicant_ids:
        conditions.append("BTRIM(COALESCE(raw_payload->>'originatorUserId', '')) = ANY(%s)")
        params.append(normalized_applicant_ids)
    if not include_inactive:
        conditions.extend([
            "UPPER(BTRIM(COALESCE(status, ''))) = ANY(%s)",
            "COALESCE(result, '') !~* %s",
        ])
        params.extend([list(ALLOWED_APPROVAL_STATUSES), DISALLOWED_APPROVAL_RESULT_PATTERN])
    config = source_database_config()
    with source_connection(config.user_dbname) as conn:
        return conn.execute(
            f"""
            SELECT id::text AS source_id,
                   process_instance_id,
                   process_code,
                   create_time,
                   updated_at,
                   status,
                   result,
                   title,
                   raw_payload
            FROM public.ding_approval_instance
            WHERE {' AND '.join(conditions)}
            ORDER BY create_time DESC NULLS LAST, id DESC
            """,
            params,
        ).fetchall()


def _decoded_component_value(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    text = value.strip()
    if not text or text[0] not in "[{\"":
        return value
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return value


def _display_component_value(value: Any) -> Optional[str]:
    decoded = _decoded_component_value(value)
    if isinstance(decoded, list):
        parts = [_display_component_value(item) for item in decoded]
        return " / ".join(part for part in parts if part) or None
    if isinstance(decoded, dict):
        return _first_text(decoded.get("name"), decoded.get("label"), decoded.get("value"), decoded.get("text"))
    return _text(decoded)


def _monthly_component(form_values: Iterable[Dict[str, Any]], *prefixes: str) -> Optional[str]:
    for item in form_values:
        name = _text(item.get("name")) or ""
        if any(name.startswith(prefix) for prefix in prefixes):
            value = _display_component_value(item.get("value"))
            if value:
                return value
    return None


def _form_component_value(form_values: Iterable[Dict[str, Any]], *prefixes: str) -> Optional[str]:
    normalized_prefixes = tuple(prefix.casefold() for prefix in prefixes)
    for item in form_values:
        name = _first_text(item.get("name"), item.get("label")) or ""
        if not name.casefold().startswith(normalized_prefixes):
            continue
        value = _display_component_value(item.get("value"))
        if value:
            return value
    return None


def currency_from_execution_region(value: Any) -> Optional[str]:
    region = _text(value) or ""
    if MEXICO_EXECUTION_REGION_RE.search(region):
        return "MXN"
    if CHINA_EXECUTION_REGION_RE.search(region):
        return "CNY"
    return None


def currency_from_summary_text(value: Any) -> Optional[str]:
    """Return only currencies that are explicitly named in business text.

    A bare ``$`` is deliberately not accepted because it is ambiguous in
    Mexico.  This helper is primarily a recovery path for legacy DingTalk
    approvals whose source-table ``currency`` column was defaulted to CNY
    even though the approval text and original amount were in pesos.
    """
    text = _text(value) or ""
    if SUMMARY_MXN_CURRENCY_RE.search(text):
        return "MXN"
    if SUMMARY_USD_CURRENCY_RE.search(text):
        return "USD"
    if SUMMARY_CNY_CURRENCY_RE.search(text):
        return "CNY"
    return None


def currency_amount_from_summary_text(value: Any, currency: Optional[str] = None) -> Optional[float]:
    """Extract an explicitly stated total in the named currency.

    This is intentionally conservative and only accepts amounts adjacent to a
    currency name, preferring totals such as ``合计 76,800 比索``.  It is used
    to repair legacy imports whose source table stored only the converted CNY
    amount while the approval summary retained the original amount.
    """
    text = _text(value) or ""
    normalized_currency = normalize_currency(currency) or currency_from_summary_text(text)
    tokens = {
        "MXN": r"(?:\bMXN\b|MX\$|墨西哥比索|比索|(?<![A-Za-z])PESOS?(?![A-Za-z]))",
        "USD": r"(?:\bUSD\b|US\$|美元|美金|(?<![A-Za-z])D[ÓO]LARES?(?![A-Za-z]))",
        "CNY": r"(?:\bCNY\b|\bRMB\b|人民币(?:元)?)",
    }
    currency_token = tokens.get(str(normalized_currency or ""))
    if not currency_token:
        return None
    number = r"(?P<amount>\d+(?:[\s,，]\d{3})*(?:\.\d+)?)"
    total_prefix = r"(?:合计|总计|总额|总金额|monto\s+total|importe\s+total|total)"
    patterns = (
        rf"{total_prefix}[^\d]{{0,12}}{number}\s*{currency_token}",
        rf"{total_prefix}[^\d]{{0,12}}{currency_token}\s*[:：]?\s*{number}",
        rf"{number}\s*{currency_token}",
        rf"{currency_token}\s*[:：]?\s*{number}",
    )
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if not match:
            continue
        try:
            return float(match.group("amount").replace(" ", "").replace(",", "").replace("，", ""))
        except (TypeError, ValueError):
            continue
    return None


def resolve_approval_currency(currency_value: Any, execution_region: Any) -> tuple[Optional[str], Optional[str]]:
    currency_text = _text(currency_value)
    if currency_text:
        return normalize_currency(currency_text), "approval_currency"
    inferred = currency_from_execution_region(execution_region)
    return inferred, "execution_region" if inferred else None


def _monthly_money(value: Any) -> Optional[float]:
    text = _display_component_value(value)
    if not text:
        return None
    match = re.search(r"-?[0-9][0-9,]*(?:\.\d+)?", text)
    return _number(match.group(0).replace(",", "")) if match else None


def _monthly_payment_details(form_values: Iterable[Dict[str, Any]]) -> list[Dict[str, Any]]:
    table_value: Any = None
    for item in form_values:
        if (_text(item.get("name")) or "").startswith("申请付款明细"):
            table_value = _decoded_component_value(item.get("value"))
            break
    if not isinstance(table_value, list):
        return []
    details: list[Dict[str, Any]] = []
    for index, raw_row in enumerate(table_value, start=1):
        if not isinstance(raw_row, dict):
            continue
        cells = raw_row.get("rowValue")
        if not isinstance(cells, list):
            cells = raw_row.get("values") if isinstance(raw_row.get("values"), list) else []
        fields: Dict[str, Any] = {}
        for cell in cells:
            if not isinstance(cell, dict):
                continue
            label = _text(cell.get("label")) or _text(cell.get("name")) or ""
            if label:
                fields[label] = _decoded_component_value(cell.get("value"))
        payment_date = next(
            (_date_text(value) for label, value in fields.items() if "日期" in label and _date_text(value)),
            None,
        )
        amount = next(
            (_monthly_money(value) for label, value in fields.items() if "金额" in label and _monthly_money(value) is not None),
            None,
        )
        description = next(
            (_display_component_value(value) for label, value in fields.items() if any(word in label for word in ("说明", "事由", "摘要", "备注")) and _display_component_value(value)),
            None,
        )
        details.append({
            "row_no": index,
            "payment_date": payment_date,
            "amount": amount,
            "description": description,
            "fields": fields,
        })
    return details


def _related_approval_nos(form_values: Iterable[Dict[str, Any]]) -> list[str]:
    values: list[str] = []
    for item in form_values:
        if "关联审批" not in (_text(item.get("name")) or ""):
            continue
        decoded = _decoded_component_value(item.get("value"))
        stack = [decoded]
        while stack:
            current = stack.pop()
            if isinstance(current, dict):
                for key in ("businessId", "business_id", "approvalNo", "approval_no"):
                    candidate = _text(current.get(key))
                    if candidate:
                        values.extend(APPROVAL_REFERENCE_RE.findall(candidate))
                stack.extend(current.values())
            elif isinstance(current, list):
                stack.extend(current)
            else:
                values.extend(APPROVAL_REFERENCE_RE.findall(_text(current) or ""))
    return sorted(set(values))


def _attachment_objects(value: Any) -> list[Dict[str, Any]]:
    decoded = _decoded_component_value(value)
    found: list[Dict[str, Any]] = []
    stack = [decoded]
    while stack:
        current = stack.pop()
        if isinstance(current, str):
            decoded_current = _decoded_component_value(current)
            if decoded_current is not current:
                stack.append(decoded_current)
            continue
        if isinstance(current, dict):
            if current.get("fileId") or current.get("file_id"):
                found.append(current)
            stack.extend(current.values())
        elif isinstance(current, list):
            stack.extend(current)
    return found


def _monthly_attachments(instance: Dict[str, Any]) -> list[Dict[str, Any]]:
    raw_payload = _json_object(instance.get("raw_payload"))
    approval_no = _text(raw_payload.get("businessId")) or ""
    process_instance_id = _text(instance.get("process_instance_id")) or ""
    source_id = _text(instance.get("source_id")) or ""
    objects: list[Dict[str, Any]] = []
    for component in _form_values(raw_payload):
        objects.extend(_attachment_objects(component.get("value")))
    unique: Dict[str, Dict[str, Any]] = {}
    for attachment in objects:
        file_id = _text(attachment.get("fileId")) or _text(attachment.get("file_id")) or ""
        if not file_id:
            continue
        stable_id = f"monthly-{process_instance_id or source_id}-{file_id}"
        try:
            file_size = int(attachment.get("fileSize")) if attachment.get("fileSize") is not None else None
        except (TypeError, ValueError):
            file_size = None
        unique.setdefault(stable_id, {
            "source_type": "monthly",
            "source_id": source_id,
            "approval_no": approval_no,
            "attachment_id": stable_id,
            "row_no": None,
            "file_id": file_id,
            "file_name": _first_text(attachment.get("fileName"), attachment.get("name")) or f"月结附件-{file_id}",
            "file_type": (_first_text(attachment.get("fileType"), attachment.get("type")) or "").lower(),
            "file_size": file_size,
            "created_at": _workflow_event_time(instance.get("create_time")),
        })
    return list(unique.values())


def map_monthly_payment(instance: Dict[str, Any], user_names: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    raw_payload = _json_object(instance.get("raw_payload"))
    form_values = _form_values(raw_payload)
    details = _monthly_payment_details(form_values)
    declared_total = _monthly_money(_monthly_component(form_values, "合计总额", "总计"))
    detail_amounts = [detail["amount"] for detail in details if detail.get("amount") is not None]
    calculated_total = round(sum(detail_amounts), 2) if detail_amounts else None
    total = declared_total if declared_total is not None else calculated_total
    application_time = _workflow_event_time(instance.get("create_time"))
    application_date = application_time[:10] if application_time else None
    currency_text = _form_component_value(form_values, *CURRENCY_COMPONENT_PREFIXES)
    execution_region = _form_component_value(form_values, *EXECUTION_REGION_COMPONENT_PREFIXES)
    currency, _ = resolve_approval_currency(currency_text, execution_region)
    base_amount: Optional[float] = total if currency == "CNY" else None
    fx_rate: Optional[float] = 1.0 if currency == "CNY" else None
    fx_actual_date = application_date
    rate_error: Optional[str] = None
    if total is not None and total > 0 and currency in {"USD", "MXN"} and application_date:
        try:
            rate = fetch_rates(date.fromisoformat(application_date), [currency])[currency]
            fx_rate = float(rate["cny_per_unit"])
            fx_actual_date = str(rate["actual_date"])
            base_amount = multiply_money(total, fx_rate)
        except (FxRateError, ValueError) as exc:
            rate_error = str(exc)
    applicant_id = _text(raw_payload.get("originatorUserId")) or ""
    raw_row = {
        "source_type": "monthly",
        "source_id": _text(instance.get("source_id")) or "",
        "effective_date": application_date,
        "approval_no": _text(raw_payload.get("businessId")) or "",
        "creator_name": applicant_id,
        "applicant_department": _text(raw_payload.get("originatorDeptName")),
        "approval_title": _first_text(instance.get("title"), raw_payload.get("title")),
        "approval_status": instance.get("status"),
        "approval_result": instance.get("result"),
        "execution_region": execution_region,
        "beneficiary": _monthly_component(form_values, "收款账户信息", "收款信息", "收款账户"),
        "expense_type": _monthly_component(form_values, "付款分类", "费用性质"),
        "summary": _monthly_component(form_values, "申请事由", "付款事由") or next((detail.get("description") for detail in details if detail.get("description")), None),
        "project": None,
        "needed_payment_date": min((detail["payment_date"] for detail in details if detail.get("payment_date")), default=None),
        "source_currency": currency_text,
        "source_amount": total,
        "base_currency_amount": base_amount,
        "source_created_at": instance.get("create_time"),
        "source_updated_at": instance.get("updated_at"),
        "raw_data": raw_payload,
    }
    mapped = map_external_expense(raw_row, user_names)
    if rate_error:
        mapped["errors"] = [message for message in mapped["errors"] if message != "缺少应付金额"]
        mapped["errors"].append(f"找不到月结付款汇率：{rate_error}")
        mapped["amount"] = total
        mapped["currency"] = currency
        mapped["request_data"]["amount"] = total
        mapped["request_data"]["currency"] = currency
    if declared_total is not None and calculated_total is not None and abs(declared_total - calculated_total) > 0.01:
        mapped["warnings"].append("月结合计金额与付款明细合计不一致，请核对")
    if len(details) > 1:
        mapped["warnings"].append(f"月结包含 {len(details)} 行付款明细，已按合计金额生成一条请款")
        detail_summaries = [
            str(detail.get("description") or "").strip()
            for detail in details
            if str(detail.get("description") or "").strip()
        ]
        if detail_summaries:
            concise_details = "；".join(dict.fromkeys(detail_summaries))
            mapped["summary"] = f"{mapped['summary']}｜{concise_details}"[:500]
            mapped["request_data"]["summary"] = mapped["summary"]
    if currency not in {"CNY", "USD", "MXN"}:
        mapped["errors"].append("月结付款缺少或无法识别币种")
    related = _related_approval_nos(form_values)
    external_source = mapped["request_data"]["raw_extra"]["external_source"]
    external_source.update({
        "system": "dingtalk_monthly_payment",
        "process_code": MONTHLY_PAYMENT_PROCESS_CODE,
        "process_instance_id": _text(instance.get("process_instance_id")),
        "monthly_payment_details": details,
        "related_approval_nos": related,
        "fx_rate_actual_date": fx_actual_date,
    })
    mapped["request_data"]["payment_account"] = _monthly_component(form_values, "付款账户类型", "付款账户")
    mapped["request_data"]["fx_rate_cny_per_unit"] = fx_rate
    mapped["request_data"]["fx_rate_actual_date"] = fx_actual_date
    mapped["related_approval_nos"] = related
    return mapped


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
        "UPPER(BTRIM(COALESCE(approval_status, ''))) = ANY(%s)",
        "(BTRIM(COALESCE(execution_region, '')) = '' OR COALESCE(execution_region, '') ~* %s)",
        "COALESCE(approval_result, '') !~* %s",
        "(base_currency_amount IS NULL OR base_currency_amount <> 0)",
    ]
    params: list[Any] = [
        normalized_sources,
        list(ALLOWED_APPROVAL_STATUSES),
        ALLOWED_EXECUTION_REGION_PATTERN,
        DISALLOWED_APPROVAL_RESULT_PATTERN,
    ]
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
    normalized_sources = [source for source in source_types if source in ALLOWED_SOURCE_TYPES]
    standard_sources = [source for source in normalized_sources if source in STANDARD_SOURCE_TYPES]
    rows: list[Dict[str, Any]] = []
    applicant_rows: list[Dict[str, Any]] = []
    if standard_sources:
        condition_sql, params = _preview_conditions(date_from, date_to, standard_sources, approval_no, applicant_ids)
        option_sql, option_params = _preview_conditions(date_from, date_to, standard_sources, approval_no, [])
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
            rows.extend(conn.execute(query, params).fetchall())
            applicant_rows.extend(conn.execute(option_query, option_params).fetchall())
    monthly_rows: list[Dict[str, Any]] = []
    monthly_option_rows: list[Dict[str, Any]] = []
    if "monthly" in normalized_sources:
        monthly_rows = _monthly_payment_query(
            date_from=date_from,
            date_to=date_to,
            approval_no=approval_no,
            applicant_ids=applicant_ids,
        )
        monthly_option_rows = monthly_rows if not list(applicant_ids or []) else _monthly_payment_query(
            date_from=date_from,
            date_to=date_to,
            approval_no=approval_no,
            applicant_ids=[],
        )
        for instance in monthly_option_rows:
            raw_payload = _json_object(instance.get("raw_payload"))
            applicant_rows.append({
                "creator_name": _text(raw_payload.get("originatorUserId")),
                "applicant_department": _text(raw_payload.get("originatorDeptName")),
                "approval_title": _first_text(instance.get("title"), raw_payload.get("title")),
                "count": 1,
            })
    user_names = fetch_dingtalk_user_names(
        [
            *[row.get("creator_name") for row in [*rows, *applicant_rows]],
            *[
                _text(_json_object(instance.get("raw_payload")).get("originatorUserId"))
                for instance in monthly_rows
            ],
        ]
    )
    mapped_rows = [map_external_expense(row, user_names) for row in rows]
    mapped_rows.extend(map_monthly_payment(instance, user_names) for instance in monthly_rows)
    mapped_rows.sort(
        key=lambda row: (row.get("application_date") or "", row.get("approval_no") or "", row.get("source_type") or ""),
        reverse=True,
    )
    return {
        "rows": mapped_rows,
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
    monthly_rows: list[Dict[str, Any]] = []
    config = source_database_config()
    with source_connection(config.user_dbname) as conn:
        for start in range(0, len(normalized), 500):
            chunk = normalized[start : start + 500]
            monthly_rows.extend(
                conn.execute(
                    """
                    SELECT id::text AS source_id, process_instance_id, process_code,
                           create_time, updated_at, status, result, title,
                           raw_payload
                    FROM public.ding_approval_instance
                    WHERE deleted_at IS NULL
                      AND process_code = %s
                      AND BTRIM(raw_payload->>'businessId') = ANY(%s)
                    ORDER BY BTRIM(raw_payload->>'businessId'), updated_at DESC NULLS LAST, id DESC
                    """,
                    [MONTHLY_PAYMENT_PROCESS_CODE, chunk],
                ).fetchall()
            )
    monthly_user_ids = [
        _text(_json_object(row.get("raw_payload")).get("originatorUserId"))
        for row in monthly_rows
    ]
    user_names = fetch_dingtalk_user_names([
        *[row.get("creator_name") for row in source_rows],
        *monthly_user_ids,
    ])
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
    for monthly_row in monthly_rows:
        mapped = map_monthly_payment(monthly_row, user_names)
        external_source = mapped["request_data"]["raw_extra"]["external_source"]
        metadata.append({
            "approval_no": mapped["approval_no"],
            "source_type": mapped["source_type"],
            "source_label": mapped["source_label"],
            "source_id": mapped["source_id"],
            **external_source,
        })
    return metadata


def fetch_external_expense_attachments(approval_nos: Iterable[str]) -> list[Dict[str, Any]]:
    normalized = sorted({str(value or "").strip() for value in approval_nos if str(value or "").strip()})
    if not normalized:
        return []
    rows: list[Dict[str, Any]] = []
    with source_connection() as conn:
        for start in range(0, len(normalized), 500):
            chunk = normalized[start : start + 500]
            rows.extend(
                conn.execute(
                    """
                    WITH parents AS (
                        SELECT 'operation'::text AS source_type,
                               id::text AS source_id,
                               id AS parent_id,
                               BTRIM(approval_no) AS approval_no
                        FROM public.approval_expense_operation

                        UNION ALL

                        SELECT 'purchase'::text AS source_type,
                               id::text AS source_id,
                               id AS parent_id,
                               BTRIM(approval_no) AS approval_no
                        FROM public.approval_expense_purchase
                    )
                    SELECT parents.source_type,
                           parents.source_id,
                           parents.approval_no,
                           attachments.id::text AS attachment_id,
                           attachments.row_no,
                           attachments.attachment_type,
                           attachments.file_name,
                           attachments.raw_data,
                           attachments.created_at
                    FROM parents
                    JOIN public.approval_expense_attachments AS attachments
                      ON attachments.parent_type = parents.source_type
                     AND attachments.parent_id = parents.parent_id
                    WHERE parents.approval_no = ANY(%s)
                    ORDER BY parents.approval_no,
                             parents.source_type,
                             parents.source_id,
                             attachments.row_no,
                             attachments.id
                    """,
                    [chunk],
                ).fetchall()
            )
    attachments: list[Dict[str, Any]] = []
    for row in rows:
        raw_data = row.get("raw_data")
        if isinstance(raw_data, str):
            try:
                raw_data = json.loads(raw_data)
            except json.JSONDecodeError:
                raw_data = {}
        if not isinstance(raw_data, dict):
            raw_data = {}
        file_id = _text(raw_data.get("fileId"))
        file_name = _text(row.get("file_name")) or _text(raw_data.get("fileName"))
        file_type = (_text(row.get("attachment_type")) or _text(raw_data.get("fileType")) or "").lower()
        try:
            file_size = int(raw_data.get("fileSize")) if raw_data.get("fileSize") is not None else None
        except (TypeError, ValueError):
            file_size = None
        attachments.append(
            {
                "source_type": _text(row.get("source_type")) or "",
                "source_id": _text(row.get("source_id")) or "",
                "approval_no": _text(row.get("approval_no")) or "",
                "attachment_id": _text(row.get("attachment_id")) or "",
                "row_no": row.get("row_no"),
                "file_id": file_id or "",
                "file_name": file_name or f"钉钉附件-{row.get('attachment_id')}",
                "file_type": file_type,
                "file_size": file_size,
                "created_at": _datetime_text(row.get("created_at")),
            }
        )
    config = source_database_config()
    monthly_rows: list[Dict[str, Any]] = []
    with source_connection(config.user_dbname) as conn:
        for start in range(0, len(normalized), 500):
            chunk = normalized[start : start + 500]
            monthly_rows.extend(
                conn.execute(
                    """
                    SELECT id::text AS source_id, process_instance_id, create_time, raw_payload
                    FROM public.ding_approval_instance
                    WHERE deleted_at IS NULL
                      AND process_code = %s
                      AND BTRIM(raw_payload->>'businessId') = ANY(%s)
                    ORDER BY BTRIM(raw_payload->>'businessId'), updated_at DESC NULLS LAST, id DESC
                    """,
                    [MONTHLY_PAYMENT_PROCESS_CODE, chunk],
                ).fetchall()
            )
    for monthly_row in monthly_rows:
        attachments.extend(_monthly_attachments(monthly_row))
    return attachments


def _workflow_event_time(value: Any) -> Optional[str]:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        timestamp = float(value)
        if timestamp > 10_000_000_000:
            timestamp /= 1000
        return datetime.fromtimestamp(timestamp, tz=timezone.utc).astimezone(
            SHANGHAI_TZ
        ).replace(microsecond=0).isoformat()
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


def _normalized_workflow_comment(value: Any) -> str:
    normalized = unicodedata.normalize("NFKC", _text(value) or "")
    return " ".join(normalized.split())


def _workflow_event_key(
    process_instance_id: str,
    operation: Dict[str, Any],
    *,
    node_name: Optional[str] = None,
    event_time: Optional[str] = None,
) -> str:
    stable_parts = [
        process_instance_id,
        _text(operation.get("activityId")) or "",
        (_text(operation.get("type")) or "").upper(),
        _text(operation.get("userId")) or "",
        event_time or _workflow_event_time(operation.get("date")) or "",
        node_name or _text(operation.get("showName")) or "",
        _normalized_workflow_comment(operation.get("remark")),
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
            "event_key": _workflow_event_key(
                process_instance_id,
                operation,
                node_name=stage_name,
                event_time=event_time,
            ),
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


def _task_assignee_ids(task: Dict[str, Any]) -> list[str]:
    values: list[Any] = []
    for key in (
        "userId",
        "assigneeUserId",
        "assigneeId",
        "processorId",
        "executorUserId",
    ):
        if task.get(key) is not None:
            values.append(task.get(key))
    for key in ("userIds", "assigneeUserIds", "processorIds", "executorUserIds"):
        raw = task.get(key)
        if isinstance(raw, list):
            values.extend(raw)
        elif isinstance(raw, str):
            decoded = _json_list(raw)
            values.extend(decoded if decoded else raw.split(","))
    return list(dict.fromkeys(filter(None, (_text(value) for value in values))))


def _current_workflow_task(
    tasks: list[Dict[str, Any]],
    events: list[Dict[str, Any]],
    user_names: Dict[str, str],
) -> Dict[str, Optional[str]]:
    active: list[tuple[str, int, Dict[str, Any]]] = []
    for index, task in enumerate(tasks):
        status = (_text(task.get("status")) or "").upper()
        if status not in {"RUNNING", "PROCESSING", "PENDING"}:
            continue
        entered_at = _workflow_event_time(
            task.get("startTime")
            or task.get("createTime")
            or task.get("createdAt")
            or task.get("updatedAt")
        )
        active.append((entered_at or "", index, task))
    if not active:
        return {
            "current_node_name": None,
            "current_approver_id": None,
            "current_approver_name": None,
            "current_node_entered_at": None,
        }

    entered_at, _, task = max(active, key=lambda item: (item[0], item[1]))
    activity_id = _text(task.get("activityId")) or ""
    node_name = (
        _text(task.get("activityName"))
        or _text(task.get("showName"))
        or _text(task.get("name"))
        or next(
            (
                _text(event.get("stage_name"))
                for event in reversed(events)
                if activity_id and _text(event.get("activity_id")) == activity_id
            ),
            None,
        )
    )
    assignee_ids = _task_assignee_ids(task)
    approver_id = assignee_ids[0] if assignee_ids else None
    return {
        "current_node_name": node_name,
        "current_approver_id": approver_id,
        "current_approver_name": (
            user_names.get(approver_id) or "未识别人员"
            if approver_id
            else None
        ),
        "current_node_entered_at": entered_at or None,
    }


def parse_dingtalk_workflow_instance(
    instance: Dict[str, Any],
    user_names: Dict[str, str],
) -> Dict[str, Any]:
    process_instance_id = _text(instance.get("process_instance_id")) or ""
    operations = [
        dict(operation)
        for operation in _json_list(instance.get("operation_records"))
        if isinstance(operation, dict)
    ]
    tasks = [
        dict(task)
        for task in _json_list(instance.get("tasks"))
        if isinstance(task, dict)
    ]
    current_activity_ids = {
        _text(task.get("activityId")) or ""
        for task in tasks
        if (_text(task.get("status")) or "").upper()
        in {"RUNNING", "PROCESSING", "PENDING"}
        and _text(task.get("activityId"))
    }
    events = _parse_workflow_events(
        process_instance_id,
        operations,
        current_activity_ids,
        user_names,
    )
    current_task = _current_workflow_task(tasks, events, user_names)
    return {
        "approval_no": _text(instance.get("approval_no")) or "",
        "process_instance_id": process_instance_id,
        "status": (_text(instance.get("status")) or "").upper(),
        "result": (_text(instance.get("result")) or "").lower(),
        "title": _text(instance.get("title")),
        "updated_at": _workflow_event_time(instance.get("updated_at")),
        "events": events,
        **current_task,
    }


def _workflow_user_ids(instances: Iterable[Dict[str, Any]]) -> list[str]:
    user_ids: set[str] = set()
    for instance in instances:
        for operation in _json_list(instance.get("operation_records")):
            if isinstance(operation, dict):
                user_id = _text(operation.get("userId")) or ""
                if user_id:
                    user_ids.add(user_id)
        for task in _json_list(instance.get("tasks")):
            if isinstance(task, dict):
                user_ids.update(_task_assignee_ids(task))
    return sorted(user_ids)


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
        user_ids = _workflow_user_ids(instances)
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
    seen_approval_nos: set[str] = set()
    for instance in instances:
        approval_no = _text(instance.get("approval_no")) or ""
        if not approval_no or approval_no in seen_approval_nos:
            continue
        seen_approval_nos.add(approval_no)
        workflows.append(parse_dingtalk_workflow_instance(instance, user_names))
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
    explicit_paid_phrase = bool(PAID_PHRASE_RE.search(comment))
    yuewei_payment_confirmation = bool(YUEWEI_PAYMENT_CONFIRM_RE.search(comment))
    if not explicit_paid_phrase and not yuewei_payment_confirmation:
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
    if yuewei_payment_confirmation and not amounts:
        return "review_required", "“悦为支付”评论缺少可核对的明确金额"
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
    monthly_ids = sorted(source_id for source_type, source_id in keys if source_type == "monthly" and source_id.isdigit())
    if not operation_ids and not purchase_ids and not monthly_ids:
        return []
    key_conditions: list[str] = []
    params: list[Any] = []
    if operation_ids:
        key_conditions.append("(source_type = 'operation' AND source_id = ANY(%s))")
        params.append(operation_ids)
    if purchase_ids:
        key_conditions.append("(source_type = 'purchase' AND source_id = ANY(%s))")
        params.append(purchase_ids)
    source_rows: list[Dict[str, Any]] = []
    if key_conditions:
        query = f"""
            {SOURCE_ROWS_CTE}
            SELECT * FROM source_rows
            WHERE {' OR '.join(key_conditions)}
            ORDER BY source_type, source_id
        """
        with source_connection() as conn:
            source_rows = conn.execute(query, params).fetchall()
    monthly_rows = _monthly_payment_query(source_ids=monthly_ids) if monthly_ids else []
    monthly_user_ids = [
        _text(_json_object(row.get("raw_payload")).get("originatorUserId"))
        for row in monthly_rows
    ]
    user_names = fetch_dingtalk_user_names([
        *[row.get("creator_name") for row in source_rows],
        *monthly_user_ids,
    ])
    rows = [map_external_expense(row, user_names) for row in source_rows]
    rows.extend(map_monthly_payment(row, user_names) for row in monthly_rows)
    approval_nos = sorted({str(row.get("approval_no") or "").strip() for row in rows if str(row.get("approval_no") or "").strip()})
    conflicts: set[str] = set()
    if approval_nos:
        metadata = fetch_external_expense_metadata(approval_nos)
        metadata_counts = Counter(str(row.get("approval_no") or "").strip() for row in metadata)
        conflicts = {approval_no for approval_no, count in metadata_counts.items() if approval_no and count > 1}
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
    execution_region = (
        _text(row.get("execution_region"))
        or _form_component_value(form_values, *EXECUTION_REGION_COMPONENT_PREFIXES)
        or ""
    )
    base_amount = _number(row.get("base_currency_amount"))
    source_amount = _number(row.get("source_amount"))
    form_currency_text = _form_component_value(form_values, *CURRENCY_COMPONENT_PREFIXES)
    table_currency_text = row.get("source_currency")
    table_currency = normalize_currency(table_currency_text)
    summary_currency = currency_from_summary_text(summary)
    region_currency = currency_from_execution_region(execution_region)
    source_currency_raw: Optional[str] = None
    if _text(form_currency_text):
        source_currency = normalize_currency(form_currency_text)
        currency_source = "approval_currency"
        source_currency_raw = _text(form_currency_text)
    elif summary_currency:
        source_currency = summary_currency
        currency_source = "summary_text"
    elif table_currency in {"USD", "MXN"}:
        # USD/MXN in the source column represents a meaningful original
        # currency.  CNY, however, is a known default on legacy Mexico rows,
        # so execution region must take precedence over that fallback.
        source_currency = table_currency
        currency_source = "approval_currency"
        source_currency_raw = _text(table_currency_text)
    elif region_currency:
        source_currency = region_currency
        currency_source = "execution_region"
    else:
        source_currency = table_currency
        currency_source = "source_table_currency" if table_currency else None
        source_currency_raw = _text(table_currency_text)
    warnings: list[str] = []
    errors: list[str] = []
    if source_currency in {"USD", "MXN"}:
        amount = source_amount
        fx_rate = None
        if source_amount is None:
            errors.append("缺少原币应付金额")
        elif source_amount <= 0:
            errors.append("原币应付金额必须大于 0")
        elif base_amount is not None:
            fx_rate = float(Decimal(str(base_amount)) / Decimal(str(source_amount)))
    elif source_currency == "CNY":
        amount = base_amount
        fx_rate = 1.0
    else:
        amount = base_amount
        fx_rate = None
        if _text(form_currency_text) or _text(table_currency_text):
            errors.append("钉钉审批中的币种无法识别，仅支持 CNY、USD 和 MXN")
        else:
            errors.append("审批未填写币种，且无法根据执行地区判断币种")
    if not beneficiary:
        warnings.append("缺少收款信息")
    if len(beneficiary_values) > 1:
        warnings.append("存在多个收款人，请确认")
    if base_amount == 0:
        errors.append("应付金额为 0，暂不导入")
    if not approval_no:
        errors.append("缺少钉钉单号")
    if base_amount is None:
        errors.append("缺少应付金额")
    elif base_amount < 0:
        errors.append("应付金额不能为负数")
    if execution_region and not execution_region_is_allowed(execution_region):
        errors.append("执行地区仅允许中国或墨西哥")
    if approval_status not in ALLOWED_APPROVAL_STATUSES:
        errors.append("审批状态不允许导入")
    if approval_result_is_disallowed(approval_result):
        errors.append("审批已拒绝、作废或终止，默认不导入")

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
        "currency": source_currency or "CNY",
        "base_amount_cny": base_amount,
        "fx_rate_cny_per_unit": fx_rate,
        "fx_rate_date": application_date,
        "fx_rate_actual_date": application_date,
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
                "source_currency": source_currency,
                "source_currency_raw": source_currency_raw,
                "source_currency_table": _text(table_currency_text),
                "currency_source": currency_source,
                "execution_region": execution_region or None,
                "source_amount": source_amount,
                "base_currency_amount": base_amount,
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
        "base_amount_cny": base_amount,
        "currency": source_currency or "CNY",
        "beneficiary": beneficiary,
        "needed_payment_date": needed_payment_date,
        "warnings": warnings,
        "errors": errors,
        "source_conflict": False,
        "request_data": request_data,
    }


def approval_result_is_disallowed(value: Any) -> bool:
    return bool(DISALLOWED_APPROVAL_RESULT_RE.search(_text(value) or ""))


def execution_region_is_allowed(value: Any) -> bool:
    return bool(re.search(ALLOWED_EXECUTION_REGION_PATTERN, _text(value) or "", re.IGNORECASE))


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
