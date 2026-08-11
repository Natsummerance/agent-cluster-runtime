"""流程引擎（设计文档 §5.1/§5.8）：YAML 流程 DSL → LangGraph StateGraph 编译与事件流运行。

职责：
- 把 ChatDev 风格的 YAML 流程 DSL 解析为 ``WorkflowSpec``（pydantic 模型），
  校验节点/边/字段级错误后编译为 ``StateGraph(ClusterState)``。
- 节点类型：``start``/``end``/``agent``/``meeting``/``gate``/``parallel``。
- 事件流：每次运行产出 ``workflow_start``/``node_start``/``node_end``/``workflow_end``
  事件；handler 可通过 ``ctx.events`` 追加自定义事件。
- 防死循环：统计每次运行累计执行的节点数，超过 ``max_iterations`` 抛
  ``WorkflowLoopError``；LangGraph ``recursion_limit = max_iterations * 4`` 兜底。

handler 契约（Task 4/5 据此注册）：
- ``WorkflowEngine(handlers={"agent": ..., "meeting": ..., "gate": ...})`` 按
  **节点类型** 注册异步 handler；``start``/``end``/``parallel`` 为内置节点，
  不查询 handlers；未注册类型的节点使用默认占位 handler（不改状态、不发额外事件），
  保证编译与运行不中断。
- handler 签名：``async def handler(state: ClusterState, node: WorkflowNode,
  ctx: NodeContext) -> dict[str, Any]``，返回 **LangGraph channel 更新字典**
  （如 ``{"tasks": [Task(...)]}``、``{"gate_payloads": {GateKind: ActionRequest(...)}}``）。
  list 字段（iterations/tasks/meetings/decisions/messages）带 ``operator.add`` reducer，
  handler 只追加、不整体替换。这是对任务简报中 ``Awaitable[ClusterState]`` 的偏离：
  dict 更新与 reducer 语义天然一致，且与简报自述的 ``handler writes {...}`` 一致。
- gate 门路由载荷（Task 4 gates.py 的契约）：
  gate 节点执行后，``"gate"`` handler 必须返回
  ``{"gate_payloads": {node.gate: ActionRequest(...)}}``，其中
  ``ActionRequest.decisions[-1]``（``ApprovalRecord.type``）为本次审批结论：
  ``accept``→``on_accept``（缺省 ``to``）；``reject``→``on_reject``（缺省 ``to``）；
  ``edit``→``on_edit``（缺省 ``to``）；``response``→``on_response``（缺省
  ``on_accept``→``to``）；``ignore`` 或未写入载荷→``on_accept``（缺省 ``to``）。
- parallel 并行：编译期用 LangGraph ``Send`` API fan-out 到子节点、子节点各自
  ``add_edge(child, fan_in_target)`` 汇聚；所有子节点仍注册为图节点并产出事件。
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any, Literal

import yaml
from langgraph.errors import GraphRecursionError
from langgraph.graph import END, START, StateGraph
from langgraph.types import Send
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from agent_cluster.models import (
    ClusterState,
    Event,
    GateKind,
    Iteration,
    MeetingKind,
    Project,
)

__all__ = [
    "WorkflowValidationError",
    "WorkflowLoopError",
    "WorkflowNode",
    "WorkflowEdge",
    "WorkflowSpec",
    "NodeContext",
    "NodeHandler",
    "CompiledWorkflow",
    "WorkflowEngine",
]


class WorkflowValidationError(Exception):
    """流程 YAML 编译校验错误（消息包含节点/边/字段级细节）。"""


class WorkflowLoopError(Exception):
    """流程执行超过 max_iterations（防死循环）。"""


class WorkflowNode(BaseModel):
    """流程节点（对齐 YAML DSL 字段）。"""

    model_config = ConfigDict(extra="ignore")

    id: str = Field(description="节点唯一标识")
    type: Literal["start", "end", "agent", "meeting", "gate", "parallel"] = Field(description="节点类型")
    meeting: MeetingKind | None = Field(default=None, description="meeting 节点会议类型")
    role: str | None = Field(default=None, description="agent 节点岗位 id")
    gate: GateKind | None = Field(default=None, description="gate 节点审批门类别")
    children: list[str] | None = Field(default=None, description="parallel 节点子节点 id 列表")


class WorkflowEdge(BaseModel):
    """流程边（``from`` 为 Python 关键字，用别名映射）。"""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    from_: str = Field(alias="from", description="起点节点 id")
    to: str = Field(description="终点节点 id（gate/parallel 的缺省目标）")
    on_accept: str | None = Field(default=None, description="gate 审批 accept 目标")
    on_reject: str | None = Field(default=None, description="gate 审批 reject 目标")
    on_edit: str | None = Field(default=None, description="gate 审批 edit 目标")
    on_response: str | None = Field(default=None, description="gate 审批 response 目标")


class WorkflowSpec(BaseModel):
    """流程规格（YAML 顶层）。"""

    model_config = ConfigDict(extra="ignore")

    name: str = Field(description="流程名称")
    description: str = Field(default="", description="流程描述")
    max_iterations: int = Field(default=10, gt=0, description="防死循环：单次运行最大节点执行次数")
    thread_id: str = Field(default="", description="线程 id（缺省运行时使用）")
    nodes: list[WorkflowNode] = Field(description="节点列表")
    edges: list[WorkflowEdge] = Field(description="边列表")


class NodeContext(BaseModel):
    """传给节点 handler 的运行上下文。"""

    model_config = ConfigDict(extra="ignore")

    node_id: str = Field(description="当前节点 id")
    spec: WorkflowSpec = Field(description="流程规格")
    events: list[Event] = Field(description="事件流缓冲，handler 可 append 追加事件")
    run_id: str = Field(description="本次运行 id")
    loop_count: int = Field(description="当前主循环轮次（start 节点已执行次数）")


NodeHandler = Callable[[ClusterState, WorkflowNode, NodeContext], Awaitable[dict[str, Any]]]


def _validate_spec(spec: WorkflowSpec) -> None:
    """编译前校验：重复 id、悬空引用、start/end 唯一性与出边、gate 出边、parallel children。"""
    nodes_by_id: dict[str, WorkflowNode] = {}
    for node in spec.nodes:
        if node.id in nodes_by_id:
            raise WorkflowValidationError(f"重复的节点 id：{node.id!r}")
        nodes_by_id[node.id] = node

    start_nodes = [node for node in spec.nodes if node.type == "start"]
    end_nodes = [node for node in spec.nodes if node.type == "end"]
    if not start_nodes:
        raise WorkflowValidationError("流程缺少 start 节点")
    if len(start_nodes) > 1:
        raise WorkflowValidationError(f"流程存在多个 start 节点：{[node.id for node in start_nodes]}")
    if not end_nodes:
        raise WorkflowValidationError("流程缺少 end 节点")
    if len(end_nodes) > 1:
        raise WorkflowValidationError(f"流程存在多个 end 节点：{[node.id for node in end_nodes]}")
    start_node = start_nodes[0]
    end_node = end_nodes[0]

    for edge in spec.edges:
        if edge.from_ not in nodes_by_id:
            raise WorkflowValidationError(f"边起点引用不存在的节点：{edge.from_!r}")
        if edge.to not in nodes_by_id:
            raise WorkflowValidationError(f"边终点引用不存在的节点：{edge.to!r}")
        for field_name in ("on_accept", "on_reject", "on_edit", "on_response"):
            target = getattr(edge, field_name)
            if target is not None and target not in nodes_by_id:
                raise WorkflowValidationError(
                    f"边 {edge.from_!r}→{edge.to!r} 的 {field_name} 引用不存在的节点：{target!r}"
                )

    if not any(edge.from_ == start_node.id for edge in spec.edges):
        raise WorkflowValidationError(f"start 节点 {start_node.id!r} 至少需要一条出边")
    if any(edge.from_ == end_node.id for edge in spec.edges):
        raise WorkflowValidationError(f"end 节点 {end_node.id!r} 不允许有出边")

    for node in spec.nodes:
        if node.type == "gate" and not any(edge.from_ == node.id for edge in spec.edges):
            raise WorkflowValidationError(f"gate 节点 {node.id!r} 至少需要一条出边")
        if node.type == "parallel":
            if not node.children:
                raise WorkflowValidationError(f"parallel 节点 {node.id!r} 必须声明 children 子节点列表")
            for child_id in node.children:
                if child_id not in nodes_by_id:
                    raise WorkflowValidationError(f"parallel 节点 {node.id!r} 的子节点 {child_id!r} 不存在")
            if not any(edge.from_ == node.id for edge in spec.edges):
                raise WorkflowValidationError(f"parallel 节点 {node.id!r} 至少需要一条出边（fan-in 目标）")


class CompiledWorkflow:
    """已编译的 LangGraph 流程：运行产出并累计事件流。"""

    def __init__(self, spec: WorkflowSpec, handlers: dict[str, NodeHandler]) -> None:
        self._spec = spec
        self._handlers = dict(handlers)
        self._events: list[Event] = []
        self._run_id = ""
        self._thread_id = ""
        self._loop_count = 0
        self._event_seq = 0
        self._drained = 0
        self._start_id = next(node.id for node in spec.nodes if node.type == "start")
        self._end_id = next(node.id for node in spec.nodes if node.type == "end")
        self._graph = self._build_graph()

    @property
    def events(self) -> list[Event]:
        """返回累计事件流（跨多次 run 累积，按 run_id 区分）。"""
        return list(self._events)

    def get_graph(self) -> dict:
        """返回图描述（节点/边列表），供测试与断言使用。"""
        nodes = [node.model_dump(exclude_none=True, mode="json") for node in self._spec.nodes]
        edges = [edge.model_dump(exclude_none=True, by_alias=True, mode="json") for edge in self._spec.edges]
        return {"nodes": nodes, "edges": edges}

    # ------------------------------------------------------------------
    # 图构建
    # ------------------------------------------------------------------

    def _build_graph(self) -> Any:
        graph = StateGraph(ClusterState)
        nodes_by_id = {node.id: node for node in self._spec.nodes}
        for node in self._spec.nodes:
            if node.type == "end":
                graph.add_node(node.id, self._make_end_wrapper())
            else:
                graph.add_node(node.id, self._make_node_wrapper(node))
        graph.add_edge(START, self._start_id)

        start_edge = next(edge for edge in self._spec.edges if edge.from_ == self._start_id)
        graph.add_edge(self._start_id, start_edge.to)
        graph.add_edge(self._end_id, END)

        wired_gates: set[str] = set()
        wired_parallels: set[str] = set()
        for edge in self._spec.edges:
            if edge.from_ in (self._start_id, self._end_id):
                continue
            source = nodes_by_id[edge.from_]
            if source.type == "gate":
                if edge.from_ not in wired_gates:
                    self._wire_gate_edges(graph, source)
                    wired_gates.add(edge.from_)
            elif source.type == "parallel":
                if edge.from_ not in wired_parallels:
                    self._wire_parallel_edges(graph, source)
                    wired_parallels.add(edge.from_)
            else:
                graph.add_edge(edge.from_, edge.to)
        return graph.compile()

    def _wire_gate_edges(self, graph, node: WorkflowNode) -> None:
        """把 gate 节点的出边编译为条件路由（基于最后一次审批结论）。"""
        gate_edges = [edge for edge in self._spec.edges if edge.from_ == node.id]
        fallback_to = gate_edges[0].to
        targets: dict[str, str] = {
            "accept": next((edge.on_accept for edge in gate_edges if edge.on_accept), fallback_to),
            "reject": next((edge.on_reject for edge in gate_edges if edge.on_reject), fallback_to),
            "edit": next((edge.on_edit for edge in gate_edges if edge.on_edit), fallback_to),
            "response": next((edge.on_response for edge in gate_edges if edge.on_response), None)
            or next((edge.on_accept for edge in gate_edges if edge.on_accept), fallback_to),
            "ignore": next((edge.on_accept for edge in gate_edges if edge.on_accept), fallback_to),
        }
        path_map = {target: target for target in targets.values()}
        graph.add_conditional_edges(node.id, self._make_gate_router(node, targets), path_map)

    def _wire_parallel_edges(self, graph, node: WorkflowNode) -> None:
        """把 parallel 节点编译为 Send fan-out + 子节点汇聚到 fan-in 目标。"""
        children = list(node.children or [])
        fan_in_target = next(edge.to for edge in self._spec.edges if edge.from_ == node.id)

        def fan_out(_state: ClusterState) -> list[Send]:
            return [Send(child_id, {}) for child_id in children]

        graph.add_conditional_edges(node.id, fan_out, list(children))
        for child_id in children:
            graph.add_edge(child_id, fan_in_target)

    def _make_gate_router(self, node: WorkflowNode, targets: dict[str, str]) -> Callable[[ClusterState], str]:
        def route(state: ClusterState) -> str:
            return targets.get(self._last_gate_decision_type(state, node), targets["accept"])

        return route

    @staticmethod
    def _last_gate_decision_type(state: ClusterState, node: WorkflowNode) -> str:
        """读取 gate 载荷的最后一条审批结论；缺失时按 accept 处理。"""
        if node.gate is None:
            return "accept"
        payload = state.gate_payloads.get(node.gate)
        if payload is None or not payload.decisions:
            return "accept"
        return payload.decisions[-1].type

    # ------------------------------------------------------------------
    # 节点包装器
    # ------------------------------------------------------------------

    def _make_node_wrapper(self, node: WorkflowNode) -> Callable[[ClusterState], Awaitable[dict[str, Any] | None]]:
        async def wrapper(state: ClusterState) -> dict[str, Any] | None:
            return await self._execute_node(state, node)

        return wrapper

    def _make_end_wrapper(self) -> Callable[[ClusterState], Awaitable[None]]:
        async def wrapper(state: ClusterState) -> None:
            self._emit("node_start", actor=self._end_id, payload={"node_type": "end", "node_id": self._end_id})
            self._emit("node_end", actor=self._end_id, payload={"node_type": "end", "node_id": self._end_id})
            return None

        return wrapper

    async def _execute_node(self, state: ClusterState, node: WorkflowNode) -> dict[str, Any] | None:
        if node.type == "start":
            self._loop_count += 1
        # model_construct 跳过校验，保证 ctx.events 与内部事件缓冲为同一列表引用
        ctx = NodeContext.model_construct(
            node_id=node.id,
            spec=self._spec,
            events=self._events,
            run_id=self._run_id,
            loop_count=self._loop_count,
        )
        start_payload: dict[str, Any] = {"node_type": node.type, "node_id": node.id}
        if node.type == "start":
            start_payload["loop_count"] = self._loop_count
        self._emit("node_start", actor=node.id, payload=start_payload)

        if node.type == "start":
            updates: dict[str, Any] | None = self._execute_start(state)
        elif node.type == "parallel":
            updates = {}
        else:
            handler = self._handlers.get(node.type)
            if handler is None:
                updates = await self._default_handler(state, node, ctx)
            else:
                updates = await handler(state, node, ctx)

        self._emit("node_end", actor=node.id, payload={"node_type": node.type, "node_id": node.id})
        if updates is None:
            return None
        if not isinstance(updates, dict):
            raise TypeError(
                f"节点 {node.id!r} 的 handler 必须返回 dict 形式的 channel 更新，实际返回 {type(updates).__name__}"
            )
        return updates

    def _execute_start(self, state: ClusterState) -> dict[str, Any]:
        """start 节点：补齐 Project/Iteration 默认值（初始状态已携带时保持原样）。"""
        updates: dict[str, Any] = {}
        project = state.project
        if project is None:
            project = Project(id=self._default_project_id(), name=self._spec.name or self._default_project_id())
            updates["project"] = project
        if not state.iterations:
            updates["iterations"] = [Iteration(id=f"{project.id}:iter:1", project_id=project.id, number=1)]
        return updates

    def _default_project_id(self) -> str:
        """从 thread_id（proj:<id>:iter:<n>）推导项目 id；否则回退流程名。"""
        thread_id = self._spec.thread_id or ""
        if thread_id.startswith("proj:"):
            parts = thread_id.split(":")
            if len(parts) >= 2 and parts[1]:
                return parts[1]
        return self._spec.name or "default-project"

    async def _default_handler(self, state: ClusterState, node: WorkflowNode, ctx: NodeContext) -> dict[str, Any]:
        """未注册 handler 的占位实现：不改状态、不发额外事件，保证运行不中断。"""
        return {}

    # ------------------------------------------------------------------
    # 事件与运行
    # ------------------------------------------------------------------

    def _emit(self, event_type: str, *, actor: str, payload: dict[str, Any]) -> Event:
        self._event_seq += 1
        event = Event(
            id=f"{self._run_id}:{self._event_seq:04d}",
            run_id=self._run_id,
            thread_id=self._thread_id,
            type=event_type,
            actor=actor,
            payload=payload,
        )
        self._events.append(event)
        return event

    async def run(self, initial: dict | None = None, *, thread_id: str | None = None) -> AsyncIterator[Event]:
        """运行流程：产出事件流并累计到 ``events``。

        - ``initial``：初始 ClusterState 的字段字典（可含 project/iterations 等）。
        - ``thread_id``：覆盖 spec.thread_id；缺省用 spec.thread_id 或 "default"。
        - 防死循环：累计执行节点数超过 max_iterations 抛 WorkflowLoopError；
          LangGraph recursion_limit（max_iterations*4）触发时同样转 WorkflowLoopError。
        """
        resolved_thread_id = thread_id or self._spec.thread_id or "default"
        self._run_id = uuid.uuid4().hex[:12]
        self._thread_id = resolved_thread_id
        self._loop_count = 0
        self._event_seq = 0
        self._drained = 0
        initial_state = ClusterState() if initial is None else ClusterState.model_validate(initial)

        yield self._emit(
            "workflow_start",
            actor="",
            payload={"name": self._spec.name, "thread_id": resolved_thread_id},
        )
        self._drained = len(self._events)

        executed = 0
        try:
            async for step in self._graph.astream(
                initial_state,
                config={
                    "recursion_limit": self._spec.max_iterations * 4,
                    "configurable": {"thread_id": resolved_thread_id},
                },
            ):
                for node_name in step:
                    executed += 1
                    if executed > self._spec.max_iterations:
                        raise WorkflowLoopError(
                            f"流程 {self._spec.name!r} 超过最大迭代次数 max_iterations="
                            f"{self._spec.max_iterations}（已执行节点数 {executed}）"
                        )
                pending = list(self._events[self._drained :])
                self._drained = len(self._events)
                for event in pending:
                    yield event
        except GraphRecursionError as exc:
            raise WorkflowLoopError(
                f"流程 {self._spec.name!r} 超过 LangGraph recursion_limit"
                f"（max_iterations*4={self._spec.max_iterations * 4}），疑似死循环"
            ) from exc

        yield self._emit(
            "workflow_end",
            actor="",
            payload={"name": self._spec.name, "thread_id": resolved_thread_id},
        )


class WorkflowEngine:
    """流程引擎：YAML 流程 DSL → 校验 → CompiledWorkflow。

    ``handlers`` 按节点类型注册（"agent"/"meeting"/"gate"）；"start"/"end"/"parallel"
    为内置节点，不查询 handlers；未注册类型的节点走默认占位 handler。
    """

    def __init__(self, handlers: dict[str, NodeHandler] | None = None) -> None:
        self._handlers: dict[str, NodeHandler] = dict(handlers or {})

    def compile(self, yaml_text: str) -> CompiledWorkflow:
        """解析 YAML → 校验 → 构建 LangGraph StateGraph，返回 CompiledWorkflow。"""
        try:
            data = yaml.safe_load(yaml_text)
        except yaml.YAMLError as exc:
            raise WorkflowValidationError(f"YAML 解析失败：{exc}") from exc
        if not isinstance(data, dict):
            raise WorkflowValidationError("流程 YAML 顶层必须是映射（含 name/nodes/edges 等字段）")
        try:
            spec = WorkflowSpec.model_validate(data)
        except ValidationError as exc:
            raise WorkflowValidationError(f"流程规格非法：{exc}") from exc
        _validate_spec(spec)
        return CompiledWorkflow(spec=spec, handlers=self._handlers)
