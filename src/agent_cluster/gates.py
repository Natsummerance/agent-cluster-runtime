"""审批门（HITL interrupt）：设计文档 §5.4 审批门 + §6.5 bypass-immune 无人值守安全策略。

职责：
- ``make_gate_handler``：构造注册进 ``WorkflowEngine`` 的 "gate" 节点 handler；
  首次执行以 ``interrupt()`` 挂起等待人工审批（挂起后 ``run()`` 产出
  ``workflow_suspended`` 事件），恢复时 ``interrupt()`` 返回 ``HumanResponse``，
  handler 把审批结论落成 ``ApprovalRecord`` 并写入 ``gate_payloads`` / ``decisions``
  通道（Task 3 门路由契约：``gate_payloads[node.gate].decisions[-1].type`` 驱动条件路由）。
- ``approval_pending``：从 checkpointer 读取当前挂起的审批请求（供 CLI/测试）。
- ``resolve_auto_response``：无人值守自动审批策略（accept/reject/ask）；
  ``bypass_immune=True`` 的高风险门在无人值守 accept 时自动转为拒绝（§6.5 自动 DENY）。

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


class GateError(Exception):
    """审批门配置错误（gate 节点缺少类别、无人值守模式非法等）。"""


def _now_utc() -> datetime:
    """返回当前 UTC 时间（审批记录时间戳）。"""
    return datetime.now(timezone.utc)


def make_gate_handler(
    role_scope: dict[str, GateKind] | None = None,
    gate: ApprovalGate | None = None,
) -> NodeHandler:
    """构造 "gate" 节点 handler：interrupt 挂起 → 恢复后落审批记录并返回路由更新。

    参数：
    - ``role_scope``：可选的岗位审批范围映射（岗位 id -> 可审批的 GateKind）。
      本任务仅作为治理元信息接收（Task 6/7 角色治理使用），不改变审批行为。
    - ``gate``：可选 ``ApprovalGate`` 模型实例；提供时使用其 ``interrupt_config``
      作为中断选项，缺省 ``HumanInterruptConfig()``（全部允许 True）。

    handler 从 gate 节点构造 ``ActionRequest``（id=节点 id、kind=节点 gate 类别、
    title/description 取节点或流程规格、risk_level="medium"、bypass_immune=False），
    调用 ``interrupt([HumanInterrupt(...)])`` 挂起；恢复后把 ``HumanResponse``
    写成 ``ApprovalRecord(by_role="human", ...)``，返回 LangGraph channel 更新：
    ``{"gate_payloads": {node.gate: ActionRequest}, "decisions": [ApprovalRecord]}``。
    """
    interrupt_config = gate.interrupt_config if gate is not None else HumanInterruptConfig()

    async def handler(state: ClusterState, node: WorkflowNode, ctx: NodeContext) -> dict[str, Any]:
        if node.gate is None:
            raise GateError(f"gate 节点 {node.id!r} 缺少 gate 类别配置（node.gate 为 None）")
        if gate is not None and gate.kind != node.gate:
            raise GateError(
                f"ApprovalGate {gate.id!r} 的类别 {gate.kind!r} 与 gate 节点 {node.id!r} "
                f"的类别 {node.gate!r} 不一致"
            )
        title = f"{node.gate.value} 审批"
        description = ctx.spec.description or f"等待人工审批：节点 {node.id}（{node.gate.value}）"
        request = ActionRequest(
            id=node.id,
            kind=node.gate,
            title=title,
            description=description,
            evidence={"node": node.id, "gate": node.gate.value, "run_id": ctx.run_id},
            risk_level="medium",
            bypass_immune=False,
        )
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