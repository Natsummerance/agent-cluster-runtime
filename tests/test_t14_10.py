"""Task 14.10 认证：local/LDAP/OIDC 三 provider、JWT token 服务、serve 登录端点与 0.0.0.0 守卫。"""

from __future__ import annotations

import json
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from types import SimpleNamespace

import jwt
import pytest

import agent_cluster.server as server_mod
from agent_cluster.auth import (
    LdapAuthProvider,
    LocalAuthProvider,
    OidcAuthProvider,
    TokenService,
)
from agent_cluster.server import WorkbenchHandler, WorkbenchServer

SECRET = "test-secret-0123456789"


def _make_server(tmp_path, monkeypatch, **kwargs):
    monkeypatch.setattr(server_mod, "INDEX_DIR", tmp_path / "home")
    workbench = WorkbenchServer(host="127.0.0.1", port=0, auth_token="", **kwargs)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), WorkbenchHandler)
    httpd.workbench = workbench
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return port, workbench, httpd


def _request(port, method, path, body=None, headers=None):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    for key, value in (headers or {}).items():
        req.add_header(key, value)
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


# ---------------------------------------------------------------------------
# local provider
# ---------------------------------------------------------------------------


def test_local_auth_provider():
    provider = LocalAuthProvider({"alice": "pw-1", "bob": "pw-2"})
    assert provider.authenticate("alice", "pw-1") == "alice"
    assert provider.authenticate("bob", "pw-2") == "bob"
    assert provider.authenticate("alice", "wrong") is None
    assert provider.authenticate("carol", "pw-1") is None
    assert provider.authenticate("", "") is None


def test_local_auth_provider_empty_map_disabled():
    provider = LocalAuthProvider({})
    assert provider.authenticate("alice", "pw") is None


# ---------------------------------------------------------------------------
# ldap provider
# ---------------------------------------------------------------------------


def test_ldap_provider_user_dn_and_bind():
    provider = LdapAuthProvider(
        server="ldap://dc.example.com",
        base_dn="ou=people,dc=example,dc=com",
        bind_template="cn={username},{base_dn}",
        user_dn_template=None,
    )
    assert provider._user_dn("alice") == "cn=alice,ou=people,dc=example,dc=com"


def test_ldap_provider_bind_flow(monkeypatch):
    calls: list[tuple[str, str]] = []
    bound = {}

    class FakeConnection:
        def __init__(self, server, user, password, auto_bind):
            calls.append((str(user), str(password)))
            bound["ok"] = user == "cn=alice,ou=people,dc=example,dc=com" and password == "secret"
            self.bound = bound["ok"]

        def unbind(self):
            bound["unbound"] = True

    monkeypatch.setattr("ldap3.Server", lambda url: f"server:{url}")
    monkeypatch.setattr("ldap3.Connection", FakeConnection)
    provider = LdapAuthProvider(
        server="ldap://dc.example.com",
        base_dn="ou=people,dc=example,dc=com",
        user_dn_template="cn={username},{base_dn}",
        bind_template=None,
    )
    assert provider.authenticate("alice", "secret") == "alice"
    assert provider.authenticate("alice", "bad") is None
    assert calls == [
        ("cn=alice,ou=people,dc=example,dc=com", "secret"),
        ("cn=alice,ou=people,dc=example,dc=com", "bad"),
    ]
    assert bound.get("unbound") is True


def test_ldap_provider_missing_extra_fail_loud(monkeypatch):
    monkeypatch.setitem(sys.modules, "ldap3", None)
    provider = LdapAuthProvider(server="ldap://x", base_dn="ou=p,dc=x", user_dn_template="cn={username},{base_dn}")
    with pytest.raises(ImportError, match="ldap3"):
        provider.authenticate("alice", "pw")


# ---------------------------------------------------------------------------
# oidc provider（id_token 校验）
# ---------------------------------------------------------------------------


def test_oidc_provider_validates_id_token():
    provider = OidcAuthProvider(shared_secret=SECRET, issuer="https://idp.example.com", audience="agent-cluster")
    token = jwt.encode(
        {"sub": "user-42", "iss": "https://idp.example.com", "aud": "agent-cluster", "exp": int(time.time()) + 600},
        SECRET,
        algorithm="HS256",
    )
    assert provider.authenticate("user-42", token) == "user-42"
    assert provider.authenticate("other", token) is None


def test_oidc_provider_rejects_bad_or_expired():
    provider = OidcAuthProvider(shared_secret=SECRET, issuer="https://idp.example.com", audience="agent-cluster")
    bad = jwt.encode({"sub": "u1", "iss": "https://idp.example.com", "aud": "agent-cluster"}, "wrong-secret", algorithm="HS256")
    assert provider.authenticate("u1", bad) is None
    expired = jwt.encode(
        {"sub": "u1", "iss": "https://idp.example.com", "aud": "agent-cluster", "exp": int(time.time()) - 60},
        SECRET,
        algorithm="HS256",
    )
    assert provider.authenticate("u1", expired) is None


# ---------------------------------------------------------------------------
# token service
# ---------------------------------------------------------------------------


def test_token_service_issue_verify_and_refresh():
    tokens = TokenService(secret=SECRET, access_ttl_seconds=300, refresh_ttl_seconds=3600)
    pair = tokens.issue("alice")
    assert pair["access_token"] and pair["refresh_token"]
    assert tokens.verify_access(pair["access_token"]) == "alice"
    assert tokens.verify_refresh(pair["refresh_token"]) == "alice"
    rotated = tokens.refresh(pair["refresh_token"])
    assert rotated["access_token"] != pair["access_token"]
    assert tokens.verify_access(rotated["access_token"]) == "alice"


def test_token_service_rejects_foreign_secret_and_garbage():
    tokens = TokenService(secret=SECRET)
    pair = tokens.issue("alice")
    other = TokenService(secret="another-secret-0123456789")
    assert other.verify_access(pair["access_token"]) is None
    assert other.verify_refresh(pair["refresh_token"]) is None
    assert tokens.verify_access("garbage") is None
    with pytest.raises(ValueError, match="refresh"):
        tokens.refresh(pair["access_token"])  # access 不能当 refresh 用


# ---------------------------------------------------------------------------
# serve 端点
# ---------------------------------------------------------------------------


def test_login_me_refresh_flow(tmp_path, monkeypatch):
    auth = LocalAuthProvider({"alice": "pw-1"})
    port, workbench, httpd = _make_server(tmp_path, monkeypatch, auth_provider=auth, auth_secret=SECRET)
    try:
        status, body = _request(port, "POST", "/api/v1/auth/login", {"username": "alice", "password": "pw-1"})
        assert status == 200, body
        data = body["data"]
        assert data["user"] == "alice"
        access = data["access_token"]
        status, body = _request(port, "GET", "/api/v1/auth/me", headers={"Authorization": f"Bearer {access}"})
        assert status == 200 and body["data"]["user"] == "alice"
        status, body = _request(port, "GET", "/api/v1/auth/me")
        assert status == 401
        status, body = _request(port, "POST", "/api/v1/auth/refresh", {"refresh_token": data["refresh_token"]})
        assert status == 200 and body["data"]["user"] == "alice"
        status, body = _request(port, "GET", "/api/v1/status", headers={"Authorization": f"Bearer {access}"})
        assert status == 200 and body["data"]["auth"] == {"enabled": True, "user": "alice"}
    finally:
        httpd.shutdown()


def test_login_rejects_bad_credentials(tmp_path, monkeypatch):
    auth = LocalAuthProvider({"alice": "pw-1"})
    port, workbench, httpd = _make_server(tmp_path, monkeypatch, auth_provider=auth, auth_secret=SECRET)
    try:
        status, body = _request(port, "POST", "/api/v1/auth/login", {"username": "alice", "password": "nope"})
        assert status == 401
        assert body.get("code") == "invalid_credentials"
        status, body = _request(port, "POST", "/api/v1/auth/login", {"username": "ghost", "password": "x"})
        assert status == 401
    finally:
        httpd.shutdown()


def test_auth_disabled_endpoints_and_status(tmp_path, monkeypatch):
    port, workbench, httpd = _make_server(tmp_path, monkeypatch)
    try:
        status, body = _request(port, "POST", "/api/v1/auth/login", {"username": "a", "password": "b"})
        assert status == 404 and body.get("code") == "auth_disabled"
        status, body = _request(port, "GET", "/api/v1/status")
        assert body["data"]["auth"] == {"enabled": False, "user": None}
    finally:
        httpd.shutdown()


def test_protected_routes_require_bearer_when_auth_enabled(tmp_path, monkeypatch):
    auth = LocalAuthProvider({"alice": "pw-1"})
    port, workbench, httpd = _make_server(tmp_path, monkeypatch, auth_provider=auth, auth_secret=SECRET)
    try:
        status, body = _request(port, "GET", "/api/v1/users")
        assert status == 401
        status, body = _request(port, "POST", "/api/v1/auth/login", {"username": "alice", "password": "pw-1"})
        access = body["data"]["access_token"]
        status, body = _request(port, "GET", "/api/v1/users", headers={"Authorization": f"Bearer {access}"})
        assert status == 200
    finally:
        httpd.shutdown()


# ---------------------------------------------------------------------------
# 0.0.0.0 守卫（启用认证才放行）
# ---------------------------------------------------------------------------


def test_host_guard_requires_auth_for_non_localhost(tmp_path, monkeypatch):
    monkeypatch.setattr(server_mod, "INDEX_DIR", tmp_path / "home")
    base = dict(
        port=0,
        auth_token="",
        plugins_dir=[],
        mcp=[],
        mcp_http=[],
    )
    with pytest.raises(RuntimeError, match="0.0.0.0"):
        server_mod.build_server(SimpleNamespace(host="0.0.0.0", **base))
    with pytest.raises(RuntimeError, match="认证"):
        server_mod.build_server(SimpleNamespace(host="0.0.0.0", auth_provider=None, auth_secret="", **base))
    # 127.0.0.1 无认证放行（仅构造，不进入 serve_forever）
    server, httpd = server_mod.build_server(SimpleNamespace(host="127.0.0.1", **base))
    httpd.server_close()
