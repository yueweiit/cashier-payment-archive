from __future__ import annotations

import io
import re
from pathlib import Path
from sqlite3 import Connection
from typing import Any, Dict, Optional, Tuple

from .db import now_iso
from .file_storage import store_stream


IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}
MAX_IMAGE_BYTES = 12 * 1024 * 1024


def clean_filename(value: str) -> str:
    cleaned = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff._-]+", "_", value).strip("._")
    return cleaned or "excel_image"


def normalize_image_extension(image: Dict[str, Any]) -> str:
    extension = str(image.get("extension") or "").lower()
    if extension and not extension.startswith("."):
        extension = f".{extension}"
    if extension in IMAGE_EXTENSIONS:
        return extension
    filename_suffix = Path(str(image.get("filename") or "")).suffix.lower()
    if filename_suffix in IMAGE_EXTENSIONS:
        return filename_suffix
    return ".png"


def save_embedded_image_attachment(
    conn: Connection,
    batch_id: int,
    request_id: int,
    image: Dict[str, Any],
    user_id: Optional[int],
) -> Optional[int]:
    data = image.get("data")
    if not isinstance(data, bytes) or not data or len(data) > MAX_IMAGE_BYTES:
        return None
    extension = normalize_image_extension(image)
    original_filename = clean_filename(str(image.get("filename") or f"excel_image{extension}"))
    if Path(original_filename).suffix.lower() not in IMAGE_EXTENSIONS:
        original_filename = f"{original_filename}{extension}"
    mime_type = image.get("mime_type") or "image/png"
    file_object = store_stream(conn, io.BytesIO(data), mime_type=mime_type)
    cursor = conn.execute(
        """
        INSERT INTO attachment_links (
            request_id, label, url_path, attachment_type, file_path,
            original_filename, mime_type, file_size, file_object_id, created_by, created_at
        )
        VALUES (?, ?, ?, 'image', ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            request_id,
            image.get("label"),
            file_object["storage_path"],
            file_object["storage_path"],
            original_filename,
            mime_type,
            len(data),
            file_object["id"],
            user_id,
            now_iso(),
        ),
    )
    return int(cursor.lastrowid)


def save_embedded_image_attachments(
    conn: Connection,
    batch_id: int,
    request_id: int,
    row: Dict[str, Any],
    user_id: Optional[int],
) -> Tuple[int, int]:
    saved = 0
    skipped = 0
    for image in row.get("_embedded_images", []) or []:
        if save_embedded_image_attachment(conn, batch_id, request_id, image, user_id):
            saved += 1
        else:
            skipped += 1
    return saved, skipped


def save_embedded_payment_vouchers(
    conn: Connection,
    batch_id: int,
    payment_id: int,
    row: Dict[str, Any],
    user_id: Optional[int],
) -> Tuple[int, int]:
    saved = 0
    skipped = 0
    for image in row.get("_embedded_images", []) or []:
        data = image.get("data")
        if not isinstance(data, bytes) or not data or len(data) > MAX_IMAGE_BYTES:
            skipped += 1
            continue
        extension = normalize_image_extension(image)
        original_filename = clean_filename(str(image.get("filename") or f"excel_payment_voucher{extension}"))
        if Path(original_filename).suffix.lower() not in IMAGE_EXTENSIONS:
            original_filename = f"{original_filename}{extension}"
        mime_type = image.get("mime_type") or "image/png"
        file_object = store_stream(conn, io.BytesIO(data), mime_type=mime_type)
        conn.execute(
            """
            INSERT INTO payment_vouchers (
                payment_id, label, file_path, original_filename, mime_type,
                file_size, file_object_id, created_by, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                payment_id,
                image.get("label"),
                file_object["storage_path"],
                original_filename,
                mime_type,
                len(data),
                file_object["id"],
                user_id,
                now_iso(),
            ),
        )
        saved += 1
    return saved, skipped
