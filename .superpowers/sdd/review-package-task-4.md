# Task 4 Review Package

Base: 952addc
Head: 81a1639

## Diff stat

```
 src/agent_cluster/__init__.py |  10 ++
 src/agent_cluster/gates.py    | 155 ++++++++++++++++++++
 src/agent_cluster/models.py   |   2 +-
 tests/test_gates.py           | 321 ++++++++++++++++++++++++++++++++++++++++++
 4 files changed, 487 insertions(+), 1 deletion(-)
```

## Full diff

```diff
diff --git a/src/agent_cluster/__init__.py b/src/agent_cluster/__init__.py
index 1293317..c9cb26b 100644
--- a/src/agent_cluster/__init__.py
+++ b/src/agent_cluster/__init__.py
@@ -50,6 +50,12 @@ from agent_cluster.workflow import (
     WorkflowSpec,
     WorkflowValidationError,
 )
+from agent_cluster.gates import (
+    GateError,
+    approval_pending,
+    make_gate_handler,
+    resolve_auto_response,
+)
 from agent_cluster.skills import (
     DisclosureLevel,
     SkillCatalog,
@@ -113,4 +119,8 @@ __all__ = [
     "WorkflowValidationError",
     "__version__",
     "format_skill_context",
+    "GateError",
+    "approval_pending",
+    "make_gate_handler",
+    "resolve_auto_response",
 ]
diff --git a/src/agent_cluster/gates.py b/src/agent_cluster/gates.py
new file mode 100644
index 0000000..01975e4
--- /dev/null
+++ b/src/agent_cluster/gates.py
@@ -0,0 +1,155 @@
+"""审批门（HITL interrupt）：设计文档 §5.4 审批门 + §6.5 bypass-immune 无人值守安全策略。
+
+职责：
+- ``make_gate_handler``：构造注册进 ``WorkflowEngine`` 的 "gate" 节点 handler；
+  首次执行以 ``interrupt()`` 挂起等待人工审批（挂起后 ``run()`` 产出
+  ``workflow_suspended`` 事件），恢复时 ``interrupt()`` 返回 ``HumanResponse``，
+  handler 把审批结论落成 ``ApprovalRecord`` 并写入 ``gate_payloads`` / ``decisions``
+  通道（Task 3 门路由契约：``gate_payloads[node.gate].decisions[-1].type`` 驱动条件路由）。
+- ``approval_pending``：从 checkpointer 读取当前挂起的审批请求（供 CLI/测试）。
+- ``resolve_auto_response``：无人值守自动审批策略（accept/reject/ask）；
+  ``bypass_immune=True`` 的高风险门在无人值守 accept 时自动转为拒绝（§6.5 自动 DENY）。
+
+兼容说明（installed langgraph 1.2.11）：
+- ``interrupt()`` 以 ``__interrupt__`` 流步挂起（不抛异常），恢复时原样返回
+  ``Command(resume=...)`` 的响应；因此 ``interrupt([payload])`` 的返回值可能是
+  list（首挂起语义）或直接是 ``HumanResponse``（恢复语义），需归一化处理。
+- 挂起状态在 ``StateSnapshot.interrupts``（元素为 ``Interrupt(value, id)``），
+  不在 ``values["__interrupt__"]`` 中；``approval_pending`` 两者都兼容。
+"""
+
+from __future__ import annotations
+
+from datetime import datetime, timezone
+from typing import Any
+
+from langgraph.types import interrupt
+
+from agent_cluster.models import (
+    ActionRequest,
+    ApprovalGate,
+    ApprovalRecord,
+    ClusterState,
+    GateKind,
+    HumanInterruptConfig,
+    HumanResponse,
+)
+from agent_cluster.workflow import NodeContext, NodeHandler, WorkflowNode
+
+__all__ = ["GateError", "make_gate_handler", "approval_pending", "resolve_auto_response"]
+
+AUTO_DENY_REASON = "bypass-immune: 无人值守自动拒绝"
+
+
+class GateError(Exception):
+    """审批门配置错误（gate 节点缺少类别、无人值守模式非法等）。"""
+
+
+def _now_utc() -> datetime:
+    """返回当前 UTC 时间（审批记录时间戳）。"""
+    return datetime.now(timezone.utc)
+
+
+def make_gate_handler(
+    role_scope: dict[str, GateKind] | None = None,
+    gate: ApprovalGate | None = None,
+) -> NodeHandler:
+    """构造 "gate" 节点 handler：interrupt 挂起 → 恢复后落审批记录并返回路由更新。
+
+    参数：
+    - ``role_scope``：可选的岗位审批范围映射（岗位 id -> 可审批的 GateKind）。
+      本任务仅作为治理元信息接收（Task 6/7 角色治理使用），不改变审批行为。
+    - ``gate``：可选 ``ApprovalGate`` 模型实例；提供时使用其 ``interrupt_config``
+      作为中断选项，缺省 ``HumanInterruptConfig()``（全部允许 True）。
+
+    handler 从 gate 节点构造 ``ActionRequest``（id=节点 id、kind=节点 gate 类别、
+    title/description 取节点或流程规格、risk_level="medium"、bypass_immune=False），
+    调用 ``interrupt([HumanInterrupt(...)])`` 挂起；恢复后把 ``HumanResponse``
+    写成 ``ApprovalRecord(by_role="human", ...)``，返回 LangGraph channel 更新：
+    ``{"gate_payloads": {node.gate: ActionRequest}, "decisions": [ApprovalRecord]}``。
+    """
+    interrupt_config = gate.interrupt_config if gate is not None else HumanInterruptConfig()
+
+    async def handler(state: ClusterState, node: WorkflowNode, ctx: NodeContext) -> dict[str, Any]:
+        if node.gate is None:
+            raise GateError(f"gate 节点 {node.id!r} 缺少 gate 类别配置（node.gate 为 None）")
+        if gate is not None and gate.kind != node.gate:
+            raise GateError(
+                f"ApprovalGate {gate.id!r} 的类别 {gate.kind!r} 与 gate 节点 {node.id!r} "
+                f"的类别 {node.gate!r} 不一致"
+            )
+        title = f"{node.gate.value} 审批"
+        description = ctx.spec.description or f"等待人工审批：节点 {node.id}（{node.gate.value}）"
+        request = ActionRequest(
+            id=node.id,
+            kind=node.gate,
+            title=title,
+            description=description,
+            evidence={"node": node.id, "gate": node.gate.value, "run_id": ctx.run_id},
+            risk_level="medium",
+            bypass_immune=False,
+        )
+        human_interrupt: dict[str, Any] = {
+            "action_request": request,
+            "config": interrupt_config.model_dump(),
+            "description": request.description,
+        }
+        resumed = interrupt([human_interrupt])
+        decision = resumed[0] if isinstance(resumed, list) else resumed
+        if not isinstance(decision, HumanResponse):
+            decision = HumanResponse.model_validate(decision)
+        record = ApprovalRecord(
+            by_role="human",
+            type=decision.type,
+            args=decision.args,
+            ts=_now_utc(),
+        )
+        request.decisions.append(record)
+        return {"gate_payloads": {node.gate: request}, "decisions": [record]}
+
+    return handler
+
+
+def approval_pending(graph: Any, thread_id: str) -> ActionRequest | None:
+    """查询 checkpointer 状态中当前挂起的审批请求，返回其 ``action_request``。
+
+    - langgraph 0.2.x：挂起状态在 ``state.values["__interrupt__"]``（HumanInterrupt 列表）。
+    - langgraph 1.x（installed 1.2.11）：挂起状态在 ``state.interrupts``（``Interrupt``
+      元组，``Interrupt.value`` 为传给 ``interrupt()`` 的载荷）。
+    两者均兼容；无挂起审批或载荷缺 ``action_request`` 时返回 None。
+    """
+    snapshot = graph.get_state({"configurable": {"thread_id": thread_id}})
+    pending = snapshot.values.get("__interrupt__")
+    if pending is None:
+        pending = getattr(snapshot, "interrupts", None)
+    if not pending:
+        return None
+    first = pending[0]
+    payload = getattr(first, "value", None)
+    if payload is None:
+        payload = first
+    if isinstance(payload, list):
+        payload = payload[0] if payload else None
+    if payload is None:
+        return None
+    action_request = payload.get("action_request") if isinstance(payload, dict) else None
+    if action_request is None:
+        return None
+    return ActionRequest.model_validate(action_request)
+
+
+def resolve_auto_response(req: ActionRequest, auto_mode: str) -> HumanResponse:
+    """无人值守自动审批策略（§6.5 安全约束）。
+
+    - ``auto_mode="accept"``：自动放行；但 ``req.bypass_immune=True``（高风险门）
+      时自动转为拒绝（原因 "bypass-immune: 无人值守自动拒绝"），禁止无人值守放行。
+    - ``auto_mode="reject"``：一律自动拒绝。
+    - ``auto_mode="ask"``：必须人工响应；无人值守下不允许自动决策，抛 ``GateError``。
+    """
+    if auto_mode == "ask":
+        raise GateError("auto_mode='ask' 需要人工响应，不能在无人值守模式下自动决策")
+    if auto_mode not in {"accept", "reject"}:
+        raise GateError(f"未知的无人值守模式：{auto_mode!r}（仅支持 accept/reject/ask）")
+    if auto_mode == "accept" and req.bypass_immune:
+        return HumanResponse(type="reject", args={"reason": AUTO_DENY_REASON})
+    return HumanResponse(type=auto_mode)
\ No newline at end of file
diff --git a/src/agent_cluster/models.py b/src/agent_cluster/models.py
index 8c23f6f..9ac0eda 100644
--- a/src/agent_cluster/models.py
+++ b/src/agent_cluster/models.py
@@ -411,7 +411,7 @@ class HumanResponse(BaseModel):
 
     model_config = ConfigDict(extra="ignore")
 
-    type: Literal["accept", "ignore", "response", "edit"] = Field(description="响应类型")
+    type: Literal["accept", "ignore", "response", "edit", "reject"] = Field(description="响应类型")
     args: Any = Field(default=None, description="响应参数，任意类型")
 
 
diff --git a/tests/test_gates.py b/tests/test_gates.py
new file mode 100644
index 0000000..4104617
--- /dev/null
+++ b/tests/test_gates.py
@@ -0,0 +1,321 @@
+"""Task 4 行为测试：审批门（HITL interrupt）真实中断/恢复、条件路由、审计落盘、bypass-immune 无人值守自动拒绝。
+
+不 mock 关键逻辑：通过 WorkflowEngine + make_gate_handler 注册 "gate" handler，
+用 MemorySaver 跑真实 interrupt() 挂起与 Command(resume=...) 恢复。
+"""
+
+from __future__ import annotations
+
+import pytest
+
+from langgraph.checkpoint.memory import MemorySaver
+
+from agent_cluster.gates import (
+    GateError,
+    approval_pending,
+    make_gate_handler,
+    resolve_auto_response,
+)
+from agent_cluster.models import (
+    ActionRequest,
+    ApprovalGate,
+    ClusterState,
+    GateKind,
+    HumanInterruptConfig,
+    HumanResponse,
+)
+from agent_cluster.workflow import WorkflowEngine
+
+THREAD_ID = "proj:demo:iter:1"
+
+SIMPLE_GATE_YAML = """
+name: release-gate-flow
+description: 发布门：人工确认后发布
+max_iterations: 10
+thread_id: "proj:demo:iter:1"
+nodes:
+  - {id: start, type: start}
+  - {id: release_gate, type: gate, gate: release}
+  - {id: end, type: end}
+edges:
+  - {from: start, to: release_gate}
+  - {from: release_gate, to: end, on_accept: end, on_reject: end}
+"""
+
+ROUTING_GATE_YAML = """
+name: gate-routing-flow
+max_iterations: 20
+thread_id: "proj:demo:iter:1"
+nodes:
+  - {id: start, type: start}
+  - {id: quality_gate, type: gate, gate: iteration_acceptance}
+  - {id: rework, type: agent, role: backend}
+  - {id: end, type: end}
+edges:
+  - {from: start, to: quality_gate}
+  - {from: quality_gate, to: end, on_accept: end, on_reject: rework, on_edit: rework, on_response: end}
+  - {from: rework, to: quality_gate}
+"""
+
+
+def _compile_flow(
+    flow_yaml: str,
+    role_scope: dict[str, GateKind] | None = None,
+    gate: ApprovalGate | None = None,
+):
+    """编译流程并注册真实 gate handler（interrupt HITL）。"""
+    handler = make_gate_handler(role_scope=role_scope, gate=gate)
+    return WorkflowEngine(handlers={"gate": handler}).compile(flow_yaml)
+
+
+def _graph_with_checkpointer(compiled, checkpointer):
+    """构造绑定 checkpointer 的已编译图（approval_pending / 读取终态需要）。"""
+    return compiled._compile_graph(checkpointer=checkpointer)
+
+
+def _final_state(compiled, checkpointer) -> ClusterState:
+    """读取线程最终 ClusterState（含 decisions/gate_payloads 审计字段）。"""
+    snapshot = _graph_with_checkpointer(compiled, checkpointer).get_state(
+        {"configurable": {"thread_id": THREAD_ID}}
+    )
+    return ClusterState.model_validate(snapshot.values)
+
+
+# ---------------------------------------------------------------------------
+# 1. 首次运行挂起 + approval_pending 读取挂起审批
+# ---------------------------------------------------------------------------
+
+
+async def test_first_run_suspends_and_approval_pending_returns_request():
+    checkpointer = MemorySaver()
+    compiled = _compile_flow(SIMPLE_GATE_YAML)
+
+    events = [event async for event in compiled.run(checkpointer=checkpointer)]
+    assert events[-1].type == "workflow_suspended"
+    assert events[-1].payload == {"node_id": "release_gate", "thread_id": THREAD_ID}
+    assert [event.actor for event in events if event.type == "node_start"] == [
+        "start",
+        "release_gate",
+    ]
+
+    request = approval_pending(_graph_with_checkpointer(compiled, checkpointer), THREAD_ID)
+    assert request is not None
+    assert request.id == "release_gate"
+    assert request.kind == GateKind.RELEASE
+    assert request.title == "release 审批"
+    assert request.risk_level == "medium"
+    assert request.bypass_immune is False
+    assert request.decisions == []
+
+
+# ---------------------------------------------------------------------------
+# 2. accept 恢复：流程走完 + decisions 通道落一条 ApprovalRecord
+# ---------------------------------------------------------------------------
+
+
+async def test_accept_resume_completes_flow_and_records_decision():
+    checkpointer = MemorySaver()
+    compiled = _compile_flow(SIMPLE_GATE_YAML, role_scope={"pm": GateKind.RELEASE})
+    _ = [event async for event in compiled.run(checkpointer=checkpointer)]
+
+    resume_events = [
+        event
+        async for event in compiled.resume(
+            THREAD_ID, HumanResponse(type="accept"), checkpointer=checkpointer
+        )
+    ]
+    assert resume_events[-1].type == "workflow_end"
+    # 挂起节点恢复后重新执行，accept 路由到 end
+    assert [event.actor for event in resume_events if event.type == "node_start"] == [
+        "release_gate",
+        "end",
+    ]
+
+    state = _final_state(compiled, checkpointer)
+    assert len(state.decisions) == 1
+    record = state.decisions[0]
+    assert record.type == "accept"
+    assert record.by_role == "human"
+    assert record.ts is not None
+    assert state.gate_payloads[GateKind.RELEASE].decisions[-1].type == "accept"
+
+
+# ---------------------------------------------------------------------------
+# 3. reject / edit 恢复：按 on_reject / on_edit 分支路由（返工再入 gate）
+# ---------------------------------------------------------------------------
+
+
+async def test_reject_resume_routes_to_rework_and_re_gates():
+    checkpointer = MemorySaver()
+    compiled = _compile_flow(ROUTING_GATE_YAML)
+    _ = [event async for event in compiled.run(checkpointer=checkpointer)]
+
+    reject_events = [
+        event
+        async for event in compiled.resume(
+            THREAD_ID, HumanResponse(type="reject", args={"reason": "验收不达标"}), checkpointer=checkpointer
+        )
+    ]
+    assert reject_events[-1].type == "workflow_suspended"
+    # reject → rework 节点运行 → 重新进入 gate 再次挂起
+    assert [event.actor for event in reject_events if event.type == "node_start"] == [
+        "quality_gate",
+        "rework",
+        "quality_gate",
+    ]
+    second_request = approval_pending(_graph_with_checkpointer(compiled, checkpointer), THREAD_ID)
+    assert second_request is not None
+    assert second_request.kind == GateKind.ITERATION_ACCEPTANCE
+
+    accept_events = [
+        event
+        async for event in compiled.resume(
+            THREAD_ID, HumanResponse(type="accept"), checkpointer=checkpointer
+        )
+    ]
+    assert accept_events[-1].type == "workflow_end"
+    assert [event.actor for event in accept_events if event.type == "node_start"] == [
+        "quality_gate",
+        "end",
+    ]
+
+    state = _final_state(compiled, checkpointer)
+    assert [record.type for record in state.decisions] == ["reject", "accept"]
+
+
+async def test_edit_resume_routes_to_rework_branch():
+    checkpointer = MemorySaver()
+    compiled = _compile_flow(ROUTING_GATE_YAML)
+    _ = [event async for event in compiled.run(checkpointer=checkpointer)]
+
+    edit_events = [
+        event
+        async for event in compiled.resume(
+            THREAD_ID,
+            HumanResponse(type="edit", args={"text": "修正验收标准"}),
+            checkpointer=checkpointer,
+        )
+    ]
+    assert edit_events[-1].type == "workflow_suspended"
+    assert [event.actor for event in edit_events if event.type == "node_start"] == [
+        "quality_gate",
+        "rework",
+        "quality_gate",
+    ]
+
+    state = _final_state(compiled, checkpointer)
+    assert state.decisions[-1].type == "edit"
+    assert state.decisions[-1].args == {"text": "修正验收标准"}
+
+
+# ---------------------------------------------------------------------------
+# 4. bypass-immune 无人值守自动拒绝策略
+# ---------------------------------------------------------------------------
+
+
+def test_bypass_immune_auto_reject_policy():
+    immune_request = ActionRequest(id="ar-immune", kind=GateKind.RELEASE, bypass_immune=True)
+    denied = resolve_auto_response(immune_request, "accept")
+    assert denied.type == "reject"
+    assert denied.args == {"reason": "bypass-immune: 无人值守自动拒绝"}
+
+    rejected = resolve_auto_response(immune_request, "reject")
+    assert rejected.type == "reject"
+
+    accepted = resolve_auto_response(ActionRequest(id="ar-plain", kind=GateKind.RELEASE), "accept")
+    assert accepted.type == "accept"
+
+    with pytest.raises(GateError, match="ask"):
+        resolve_auto_response(immune_request, "ask")
+    with pytest.raises(GateError, match="未知的无人值守模式"):
+        resolve_auto_response(immune_request, "maybe")
+
+
+# ---------------------------------------------------------------------------
+# 5. 审计：审批记录 ts/args 完整落盘
+# ---------------------------------------------------------------------------
+
+
+async def test_audit_trail_record_ts_and_args():
+    checkpointer = MemorySaver()
+    compiled = _compile_flow(SIMPLE_GATE_YAML)
+    _ = [event async for event in compiled.run(checkpointer=checkpointer)]
+    _ = [
+        event
+        async for event in compiled.resume(
+            THREAD_ID,
+            HumanResponse(type="accept", args={"approver": "pm", "note": "发布窗口确认"}),
+            checkpointer=checkpointer,
+        )
+    ]
+
+    state = _final_state(compiled, checkpointer)
+    assert len(state.decisions) == 1
+    record = state.decisions[0]
+    assert record.type == "accept"
+    assert record.by_role == "human"
+    assert record.args == {"approver": "pm", "note": "发布窗口确认"}
+    assert record.ts is not None
+    assert record.ts.tzinfo is not None  # now_utc 带时区
+
+
+async def test_approval_pending_returns_none_after_completion():
+    checkpointer = MemorySaver()
+    compiled = _compile_flow(SIMPLE_GATE_YAML)
+    _ = [event async for event in compiled.run(checkpointer=checkpointer)]
+    _ = [
+        event
+        async for event in compiled.resume(
+            THREAD_ID, HumanResponse(type="accept"), checkpointer=checkpointer
+        )
+    ]
+    assert approval_pending(_graph_with_checkpointer(compiled, checkpointer), THREAD_ID) is None
+
+
+# ---------------------------------------------------------------------------
+# 附加：GateError 非法配置 + ApprovalGate interrupt_config 透传
+# ---------------------------------------------------------------------------
+
+
+async def test_gate_handler_rejects_gate_node_without_kind():
+    bad_yaml = """
+name: bad-gate-flow
+max_iterations: 10
+thread_id: "proj:demo:iter:1"
+nodes:
+  - {id: start, type: start}
+  - {id: broken_gate, type: gate}
+  - {id: end, type: end}
+edges:
+  - {from: start, to: broken_gate}
+  - {from: broken_gate, to: end}
+"""
+    compiled = _compile_flow(bad_yaml)
+    with pytest.raises(GateError, match="缺少 gate 类别"):
+        _ = [event async for event in compiled.run()]
+
+
+async def test_gate_factory_uses_provided_interrupt_config():
+    checkpointer = MemorySaver()
+    gate_model = ApprovalGate(
+        id="release_gate",
+        kind=GateKind.RELEASE,
+        node="release_gate",
+        interrupt_config=HumanInterruptConfig(
+            allow_ignore=False, allow_respond=False, allow_edit=True, allow_accept=True
+        ),
+        payload=ActionRequest(id="ar-preset", kind=GateKind.RELEASE, title="预置载荷"),
+    )
+    compiled = _compile_flow(SIMPLE_GATE_YAML, gate=gate_model)
+    _ = [event async for event in compiled.run(checkpointer=checkpointer)]
+
+    snapshot = _graph_with_checkpointer(compiled, checkpointer).get_state(
+        {"configurable": {"thread_id": THREAD_ID}}
+    )
+    payload = snapshot.interrupts[0].value[0]
+    assert payload["config"] == {
+        "allow_ignore": False,
+        "allow_respond": False,
+        "allow_edit": True,
+        "allow_accept": True,
+    }
\ No newline at end of file
```
