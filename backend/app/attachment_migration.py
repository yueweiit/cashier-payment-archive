from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterable, Optional

from . import file_storage
from .db import ATTACHMENT_STORAGE_DIR, DATA_DIR, backup_database, connect, init_db, now_iso


RELATIONS = (
    ("attachment_links", "attachment"),
    ("payment_vouchers", "payment_voucher"),
)


def _safe_legacy_path(data_root: Path, relative_path: str) -> Path:
    root = data_root.resolve()
    path = Path(str(relative_path))
    target = path.resolve() if path.is_absolute() else (data_root / path).resolve()
    if target != root and root not in target.parents:
        raise ValueError(f"旧附件路径越界: {relative_path}")
    return target


def _legacy_rows(conn: sqlite3.Connection) -> Iterable[dict[str, Any]]:
    for table, relation_type in RELATIONS:
        rows = conn.execute(
            f"""
            SELECT id, file_path, original_filename, mime_type
            FROM {table}
            WHERE file_object_id IS NULL
              AND TRIM(COALESCE(file_path, '')) <> ''
            ORDER BY id
            """
        ).fetchall()
        for row in rows:
            yield {"table": table, "relation_type": relation_type, **dict(row)}


def inventory_legacy_files(
    conn: sqlite3.Connection,
    *,
    data_root: Path = DATA_DIR,
) -> dict[str, Any]:
    entries: list[dict[str, Any]] = []
    missing = 0
    total_bytes = 0
    for row in _legacy_rows(conn):
        entry = dict(row)
        try:
            source = _safe_legacy_path(data_root, str(row["file_path"]))
            exists = source.is_file()
        except ValueError as exc:
            source = None
            exists = False
            entry["error"] = str(exc)
        entry["exists"] = exists
        if exists and source is not None:
            entry["size_bytes"] = source.stat().st_size
            total_bytes += int(entry["size_bytes"])
        else:
            entry["size_bytes"] = None
            missing += 1
        entries.append(entry)
    return {
        "created_at": now_iso(),
        "data_root": str(data_root.resolve()),
        "legacy_references": len(entries),
        "missing_references": missing,
        "legacy_bytes": total_bytes,
        "entries": entries,
    }


def migrate_legacy_files(
    conn: sqlite3.Connection,
    *,
    data_root: Path = DATA_DIR,
    storage_root: Path = ATTACHMENT_STORAGE_DIR,
    commit_every: int = 100,
) -> dict[str, Any]:
    inventory = inventory_legacy_files(conn, data_root=data_root)
    migrated = 0
    missing = 0
    created_objects = 0
    bytes_copied = 0
    errors: list[dict[str, Any]] = []

    for index, entry in enumerate(inventory["entries"], start=1):
        if not entry["exists"]:
            missing += 1
            errors.append(
                {
                    "table": entry["table"],
                    "id": entry["id"],
                    "file_path": entry["file_path"],
                    "message": entry.get("error") or "旧附件文件不存在",
                }
            )
            continue
        try:
            source = _safe_legacy_path(data_root, str(entry["file_path"]))
            mime_type = entry.get("mime_type") or mimetypes.guess_type(
                str(entry.get("original_filename") or source.name)
            )[0]
            with source.open("rb") as stream:
                staged = file_storage.write_stream(
                    stream,
                    mime_type=mime_type,
                    storage_root=storage_root,
                )
            savepoint = f"attachment_migration_{index}"
            conn.execute(f"SAVEPOINT {savepoint}")
            existed = conn.execute(
                "SELECT id FROM file_objects WHERE sha256 = ?",
                (staged["sha256"],),
            ).fetchone()
            file_object = file_storage.register_file_object(conn, staged)
            cursor = conn.execute(
                f"""
                UPDATE {entry['table']}
                SET file_object_id = ?, file_path = ?
                WHERE id = ? AND file_object_id IS NULL
                """,
                (file_object["id"], staged["storage_path"], entry["id"]),
            )
            if cursor.rowcount:
                migrated += 1
                if existed is None:
                    created_objects += 1
                    bytes_copied += int(staged["size_bytes"])
            conn.execute(f"RELEASE SAVEPOINT {savepoint}")
            if commit_every > 0 and index % commit_every == 0:
                conn.commit()
        except Exception as exc:
            try:
                conn.execute(f"ROLLBACK TO SAVEPOINT attachment_migration_{index}")
                conn.execute(f"RELEASE SAVEPOINT attachment_migration_{index}")
            except sqlite3.Error:
                pass
            errors.append(
                {
                    "table": entry["table"],
                    "id": entry["id"],
                    "file_path": entry["file_path"],
                    "message": str(exc),
                }
            )
    conn.commit()
    return {
        **{key: value for key, value in inventory.items() if key != "entries"},
        "storage_root": str(storage_root.resolve()),
        "migrated_references": migrated,
        "missing_references": missing,
        "file_objects_created": created_objects,
        "bytes_copied": bytes_copied,
        "errors": errors,
        # The originals intentionally remain on the system disk for rollback.
        "legacy_files_deleted": 0,
        "entries": inventory["entries"],
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_storage(
    conn: sqlite3.Connection,
    *,
    storage_root: Path = ATTACHMENT_STORAGE_DIR,
    check_hashes: bool = True,
) -> dict[str, Any]:
    ready = conn.execute("SELECT * FROM file_objects WHERE status = 'ready' ORDER BY id").fetchall()
    missing = 0
    size_mismatches = 0
    hash_mismatches = 0
    errors: list[dict[str, Any]] = []
    for row in ready:
        path = file_storage.resolve_file_object(row, storage_root=storage_root)
        if path is None:
            missing += 1
            errors.append({"file_object_id": row["id"], "message": "文件不存在"})
            continue
        if path.stat().st_size != int(row["size_bytes"]):
            size_mismatches += 1
            errors.append({"file_object_id": row["id"], "message": "文件大小不一致"})
        if check_hashes and _sha256(path) != str(row["sha256"]):
            hash_mismatches += 1
            errors.append({"file_object_id": row["id"], "message": "SHA-256 不一致"})

    dangling_refs = 0
    for table, _ in RELATIONS:
        dangling_refs += int(
            conn.execute(
                f"""
                SELECT COUNT(*) FROM {table}
                WHERE file_object_id IS NOT NULL
                  AND NOT EXISTS (SELECT 1 FROM file_objects WHERE file_objects.id = {table}.file_object_id)
                """
            ).fetchone()[0]
        )
    legacy_refs = sum(1 for _ in _legacy_rows(conn))
    return {
        "verified_at": now_iso(),
        "storage_root": str(storage_root.resolve()),
        "ready_file_objects": len(ready),
        "missing_file_objects": missing,
        "size_mismatches": size_mismatches,
        "hash_mismatches": hash_mismatches,
        "dangling_references": dangling_refs,
        "remaining_legacy_references": legacy_refs,
        "ok": (
            missing == 0
            and size_mismatches == 0
            and hash_mismatches == 0
            and dangling_refs == 0
            and legacy_refs == 0
        ),
        "errors": errors,
    }


def cleanup_legacy_from_manifest(
    conn: sqlite3.Connection,
    manifest: dict[str, Any],
    *,
    data_root: Path = DATA_DIR,
    retention_days: int = 30,
    execute: bool = False,
) -> dict[str, Any]:
    cutoff = datetime.now() - timedelta(days=max(0, retention_days))
    candidates: list[str] = []
    skipped: list[dict[str, str]] = []
    for entry in manifest.get("entries") or []:
        relative_path = str(entry.get("file_path") or "").strip()
        if not relative_path:
            continue
        if any(
            conn.execute(
                f"SELECT 1 FROM {table} WHERE file_object_id IS NULL AND file_path = ? LIMIT 1",
                (relative_path,),
            ).fetchone()
            for table, _ in RELATIONS
        ):
            skipped.append({"file_path": relative_path, "reason": "仍被旧关系引用"})
            continue
        try:
            path = _safe_legacy_path(data_root, relative_path)
        except ValueError as exc:
            skipped.append({"file_path": relative_path, "reason": str(exc)})
            continue
        if not path.is_file():
            continue
        if datetime.fromtimestamp(path.stat().st_mtime) > cutoff:
            skipped.append({"file_path": relative_path, "reason": "未达到保留天数"})
            continue
        candidates.append(relative_path)
        if execute:
            path.unlink()
    return {
        "dry_run": not execute,
        "retention_days": retention_days,
        "candidate_count": len(candidates),
        "deleted_count": len(candidates) if execute else 0,
        "candidates": candidates,
        "skipped": skipped,
    }


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="附件哈希存储迁移与校验工具")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in ("inventory", "migrate", "verify"):
        command = subparsers.add_parser(name)
        command.add_argument("--output", type=Path)
    backup = subparsers.add_parser("backup")
    backup.add_argument("destination", type=Path)
    cleanup = subparsers.add_parser("cleanup")
    cleanup.add_argument("--manifest", type=Path, required=True)
    cleanup.add_argument("--retention-days", type=int, default=30)
    cleanup.add_argument("--execute", action="store_true")
    cleanup.add_argument("--output", type=Path)
    args = parser.parse_args(argv)

    if args.command == "backup":
        payload = backup_database(args.destination)
    else:
        init_db()
        with connect() as conn:
            if args.command == "inventory":
                payload = inventory_legacy_files(conn)
            elif args.command == "migrate":
                payload = migrate_legacy_files(conn)
            elif args.command == "verify":
                payload = verify_storage(conn)
            else:
                manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
                payload = cleanup_legacy_from_manifest(
                    conn,
                    manifest,
                    retention_days=args.retention_days,
                    execute=args.execute,
                )
    output = getattr(args, "output", None)
    if output:
        _write_json(output, payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
