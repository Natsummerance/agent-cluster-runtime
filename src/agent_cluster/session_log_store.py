"""会话事件日志持久化（v0.7 Task 14.3，dsh 契约移植）。

- JSONL 头行/事件行契约对照 dsh ``session-persistence-jsonl/src/format.ts``：
  ``{"type":"session","version","id","createdAt","cwd","parentSession","seedLength"}``。
- 版本机制：单调整数、写者决定 bump、**方向感知拒绝**（新于读者 → 抛错；旧于读者 → 原样读取）、
  per-event ``ignorable`` 跳过未知事件。
- SQLite WAL 后端：events 1:1 行、revision 令牌 = max(seq)、按 (session_id, seq) 幂等。
- ``migrate_v06_session``：v0.6 ``session.json`` → 合成 ``migration/restored`` +
  表面消息 + 门决策事件（幂等，由 ``migrate_v06_to_jsonl`` 提供文件级去重）。

契约出处见 ``docs/porting/2026-08-14-dsh-porting.md``（MIT，dsh ``47f943859b``）。
"""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import replace
from pathlib import Path
from typing import Any, Mapping

from agent_cluster.events import KNOWN_SESSION_EVENT_TYPES, SessionEvent, SessionEventLog

__all__ = [
    "SESSION_FORMAT_VERSION",
    "JsonlSessionLogStore",
    "SessionFormatUnsupportedError",
    "SqliteSessionLogStore",
    "build_header_line",
    "migrate_v06_session",
    "migrate_v06_to_jsonl",
]

SESSION_FORMAT_VERSION = 1


class SessionFormatUnsupportedError(ValueError):
    """会话日志格式版本不被支持（方向感知拒绝）。"""


def build_header_line(
    session_id: str,
    cwd: str = "",
    parent_session: str | None = None,
    seed_length: int | None = None,
    created_at: float | None = None,
) -> dict[str, Any]:
    """构造 dsh 契约 JSONL 头行；可选字段缺省时省略（never null）。"""
    header: dict[str, Any] = {
        "type": "session",
        "version": SESSION_FORMAT_VERSION,
        "id": session_id,
        "createdAt": created_at if created_at is not None else time.time(),
    }
    if cwd:
        header["cwd"] = cwd
    if parent_session is not None:
        header["parentSession"] = parent_session
    if seed_length is not None:
        header["seedLength"] = seed_length
    return header


def _event_from_line(raw: Mapping[str, Any]) -> SessionEvent | None:
    """从 JSONL 行还原事件；未知类型按 ignorable 跳过，否则拒绝。"""
    etype = raw.get("type", "")
    if etype not in KNOWN_SESSION_EVENT_TYPES:
        if raw.get("ignorable"):
            return None
        raise SessionFormatUnsupportedError(
            f"unknown session event type {etype!r} without ignorable marker"
        )
    return SessionEvent(
        type=etype,
        seq=int(raw.get("seq", 0)),
        ts=float(raw.get("ts", 0.0)),
        payload=dict(raw.get("payload") or {}),
        ignorable=bool(raw.get("ignorable", False)),
    )


class JsonlSessionLogStore:
    """JSONL 会话日志文件后端（``<dir>/session.jsonl``）。"""

    @staticmethod
    def append(
        log: SessionEventLog,
        path: str | Path,
        header: dict[str, Any] | None = None,
    ) -> None:
        """追加事件；已有文件时按文件最大 seq 偏移续接（保持文件内 seq 连续）。"""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        exists = path.exists() and path.stat().st_size > 0
        base = 0
        if exists:
            _, existing = JsonlSessionLogStore.load(path)
            base = existing[-1].seq if existing else 0
        lines: list[str] = []
        if not exists and header is not None:
            lines.append(json.dumps(header, ensure_ascii=False))
        for event in log.events:
            shifted = event if base == 0 else replace(event, seq=event.seq + base)
            lines.append(json.dumps(shifted.to_dict(), ensure_ascii=False))
        with path.open("a", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + ("\n" if lines else ""))

    @staticmethod
    def load(path: str | Path) -> tuple[dict[str, Any], list[SessionEvent]]:
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"session log not found: {path}")
        header: dict[str, Any] | None = None
        events: list[SessionEvent] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            raw = json.loads(line)
            if raw.get("type") == "session":
                if header is not None:
                    raise SessionFormatUnsupportedError("duplicate header line")
                header = raw
                if int(raw.get("version", 0)) > SESSION_FORMAT_VERSION:
                    raise SessionFormatUnsupportedError(
                        f"session log version {raw.get('version')} newer than reader "
                        f"(SESSION_FORMAT_VERSION={SESSION_FORMAT_VERSION})"
                    )
                continue
            event = _event_from_line(raw)
            if event is not None:
                events.append(event)
        if header is None:
            raise SessionFormatUnsupportedError("missing session header line")
        return header, events


class SqliteSessionLogStore:
    """SQLite WAL 会话事件后端；revision 令牌 = max(seq)。"""

    _SCHEMA = """
    CREATE TABLE IF NOT EXISTS session_events (
        session_id TEXT NOT NULL,
        seq INTEGER NOT NULL,
        type TEXT NOT NULL,
        ts REAL NOT NULL,
        ignorable INTEGER NOT NULL,
        payload TEXT NOT NULL,
        PRIMARY KEY (session_id, seq)
    );
    """

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = str(db_path)
        self._conn = sqlite3.connect(self.db_path)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute(self._SCHEMA)
        self._conn.commit()

    def append(self, session_id: str, events: tuple[SessionEvent, ...] | list[SessionEvent]) -> None:
        rows = [
            (session_id, e.seq, e.type, e.ts, int(e.ignorable), json.dumps(dict(e.payload), ensure_ascii=False))
            for e in events
        ]
        self._conn.executemany(
            "INSERT OR IGNORE INTO session_events "
            "(session_id, seq, type, ts, ignorable, payload) VALUES (?, ?, ?, ?, ?, ?)",
            rows,
        )
        self._conn.commit()

    def load(self, session_id: str) -> list[SessionEvent]:
        rows = self._conn.execute(
            "SELECT seq, type, ts, ignorable, payload FROM session_events "
            "WHERE session_id = ? ORDER BY seq",
            (session_id,),
        ).fetchall()
        return [
            SessionEvent(
                type=row[1],
                seq=row[0],
                ts=row[2],
                ignorable=bool(row[3]),
                payload=json.loads(row[4]),
            )
            for row in rows
        ]

    def revision(self, session_id: str) -> int:
        row = self._conn.execute(
            "SELECT COALESCE(MAX(seq), 0) FROM session_events WHERE session_id = ?", (session_id,)
        ).fetchone()
        return int(row[0])

    def close(self) -> None:
        self._conn.close()


def migrate_v06_session(session_json: Mapping[str, Any]) -> SessionEventLog:
    """把 v0.6 ``session.json`` 内容合成事件日志（不落盘，纯内存）。"""
    log = SessionEventLog(session_id=str(session_json.get("session_id", "")))
    transcript = session_json.get("transcript") or []
    gates = session_json.get("gate_decisions") or []
    log.append(
        "migration/restored",
        {
            "session_id": str(session_json.get("session_id", "")),
            "goal": str(session_json.get("goal", "")),
            "flow": str(session_json.get("flow", "")),
            "model": str(session_json.get("model", "")),
            "workspace": str(session_json.get("workspace", "")),
            "status": str(session_json.get("status", "")),
            "project_id": str(session_json.get("project_id", "")),
            "parent_session_id": str(session_json.get("parent_session_id", "")),
            "fork_depth": int(session_json.get("fork_depth", 0)),
            "budget": int(session_json.get("budget", 0)),
            "transcript_len": len(transcript),
            "gate_decisions": len(gates),
            "phases": dict(session_json.get("phases") or {}),
        },
    )
    for record in transcript:
        log.append("user/message", {"content": str(record.get("question", ""))})
        log.append("assistant/message", {"content": str(record.get("answer", ""))})
    for gate in gates:
        log.append(
            "gate/decision",
            {
                "node": str(gate.get("node", "")),
                "kind": str(gate.get("kind", "")),
                "attempts": int(gate.get("attempts", 0)),
                "rejections": int(gate.get("rejections", 0)),
                "last_decision": str(gate.get("last_decision", "")),
                "escalated": bool(gate.get("escalated", False)),
            },
        )
    return log


def migrate_v06_to_jsonl(session_json: Mapping[str, Any], path: str | Path) -> bool:
    """迁移 v0.6 会话到 JSONL；目标已存在（含 header）时幂等跳过，返回 False。"""
    path = Path(path)
    if path.exists() and path.stat().st_size > 0:
        first = path.read_text(encoding="utf-8").splitlines()[0]
        try:
            raw = json.loads(first)
        except json.JSONDecodeError:
            return False
        if raw.get("type") == "session" and int(raw.get("version", 0)) <= SESSION_FORMAT_VERSION:
            return False
    log = migrate_v06_session(session_json)
    JsonlSessionLogStore.append(
        log,
        path,
        build_header_line(
            log.session_id,
            cwd=str(session_json.get("workspace", "")),
            parent_session=str(session_json.get("parent_session_id", "")) or None,
            seed_length=len(session_json.get("transcript") or []),
        ),
    )
    return True
