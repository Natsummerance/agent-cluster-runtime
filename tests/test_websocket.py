"""T13.8 WebSocket：RFC 6455 握手/帧层/认证/协议流/错误路径（raw socket + 手写帧字节）。

in-process WorkbenchServer + 随机端口 + deterministic 模型（沿用 test_t13_rest 启动模式）。
"""

from __future__ import annotations

import json
import socket
import struct
import threading
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

import agent_cluster.server as server_mod
from agent_cluster.server import WorkbenchHandler, WorkbenchServer
from agent_cluster.ws import MAX_TEXT_PAYLOAD, WebSocketPeer, WsProtocolError

MINI_GATE_FLOW = """name: t13.8-ws
thread_id: "t:13.8"
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

_MASK_KEY = b"\x11\x22\x33\x44"


def _mask(payload: bytes, key: bytes = _MASK_KEY) -> bytes:
    return bytes(byte ^ key[index % 4] for index, byte in enumerate(payload))


def _client_frame(opcode: int, payload: bytes = b"", *, masked: bool = True) -> bytes:
    b0 = 0x80 | opcode
    length = len(payload)
    if length <= 125:
        header = bytes([b0, (0x80 if masked else 0) | length])
    elif length <= 0xFFFF:
        header = bytes([b0, (0x80 if masked else 0) | 126]) + struct.pack(">H", length)
    else:
        header = bytes([b0, (0x80 if masked else 0) | 127]) + struct.pack(">Q", length)
    if masked:
        return header + _MASK_KEY + _mask(payload)
    return header + payload


def _parse_server_frame(data: bytes) -> tuple[int, bytes]:
    b0, b1 = data[0], data[1]
    opcode = b0 & 0x0F
    length = b1 & 0x7F
    offset = 2
    if length == 126:
        length = struct.unpack(">H", data[offset:offset + 2])[0]
        offset += 2
    elif length == 127:
        length = struct.unpack(">Q", data[offset:offset + 8])[0]
        offset += 8
    return opcode, data[offset:offset + length]


def _make_server(tmp_path, monkeypatch, *, auth_token=""):
    monkeypatch.setattr(server_mod, "INDEX_DIR", tmp_path / "home")
    ws = WorkbenchServer(host="127.0.0.1", port=0, auth_token=auth_token)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), WorkbenchHandler)
    httpd.workbench = ws
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return ws, httpd, port


@pytest.fixture()
def server(tmp_path, monkeypatch):
    ws, httpd, port = _make_server(tmp_path, monkeypatch)
    yield {"ws": ws, "httpd": httpd, "port": port}
    httpd.shutdown()
    httpd.server_close()


@pytest.fixture()
def server_auth(tmp_path, monkeypatch):
    ws, httpd, port = _make_server(tmp_path, monkeypatch, auth_token="secret")
    yield {"ws": ws, "httpd": httpd, "port": port}
    httpd.shutdown()
    httpd.server_close()


class WsClient:
    """raw socket 客户端：握手 + 帧编解码（手写帧字节，不 mock 服务端编解码）。"""

    def __init__(self, port: int, path: str, *, key: str = "dGhlIHNhbXBsZSBub25jZQ==", token: str = ""):
        self.sock = socket.create_connection(("127.0.0.1", port), timeout=8)
        request = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: 127.0.0.1:{port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n"
        )
        if token:
            request += f"X-Auth-Token: {token}\r\n"
        request += "\r\n"
        self.sock.sendall(request.encode("ascii"))
        self._buf = b""
        header_block = b""
        while b"\r\n\r\n" not in header_block:
            chunk = self.sock.recv(4096)
            if not chunk:
                break
            header_block += chunk
        header_text, _, rest = header_block.partition(b"\r\n\r\n")
        self._buf = rest
        self.status = int(header_text.split(b" ", 2)[1]) if header_text else 0
        self.headers = {}
        for line in header_text.split(b"\r\n")[1:]:
            if b":" in line:
                name, _, value = line.partition(b":")
                self.headers[name.strip().lower().decode("ascii", "replace")] = value.strip().decode("ascii", "replace")
        self.sock.settimeout(8)

    def send_text(self, obj: object) -> None:
        payload = obj if isinstance(obj, str) else json.dumps(obj, ensure_ascii=False)
        self.sock.sendall(_client_frame(0x1, payload.encode("utf-8")))

    def send_frame(self, opcode: int, payload: bytes = b"") -> None:
        self.sock.sendall(_client_frame(opcode, payload))

    def recv_frame(self) -> tuple[int, bytes] | None:
        while len(self._buf) < 2:
            chunk = self.sock.recv(4096)
            if not chunk:
                return None
            self._buf += chunk
        header_length = 2
        b1 = self._buf[1]
        length = b1 & 0x7F
        if length == 126:
            while len(self._buf) < 4:
                self._buf += self.sock.recv(4096)
            length = struct.unpack(">H", self._buf[2:4])[0]
            header_length = 4
        elif length == 127:
            while len(self._buf) < 10:
                self._buf += self.sock.recv(4096)
            length = struct.unpack(">Q", self._buf[2:10])[0]
            header_length = 10
        while len(self._buf) < header_length + length:
            chunk = self.sock.recv(4096)
            if not chunk:
                break
            self._buf += chunk
        frame, self._buf = self._buf[:header_length + length], self._buf[header_length + length:]
        opcode, payload = _parse_server_frame(frame)
        return opcode, payload

    def recv_json(self, skip_control: bool = True) -> dict:
        deadline = time.time() + 8
        while time.time() < deadline:
            frame = self.recv_frame()
            assert frame is not None, "连接在收到 JSON 帧前关闭"
            opcode, payload = frame
            if opcode == 0x1:
                return json.loads(payload.decode("utf-8"))
            if opcode == 0x9 and skip_control:
                self.send_frame(0xA, payload)  # 回 pong
                continue
            if opcode == 0xA and skip_control:
                continue
            raise AssertionError(f"非文本帧：opcode={opcode} payload={payload[:80]!r}")
        raise AssertionError("等待 JSON 帧超时")

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass


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


def _create_project(port, tmp_path, name="p"):
    ws_dir = tmp_path / f"ws-{name}"
    ws_dir.mkdir()
    status, created = _request(port, "POST", "/api/v1/projects", {"name": name, "workspace": str(ws_dir)})
    assert status == 201, created
    return created["data"]["id"]


def _start_waiting(port, pid, tmp_path, flow_id="flow"):
    flow = tmp_path / f"{flow_id}.yaml"
    flow.write_text(MINI_GATE_FLOW, encoding="utf-8")
    status, started = _request(
        port,
        "POST",
        f"/api/v1/projects/{pid}/sessions",
        {"goal": "待办应用", "flow": str(flow), "model": "deterministic", "deterministic": True},
    )
    assert status == 201, started
    sid = started["data"]["session_id"]
    deadline = time.time() + 30
    while time.time() < deadline:
        status, body = _request(port, "GET", f"/api/v1/sessions/{sid}")
        if status == 200 and body["data"]["status"] in ("waiting_approval", "running"):
            return sid
        time.sleep(0.05)
    raise AssertionError(f"会话 {sid} 未进入挂起态")


# ---------------------------------------------------------------------------
# 握手
# ---------------------------------------------------------------------------


def test_handshake_accept_key():
    assert (
        WebSocketPeer.accept_key("dGhlIHNhbXBsZSBub25jZQ==") == "s3pPLMBiTxaQ9kYGzzhZRbK+xOo="
    )  # RFC 6455 附录固定向量


def test_handshake_missing_key_400(server):
    port = server["port"]
    sock = socket.create_connection(("127.0.0.1", port), timeout=8)
    sock.sendall(
        b"GET /api/v1/ws HTTP/1.1\r\nHost: 127.0.0.1\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n\r\n"
    )
    response = b""
    while b"\r\n\r\n" not in response:
        response += sock.recv(4096)
    assert b" 400 " in response.split(b"\r\n", 1)[0]
    sock.close()


def test_auth_401_and_query_token(server_auth):
    port = server_auth["port"]
    # 无 token / 错 token → 401 不升级
    client = WsClient(port, "/api/v1/ws", token="wrong")
    assert client.status == 401
    assert "upgrade" not in client.headers
    client.close()
    client = WsClient(port, "/api/v1/ws")
    assert client.status == 401
    client.close()
    # token 查询参数等价认证 → 101 + 正确 accept
    client = WsClient(port, "/api/v1/ws?token=secret")
    assert client.status == 101
    assert client.headers["sec-websocket-accept"] == "s3pPLMBiTxaQ9kYGzzhZRbK+xOo="
    client.send_text({"type": "ping", "id": "x1"})
    assert client.recv_json() == {"type": "pong", "id": "x1", "payload": {}}
    client.close()


# ---------------------------------------------------------------------------
# 帧层（peer 级，socketpair，不经过 HTTP）
# ---------------------------------------------------------------------------


def test_frame_roundtrip():
    # 长度边界（125/126/65535：≤64KiB 合法，126 触发 2 字节扩展长度）
    server_sock, client_sock = socket.socketpair()
    peer = WebSocketPeer(server_sock)
    try:
        for size in (0, 1, 125, 126, 65535):
            payload = b"a" * size
            client_sock.sendall(_client_frame(0x1, payload))
            opcode, decoded = peer.recv_frame()
            assert opcode == 0x1 and decoded == payload, size
    finally:
        server_sock.close()
        client_sock.close()


def test_frame_protocol_errors():
    # 超限文本帧（65536 > 64KiB）→ 协议错误（流已失同步，独立 socketpair 隔离）
    server_sock, client_sock = socket.socketpair()
    peer = WebSocketPeer(server_sock)
    try:
        client_sock.sendall(_client_frame(0x1, b"a" * (MAX_TEXT_PAYLOAD + 1)))
        with pytest.raises(WsProtocolError):
            peer.recv_frame()
    finally:
        server_sock.close()
        client_sock.close()

    server_sock, client_sock = socket.socketpair()
    peer = WebSocketPeer(server_sock)
    try:
        # 未 mask 客户端帧 → 协议错误
        client_sock.sendall(_client_frame(0x1, b"x", masked=False))
        with pytest.raises(WsProtocolError):
            peer.recv_frame()
    finally:
        server_sock.close()
        client_sock.close()

    server_sock, client_sock = socket.socketpair()
    peer = WebSocketPeer(server_sock)
    try:
        # 分片（fin=0）→ 协议错误
        client_sock.sendall(bytes([0x01, 0x80 | 1]) + _MASK_KEY + _mask(b"x"))
        with pytest.raises(WsProtocolError):
            peer.recv_frame()
    finally:
        server_sock.close()
        client_sock.close()


def test_frame_ping_pong_close():
    server_sock, client_sock = socket.socketpair()
    peer = WebSocketPeer(server_sock)
    try:
        # ping → pong 往返
        client_sock.sendall(_client_frame(0x9, b"hb"))
        opcode, payload = peer.recv_frame()
        assert (opcode, payload) == (0x9, b"hb")
        peer.send_pong(payload)
        assert _parse_server_frame(_read_exact(client_sock)) == (0xA, b"hb")
        # close 帧（含 code）
        client_sock.sendall(_client_frame(0x8, struct.pack(">H", 1000) + b"bye"))
        opcode, payload = peer.recv_frame()
        assert opcode == 0x8 and struct.unpack(">H", payload[:2])[0] == 1000
    finally:
        server_sock.close()
        client_sock.close()


def _read_exact(sock: socket.socket) -> bytes:
    frame = b""
    while len(frame) < 2:
        frame += sock.recv(4096)
    b1 = frame[1]
    length = b1 & 0x7F
    offset = 2
    if length == 126:
        while len(frame) < 4:
            frame += sock.recv(4096)
        length = struct.unpack(">H", frame[2:4])[0]
        offset = 4
    elif length == 127:
        while len(frame) < 10:
            frame += sock.recv(4096)
        length = struct.unpack(">Q", frame[2:10])[0]
        offset = 10
    while len(frame) < offset + length:
        frame += sock.recv(4096)
    return frame


# ---------------------------------------------------------------------------
# 协议流
# ---------------------------------------------------------------------------


def test_protocol_flow(server, tmp_path):
    port = server["port"]
    ws = server["ws"]
    pid = _create_project(port, tmp_path)
    sid = _start_waiting(port, pid, tmp_path)

    client = WsClient(port, f"/api/v1/ws?session_id={sid}")
    assert client.status == 101
    client.send_text({"type": "subscribe", "id": "c1", "payload": {"session_ids": [sid]}})
    snapshot = client.recv_json()
    assert snapshot["type"] == "snapshot" and snapshot["id"] == "c1"
    assert snapshot["payload"]["project"] == pid
    assert any(item["session_id"] == sid for item in snapshot["payload"]["sessions"])

    client.send_text({"type": "cancel", "id": "c2", "payload": {"session_id": sid}})
    ack = client.recv_json()
    assert ack == {"type": "ack", "id": "c2", "payload": {"ok": True}}

    deadline = time.time() + 8
    saw_cancel_event = False
    while time.time() < deadline and not saw_cancel_event:
        frame = client.recv_json(skip_control=True)
        if frame["type"] == "event" and frame["event_type"] == "session.cancel":
            assert frame["session_id"] == sid
            saw_cancel_event = True
    assert saw_cancel_event, "未收到 session.cancel 事件帧"

    deadline = time.time() + 30
    while time.time() < deadline:
        session = ws.manager.get(sid)
        if session.status in ("completed", "failed"):
            break
        time.sleep(0.1)
    assert session.status in ("completed", "failed")
    assert session.exit_code in (2, 3), session.exit_code  # /abort 语义
    client.close()


def test_approval_interrupt_stdin_roundtrip(server, tmp_path):
    port = server["port"]
    ws = server["ws"]
    pid = _create_project(port, tmp_path)
    s1 = _start_waiting(port, pid, tmp_path, flow_id="f1")
    s2 = _start_waiting(port, pid, tmp_path, flow_id="f2")
    s3 = _start_waiting(port, pid, tmp_path, flow_id="f3")

    # approval：approve → ack → 会话越过需求门（hint 不再滞留 requirement_confirmation）
    client = WsClient(port, f"/api/v1/ws?session_id={s1}")
    assert client.status == 101
    client.send_text({"type": "approval", "id": "a1", "payload": {"session_id": s1, "decision": "approve"}})
    assert client.recv_json() == {"type": "ack", "id": "a1", "payload": {"ok": True}}
    deadline = time.time() + 30
    advanced = False
    while time.time() < deadline:
        status, body = _request(port, "GET", f"/api/v1/sessions/{s1}")
        data = body["data"]
        if data["status"] in ("completed", "failed") or "requirement_confirmation" not in (
            data.get("pending_hint") or ""
        ):
            advanced = True
            break
        time.sleep(0.1)
    assert advanced, "approve 后会话未越过需求门"
    client.close()

    # interrupt：注入变更 → ack → driver 变更队列可观察（REST /interrupt 同一链路）
    client = WsClient(port, "/api/v1/ws")
    client.send_text({"type": "interrupt", "id": "a2", "payload": {"session_id": s2, "text": "增加导出功能"}})
    assert client.recv_json() == {"type": "ack", "id": "a2", "payload": {"ok": True}}
    session2 = ws.manager.get(s2)
    assert "增加导出功能" in session2.driver._injected_changes
    client.close()

    # stdin：注入行 → ack → 挂起 prompt 消费该行（队列被清空）
    client = WsClient(port, "/api/v1/ws")
    client.send_text({"type": "stdin", "id": "a3", "payload": {"session_id": s3, "text": "接受并继续"}})
    assert client.recv_json() == {"type": "ack", "id": "a3", "payload": {"ok": True}}
    session3 = ws.manager.get(s3)
    deadline = time.time() + 8
    while time.time() < deadline and session3.stdin_queue.qsize() > 0:
        time.sleep(0.1)
    assert session3.stdin_queue.qsize() == 0, "stdin 行未被挂起点消费"
    client.close()


def test_bad_frame_close_1002(server):
    port = server["port"]
    client = WsClient(port, "/api/v1/ws")
    assert client.status == 101
    for index in range(3):
        client.send_text("{bad json")
        error = client.recv_json()
        assert error["type"] == "error" and error["payload"]["code"] == "bad_frame", (index, error)
    frame = client.recv_frame()
    assert frame is not None
    opcode, payload = frame
    assert opcode == 0x8
    assert struct.unpack(">H", payload[:2])[0] == 1002
    client.close()


def test_unknown_type_keeps_alive(server):
    port = server["port"]
    client = WsClient(port, "/api/v1/ws")
    assert client.status == 101
    client.send_text({"type": "nope", "id": "u1", "payload": {}})
    error = client.recv_json()
    assert error["type"] == "error" and error["payload"]["code"] == "unknown_type"
    # 连接未关闭：协议层 ping → pong 仍可用
    client.send_frame(0x9, b"hb")
    frame = client.recv_frame()
    assert frame is not None
    assert frame[0] == 0xA and frame[1] == b"hb"
    client.send_text({"type": "ping", "id": "u2"})
    assert client.recv_json() == {"type": "pong", "id": "u2", "payload": {}}
    client.close()
