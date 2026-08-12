# Final Fix Review Package (source+tests only)

Fix base: d7d1c34
Head: d791d02

## Diff stat

```
 src/agent_cluster/cli.py      | 31 ++++++++++++++++++-
 src/agent_cluster/meetings.py | 22 ++++++++-----
 src/agent_cluster/workflow.py | 32 ++++++++++++++++---
 tests/test_integration.py     | 18 +++++++++++
 tests/test_meetings.py        | 25 ++++++++++-----
 tests/test_workflow.py        | 72 ++++++++++++++++++++++++++++++++++++++++---
 6 files changed, 176 insertions(+), 24 deletions(-)
```

## Full diff

```diff
diff --git a/src/agent_cluster/cli.py b/src/agent_cluster/cli.py
index f245308..9e5ca56 100644
--- a/src/agent_cluster/cli.py
+++ b/src/agent_cluster/cli.py
@@ -21,12 +21,16 @@ import sys
 from collections import Counter
 from collections.abc import Callable, Sequence
 from dataclasses import dataclass, field
+from enum import StrEnum
 from pathlib import Path
 from typing import TextIO
 
 import yaml
 from langgraph.checkpoint.memory import MemorySaver
+from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
+from pydantic import BaseModel
 
+from agent_cluster import models
 from agent_cluster.evolution import Candidate, EvolutionEngine, EvolutionError
 from agent_cluster.gates import approval_pending, make_gate_handler, resolve_auto_response
 from agent_cluster.meetings import MeetingHost, make_meeting_handler
@@ -53,6 +57,27 @@ __all__ = ["main", "run_flow", "RunSummary"]
 PROMPT_HINT = "请选择审批结论 [accept|reject|response <内容>|edit <内容>]："
 
 
+def _collect_state_model_names() -> tuple[str, ...]:
+    """枚举 agent_cluster.models 中参与状态序列化的公开类名（BaseModel 子类与 StrEnum）。
+
+    - 供 ``MemorySaver(serde=JsonPlusSerializer(...))`` 的 allowed_msgpack_modules
+      使用，避免每次 agent-cluster run 打印 langgraph 的「未注册类型」告警。
+    - 覆盖 ClusterState 各通道实际出现的模型/枚举（Project/Iteration/Task/Meeting/
+      Message/ActionRequest/ApprovalRecord/Ledger 及 GateKind/MeetingKind/
+      MessageType/TaskStatus 等）；多列出的类不会序列化，无副作用。
+    """
+    names: list[str] = []
+    for name, obj in vars(models).items():
+        if name.startswith("_") or getattr(obj, "__module__", "") != models.__name__:
+            continue
+        if isinstance(obj, type) and (issubclass(obj, BaseModel) or issubclass(obj, StrEnum)):
+            names.append(name)
+    return tuple(sorted(names))
+
+
+MODEL_NAMES = _collect_state_model_names()
+
+
 @dataclass
 class RunSummary:
     """一次 CLI run 会话的汇总结果（供测试与摘要打印）。"""
@@ -124,7 +149,11 @@ async def run_flow(
         "gate_payloads": {},
     }
 
-    checkpointer = MemorySaver()
+    checkpointer = MemorySaver(
+        serde=JsonPlusSerializer(
+            allowed_msgpack_modules={("agent_cluster.models", name) for name in MODEL_NAMES}
+        )
+    )
     graph = compiled.compile_graph(checkpointer=checkpointer)
     prompt_fn = prompt if prompt is not None else input
     events: list[Event] = []
diff --git a/src/agent_cluster/meetings.py b/src/agent_cluster/meetings.py
index 66fc8ac..990a6f8 100644
--- a/src/agent_cluster/meetings.py
+++ b/src/agent_cluster/meetings.py
@@ -52,7 +52,7 @@ class _MeetingTemplate:
     """会议模板：发言模板 + 决策结论模板（占位符 {agenda}/{participant}/{owner}）。
 
     ``decision_conclusion_reject`` / ``decision_reason_reject`` 为未通过变体
-    （当前仅 code_review 使用，如 3 位以上参与者时第 3 位发言者给出 LBTM）。
+    （当前仅 code_review 使用：存在显式 LBTM 发言者（debugger 岗）时未通过）。
     """
 
     speech: str
@@ -138,14 +138,22 @@ def _default_agenda(kind: MeetingKind) -> list[str]:
     return list(_DEFAULT_AGENDAS[kind])
 
 
-def _speech_verdict(participant_index: int) -> str:
-    """code_review 发言裁决（确定性）：第 3 位（index%3==2）发言者给出 LBTM，其余 LGTM。"""
-    return "LBTM（需修复高优问题）" if participant_index % 3 == 2 else "LGTM（通过）"
+def _speech_verdict(participant: str) -> str:
+    """code_review 发言裁决（确定性）：默认全部 LGTM（含评审人 reviewer）。
+
+    显式 LBTM 发言者 = 缺陷排查岗（debugger）——其在评审中给出「需修复高优问题」
+    的阻塞意见；其余发言者（含 reviewer）默认给出 LGTM。
+    """
+    return "LBTM（需修复高优问题）" if participant == "debugger" else "LGTM（通过）"
 
 
 def _review_passed(participants: list[str]) -> bool:
-    """code_review 是否通过：参与者 < 3 时无 LBTM 发言者，判定通过。"""
-    return len(participants) < 3
+    """code_review 是否通过：按各发言者的实际裁决推导——不存在 LBTM 意见即通过。
+
+    默认参与岗（frontend/backend/reviewer）全员 LGTM 通过；若参与者包含
+    debugger（显式 LBTM 发言者）则未通过。
+    """
+    return not any(_speech_verdict(participant) == "LBTM（需修复高优问题）" for participant in participants)
 
 
 def _now_stamp() -> str:
@@ -184,7 +192,7 @@ class MeetingHost:
         transcript: list[Message] = []
         for item in agenda:
             for index, participant in enumerate(participants):
-                verdict = _speech_verdict(index) if meeting_kind == MeetingKind.CODE_REVIEW else ""
+                verdict = _speech_verdict(participant) if meeting_kind == MeetingKind.CODE_REVIEW else ""
                 content = template.speech.format(agenda=item, participant=participant, verdict=verdict)
                 transcript.append(
                     Message(
diff --git a/src/agent_cluster/workflow.py b/src/agent_cluster/workflow.py
index 0d9708f..820bc07 100644
--- a/src/agent_cluster/workflow.py
+++ b/src/agent_cluster/workflow.py
@@ -18,8 +18,9 @@
 handler 契约（Task 4/5 据此注册）：
 - ``WorkflowEngine(handlers={"agent": ..., "meeting": ..., "gate": ...})`` 按
   **节点类型** 注册异步 handler；``start``/``end``/``parallel`` 为内置节点，
-  不查询 handlers；未注册类型的节点使用默认占位 handler（不改状态、不发额外事件），
-  保证编译与运行不中断。
+  不查询 handlers；未注册的 agent/meeting 节点使用默认占位 handler（不改状态、不发额外事件），
+  保证编译与运行不中断；含 gate 节点的流程编译时必须注册 "gate" handler
+  （门节点不允许静默放行，见 WorkflowEngine.compile）。
 - handler 签名：``async def handler(state: ClusterState, node: WorkflowNode,
   ctx: NodeContext) -> dict[str, Any]``，返回 **LangGraph channel 更新字典**
   （如 ``{"tasks": [Task(...)]}``、``{"gate_payloads": {GateKind: ActionRequest(...)}}``）。
@@ -163,7 +164,7 @@ class _RunState:
 
 
 def _validate_spec(spec: WorkflowSpec) -> None:
-    """编译前校验：重复 id、悬空引用、start/end 唯一性与出边、gate 出边、parallel children、max_iterations。"""
+    """编译前校验：重复 id、悬空引用、start/end 唯一性与出边唯一、gate 出边、parallel children 与子节点禁出边、max_iterations。"""
     nodes_by_id: dict[str, WorkflowNode] = {}
     for node in spec.nodes:
         if node.id in nodes_by_id:
@@ -201,11 +202,18 @@ def _validate_spec(spec: WorkflowSpec) -> None:
                     f"边 {edge.from_!r}→{edge.to!r} 的 {field_name} 引用不存在的节点：{target!r}"
                 )
 
-    if not any(edge.from_ == start_node.id for edge in spec.edges):
+    start_edges = [edge for edge in spec.edges if edge.from_ == start_node.id]
+    if not start_edges:
         raise WorkflowValidationError(f"start 节点 {start_node.id!r} 至少需要一条出边")
+    if len(start_edges) > 1:
+        raise WorkflowValidationError(
+            f"start 节点 {start_node.id!r} 必须恰好一条出边，实际 {len(start_edges)} 条："
+            f"{[edge.to for edge in start_edges]}"
+        )
     if any(edge.from_ == end_node.id for edge in spec.edges):
         raise WorkflowValidationError(f"end 节点 {end_node.id!r} 不允许有出边")
 
+    parallel_children: set[str] = set()
     for node in spec.nodes:
         if node.type == "gate" and not any(edge.from_ == node.id for edge in spec.edges):
             raise WorkflowValidationError(f"gate 节点 {node.id!r} 至少需要一条出边")
@@ -215,8 +223,15 @@ def _validate_spec(spec: WorkflowSpec) -> None:
             for child_id in node.children:
                 if child_id not in nodes_by_id:
                     raise WorkflowValidationError(f"parallel 节点 {node.id!r} 的子节点 {child_id!r} 不存在")
+                parallel_children.add(child_id)
             if not any(edge.from_ == node.id for edge in spec.edges):
                 raise WorkflowValidationError(f"parallel 节点 {node.id!r} 至少需要一条出边（fan-in 目标）")
+    for edge in spec.edges:
+        if edge.from_ in parallel_children:
+            raise WorkflowValidationError(
+                f"parallel 子节点 {edge.from_!r} 不允许声明出边（fan-in 由 parallel 节点自动汇聚，"
+                "子节点自带出边会导致未声明节点被执行）"
+            )
 
 
 class CompiledWorkflow:
@@ -609,7 +624,8 @@ class WorkflowEngine:
     """流程引擎：YAML 流程 DSL → 校验 → CompiledWorkflow。
 
     ``handlers`` 按节点类型注册（"agent"/"meeting"/"gate"）；"start"/"end"/"parallel"
-    为内置节点，不查询 handlers；未注册类型的节点走默认占位 handler。
+    为内置节点，不查询 handlers；未注册的 agent/meeting 节点走默认占位 handler，
+    但含 gate 节点的流程编译时必须注册 "gate" handler（门节点不允许静默放行）。
     """
 
     def __init__(self, handlers: dict[str, NodeHandler] | None = None) -> None:
@@ -628,4 +644,10 @@ class WorkflowEngine:
         except ValidationError as exc:
             raise WorkflowValidationError(f"流程规格非法：{exc}") from exc
         _validate_spec(spec)
+        gate_ids = [node.id for node in spec.nodes if node.type == "gate"]
+        if gate_ids and "gate" not in self._handlers:
+            raise WorkflowValidationError(
+                "流程包含 gate 节点但未注册 'gate' handler，门节点不允许静默放行："
+                f"{gate_ids}（请注册 make_gate_handler 等 gate handler）"
+            )
         return CompiledWorkflow(spec=spec, handlers=self._handlers)
diff --git a/tests/test_integration.py b/tests/test_integration.py
index 1026111..b341a68 100644
--- a/tests/test_integration.py
+++ b/tests/test_integration.py
@@ -11,6 +11,8 @@
 from __future__ import annotations
 
 import asyncio
+import contextlib
+import io
 import subprocess
 import sys
 from pathlib import Path
@@ -77,6 +79,12 @@ def test_cli_run_yes_full_flow_completes_without_hanging():
         MeetingKind.CODE_REVIEW,
     }
 
+    # 代码评审通过（最终评审 Fix 4：默认评审人给出 LGTM，纪要与流程走向一致）
+    code_review = next(meeting for meeting in state.meetings if meeting.kind == MeetingKind.CODE_REVIEW)
+    assert all("LGTM" in decision.conclusion for decision in code_review.decisions)
+    assert all("未通过" not in decision.conclusion for decision in code_review.decisions)
+    assert not any("LBTM" in message.payload["content"] for message in code_review.transcript)
+
     # 任务板验收：全部 Done 且每条任务 ≥1 产出物
     assert state.tasks, "终态应包含任务"
     assert all(task.status == TaskStatus.DONE for task in state.tasks), "任务板应全部 Done"
@@ -90,6 +98,16 @@ def test_cli_run_yes_full_flow_completes_without_hanging():
     assert GateKind.RELEASE in state.gate_payloads
 
 
+def test_cli_run_yes_no_msgpack_unregistered_warnings():
+    """--yes 全流程：stderr 不含 langgraph「未注册类型」告警（JsonPlusSerializer 白名单）。"""
+    stderr = io.StringIO()
+    with contextlib.redirect_stderr(stderr):
+        summary = asyncio.run(run_flow(FLOW_PATH, project=str(REPO_ROOT), yes=True))
+    assert summary.events[-1].type == "workflow_end"
+    assert summary.suspended_count == 0
+    assert "unregistered type" not in stderr.getvalue()
+
+
 def test_cli_run_ask_mode_prompts_and_resumes():
     """交互模式：4 次挂起、人工 accept 恢复、最终 workflow_end。"""
     prompts = iter(["accept"] * 10)
diff --git a/tests/test_meetings.py b/tests/test_meetings.py
index 34a4df8..5bcf587 100644
--- a/tests/test_meetings.py
+++ b/tests/test_meetings.py
@@ -91,7 +91,7 @@ async def test_code_review_transcript_exercises_lgtm_and_lbtm_verdicts():
     meeting = await host.run(
         MeetingKind.CODE_REVIEW,
         agenda=["代码可读性与结构"],
-        participants=["backend", "frontend", "reviewer"],
+        participants=["backend", "frontend", "reviewer", "debugger"],
         project_id="proj1",
         state=None,
     )
@@ -102,11 +102,11 @@ async def test_code_review_transcript_exercises_lgtm_and_lbtm_verdicts():
 
 async def test_code_review_decision_matches_verdict():
     host = MeetingHost()
-    # 3 位参与者：第 3 位发言者给出 LBTM -> 决策为未通过
+    # 显式 LBTM 发言者（debugger）参与 -> 决策为未通过
     fail_meeting = await host.run(
         MeetingKind.CODE_REVIEW,
         agenda=["代码可读性与结构", "安全性"],
-        participants=["backend", "frontend", "reviewer"],
+        participants=["backend", "frontend", "reviewer", "debugger"],
         project_id="proj1",
         state=None,
     )
@@ -114,16 +114,27 @@ async def test_code_review_decision_matches_verdict():
     assert all("LBTM" in decision.conclusion for decision in fail_meeting.decisions)
     assert all("未通过" in decision.conclusion for decision in fail_meeting.decisions)
 
-    # 2 位参与者：无 LBTM 发言者 -> 决策为通过
-    pass_meeting = await host.run(
+    # 默认 3 位参与者（frontend/backend/reviewer）：评审人给出 LGTM -> 决策为通过
+    default_meeting = await host.run(
+        MeetingKind.CODE_REVIEW,
+        agenda=["代码可读性与结构"],
+        participants=["frontend", "backend", "reviewer"],
+        project_id="proj1",
+        state=None,
+    )
+    assert all("LGTM" in decision.conclusion for decision in default_meeting.decisions)
+    assert all("通过" in decision.conclusion for decision in default_meeting.decisions)
+
+    # 2 位参与者：全员 LGTM -> 决策为通过
+    small_meeting = await host.run(
         MeetingKind.CODE_REVIEW,
         agenda=["代码可读性与结构"],
         participants=["backend", "reviewer"],
         project_id="proj1",
         state=None,
     )
-    assert all("LGTM" in decision.conclusion for decision in pass_meeting.decisions)
-    assert all("通过" in decision.conclusion for decision in pass_meeting.decisions)
+    assert all("LGTM" in decision.conclusion for decision in small_meeting.decisions)
+    assert all("通过" in decision.conclusion for decision in small_meeting.decisions)
 
 
 async def test_select_speaker_round_robin():
diff --git a/tests/test_workflow.py b/tests/test_workflow.py
index f2eb5ac..eb4d472 100644
--- a/tests/test_workflow.py
+++ b/tests/test_workflow.py
@@ -121,13 +121,24 @@ edges:
 """
 
 
+async def _accept_gate_handler(state: ClusterState, node: WorkflowNode, ctx: NodeContext) -> dict:
+    """gate 占位 handler：直接返回 accept 审批，供编译/路由测试使用。"""
+    request = ActionRequest(
+        id=f"ar-{ctx.run_id}",
+        kind=node.gate,
+        title="迭代验收审批",
+        decisions=[ApprovalRecord(by_role="pm", type="accept")],
+    )
+    return {"gate_payloads": {node.gate: request}}
+
+
 # ---------------------------------------------------------------------------
 # 编译与图描述
 # ---------------------------------------------------------------------------
 
 
 def test_compile_valid_yaml_with_gate_and_parallel():
-    compiled = WorkflowEngine().compile(GATE_AND_PARALLEL_YAML)
+    compiled = WorkflowEngine(handlers={"gate": _accept_gate_handler}).compile(GATE_AND_PARALLEL_YAML)
     assert isinstance(compiled, CompiledWorkflow)
     graph = compiled.get_graph()
     assert set(graph) == {"nodes", "edges"}
@@ -316,6 +327,59 @@ def test_non_mapping_yaml_raises_validation_error():
         WorkflowEngine().compile("- just\n- a\n- list\n")
 
 
+# ---------------------------------------------------------------------------
+# 最终评审修复：start 出边唯一、parallel 子节点禁出边、gate 必须注册 handler
+# ---------------------------------------------------------------------------
+
+
+def test_compile_rejects_multiple_start_out_edges():
+    """start 节点多条出边：编译期拒绝（避免多余边被静默丢弃）。"""
+    yaml_text = """
+name: invalid
+max_iterations: 10
+nodes:
+  - {id: start, type: start}
+  - {id: a, type: agent}
+  - {id: b, type: agent}
+  - {id: end, type: end}
+edges:
+  - {from: start, to: a}
+  - {from: start, to: b}
+  - {from: a, to: end}
+  - {from: b, to: end}
+"""
+    with pytest.raises(WorkflowValidationError, match="必须恰好一条出边"):
+        WorkflowEngine().compile(yaml_text)
+
+
+def test_compile_rejects_parallel_child_outgoing_edge():
+    """parallel 子节点自带出边：编译期拒绝（防止未声明节点被误执行）。"""
+    yaml_text = """
+name: invalid
+max_iterations: 10
+nodes:
+  - {id: start, type: start}
+  - {id: fanout, type: parallel, children: [c1, c2]}
+  - {id: c1, type: agent}
+  - {id: c2, type: agent}
+  - {id: other, type: agent}
+  - {id: end, type: end}
+edges:
+  - {from: start, to: fanout}
+  - {from: fanout, to: end}
+  - {from: c1, to: other}
+  - {from: other, to: end}
+"""
+    with pytest.raises(WorkflowValidationError, match="parallel 子节点 'c1' 不允许声明出边"):
+        WorkflowEngine().compile(yaml_text)
+
+
+def test_compile_rejects_gate_without_registered_handler():
+    """含 gate 节点但未注册 'gate' handler：编译期拒绝（门不允许静默放行）。"""
+    with pytest.raises(WorkflowValidationError, match="未注册 'gate' handler"):
+        WorkflowEngine().compile(GATE_YAML)
+
+
 # ---------------------------------------------------------------------------
 # 运行：事件序列
 # ---------------------------------------------------------------------------
@@ -437,9 +501,9 @@ async def test_gate_conditional_routing_takes_rework_then_accept():
 
 
 async def test_gate_accept_routes_straight_to_end():
-    """门 handler 未注入时（默认占位），gate 按缺省 accept 路由到 to。"""
+    """门 handler 返回 accept 时，gate 按 accept 路由到 to（直通 end）。"""
 
-    compiled = WorkflowEngine().compile(GATE_YAML)
+    compiled = WorkflowEngine(handlers={"gate": _accept_gate_handler}).compile(GATE_YAML)
     events = [event async for event in compiled.run()]
     actors = [event.actor for event in events if event.type == "node_start"]
     assert actors == ["start", "dev", "quality_gate", "end"]
@@ -580,7 +644,7 @@ async def test_interrupt_suspends_then_resume_completes():
 
 
 async def test_resume_requires_checkpointer():
-    compiled = WorkflowEngine().compile(GATE_YAML)
+    compiled = WorkflowEngine(handlers={"gate": _accept_gate_handler}).compile(GATE_YAML)
     with pytest.raises(ValueError, match="checkpointer"):
         _ = [event async for event in compiled.resume("proj:demo:iter:1", "accept")]
 
```
