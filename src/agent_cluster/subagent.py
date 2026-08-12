"""有界子代理（v0.4 T11.7，参考 openhands/swe-agent 的 issue→PR 与子任务拆分思路）。

- ``BoundedSubagent``：独立 ReAct 循环——子任务 prompt + 工具 schema -> 逐轮
  ``complete_with_tools`` -> 执行工具 -> 结果回传；带 **token 预算上限** 与
  ``max_rounds`` 双截断，超限/耗尽即返回（不无限循环）。
- 子代理只使用只读 + 工作区写工具（不内置危险工具；run_subagent 本身是危险
  工具，需人工审批，--yes 自动拒绝）。
- ``SubagentBroker``：持有 client 工厂与 usage hook，把子代理消耗的 token 记入
  现有管线（runtime.report_usage / TokenLedger），事件与计量不旁路。
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import Any, Callable

from agent_cluster.tools import ToolCall, ToolPermission, ToolResult, ToolSession
from agent_cluster.tools import ToolSession as _ToolSession

__all__ = ["BoundedSubagent", "SubagentBroker", "SubagentResult", "register_subagent_tool"]

DEFAULT_SUBAGENT_BUDGET = 20_000
DEFAULT_SUBAGENT_ROUNDS = 6


@dataclass
class SubagentResult:
    """子代理执行结果（结果/产物回传契约）。"""

    ok: bool
    text: str
    tool_calls: int = 0
    tokens_used: int = 0
    rounds: int = 0
    truncated: bool = False  # 预算或轮数截断


class BoundedSubagent:
    """有界 ReAct 子代理：独立上下文 + token 预算上限 + 最大轮数。"""

    def __init__(
        self,
        *,
        client: Any,
        session: ToolSession,
        max_rounds: int = DEFAULT_SUBAGENT_ROUNDS,
        token_budget: int = DEFAULT_SUBAGENT_BUDGET,
        ledger: Any | None = None,
        persona: str = "子代理",
        usage_hook: Callable[[Any, str], None] | None = None,
    ) -> None:
        self.client = client
        self.session = session
        self.max_rounds = max_rounds if max_rounds and max_rounds > 0 else DEFAULT_SUBAGENT_ROUNDS
        self.token_budget = token_budget if token_budget and token_budget > 0 else DEFAULT_SUBAGENT_BUDGET
        self.ledger = ledger
        self.persona = persona
        self.usage_hook = usage_hook

    # ------------------------------------------------------------------
    # 主循环
    # ------------------------------------------------------------------

    async def run(self, task: str, *, context: str = "") -> SubagentResult:
        """执行子任务：ReAct 循环直到完成/轮数耗尽/预算超限。"""
        allowed = [
            spec.name
            for spec in self.session.registry.list()
            if spec.permission in (ToolPermission.READ, ToolPermission.WORKSPACE_WRITE)
        ]
        schemas = self.session.registry.as_openai_schemas(names=sorted(allowed))
        system_parts = [
            f"{self.persona}（有界子代理）：专注完成单个子任务。",
            f"工作区根目录：{self.session.workspace_root}",
            f"可用工具：{', '.join(sorted(allowed)) or '（无）'}",
            "约束：只做任务范围内的事；完成即输出最终结论文本，不要继续调用工具。",
        ]
        if context:
            system_parts.append(f"上下文：{context[:2000]}")
        messages: list[dict] = [
            {"role": "system", "content": "\n".join(system_parts)},
            {"role": "user", "content": task},
        ]
        tokens_used = 0
        tool_calls = 0
        truncated = False
        final_text = ""

        for round_no in range(1, self.max_rounds + 1):
            if tokens_used > self.token_budget:
                truncated = True
                final_text = f"{self.persona}：token 预算超限（{tokens_used}>{self.token_budget}），提前结束。"
                break
            try:
                response = await self.client.complete_with_tools(messages, schemas)
            except Exception as exc:  # noqa: BLE001 —— 子代理模型故障按失败返回
                return SubagentResult(
                    ok=False,
                    text=f"{self.persona}：模型调用失败：{type(exc).__name__}: {exc}",
                    tool_calls=tool_calls,
                    tokens_used=tokens_used,
                    rounds=round_no - 1,
                    truncated=False,
                )
            if response.usage is not None:
                tokens_used += response.usage.total_tokens
                self._record_usage(response.usage)
            if not response.tool_calls:
                final_text = response.text or f"{self.persona}：任务完成。"
                return SubagentResult(
                    ok=True,
                    text=final_text,
                    tool_calls=tool_calls,
                    tokens_used=tokens_used,
                    rounds=round_no,
                    truncated=False,
                )
            for call in response.tool_calls:
                tool_calls += 1
                result = await self._execute(call)
                messages.append(
                    {
                        "role": "user",
                        "content": f"[工具结果 {call.name} ok={result.ok}] {result.output[:1500]}",
                    }
                )
        else:
            truncated = True
            final_text = f"{self.persona}：达到最大轮数（{self.max_rounds}），任务未完全收敛。"
        return SubagentResult(
            ok=not truncated,
            text=final_text,
            tool_calls=tool_calls,
            tokens_used=tokens_used,
            rounds=self.max_rounds,
            truncated=truncated,
        )

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    async def _execute(self, call: ToolCall) -> ToolResult:
        """执行工具；子代理不内置危险/人工工具——需要审批的调用记为拒绝。"""
        result = await self.session.execute(call)
        if not result.needs_approval:
            return result
        return ToolResult(
            id=call.id,
            name=call.name,
            ok=False,
            output=f"子代理无审批权：{call.name} 被跳过（需在父循环人工审批）",
            error="needs_approval",
            args=dict(call.args),
        )

    def _record_usage(self, usage: Any) -> None:
        """把消耗记入 ledger/usage_hook（异常不中断子代理）。"""
        try:
            if self.usage_hook is not None:
                self.usage_hook(usage, self.persona)
            elif self.ledger is not None:
                self.ledger.record(role=self.persona, phase="subagent", usage=usage)
        except Exception:  # noqa: BLE001 —— 记账失败不阻断
            pass


class SubagentBroker:
    """把 run_subagent 工具绑定到 client 工厂与 usage hook（注册进 ToolRegistry）。"""

    def __init__(
        self,
        *,
        client_factory: Callable[[str], Any],
        usage_hook: Callable[[Any, str], None] | None = None,
        default_role: str = "backend",
    ) -> None:
        self.client_factory = client_factory
        self.usage_hook = usage_hook
        self.default_role = default_role

    async def handle(self, session: ToolSession, args: dict) -> dict[str, Any]:
        """run_subagent 工具 handler：解析参数 -> 派生子代理 -> 回传结果。"""
        task = str(args.get("task", "")).strip()
        if not task:
            from agent_cluster.tools import ToolError

            raise ToolError("run_subagent 需要 task 参数（子任务描述）")
        role_id = str(args.get("role") or self.default_role)
        budget = int(args.get("budget") or DEFAULT_SUBAGENT_BUDGET)
        rounds = int(args.get("max_rounds") or DEFAULT_SUBAGENT_ROUNDS)
        persona = str(args.get("persona") or f"{role_id} 子代理")
        context = str(args.get("context") or "")
        # 独立子会话：子代理在父会话写锁内运行，若直接复用父 ToolSession，
        # 子代理内部的写工具会再次获取同一把 asyncio.Lock 造成自锁。
        # 子会话绑定同一工作区根目录，自带写锁与审计（不共享审批缓存）。
        child = _ToolSession(
            session.workspace_root,
            registry=session.registry,
            shell_whitelist=session.shell_whitelist,
            sandbox=session.sandbox,
        )
        sub = BoundedSubagent(
            client=self.client_factory(role_id),
            session=child,
            max_rounds=rounds,
            token_budget=budget,
            persona=persona,
            usage_hook=self.usage_hook,
        )
        result = await sub.run(task, context=context)
        return {
            "ok": result.ok,
            "output": result.text,
            "error": None if result.ok else "子代理未收敛",
            "meta": {
                "tool_calls": result.tool_calls,
                "tokens_used": result.tokens_used,
                "rounds": result.rounds,
                "truncated": result.truncated,
            },
        }


def register_subagent_tool(registry: Any, broker: SubagentBroker) -> None:
    """把 ``run_subagent``（危险权限）注册进工具注册表。

    - 危险权限：派生子代理会消耗 token/执行工作区写操作，需人工审批；
      ``--yes`` 下自动拒绝（与其它危险工具语义一致）。
    - 幂等：重复注册直接返回。
    """
    if "run_subagent" in registry.names():
        return
    from agent_cluster.tools import ToolPermission, ToolSpec

    registry.register(
        ToolSpec(
            name="run_subagent",
            description=(
                "派生出有界子代理完成独立子任务（如实现某模块/写某文件）："
                "带 token 预算上限与最大轮数，完成后回传结果文本。危险：消耗 token 并写工作区。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "task": {"type": "string", "description": "子任务描述（必填）"},
                    "context": {"type": "string", "description": "附加上下文（可选）"},
                    "role": {"type": "string", "description": "子代理模型岗位（可选，缺省 backend）"},
                    "budget": {"type": "integer", "description": f"token 预算上限（可选，缺省 {DEFAULT_SUBAGENT_BUDGET}）"},
                    "max_rounds": {"type": "integer", "description": f"最大 ReAct 轮数（可选，缺省 {DEFAULT_SUBAGENT_ROUNDS}）"},
                    "persona": {"type": "string", "description": "子代理身份标签（可选）"},
                },
                "required": ["task"],
            },
            permission=ToolPermission.DANGEROUS,
            handler=broker.handle,
        )
    )
