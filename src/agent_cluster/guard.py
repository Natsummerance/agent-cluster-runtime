"""工具 guard 契约（v0.7 Task 14.5，dsh guard 移植）。

- 合作式超时：``TOOL_TIMEOUT`` 结构化错误（只增强不授权，与审批正交）。
- 连续重复调用提醒：同一工具同一输入签名连续 N 次后提醒（不阻止执行）。
"""

from __future__ import annotations

import asyncio
from typing import Any, Awaitable

__all__ = ["ToolGuard", "ToolTimeoutError"]


class ToolTimeoutError(TimeoutError):
    """工具合作式超时错误（结构化：code=TOOL_TIMEOUT）。"""

    def __init__(self, tool: str, timeout_s: float) -> None:
        self.code = "TOOL_TIMEOUT"
        self.tool = tool
        self.timeout_s = timeout_s
        super().__init__(f"tool {tool!r} exceeded cooperative timeout {timeout_s}s")


class ToolGuard:
    """合作式超时 + 重复调用提醒。"""

    def __init__(self, default_timeout_s: float = 120, reminder_after: int = 3) -> None:
        self.default_timeout_s = default_timeout_s
        self.reminder_after = reminder_after
        self._last_tool: str = ""
        self._last_signature: str = ""
        self._consecutive = 0

    async def run_with_timeout(
        self,
        tool: str,
        coro: Awaitable[Any],
        timeout_s: float | None = None,
    ) -> Any:
        limit = self.default_timeout_s if timeout_s is None else timeout_s
        try:
            return await asyncio.wait_for(coro, timeout=limit)
        except asyncio.TimeoutError as exc:
            raise ToolTimeoutError(tool, limit) from exc

    def check_repeat(self, tool: str, signature: str) -> str | None:
        """连续相同（工具+签名）调用达到阈值时返回提醒文案；不同调用重置计数。"""
        if tool == self._last_tool and signature == self._last_signature:
            self._consecutive += 1
        else:
            self._last_tool = tool
            self._last_signature = signature
            self._consecutive = 1
        if self._consecutive >= self.reminder_after:
            return (
                f"reminder: tool {tool!r} has been called {self._consecutive} consecutive "
                "times with the same input; consider a different approach"
            )
        return None
