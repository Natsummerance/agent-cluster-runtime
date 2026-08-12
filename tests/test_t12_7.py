"""T12.7 远程 MCP（Streamable HTTP）+ doctor 扩展（Node / MCP HTTP 检查）。"""

from __future__ import annotations

import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from agent_cluster.doctor import run_doctor
from agent_cluster.mcp_client import (
    MCPError,
    StreamableHTTPMCPClient,
    parse_http_server,
    register_mcp_tools,
)
from agent_cluster.tools import ToolCall, ToolPermission, ToolSession, build_default_tools

# ---------------------------------------------------------------------------
# parse_http_server
# ---------------------------------------------------------------------------


def test_parse_http_server_valid():
    name, url, token = parse_http_server("remote=https://mcp.example.com/mcp#token=abc123")
    assert name == "remote"
    assert url == "https://mcp.example.com/mcp"
    assert token == "abc123"


def test_parse_http_server_no_token():
    name, url, token = parse_http_server("remote=http://127.0.0.1:8000/mcp")
    assert token == ""
    assert url == "http://127.0.0.1:8000/mcp"


def test_parse_http_server_invalid():
    with pytest.raises(MCPError):
        parse_http_server("npx foo")
    with pytest.raises(MCPError):
        parse_http_server("=http://x")


# ---------------------------------------------------------------------------
# Streamable HTTP 客户端（假 MCP 服务器）
# ---------------------------------------------------------------------------


class _FakeMCPHandler(BaseHTTPRequestHandler):
    """JSON 响应的假 MCP Streamable HTTP 服务器。"""

    mode = "json"

    def log_message(self, format, *args):  # noqa: A002
        return

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length).decode("utf-8"))
        method = body.get("method")
        if self.mode == "sse":
            result = {"serverInfo": {"name": "fake-sse"}}
            payload = json.dumps({"jsonrpc": "2.0", "id": body.get("id"), "result": result})
            data = f"event: message\ndata: {payload}\n\n".encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        if method == "initialize":
            result = {"protocolVersion": "2025-03-26", "serverInfo": {"name": "fake", "version": "1.0"}}
        elif method == "tools/list":
            result = {
                "tools": [
                    {
                        "name": "echo",
                        "description": "回显文本",
                        "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}},
                    }
                ]
            }
        elif method == "tools/call":
            arguments = (body.get("params") or {}).get("arguments") or {}
            result = {"content": [{"type": "text", "text": f"echo:{arguments.get('text', '')}"}]}
        else:
            result = {}
        payload = json.dumps({"jsonrpc": "2.0", "id": body.get("id"), "result": result}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


class _FakeErrorHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):  # noqa: A002
        return

    def do_POST(self):  # noqa: N802
        body = b'{"error": "boom"}'
        self.send_response(500)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def _start(handler_cls, mode="json"):
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
    handler_cls.mode = mode
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, f"http://127.0.0.1:{port}/mcp"


async def test_http_client_connect_list_call():
    server, url = _start(_FakeMCPHandler)
    try:
        client = StreamableHTTPMCPClient("remote", url)
        info = await client.connect()
        assert info["serverInfo"]["name"] == "fake"
        tools = await client.list_tools()
        assert tools[0]["name"] == "echo"
        result = await client.call_tool("echo", {"text": "hi"})
        assert "echo:hi" in str(result)
    finally:
        server.shutdown()
        server.server_close()


async def test_http_client_sse_response():
    server, url = _start(_FakeMCPHandler, mode="sse")
    try:
        client = StreamableHTTPMCPClient("remote", url)
        info = await client.connect()
        assert info["serverInfo"]["name"] == "fake-sse"
    finally:
        server.shutdown()
        server.server_close()


async def test_http_client_error_raises():
    server, url = _start(_FakeErrorHandler)
    try:
        client = StreamableHTTPMCPClient("remote", url)
        with pytest.raises(MCPError):
            await client.connect()
    finally:
        server.shutdown()
        server.server_close()


def test_register_http_mcp_tools(tmp_path):
    async def main():
        server, url = _start(_FakeMCPHandler)
        try:
            client = StreamableHTTPMCPClient("remote", url)
            await client.connect()
            registry = build_default_tools()
            registered = await register_mcp_tools(registry, client, "remote")
            assert "mcp_remote_echo" in registered
            spec = registry.get("mcp_remote_echo")
            assert spec.permission == ToolPermission.DANGEROUS
            session = ToolSession(tmp_path, registry=registry)
            call = ToolCall(name="mcp_remote_echo", args={"text": "x"})
            result = await session.execute(call)
            assert result.ok is False  # 危险工具未批准 → 需审批
            assert result.needs_approval is True
        finally:
            server.shutdown()
            server.server_close()

    asyncio.run(main())


# ---------------------------------------------------------------------------
# doctor：MCP HTTP + Node 检查
# ---------------------------------------------------------------------------


def test_doctor_mcp_http_valid():
    report = run_doctor(mcp_http_servers=["remote=https://mcp.example.com/mcp"])
    check = next(item for item in report.checks if item.name == "mcp_http")
    assert check.ok is True


def test_doctor_mcp_http_invalid():
    report = run_doctor(mcp_http_servers=["not-a-url"])
    check = next(item for item in report.checks if item.name == "mcp_http")
    assert check.ok is False


def test_doctor_node_check_present():
    report = run_doctor()
    names = [item.name for item in report.checks]
    assert "node" in names


# ---------------------------------------------------------------------------
# CLI：--mcp-http 解析
# ---------------------------------------------------------------------------


def test_cli_parser_mcp_http():
    from agent_cluster.cli import build_parser

    parser = build_parser()
    args = parser.parse_args(["run", "--flow", "x.yaml", "--mcp-http", "r=https://x/y#token=t"])
    assert args.mcp_http == ["r=https://x/y#token=t"]
    args2 = parser.parse_args(["build", "--goal", "g", "--mcp-http", "r=https://x/y"])
    assert args2.mcp_http == ["r=https://x/y"]
    args3 = parser.parse_args(["chat", "--mcp-http", "r=https://x/y"])
    assert args3.mcp_http == ["r=https://x/y"]
    args4 = parser.parse_args(["doctor", "--mcp-http", "r=https://x/y"])
    assert args4.mcp_http == ["r=https://x/y"]
