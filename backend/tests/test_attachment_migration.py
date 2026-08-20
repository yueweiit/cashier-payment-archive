from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

from backend.app.attachment_migration import inventory_legacy_files, migrate_legacy_files, verify_storage
from backend.app.db import ensure_file_storage_tables


def make_connection(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE attachment_links (
            id INTEGER PRIMARY KEY,
            request_id INTEGER NOT NULL,
            file_path TEXT,
            url_path TEXT,
            original_filename TEXT,
            mime_type TEXT,
            source_system TEXT,
            source_attachment_id TEXT
        );
        CREATE TABLE payment_vouchers (
            id INTEGER PRIMARY KEY,
            payment_id INTEGER NOT NULL,
            file_path TEXT,
            original_filename TEXT,
            mime_type TEXT
        );
        """
    )
    ensure_file_storage_tables(conn)
    return conn


def test_migration_deduplicates_physical_content_and_keeps_legacy_files(tmp_path: Path):
    data_root = tmp_path / "data"
    storage_root = tmp_path / "storage"
    (data_root / "uploads").mkdir(parents=True)
    content = b"same attachment content"
    (data_root / "uploads" / "one.pdf").write_bytes(content)
    (data_root / "uploads" / "two.pdf").write_bytes(content)

    conn = make_connection(tmp_path / "app.db")
    conn.execute(
        """INSERT INTO attachment_links
           (id, request_id, file_path, url_path, original_filename, mime_type)
           VALUES (1, 10, 'uploads/one.pdf', 'uploads/one.pdf', 'one.pdf', 'application/pdf')"""
    )
    conn.execute(
        """INSERT INTO payment_vouchers
           (id, payment_id, file_path, original_filename, mime_type)
           VALUES (2, 20, 'uploads/two.pdf', 'two.pdf', 'application/pdf')"""
    )
    conn.commit()

    inventory = inventory_legacy_files(conn, data_root=data_root)
    assert inventory["legacy_references"] == 2
    assert inventory["missing_references"] == 0

    result = migrate_legacy_files(conn, data_root=data_root, storage_root=storage_root)
    assert result["migrated_references"] == 2
    assert result["file_objects_created"] == 1
    assert result["bytes_copied"] == len(content)

    attachment = conn.execute("SELECT * FROM attachment_links WHERE id = 1").fetchone()
    voucher = conn.execute("SELECT * FROM payment_vouchers WHERE id = 2").fetchone()
    assert attachment["file_object_id"] == voucher["file_object_id"]
    digest = hashlib.sha256(content).hexdigest()
    blob = storage_root / "attachments" / "sha256" / digest[:2] / digest
    assert blob.read_bytes() == content
    assert (data_root / "uploads" / "one.pdf").exists()
    assert (data_root / "uploads" / "two.pdf").exists()

    verified = verify_storage(conn, storage_root=storage_root, check_hashes=True)
    assert verified["ready_file_objects"] == 1
    assert verified["missing_file_objects"] == 0
    assert verified["hash_mismatches"] == 0
    conn.close()


def test_migration_reports_missing_legacy_file_without_changing_relation(tmp_path: Path):
    data_root = tmp_path / "data"
    storage_root = tmp_path / "storage"
    data_root.mkdir()
    conn = make_connection(tmp_path / "app.db")
    conn.execute(
        """INSERT INTO attachment_links
           (id, request_id, file_path, original_filename, mime_type)
           VALUES (1, 10, 'uploads/missing.pdf', 'missing.pdf', 'application/pdf')"""
    )
    conn.commit()

    result = migrate_legacy_files(conn, data_root=data_root, storage_root=storage_root)
    assert result["migrated_references"] == 0
    assert result["missing_references"] == 1
    row = conn.execute("SELECT * FROM attachment_links WHERE id = 1").fetchone()
    assert row["file_object_id"] is None
    assert row["file_path"] == "uploads/missing.pdf"
    verified = verify_storage(conn, storage_root=storage_root, check_hashes=True)
    assert verified["remaining_legacy_references"] == 1
    assert verified["ok"] is False
    conn.close()
