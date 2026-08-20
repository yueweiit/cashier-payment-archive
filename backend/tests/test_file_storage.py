from __future__ import annotations

import importlib
import io
import sqlite3
from pathlib import Path

import pytest


@pytest.fixture()
def isolated_db(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    import backend.app.db as db_module

    original_paths = (
        db_module.DATA_DIR,
        db_module.DB_PATH,
        db_module.ATTACHMENT_STORAGE_DIR,
    )
    monkeypatch.setattr(db_module, "DATA_DIR", tmp_path / "app-data")
    monkeypatch.setattr(db_module, "DB_PATH", tmp_path / "app-data" / "app.db")
    monkeypatch.setattr(db_module, "ATTACHMENT_STORAGE_DIR", tmp_path / "attachment-data")
    db_module.init_db()
    try:
        yield db_module
    finally:
        (
            db_module.DATA_DIR,
            db_module.DB_PATH,
            db_module.ATTACHMENT_STORAGE_DIR,
        ) = original_paths


def test_attachment_schema_supports_content_addressed_file_objects(isolated_db):
    with isolated_db.connect() as conn:
        file_object_columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(file_objects)").fetchall()
        }
        attachment_columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(attachment_links)").fetchall()
        }
        voucher_columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(payment_vouchers)").fetchall()
        }
        assert {
            "id",
            "sha256",
            "size_bytes",
            "mime_type",
            "storage_backend",
            "storage_path",
            "status",
            "created_at",
            "verified_at",
        } <= file_object_columns
        assert {"file_object_id", "source_instance_id"} <= attachment_columns
        assert "file_object_id" in voucher_columns

        indexes = {
            row["name"]: row for row in conn.execute("PRAGMA index_list(attachment_links)").fetchall()
        }
        assert indexes["idx_attachment_links_external_source"]["unique"] == 1
        index_columns = [
            row["name"]
            for row in conn.execute(
                "PRAGMA index_info(idx_attachment_links_external_source)"
            ).fetchall()
        ]
        assert index_columns == [
            "request_id",
            "source_system",
            "source_instance_id",
            "source_attachment_id",
        ]


def test_file_object_sha256_is_unique(isolated_db):
    timestamp = isolated_db.now_iso()
    with isolated_db.connect() as conn:
        conn.execute(
            """
            INSERT INTO file_objects (
                sha256, size_bytes, mime_type, storage_backend, storage_path,
                status, created_at, verified_at
            ) VALUES (?, ?, ?, 'local', ?, 'ready', ?, ?)
            """,
            ("a" * 64, 3, "text/plain", "attachments/sha256/aa/" + "a" * 64, timestamp, timestamp),
        )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                INSERT INTO file_objects (
                    sha256, size_bytes, mime_type, storage_backend, storage_path,
                    status, created_at, verified_at
                ) VALUES (?, ?, ?, 'local', ?, 'ready', ?, ?)
                """,
                ("a" * 64, 3, "text/plain", "other", timestamp, timestamp),
            )


def test_store_stream_reuses_physical_file_for_equal_content(isolated_db):
    from backend.app import file_storage

    file_storage = importlib.reload(file_storage)
    with isolated_db.connect() as conn:
        first = file_storage.store_stream(conn, io.BytesIO(b"same-content"), mime_type="text/plain")
        second = file_storage.store_stream(conn, io.BytesIO(b"same-content"), mime_type="application/octet-stream")
        conn.commit()

        assert first["id"] == second["id"]
        assert first["sha256"] == second["sha256"]
        assert first["storage_path"] == second["storage_path"]
        assert conn.execute("SELECT COUNT(*) FROM file_objects").fetchone()[0] == 1

        target = file_storage.resolve_file_object(first)
        assert target is not None
        assert target.read_bytes() == b"same-content"
        assert str(target).startswith(str(isolated_db.ATTACHMENT_STORAGE_DIR.resolve()))


def test_write_then_register_keeps_database_work_out_of_download_phase(isolated_db):
    from backend.app import file_storage

    file_storage = importlib.reload(file_storage)
    staged = file_storage.write_stream(io.BytesIO(b"downloaded"), mime_type="application/pdf")

    with isolated_db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM file_objects").fetchone()[0] == 0
        registered = file_storage.register_file_object(conn, staged)
        conn.commit()

        assert registered["sha256"] == staged["sha256"]
        assert registered["storage_path"] == staged["storage_path"]
        assert conn.execute("SELECT COUNT(*) FROM file_objects").fetchone()[0] == 1


def test_resolve_attachment_path_falls_back_to_legacy_data_dir(isolated_db):
    from backend.app import file_storage

    file_storage = importlib.reload(file_storage)
    legacy = isolated_db.DATA_DIR / "uploads" / "legacy.txt"
    legacy.parent.mkdir(parents=True, exist_ok=True)
    legacy.write_text("legacy", encoding="utf-8")

    resolved, used_legacy = file_storage.resolve_attachment_path(
        {"file_object_id": None, "file_path": "uploads/legacy.txt"}
    )

    assert resolved == legacy.resolve()
    assert used_legacy is True


def test_resolve_file_object_rejects_path_outside_storage_root(isolated_db):
    from backend.app import file_storage

    file_storage = importlib.reload(file_storage)
    with pytest.raises(ValueError, match="存储路径无效"):
        file_storage.resolve_file_object(
            {
                "storage_path": "../escape",
                "status": "ready",
            }
        )
