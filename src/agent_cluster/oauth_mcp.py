"""OAuth MCP 授权服务器（v0.7 Task 14.14，RFC 8844 / RFC 6749 / RFC 7636，纯 stdlib）。

- ``OAuthAuthorizationServer``：授权服务器语义——客户端注册、``authorize``（PKCE
  S256 校验 + 授权码一次性）、``token``（authorization_code/refresh_token 交换）、
  RFC 8844 保护资源元数据与 RFC 8414 授权服务器元数据。
- ``OAuthTokenStore``：OAuth token 凭据存储（credentials 契约：配置面只存环境变量
  名引用，绝不落盘明文；经 ``CredentialResolver`` 解析）。
- PKCE 原语：``generate_code_verifier`` / ``generate_code_challenge`` /
  ``validate_code_verifier``（S256，``hmac.compare_digest`` 常量时间比较）。

设计约定：默认签发不透明内存 token（无第三方依赖）；注入 ``TokenService``（auth.py）
时签发 HS256 JWT（access/refresh 与 serve API 认证互通）。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote

from agent_cluster.credentials import CredentialResolver

__all__ = [
    "DEFAULT_SCOPES",
    "OAuthAuthorizationServer",
    "OAuthError",
    "OAuthTokenStore",
    "generate_code_challenge",
    "generate_code_verifier",
    "validate_code_challenge",
    "validate_code_verifier",
]

DEFAULT_SCOPES = ("mcp", "mcp/resources", "mcp/tools", "mcp/prompts")
CODE_CHARSET = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~")


class OAuthError(Exception):
    """OAuth 协议错误（code = RFC 6749 错误码，status 为建议 HTTP 状态）。"""

    def __init__(self, code: str, description: str, status: int = 400) -> None:
        super().__init__(f"{code}: {description}")
        self.code = code
        self.description = description
        self.status = status


# ---------------------------------------------------------------------------
# PKCE 原语（RFC 7636 §4）
# ---------------------------------------------------------------------------


def generate_code_verifier() -> str:
    """生成 43 字符 code_verifier（32 随机字节 base64url，无填充）。"""
    return base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode()


def generate_code_challenge(verifier: str) -> str:
    """S256 code_challenge：base64url(sha256(verifier))，无填充。"""
    digest = hashlib.sha256(verifier.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def validate_code_verifier(verifier: str, challenge: str | None) -> bool:
    """校验 code_verifier 格式（43-128 字符、unreserved 字符集）并与 challenge 匹配。

    ``challenge`` 为 None 时仅校验格式；匹配用常量时间比较。
    """
    if not verifier or not (43 <= len(verifier) <= 128):
        return False
    if any(char not in CODE_CHARSET for char in verifier):
        return False
    if challenge is None:
        return True
    return hmac.compare_digest(generate_code_challenge(verifier), challenge)


def validate_code_challenge(challenge: str, method: str) -> bool:
    """校验 code_challenge 可接受（仅 S256；43-128 字符、unreserved 字符集）。"""
    if method != "S256" or not challenge:
        return False
    if not (43 <= len(challenge) <= 128):
        return False
    return all(char in CODE_CHARSET for char in challenge)


# ---------------------------------------------------------------------------
# 授权服务器
# ---------------------------------------------------------------------------


class OAuthAuthorizationServer:
    """RFC 8844 OAuth MCP 授权服务器（内存态；线程安全；失败即 loud）。"""

    def __init__(
        self,
        *,
        issuer: str,
        resource: str,
        authorization_servers: list[str] | None = None,
        token_service: Any = None,
        code_ttl_seconds: int = 600,
        access_token_ttl: int = 3600,
        refresh_token_ttl: int = 7 * 24 * 3600,
        scopes: tuple[str, ...] = DEFAULT_SCOPES,
    ) -> None:
        self.issuer = issuer.rstrip("/")
        self.resource = resource
        self.authorization_servers = [str(url).rstrip("/") for url in (authorization_servers or [self.issuer])]
        self._token_service = token_service
        self._code_ttl = code_ttl_seconds
        self._access_ttl = access_token_ttl
        self._refresh_ttl = refresh_token_ttl
        self.scopes = tuple(scopes)
        self._clients: dict[str, dict[str, Any]] = {}
        self._codes: dict[str, dict[str, Any]] = {}
        self._access_tokens: dict[str, dict[str, Any]] = {}
        self._refresh_tokens: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()

    # -- 客户端注册 ---------------------------------------------------------

    def register_client(self, client_id: str, redirect_uris: list[str], name: str = "") -> dict:
        """注册 OAuth 客户端；重复注册 fail loud。"""
        if not client_id:
            raise ValueError("client_id 必填")
        if not redirect_uris:
            raise ValueError("client 至少需要一个 redirect_uri")
        with self._lock:
            if client_id in self._clients:
                raise ValueError(f"client {client_id!r} 已注册")
            record = {"client_id": client_id, "redirect_uris": list(redirect_uris), "name": name or client_id}
            self._clients[client_id] = record
            return dict(record)

    def get_client(self, client_id: str) -> dict:
        with self._lock:
            if client_id not in self._clients:
                raise KeyError(client_id)
            return dict(self._clients[client_id])

    def can_redirect_to(self, client_id: str, redirect_uri: str) -> bool:
        """redirect_uri 是否属于已注册客户端的合法回调（用于错误重定向决策）。"""
        with self._lock:
            client = self._clients.get(client_id)
        return bool(client and redirect_uri in client["redirect_uris"])

    @staticmethod
    def error_redirect(redirect_uri: str, code: str, description: str) -> str:
        """构造带 error 参数的重定向 Location（RFC 6749 §4.1.2.1）。"""
        separator = "&" if "?" in redirect_uri else "?"
        return f"{redirect_uri}{separator}error={quote(code)}&error_description={quote(description)}"

    # -- 元数据 -------------------------------------------------------------

    def protected_resource_metadata(self) -> dict:
        """RFC 8844 §3.1：``/.well-known/oauth-protected-resource`` 载荷。"""
        return {"resource": self.resource, "authorization_servers": list(self.authorization_servers)}

    def authorization_server_metadata(self) -> dict:
        """RFC 8414：``/.well-known/oauth-authorization-server`` 载荷。"""
        return {
            "issuer": self.issuer,
            "authorization_endpoint": f"{self.issuer}/oauth/authorize",
            "token_endpoint": f"{self.issuer}/oauth/token",
            "response_types_supported": ["code"],
            "grant_types_supported": ["authorization_code", "refresh_token"],
            "code_challenge_methods_supported": ["S256"],
            "token_endpoint_auth_methods_supported": ["none"],
            "scopes_supported": list(self.scopes),
        }

    # -- authorize（RFC 6749 §4.1.1 + RFC 7636）-------------------------------

    def authorize_redirect(self, params: dict[str, str]) -> str:
        """校验授权请求并签发一次性授权码，返回重定向 Location。

        校验：response_type=code、client 注册、redirect_uri 精确匹配、
        code_challenge（S256，43-128 字符）、scope 白名单。失败抛 ``OAuthError``。
        """
        if params.get("response_type", "") != "code":
            raise OAuthError("unsupported_response_type", "仅支持 response_type=code")
        client_id = params.get("client_id", "")
        redirect_uri = params.get("redirect_uri", "")
        with self._lock:
            client = self._clients.get(client_id)
        if client is None:
            raise OAuthError("unauthorized_client", f"未知 client_id：{client_id!r}")
        if not redirect_uri or redirect_uri not in client["redirect_uris"]:
            raise OAuthError("invalid_request", "redirect_uri 缺失或与注册值不匹配")
        challenge = params.get("code_challenge", "")
        method = params.get("code_challenge_method", "S256")
        if not validate_code_challenge(challenge, method):
            raise OAuthError("invalid_request", "code_challenge 缺失或格式非法（需 S256，43-128 字符）")
        scope = params.get("scope", "mcp")
        requested = [part for part in scope.split() if part]
        unknown = [part for part in requested if part not in self.scopes]
        if unknown:
            raise OAuthError("invalid_scope", f"不支持的 scope：{', '.join(unknown)}")
        code = secrets.token_urlsafe(24)
        state = params.get("state", "")
        with self._lock:
            self._codes[code] = {
                "client_id": client_id,
                "redirect_uri": redirect_uri,
                "code_challenge": challenge,
                "state": state,
                "scope": " ".join(requested) or "mcp",
                "expires_at": time.time() + self._code_ttl,
            }
        query = [f"code={quote(code)}"]
        if state:
            query.append(f"state={quote(state)}")
        separator = "&" if "?" in redirect_uri else "?"
        return f"{redirect_uri}{separator}{'&'.join(query)}"

    # -- token（RFC 6749 §4.1.3 / §6）----------------------------------------

    def token_exchange(self, params: dict[str, str]) -> dict[str, Any]:
        """处理 token 端点请求：authorization_code 或 refresh_token 交换。"""
        grant_type = params.get("grant_type", "")
        if grant_type == "authorization_code":
            return self._exchange_authorization_code(params)
        if grant_type == "refresh_token":
            return self._exchange_refresh_token(params)
        raise OAuthError("unsupported_grant_type", f"不支持的 grant_type：{grant_type!r}")

    def _exchange_authorization_code(self, params: dict[str, str]) -> dict[str, Any]:
        code = params.get("code", "")
        client_id = params.get("client_id", "")
        redirect_uri = params.get("redirect_uri", "")
        verifier = params.get("code_verifier", "")
        with self._lock:
            record = self._codes.pop(code, None)  # 一次性：无论成败先消费（防重放）
        if record is None:
            raise OAuthError("invalid_grant", "授权码无效或已过期")
        if record["client_id"] != client_id:
            raise OAuthError("invalid_grant", "client_id 与授权请求不一致")
        if redirect_uri and redirect_uri != record["redirect_uri"]:
            raise OAuthError("invalid_grant", "redirect_uri 与授权请求不一致")
        if record["expires_at"] < time.time():
            raise OAuthError("invalid_grant", "授权码已过期")
        if not validate_code_verifier(verifier, record["code_challenge"]):
            raise OAuthError("invalid_grant", "code_verifier 校验失败（PKCE S256）")
        return self._issue_tokens(client_id, record["scope"])

    def _exchange_refresh_token(self, params: dict[str, str]) -> dict[str, Any]:
        refresh_token = params.get("refresh_token", "")
        client_id = params.get("client_id", "")
        if self._token_service is not None:
            user_id = self._token_service.verify_refresh(refresh_token)
            if not user_id or (client_id and user_id != client_id):
                raise OAuthError("invalid_grant", "refresh_token 无效或已过期")
            tokens = self._token_service.refresh(refresh_token)
            return {
                "access_token": tokens["access_token"],
                "token_type": "Bearer",
                "expires_in": self._access_ttl,
                "refresh_token": tokens["refresh_token"],
                "scope": "mcp",
            }
        with self._lock:
            record = self._refresh_tokens.pop(refresh_token, None)  # 轮换：旧 refresh 作废
        if record is None or record["expires_at"] < time.time():
            raise OAuthError("invalid_grant", "refresh_token 无效或已过期")
        if client_id and record["client_id"] != client_id:
            raise OAuthError("invalid_grant", "client_id 与 refresh_token 不一致")
        return self._issue_tokens(record["client_id"], record["scope"])

    def _issue_tokens(self, client_id: str, scope: str) -> dict[str, Any]:
        if self._token_service is not None:
            tokens = self._token_service.issue(client_id)
            return {
                "access_token": tokens["access_token"],
                "token_type": "Bearer",
                "expires_in": self._access_ttl,
                "refresh_token": tokens["refresh_token"],
                "scope": scope,
            }
        access_token = secrets.token_urlsafe(32)
        refresh_token = secrets.token_urlsafe(40)
        now = time.time()
        with self._lock:
            self._access_tokens[access_token] = {
                "client_id": client_id,
                "scope": scope,
                "expires_at": now + self._access_ttl,
            }
            self._refresh_tokens[refresh_token] = {
                "client_id": client_id,
                "scope": scope,
                "expires_at": now + self._refresh_ttl,
            }
        return {
            "access_token": access_token,
            "token_type": "Bearer",
            "expires_in": self._access_ttl,
            "refresh_token": refresh_token,
            "scope": scope,
        }

    def verify_access_token(self, token: str) -> bool:
        """校验 access token（JWT 模式走 TokenService；默认不透明内存 token）。"""
        if not token:
            return False
        if self._token_service is not None:
            return self._token_service.verify_access(token) is not None
        with self._lock:
            record = self._access_tokens.get(token)
        return bool(record and record["expires_at"] > time.time())


# ---------------------------------------------------------------------------
# OAuth token 凭据存储（credentials 契约）
# ---------------------------------------------------------------------------


class OAuthTokenStore:
    """把远程 MCP 服务器 OAuth token 按 credentials 契约落盘：只存环境变量名引用。

    - ``{root}/oauth_tokens.json`` 只写 ``{server_name: "env:VAR_NAME"}``，绝不写明文。
    - ``set_token`` 同时把 token 注入 env（缺省 ``os.environ``；测试可注入 dict）。
    - ``resolve_token`` 经 ``CredentialResolver`` 解析；未存储 -> ``KeyError``，
      引用存在但值缺失 -> ``CredentialMissingError``（fail loud）。
    """

    def __init__(self, root: str | Path | None = None, env: dict[str, str] | None = None) -> None:
        self._env = env if env is not None else os.environ
        base = Path(root) if root else Path.home() / ".agent-cluster"
        self._file = base / "oauth_tokens.json"

    @staticmethod
    def _env_var_name(server_name: str) -> str:
        safe = re.sub(r"[^A-Za-z0-9]", "_", server_name).upper()
        return f"AGENT_CLUSTER_MCP_TOKEN_{safe}"

    def _load(self) -> dict[str, str]:
        if not self._file.exists():
            return {}
        try:
            data = json.loads(self._file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
        if not isinstance(data, dict):
            return {}
        return {str(key): str(value) for key, value in data.items()}

    def _save(self, mapping: dict[str, str]) -> None:
        self._file.parent.mkdir(parents=True, exist_ok=True)
        self._file.write_text(json.dumps(mapping, ensure_ascii=False, indent=2), encoding="utf-8")

    def set_token(self, server_name: str, token: str) -> str:
        """存储 token 引用并注入 env；返回 ``env:VAR_NAME`` 引用。空值 fail loud。"""
        if not server_name:
            raise ValueError("server_name 必填")
        if not token:
            raise ValueError("token 不能为空（不落盘明文空值）")
        name = self._env_var_name(server_name)
        reference = f"env:{name}"
        mapping = self._load()
        mapping[server_name] = reference
        self._save(mapping)
        self._env[name] = token
        return reference

    def token_ref(self, server_name: str) -> str:
        """返回已存储的环境变量引用；未存储 -> KeyError。"""
        mapping = self._load()
        if server_name not in mapping:
            raise KeyError(f"未存储 {server_name} 的 OAuth token 引用")
        return mapping[server_name]

    def resolve_token(self, server_name: str, resolver: CredentialResolver | None = None) -> str:
        """经 CredentialResolver 解析 token；未存储 -> KeyError，空值 fail loud。"""
        reference = self.token_ref(server_name)
        resolver = resolver or CredentialResolver(env=self._env)
        return resolver.resolve(reference)

    def revoke(self, server_name: str) -> None:
        """删除引用并清理注入的 env 变量。"""
        mapping = self._load()
        reference = mapping.pop(server_name, None)
        self._save(mapping)
        if reference and reference.startswith("env:"):
            self._env.pop(reference[4:], None)
