from __future__ import annotations

import base64
import hashlib
import uuid
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from fastapi.testclient import TestClient

from backend.app import eims_sso
from backend.app.db import connect, now_iso
from backend.app.main import app
from backend.app.security import hash_password


class FakeEims:
    def __init__(self):
        self.app_user_id: str | None = None
        self.token_calls = 0
        self.challenge: str | None = None

    def response(self, url: str, payload: dict) -> httpx.Response:
        return httpx.Response(200, json=payload, request=httpx.Request("GET", url))

    def get(self, url: str, **kwargs) -> httpx.Response:
        if url.endswith("/oauth/.well-known/openid-configuration"):
            return self.response(url, {
                "issuer": "https://eims.example.test",
                "authorization_endpoint": "https://eims.example.test/oauth/authorize",
                "token_endpoint": "https://eims.example.test/oauth/token",
                "userinfo_endpoint": "https://eims.example.test/oauth/userinfo",
                "end_session_endpoint": "https://eims.example.test/oauth/logout",
            })
        assert url.endswith("/oauth/userinfo")
        assert kwargs["headers"]["Authorization"] == "Bearer test-access-token"
        result = {"sub": "eims-17", "name": "测试用户"}
        if self.app_user_id is not None:
            result["app_user_id"] = self.app_user_id
        return self.response(url, result)

    def post(self, url: str, **kwargs) -> httpx.Response:
        assert url.endswith("/oauth/token")
        assert kwargs["auth"] == ("cashier-client", "server-secret")
        assert kwargs["data"]["redirect_uri"] == "https://payment.example.test/api/auth/eims/callback"
        verifier = kwargs["data"]["code_verifier"]
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode("ascii")
        assert challenge == self.challenge
        self.token_calls += 1
        return self.response(url, {"access_token": "test-access-token", "token_type": "Bearer", "expires_in": 3600})


@pytest.fixture
def sso_client(monkeypatch):
    monkeypatch.setenv("PAYMENT_AUTH_MODE", "eims")
    monkeypatch.setenv("PAYMENT_PUBLIC_BASE_URL", "https://payment.example.test")
    monkeypatch.setenv("EIMS_ISSUER", "https://eims.example.test")
    monkeypatch.setenv("EIMS_CLIENT_ID", "cashier-client")
    monkeypatch.setenv("EIMS_CLIENT_SECRET", "server-secret")
    gateway = FakeEims()
    monkeypatch.setattr(eims_sso.httpx, "get", gateway.get)
    monkeypatch.setattr(eims_sso.httpx, "post", gateway.post)
    with TestClient(app, base_url="https://payment.example.test") as client:
        name = f"sso-{uuid.uuid4().hex}"
        with connect() as conn:
            user_id = conn.execute(
                "INSERT INTO users (username, password_hash, role, display_name, active, created_at) VALUES (?, ?, 'business', ?, 1, ?)",
                (name, hash_password("unused-secret"), name, now_iso()),
            ).lastrowid
        gateway.app_user_id = str(user_id)
        yield client, gateway, user_id


def start_login(client: TestClient, gateway: FakeEims) -> str:
    response = client.get("/api/auth/eims/start", follow_redirects=False)
    assert response.status_code == 302
    location = urlsplit(response.headers["location"])
    assert location.scheme == "https" and location.netloc == "eims.example.test"
    params = parse_qs(location.query)
    assert params["response_type"] == ["code"]
    assert params["scope"] == ["openid profile"]
    assert params["redirect_uri"] == ["https://payment.example.test/api/auth/eims/callback"]
    assert params["code_challenge_method"] == ["S256"]
    gateway.challenge = params["code_challenge"][0]
    assert response.cookies.get(eims_sso.LOGIN_COOKIE)
    return params["state"][0]


def finish_login(client: TestClient, state: str) -> httpx.Response:
    return client.get(
        "/api/auth/eims/callback",
        params={"code": "one-use-code", "state": state},
        follow_redirects=False,
    )


def test_eims_login_uses_binding_and_keeps_local_role(sso_client):
    client, gateway, user_id = sso_client
    state = start_login(client, gateway)
    response = finish_login(client, state)
    assert response.status_code == 303
    assert response.headers["location"] == "/"
    assert response.cookies.get("session")
    assert "Secure" in response.headers["set-cookie"]
    current = client.get("/api/me")
    assert current.status_code == 200
    assert current.json()["user"]["id"] == user_id
    assert current.json()["user"]["role"] == "business"
    with connect() as conn:
        session = conn.execute("SELECT auth_source, eims_sub, expires_at FROM sessions WHERE user_id = ? ORDER BY created_at DESC LIMIT 1", (user_id,)).fetchone()
    assert session["auth_source"] == "eims"
    assert session["eims_sub"] == "eims-17"
    assert session["expires_at"]
    assert finish_login(client, state).headers["location"] == "/?sso_error=invalid_state"
    assert gateway.token_calls == 1


def test_eims_state_mismatch_never_exchanges_token(sso_client):
    client, gateway, _ = sso_client
    state = start_login(client, gateway)
    wrong = finish_login(client, state + "-changed")
    assert wrong.headers["location"] == "/?sso_error=invalid_state"
    assert gateway.token_calls == 0
    assert client.get("/api/me").status_code == 401


def test_eims_start_retires_previous_local_session_even_if_login_fails(sso_client, monkeypatch):
    client, gateway, user_id = sso_client
    with connect() as conn:
        username = conn.execute("SELECT username FROM users WHERE id = ?", (user_id,)).fetchone()["username"]
    monkeypatch.setenv("PAYMENT_AUTH_MODE", "hybrid")
    assert client.post("/api/auth/login", json={"username": username, "password": "unused-secret"}).status_code == 200
    old_token = client.cookies.get("session")

    state = start_login(client, gateway)
    assert client.cookies.get("session") is None
    assert client.get("/api/me").status_code == 401
    with connect() as conn:
        assert conn.execute("SELECT 1 FROM sessions WHERE token = ?", (old_token,)).fetchone() is None

    gateway.app_user_id = None
    assert finish_login(client, state).headers["location"] == "/?sso_error=binding_missing"
    assert client.get("/api/me").status_code == 401


def test_eims_start_retires_previous_session_when_provider_is_unavailable(sso_client, monkeypatch):
    client, _, user_id = sso_client
    with connect() as conn:
        username = conn.execute("SELECT username FROM users WHERE id = ?", (user_id,)).fetchone()["username"]
    monkeypatch.setenv("PAYMENT_AUTH_MODE", "hybrid")
    assert client.post("/api/auth/login", json={"username": username, "password": "unused-secret"}).status_code == 200
    old_token = client.cookies.get("session")

    def unavailable(_config):
        raise eims_sso.SsoError("provider_unavailable")

    monkeypatch.setattr("backend.app.main.discover", unavailable)
    response = client.get("/api/auth/eims/start", follow_redirects=False)
    assert response.headers["location"] == "/?sso_error=provider_unavailable"
    assert client.cookies.get("session") is None
    assert client.get("/api/me").status_code == 401
    with connect() as conn:
        assert conn.execute("SELECT 1 FROM sessions WHERE token = ?", (old_token,)).fetchone() is None


def test_eims_denied_authorization_consumes_flow(sso_client):
    client, gateway, _ = sso_client
    state = start_login(client, gateway)
    denied = client.get(
        "/api/auth/eims/callback",
        params={"error": "access_denied", "state": state},
        follow_redirects=False,
    )
    assert denied.headers["location"] == "/?sso_error=access_denied"
    assert finish_login(client, state).headers["location"] == "/?sso_error=invalid_state"
    assert gateway.token_calls == 0


@pytest.mark.parametrize(("binding", "error"), [
    (None, "binding_missing"),
    ("001", "invalid_binding"),
    ("999999999", "account_missing"),
])
def test_eims_bad_binding_never_creates_session(sso_client, binding, error):
    client, gateway, _ = sso_client
    gateway.app_user_id = binding
    response = finish_login(client, start_login(client, gateway))
    assert response.headers["location"] == f"/?sso_error={error}"
    assert client.get("/api/me").status_code == 401


def test_eims_disabled_local_user_is_rejected(sso_client):
    client, gateway, user_id = sso_client
    with connect() as conn:
        conn.execute("UPDATE users SET active = 0 WHERE id = ?", (user_id,))
    response = finish_login(client, start_login(client, gateway))
    assert response.headers["location"] == "/?sso_error=account_disabled"


def test_eims_logout_clears_local_session_and_validates_state(sso_client):
    client, gateway, _ = sso_client
    assert finish_login(client, start_login(client, gateway)).status_code == 303
    response = client.post("/api/auth/logout")
    assert response.status_code == 200
    logout_url = urlsplit(response.json()["redirect_url"])
    assert logout_url.path == "/oauth/logout"
    params = parse_qs(logout_url.query)
    assert params["client_id"] == ["cashier-client"]
    assert params["post_logout_redirect_uri"] == ["https://payment.example.test/api/auth/eims/logout/callback"]
    assert client.get("/api/me").status_code == 401
    callback = client.get("/api/auth/eims/logout/callback", params={"state": params["state"][0]}, follow_redirects=False)
    assert callback.headers["location"] == "/?logged_out=1"
    repeated = client.get("/api/auth/eims/logout/callback", params={"state": params["state"][0]}, follow_redirects=False)
    assert repeated.headers["location"] == "/?sso_error=invalid_logout_state"


def test_eims_only_disables_password_login_and_expires_sessions(sso_client):
    client, gateway, user_id = sso_client
    assert client.post("/api/auth/login", json={"username": "admin", "password": "admin123"}).status_code == 403
    assert finish_login(client, start_login(client, gateway)).status_code == 303
    with connect() as conn:
        conn.execute("UPDATE sessions SET expires_at = '2000-01-01T00:00:00' WHERE user_id = ?", (user_id,))
    assert client.get("/api/me").status_code == 401


def test_hybrid_rotates_local_session_and_mode_switch_rejects_wrong_source(sso_client, monkeypatch):
    client, gateway, user_id = sso_client
    with connect() as conn:
        username = conn.execute("SELECT username FROM users WHERE id = ?", (user_id,)).fetchone()["username"]
    monkeypatch.setenv("PAYMENT_AUTH_MODE", "hybrid")
    local_login = client.post("/api/auth/login", json={"username": username, "password": "unused-secret"})
    assert local_login.status_code == 200
    local_token = client.cookies.get("session")
    monkeypatch.setenv("PAYMENT_AUTH_MODE", "eims")
    assert client.get("/api/me").status_code == 401
    monkeypatch.setenv("PAYMENT_AUTH_MODE", "hybrid")
    assert finish_login(client, start_login(client, gateway)).status_code == 303
    assert client.cookies.get("session") != local_token
    with connect() as conn:
        assert conn.execute("SELECT 1 FROM sessions WHERE token = ?", (local_token,)).fetchone() is None
    monkeypatch.setenv("PAYMENT_AUTH_MODE", "local")
    assert client.get("/api/me").status_code == 401


def test_eims_admin_can_create_local_binding_account_without_password(sso_client):
    client, gateway, _ = sso_client
    admin_name = f"sso-admin-{uuid.uuid4().hex}"
    with connect() as conn:
        admin_id = conn.execute(
            "INSERT INTO users (username, password_hash, role, display_name, active, created_at) VALUES (?, ?, 'admin', ?, 1, ?)",
            (admin_name, hash_password("unused-admin-secret"), admin_name, now_iso()),
        ).lastrowid
    gateway.app_user_id = str(admin_id)
    assert finish_login(client, start_login(client, gateway)).status_code == 303
    created = client.post("/api/admin/users", json={
        "username": f"new-sso-{uuid.uuid4().hex}",
        "role": "business",
        "display_name": "待绑定用户",
    })
    assert created.status_code == 200
    new_user_id = created.json()["user"]["id"]
    assert isinstance(new_user_id, int)
    assert client.post(f"/api/admin/users/{new_user_id}/reset-password").status_code == 403
    assert client.post("/api/auth/change-password", json={
        "current_password": "unused-admin-secret",
        "new_password": "another-secret",
        "confirm_password": "another-secret",
    }).status_code == 403


def test_sso_configuration_requires_https_by_default(monkeypatch):
    monkeypatch.setenv("EIMS_ISSUER", "http://eims.example.test")
    monkeypatch.setenv("EIMS_CLIENT_ID", "cashier-client")
    monkeypatch.setenv("EIMS_CLIENT_SECRET", "server-secret")
    monkeypatch.setenv("PAYMENT_PUBLIC_BASE_URL", "http://payment.example.test")
    monkeypatch.delenv("PAYMENT_SSO_ALLOW_HTTP", raising=False)
    with pytest.raises(eims_sso.SsoError, match="invalid_configuration"):
        eims_sso.settings()
    monkeypatch.setenv("PAYMENT_SSO_ALLOW_HTTP", "1")
    assert not eims_sso.settings().secure_cookies


def test_discovery_rejects_cross_origin_endpoints(sso_client, monkeypatch):
    def wrong_discovery(url: str, **kwargs) -> httpx.Response:
        return httpx.Response(200, json={
            "issuer": "https://eims.example.test",
            "authorization_endpoint": "https://other.example.test/oauth/authorize",
            "token_endpoint": "https://eims.example.test/oauth/token",
            "userinfo_endpoint": "https://eims.example.test/oauth/userinfo",
            "end_session_endpoint": "https://eims.example.test/oauth/logout",
        }, request=httpx.Request("GET", url))

    monkeypatch.setattr(eims_sso.httpx, "get", wrong_discovery)
    client, _, _ = sso_client
    response = client.get("/api/auth/eims/start", follow_redirects=False)
    assert response.headers["location"] == "/?sso_error=invalid_discovery"
