from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
from typing import Tuple


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 240_000)
    return "pbkdf2_sha256$240000${}${}".format(
        base64.b64encode(salt).decode("ascii"),
        base64.b64encode(digest).decode("ascii"),
    )


def _parts(stored_hash: str) -> Tuple[int, bytes, bytes]:
    algorithm, iterations, salt, digest = stored_hash.split("$", 3)
    if algorithm != "pbkdf2_sha256":
        raise ValueError("Unsupported password hash")
    return int(iterations), base64.b64decode(salt), base64.b64decode(digest)


def verify_password(password: str, stored_hash: str) -> bool:
    try:
        iterations, salt, expected = _parts(stored_hash)
    except Exception:
        return False
    actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return hmac.compare_digest(actual, expected)


def new_session_token() -> str:
    return secrets.token_urlsafe(40)
