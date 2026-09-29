"""Server-side EIMS authorization-code login helpers.

EIMS tokens are used only to fetch UserInfo. They are never persisted in the
application database or returned to the browser.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import sqlite3
from dataclasses import dataclass
from time import time
from typing import Any
from urllib.parse import urlencode, urlsplit

import httpx

from .db import now_iso


LOGIN_COOKIE = "eims_login_flow"
LOGOUT_COOKIE = "eims_logout_flow"
FLOW_COOKIE_PATH = "/api/auth/eims"
FLOW_TTL_SECONDS = 600


class SsoError(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class SsoSettings:
    issuer: str
    client_id: str
    client_secret: str
    public_base_url: str
    secure_cookies: bool

    @property
    def callback_uri(self) -> str:
        return f"{self.public_base_url}/api/auth/eims/callback"

    @property
    def logout_callback_uri(self) -> str:
        return f"{self.public_base_url}/api/auth/eims/logout/callback"


@dataclass(frozen=True)
class SsoEndpoints:
    authorize: str
    token: str
    userinfo: str
    logout: str


@dataclass(frozen=True)
class SsoFlow:
    flow_id: str
    state: str
    code_verifier: str | None = None


def auth_mode() -> str:
    mode = os.environ.get("PAYMENT_AUTH_MODE", "local").strip().lower()
    if mode not in {"local", "hybrid", "eims"}:
        raise RuntimeError("PAYMENT_AUTH_MODE 必须为 local、hybrid 或 eims")
    return mode


def settings() -> SsoSettings:
    issuer = os.environ.get("EIMS_ISSUER", "").strip().rstrip("/")
    client_id = os.environ.get("EIMS_CLIENT_ID", "").strip()
    client_secret = os.environ.get("EIMS_CLIENT_SECRET", "").strip()
    public_base_url = os.environ.get("PAYMENT_PUBLIC_BASE_URL", "").strip().rstrip("/")
    if not all((issuer, client_id, client_secret, public_base_url)):
        raise SsoError("not_configured")
    allow_http = os.environ.get("PAYMENT_SSO_ALLOW_HTTP", "").strip() == "1"
    for address in (issuer, public_base_url):
        parsed = urlsplit(address)
        if not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise SsoError("invalid_configuration")
        if parsed.path not in {"", "/"}:
            raise SsoError("invalid_configuration")
        if parsed.scheme != "https" and not (allow_http and parsed.scheme == "http"):
            raise SsoError("invalid_configuration")
    return SsoSettings(issuer, client_id, client_secret, public_base_url, urlsplit(public_base_url).scheme == "https")


def _provider_url(value: Any, issuer: str) -> str:
    if not isinstance(value, str):
        raise SsoError("invalid_discovery")
    parsed = urlsplit(value)
    expected = urlsplit(issuer)
    if (parsed.scheme, parsed.netloc) != (expected.scheme, expected.netloc):
        raise SsoError("invalid_discovery")
    if not parsed.path or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise SsoError("invalid_discovery")
    return value


def discover(config: SsoSettings) -> SsoEndpoints:
    try:
        response = httpx.get(
            f"{config.issuer}/oauth/.well-known/openid-configuration",
            timeout=5.0,
            follow_redirects=False,
        )
        response.raise_for_status()
        document = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise SsoError("provider_unavailable") from exc
    if not isinstance(document, dict) or document.get("issuer") != config.issuer:
        raise SsoError("invalid_discovery")
    return SsoEndpoints(
        authorize=_provider_url(document.get("authorization_endpoint"), config.issuer),
        token=_provider_url(document.get("token_endpoint"), config.issuer),
        userinfo=_provider_url(document.get("userinfo_endpoint"), config.issuer),
        logout=_provider_url(document.get("end_session_endpoint"), config.issuer),
    )


def create_flow(conn: sqlite3.Connection, kind: str) -> SsoFlow:
    if kind not in {"login", "logout"}:
        raise ValueError("invalid SSO flow kind")
    flow = SsoFlow(
        flow_id=secrets.token_urlsafe(32),
        state=secrets.token_urlsafe(32),
        code_verifier=secrets.token_urlsafe(64) if kind == "login" else None,
    )
    conn.execute("DELETE FROM sso_transactions WHERE expires_at <= ?", (int(time()),))
    conn.execute(
        """
        INSERT INTO sso_transactions (flow_id, kind, state, code_verifier, created_at, expires_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (flow.flow_id, kind, flow.state, flow.code_verifier, now_iso(), int(time()) + FLOW_TTL_SECONDS),
    )
    return flow


def consume_flow(conn: sqlite3.Connection, kind: str, flow_id: str | None, state: str | None) -> SsoFlow:
    if not flow_id or not state:
        raise SsoError("invalid_state")
    row = conn.execute(
        "SELECT state, code_verifier, expires_at FROM sso_transactions WHERE flow_id = ? AND kind = ?",
        (flow_id, kind),
    ).fetchone()
    if not row or int(row["expires_at"]) <= int(time()) or not hmac.compare_digest(row["state"], state):
        raise SsoError("invalid_state")
    deleted = conn.execute(
        "DELETE FROM sso_transactions WHERE flow_id = ? AND kind = ? AND state = ? AND expires_at > ?",
        (flow_id, kind, row["state"], int(time())),
    )
    if deleted.rowcount != 1:
        raise SsoError("invalid_state")
    return SsoFlow(flow_id=flow_id, state=state, code_verifier=row["code_verifier"])


def authorize_url(config: SsoSettings, endpoints: SsoEndpoints, flow: SsoFlow) -> str:
    if not flow.code_verifier:
        raise ValueError("login flow has no PKCE verifier")
    challenge = base64.urlsafe_b64encode(hashlib.sha256(flow.code_verifier.encode("ascii")).digest()).rstrip(b"=").decode("ascii")
    query = urlencode({
        "client_id": config.client_id,
        "redirect_uri": config.callback_uri,
        "response_type": "code",
        "scope": "openid profile",
        "state": flow.state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    })
    return f"{endpoints.authorize}?{query}"


def userinfo_for_code(config: SsoSettings, endpoints: SsoEndpoints, code: str, verifier: str) -> dict[str, Any]:
    try:
        token_response = httpx.post(
            endpoints.token,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": config.callback_uri,
                "code_verifier": verifier,
            },
            auth=(config.client_id, config.client_secret),
            timeout=8.0,
            follow_redirects=False,
        )
        token_response.raise_for_status()
        token = token_response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise SsoError("token_exchange_failed") from exc
    if not isinstance(token, dict) or not isinstance(token.get("token_type"), str) or token["token_type"].lower() != "bearer":
        raise SsoError("invalid_token_response")
    access_token = token.get("access_token")
    expires_in = token.get("expires_in")
    if not isinstance(access_token, str) or not access_token or not isinstance(expires_in, int) or isinstance(expires_in, bool) or expires_in <= 0:
        raise SsoError("invalid_token_response")
    try:
        user_response = httpx.get(
            endpoints.userinfo,
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=8.0,
            follow_redirects=False,
        )
        user_response.raise_for_status()
        userinfo = user_response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise SsoError("userinfo_failed") from exc
    if not isinstance(userinfo, dict) or not isinstance(userinfo.get("sub"), str) or not userinfo["sub"]:
        raise SsoError("invalid_userinfo")
    return userinfo


def logout_url(config: SsoSettings, endpoints: SsoEndpoints, state: str) -> str:
    return f"{endpoints.logout}?{urlencode({'client_id': config.client_id, 'post_logout_redirect_uri': config.logout_callback_uri, 'state': state})}"
