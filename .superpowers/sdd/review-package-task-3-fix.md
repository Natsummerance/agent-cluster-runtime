# Task 3 Fix Review Package

Fix base: 4179512
Head: 18863ec

## Diff stat

```
 .superpowers/sdd/ledger.md                    |    1 +
 .superpowers/sdd/review-package-task-3.md     | 1083 +++++++++++++++++++++++++
 .superpowers/sdd/task-3-report.md             |   89 ++
 docs/superpowers/plans/implementation-plan.md |    3 +-
 src/agent_cluster/workflow.py                 |  282 +++++--
 tests/test_workflow.py                        |   88 +-
 6 files changed, 1479 insertions(+), 67 deletions(-)
```

## Full diff

```diff
diff --git a/.superpowers/sdd/ledger.md b/.superpowers/sdd/ledger.md
index 520adab..62da17b 100644
--- a/.superpowers/sdd/ledger.md
+++ b/.superpowers/sdd/ledger.md
@@ -7,4 +7,5 @@ Plan: docs/superpowers/plans/implementation-plan.md
 | Task 1 工程骨架与数据模型 | complete | 757cc4f..fc6f7f6 | Approved (33 passed) | Minor 交接：Task 3 需给 ClusterState 配 reducers；Task 5 处理 TaskStatus/Board 列名映射 |
 | Task 2 技能层 SKILL.md 加载与渐进披露 | complete | 9b8e68c | Approved (52 passed) | Skill 模型新增 compatibility 字段（默认 None）；examples/skills 已有 2 个技能包，Task 7 补齐至 4 个 |
 | Task 2 技能层 | complete | 9b8e68c..245c458 | Approved (52 passed) | Minor: 兼容性 <= 语义、anchor 转义、allowed_tools union（Task 7 注意）、@ 退化源；记入最终评审 |
+| Task 3 流程引擎 YAML→StateGraph | complete | 4179512 | 73 passed（52 既有 + 21 新增） | gate 载荷契约：gate_payloads[node.gate].decisions[-1]；max_iterations=总节点执行数上限（线性流程需 ≥ 节点数）；NodeHandler 返回 dict channel updates |
 
diff --git a/.superpowers/sdd/review-package-task-3.md b/.superpowers/sdd/review-package-task-3.md
new file mode 100644
index 0000000..4671943
--- /dev/null
+++ b/.superpowers/sdd/review-package-task-3.md
@@ -0,0 +1,1083 @@
+# Task 3 Review Package
+
+Base: 72456c1
+Head: 4179512
+
+## Diff stat
+
+```
+ src/agent_cluster/__init__.py |  20 ++
+ src/agent_cluster/models.py   |  17 +-
+ src/agent_cluster/workflow.py | 461 ++++++++++++++++++++++++++++++++++++++
+ tests/test_workflow.py        | 509 ++++++++++++++++++++++++++++++++++++++++++
+ 4 files changed, 1001 insertions(+), 6 deletions(-)
+```
+
+## Full diff
+
+```diff
+diff --git a/src/agent_cluster/__init__.py b/src/agent_cluster/__init__.py
+index 6220ad9..1293317 100644
+--- a/src/agent_cluster/__init__.py
++++ b/src/agent_cluster/__init__.py
+@@ -39,6 +39,17 @@ from agent_cluster.models import (
+     TaskStatus,
+     Vote,
+ )
++from agent_cluster.workflow import (
++    CompiledWorkflow,
++    NodeContext,
++    NodeHandler,
++    WorkflowEdge,
++    WorkflowEngine,
++    WorkflowLoopError,
++    WorkflowNode,
++    WorkflowSpec,
++    WorkflowValidationError,
++)
+ from agent_cluster.skills import (
+     DisclosureLevel,
+     SkillCatalog,
+@@ -91,6 +102,15 @@ __all__ = [
+     "Task",
+     "TaskStatus",
+     "Vote",
++    "CompiledWorkflow",
++    "NodeContext",
++    "NodeHandler",
++    "WorkflowEdge",
++    "WorkflowEngine",
++    "WorkflowLoopError",
++    "WorkflowNode",
++    "WorkflowSpec",
++    "WorkflowValidationError",
+     "__version__",
+     "format_skill_context",
+ ]
+diff --git a/src/agent_cluster/models.py b/src/agent_cluster/models.py
+index 7901b41..8c23f6f 100644
+--- a/src/agent_cluster/models.py
++++ b/src/agent_cluster/models.py
+@@ -7,9 +7,11 @@
+ 
+ from __future__ import annotations
+ 
++import operator
++
+ from datetime import date, datetime
+ from enum import StrEnum
+-from typing import Any, Literal
++from typing import Annotated, Any, Literal
+ 
+ from pydantic import BaseModel, ConfigDict, Field
+ 
+@@ -425,6 +427,9 @@ class ActionRequest(BaseModel):
+     evidence: dict = Field(default_factory=dict, description="证据 / 上下文")
+     risk_level: Literal["low", "medium", "high", "critical"] = Field(default="medium", description="风险级别")
+     bypass_immune: bool = Field(default=False, description="无人值守时是否禁止自动放行")
++    decisions: list[ApprovalRecord] = Field(
++        default_factory=list, description="审批记录，最后一条为当前结论（Task 3 门路由契约）"
++    )
+ 
+ 
+ class ApprovalRecord(BaseModel):
+@@ -527,11 +532,11 @@ class ClusterState(BaseModel):
+     model_config = ConfigDict(extra="ignore")
+ 
+     project: Project | None = Field(default=None, description="当前项目")
+-    iterations: list[Iteration] = Field(default_factory=list, description="迭代列表")
+-    tasks: list[Task] = Field(default_factory=list, description="任务列表")
+-    meetings: list[Meeting] = Field(default_factory=list, description="会议记录列表")
++    iterations: Annotated[list[Iteration], operator.add] = Field(default_factory=list, description="迭代列表")
++    tasks: Annotated[list[Task], operator.add] = Field(default_factory=list, description="任务列表")
++    meetings: Annotated[list[Meeting], operator.add] = Field(default_factory=list, description="会议记录列表")
+     ledger: Ledger | None = Field(default=None, description="当前任务账本")
+     gate_payloads: dict[GateKind, ActionRequest] = Field(default_factory=dict, description="待审批请求，按门类别索引")
+-    decisions: list[ApprovalRecord] = Field(default_factory=list, description="审批记录")
++    decisions: Annotated[list[ApprovalRecord], operator.add] = Field(default_factory=list, description="审批记录")
+     skill_catalog: dict[str, Skill] = Field(default_factory=dict, description="技能目录：name@version -> Skill")
+-    messages: list[Message] = Field(default_factory=list, description="消息流")
++    messages: Annotated[list[Message], operator.add] = Field(default_factory=list, description="消息流")
+diff --git a/src/agent_cluster/workflow.py b/src/agent_cluster/workflow.py
+new file mode 100644
+index 0000000..e7c0039
+--- /dev/null
++++ b/src/agent_cluster/workflow.py
+@@ -0,0 +1,461 @@
++"""流程引擎（设计文档 §5.1/§5.8）：YAML 流程 DSL → LangGraph StateGraph 编译与事件流运行。
++
++职责：
++- 把 ChatDev 风格的 YAML 流程 DSL 解析为 ``WorkflowSpec``（pydantic 模型），
++  校验节点/边/字段级错误后编译为 ``StateGraph(ClusterState)``。
++- 节点类型：``start``/``end``/``agent``/``meeting``/``gate``/``parallel``。
++- 事件流：每次运行产出 ``workflow_start``/``node_start``/``node_end``/``workflow_end``
++  事件；handler 可通过 ``ctx.events`` 追加自定义事件。
++- 防死循环：统计每次运行累计执行的节点数，超过 ``max_iterations`` 抛
++  ``WorkflowLoopError``；LangGraph ``recursion_limit = max_iterations * 4`` 兜底。
++
++handler 契约（Task 4/5 据此注册）：
++- ``WorkflowEngine(handlers={"agent": ..., "meeting": ..., "gate": ...})`` 按
++  **节点类型** 注册异步 handler；``start``/``end``/``parallel`` 为内置节点，
++  不查询 handlers；未注册类型的节点使用默认占位 handler（不改状态、不发额外事件），
++  保证编译与运行不中断。
++- handler 签名：``async def handler(state: ClusterState, node: WorkflowNode,
++  ctx: NodeContext) -> dict[str, Any]``，返回 **LangGraph channel 更新字典**
++  （如 ``{"tasks": [Task(...)]}``、``{"gate_payloads": {GateKind: ActionRequest(...)}}``）。
++  list 字段（iterations/tasks/meetings/decisions/messages）带 ``operator.add`` reducer，
++  handler 只追加、不整体替换。这是对任务简报中 ``Awaitable[ClusterState]`` 的偏离：
++  dict 更新与 reducer 语义天然一致，且与简报自述的 ``handler writes {...}`` 一致。
++- gate 门路由载荷（Task 4 gates.py 的契约）：
++  gate 节点执行后，``"gate"`` handler 必须返回
++  ``{"gate_payloads": {node.gate: ActionRequest(...)}}``，其中
++  ``ActionRequest.decisions[-1]``（``ApprovalRecord.type``）为本次审批结论：
++  ``accept``→``on_accept``（缺省 ``to``）；``reject``→``on_reject``（缺省 ``to``）；
++  ``edit``→``on_edit``（缺省 ``to``）；``response``→``on_response``（缺省
++  ``on_accept``→``to``）；``ignore`` 或未写入载荷→``on_accept``（缺省 ``to``）。
++- parallel 并行：编译期用 LangGraph ``Send`` API fan-out 到子节点、子节点各自
++  ``add_edge(child, fan_in_target)`` 汇聚；所有子节点仍注册为图节点并产出事件。
++"""
++
++from __future__ import annotations
++
++import uuid
++from collections.abc import AsyncIterator, Awaitable, Callable
++from typing import Any, Literal
++
++import yaml
++from langgraph.errors import GraphRecursionError
++from langgraph.graph import END, START, StateGraph
++from langgraph.types import Send
++from pydantic import BaseModel, ConfigDict, Field, ValidationError
++
++from agent_cluster.models import (
++    ClusterState,
++    Event,
++    GateKind,
++    Iteration,
++    MeetingKind,
++    Project,
++)
++
++__all__ = [
++    "WorkflowValidationError",
++    "WorkflowLoopError",
++    "WorkflowNode",
++    "WorkflowEdge",
++    "WorkflowSpec",
++    "NodeContext",
++    "NodeHandler",
++    "CompiledWorkflow",
++    "WorkflowEngine",
++]
++
++
++class WorkflowValidationError(Exception):
++    """流程 YAML 编译校验错误（消息包含节点/边/字段级细节）。"""
++
++
++class WorkflowLoopError(Exception):
++    """流程执行超过 max_iterations（防死循环）。"""
++
++
++class WorkflowNode(BaseModel):
++    """流程节点（对齐 YAML DSL 字段）。"""
++
++    model_config = ConfigDict(extra="ignore")
++
++    id: str = Field(description="节点唯一标识")
++    type: Literal["start", "end", "agent", "meeting", "gate", "parallel"] = Field(description="节点类型")
++    meeting: MeetingKind | None = Field(default=None, description="meeting 节点会议类型")
++    role: str | None = Field(default=None, description="agent 节点岗位 id")
++    gate: GateKind | None = Field(default=None, description="gate 节点审批门类别")
++    children: list[str] | None = Field(default=None, description="parallel 节点子节点 id 列表")
++
++
++class WorkflowEdge(BaseModel):
++    """流程边（``from`` 为 Python 关键字，用别名映射）。"""
++
++    model_config = ConfigDict(populate_by_name=True, extra="ignore")
++
++    from_: str = Field(alias="from", description="起点节点 id")
++    to: str = Field(description="终点节点 id（gate/parallel 的缺省目标）")
++    on_accept: str | None = Field(default=None, description="gate 审批 accept 目标")
++    on_reject: str | None = Field(default=None, description="gate 审批 reject 目标")
++    on_edit: str | None = Field(default=None, description="gate 审批 edit 目标")
++    on_response: str | None = Field(default=None, description="gate 审批 response 目标")
++
++
++class WorkflowSpec(BaseModel):
++    """流程规格（YAML 顶层）。"""
++
++    model_config = ConfigDict(extra="ignore")
++
++    name: str = Field(description="流程名称")
++    description: str = Field(default="", description="流程描述")
++    max_iterations: int = Field(default=10, gt=0, description="防死循环：单次运行最大节点执行次数")
++    thread_id: str = Field(default="", description="线程 id（缺省运行时使用）")
++    nodes: list[WorkflowNode] = Field(description="节点列表")
++    edges: list[WorkflowEdge] = Field(description="边列表")
++
++
++class NodeContext(BaseModel):
++    """传给节点 handler 的运行上下文。"""
++
++    model_config = ConfigDict(extra="ignore")
++
++    node_id: str = Field(description="当前节点 id")
++    spec: WorkflowSpec = Field(description="流程规格")
++    events: list[Event] = Field(description="事件流缓冲，handler 可 append 追加事件")
++    run_id: str = Field(description="本次运行 id")
++    loop_count: int = Field(description="当前主循环轮次（start 节点已执行次数）")
++
++
++NodeHandler = Callable[[ClusterState, WorkflowNode, NodeContext], Awaitable[dict[str, Any]]]
++
++
++def _validate_spec(spec: WorkflowSpec) -> None:
++    """编译前校验：重复 id、悬空引用、start/end 唯一性与出边、gate 出边、parallel children。"""
++    nodes_by_id: dict[str, WorkflowNode] = {}
++    for node in spec.nodes:
++        if node.id in nodes_by_id:
++            raise WorkflowValidationError(f"重复的节点 id：{node.id!r}")
++        nodes_by_id[node.id] = node
++
++    start_nodes = [node for node in spec.nodes if node.type == "start"]
++    end_nodes = [node for node in spec.nodes if node.type == "end"]
++    if not start_nodes:
++        raise WorkflowValidationError("流程缺少 start 节点")
++    if len(start_nodes) > 1:
++        raise WorkflowValidationError(f"流程存在多个 start 节点：{[node.id for node in start_nodes]}")
++    if not end_nodes:
++        raise WorkflowValidationError("流程缺少 end 节点")
++    if len(end_nodes) > 1:
++        raise WorkflowValidationError(f"流程存在多个 end 节点：{[node.id for node in end_nodes]}")
++    start_node = start_nodes[0]
++    end_node = end_nodes[0]
++
++    for edge in spec.edges:
++        if edge.from_ not in nodes_by_id:
++            raise WorkflowValidationError(f"边起点引用不存在的节点：{edge.from_!r}")
++        if edge.to not in nodes_by_id:
++            raise WorkflowValidationError(f"边终点引用不存在的节点：{edge.to!r}")
++        for field_name in ("on_accept", "on_reject", "on_edit", "on_response"):
++            target = getattr(edge, field_name)
++            if target is not None and target not in nodes_by_id:
++                raise WorkflowValidationError(
++                    f"边 {edge.from_!r}→{edge.to!r} 的 {field_name} 引用不存在的节点：{target!r}"
++                )
++
++    if not any(edge.from_ == start_node.id for edge in spec.edges):
++        raise WorkflowValidationError(f"start 节点 {start_node.id!r} 至少需要一条出边")
++    if any(edge.from_ == end_node.id for edge in spec.edges):
++        raise WorkflowValidationError(f"end 节点 {end_node.id!r} 不允许有出边")
++
++    for node in spec.nodes:
++        if node.type == "gate" and not any(edge.from_ == node.id for edge in spec.edges):
++            raise WorkflowValidationError(f"gate 节点 {node.id!r} 至少需要一条出边")
++        if node.type == "parallel":
++            if not node.children:
++                raise WorkflowValidationError(f"parallel 节点 {node.id!r} 必须声明 children 子节点列表")
++            for child_id in node.children:
++                if child_id not in nodes_by_id:
++                    raise WorkflowValidationError(f"parallel 节点 {node.id!r} 的子节点 {child_id!r} 不存在")
++            if not any(edge.from_ == node.id for edge in spec.edges):
++                raise WorkflowValidationError(f"parallel 节点 {node.id!r} 至少需要一条出边（fan-in 目标）")
++
++
++class CompiledWorkflow:
++    """已编译的 LangGraph 流程：运行产出并累计事件流。"""
++
++    def __init__(self, spec: WorkflowSpec, handlers: dict[str, NodeHandler]) -> None:
++        self._spec = spec
++        self._handlers = dict(handlers)
++        self._events: list[Event] = []
++        self._run_id = ""
++        self._thread_id = ""
++        self._loop_count = 0
++        self._event_seq = 0
++        self._drained = 0
++        self._start_id = next(node.id for node in spec.nodes if node.type == "start")
++        self._end_id = next(node.id for node in spec.nodes if node.type == "end")
++        self._graph = self._build_graph()
++
++    @property
++    def events(self) -> list[Event]:
++        """返回累计事件流（跨多次 run 累积，按 run_id 区分）。"""
++        return list(self._events)
++
++    def get_graph(self) -> dict:
++        """返回图描述（节点/边列表），供测试与断言使用。"""
++        nodes = [node.model_dump(exclude_none=True, mode="json") for node in self._spec.nodes]
++        edges = [edge.model_dump(exclude_none=True, by_alias=True, mode="json") for edge in self._spec.edges]
++        return {"nodes": nodes, "edges": edges}
++
++    # ------------------------------------------------------------------
++    # 图构建
++    # ------------------------------------------------------------------
++
++    def _build_graph(self) -> Any:
++        graph = StateGraph(ClusterState)
++        nodes_by_id = {node.id: node for node in self._spec.nodes}
++        for node in self._spec.nodes:
++            if node.type == "end":
++                graph.add_node(node.id, self._make_end_wrapper())
++            else:
++                graph.add_node(node.id, self._make_node_wrapper(node))
++        graph.add_edge(START, self._start_id)
++
++        start_edge = next(edge for edge in self._spec.edges if edge.from_ == self._start_id)
++        graph.add_edge(self._start_id, start_edge.to)
++        graph.add_edge(self._end_id, END)
++
++        wired_gates: set[str] = set()
++        wired_parallels: set[str] = set()
++        for edge in self._spec.edges:
++            if edge.from_ in (self._start_id, self._end_id):
++                continue
++            source = nodes_by_id[edge.from_]
++            if source.type == "gate":
++                if edge.from_ not in wired_gates:
++                    self._wire_gate_edges(graph, source)
++                    wired_gates.add(edge.from_)
++            elif source.type == "parallel":
++                if edge.from_ not in wired_parallels:
++                    self._wire_parallel_edges(graph, source)
++                    wired_parallels.add(edge.from_)
++            else:
++                graph.add_edge(edge.from_, edge.to)
++        return graph.compile()
++
++    def _wire_gate_edges(self, graph, node: WorkflowNode) -> None:
++        """把 gate 节点的出边编译为条件路由（基于最后一次审批结论）。"""
++        gate_edges = [edge for edge in self._spec.edges if edge.from_ == node.id]
++        fallback_to = gate_edges[0].to
++        targets: dict[str, str] = {
++            "accept": next((edge.on_accept for edge in gate_edges if edge.on_accept), fallback_to),
++            "reject": next((edge.on_reject for edge in gate_edges if edge.on_reject), fallback_to),
++            "edit": next((edge.on_edit for edge in gate_edges if edge.on_edit), fallback_to),
++            "response": next((edge.on_response for edge in gate_edges if edge.on_response), None)
++            or next((edge.on_accept for edge in gate_edges if edge.on_accept), fallback_to),
++            "ignore": next((edge.on_accept for edge in gate_edges if edge.on_accept), fallback_to),
++        }
++        path_map = {target: target for target in targets.values()}
++        graph.add_conditional_edges(node.id, self._make_gate_router(node, targets), path_map)
++
++    def _wire_parallel_edges(self, graph, node: WorkflowNode) -> None:
++        """把 parallel 节点编译为 Send fan-out + 子节点汇聚到 fan-in 目标。"""
++        children = list(node.children or [])
++        fan_in_target = next(edge.to for edge in self._spec.edges if edge.from_ == node.id)
++
++        def fan_out(_state: ClusterState) -> list[Send]:
++            return [Send(child_id, {}) for child_id in children]
++
++        graph.add_conditional_edges(node.id, fan_out, list(children))
++        for child_id in children:
++            graph.add_edge(child_id, fan_in_target)
++
++    def _make_gate_router(self, node: WorkflowNode, targets: dict[str, str]) -> Callable[[ClusterState], str]:
++        def route(state: ClusterState) -> str:
++            return targets.get(self._last_gate_decision_type(state, node), targets["accept"])
++
++        return route
++
++    @staticmethod
++    def _last_gate_decision_type(state: ClusterState, node: WorkflowNode) -> str:
++        """读取 gate 载荷的最后一条审批结论；缺失时按 accept 处理。"""
++        if node.gate is None:
++            return "accept"
++        payload = state.gate_payloads.get(node.gate)
++        if payload is None or not payload.decisions:
++            return "accept"
++        return payload.decisions[-1].type
++
++    # ------------------------------------------------------------------
++    # 节点包装器
++    # ------------------------------------------------------------------
++
++    def _make_node_wrapper(self, node: WorkflowNode) -> Callable[[ClusterState], Awaitable[dict[str, Any] | None]]:
++        async def wrapper(state: ClusterState) -> dict[str, Any] | None:
++            return await self._execute_node(state, node)
++
++        return wrapper
++
++    def _make_end_wrapper(self) -> Callable[[ClusterState], Awaitable[None]]:
++        async def wrapper(state: ClusterState) -> None:
++            self._emit("node_start", actor=self._end_id, payload={"node_type": "end", "node_id": self._end_id})
++            self._emit("node_end", actor=self._end_id, payload={"node_type": "end", "node_id": self._end_id})
++            return None
++
++        return wrapper
++
++    async def _execute_node(self, state: ClusterState, node: WorkflowNode) -> dict[str, Any] | None:
++        if node.type == "start":
++            self._loop_count += 1
++        # model_construct 跳过校验，保证 ctx.events 与内部事件缓冲为同一列表引用
++        ctx = NodeContext.model_construct(
++            node_id=node.id,
++            spec=self._spec,
++            events=self._events,
++            run_id=self._run_id,
++            loop_count=self._loop_count,
++        )
++        start_payload: dict[str, Any] = {"node_type": node.type, "node_id": node.id}
++        if node.type == "start":
++            start_payload["loop_count"] = self._loop_count
++        self._emit("node_start", actor=node.id, payload=start_payload)
++
++        if node.type == "start":
++            updates: dict[str, Any] | None = self._execute_start(state)
++        elif node.type == "parallel":
++            updates = {}
++        else:
++            handler = self._handlers.get(node.type)
++            if handler is None:
++                updates = await self._default_handler(state, node, ctx)
++            else:
++                updates = await handler(state, node, ctx)
++
++        self._emit("node_end", actor=node.id, payload={"node_type": node.type, "node_id": node.id})
++        if updates is None:
++            return None
++        if not isinstance(updates, dict):
++            raise TypeError(
++                f"节点 {node.id!r} 的 handler 必须返回 dict 形式的 channel 更新，实际返回 {type(updates).__name__}"
++            )
++        return updates
++
++    def _execute_start(self, state: ClusterState) -> dict[str, Any]:
++        """start 节点：补齐 Project/Iteration 默认值（初始状态已携带时保持原样）。"""
++        updates: dict[str, Any] = {}
++        project = state.project
++        if project is None:
++            project = Project(id=self._default_project_id(), name=self._spec.name or self._default_project_id())
++            updates["project"] = project
++        if not state.iterations:
++            updates["iterations"] = [Iteration(id=f"{project.id}:iter:1", project_id=project.id, number=1)]
++        return updates
++
++    def _default_project_id(self) -> str:
++        """从 thread_id（proj:<id>:iter:<n>）推导项目 id；否则回退流程名。"""
++        thread_id = self._spec.thread_id or ""
++        if thread_id.startswith("proj:"):
++            parts = thread_id.split(":")
++            if len(parts) >= 2 and parts[1]:
++                return parts[1]
++        return self._spec.name or "default-project"
++
++    async def _default_handler(self, state: ClusterState, node: WorkflowNode, ctx: NodeContext) -> dict[str, Any]:
++        """未注册 handler 的占位实现：不改状态、不发额外事件，保证运行不中断。"""
++        return {}
++
++    # ------------------------------------------------------------------
++    # 事件与运行
++    # ------------------------------------------------------------------
++
++    def _emit(self, event_type: str, *, actor: str, payload: dict[str, Any]) -> Event:
++        self._event_seq += 1
++        event = Event(
++            id=f"{self._run_id}:{self._event_seq:04d}",
++            run_id=self._run_id,
++            thread_id=self._thread_id,
++            type=event_type,
++            actor=actor,
++            payload=payload,
++        )
++        self._events.append(event)
++        return event
++
++    async def run(self, initial: dict | None = None, *, thread_id: str | None = None) -> AsyncIterator[Event]:
++        """运行流程：产出事件流并累计到 ``events``。
++
++        - ``initial``：初始 ClusterState 的字段字典（可含 project/iterations 等）。
++        - ``thread_id``：覆盖 spec.thread_id；缺省用 spec.thread_id 或 "default"。
++        - 防死循环：累计执行节点数超过 max_iterations 抛 WorkflowLoopError；
++          LangGraph recursion_limit（max_iterations*4）触发时同样转 WorkflowLoopError。
++        """
++        resolved_thread_id = thread_id or self._spec.thread_id or "default"
++        self._run_id = uuid.uuid4().hex[:12]
++        self._thread_id = resolved_thread_id
++        self._loop_count = 0
++        self._event_seq = 0
++        self._drained = 0
++        initial_state = ClusterState() if initial is None else ClusterState.model_validate(initial)
++
++        yield self._emit(
++            "workflow_start",
++            actor="",
++            payload={"name": self._spec.name, "thread_id": resolved_thread_id},
++        )
++        self._drained = len(self._events)
++
++        executed = 0
++        try:
++            async for step in self._graph.astream(
++                initial_state,
++                config={
++                    "recursion_limit": self._spec.max_iterations * 4,
++                    "configurable": {"thread_id": resolved_thread_id},
++                },
++            ):
++                for node_name in step:
++                    executed += 1
++                    if executed > self._spec.max_iterations:
++                        raise WorkflowLoopError(
++                            f"流程 {self._spec.name!r} 超过最大迭代次数 max_iterations="
++                            f"{self._spec.max_iterations}（已执行节点数 {executed}）"
++                        )
++                pending = list(self._events[self._drained :])
++                self._drained = len(self._events)
++                for event in pending:
++                    yield event
++        except GraphRecursionError as exc:
++            raise WorkflowLoopError(
++                f"流程 {self._spec.name!r} 超过 LangGraph recursion_limit"
++                f"（max_iterations*4={self._spec.max_iterations * 4}），疑似死循环"
++            ) from exc
++
++        yield self._emit(
++            "workflow_end",
++            actor="",
++            payload={"name": self._spec.name, "thread_id": resolved_thread_id},
++        )
++
++
++class WorkflowEngine:
++    """流程引擎：YAML 流程 DSL → 校验 → CompiledWorkflow。
++
++    ``handlers`` 按节点类型注册（"agent"/"meeting"/"gate"）；"start"/"end"/"parallel"
++    为内置节点，不查询 handlers；未注册类型的节点走默认占位 handler。
++    """
++
++    def __init__(self, handlers: dict[str, NodeHandler] | None = None) -> None:
++        self._handlers: dict[str, NodeHandler] = dict(handlers or {})
++
++    def compile(self, yaml_text: str) -> CompiledWorkflow:
++        """解析 YAML → 校验 → 构建 LangGraph StateGraph，返回 CompiledWorkflow。"""
++        try:
++            data = yaml.safe_load(yaml_text)
++        except yaml.YAMLError as exc:
++            raise WorkflowValidationError(f"YAML 解析失败：{exc}") from exc
++        if not isinstance(data, dict):
++            raise WorkflowValidationError("流程 YAML 顶层必须是映射（含 name/nodes/edges 等字段）")
++        try:
++            spec = WorkflowSpec.model_validate(data)
++        except ValidationError as exc:
++            raise WorkflowValidationError(f"流程规格非法：{exc}") from exc
++        _validate_spec(spec)
++        return CompiledWorkflow(spec=spec, handlers=self._handlers)
+diff --git a/tests/test_workflow.py b/tests/test_workflow.py
+new file mode 100644
+index 0000000..323d7f3
+--- /dev/null
++++ b/tests/test_workflow.py
+@@ -0,0 +1,509 @@
++"""Task 3 行为测试：YAML→StateGraph 编译、校验、事件流运行、gate 条件路由、parallel 并行、loop 防死循环。
++
++不依赖 gates.py/roles.py/meetings.py：gate/agent handler 一律用测试内注入的 fake handler。
++"""
++
++from __future__ import annotations
++
++import operator
++import typing
++
++import pytest
++
++from agent_cluster.models import (
++    ActionRequest,
++    ApprovalRecord,
++    ClusterState,
++    Event,
++    GateKind,
++)
++from agent_cluster.workflow import (
++    CompiledWorkflow,
++    NodeContext,
++    WorkflowEngine,
++    WorkflowLoopError,
++    WorkflowNode,
++    WorkflowValidationError,
++)
++
++GATE_AND_PARALLEL_YAML = """
++name: demo-flow
++description: 含 gate 条件路由与 parallel 的演示流程
++max_iterations: 30
++thread_id: "proj:demo:iter:1"
++nodes:
++  - {id: start, type: start}
++  - {id: requirement_review, type: meeting, meeting: requirement_review}
++  - {id: requirement_gate, type: gate, gate: requirement_confirmation}
++  - {id: design, type: agent, role: architect}
++  - {id: dev_fanout, type: parallel, children: [frontend_dev, backend_dev]}
++  - {id: frontend_dev, type: agent, role: frontend}
++  - {id: backend_dev, type: agent, role: backend}
++  - {id: code_review, type: meeting, meeting: code_review}
++  - {id: release_gate, type: gate, gate: release}
++  - {id: end, type: end}
++edges:
++  - {from: start, to: requirement_review}
++  - {from: requirement_review, to: requirement_gate}
++  - {from: requirement_gate, to: design, on_accept: design, on_reject: requirement_review, on_edit: requirement_review}
++  - {from: design, to: dev_fanout}
++  - {from: dev_fanout, to: code_review}
++  - {from: code_review, to: release_gate}
++  - {from: release_gate, to: end, on_accept: end, on_reject: code_review}
++"""
++
++SIMPLE_YAML = """
++name: simple
++max_iterations: 10
++thread_id: "proj:demo:iter:1"
++nodes:
++  - {id: start, type: start}
++  - {id: code, type: agent, role: backend}
++  - {id: review, type: meeting, meeting: code_review}
++  - {id: end, type: end}
++edges:
++  - {from: start, to: code}
++  - {from: code, to: review}
++  - {from: review, to: end}
++"""
++
++GATE_YAML = """
++name: gate-flow
++max_iterations: 20
++thread_id: "proj:demo:iter:1"
++nodes:
++  - {id: start, type: start}
++  - {id: dev, type: agent, role: backend}
++  - {id: quality_gate, type: gate, gate: iteration_acceptance}
++  - {id: rework, type: agent, role: backend}
++  - {id: end, type: end}
++edges:
++  - {from: start, to: dev}
++  - {from: dev, to: quality_gate}
++  - {from: quality_gate, to: end, on_accept: end, on_reject: rework, on_edit: rework, on_response: end}
++  - {from: rework, to: quality_gate}
++"""
++
++PARALLEL_YAML = """
++name: parallel-flow
++max_iterations: 20
++thread_id: "proj:demo:iter:1"
++nodes:
++  - {id: start, type: start}
++  - {id: fanout, type: parallel, children: [fe, be]}
++  - {id: fe, type: agent, role: frontend}
++  - {id: be, type: agent, role: backend}
++  - {id: end, type: end}
++edges:
++  - {from: start, to: fanout}
++  - {from: fanout, to: end}
++"""
++
++LOOP_YAML = """
++name: loop-flow
++max_iterations: 4
++thread_id: "proj:demo:iter:1"
++nodes:
++  - {id: start, type: start}
++  - {id: dev, type: agent, role: backend}
++  - {id: quality_gate, type: gate, gate: iteration_acceptance}
++  - {id: rework, type: agent, role: backend}
++  - {id: end, type: end}
++edges:
++  - {from: start, to: dev}
++  - {from: dev, to: quality_gate}
++  - {from: quality_gate, to: end, on_accept: end, on_reject: rework}
++  - {from: rework, to: quality_gate}
++"""
++
++
++# ---------------------------------------------------------------------------
++# 编译与图描述
++# ---------------------------------------------------------------------------
++
++
++def test_compile_valid_yaml_with_gate_and_parallel():
++    compiled = WorkflowEngine().compile(GATE_AND_PARALLEL_YAML)
++    assert isinstance(compiled, CompiledWorkflow)
++    graph = compiled.get_graph()
++    assert set(graph) == {"nodes", "edges"}
++    node_ids = {node["id"] for node in graph["nodes"]}
++    assert node_ids == {
++        "start",
++        "requirement_review",
++        "requirement_gate",
++        "design",
++        "dev_fanout",
++        "frontend_dev",
++        "backend_dev",
++        "code_review",
++        "release_gate",
++        "end",
++    }
++    by_id = {node["id"]: node for node in graph["nodes"]}
++    assert by_id["start"]["type"] == "start"
++    assert by_id["requirement_gate"]["type"] == "gate"
++    assert by_id["requirement_gate"]["gate"] == "requirement_confirmation"
++    assert by_id["dev_fanout"]["type"] == "parallel"
++    assert by_id["dev_fanout"]["children"] == ["frontend_dev", "backend_dev"]
++    gate_edges = [edge for edge in graph["edges"] if edge["from"] == "requirement_gate"]
++    assert gate_edges == [
++        {
++            "from": "requirement_gate",
++            "to": "design",
++            "on_accept": "design",
++            "on_reject": "requirement_review",
++            "on_edit": "requirement_review",
++        }
++    ]
++
++
++# ---------------------------------------------------------------------------
++# 非法 YAML 逐一抛 WorkflowValidationError
++# ---------------------------------------------------------------------------
++
++INVALID_CASES = [
++    (
++        "duplicate-id",
++        """
++name: invalid
++max_iterations: 10
++nodes:
++  - {id: start, type: start}
++  - {id: dup, type: agent}
++  - {id: dup, type: agent}
++  - {id: end, type: end}
++edges:
++  - {from: start, to: dup}
++  - {from: dup, to: end}
++""",
++        "重复的节点 id",
++    ),
++    (
++        "missing-edge-target",
++        """
++name: invalid
++max_iterations: 10
++nodes:
++  - {id: start, type: start}
++  - {id: a, type: agent}
++  - {id: end, type: end}
++edges:
++  - {from: start, to: ghost}
++  - {from: a, to: end}
++""",
++        "边终点引用不存在的节点",
++    ),
++    (
++        "missing-start",
++        """
++name: invalid
++max_iterations: 10
++nodes:
++  - {id: a, type: agent}
++  - {id: end, type: end}
++edges:
++  - {from: a, to: end}
++""",
++        "缺少 start 节点",
++    ),
++    (
++        "two-starts",
++        """
++name: invalid
++max_iterations: 10
++nodes:
++  - {id: start, type: start}
++  - {id: start2, type: start}
++  - {id: end, type: end}
++edges:
++  - {from: start, to: end}
++""",
++        "多个 start 节点",
++    ),
++    (
++        "gate-without-outgoing-edge",
++        """
++name: invalid
++max_iterations: 10
++nodes:
++  - {id: start, type: start}
++  - {id: g, type: gate, gate: release}
++  - {id: end, type: end}
++edges:
++  - {from: start, to: g}
++""",
++        "gate 节点 'g' 至少需要一条出边",
++    ),
++    (
++        "edge-without-to",
++        """
++name: invalid
++max_iterations: 10
++nodes:
++  - {id: start, type: start}
++  - {id: a, type: agent}
++  - {id: end, type: end}
++edges:
++  - {from: start}
++  - {from: a, to: end}
++""",
++        "流程规格非法",
++    ),
++    (
++        "edge-from-missing-node",
++        """
++name: invalid
++max_iterations: 10
++nodes:
++  - {id: start, type: start}
++  - {id: end, type: end}
++edges:
++  - {from: ghost, to: end}
++  - {from: start, to: end}
++""",
++        "边起点引用不存在的节点",
++    ),
++    (
++        "parallel-without-children",
++        """
++name: invalid
++max_iterations: 10
++nodes:
++  - {id: start, type: start}
++  - {id: p, type: parallel}
++  - {id: end, type: end}
++edges:
++  - {from: start, to: p}
++  - {from: p, to: end}
++""",
++        "必须声明 children",
++    ),
++    (
++        "end-with-outgoing-edge",
++        """
++name: invalid
++max_iterations: 10
++nodes:
++  - {id: start, type: start}
++  - {id: end, type: end}
++  - {id: a, type: agent}
++edges:
++  - {from: start, to: end}
++  - {from: end, to: a}
++""",
++        "end 节点 'end' 不允许有出边",
++    ),
++]
++
++
++@pytest.mark.parametrize(
++    ("_case_name", "yaml_text", "message_part"),
++    INVALID_CASES,
++    ids=[case[0] for case in INVALID_CASES],
++)
++def test_invalid_yaml_raises_validation_error(_case_name, yaml_text, message_part):
++    with pytest.raises(WorkflowValidationError, match=message_part):
++        WorkflowEngine().compile(yaml_text)
++
++
++def test_non_mapping_yaml_raises_validation_error():
++    with pytest.raises(WorkflowValidationError, match="顶层必须是映射"):
++        WorkflowEngine().compile("- just\n- a\n- list\n")
++
++
++# ---------------------------------------------------------------------------
++# 运行：事件序列
++# ---------------------------------------------------------------------------
++
++
++async def test_simple_flow_full_event_sequence():
++    compiled = WorkflowEngine().compile(SIMPLE_YAML)
++    events = [event async for event in compiled.run()]
++    assert [(event.type, event.actor) for event in events] == [
++        ("workflow_start", ""),
++        ("node_start", "start"),
++        ("node_end", "start"),
++        ("node_start", "code"),
++        ("node_end", "code"),
++        ("node_start", "review"),
++        ("node_end", "review"),
++        ("node_start", "end"),
++        ("node_end", "end"),
++        ("workflow_end", ""),
++    ]
++    # events 属性与产出的事件一致
++    assert compiled.events == events
++    assert all(event.thread_id == "proj:demo:iter:1" for event in events)
++
++
++async def test_sequential_chain_runs_all_nodes_in_order():
++    compiled = WorkflowEngine().compile(SIMPLE_YAML)
++    events = [event async for event in compiled.run()]
++    actors = [event.actor for event in events if event.type == "node_start"]
++    assert actors == ["start", "code", "review", "end"]
++
++
++async def test_start_node_defaults_project_and_iteration():
++    """无初始状态时，start 节点从 thread_id 推导 Project/Iteration 默认值。"""
++
++    async def report_handler(state: ClusterState, node: WorkflowNode, ctx: NodeContext) -> dict:
++        ctx.events.append(
++            Event(
++                id=f"{ctx.run_id}:report",
++                run_id=ctx.run_id,
++                thread_id=ctx.spec.thread_id,
++                type="state_report",
++                actor=node.id,
++                payload={
++                    "project_id": state.project.id if state.project else None,
++                    "project_name": state.project.name if state.project else None,
++                    "iteration_ids": [iteration.id for iteration in state.iterations],
++                    "loop_count": ctx.loop_count,
++                },
++            )
++        )
++        return {}
++
++    compiled = WorkflowEngine(handlers={"agent": report_handler}).compile(SIMPLE_YAML)
++    events = [event async for event in compiled.run()]
++    report = next(event for event in events if event.type == "state_report")
++    assert report.payload == {
++        "project_id": "demo",
++        "project_name": "simple",
++        "iteration_ids": ["demo:iter:1"],
++        "loop_count": 1,
++    }
++
++
++async def test_initial_state_is_preserved():
++    """初始状态已携带 project 时，start 节点保持原值不覆盖。"""
++
++    async def report_handler(state: ClusterState, node: WorkflowNode, ctx: NodeContext) -> dict:
++        ctx.events.append(
++            Event(
++                id=f"{ctx.run_id}:report",
++                run_id=ctx.run_id,
++                thread_id=ctx.spec.thread_id,
++                type="state_report",
++                actor=node.id,
++                payload={"project_id": state.project.id if state.project else None},
++            )
++        )
++        return {}
++
++    compiled = WorkflowEngine(handlers={"agent": report_handler}).compile(SIMPLE_YAML)
++    initial = {"project": {"id": "p9", "name": "既有项目"}}
++    events = [event async for event in compiled.run(initial)]
++    report = next(event for event in events if event.type == "state_report")
++    assert report.payload == {"project_id": "p9"}
++
++
++# ---------------------------------------------------------------------------
++# gate 条件路由
++# ---------------------------------------------------------------------------
++
++
++async def test_gate_conditional_routing_takes_rework_then_accept():
++    """第一次审批 reject 走返工边，第二次 accept 放行到 end。"""
++
++    calls = {"count": 0}
++
++    async def fake_gate_handler(state: ClusterState, node: WorkflowNode, ctx: NodeContext) -> dict:
++        calls["count"] += 1
++        decision = "reject" if calls["count"] == 1 else "accept"
++        request = ActionRequest(
++            id=f"ar-{ctx.run_id}-{calls['count']}",
++            kind=node.gate,
++            title="迭代验收审批",
++            decisions=[ApprovalRecord(by_role="pm", type=decision, args={"round": calls["count"]})],
++        )
++        return {
++            "gate_payloads": {node.gate: request},
++            "decisions": [ApprovalRecord(by_role="pm", type=decision)],
++        }
++
++    compiled = WorkflowEngine(handlers={"gate": fake_gate_handler}).compile(GATE_YAML)
++    events = [event async for event in compiled.run()]
++    node_starts = [event for event in events if event.type == "node_start"]
++    actors = [event.actor for event in node_starts]
++    # 第一次 quality_gate reject → rework；第二次 accept → end
++    assert actors == ["start", "dev", "quality_gate", "rework", "quality_gate", "end"]
++    assert calls["count"] == 2
++
++
++async def test_gate_accept_routes_straight_to_end():
++    """门 handler 未注入时（默认占位），gate 按缺省 accept 路由到 to。"""
++
++    compiled = WorkflowEngine().compile(GATE_YAML)
++    events = [event async for event in compiled.run()]
++    actors = [event.actor for event in events if event.type == "node_start"]
++    assert actors == ["start", "dev", "quality_gate", "end"]
++
++
++# ---------------------------------------------------------------------------
++# parallel 并行 fan-out / fan-in
++# ---------------------------------------------------------------------------
++
++
++async def test_parallel_fan_out_all_children_ran():
++    async def agent_handler(state: ClusterState, node: WorkflowNode, ctx: NodeContext) -> dict:
++        ctx.events.append(
++            Event(
++                id=f"{ctx.run_id}:{node.id}",
++                run_id=ctx.run_id,
++                thread_id=ctx.spec.thread_id,
++                type="agent_ran",
++                actor=node.id,
++                payload={"role": node.role},
++            )
++        )
++        return {}
++
++    compiled = WorkflowEngine(handlers={"agent": agent_handler}).compile(PARALLEL_YAML)
++    events = [event async for event in compiled.run()]
++    node_starts = [event for event in events if event.type == "node_start"]
++    assert [event.actor for event in node_starts] == ["start", "fanout", "fe", "be", "end"]
++    agent_ran = {event.actor: event.payload["role"] for event in events if event.type == "agent_ran"}
++    assert agent_ran == {"fe": "frontend", "be": "backend"}
++
++
++# ---------------------------------------------------------------------------
++# 防死循环
++# ---------------------------------------------------------------------------
++
++
++async def test_loop_limit_raises_workflow_loop_error():
++    async def always_reject(state: ClusterState, node: WorkflowNode, ctx: NodeContext) -> dict:
++        request = ActionRequest(
++            id=f"ar-{ctx.run_id}",
++            kind=node.gate,
++            title="迭代验收审批",
++            decisions=[ApprovalRecord(by_role="pm", type="reject")],
++        )
++        return {"gate_payloads": {node.gate: request}}
++
++    compiled = WorkflowEngine(handlers={"gate": always_reject}).compile(LOOP_YAML)
++    with pytest.raises(WorkflowLoopError, match="max_iterations=4"):
++        _ = [event async for event in compiled.run()]
++
++
++# ---------------------------------------------------------------------------
++# ClusterState reducer 契约（Task 1 模型 retrofit）
++# ---------------------------------------------------------------------------
++
++
++def test_cluster_state_list_channels_use_add_reducers():
++    hints = typing.get_type_hints(ClusterState, include_extras=True)
++    for field_name in ("iterations", "tasks", "meetings", "decisions", "messages"):
++        assert hints[field_name].__metadata__ == (operator.add,), field_name
++
++
++def test_action_request_carries_decisions():
++    request = ActionRequest(
++        id="ar1",
++        kind=GateKind.RELEASE,
++        title="发布审批",
++        decisions=[ApprovalRecord(by_role="pm", type="reject")],
++    )
++    assert request.decisions[-1].type == "reject"
+```
diff --git a/.superpowers/sdd/task-3-report.md b/.superpowers/sdd/task-3-report.md
new file mode 100644
index 0000000..1479856
--- /dev/null
+++ b/.superpowers/sdd/task-3-report.md
@@ -0,0 +1,89 @@
+# Task 3 报告：流程引擎（YAML→StateGraph 编译与事件流运行）
+
+## 1. 实现摘要
+
+- `src/agent_cluster/workflow.py`（新增，约 470 行）：
+  - `WorkflowValidationError` / `WorkflowLoopError` 两个异常。
+  - `WorkflowNode`（id/type/meeting/role/gate/children）、`WorkflowEdge`（`from_` 用 pydantic alias 映射 YAML 的 `from`；`to`/`on_accept`/`on_reject`/`on_edit`/`on_response`）、`WorkflowSpec`（name 必填，description 默认空串，max_iterations 默认 10 且 >0，thread_id 默认空串，nodes/edges 必填）。
+  - `NodeContext`（node_id/spec/events/run_id/loop_count）；`NodeHandler` 类型别名。
+  - `WorkflowEngine.compile(yaml_text)`：`yaml.safe_load` → `WorkflowSpec.model_validate`（pydantic ValidationError 包装为 WorkflowValidationError）→ `_validate_spec` → `CompiledWorkflow`（内部构建 `StateGraph(ClusterState)`）。
+  - `CompiledWorkflow.run(initial=None, *, thread_id=None)`：`graph.astream(initial_state, config={"recursion_limit": max_iterations*4, "configurable": {"thread_id": ...}})`，产出 `workflow_start` → 每节点 `node_start`/`node_end` → `workflow_end`，全部累计进 `events`。
+  - 编译校验：重复节点 id；边 from/to 及 on_* 目标引用不存在的节点；start/end 缺失或重复；start 必须有出边；end 不允许有出边；gate 必须至少一条出边；parallel 必须声明 children、子节点必须存在、必须有 fan-in 出边；边必须有 from/to（缺字段走 pydantic 校验）。
+  - 节点语义：`start` 在初始状态缺 project/iterations 时补默认值（project id 从 `thread_id="proj:<id>:iter:<n>"` 推导，回退流程名；iteration id `{project.id}:iter:1`），走第一条出边；`end` 为终止节点（返回 None，接 `END`）；`agent`/`meeting`/`gate` 查 `handlers`（按节点类型注册），未注册走默认占位 handler（返回 `{}`，不改状态不发额外事件）；`parallel` 内置 fan-out/fan-in（见 §4）。
+  - 防死循环：`run()` 内统计每次运行累计执行节点数，超过 `spec.max_iterations` 抛 `WorkflowLoopError`；LangGraph `GraphRecursionError`（recursion_limit 触顶）也转为 `WorkflowLoopError`。每条边按 on_reject/on_edit 等天然支持返工回环，无需额外机制。
+- `src/agent_cluster/models.py`（Task 1  sanctioned retrofit，最小改动）：
+  - `ClusterState` 五个 list 字段改为 `Annotated[list[X], operator.add]`：`iterations/tasks/meetings/decisions/messages`，LangGraph 频道追加而非覆盖。
+  - `ActionRequest` 新增 `decisions: list[ApprovalRecord]`（default_factory=list，向后兼容）：Task 3 门路由契约的载荷载体（Task 1 模型没有该字段，简报路由描述"ActionRequest 的 .decisions"正是此意）。
+  - 其余字段与模型不动；Task 1 的 33 个测试原样通过。
+- `src/agent_cluster/__init__.py`：导出 `WorkflowEngine/CompiledWorkflow/WorkflowSpec/WorkflowNode/WorkflowEdge/WorkflowValidationError/WorkflowLoopError/NodeContext/NodeHandler`。
+- `tests/test_workflow.py`（新增 21 个测试）。
+
+## 2. 测试与命令输出
+
+新增测试覆盖：合法 YAML（含 gate 条件路由 + parallel）编译与 `get_graph()` 断言；非法 YAML 逐项抛 `WorkflowValidationError`（重复 id、缺失边终点、无 start、双 start、gate 无出边、边缺 to、边起点悬空、parallel 缺 children、end 有出边、非映射顶层）；简单流程完整事件序列；顺序链；start 默认 Project/Iteration 与初始状态保留；gate 条件路由（fake handler 先 reject 走返工边、再 accept 到 end）；gate 无 handler 时按缺省 accept 路由；parallel 全部子节点运行；loop 超限抛 `WorkflowLoopError`；ClusterState reducer 注解契约；ActionRequest.decisions。
+
+`uv run pytest -q` 全量输出（73 passed = 既有 52 + 新增 21）：
+
+```
+$ uv run pytest -q
+........................................................................ [ 98%]
+.                                                                        [100%]
+73 passed in 0.94s
+```
+
+`uv run pytest tests/test_workflow.py -q`：
+
+```
+$ uv run pytest tests/test_workflow.py -q
+.....................                                                    [100%]
+21 passed in 0.81s
+```
+
+## 3. gate_payloads / 审批载荷契约（Task 4 gates.py 必须遵守）
+
+- **存储位置与键**：`ClusterState.gate_payloads: dict[GateKind, ActionRequest]`（Task 1 已锁定键类型为 GateKind，键 = gate 节点的 `node.gate` 字段；简报原文写 `gate_payloads[node_id]`，因 Task 1 模型约束改为按 GateKind 键，见 §5 偏离说明）。
+- **载荷**：`ActionRequest`（含 `decisions: list[ApprovalRecord]`，新增字段），其 `decisions[-1]` 为本次审批结论。
+- **Task 4 的 "gate" handler 返回**（LangGraph channel 更新字典）：
+  ```python
+  async def gate_handler(state: ClusterState, node: WorkflowNode, ctx: NodeContext) -> dict:
+      request = ActionRequest(
+          id=..., kind=node.gate, title=..., description=...,
+          evidence=..., risk_level=..., bypass_immune=...,
+          decisions=[ApprovalRecord(by_role=..., type="reject", args=...)],
+      )
+      return {"gate_payloads": {node.gate: request}, "decisions": [ApprovalRecord(by_role=..., type="reject")]}
+  ```
+  （`decisions` 频道有 `operator.add` reducer，追加即可；`gate_payloads` 无 reducer，整体覆盖。）
+- **路由表**（路由器读 `state.gate_payloads[node.gate].decisions[-1].type`）：
+  | 结论 type | 目标 |
+  |---|---|
+  | `accept` | `on_accept`（缺省 `to`） |
+  | `reject` | `on_reject`（缺省 `to`） |
+  | `edit` | `on_edit`（缺省 `to`） |
+  | `response` | `on_response`（缺省 `on_accept`→`to`） |
+  | `ignore` 或载荷缺失/无 decisions | `on_accept`（缺省 `to`） |
+- `ApprovalRecord.type` 的合法值为 `accept/reject/edit/response/ignore`（Task 1 定义）；注意 `HumanResponse.type` 只有 `accept/ignore/response/edit` 且无 reject，因此**载荷不用 HumanResponse**，统一用 ActionRequest.decisions 里的 ApprovalRecord。
+
+## 4. parallel 并行方案
+
+- 采用 **LangGraph `Send` API**（实测 langgraph 1.2.11 可用，无需 asyncio.gather）：
+  - 编译期对 parallel 节点注册条件边：`add_conditional_edges(parallel_id, fan_out, list(children))`，`fan_out` 返回 `[Send(child_id, {}) for child_id in children]`。
+  - 每个子节点仍以普通图节点注册（走同一套 wrapper，产出 `node_start`/`node_end`），并自动 `add_edge(child, fan_in_target)`，fan_in_target = parallel 节点的 `to` 出边目标；所有分支完成后 fan-in 节点只跑一次（LangGraph 原生等待 Send 分支合并）。
+  - 子节点可为任意节点类型；children 不应自带出边（并行汇聚由 parallel 的 `to` 决定），已在报告/模块 docstring 说明。
+
+## 5. 偏离与决策说明
+
+1. **NodeHandler 返回类型**：简报给出 `Awaitable[ClusterState]` 并注明"或返回 dict of channel updates —— pick ONE, document it"。本实现选 **dict of channel updates**（`Callable[[ClusterState, WorkflowNode, NodeContext], Awaitable[dict[str, Any]]]`）：与 `operator.add` reducer 天然一致（handler 追加、绝不整体替换 list 频道），且与简报自述的 `handler writes {"gate_payloads": {...}}` 一致；返回 None 视为无更新，返回非 dict 抛 TypeError。Task 4/5 按此注册。
+2. **gate_payloads 键**：简报写 `state.gate_payloads[node_id]`，但 Task 1 已锁定 `dict[GateKind, ActionRequest]`（test_models.py 断言 `gate_payloads[GateKind.RELEASE]`），"keep everything else unchanged" 为硬约束，故键改用 `node.gate`（GateKind）。局限：同流程内两个同 GateKind 的门会互相覆盖载荷，Task 7 编排示例时应避免；如需按 node_id 键可在后续任务演进模型（会破坏 Task 1 契约，需评审）。
+3. **ActionRequest 新增 `decisions` 字段**：简报路由描述的前提（"ActionRequest 的 .decisions"）在 Task 1 模型中缺失，本任务以向后兼容的 default_factory 字段补齐，Task 1 的 33 个测试原样通过。
+4. **loop 语义**：`max_iterations` = 单次运行累计**节点执行次数**上限（不是"轮数"）。这是对简报 "track executed-node count" 的字面实现，且能覆盖不经过 start 的 gate 返工回环（如 reject→rework→gate→…），无需额外机制；LangGraph recursion_limit=`max_iterations*4` 兜底。注意：线性流程的节点数必须 ≤ max_iterations（共享契约示例中 8 节点配 max_iterations: 5 需要 Task 7 建示例时调大，如 30）。
+5. **node_start/node_end 事件**：由编译期 wrapper 对每个执行节点统一发出（含 start/end），默认占位 handler 不发额外事件——避免与 wrapper 事件重复；满足"未注册 handler 编译与运行不中断"与"每节点 node_start/node_end"双重约束。
+6. **事件缓冲**：`NodeContext.events` 通过 `NodeContext.model_construct` 与内部事件缓冲保持同一列表引用（pydantic 构造默认会拷贝列表，直接构造会让 handler append 丢失）。
+7. **无 checkpointer**：`run()` 未挂 MemorySaver（简报未要求）；`configurable.thread_id` 仅作元数据传入。Task 7 CLI 若需断点续跑/审批恢复，可自行挂 MemorySaver + interrupt。
+8. 未创建 gates.py/roles.py/meetings.py/examples 示例（属 Task 4/5/7）；tests 用注入的 fake handler，不依赖这些模块。
+
+## 6. 提交
+
+- Commit SHA：`4179512`（`Task 3: 流程引擎 YAML→StateGraph`）
+- 变更文件：`src/agent_cluster/workflow.py`（新增）、`tests/test_workflow.py`（新增）、`src/agent_cluster/models.py`、`src/agent_cluster/__init__.py`
+- 工作区干净；`uv run pytest -q` 全绿（73 passed）。
diff --git a/docs/superpowers/plans/implementation-plan.md b/docs/superpowers/plans/implementation-plan.md
index 2fa623b..8e86e2e 100644
--- a/docs/superpowers/plans/implementation-plan.md
+++ b/docs/superpowers/plans/implementation-plan.md
@@ -38,7 +38,7 @@
 - YAML 流程 DSL（ChatDev 风格，`WorkflowEngine` 编译为图）：
   ```yaml
   name: <流程名>
-  max_iterations: 5            # 防死循环（ChatDev loop_counter）
+  max_iterations: 20           # 防死循环：总节点执行上限，编译期校验必须 ≥ 节点总数（ChatDev loop_counter 思路）
   thread_id: "proj:demo:iter:1"
   nodes:
     - {id: start, type: start}
@@ -56,6 +56,7 @@
   ```
   - 节点类型：`start/end/agent/meeting/gate/parallel`；`agent` 节点执行指定岗位（走 AgentRuntime）；`meeting` 节点跑会议子图；`gate` 节点触发 interrupt 审批；`parallel` 节点并行跑多个子节点（fan-out/fan-in）。
   - 边：`from/to`；gate 后允许 `on_accept/on_reject/on_edit/on_response` 条件路由（缺省回落到 `to`）；其余边默认顺序流转。
+  - 语义：`max_iterations` = 单次运行总节点执行上限，编译期校验必须 ≥ 节点总数；线性流程节点数不得大于该值，运行时累计执行节点数超过即抛 `WorkflowLoopError`。
   - 编译规则：非法节点引用/缺边/重复 id 一律抛 `WorkflowValidationError`（含精确报错信息）。
 - 事件模型（§5.7）：`Event{id, run_id, thread_id, type, actor, payload, ts}`；type：`node_start/node_end/meeting/approval_created/approval_resolved/tool_call/metrics/evolution_*`；EventBus 为 append-only 列表。
 - 运行方式：`WorkflowEngine.compile(yaml_text) -> CompiledWorkflow`；`CompiledWorkflow.run(initial_state) -> AsyncIterator[Event]`；审批通过 `WorkflowEngine.resume(thread_id, decision)` 恢复。
diff --git a/src/agent_cluster/workflow.py b/src/agent_cluster/workflow.py
index e7c0039..030ae98 100644
--- a/src/agent_cluster/workflow.py
+++ b/src/agent_cluster/workflow.py
@@ -6,8 +6,14 @@
 - 节点类型：``start``/``end``/``agent``/``meeting``/``gate``/``parallel``。
 - 事件流：每次运行产出 ``workflow_start``/``node_start``/``node_end``/``workflow_end``
   事件；handler 可通过 ``ctx.events`` 追加自定义事件。
-- 防死循环：统计每次运行累计执行的节点数，超过 ``max_iterations`` 抛
-  ``WorkflowLoopError``；LangGraph ``recursion_limit = max_iterations * 4`` 兜底。
+- 防死循环：``max_iterations`` = 单次运行总节点执行上限（编译期校验必须 ≥ 节点总数），
+  运行时累计执行节点数超过即抛 ``WorkflowLoopError``；LangGraph
+  ``recursion_limit = max_iterations * 4`` 兜底。
+- 中断/恢复：gate handler 调用 ``interrupt()`` 时流程挂起，``run()`` 排空事件后产出
+  ``workflow_suspended``（payload 含 ``node_id``/``thread_id``）并正常结束迭代；
+  ``resume()`` 以 ``Command(resume=response)`` 继续（需与 run() 相同的 checkpointer）。
+- 并发安全：每次 run()/resume() 迭代的 ``run_id``/事件缓冲/计数器保存在本地
+  ``_RunState`` 对象中，节点包装器通过 ContextVar 读取，不共享可变状态。
 
 handler 契约（Task 4/5 据此注册）：
 - ``WorkflowEngine(handlers={"agent": ..., "meeting": ..., "gate": ...})`` 按
@@ -27,20 +33,25 @@ handler 契约（Task 4/5 据此注册）：
   ``accept``→``on_accept``（缺省 ``to``）；``reject``→``on_reject``（缺省 ``to``）；
   ``edit``→``on_edit``（缺省 ``to``）；``response``→``on_response``（缺省
   ``on_accept``→``to``）；``ignore`` 或未写入载荷→``on_accept``（缺省 ``to``）。
+- 中断契约（Task 4 gates.py）：gate handler 可调用
+  ``decision = interrupt(action_request)`` 挂起流程等待人工审批；恢复时
+  ``interrupt()`` 返回审批响应（如 ``HumanResponse``），handler 据此写
+  ``gate_payloads``。``run()`` 检测到挂起时产出 ``workflow_suspended`` 事件。
 - parallel 并行：编译期用 LangGraph ``Send`` API fan-out 到子节点、子节点各自
   ``add_edge(child, fan_in_target)`` 汇聚；所有子节点仍注册为图节点并产出事件。
 """
 
 from __future__ import annotations
 
+import contextvars
 import uuid
 from collections.abc import AsyncIterator, Awaitable, Callable
 from typing import Any, Literal
 
 import yaml
-from langgraph.errors import GraphRecursionError
+from langgraph.errors import GraphInterrupt, GraphRecursionError
 from langgraph.graph import END, START, StateGraph
-from langgraph.types import Send
+from langgraph.types import Command, Send
 from pydantic import BaseModel, ConfigDict, Field, ValidationError
 
 from agent_cluster.models import (
@@ -106,7 +117,11 @@ class WorkflowSpec(BaseModel):
 
     name: str = Field(description="流程名称")
     description: str = Field(default="", description="流程描述")
-    max_iterations: int = Field(default=10, gt=0, description="防死循环：单次运行最大节点执行次数")
+    max_iterations: int = Field(
+        default=10,
+        gt=0,
+        description="防死循环：总节点执行上限，编译期校验必须 ≥ 节点总数",
+    )
     thread_id: str = Field(default="", description="线程 id（缺省运行时使用）")
     nodes: list[WorkflowNode] = Field(description="节点列表")
     edges: list[WorkflowEdge] = Field(description="边列表")
@@ -127,14 +142,37 @@ class NodeContext(BaseModel):
 NodeHandler = Callable[[ClusterState, WorkflowNode, NodeContext], Awaitable[dict[str, Any]]]
 
 
+class _RunState:
+    """单次 run()/resume() 迭代的本地运行状态（事件缓冲与计数器）。
+
+    每次迭代独立持有，避免并发运行共享可变状态；节点包装器通过 ContextVar 读取。
+    """
+
+    __slots__ = ("run_id", "thread_id", "loop_count", "event_seq", "drained", "events")
+
+    def __init__(self, run_id: str, thread_id: str) -> None:
+        self.run_id = run_id
+        self.thread_id = thread_id
+        self.loop_count = 0
+        self.event_seq = 0
+        self.drained = 0
+        self.events: list[Event] = []
+
+
 def _validate_spec(spec: WorkflowSpec) -> None:
-    """编译前校验：重复 id、悬空引用、start/end 唯一性与出边、gate 出边、parallel children。"""
+    """编译前校验：重复 id、悬空引用、start/end 唯一性与出边、gate 出边、parallel children、max_iterations。"""
     nodes_by_id: dict[str, WorkflowNode] = {}
     for node in spec.nodes:
         if node.id in nodes_by_id:
             raise WorkflowValidationError(f"重复的节点 id：{node.id!r}")
         nodes_by_id[node.id] = node
 
+    if spec.max_iterations < len(spec.nodes):
+        raise WorkflowValidationError(
+            f"max_iterations={spec.max_iterations} 小于节点总数 {len(spec.nodes)}："
+            "max_iterations 为总节点执行上限，编译期必须 ≥ 节点总数"
+        )
+
     start_nodes = [node for node in spec.nodes if node.type == "start"]
     end_nodes = [node for node in spec.nodes if node.type == "end"]
     if not start_nodes:
@@ -179,25 +217,25 @@ def _validate_spec(spec: WorkflowSpec) -> None:
 
 
 class CompiledWorkflow:
-    """已编译的 LangGraph 流程：运行产出并累计事件流。"""
+    """已编译的 LangGraph 流程：运行/恢复产出事件流。"""
 
     def __init__(self, spec: WorkflowSpec, handlers: dict[str, NodeHandler]) -> None:
         self._spec = spec
         self._handlers = dict(handlers)
-        self._events: list[Event] = []
-        self._run_id = ""
-        self._thread_id = ""
-        self._loop_count = 0
-        self._event_seq = 0
-        self._drained = 0
         self._start_id = next(node.id for node in spec.nodes if node.type == "start")
         self._end_id = next(node.id for node in spec.nodes if node.type == "end")
-        self._graph = self._build_graph()
+        self._graph = self._compile_graph()
+        self._run_state_var: contextvars.ContextVar[_RunState | None] = contextvars.ContextVar(
+            f"agent_cluster_run_state_{id(self)}", default=None
+        )
+        self._last_run_state: _RunState | None = None
 
     @property
     def events(self) -> list[Event]:
-        """返回累计事件流（跨多次 run 累积，按 run_id 区分）。"""
-        return list(self._events)
+        """最近一次 run()/resume() 迭代的事件流（每次迭代独立持有，避免并发共享）。"""
+        if self._last_run_state is None:
+            return []
+        return list(self._last_run_state.events)
 
     def get_graph(self) -> dict:
         """返回图描述（节点/边列表），供测试与断言使用。"""
@@ -205,11 +243,15 @@ class CompiledWorkflow:
         edges = [edge.model_dump(exclude_none=True, by_alias=True, mode="json") for edge in self._spec.edges]
         return {"nodes": nodes, "edges": edges}
 
+    def get_compiled_graph(self) -> Any:
+        """返回底层已编译的 LangGraph StateGraph（供 Task 4/7 检查或驱动）。"""
+        return self._graph
+
     # ------------------------------------------------------------------
     # 图构建
     # ------------------------------------------------------------------
 
-    def _build_graph(self) -> Any:
+    def _make_state_graph(self) -> StateGraph:
         graph = StateGraph(ClusterState)
         nodes_by_id = {node.id: node for node in self._spec.nodes}
         for node in self._spec.nodes:
@@ -239,7 +281,11 @@ class CompiledWorkflow:
                     wired_parallels.add(edge.from_)
             else:
                 graph.add_edge(edge.from_, edge.to)
-        return graph.compile()
+        return graph
+
+    def _compile_graph(self, checkpointer: Any | None = None):
+        """编译 StateGraph；checkpointer 需在 compile 时绑定（LangGraph 约束）。"""
+        return self._make_state_graph().compile(checkpointer=checkpointer)
 
     def _wire_gate_edges(self, graph, node: WorkflowNode) -> None:
         """把 gate 节点的出边编译为条件路由（基于最后一次审批结论）。"""
@@ -303,19 +349,20 @@ class CompiledWorkflow:
         return wrapper
 
     async def _execute_node(self, state: ClusterState, node: WorkflowNode) -> dict[str, Any] | None:
+        run_state = self._require_run_state()
         if node.type == "start":
-            self._loop_count += 1
-        # model_construct 跳过校验，保证 ctx.events 与内部事件缓冲为同一列表引用
+            run_state.loop_count += 1
+        # model_construct 跳过校验，保证 ctx.events 与本次迭代事件缓冲为同一列表引用
         ctx = NodeContext.model_construct(
             node_id=node.id,
             spec=self._spec,
-            events=self._events,
-            run_id=self._run_id,
-            loop_count=self._loop_count,
+            events=run_state.events,
+            run_id=run_state.run_id,
+            loop_count=run_state.loop_count,
         )
         start_payload: dict[str, Any] = {"node_type": node.type, "node_id": node.id}
         if node.type == "start":
-            start_payload["loop_count"] = self._loop_count
+            start_payload["loop_count"] = run_state.loop_count
         self._emit("node_start", actor=node.id, payload=start_payload)
 
         if node.type == "start":
@@ -366,51 +413,78 @@ class CompiledWorkflow:
     # 事件与运行
     # ------------------------------------------------------------------
 
+    def _require_run_state(self) -> _RunState:
+        run_state = self._run_state_var.get()
+        if run_state is None:
+            raise RuntimeError("节点只能在 run()/resume() 迭代内执行")
+        return run_state
+
     def _emit(self, event_type: str, *, actor: str, payload: dict[str, Any]) -> Event:
-        self._event_seq += 1
+        run_state = self._require_run_state()
+        run_state.event_seq += 1
         event = Event(
-            id=f"{self._run_id}:{self._event_seq:04d}",
-            run_id=self._run_id,
-            thread_id=self._thread_id,
+            id=f"{run_state.run_id}:{run_state.event_seq:04d}",
+            run_id=run_state.run_id,
+            thread_id=run_state.thread_id,
             type=event_type,
             actor=actor,
             payload=payload,
         )
-        self._events.append(event)
+        run_state.events.append(event)
         return event
 
-    async def run(self, initial: dict | None = None, *, thread_id: str | None = None) -> AsyncIterator[Event]:
-        """运行流程：产出事件流并累计到 ``events``。
-
-        - ``initial``：初始 ClusterState 的字段字典（可含 project/iterations 等）。
-        - ``thread_id``：覆盖 spec.thread_id；缺省用 spec.thread_id 或 "default"。
-        - 防死循环：累计执行节点数超过 max_iterations 抛 WorkflowLoopError；
-          LangGraph recursion_limit（max_iterations*4）触发时同样转 WorkflowLoopError。
-        """
-        resolved_thread_id = thread_id or self._spec.thread_id or "default"
-        self._run_id = uuid.uuid4().hex[:12]
-        self._thread_id = resolved_thread_id
-        self._loop_count = 0
-        self._event_seq = 0
-        self._drained = 0
-        initial_state = ClusterState() if initial is None else ClusterState.model_validate(initial)
-
-        yield self._emit(
-            "workflow_start",
+    def _build_config(self, resolved_thread_id: str, config: dict | None) -> dict:
+        """合并运行配置：内部 recursion_limit/thread_id 为基，用户 config 覆盖合并。"""
+        merged: dict[str, Any] = {
+            "recursion_limit": self._spec.max_iterations * 4,
+            "configurable": {"thread_id": resolved_thread_id},
+        }
+        if config:
+            merged = {**merged, **config}
+            if isinstance(config.get("configurable"), dict):
+                merged["configurable"] = {**merged["configurable"], **config["configurable"]}
+        return merged
+
+    def _drain_pending(self, run_state: _RunState) -> list[Event]:
+        pending = list(run_state.events[run_state.drained :])
+        run_state.drained = len(run_state.events)
+        return pending
+
+    def _suspended_event(self, run_state: _RunState) -> Event:
+        """从最近一次 node_start 推导被 interrupt() 挂起的节点 id。"""
+        node_id = next(
+            (event.actor for event in reversed(run_state.events) if event.type == "node_start"),
+            "",
+        )
+        return self._emit(
+            "workflow_suspended",
             actor="",
-            payload={"name": self._spec.name, "thread_id": resolved_thread_id},
+            payload={"node_id": node_id, "thread_id": run_state.thread_id},
         )
-        self._drained = len(self._events)
 
+    async def _stream_steps(
+        self,
+        graph: Any,
+        astream_input: Any,
+        run_state: _RunState,
+        config: dict,
+    ) -> AsyncIterator[Event]:
+        """驱动 astream：循环守卫 + 事件排空 + 挂起/异常处理。
+
+        - 累计执行节点数超过 max_iterations 抛 WorkflowLoopError；
+          GraphRecursionError 同样转 WorkflowLoopError。
+        - langgraph 1.x 的 interrupt() 以 ``__interrupt__`` 流步挂起（不抛异常）；
+          兼容旧版以 GraphInterrupt 异常挂起。两者都排空事件并产出
+          ``workflow_suspended`` 后正常结束迭代（不向上抛）。
+        """
         executed = 0
         try:
-            async for step in self._graph.astream(
-                initial_state,
-                config={
-                    "recursion_limit": self._spec.max_iterations * 4,
-                    "configurable": {"thread_id": resolved_thread_id},
-                },
-            ):
+            async for step in graph.astream(astream_input, config=config):
+                if "__interrupt__" in step:
+                    for event in self._drain_pending(run_state):
+                        yield event
+                    yield self._suspended_event(run_state)
+                    return
                 for node_name in step:
                     executed += 1
                     if executed > self._spec.max_iterations:
@@ -418,21 +492,101 @@ class CompiledWorkflow:
                             f"流程 {self._spec.name!r} 超过最大迭代次数 max_iterations="
                             f"{self._spec.max_iterations}（已执行节点数 {executed}）"
                         )
-                pending = list(self._events[self._drained :])
-                self._drained = len(self._events)
-                for event in pending:
+                for event in self._drain_pending(run_state):
                     yield event
+        except GraphInterrupt:
+            for event in self._drain_pending(run_state):
+                yield event
+            yield self._suspended_event(run_state)
         except GraphRecursionError as exc:
             raise WorkflowLoopError(
                 f"流程 {self._spec.name!r} 超过 LangGraph recursion_limit"
                 f"（max_iterations*4={self._spec.max_iterations * 4}），疑似死循环"
             ) from exc
 
-        yield self._emit(
-            "workflow_end",
-            actor="",
-            payload={"name": self._spec.name, "thread_id": resolved_thread_id},
-        )
+    async def run(
+        self,
+        initial: dict | None = None,
+        *,
+        thread_id: str | None = None,
+        checkpointer: Any | None = None,
+        config: dict | None = None,
+    ) -> AsyncIterator[Event]:
+        """运行流程：产出事件流（最近一次迭代可从 ``events`` 属性取回）。
+
+        - ``initial``：初始 ClusterState 的字段字典（可含 project/iterations 等）。
+        - ``thread_id``：覆盖 spec.thread_id；缺省用 spec.thread_id 或 "default"。
+        - ``checkpointer``：可选，如 ``langgraph.checkpoint.memory.MemorySaver``，
+          用于 interrupt() 挂起后的 resume()；不传则无法恢复。
+        - ``config``：可选，覆盖合并到内部 config（recursion_limit/thread_id）。
+        - 挂起：gate handler 调用 interrupt() 时产出 ``workflow_suspended`` 事件并
+          正常结束迭代（不抛异常）；随后用 ``resume()`` 继续。
+        """
+        resolved_thread_id = thread_id or self._spec.thread_id or "default"
+        run_state = _RunState(run_id=uuid.uuid4().hex[:12], thread_id=resolved_thread_id)
+        token = self._run_state_var.set(run_state)
+        try:
+            self._last_run_state = run_state
+            initial_state = ClusterState() if initial is None else ClusterState.model_validate(initial)
+            yield self._emit(
+                "workflow_start",
+                actor="",
+                payload={"name": self._spec.name, "thread_id": resolved_thread_id},
+            )
+            run_state.drained = len(run_state.events)  # workflow_start 已产出
+            graph = self._graph if checkpointer is None else self._compile_graph(checkpointer=checkpointer)
+            async for event in self._stream_steps(
+                graph, initial_state, run_state, self._build_config(resolved_thread_id, config)
+            ):
+                yield event
+            if run_state.events and run_state.events[-1].type != "workflow_suspended":
+                yield self._emit(
+                    "workflow_end",
+                    actor="",
+                    payload={"name": self._spec.name, "thread_id": resolved_thread_id},
+                )
+        finally:
+            self._run_state_var.reset(token)
+
+    async def resume(
+        self,
+        thread_id: str,
+        response: Any,
+        *,
+        checkpointer: Any | None = None,
+        config: dict | None = None,
+    ) -> AsyncIterator[Event]:
+        """恢复被 interrupt() 挂起的流程：以 ``Command(resume=response)`` 重新 astream。
+
+        - 必须传入与 run() 相同的 checkpointer（LangGraph 检查点保存挂起状态）。
+        - 挂起节点在恢复时会重新执行：``interrupt()`` 返回 ``response``（如
+          HumanResponse），handler 据此继续并产出后续事件。
+        """
+        if checkpointer is None:
+            raise ValueError("resume() 需要 checkpointer（如 MemorySaver）以读取线程检查点")
+        run_state = _RunState(run_id=uuid.uuid4().hex[:12], thread_id=thread_id)
+        token = self._run_state_var.set(run_state)
+        try:
+            self._last_run_state = run_state
+            yield self._emit(
+                "workflow_start",
+                actor="",
+                payload={"name": self._spec.name, "thread_id": thread_id, "resume": True},
+            )
+            run_state.drained = len(run_state.events)  # workflow_start 已产出
+            graph = self._compile_graph(checkpointer=checkpointer)
+            async for event in self._stream_steps(
+                graph, Command(resume=response), run_state, self._build_config(thread_id, config)
+            ):
+                yield event
+            if run_state.events and run_state.events[-1].type != "workflow_suspended":
+                yield self._emit(
+                    "workflow_end",
+                    actor="",
+                    payload={"name": self._spec.name, "thread_id": thread_id},
+                )
+        finally:
+            self._run_state_var.reset(token)
 
 
 class WorkflowEngine:
diff --git a/tests/test_workflow.py b/tests/test_workflow.py
index 323d7f3..f2eb5ac 100644
--- a/tests/test_workflow.py
+++ b/tests/test_workflow.py
@@ -16,7 +16,11 @@ from agent_cluster.models import (
     ClusterState,
     Event,
     GateKind,
+    HumanResponse,
 )
+from langgraph.checkpoint.memory import MemorySaver
+from langgraph.types import interrupt
+
 from agent_cluster.workflow import (
     CompiledWorkflow,
     NodeContext,
@@ -101,7 +105,7 @@ edges:
 
 LOOP_YAML = """
 name: loop-flow
-max_iterations: 4
+max_iterations: 5
 thread_id: "proj:demo:iter:1"
 nodes:
   - {id: start, type: start}
@@ -484,7 +488,7 @@ async def test_loop_limit_raises_workflow_loop_error():
         return {"gate_payloads": {node.gate: request}}
 
     compiled = WorkflowEngine(handlers={"gate": always_reject}).compile(LOOP_YAML)
-    with pytest.raises(WorkflowLoopError, match="max_iterations=4"):
+    with pytest.raises(WorkflowLoopError, match="max_iterations=5"):
         _ = [event async for event in compiled.run()]
 
 
@@ -507,3 +511,83 @@ def test_action_request_carries_decisions():
         decisions=[ApprovalRecord(by_role="pm", type="reject")],
     )
     assert request.decisions[-1].type == "reject"
+
+
+# ---------------------------------------------------------------------------
+# Finding 1：max_iterations 编译期校验（总节点执行上限必须 >= 节点总数）
+# ---------------------------------------------------------------------------
+
+
+def test_compile_rejects_max_iterations_below_node_count():
+    yaml_text = SIMPLE_YAML.replace("max_iterations: 10", "max_iterations: 3")
+    with pytest.raises(WorkflowValidationError, match="max_iterations=3 小于节点总数 4"):
+        WorkflowEngine().compile(yaml_text)
+
+
+async def test_run_passes_with_max_iterations_equal_to_node_count():
+    yaml_text = SIMPLE_YAML.replace("max_iterations: 10", "max_iterations: 4")
+    compiled = WorkflowEngine().compile(yaml_text)
+    events = [event async for event in compiled.run()]
+    assert events[-1].type == "workflow_end"
+
+
+# ---------------------------------------------------------------------------
+# Finding 2：checkpointer/config 透传、interrupt 挂起 + resume 恢复契约
+# ---------------------------------------------------------------------------
+
+
+async def _interrupting_gate_handler(
+    state: ClusterState, node: WorkflowNode, ctx: NodeContext
+) -> dict:
+    """gate handler：interrupt() 挂起等待审批，恢复时按响应写 gate_payloads。"""
+    decision = interrupt(ActionRequest(id="ar1", kind=node.gate, title="迭代验收审批"))
+    decision_type = decision.type if isinstance(decision, HumanResponse) else "accept"
+    request = ActionRequest(
+        id="ar1",
+        kind=node.gate,
+        title="迭代验收审批",
+        decisions=[ApprovalRecord(by_role="pm", type=decision_type)],
+    )
+    return {"gate_payloads": {node.gate: request}}
+
+
+async def test_interrupt_suspends_then_resume_completes():
+    checkpointer = MemorySaver()
+    compiled = WorkflowEngine(handlers={"gate": _interrupting_gate_handler}).compile(GATE_YAML)
+
+    run_events = [event async for event in compiled.run(checkpointer=checkpointer)]
+    # 挂起：正常结束迭代，产出 workflow_suspended，不抛异常
+    assert run_events[-1].type == "workflow_suspended"
+    assert run_events[-1].payload == {"node_id": "quality_gate", "thread_id": "proj:demo:iter:1"}
+    # gate 节点已发出 node_start 但尚未发出 node_end
+    assert [event.actor for event in run_events if event.type == "node_start"] == [
+        "start",
+        "dev",
+        "quality_gate",
+    ]
+
+    resumed = [
+        event
+        async for event in compiled.resume(
+            "proj:demo:iter:1", HumanResponse(type="accept"), checkpointer=checkpointer
+        )
+    ]
+    assert resumed[0].type == "workflow_start"
+    assert resumed[0].payload.get("resume") is True
+    assert resumed[-1].type == "workflow_end"
+    # 挂起节点恢复后重新执行，accept 路由到 end
+    assert [event.actor for event in resumed if event.type == "node_start"] == ["quality_gate", "end"]
+
+
+async def test_resume_requires_checkpointer():
+    compiled = WorkflowEngine().compile(GATE_YAML)
+    with pytest.raises(ValueError, match="checkpointer"):
+        _ = [event async for event in compiled.resume("proj:demo:iter:1", "accept")]
+
+
+def test_get_compiled_graph_exposed():
+    compiled = WorkflowEngine().compile(SIMPLE_YAML)
+    graph = compiled.get_compiled_graph()
+    assert graph is not None
+    assert hasattr(graph, "astream")
+    assert hasattr(graph, "get_graph")
```
