# Task 3 Review Package

Base: 72456c1
Head: 4179512

## Diff stat

```
 src/agent_cluster/__init__.py |  20 ++
 src/agent_cluster/models.py   |  17 +-
 src/agent_cluster/workflow.py | 461 ++++++++++++++++++++++++++++++++++++++
 tests/test_workflow.py        | 509 ++++++++++++++++++++++++++++++++++++++++++
 4 files changed, 1001 insertions(+), 6 deletions(-)
```

## Full diff

```diff
diff --git a/src/agent_cluster/__init__.py b/src/agent_cluster/__init__.py
index 6220ad9..1293317 100644
--- a/src/agent_cluster/__init__.py
+++ b/src/agent_cluster/__init__.py
@@ -39,6 +39,17 @@ from agent_cluster.models import (
     TaskStatus,
     Vote,
 )
+from agent_cluster.workflow import (
+    CompiledWorkflow,
+    NodeContext,
+    NodeHandler,
+    WorkflowEdge,
+    WorkflowEngine,
+    WorkflowLoopError,
+    WorkflowNode,
+    WorkflowSpec,
+    WorkflowValidationError,
+)
 from agent_cluster.skills import (
     DisclosureLevel,
     SkillCatalog,
@@ -91,6 +102,15 @@ __all__ = [
     "Task",
     "TaskStatus",
     "Vote",
+    "CompiledWorkflow",
+    "NodeContext",
+    "NodeHandler",
+    "WorkflowEdge",
+    "WorkflowEngine",
+    "WorkflowLoopError",
+    "WorkflowNode",
+    "WorkflowSpec",
+    "WorkflowValidationError",
     "__version__",
     "format_skill_context",
 ]
diff --git a/src/agent_cluster/models.py b/src/agent_cluster/models.py
index 7901b41..8c23f6f 100644
--- a/src/agent_cluster/models.py
+++ b/src/agent_cluster/models.py
@@ -7,9 +7,11 @@
 
 from __future__ import annotations
 
+import operator
+
 from datetime import date, datetime
 from enum import StrEnum
-from typing import Any, Literal
+from typing import Annotated, Any, Literal
 
 from pydantic import BaseModel, ConfigDict, Field
 
@@ -425,6 +427,9 @@ class ActionRequest(BaseModel):
     evidence: dict = Field(default_factory=dict, description="证据 / 上下文")
     risk_level: Literal["low", "medium", "high", "critical"] = Field(default="medium", description="风险级别")
     bypass_immune: bool = Field(default=False, description="无人值守时是否禁止自动放行")
+    decisions: list[ApprovalRecord] = Field(
+        default_factory=list, description="审批记录，最后一条为当前结论（Task 3 门路由契约）"
+    )
 
 
 class ApprovalRecord(BaseModel):
@@ -527,11 +532,11 @@ class ClusterState(BaseModel):
     model_config = ConfigDict(extra="ignore")
 
     project: Project | None = Field(default=None, description="当前项目")
-    iterations: list[Iteration] = Field(default_factory=list, description="迭代列表")
-    tasks: list[Task] = Field(default_factory=list, description="任务列表")
-    meetings: list[Meeting] = Field(default_factory=list, description="会议记录列表")
+    iterations: Annotated[list[Iteration], operator.add] = Field(default_factory=list, description="迭代列表")
+    tasks: Annotated[list[Task], operator.add] = Field(default_factory=list, description="任务列表")
+    meetings: Annotated[list[Meeting], operator.add] = Field(default_factory=list, description="会议记录列表")
     ledger: Ledger | None = Field(default=None, description="当前任务账本")
     gate_payloads: dict[GateKind, ActionRequest] = Field(default_factory=dict, description="待审批请求，按门类别索引")
-    decisions: list[ApprovalRecord] = Field(default_factory=list, description="审批记录")
+    decisions: Annotated[list[ApprovalRecord], operator.add] = Field(default_factory=list, description="审批记录")
     skill_catalog: dict[str, Skill] = Field(default_factory=dict, description="技能目录：name@version -> Skill")
-    messages: list[Message] = Field(default_factory=list, description="消息流")
+    messages: Annotated[list[Message], operator.add] = Field(default_factory=list, description="消息流")
diff --git a/src/agent_cluster/workflow.py b/src/agent_cluster/workflow.py
new file mode 100644
index 0000000..e7c0039
--- /dev/null
+++ b/src/agent_cluster/workflow.py
@@ -0,0 +1,461 @@
+"""流程引擎（设计文档 §5.1/§5.8）：YAML 流程 DSL → LangGraph StateGraph 编译与事件流运行。
+
+职责：
+- 把 ChatDev 风格的 YAML 流程 DSL 解析为 ``WorkflowSpec``（pydantic 模型），
+  校验节点/边/字段级错误后编译为 ``StateGraph(ClusterState)``。
+- 节点类型：``start``/``end``/``agent``/``meeting``/``gate``/``parallel``。
+- 事件流：每次运行产出 ``workflow_start``/``node_start``/``node_end``/``workflow_end``
+  事件；handler 可通过 ``ctx.events`` 追加自定义事件。
+- 防死循环：统计每次运行累计执行的节点数，超过 ``max_iterations`` 抛
+  ``WorkflowLoopError``；LangGraph ``recursion_limit = max_iterations * 4`` 兜底。
+
+handler 契约（Task 4/5 据此注册）：
+- ``WorkflowEngine(handlers={"agent": ..., "meeting": ..., "gate": ...})`` 按
+  **节点类型** 注册异步 handler；``start``/``end``/``parallel`` 为内置节点，
+  不查询 handlers；未注册类型的节点使用默认占位 handler（不改状态、不发额外事件），
+  保证编译与运行不中断。
+- handler 签名：``async def handler(state: ClusterState, node: WorkflowNode,
+  ctx: NodeContext) -> dict[str, Any]``，返回 **LangGraph channel 更新字典**
+  （如 ``{"tasks": [Task(...)]}``、``{"gate_payloads": {GateKind: ActionRequest(...)}}``）。
+  list 字段（iterations/tasks/meetings/decisions/messages）带 ``operator.add`` reducer，
+  handler 只追加、不整体替换。这是对任务简报中 ``Awaitable[ClusterState]`` 的偏离：
+  dict 更新与 reducer 语义天然一致，且与简报自述的 ``handler writes {...}`` 一致。
+- gate 门路由载荷（Task 4 gates.py 的契约）：
+  gate 节点执行后，``"gate"`` handler 必须返回
+  ``{"gate_payloads": {node.gate: ActionRequest(...)}}``，其中
+  ``ActionRequest.decisions[-1]``（``ApprovalRecord.type``）为本次审批结论：
+  ``accept``→``on_accept``（缺省 ``to``）；``reject``→``on_reject``（缺省 ``to``）；
+  ``edit``→``on_edit``（缺省 ``to``）；``response``→``on_response``（缺省
+  ``on_accept``→``to``）；``ignore`` 或未写入载荷→``on_accept``（缺省 ``to``）。
+- parallel 并行：编译期用 LangGraph ``Send`` API fan-out 到子节点、子节点各自
+  ``add_edge(child, fan_in_target)`` 汇聚；所有子节点仍注册为图节点并产出事件。
+"""
+
+from __future__ import annotations
+
+import uuid
+from collections.abc import AsyncIterator, Awaitable, Callable
+from typing import Any, Literal
+
+import yaml
+from langgraph.errors import GraphRecursionError
+from langgraph.graph import END, START, StateGraph
+from langgraph.types import Send
+from pydantic import BaseModel, ConfigDict, Field, ValidationError
+
+from agent_cluster.models import (
+    ClusterState,
+    Event,
+    GateKind,
+    Iteration,
+    MeetingKind,
+    Project,
+)
+
+__all__ = [
+    "WorkflowValidationError",
+    "WorkflowLoopError",
+    "WorkflowNode",
+    "WorkflowEdge",
+    "WorkflowSpec",
+    "NodeContext",
+    "NodeHandler",
+    "CompiledWorkflow",
+    "WorkflowEngine",
+]
+
+
+class WorkflowValidationError(Exception):
+    """流程 YAML 编译校验错误（消息包含节点/边/字段级细节）。"""
+
+
+class WorkflowLoopError(Exception):
+    """流程执行超过 max_iterations（防死循环）。"""
+
+
+class WorkflowNode(BaseModel):
+    """流程节点（对齐 YAML DSL 字段）。"""
+
+    model_config = ConfigDict(extra="ignore")
+
+    id: str = Field(description="节点唯一标识")
+    type: Literal["start", "end", "agent", "meeting", "gate", "parallel"] = Field(description="节点类型")
+    meeting: MeetingKind | None = Field(default=None, description="meeting 节点会议类型")
+    role: str | None = Field(default=None, description="agent 节点岗位 id")
+    gate: GateKind | None = Field(default=None, description="gate 节点审批门类别")
+    children: list[str] | None = Field(default=None, description="parallel 节点子节点 id 列表")
+
+
+class WorkflowEdge(BaseModel):
+    """流程边（``from`` 为 Python 关键字，用别名映射）。"""
+
+    model_config = ConfigDict(populate_by_name=True, extra="ignore")
+
+    from_: str = Field(alias="from", description="起点节点 id")
+    to: str = Field(description="终点节点 id（gate/parallel 的缺省目标）")
+    on_accept: str | None = Field(default=None, description="gate 审批 accept 目标")
+    on_reject: str | None = Field(default=None, description="gate 审批 reject 目标")
+    on_edit: str | None = Field(default=None, description="gate 审批 edit 目标")
+    on_response: str | None = Field(default=None, description="gate 审批 response 目标")
+
+
+class WorkflowSpec(BaseModel):
+    """流程规格（YAML 顶层）。"""
+
+    model_config = ConfigDict(extra="ignore")
+
+    name: str = Field(description="流程名称")
+    description: str = Field(default="", description="流程描述")
+    max_iterations: int = Field(default=10, gt=0, description="防死循环：单次运行最大节点执行次数")
+    thread_id: str = Field(default="", description="线程 id（缺省运行时使用）")
+    nodes: list[WorkflowNode] = Field(description="节点列表")
+    edges: list[WorkflowEdge] = Field(description="边列表")
+
+
+class NodeContext(BaseModel):
+    """传给节点 handler 的运行上下文。"""
+
+    model_config = ConfigDict(extra="ignore")
+
+    node_id: str = Field(description="当前节点 id")
+    spec: WorkflowSpec = Field(description="流程规格")
+    events: list[Event] = Field(description="事件流缓冲，handler 可 append 追加事件")
+    run_id: str = Field(description="本次运行 id")
+    loop_count: int = Field(description="当前主循环轮次（start 节点已执行次数）")
+
+
+NodeHandler = Callable[[ClusterState, WorkflowNode, NodeContext], Awaitable[dict[str, Any]]]
+
+
+def _validate_spec(spec: WorkflowSpec) -> None:
+    """编译前校验：重复 id、悬空引用、start/end 唯一性与出边、gate 出边、parallel children。"""
+    nodes_by_id: dict[str, WorkflowNode] = {}
+    for node in spec.nodes:
+        if node.id in nodes_by_id:
+            raise WorkflowValidationError(f"重复的节点 id：{node.id!r}")
+        nodes_by_id[node.id] = node
+
+    start_nodes = [node for node in spec.nodes if node.type == "start"]
+    end_nodes = [node for node in spec.nodes if node.type == "end"]
+    if not start_nodes:
+        raise WorkflowValidationError("流程缺少 start 节点")
+    if len(start_nodes) > 1:
+        raise WorkflowValidationError(f"流程存在多个 start 节点：{[node.id for node in start_nodes]}")
+    if not end_nodes:
+        raise WorkflowValidationError("流程缺少 end 节点")
+    if len(end_nodes) > 1:
+        raise WorkflowValidationError(f"流程存在多个 end 节点：{[node.id for node in end_nodes]}")
+    start_node = start_nodes[0]
+    end_node = end_nodes[0]
+
+    for edge in spec.edges:
+        if edge.from_ not in nodes_by_id:
+            raise WorkflowValidationError(f"边起点引用不存在的节点：{edge.from_!r}")
+        if edge.to not in nodes_by_id:
+            raise WorkflowValidationError(f"边终点引用不存在的节点：{edge.to!r}")
+        for field_name in ("on_accept", "on_reject", "on_edit", "on_response"):
+            target = getattr(edge, field_name)
+            if target is not None and target not in nodes_by_id:
+                raise WorkflowValidationError(
+                    f"边 {edge.from_!r}→{edge.to!r} 的 {field_name} 引用不存在的节点：{target!r}"
+                )
+
+    if not any(edge.from_ == start_node.id for edge in spec.edges):
+        raise WorkflowValidationError(f"start 节点 {start_node.id!r} 至少需要一条出边")
+    if any(edge.from_ == end_node.id for edge in spec.edges):
+        raise WorkflowValidationError(f"end 节点 {end_node.id!r} 不允许有出边")
+
+    for node in spec.nodes:
+        if node.type == "gate" and not any(edge.from_ == node.id for edge in spec.edges):
+            raise WorkflowValidationError(f"gate 节点 {node.id!r} 至少需要一条出边")
+        if node.type == "parallel":
+            if not node.children:
+                raise WorkflowValidationError(f"parallel 节点 {node.id!r} 必须声明 children 子节点列表")
+            for child_id in node.children:
+                if child_id not in nodes_by_id:
+                    raise WorkflowValidationError(f"parallel 节点 {node.id!r} 的子节点 {child_id!r} 不存在")
+            if not any(edge.from_ == node.id for edge in spec.edges):
+                raise WorkflowValidationError(f"parallel 节点 {node.id!r} 至少需要一条出边（fan-in 目标）")
+
+
+class CompiledWorkflow:
+    """已编译的 LangGraph 流程：运行产出并累计事件流。"""
+
+    def __init__(self, spec: WorkflowSpec, handlers: dict[str, NodeHandler]) -> None:
+        self._spec = spec
+        self._handlers = dict(handlers)
+        self._events: list[Event] = []
+        self._run_id = ""
+        self._thread_id = ""
+        self._loop_count = 0
+        self._event_seq = 0
+        self._drained = 0
+        self._start_id = next(node.id for node in spec.nodes if node.type == "start")
+        self._end_id = next(node.id for node in spec.nodes if node.type == "end")
+        self._graph = self._build_graph()
+
+    @property
+    def events(self) -> list[Event]:
+        """返回累计事件流（跨多次 run 累积，按 run_id 区分）。"""
+        return list(self._events)
+
+    def get_graph(self) -> dict:
+        """返回图描述（节点/边列表），供测试与断言使用。"""
+        nodes = [node.model_dump(exclude_none=True, mode="json") for node in self._spec.nodes]
+        edges = [edge.model_dump(exclude_none=True, by_alias=True, mode="json") for edge in self._spec.edges]
+        return {"nodes": nodes, "edges": edges}
+
+    # ------------------------------------------------------------------
+    # 图构建
+    # ------------------------------------------------------------------
+
+    def _build_graph(self) -> Any:
+        graph = StateGraph(ClusterState)
+        nodes_by_id = {node.id: node for node in self._spec.nodes}
+        for node in self._spec.nodes:
+            if node.type == "end":
+                graph.add_node(node.id, self._make_end_wrapper())
+            else:
+                graph.add_node(node.id, self._make_node_wrapper(node))
+        graph.add_edge(START, self._start_id)
+
+        start_edge = next(edge for edge in self._spec.edges if edge.from_ == self._start_id)
+        graph.add_edge(self._start_id, start_edge.to)
+        graph.add_edge(self._end_id, END)
+
+        wired_gates: set[str] = set()
+        wired_parallels: set[str] = set()
+        for edge in self._spec.edges:
+            if edge.from_ in (self._start_id, self._end_id):
+                continue
+            source = nodes_by_id[edge.from_]
+            if source.type == "gate":
+                if edge.from_ not in wired_gates:
+                    self._wire_gate_edges(graph, source)
+                    wired_gates.add(edge.from_)
+            elif source.type == "parallel":
+                if edge.from_ not in wired_parallels:
+                    self._wire_parallel_edges(graph, source)
+                    wired_parallels.add(edge.from_)
+            else:
+                graph.add_edge(edge.from_, edge.to)
+        return graph.compile()
+
+    def _wire_gate_edges(self, graph, node: WorkflowNode) -> None:
+        """把 gate 节点的出边编译为条件路由（基于最后一次审批结论）。"""
+        gate_edges = [edge for edge in self._spec.edges if edge.from_ == node.id]
+        fallback_to = gate_edges[0].to
+        targets: dict[str, str] = {
+            "accept": next((edge.on_accept for edge in gate_edges if edge.on_accept), fallback_to),
+            "reject": next((edge.on_reject for edge in gate_edges if edge.on_reject), fallback_to),
+            "edit": next((edge.on_edit for edge in gate_edges if edge.on_edit), fallback_to),
+            "response": next((edge.on_response for edge in gate_edges if edge.on_response), None)
+            or next((edge.on_accept for edge in gate_edges if edge.on_accept), fallback_to),
+            "ignore": next((edge.on_accept for edge in gate_edges if edge.on_accept), fallback_to),
+        }
+        path_map = {target: target for target in targets.values()}
+        graph.add_conditional_edges(node.id, self._make_gate_router(node, targets), path_map)
+
+    def _wire_parallel_edges(self, graph, node: WorkflowNode) -> None:
+        """把 parallel 节点编译为 Send fan-out + 子节点汇聚到 fan-in 目标。"""
+        children = list(node.children or [])
+        fan_in_target = next(edge.to for edge in self._spec.edges if edge.from_ == node.id)
+
+        def fan_out(_state: ClusterState) -> list[Send]:
+            return [Send(child_id, {}) for child_id in children]
+
+        graph.add_conditional_edges(node.id, fan_out, list(children))
+        for child_id in children:
+            graph.add_edge(child_id, fan_in_target)
+
+    def _make_gate_router(self, node: WorkflowNode, targets: dict[str, str]) -> Callable[[ClusterState], str]:
+        def route(state: ClusterState) -> str:
+            return targets.get(self._last_gate_decision_type(state, node), targets["accept"])
+
+        return route
+
+    @staticmethod
+    def _last_gate_decision_type(state: ClusterState, node: WorkflowNode) -> str:
+        """读取 gate 载荷的最后一条审批结论；缺失时按 accept 处理。"""
+        if node.gate is None:
+            return "accept"
+        payload = state.gate_payloads.get(node.gate)
+        if payload is None or not payload.decisions:
+            return "accept"
+        return payload.decisions[-1].type
+
+    # ------------------------------------------------------------------
+    # 节点包装器
+    # ------------------------------------------------------------------
+
+    def _make_node_wrapper(self, node: WorkflowNode) -> Callable[[ClusterState], Awaitable[dict[str, Any] | None]]:
+        async def wrapper(state: ClusterState) -> dict[str, Any] | None:
+            return await self._execute_node(state, node)
+
+        return wrapper
+
+    def _make_end_wrapper(self) -> Callable[[ClusterState], Awaitable[None]]:
+        async def wrapper(state: ClusterState) -> None:
+            self._emit("node_start", actor=self._end_id, payload={"node_type": "end", "node_id": self._end_id})
+            self._emit("node_end", actor=self._end_id, payload={"node_type": "end", "node_id": self._end_id})
+            return None
+
+        return wrapper
+
+    async def _execute_node(self, state: ClusterState, node: WorkflowNode) -> dict[str, Any] | None:
+        if node.type == "start":
+            self._loop_count += 1
+        # model_construct 跳过校验，保证 ctx.events 与内部事件缓冲为同一列表引用
+        ctx = NodeContext.model_construct(
+            node_id=node.id,
+            spec=self._spec,
+            events=self._events,
+            run_id=self._run_id,
+            loop_count=self._loop_count,
+        )
+        start_payload: dict[str, Any] = {"node_type": node.type, "node_id": node.id}
+        if node.type == "start":
+            start_payload["loop_count"] = self._loop_count
+        self._emit("node_start", actor=node.id, payload=start_payload)
+
+        if node.type == "start":
+            updates: dict[str, Any] | None = self._execute_start(state)
+        elif node.type == "parallel":
+            updates = {}
+        else:
+            handler = self._handlers.get(node.type)
+            if handler is None:
+                updates = await self._default_handler(state, node, ctx)
+            else:
+                updates = await handler(state, node, ctx)
+
+        self._emit("node_end", actor=node.id, payload={"node_type": node.type, "node_id": node.id})
+        if updates is None:
+            return None
+        if not isinstance(updates, dict):
+            raise TypeError(
+                f"节点 {node.id!r} 的 handler 必须返回 dict 形式的 channel 更新，实际返回 {type(updates).__name__}"
+            )
+        return updates
+
+    def _execute_start(self, state: ClusterState) -> dict[str, Any]:
+        """start 节点：补齐 Project/Iteration 默认值（初始状态已携带时保持原样）。"""
+        updates: dict[str, Any] = {}
+        project = state.project
+        if project is None:
+            project = Project(id=self._default_project_id(), name=self._spec.name or self._default_project_id())
+            updates["project"] = project
+        if not state.iterations:
+            updates["iterations"] = [Iteration(id=f"{project.id}:iter:1", project_id=project.id, number=1)]
+        return updates
+
+    def _default_project_id(self) -> str:
+        """从 thread_id（proj:<id>:iter:<n>）推导项目 id；否则回退流程名。"""
+        thread_id = self._spec.thread_id or ""
+        if thread_id.startswith("proj:"):
+            parts = thread_id.split(":")
+            if len(parts) >= 2 and parts[1]:
+                return parts[1]
+        return self._spec.name or "default-project"
+
+    async def _default_handler(self, state: ClusterState, node: WorkflowNode, ctx: NodeContext) -> dict[str, Any]:
+        """未注册 handler 的占位实现：不改状态、不发额外事件，保证运行不中断。"""
+        return {}
+
+    # ------------------------------------------------------------------
+    # 事件与运行
+    # ------------------------------------------------------------------
+
+    def _emit(self, event_type: str, *, actor: str, payload: dict[str, Any]) -> Event:
+        self._event_seq += 1
+        event = Event(
+            id=f"{self._run_id}:{self._event_seq:04d}",
+            run_id=self._run_id,
+            thread_id=self._thread_id,
+            type=event_type,
+            actor=actor,
+            payload=payload,
+        )
+        self._events.append(event)
+        return event
+
+    async def run(self, initial: dict | None = None, *, thread_id: str | None = None) -> AsyncIterator[Event]:
+        """运行流程：产出事件流并累计到 ``events``。
+
+        - ``initial``：初始 ClusterState 的字段字典（可含 project/iterations 等）。
+        - ``thread_id``：覆盖 spec.thread_id；缺省用 spec.thread_id 或 "default"。
+        - 防死循环：累计执行节点数超过 max_iterations 抛 WorkflowLoopError；
+          LangGraph recursion_limit（max_iterations*4）触发时同样转 WorkflowLoopError。
+        """
+        resolved_thread_id = thread_id or self._spec.thread_id or "default"
+        self._run_id = uuid.uuid4().hex[:12]
+        self._thread_id = resolved_thread_id
+        self._loop_count = 0
+        self._event_seq = 0
+        self._drained = 0
+        initial_state = ClusterState() if initial is None else ClusterState.model_validate(initial)
+
+        yield self._emit(
+            "workflow_start",
+            actor="",
+            payload={"name": self._spec.name, "thread_id": resolved_thread_id},
+        )
+        self._drained = len(self._events)
+
+        executed = 0
+        try:
+            async for step in self._graph.astream(
+                initial_state,
+                config={
+                    "recursion_limit": self._spec.max_iterations * 4,
+                    "configurable": {"thread_id": resolved_thread_id},
+                },
+            ):
+                for node_name in step:
+                    executed += 1
+                    if executed > self._spec.max_iterations:
+                        raise WorkflowLoopError(
+                            f"流程 {self._spec.name!r} 超过最大迭代次数 max_iterations="
+                            f"{self._spec.max_iterations}（已执行节点数 {executed}）"
+                        )
+                pending = list(self._events[self._drained :])
+                self._drained = len(self._events)
+                for event in pending:
+                    yield event
+        except GraphRecursionError as exc:
+            raise WorkflowLoopError(
+                f"流程 {self._spec.name!r} 超过 LangGraph recursion_limit"
+                f"（max_iterations*4={self._spec.max_iterations * 4}），疑似死循环"
+            ) from exc
+
+        yield self._emit(
+            "workflow_end",
+            actor="",
+            payload={"name": self._spec.name, "thread_id": resolved_thread_id},
+        )
+
+
+class WorkflowEngine:
+    """流程引擎：YAML 流程 DSL → 校验 → CompiledWorkflow。
+
+    ``handlers`` 按节点类型注册（"agent"/"meeting"/"gate"）；"start"/"end"/"parallel"
+    为内置节点，不查询 handlers；未注册类型的节点走默认占位 handler。
+    """
+
+    def __init__(self, handlers: dict[str, NodeHandler] | None = None) -> None:
+        self._handlers: dict[str, NodeHandler] = dict(handlers or {})
+
+    def compile(self, yaml_text: str) -> CompiledWorkflow:
+        """解析 YAML → 校验 → 构建 LangGraph StateGraph，返回 CompiledWorkflow。"""
+        try:
+            data = yaml.safe_load(yaml_text)
+        except yaml.YAMLError as exc:
+            raise WorkflowValidationError(f"YAML 解析失败：{exc}") from exc
+        if not isinstance(data, dict):
+            raise WorkflowValidationError("流程 YAML 顶层必须是映射（含 name/nodes/edges 等字段）")
+        try:
+            spec = WorkflowSpec.model_validate(data)
+        except ValidationError as exc:
+            raise WorkflowValidationError(f"流程规格非法：{exc}") from exc
+        _validate_spec(spec)
+        return CompiledWorkflow(spec=spec, handlers=self._handlers)
diff --git a/tests/test_workflow.py b/tests/test_workflow.py
new file mode 100644
index 0000000..323d7f3
--- /dev/null
+++ b/tests/test_workflow.py
@@ -0,0 +1,509 @@
+"""Task 3 行为测试：YAML→StateGraph 编译、校验、事件流运行、gate 条件路由、parallel 并行、loop 防死循环。
+
+不依赖 gates.py/roles.py/meetings.py：gate/agent handler 一律用测试内注入的 fake handler。
+"""
+
+from __future__ import annotations
+
+import operator
+import typing
+
+import pytest
+
+from agent_cluster.models import (
+    ActionRequest,
+    ApprovalRecord,
+    ClusterState,
+    Event,
+    GateKind,
+)
+from agent_cluster.workflow import (
+    CompiledWorkflow,
+    NodeContext,
+    WorkflowEngine,
+    WorkflowLoopError,
+    WorkflowNode,
+    WorkflowValidationError,
+)
+
+GATE_AND_PARALLEL_YAML = """
+name: demo-flow
+description: 含 gate 条件路由与 parallel 的演示流程
+max_iterations: 30
+thread_id: "proj:demo:iter:1"
+nodes:
+  - {id: start, type: start}
+  - {id: requirement_review, type: meeting, meeting: requirement_review}
+  - {id: requirement_gate, type: gate, gate: requirement_confirmation}
+  - {id: design, type: agent, role: architect}
+  - {id: dev_fanout, type: parallel, children: [frontend_dev, backend_dev]}
+  - {id: frontend_dev, type: agent, role: frontend}
+  - {id: backend_dev, type: agent, role: backend}
+  - {id: code_review, type: meeting, meeting: code_review}
+  - {id: release_gate, type: gate, gate: release}
+  - {id: end, type: end}
+edges:
+  - {from: start, to: requirement_review}
+  - {from: requirement_review, to: requirement_gate}
+  - {from: requirement_gate, to: design, on_accept: design, on_reject: requirement_review, on_edit: requirement_review}
+  - {from: design, to: dev_fanout}
+  - {from: dev_fanout, to: code_review}
+  - {from: code_review, to: release_gate}
+  - {from: release_gate, to: end, on_accept: end, on_reject: code_review}
+"""
+
+SIMPLE_YAML = """
+name: simple
+max_iterations: 10
+thread_id: "proj:demo:iter:1"
+nodes:
+  - {id: start, type: start}
+  - {id: code, type: agent, role: backend}
+  - {id: review, type: meeting, meeting: code_review}
+  - {id: end, type: end}
+edges:
+  - {from: start, to: code}
+  - {from: code, to: review}
+  - {from: review, to: end}
+"""
+
+GATE_YAML = """
+name: gate-flow
+max_iterations: 20
+thread_id: "proj:demo:iter:1"
+nodes:
+  - {id: start, type: start}
+  - {id: dev, type: agent, role: backend}
+  - {id: quality_gate, type: gate, gate: iteration_acceptance}
+  - {id: rework, type: agent, role: backend}
+  - {id: end, type: end}
+edges:
+  - {from: start, to: dev}
+  - {from: dev, to: quality_gate}
+  - {from: quality_gate, to: end, on_accept: end, on_reject: rework, on_edit: rework, on_response: end}
+  - {from: rework, to: quality_gate}
+"""
+
+PARALLEL_YAML = """
+name: parallel-flow
+max_iterations: 20
+thread_id: "proj:demo:iter:1"
+nodes:
+  - {id: start, type: start}
+  - {id: fanout, type: parallel, children: [fe, be]}
+  - {id: fe, type: agent, role: frontend}
+  - {id: be, type: agent, role: backend}
+  - {id: end, type: end}
+edges:
+  - {from: start, to: fanout}
+  - {from: fanout, to: end}
+"""
+
+LOOP_YAML = """
+name: loop-flow
+max_iterations: 4
+thread_id: "proj:demo:iter:1"
+nodes:
+  - {id: start, type: start}
+  - {id: dev, type: agent, role: backend}
+  - {id: quality_gate, type: gate, gate: iteration_acceptance}
+  - {id: rework, type: agent, role: backend}
+  - {id: end, type: end}
+edges:
+  - {from: start, to: dev}
+  - {from: dev, to: quality_gate}
+  - {from: quality_gate, to: end, on_accept: end, on_reject: rework}
+  - {from: rework, to: quality_gate}
+"""
+
+
+# ---------------------------------------------------------------------------
+# 编译与图描述
+# ---------------------------------------------------------------------------
+
+
+def test_compile_valid_yaml_with_gate_and_parallel():
+    compiled = WorkflowEngine().compile(GATE_AND_PARALLEL_YAML)
+    assert isinstance(compiled, CompiledWorkflow)
+    graph = compiled.get_graph()
+    assert set(graph) == {"nodes", "edges"}
+    node_ids = {node["id"] for node in graph["nodes"]}
+    assert node_ids == {
+        "start",
+        "requirement_review",
+        "requirement_gate",
+        "design",
+        "dev_fanout",
+        "frontend_dev",
+        "backend_dev",
+        "code_review",
+        "release_gate",
+        "end",
+    }
+    by_id = {node["id"]: node for node in graph["nodes"]}
+    assert by_id["start"]["type"] == "start"
+    assert by_id["requirement_gate"]["type"] == "gate"
+    assert by_id["requirement_gate"]["gate"] == "requirement_confirmation"
+    assert by_id["dev_fanout"]["type"] == "parallel"
+    assert by_id["dev_fanout"]["children"] == ["frontend_dev", "backend_dev"]
+    gate_edges = [edge for edge in graph["edges"] if edge["from"] == "requirement_gate"]
+    assert gate_edges == [
+        {
+            "from": "requirement_gate",
+            "to": "design",
+            "on_accept": "design",
+            "on_reject": "requirement_review",
+            "on_edit": "requirement_review",
+        }
+    ]
+
+
+# ---------------------------------------------------------------------------
+# 非法 YAML 逐一抛 WorkflowValidationError
+# ---------------------------------------------------------------------------
+
+INVALID_CASES = [
+    (
+        "duplicate-id",
+        """
+name: invalid
+max_iterations: 10
+nodes:
+  - {id: start, type: start}
+  - {id: dup, type: agent}
+  - {id: dup, type: agent}
+  - {id: end, type: end}
+edges:
+  - {from: start, to: dup}
+  - {from: dup, to: end}
+""",
+        "重复的节点 id",
+    ),
+    (
+        "missing-edge-target",
+        """
+name: invalid
+max_iterations: 10
+nodes:
+  - {id: start, type: start}
+  - {id: a, type: agent}
+  - {id: end, type: end}
+edges:
+  - {from: start, to: ghost}
+  - {from: a, to: end}
+""",
+        "边终点引用不存在的节点",
+    ),
+    (
+        "missing-start",
+        """
+name: invalid
+max_iterations: 10
+nodes:
+  - {id: a, type: agent}
+  - {id: end, type: end}
+edges:
+  - {from: a, to: end}
+""",
+        "缺少 start 节点",
+    ),
+    (
+        "two-starts",
+        """
+name: invalid
+max_iterations: 10
+nodes:
+  - {id: start, type: start}
+  - {id: start2, type: start}
+  - {id: end, type: end}
+edges:
+  - {from: start, to: end}
+""",
+        "多个 start 节点",
+    ),
+    (
+        "gate-without-outgoing-edge",
+        """
+name: invalid
+max_iterations: 10
+nodes:
+  - {id: start, type: start}
+  - {id: g, type: gate, gate: release}
+  - {id: end, type: end}
+edges:
+  - {from: start, to: g}
+""",
+        "gate 节点 'g' 至少需要一条出边",
+    ),
+    (
+        "edge-without-to",
+        """
+name: invalid
+max_iterations: 10
+nodes:
+  - {id: start, type: start}
+  - {id: a, type: agent}
+  - {id: end, type: end}
+edges:
+  - {from: start}
+  - {from: a, to: end}
+""",
+        "流程规格非法",
+    ),
+    (
+        "edge-from-missing-node",
+        """
+name: invalid
+max_iterations: 10
+nodes:
+  - {id: start, type: start}
+  - {id: end, type: end}
+edges:
+  - {from: ghost, to: end}
+  - {from: start, to: end}
+""",
+        "边起点引用不存在的节点",
+    ),
+    (
+        "parallel-without-children",
+        """
+name: invalid
+max_iterations: 10
+nodes:
+  - {id: start, type: start}
+  - {id: p, type: parallel}
+  - {id: end, type: end}
+edges:
+  - {from: start, to: p}
+  - {from: p, to: end}
+""",
+        "必须声明 children",
+    ),
+    (
+        "end-with-outgoing-edge",
+        """
+name: invalid
+max_iterations: 10
+nodes:
+  - {id: start, type: start}
+  - {id: end, type: end}
+  - {id: a, type: agent}
+edges:
+  - {from: start, to: end}
+  - {from: end, to: a}
+""",
+        "end 节点 'end' 不允许有出边",
+    ),
+]
+
+
+@pytest.mark.parametrize(
+    ("_case_name", "yaml_text", "message_part"),
+    INVALID_CASES,
+    ids=[case[0] for case in INVALID_CASES],
+)
+def test_invalid_yaml_raises_validation_error(_case_name, yaml_text, message_part):
+    with pytest.raises(WorkflowValidationError, match=message_part):
+        WorkflowEngine().compile(yaml_text)
+
+
+def test_non_mapping_yaml_raises_validation_error():
+    with pytest.raises(WorkflowValidationError, match="顶层必须是映射"):
+        WorkflowEngine().compile("- just\n- a\n- list\n")
+
+
+# ---------------------------------------------------------------------------
+# 运行：事件序列
+# ---------------------------------------------------------------------------
+
+
+async def test_simple_flow_full_event_sequence():
+    compiled = WorkflowEngine().compile(SIMPLE_YAML)
+    events = [event async for event in compiled.run()]
+    assert [(event.type, event.actor) for event in events] == [
+        ("workflow_start", ""),
+        ("node_start", "start"),
+        ("node_end", "start"),
+        ("node_start", "code"),
+        ("node_end", "code"),
+        ("node_start", "review"),
+        ("node_end", "review"),
+        ("node_start", "end"),
+        ("node_end", "end"),
+        ("workflow_end", ""),
+    ]
+    # events 属性与产出的事件一致
+    assert compiled.events == events
+    assert all(event.thread_id == "proj:demo:iter:1" for event in events)
+
+
+async def test_sequential_chain_runs_all_nodes_in_order():
+    compiled = WorkflowEngine().compile(SIMPLE_YAML)
+    events = [event async for event in compiled.run()]
+    actors = [event.actor for event in events if event.type == "node_start"]
+    assert actors == ["start", "code", "review", "end"]
+
+
+async def test_start_node_defaults_project_and_iteration():
+    """无初始状态时，start 节点从 thread_id 推导 Project/Iteration 默认值。"""
+
+    async def report_handler(state: ClusterState, node: WorkflowNode, ctx: NodeContext) -> dict:
+        ctx.events.append(
+            Event(
+                id=f"{ctx.run_id}:report",
+                run_id=ctx.run_id,
+                thread_id=ctx.spec.thread_id,
+                type="state_report",
+                actor=node.id,
+                payload={
+                    "project_id": state.project.id if state.project else None,
+                    "project_name": state.project.name if state.project else None,
+                    "iteration_ids": [iteration.id for iteration in state.iterations],
+                    "loop_count": ctx.loop_count,
+                },
+            )
+        )
+        return {}
+
+    compiled = WorkflowEngine(handlers={"agent": report_handler}).compile(SIMPLE_YAML)
+    events = [event async for event in compiled.run()]
+    report = next(event for event in events if event.type == "state_report")
+    assert report.payload == {
+        "project_id": "demo",
+        "project_name": "simple",
+        "iteration_ids": ["demo:iter:1"],
+        "loop_count": 1,
+    }
+
+
+async def test_initial_state_is_preserved():
+    """初始状态已携带 project 时，start 节点保持原值不覆盖。"""
+
+    async def report_handler(state: ClusterState, node: WorkflowNode, ctx: NodeContext) -> dict:
+        ctx.events.append(
+            Event(
+                id=f"{ctx.run_id}:report",
+                run_id=ctx.run_id,
+                thread_id=ctx.spec.thread_id,
+                type="state_report",
+                actor=node.id,
+                payload={"project_id": state.project.id if state.project else None},
+            )
+        )
+        return {}
+
+    compiled = WorkflowEngine(handlers={"agent": report_handler}).compile(SIMPLE_YAML)
+    initial = {"project": {"id": "p9", "name": "既有项目"}}
+    events = [event async for event in compiled.run(initial)]
+    report = next(event for event in events if event.type == "state_report")
+    assert report.payload == {"project_id": "p9"}
+
+
+# ---------------------------------------------------------------------------
+# gate 条件路由
+# ---------------------------------------------------------------------------
+
+
+async def test_gate_conditional_routing_takes_rework_then_accept():
+    """第一次审批 reject 走返工边，第二次 accept 放行到 end。"""
+
+    calls = {"count": 0}
+
+    async def fake_gate_handler(state: ClusterState, node: WorkflowNode, ctx: NodeContext) -> dict:
+        calls["count"] += 1
+        decision = "reject" if calls["count"] == 1 else "accept"
+        request = ActionRequest(
+            id=f"ar-{ctx.run_id}-{calls['count']}",
+            kind=node.gate,
+            title="迭代验收审批",
+            decisions=[ApprovalRecord(by_role="pm", type=decision, args={"round": calls["count"]})],
+        )
+        return {
+            "gate_payloads": {node.gate: request},
+            "decisions": [ApprovalRecord(by_role="pm", type=decision)],
+        }
+
+    compiled = WorkflowEngine(handlers={"gate": fake_gate_handler}).compile(GATE_YAML)
+    events = [event async for event in compiled.run()]
+    node_starts = [event for event in events if event.type == "node_start"]
+    actors = [event.actor for event in node_starts]
+    # 第一次 quality_gate reject → rework；第二次 accept → end
+    assert actors == ["start", "dev", "quality_gate", "rework", "quality_gate", "end"]
+    assert calls["count"] == 2
+
+
+async def test_gate_accept_routes_straight_to_end():
+    """门 handler 未注入时（默认占位），gate 按缺省 accept 路由到 to。"""
+
+    compiled = WorkflowEngine().compile(GATE_YAML)
+    events = [event async for event in compiled.run()]
+    actors = [event.actor for event in events if event.type == "node_start"]
+    assert actors == ["start", "dev", "quality_gate", "end"]
+
+
+# ---------------------------------------------------------------------------
+# parallel 并行 fan-out / fan-in
+# ---------------------------------------------------------------------------
+
+
+async def test_parallel_fan_out_all_children_ran():
+    async def agent_handler(state: ClusterState, node: WorkflowNode, ctx: NodeContext) -> dict:
+        ctx.events.append(
+            Event(
+                id=f"{ctx.run_id}:{node.id}",
+                run_id=ctx.run_id,
+                thread_id=ctx.spec.thread_id,
+                type="agent_ran",
+                actor=node.id,
+                payload={"role": node.role},
+            )
+        )
+        return {}
+
+    compiled = WorkflowEngine(handlers={"agent": agent_handler}).compile(PARALLEL_YAML)
+    events = [event async for event in compiled.run()]
+    node_starts = [event for event in events if event.type == "node_start"]
+    assert [event.actor for event in node_starts] == ["start", "fanout", "fe", "be", "end"]
+    agent_ran = {event.actor: event.payload["role"] for event in events if event.type == "agent_ran"}
+    assert agent_ran == {"fe": "frontend", "be": "backend"}
+
+
+# ---------------------------------------------------------------------------
+# 防死循环
+# ---------------------------------------------------------------------------
+
+
+async def test_loop_limit_raises_workflow_loop_error():
+    async def always_reject(state: ClusterState, node: WorkflowNode, ctx: NodeContext) -> dict:
+        request = ActionRequest(
+            id=f"ar-{ctx.run_id}",
+            kind=node.gate,
+            title="迭代验收审批",
+            decisions=[ApprovalRecord(by_role="pm", type="reject")],
+        )
+        return {"gate_payloads": {node.gate: request}}
+
+    compiled = WorkflowEngine(handlers={"gate": always_reject}).compile(LOOP_YAML)
+    with pytest.raises(WorkflowLoopError, match="max_iterations=4"):
+        _ = [event async for event in compiled.run()]
+
+
+# ---------------------------------------------------------------------------
+# ClusterState reducer 契约（Task 1 模型 retrofit）
+# ---------------------------------------------------------------------------
+
+
+def test_cluster_state_list_channels_use_add_reducers():
+    hints = typing.get_type_hints(ClusterState, include_extras=True)
+    for field_name in ("iterations", "tasks", "meetings", "decisions", "messages"):
+        assert hints[field_name].__metadata__ == (operator.add,), field_name
+
+
+def test_action_request_carries_decisions():
+    request = ActionRequest(
+        id="ar1",
+        kind=GateKind.RELEASE,
+        title="发布审批",
+        decisions=[ApprovalRecord(by_role="pm", type="reject")],
+    )
+    assert request.decisions[-1].type == "reject"
```
