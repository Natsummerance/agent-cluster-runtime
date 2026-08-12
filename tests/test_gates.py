"""Task 4 行为测试：审批门（HITL interrupt）真实中断/恢复、条件路由、审计落盘、bypass-immune 无人值守自动拒绝。

不 mock 关键逻辑：通过 WorkflowEngine + make_gate_handler 注册 "gate" handler，
用 MemorySaver 跑真实 interrupt() 挂起与 Command(resume=...) 恢复。
"""

from __future__ import annotations

import pytest

from langgraph.checkpoint.memory import MemorySaver

from agent_cluster.gates import (
    GateError,
    approval_pending,
    make_gate_handler,
    resolve_auto_response,
)
from agent_cluster.models import (
    ActionRequest,
    ApprovalGate,
    ClusterState,
    GateKind,
    HumanInterruptConfig,
    HumanResponse,
)
from agent_cluster.workflow import WorkflowEngine

THREAD_ID = "proj:demo:iter:1"

SIMPLE_GATE_YAML = """
name: release-gate-flow
description: 发布门：人工确认后发布
max_iterations: 10
thread_id: "proj:demo:iter:1"
nodes:
  - {id: start, type: start}
  - {id: release_gate, type: gate, gate: release}
  - {id: end, type: end}
edges:
  - {from: start, to: release_gate}
  - {from: release_gate, to: end, on_accept: end, on_reject: end}
"""

ROUTING_GATE_YAML = """
name: gate-routing-flow
max_iterations: 20
thread_id: "proj:demo:iter:1"
nodes:
  - {id: start, type: start}
  - {id: quality_gate, type: gate, gate: iteration_acceptance}
  - {id: rework, type: agent, role: backend}
  - {id: end, type: end}
edges:
  - {from: start, to: quality_gate}
  - {from: quality_gate, to: end, on_accept: end, on_reject: rework, on_edit: rework, on_response: end}
  - {from: rework, to: quality_gate}
"""


def _compile_flow(
    flow_yaml: str,
    role_scope: dict[str, GateKind] | None = None,
    gate: ApprovalGate | None = None,
):
    """编译流程并注册真实 gate handler（interrupt HITL）。"""
    handler = make_gate_handler(role_scope=role_scope, gate=gate)
    return WorkflowEngine(handlers={"gate": handler}).compile(flow_yaml)


def _graph_with_checkpointer(compiled, checkpointer):
    """构造绑定 checkpointer 的已编译图（approval_pending / 读取终态需要）。"""
    return compiled._compile_graph(checkpointer=checkpointer)


def _final_state(compiled, checkpointer) -> ClusterState:
    """读取线程最终 ClusterState（含 decisions/gate_payloads 审计字段）。"""
    snapshot = _graph_with_checkpointer(compiled, checkpointer).get_state(
        {"configurable": {"thread_id": THREAD_ID}}
    )
    return ClusterState.model_validate(snapshot.values)


# ---------------------------------------------------------------------------
# 1. 首次运行挂起 + approval_pending 读取挂起审批
# ---------------------------------------------------------------------------


async def test_first_run_suspends_and_approval_pending_returns_request():
    checkpointer = MemorySaver()
    compiled = _compile_flow(SIMPLE_GATE_YAML)

    events = [event async for event in compiled.run(checkpointer=checkpointer)]
    assert events[-1].type == "workflow_suspended"
    assert events[-1].payload == {"node_id": "release_gate", "thread_id": THREAD_ID}
    assert [event.actor for event in events if event.type == "node_start"] == [
        "start",
        "release_gate",
    ]

    request = approval_pending(_graph_with_checkpointer(compiled, checkpointer), THREAD_ID)
    assert request is not None
    assert request.id == "release_gate"
    assert request.kind == GateKind.RELEASE
    assert request.title == "release 审批"
    assert request.risk_level == "medium"
    assert request.bypass_immune is False
    assert request.decisions == []


# ---------------------------------------------------------------------------
# 2. accept 恢复：流程走完 + decisions 通道落一条 ApprovalRecord
# ---------------------------------------------------------------------------


async def test_accept_resume_completes_flow_and_records_decision():
    checkpointer = MemorySaver()
    compiled = _compile_flow(SIMPLE_GATE_YAML, role_scope={"pm": GateKind.RELEASE})
    _ = [event async for event in compiled.run(checkpointer=checkpointer)]

    resume_events = [
        event
        async for event in compiled.resume(
            THREAD_ID, HumanResponse(type="accept"), checkpointer=checkpointer
        )
    ]
    assert resume_events[-1].type == "workflow_end"
    # 挂起节点恢复后重新执行，accept 路由到 end
    assert [event.actor for event in resume_events if event.type == "node_start"] == [
        "release_gate",
        "end",
    ]

    state = _final_state(compiled, checkpointer)
    assert len(state.decisions) == 1
    record = state.decisions[0]
    assert record.type == "accept"
    assert record.by_role == "human"
    assert record.ts is not None
    assert state.gate_payloads[GateKind.RELEASE].decisions[-1].type == "accept"


# ---------------------------------------------------------------------------
# 3. reject / edit 恢复：按 on_reject / on_edit 分支路由（返工再入 gate）
# ---------------------------------------------------------------------------


async def test_reject_resume_routes_to_rework_and_re_gates():
    checkpointer = MemorySaver()
    compiled = _compile_flow(ROUTING_GATE_YAML)
    _ = [event async for event in compiled.run(checkpointer=checkpointer)]

    reject_events = [
        event
        async for event in compiled.resume(
            THREAD_ID, HumanResponse(type="reject", args={"reason": "验收不达标"}), checkpointer=checkpointer
        )
    ]
    assert reject_events[-1].type == "workflow_suspended"
    # reject → rework 节点运行 → 重新进入 gate 再次挂起
    assert [event.actor for event in reject_events if event.type == "node_start"] == [
        "quality_gate",
        "rework",
        "quality_gate",
    ]
    second_request = approval_pending(_graph_with_checkpointer(compiled, checkpointer), THREAD_ID)
    assert second_request is not None
    assert second_request.kind == GateKind.ITERATION_ACCEPTANCE

    accept_events = [
        event
        async for event in compiled.resume(
            THREAD_ID, HumanResponse(type="accept"), checkpointer=checkpointer
        )
    ]
    assert accept_events[-1].type == "workflow_end"
    assert [event.actor for event in accept_events if event.type == "node_start"] == [
        "quality_gate",
        "end",
    ]

    state = _final_state(compiled, checkpointer)
    assert [record.type for record in state.decisions] == ["reject", "accept"]


async def test_edit_resume_routes_to_rework_branch():
    checkpointer = MemorySaver()
    compiled = _compile_flow(ROUTING_GATE_YAML)
    _ = [event async for event in compiled.run(checkpointer=checkpointer)]

    edit_events = [
        event
        async for event in compiled.resume(
            THREAD_ID,
            HumanResponse(type="edit", args={"text": "修正验收标准"}),
            checkpointer=checkpointer,
        )
    ]
    assert edit_events[-1].type == "workflow_suspended"
    assert [event.actor for event in edit_events if event.type == "node_start"] == [
        "quality_gate",
        "rework",
        "quality_gate",
    ]

    state = _final_state(compiled, checkpointer)
    assert state.decisions[-1].type == "edit"
    assert state.decisions[-1].args == {"text": "修正验收标准"}


# ---------------------------------------------------------------------------
# 4. bypass-immune 无人值守自动拒绝策略
# ---------------------------------------------------------------------------


def test_bypass_immune_auto_reject_policy():
    immune_request = ActionRequest(id="ar-immune", kind=GateKind.RELEASE, bypass_immune=True)
    denied = resolve_auto_response(immune_request, "accept")
    assert denied.type == "reject"
    assert denied.args == {"reason": "bypass-immune: 无人值守自动拒绝"}

    rejected = resolve_auto_response(immune_request, "reject")
    assert rejected.type == "reject"

    accepted = resolve_auto_response(ActionRequest(id="ar-plain", kind=GateKind.RELEASE), "accept")
    assert accepted.type == "accept"

    with pytest.raises(GateError, match="ask"):
        resolve_auto_response(immune_request, "ask")
    with pytest.raises(GateError, match="未知的无人值守模式"):
        resolve_auto_response(immune_request, "maybe")


# ---------------------------------------------------------------------------
# 5. 审计：审批记录 ts/args 完整落盘
# ---------------------------------------------------------------------------


async def test_audit_trail_record_ts_and_args():
    checkpointer = MemorySaver()
    compiled = _compile_flow(SIMPLE_GATE_YAML)
    _ = [event async for event in compiled.run(checkpointer=checkpointer)]
    _ = [
        event
        async for event in compiled.resume(
            THREAD_ID,
            HumanResponse(type="accept", args={"approver": "pm", "note": "发布窗口确认"}),
            checkpointer=checkpointer,
        )
    ]

    state = _final_state(compiled, checkpointer)
    assert len(state.decisions) == 1
    record = state.decisions[0]
    assert record.type == "accept"
    assert record.by_role == "human"
    assert record.args == {"approver": "pm", "note": "发布窗口确认"}
    assert record.ts is not None
    assert record.ts.tzinfo is not None  # now_utc 带时区


async def test_approval_pending_returns_none_after_completion():
    checkpointer = MemorySaver()
    compiled = _compile_flow(SIMPLE_GATE_YAML)
    _ = [event async for event in compiled.run(checkpointer=checkpointer)]
    _ = [
        event
        async for event in compiled.resume(
            THREAD_ID, HumanResponse(type="accept"), checkpointer=checkpointer
        )
    ]
    assert approval_pending(_graph_with_checkpointer(compiled, checkpointer), THREAD_ID) is None


# ---------------------------------------------------------------------------
# 附加：GateError 非法配置 + ApprovalGate interrupt_config 透传
# ---------------------------------------------------------------------------


async def test_gate_handler_rejects_gate_node_without_kind():
    bad_yaml = """
name: bad-gate-flow
max_iterations: 10
thread_id: "proj:demo:iter:1"
nodes:
  - {id: start, type: start}
  - {id: broken_gate, type: gate}
  - {id: end, type: end}
edges:
  - {from: start, to: broken_gate}
  - {from: broken_gate, to: end}
"""
    compiled = _compile_flow(bad_yaml)
    with pytest.raises(GateError, match="缺少 gate 类别"):
        _ = [event async for event in compiled.run()]


async def test_gate_factory_uses_provided_interrupt_config():
    checkpointer = MemorySaver()
    gate_model = ApprovalGate(
        id="release_gate",
        kind=GateKind.RELEASE,
        node="release_gate",
        interrupt_config=HumanInterruptConfig(
            allow_ignore=False, allow_respond=False, allow_edit=True, allow_accept=True
        ),
        payload=ActionRequest(id="ar-preset", kind=GateKind.RELEASE, title="预置载荷"),
    )
    compiled = _compile_flow(SIMPLE_GATE_YAML, gate=gate_model)
    _ = [event async for event in compiled.run(checkpointer=checkpointer)]

    snapshot = _graph_with_checkpointer(compiled, checkpointer).get_state(
        {"configurable": {"thread_id": THREAD_ID}}
    )
    payload = snapshot.interrupts[0].value[0]
    assert payload["config"] == {
        "allow_ignore": False,
        "allow_respond": False,
        "allow_edit": True,
        "allow_accept": True,
    }