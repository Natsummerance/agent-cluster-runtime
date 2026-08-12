"""v0.2 MCP 轻量 stdio 客户端测试：假 MCP 服务器进程验证 list/call/注册/审批。"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

from agent_cluster.mcp_client import (
    MCPError,
    StdioMCPClient,
    parse_server_command,
    register_mcp_tools,
)
from agent_cluster.tools import ToolCall, ToolPermission, ToolSession, build_default_tools

FAKE_SERVER_SRC = r'''
import sys, json

def send(msg):
    sys.stdout.write(json.dumps(msg) + "\n")
    sys.stdout.flush()

for line in sys.stdin:
    msg = json.loads(line)
    method = msg.get("method")
    if method == "initialize":
        send({"jsonrpc": "2.0", "id": msg["id"], "result": {
            "protocolVersion": "2025-03-26",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "fake", "version": "1.0"},
        }})
    elif method == "notifications/initialized":
        pass
    elif method == "tools/list":
        send({"jsonrpc": "2.0", "id": msg["id"], "result": {"tools": [
            {"name": "echo", "description": "echo text",
             "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}},
            {"name": "boom", "description": "always error",
             "inputSchema": {"type": "object", "properties": {}}},
        ]}})
    elif method == "tools/call":
        name = (msg.get("params") or {}).get("name")
        args = (msg.get("params") or {}).get("arguments", {})
        if name == "boom":
            send({"jsonrpc": "2.0", "id": msg["id"], "result": {
                "content": [{"type": "text", "text": "boom failed"}], "isError": True}})
        else:
            send({"jsonrpc": "2.0", "id": msg["id"], "result": {
                "content": [{"type": "text", "text": "ECHO:" + str(args.get("text", ""))}], "isError": False}})
'''


@pytest.fixture()
def fake_server_script(tmp_path: Path) -> Path:
    script = tmp_path / "fake_mcp_server.py"
    script.write_text(FAKE_SERVER_SRC, encoding="utf-8")
    return script


def test_parse_server_command_splits_quoted_and_unquoted():
    name, argv = parse_server_command('fs="npx -y @modelcontextprotocol/server-filesystem C:\\tmp"')
    assert name == "fs"
    assert argv == ["npx -y @modelcontextprotocol/server-filesystem C:\\tmp"]
    name2, argv2 = parse_server_command("fs=npx -y foo bar")
    assert name2 == "fs" and argv2 == ["npx", "-y", "foo", "bar"]


def test_parse_server_command_rejects_missing_name_or_command():
    with pytest.raises(MCPError, match="非法"):
        parse_server_command("no-equals")
    with pytest.raises(MCPError, match="非法"):
        parse_server_command("=cmd")


async def test_mcp_client_connect_list_and_call(fake_server_script: Path):
    client = StdioMCPClient("fake", [sys.executable, str(fake_server_script)], timeout=10)
    info = await client.connect()
    assert info["name"] == "fake"
    tools = await client.list_tools()
    names = {tool["name"] for tool in tools}
    assert names == {"echo", "boom"}
    result = await client.call_tool("echo", {"text": "hello"})
    assert result["content"][0]["text"] == "ECHO:hello"
    await client.close()


async def test_mcp_client_call_error_flag(fake_server_script: Path):
    client = StdioMCPClient("fake", [sys.executable, str(fake_server_script)], timeout=10)
    await client.connect()
    result = await client.call_tool("boom", {})
    assert result["isError"] is True
    await client.close()


async def test_mcp_client_startup_failure():
    client = StdioMCPClient("fake", ["nonexistent-command-xyz"], timeout=5)
    with pytest.raises(MCPError, match="启动失败"):
        await client.connect()


async def test_register_mcp_tools_marks_dangerous_and_forwards(fake_server_script: Path, tmp_path: Path):
    client = StdioMCPClient("fake", [sys.executable, str(fake_server_script)], timeout=10)
    await client.connect()
    registry = build_default_tools()
    registered = await register_mcp_tools(registry, client, "fake")
    assert sorted(registered) == ["mcp_fake_boom", "mcp_fake_echo"]
    spec = registry.get("mcp_fake_echo")
    assert spec.permission == ToolPermission.DANGEROUS
    assert spec.mcp_server == "fake"
    session = ToolSession(tmp_path, registry=registry)
    blocked = await session.execute(ToolCall(name="mcp_fake_echo", args={"text": "x"}))
    assert blocked.needs_approval
    result = await session.execute(ToolCall(name="mcp_fake_echo", args={"text": "hi"}), approved=True)
    assert result.ok and "ECHO:hi" in result.output
    err = await session.execute(ToolCall(name="mcp_fake_boom", args={}), approved=True)
    assert not err.ok and "boom failed" in err.output
    await client.close()
