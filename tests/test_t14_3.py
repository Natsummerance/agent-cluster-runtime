"""Task 14.3 会话事件日志持久化（JSONL/SQLite）+ v0.6 自动迁移 + spill 测试。"""

from __future__ import annotations

import json
import sqlite3

import pytest

from agent_cluster.events import KNOWN_SESSION_EVENT_TYPES, SessionEvent, SessionEventLog
from agent_cluster.session_log_store import (
    SESSION_FORMAT_VERSION,
    JsonlSessionLogStore,
    SessionFormatUnsupportedError,
    SqliteSessionLogStore,
    build_header_line,
    migrate_v06_session,
    migrate_v06_to_jsonl,
)
from agent_cluster.spill import spill_text


# --- JSONL 头行（dsh 契约） ---


def test_header_line_shape_matches_dsh_contract() -> None:
    header = build_header_line("s1", cwd="/ws", parent_session="p0", seed_length=3)
    assert header["type"] == "session"
    assert header["version"] == SESSION_FORMAT_VERSION
    assert header["id"] == "s1"
    assert header["cwd"] == "/ws"
    assert header["parentSession"] == "p0"
    assert header["seedLength"] == 3
    assert isinstance(header["createdAt"], float)
    # 可选字段缺省时省略（dsh：absent fields never null）
    minimal = build_header_line("s2")
    assert "cwd" not in minimal and "parentSession" not in minimal and "seedLength" not in minimal


# --- JSONL 后端 ---


def _sample_log() -> SessionEventLog:
    log = SessionEventLog("s1")
    log.append("session/start", {"id": "s1"})
    log.append("user/message", {"content": "hi"})
    log.append("assistant/message", {"content": "hello"})
    return log


def test_jsonl_roundtrip(tmp_path) -> None:
    path = tmp_path / "session.jsonl"
    log = _sample_log()
    JsonlSessionLogStore.append(log, path, build_header_line("s1"))
    header, events = JsonlSessionLogStore.load(path)
    assert header["id"] == "s1" and header["version"] == SESSION_FORMAT_VERSION
    assert [(e.type, e.payload) for e in events] == [
        ("session/start", {"id": "s1"}),
        ("user/message", {"content": "hi"}),
        ("assistant/message", {"content": "hello"}),
    ]
    assert [e.seq for e in events] == [1, 2, 3]


def test_jsonl_append_continues_seq(tmp_path) -> None:
    path = tmp_path / "session.jsonl"
    log = _sample_log()
    JsonlSessionLogStore.append(log, path, build_header_line("s1"))
    log2 = SessionEventLog("s1")
    log2.append("user/message", {"content": "again"})
    JsonlSessionLogStore.append(log2, path)  # 不重复写头
    header, events = JsonlSessionLogStore.load(path)
    assert len(events) == 4 and events[-1].seq == 4


def test_jsonl_refuses_newer_version(tmp_path) -> None:
    path = tmp_path / "session.jsonl"
    path.write_text(
        json.dumps({"type": "session", "version": 99, "id": "s1", "createdAt": 1.0})
        + "\n",
        encoding="utf-8",
    )
    with pytest.raises(SessionFormatUnsupportedError):
        JsonlSessionLogStore.load(path)


def test_jsonl_skips_ignorable_unknown_event(tmp_path) -> None:
    path = tmp_path / "session.jsonl"
    header = json.dumps({"type": "session", "version": 1, "id": "s1", "createdAt": 1.0})
    known = json.dumps({"type": "user/message", "seq": 1, "ts": 1.0, "ignorable": False, "payload": {"content": "hi"}})
    unknown = json.dumps({"type": "future/event", "seq": 2, "ts": 1.0, "ignorable": True, "payload": {}})
    path.write_text(f"{header}\n{known}\n{unknown}\n", encoding="utf-8")
    _, events = JsonlSessionLogStore.load(path)
    assert [e.type for e in events] == ["user/message"]


def test_jsonl_refuses_unknown_non_ignorable(tmp_path) -> None:
    path = tmp_path / "session.jsonl"
    header = json.dumps({"type": "session", "version": 1, "id": "s1", "createdAt": 1.0})
    unknown = json.dumps({"type": "future/event", "seq": 1, "ts": 1.0, "ignorable": False, "payload": {}})
    path.write_text(f"{header}\n{unknown}\n", encoding="utf-8")
    with pytest.raises(SessionFormatUnsupportedError):
        JsonlSessionLogStore.load(path)


# --- SQLite 后端 ---


def test_sqlite_roundtrip_wal(tmp_path) -> None:
    db = tmp_path / "session.db"
    store = SqliteSessionLogStore(str(db))
    log = _sample_log()
    store.append("s1", log.events)
    events = store.load("s1")
    assert [(e.type, e.payload) for e in events] == [
        ("session/start", {"id": "s1"}),
        ("user/message", {"content": "hi"}),
        ("assistant/message", {"content": "hello"}),
    ]
    conn = sqlite3.connect(db)
    assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"


def test_sqlite_revision_token(tmp_path) -> None:
    store = SqliteSessionLogStore(str(tmp_path / "session.db"))
    log = _sample_log()
    store.append("s1", log.events)
    assert store.revision("s1") == 3
    assert store.revision("missing") == 0


def test_sqlite_append_idempotent_by_seq(tmp_path) -> None:
    store = SqliteSessionLogStore(str(tmp_path / "session.db"))
    log = _sample_log()
    store.append("s1", log.events)
    store.append("s1", log.events)  # 重复 append 不产生重复行
    assert len(store.load("s1")) == 3


# --- v0.6 自动迁移 ---


def _v06_session_json() -> dict:
    return {
        "session_id": "legacy-1",
        "thread_id": "t1",
        "goal": "构建登录页",
        "flow": "flows/dev.yml",
        "model": "codex",
        "workspace": "C:/ws",
        "status": "completed",
        "project_id": "p1",
        "parent_session_id": "",
        "fork_depth": 0,
        "budget": 200,
        "transcript": [
            {"id": "q1", "question": "目标?", "answer": "登录页", "source": "human"},
            {"id": "q2", "question": "验收?", "answer": "e2e 绿", "source": "script"},
        ],
        "gate_decisions": [
            {"node": "design_review", "kind": "design", "attempts": 2, "rejections": 1, "last_decision": "approve", "escalated": False}
        ],
        "phases": {"dev": "done"},
    }


def test_migrate_v06_emits_events() -> None:
    log = migrate_v06_session(_v06_session_json())
    types = [e.type for e in log.events]
    assert types[0] == "migration/restored"
    assert types.count("user/message") == 2
    assert types.count("assistant/message") == 2
    assert "gate/decision" in types
    restored = log.events[0].payload
    assert restored["session_id"] == "legacy-1"
    assert restored["goal"] == "构建登录页"
    assert restored["status"] == "completed"
    assert restored["transcript_len"] == 2
    # 表面投影 = 迁移后的模型历史
    assert log.derive_messages()[0] == {"role": "user", "content": "目标?"}


def test_migrate_v06_to_jsonl_idempotent(tmp_path) -> None:
    path = tmp_path / "session.jsonl"
    assert migrate_v06_to_jsonl(_v06_session_json(), path) is True
    assert migrate_v06_to_jsonl(_v06_session_json(), path) is False  # 已迁移，跳过
    header, events = JsonlSessionLogStore.load(path)
    assert header["id"] == "legacy-1"
    assert events[0].type == "migration/restored"


# --- spill 契约 ---


def test_spill_small_content_inline(tmp_path) -> None:
    result = spill_text("short", str(tmp_path))
    assert result["inline"] == "short"
    assert result["path"] == "" and result["truncated"] is False


def test_spill_large_content_to_disk(tmp_path) -> None:
    big = "x" * 60000
    result = spill_text(big, str(tmp_path))
    assert result["truncated"] is True
    assert result["path"]
    assert "tail" in result["inline"] and "head" in result["inline"]
    stored = (tmp_path / result["path"]).read_text(encoding="utf-8")
    assert stored == big
