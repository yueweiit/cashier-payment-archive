from __future__ import annotations

import json
import inspect
import sqlite3
import uuid
from contextlib import AbstractContextManager
from contextvars import ContextVar, Token
from datetime import datetime, timedelta
from functools import wraps
from typing import Any, Dict, Optional

from fastapi import HTTPException

from .db import connect, now_iso, row_to_dict


DEFAULT_LEASE_SECONDS = 30 * 60
_CURRENT_OPERATION_ID: ContextVar[Optional[str]] = ContextVar("batch_operation_id", default=None)


def _lease_expiry(seconds: int = DEFAULT_LEASE_SECONDS) -> str:
    return (datetime.now() + timedelta(seconds=seconds)).isoformat(timespec="microseconds")


def _operation_conflict(row: sqlite3.Row) -> HTTPException:
    return HTTPException(
        status_code=409,
        detail={
            "code": "BATCH_OPERATION_IN_PROGRESS",
            "operation_id": row["id"],
            "operation_type": row["operation_type"],
            "batch_id": int(row["batch_id"]),
            "lease_expires_at": row["lease_expires_at"],
            "message": "当前批次正在执行其他操作，请稍后再试",
        },
    )


def _operation_payload(row: sqlite3.Row) -> Dict[str, Any]:
    payload = row_to_dict(row)
    for source_key, target_key in (
        ("result_json", "result"),
        ("partial_result_json", "partial_result"),
        ("timings_json", "timings"),
    ):
        try:
            payload[target_key] = json.loads(payload.get(source_key) or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            payload[target_key] = {}
    payload["blocks_writes"] = bool(payload.get("blocks_writes", 1))
    return payload


def acquire_batch_operation(
    batch_id: int,
    operation_type: str,
    actor_id: Optional[int],
    *,
    import_job_id: Optional[int] = None,
    lease_seconds: int = DEFAULT_LEASE_SECONDS,
    blocks_writes: bool = True,
) -> Dict[str, Any]:
    operation_id = uuid.uuid4().hex
    timestamp = now_iso()
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        batch = conn.execute("SELECT id FROM request_batches WHERE id = ?", (batch_id,)).fetchone()
        if not batch:
            raise HTTPException(status_code=404, detail="批次不存在")
        conn.execute(
            """
            UPDATE batch_operations
            SET status = 'interrupted', finished_at = ?,
                failure_reason = COALESCE(failure_reason, '操作租约过期，已由后续操作接管')
            WHERE batch_id = ? AND status = 'running' AND lease_expires_at <= ?
            """,
            (timestamp, batch_id, timestamp),
        )
        active = conn.execute(
            """
            SELECT * FROM batch_operations
            WHERE batch_id = ? AND status = 'running'
            ORDER BY started_at DESC LIMIT 1
            """,
            (batch_id,),
        ).fetchone()
        if active:
            raise _operation_conflict(active)
        try:
            conn.execute(
                """
                INSERT INTO batch_operations (
                    id, batch_id, operation_type, actor_id, status,
                    lease_expires_at, started_at, heartbeat_at, import_job_id,
                    blocks_writes, stage, progress_message
                ) VALUES (?, ?, ?, ?, 'running', ?, ?, ?, ?, ?, 'starting', '任务已创建')
                """,
                (
                    operation_id,
                    batch_id,
                    str(operation_type or "operation").strip() or "operation",
                    actor_id,
                    _lease_expiry(lease_seconds),
                    timestamp,
                    timestamp,
                    import_job_id,
                    1 if blocks_writes else 0,
                ),
            )
        except sqlite3.IntegrityError:
            active = conn.execute(
                "SELECT * FROM batch_operations WHERE batch_id = ? AND status = 'running'",
                (batch_id,),
            ).fetchone()
            if active:
                raise _operation_conflict(active)
            raise
        row = conn.execute("SELECT * FROM batch_operations WHERE id = ?", (operation_id,)).fetchone()
    return _operation_payload(row)


def acquire_or_reuse_batch_operation(
    batch_id: int,
    operation_type: str,
    actor_id: Optional[int],
    *,
    import_job_id: Optional[int] = None,
    lease_seconds: int = DEFAULT_LEASE_SECONDS,
    blocks_writes: bool = True,
) -> tuple[Dict[str, Any], bool]:
    """Create an operation, or reuse the active operation of the same type."""

    operation_id = uuid.uuid4().hex
    timestamp = now_iso()
    normalized_type = str(operation_type or "operation").strip() or "operation"
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        batch = conn.execute("SELECT id FROM request_batches WHERE id = ?", (batch_id,)).fetchone()
        if not batch:
            raise HTTPException(status_code=404, detail="批次不存在")
        conn.execute(
            """
            UPDATE batch_operations
            SET status = 'interrupted', finished_at = ?, stage = 'interrupted',
                failure_reason = COALESCE(failure_reason, '操作租约过期，已由后续操作接管')
            WHERE batch_id = ? AND status = 'running' AND lease_expires_at <= ?
            """,
            (timestamp, batch_id, timestamp),
        )
        active = conn.execute(
            """
            SELECT * FROM batch_operations
            WHERE batch_id = ? AND status = 'running'
            ORDER BY started_at DESC LIMIT 1
            """,
            (batch_id,),
        ).fetchone()
        if active:
            if str(active["operation_type"]) == normalized_type:
                return _operation_payload(active), True
            raise _operation_conflict(active)
        conn.execute(
            """
            INSERT INTO batch_operations (
                id, batch_id, operation_type, actor_id, status,
                lease_expires_at, started_at, heartbeat_at, import_job_id,
                blocks_writes, stage, progress_message
            ) VALUES (?, ?, ?, ?, 'running', ?, ?, ?, ?, ?, 'starting', '任务已创建')
            """,
            (
                operation_id,
                batch_id,
                normalized_type,
                actor_id,
                _lease_expiry(lease_seconds),
                timestamp,
                timestamp,
                import_job_id,
                1 if blocks_writes else 0,
            ),
        )
        row = conn.execute("SELECT * FROM batch_operations WHERE id = ?", (operation_id,)).fetchone()
    return _operation_payload(row), False


def get_batch_operation(operation_id: str) -> Dict[str, Any]:
    with connect() as conn:
        row = conn.execute("SELECT * FROM batch_operations WHERE id = ?", (operation_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="批次操作不存在")
    return _operation_payload(row)


def update_batch_operation_progress(
    operation_id: str,
    *,
    stage: str,
    progress_current: int = 0,
    progress_total: int = 0,
    progress_message: Optional[str] = None,
    timings: Optional[Dict[str, Any]] = None,
    partial_result: Optional[Dict[str, Any]] = None,
    lease_seconds: int = DEFAULT_LEASE_SECONDS,
) -> Dict[str, Any]:
    with connect() as conn:
        timestamp = now_iso()
        cursor = conn.execute(
            """
            UPDATE batch_operations
            SET stage = ?, progress_current = ?, progress_total = ?, progress_message = ?,
                timings_json = COALESCE(?, timings_json),
                partial_result_json = COALESCE(?, partial_result_json),
                heartbeat_at = ?, lease_expires_at = ?
            WHERE id = ? AND status = 'running'
            """,
            (
                str(stage or "running"),
                max(0, int(progress_current or 0)),
                max(0, int(progress_total or 0)),
                progress_message,
                json.dumps(timings, ensure_ascii=False, default=str) if timings is not None else None,
                json.dumps(partial_result, ensure_ascii=False, default=str) if partial_result is not None else None,
                timestamp,
                _lease_expiry(lease_seconds),
                operation_id,
            ),
        )
        if cursor.rowcount != 1:
            raise HTTPException(status_code=409, detail="批次操作已结束或租约已失效")
        row = conn.execute("SELECT * FROM batch_operations WHERE id = ?", (operation_id,)).fetchone()
    return _operation_payload(row)


def heartbeat_batch_operation(
    operation_id: str,
    *,
    lease_seconds: int = DEFAULT_LEASE_SECONDS,
) -> None:
    with connect() as conn:
        timestamp = now_iso()
        cursor = conn.execute(
            """
            UPDATE batch_operations
            SET heartbeat_at = ?, lease_expires_at = ?
            WHERE id = ? AND status = 'running'
            """,
            (timestamp, _lease_expiry(lease_seconds), operation_id),
        )
        if cursor.rowcount != 1:
            raise HTTPException(status_code=409, detail="批次操作已结束或租约已失效")


def complete_batch_operation(operation_id: str, result: Optional[Dict[str, Any]] = None) -> None:
    result = result or {}
    import_job_id = result.get("job_id")
    with connect() as conn:
        timestamp = now_iso()
        conn.execute(
            """
            UPDATE batch_operations
            SET status = 'succeeded', stage = 'completed', finished_at = ?, heartbeat_at = ?,
                result_json = ?, failure_reason = NULL,
                import_job_id = COALESCE(import_job_id, ?)
            WHERE id = ? AND status = 'running'
            """,
            (
                timestamp,
                timestamp,
                json.dumps(result, ensure_ascii=False, default=str),
                int(import_job_id) if import_job_id is not None else None,
                operation_id,
            ),
        )


def fail_batch_operation(operation_id: str, error: BaseException) -> None:
    reason = str(error or "操作失败").strip()[:2000] or "操作失败"
    with connect() as conn:
        timestamp = now_iso()
        conn.execute(
            """
            UPDATE batch_operations
            SET status = 'failed', stage = 'failed', finished_at = ?, heartbeat_at = ?, failure_reason = ?
            WHERE id = ? AND status = 'running'
            """,
            (timestamp, timestamp, reason, operation_id),
        )


def ensure_batch_operation_available(
    conn: sqlite3.Connection,
    batch_id: int,
    *,
    operation_id: Optional[str] = None,
) -> None:
    operation_id = operation_id or _CURRENT_OPERATION_ID.get()
    timestamp = now_iso()
    conn.execute(
        """
        UPDATE batch_operations
        SET status = 'interrupted', finished_at = ?,
            failure_reason = COALESCE(failure_reason, '操作租约过期')
        WHERE batch_id = ? AND status = 'running' AND lease_expires_at <= ?
        """,
        (timestamp, batch_id, timestamp),
    )
    row = conn.execute(
        """
        SELECT * FROM batch_operations
        WHERE batch_id = ? AND status = 'running' AND blocks_writes = 1
          AND (? IS NULL OR id != ?)
        ORDER BY started_at DESC LIMIT 1
        """,
        (batch_id, operation_id, operation_id),
    ).fetchone()
    if row:
        raise _operation_conflict(row)


class BatchOperationLease(AbstractContextManager["BatchOperationLease"]):
    def __init__(
        self,
        batch_id: int,
        operation_type: str,
        actor_id: Optional[int],
        *,
        operation_id: Optional[str] = None,
        manage_lifecycle: bool = True,
    ) -> None:
        self.operation = (
            get_batch_operation(operation_id)
            if operation_id
            else acquire_batch_operation(batch_id, operation_type, actor_id)
        )
        self.manage_lifecycle = manage_lifecycle
        self.result: Dict[str, Any] = {}
        self._context_token: Optional[Token] = None

    @property
    def id(self) -> str:
        return str(self.operation["id"])

    def heartbeat(self) -> None:
        heartbeat_batch_operation(self.id)

    def set_result(self, result: Dict[str, Any]) -> None:
        self.result = result

    def __enter__(self) -> "BatchOperationLease":
        self._context_token = _CURRENT_OPERATION_ID.set(self.id)
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> bool:
        try:
            if self.manage_lifecycle:
                if exc_value is not None:
                    fail_batch_operation(self.id, exc_value)
                else:
                    complete_batch_operation(self.id, self.result)
        finally:
            if self._context_token is not None:
                _CURRENT_OPERATION_ID.reset(self._context_token)
                self._context_token = None
        return False


def leased_batch_operation(operation_type: str, batch_id_resolver):
    """Run an endpoint under a database-backed batch lease.

    ``batch_id_resolver`` receives the endpoint's bound argument mapping. Returning
    ``None`` skips leasing, which is useful for imports that create a new batch.
    ``functools.wraps`` keeps the original FastAPI dependency signature intact.
    """

    def decorator(func):
        signature = inspect.signature(func)

        def resolve(args, kwargs):
            bound = signature.bind_partial(*args, **kwargs)
            arguments = dict(bound.arguments)
            batch_id = batch_id_resolver(arguments)
            user = arguments.get("user") or {}
            actor_id = user.get("id") if isinstance(user, dict) else None
            return int(batch_id) if batch_id is not None else None, actor_id

        if inspect.iscoroutinefunction(func):
            @wraps(func)
            async def async_wrapper(*args, **kwargs):
                batch_id, actor_id = resolve(args, kwargs)
                if batch_id is None:
                    return await func(*args, **kwargs)
                with BatchOperationLease(batch_id, operation_type, actor_id) as lease:
                    result = await func(*args, **kwargs)
                    if isinstance(result, dict):
                        lease.set_result(result)
                    return result

            return async_wrapper

        @wraps(func)
        def sync_wrapper(*args, **kwargs):
            batch_id, actor_id = resolve(args, kwargs)
            if batch_id is None:
                return func(*args, **kwargs)
            with BatchOperationLease(batch_id, operation_type, actor_id) as lease:
                result = func(*args, **kwargs)
                if isinstance(result, dict):
                    lease.set_result(result)
                return result

        return sync_wrapper

    return decorator
