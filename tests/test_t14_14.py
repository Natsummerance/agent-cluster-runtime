"""Task 14.14 OAuth MCP（rfc 8844）：PKCE S256、授权码一次性、token 交换、
serve 端点（well-known / authorize / token）与远程 MCP 客户端全流程 + credentials 存储。"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import threading
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlencode, urlparse

import pytest

import agent_cluster.server as server_mod
from agent_cluster.auth import LocalAuthProvider, TokenService
from agent_cluster.mcp_client import MCPError, MCPOAuthClient, StreamableHTTPMCPClient
from agent_cluster.oauth_mcp import (
    OAuthAuthorizationServer,
    OAuthError,
    OAuthTokenStore,
    generate_code_challenge,
    generate_code_verifier,
    validate_code_challenge,
    validate_code_verifier,
)
from agent_cluster.server import WorkbenchHandler, WorkbenchServer

SECRET = "test-secret-0123456789"
CALLBACK = "http://127.0.0.1:1/callback"


# ---------------------------------------------------------------------------
# serve 测试基建（沿用 test_t14_9/test_t14_10 模式）
# ---------------------------------------------------------------------------


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """捕获 302 Location 而不跟随（headless 客户端同款行为）。"""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


def _make_server(tmp_path, monkeypatch, **kwargs):
    monkeypatch.setattr(server_mod, "INDEX_DIR", tmp_path / "home")
    workbench = WorkbenchServer(
        host="127.0.0.1",
        port=0,
        auth_token="",
        oauth_issuer="http://127.0.0.1:8765",
        **kwargs,
    )
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), WorkbenchHandler)
    httpd.workbench = workbench
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return port, workbench, httpd


def _request(port, method, path, body=None, headers=None, form=False, follow_redirects=True):
    if body is not None and form:
        data = urllib.parse.urlencode(body).encode("utf-8")
        content_type = "application/x-www-form-urlencoded"
    elif body is not None:
        data = json.dumps(body).encode("utf-8")
        content_type = "application/json"
    else:
        data = None
        content_type = None
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=data, method=method)
    if content_type:
        req.add_header("Content-Type", content_type)
    for key, value in (headers or {}).items():
        req.add_header(key, value)
    opener = urllib.request.build_opener(_NoRedirect()) if not follow_redirects else urllib.request.build_opener()
    try:
        with opener.open(req, timeout=8) as resp:
            raw = resp.read().decode("utf-8")
            return resp.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as exc:
        if not follow_redirects and exc.code in (301, 302, 303, 307, 308):
            return exc.code, exc.headers.get("Location", "")
        raw = exc.read().decode("utf-8")
        try:
            return exc.code, json.loads(raw)
        except json.JSONDecodeError:
            return exc.code, raw


def _authorize_query(client_id="web-app", redirect_uri=CALLBACK, challenge="", method="S256", state="st-1"):
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "code_challenge_method": method,
        "state": state,
    }
    if challenge:
        params["code_challenge"] = challenge
    return urlencode(params)


# ---------------------------------------------------------------------------
# PKCE 原语（RFC 7636 S256）
# ---------------------------------------------------------------------------


def test_pkce_verifier_and_challenge_roundtrip():
    verifier = generate_code_verifier()
    assert 43 <= len(verifier) <= 128
    assert set(verifier) <= set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~")
    challenge = generate_code_challenge(verifier)
    expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    assert challenge == expected
    assert validate_code_verifier(verifier, challenge) is True
    assert validate_code_verifier(verifier + "x", challenge) is False
    assert validate_code_verifier("short", challenge) is False
    assert validate_code_verifier(verifier, None) is True


def test_pkce_challenge_deterministic_and_challenge_format():
    verifier_a = generate_code_verifier()
    verifier_b = generate_code_verifier()
    assert generate_code_challenge(verifier_a) == generate_code_challenge(verifier_a)
    assert generate_code_challenge(verifier_a) != generate_code_challenge(verifier_b)
    assert validate_code_challenge(generate_code_challenge(verifier_a), "S256") is True
    assert validate_code_challenge("x", "S256") is False
    assert validate_code_challenge(generate_code_challenge(verifier_a), "plain") is False


# ---------------------------------------------------------------------------
# OAuthAuthorizationServer：注册 / 授权 / 交换
# ---------------------------------------------------------------------------


def _server(**kwargs):
    return OAuthAuthorizationServer(
        issuer="http://as.example.com",
        resource="http://rs.example.com/mcp",
        authorization_servers=["http://as.example.com"],
        **kwargs,
    )


def _authorize(server, challenge=None, method="S256", **overrides):
    params = {
        "response_type": "code",
        "client_id": "web-app",
        "redirect_uri": CALLBACK,
        "code_challenge": challenge or generate_code_challenge(generate_code_verifier()),
        "code_challenge_method": method,
        "state": "st-1",
    }
    params.update(overrides)
    return server.authorize_redirect(params)


def _code_from_location(location):
    return parse_qs(urlparse(location).query)["code"][0]


def test_register_client_fail_loud():
    server = _server()
    server.register_client("web-app", [CALLBACK], name="网页应用")
    assert server.get_client("web-app")["redirect_uris"] == [CALLBACK]
    with pytest.raises(ValueError, match="web-app"):
        server.register_client("web-app", [CALLBACK])
    with pytest.raises(ValueError, match="client_id"):
        server.register_client("", [CALLBACK])
    with pytest.raises(ValueError, match="redirect_uri"):
        server.register_client("ghost", [])
    with pytest.raises(KeyError):
        server.get_client("ghost")


def test_authorize_requires_code_challenge():
    server = _server()
    server.register_client("web-app", [CALLBACK])
    with pytest.raises(OAuthError) as exc:
        server.authorize_redirect(
            {"response_type": "code", "client_id": "web-app", "redirect_uri": CALLBACK}
        )
    assert exc.value.code == "invalid_request"


def test_authorize_rejects_bad_challenge_and_method():
    server = _server()
    server.register_client("web-app", [CALLBACK])
    with pytest.raises(OAuthError) as exc:
        _authorize(server, challenge="x")
    assert exc.value.code == "invalid_request"
    with pytest.raises(OAuthError) as exc:
        _authorize(server, method="plain")
    assert exc.value.code == "invalid_request"


def test_authorize_rejects_unknown_client_and_redirect_mismatch():
    server = _server()
    server.register_client("web-app", [CALLBACK])
    with pytest.raises(OAuthError) as exc:
        _authorize(server, client_id="ghost")
    assert exc.value.code == "unauthorized_client"
    with pytest.raises(OAuthError) as exc:
        _authorize(server, redirect_uri="http://evil.example.com/cb")
    assert exc.value.code == "invalid_request"


def test_authorize_rejects_unsupported_response_type_and_scope():
    server = _server()
    server.register_client("web-app", [CALLBACK])
    with pytest.raises(OAuthError) as exc:
        _authorize(server, response_type="token")
    assert exc.value.code == "unsupported_response_type"
    with pytest.raises(OAuthError) as exc:
        _authorize(server, scope="admin")
    assert exc.value.code == "invalid_scope"


def test_authorize_ok_returns_redirect_with_code_and_state():
    server = _server()
    server.register_client("web-app", [CALLBACK])
    verifier = generate_code_verifier()
    location = _authorize(server, challenge=generate_code_challenge(verifier))
    assert location.startswith(CALLBACK)
    parsed = urlparse(location)
    assert parsed.netloc == "127.0.0.1:1" and parsed.path == "/callback"
    query = parse_qs(parsed.query)
    assert query["state"] == ["st-1"]
    assert query["code"] and query["code"][0] != generate_code_challenge(verifier)


def test_token_exchange_happy_path():
    server = _server()
    server.register_client("web-app", [CALLBACK])
    verifier = generate_code_verifier()
    code = _code_from_location(_authorize(server, challenge=generate_code_challenge(verifier)))
    tokens = server.token_exchange(
        {
            "grant_type": "authorization_code",
            "code": code,
            "client_id": "web-app",
            "redirect_uri": CALLBACK,
            "code_verifier": verifier,
        }
    )
    assert tokens["token_type"] == "Bearer"
    assert tokens["access_token"] and tokens["refresh_token"]
    assert tokens["expires_in"] > 0
    assert server.verify_access_token(tokens["access_token"]) is True


def test_token_exchange_code_one_time():
    server = _server()
    server.register_client("web-app", [CALLBACK])
    verifier = generate_code_verifier()
    code = _code_from_location(_authorize(server, challenge=generate_code_challenge(verifier)))
    params = {
        "grant_type": "authorization_code",
        "code": code,
        "client_id": "web-app",
        "redirect_uri": CALLBACK,
        "code_verifier": verifier,
    }
    assert server.token_exchange(params)["access_token"]
    with pytest.raises(OAuthError) as exc:
        server.token_exchange(params)
    assert exc.value.code == "invalid_grant"


def test_token_exchange_wrong_verifier_consumes_code():
    server = _server()
    server.register_client("web-app", [CALLBACK])
    verifier = generate_code_verifier()
    code = _code_from_location(_authorize(server, challenge=generate_code_challenge(verifier)))
    params = {
        "grant_type": "authorization_code",
        "code": code,
        "client_id": "web-app",
        "redirect_uri": CALLBACK,
        "code_verifier": verifier + "x",
    }
    with pytest.raises(OAuthError) as exc:
        server.token_exchange(params)
    assert exc.value.code == "invalid_grant"
    # 失败也消耗授权码（防暴力重放）
    params["code_verifier"] = verifier
    with pytest.raises(OAuthError):
        server.token_exchange(params)


def test_token_exchange_expired_code():
    server = _server()
    server.register_client("web-app", [CALLBACK])
    verifier = generate_code_verifier()
    code = _code_from_location(_authorize(server, challenge=generate_code_challenge(verifier)))
    # 白盒置为已过期（Windows time.time() 分辨率下 ttl=0 不可靠）
    server._codes[code]["expires_at"] = 0
    with pytest.raises(OAuthError) as exc:
        server.token_exchange(
            {
                "grant_type": "authorization_code",
                "code": code,
                "client_id": "web-app",
                "redirect_uri": CALLBACK,
                "code_verifier": verifier,
            }
        )
    assert exc.value.code == "invalid_grant"


def test_token_exchange_unsupported_grant_and_refresh_grant():
    server = _server()
    server.register_client("web-app", [CALLBACK])
    with pytest.raises(OAuthError) as exc:
        server.token_exchange({"grant_type": "password", "username": "a", "password": "b"})
    assert exc.value.code == "unsupported_grant_type"
    verifier = generate_code_verifier()
    code = _code_from_location(_authorize(server, challenge=generate_code_challenge(verifier)))
    tokens = server.token_exchange(
        {
            "grant_type": "authorization_code",
            "code": code,
            "client_id": "web-app",
            "redirect_uri": CALLBACK,
            "code_verifier": verifier,
        }
    )
    refreshed = server.token_exchange(
        {"grant_type": "refresh_token", "refresh_token": tokens["refresh_token"], "client_id": "web-app"}
    )
    assert refreshed["access_token"] and refreshed["refresh_token"] != tokens["refresh_token"]
    assert server.verify_access_token(refreshed["access_token"]) is True
    # refresh token 轮换：旧 refresh 作废
    with pytest.raises(OAuthError) as exc:
        server.token_exchange(
            {"grant_type": "refresh_token", "refresh_token": tokens["refresh_token"], "client_id": "web-app"}
        )
    assert exc.value.code == "invalid_grant"


def test_token_service_jwt_mode():
    token_service = TokenService(secret=SECRET)
    server = _server(token_service=token_service)
    server.register_client("web-app", [CALLBACK])
    verifier = generate_code_verifier()
    code = _code_from_location(_authorize(server, challenge=generate_code_challenge(verifier)))
    tokens = server.token_exchange(
        {
            "grant_type": "authorization_code",
            "code": code,
            "client_id": "web-app",
            "redirect_uri": CALLBACK,
            "code_verifier": verifier,
        }
    )
    assert token_service.verify_access(tokens["access_token"]) == "web-app"
    assert server.verify_access_token(tokens["access_token"]) is True


def test_verify_access_token_rejects_unknown():
    server = _server()
    server.register_client("web-app", [CALLBACK])
    assert server.verify_access_token("bogus-token") is False
    assert server.verify_access_token("") is False


def test_protected_resource_and_server_metadata():
    server = _server()
    meta = server.protected_resource_metadata()
    assert meta["resource"] == "http://rs.example.com/mcp"
    assert meta["authorization_servers"] == ["http://as.example.com"]
    info = server.authorization_server_metadata()
    assert info["authorization_endpoint"] == "http://as.example.com/oauth/authorize"
    assert info["token_endpoint"] == "http://as.example.com/oauth/token"
    assert info["code_challenge_methods_supported"] == ["S256"]
    assert "authorization_code" in info["grant_types_supported"]
    assert "mcp" in info["scopes_supported"]


# ---------------------------------------------------------------------------
# credentials 契约（OAuthTokenStore：只存环境变量名引用）
# ---------------------------------------------------------------------------


def test_token_store_contract(tmp_path):
    env: dict[str, str] = {}
    store = OAuthTokenStore(root=tmp_path, env=env)
    with pytest.raises(ValueError):
        store.set_token("srv", "")
    reference = store.set_token("srv", "tok-123")
    assert reference.startswith("env:AGENT_CLUSTER_MCP_TOKEN_")
    index = json.loads((tmp_path / "oauth_tokens.json").read_text(encoding="utf-8"))
    assert index["srv"] == reference
    assert "tok-123" not in (tmp_path / "oauth_tokens.json").read_text(encoding="utf-8")
    assert store.resolve_token("srv") == "tok-123"
    assert env[reference[4:]] == "tok-123"
    with pytest.raises(KeyError):
        store.resolve_token("ghost")
    store.revoke("srv")
    with pytest.raises(KeyError):
        store.resolve_token("srv")
    assert reference[4:] not in env


# ---------------------------------------------------------------------------
# serve 端点（RFC 8844 挂载，公开免认证）
# ---------------------------------------------------------------------------


def test_well_known_protected_resource_endpoint(tmp_path, monkeypatch):
    port, workbench, httpd = _make_server(tmp_path, monkeypatch)
    try:
        status, body = _request(port, "GET", "/.well-known/oauth-protected-resource")
        assert status == 200, body
        data = body["data"]
        assert data["resource"] == "http://127.0.0.1:8765"
        assert data["authorization_servers"] == ["http://127.0.0.1:8765"]
    finally:
        httpd.shutdown()


def test_authorization_server_metadata_endpoint(tmp_path, monkeypatch):
    port, workbench, httpd = _make_server(tmp_path, monkeypatch)
    try:
        status, body = _request(port, "GET", "/.well-known/oauth-authorization-server")
        assert status == 200, body
        data = body["data"]
        assert data["authorization_endpoint"] == "http://127.0.0.1:8765/oauth/authorize"
        assert data["token_endpoint"] == "http://127.0.0.1:8765/oauth/token"
        assert "S256" in data["code_challenge_methods_supported"]
    finally:
        httpd.shutdown()


def test_oauth_authorize_endpoint_redirects_with_code(tmp_path, monkeypatch):
    port, workbench, httpd = _make_server(tmp_path, monkeypatch)
    try:
        workbench.oauth.register_client("web-app", [CALLBACK])
        verifier = generate_code_verifier()
        query = _authorize_query(challenge=generate_code_challenge(verifier))
        status, location = _request(port, "GET", f"/oauth/authorize?{query}", follow_redirects=False)
        assert status == 302
        parsed = urlparse(location)
        assert parsed.netloc == "127.0.0.1:1"
        query_params = parse_qs(parsed.query)
        assert query_params["state"] == ["st-1"]
        assert query_params["code"]
    finally:
        httpd.shutdown()


def test_oauth_authorize_endpoint_accepts_post_form(tmp_path, monkeypatch):
    port, workbench, httpd = _make_server(tmp_path, monkeypatch)
    try:
        workbench.oauth.register_client("web-app", [CALLBACK])
        verifier = generate_code_verifier()
        form = {
            "response_type": "code",
            "client_id": "web-app",
            "redirect_uri": CALLBACK,
            "code_challenge": generate_code_challenge(verifier),
            "code_challenge_method": "S256",
            "state": "st-post",
        }
        status, location = _request(port, "POST", "/oauth/authorize", form, form=True, follow_redirects=False)
        assert status == 302
        assert parse_qs(urlparse(location).query)["state"] == ["st-post"]
    finally:
        httpd.shutdown()


def test_oauth_authorize_endpoint_rejects_bad_challenge(tmp_path, monkeypatch):
    port, workbench, httpd = _make_server(tmp_path, monkeypatch)
    try:
        workbench.oauth.register_client("web-app", [CALLBACK])
        query = _authorize_query(challenge="x")
        # 已注册回调：错误走重定向（RFC 6749 §4.1.2.1）
        status, location = _request(port, "GET", f"/oauth/authorize?{query}", follow_redirects=False)
        assert status == 302
        assert parse_qs(urlparse(location).query)["error"] == ["invalid_request"]
        # 未注册回调：400 JSON 错误
        evil = "http://evil.example.com/cb"
        status, body = _request(port, "GET", f"/oauth/authorize?{_authorize_query(redirect_uri=evil, challenge='x')}")
        assert status == 400
        assert body["error"] == "invalid_request"
    finally:
        httpd.shutdown()


def test_oauth_token_endpoint_exchange_and_one_time(tmp_path, monkeypatch):
    port, workbench, httpd = _make_server(tmp_path, monkeypatch)
    try:
        workbench.oauth.register_client("web-app", [CALLBACK])
        verifier = generate_code_verifier()
        query = _authorize_query(challenge=generate_code_challenge(verifier))
        status, location = _request(port, "GET", f"/oauth/authorize?{query}", follow_redirects=False)
        code = parse_qs(urlparse(location).query)["code"][0]
        form = {
            "grant_type": "authorization_code",
            "code": code,
            "client_id": "web-app",
            "redirect_uri": CALLBACK,
            "code_verifier": verifier,
        }
        status, body = _request(port, "POST", "/oauth/token", form, form=True)
        assert status == 200, body
        tokens = body["data"]
        assert tokens["token_type"] == "Bearer"
        assert workbench.oauth.verify_access_token(tokens["access_token"]) is True
        status, body = _request(port, "POST", "/oauth/token", form, form=True)
        assert status == 400
        assert body["error"] == "invalid_grant"
    finally:
        httpd.shutdown()


def test_oauth_token_refresh_via_http(tmp_path, monkeypatch):
    port, workbench, httpd = _make_server(tmp_path, monkeypatch)
    try:
        workbench.oauth.register_client("web-app", [CALLBACK])
        verifier = generate_code_verifier()
        query = _authorize_query(challenge=generate_code_challenge(verifier))
        status, location = _request(port, "GET", f"/oauth/authorize?{query}", follow_redirects=False)
        code = parse_qs(urlparse(location).query)["code"][0]
        status, body = _request(
            port,
            "POST",
            "/oauth/token",
            {
                "grant_type": "authorization_code",
                "code": code,
                "client_id": "web-app",
                "redirect_uri": CALLBACK,
                "code_verifier": verifier,
            },
            form=True,
        )
        refresh_token = body["data"]["refresh_token"]
        status, body = _request(
            port,
            "POST",
            "/oauth/token",
            {"grant_type": "refresh_token", "refresh_token": refresh_token, "client_id": "web-app"},
            form=True,
        )
        assert status == 200, body
        assert workbench.oauth.verify_access_token(body["data"]["access_token"]) is True
    finally:
        httpd.shutdown()


def test_oauth_endpoints_public_when_auth_enabled(tmp_path, monkeypatch):
    auth = LocalAuthProvider({"alice": "pw-1"})
    port, workbench, httpd = _make_server(tmp_path, monkeypatch, auth_provider=auth, auth_secret=SECRET)
    try:
        status, body = _request(port, "GET", "/.well-known/oauth-protected-resource")
        assert status == 200
        status, body = _request(port, "GET", "/.well-known/oauth-authorization-server")
        assert status == 200
        status, _ = _request(port, "GET", "/api/v1/users")
        assert status == 401
    finally:
        httpd.shutdown()


# ---------------------------------------------------------------------------
# 远程 MCP 服务器 OAuth 全流程（mock 服务器 = stdlib http.server）
# ---------------------------------------------------------------------------


class MockMCPHandler(BaseHTTPRequestHandler):
    """假远程 MCP 服务器：RFC 8844 元数据 + OAuth 端点 + 需 Bearer 的 JSON-RPC。"""

    oauth: OAuthAuthorizationServer = None
    base_url = ""
    authorize_count = 0
    break_state = False

    def log_message(self, format, *args):  # noqa: A002
        return

    def _json(self, status, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _redirect(self, location):
        self.send_response(302)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/.well-known/oauth-protected-resource":
            return self._json(200, self.oauth.protected_resource_metadata())
        if parsed.path == "/.well-known/oauth-authorization-server":
            return self._json(200, self.oauth.authorization_server_metadata())
        if parsed.path == "/oauth/authorize":
            type(self).authorize_count += 1
            params = {key: values[0] for key, values in parse_qs(parsed.query).items()}
            try:
                location = self.oauth.authorize_redirect(params)
            except OAuthError as exc:
                target = params.get("redirect_uri", "")
                if target and self.oauth.can_redirect_to(params.get("client_id", ""), target):
                    return self._redirect(self.oauth.error_redirect(target, exc.code, exc.description))
                return self._json(400, {"error": exc.code, "error_description": exc.description})
            if type(self).break_state:
                loc_parts = urlparse(location)
                qs = parse_qs(loc_parts.query)
                qs["state"] = [qs["state"][0] + "-tampered"]
                location = f"{loc_parts.scheme}://{loc_parts.netloc}{loc_parts.path}?{urlencode({k: v[0] for k, v in qs.items()})}"
            return self._redirect(location)
        return self._json(404, {"error": "not_found"})

    def do_POST(self):  # noqa: N802
        parsed = urlparse(self.path)
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length).decode("utf-8", errors="replace") if length else ""
        if parsed.path == "/oauth/token":
            params = {key: values[0] for key, values in parse_qs(raw).items()}
            try:
                tokens = self.oauth.token_exchange(params)
            except OAuthError as exc:
                return self._json(400, {"error": exc.code, "error_description": exc.description})
            return self._json(200, tokens)
        if parsed.path == "/mcp":
            auth = self.headers.get("Authorization", "")
            token = auth[7:] if auth.lower().startswith("bearer ") else ""
            if not token or not self.oauth.verify_access_token(token):
                return self._json(
                    401,
                    {"jsonrpc": "2.0", "error": {"code": -32001, "message": "unauthorized"}, "id": None},
                )
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                return self._json(
                    400,
                    {"jsonrpc": "2.0", "error": {"code": -32700, "message": "parse error"}, "id": None},
                )
            method = msg.get("method")
            if method == "initialize":
                result = {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "mock-remote", "version": "1.0"},
                }
            elif method == "tools/list":
                result = {
                    "tools": [
                        {
                            "name": "echo",
                            "description": "echo text",
                            "inputSchema": {
                                "type": "object",
                                "properties": {"text": {"type": "string"}},
                                "required": ["text"],
                            },
                        }
                    ]
                }
            else:
                result = {}
            return self._json(200, {"jsonrpc": "2.0", "id": msg.get("id"), "result": result})
        return self._json(404, {"error": "not_found"})


@pytest.fixture()
def mock_mcp_server():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), MockMCPHandler)
    port = httpd.server_address[1]
    base = f"http://127.0.0.1:{port}"
    oauth = OAuthAuthorizationServer(
        issuer=base,
        resource=f"{base}/mcp",
        authorization_servers=[base],
    )
    oauth.register_client("mock-client", ["http://127.0.0.1:0/callback"], name="mock 客户端")
    MockMCPHandler.oauth = oauth
    MockMCPHandler.base_url = base
    MockMCPHandler.authorize_count = 0
    MockMCPHandler.break_state = False
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield {"base": base, "oauth": oauth, "httpd": httpd}
    httpd.shutdown()


def _oauth_client():
    return MCPOAuthClient(client_id="mock-client", redirect_uri="http://127.0.0.1:0/callback")


async def test_remote_mcp_oauth_full_flow(mock_mcp_server, tmp_path):
    env: dict[str, str] = {}
    store = OAuthTokenStore(root=tmp_path, env=env)
    client = StreamableHTTPMCPClient(
        "remote",
        f"{mock_mcp_server['base']}/mcp",
        oauth=_oauth_client(),
        oauth_token_store=store,
    )
    await client.connect()
    tools = await client.list_tools()
    assert [tool["name"] for tool in tools] == ["echo"]
    assert client.token
    assert mock_mcp_server["oauth"].verify_access_token(client.token) is True
    # credentials 契约：落盘只存环境变量名引用，token 不出现明文
    raw = (tmp_path / "oauth_tokens.json").read_text(encoding="utf-8")
    assert client.token not in raw
    index = json.loads(raw)
    assert index["remote"].startswith("env:AGENT_CLUSTER_MCP_TOKEN_")
    assert store.resolve_token("remote") == client.token
    assert env[index["remote"][4:]] == client.token


async def test_remote_mcp_oauth_reuses_stored_token(mock_mcp_server, tmp_path):
    env: dict[str, str] = {}
    store = OAuthTokenStore(root=tmp_path, env=env)
    first = StreamableHTTPMCPClient(
        "remote",
        f"{mock_mcp_server['base']}/mcp",
        oauth=_oauth_client(),
        oauth_token_store=store,
    )
    await first.connect()
    token = first.token
    assert MockMCPHandler.authorize_count == 1
    second = StreamableHTTPMCPClient(
        "remote",
        f"{mock_mcp_server['base']}/mcp",
        oauth=_oauth_client(),
        oauth_token_store=store,
    )
    await second.connect()
    assert second.token == token
    assert MockMCPHandler.authorize_count == 1
    assert [tool["name"] for tool in await second.list_tools()] == ["echo"]


async def test_remote_mcp_oauth_rejects_state_tampering(mock_mcp_server):
    MockMCPHandler.break_state = True
    client = StreamableHTTPMCPClient(
        "remote",
        f"{mock_mcp_server['base']}/mcp",
        oauth=_oauth_client(),
    )
    with pytest.raises(MCPError, match="state"):
        await client.connect()


async def test_remote_mcp_requires_bearer_without_oauth(mock_mcp_server):
    client = StreamableHTTPMCPClient("remote", f"{mock_mcp_server['base']}/mcp")
    with pytest.raises(MCPError, match="401"):
        await client.connect()
