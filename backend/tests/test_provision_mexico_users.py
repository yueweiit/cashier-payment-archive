from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.app.provision_mexico_users import (
    ProvisioningConflict,
    provision_mexico_users,
)
from backend.app.security import hash_password, verify_password


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


def _insert_existing_user(conn, *, username: str, display_name: str, role: str, password: str) -> int:
    from backend.app.db import now_iso

    return int(
        conn.execute(
            """
            INSERT INTO users (
                username, password_hash, role, display_name, active,
                mexico_access_scope, created_at
            ) VALUES (?, ?, ?, ?, 1, 'none', ?)
            """,
            (username, hash_password(password), role, display_name, now_iso()),
        ).lastrowid
    )


def test_provision_mexico_users_preserves_existing_credentials_and_is_idempotent(
    isolated_db,
) -> None:
    with isolated_db.connect() as conn:
        actor_id = int(
            conn.execute("SELECT id FROM users WHERE username = 'admin'").fetchone()[0]
        )
        tiffany_id = _insert_existing_user(
            conn,
            username="Tiffany",
            display_name="周汉琴",
            role="general_manager",
            password="TiffanyExistingPassword",
        )
        shimingkun_id = _insert_existing_user(
            conn,
            username="smk",
            display_name="施鸣坤",
            role="admin",
            password="SmkExistingPassword",
        )
        before = {
            row["id"]: (row["role"], row["password_hash"])
            for row in conn.execute(
                "SELECT id, role, password_hash FROM users WHERE id IN (?, ?)",
                (tiffany_id, shimingkun_id),
            ).fetchall()
        }

        first = provision_mexico_users(conn, actor_id=actor_id)

        assert first == {"created": 9, "updated": 2, "unchanged": 0}
        existing = conn.execute(
            """
            SELECT id, role, password_hash, mexico_access_scope, mexico_identity_name
            FROM users WHERE id IN (?, ?) ORDER BY id
            """,
            (tiffany_id, shimingkun_id),
        ).fetchall()
        assert {
            row["id"]: (row["role"], row["password_hash"])
            for row in existing
        } == before
        assert [
            (row["mexico_access_scope"], row["mexico_identity_name"])
            for row in existing
        ] == [("all", "周汉琴"), ("all", "施鸣坤")]

        created = {
            row["username"]: row
            for row in conn.execute(
                """
                SELECT username, role, password_hash, mexico_access_scope,
                       mexico_identity_name
                FROM users
                WHERE username IN (
                    'nelly', 'angelica', 'areli', 'daul', 'carlos', 'eduardo',
                    'tonantzin', 'zhouhanjun', 'lizhonghua'
                )
                """
            ).fetchall()
        }
        assert set(created) == {
            "nelly",
            "angelica",
            "areli",
            "daul",
            "carlos",
            "eduardo",
            "tonantzin",
            "zhouhanjun",
            "lizhonghua",
        }
        assert created["nelly"]["role"] == "finance"
        assert created["angelica"]["role"] == "finance"
        for username, row in created.items():
            assert verify_password("Yuewei123", row["password_hash"])
            if username not in {"nelly", "angelica"}:
                assert row["role"] == "business"
                assert row["mexico_access_scope"] == "participant"
                user_id = conn.execute(
                    "SELECT id FROM users WHERE username = ?", (username,)
                ).fetchone()[0]
                assert conn.execute(
                    "SELECT COUNT(*) FROM user_sheet_permissions WHERE user_id = ?",
                    (user_id,),
                ).fetchone()[0] == 0
        assert created["nelly"]["mexico_access_scope"] == "all"
        assert created["angelica"]["mexico_access_scope"] == "all"
        assert conn.execute(
            "SELECT COUNT(*) FROM users WHERE display_name LIKE '%未识别%'"
        ).fetchone()[0] == 0

        audit_rows = conn.execute(
            """
            SELECT action, old_value_json, new_value_json
            FROM audit_logs
            WHERE action IN ('user.create', 'user.update')
            ORDER BY id
            """
        ).fetchall()
        assert [row["action"] for row in audit_rows].count("user.create") == 9
        assert [row["action"] for row in audit_rows].count("user.update") == 2
        for row in audit_rows:
            serialized = json.dumps(dict(row), ensure_ascii=False)
            assert "password" not in serialized.lower()
            assert "Yuewei123" not in serialized

        second = provision_mexico_users(conn, actor_id=actor_id)
        assert second == {"created": 0, "updated": 0, "unchanged": 11}
        assert conn.execute(
            "SELECT COUNT(*) FROM audit_logs WHERE action IN ('user.create', 'user.update')"
        ).fetchone()[0] == 11


def test_provision_mexico_users_aborts_before_mutation_on_ambiguous_protected_match(
    isolated_db,
) -> None:
    with isolated_db.connect() as conn:
        actor_id = int(
            conn.execute("SELECT id FROM users WHERE username = 'admin'").fetchone()[0]
        )
        _insert_existing_user(
            conn,
            username="Tiffany",
            display_name="Tiffany",
            role="general_manager",
            password="one",
        )
        _insert_existing_user(
            conn,
            username="zhouhanqin",
            display_name="周汉琴",
            role="finance",
            password="two",
        )
        _insert_existing_user(
            conn,
            username="smk",
            display_name="施鸣坤",
            role="admin",
            password="three",
        )
        before_users = int(conn.execute("SELECT COUNT(*) FROM users").fetchone()[0])

        with pytest.raises(ProvisioningConflict, match="Tiffany"):
            provision_mexico_users(conn, actor_id=actor_id)

        assert conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == before_users
        assert conn.execute(
            "SELECT COUNT(*) FROM audit_logs WHERE action IN ('user.create', 'user.update')"
        ).fetchone()[0] == 0
        assert conn.execute(
            "SELECT COUNT(*) FROM users WHERE username = 'nelly'"
        ).fetchone()[0] == 0
