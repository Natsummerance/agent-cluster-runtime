"""T13.7 SSE 扩展：retry 首帧 / 每事件 id / 心跳 / session.end 哨兵 / Last-Event-ID 重放 / 断连退订。

in-process WorkbenchServer + 随机端口 + deterministic 模型（沿用 test_t12_3 启动模式）；
heartbeat_seconds 注入 0.05 以在测试时间窗内观测心跳与退订。
"""

from __future__ import annotations

import json
import socket
import threading
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

import agent_cluster.server as server_mod
from agent_cluster.server import WorkbenchHandler, WorkbenchServer

MINI_GATE_FLOW = """name: t13.7-sse
thread_id: "t:13.7s"
nodes:
  - {id: start, type: start}
  - {id: requirements, type: agent, role: pm}
  - {id: requirement_gate, type: gate, gate: requirement_confirmation}
  - {id: end, type: end}
edges:
  - {from: start, to: requirements}
  - {from: requirements, to: requirement_gate}
  - {from: requirement_gate, to: end, on_accept: end, on_reject: requirements}
"""


@pytest.fixture()
def server(tmp_path, monkeypatch):
    monkeypatch.setattr(server_mod, "INDEX_DIR", tmp_path / "home")
    ws = WorkbenchServer(host="127.0.0.1", port=0, auth_token="", heartbeat_seconds=0.05)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), WorkbenchHandler)
    httpd.workbench = ws
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield {"ws": ws, "httpd": httpd, "port": port}
    httpd.shutdown()
    httpd.server_close()


def _request(port, method, path, body=None):
    data = json.dumps(body or {}).encode("utf-8") if body is not None else None
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def _get(port, path):
    return _request(port, "GET", path)


def _post(port, path, body=None):
    return _request(port, "POST", path, body)


def _create_project(port, tmp_path):
    ws_dir = tmp_path / "ws-sse"
    ws_dir.mkdir()
    status, created = _post(port, "/api/v1/projects", {"name": "p", "workspace": str(ws_dir)})
    assert status == 201, created
    return created["data"]["id"]


def _write_flow(tmp_path, flow_id="flow"):
    flow = tmp_path / f"{flow_id}.yaml"
    flow.write_text(MINI_GATE_FLOW, encoding="utf-8")
    return flow


def _start(port, pid, tmp_path, *, yes):
    flow = _write_flow(tmp_path, flow_id=f"finish-{yes}")
    status, started = _post(
        port,
        f"/api/v1/projects/{pid}/sessions",
        {"goal": "待办应用", "flow": str(flow), "model": "deterministic", "deterministic": True, "yes": yes},
    )
    assert status == 201, started
    return started["data"]["session_id"]


def _wait_terminal(port, sid, timeout=30.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        status, body = _get(port, f"/api/v1/sessions/{sid}")
        if status == 200 and body["data"]["status"] in ("completed", "failed"):
            return body["data"]
        time.sleep(0.05)
    raise AssertionError(f"会话 {sid} 未在 {timeout}s 内达到终态")


class SseConnection:
    """原始 socket SSE 读取器：支持 Last-Event-ID 头与增量读帧。"""

    def __init__(self, port: int, path: str, *, last_event_id: str | None = None):
        self.sock = socket.create_connection(("127.0.0.1", port), timeout=3)
        request = f"GET {path} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nAccept: text/event-stream\r\n"
        if last_event_id is not None:
            request += f"Last-Event-ID: {last_event_id}\r\n"
        request += "Connection: close\r\n\r\n"
        self.sock.sendall(request.encode("ascii"))
        self._buf = b""
        header_block = b""
        while b"\r\n\r\n" not in header_block:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise AssertionError("SSE 连接在响应头阶段被关闭")
            header_block += chunk
        header_text, rest = header_block.split(b"\r\n\r\n", 1)
        self._buf = rest
        status_line = header_text.split(b"\r\n", 1)[0].decode("ascii", "replace")
        assert " 200 " in status_line, status_line

    def read_line(self) -> str | None:
        while b"\n" not in self._buf:
            chunk = self.sock.recv(4096)
            if not chunk:
                if self._buf:
                    line, self._buf = self._buf, b""
                    return line.rstrip(b"\r\n").decode("utf-8", "replace")
                return None
            self._buf += chunk
        line, self._buf = self._buf.split(b"\n", 1)
        return line.rstrip(b"\r").decode("utf-8", "replace")

    def read_frame(self) -> dict:
        """读取一个 SSE 帧（直到空行）；无任何字节的 EOF 返回 {"eof": True}。"""
        fields: dict = {"event": None, "id": None, "retry": None, "data": [], "comments": [], "eof": False}
        saw = False
        while True:
            line = self.read_line()
            if line is None:
                fields["eof"] = not saw
                return fields
            if line == "":
                return fields
            saw = True
            if line.startswith(":"):
                fields["comments"].append(line[1:].lstrip(" "))
                continue
            name, _, value = line.partition(":")
            value = value.lstrip(" ")
            if name == "event":
                fields["event"] = value
            elif name == "id":
                fields["id"] = int(value)
            elif name == "retry":
                fields["retry"] = int(value)
            elif name == "data":
                fields["data"].append(value)

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass


def _read_all(port: int, path: str, *, last_event_id: str | None = None) -> list[dict]:
    """读到服务端关闭连接（终态会话 + 哨兵后 EOF）。"""
    conn = SseConnection(port, path, last_event_id=last_event_id)
    frames: list[dict] = []
    try:
        while True:
            frame = conn.read_frame()
            if frame["eof"]:
                return frames
            frames.append(frame)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 首帧 retry / 事件 id / 心跳
# ---------------------------------------------------------------------------


def test_sse_id_retry_heartbeat(server, tmp_path):
    port = server["port"]
    pid = _create_project(port, tmp_path)
    sid = _start(port, pid, tmp_path, yes=False)
    # 确保会话进入挂起
    deadline = time.time() + 30
    while time.time() < deadline:
        status, body = _get(port, f"/api/v1/sessions/{sid}")
        if status == 200 and body["data"]["status"] in ("waiting_approval", "running"):
            break
        time.sleep(0.05)

    conn = SseConnection(port, f"/api/v1/sessions/{sid}/events?since=0")
    try:
        first = conn.read_frame()
        assert first["retry"] == 3000, first
        ids: list[int] = []
        deadline = time.time() + 8
        saw_ping = False
        while time.time() < deadline:
            frame = conn.read_frame()
            if frame["data"]:
                payload = json.loads(frame["data"][-1])
                assert frame["id"] == payload["seq"], frame
                ids.append(payload["seq"])
            if any("ping" in c for c in frame["comments"]):
                saw_ping = True
                break
        assert saw_ping, "心跳 : ping 未在 8s 内出现"
        assert ids and ids[0] == 0, ids
        assert ids[-1] == len(ids) - 1, ids  # seq 连续无缺口
        assert len(set(ids)) == len(ids), ids  # 无重复
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# session.end 哨兵：completed 与 cancelled（abort）两态，后关连接
# ---------------------------------------------------------------------------


def test_sse_session_end_sentinel(server, tmp_path):
    port = server["port"]
    pid = _create_project(port, tmp_path)

    sid = _start(port, pid, tmp_path, yes=True)
    _wait_terminal(port, sid)
    frames = _read_all(port, f"/api/v1/sessions/{sid}/events?since=0")
    sentinels = [f for f in frames if f["event"] == "session.end"]
    assert sentinels, frames
    assert json.loads(sentinels[-1]["data"][0])["status"] == "completed"

    sid2 = _start(port, pid, tmp_path, yes=False)
    deadline = time.time() + 30
    while time.time() < deadline:
        status, body = _get(port, f"/api/v1/sessions/{sid2}")
        if status == 200 and body["data"]["status"] in ("waiting_approval", "running"):
            break
        time.sleep(0.05)
    status, cancelled = _post(port, f"/api/v1/sessions/{sid2}/cancel")
    assert status == 202 and cancelled["data"]["cancelled"] == "pending"
    _wait_terminal(port, sid2)
    frames = _read_all(port, f"/api/v1/sessions/{sid2}/events?since=0")
    sentinels = [f for f in frames if f["event"] == "session.end"]
    assert sentinels, frames
    assert json.loads(sentinels[-1]["data"][0])["status"] == "cancelled"


# ---------------------------------------------------------------------------
# Last-Event-ID 优先 / ?since= 回退：按 seq 重放，不丢不重
# ---------------------------------------------------------------------------


def test_sse_replay_last_event_id(server, tmp_path):
    port = server["port"]
    pid = _create_project(port, tmp_path)
    sid = _start(port, pid, tmp_path, yes=True)
    _wait_terminal(port, sid)

    frames = _read_all(port, f"/api/v1/sessions/{sid}/events?since=0")
    seqs = sorted({f["id"] for f in frames if f["data"]})
    assert len(seqs) >= 4, seqs
    marker = seqs[-3]

    # 头优先：Last-Event-ID: N → 重放 seq >= N+1
    frames = _read_all(port, f"/api/v1/sessions/{sid}/events", last_event_id=str(marker))
    got = [f["id"] for f in frames if f["data"] and f["event"] is None]
    assert got == [s for s in seqs if s > marker], got

    # ?since=N → 重放 seq >= N（与头的 +1 语义一致：不丢事件）
    frames = _read_all(port, f"/api/v1/sessions/{sid}/events?since={marker}")
    got2 = [f["id"] for f in frames if f["data"] and f["event"] is None]
    assert got2 == [s for s in seqs if s >= marker], got2
    assert got == got2[-len(got):]


# ---------------------------------------------------------------------------
# 客户端半途断连：服务端退订、不抛脏异常、服务仍健康
# ---------------------------------------------------------------------------


def test_sse_write_failure_exits(server, tmp_path):
    port = server["port"]
    ws = server["ws"]
    httpd = server["httpd"]
    pid = _create_project(port, tmp_path)
    sid = _start(port, pid, tmp_path, yes=False)
    deadline = time.time() + 30
    while time.time() < deadline:
        status, body = _get(port, f"/api/v1/sessions/{sid}")
        if status == 200 and body["data"]["status"] in ("waiting_approval", "running"):
            break
        time.sleep(0.05)

    handler_errors: list = []
    original = httpd.handle_error
    httpd.handle_error = lambda *args: handler_errors.append(args)  # type: ignore[method-assign]

    conn = SseConnection(port, f"/api/v1/sessions/{sid}/events?since=0")
    assert conn.read_frame()["retry"] == 3000
    conn.close()  # 半途断连（心跳写入将失败）

    session = ws.manager.get(sid)
    deadline = time.time() + 5
    while time.time() < deadline and session.log._subscribers:
        time.sleep(0.05)
    assert not session.log._subscribers, "断连后服务端未退订"
    time.sleep(0.2)
    assert not handler_errors, f"SSE 写失败抛脏异常: {handler_errors}"
    status, body = _get(port, "/api/v1/status")
    assert status == 200 and body["ok"] is True
    httpd.handle_error = original
