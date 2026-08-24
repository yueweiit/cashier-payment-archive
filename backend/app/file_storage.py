from __future__ import annotations

import hashlib
import os
import sqlite3
import uuid
from pathlib import Path
from typing import Any, BinaryIO, Mapping, Optional

from .db import ATTACHMENT_STORAGE_DIR, DATA_DIR, now_iso


CHUNK_SIZE = 1024 * 1024


def _safe_child(root: Path, relative_path: str) -> Path:
    resolved_root = root.resolve()
    target = (root / relative_path).resolve()
    if target != resolved_root and resolved_root not in target.parents:
        raise ValueError("附件存储路径无效")
    return target


def _row_dict(row: sqlite3.Row | Mapping[str, Any]) -> dict[str, Any]:
    return dict(row)


def store_stream(
    conn: sqlite3.Connection,
    stream: BinaryIO,
    *,
    mime_type: Optional[str] = None,
    storage_root: Optional[Path] = None,
) -> dict[str, Any]:
    staged = write_stream(stream, mime_type=mime_type, storage_root=storage_root)
    return register_file_object(conn, staged)


def write_stream(
    stream: BinaryIO,
    *,
    mime_type: Optional[str] = None,
    storage_root: Optional[Path] = None,
) -> dict[str, Any]:
    """Write content to its final hash path without touching SQLite."""
    root = storage_root or ATTACHMENT_STORAGE_DIR
    temporary_dir = root / "tmp"
    temporary_dir.mkdir(parents=True, exist_ok=True)
    temporary = temporary_dir / f".{uuid.uuid4().hex}.part"
    digest = hashlib.sha256()
    size_bytes = 0
    try:
        with temporary.open("wb") as handle:
            while True:
                chunk = stream.read(CHUNK_SIZE)
                if not chunk:
                    break
                if not isinstance(chunk, (bytes, bytearray)):
                    raise TypeError("附件流必须返回字节")
                handle.write(chunk)
                digest.update(chunk)
                size_bytes += len(chunk)
            handle.flush()
            os.fsync(handle.fileno())

        sha256 = digest.hexdigest()
        storage_path = f"attachments/sha256/{sha256[:2]}/{sha256}"
        target = _safe_child(root, storage_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            if target.stat().st_size != size_bytes:
                raise RuntimeError("相同哈希的附件大小不一致")
            temporary.unlink(missing_ok=True)
        else:
            os.replace(temporary, target)

        return {
            "sha256": sha256,
            "size_bytes": size_bytes,
            "mime_type": mime_type,
            "storage_backend": "local",
            "storage_path": storage_path,
            "status": "ready",
        }
    finally:
        temporary.unlink(missing_ok=True)


def register_file_object(
    conn: sqlite3.Connection,
    staged: Mapping[str, Any],
) -> dict[str, Any]:
    """Register an already-written hash object inside the caller's transaction."""
    timestamp = now_iso()
    conn.execute(
        """
        INSERT OR IGNORE INTO file_objects (
            sha256, size_bytes, mime_type, storage_backend, storage_path,
            status, created_at, verified_at
        ) VALUES (?, ?, ?, ?, ?, 'ready', ?, ?)
        """,
        (
            staged["sha256"],
            int(staged["size_bytes"]),
            staged.get("mime_type"),
            staged.get("storage_backend") or "local",
            staged["storage_path"],
            timestamp,
            timestamp,
        ),
    )
    row = conn.execute(
        "SELECT * FROM file_objects WHERE sha256 = ?",
        (staged["sha256"],),
    ).fetchone()
    if row is None:
        raise RuntimeError("附件文件对象写入失败")
    if int(row["size_bytes"]) != int(staged["size_bytes"]):
        raise RuntimeError("附件文件对象大小校验失败")
    return _row_dict(row)


def store_path(
    conn: sqlite3.Connection,
    source_path: Path,
    *,
    mime_type: Optional[str] = None,
    storage_root: Optional[Path] = None,
) -> dict[str, Any]:
    with source_path.open("rb") as stream:
        return store_stream(conn, stream, mime_type=mime_type, storage_root=storage_root)


def resolve_file_object(
    row: sqlite3.Row | Mapping[str, Any],
    *,
    storage_root: Optional[Path] = None,
) -> Optional[Path]:
    data = _row_dict(row)
    if data.get("status") and data.get("status") != "ready":
        return None
    storage_path = str(data.get("storage_path") or "").strip()
    if not storage_path:
        return None
    target = _safe_child(storage_root or ATTACHMENT_STORAGE_DIR, storage_path)
    return target if target.is_file() else None


def resolve_attachment_path(
    row: sqlite3.Row | Mapping[str, Any],
    conn: Optional[sqlite3.Connection] = None,
) -> tuple[Optional[Path], bool]:
    data = _row_dict(row)
    file_object_id = data.get("file_object_id")
    if file_object_id:
        file_object: Optional[sqlite3.Row | Mapping[str, Any]] = None
        if data.get("storage_path"):
            file_object = data
        elif conn is not None:
            file_object = conn.execute(
                "SELECT * FROM file_objects WHERE id = ?",
                (file_object_id,),
            ).fetchone()
        if file_object is not None:
            target = resolve_file_object(file_object)
            if target is not None:
                return target, False

    legacy_path = str(data.get("file_path") or "").strip()
    if not legacy_path:
        return None, False
    target = _safe_child(DATA_DIR, legacy_path)
    return (target if target.is_file() else None), True


def delete_physical_file_if_unreferenced(
    conn: sqlite3.Connection,
    file_object_id: int,
) -> bool:
    reference_count = conn.execute(
        """
        SELECT
            (SELECT COUNT(*) FROM attachment_links WHERE file_object_id = ?)
          + (SELECT COUNT(*) FROM payment_vouchers WHERE file_object_id = ?)
          + (SELECT COUNT(*) FROM mexico_approval_attachments WHERE file_object_id = ?)
        """,
        (file_object_id, file_object_id, file_object_id),
    ).fetchone()[0]
    if int(reference_count or 0) > 0:
        return False
    row = conn.execute("SELECT * FROM file_objects WHERE id = ?", (file_object_id,)).fetchone()
    if row is None:
        return False
    target = resolve_file_object(row)
    if target is not None:
        target.unlink()
    conn.execute("DELETE FROM file_objects WHERE id = ?", (file_object_id,))
    return True
