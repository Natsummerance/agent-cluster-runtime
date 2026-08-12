"""审批门（HITL interrupt）：设计文档 §5.4 审批门 + §6.5 bypass-immune 无人值守安全策略。

职责：
- ``make_gate_handler``：构造注册进 ``WorkflowEngine`` 的 "gate" 节点 handler；
  ``auto_mode="ask"``（缺省）以 ``interrupt()`` 挂起等待人工审批（挂起后 ``run()``
  产出 ``workflow_suspended`` 事件），恢复时 ``interrupt()`` 返回 ``HumanResponse``，
  handler 把审批结论落成 ``ApprovalRecord`` 并写入 ``gate_payloads`` / ``decisions``
  通道（Task 3 门路由契约：``gate_payloads[node.gate].decisions[-1].type`` 驱动条件路由）；
  ``auto_mode != "ask"`` 时按无人值守策略直接落 ``bypass-immune`` 结论，不挂起。
- ``approval_pending``：从 checkpointer 读取当前挂起的审批请求（供 CLI/测试）。
- ``resolve_auto_response``：无人值守自动审批策略（accept/reject/ask）；
  ``bypass_immune=True`` 的高风险门在无人值守 accept 时自动转为拒绝（§6.5 自动 DENY）。

bypass-immune 缺省推导（Task 7 契约）：``dangerous_tool`` / ``evolution_apply``
两类高风险门缺省 ``bypass_immune=True``（``risk_level="high"``），其余门
``bypass_immune=False``（``risk_level="medium"``）；均可经 ``gate`` 覆盖。

兼容说明（installed langgraph 1.2.11）：
- ``interrupt()`` 以 ``__interrupt__`` 流步挂起（不抛异常），恢复时原样返回
  ``Command(resume=...)`` 的响应；因此 ``interrupt([payload])`` 的返回值可能是
  list（首挂起语义）或直接是 ``HumanResponse``（恢复语义），需归一化处理。
- 挂起状态在 ``StateSnapshot.interrupts``（元素为 ``Interrupt(value, id)``），
  不在 ``values["__interrupt__"]`` 中；``approval_pending`` 两者都兼容。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from langgraph.types import interrupt

from agent_cluster.models import (
    ActionRequest,
    ApprovalGate,
    ApprovalRecord,
    ClusterState,
    GateKind,
    HumanInterruptConfig,
    HumanResponse,
)
from agent_cluster.workflow import NodeContext, NodeHandler, WorkflowNode

__all__ = ["GateError", "make_gate_handler", "approval_pending", "resolve_auto_response"]

AUTO_DENY_REASON = "bypass-immune: 无人值守自动拒绝"

# 缺省 bypass-immune 的高风险门类别（§6.5：无人值守禁止自动放行）
BY_PASS_IMMUNE_KINDS: frozenset[GateKind] = frozenset(
    {GateKind.DANGEROUS_TOOL, GateKind.EVOLUTION_APPLY}
)


class GateError(Exception):
    """审批门配置错误（gate 节点缺少类别、无人值守模式非法等）。"""


def _now_utc() -> datetime:
    """返回当前 UTC 时间（审批记录时间戳）。"""
    return datetime.now(timezone.utc)


def make_gate_handler(
    role_scope: dict[str, GateKind] | None = None,
    gate: ApprovalGate | dict[str, Any] | None = None,
    auto_mode: str = "ask",
) -> NodeHandler:
    """构造 "gate" 节点 handler：interrupt 挂起 → 恢复后落审批记录并返回路由更新。

    参数：
    - ``role_scope``：可选的岗位审批范围映射（岗位 id -> 可审批的 GateKind）。
      仅作为治理元信息接收（Task 6/7 角色治理使用），不改变审批行为。
    - ``gate``：可选覆盖项——``ApprovalGate`` 模型实例或 ``dict`` 覆盖映射。
      - ``ApprovalGate``：使用其 ``interrupt_config`` 作为中断选项；若其
        ``payload`` 显式设置了 ``bypass_immune``/``risk_level``（按 pydantic
        ``model_fields_set`` 判断），则覆盖按门类别推导的默认值。
      - ``dict``：键可为 ``bypass_immune``/``risk_level``/``interrupt_config``
        （``interrupt_config`` 接受 ``HumanInterruptConfig`` 或等价 dict），
        以及 ``kind``（提供时校验与 gate 节点类别一致）。
    - ``auto_mode``：无人值守审批模式（"ask"/"accept"/"reject"），缺省 "ask"。
      - ``"ask"``（缺省）：保持 interrupt() 挂起等待人工审批。
      - 非 "ask"：不调用 interrupt()，直接按 ``resolve_auto_response`` 得出
        ``HumanResponse`` 并落 ``ApprovalRecord(by_role="system")`` 返回通道更新，
        无人值守运行永不挂起（§6.5：bypass-immune + accept 自动转为拒绝）。

    handler 从 gate 节点构造 ``ActionRequest``（id=节点 id、kind=节点 gate 类别、
    title/description 取节点或流程规格；``bypass_immune`` 按门类别推导——
    ``dangerous_tool``/``evolution_apply`` 缺省 True 且 ``risk_level="high"``，
    其余 False 且 ``risk_level="medium"``——可用 ``gate`` 覆盖）。
    调用 ``interrupt([HumanInterrupt(...)])`` 挂起；恢复后把 ``HumanResponse``
    写成 ``ApprovalRecord(by_role="human", ...)``，返回 LangGraph channel 更新：
    ``{"gate_payloads": {node.gate: ActionRequest}, "decisions": [ApprovalRecord]}``。
    """
    if auto_mode not in ("ask", "accept", "reject"):
        raise GateError(f"未知的无人值守模式：{auto_mode!r}（仅支持 accept/reject/ask）")

    interrupt_config = HumanInterruptConfig()
    overrides: dict[str, Any] = {}
    if isinstance(gate, ApprovalGate):
        interrupt_config = gate.interrupt_config
        if "bypass_immune" in gate.payload.model_fields_set:
            overrides["bypass_immune"] = gate.payload.bypass_immune
        if "risk_level" in gate.payload.model_fields_set:
            overrides["risk_level"] = gate.payload.risk_level
    elif isinstance(gate, dict):
        raw_interrupt_config = gate.get("interrupt_config")
        if raw_interrupt_config is not None:
            interrupt_config = HumanInterruptConfig.model_validate(raw_interrupt_config)
        for key in ("bypass_immune", "risk_level"):
            if key in gate:
                overrides[key] = gate[key]

    async def handler(state: ClusterState, node: WorkflowNode, ctx: NodeContext) -> dict[str, Any]:
        if node.gate is None:
            raise GateError(f"gate 节点 {node.id!r} 缺少 gate 类别配置（node.gate 为 None）")
        if isinstance(gate, ApprovalGate) and gate.kind != node.gate:
            raise GateError(
                f"ApprovalGate {gate.id!r} 的类别 {gate.kind!r} 与 gate 节点 {node.id!r} "
                f"的类别 {node.gate!r} 不一致"
            )
        if isinstance(gate, dict) and gate.get("kind") is not None and gate.get("kind") != node.gate:
            raise GateError(
                f"gate 覆盖配置的类别 {gate.get('kind')!r} 与 gate 节点 {node.id!r} "
                f"的类别 {node.gate!r} 不一致"
            )
        title = f"{node.gate.value} 审批"
        description = ctx.spec.description or f"等待人工审批：节点 {node.id}（{node.gate.value}）"
        bypass_immune_default = node.gate in BY_PASS_IMMUNE_KINDS
        risk_level_default = "high" if bypass_immune_default else "medium"
        request = ActionRequest(
            id=node.id,
            kind=node.gate,
            title=title,
            description=description,
            evidence={"node": node.id, "gate": node.gate.value, "run_id": ctx.run_id},
            risk_level=overrides.get("risk_level", risk_level_default),
            bypass_immune=overrides.get("bypass_immune", bypass_immune_default),
        )
        if auto_mode != "ask":
            decision = resolve_auto_response(request, auto_mode)
            record = ApprovalRecord(
                by_role="system",
                type=decision.type,
                args=decision.args,
                ts=_now_utc(),
            )
            request.decisions.append(record)
            return {"gate_payloads": {node.gate: request}, "decisions": [record]}
        human_interrupt: dict[str, Any] = {
            "action_request": request,
            "config": interrupt_config.model_dump(),
            "description": request.description,
        }
        resumed = interrupt([human_interrupt])
        decision = resumed[0] if isinstance(resumed, list) else resumed
        if not isinstance(decision, HumanResponse):
            decision = HumanResponse.model_validate(decision)
        record = ApprovalRecord(
            by_role="human",
            type=decision.type,
            args=decision.args,
            ts=_now_utc(),
        )
        request.decisions.append(record)
        return {"gate_payloads": {node.gate: request}, "decisions": [record]}

    return handler


def approval_pending(graph: Any, thread_id: str) -> ActionRequest | None:
    """查询 checkpointer 状态中当前挂起的审批请求，返回其 ``action_request``。

    - langgraph 0.2.x：挂起状态在 ``state.values["__interrupt__"]``（HumanInterrupt 列表）。
    - langgraph 1.x（installed 1.2.11）：挂起状态在 ``state.interrupts``（``Interrupt``
      元组，``Interrupt.value`` 为传给 ``interrupt()`` 的载荷）。
    两者均兼容；无挂起审批或载荷缺 ``action_request`` 时返回 None。
    """
    snapshot = graph.get_state({"configurable": {"thread_id": thread_id}})
    pending = snapshot.values.get("__interrupt__")
    if pending is None:
        pending = getattr(snapshot, "interrupts", None)
    if not pending:
        return None
    first = pending[0]
    payload = getattr(first, "value", None)
    if payload is None:
        payload = first
    if isinstance(payload, list):
        payload = payload[0] if payload else None
    if payload is None:
        return None
    action_request = payload.get("action_request") if isinstance(payload, dict) else None
    if action_request is None:
        return None
    return ActionRequest.model_validate(action_request)


def resolve_auto_response(req: ActionRequest, auto_mode: str) -> HumanResponse:
    """无人值守自动审批策略（§6.5 安全约束）。

    - ``auto_mode="accept"``：自动放行；但 ``req.bypass_immune=True``（高风险门）
      时自动转为拒绝（原因 "bypass-immune: 无人值守自动拒绝"），禁止无人值守放行。
    - ``auto_mode="reject"``：一律自动拒绝。
    - ``auto_mode="ask"``：必须人工响应；无人值守下不允许自动决策，抛 ``GateError``。
    """
    if auto_mode == "ask":
        raise GateError("auto_mode='ask' 需要人工响应，不能在无人值守模式下自动决策")
    if auto_mode not in {"accept", "reject"}:
        raise GateError(f"未知的无人值守模式：{auto_mode!r}（仅支持 accept/reject/ask）")
    if auto_mode == "accept" and req.bypass_immune:
        return HumanResponse(type="reject", args={"reason": AUTO_DENY_REASON})
    return HumanResponse(type=auto_mode)