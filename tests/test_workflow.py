"""Task 3 行为测试：YAML→StateGraph 编译、校验、事件流运行、gate 条件路由、parallel 并行、loop 防死循环。

不依赖 gates.py/roles.py/meetings.py：gate/agent handler 一律用测试内注入的 fake handler。
"""

from __future__ import annotations

import operator
import typing

import pytest

from agent_cluster.models import (
    ActionRequest,
    ApprovalRecord,
    ClusterState,
    Event,
    GateKind,
)
from agent_cluster.workflow import (
    CompiledWorkflow,
    NodeContext,
    WorkflowEngine,
    WorkflowLoopError,
    WorkflowNode,
    WorkflowValidationError,
)

GATE_AND_PARALLEL_YAML = """
name: demo-flow
description: 含 gate 条件路由与 parallel 的演示流程
max_iterations: 30
thread_id: "proj:demo:iter:1"
nodes:
  - {id: start, type: start}
  - {id: requirement_review, type: meeting, meeting: requirement_review}
  - {id: requirement_gate, type: gate, gate: requirement_confirmation}
  - {id: design, type: agent, role: architect}
  - {id: dev_fanout, type: parallel, children: [frontend_dev, backend_dev]}
  - {id: frontend_dev, type: agent, role: frontend}
  - {id: backend_dev, type: agent, role: backend}
  - {id: code_review, type: meeting, meeting: code_review}
  - {id: release_gate, type: gate, gate: release}
  - {id: end, type: end}
edges:
  - {from: start, to: requirement_review}
  - {from: requirement_review, to: requirement_gate}
  - {from: requirement_gate, to: design, on_accept: design, on_reject: requirement_review, on_edit: requirement_review}
  - {from: design, to: dev_fanout}
  - {from: dev_fanout, to: code_review}
  - {from: code_review, to: release_gate}
  - {from: release_gate, to: end, on_accept: end, on_reject: code_review}
"""

SIMPLE_YAML = """
name: simple
max_iterations: 10
thread_id: "proj:demo:iter:1"
nodes:
  - {id: start, type: start}
  - {id: code, type: agent, role: backend}
  - {id: review, type: meeting, meeting: code_review}
  - {id: end, type: end}
edges:
  - {from: start, to: code}
  - {from: code, to: review}
  - {from: review, to: end}
"""

GATE_YAML = """
name: gate-flow
max_iterations: 20
thread_id: "proj:demo:iter:1"
nodes:
  - {id: start, type: start}
  - {id: dev, type: agent, role: backend}
  - {id: quality_gate, type: gate, gate: iteration_acceptance}
  - {id: rework, type: agent, role: backend}
  - {id: end, type: end}
edges:
  - {from: start, to: dev}
  - {from: dev, to: quality_gate}
  - {from: quality_gate, to: end, on_accept: end, on_reject: rework, on_edit: rework, on_response: end}
  - {from: rework, to: quality_gate}
"""

PARALLEL_YAML = """
name: parallel-flow
max_iterations: 20
thread_id: "proj:demo:iter:1"
nodes:
  - {id: start, type: start}
  - {id: fanout, type: parallel, children: [fe, be]}
  - {id: fe, type: agent, role: frontend}
  - {id: be, type: agent, role: backend}
  - {id: end, type: end}
edges:
  - {from: start, to: fanout}
  - {from: fanout, to: end}
"""

LOOP_YAML = """
name: loop-flow
max_iterations: 4
thread_id: "proj:demo:iter:1"
nodes:
  - {id: start, type: start}
  - {id: dev, type: agent, role: backend}
  - {id: quality_gate, type: gate, gate: iteration_acceptance}
  - {id: rework, type: agent, role: backend}
  - {id: end, type: end}
edges:
  - {from: start, to: dev}
  - {from: dev, to: quality_gate}
  - {from: quality_gate, to: end, on_accept: end, on_reject: rework}
  - {from: rework, to: quality_gate}
"""


# ---------------------------------------------------------------------------
# 编译与图描述
# ---------------------------------------------------------------------------


def test_compile_valid_yaml_with_gate_and_parallel():
    compiled = WorkflowEngine().compile(GATE_AND_PARALLEL_YAML)
    assert isinstance(compiled, CompiledWorkflow)
    graph = compiled.get_graph()
    assert set(graph) == {"nodes", "edges"}
    node_ids = {node["id"] for node in graph["nodes"]}
    assert node_ids == {
        "start",
        "requirement_review",
        "requirement_gate",
        "design",
        "dev_fanout",
        "frontend_dev",
        "backend_dev",
        "code_review",
        "release_gate",
        "end",
    }
    by_id = {node["id"]: node for node in graph["nodes"]}
    assert by_id["start"]["type"] == "start"
    assert by_id["requirement_gate"]["type"] == "gate"
    assert by_id["requirement_gate"]["gate"] == "requirement_confirmation"
    assert by_id["dev_fanout"]["type"] == "parallel"
    assert by_id["dev_fanout"]["children"] == ["frontend_dev", "backend_dev"]
    gate_edges = [edge for edge in graph["edges"] if edge["from"] == "requirement_gate"]
    assert gate_edges == [
        {
            "from": "requirement_gate",
            "to": "design",
            "on_accept": "design",
            "on_reject": "requirement_review",
            "on_edit": "requirement_review",
        }
    ]


# ---------------------------------------------------------------------------
# 非法 YAML 逐一抛 WorkflowValidationError
# ---------------------------------------------------------------------------

INVALID_CASES = [
    (
        "duplicate-id",
        """
name: invalid
max_iterations: 10
nodes:
  - {id: start, type: start}
  - {id: dup, type: agent}
  - {id: dup, type: agent}
  - {id: end, type: end}
edges:
  - {from: start, to: dup}
  - {from: dup, to: end}
""",
        "重复的节点 id",
    ),
    (
        "missing-edge-target",
        """
name: invalid
max_iterations: 10
nodes:
  - {id: start, type: start}
  - {id: a, type: agent}
  - {id: end, type: end}
edges:
  - {from: start, to: ghost}
  - {from: a, to: end}
""",
        "边终点引用不存在的节点",
    ),
    (
        "missing-start",
        """
name: invalid
max_iterations: 10
nodes:
  - {id: a, type: agent}
  - {id: end, type: end}
edges:
  - {from: a, to: end}
""",
        "缺少 start 节点",
    ),
    (
        "two-starts",
        """
name: invalid
max_iterations: 10
nodes:
  - {id: start, type: start}
  - {id: start2, type: start}
  - {id: end, type: end}
edges:
  - {from: start, to: end}
""",
        "多个 start 节点",
    ),
    (
        "gate-without-outgoing-edge",
        """
name: invalid
max_iterations: 10
nodes:
  - {id: start, type: start}
  - {id: g, type: gate, gate: release}
  - {id: end, type: end}
edges:
  - {from: start, to: g}
""",
        "gate 节点 'g' 至少需要一条出边",
    ),
    (
        "edge-without-to",
        """
name: invalid
max_iterations: 10
nodes:
  - {id: start, type: start}
  - {id: a, type: agent}
  - {id: end, type: end}
edges:
  - {from: start}
  - {from: a, to: end}
""",
        "流程规格非法",
    ),
    (
        "edge-from-missing-node",
        """
name: invalid
max_iterations: 10
nodes:
  - {id: start, type: start}
  - {id: end, type: end}
edges:
  - {from: ghost, to: end}
  - {from: start, to: end}
""",
        "边起点引用不存在的节点",
    ),
    (
        "parallel-without-children",
        """
name: invalid
max_iterations: 10
nodes:
  - {id: start, type: start}
  - {id: p, type: parallel}
  - {id: end, type: end}
edges:
  - {from: start, to: p}
  - {from: p, to: end}
""",
        "必须声明 children",
    ),
    (
        "end-with-outgoing-edge",
        """
name: invalid
max_iterations: 10
nodes:
  - {id: start, type: start}
  - {id: end, type: end}
  - {id: a, type: agent}
edges:
  - {from: start, to: end}
  - {from: end, to: a}
""",
        "end 节点 'end' 不允许有出边",
    ),
]


@pytest.mark.parametrize(
    ("_case_name", "yaml_text", "message_part"),
    INVALID_CASES,
    ids=[case[0] for case in INVALID_CASES],
)
def test_invalid_yaml_raises_validation_error(_case_name, yaml_text, message_part):
    with pytest.raises(WorkflowValidationError, match=message_part):
        WorkflowEngine().compile(yaml_text)


def test_non_mapping_yaml_raises_validation_error():
    with pytest.raises(WorkflowValidationError, match="顶层必须是映射"):
        WorkflowEngine().compile("- just\n- a\n- list\n")


# ---------------------------------------------------------------------------
# 运行：事件序列
# ---------------------------------------------------------------------------


async def test_simple_flow_full_event_sequence():
    compiled = WorkflowEngine().compile(SIMPLE_YAML)
    events = [event async for event in compiled.run()]
    assert [(event.type, event.actor) for event in events] == [
        ("workflow_start", ""),
        ("node_start", "start"),
        ("node_end", "start"),
        ("node_start", "code"),
        ("node_end", "code"),
        ("node_start", "review"),
        ("node_end", "review"),
        ("node_start", "end"),
        ("node_end", "end"),
        ("workflow_end", ""),
    ]
    # events 属性与产出的事件一致
    assert compiled.events == events
    assert all(event.thread_id == "proj:demo:iter:1" for event in events)


async def test_sequential_chain_runs_all_nodes_in_order():
    compiled = WorkflowEngine().compile(SIMPLE_YAML)
    events = [event async for event in compiled.run()]
    actors = [event.actor for event in events if event.type == "node_start"]
    assert actors == ["start", "code", "review", "end"]


async def test_start_node_defaults_project_and_iteration():
    """无初始状态时，start 节点从 thread_id 推导 Project/Iteration 默认值。"""

    async def report_handler(state: ClusterState, node: WorkflowNode, ctx: NodeContext) -> dict:
        ctx.events.append(
            Event(
                id=f"{ctx.run_id}:report",
                run_id=ctx.run_id,
                thread_id=ctx.spec.thread_id,
                type="state_report",
                actor=node.id,
                payload={
                    "project_id": state.project.id if state.project else None,
                    "project_name": state.project.name if state.project else None,
                    "iteration_ids": [iteration.id for iteration in state.iterations],
                    "loop_count": ctx.loop_count,
                },
            )
        )
        return {}

    compiled = WorkflowEngine(handlers={"agent": report_handler}).compile(SIMPLE_YAML)
    events = [event async for event in compiled.run()]
    report = next(event for event in events if event.type == "state_report")
    assert report.payload == {
        "project_id": "demo",
        "project_name": "simple",
        "iteration_ids": ["demo:iter:1"],
        "loop_count": 1,
    }


async def test_initial_state_is_preserved():
    """初始状态已携带 project 时，start 节点保持原值不覆盖。"""

    async def report_handler(state: ClusterState, node: WorkflowNode, ctx: NodeContext) -> dict:
        ctx.events.append(
            Event(
                id=f"{ctx.run_id}:report",
                run_id=ctx.run_id,
                thread_id=ctx.spec.thread_id,
                type="state_report",
                actor=node.id,
                payload={"project_id": state.project.id if state.project else None},
            )
        )
        return {}

    compiled = WorkflowEngine(handlers={"agent": report_handler}).compile(SIMPLE_YAML)
    initial = {"project": {"id": "p9", "name": "既有项目"}}
    events = [event async for event in compiled.run(initial)]
    report = next(event for event in events if event.type == "state_report")
    assert report.payload == {"project_id": "p9"}


# ---------------------------------------------------------------------------
# gate 条件路由
# ---------------------------------------------------------------------------


async def test_gate_conditional_routing_takes_rework_then_accept():
    """第一次审批 reject 走返工边，第二次 accept 放行到 end。"""

    calls = {"count": 0}

    async def fake_gate_handler(state: ClusterState, node: WorkflowNode, ctx: NodeContext) -> dict:
        calls["count"] += 1
        decision = "reject" if calls["count"] == 1 else "accept"
        request = ActionRequest(
            id=f"ar-{ctx.run_id}-{calls['count']}",
            kind=node.gate,
            title="迭代验收审批",
            decisions=[ApprovalRecord(by_role="pm", type=decision, args={"round": calls["count"]})],
        )
        return {
            "gate_payloads": {node.gate: request},
            "decisions": [ApprovalRecord(by_role="pm", type=decision)],
        }

    compiled = WorkflowEngine(handlers={"gate": fake_gate_handler}).compile(GATE_YAML)
    events = [event async for event in compiled.run()]
    node_starts = [event for event in events if event.type == "node_start"]
    actors = [event.actor for event in node_starts]
    # 第一次 quality_gate reject → rework；第二次 accept → end
    assert actors == ["start", "dev", "quality_gate", "rework", "quality_gate", "end"]
    assert calls["count"] == 2


async def test_gate_accept_routes_straight_to_end():
    """门 handler 未注入时（默认占位），gate 按缺省 accept 路由到 to。"""

    compiled = WorkflowEngine().compile(GATE_YAML)
    events = [event async for event in compiled.run()]
    actors = [event.actor for event in events if event.type == "node_start"]
    assert actors == ["start", "dev", "quality_gate", "end"]


# ---------------------------------------------------------------------------
# parallel 并行 fan-out / fan-in
# ---------------------------------------------------------------------------


async def test_parallel_fan_out_all_children_ran():
    async def agent_handler(state: ClusterState, node: WorkflowNode, ctx: NodeContext) -> dict:
        ctx.events.append(
            Event(
                id=f"{ctx.run_id}:{node.id}",
                run_id=ctx.run_id,
                thread_id=ctx.spec.thread_id,
                type="agent_ran",
                actor=node.id,
                payload={"role": node.role},
            )
        )
        return {}

    compiled = WorkflowEngine(handlers={"agent": agent_handler}).compile(PARALLEL_YAML)
    events = [event async for event in compiled.run()]
    node_starts = [event for event in events if event.type == "node_start"]
    assert [event.actor for event in node_starts] == ["start", "fanout", "fe", "be", "end"]
    agent_ran = {event.actor: event.payload["role"] for event in events if event.type == "agent_ran"}
    assert agent_ran == {"fe": "frontend", "be": "backend"}


# ---------------------------------------------------------------------------
# 防死循环
# ---------------------------------------------------------------------------


async def test_loop_limit_raises_workflow_loop_error():
    async def always_reject(state: ClusterState, node: WorkflowNode, ctx: NodeContext) -> dict:
        request = ActionRequest(
            id=f"ar-{ctx.run_id}",
            kind=node.gate,
            title="迭代验收审批",
            decisions=[ApprovalRecord(by_role="pm", type="reject")],
        )
        return {"gate_payloads": {node.gate: request}}

    compiled = WorkflowEngine(handlers={"gate": always_reject}).compile(LOOP_YAML)
    with pytest.raises(WorkflowLoopError, match="max_iterations=4"):
        _ = [event async for event in compiled.run()]


# ---------------------------------------------------------------------------
# ClusterState reducer 契约（Task 1 模型 retrofit）
# ---------------------------------------------------------------------------


def test_cluster_state_list_channels_use_add_reducers():
    hints = typing.get_type_hints(ClusterState, include_extras=True)
    for field_name in ("iterations", "tasks", "meetings", "decisions", "messages"):
        assert hints[field_name].__metadata__ == (operator.add,), field_name


def test_action_request_carries_decisions():
    request = ActionRequest(
        id="ar1",
        kind=GateKind.RELEASE,
        title="发布审批",
        decisions=[ApprovalRecord(by_role="pm", type="reject")],
    )
    assert request.decisions[-1].type == "reject"
