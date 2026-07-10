from __future__ import annotations

import json
import mimetypes
import shutil
import uuid
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import quote

from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, Request, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .attachment_io import save_embedded_image_attachments
from .db import DATA_DIR, ROOT_DIR, connect, init_db, now_iso, row_to_dict, rows_to_dicts, write_audit
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
from .security import new_session_token, verify_password, hash_password


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


class BatchIn(BaseModel):
    name: str
    start_date: Optional[str] = None
    end_date: Optional[str] = None


class RolloverIn(BaseModel):
    name: str
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    copy_mode: str = "unfinished"


class RequestIn(BaseModel):
    dingding_id: Optional[str] = None
    payment_account: Optional[str] = None
    expense_type: Optional[str] = None
    summary: Optional[str] = None
    style_name: Optional[str] = None
    amount: Optional[float] = None
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


IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}
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
FINANCE_CONTROLLED_FIELDS = {
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
    "payment_account",
    "expense_type",
    "summary",
    "style_name",
    "amount",
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
    return comparable_value(left) == comparable_value(right)


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


@app.get("/api/batches")
def list_batches(user: Dict[str, Any] = Depends(current_user)) -> Dict[str, Any]:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT b.*, COUNT(p.id) AS request_count, COALESCE(SUM(p.amount), 0) AS total_amount
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
                source_file, created_by, created_at, updated_at
            ) VALUES (?, ?, ?, ?, 'draft', ?, ?, ?, ?)
            """,
            (
                source_batch_id,
                payload.name,
                payload.start_date,
                payload.end_date,
                f"rollover:{source['name']}",
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
            new_request_id = insert_request(conn, target_batch_id, source_data, user["id"], ROLE_GENERAL_MANAGER)
            copy_attachment_links(conn, copied_from_request_id, new_request_id, user["id"])
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
    return {"batch": row_to_dict(target), "copied_count": copied_count, "copy_mode": payload.copy_mode, "operation_id": operation_id}


@app.get("/api/batches/{batch_id}")
def get_batch(batch_id: int, user: Dict[str, Any] = Depends(current_user)) -> Dict[str, Any]:
    with connect() as conn:
        row = conn.execute(
            """
            SELECT b.*, COUNT(p.id) AS request_count, COALESCE(SUM(p.amount), 0) AS total_amount
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
            SELECT payment_account, invoice_status, project, COUNT(*) AS count, COALESCE(SUM(amount), 0) AS amount
            FROM payment_requests
            WHERE batch_id = ?
            GROUP BY payment_account, invoice_status, project
            """,
            (batch_id,),
        ).fetchall()
    return {"batch": row_to_dict(row), "stats": rows_to_dicts(stats)}


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


@app.delete("/api/batches/{batch_id}")
def delete_draft_batch(batch_id: int, user: Dict[str, Any] = Depends(require_roles(*ALL_ROLES))) -> Dict[str, str]:
    with connect() as conn:
        row = conn.execute("SELECT * FROM request_batches WHERE id = ?", (batch_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="批次不存在")
        if row["status"] != "draft":
            raise HTTPException(status_code=400, detail="已归档批次不能删除，请先恢复草稿后再删除")
        old_value = row_to_dict(row)
        file_paths = [
            item["file_path"]
            for item in conn.execute(
                """
                SELECT attachment_links.file_path FROM attachment_links
                JOIN payment_requests ON payment_requests.id = attachment_links.request_id
                WHERE payment_requests.batch_id = ? AND attachment_links.file_path IS NOT NULL
                """,
                (batch_id,),
            ).fetchall()
            if item["file_path"]
        ]
        write_audit(conn, user["id"], "batch.delete_draft", "batch", batch_id, batch_id=batch_id, old_value=old_value)
        conn.execute("DELETE FROM request_batches WHERE id = ?", (batch_id,))
        for file_path in set(file_paths):
            delete_file_if_unreferenced(conn, file_path)
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
            f"SELECT * FROM payment_requests WHERE {' AND '.join(conditions)} ORDER BY id DESC",
            params,
        ).fetchall()
        totals = conn.execute(
            f"SELECT COUNT(*) AS count, COALESCE(SUM(amount), 0) AS amount FROM payment_requests WHERE {' AND '.join(conditions)}",
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
        data = payload.dict()
        request_id = insert_request(conn, batch_id, data, user["id"], user["role"])
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
            request_id = insert_request(conn, batch_id, item, user["id"], user["role"])
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
            conn.execute("DELETE FROM payment_requests WHERE id = ?", (request_id,))
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
        conn.execute("DELETE FROM payment_requests WHERE id = ?", (request_id,))
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
    meta["source_copy"] = str(saved_path.relative_to(DATA_DIR))
    start_date, end_date, default_name = parse_batch_dates(file.filename or saved_path.name)
    with connect() as conn:
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
        duplicates = duplicate_candidates(conn, batch_id, rows)
        imported = []
        saved_images = 0
        skipped_images = 0
        for row in rows:
            request_id = insert_request(conn, batch_id, row, user["id"], user["role"])
            imported.append(request_id)
            row_saved_images, row_skipped_images = save_embedded_image_attachments(conn, batch_id, request_id, row, user["id"])
            saved_images += row_saved_images
            skipped_images += row_skipped_images
        meta.setdefault("images", {})["saved"] = saved_images
        meta["images"]["save_skipped"] = skipped_images
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
    meta["source_copy"] = str(saved_path.relative_to(DATA_DIR))
    start_date, end_date, default_name = parse_batch_dates(file.filename or saved_path.name)
    with connect() as conn:
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
    return {"status": "imported", "job_id": job_id, "batch_id": batch_id, "imported_rows": len(imported), "duplicate_rows": len(duplicates), "duplicates": duplicates[:100], "meta": meta}


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
              AND kind IN ('weekly-excel', 'dingtalk')
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
        if not request_ids:
            raise HTTPException(status_code=404, detail="未找到这次导入生成的请款记录")
        placeholders = ", ".join("?" for _ in request_ids)
        attachment_rows = rows_to_dicts(
            conn.execute(f"SELECT * FROM attachment_links WHERE request_id IN ({placeholders})", request_ids).fetchall()
        )
        conn.execute(f"DELETE FROM attachment_links WHERE request_id IN ({placeholders})", request_ids)
        conn.execute(f"DELETE FROM payment_requests WHERE id IN ({placeholders})", request_ids)
        conn.execute("UPDATE import_jobs SET status = 'rolled_back', imported_rows = 0 WHERE id = ?", (job["id"],))
        conn.execute("UPDATE request_batches SET updated_at = ? WHERE id = ?", (now_iso(), batch_id))
        removed_files = 0
        for attachment in attachment_rows:
            file_path = attachment.get("file_path")
            if not file_path:
                continue
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
            new_value={"deleted_requests": len(request_ids), "deleted_attachments": len(attachment_rows), "removed_files": removed_files},
            reason="撤回最近导入",
        )
    return {
        "status": "rolled_back",
        "job_id": job["id"],
        "batch_id": batch_id,
        "deleted_requests": len(request_ids),
        "deleted_attachments": len(attachment_rows),
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
        content = export_workbook(row_to_dict(batch), records, attachments)
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


def require_request(conn, batch_id: int, request_id: int):
    row = conn.execute("SELECT * FROM payment_requests WHERE id = ? AND batch_id = ?", (request_id, batch_id)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="请款记录不存在")
    return row


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


def insert_request(conn, batch_id: int, data: Dict[str, Any], user_id: int, user_role: str = ROLE_GENERAL_MANAGER) -> int:
    data = enforce_request_field_permissions(data, user_role, creating=True)
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
    conn.execute("UPDATE request_batches SET updated_at = ? WHERE id = ?", (timestamp, batch_id))
    return cursor.lastrowid


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
    payload = enforce_request_field_permissions(payload, user_role, existing, creating=False)
    if not payload:
        return False
    merged = {**existing, **payload}
    normalize_request_business_fields(merged)
    for key in (
        "finance_review",
        "general_manager_approval",
        "general_manager_approval_date",
        "general_manager_opinion",
        "actual_payment_date",
        "remark",
        "payment_status",
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
    row = row_to_dict(conn.execute("SELECT * FROM payment_requests WHERE id = ?", (request_id,)).fetchone())
    conn.execute("UPDATE payment_requests SET content_hash = ? WHERE id = ?", (content_hash(row), request_id))
    return True


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
    if remaining:
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
