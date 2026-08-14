"""Task 14.2 会话事件日志核心测试（dsh 契约移植）。"""

from __future__ import annotations

import json

import pytest

from agent_cluster.events import (
    InvariantViolationError,
    KNOWN_SESSION_EVENT_TYPES,
    SessionEvent,
    SessionEventLog,
    UnknownEventTypeError,
)


def _log(session_id: str = "s1") -> SessionEventLog:
    return SessionEventLog(session_id=session_id)


def test_append_assigns_monotonic_seq() -> None:
    log = _log()
    e1 = log.append("session/start", {"id": "s1"})
    e2 = log.append("user/message", {"content": "hi"})
    e3 = log.append("assistant/message", {"content": "hello"})
    assert (e1.seq, e2.seq, e3.seq) == (1, 2, 3)
    assert log.events == (e1, e2, e3)


def test_append_rejects_unknown_type() -> None:
    log = _log()
    with pytest.raises(UnknownEventTypeError):
        log.append("mystery/event", {})


def test_append_rejects_empty_type() -> None:
    log = _log()
    with pytest.raises(UnknownEventTypeError):
        log.append("", {})


def test_append_defaults_empty_payload() -> None:
    log = _log()
    e = log.append("turn/end", None)
    assert e.payload == {}


def test_vocabulary_uses_domain_slash_verb() -> None:
    for name in KNOWN_SESSION_EVENT_TYPES:
        assert "/" in name and not name.startswith("/") and not name.endswith("/")


def test_vocabulary_contains_core_types() -> None:
    required = {
        "session/start",
        "session/end",
        "turn/start",
        "turn/end",
        "step/start",
        "step/end",
        "user/message",
        "assistant/message",
        "tool/call",
        "tool/result",
        "approval/asked",
        "approval/decided",
        "gate/decision",
        "meeting/decision",
        "ledger/entry",
        "budget/alert",
        "migration/restored",
    }
    assert required <= KNOWN_SESSION_EVENT_TYPES


def test_derive_messages_surface_only() -> None:
    log = _log()
    log.append("session/start", {"id": "s1"})
    log.append("turn/start", {})
    log.append("user/message", {"content": "hi"})
    log.append("assistant/message", {"content": "hello"})
    log.append("tool/call", {"tool": "read", "input": {}})
    log.append("tool/result", {"tool": "read", "content": "file content"})
    log.append("turn/end", {"reason": "completed"})
    assert log.derive_messages() == [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
        {"role": "tool", "tool_call_id": "", "content": "file content"},
    ]


def test_derive_messages_preserves_order() -> None:
    log = _log()
    for i in range(5):
        log.append("user/message", {"content": f"u{i}"})
        log.append("assistant/message", {"content": f"a{i}"})
    derived = log.derive_messages()
    assert [m["content"] for m in derived] == [
        "u0", "a0", "u1", "a1", "u2", "a2", "u3", "a3", "u4", "a4",
    ]


def test_request_payload_deterministic_bytes() -> None:
    log = _log()
    log.append("user/message", {"content": "hi"})
    log.append("assistant/message", {"content": "hello"})
    tools = [{"name": "read"}, {"name": "write"}]
    p1 = log.request_payload(system_prompt="sys", tools=tools)
    p2 = log.request_payload(system_prompt="sys", tools=tools)
    assert json.dumps(p1, ensure_ascii=False, sort_keys=False) == json.dumps(
        p2, ensure_ascii=False, sort_keys=False
    )


def test_request_payload_shape() -> None:
    log = _log()
    log.append("user/message", {"content": "hi"})
    payload = log.request_payload(system_prompt="sys", tools=[{"name": "read"}])
    assert payload == {
        "system": "sys",
        "tools": [{"name": "read"}],
        "messages": [{"role": "user", "content": "hi"}],
    }


def test_verify_derivation_passes_on_matching() -> None:
    log = _log()
    log.append("user/message", {"content": "hi"})
    log.append("assistant/message", {"content": "hello"})
    log.verify_derivation(log.derive_messages())  # must not raise


def test_verify_derivation_raises_on_mismatch() -> None:
    log = _log()
    log.append("user/message", {"content": "hi"})
    with pytest.raises(InvariantViolationError):
        log.verify_derivation([{"role": "user", "content": "NOT hi"}])


def test_events_are_readonly_tuple() -> None:
    log = _log()
    log.append("session/start", {})
    with pytest.raises(AttributeError):
        log.events.append("nope")  # type: ignore[attr-defined]


def test_event_to_dict_roundtrip() -> None:
    log = _log()
    e = log.append("approval/decided", {"gate": "release", "decision": "approve"})
    d = e.to_dict()
    assert d["type"] == "approval/decided"
    assert d["payload"] == {"gate": "release", "decision": "approve"}
    assert d["seq"] == 1
    assert d["ignorable"] is False
    assert isinstance(d["ts"], float)


def test_session_id_exposed() -> None:
    assert _log("s42").session_id == "s42"
