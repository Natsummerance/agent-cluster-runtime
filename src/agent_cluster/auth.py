"""认证层（v0.7 Task 14.10）：local/LDAP/OIDC 三 provider + JWT token 服务。

- ``LocalAuthProvider``：静态用户名/密码表（来自配置或环境变量），空表即禁用。
- ``LdapAuthProvider``：经 ``ldap3`` 绑定校验（enterprise extras，缺失 fail loud）。
- ``OidcAuthProvider``：把 ``password`` 视为签名 id_token（``pyjwt``）校验，
  subject 即用户 id；issuer/audience/共享密钥来自配置。
- ``TokenService``：HS256 access+refresh JWT（``pyjwt`` 可选缺失时 fail loud）。
- 设计约定：provider 均为纯函数式校验（无副作用、不依赖网络在构造期），
  供 serve 登录端点与接缝消费；认证启停由 ``WorkbenchServer`` 注入决定。
"""

from __future__ import annotations

import time
import uuid
from typing import Protocol

__all__ = [
    "AuthProvider",
    "LdapAuthProvider",
    "LocalAuthProvider",
    "OidcAuthProvider",
    "TokenService",
]


class AuthProvider(Protocol):
    """认证 provider 协议：authenticate(username, password) -> user_id | None。"""

    def authenticate(self, username: str, password: str) -> str | None: ...


class LocalAuthProvider:
    """静态密码表校验（配置/环境注入的 {username: password} 映射）。"""

    def __init__(self, users: dict[str, str]) -> None:
        self._users = dict(users)

    def authenticate(self, username: str, password: str) -> str | None:
        if not username or not password:
            return None
        expected = self._users.get(username)
        if expected is None or expected != password:
            return None
        return username


class LdapAuthProvider:
    """LDAP/AD 绑定校验（enterprise extras：``ldap3``）。

    ``user_dn_template`` 用 ``{username}``/``{base_dn}`` 占位渲染用户 DN 后
    auto_bind；绑定成功即认证通过，随后无条件 unbind。
    """

    def __init__(
        self,
        *,
        server: str,
        base_dn: str,
        user_dn_template: str | None = None,
        bind_template: str | None = None,
    ) -> None:
        self.server_url = server
        self.base_dn = base_dn
        template = user_dn_template or bind_template or "cn={username},{base_dn}"
        if "{username}" not in template:
            raise ValueError("user_dn_template 必须包含 {username} 占位符")
        self._template = template

    def _user_dn(self, username: str) -> str:
        return self._template.format(username=username, base_dn=self.base_dn)

    def authenticate(self, username: str, password: str) -> str | None:
        if not username or not password:
            return None
        try:
            import ldap3
        except ImportError as exc:  # pragma: no cover - 依赖缺失路径
            raise ImportError(
                "LDAP 认证需要 enterprise extras：uv pip install 'agent-cluster[enterprise]'（ldap3）"
            ) from exc
        user_dn = self._user_dn(username)
        server = ldap3.Server(self.server_url)
        connection = ldap3.Connection(server, user=user_dn, password=password, auto_bind=True)
        try:
            if not connection.bound:
                return None
            return username
        finally:
            try:
                connection.unbind()
            except Exception:  # noqa: BLE001 - unbind 失败不影响认证结果
                pass


class OidcAuthProvider:
    """OIDC id_token 校验（enterprise extras：``pyjwt``）。

    约定：``username`` = subject（sub），``password`` = 签名 id_token；
    校验 iss/aud/exp 与共享密钥签名后返回 sub。
    """

    def __init__(self, *, shared_secret: str, issuer: str, audience: str) -> None:
        self._secret = shared_secret
        self._issuer = issuer
        self._audience = audience

    def authenticate(self, username: str, password: str) -> str | None:
        if not username or not password:
            return None
        try:
            import jwt
        except ImportError as exc:  # pragma: no cover - 依赖缺失路径
            raise ImportError(
                "OIDC 认证需要 enterprise extras：uv pip install 'agent-cluster[enterprise]'（pyjwt）"
            ) from exc
        try:
            claims = jwt.decode(
                password,
                self._secret,
                algorithms=["HS256"],
                audience=self._audience,
                issuer=self._issuer,
            )
        except jwt.PyJWTError:
            return None
        if claims.get("sub") != username:
            return None
        return username


class TokenService:
    """HS256 access/refresh JWT 签发与校验（pyjwt，缺失 fail loud）。"""

    def __init__(
        self,
        *,
        secret: str,
        access_ttl_seconds: int = 900,
        refresh_ttl_seconds: int = 7 * 24 * 3600,
    ) -> None:
        if not secret or len(secret) < 16:
            raise ValueError("auth secret 至少 16 字符（--auth-secret 或 AGENT_CLUSTER_AUTH_SECRET）")
        self._secret = secret
        self._access_ttl = access_ttl_seconds
        self._refresh_ttl = refresh_ttl_seconds

    @staticmethod
    def _jwt() -> object:
        try:
            import jwt
        except ImportError as exc:  # pragma: no cover - 依赖缺失路径
            raise ImportError(
                "token 服务需要 enterprise extras：uv pip install 'agent-cluster[enterprise]'（pyjwt）"
            ) from exc
        return jwt

    def issue(self, user_id: str) -> dict[str, str]:
        jwt_mod = self._jwt()
        now = int(time.time())
        return {
            "access_token": jwt_mod.encode(
                {"sub": user_id, "type": "access", "exp": now + self._access_ttl, "jti": uuid.uuid4().hex[:12]},
                self._secret,
                algorithm="HS256",
            ),
            "refresh_token": jwt_mod.encode(
                {"sub": user_id, "type": "refresh", "exp": now + self._refresh_ttl, "jti": uuid.uuid4().hex[:12]},
                self._secret,
                algorithm="HS256",
            ),
        }

    def _decode(self, token: str, expected_type: str) -> str | None:
        jwt_mod = self._jwt()
        try:
            claims = jwt_mod.decode(token, self._secret, algorithms=["HS256"])
        except jwt_mod.PyJWTError:
            return None
        if claims.get("type") != expected_type:
            return None
        return str(claims.get("sub") or "")

    def verify_access(self, token: str) -> str | None:
        user_id = self._decode(token, "access")
        return user_id or None

    def verify_refresh(self, token: str) -> str | None:
        user_id = self._decode(token, "refresh")
        return user_id or None

    def refresh(self, refresh_token: str) -> dict[str, str]:
        user_id = self.verify_refresh(refresh_token)
        if not user_id:
            raise ValueError("无效或过期的 refresh token")
        return self.issue(user_id)
