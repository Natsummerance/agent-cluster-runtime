"""v0.6 T13.8：自研最小 RFC 6455 WebSocket（仅 stdlib，无第三方依赖）。

范围（设计 §6.4 + 实现计划 13.8）：
- ``WebSocketPeer``：握手 accept-key、masked 客户端帧解码（强制校验 mask）、
  服务端帧（不 mask）编码、文本帧 ≤ 64KiB、不支持分片（按 bad_frame 处理）、
  控制帧 fin 必真且 payload ≤ 125；
- ``handle_ws``：产品内消息循环（subscribe/ping/cancel/approval/interrupt/stdin →
  ack/snapshot/event/error/pong），坏 JSON 连续 3 次 → close 1002；
- ``event`` 帧与 SSE data 同构：``seq/session_id/payload`` + ``event_type``
  （SSE data 顶层 ``type`` 在 WS 帧里被外层 ``"type":"event"`` 占用，
  因此内层事件类型平移为 ``event_type``，13.10 去重 reducer 以此识别事件种类）。
"""

from __future__ import annotations

import base64
import hashlib
import json
import queue
import socket
import struct
import time
from typing import Any

WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
MAX_TEXT_PAYLOAD = 64 * 1024

OP_TEXT = 0x1
OP_CLOSE = 0x8
OP_PING = 0x9
OP_PONG = 0xA

CLOSE_NORMAL = 1000
CLOSE_GOING_AWAY = 1001
CLOSE_PROTOCOL_ERROR = 1002

_TERMINAL_STATUSES = ("completed", "failed")


class WsProtocolError(Exception):
    """帧层协议错误（非法 mask/超长/分片/RSV），连接方按 1002 处理。"""


class WebSocketPeer:
    """单连接帧编解码（仅由连接 handler 线程使用）。"""

    def __init__(self, sock: socket.socket) -> None:
        self.sock = sock

    @staticmethod
    def accept_key(key: str) -> str:
        digest = hashlib.sha1(f"{key}{WS_GUID}".encode("ascii")).digest()
        return base64.b64encode(digest).decode("ascii")

    def _recv_exact(self, length: int, *, idle_ok: bool = False) -> bytes | None:
        chunks: list[bytes] = []
        remaining = length
        while remaining > 0:
            try:
                chunk = self.sock.recv(remaining)
            except socket.timeout:
                if idle_ok and not chunks:
                    raise
                # 帧已开始但客户端迟迟不补齐 → 帧层协议错误
                raise WsProtocolError("帧读取超时") from None
            if not chunk:
                if chunks:
                    raise WsProtocolError("连接在帧中途断开")
                return None
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)

    def recv_frame(self) -> tuple[int, bytes] | None:
        """读取一帧：返回 (opcode, payload)；EOF 返回 None；非法帧抛 WsProtocolError。"""
        header = self._recv_exact(2, idle_ok=True)
        if header is None:
            return None
        b0, b1 = header[0], header[1]
        fin = bool(b0 & 0x80)
        opcode = b0 & 0x0F
        if not fin:
            raise WsProtocolError("不支持分片帧")
        if b0 & 0x70:
            raise WsProtocolError("RSV 位必须为 0")
        masked = bool(b1 & 0x80)
        if not masked:
            raise WsProtocolError("客户端帧必须 mask")
        length = b1 & 0x7F
        if length == 126:
            extended = self._recv_exact(2)
            if extended is None:
                raise WsProtocolError("扩展长度不完整")
            length = struct.unpack(">H", extended)[0]
        elif length == 127:
            extended = self._recv_exact(8)
            if extended is None:
                raise WsProtocolError("扩展长度不完整")
            length = struct.unpack(">Q", extended)[0]
        if opcode >= OP_CLOSE and length > 125:
            raise WsProtocolError("控制帧 payload 超限")
        if opcode == OP_TEXT and length > MAX_TEXT_PAYLOAD:
            raise WsProtocolError(f"文本帧超过 {MAX_TEXT_PAYLOAD} 字节上限")
        if opcode not in (OP_TEXT, OP_CLOSE, OP_PING, OP_PONG):
            raise WsProtocolError(f"不支持的 opcode：{opcode}")
        mask_key = self._recv_exact(4)
        if mask_key is None:
            raise WsProtocolError("masking-key 缺失")
        payload = self._recv_exact(length) if length else b""
        if payload is None:
            raise WsProtocolError("payload 不完整")
        if mask_key:
            payload = bytes(byte ^ mask_key[index % 4] for index, byte in enumerate(payload))
        return opcode, payload

    def _send_frame(self, opcode: int, payload: bytes = b"") -> None:
        """服务端帧：FIN=1、不 mask。"""
        header = bytes([0x80 | opcode])
        length = len(payload)
        if length <= 125:
            header += bytes([length])
        else:
            header += bytes([126]) + struct.pack(">H", length)
        self.sock.sendall(header + payload)

    def send_text(self, text: str) -> None:
        self._send_frame(OP_TEXT, text.encode("utf-8"))

    def send_ping(self, payload: bytes = b"") -> None:
        self._send_frame(OP_PING, payload)

    def send_pong(self, payload: bytes = b"") -> None:
        self._send_frame(OP_PONG, payload)

    def send_close(self, code: int = CLOSE_NORMAL, reason: str = "") -> None:
        payload = struct.pack(">H", code) + reason.encode("utf-8")[:123]
        try:
            self._send_frame(OP_CLOSE, payload)
        except OSError:
            pass


def _encode_event_frame(event: dict) -> dict:
    """SSE data 同构帧：seq/session_id/payload + 内层事件类型平移为 event_type。"""
    return {
        "type": "event",
        "seq": event.get("seq", 0),
        "session_id": event.get("session_id", ""),
        "event_type": event.get("type", ""),
        "payload": event.get("payload") or {},
    }


def _send_json_frame(peer: WebSocketPeer, message: dict) -> None:
    peer.send_text(json.dumps(message, ensure_ascii=False))


def _error(peer: WebSocketPeer, request_id: Any, code: str, message: str) -> None:
    _send_json_frame(
        peer,
        {"type": "error", "id": request_id, "payload": {"code": code, "message": message, "fatal": False}},
    )


def handle_ws(sock: socket.socket, server: Any, session_id: str | None = None) -> None:
    """产品内 WS 消息循环（设计 §6.4）。``server`` 为 ``WorkbenchServer``。"""
    peer = WebSocketPeer(sock)
    manager = server.manager
    subs: dict[str, queue.Queue] = {}  # sid -> 订阅队列（退订时按会话日志反查）
    bad_frames = 0
    last_ping = time.monotonic()
    heartbeat = float(getattr(server, "heartbeat_seconds", 15.0) or 15.0)

    def subscribe_sids(sids: list[str]) -> None:
        for sid in sids:
            if sid in subs:
                continue
            session = manager.sessions.get(sid)
            if session is None:
                continue
            subs[sid] = session.log.subscribe()

    def discover_global() -> None:
        """全局订阅：覆盖当前全部会话 + 后续新建会话（注册表轮询补订）。"""
        for session in manager.sessions.values():
            if session.session_id not in subs:
                subs[session.session_id] = session.log.subscribe()

    def snapshot_payload(request_id: Any) -> None:
        if not subs and session_id:
            subscribe_sids([session_id])
        if subs:
            first = manager.sessions.get(next(iter(subs)))
            project_id = first.project_id if first is not None else ""
            if project_id:
                try:
                    dashboard = server._dashboard(project_id)
                except Exception:  # noqa: BLE001 —— 项目缺失不阻断快照
                    dashboard = {}
                sessions = [session.snapshot() for session in manager.list_for(project_id)]
            else:
                dashboard, sessions = {}, []
            project = project_id
        else:
            project = ""
            dashboard = server.metrics_snapshot()
            sessions = [session.snapshot() for session in manager.sessions.values()]
        _send_json_frame(
            peer,
            {
                "type": "snapshot",
                "id": request_id,
                "payload": {"project": project, "dashboard": dashboard, "sessions": sessions},
            },
        )

    def forward_events() -> None:
        for sub in subs.values():
            while True:
                try:
                    event = sub.get_nowait()
                except queue.Empty:
                    break
                _send_json_frame(peer, _encode_event_frame(event))
        # 事件转发也会因客户端断连抛 OSError：由主循环统一捕获退出

    def route_message(message: dict) -> None:
        request_id = message.get("id")
        message_type = str(message.get("type") or "")
        payload = message.get("payload") if isinstance(message.get("payload"), dict) else {}
        if message_type == "subscribe":
            wanted = payload.get("session_ids") or []
            if wanted:
                subscribe_sids([str(sid) for sid in wanted])
            else:
                discover_global()
            snapshot_payload(request_id)
            return
        if message_type == "ping":
            _send_json_frame(peer, {"type": "pong", "id": request_id, "payload": {}})
            return
        target_sid = str(payload.get("session_id") or "")
        if message_type == "cancel":
            if not manager.cancel(target_sid):
                _error(peer, request_id, "not_found", f"会话不存在：{target_sid}")
                return
            _send_json_frame(peer, {"type": "ack", "id": request_id, "payload": {"ok": True}})
            return
        if message_type == "approval":
            decision = str(payload.get("decision") or "")
            text = str(payload.get("text") or "")
            session = manager.sessions.get(target_sid)
            if session is None:
                _error(peer, request_id, "not_found", f"会话不存在：{target_sid}")
                return
            if session.status in _TERMINAL_STATUSES:
                _error(peer, request_id, "session_busy", "会话已终态，无法审批")
                return
            if decision == "approve":
                answer = "accept"
            elif decision == "reject":
                answer = "reject"
            elif decision in ("edit", "response"):
                if not text.strip():
                    _error(peer, request_id, "bad_request", f"{decision} 需要 text")
                    return
                answer = f"{decision} {text}"
            else:
                _error(peer, request_id, "bad_request", f"非法 decision：{decision}")
                return
            session.submit_answer(answer)
            _send_json_frame(peer, {"type": "ack", "id": request_id, "payload": {"ok": True}})
            return
        if message_type == "interrupt":
            text = str(payload.get("text") or "").strip()
            if not text:
                _error(peer, request_id, "bad_request", "interrupt 需要 text")
                return
            session = manager.sessions.get(target_sid)
            if session is None:
                _error(peer, request_id, "not_found", f"会话不存在：{target_sid}")
                return
            session.inject_change(text)
            _send_json_frame(peer, {"type": "ack", "id": request_id, "payload": {"ok": True}})
            return
        if message_type == "stdin":
            text = str(payload.get("text") or "").strip()
            if not text:
                _error(peer, request_id, "bad_request", "stdin 需要 text")
                return
            session = manager.sessions.get(target_sid)
            if session is None:
                _error(peer, request_id, "not_found", f"会话不存在：{target_sid}")
                return
            if session.status in _TERMINAL_STATUSES:
                _error(peer, request_id, "session_busy", "会话已终态，无法注入")
                return
            if not manager.submit_stdin(target_sid, text):
                _error(peer, request_id, "not_found", f"会话不存在：{target_sid}")
                return
            _send_json_frame(peer, {"type": "ack", "id": request_id, "payload": {"ok": True}})
            return
        _error(peer, request_id, "unknown_type", f"未知消息类型：{message_type}")

    sock.settimeout(0.25)
    try:
        if session_id:
            subscribe_sids([session_id])
        while True:
            if heartbeat > 0 and time.monotonic() - last_ping >= heartbeat:
                try:
                    peer.send_ping()
                except OSError:
                    return
                last_ping = time.monotonic()
            try:
                forward_events()
            except OSError:
                return
            try:
                frame = peer.recv_frame()
            except socket.timeout:
                continue
            except WsProtocolError:
                peer.send_close(CLOSE_PROTOCOL_ERROR, "protocol error")
                return
            if frame is None:
                return
            opcode, payload = frame
            if opcode == OP_CLOSE:
                peer.send_close(CLOSE_NORMAL)
                return
            if opcode == OP_PING:
                try:
                    peer.send_pong(payload)
                except OSError:
                    return
                continue
            if opcode == OP_PONG:
                continue
            try:
                message = json.loads(payload.decode("utf-8"))
                if not isinstance(message, dict):
                    raise ValueError("消息必须是 JSON 对象")
            except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
                bad_frames += 1
                _error(peer, None, "bad_frame", "非法 JSON 文本帧")
                if bad_frames >= 3:
                    peer.send_close(CLOSE_PROTOCOL_ERROR, "too many bad frames")
                    return
                continue
            bad_frames = 0
            try:
                route_message(message)
            except OSError:
                return
    finally:
        for sid, sub in subs.items():
            session = manager.sessions.get(sid)
            if session is not None:
                session.log.unsubscribe(sub)
        try:
            sock.close()
        except OSError:
            pass
