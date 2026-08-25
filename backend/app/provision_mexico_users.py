from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import unicodedata
from dataclasses import dataclass
from typing import Any, Iterable, Optional

from .db import connect, init_db, now_iso, write_audit
from .security import hash_password


INITIAL_PASSWORD = "Yuewei123"


class ProvisioningConflict(RuntimeError):
    pass


@dataclass(frozen=True)
class MexicoAccountSpec:
    username: str
    identity_name: str
    scope: str
    role: Optional[str] = None
    protected_existing: bool = False
    username_aliases: tuple[str, ...] = ()
    display_aliases: tuple[str, ...] = ()


ACCOUNT_SPECS: tuple[MexicoAccountSpec, ...] = (
    MexicoAccountSpec(
        username="tiffany",
        identity_name="周汉琴",
        scope="all",
        protected_existing=True,
        username_aliases=("tiffany", "zhouhanqin", "周汉琴"),
        display_aliases=("Tiffany", "周汉琴", "Tiffany 周汉琴", "周汉琴 Tiffany"),
    ),
    MexicoAccountSpec(
        username="shimingkun",
        identity_name="施鸣坤",
        scope="all",
        protected_existing=True,
        username_aliases=("shimingkun", "smk", "施鸣坤"),
        display_aliases=("施鸣坤", "Shi Mingkun"),
    ),
    MexicoAccountSpec("nelly", "MENDEZ.TORRES.NELLY", "all", "finance"),
    MexicoAccountSpec("angelica", "PEREZ.MOLINA.ANGELICA", "all", "finance"),
    MexicoAccountSpec("areli", "ARELI GECEL FUENTES REYNA", "participant", "business"),
    MexicoAccountSpec("daul", "CHONG.MARTINEZ.DAUL", "participant", "business"),
    MexicoAccountSpec("carlos", "Carlos Michaell Diaz Rodriguez", "participant", "business"),
    MexicoAccountSpec(
        "eduardo",
        "MARIO.EDUARDO.GOMEZ.GONZALEZ（爱德华多）",
        "participant",
        "business",
    ),
    MexicoAccountSpec("tonantzin", "TONANTZIN GRANILLO RUBIO", "participant", "business"),
    MexicoAccountSpec("zhouhanjun", "周汉军", "participant", "business"),
    MexicoAccountSpec("lizhonghua", "李仲华", "participant", "business"),
)


def _normalized_identity(value: Any) -> str:
    normalized = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return "".join(character for character in normalized if character.isalnum())


def _safe_user(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    payload = dict(row)
    return {
        "id": int(payload["id"]),
        "username": str(payload["username"]),
        "display_name": str(payload["display_name"]),
        "role": str(payload["role"]),
        "active": bool(payload["active"]),
        "mexico_access_scope": str(payload.get("mexico_access_scope") or "none"),
        "mexico_identity_name": payload.get("mexico_identity_name"),
    }


def _protected_matches(
    users: Iterable[sqlite3.Row],
    spec: MexicoAccountSpec,
) -> list[sqlite3.Row]:
    username_aliases = {
        _normalized_identity(alias)
        for alias in (spec.username, *spec.username_aliases)
    }
    display_aliases = {
        _normalized_identity(alias)
        for alias in (spec.identity_name, *spec.display_aliases)
    }
    return [
        user
        for user in users
        if _normalized_identity(user["username"]) in username_aliases
        or _normalized_identity(user["display_name"]) in display_aliases
    ]


def _preflight_assignments(
    users: list[sqlite3.Row],
) -> list[tuple[MexicoAccountSpec, Optional[sqlite3.Row]]]:
    assignments: list[tuple[MexicoAccountSpec, Optional[sqlite3.Row]]] = []
    assigned_ids: set[int] = set()
    for spec in ACCOUNT_SPECS:
        if spec.protected_existing:
            matches = _protected_matches(users, spec)
            if len(matches) != 1:
                candidates = [
                    f"{row['username']} ({row['display_name']})" for row in matches
                ]
                raise ProvisioningConflict(
                    f"无法唯一匹配 {spec.username.title()}，候选账号：{candidates or ['无']}"
                )
            match: Optional[sqlite3.Row] = matches[0]
        else:
            normalized_username = _normalized_identity(spec.username)
            matches = [
                user
                for user in users
                if _normalized_identity(user["username"]) == normalized_username
            ]
            if len(matches) > 1:
                raise ProvisioningConflict(f"账号 {spec.username} 存在大小写或字符重复")
            match = matches[0] if matches else None
        if match is not None:
            if match["deleted_at"] is not None:
                raise ProvisioningConflict(f"账号 {match['username']} 已删除，需人工处理")
            user_id = int(match["id"])
            if user_id in assigned_ids:
                raise ProvisioningConflict(f"账号 {match['username']} 同时匹配多个人员")
            assigned_ids.add(user_id)
        assignments.append((spec, match))
    return assignments


def provision_mexico_users(
    conn: sqlite3.Connection,
    *,
    actor_id: int,
) -> dict[str, int]:
    actor = conn.execute(
        """
        SELECT * FROM users
        WHERE id = ? AND role = 'admin' AND active = 1 AND deleted_at IS NULL
        """,
        (int(actor_id),),
    ).fetchone()
    if actor is None:
        raise ProvisioningConflict("开通操作人必须是有效的管理员账号")
    users = conn.execute("SELECT * FROM users ORDER BY id").fetchall()
    assignments = _preflight_assignments(users)
    counts = {"created": 0, "updated": 0, "unchanged": 0}
    conn.execute("SAVEPOINT provision_mexico_users")
    try:
        for spec, existing in assignments:
            if existing is None:
                cursor = conn.execute(
                    """
                    INSERT INTO users (
                        username, password_hash, role, display_name, active,
                        mexico_access_scope, mexico_identity_name, created_at
                    ) VALUES (?, ?, ?, ?, 1, ?, ?, ?)
                    """,
                    (
                        spec.username,
                        hash_password(INITIAL_PASSWORD),
                        spec.role,
                        spec.identity_name,
                        spec.scope,
                        spec.identity_name,
                        now_iso(),
                    ),
                )
                created = conn.execute(
                    "SELECT * FROM users WHERE id = ?", (cursor.lastrowid,)
                ).fetchone()
                write_audit(
                    conn,
                    actor_id,
                    "user.create",
                    "user",
                    int(cursor.lastrowid),
                    new_value=_safe_user(created),
                    reason="mexico approval access provisioning",
                )
                counts["created"] += 1
                continue

            old_public = _safe_user(existing)
            current_scope = str(existing["mexico_access_scope"] or "none")
            current_identity = str(existing["mexico_identity_name"] or "").strip()
            if current_scope == spec.scope and current_identity == spec.identity_name:
                counts["unchanged"] += 1
                continue
            conn.execute(
                """
                UPDATE users
                SET mexico_access_scope = ?, mexico_identity_name = ?
                WHERE id = ?
                """,
                (spec.scope, spec.identity_name, int(existing["id"])),
            )
            updated = conn.execute(
                "SELECT * FROM users WHERE id = ?", (int(existing["id"]),)
            ).fetchone()
            write_audit(
                conn,
                actor_id,
                "user.update",
                "user",
                int(existing["id"]),
                old_value=old_public,
                new_value=_safe_user(updated),
                reason="mexico approval access provisioning",
            )
            counts["updated"] += 1
        conn.execute("RELEASE SAVEPOINT provision_mexico_users")
    except Exception:
        conn.execute("ROLLBACK TO SAVEPOINT provision_mexico_users")
        conn.execute("RELEASE SAVEPOINT provision_mexico_users")
        raise
    return counts


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Provision Mexico approval users safely")
    parser.add_argument("--actor-username", required=True)
    args = parser.parse_args(argv)
    init_db()
    try:
        with connect() as conn:
            actor = conn.execute(
                """
                SELECT id FROM users
                WHERE LOWER(TRIM(username)) = LOWER(TRIM(?))
                  AND role = 'admin' AND active = 1 AND deleted_at IS NULL
                """,
                (args.actor_username,),
            ).fetchall()
            if len(actor) != 1:
                raise ProvisioningConflict("无法唯一找到有效的管理员操作人")
            result = provision_mexico_users(conn, actor_id=int(actor[0]["id"]))
    except ProvisioningConflict as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
