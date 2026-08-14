"""会话事件日志核心（v0.7 Task 14.2，dsh 契约移植）。

- ``SessionEvent``：append-only 事件条目（type/seq/ts/payload/ignorable）。
- ``KNOWN_SESSION_EVENT_TYPES``：事件词汇生成集（``domain/verb`` 命名，对照 dsh
  ``known-event-types``，初始集由本模块定义；14.8 生成器接管 freshness 校验）。
- ``SessionEventLog``：内存 append-only 日志；``derive_messages()`` 表面投影；
  ``request_payload()`` 确定性请求派生；``verify_derivation()`` 强制执行
  **model-visible ⟺ logged** 不变量（对照 dsh ``agent-loop/src/invariant.ts``）。

契约出处见 ``docs/porting/2026-08-14-dsh-porting.md``（MIT，dsh ``47f943859b``）。
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Mapping

__all__ = [
    "InvariantViolationError",
    "KNOWN_SESSION_EVENT_TYPES",
    "SessionEvent",
    "SessionEventLog",
    "UnknownEventTypeError",
]

# surface 事件：模型可见 ⟺ 已入日志 的唯一投影源（其余为 durable 事实）。
_SURFACE_EVENTS = frozenset({"user/message", "assistant/message", "tool/result"})

KNOWN_SESSION_EVENT_TYPES: frozenset[str] = frozenset(
    {
        # 会话生命周期
        "session/start",
        "session/end",
        "session/forked",
        "session/title",
        # 轮次/步骤
        "turn/start",
        "turn/end",
        "step/start",
        "step/end",
        # 表面事件（模型可见）
        "user/message",
        "assistant/message",
        "tool/call",
        "tool/result",
        # 审批与门
        "approval/asked",
        "approval/decided",
        "gate/decision",
        "gate/auto",
        "gate/escalate",
        # 会议
        "meeting/start",
        "meeting/end",
        "meeting/decision",
        # 账本与预算
        "ledger/entry",
        "ledger/fork",
        "budget/alert",
        "budget/approved",
        "budget/denied",
        # 注入与迁移
        "stdin/applied",
        "migration/restored",
        # LLM 缓存统计（14.6 消费）
        "llm/cache",
    }
)


class UnknownEventTypeError(ValueError):
    """append 了词汇外的事件类型（fail loud，对照 dsh 读取路径拒绝语义）。"""


class InvariantViolationError(AssertionError):
    """model-visible ⟺ logged 不变量被破坏。"""


@dataclass(frozen=True)
class SessionEvent:
    """一条不可变会话事件。"""

    type: str
    seq: int
    ts: float
    payload: Mapping[str, Any] = field(default_factory=dict)
    ignorable: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "seq": self.seq,
            "ts": self.ts,
            "ignorable": self.ignorable,
            "payload": dict(self.payload),
        }


class SessionEventLog:
    """append-only 会话事件日志（内存实现，持久化见 14.3）。"""

    def __init__(self, session_id: str = "") -> None:
        self.session_id = session_id
        self._events: list[SessionEvent] = []

    @property
    def events(self) -> tuple[SessionEvent, ...]:
        """只读事件序列（append-only 不变量）。"""
        return tuple(self._events)

    @property
    def next_seq(self) -> int:
        return len(self._events) + 1

    def append(
        self,
        type: str,
        payload: Mapping[str, Any] | None = None,
        *,
        ignorable: bool = False,
    ) -> SessionEvent:
        """追加一条事件；未知类型立即抛错（fail loud）。"""
        if type not in KNOWN_SESSION_EVENT_TYPES:
            raise UnknownEventTypeError(
                f"unknown session event type {type!r} (known set: {len(KNOWN_SESSION_EVENT_TYPES)} types)"
            )
        event = SessionEvent(
            type=type,
            seq=self.next_seq,
            ts=time.time(),
            payload=dict(payload) if payload else {},
            ignorable=ignorable,
        )
        self._events.append(event)
        return event

    def derive_messages(self) -> list[dict[str, Any]]:
        """表面投影：仅 user/assistant message 与 tool/result 进入模型历史。"""
        messages: list[dict[str, Any]] = []
        for event in self._events:
            if event.type == "user/message":
                messages.append({"role": "user", "content": str(event.payload.get("content", ""))})
            elif event.type == "assistant/message":
                messages.append({"role": "assistant", "content": str(event.payload.get("content", ""))})
            elif event.type == "tool/result":
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": str(event.payload.get("tool_call_id", "")),
                        "content": str(event.payload.get("content", "")),
                    }
                )
        return messages

    def request_payload(
        self,
        system_prompt: str,
        tools: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """确定性请求派生：system + tools + 消息投影，顺序字节级稳定。

        对应 dsh ``request/header`` 折叠：同一日志必然产出同一请求 JSON。
        """
        return {
            "system": system_prompt,
            "tools": list(tools) if tools else [],
            "messages": self.derive_messages(),
        }

    def verify_derivation(self, messages: list[dict[str, Any]]) -> None:
        """强制执行 model-visible ⟺ logged：派生消息与请求消息 JSON 串必须一致。"""
        derived = self.derive_messages()
        if json.dumps(derived, ensure_ascii=False, sort_keys=False) != json.dumps(
            messages, ensure_ascii=False, sort_keys=False
        ):
            raise InvariantViolationError(
                "model-visible != logged: request messages diverge from session event log "
                f"(derived={len(derived)} messages, request={len(messages)} messages)"
            )
