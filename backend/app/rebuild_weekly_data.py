from __future__ import annotations

import argparse
import shutil
from pathlib import Path
from typing import Any, Dict, Optional

from .attachment_io import save_embedded_image_attachments
from .db import DATA_DIR, connect, init_db, now_iso, row_to_dict, write_audit
from .excel_io import parse_batch_dates, parse_weekly_excel
from .main import import_excel_payment_details, insert_request, write_import_job
from .snapshots import create_batch_snapshot


BUSINESS_TABLES = [
    "audit_logs",
    "import_jobs",
    "payment_vouchers",
    "payment_records",
    "attachment_links",
    "batch_snapshots",
    "payment_requests",
    "request_batches",
]


def reset_business_tables(conn) -> None:
    for table in BUSINESS_TABLES:
        conn.execute(f"DELETE FROM {table}")
    placeholders = ", ".join("?" for _ in BUSINESS_TABLES)
    conn.execute(f"DELETE FROM sqlite_sequence WHERE name IN ({placeholders})", BUSINESS_TABLES)


def reset_uploads_dir() -> None:
    uploads_dir = DATA_DIR / "uploads"
    shutil.rmtree(uploads_dir, ignore_errors=True)
    uploads_dir.mkdir(parents=True, exist_ok=True)
    shutil.rmtree(DATA_DIR / "snapshots", ignore_errors=True)
    (DATA_DIR / "snapshots").mkdir(parents=True, exist_ok=True)


def save_source_copy(source_path: Path, source_bytes: bytes) -> Path:
    suffix = source_path.suffix or ".xlsx"
    target = DATA_DIR / "uploads" / f"{now_iso().replace(':', '-')}-{source_path.stem}{suffix}"
    target.write_bytes(source_bytes)
    return target


def default_actor_id(conn) -> Optional[int]:
    row = conn.execute(
        """
        SELECT id FROM users
        WHERE active = 1
        ORDER BY CASE WHEN role = 'admin' THEN 0 WHEN role = 'general_manager' THEN 1 ELSE 2 END, id
        LIMIT 1
        """
    ).fetchone()
    return int(row["id"]) if row else None


def rebuild_weekly_data(source: Path | str) -> Dict[str, Any]:
    source_path = Path(source).expanduser().resolve()
    if not source_path.exists():
        raise FileNotFoundError(f"Excel 文件不存在: {source_path}")
    source_bytes = source_path.read_bytes()
    rows, meta = parse_weekly_excel(source_path)
    payment_details = meta.pop("payment_details", [])
    payment_detail_sheet_present = bool(meta.get("payment_detail_sheet_present"))
    start_date, end_date, default_name = parse_batch_dates(source_path.name)
    init_db()
    reset_uploads_dir()
    with connect() as conn:
        reset_business_tables(conn)
        actor_id = default_actor_id(conn)
        saved_copy = save_source_copy(source_path, source_bytes)
        timestamp = now_iso()
        cursor = conn.execute(
            """
            INSERT INTO request_batches (name, start_date, end_date, status, source_file, created_by, created_at, updated_at)
            VALUES (?, ?, ?, 'draft', ?, ?, ?, ?)
            """,
            (default_name, start_date, end_date, source_path.name, actor_id, timestamp, timestamp),
        )
        batch_id = int(cursor.lastrowid)
        request_ids: list[int] = []
        imported_summaries: Dict[int, float] = {}
        saved_images = 0
        skipped_images = 0
        for row in rows:
            request_id = insert_request(
                conn,
                batch_id,
                row,
                actor_id,
                "admin",
                create_summary_payment=not payment_detail_sheet_present,
            )
            request_ids.append(request_id)
            imported_summaries[request_id] = round(float(row.get("paid_amount") or 0), 2)
            row_saved_images, row_skipped_images = save_embedded_image_attachments(conn, batch_id, request_id, row, actor_id)
            saved_images += row_saved_images
            skipped_images += row_skipped_images
        meta.setdefault("images", {})["saved"] = saved_images
        meta["images"]["save_skipped"] = skipped_images
        if payment_detail_sheet_present:
            meta["payment_details"] = import_excel_payment_details(
                conn,
                batch_id,
                payment_details,
                int(actor_id) if actor_id is not None else 1,
                imported_summaries,
            )
        meta["source_copy"] = str(saved_copy.relative_to(DATA_DIR))
        job_id = write_import_job(conn, "weekly-excel-rebuild", source_path.name, "imported", batch_id, rows, [], meta, actor_id)
        create_batch_snapshot(conn, batch_id, "baseline", actor_id, replace_existing=True)
        batch = row_to_dict(conn.execute("SELECT * FROM request_batches WHERE id = ?", (batch_id,)).fetchone())
        write_audit(
            conn,
            actor_id,
            "maintenance.rebuild_weekly_data",
            "batch",
            batch_id,
            batch_id,
            new_value={"filename": source_path.name, "rows": len(rows), "images": saved_images, "job_id": job_id},
        )
    return {
        "batch": batch,
        "batch_id": batch_id,
        "requests": len(request_ids),
        "attachments": saved_images,
        "skipped_attachments": skipped_images,
        "job_id": job_id,
        "meta": meta,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="清空业务数据并从周报 Excel 重建一个草稿批次")
    parser.add_argument("excel_path", help="周报 Excel 文件路径")
    args = parser.parse_args()
    result = rebuild_weekly_data(args.excel_path)
    batch = result["batch"]
    print(
        f"已重建批次 {batch['id']} {batch['name']}："
        f"{result['requests']} 条请款，{result['attachments']} 个图片附件，导入任务 {result['job_id']}"
    )


if __name__ == "__main__":
    main()
