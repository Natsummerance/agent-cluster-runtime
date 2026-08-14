"""MCP 轻量 stdio 客户端（v0.2，T11.8 扩展 resources）：JSON-RPC 2.0 逐行协议，零新依赖。

- ``StdioMCPClient``：spawn 子进程 → ``initialize`` → ``notifications/initialized``
  → ``tools/list`` → ``tools/call`` / ``resources/list`` → ``resources/read``
  （新行分隔 JSON-RPC 2.0，参考 goose/openclaw 的 stdio 模式）。
- ``register_mcp_tools(registry, client, server_name)``：把发现到的 MCP 工具注册为
  ``mcp_<server>_<tool>``，权限一律 ``dangerous``（外部工具不可信，需人工审批）。
- ``register_mcp_resource_tool(registry, client, server_name)``：注册外部资源读取
  工具 ``mcp_<server>_read_resource``（dangerous；服务器不支持 resources 时跳过）。

设计说明：
- MCP stdio 传输 = 每行一个 JSON-RPC 2.0 消息（非 LSP 的 Content-Length 分帧）。
- 服务器可能推送无 id 的通知（如进度），读取时跳过。
- 超时（缺省 30s）：initialize / 每次 call 单独 ``asyncio.wait_for``。
- 官方 ``mcp`` 包为 optional extra；本实现不依赖它即可与任意 MCP server 通信。
"""

from __future__ import annotations

import asyncio
import base64
import json
import secrets
import urllib.error
import urllib.request
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

from agent_cluster.oauth_mcp import generate_code_challenge, generate_code_verifier
from agent_cluster.tools import (
    MCP_TOOL_PREFIX,
    ToolCall,
    ToolError,
    ToolPermission,
    ToolResult,
    ToolSession,
    ToolSpec,
)

__all__ = [
    "MCPError",
    "MCPOAuthClient",
    "StdioMCPClient",
    "StreamableHTTPMCPClient",
    "register_mcp_resource_tool",
    "register_mcp_tools",
    "parse_server_command",
    "parse_http_server",
]

DEFAULT_MCP_TIMEOUT = 30.0
PROTOCOL_VERSION = "2025-03-26"


class MCPError(Exception):
    """MCP 客户端错误（连接失败、RPC 错误、超时、工具调用失败）。"""


def _split_command(command: str) -> list[str]:
    """引号感知的命令行分词（Windows 安全：不把反斜杠当转义符）。

    支持单/双引号包裹含空格的参数；未闭合引号抛 MCPError。
    """
    tokens: list[str] = []
    current: list[str] = []
    quote: str | None = None
    for char in command:
        if quote is not None:
            if char == quote:
                quote = None
            else:
                current.append(char)
        elif char in ("'", '"'):
            quote = char
        elif char.isspace():
            if current:
                tokens.append("".join(current))
                current = []
        else:
            current.append(char)
    if quote is not None:
        raise MCPError("MCP 命令参数存在未闭合的引号")
    if current:
        tokens.append("".join(current))
    return tokens


def parse_server_command(spec: str) -> tuple[str, list[str]]:
    """把 ``name=command`` 拆成 (name, argv)；command 支持引号。

    例：``fs="npx -y @modelcontextprotocol/server-filesystem C:\\tmp"`` ->
    ``("fs", ["npx", "-y", "@modelcontextprotocol/server-filesystem", "C:\\tmp"])``。
    """
    name, _, command = spec.partition("=")
    name = name.strip()
    command = command.strip()
    if not name or not command:
        raise MCPError(f"非法 MCP 服务器参数：{spec!r}（格式 name=command）")
    return name, _split_command(command)


def parse_http_server(spec: str) -> tuple[str, str, str]:
    """解析 ``name=url[#token=...]`` -> (name, url, token)。

    - ``url`` 必须是 http(s):// 开头的 MCP Streamable HTTP 端点；
    - 静态 bearer token 经 URL fragment ``#token=...`` 传入（不落库到命令行历史）。
    """
    name, _, rest = spec.partition("=")
    name = name.strip()
    rest = rest.strip()
    if not name or not rest.startswith(("http://", "https://")):
        raise MCPError(
            f"非法 MCP HTTP 服务器参数：{spec!r}（格式 name=http(s)://host/path，可选 #token=...）"
        )
    token = ""
    if "#token=" in rest:
        rest, _, token = rest.partition("#token=")
        token = token.strip()
    return name, rest, token


class _OAuthRedirectCapture(urllib.request.HTTPRedirectHandler):
    """捕获 302 Location 而不跟随（headless 客户端：浏览器回调由客户端自解析）。"""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


class MCPOAuthClient:
    """MCP 远程服务器 OAuth 客户端（RFC 8844 + RFC 7636 PKCE S256，纯 stdlib）。

    流程：保护资源元数据发现（``/.well-known/oauth-protected-resource``） ->
    授权服务器元数据（RFC 8414）-> PKCE 授权码（捕获 302 Location，state 校验）
    -> token 交换。所有 HTTP 经 ``asyncio.to_thread`` 执行，可在事件循环内安全调用。
    """

    def __init__(
        self,
        *,
        client_id: str,
        redirect_uri: str = "http://127.0.0.1:0/callback",
        scopes: tuple[str, ...] = ("mcp",),
        timeout: float = DEFAULT_MCP_TIMEOUT,
    ) -> None:
        if not client_id:
            raise MCPError("OAuth client_id 必填")
        self.client_id = client_id
        self.redirect_uri = redirect_uri
        self.scopes = tuple(scopes)
        self.timeout = timeout

    @staticmethod
    def _origin(url: str) -> str:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise MCPError(f"非法 MCP 资源 URL：{url!r}")
        return f"{parsed.scheme}://{parsed.netloc}"

    def _get_json(self, url: str) -> dict:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
                data = json.loads(raw)
        except urllib.error.HTTPError as exc:
            raise MCPError(f"OAuth 元数据请求失败：HTTP {exc.code} {url}") from exc
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            raise MCPError(f"OAuth 元数据请求失败：{url}：{exc}") from exc
        except json.JSONDecodeError as exc:
            raise MCPError(f"OAuth 元数据响应非 JSON：{url}") from exc
        if not isinstance(data, dict):
            raise MCPError(f"OAuth 元数据响应不是 JSON 对象：{url}")
        return data

    def _authorize_capture(self, authorize_url: str, expected_state: str) -> str:
        """请求 authorize 端点并捕获 302 Location 中的授权码（校验 state 防 CSRF）。"""
        opener = urllib.request.build_opener(_OAuthRedirectCapture())
        req = urllib.request.Request(authorize_url, headers={"Accept": "application/json"})
        try:
            with opener.open(req, timeout=self.timeout):
                raise MCPError("authorize 端点未返回重定向（缺少 Location）")
        except urllib.error.HTTPError as exc:
            location = exc.headers.get("Location", "")
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            raise MCPError(f"OAuth authorize 请求失败：{exc}") from exc
        if not location:
            raise MCPError("authorize 端点未返回重定向 Location")
        parsed = urlparse(location)
        params = {key: values[0] for key, values in parse_qs(parsed.query).items()}
        if "error" in params:
            detail = f"{params.get('error')} {params.get('error_description', '')}".strip()
            raise MCPError(f"OAuth authorize 拒绝：{detail}")
        code = params.get("code", "")
        state = params.get("state", "")
        if not code:
            raise MCPError("authorize 重定向缺少 code")
        if state != expected_state:
            raise MCPError("OAuth state 校验失败（防 CSRF）")
        return code

    def _post_token(self, token_endpoint: str, form_params: dict[str, str]) -> dict:
        """向 token 端点提交表单并解析 token 响应（错误映射为 MCPError）。"""
        form = urlencode(form_params).encode("utf-8")
        req = urllib.request.Request(
            token_endpoint,
            data=form,
            method="POST",
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
                tokens = json.loads(raw)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            error_text = detail[:300]
            try:
                payload = json.loads(detail)
                if isinstance(payload, dict):
                    error_text = f"{payload.get('error', exc.code)} {payload.get('error_description', '')}".strip()
            except json.JSONDecodeError:
                pass
            raise MCPError(f"OAuth token 交换失败：{error_text}") from exc
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            raise MCPError(f"OAuth token 交换失败：{exc}") from exc
        except json.JSONDecodeError as exc:
            raise MCPError("OAuth token 响应非 JSON") from exc
        if not isinstance(tokens, dict) or not tokens.get("access_token"):
            raise MCPError("OAuth token 响应缺少 access_token")
        return tokens

    async def obtain_token(self, resource_url: str) -> dict:
        """RFC 8844 全流程，返回 token 响应（access_token/refresh_token 等）。"""
        origin = self._origin(resource_url)
        metadata = self._get_json(f"{origin}/.well-known/oauth-protected-resource")
        auth_servers = metadata.get("authorization_servers") or []
        if not auth_servers:
            raise MCPError("资源服务器未声明 authorization_servers")
        auth_server = str(auth_servers[0]).rstrip("/")
        server_meta = self._get_json(f"{auth_server}/.well-known/oauth-authorization-server")
        authorization_endpoint = str(server_meta.get("authorization_endpoint") or "")
        token_endpoint = str(server_meta.get("token_endpoint") or "")
        if not authorization_endpoint or not token_endpoint:
            raise MCPError("授权服务器元数据缺少 authorization_endpoint/token_endpoint")
        verifier = generate_code_verifier()
        challenge = generate_code_challenge(verifier)
        state = secrets.token_urlsafe(16)
        query = urlencode(
            {
                "response_type": "code",
                "client_id": self.client_id,
                "redirect_uri": self.redirect_uri,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "state": state,
                "scope": " ".join(self.scopes),
            }
        )
        code = await asyncio.to_thread(self._authorize_capture, f"{authorization_endpoint}?{query}", state)
        return self._post_token(
            token_endpoint,
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": self.redirect_uri,
                "client_id": self.client_id,
                "code_verifier": verifier,
            },
        )

    async def refresh_token(self, refresh_token: str, authorization_server: str) -> dict:
        """用 refresh token 换新 token（RFC 6749 §6，经授权服务器元数据发现端点）。"""
        auth_server = authorization_server.rstrip("/")
        server_meta = self._get_json(f"{auth_server}/.well-known/oauth-authorization-server")
        token_endpoint = str(server_meta.get("token_endpoint") or "")
        if not token_endpoint:
            raise MCPError("授权服务器元数据缺少 token_endpoint")
        return self._post_token(
            token_endpoint,
            {"grant_type": "refresh_token", "refresh_token": refresh_token, "client_id": self.client_id},
        )


class StreamableHTTPMCPClient:
    """MCP Streamable HTTP 客户端：stdlib urllib 一次性 POST，JSON-RPC 2.0。

    - 静态 bearer token（可选）；``initialize`` -> ``notifications/initialized``
      -> ``tools/list`` -> ``tools/call`` / ``resources/list`` -> ``resources/read``。
    - 响应兼容 ``application/json`` 与 ``text/event-stream``（SSE 取 ``data:`` 行）。
    - 所有请求经 ``asyncio.to_thread`` 执行，可在会话事件循环内安全调用。
    """

    def __init__(
        self,
        server_name: str,
        url: str,
        *,
        token: str = "",
        oauth: MCPOAuthClient | None = None,
        oauth_token_store: Any = None,
        timeout: float = DEFAULT_MCP_TIMEOUT,
    ) -> None:
        self.server_name = server_name
        self.url = url
        self.token = token
        self.oauth = oauth
        self.oauth_token_store = oauth_token_store
        self.timeout = timeout
        self._next_id = 0
        self.server_info: dict[str, Any] = {}
        self._connected = False

    def _post_sync(self, payload: dict) -> tuple[int, str, str]:
        """同步 POST（在 to_thread 内执行）。"""
        data = json.dumps(payload).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": PROTOCOL_VERSION,
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        req = urllib.request.Request(self.url, data=data, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                body = resp.read().decode("utf-8", errors="replace")
                return resp.status, resp.headers.get("Content-Type", ""), body
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            return exc.code, exc.headers.get("Content-Type", ""), body
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            raise MCPError(f"MCP HTTP 服务器 {self.server_name} 请求失败：{exc}") from exc

    @staticmethod
    def _parse_body(content_type: str, body: str) -> dict:
        if "text/event-stream" in (content_type or "").lower():
            for line in body.splitlines():
                line = line.strip()
                if line.startswith("data:"):
                    payload = line[5:].strip()
                    if payload:
                        try:
                            return json.loads(payload)
                        except json.JSONDecodeError as exc:
                            raise MCPError(f"MCP HTTP SSE data 载荷非 JSON：{payload[:200]!r}") from exc
            raise MCPError("MCP HTTP 服务器 SSE 响应中未找到 data 载荷")
        try:
            data = json.loads(body)
        except json.JSONDecodeError as exc:
            raise MCPError(f"MCP HTTP 服务器返回非 JSON 响应：{body[:200]!r}") from exc
        if not isinstance(data, dict):
            raise MCPError(f"MCP HTTP 响应不是 JSON 对象：{str(data)[:200]!r}")
        return data

    async def _post(self, payload: dict) -> tuple[int, str, str]:
        return await asyncio.to_thread(self._post_sync, payload)

    async def request(self, method: str, params: dict | None = None) -> dict:
        """发送一次 JSON-RPC 请求并返回 result。"""
        self._next_id += 1
        payload: dict[str, Any] = {
            "jsonrpc": "2.0",
            "id": self._next_id,
            "method": method,
            "params": params or {},
        }
        status, content_type, body = await self._post(payload)
        if status >= 400:
            raise MCPError(f"MCP HTTP {method} 失败：HTTP {status} {body[:300]}")
        result = self._parse_body(content_type, body)
        if "error" in result and result.get("error") is not None:
            err = result["error"]
            raise MCPError(f"MCP HTTP {method} RPC 错误：{err.get('message', err)}")
        if "result" not in result:
            raise MCPError(f"MCP HTTP {method} 响应缺少 result：{str(result)[:200]}")
        value = result["result"]
        return value if isinstance(value, dict) else {"value": value}

    async def ensure_oauth_token(self) -> str:
        """获取远程 MCP 服务器 OAuth token（RFC 8844；结果按 credentials 契约存储）。

        优先级：已有静态 token -> credentials 存储引用（复用）-> OAuth 流程。
        全部不可用时 fail loud。
        """
        if self.token:
            return self.token
        if self.oauth_token_store is not None:
            try:
                self.token = self.oauth_token_store.resolve_token(self.server_name)
                return self.token
            except KeyError:
                pass
        if self.oauth is None:
            raise MCPError(
                f"MCP HTTP 服务器 {self.server_name} 需要 OAuth 授权，但未配置 oauth（静态 token 为空）"
            )
        tokens = await self.oauth.obtain_token(self.url)
        token = str(tokens.get("access_token") or "")
        if not token:
            raise MCPError("OAuth 流程未返回 access_token")
        self.token = token
        if self.oauth_token_store is not None:
            self.oauth_token_store.set_token(self.server_name, self.token)
        return self.token

    async def connect(self) -> dict[str, Any]:
        """完成 initialize 握手，返回 serverInfo。

        未配置静态 token 且配置了 OAuth（或凭据存储）时自动执行 RFC 8844 流程。
        """
        if self._connected:
            return self.server_info
        if not self.token and (self.oauth is not None or self.oauth_token_store is not None):
            await self.ensure_oauth_token()
        info = await self.request(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "agent-cluster", "version": "0.7.0"},
            },
        )
        self.server_info = dict(info or {})
        # notifications/initialized（无 id 的通知，错误可忽略）
        try:
            await self._post({"jsonrpc": "2.0", "method": "notifications/initialized"})
        except MCPError:
            pass
        self._connected = True
        return self.server_info

    async def list_tools(self) -> list[dict]:
        result = await self.request("tools/list")
        return list(result.get("tools") or [])

    async def list_resources(self) -> list[dict]:
        result = await self.request("resources/list")
        return list(result.get("resources") or [])

    async def read_resource(self, uri: str) -> dict:
        return await self.request("resources/read", {"uri": uri})

    async def call_tool(self, name: str, args: dict | None = None) -> dict:
        return await self.request("tools/call", {"name": name, "arguments": dict(args or {})})


class StdioMCPClient:
    """MCP stdio 客户端：逐行 JSON-RPC 2.0，异步子进程通信。"""

    def __init__(
        self,
        server_name: str,
        argv: list[str],
        *,
        timeout: float = DEFAULT_MCP_TIMEOUT,
    ) -> None:
        self.server_name = server_name
        self.argv = list(argv)
        self.timeout = timeout
        self._process: asyncio.subprocess.Process | None = None
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._next_id = 0
        self._connected = False
        self._pending: dict[int, asyncio.Future] = {}
        self._listener: asyncio.Task | None = None

    async def connect(self) -> dict[str, Any]:
        """spawn 子进程并完成 initialize 握手，返回 serverInfo。"""
        if self._connected:
            return self.server_info
        try:
            self._process = await asyncio.create_subprocess_exec(
                *self.argv,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError as exc:
            raise MCPError(f"MCP 服务器 {self.server_name} 启动失败：{exc}") from exc
        self._reader = self._process.stdout
        self._writer = self._process.stdin
        self._listener = asyncio.create_task(self._read_loop())
        result = await self.request(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "agent-cluster", "version": "0.1.0"},
            },
        )
        self.server_info = dict(result.get("serverInfo") or {})
        await self.notify("notifications/initialized")
        self._connected = True
        return self.server_info

    async def close(self) -> None:
        """关闭子进程与监听任务（幂等）。"""
        if self._listener is not None:
            self._listener.cancel()
            self._listener = None
        if self._writer is not None:
            try:
                self._writer.close()
            except Exception:  # noqa: BLE001
                pass
            self._writer = None
        if self._process is not None and self._process.returncode is None:
            try:
                self._process.terminate()
                await asyncio.wait_for(self._process.wait(), timeout=5)
            except (ProcessLookupError, asyncio.TimeoutError):
                self._process.kill()
        self._process = None
        self._connected = False

    async def list_tools(self) -> list[dict[str, Any]]:
        """调用 tools/list，返回 [{name, description, inputSchema}]。"""
        result = await self.request("tools/list", {})
        tools = result.get("tools") or []
        return [dict(tool) for tool in tools if isinstance(tool, dict)]

    async def call_tool(self, name: str, arguments: dict | None = None) -> dict[str, Any]:
        """调用 tools/call，返回 {content: [{type, text}], isError}。"""
        return await self.request("tools/call", {"name": name, "arguments": dict(arguments or {})})

    async def list_resources(self) -> list[dict[str, Any]]:
        """调用 resources/list，返回 [{uri, name, description, mimeType}]。"""
        result = await self.request("resources/list", {})
        resources = result.get("resources") or []
        return [dict(resource) for resource in resources if isinstance(resource, dict)]

    async def read_resource(self, uri: str) -> dict[str, Any]:
        """调用 resources/read，返回 {contents: [{uri, mimeType, text|blob}]}。"""
        return await self.request("resources/read", {"uri": uri})

    # ------------------------------------------------------------------
    # 底层 JSON-RPC
    # ------------------------------------------------------------------

    async def request(self, method: str, params: dict) -> dict[str, Any]:
        """发送请求并等待同 id 的响应（超时抛 MCPError）。"""
        if self._writer is None:
            raise MCPError(f"MCP 服务器 {self.server_name} 未连接")
        self._next_id += 1
        request_id = self._next_id
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        message = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params,
        }
        self._write_line(message)
        try:
            response = await asyncio.wait_for(future, timeout=self.timeout)
        except asyncio.TimeoutError as exc:
            self._pending.pop(request_id, None)
            raise MCPError(
                f"MCP 请求超时（{self.timeout}s）：{self.server_name} {method}"
            ) from exc
        if "error" in response and response["error"] is not None:
            error = response["error"]
            raise MCPError(
                f"MCP RPC 错误（{self.server_name} {method}）："
                f"{error.get('code')}: {error.get('message')}"
            )
        return dict(response.get("result") or {})

    async def notify(self, method: str, params: dict | None = None) -> None:
        """发送通知（无 id，不等待响应）。"""
        if self._writer is None:
            raise MCPError(f"MCP 服务器 {self.server_name} 未连接")
        self._write_line({"jsonrpc": "2.0", "method": method, "params": params or {}})

    def _write_line(self, message: dict) -> None:
        if self._writer is None:
            return
        self._writer.write((json.dumps(message) + "\n").encode("utf-8"))

    async def _read_loop(self) -> None:
        """持续读取服务器输出：解析响应投递 Future，通知跳过，异常关闭。"""
        try:
            while self._reader is not None:
                line = await self._reader.readline()
                if not line:
                    break
                try:
                    message = json.loads(line.decode("utf-8"))
                except (json.JSONDecodeError, UnicodeDecodeError):
                    continue
                if not isinstance(message, dict) or "id" not in message:
                    continue  # 通知 / 心跳，忽略
                request_id = message.get("id")
                if isinstance(request_id, int) and request_id in self._pending:
                    future = self._pending.pop(request_id)
                    if not future.done():
                        future.set_result(message)
        except asyncio.CancelledError:
            pass
        except Exception:  # noqa: BLE001 —— 服务器崩溃：结束监听
            pass
        finally:
            for future in self._pending.values():
                if not future.done():
                    future.set_exception(
                        MCPError(f"MCP 服务器 {self.server_name} 连接已关闭")
                    )
            self._pending.clear()


def _format_mcp_content(content: list[dict] | None) -> str:
    """把 MCP 响应 content 数组格式化为文本。"""
    if not content:
        return "(无内容)"
    parts: list[str] = []
    for item in content:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "text":
            parts.append(str(item.get("text", "")))
        elif item.get("type") == "resource":
            parts.append(f"resource: {item.get('uri', '')}")
        else:
            parts.append(json.dumps(item, ensure_ascii=False, default=str))
    return "\n".join(part for part in parts if part) or "(空内容)"


def _format_mcp_resource_contents(contents: list[dict] | None) -> str:
    """把 resources/read 的 contents 数组格式化为文本（text 直接展示，blob 解码）。"""
    if not contents:
        return "(无内容)"
    parts: list[str] = []
    for item in contents:
        if not isinstance(item, dict):
            continue
        uri = str(item.get("uri") or "")
        text = item.get("text")
        if isinstance(text, str):
            parts.append(f"resource: {uri}\n{text}")
            continue
        blob = item.get("blob")
        if isinstance(blob, str):
            try:
                decoded = base64.b64decode(blob).decode("utf-8", errors="replace")
            except Exception:  # noqa: BLE001
                decoded = f"（blob 解码失败：{len(blob)} 字符 base64）"
            parts.append(f"resource: {uri} (blob)\n{decoded}")
            continue
        parts.append(f"resource: {uri}\n{json.dumps(item, ensure_ascii=False, default=str)}")
    return "\n\n".join(part for part in parts if part) or "(空内容)"


async def register_mcp_tools(
    registry: Any,
    client: StdioMCPClient,
    server_name: str,
) -> list[str]:
    """把 MCP 服务器发现的工具注册为 ``mcp_<server>_<tool>``（dangerous）。

    返回注册的工具名列表；工具 handler 转发到 ``client.call_tool``。
    MCP 外部工具一律视为不可信，权限 = ``dangerous``（走人工审批门）。
    """
    tools = await client.list_tools()
    registered: list[str] = []
    for tool in tools:
        tool_name = str(tool.get("name") or "")
        if not tool_name:
            continue
        full_name = f"{MCP_TOOL_PREFIX}{server_name}_{tool_name}"

        async def handler(session: ToolSession, args: dict, _name: str = tool_name) -> dict[str, Any]:
            result = await client.call_tool(_name, args)
            output = _format_mcp_content(result.get("content"))
            if result.get("isError"):
                return {"ok": False, "output": output, "error": f"MCP 工具 {_name} 返回错误"}
            return {"ok": True, "output": output}

        registry.register(
            ToolSpec(
                name=full_name,
                description=(
                    f"MCP 工具（服务器 {server_name}）："
                    f"{tool.get('description') or tool_name}"
                ),
                permission=ToolPermission.DANGEROUS,
                parameters=dict(tool.get("inputSchema") or {}),
                handler=handler,
                mcp_server=server_name,
            )
        )
        registered.append(full_name)
    return registered


async def register_mcp_resource_tool(
    registry: Any,
    client: StdioMCPClient,
    server_name: str,
) -> str | None:
    """注册 MCP 外部资源读取工具 ``mcp_<server>_read_resource``（dangerous）。

    - 服务器不支持 resources（``resources/list`` 抛 MCPError）时返回 None 跳过，
      不阻断流程。
    - 注册成功返回工具名；资源读取走人工审批门（--yes 自动拒绝）。
    """
    try:
        resources = await client.list_resources()
    except MCPError:
        return None
    tool_name = f"{MCP_TOOL_PREFIX}{server_name}_read_resource"

    async def handler(session: ToolSession, args: dict) -> dict[str, Any]:
        uri = str(args.get("uri") or "").strip()
        if not uri:
            raise ToolError("read_resource 需要 uri 参数")
        result = await client.read_resource(uri)
        return {"ok": True, "output": _format_mcp_resource_contents(result.get("contents"))}

    known = ", ".join(str(resource.get("uri")) for resource in resources[:10]) or "（服务器未声明资源）"
    registry.register(
        ToolSpec(
            name=tool_name,
            description=(
                f"MCP 外部资源读取工具（服务器 {server_name}）：按 uri 读取 MCP resource。"
                f"已知资源：{known}"
            ),
            permission=ToolPermission.DANGEROUS,
            parameters={"type": "object", "properties": {"uri": {"type": "string"}}, "required": ["uri"]},
            handler=handler,
            mcp_server=server_name,
        )
    )
    return tool_name
