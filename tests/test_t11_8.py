"""T11.8 工具与记忆增强测试：apply_patch / http_fetch / MCP resources / AGENTS.md 注入。"""

from __future__ import annotations

import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from threading import Thread

import pytest

from agent_cluster.mcp_client import (
    MCPError,
    StdioMCPClient,
    register_mcp_resource_tool,
)
from agent_cluster.models import ClusterState, Iteration, Project
from agent_cluster.roles import RoleRegistry
from agent_cluster.runtime import ChatResponse, _tool_mode_agent_step
from agent_cluster.tools import (
    MAX_AGENTS_MD_CHARS,
    ToolCall,
    ToolError,
    ToolPermission,
    ToolSession,
    apply_patch_text,
    build_default_tools,
    load_agents_md,
)
from agent_cluster.workflow import NodeContext, WorkflowEdge, WorkflowNode, WorkflowSpec


@pytest.fixture()
def session(tmp_path: Path) -> ToolSession:
    return ToolSession(tmp_path)


# ---------------------------------------------------------------------------
# apply_patch：解析 / 多 hunk / 不匹配报错 / 越界与引用校验
# ---------------------------------------------------------------------------


def test_apply_patch_text_multi_hunk():
    text = "line1\nkeep\nline2\nkeep\nline3\n"
    patch = (
        "*** Begin Patch\n"
        "*** Update File: demo.txt\n"
        "@@ -1,5 +1,5 @@\n"
        "-line1\n"
        "+first\n"
        " keep\n"
        "@@ -3,5 +3,5 @@\n"
        "-line3\n"
        "+third\n"
        "*** End Patch\n"
    )
    updated = apply_patch_text(text, patch)
    assert updated.splitlines() == ["first", "keep", "line2", "keep", "third"]


def test_apply_patch_text_unmatched_hunk_raises_with_location():
    text = "alpha\nbeta\ngamma\n"
    patch = (
        "*** Begin Patch\n"
        "*** Update File: demo.txt\n"
        "-alpha\n"
        "+ALPHA\n"
        "-not-there\n"
        "+replacement\n"
        "*** End Patch\n"
    )
    with pytest.raises(ToolError, match="未匹配") as exc:
        apply_patch_text(text, patch)
    message = str(exc.value)
    assert "demo.txt" in message
    assert "alpha" in message  # 定位信息：首锚行


def test_apply_patch_text_pure_insert_hunk_rejected():
    patch = (
        "*** Begin Patch\n"
        "*** Update File: demo.txt\n"
        "+only-add\n"
        "*** End Patch\n"
    )
    with pytest.raises(ToolError, match="只有新增行"):
        apply_patch_text("line\n", patch)


def test_apply_patch_text_missing_markers_rejected():
    with pytest.raises(ToolError, match="Begin Patch"):
        apply_patch_text("line\n", "*** Update File: a.txt\n-line\n+new\n")
    with pytest.raises(ToolError, match="End Patch"):
        apply_patch_text("line\n", "*** Begin Patch\n*** Update File: a.txt\n-line\n+new\n")


async def test_apply_patch_tool_roundtrip_multi_hunk(session: ToolSession):
    (session.workspace_root / "a.txt").write_text("one\ntwo\nthree\n", encoding="utf-8")
    result = await session.execute(
        ToolCall(
            name="apply_patch",
            args={
                "path": "a.txt",
                "patch_text": (
                    "*** Begin Patch\n"
                    "*** Update File: a.txt\n"
                    "@@\n"
                    "-one\n"
                    "+ONE\n"
                    "@@\n"
                    "-three\n"
                    "+THREE\n"
                    "*** End Patch\n"
                ),
            },
        )
    )
    assert result.ok
    assert "2 个 hunk" in result.output
    assert (session.workspace_root / "a.txt").read_text(encoding="utf-8") == "ONE\ntwo\nTHREE\n"


async def test_apply_patch_unmatched_hunk_leaves_file_unchanged(session: ToolSession):
    """原子性：任一 hunk 未匹配即整体失败，不产生部分修改。"""
    (session.workspace_root / "a.txt").write_text("one\ntwo\n", encoding="utf-8")
    result = await session.execute(
        ToolCall(
            name="apply_patch",
            args={
                "path": "a.txt",
                "patch_text": (
                    "*** Begin Patch\n"
                    "*** Update File: a.txt\n"
                    "-one\n"
                    "+ONE\n"
                    "-missing\n"
                    "+MISSING\n"
                    "*** End Patch\n"
                ),
            },
        )
    )
    assert not result.ok
    assert "未匹配" in result.output
    assert (session.workspace_root / "a.txt").read_text(encoding="utf-8") == "one\ntwo\n"


async def test_apply_patch_path_escape_rejected(session: ToolSession, tmp_path: Path):
    outside = tmp_path.parent / "outside-t118.txt"
    outside.write_text("secret", encoding="utf-8")
    result = await session.execute(
        ToolCall(
            name="apply_patch",
            args={
                "path": str(outside),
                "patch_text": "*** Begin Patch\n*** Update File: outside-t118.txt\n-x\n+y\n*** End Patch\n",
            },
        )
    )
    assert not result.ok
    assert "越界" in result.output


async def test_apply_patch_missing_file_rejected(session: ToolSession):
    result = await session.execute(
        ToolCall(
            name="apply_patch",
            args={
                "path": "nope.txt",
                "patch_text": "*** Begin Patch\n*** Update File: nope.txt\n-x\n+y\n*** End Patch\n",
            },
        )
    )
    assert not result.ok
    assert "文件不存在" in result.output


async def test_apply_patch_reference_mismatch_rejected(session: ToolSession):
    (session.workspace_root / "a.txt").write_text("x\n", encoding="utf-8")
    result = await session.execute(
        ToolCall(
            name="apply_patch",
            args={
                "path": "a.txt",
                "patch_text": "*** Begin Patch\n*** Update File: b.txt\n-x\n+y\n*** End Patch\n",
            },
        )
    )
    assert not result.ok
    assert "不一致" in result.output


# ---------------------------------------------------------------------------
# http_fetch：审批门 / 本地假服务器 / 异常转失败 ToolResult
# ---------------------------------------------------------------------------


class _FakeHttpHandler(BaseHTTPRequestHandler):
    """本地假 HTTP 服务：普通文本与大响应（测试截断）。"""

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/big":
            body = b"x" * 300_000
        else:
            body = b"hello-fetch"
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # noqa: ANN002
        pass


@pytest.fixture()
def http_server():
    server = HTTPServer(("127.0.0.1", 0), _FakeHttpHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_http_fetch_permission_is_dangerous():
    assert build_default_tools().get("http_fetch").permission == ToolPermission.DANGEROUS
    assert build_default_tools().get("apply_patch").permission == ToolPermission.WORKSPACE_WRITE


async def test_http_fetch_requires_approval_and_succeeds(session: ToolSession, http_server: str):
    call = ToolCall(name="http_fetch", args={"url": http_server + "/"})
    blocked = await session.execute(call)
    assert blocked.needs_approval  # 出网需人工审批；--yes 自动拒绝
    result = await session.execute(call, approved=True)
    assert result.ok
    assert "hello-fetch" in result.output


async def test_http_fetch_truncates_over_max_bytes(session: ToolSession, http_server: str):
    result = await session.execute(
        ToolCall(name="http_fetch", args={"url": http_server + "/big", "max_bytes": 1024}),
        approved=True,
    )
    assert result.ok
    assert "截断" in result.output
    assert len(result.output) < 4000


async def test_http_fetch_rejects_non_http_scheme(session: ToolSession):
    result = await session.execute(
        ToolCall(name="http_fetch", args={"url": "file:///C:/Windows/win.ini"}),
        approved=True,
    )
    assert not result.ok
    assert "仅允许 http/https" in result.output


async def test_http_fetch_network_error_becomes_failed_result(session: ToolSession, monkeypatch):
    import urllib.request

    def _boom(url, timeout=None):  # noqa: ANN001, ANN002
        raise OSError("connection refused")

    monkeypatch.setattr(urllib.request, "urlopen", _boom)
    result = await session.execute(
        ToolCall(name="http_fetch", args={"url": "http://127.0.0.1:9/"}),
        approved=True,
    )
    assert not result.ok
    assert "HTTP 请求失败" in result.output
    assert result.error


# ---------------------------------------------------------------------------
# MCP resources：list / read / 外部资源读取工具注册
# ---------------------------------------------------------------------------

FAKE_SERVER_SRC = r'''
import sys, json, base64

def send(msg):
    sys.stdout.write(json.dumps(msg) + "\n")
    sys.stdout.flush()

for line in sys.stdin:
    msg = json.loads(line)
    method = msg.get("method")
    if method == "initialize":
        send({"jsonrpc": "2.0", "id": msg["id"], "result": {
            "protocolVersion": "2025-03-26",
            "capabilities": {"tools": {}, "resources": {}},
            "serverInfo": {"name": "fake", "version": "1.0"},
        }})
    elif method == "notifications/initialized":
        pass
    elif method == "tools/list":
        send({"jsonrpc": "2.0", "id": msg["id"], "result": {"tools": [
            {"name": "echo", "description": "echo text",
             "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}}},
        ]}})
    elif method == "tools/call":
        args = (msg.get("params") or {}).get("arguments", {})
        send({"jsonrpc": "2.0", "id": msg["id"], "result": {
            "content": [{"type": "text", "text": "ECHO:" + str(args.get("text", ""))}], "isError": False}})
    elif method == "resources/list":
        send({"jsonrpc": "2.0", "id": msg["id"], "result": {"resources": [
            {"uri": "note://demo/1", "name": "note1", "description": "demo note", "mimeType": "text/plain"},
        ]}})
    elif method == "resources/read":
        uri = (msg.get("params") or {}).get("uri")
        if uri == "note://demo/1":
            send({"jsonrpc": "2.0", "id": msg["id"], "result": {"contents": [
                {"uri": uri, "mimeType": "text/plain", "text": "hello resource"}]}})
        elif uri == "blob://demo/1":
            send({"jsonrpc": "2.0", "id": msg["id"], "result": {"contents": [
                {"uri": uri, "mimeType": "application/octet-stream",
                 "blob": base64.b64encode(b"blob-bytes").decode()}]}})
        else:
            send({"jsonrpc": "2.0", "id": msg["id"], "error": {"code": -32002, "message": "resource not found"}})
'''


@pytest.fixture()
def fake_server_script(tmp_path: Path) -> Path:
    script = tmp_path / "fake_mcp_server_t118.py"
    script.write_text(FAKE_SERVER_SRC, encoding="utf-8")
    return script


async def test_mcp_client_list_and_read_resources(fake_server_script: Path):
    client = StdioMCPClient("fake", [sys.executable, str(fake_server_script)], timeout=10)
    await client.connect()
    resources = await client.list_resources()
    assert resources[0]["uri"] == "note://demo/1"
    result = await client.read_resource("note://demo/1")
    assert result["contents"][0]["text"] == "hello resource"
    with pytest.raises(MCPError, match="not found"):
        await client.read_resource("note://missing")
    await client.close()


async def test_register_mcp_resource_tool_dangerous_and_forwards(fake_server_script: Path, tmp_path: Path):
    client = StdioMCPClient("fake", [sys.executable, str(fake_server_script)], timeout=10)
    await client.connect()
    registry = build_default_tools()
    tool_name = await register_mcp_resource_tool(registry, client, "fake")
    assert tool_name == "mcp_fake_read_resource"
    spec = registry.get(tool_name)
    assert spec.permission == ToolPermission.DANGEROUS
    assert spec.mcp_server == "fake"
    session = ToolSession(tmp_path, registry=registry)
    blocked = await session.execute(ToolCall(name=tool_name, args={"uri": "note://demo/1"}))
    assert blocked.needs_approval
    ok = await session.execute(ToolCall(name=tool_name, args={"uri": "note://demo/1"}), approved=True)
    assert ok.ok and "hello resource" in ok.output
    blob = await session.execute(ToolCall(name=tool_name, args={"uri": "blob://demo/1"}), approved=True)
    assert blob.ok and "blob-bytes" in blob.output
    await client.close()


async def test_register_mcp_resource_tool_skips_when_unsupported(monkeypatch):
    client = StdioMCPClient("fake", ["python", "x"], timeout=5)

    async def _unsupported():
        raise MCPError("method not found: resources/list")

    monkeypatch.setattr(client, "list_resources", _unsupported)
    registry = build_default_tools()
    assert await register_mcp_resource_tool(registry, client, "fake") is None
    assert "mcp_fake_read_resource" not in registry.names()


# ---------------------------------------------------------------------------
# AGENTS.md 项目记忆：加载 / 截断 / ToolSession 携带 / system 注入
# ---------------------------------------------------------------------------


def test_load_agents_md_reads_existing(tmp_path: Path):
    (tmp_path / "AGENTS.md").write_text("# 项目记忆\n- 使用 CRLF 行尾\n", encoding="utf-8")
    assert load_agents_md(tmp_path) == "# 项目记忆\n- 使用 CRLF 行尾\n"


def test_load_agents_md_skips_missing(tmp_path: Path):
    assert load_agents_md(tmp_path) == ""


def test_load_agents_md_truncates_oversized(tmp_path: Path):
    (tmp_path / "AGENTS.md").write_text("x" * (MAX_AGENTS_MD_CHARS + 500), encoding="utf-8")
    text = load_agents_md(tmp_path)
    assert len(text) <= MAX_AGENTS_MD_CHARS + 64
    assert "截断" in text


def test_tool_session_carries_agents_md(tmp_path: Path):
    tool_session = ToolSession(tmp_path, agents_md="项目约定 memo")
    assert tool_session.agents_md == "项目约定 memo"


class _RecordingClient:
    """记录收到的 system 消息；无工具调用直接返回完成文本。"""

    def __init__(self) -> None:
        self.system = ""

    async def complete_with_tools(self, messages, tools):  # noqa: ANN001
        for message in messages:
            if message.get("role") == "system":
                self.system = str(message.get("content") or "")
        return ChatResponse(text="完成。", tool_calls=[])


class _StubRuntime:
    """仅提供 _tool_mode_agent_step 用到的 client_for/report_usage。"""

    def __init__(self, client: _RecordingClient) -> None:
        self._client = client

    def client_for(self, role):  # noqa: ANN001
        return self._client

    def report_usage(self, usage, role_id):  # noqa: ANN001, ANN002
        return usage


async def test_tool_mode_step_injects_agents_md(tmp_path: Path):
    (tmp_path / "AGENTS.md").write_text("项目约定：使用 CRLF 行尾\n", encoding="utf-8")
    client = _RecordingClient()
    runtime = _StubRuntime(client)
    tool_session = ToolSession(tmp_path, agents_md=load_agents_md(tmp_path))
    role = RoleRegistry().get("backend")
    node = WorkflowNode(id="dev", type="agent", role="backend")
    spec = WorkflowSpec(
        name="t11-8",
        max_iterations=3,
        thread_id="t",
        nodes=[
            WorkflowNode(id="start", type="start"),
            node,
            WorkflowNode(id="end", type="end"),
        ],
        edges=[
            WorkflowEdge(from_="start", to=node.id),
            WorkflowEdge(from_=node.id, to="end"),
        ],
    )
    ctx = NodeContext(node_id=node.id, spec=spec, events=[], run_id="run", loop_count=1)
    state = ClusterState(
        project=Project(id="p", name="p"),
        iterations=[Iteration(id="i", project_id="p", number=1)],
    )
    await _tool_mode_agent_step(
        runtime,
        role,
        node,
        ctx,
        "p",
        "i",
        "t",
        state,
        tool_session,
        max_rounds=1,
        catalog=None,
        interrupt_fn=lambda *args, **kwargs: None,
    )
    assert "AGENTS.md" in client.system
    assert "项目约定：使用 CRLF 行尾" in client.system