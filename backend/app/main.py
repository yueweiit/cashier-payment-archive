from __future__ import annotations

import json
import mimetypes
import shutil
import uuid
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import quote

from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, Request, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .attachment_io import save_embedded_image_attachments, save_embedded_payment_vouchers
from .db import (
    DATA_DIR,
    ROOT_DIR,
    connect,
    init_db,
    now_iso,
    payment_record_hash,
    refresh_payment_summaries,
    row_to_dict,
    rows_to_dicts,
    write_audit,
)
from .excel_io import (
    TARGET_FIELDS,
    content_hash,
    detect_table_headers,
    export_workbook,
    normalize_request_business_fields,
    parse_batch_dates,
    parse_dingtalk_file,
    parse_weekly_excel,
    suggest_mapping,
)
from .external_expenses import (
    ALLOWED_SOURCE_TYPES,
    ExternalExpenseError,
    fetch_external_expense_metadata,
    fetch_external_expenses,
    preview_external_expenses,
)
from .security import new_session_token, verify_password, hash_password
from .snapshots import (
    cleanup_batch_snapshot_files,
    create_batch_snapshot,
    ensure_draft_baselines,
    restore_batch_from_baseline,
)


app = FastAPI(title="出纳请款明细系统")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class LoginIn(BaseModel):
    username: str
    password: str


class ChangePasswordIn(BaseModel):
    current_password: str
    new_password: str
    confirm_password: str


class BatchIn(BaseModel):
    name: str
    start_date: Optional[str] = None
    end_date: Optional[str] = None


class RolloverIn(BaseModel):
    name: str
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    copy_mode: str = "unfinished"


class SheetOrderIn(BaseModel):
    sheet_order: list[str] = Field(default_factory=list)


class RequestIn(BaseModel):
    dingding_id: Optional[str] = None
    applicant: Optional[str] = None
    payment_account: Optional[str] = None
    expense_type: Optional[str] = None
    summary: Optional[str] = None
    style_name: Optional[str] = None
    amount: Optional[float] = None
    paid_amount: Optional[float] = None
    pending_amount: Optional[float] = None
    currency: str = "CNY"
    project: Optional[str] = None
    bu: Optional[str] = None
    payee_account: Optional[str] = None
    payee_name: Optional[str] = None
    bank_name: Optional[str] = None
    invoice_status: Optional[str] = None
    needed_payment_date: Optional[str] = None
    owner_confirmation: Optional[str] = None
    finance_review: Optional[str] = None
    finance_manager_approval: Optional[str] = None
    general_manager_approval: Optional[str] = None
    general_manager_approval_date: Optional[str] = None
    general_manager_opinion: Optional[str] = None
    actual_payment_date: Optional[str] = None
    remark: Optional[str] = None
    payment_status: Optional[str] = None
    overdue_status: Optional[str] = None
    payer: Optional[str] = None
    source_sheet: Optional[str] = None
    source_row: Optional[int] = None
    raw_extra: Dict[str, Any] = Field(default_factory=dict)


class RequestPatch(RequestIn):
    reason: Optional[str] = None


class CorrectionIn(BaseModel):
    request_id: int
    changes: Dict[str, Any]
    reason: str


class BulkRequestsIn(BaseModel):
    creates: list[Dict[str, Any]] = Field(default_factory=list)
    updates: list[Dict[str, Any]] = Field(default_factory=list)
    deletes: list[int] = Field(default_factory=list)
    reason: Optional[str] = None


class ExternalExpensePreviewIn(BaseModel):
    batch_id: int
    date_from: str = ""
    date_to: str = ""
    source_types: list[str] = Field(default_factory=lambda: list(ALLOWED_SOURCE_TYPES))
    approval_no: str = ""
    applicant_ids: list[str] = Field(default_factory=list)
    result_filter: str = "matched"
    page: int = 1
    page_size: int = 50


class ExternalExpenseKey(BaseModel):
    source_type: str
    source_id: str


class ExternalExpenseImportIn(BaseModel):
    items: list[ExternalExpenseKey] = Field(default_factory=list)


class UserIn(BaseModel):
    username: str
    password: str
    role: str
    display_name: str
    active: bool = True


class UserPatch(BaseModel):
    password: Optional[str] = None
    role: Optional[str] = None
    display_name: Optional[str] = None
    active: Optional[bool] = None


class DictionaryIn(BaseModel):
    kind: str
    value: str
    active: bool = True


class AttachmentIn(BaseModel):
    label: Optional[str] = None
    url_path: str


class PaymentRecordIn(BaseModel):
    amount: float = Field(gt=0)
    payment_date: str
    payer: Optional[str] = None
    payment_account: Optional[str] = None
    bank_reference: Optional[str] = None
    remark: Optional[str] = None
    reason: Optional[str] = None


class PaymentRecordPatch(BaseModel):
    amount: Optional[float] = Field(default=None, gt=0)
    payment_date: Optional[str] = None
    payer: Optional[str] = None
    payment_account: Optional[str] = None
    bank_reference: Optional[str] = None
    remark: Optional[str] = None
    reason: Optional[str] = None


IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}
PAYMENT_VOUCHER_EXTENSIONS = IMAGE_EXTENSIONS | {".pdf"}
MAX_IMAGE_BYTES = 12 * 1024 * 1024
DEFAULT_RESET_PASSWORD = "123456"
ROLE_BUSINESS = "business"
ROLE_FINANCE = "finance"
ROLE_GENERAL_MANAGER = "general_manager"
ROLE_ADMIN = "admin"
PRIVILEGED_ROLES = (ROLE_GENERAL_MANAGER, ROLE_ADMIN)
ALL_ROLES = (ROLE_BUSINESS, ROLE_FINANCE, ROLE_GENERAL_MANAGER, ROLE_ADMIN)
FINANCE_FIELD_ROLES = (ROLE_FINANCE, *PRIVILEGED_ROLES)
GENERAL_MANAGER_ROLES = PRIVILEGED_ROLES
DERIVED_PAYMENT_FIELDS = {
    "paid_amount",
    "pending_amount",
    "finance_review",
    "actual_payment_date",
    "payer",
    "payment_status",
}
FINANCE_CONTROLLED_FIELDS = {
    "paid_amount",
    "finance_review",
    "finance_manager_approval",
    "actual_payment_date",
    "payment_status",
    "overdue_status",
    "payer",
}
GENERAL_MANAGER_CONTROLLED_FIELDS = {
    "general_manager_approval",
    "general_manager_approval_date",
    "general_manager_opinion",
}
REQUEST_FIELD_LABELS = {
    "applicant": "申请人",
    "paid_amount": "已支付金额",
    "pending_amount": "待付款金额",
    "finance_review": "财务审批",
    "finance_manager_approval": "财务主管审批",
    "actual_payment_date": "财务付款时间",
    "payment_status": "付款情况",
    "overdue_status": "逾期情况",
    "payer": "付款人",
    "general_manager_approval": "总经理审批",
    "general_manager_approval_date": "总经理审批时间",
    "general_manager_opinion": "总经理意见",
}
REQUEST_WRITE_FIELDS = {
    "dingding_id",
    "applicant",
    "payment_account",
    "expense_type",
    "summary",
    "style_name",
    "amount",
    "paid_amount",
    "pending_amount",
    "currency",
    "project",
    "bu",
    "payee_account",
    "payee_name",
    "bank_name",
    "invoice_status",
    "needed_payment_date",
    "owner_confirmation",
    "finance_review",
    "finance_manager_approval",
    "general_manager_approval",
    "general_manager_approval_date",
    "general_manager_opinion",
    "actual_payment_date",
    "remark",
    "payment_status",
    "overdue_status",
    "payer",
    "source_sheet",
    "source_row",
    "content_hash",
    "raw_extra_json",
    "copied_from_request_id",
}


def user_public(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": row["id"],
        "username": row["username"],
        "role": row["role"],
        "display_name": row["display_name"],
        "active": bool(row["active"]),
    }


def attachment_public(row: Dict[str, Any]) -> Dict[str, Any]:
    data = row_to_dict(row) if not isinstance(row, dict) else dict(row)
    if data.get("attachment_type") == "image":
        data["file_url"] = f"/api/attachments/{data['id']}/file"
    return data


def attachments_public(rows) -> list[Dict[str, Any]]:
    return [attachment_public(row) for row in rows]


def payment_voucher_public(row: Dict[str, Any]) -> Dict[str, Any]:
    data = row_to_dict(row) if not isinstance(row, dict) else dict(row)
    data["file_url"] = f"/api/payment-vouchers/{data['id']}/file"
    data["voucher_type"] = "pdf" if data.get("mime_type") == "application/pdf" else "image"
    return data


def payment_record_public(conn, row: Dict[str, Any]) -> Dict[str, Any]:
    data = row_to_dict(row) if not isinstance(row, dict) else dict(row)
    vouchers = conn.execute("SELECT * FROM payment_vouchers WHERE payment_id = ? ORDER BY id", (data["id"],)).fetchall()
    data["vouchers"] = [payment_voucher_public(item) for item in vouchers]
    data["inherited"] = payment_record_is_inherited(data)
    return data


def payment_record_is_inherited(payment: Dict[str, Any]) -> bool:
    return bool(payment.get("copied_from_payment_id") or payment.get("source_type") == "rollover")


def payment_records_public(conn, request_id: int) -> list[Dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT payment_records.*, users.display_name AS creator_name
        FROM payment_records
        LEFT JOIN users ON users.id = payment_records.created_by
        WHERE payment_records.request_id = ?
        ORDER BY CASE WHEN payment_date IS NULL OR TRIM(payment_date) = '' THEN 1 ELSE 0 END,
                 payment_date, payment_records.id
        """,
        (request_id,),
    ).fetchall()
    return [payment_record_public(conn, row) for row in rows]


def current_user(request: Request) -> Dict[str, Any]:
    token = request.cookies.get("session")
    if not token:
        raise HTTPException(status_code=401, detail="未登录")
    with connect() as conn:
        row = conn.execute(
            """
            SELECT users.* FROM sessions
            JOIN users ON users.id = sessions.user_id
            WHERE sessions.token = ? AND users.active = 1 AND users.deleted_at IS NULL
            """,
            (token,),
        ).fetchone()
    if not row:
        raise HTTPException(status_code=401, detail="登录已失效")
    return row_to_dict(row)


def require_roles(*roles: str):
    def dependency(user: Dict[str, Any] = Depends(current_user)) -> Dict[str, Any]:
        if user["role"] not in roles:
            raise HTTPException(status_code=403, detail="权限不足")
        return user

    return dependency


def restricted_request_fields(role: str) -> set[str]:
    if role in PRIVILEGED_ROLES:
        return set()
    if role == ROLE_FINANCE:
        return set(GENERAL_MANAGER_CONTROLLED_FIELDS)
    return set(FINANCE_CONTROLLED_FIELDS | GENERAL_MANAGER_CONTROLLED_FIELDS)


def comparable_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "1" if value else "0"
    return str(value).strip()


def request_values_equal(left: Any, right: Any) -> bool:
    if isinstance(left, (int, float)) and not isinstance(left, bool) and isinstance(right, (int, float)) and not isinstance(right, bool):
        return abs(float(left) - float(right)) < 0.000001
    return comparable_value(left) == comparable_value(right)


def reject_direct_payment_summary_changes(
    data: Dict[str, Any],
    existing: Optional[Dict[str, Any]] = None,
    *,
    creating: bool = False,
) -> None:
    changed = []
    for field in DERIVED_PAYMENT_FIELDS:
        if field not in data:
            continue
        if creating or existing is None or not request_values_equal(data.get(field), existing.get(field)):
            changed.append(REQUEST_FIELD_LABELS.get(field, field))
    if changed:
        raise HTTPException(status_code=400, detail=f"{'、'.join(sorted(changed))}由付款明细自动生成，不能直接修改")


def enforce_request_field_permissions(
    data: Dict[str, Any],
    role: str,
    existing: Optional[Dict[str, Any]] = None,
    *,
    creating: bool = False,
) -> Dict[str, Any]:
    restricted = restricted_request_fields(role)
    if not restricted:
        return dict(data)
    payload = dict(data)
    if creating:
        for field in restricted:
            payload.pop(field, None)
        return payload
    if existing is None:
        raise HTTPException(status_code=404, detail="请款记录不存在")
    changed = [
        REQUEST_FIELD_LABELS.get(field, field)
        for field in sorted(restricted)
        if field in payload and not request_values_equal(payload.get(field), existing.get(field))
    ]
    if changed:
        raise HTTPException(status_code=403, detail=f"当前角色不能修改：{'、'.join(changed)}")
    for field in restricted:
        payload.pop(field, None)
    return payload


def validate_user_role(role: str) -> None:
    if role not in ALL_ROLES:
        raise HTTPException(status_code=400, detail="角色无效")


def active_privileged_user_count(conn) -> int:
    row = conn.execute(
        f"""
        SELECT COUNT(*) AS count FROM users
        WHERE role IN ({', '.join('?' for _ in PRIVILEGED_ROLES)})
          AND active = 1
          AND deleted_at IS NULL
        """,
        PRIVILEGED_ROLES,
    ).fetchone()
    return int(row["count"])


def ensure_can_change_user(conn, target, actor: Dict[str, Any], updates: Dict[str, Any]) -> None:
    if "role" in updates:
        validate_user_role(updates["role"])
    deleting_user = bool(updates.get("deleted_at"))
    if target["id"] == actor["id"] and (updates.get("active") == 0 or deleting_user):
        raise HTTPException(status_code=400, detail="不能停用或删除当前登录账号")
    removing_privileged_user = (
        target["role"] in PRIVILEGED_ROLES
        and target["active"]
        and not target["deleted_at"]
        and (
            updates.get("active") == 0
            or deleting_user
            or ("role" in updates and updates["role"] not in PRIVILEGED_ROLES)
        )
    )
    if removing_privileged_user and active_privileged_user_count(conn) <= 1:
        raise HTTPException(status_code=400, detail="至少保留一个启用的管理员或总经理账号")


@app.on_event("startup")
def startup() -> None:
    init_db()
    (DATA_DIR / "uploads").mkdir(parents=True, exist_ok=True)
    (DATA_DIR / "snapshots").mkdir(parents=True, exist_ok=True)
    with connect() as conn:
        ensure_draft_baselines(conn)


@app.post("/api/auth/login")
def login(payload: LoginIn, response: Response) -> Dict[str, Any]:
    with connect() as conn:
        row = conn.execute("SELECT * FROM users WHERE username = ? AND active = 1 AND deleted_at IS NULL", (payload.username,)).fetchone()
        if not row or not verify_password(payload.password, row["password_hash"]):
            raise HTTPException(status_code=401, detail="用户名或密码错误")
        token = new_session_token()
        conn.execute(
            "INSERT INTO sessions (token, user_id, created_at) VALUES (?, ?, ?)",
            (token, row["id"], now_iso()),
        )
        write_audit(conn, row["id"], "auth.login", "user", row["id"], new_value={"username": row["username"]})
    response.set_cookie("session", token, httponly=True, samesite="lax", max_age=60 * 60 * 12)
    return {"user": user_public(row_to_dict(row))}


@app.post("/api/auth/logout")
def logout(request: Request, response: Response) -> Dict[str, str]:
    token = request.cookies.get("session")
    if token:
        with connect() as conn:
            conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
    response.delete_cookie("session")
    return {"status": "ok"}


@app.get("/api/me")
def me(user: Dict[str, Any] = Depends(current_user)) -> Dict[str, Any]:
    return {"user": user_public(user)}


@app.post("/api/auth/change-password")
def change_password(
    payload: ChangePasswordIn,
    request: Request,
    user: Dict[str, Any] = Depends(current_user),
) -> Dict[str, Any]:
    if not payload.current_password or not payload.new_password or not payload.confirm_password:
        raise HTTPException(status_code=400, detail="当前密码、新密码和确认密码不能为空")
    if len(payload.new_password) < 6:
        raise HTTPException(status_code=400, detail="新密码至少需要 6 位")
    if payload.new_password != payload.confirm_password:
        raise HTTPException(status_code=400, detail="两次输入的新密码不一致")

    current_token = request.cookies.get("session")
    if not current_token:
        raise HTTPException(status_code=401, detail="登录已失效")

    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE id = ? AND active = 1 AND deleted_at IS NULL",
            (user["id"],),
        ).fetchone()
        if not row or not verify_password(payload.current_password, row["password_hash"]):
            raise HTTPException(status_code=400, detail="当前密码错误")
        if verify_password(payload.new_password, row["password_hash"]):
            raise HTTPException(status_code=400, detail="新密码不能与当前密码相同")

        signed_out_sessions = int(
            conn.execute(
                "SELECT COUNT(*) AS count FROM sessions WHERE user_id = ? AND token <> ?",
                (user["id"], current_token),
            ).fetchone()["count"]
        )
        conn.execute(
            "UPDATE users SET password_hash = ? WHERE id = ?",
            (hash_password(payload.new_password), user["id"]),
        )
        conn.execute(
            "DELETE FROM sessions WHERE user_id = ? AND token <> ?",
            (user["id"], current_token),
        )
        write_audit(
            conn,
            user["id"],
            "user.change_password",
            "user",
            user["id"],
            new_value={"signed_out_sessions": signed_out_sessions},
        )
    return {"status": "ok", "signed_out_sessions": signed_out_sessions}


@app.get("/api/batches")
def list_batches(user: Dict[str, Any] = Depends(current_user)) -> Dict[str, Any]:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT b.*, COUNT(p.id) AS request_count,
                   COALESCE(SUM(p.amount), 0) AS total_amount,
                   COALESCE(SUM(p.paid_amount), 0) AS total_paid_amount,
                   COALESCE(SUM(p.pending_amount), 0) AS total_pending_amount
            FROM request_batches b
            LEFT JOIN payment_requests p ON p.batch_id = b.id
            GROUP BY b.id
            ORDER BY COALESCE(b.end_date, b.created_at) DESC, b.id DESC
            """
        ).fetchall()
    return {"batches": rows_to_dicts(rows)}


@app.post("/api/batches")
def create_batch(payload: BatchIn, user: Dict[str, Any] = Depends(require_roles(*ALL_ROLES))) -> Dict[str, Any]:
    timestamp = now_iso()
    with connect() as conn:
        cursor = conn.execute(
            """
            INSERT INTO request_batches (name, start_date, end_date, status, created_by, created_at, updated_at)
            VALUES (?, ?, ?, 'draft', ?, ?, ?)
            """,
            (payload.name, payload.start_date, payload.end_date, user["id"], timestamp, timestamp),
        )
        batch_id = cursor.lastrowid
        row = conn.execute("SELECT * FROM request_batches WHERE id = ?", (batch_id,)).fetchone()
        create_batch_snapshot(conn, int(batch_id), "baseline", user["id"], replace_existing=True)
        write_audit(conn, user["id"], "batch.create", "batch", batch_id, batch_id=batch_id, new_value=row_to_dict(row))
    return {"batch": row_to_dict(row)}


@app.post("/api/batches/{source_batch_id}/rollover")
def rollover_batch(
    source_batch_id: int,
    payload: RolloverIn,
    user: Dict[str, Any] = Depends(require_roles(*ALL_ROLES)),
) -> Dict[str, Any]:
    if not payload.name.strip():
        raise HTTPException(status_code=400, detail="新批次名称不能为空")
    if payload.copy_mode not in {"unfinished", "all"}:
        raise HTTPException(status_code=400, detail="复制模式无效")
    operation_id = uuid.uuid4().hex
    timestamp = now_iso()
    with connect() as conn:
        source = require_batch(conn, source_batch_id)
        cursor = conn.execute(
            """
            INSERT INTO request_batches (
                parent_batch_id, name, start_date, end_date, status,
                source_file, sheet_order_json, created_by, created_at, updated_at
            ) VALUES (?, ?, ?, ?, 'draft', ?, ?, ?, ?, ?)
            """,
            (
                source_batch_id,
                payload.name,
                payload.start_date,
                payload.end_date,
                f"rollover:{source['name']}",
                source["sheet_order_json"],
                user["id"],
                timestamp,
                timestamp,
            ),
        )
        target_batch_id = cursor.lastrowid
        if payload.copy_mode == "all":
            source_rows = conn.execute(
                """
                SELECT * FROM payment_requests
                WHERE batch_id = ?
                ORDER BY source_sheet, source_row, id
                """,
                (source_batch_id,),
            ).fetchall()
        else:
            source_rows = conn.execute(
                """
                SELECT * FROM payment_requests
                WHERE batch_id = ?
                  AND COALESCE(NULLIF(TRIM(finance_review), ''), '未付款') != '已付款'
                ORDER BY source_sheet, source_row, id
                """,
                (source_batch_id,),
            ).fetchall()
        copied_count = 0
        for source_row in source_rows:
            source_data = row_to_dict(source_row)
            copied_from_request_id = source_data["id"]
            for key in [
                "id",
                "batch_id",
                "created_by",
                "updated_by",
                "created_at",
                "updated_at",
                "content_hash",
            ]:
                source_data.pop(key, None)
            source_data["copied_from_request_id"] = copied_from_request_id
            new_request_id = insert_request(
                conn,
                target_batch_id,
                source_data,
                user["id"],
                ROLE_GENERAL_MANAGER,
                create_summary_payment=False,
            )
            copy_attachment_links(conn, copied_from_request_id, new_request_id, user["id"])
            copy_payment_records(conn, copied_from_request_id, new_request_id, user["id"])
            copied_count += 1
        target = conn.execute("SELECT * FROM request_batches WHERE id = ?", (target_batch_id,)).fetchone()
        write_audit(
            conn,
            user["id"],
            "batch.rollover",
            "batch",
            target_batch_id,
            target_batch_id,
            old_value={"source_batch_id": source_batch_id, "source_name": source["name"]},
            new_value={"target_batch_id": target_batch_id, "copied_count": copied_count, "copy_mode": payload.copy_mode},
            operation_id=operation_id,
        )
        create_batch_snapshot(conn, int(target_batch_id), "baseline", user["id"], replace_existing=True)
    return {"batch": row_to_dict(target), "copied_count": copied_count, "copy_mode": payload.copy_mode, "operation_id": operation_id}


@app.get("/api/batches/{batch_id}")
def get_batch(batch_id: int, user: Dict[str, Any] = Depends(current_user)) -> Dict[str, Any]:
    with connect() as conn:
        row = conn.execute(
            """
            SELECT b.*, COUNT(p.id) AS request_count,
                   COALESCE(SUM(p.amount), 0) AS total_amount,
                   COALESCE(SUM(p.paid_amount), 0) AS total_paid_amount,
                   COALESCE(SUM(p.pending_amount), 0) AS total_pending_amount
            FROM request_batches b
            LEFT JOIN payment_requests p ON p.batch_id = b.id
            WHERE b.id = ?
            GROUP BY b.id
            """,
            (batch_id,),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="批次不存在")
        stats = conn.execute(
            """
            SELECT payment_account, invoice_status, project, COUNT(*) AS count,
                   COALESCE(SUM(amount), 0) AS amount,
                   COALESCE(SUM(paid_amount), 0) AS paid_amount,
                   COALESCE(SUM(pending_amount), 0) AS pending_amount
            FROM payment_requests
            WHERE batch_id = ?
            GROUP BY payment_account, invoice_status, project
            """,
            (batch_id,),
        ).fetchall()
    return {"batch": row_to_dict(row), "stats": rows_to_dicts(stats)}


@app.put("/api/batches/{batch_id}/sheet-order")
def update_batch_sheet_order(
    batch_id: int,
    payload: SheetOrderIn,
    user: Dict[str, Any] = Depends(require_roles(*ALL_ROLES)),
) -> Dict[str, Any]:
    requested_order: list[str] = []
    seen: set[str] = set()
    for value in payload.sheet_order:
        name = str(value or "").strip()
        if not name or name == "全部" or len(name) > 200 or name in seen:
            continue
        requested_order.append(name)
        seen.add(name)
    if len(requested_order) > 200:
        raise HTTPException(status_code=400, detail="Sheet 数量不能超过 200 个")

    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        batch = require_batch(conn, batch_id)
        if batch["status"] == "archived":
            raise HTTPException(status_code=400, detail="归档批次不能调整 Sheet 顺序")
        existing_names = {
            str(row["sheet_name"] or "").strip()
            for row in conn.execute(
                """
                SELECT DISTINCT COALESCE(NULLIF(TRIM(source_sheet), ''), '未分 Sheet') AS sheet_name
                FROM payment_requests
                WHERE batch_id = ?
                """,
                (batch_id,),
            ).fetchall()
        }
        final_order = [name for name in requested_order if name in existing_names]
        final_order.extend(sorted(existing_names - set(final_order)))
        old_order = batch_sheet_order(row_to_dict(batch))
        timestamp = now_iso()
        conn.execute(
            "UPDATE request_batches SET sheet_order_json = ?, updated_at = ? WHERE id = ?",
            (json.dumps(final_order, ensure_ascii=False), timestamp, batch_id),
        )
        updated = conn.execute("SELECT * FROM request_batches WHERE id = ?", (batch_id,)).fetchone()
        write_audit(
            conn,
            user["id"],
            "batch.sheet_order.update",
            "batch",
            batch_id,
            batch_id=batch_id,
            old_value={"sheet_order": old_order},
            new_value={"sheet_order": final_order},
        )
    return {"batch": row_to_dict(updated)}


@app.post("/api/batches/{batch_id}/archive")
def archive_batch(batch_id: int, user: Dict[str, Any] = Depends(require_roles(*FINANCE_FIELD_ROLES))) -> Dict[str, Any]:
    with connect() as conn:
        row = conn.execute("SELECT * FROM request_batches WHERE id = ?", (batch_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="批次不存在")
        if row["status"] == "archived":
            return {"batch": row_to_dict(row)}
        old_value = row_to_dict(row)
        conn.execute(
            "UPDATE request_batches SET status = 'archived', archived_by = ?, archived_at = ?, updated_at = ? WHERE id = ?",
            (user["id"], now_iso(), now_iso(), batch_id),
        )
        new_row = conn.execute("SELECT * FROM request_batches WHERE id = ?", (batch_id,)).fetchone()
        write_audit(conn, user["id"], "batch.archive", "batch", batch_id, batch_id, old_value, row_to_dict(new_row))
    return {"batch": row_to_dict(new_row)}


@app.post("/api/batches/{batch_id}/unarchive")
def unarchive_batch(batch_id: int, user: Dict[str, Any] = Depends(require_roles(*GENERAL_MANAGER_ROLES))) -> Dict[str, Any]:
    with connect() as conn:
        row = conn.execute("SELECT * FROM request_batches WHERE id = ?", (batch_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="批次不存在")
        if row["status"] == "draft":
            return {"batch": row_to_dict(row)}
        old_value = row_to_dict(row)
        conn.execute(
            "UPDATE request_batches SET status = 'draft', archived_by = NULL, archived_at = NULL, updated_at = ? WHERE id = ?",
            (now_iso(), batch_id),
        )
        new_row = conn.execute("SELECT * FROM request_batches WHERE id = ?", (batch_id,)).fetchone()
        write_audit(conn, user["id"], "batch.unarchive", "batch", batch_id, batch_id, old_value, row_to_dict(new_row))
    return {"batch": row_to_dict(new_row)}


@app.post("/api/batches/{batch_id}/snapshots/baseline")
def set_batch_baseline(batch_id: int, user: Dict[str, Any] = Depends(require_roles(*GENERAL_MANAGER_ROLES))) -> Dict[str, Any]:
    with connect() as conn:
        batch = require_batch(conn, batch_id)
        if batch["status"] != "draft":
            raise HTTPException(status_code=400, detail="只有草稿批次可以设置还原点")
        snapshot = create_batch_snapshot(conn, batch_id, "baseline", user["id"], replace_existing=True)
        write_audit(
            conn,
            user["id"],
            "batch.snapshot.baseline",
            "batch_snapshot",
            snapshot["id"],
            batch_id,
            new_value=snapshot,
            reason="设置草稿还原点",
        )
    return {"snapshot": snapshot}


@app.post("/api/batches/{batch_id}/restore-baseline")
def restore_batch_baseline(batch_id: int, user: Dict[str, Any] = Depends(require_roles(*GENERAL_MANAGER_ROLES))) -> Dict[str, Any]:
    with connect() as conn:
        try:
            result = restore_batch_from_baseline(conn, batch_id, user["id"])
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except FileNotFoundError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        write_audit(
            conn,
            user["id"],
            "batch.restore_baseline",
            "batch",
            batch_id,
            batch_id,
            old_value={"counts": result["before"], "pre_restore_snapshot": result["pre_restore_snapshot"]},
            new_value={"counts": result["after"], "baseline_snapshot": result["snapshot"]},
            reason="还原到初始状态",
        )
    return {
        "status": "restored",
        "snapshot_id": result["snapshot"]["id"],
        "pre_restore_snapshot_id": result["pre_restore_snapshot"]["id"],
        "before": result["before"],
        "after": result["after"],
    }


@app.delete("/api/batches/{batch_id}")
def delete_draft_batch(batch_id: int, user: Dict[str, Any] = Depends(require_roles(*GENERAL_MANAGER_ROLES))) -> Dict[str, str]:
    with connect() as conn:
        row = conn.execute("SELECT * FROM request_batches WHERE id = ?", (batch_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="批次不存在")
        if row["status"] != "draft":
            raise HTTPException(status_code=400, detail="已归档批次不能删除，请先恢复草稿后再删除")
        old_value = row_to_dict(row)
        request_ids = [
            int(item["id"])
            for item in conn.execute("SELECT id FROM payment_requests WHERE batch_id = ?", (batch_id,)).fetchall()
        ]
        file_paths = request_owned_file_paths(conn, request_ids)
        write_audit(conn, user["id"], "batch.delete_draft", "batch", batch_id, batch_id=batch_id, old_value=old_value)
        conn.execute("DELETE FROM request_batches WHERE id = ?", (batch_id,))
        for file_path in set(file_paths):
            delete_file_if_unreferenced(conn, file_path)
        cleanup_batch_snapshot_files(batch_id)
    return {"status": "ok"}


@app.post("/api/batches/{batch_id}/corrections")
def correct_archived_request(
    batch_id: int,
    payload: CorrectionIn,
    user: Dict[str, Any] = Depends(require_roles(*GENERAL_MANAGER_ROLES)),
) -> Dict[str, Any]:
    if not payload.reason.strip():
        raise HTTPException(status_code=400, detail="归档后更正必须填写原因")
    with connect() as conn:
        batch = require_batch(conn, batch_id)
        if batch["status"] != "archived":
            raise HTTPException(status_code=400, detail="只有归档批次需要走更正入口")
        old_row = conn.execute(
            "SELECT * FROM payment_requests WHERE id = ? AND batch_id = ?",
            (payload.request_id, batch_id),
        ).fetchone()
        if not old_row:
            raise HTTPException(status_code=404, detail="请款记录不存在")
        changed = update_request_row(conn, payload.request_id, payload.changes, user["id"], user["role"])
        new_row = conn.execute("SELECT * FROM payment_requests WHERE id = ?", (payload.request_id,)).fetchone()
        if changed:
            write_audit(
                conn,
                user["id"],
                "request.correction",
                "payment_request",
                payload.request_id,
                batch_id,
                row_to_dict(old_row),
                row_to_dict(new_row),
                payload.reason,
            )
    return {"request": row_to_dict(new_row)}


@app.get("/api/batches/{batch_id}/requests")
def list_requests(
    batch_id: int,
    q: str = "",
    payment_account: str = "",
    invoice_status: str = "",
    finance_review: str = "",
    general_manager_approval: str = "",
    payment_status: str = "",
    source_sheet: str = "",
    user: Dict[str, Any] = Depends(current_user),
) -> Dict[str, Any]:
    conditions = ["batch_id = ?"]
    params: list[Any] = [batch_id]
    if q:
        conditions.append(
            """
            (
                dingding_id LIKE ? OR summary LIKE ? OR payee_name LIKE ? OR payee_account LIKE ?
                OR project LIKE ? OR expense_type LIKE ? OR general_manager_opinion LIKE ? OR remark LIKE ?
            )
            """
        )
        needle = f"%{q}%"
        params.extend([needle] * 8)
    for column, value in [
        ("payment_account", payment_account),
        ("invoice_status", invoice_status),
        ("finance_review", finance_review or {"未支付": "未付款", "已支付": "已付款"}.get(payment_status, payment_status)),
    ]:
        if value:
            conditions.append(f"{column} = ?")
            params.append(value)
    if general_manager_approval == "__empty_general_manager_approval__":
        conditions.append("(general_manager_approval IS NULL OR TRIM(general_manager_approval) = '')")
    elif general_manager_approval:
        conditions.append("general_manager_approval = ?")
        params.append(general_manager_approval)
    if source_sheet:
        conditions.append("source_sheet = ?")
        params.append(source_sheet)
    with connect() as conn:
        require_batch(conn, batch_id)
        rows = conn.execute(
            f"""
            SELECT payment_requests.*,
                   (SELECT COUNT(*) FROM payment_records WHERE payment_records.request_id = payment_requests.id) AS payment_count
            FROM payment_requests
            WHERE {' AND '.join(conditions)}
            ORDER BY payment_requests.id DESC
            """,
            params,
        ).fetchall()
        totals = conn.execute(
            f"""
            SELECT COUNT(*) AS count,
                   COALESCE(SUM(amount), 0) AS amount,
                   COALESCE(SUM(paid_amount), 0) AS paid_amount,
                   COALESCE(SUM(pending_amount), 0) AS pending_amount
            FROM payment_requests WHERE {' AND '.join(conditions)}
            """,
            params,
        ).fetchone()
    return {"requests": rows_to_dicts(rows), "totals": row_to_dict(totals)}


@app.post("/api/batches/{batch_id}/requests")
def create_request(
    batch_id: int,
    payload: RequestIn,
    user: Dict[str, Any] = Depends(require_roles(*ALL_ROLES)),
) -> Dict[str, Any]:
    with connect() as conn:
        batch = require_batch(conn, batch_id)
        ensure_editable(batch, user)
        data = payload.dict(exclude_unset=True)
        reject_direct_payment_summary_changes(data, creating=True)
        request_id = insert_request(conn, batch_id, data, user["id"], user["role"], create_summary_payment=False)
        row = conn.execute("SELECT * FROM payment_requests WHERE id = ?", (request_id,)).fetchone()
        write_audit(conn, user["id"], "request.create", "payment_request", request_id, batch_id, new_value=row_to_dict(row))
    return {"request": row_to_dict(row)}


@app.patch("/api/batches/{batch_id}/requests/bulk")
def bulk_save_requests(
    batch_id: int,
    payload: BulkRequestsIn,
    user: Dict[str, Any] = Depends(require_roles(*ALL_ROLES)),
) -> Dict[str, Any]:
    operation_id = uuid.uuid4().hex
    with connect() as conn:
        batch = require_batch(conn, batch_id)
        ensure_bulk_editable(batch, user, payload.reason)
        created: list[int] = []
        updated: list[int] = []
        deleted: list[int] = []
        for item in payload.creates:
            reject_direct_payment_summary_changes(item, creating=True)
            request_id = insert_request(conn, batch_id, item, user["id"], user["role"], create_summary_payment=False)
            row = conn.execute("SELECT * FROM payment_requests WHERE id = ?", (request_id,)).fetchone()
            write_audit(
                conn,
                user["id"],
                "request.correction.create" if batch["status"] == "archived" else "request.bulk_create",
                "payment_request",
                request_id,
                batch_id,
                new_value=row_to_dict(row),
                reason=payload.reason,
                operation_id=operation_id,
            )
            created.append(request_id)
        for item in payload.updates:
            request_id = item.get("id")
            if not request_id:
                raise HTTPException(status_code=400, detail="批量更新缺少记录 id")
            old_row = require_request(conn, batch_id, int(request_id))
            changes = {key: value for key, value in item.items() if key != "id"}
            if not changes:
                continue
            if not update_request_row(conn, int(request_id), changes, user["id"], user["role"]):
                continue
            new_row = conn.execute("SELECT * FROM payment_requests WHERE id = ?", (request_id,)).fetchone()
            write_audit(
                conn,
                user["id"],
                "request.correction" if batch["status"] == "archived" else "request.bulk_update",
                "payment_request",
                int(request_id),
                batch_id,
                row_to_dict(old_row),
                row_to_dict(new_row),
                payload.reason,
                operation_id,
            )
            updated.append(int(request_id))
        for request_id in payload.deletes:
            old_row = require_request(conn, batch_id, int(request_id))
            ensure_can_delete_request_with_payments(conn, int(request_id), user)
            file_paths = request_owned_file_paths(conn, [int(request_id)])
            conn.execute("DELETE FROM payment_requests WHERE id = ?", (request_id,))
            for file_path in file_paths:
                delete_file_if_unreferenced(conn, file_path)
            write_audit(
                conn,
                user["id"],
                "request.correction.delete" if batch["status"] == "archived" else "request.bulk_delete",
                "payment_request",
                int(request_id),
                batch_id,
                row_to_dict(old_row),
                None,
                payload.reason,
                operation_id,
            )
            deleted.append(int(request_id))
        conn.execute("UPDATE request_batches SET updated_at = ? WHERE id = ?", (now_iso(), batch_id))
    return {
        "operation_id": operation_id,
        "created": created,
        "updated": updated,
        "deleted": deleted,
        "counts": {"created": len(created), "updated": len(updated), "deleted": len(deleted)},
    }


@app.patch("/api/batches/{batch_id}/requests/{request_id}")
def update_request(
    batch_id: int,
    request_id: int,
    payload: RequestPatch,
    user: Dict[str, Any] = Depends(require_roles(*ALL_ROLES)),
) -> Dict[str, Any]:
    with connect() as conn:
        batch = require_batch(conn, batch_id)
        old_row = conn.execute("SELECT * FROM payment_requests WHERE id = ? AND batch_id = ?", (request_id, batch_id)).fetchone()
        if not old_row:
            raise HTTPException(status_code=404, detail="请款记录不存在")
        if batch["status"] == "archived" and user["role"] not in PRIVILEGED_ROLES:
            raise HTTPException(status_code=403, detail="归档批次只能由管理员或总经理更正")
        if batch["status"] == "archived" and not payload.reason:
            raise HTTPException(status_code=400, detail="归档后更正必须填写原因")
        data = payload.dict(exclude_unset=True)
        reason = data.pop("reason", None)
        changed = update_request_row(conn, request_id, data, user["id"], user["role"])
        new_row = conn.execute("SELECT * FROM payment_requests WHERE id = ?", (request_id,)).fetchone()
        if changed:
            write_audit(
                conn,
                user["id"],
                "request.correction" if batch["status"] == "archived" else "request.update",
                "payment_request",
                request_id,
                batch_id,
                row_to_dict(old_row),
                row_to_dict(new_row),
                reason,
            )
    return {"request": row_to_dict(new_row)}


@app.delete("/api/batches/{batch_id}/requests/{request_id}")
def delete_request(
    batch_id: int,
    request_id: int,
    reason: str = Query(""),
    user: Dict[str, Any] = Depends(require_roles(*ALL_ROLES)),
) -> Dict[str, str]:
    with connect() as conn:
        batch = require_batch(conn, batch_id)
        old_row = conn.execute("SELECT * FROM payment_requests WHERE id = ? AND batch_id = ?", (request_id, batch_id)).fetchone()
        if not old_row:
            raise HTTPException(status_code=404, detail="请款记录不存在")
        if batch["status"] == "archived" and user["role"] not in PRIVILEGED_ROLES:
            raise HTTPException(status_code=403, detail="归档批次只能由管理员或总经理更正")
        if batch["status"] == "archived" and not reason.strip():
            raise HTTPException(status_code=400, detail="归档后删除必须填写原因")
        ensure_can_delete_request_with_payments(conn, request_id, user)
        file_paths = request_owned_file_paths(conn, [request_id])
        conn.execute("DELETE FROM payment_requests WHERE id = ?", (request_id,))
        for file_path in file_paths:
            delete_file_if_unreferenced(conn, file_path)
        write_audit(
            conn,
            user["id"],
            "request.delete" if batch["status"] != "archived" else "request.correction.delete",
            "payment_request",
            request_id,
            batch_id,
            row_to_dict(old_row),
            None,
            reason or None,
        )
    return {"status": "ok"}


@app.get("/api/batches/{batch_id}/requests/{request_id}/payments")
def list_request_payments(
    batch_id: int,
    request_id: int,
    user: Dict[str, Any] = Depends(current_user),
) -> Dict[str, Any]:
    with connect() as conn:
        require_batch(conn, batch_id)
        require_request(conn, batch_id, request_id)
        refresh_payment_summaries(conn, request_id)
        request_row = conn.execute("SELECT * FROM payment_requests WHERE id = ?", (request_id,)).fetchone()
        payments = payment_records_public(conn, request_id)
    return {
        "payments": payments,
        "summary": {
            "amount": request_row["amount"],
            "paid_amount": request_row["paid_amount"],
            "pending_amount": request_row["pending_amount"],
            "finance_review": request_row["finance_review"],
            "payment_count": len(payments),
            "actual_payment_date": request_row["actual_payment_date"],
            "payer": request_row["payer"],
        },
    }


@app.post("/api/batches/{batch_id}/requests/{request_id}/payments")
def create_request_payment(
    batch_id: int,
    request_id: int,
    payload: PaymentRecordIn,
    user: Dict[str, Any] = Depends(require_roles(*FINANCE_FIELD_ROLES)),
) -> Dict[str, Any]:
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        batch = require_batch(conn, batch_id)
        ensure_can_manage_payments(batch, user, payload.reason)
        request_row = require_request(conn, batch_id, request_id)
        payment_id = insert_payment_record_internal(
            conn,
            request_id,
            amount=payload.amount,
            payment_date=payload.payment_date,
            payer=payload.payer,
            payment_account=payload.payment_account or request_row["payment_account"],
            bank_reference=payload.bank_reference,
            remark=payload.remark,
            source_type="manual",
            user_id=user["id"],
        )
        row = conn.execute(
            """
            SELECT payment_records.*, users.display_name AS creator_name
            FROM payment_records LEFT JOIN users ON users.id = payment_records.created_by
            WHERE payment_records.id = ?
            """,
            (payment_id,),
        ).fetchone()
        write_audit(
            conn,
            user["id"],
            "payment.create" if batch["status"] != "archived" else "payment.correction.create",
            "payment_record",
            payment_id,
            batch_id,
            new_value=row_to_dict(row),
            reason=payload.reason,
        )
        request_after = conn.execute("SELECT * FROM payment_requests WHERE id = ?", (request_id,)).fetchone()
        payment = payment_record_public(conn, row)
    return {"payment": payment, "request": row_to_dict(request_after)}


@app.patch("/api/batches/{batch_id}/requests/{request_id}/payments/{payment_id}")
def update_request_payment(
    batch_id: int,
    request_id: int,
    payment_id: int,
    payload: PaymentRecordPatch,
    user: Dict[str, Any] = Depends(require_roles(*FINANCE_FIELD_ROLES)),
) -> Dict[str, Any]:
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        batch = require_batch(conn, batch_id)
        data = payload.dict(exclude_unset=True)
        reason = data.pop("reason", None)
        ensure_can_manage_payments(batch, user, reason)
        require_request(conn, batch_id, request_id)
        old = require_payment_record(conn, request_id, payment_id)
        if payment_record_is_inherited(row_to_dict(old)):
            raise HTTPException(status_code=400, detail="结转继承的付款明细只读")
        amount = payment_record_amount(data.get("amount", old["amount"]))
        payment_date = normalize_payment_date(data.get("payment_date", old["payment_date"]))
        validate_payment_total(conn, request_id, amount, payment_id)
        merged = {
            "amount": amount,
            "payment_date": payment_date,
            "payer": data.get("payer", old["payer"]),
            "payment_account": data.get("payment_account", old["payment_account"]),
            "bank_reference": data.get("bank_reference", old["bank_reference"]),
            "remark": data.get("remark", old["remark"]),
        }
        merged["content_hash"] = payment_record_hash(
            request_id,
            merged["amount"],
            merged["payment_date"],
            merged["payer"],
            merged["bank_reference"],
        )
        conn.execute(
            """
            UPDATE payment_records
            SET amount = ?, payment_date = ?, payer = ?, payment_account = ?,
                bank_reference = ?, remark = ?, content_hash = ?, updated_by = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                merged["amount"],
                merged["payment_date"],
                str(merged["payer"] or "").strip() or None,
                str(merged["payment_account"] or "").strip() or None,
                str(merged["bank_reference"] or "").strip() or None,
                str(merged["remark"] or "").strip() or None,
                merged["content_hash"],
                user["id"],
                now_iso(),
                payment_id,
            ),
        )
        refresh_payment_summaries(conn, request_id)
        row = conn.execute(
            """
            SELECT payment_records.*, users.display_name AS creator_name
            FROM payment_records LEFT JOIN users ON users.id = payment_records.created_by
            WHERE payment_records.id = ?
            """,
            (payment_id,),
        ).fetchone()
        write_audit(
            conn,
            user["id"],
            "payment.update" if batch["status"] != "archived" else "payment.correction",
            "payment_record",
            payment_id,
            batch_id,
            old_value=row_to_dict(old),
            new_value=row_to_dict(row),
            reason=reason,
        )
        request_after = conn.execute("SELECT * FROM payment_requests WHERE id = ?", (request_id,)).fetchone()
        payment = payment_record_public(conn, row)
    return {"payment": payment, "request": row_to_dict(request_after)}


@app.delete("/api/batches/{batch_id}/requests/{request_id}/payments/{payment_id}")
def delete_request_payment(
    batch_id: int,
    request_id: int,
    payment_id: int,
    reason: str = Query(""),
    user: Dict[str, Any] = Depends(require_roles(*FINANCE_FIELD_ROLES)),
) -> Dict[str, Any]:
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        batch = require_batch(conn, batch_id)
        ensure_can_manage_payments(batch, user, reason)
        require_request(conn, batch_id, request_id)
        old = require_payment_record(conn, request_id, payment_id)
        if payment_record_is_inherited(row_to_dict(old)):
            raise HTTPException(status_code=400, detail="结转继承的付款明细只读")
        vouchers = rows_to_dicts(conn.execute("SELECT * FROM payment_vouchers WHERE payment_id = ?", (payment_id,)).fetchall())
        conn.execute("DELETE FROM payment_records WHERE id = ?", (payment_id,))
        refresh_payment_summaries(conn, request_id)
        write_audit(
            conn,
            user["id"],
            "payment.delete" if batch["status"] != "archived" else "payment.correction.delete",
            "payment_record",
            payment_id,
            batch_id,
            old_value=row_to_dict(old),
            reason=reason or None,
        )
        request_after = conn.execute("SELECT * FROM payment_requests WHERE id = ?", (request_id,)).fetchone()
        for voucher in vouchers:
            delete_payment_voucher_file_if_unused(conn, voucher)
    return {"status": "ok", "request": row_to_dict(request_after)}


@app.post("/api/batches/{batch_id}/requests/{request_id}/payments/{payment_id}/vouchers")
async def upload_payment_voucher(
    batch_id: int,
    request_id: int,
    payment_id: int,
    file: UploadFile = File(...),
    label: Optional[str] = Form(None),
    reason: Optional[str] = Form(None),
    user: Dict[str, Any] = Depends(require_roles(*FINANCE_FIELD_ROLES)),
) -> Dict[str, Any]:
    with connect() as conn:
        batch = require_batch(conn, batch_id)
        ensure_can_manage_payments(batch, user, reason)
        require_request(conn, batch_id, request_id)
        payment = require_payment_record(conn, request_id, payment_id)
        if payment_record_is_inherited(row_to_dict(payment)):
            raise HTTPException(status_code=400, detail="结转继承的付款凭证只读")
        _, relative_path, file_size, mime_type = await save_payment_voucher_upload(file, batch_id, payment_id)
        cursor = conn.execute(
            """
            INSERT INTO payment_vouchers (
                payment_id, label, file_path, original_filename, mime_type,
                file_size, created_by, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                payment_id,
                str(label or "").strip() or None,
                str(relative_path),
                file.filename,
                mime_type,
                file_size,
                user["id"],
                now_iso(),
            ),
        )
        row = conn.execute("SELECT * FROM payment_vouchers WHERE id = ?", (cursor.lastrowid,)).fetchone()
        write_audit(
            conn,
            user["id"],
            "payment.voucher_upload",
            "payment_voucher",
            cursor.lastrowid,
            batch_id,
            new_value=row_to_dict(row),
            reason=reason,
        )
    return {"voucher": payment_voucher_public(row)}


@app.get("/api/payment-vouchers/{voucher_id}/file")
def get_payment_voucher_file(voucher_id: int, user: Dict[str, Any] = Depends(current_user)) -> FileResponse:
    with connect() as conn:
        row = conn.execute("SELECT * FROM payment_vouchers WHERE id = ?", (voucher_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="付款凭证不存在")
    path = resolve_data_file(row["file_path"])
    if not path.exists():
        raise HTTPException(status_code=404, detail="付款凭证文件不存在")
    return FileResponse(
        path,
        media_type=row["mime_type"] or "application/octet-stream",
        filename=row["original_filename"] or path.name,
        content_disposition_type="inline",
    )


@app.delete("/api/batches/{batch_id}/requests/{request_id}/payments/{payment_id}/vouchers/{voucher_id}")
def delete_payment_voucher(
    batch_id: int,
    request_id: int,
    payment_id: int,
    voucher_id: int,
    reason: str = Query(""),
    user: Dict[str, Any] = Depends(require_roles(*FINANCE_FIELD_ROLES)),
) -> Dict[str, str]:
    with connect() as conn:
        batch = require_batch(conn, batch_id)
        ensure_can_manage_payments(batch, user, reason)
        require_request(conn, batch_id, request_id)
        payment = require_payment_record(conn, request_id, payment_id)
        if payment_record_is_inherited(row_to_dict(payment)):
            raise HTTPException(status_code=400, detail="结转继承的付款凭证只读")
        old = conn.execute("SELECT * FROM payment_vouchers WHERE id = ? AND payment_id = ?", (voucher_id, payment_id)).fetchone()
        if not old:
            raise HTTPException(status_code=404, detail="付款凭证不存在")
        conn.execute("DELETE FROM payment_vouchers WHERE id = ?", (voucher_id,))
        delete_payment_voucher_file_if_unused(conn, row_to_dict(old))
        write_audit(
            conn,
            user["id"],
            "payment.voucher_delete",
            "payment_voucher",
            voucher_id,
            batch_id,
            old_value=row_to_dict(old),
            reason=reason or None,
        )
    return {"status": "ok"}


@app.get("/api/batches/{batch_id}/attachments")
def list_batch_attachments(batch_id: int, user: Dict[str, Any] = Depends(current_user)) -> Dict[str, Any]:
    with connect() as conn:
        require_batch(conn, batch_id)
        rows = conn.execute(
            """
            SELECT attachment_links.* FROM attachment_links
            JOIN payment_requests ON payment_requests.id = attachment_links.request_id
            WHERE payment_requests.batch_id = ?
            ORDER BY payment_requests.id, attachment_links.id
            """,
            (batch_id,),
        ).fetchall()
    return {"attachments": attachments_public(rows)}


@app.get("/api/batches/{batch_id}/requests/{request_id}/attachments")
def list_attachments(
    batch_id: int,
    request_id: int,
    user: Dict[str, Any] = Depends(current_user),
) -> Dict[str, Any]:
    with connect() as conn:
        require_batch(conn, batch_id)
        require_request(conn, batch_id, request_id)
        rows = conn.execute(
            "SELECT * FROM attachment_links WHERE request_id = ? ORDER BY id",
            (request_id,),
        ).fetchall()
    return {"attachments": attachments_public(rows)}


@app.post("/api/batches/{batch_id}/requests/{request_id}/attachments")
def create_attachment(
    batch_id: int,
    request_id: int,
    payload: AttachmentIn,
    user: Dict[str, Any] = Depends(require_roles(*ALL_ROLES)),
) -> Dict[str, Any]:
    if not payload.url_path.strip():
        raise HTTPException(status_code=400, detail="附件链接不能为空")
    with connect() as conn:
        batch = require_batch(conn, batch_id)
        ensure_editable(batch, user)
        require_request(conn, batch_id, request_id)
        cursor = conn.execute(
            """
            INSERT INTO attachment_links (request_id, label, url_path, created_by, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (request_id, payload.label, payload.url_path, user["id"], now_iso()),
        )
        row = conn.execute("SELECT * FROM attachment_links WHERE id = ?", (cursor.lastrowid,)).fetchone()
        write_audit(conn, user["id"], "attachment.create", "attachment", cursor.lastrowid, batch_id, new_value=row_to_dict(row))
    return {"attachment": attachment_public(row)}


@app.post("/api/batches/{batch_id}/requests/{request_id}/attachments/image")
async def upload_image_attachment(
    batch_id: int,
    request_id: int,
    file: UploadFile = File(...),
    label: Optional[str] = Form(None),
    reason: Optional[str] = Form(None),
    user: Dict[str, Any] = Depends(require_roles(*ALL_ROLES)),
) -> Dict[str, Any]:
    with connect() as conn:
        batch = require_batch(conn, batch_id)
        ensure_bulk_editable(batch, user, reason)
        require_request(conn, batch_id, request_id)
        saved_path, relative_path, file_size = await save_image_upload(file, batch_id)
        cursor = conn.execute(
            """
            INSERT INTO attachment_links (
                request_id, label, url_path, attachment_type, file_path,
                original_filename, mime_type, file_size, created_by, created_at
            )
            VALUES (?, ?, ?, 'image', ?, ?, ?, ?, ?, ?)
            """,
            (
                request_id,
                label,
                str(relative_path),
                str(relative_path),
                file.filename,
                file.content_type,
                file_size,
                user["id"],
                now_iso(),
            ),
        )
        row = conn.execute("SELECT * FROM attachment_links WHERE id = ?", (cursor.lastrowid,)).fetchone()
        write_audit(
            conn,
            user["id"],
            "attachment.image_upload",
            "attachment",
            cursor.lastrowid,
            batch_id,
            new_value=row_to_dict(row),
            reason=reason or None,
        )
    return {"attachment": attachment_public(row)}


@app.delete("/api/batches/{batch_id}/requests/{request_id}/attachments/{attachment_id}")
def delete_attachment(
    batch_id: int,
    request_id: int,
    attachment_id: int,
    reason: str = Query(""),
    user: Dict[str, Any] = Depends(require_roles(*ALL_ROLES)),
) -> Dict[str, str]:
    with connect() as conn:
        batch = require_batch(conn, batch_id)
        if batch["status"] == "archived" and user["role"] not in PRIVILEGED_ROLES:
            raise HTTPException(status_code=403, detail="归档批次只能由管理员或总经理更正")
        if batch["status"] == "archived" and not reason.strip():
            raise HTTPException(status_code=400, detail="归档后删除附件必须填写原因")
        require_request(conn, batch_id, request_id)
        old = conn.execute(
            "SELECT * FROM attachment_links WHERE id = ? AND request_id = ?",
            (attachment_id, request_id),
        ).fetchone()
        if not old:
            raise HTTPException(status_code=404, detail="附件链接不存在")
        conn.execute("DELETE FROM attachment_links WHERE id = ?", (attachment_id,))
        delete_attachment_file_if_unused(conn, row_to_dict(old))
        write_audit(conn, user["id"], "attachment.delete", "attachment", attachment_id, batch_id, row_to_dict(old), None, reason or None)
    return {"status": "ok"}


@app.get("/api/attachments/{attachment_id}/file")
def get_attachment_file(attachment_id: int, user: Dict[str, Any] = Depends(current_user)) -> FileResponse:
    with connect() as conn:
        row = conn.execute("SELECT * FROM attachment_links WHERE id = ?", (attachment_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="附件不存在")
    attachment = row_to_dict(row)
    if attachment.get("attachment_type") != "image" or not attachment.get("file_path"):
        raise HTTPException(status_code=404, detail="附件文件不存在")
    path = resolve_data_file(attachment["file_path"])
    if not path.exists():
        raise HTTPException(status_code=404, detail="附件文件不存在")
    return FileResponse(
        path,
        media_type=attachment.get("mime_type") or "application/octet-stream",
        filename=attachment.get("original_filename") or path.name,
        content_disposition_type="inline",
    )


@app.post("/api/import/weekly-excel")
async def import_weekly_excel(
    file: UploadFile = File(...),
    batch_id: Optional[int] = Form(None),
    user: Dict[str, Any] = Depends(require_roles(*ALL_ROLES)),
) -> Dict[str, Any]:
    saved_path = await save_upload(file)
    rows, meta = parse_weekly_excel(saved_path)
    payment_details = meta.pop("payment_details", [])
    payment_detail_sheet_present = bool(meta.get("payment_detail_sheet_present"))
    if (payment_detail_sheet_present or any(float(row.get("paid_amount") or 0) > 0 for row in rows)) and user["role"] not in FINANCE_FIELD_ROLES:
        raise HTTPException(status_code=403, detail="包含付款数据的 Excel 只能由财务、总经理或管理员导入")
    meta["source_copy"] = str(saved_path.relative_to(DATA_DIR))
    start_date, end_date, default_name = parse_batch_dates(file.filename or saved_path.name)
    with connect() as conn:
        created_new_batch = batch_id is None
        existing_request_count = 0
        if batch_id is None:
            timestamp = now_iso()
            cursor = conn.execute(
                """
                INSERT INTO request_batches (name, start_date, end_date, status, source_file, created_by, created_at, updated_at)
                VALUES (?, ?, ?, 'draft', ?, ?, ?, ?)
                """,
                (default_name, start_date, end_date, file.filename, user["id"], timestamp, timestamp),
            )
            batch_id = cursor.lastrowid
        else:
            batch = require_batch(conn, batch_id)
            ensure_editable(batch, user)
            existing_request_count = conn.execute(
                "SELECT COUNT(*) AS count FROM payment_requests WHERE batch_id = ?",
                (batch_id,),
            ).fetchone()["count"]
        duplicates = duplicate_candidates(conn, batch_id, rows)
        imported = []
        imported_summaries: Dict[int, float] = {}
        saved_images = 0
        skipped_images = 0
        for row in rows:
            request_id = insert_request(
                conn,
                batch_id,
                row,
                user["id"],
                user["role"],
                create_summary_payment=not payment_detail_sheet_present,
            )
            imported.append(request_id)
            imported_summaries[request_id] = round(float(row.get("paid_amount") or 0), 2)
            row_saved_images, row_skipped_images = save_embedded_image_attachments(conn, batch_id, request_id, row, user["id"])
            saved_images += row_saved_images
            skipped_images += row_skipped_images
        meta.setdefault("images", {})["saved"] = saved_images
        meta["images"]["save_skipped"] = skipped_images
        if payment_detail_sheet_present:
            meta["payment_details"] = import_excel_payment_details(
                conn,
                int(batch_id),
                payment_details,
                user["id"],
                imported_summaries,
            )
        meta["request_ids"] = imported
        job_id = write_import_job(conn, "weekly-excel", file.filename or saved_path.name, "imported", batch_id, rows, duplicates, meta, user["id"])
        write_audit(
            conn,
            user["id"],
            "import.weekly_excel",
            "batch",
            batch_id,
            batch_id,
            new_value={"filename": file.filename, "rows": len(rows), "images": saved_images, "job_id": job_id},
        )
        if created_new_batch or existing_request_count == 0:
            create_batch_snapshot(conn, int(batch_id), "baseline", user["id"], replace_existing=True)
    return {
        "job_id": job_id,
        "batch_id": batch_id,
        "imported_rows": len(imported),
        "duplicate_rows": len(duplicates),
        "duplicates": duplicates[:100],
        "meta": meta,
    }


@app.post("/api/import/dingtalk")
async def import_dingtalk(
    file: UploadFile = File(...),
    batch_id: Optional[int] = Form(None),
    mapping_json: Optional[str] = Form(None),
    user: Dict[str, Any] = Depends(require_roles(*ALL_ROLES)),
) -> Dict[str, Any]:
    saved_path = await save_upload(file)
    if not mapping_json:
        headers, preview = detect_table_headers(saved_path)
        return {
            "status": "needs_mapping",
            "headers": headers,
            "target_fields": TARGET_FIELDS,
            "suggested_mapping": suggest_mapping(headers),
            "preview": preview,
        }
    mapping = json.loads(mapping_json)
    rows, meta = parse_dingtalk_file(saved_path, mapping)
    if any(float(row.get("paid_amount") or 0) > 0 for row in rows) and user["role"] not in FINANCE_FIELD_ROLES:
        raise HTTPException(status_code=403, detail="包含付款数据的文件只能由财务、总经理或管理员导入")
    meta["source_copy"] = str(saved_path.relative_to(DATA_DIR))
    start_date, end_date, default_name = parse_batch_dates(file.filename or saved_path.name)
    with connect() as conn:
        created_new_batch = batch_id is None
        existing_request_count = 0
        if batch_id is None:
            timestamp = now_iso()
            cursor = conn.execute(
                """
                INSERT INTO request_batches (name, start_date, end_date, status, source_file, created_by, created_at, updated_at)
                VALUES (?, ?, ?, 'draft', ?, ?, ?, ?)
                """,
                (default_name or "钉钉导入批次", start_date, end_date, file.filename, user["id"], timestamp, timestamp),
            )
            batch_id = cursor.lastrowid
        else:
            batch = require_batch(conn, batch_id)
            ensure_editable(batch, user)
            existing_request_count = conn.execute(
                "SELECT COUNT(*) AS count FROM payment_requests WHERE batch_id = ?",
                (batch_id,),
            ).fetchone()["count"]
        duplicates = duplicate_candidates(conn, batch_id, rows)
        imported = [insert_request(conn, batch_id, row, user["id"], user["role"]) for row in rows]
        conn.execute(
            """
            INSERT INTO import_mappings (name, mapping_json, created_at, updated_at)
            VALUES ('dingtalk-default', ?, ?, ?)
            ON CONFLICT(name) DO UPDATE SET mapping_json = excluded.mapping_json, updated_at = excluded.updated_at
            """,
            (json.dumps(mapping, ensure_ascii=False), now_iso(), now_iso()),
        )
        meta["request_ids"] = imported
        job_id = write_import_job(conn, "dingtalk", file.filename or saved_path.name, "imported", batch_id, rows, duplicates, {"mapping": mapping, **meta}, user["id"])
        write_audit(conn, user["id"], "import.dingtalk", "batch", batch_id, batch_id, new_value={"filename": file.filename, "rows": len(rows), "job_id": job_id})
        if created_new_batch or existing_request_count == 0:
            create_batch_snapshot(conn, int(batch_id), "baseline", user["id"], replace_existing=True)
    return {"status": "imported", "job_id": job_id, "batch_id": batch_id, "imported_rows": len(imported), "duplicate_rows": len(duplicates), "duplicates": duplicates[:100], "meta": meta}


@app.post("/api/external-expenses/preview")
def preview_external_expense_rows(
    payload: ExternalExpensePreviewIn,
    user: Dict[str, Any] = Depends(require_roles(*ALL_ROLES)),
) -> Dict[str, Any]:
    date_from, date_to = validate_external_expense_filter(payload)
    with connect() as conn:
        require_batch(conn, payload.batch_id)
    try:
        source_result = preview_external_expenses(
            date_from=date_from,
            date_to=date_to,
            source_types=payload.source_types,
            approval_no=payload.approval_no,
            applicant_ids=payload.applicant_ids,
        )
    except ExternalExpenseError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    rows = source_result["rows"]
    approval_counts = Counter(row.get("approval_no") for row in rows if row.get("approval_no"))
    conflict_approval_nos = {approval_no for approval_no, count in approval_counts.items() if count > 1}
    with connect() as conn:
        duplicates = external_expense_duplicate_map(conn, [row.get("approval_no") for row in rows])

    public_rows: list[Dict[str, Any]] = []
    for source_row in rows:
        row = {key: value for key, value in source_row.items() if key != "request_data"}
        approval_no = row.get("approval_no") or ""
        if approval_no in conflict_approval_nos:
            row["source_conflict"] = True
            if "同一钉钉单号存在多条来源记录" not in row["errors"]:
                row["errors"].append("同一钉钉单号存在多条来源记录")
        row["duplicate"] = duplicates.get(approval_no)
        row["importable"] = not row["errors"] and row["duplicate"] is None
        public_rows.append(row)

    summary = {
        "matched": len(public_rows),
        "importable": sum(1 for row in public_rows if row["importable"]),
        "duplicates": sum(1 for row in public_rows if row["duplicate"] is not None),
        "warnings": sum(1 for row in public_rows if row["warnings"]),
        "invalid": sum(1 for row in public_rows if row["errors"]),
    }
    result_predicates = {
        "matched": lambda row: True,
        "importable": lambda row: row["importable"],
        "duplicates": lambda row: row["duplicate"] is not None,
        "warnings": lambda row: bool(row["warnings"]),
        "invalid": lambda row: bool(row["errors"]),
    }
    filtered_rows = [row for row in public_rows if result_predicates[payload.result_filter](row)]

    page_size = min(50, max(1, payload.page_size))
    total = len(filtered_rows)
    total_pages = max(1, (total + page_size - 1) // page_size)
    page = min(max(1, payload.page), total_pages)
    start = (page - 1) * page_size
    page_rows = filtered_rows[start : start + page_size]
    return {
        "rows": page_rows,
        "all_rows": public_rows,
        "applicant_options": source_result["applicant_options"],
        "summary": summary,
        "pagination": {
            "page": page,
            "page_size": page_size,
            "total": total,
            "total_pages": total_pages,
        },
    }


@app.post("/api/batches/{batch_id}/imports/external-expenses")
def import_external_expense_rows(
    batch_id: int,
    payload: ExternalExpenseImportIn,
    user: Dict[str, Any] = Depends(require_roles(*ALL_ROLES)),
) -> Dict[str, Any]:
    if not payload.items:
        raise HTTPException(status_code=400, detail="请至少选择一条来源记录")
    if len(payload.items) > 200:
        raise HTTPException(status_code=400, detail="单次最多导入 200 条来源记录")
    keys: list[Dict[str, str]] = []
    seen_keys: set[tuple[str, str]] = set()
    for item in payload.items:
        source_type = item.source_type.strip()
        source_id = item.source_id.strip()
        if source_type not in ALLOWED_SOURCE_TYPES or not source_id.isdigit():
            raise HTTPException(status_code=400, detail="来源记录标识无效")
        key = (source_type, source_id)
        if key not in seen_keys:
            seen_keys.add(key)
            keys.append({"source_type": source_type, "source_id": source_id})
    try:
        source_rows = fetch_external_expenses(keys)
    except ExternalExpenseError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    rows_by_key = {(row["source_type"], row["source_id"]): row for row in source_rows}
    missing_keys = [key for key in seen_keys if key not in rows_by_key]
    invalid_rows = [row for row in source_rows if row.get("errors")]
    candidate_rows = [row for row in source_rows if not row.get("errors")]
    imported_ids: list[int] = []
    imported_rows: list[Dict[str, Any]] = []
    skipped_duplicates: list[Dict[str, Any]] = []

    with connect() as conn:
        batch = require_batch(conn, batch_id)
        if batch["status"] != "draft":
            raise HTTPException(status_code=400, detail="只能向草稿批次导入中间表数据")
        conn.execute("BEGIN IMMEDIATE")
        existing_request_count = conn.execute(
            "SELECT COUNT(*) AS count FROM payment_requests WHERE batch_id = ?",
            (batch_id,),
        ).fetchone()["count"]
        duplicates = external_expense_duplicate_map(conn, [row.get("approval_no") for row in candidate_rows])
        for source_row in candidate_rows:
            approval_no = source_row.get("approval_no") or ""
            duplicate = duplicates.get(approval_no)
            if duplicate:
                skipped_duplicates.append({
                    "source_type": source_row["source_type"],
                    "source_id": source_row["source_id"],
                    "approval_no": approval_no,
                    "existing": duplicate,
                })
                continue
            request_data = dict(source_row["request_data"])
            request_id = insert_request(
                conn,
                batch_id,
                request_data,
                user["id"],
                user["role"],
                create_summary_payment=False,
            )
            imported_ids.append(request_id)
            imported_rows.append({
                "source_type": source_row["source_type"],
                "source_id": source_row["source_id"],
                "approval_no": approval_no,
                "warnings": source_row.get("warnings", []),
                "request_data": request_data,
            })
            duplicates[approval_no] = {
                "request_id": request_id,
                "batch_id": batch_id,
                "batch_name": batch["name"],
            }

        job_id: Optional[int] = None
        if imported_ids:
            meta = {
                "request_ids": imported_ids,
                "source_items": [
                    {key: row[key] for key in ("source_type", "source_id", "approval_no")}
                    for row in imported_rows
                ],
                "warnings": [
                    {"approval_no": row["approval_no"], "messages": row["warnings"]}
                    for row in imported_rows
                    if row["warnings"]
                ],
                "errors": [
                    {"source_type": row["source_type"], "source_id": row["source_id"], "messages": row["errors"]}
                    for row in invalid_rows
                ],
                "missing_source_items": [
                    {"source_type": source_type, "source_id": source_id}
                    for source_type, source_id in missing_keys
                ],
            }
            job_id = write_import_job(
                conn,
                "external-expenses",
                f"中间表拉取-{date.today().isoformat()}",
                "imported",
                batch_id,
                imported_rows,
                skipped_duplicates,
                meta,
                user["id"],
            )
            write_audit(
                conn,
                user["id"],
                "import.external_expenses",
                "batch",
                batch_id,
                batch_id,
                new_value={
                    "job_id": job_id,
                    "imported_rows": len(imported_ids),
                    "duplicate_rows": len(skipped_duplicates),
                    "invalid_rows": len(invalid_rows) + len(missing_keys),
                },
            )
            if existing_request_count == 0:
                create_batch_snapshot(conn, batch_id, "baseline", user["id"], replace_existing=True)

    return {
        "status": "imported",
        "job_id": job_id,
        "batch_id": batch_id,
        "imported_rows": len(imported_ids),
        "duplicate_rows": len(skipped_duplicates),
        "invalid_rows": len(invalid_rows) + len(missing_keys),
        "warnings": sum(1 for row in imported_rows if row["warnings"]),
        "duplicates": skipped_duplicates,
        "errors": [
            {"source_type": row["source_type"], "source_id": row["source_id"], "messages": row["errors"]}
            for row in invalid_rows
        ] + [
            {"source_type": source_type, "source_id": source_id, "messages": ["来源记录不存在"]}
            for source_type, source_id in missing_keys
        ],
    }


@app.post("/api/batches/{batch_id}/external-expenses/sync-metadata")
def sync_external_expense_metadata(
    batch_id: int,
    user: Dict[str, Any] = Depends(require_roles(*ALL_ROLES)),
) -> Dict[str, Any]:
    with connect() as conn:
        batch = require_batch(conn, batch_id)
        if batch["status"] != "draft":
            raise HTTPException(status_code=400, detail="只能同步草稿批次的钉钉状态")
        initial_rows = conn.execute(
            """
            SELECT id, dingding_id
            FROM payment_requests
            WHERE batch_id = ? AND TRIM(COALESCE(dingding_id, '')) <> ''
            ORDER BY id
            """,
            (batch_id,),
        ).fetchall()

    initial_approval_by_request = {
        int(row["id"]): str(row["dingding_id"] or "").strip()
        for row in initial_rows
        if str(row["dingding_id"] or "").strip()
    }
    approval_nos = sorted(set(initial_approval_by_request.values()))
    try:
        source_metadata = fetch_external_expense_metadata(approval_nos)
    except ExternalExpenseError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    metadata_by_approval: Dict[str, list[Dict[str, Any]]] = {}
    for metadata in source_metadata:
        approval_no = str(metadata.get("approval_no") or "").strip()
        if approval_no:
            metadata_by_approval.setdefault(approval_no, []).append(metadata)

    matched = {approval_no for approval_no, values in metadata_by_approval.items() if len(values) == 1}
    conflicts = {approval_no for approval_no, values in metadata_by_approval.items() if len(values) > 1}
    unmatched = set(approval_nos) - matched - conflicts
    timestamp = now_iso()
    updated_requests = 0
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        batch = require_batch(conn, batch_id)
        if batch["status"] != "draft":
            raise HTTPException(status_code=400, detail="只能同步草稿批次的钉钉状态")
        current_rows = conn.execute(
            "SELECT * FROM payment_requests WHERE batch_id = ? ORDER BY id",
            (batch_id,),
        ).fetchall()
        for row in current_rows:
            request_id = int(row["id"])
            approval_no = str(row["dingding_id"] or "").strip()
            if not approval_no or initial_approval_by_request.get(request_id) != approval_no:
                continue
            request_data = row_to_dict(row)
            raw_extra = dict(request_data.get("raw_extra") or {})
            existing_source = dict(raw_extra.get("external_source") or {})
            if approval_no in matched:
                external_source = {
                    **existing_source,
                    **metadata_by_approval[approval_no][0],
                    "system": "dingtalk_expense_database",
                    "lookup_status": "matched",
                    "metadata_synced_at": timestamp,
                }
            elif approval_no in conflicts:
                external_source = {
                    **existing_source,
                    "system": "dingtalk_expense_database",
                    "approval_no": approval_no,
                    "approval_status": None,
                    "approval_result": None,
                    "lookup_status": "conflict",
                    "metadata_synced_at": timestamp,
                }
            else:
                external_source = {
                    **existing_source,
                    "system": "dingtalk_expense_database",
                    "approval_no": approval_no,
                    "approval_status": None,
                    "approval_result": None,
                    "lookup_status": "unmatched",
                    "metadata_synced_at": timestamp,
                }
            raw_extra["external_source"] = external_source
            conn.execute(
                """
                UPDATE payment_requests
                SET raw_extra_json = ?, updated_by = ?, updated_at = ?
                WHERE id = ? AND batch_id = ?
                """,
                (json.dumps(raw_extra, ensure_ascii=False, default=str), user["id"], timestamp, request_id, batch_id),
            )
            updated_requests += 1

        summary = {
            "unique_approval_nos": len(approval_nos),
            "matched": len(matched),
            "unmatched": len(unmatched),
            "conflicts": len(conflicts),
            "updated_requests": updated_requests,
        }
        write_audit(
            conn,
            user["id"],
            "external_expenses.metadata_sync",
            "batch",
            batch_id,
            batch_id=batch_id,
            new_value=summary,
        )
    return {"status": "synced", "batch_id": batch_id, **summary}


@app.get("/api/import-jobs/{job_id}")
def get_import_job(job_id: int, user: Dict[str, Any] = Depends(current_user)) -> Dict[str, Any]:
    with connect() as conn:
        row = conn.execute("SELECT * FROM import_jobs WHERE id = ?", (job_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="导入任务不存在")
    return {"job": row_to_dict(row)}


@app.post("/api/batches/{batch_id}/imports/latest/rollback")
def rollback_latest_import(batch_id: int, user: Dict[str, Any] = Depends(require_roles(*FINANCE_FIELD_ROLES))) -> Dict[str, Any]:
    with connect() as conn:
        batch = require_batch(conn, batch_id)
        if batch["status"] == "archived" and user["role"] not in PRIVILEGED_ROLES:
            raise HTTPException(status_code=403, detail="归档批次只能由管理员或总经理撤回导入")
        job = conn.execute(
            """
            SELECT * FROM import_jobs
            WHERE batch_id = ?
              AND status = 'imported'
              AND kind IN ('weekly-excel', 'dingtalk', 'external-expenses')
            ORDER BY created_at DESC, id DESC
            LIMIT 1
            """,
            (batch_id,),
        ).fetchone()
        if not job:
            raise HTTPException(status_code=404, detail="没有可撤回的导入任务")
        try:
            meta = json.loads(job["mapping_json"] or "{}")
        except json.JSONDecodeError:
            meta = {}
        raw_payment_ids = [
            int(value)
            for value in (meta.get("payment_details") or {}).get("payment_ids", [])
            if str(value).isdigit()
        ]
        raw_request_ids = [int(value) for value in meta.get("request_ids", []) if str(value).isdigit()]
        if raw_request_ids:
            request_placeholders = ", ".join("?" for _ in raw_request_ids)
            request_rows = conn.execute(
                f"SELECT id FROM payment_requests WHERE batch_id = ? AND id IN ({request_placeholders}) ORDER BY id",
                [batch_id, *raw_request_ids],
            ).fetchall()
            request_ids = [row["id"] for row in request_rows]
        else:
            request_rows = conn.execute(
                """
                SELECT id FROM payment_requests
                WHERE batch_id = ? AND created_at = ? AND created_by = ?
                ORDER BY id
                """,
                (job["batch_id"], job["created_at"], job["created_by"]),
            ).fetchall()
            request_ids = [row["id"] for row in request_rows]
        if raw_payment_ids:
            raw_payment_placeholders = ", ".join("?" for _ in raw_payment_ids)
            explicit_payment_rows = rows_to_dicts(
                conn.execute(
                    f"""
                    SELECT payment_records.* FROM payment_records
                    JOIN payment_requests ON payment_requests.id = payment_records.request_id
                    WHERE payment_requests.batch_id = ?
                      AND payment_records.id IN ({raw_payment_placeholders})
                    """,
                    [batch_id, *raw_payment_ids],
                ).fetchall()
            )
        else:
            explicit_payment_rows = []
        if not request_ids and not explicit_payment_rows:
            raise HTTPException(status_code=404, detail="未找到这次导入生成的请款或付款明细")
        if request_ids:
            placeholders = ", ".join("?" for _ in request_ids)
            attachment_rows = rows_to_dicts(
                conn.execute(f"SELECT * FROM attachment_links WHERE request_id IN ({placeholders})", request_ids).fetchall()
            )
            request_payment_rows = rows_to_dicts(
                conn.execute(f"SELECT * FROM payment_records WHERE request_id IN ({placeholders})", request_ids).fetchall()
            )
        else:
            placeholders = ""
            attachment_rows = []
            request_payment_rows = []
        payment_rows_by_id = {
            int(row["id"]): row for row in [*request_payment_rows, *explicit_payment_rows]
        }
        payment_rows = list(payment_rows_by_id.values())
        payment_ids = [int(row["id"]) for row in payment_rows]
        if payment_ids:
            payment_placeholders = ", ".join("?" for _ in payment_ids)
            voucher_rows = rows_to_dicts(
                conn.execute(
                    f"SELECT * FROM payment_vouchers WHERE payment_id IN ({payment_placeholders})",
                    payment_ids,
                ).fetchall()
            )
        else:
            voucher_rows = []
        owned_file_paths = {
            str(row["file_path"])
            for row in [*attachment_rows, *voucher_rows]
            if row.get("file_path")
        }
        surviving_request_ids = {
            int(row["request_id"])
            for row in explicit_payment_rows
            if int(row["request_id"]) not in set(request_ids)
        }
        if explicit_payment_rows:
            explicit_ids = [int(row["id"]) for row in explicit_payment_rows]
            explicit_placeholders = ", ".join("?" for _ in explicit_ids)
            conn.execute(f"DELETE FROM payment_records WHERE id IN ({explicit_placeholders})", explicit_ids)
        if request_ids:
            conn.execute(f"DELETE FROM attachment_links WHERE request_id IN ({placeholders})", request_ids)
            conn.execute(f"DELETE FROM payment_requests WHERE id IN ({placeholders})", request_ids)
        for surviving_request_id in surviving_request_ids:
            refresh_payment_summaries(conn, surviving_request_id)
        conn.execute("UPDATE import_jobs SET status = 'rolled_back', imported_rows = 0 WHERE id = ?", (job["id"],))
        conn.execute("UPDATE request_batches SET updated_at = ? WHERE id = ?", (now_iso(), batch_id))
        removed_files = 0
        for file_path in owned_file_paths:
            before = resolve_data_file(file_path)
            delete_file_if_unreferenced(conn, file_path)
            if not before.exists():
                removed_files += 1
        source_copy = meta.get("source_copy")
        if source_copy:
            path = resolve_data_file(source_copy)
            if path.exists():
                path.unlink()
                removed_files += 1
        write_audit(
            conn,
            user["id"],
            "import.rollback",
            "import_job",
            job["id"],
            batch_id,
            old_value={"job_id": job["id"], "kind": job["kind"], "filename": job["filename"], "imported_rows": job["imported_rows"]},
            new_value={
                "deleted_requests": len(request_ids),
                "deleted_attachments": len(attachment_rows),
                "deleted_payments": len(payment_rows),
                "deleted_payment_vouchers": len(voucher_rows),
                "removed_files": removed_files,
            },
            reason="撤回最近导入",
        )
    return {
        "status": "rolled_back",
        "job_id": job["id"],
        "batch_id": batch_id,
        "deleted_requests": len(request_ids),
        "deleted_attachments": len(attachment_rows),
        "deleted_payments": len(payment_rows),
        "deleted_payment_vouchers": len(voucher_rows),
        "removed_files": removed_files,
    }


@app.get("/api/batches/{batch_id}/export.xlsx")
def export_batch(batch_id: int, user: Dict[str, Any] = Depends(current_user)) -> StreamingResponse:
    with connect() as conn:
        batch = require_batch(conn, batch_id)
        records = rows_to_dicts(conn.execute("SELECT * FROM payment_requests WHERE batch_id = ? ORDER BY source_sheet, source_row, id", (batch_id,)).fetchall())
        links = rows_to_dicts(
            conn.execute(
                """
                SELECT attachment_links.* FROM attachment_links
                JOIN payment_requests ON payment_requests.id = attachment_links.request_id
                WHERE payment_requests.batch_id = ?
                """,
                (batch_id,),
            ).fetchall()
        )
        attachments: Dict[int, list[Dict[str, Any]]] = {}
        for link in links:
            if link.get("file_path"):
                link["absolute_path"] = str(resolve_data_file(link["file_path"]))
            attachments.setdefault(link["request_id"], []).append(link)
        payments = rows_to_dicts(
            conn.execute(
                """
                SELECT payment_records.*, payment_requests.dingding_id,
                       payment_requests.source_sheet AS request_source_sheet
                FROM payment_records
                JOIN payment_requests ON payment_requests.id = payment_records.request_id
                WHERE payment_requests.batch_id = ?
                ORDER BY payment_records.payment_date, payment_records.id
                """,
                (batch_id,),
            ).fetchall()
        )
        voucher_rows = rows_to_dicts(
            conn.execute(
                """
                SELECT payment_vouchers.* FROM payment_vouchers
                JOIN payment_records ON payment_records.id = payment_vouchers.payment_id
                JOIN payment_requests ON payment_requests.id = payment_records.request_id
                WHERE payment_requests.batch_id = ?
                ORDER BY payment_vouchers.id
                """,
                (batch_id,),
            ).fetchall()
        )
        vouchers_by_payment: Dict[int, list[Dict[str, Any]]] = {}
        for voucher in voucher_rows:
            voucher["file_url"] = f"/api/payment-vouchers/{voucher['id']}/file"
            if str(voucher.get("mime_type") or "").startswith("image/") and voucher.get("file_path"):
                voucher["absolute_path"] = str(resolve_data_file(voucher["file_path"]))
            vouchers_by_payment.setdefault(int(voucher["payment_id"]), []).append(voucher)
        for payment in payments:
            payment["vouchers"] = vouchers_by_payment.get(int(payment["id"]), [])
        content = export_workbook(row_to_dict(batch), records, attachments, payments)
    filename = f"{batch['name']}.xlsx".replace("/", "_")
    return StreamingResponse(
        iter([content]),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"},
    )


@app.get("/api/batches/{batch_id}/audit")
def list_audit(batch_id: int, user: Dict[str, Any] = Depends(current_user)) -> Dict[str, Any]:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT audit_logs.*, users.display_name AS actor_name
            FROM audit_logs
            LEFT JOIN users ON users.id = audit_logs.actor_id
            WHERE audit_logs.batch_id = ?
            ORDER BY audit_logs.id DESC
            LIMIT 300
            """,
            (batch_id,),
        ).fetchall()
    return {"logs": rows_to_dicts(rows)}


@app.get("/api/admin/users")
def list_users(user: Dict[str, Any] = Depends(require_roles(*GENERAL_MANAGER_ROLES))) -> Dict[str, Any]:
    with connect() as conn:
        rows = conn.execute("SELECT * FROM users WHERE deleted_at IS NULL ORDER BY id").fetchall()
    return {"users": [user_public(row_to_dict(row)) for row in rows]}


@app.post("/api/admin/users")
def create_user(payload: UserIn, user: Dict[str, Any] = Depends(require_roles(*GENERAL_MANAGER_ROLES))) -> Dict[str, Any]:
    validate_user_role(payload.role)
    if not payload.username.strip() or not payload.password or not payload.display_name.strip():
        raise HTTPException(status_code=400, detail="账号、姓名和初始密码不能为空")
    with connect() as conn:
        try:
            cursor = conn.execute(
                """
                INSERT INTO users (username, password_hash, role, display_name, active, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (payload.username.strip(), hash_password(payload.password), payload.role, payload.display_name.strip(), int(payload.active), now_iso()),
            )
        except Exception as exc:
            if "UNIQUE" in str(exc).upper():
                raise HTTPException(status_code=400, detail="账号已存在") from exc
            raise
        row = conn.execute("SELECT * FROM users WHERE id = ?", (cursor.lastrowid,)).fetchone()
        write_audit(conn, user["id"], "user.create", "user", cursor.lastrowid, new_value=user_public(row_to_dict(row)))
    return {"user": user_public(row_to_dict(row))}


@app.patch("/api/admin/users/{user_id}")
def update_user(user_id: int, payload: UserPatch, user: Dict[str, Any] = Depends(require_roles(*GENERAL_MANAGER_ROLES))) -> Dict[str, Any]:
    updates = payload.dict(exclude_unset=True)
    if "password" in updates:
        password = updates.pop("password")
        if password:
            updates["password_hash"] = hash_password(password)
    if "active" in updates:
        updates["active"] = int(updates["active"])
    if not updates:
        raise HTTPException(status_code=400, detail="没有需要更新的字段")
    allowed = {"password_hash", "role", "display_name", "active"}
    columns = [key for key in updates if key in allowed]
    if not columns:
        raise HTTPException(status_code=400, detail="没有需要更新的字段")
    with connect() as conn:
        old = conn.execute("SELECT * FROM users WHERE id = ? AND deleted_at IS NULL", (user_id,)).fetchone()
        if not old:
            raise HTTPException(status_code=404, detail="用户不存在")
        ensure_can_change_user(conn, old, user, updates)
        if "display_name" in updates:
            updates["display_name"] = str(updates["display_name"]).strip()
        conn.execute(
            f"UPDATE users SET {', '.join(f'{col} = ?' for col in columns)} WHERE id = ?",
            [updates[col] for col in columns] + [user_id],
        )
        if "active" in updates and updates["active"] == 0:
            conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        audit_new_value = user_public(row_to_dict(row))
        if "password_hash" in updates:
            audit_new_value["password_reset"] = True
        action = "user.update"
        if "active" in updates and len(columns) == 1:
            action = "user.activate" if updates["active"] else "user.deactivate"
        elif "active" in updates and old["active"] != updates["active"]:
            action = "user.activate" if updates["active"] else "user.deactivate"
        write_audit(conn, user["id"], action, "user", user_id, old_value=user_public(row_to_dict(old)), new_value=audit_new_value)
    return {"user": user_public(row_to_dict(row))}


@app.post("/api/admin/users/{user_id}/reset-password")
def reset_user_password(user_id: int, user: Dict[str, Any] = Depends(require_roles(*GENERAL_MANAGER_ROLES))) -> Dict[str, Any]:
    with connect() as conn:
        old = conn.execute("SELECT * FROM users WHERE id = ? AND deleted_at IS NULL", (user_id,)).fetchone()
        if not old:
            raise HTTPException(status_code=404, detail="用户不存在")
        conn.execute("UPDATE users SET password_hash = ? WHERE id = ?", (hash_password(DEFAULT_RESET_PASSWORD), user_id))
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        audit_new_value = user_public(row_to_dict(row))
        audit_new_value["password_reset"] = True
        write_audit(conn, user["id"], "user.reset_password", "user", user_id, old_value=user_public(row_to_dict(old)), new_value=audit_new_value)
    return {"user": user_public(row_to_dict(row)), "password": DEFAULT_RESET_PASSWORD}


@app.delete("/api/admin/users/{user_id}")
def delete_user(user_id: int, user: Dict[str, Any] = Depends(require_roles(*GENERAL_MANAGER_ROLES))) -> Dict[str, str]:
    with connect() as conn:
        old = conn.execute("SELECT * FROM users WHERE id = ? AND deleted_at IS NULL", (user_id,)).fetchone()
        if not old:
            raise HTTPException(status_code=404, detail="用户不存在")
        timestamp = now_iso()
        ensure_can_change_user(conn, old, user, {"active": 0, "deleted_at": timestamp})
        conn.execute("UPDATE users SET active = 0, deleted_at = ?, deleted_by = ? WHERE id = ?", (timestamp, user["id"], user_id))
        conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        write_audit(conn, user["id"], "user.delete", "user", user_id, old_value=user_public(row_to_dict(old)), new_value={**user_public(row_to_dict(row)), "deleted": True})
    return {"status": "ok"}


@app.get("/api/admin/dictionaries")
def list_dictionaries(user: Dict[str, Any] = Depends(current_user)) -> Dict[str, Any]:
    with connect() as conn:
        rows = conn.execute("SELECT * FROM dictionaries ORDER BY kind, value").fetchall()
    return {"dictionaries": rows_to_dicts(rows)}


@app.post("/api/admin/dictionaries")
def create_dictionary(payload: DictionaryIn, user: Dict[str, Any] = Depends(require_roles(*GENERAL_MANAGER_ROLES))) -> Dict[str, Any]:
    with connect() as conn:
        cursor = conn.execute(
            "INSERT OR IGNORE INTO dictionaries (kind, value, active, created_at) VALUES (?, ?, ?, ?)",
            (payload.kind, payload.value, int(payload.active), now_iso()),
        )
        row_id = cursor.lastrowid or conn.execute("SELECT id FROM dictionaries WHERE kind = ? AND value = ?", (payload.kind, payload.value)).fetchone()["id"]
        row = conn.execute("SELECT * FROM dictionaries WHERE id = ?", (row_id,)).fetchone()
        write_audit(conn, user["id"], "dictionary.create", "dictionary", row_id, new_value=row_to_dict(row))
    return {"dictionary": row_to_dict(row)}


@app.patch("/api/admin/dictionaries/{dictionary_id}")
def update_dictionary(dictionary_id: int, payload: DictionaryIn, user: Dict[str, Any] = Depends(require_roles(*GENERAL_MANAGER_ROLES))) -> Dict[str, Any]:
    with connect() as conn:
        old = conn.execute("SELECT * FROM dictionaries WHERE id = ?", (dictionary_id,)).fetchone()
        if not old:
            raise HTTPException(status_code=404, detail="字典项不存在")
        conn.execute(
            "UPDATE dictionaries SET kind = ?, value = ?, active = ? WHERE id = ?",
            (payload.kind, payload.value, int(payload.active), dictionary_id),
        )
        row = conn.execute("SELECT * FROM dictionaries WHERE id = ?", (dictionary_id,)).fetchone()
        write_audit(conn, user["id"], "dictionary.update", "dictionary", dictionary_id, old_value=row_to_dict(old), new_value=row_to_dict(row))
    return {"dictionary": row_to_dict(row)}


def require_batch(conn, batch_id: int):
    row = conn.execute("SELECT * FROM request_batches WHERE id = ?", (batch_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="批次不存在")
    return row


def batch_sheet_order(batch: Dict[str, Any]) -> list[str]:
    value = batch.get("sheet_order")
    if value is None and batch.get("sheet_order_json"):
        try:
            value = json.loads(batch["sheet_order_json"])
        except (TypeError, json.JSONDecodeError):
            value = []
    if not isinstance(value, list):
        return []
    order: list[str] = []
    seen: set[str] = set()
    for item in value:
        name = str(item or "").strip()
        if name and name not in seen:
            order.append(name)
            seen.add(name)
    return order


def validate_external_expense_filter(payload: ExternalExpensePreviewIn) -> tuple[Optional[date], Optional[date]]:
    source_types = {value.strip() for value in payload.source_types}
    if not source_types or not source_types <= set(ALLOWED_SOURCE_TYPES):
        raise HTTPException(status_code=400, detail="请选择有效的支出来源")
    if payload.result_filter not in {"matched", "importable", "duplicates", "warnings", "invalid"}:
        raise HTTPException(status_code=400, detail="请选择有效的校验结果筛选")
    if payload.page < 1 or payload.page_size < 1 or payload.page_size > 50:
        raise HTTPException(status_code=400, detail="分页参数无效")
    if payload.approval_no.strip():
        return None, None
    if not payload.date_from.strip() or not payload.date_to.strip():
        raise HTTPException(status_code=400, detail="请选择申请开始和结束日期")
    try:
        date_from = date.fromisoformat(payload.date_from)
        date_to = date.fromisoformat(payload.date_to)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="申请日期格式无效") from exc
    if date_to < date_from:
        raise HTTPException(status_code=400, detail="申请结束日期不能早于开始日期")
    if (date_to - date_from).days > 30:
        raise HTTPException(status_code=400, detail="单次查询的申请日期范围不能超过 31 天")
    return date_from, date_to


def external_expense_duplicate_map(conn, approval_nos: Iterable[Any]) -> Dict[str, Dict[str, Any]]:
    normalized = sorted({str(value or "").strip() for value in approval_nos if str(value or "").strip()})
    duplicates: Dict[str, Dict[str, Any]] = {}
    for start in range(0, len(normalized), 500):
        chunk = normalized[start : start + 500]
        placeholders = ", ".join("?" for _ in chunk)
        rows = conn.execute(
            f"""
            SELECT payment_requests.id AS request_id,
                   TRIM(payment_requests.dingding_id) AS approval_no,
                   payment_requests.batch_id,
                   payment_requests.source_sheet,
                   request_batches.name AS batch_name
            FROM payment_requests
            JOIN request_batches ON request_batches.id = payment_requests.batch_id
            WHERE TRIM(payment_requests.dingding_id) IN ({placeholders})
            ORDER BY payment_requests.id DESC
            """,
            chunk,
        ).fetchall()
        for row in rows:
            approval_no = str(row["approval_no"])
            duplicates.setdefault(
                approval_no,
                {
                    "request_id": row["request_id"],
                    "batch_id": row["batch_id"],
                    "batch_name": row["batch_name"],
                    "source_sheet": row["source_sheet"],
                },
            )
    return duplicates


def require_request(conn, batch_id: int, request_id: int):
    row = conn.execute("SELECT * FROM payment_requests WHERE id = ? AND batch_id = ?", (request_id, batch_id)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="请款记录不存在")
    return row


def request_owned_file_paths(conn, request_ids: list[int]) -> set[str]:
    if not request_ids:
        return set()
    placeholders = ", ".join("?" for _ in request_ids)
    rows = conn.execute(
        f"""
        SELECT file_path FROM attachment_links
        WHERE request_id IN ({placeholders}) AND file_path IS NOT NULL
        UNION ALL
        SELECT payment_vouchers.file_path FROM payment_vouchers
        JOIN payment_records ON payment_records.id = payment_vouchers.payment_id
        WHERE payment_records.request_id IN ({placeholders})
          AND payment_vouchers.file_path IS NOT NULL
        """,
        [*request_ids, *request_ids],
    ).fetchall()
    return {str(row["file_path"]) for row in rows if row["file_path"]}


def ensure_can_delete_request_with_payments(conn, request_id: int, user: Dict[str, Any]) -> None:
    has_payments = conn.execute(
        "SELECT 1 FROM payment_records WHERE request_id = ? LIMIT 1",
        (request_id,),
    ).fetchone()
    if has_payments and user["role"] not in FINANCE_FIELD_ROLES:
        raise HTTPException(status_code=403, detail="包含付款明细的请款只能由财务、总经理或管理员删除")


def require_payment_record(conn, request_id: int, payment_id: int):
    row = conn.execute(
        "SELECT * FROM payment_records WHERE id = ? AND request_id = ?",
        (payment_id, request_id),
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="付款明细不存在")
    return row


def normalize_payment_date(value: Optional[str], *, required: bool = True) -> Optional[str]:
    text = str(value or "").strip()
    if not text:
        if required:
            raise HTTPException(status_code=400, detail="付款日期不能为空")
        return None
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="付款日期格式无效") from exc


def payment_record_amount(value: Any) -> float:
    try:
        amount = round(float(value), 2)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="付款金额无效") from exc
    if amount <= 0:
        raise HTTPException(status_code=400, detail="付款金额必须大于 0")
    return amount


def validate_payment_total(conn, request_id: int, amount: float, exclude_payment_id: Optional[int] = None) -> None:
    request = conn.execute("SELECT amount FROM payment_requests WHERE id = ?", (request_id,)).fetchone()
    if not request or request["amount"] is None:
        raise HTTPException(status_code=400, detail="请先填写应付金额")
    if exclude_payment_id is None:
        paid = conn.execute(
            "SELECT COALESCE(SUM(amount), 0) AS amount FROM payment_records WHERE request_id = ?",
            (request_id,),
        ).fetchone()["amount"]
    else:
        paid = conn.execute(
            "SELECT COALESCE(SUM(amount), 0) AS amount FROM payment_records WHERE request_id = ? AND id != ?",
            (request_id, exclude_payment_id),
        ).fetchone()["amount"]
    remaining = round(float(request["amount"]) - float(paid or 0), 2)
    if amount > remaining + 0.000001:
        raise HTTPException(status_code=400, detail=f"本次付款超过剩余可付金额 {remaining:.2f}")


def ensure_can_manage_payments(batch, user: Dict[str, Any], reason: Optional[str] = None) -> None:
    if user["role"] not in FINANCE_FIELD_ROLES:
        raise HTTPException(status_code=403, detail="只有财务、总经理或管理员可以维护付款明细")
    ensure_bulk_editable(batch, user, reason)


def ensure_editable(batch, user: Dict[str, Any]) -> None:
    if batch["status"] == "archived" and user["role"] not in PRIVILEGED_ROLES:
        raise HTTPException(status_code=403, detail="归档批次已锁定")


def ensure_bulk_editable(batch, user: Dict[str, Any], reason: Optional[str]) -> None:
    if batch["status"] == "archived" and user["role"] not in PRIVILEGED_ROLES:
        raise HTTPException(status_code=403, detail="归档批次已锁定")
    if batch["status"] == "archived" and not (reason or "").strip():
        raise HTTPException(status_code=400, detail="归档后更正必须填写原因")


def copy_attachment_links(conn, source_request_id: int, target_request_id: int, user_id: int) -> None:
    links = conn.execute(
        """
        SELECT label, url_path, attachment_type, file_path, original_filename, mime_type, file_size
        FROM attachment_links
        WHERE request_id = ?
        ORDER BY id
        """,
        (source_request_id,),
    ).fetchall()
    for link in links:
        conn.execute(
            """
            INSERT INTO attachment_links (
                request_id, label, url_path, attachment_type, file_path,
                original_filename, mime_type, file_size, created_by, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                target_request_id,
                link["label"],
                link["url_path"],
                link["attachment_type"],
                link["file_path"],
                link["original_filename"],
                link["mime_type"],
                link["file_size"],
                user_id,
                now_iso(),
            ),
        )


def insert_payment_record_internal(
    conn,
    request_id: int,
    *,
    amount: Any,
    payment_date: Optional[str],
    payer: Optional[str],
    payment_account: Optional[str],
    bank_reference: Optional[str],
    remark: Optional[str],
    source_type: str,
    user_id: Optional[int],
    copied_from_payment_id: Optional[int] = None,
    root_payment_id: Optional[int] = None,
    validate_total: bool = True,
) -> int:
    normalized_amount = payment_record_amount(amount)
    normalized_date = normalize_payment_date(
        payment_date,
        required=source_type not in {"legacy_migration", "snapshot_legacy", "excel_summary", "rollover"},
    )
    if validate_total:
        validate_payment_total(conn, request_id, normalized_amount)
    timestamp = now_iso()
    record_hash = payment_record_hash(request_id, normalized_amount, normalized_date, payer, bank_reference)
    cursor = conn.execute(
        """
        INSERT INTO payment_records (
            request_id, copied_from_payment_id, root_payment_id, amount, payment_date,
            payer, payment_account, bank_reference, remark, source_type, content_hash,
            created_by, updated_by, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            request_id,
            copied_from_payment_id,
            root_payment_id,
            normalized_amount,
            normalized_date,
            str(payer or "").strip() or None,
            str(payment_account or "").strip() or None,
            str(bank_reference or "").strip() or None,
            str(remark or "").strip() or None,
            source_type,
            record_hash,
            user_id,
            user_id,
            timestamp,
            timestamp,
        ),
    )
    payment_id = int(cursor.lastrowid)
    if root_payment_id is None:
        conn.execute("UPDATE payment_records SET root_payment_id = ? WHERE id = ?", (payment_id, payment_id))
    refresh_payment_summaries(conn, request_id)
    return payment_id


def copy_payment_records(conn, source_request_id: int, target_request_id: int, user_id: int) -> int:
    payments = conn.execute(
        "SELECT * FROM payment_records WHERE request_id = ? ORDER BY id",
        (source_request_id,),
    ).fetchall()
    copied = 0
    for payment in payments:
        target_payment_id = insert_payment_record_internal(
            conn,
            target_request_id,
            amount=payment["amount"],
            payment_date=payment["payment_date"],
            payer=payment["payer"],
            payment_account=payment["payment_account"],
            bank_reference=payment["bank_reference"],
            remark=payment["remark"],
            source_type="rollover",
            user_id=user_id,
            copied_from_payment_id=payment["id"],
            root_payment_id=payment["root_payment_id"] or payment["id"],
            validate_total=False,
        )
        vouchers = conn.execute("SELECT * FROM payment_vouchers WHERE payment_id = ? ORDER BY id", (payment["id"],)).fetchall()
        for voucher in vouchers:
            conn.execute(
                """
                INSERT INTO payment_vouchers (
                    payment_id, label, file_path, original_filename, mime_type,
                    file_size, created_by, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    target_payment_id,
                    voucher["label"],
                    voucher["file_path"],
                    voucher["original_filename"],
                    voucher["mime_type"],
                    voucher["file_size"],
                    user_id,
                    now_iso(),
                ),
            )
        copied += 1
    refresh_payment_summaries(conn, target_request_id)
    return copied


def insert_request(
    conn,
    batch_id: int,
    data: Dict[str, Any],
    user_id: int,
    user_role: str = ROLE_GENERAL_MANAGER,
    *,
    create_summary_payment: bool = True,
) -> int:
    data = enforce_request_field_permissions(data, user_role, creating=True)
    summary_paid_amount = data.get("paid_amount")
    summary_payment_date = data.get("actual_payment_date")
    summary_payer = data.get("payer")
    summary_payment_account = data.get("payment_account")
    summary_source_type = str(data.get("_payment_source_type") or "excel_summary")
    payload = normalize_request_payload(data)
    timestamp = now_iso()
    columns = [
        "batch_id",
        *payload.keys(),
        "created_by",
        "updated_by",
        "created_at",
        "updated_at",
    ]
    values = [batch_id, *payload.values(), user_id, user_id, timestamp, timestamp]
    placeholders = ", ".join(["?"] * len(columns))
    cursor = conn.execute(f"INSERT INTO payment_requests ({', '.join(columns)}) VALUES ({placeholders})", values)
    request_id = int(cursor.lastrowid)
    if create_summary_payment and summary_paid_amount not in (None, "") and float(summary_paid_amount or 0) > 0:
        insert_payment_record_internal(
            conn,
            request_id,
            amount=summary_paid_amount,
            payment_date=summary_payment_date,
            payer=summary_payer,
            payment_account=summary_payment_account,
            bank_reference=None,
            remark="由导入汇总金额生成",
            source_type=summary_source_type,
            user_id=user_id,
            validate_total=True,
        )
    else:
        refresh_payment_summaries(conn, request_id)
    conn.execute("UPDATE request_batches SET updated_at = ? WHERE id = ?", (timestamp, batch_id))
    return request_id


def normalize_request_payload(data: Dict[str, Any]) -> Dict[str, Any]:
    payload = {key: value for key, value in data.items() if key in REQUEST_WRITE_FIELDS}
    if "raw_extra" in data and "raw_extra_json" not in payload:
        payload["raw_extra_json"] = json.dumps(data["raw_extra"], ensure_ascii=False, default=str)
    if "raw_extra_json" not in payload:
        payload["raw_extra_json"] = json.dumps({}, ensure_ascii=False)
    if "currency" not in payload or not payload["currency"]:
        payload["currency"] = "CNY"
    normalize_request_business_fields(payload)
    if "content_hash" not in payload:
        payload["content_hash"] = content_hash(payload)
    return payload


def update_request_row(conn, request_id: int, data: Dict[str, Any], user_id: int, user_role: str = ROLE_GENERAL_MANAGER) -> bool:
    allowed = REQUEST_WRITE_FIELDS - {"content_hash"}
    payload = {key: value for key, value in data.items() if key in allowed}
    if "raw_extra" in data:
        payload["raw_extra_json"] = json.dumps(data["raw_extra"], ensure_ascii=False, default=str)
    elif "raw_extra_json" in data:
        payload["raw_extra_json"] = data["raw_extra_json"]
    existing_row = conn.execute("SELECT * FROM payment_requests WHERE id = ?", (request_id,)).fetchone()
    if not existing_row:
        raise HTTPException(status_code=404, detail="请款记录不存在")
    existing = row_to_dict(existing_row)
    reject_direct_payment_summary_changes(payload, existing)
    for field in DERIVED_PAYMENT_FIELDS:
        payload.pop(field, None)
    payload = enforce_request_field_permissions(payload, user_role, existing, creating=False)
    if not payload:
        return False
    if "amount" in payload:
        try:
            next_amount = round(float(payload["amount"]), 2) if payload["amount"] not in (None, "") else None
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail="应付金额无效") from exc
        paid_amount = float(
            conn.execute(
                "SELECT COALESCE(SUM(amount), 0) AS amount FROM payment_records WHERE request_id = ?",
                (request_id,),
            ).fetchone()["amount"]
            or 0
        )
        if next_amount is not None and paid_amount > next_amount + 0.000001:
            raise HTTPException(status_code=400, detail=f"应付金额不能低于累计已支付金额 {paid_amount:.2f}")
    merged = {**existing, **payload}
    normalize_request_business_fields(merged)
    for key in (
        "general_manager_approval",
        "general_manager_approval_date",
        "general_manager_opinion",
        "remark",
    ):
        if key in payload or merged.get(key) != existing.get(key):
            payload[key] = merged.get(key)
    changed_payload = {
        key: value
        for key, value in payload.items()
        if not request_values_equal(value, existing.get(key))
    }
    if not changed_payload:
        return False
    changed_payload["updated_by"] = user_id
    changed_payload["updated_at"] = now_iso()
    columns = list(changed_payload.keys())
    conn.execute(
        f"UPDATE payment_requests SET {', '.join(f'{col} = ?' for col in columns)} WHERE id = ?",
        [changed_payload[col] for col in columns] + [request_id],
    )
    refresh_payment_summaries(conn, request_id)
    row = row_to_dict(conn.execute("SELECT * FROM payment_requests WHERE id = ?", (request_id,)).fetchone())
    conn.execute("UPDATE payment_requests SET content_hash = ? WHERE id = ?", (content_hash(row), request_id))
    return True


def import_excel_payment_details(
    conn,
    batch_id: int,
    details: list[Dict[str, Any]],
    user_id: int,
    imported_summaries: Dict[int, float],
) -> Dict[str, Any]:
    imported_ids: list[int] = []
    errors: list[Dict[str, Any]] = []
    warnings: list[Dict[str, Any]] = []
    skipped_duplicates = 0
    saved_vouchers = 0
    skipped_vouchers = 0

    for detail in details:
        source_row = detail.get("source_row")
        request = None
        if detail.get("request_id"):
            request = conn.execute(
                "SELECT * FROM payment_requests WHERE id = ? AND batch_id = ?",
                (detail["request_id"], batch_id),
            ).fetchone()
        if request is None:
            dingding_id = str(detail.get("dingding_id") or "").strip()
            source_sheet = str(detail.get("source_sheet") or "").strip()
            if not dingding_id or not source_sheet:
                errors.append({"row": source_row, "message": "无法匹配请款：请款标识无效，且缺少钉钉单号或来源 Sheet"})
                continue
            matches = conn.execute(
                """
                SELECT * FROM payment_requests
                WHERE batch_id = ? AND dingding_id = ? AND source_sheet = ?
                ORDER BY id
                """,
                (batch_id, dingding_id, source_sheet),
            ).fetchall()
            if len(matches) != 1:
                message = "未找到匹配请款" if not matches else "匹配到多条请款"
                errors.append({"row": source_row, "message": f"{message}：{dingding_id} + {source_sheet}"})
                continue
            request = matches[0]

        request_id = int(request["id"])
        try:
            amount = payment_record_amount(detail.get("amount"))
            source_type = str(detail.get("source_type") or "excel_detail").strip() or "excel_detail"
            payment_date = normalize_payment_date(
                detail.get("payment_date"),
                required=source_type not in {"legacy_migration", "snapshot_legacy"},
            )
        except HTTPException as exc:
            errors.append({"row": source_row, "message": str(exc.detail)})
            continue

        duplicate = conn.execute(
            """
            SELECT id FROM payment_records
            WHERE request_id = ?
              AND COALESCE(payment_date, '') = COALESCE(?, '')
              AND ABS(amount - ?) < 0.000001
              AND COALESCE(bank_reference, '') = COALESCE(?, '')
              AND COALESCE(payer, '') = COALESCE(?, '')
            LIMIT 1
            """,
            (
                request_id,
                payment_date,
                amount,
                str(detail.get("bank_reference") or "").strip() or None,
                str(detail.get("payer") or "").strip() or None,
            ),
        ).fetchone()
        if duplicate:
            skipped_duplicates += 1
            warnings.append({"row": source_row, "message": f"重复付款明细已跳过（付款标识 {duplicate['id']}）"})
            continue
        try:
            payment_id = insert_payment_record_internal(
                conn,
                request_id,
                amount=amount,
                payment_date=payment_date,
                payer=detail.get("payer"),
                payment_account=detail.get("payment_account") or request["payment_account"],
                bank_reference=detail.get("bank_reference"),
                remark=detail.get("remark"),
                source_type=source_type,
                user_id=user_id,
            )
        except HTTPException as exc:
            errors.append({"row": source_row, "message": str(exc.detail)})
            continue
        row_saved, row_skipped = save_embedded_payment_vouchers(conn, batch_id, payment_id, detail, user_id)
        saved_vouchers += row_saved
        skipped_vouchers += row_skipped
        imported_ids.append(payment_id)
        inserted = conn.execute("SELECT * FROM payment_records WHERE id = ?", (payment_id,)).fetchone()
        write_audit(
            conn,
            user_id,
            "payment.import",
            "payment_record",
            payment_id,
            batch_id,
            new_value=row_to_dict(inserted),
            reason=f"Excel 付款明细第 {source_row} 行",
        )

    for request_id, summary_amount in imported_summaries.items():
        detail_total = float(
            conn.execute(
                "SELECT COALESCE(SUM(amount), 0) AS amount FROM payment_records WHERE request_id = ?",
                (request_id,),
            ).fetchone()["amount"]
            or 0
        )
        if abs(round(detail_total - summary_amount, 2)) > 0.000001:
            warnings.append(
                {
                    "request_id": request_id,
                    "message": f"主表累计已付 {summary_amount:.2f} 与付款明细合计 {detail_total:.2f} 不一致，已以付款明细为准",
                }
            )

    return {
        "rows": len(details),
        "imported": len(imported_ids),
        "payment_ids": imported_ids,
        "duplicates": skipped_duplicates,
        "errors": errors,
        "warnings": warnings,
        "saved_vouchers": saved_vouchers,
        "skipped_vouchers": skipped_vouchers,
    }


def duplicate_candidates(conn, batch_id: int, rows: list[Dict[str, Any]]) -> list[Dict[str, Any]]:
    duplicates = []
    for index, row in enumerate(rows, start=1):
        found = conn.execute(
            "SELECT id, dingding_id, amount, payee_name, source_sheet FROM payment_requests WHERE batch_id = ? AND content_hash = ?",
            (batch_id, row.get("content_hash")),
        ).fetchone()
        if found:
            incoming = {key: value for key, value in row.items() if not key.startswith("_")}
            duplicates.append({"import_index": index, "existing": row_to_dict(found), "incoming": incoming})
    return duplicates


def write_import_job(
    conn,
    kind: str,
    filename: str,
    status: str,
    batch_id: int,
    rows: list[Dict[str, Any]],
    duplicates: list[Dict[str, Any]],
    meta: Dict[str, Any],
    user_id: int,
) -> int:
    cursor = conn.execute(
        """
        INSERT INTO import_jobs (
            kind, filename, status, total_rows, imported_rows, duplicate_rows,
            errors_json, mapping_json, batch_id, created_by, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            kind,
            filename,
            status,
            len(rows),
            len(rows),
            len(duplicates),
            json.dumps(meta.get("errors", []), ensure_ascii=False, default=str),
            json.dumps(meta, ensure_ascii=False, default=str),
            batch_id,
            user_id,
            now_iso(),
        ),
    )
    return cursor.lastrowid


async def save_upload(file: UploadFile) -> Path:
    suffix = Path(file.filename or "upload.xlsx").suffix or ".xlsx"
    target = DATA_DIR / "uploads" / f"{now_iso().replace(':', '-')}-{Path(file.filename or 'upload').stem}{suffix}"
    with target.open("wb") as handle:
        shutil.copyfileobj(file.file, handle)
    return target


async def save_image_upload(file: UploadFile, batch_id: int) -> tuple[Path, Path, int]:
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in IMAGE_EXTENSIONS:
        raise HTTPException(status_code=400, detail="只支持 PNG、JPG、WEBP、GIF、BMP 图片")
    content_type = file.content_type or ""
    if content_type and not content_type.startswith("image/") and content_type != "application/octet-stream":
        raise HTTPException(status_code=400, detail="上传文件不是图片")
    content = await file.read(MAX_IMAGE_BYTES + 1)
    if not content:
        raise HTTPException(status_code=400, detail="图片不能为空")
    if len(content) > MAX_IMAGE_BYTES:
        raise HTTPException(status_code=400, detail="图片不能超过 12MB")
    directory = DATA_DIR / "uploads" / "attachments" / str(batch_id)
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{uuid.uuid4().hex}{suffix}"
    target.write_bytes(content)
    return target, target.relative_to(DATA_DIR), len(content)


async def save_payment_voucher_upload(file: UploadFile, batch_id: int, payment_id: int) -> tuple[Path, Path, int, str]:
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in PAYMENT_VOUCHER_EXTENSIONS:
        raise HTTPException(status_code=400, detail="付款凭证只支持图片或 PDF")
    content_type = file.content_type or ""
    expected_pdf = suffix == ".pdf"
    if content_type and content_type != "application/octet-stream":
        if expected_pdf and content_type != "application/pdf":
            raise HTTPException(status_code=400, detail="上传文件不是 PDF")
        if not expected_pdf and not content_type.startswith("image/"):
            raise HTTPException(status_code=400, detail="上传文件不是图片")
    content = await file.read(MAX_IMAGE_BYTES + 1)
    if not content:
        raise HTTPException(status_code=400, detail="付款凭证不能为空")
    if len(content) > MAX_IMAGE_BYTES:
        raise HTTPException(status_code=400, detail="付款凭证不能超过 12MB")
    directory = DATA_DIR / "uploads" / "payment-vouchers" / str(batch_id) / str(payment_id)
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{uuid.uuid4().hex}{suffix}"
    target.write_bytes(content)
    mime_type = "application/pdf" if expected_pdf else (content_type if content_type.startswith("image/") else mimetypes.guess_type(target.name)[0] or "application/octet-stream")
    return target, target.relative_to(DATA_DIR), len(content), mime_type


def resolve_data_file(relative_path: str) -> Path:
    root = DATA_DIR.resolve()
    target = (DATA_DIR / relative_path).resolve()
    if target != root and root not in target.parents:
        raise HTTPException(status_code=400, detail="附件路径无效")
    return target


def delete_attachment_file_if_unused(conn, attachment: Dict[str, Any]) -> None:
    file_path = attachment.get("file_path")
    if not file_path:
        return
    delete_file_if_unreferenced(conn, file_path, attachment["id"])


def delete_payment_voucher_file_if_unused(conn, voucher: Dict[str, Any]) -> None:
    file_path = voucher.get("file_path")
    if file_path:
        delete_file_if_unreferenced(conn, file_path)


def delete_file_if_unreferenced(conn, file_path: str, excluding_attachment_id: Optional[int] = None) -> None:
    if excluding_attachment_id is None:
        remaining = conn.execute(
            "SELECT COUNT(*) AS count FROM attachment_links WHERE file_path = ?",
            (file_path,),
        ).fetchone()["count"]
    else:
        remaining = conn.execute(
            "SELECT COUNT(*) AS count FROM attachment_links WHERE file_path = ? AND id != ?",
            (file_path, excluding_attachment_id),
        ).fetchone()["count"]
    voucher_remaining = conn.execute(
        "SELECT COUNT(*) AS count FROM payment_vouchers WHERE file_path = ?",
        (file_path,),
    ).fetchone()["count"]
    if remaining or voucher_remaining:
        return
    path = resolve_data_file(file_path)
    if path.exists():
        path.unlink()


dist_dir = ROOT_DIR / "frontend" / "dist"
if dist_dir.exists():
    def frontend_response(path: Path) -> Response:
        root = dist_dir.resolve()
        target = path.resolve()
        if target != root and root not in target.parents:
            raise HTTPException(status_code=404, detail="文件不存在")
        if not target.is_file():
            raise HTTPException(status_code=404, detail="文件不存在")
        media_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        return Response(content=target.read_bytes(), media_type=media_type)

    @app.get("/assets/{asset_path:path}", include_in_schema=False)
    def frontend_asset(asset_path: str) -> Response:
        return frontend_response(dist_dir / "assets" / asset_path)

    @app.get("/{full_path:path}", include_in_schema=False)
    def frontend_app(full_path: str) -> Response:
        if full_path.startswith("api/"):
            raise HTTPException(status_code=404, detail="Not Found")
        candidate = dist_dir / full_path
        if full_path and candidate.is_file():
            return frontend_response(candidate)
        return frontend_response(dist_dir / "index.html")
