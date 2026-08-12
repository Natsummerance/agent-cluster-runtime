# Task 7 Review Package

Base: c75c6c0
Head: 31d666a

## Diff stat

```
 README.md                                | 133 +++++++++
 examples/flows/fullstack-sprint.yaml     |  33 +++
 examples/skills/frontend-design/SKILL.md |  15 +
 examples/skills/qa-testing/SKILL.md      |  15 +
 pyproject.toml                           |   3 +
 src/agent_cluster/__main__.py            |  16 +-
 src/agent_cluster/cli.py                 | 474 +++++++++++++++++++++++++++++++
 src/agent_cluster/gates.py               |  81 +++++-
 src/agent_cluster/meetings.py            |   3 +-
 src/agent_cluster/models.py              |  11 +-
 src/agent_cluster/workflow.py            |  16 ++
 tests/test_gates.py                      | 125 +++++++-
 tests/test_integration.py                | 132 +++++++++
 13 files changed, 1030 insertions(+), 27 deletions(-)
```

## Full diff

```diff
diff --git a/README.md b/README.md
new file mode 100644
index 0000000..52cc1a7
--- /dev/null
+++ b/README.md
@@ -0,0 +1,133 @@
+# agent-cluster-runtime — 多 Agent 组织型全栈开发集群运行时
+
+> 版本：0.1.0 ｜ 语言：Python 3.11+ ｜ 底座：LangGraph + pydantic v2 ｜ 无 LLM 也可运行
+> 设计落地自 [`agent-clusters/智能体集群设计方案.md`](../agent-clusters/智能体集群设计方案.md)（v1.0）
+
+## 项目简介
+
+`agent-cluster-runtime` 是一个「像企业一样运转」的多 Agent 组织型全栈开发集群运行时：
+12 个岗位（产品/项目/前端/后端/算法/架构/测试/运维/文档/评审/排查/治理）按「决策—管理—执行」
+三层治理组织，7 类会议以审批门（HITL interrupt）落地，YAML 流程 DSL 编译为 LangGraph
+StateGraph，跑通「需求评审 → 设计评审 → 开发 → 代码评审 → 测试 → 发布评审」MVP 闭环，
+并通过六步进化闭环（收集→提炼→提案→评审→生效→回滚）实现流程/组织级自我进化。
+
+设计要点：
+
+- **流程即配置**：SOP 用可编译的图（YAML → StateGraph）表达，进化 = 重新编译流程，可灰度、可回滚。
+- **会议即审批门**：关键决策用 `interrupt`（HITL）落地，人机共治；无人值守（`--yes`）下
+  bypass-immune 高风险门自动拒绝（§6.5 自动 DENY）。
+- **岗位即技能**：每个岗位 = 角色画像 + 工具集 + SKILL.md 技能包 + 审批权限。
+- **可观测是进化的前提**：事件流 + 检查点 + 审批审计 + 绩效度量驱动进化信号。
+
+## 架构图
+
+```mermaid
+flowchart TD
+    subgraph 六层运行时
+        P1[流程编排层<br/>WorkflowEngine：YAML→StateGraph]
+        P2[角色执行层<br/>AgentRuntime / RoleRegistry]
+        P3[技能层<br/>SkillLoader / SkillCatalog]
+        P4[会议与审批门<br/>MeetingHost / 审批门 interrupt]
+        P5[记忆与账本<br/>Ledger / TaskBoard / 检查点]
+        P6[可观测与进化<br/>EventBus / Metrics / EvolutionEngine]
+    end
+
+    subgraph 六步闭环
+        E1[① 收集 collect] --> E2[② 提炼 distill] --> E3[③ 提案 propose]
+        E3 --> E4[④ 评审门 review] --> E5[⑤ 生效 apply] --> E6[⑥ 回滚 rollback]
+        E6 -. 复盘与度量反馈 .-> E1
+    end
+
+    P1 --> P2 --> P3 --> P4 --> P5 --> P6
+    P6 -. 度量信号 .-> E1
+```
+
+## 安装与运行
+
+前置：Python 3.11+ 与 [uv](https://docs.astral.sh/uv/)（Windows/macOS/Linux 均可）。
+
+```bash
+# 1) 安装依赖（首次）与进入虚拟环境
+uv sync
+
+# 2) 查看 CLI 帮助（中文）
+uv run agent-cluster --help
+
+# 3) 无人值守跑通完整 MVP 闭环（--yes 自动接受全部审批，bypass-immune 门自动拒绝）
+uv run agent-cluster run --flow examples/flows/fullstack-sprint.yaml --project examples --yes
+
+# 4) 交互式运行：遇审批门打印请求并读取 accept/reject/response <内容>/edit <内容>
+uv run agent-cluster run --flow examples/flows/fullstack-sprint.yaml --project examples
+```
+
+> 默认确定性模型后端（`DeterministicClient`），无需任何 API key；接入真实 LLM 时替换
+> `AgentConfig.model.model_name`（如 `openai/gpt-4o-mini`）并提供对应环境变量。
+
+## CLI 用法
+
+| 命令 | 说明 |
+|---|---|
+| `agent-cluster run --flow <yaml> [--project <dir>] [--yes] [--thread <id>]` | 编译并运行 YAML 流程；`--yes` 无人值守自动审批 |
+| `agent-cluster skills list --root <dir>` | 列出技能目录（name/version/description） |
+| `agent-cluster roles list` | 列出 12 岗位（id/name/kind/approval_scope） |
+| `agent-cluster proposals demo` | 六步进化闭环演示（collect→distill→propose→review→apply→rollback） |
+| `agent-cluster metrics demo` | 度量采集与阈值信号演示 |
+
+`python -m agent_cluster` 与 `agent-cluster` 等价；`main()` 返回 int 退出码（0 成功，1 失败）。
+
+### 示例流程说明
+
+`examples/flows/fullstack-sprint.yaml` 的完整 MVP 链：
+
+```text
+start → requirement_review(会议) → requirement_gate(需求确认门) → design(架构师)
+→ design_review(会议) → design_gate(设计门) → develop_parallel(前后端并行)
+→ code_review(会议) → test(QA) → iteration_gate(迭代验收门) → release(运维)
+→ release_gate(发布门) → end
+```
+
+返工边：`requirement_gate.reject → requirement_review`；`design_gate.reject → design`；
+`iteration_gate.reject → test`；`release_gate.reject → release`。`max_iterations=40`
+（节点总数 15，含返工余量），编译期校验必须 ≥ 节点总数。
+
+## 模块导览
+
+| 模块 | 职责 |
+|---|---|
+| `agent_cluster.models` | pydantic v2 数据模型：Role/Agent/Task/Meeting/Proposal/Skill/Ledger/ApprovalGate/Message/ClusterState/Event 与 GateKind 等枚举 |
+| `agent_cluster.skills` | SKILL.md 加载（frontmatter/正文/资源分类）、注册去重、按角色挂载与三级渐进披露 |
+| `agent_cluster.workflow` | YAML 流程 DSL 解析与校验、编译为 LangGraph StateGraph、事件流运行、parallel 并行与 gate 条件路由 |
+| `agent_cluster.gates` | 审批门（interrupt HITL）、bypass-immune 无人值守策略、`approval_pending` 查询挂起请求 |
+| `agent_cluster.roles` | 12 岗位目录（goal/backstory/skills/tools/approval_scope）与 RoleRegistry（会议默认参与岗位） |
+| `agent_cluster.runtime` | AgentRuntime（reply/observe）、ChatModelClient 抽象（默认确定性后端）、EventBus、agent 节点 handler |
+| `agent_cluster.meetings` | MeetingHost 7 类会议模板 + meeting 节点 handler（纪要/决策/行动项） |
+| `agent_cluster.ledger` | LedgerStore 任务账本 + TaskBoard 任务板（Backlog/Ready/InProgress/Review/Done 流转） |
+| `agent_cluster.evolution` | 六步进化闭环（collect→distill→propose→review→apply→rollback）+ 审计 + 禁止自我扩权 |
+| `agent_cluster.metrics` | MetricsCollector 度量采集 + MetricRules 阈值规则引擎（产出进化信号） |
+| `agent_cluster.cli` | `agent-cluster` 命令行入口（run/skills/roles/proposals/metrics） |
+
+## 参考项目映射表
+
+> 本方案为组合式架构：借鉴下表项目设计思想，不复制其运行时代码；`gpt-pilot`（自定义许可）
+> 与 `autogen`（CC-BY-4.0）**仅参考不运行**。
+
+| 参考项目 | 许可 | 借鉴内容 | 本方案组件 |
+|---|---|---|---|
+| MetaGPT | MIT | 软件公司角色模式、SOP 串联、角色化 agent 行动 | `roles.py`（12 岗位）、`runtime.py`（AgentRuntime） |
+| ChatDev | Apache-2.0 | YAML 流程 DSL、loop_counter 防死循环、多角色对话协作 | `workflow.py`（YAML→StateGraph、max_iterations） |
+| GPT Pilot | 自定义 | 任务状态机、规格/前端/排查岗位分工 | `runtime.py`、`roles.py`（仅设计参考，不运行） |
+| CrewAI | MIT | 角色画像（role/goal/backstory）、Flow 监听/路由/人工反馈 | `roles.py`（Role 模型）、`workflow.py`（条件路由） |
+| AutoGen | CC-BY-4.0 | 群聊多 Agent、反思与终止条件（仅设计参考，不运行） | `meetings.py`（会议子图设计思想） |
+| AgentScope | Apache-2.0 | Agent 配置四件套（Model/ReAct/Injection/Context）、事件驱动 | `models.py`（AgentConfig）、`runtime.py`（EventBus） |
+| LangGraph | MIT | StateGraph 编排、interrupt 审批门、检查点续跑、时间旅行审计 | `workflow.py`、`gates.py`（流程底座） |
+| anthropic-skills | 混合 | SKILL.md 技能包标准与渐进披露 | `skills.py`（SkillLoader/SkillCatalog）、`examples/skills/` |
+
+## 许可与致谢
+
+- 本项目代码许可：MIT（见各文件头声明约定；仓库内未附 LICENSE 文件时按 MIT 理解）。
+- 设计依据：[`agent-clusters/智能体集群设计方案.md`](../agent-clusters/智能体集群设计方案.md)
+  及其 8 份参考项目研读（`agent-clusters/docs/`）。
+- 参考项目许可提示：`gpt-pilot` 为自定义许可（已停止维护且曾遭供应链投毒，**切勿运行源码**）；
+  `autogen` 为 CC-BY-4.0；两者仅作设计参考，本方案不复用其代码。
+- 致谢 MetaGPT / ChatDev / GPT Pilot / CrewAI / AutoGen / AgentScope / LangGraph /
+  anthropic-skills 开源社区为多 Agent 协作提供的设计范式。
\ No newline at end of file
diff --git a/examples/flows/fullstack-sprint.yaml b/examples/flows/fullstack-sprint.yaml
new file mode 100644
index 0000000..919f87d
--- /dev/null
+++ b/examples/flows/fullstack-sprint.yaml
@@ -0,0 +1,33 @@
+name: fullstack-sprint
+description: 全栈冲刺 MVP 闭环：需求评审 → 需求确认门 → 设计 → 设计评审 → 设计门 → 前后端并行开发 → 代码评审 → 测试 → 迭代验收门 → 发布 → 发布门
+max_iterations: 40
+thread_id: "proj:demo:iter:1"
+nodes:
+  - {id: start, type: start}
+  - {id: requirement_review, type: meeting, meeting: requirement_review, participants: [pm, architect, frontend, backend, qa]}
+  - {id: requirement_gate, type: gate, gate: requirement_confirmation}
+  - {id: design, type: agent, role: architect}
+  - {id: design_review, type: meeting, meeting: design_review, participants: [architect, pmo, frontend, backend, qa, devops]}
+  - {id: design_gate, type: gate, gate: design_review}
+  - {id: develop_parallel, type: parallel, children: [develop_frontend, develop_backend]}
+  - {id: develop_frontend, type: agent, role: frontend}
+  - {id: develop_backend, type: agent, role: backend}
+  - {id: code_review, type: meeting, meeting: code_review, participants: [frontend, backend, reviewer]}
+  - {id: test, type: agent, role: qa}
+  - {id: iteration_gate, type: gate, gate: iteration_acceptance}
+  - {id: release, type: agent, role: devops}
+  - {id: release_gate, type: gate, gate: release}
+  - {id: end, type: end}
+edges:
+  - {from: start, to: requirement_review}
+  - {from: requirement_review, to: requirement_gate}
+  - {from: requirement_gate, to: design, on_accept: design, on_reject: requirement_review, on_edit: design}
+  - {from: design, to: design_review}
+  - {from: design_review, to: design_gate}
+  - {from: design_gate, to: develop_parallel, on_accept: develop_parallel, on_reject: design, on_edit: design}
+  - {from: develop_parallel, to: code_review}
+  - {from: code_review, to: test}
+  - {from: test, to: iteration_gate}
+  - {from: iteration_gate, to: release, on_accept: release, on_reject: test, on_edit: code_review}
+  - {from: release, to: release_gate}
+  - {from: release_gate, to: end, on_accept: end, on_reject: release}
\ No newline at end of file
diff --git a/examples/skills/frontend-design/SKILL.md b/examples/skills/frontend-design/SKILL.md
new file mode 100644
index 0000000..e29a35f
--- /dev/null
+++ b/examples/skills/frontend-design/SKILL.md
@@ -0,0 +1,15 @@
+---
+name: frontend-design
+description: 前端设计技能：UI 还原、组件拆分与交互设计，产出可实现的页面与组件规格。
+version: 1.0.0
+license: MIT
+allowed-tools:
+  - read_file
+  - write_file
+  - review
+---
+# 前端设计执行指引
+
+1. 先核对设计稿与交互流程，再拆分组件树与状态模型。
+2. 组件遵循单一职责，样式与业务逻辑分离，接口对齐后端 API 契约。
+3. 交付前自查响应式布局、可访问性与构建通过。 
\ No newline at end of file
diff --git a/examples/skills/qa-testing/SKILL.md b/examples/skills/qa-testing/SKILL.md
new file mode 100644
index 0000000..d29f2c4
--- /dev/null
+++ b/examples/skills/qa-testing/SKILL.md
@@ -0,0 +1,15 @@
+---
+name: qa-testing
+description: 测试质量保障技能：测试计划、用例设计、自动化执行与缺陷回归。
+version: 1.0.0
+license: MIT
+allowed-tools:
+  - read_file
+  - run_tests
+  - review
+---
+# 测试执行指引
+
+1. 依据验收标准编写测试计划与用例（Given/When/Then 格式）。
+2. 优先自动化冒烟与回归，覆盖边界条件与异常路径。
+3. 缺陷单须含复现步骤、期望/实际结果与优先级，回归通过后关闭。
\ No newline at end of file
diff --git a/pyproject.toml b/pyproject.toml
index 610686d..dc07c23 100644
--- a/pyproject.toml
+++ b/pyproject.toml
@@ -16,6 +16,9 @@ dev = [
     "pytest-asyncio",
 ]
 
+[project.scripts]
+agent-cluster = "agent_cluster.cli:main"
+
 [build-system]
 requires = ["hatchling"]
 build-backend = "hatchling.build"
diff --git a/src/agent_cluster/__main__.py b/src/agent_cluster/__main__.py
index e1c0383..29f5068 100644
--- a/src/agent_cluster/__main__.py
+++ b/src/agent_cluster/__main__.py
@@ -1,16 +1,8 @@
-"""CLI 占位入口：``python -m agent_cluster`` 打印版本与用法。
+"""CLI 入口：``python -m agent_cluster`` 等价于 ``agent-cluster`` 命令。"""
 
-完整 CLI（agent-cluster 命令）由后续任务（Task 7）实现。
-"""
-
-from agent_cluster import __version__
-
-
-def main() -> None:
-    """打印版本与用法占位。"""
-    print(f"agent_cluster {__version__}")
-    print("用法：后续任务将提供 agent-cluster 命令（run / skills / roles / proposals / metrics）。")
+import sys
 
+from agent_cluster.cli import main
 
 if __name__ == "__main__":
-    main()
+    sys.exit(main())
\ No newline at end of file
diff --git a/src/agent_cluster/cli.py b/src/agent_cluster/cli.py
new file mode 100644
index 0000000..6794ec5
--- /dev/null
+++ b/src/agent_cluster/cli.py
@@ -0,0 +1,474 @@
+"""CLI 入口（Task 7）：agent-cluster 命令（多 agent 组织型全栈开发集群运行时）。
+
+子命令：
+- ``run``：编译并运行 YAML 流程；遇审批门打印 ActionRequest 并交互读取
+  ``accept/reject/response <内容>/edit <内容>`` 恢复运行；``--yes`` 无人值守
+  模式自动接受（bypass-immune 高风险门自动转为拒绝），结束后打印运行摘要。
+- ``skills list``：列出技能目录（name/version/description）。
+- ``roles list``：列出 12 岗位（id/name/kind/approval_scope）。
+- ``proposals demo``：进化闭环演示（collect→distill→propose→review→apply→rollback）。
+- ``metrics demo``：度量采集与信号触发演示。
+
+``main()`` 返回 int 退出码；``python -m agent_cluster`` 等价于 agent-cluster。
+"""
+
+from __future__ import annotations
+
+import argparse
+import asyncio
+import os
+import sys
+from collections import Counter
+from collections.abc import Callable, Sequence
+from dataclasses import dataclass, field
+from pathlib import Path
+from typing import TextIO
+
+import yaml
+from langgraph.checkpoint.memory import MemorySaver
+
+from agent_cluster.evolution import EvolutionEngine
+from agent_cluster.gates import approval_pending, make_gate_handler, resolve_auto_response
+from agent_cluster.meetings import MeetingHost, make_meeting_handler
+from agent_cluster.metrics import MetricRules, MetricsCollector
+from agent_cluster.models import (
+    ActionRequest,
+    ApprovalRecord,
+    ClusterState,
+    Event,
+    HumanResponse,
+    Iteration,
+    Project,
+)
+from agent_cluster.roles import RoleRegistry, build_role_catalog
+from agent_cluster.runtime import AgentRuntime, make_agent_handler
+from agent_cluster.skills import SkillLoader
+from agent_cluster.workflow import WorkflowEngine
+
+__all__ = ["main", "run_flow", "RunSummary"]
+
+# 审批交互提示文案
+PROMPT_HINT = "请选择审批结论 [accept|reject|response <内容>|edit <内容>]："
+
+
+@dataclass
+class RunSummary:
+    """一次 CLI run 会话的汇总结果（供测试与摘要打印）。"""
+
+    thread_id: str
+    events: list[Event] = field(default_factory=list)
+    state: ClusterState | None = None
+    decisions: list[ApprovalRecord] = field(default_factory=list)
+    suspended_count: int = 0
+
+
+# ---------------------------------------------------------------------------
+# run 子命令核心逻辑（公开，供集成测试直接调用）
+# ---------------------------------------------------------------------------
+
+
+async def run_flow(
+    flow_path: str | os.PathLike[str],
+    *,
+    project: str | None = None,
+    yes: bool = False,
+    thread_id: str | None = None,
+    print_event: Callable[[Event], None] | None = None,
+    print_request: Callable[[ActionRequest], None] | None = None,
+    prompt: Callable[[str], str] | None = None,
+) -> RunSummary:
+    """编译并运行 YAML 流程，处理审批门挂起/恢复，返回汇总结果。
+
+    - 编译 handlers：agent（AgentRuntime+RoleRegistry）、meeting
+      （MeetingHost+RoleRegistry）、gate（make_gate_handler，``--yes`` 时
+      auto_mode="accept"，否则 "ask" 交互挂起）。
+    - ``MemorySaver`` 检查点；初始状态含 Project（来自 --project 目录名或流程名）、
+      Iteration 与空列表。
+    - 挂起时经 ``approval_pending`` 读取 ActionRequest：``yes=True`` 用
+      ``resolve_auto_response(req, "accept")``（bypass-immune 自动拒绝），否则调用
+      ``prompt`` 读取人工结论后 ``resume``；循环至 ``workflow_end``。
+    """
+    yaml_text = Path(flow_path).read_text(encoding="utf-8")
+    flow_data = yaml.safe_load(yaml_text)
+    spec_name = str((flow_data or {}).get("name") or "demo-flow")
+    spec_thread = str((flow_data or {}).get("thread_id") or "")
+    resolved_thread = thread_id or spec_thread or "default"
+
+    role_registry = RoleRegistry()
+    runtime = AgentRuntime()
+    host = MeetingHost()
+    engine = WorkflowEngine(
+        handlers={
+            "agent": make_agent_handler(runtime, role_registry),
+            "meeting": make_meeting_handler(host, role_registry),
+            "gate": make_gate_handler(auto_mode="accept" if yes else "ask"),
+        }
+    )
+    compiled = engine.compile(yaml_text)
+
+    if project:
+        project_name = os.path.basename(os.path.abspath(project))
+    else:
+        project_name = spec_name
+    initial = {
+        "project": Project(id=project_name, name=project_name, vision="多 agent 全栈 MVP 演示"),
+        "iterations": [
+            Iteration(id="iter:1", project_id=project_name, number=1, goal="交付可运行 MVP", status="in_progress")
+        ],
+        "tasks": [],
+        "meetings": [],
+        "messages": [],
+        "decisions": [],
+        "gate_payloads": {},
+    }
+
+    checkpointer = MemorySaver()
+    graph = compiled.compile_graph(checkpointer=checkpointer)
+    prompt_fn = prompt if prompt is not None else input
+    events: list[Event] = []
+    suspended_count = 0
+    first_run = True
+
+    while True:
+        if first_run:
+            stream = compiled.run(
+                initial=initial, thread_id=resolved_thread, checkpointer=checkpointer
+            )
+            first_run = False
+        else:
+            request = approval_pending(graph, resolved_thread)
+            if request is None:
+                raise RuntimeError("流程挂起但未从检查点找到待审批请求")
+            if print_request is not None:
+                print_request(request)
+            if yes:
+                response: HumanResponse = resolve_auto_response(request, "accept")
+            else:
+                response = _prompt_human(request, prompt_fn)
+            stream = compiled.resume(resolved_thread, response, checkpointer=checkpointer)
+
+        iteration_events = [event async for event in stream]
+        for event in iteration_events:
+            events.append(event)
+            if print_event is not None:
+                print_event(event)
+
+        if not iteration_events or iteration_events[-1].type != "workflow_suspended":
+            break
+        suspended_count += 1
+
+    snapshot = graph.get_state({"configurable": {"thread_id": resolved_thread}})
+    final_state = ClusterState.model_validate(snapshot.values)
+    return RunSummary(
+        thread_id=resolved_thread,
+        events=events,
+        state=final_state,
+        decisions=list(final_state.decisions),
+        suspended_count=suspended_count,
+    )
+
+
+def _prompt_human(request: ActionRequest, prompt_fn: Callable[[str], str]) -> HumanResponse:
+    """交互读取人工审批结论，返回对应 HumanResponse。"""
+    while True:
+        raw = prompt_fn(PROMPT_HINT).strip()
+        if not raw:
+            continue
+        parts = raw.split(maxsplit=1)
+        kind = parts[0].lower()
+        arg = parts[1] if len(parts) > 1 else None
+        if kind == "accept":
+            return HumanResponse(type="accept")
+        if kind == "reject":
+            return HumanResponse(type="reject")
+        if kind in ("response", "edit"):
+            if arg is None:
+                print(f"  提示：{kind} 需要提供内容，例如：{kind} 请补充验收标准")
+                continue
+            return HumanResponse(type=kind, args={"text": arg})
+        print(f"  无效输入：{raw!r}（支持 accept / reject / response <内容> / edit <内容>）")
+
+
+# ---------------------------------------------------------------------------
+# 事件 / 请求 / 摘要打印
+# ---------------------------------------------------------------------------
+
+
+def _print_event(event: Event, out: TextIO) -> None:
+    """按事件类型打印一行中文描述。"""
+    if event.type == "node_start":
+        print(f"[节点开始] {event.actor}", file=out)
+    elif event.type == "node_end":
+        print(f"[节点结束] {event.actor}", file=out)
+    elif event.type == "meeting_held":
+        print(f"[会议] {event.actor} 完成（决策 {event.payload.get('decisions', 0)} 项）", file=out)
+    elif event.type == "agent_step":
+        print(f"[执行] {event.actor}（节点 {event.payload.get('node', '')}）", file=out)
+    elif event.type == "workflow_suspended":
+        print(f"[挂起] 流程在节点 {event.payload.get('node_id', '')} 等待审批", file=out)
+    elif event.type == "workflow_start":
+        print(f"[开始] 流程「{event.payload.get('name', '')}」运行", file=out)
+    elif event.type == "workflow_end":
+        print("[完成] 流程运行结束", file=out)
+    else:
+        print(f"[{event.type}] {event.actor}", file=out)
+
+
+def _print_request(request: ActionRequest, out: TextIO) -> None:
+    """打印待审批 ActionRequest 的要点。"""
+    print(f"  待审批请求：{request.title}", file=out)
+    print(
+        f"    类别：{request.kind.value} | 风险：{request.risk_level} | "
+        f"bypass-immune：{request.bypass_immune}",
+        file=out,
+    )
+    print(f"    说明：{request.description}", file=out)
+
+
+def _print_summary(summary: RunSummary, out: TextIO) -> None:
+    """打印运行摘要：会议/任务/审批/事件统计。"""
+    state = summary.state
+    print("\n===== 运行摘要 =====", file=out)
+    print(f"线程：{summary.thread_id}", file=out)
+    print(f"事件总数：{len(summary.events)}", file=out)
+    print(f"挂起次数：{summary.suspended_count}", file=out)
+    if state is None:
+        return
+    print(f"会议数：{len(state.meetings)}", file=out)
+    statuses = Counter(task.status.value for task in state.tasks)
+    print(f"任务数：{len(state.tasks)}（状态分布：{dict(statuses)}）", file=out)
+    print(f"审批记录数：{len(summary.decisions)}", file=out)
+    for record in summary.decisions:
+        print(f"  - {record.type}（by {record.by_role}）", file=out)
+
+
+# ---------------------------------------------------------------------------
+# 子命令实现
+# ---------------------------------------------------------------------------
+
+
+def _cmd_run(args: argparse.Namespace) -> int:
+    """run 子命令：编译并运行流程。"""
+    out = sys.stdout
+    try:
+        summary = asyncio.run(
+            run_flow(
+                args.flow,
+                project=args.project,
+                yes=args.yes,
+                thread_id=args.thread,
+                print_event=lambda event: _print_event(event, out),
+                print_request=lambda request: _print_request(request, out),
+            )
+        )
+    except Exception as exc:  # noqa: BLE001 —— CLI 顶层统一错误出口
+        print(f"运行失败：{exc}", file=sys.stderr)
+        return 1
+    _print_summary(summary, out)
+    return 0
+
+
+def _cmd_skills_list(args: argparse.Namespace) -> int:
+    """skills list 子命令：列出技能目录。"""
+    try:
+        skills = SkillLoader().list_skills(args.root)
+    except Exception as exc:  # noqa: BLE001 —— CLI 顶层统一错误出口
+        print(f"技能列表失败：{exc}", file=sys.stderr)
+        return 1
+    print(f"共 {len(skills)} 个技能：")
+    for skill in skills:
+        print(f"  - {skill.name}@{skill.version}：{skill.description}")
+    return 0
+
+
+def _cmd_roles_list(args: argparse.Namespace) -> int:
+    """roles list 子命令：列出 12 岗位。"""
+    roles = RoleRegistry(build_role_catalog()).list()
+    print(f"共 {len(roles)} 个岗位：")
+    for role in roles:
+        scope = ",".join(gate.value for gate in role.approval_scope) or "-"
+        print(
+            f"  - {role.id}（{role.name}）| 类别：{role.kind.value} | 审批范围：{scope}"
+        )
+    return 0
+
+
+def _cmd_proposals_demo(args: argparse.Namespace) -> int:
+    """proposals demo 子命令：六步进化闭环演示。"""
+    engine = EvolutionEngine()
+    fabricated_events = [
+        Event(
+            id="ev-metric-1",
+            run_id="demo",
+            thread_id="demo",
+            type="metric_threshold",
+            actor="metric_rules",
+            payload={"source": "rework_rate", "evidence": ["rework_rate=0.45@iter=1"], "severity": "high"},
+        ),
+        Event(
+            id="ev-review-1",
+            run_id="demo",
+            thread_id="demo",
+            type="review_result",
+            actor="reviewer",
+            payload={"verdict": "reject", "target": "frontend-design"},
+        ),
+        Event(
+            id="ev-review-2",
+            run_id="demo",
+            thread_id="demo",
+            type="review_result",
+            actor="reviewer",
+            payload={"verdict": "reject", "target": "frontend-design"},
+        ),
+        Event(
+            id="ev-retro-1",
+            run_id="demo",
+            thread_id="demo",
+            type="retro",
+            actor="pm",
+            payload={"root_cause": "需求歧义导致返工"},
+        ),
+    ]
+
+    print("① 收集信号：")
+    signals = engine.collect(fabricated_events)
+    for signal in signals:
+        print(f"  - {signal.type} | severity={signal.severity} | source={signal.source}")
+    if not signals:
+        print("  未收集到信号")
+        return 0
+
+    print("② 提炼候选：")
+    candidates = engine.distill(signals)
+    for candidate in candidates:
+        print(f"  - {candidate.category} → {candidate.target}（{len(candidate.evidence)} 条证据）")
+    if not candidates:
+        print("  无可提炼候选")
+        return 0
+
+    print("③ 提案：")
+    chosen = candidates[0]
+    proposal = engine.propose(
+        chosen,
+        author_role="pm",
+        title=f"改进 {chosen.target}（{chosen.category}）",
+        rollback_plan="回滚到上一版本并恢复目录",
+        validation_plan="灰度 1 个迭代验证后再全量",
+    )
+    print(
+        f"  - {proposal.title} | 类别：{proposal.category} | 风险：{proposal.risk_level} | "
+        f"状态：{proposal.status} | 回滚方案：{proposal.rollback_plan}"
+    )
+
+    print("④ 评审：")
+    engine.review(proposal, approver="governance", decision="approve", reason="演示评审通过")
+    print(f"  - 状态：{proposal.status}（approver=governance）")
+
+    print("⑤ 生效：")
+    engine.apply(proposal)
+    print(
+        f"  - 状态：{proposal.status} | 版本：{proposal.effective_version} | "
+        f"灰度：{proposal.gray}"
+    )
+
+    print("⑥ 回滚：")
+    engine.rollback(proposal, reason="演示回滚（观察期发现回归）")
+    print(f"  - 状态：{proposal.status} | 审计事件：{len(engine.audit_events)} 条")
+    return 0
+
+
+def _cmd_metrics_demo(args: argparse.Namespace) -> int:
+    """metrics demo 子命令：度量采集 + 阈值规则信号演示。"""
+    collector = MetricsCollector()
+    print("采集度量点：")
+    points = [
+        ("review_pass_rate", 0.45, {"iteration": "iter-1"}),
+        ("rework_rate", 0.40, {"iteration": "iter-1"}),
+        ("rework_rate", 0.55, {"iteration": "iter-2"}),
+        ("action_item_close_rate", 0.60, {"iteration": "iter-2"}),
+        ("loop_iterations", 6, {"iteration": "iter-2"}),
+        ("gate_wait_seconds", 96000, {"iteration": "iter-2"}),
+    ]
+    for name, value, tags in points:
+        collector.record(name, value, tags=tags)
+        print(f"  - {name}={value}（tags={tags}）")
+
+    snapshot = collector.snapshot()
+    print(f"快照指标数：{len(snapshot.metrics)}")
+    signals = MetricRules.evaluate(snapshot)
+    print(f"触发信号数：{len(signals)}")
+    for signal in signals:
+        print(
+            f"  - {signal.type} | severity={signal.severity} | "
+            f"evidence={signal.evidence}"
+        )
+    return 0
+
+
+# ---------------------------------------------------------------------------
+# argparse 装配与入口
+# ---------------------------------------------------------------------------
+
+
+def build_parser() -> argparse.ArgumentParser:
+    """构造 CLI 参数解析器（全部子命令中文帮助）。"""
+    parser = argparse.ArgumentParser(
+        prog="agent-cluster",
+        description="多 agent 组织型全栈开发集群运行时（Python + LangGraph）",
+    )
+    subparsers = parser.add_subparsers(dest="command", required=True)
+
+    run_parser = subparsers.add_parser("run", help="编译并运行 YAML 流程（含审批交互）")
+    run_parser.add_argument("--flow", required=True, help="流程 YAML 文件路径")
+    run_parser.add_argument("--project", default=None, help="项目目录（生成项目名，缺省用流程名）")
+    run_parser.add_argument("--yes", action="store_true", help="无人值守：自动接受全部审批（bypass-immune 自动拒绝）")
+    run_parser.add_argument("--thread", default=None, help="线程 id（缺省用流程 YAML 的 thread_id）")
+    run_parser.set_defaults(func=_cmd_run)
+
+    skills_parser = subparsers.add_parser("skills", help="技能管理")
+    skills_sub = skills_parser.add_subparsers(dest="skills_command", required=True)
+    skills_list = skills_sub.add_parser("list", help="列出技能目录")
+    skills_list.add_argument("--root", required=True, help="技能根目录")
+    skills_list.set_defaults(func=_cmd_skills_list)
+
+    roles_parser = subparsers.add_parser("roles", help="岗位管理")
+    roles_sub = roles_parser.add_subparsers(dest="roles_command", required=True)
+    roles_list = roles_sub.add_parser("list", help="列出 12 岗位")
+    roles_list.set_defaults(func=_cmd_roles_list)
+
+    proposals_parser = subparsers.add_parser("proposals", help="进化提案（六步闭环演示）")
+    proposals_sub = proposals_parser.add_subparsers(dest="proposals_command", required=True)
+    proposals_demo = proposals_sub.add_parser("demo", help="进化闭环演示（收集→提炼→提案→评审→生效→回滚）")
+    proposals_demo.set_defaults(func=_cmd_proposals_demo)
+
+    metrics_parser = subparsers.add_parser("metrics", help="绩效度量")
+    metrics_sub = metrics_parser.add_subparsers(dest="metrics_command", required=True)
+    metrics_demo = metrics_sub.add_parser("demo", help="度量采集与信号触发演示")
+    metrics_demo.set_defaults(func=_cmd_metrics_demo)
+
+    return parser
+
+
+def _configure_utf8_stdio() -> None:
+    """把 stdout/stderr 重配置为 UTF-8，保证管道/重定向输出编码稳定（仓库约定 UTF-8）。"""
+    for stream in (sys.stdout, sys.stderr):
+        reconfigure = getattr(stream, "reconfigure", None)
+        if reconfigure is None:
+            continue
+        try:
+            reconfigure(encoding="utf-8")
+        except (ValueError, OSError):
+            pass
+
+
+def main(argv: Sequence[str] | None = None) -> int:
+    """CLI 入口：解析参数并分发子命令，返回 int 退出码。"""
+    _configure_utf8_stdio()
+    parser = build_parser()
+    args = parser.parse_args(argv)
+    return args.func(args)
+
+
+if __name__ == "__main__":
+    sys.exit(main())
\ No newline at end of file
diff --git a/src/agent_cluster/gates.py b/src/agent_cluster/gates.py
index 01975e4..d660042 100644
--- a/src/agent_cluster/gates.py
+++ b/src/agent_cluster/gates.py
@@ -2,14 +2,19 @@
 
 职责：
 - ``make_gate_handler``：构造注册进 ``WorkflowEngine`` 的 "gate" 节点 handler；
-  首次执行以 ``interrupt()`` 挂起等待人工审批（挂起后 ``run()`` 产出
-  ``workflow_suspended`` 事件），恢复时 ``interrupt()`` 返回 ``HumanResponse``，
+  ``auto_mode="ask"``（缺省）以 ``interrupt()`` 挂起等待人工审批（挂起后 ``run()``
+  产出 ``workflow_suspended`` 事件），恢复时 ``interrupt()`` 返回 ``HumanResponse``，
   handler 把审批结论落成 ``ApprovalRecord`` 并写入 ``gate_payloads`` / ``decisions``
-  通道（Task 3 门路由契约：``gate_payloads[node.gate].decisions[-1].type`` 驱动条件路由）。
+  通道（Task 3 门路由契约：``gate_payloads[node.gate].decisions[-1].type`` 驱动条件路由）；
+  ``auto_mode != "ask"`` 时按无人值守策略直接落 ``bypass-immune`` 结论，不挂起。
 - ``approval_pending``：从 checkpointer 读取当前挂起的审批请求（供 CLI/测试）。
 - ``resolve_auto_response``：无人值守自动审批策略（accept/reject/ask）；
   ``bypass_immune=True`` 的高风险门在无人值守 accept 时自动转为拒绝（§6.5 自动 DENY）。
 
+bypass-immune 缺省推导（Task 7 契约）：``dangerous_tool`` / ``evolution_apply``
+两类高风险门缺省 ``bypass_immune=True``（``risk_level="high"``），其余门
+``bypass_immune=False``（``risk_level="medium"``）；均可经 ``gate`` 覆盖。
+
 兼容说明（installed langgraph 1.2.11）：
 - ``interrupt()`` 以 ``__interrupt__`` 流步挂起（不抛异常），恢复时原样返回
   ``Command(resume=...)`` 的响应；因此 ``interrupt([payload])`` 的返回值可能是
@@ -40,6 +45,11 @@ __all__ = ["GateError", "make_gate_handler", "approval_pending", "resolve_auto_r
 
 AUTO_DENY_REASON = "bypass-immune: 无人值守自动拒绝"
 
+# 缺省 bypass-immune 的高风险门类别（§6.5：无人值守禁止自动放行）
+BY_PASS_IMMUNE_KINDS: frozenset[GateKind] = frozenset(
+    {GateKind.DANGEROUS_TOOL, GateKind.EVOLUTION_APPLY}
+)
+
 
 class GateError(Exception):
     """审批门配置错误（gate 节点缺少类别、无人值守模式非法等）。"""
@@ -52,43 +62,90 @@ def _now_utc() -> datetime:
 
 def make_gate_handler(
     role_scope: dict[str, GateKind] | None = None,
-    gate: ApprovalGate | None = None,
+    gate: ApprovalGate | dict[str, Any] | None = None,
+    auto_mode: str = "ask",
 ) -> NodeHandler:
     """构造 "gate" 节点 handler：interrupt 挂起 → 恢复后落审批记录并返回路由更新。
 
     参数：
     - ``role_scope``：可选的岗位审批范围映射（岗位 id -> 可审批的 GateKind）。
-      本任务仅作为治理元信息接收（Task 6/7 角色治理使用），不改变审批行为。
-    - ``gate``：可选 ``ApprovalGate`` 模型实例；提供时使用其 ``interrupt_config``
-      作为中断选项，缺省 ``HumanInterruptConfig()``（全部允许 True）。
+      仅作为治理元信息接收（Task 6/7 角色治理使用），不改变审批行为。
+    - ``gate``：可选覆盖项——``ApprovalGate`` 模型实例或 ``dict`` 覆盖映射。
+      - ``ApprovalGate``：使用其 ``interrupt_config`` 作为中断选项；若其
+        ``payload`` 显式设置了 ``bypass_immune``/``risk_level``（按 pydantic
+        ``model_fields_set`` 判断），则覆盖按门类别推导的默认值。
+      - ``dict``：键可为 ``bypass_immune``/``risk_level``/``interrupt_config``
+        （``interrupt_config`` 接受 ``HumanInterruptConfig`` 或等价 dict），
+        以及 ``kind``（提供时校验与 gate 节点类别一致）。
+    - ``auto_mode``：无人值守审批模式（"ask"/"accept"/"reject"），缺省 "ask"。
+      - ``"ask"``（缺省）：保持 interrupt() 挂起等待人工审批。
+      - 非 "ask"：不调用 interrupt()，直接按 ``resolve_auto_response`` 得出
+        ``HumanResponse`` 并落 ``ApprovalRecord(by_role="system")`` 返回通道更新，
+        无人值守运行永不挂起（§6.5：bypass-immune + accept 自动转为拒绝）。
 
     handler 从 gate 节点构造 ``ActionRequest``（id=节点 id、kind=节点 gate 类别、
-    title/description 取节点或流程规格、risk_level="medium"、bypass_immune=False），
+    title/description 取节点或流程规格；``bypass_immune`` 按门类别推导——
+    ``dangerous_tool``/``evolution_apply`` 缺省 True 且 ``risk_level="high"``，
+    其余 False 且 ``risk_level="medium"``——可用 ``gate`` 覆盖）。
     调用 ``interrupt([HumanInterrupt(...)])`` 挂起；恢复后把 ``HumanResponse``
     写成 ``ApprovalRecord(by_role="human", ...)``，返回 LangGraph channel 更新：
     ``{"gate_payloads": {node.gate: ActionRequest}, "decisions": [ApprovalRecord]}``。
     """
-    interrupt_config = gate.interrupt_config if gate is not None else HumanInterruptConfig()
+    if auto_mode not in ("ask", "accept", "reject"):
+        raise GateError(f"未知的无人值守模式：{auto_mode!r}（仅支持 accept/reject/ask）")
+
+    interrupt_config = HumanInterruptConfig()
+    overrides: dict[str, Any] = {}
+    if isinstance(gate, ApprovalGate):
+        interrupt_config = gate.interrupt_config
+        if "bypass_immune" in gate.payload.model_fields_set:
+            overrides["bypass_immune"] = gate.payload.bypass_immune
+        if "risk_level" in gate.payload.model_fields_set:
+            overrides["risk_level"] = gate.payload.risk_level
+    elif isinstance(gate, dict):
+        raw_interrupt_config = gate.get("interrupt_config")
+        if raw_interrupt_config is not None:
+            interrupt_config = HumanInterruptConfig.model_validate(raw_interrupt_config)
+        for key in ("bypass_immune", "risk_level"):
+            if key in gate:
+                overrides[key] = gate[key]
 
     async def handler(state: ClusterState, node: WorkflowNode, ctx: NodeContext) -> dict[str, Any]:
         if node.gate is None:
             raise GateError(f"gate 节点 {node.id!r} 缺少 gate 类别配置（node.gate 为 None）")
-        if gate is not None and gate.kind != node.gate:
+        if isinstance(gate, ApprovalGate) and gate.kind != node.gate:
             raise GateError(
                 f"ApprovalGate {gate.id!r} 的类别 {gate.kind!r} 与 gate 节点 {node.id!r} "
                 f"的类别 {node.gate!r} 不一致"
             )
+        if isinstance(gate, dict) and gate.get("kind") is not None and gate.get("kind") != node.gate:
+            raise GateError(
+                f"gate 覆盖配置的类别 {gate.get('kind')!r} 与 gate 节点 {node.id!r} "
+                f"的类别 {node.gate!r} 不一致"
+            )
         title = f"{node.gate.value} 审批"
         description = ctx.spec.description or f"等待人工审批：节点 {node.id}（{node.gate.value}）"
+        bypass_immune_default = node.gate in BY_PASS_IMMUNE_KINDS
+        risk_level_default = "high" if bypass_immune_default else "medium"
         request = ActionRequest(
             id=node.id,
             kind=node.gate,
             title=title,
             description=description,
             evidence={"node": node.id, "gate": node.gate.value, "run_id": ctx.run_id},
-            risk_level="medium",
-            bypass_immune=False,
+            risk_level=overrides.get("risk_level", risk_level_default),
+            bypass_immune=overrides.get("bypass_immune", bypass_immune_default),
         )
+        if auto_mode != "ask":
+            decision = resolve_auto_response(request, auto_mode)
+            record = ApprovalRecord(
+                by_role="system",
+                type=decision.type,
+                args=decision.args,
+                ts=_now_utc(),
+            )
+            request.decisions.append(record)
+            return {"gate_payloads": {node.gate: request}, "decisions": [record]}
         human_interrupt: dict[str, Any] = {
             "action_request": request,
             "config": interrupt_config.model_dump(),
diff --git a/src/agent_cluster/meetings.py b/src/agent_cluster/meetings.py
index 4ea75ad..66fc8ac 100644
--- a/src/agent_cluster/meetings.py
+++ b/src/agent_cluster/meetings.py
@@ -261,7 +261,8 @@ def make_meeting_handler(host: MeetingHost, role_registry: Any) -> NodeHandler:
     async def handler(state: ClusterState, node: WorkflowNode, ctx: NodeContext) -> dict[str, Any]:
         if node.meeting is None:
             raise ValueError(f"meeting 节点 {node.id!r} 缺少 meeting 配置（node.meeting 为 None）")
-        participants = role_registry.default_role_ids(node.meeting)
+        # 参与岗位：节点显式声明优先（用角色 id），缺省用 RoleRegistry 默认参与岗位
+        participants = node.participants or role_registry.default_role_ids(node.meeting)
         project_id = state.project.id if state.project is not None else "demo"
         iteration_id = state.iterations[0].id if state.iterations else "iter:1"
         agenda = _default_agenda(node.meeting)
diff --git a/src/agent_cluster/models.py b/src/agent_cluster/models.py
index 9ac0eda..fdee7e3 100644
--- a/src/agent_cluster/models.py
+++ b/src/agent_cluster/models.py
@@ -522,6 +522,15 @@ class Iteration(BaseModel):
     )
 
 
+def _last_ledger(current: Ledger | None, update: Ledger | None) -> Ledger | None:
+    """``ledger`` 通道 reducer：保留最后一次写入的账本。
+
+    parallel 并行子节点在同一超步并发写 ``ledger``（LangGraph 要求带 reducer 的
+    通道才能并发更新），取最后一次写入（后写者胜），顺序执行时等价于整体替换。
+    """
+    return update if update is not None else current
+
+
 class ClusterState(BaseModel):
     """LangGraph 共享状态（§5.3），list/dict 字段默认空。
 
@@ -535,7 +544,7 @@ class ClusterState(BaseModel):
     iterations: Annotated[list[Iteration], operator.add] = Field(default_factory=list, description="迭代列表")
     tasks: Annotated[list[Task], operator.add] = Field(default_factory=list, description="任务列表")
     meetings: Annotated[list[Meeting], operator.add] = Field(default_factory=list, description="会议记录列表")
-    ledger: Ledger | None = Field(default=None, description="当前任务账本")
+    ledger: Annotated[Ledger | None, _last_ledger] = Field(default=None, description="当前任务账本")
     gate_payloads: dict[GateKind, ActionRequest] = Field(default_factory=dict, description="待审批请求，按门类别索引")
     decisions: Annotated[list[ApprovalRecord], operator.add] = Field(default_factory=list, description="审批记录")
     skill_catalog: dict[str, Skill] = Field(default_factory=dict, description="技能目录：name@version -> Skill")
diff --git a/src/agent_cluster/workflow.py b/src/agent_cluster/workflow.py
index 030ae98..0d9708f 100644
--- a/src/agent_cluster/workflow.py
+++ b/src/agent_cluster/workflow.py
@@ -92,6 +92,9 @@ class WorkflowNode(BaseModel):
     id: str = Field(description="节点唯一标识")
     type: Literal["start", "end", "agent", "meeting", "gate", "parallel"] = Field(description="节点类型")
     meeting: MeetingKind | None = Field(default=None, description="meeting 节点会议类型")
+    participants: list[str] | None = Field(
+        default=None, description="meeting 节点参与岗位 id 列表（用角色 id），缺省用 RoleRegistry 默认参与岗位"
+    )
     role: str | None = Field(default=None, description="agent 节点岗位 id")
     gate: GateKind | None = Field(default=None, description="gate 节点审批门类别")
     children: list[str] | None = Field(default=None, description="parallel 节点子节点 id 列表")
@@ -247,6 +250,15 @@ class CompiledWorkflow:
         """返回底层已编译的 LangGraph StateGraph（供 Task 4/7 检查或驱动）。"""
         return self._graph
 
+    def compile_graph(self, checkpointer: Any | None = None) -> Any:
+        """公开方法：返回绑定 checkpointer 的全新编译图（等价于 run()/resume() 内部使用）。
+
+        - 供 CLI/外部在 run() 之外获得带 checkpointer 的图，从而配合
+          ``gates.approval_pending(graph, thread_id)`` 查询挂起审批。
+        - 每次调用返回全新编译实例；checkpointer 需在 compile 时绑定（LangGraph 约束）。
+        """
+        return self._compile_graph(checkpointer=checkpointer)
+
     # ------------------------------------------------------------------
     # 图构建
     # ------------------------------------------------------------------
@@ -350,6 +362,10 @@ class CompiledWorkflow:
 
     async def _execute_node(self, state: ClusterState, node: WorkflowNode) -> dict[str, Any] | None:
         run_state = self._require_run_state()
+        # LangGraph 的 Send 并行子节点传入 dict 状态，统一归一化为 ClusterState，
+        # 保证 handler 以模型实例访问 state.project/iterations/ledger 等字段。
+        if not isinstance(state, ClusterState):
+            state = ClusterState.model_validate(state)
         if node.type == "start":
             run_state.loop_count += 1
         # model_construct 跳过校验，保证 ctx.events 与本次迭代事件缓冲为同一列表引用
diff --git a/tests/test_gates.py b/tests/test_gates.py
index 4104617..48a415b 100644
--- a/tests/test_gates.py
+++ b/tests/test_gates.py
@@ -70,7 +70,7 @@ def _compile_flow(
 
 def _graph_with_checkpointer(compiled, checkpointer):
     """构造绑定 checkpointer 的已编译图（approval_pending / 读取终态需要）。"""
-    return compiled._compile_graph(checkpointer=checkpointer)
+    return compiled.compile_graph(checkpointer=checkpointer)
 
 
 def _final_state(compiled, checkpointer) -> ClusterState:
@@ -295,6 +295,129 @@ edges:
         _ = [event async for event in compiled.run()]
 
 
+async def test_bypass_immune_derived_from_gate_kind():
+    """Task 7：dangerous_tool / evolution_apply 缺省 bypass_immune=True 且 risk_level=high。"""
+    checkpointer = MemorySaver()
+    dangerous_yaml = """
+name: dangerous-gate-flow
+max_iterations: 10
+thread_id: "proj:demo:iter:1"
+nodes:
+  - {id: start, type: start}
+  - {id: tool_gate, type: gate, gate: dangerous_tool}
+  - {id: end, type: end}
+edges:
+  - {from: start, to: tool_gate}
+  - {from: tool_gate, to: end, on_accept: end, on_reject: end}
+"""
+    compiled = _compile_flow(dangerous_yaml)
+    _ = [event async for event in compiled.run(checkpointer=checkpointer)]
+    request = approval_pending(_graph_with_checkpointer(compiled, checkpointer), THREAD_ID)
+    assert request is not None
+    assert request.bypass_immune is True
+    assert request.risk_level == "high"
+
+    evolution_yaml = dangerous_yaml.replace("dangerous_tool", "evolution_apply")
+    compiled_evo = _compile_flow(evolution_yaml)
+    _ = [event async for event in compiled_evo.run(checkpointer=checkpointer)]
+    evo_request = approval_pending(_graph_with_checkpointer(compiled_evo, checkpointer), THREAD_ID)
+    assert evo_request is not None
+    assert evo_request.bypass_immune is True
+    assert evo_request.risk_level == "high"
+
+
+async def test_auto_mode_accept_plain_gate_completes_without_suspending():
+    """Task 7：auto_mode='accept' 的普通门不挂起，自动 accept 并走完流程。"""
+    checkpointer = MemorySaver()
+    handler = make_gate_handler(gate={"kind": "release"}, auto_mode="accept")
+    compiled = WorkflowEngine(handlers={"gate": handler}).compile(SIMPLE_GATE_YAML)
+
+    events = [event async for event in compiled.run(checkpointer=checkpointer)]
+    assert events[-1].type == "workflow_end"
+    assert not any(event.type == "workflow_suspended" for event in events)
+
+    state = _final_state(compiled, checkpointer)
+    assert [record.type for record in state.decisions] == ["accept"]
+    assert state.decisions[0].by_role == "system"
+    assert state.gate_payloads[GateKind.RELEASE].decisions[-1].type == "accept"
+
+
+async def test_auto_mode_accept_bypass_immune_gate_auto_rejects():
+    """Task 7：auto_mode='accept' 遇 bypass-immune 高风险门自动转为拒绝，且不挂起。"""
+    checkpointer = MemorySaver()
+    dangerous_yaml = """
+name: dangerous-gate-flow
+max_iterations: 10
+thread_id: "proj:demo:iter:1"
+nodes:
+  - {id: start, type: start}
+  - {id: tool_gate, type: gate, gate: dangerous_tool}
+  - {id: end, type: end}
+edges:
+  - {from: start, to: tool_gate}
+  - {from: tool_gate, to: end, on_accept: end, on_reject: end}
+"""
+    handler = make_gate_handler(auto_mode="accept")
+    compiled = WorkflowEngine(handlers={"gate": handler}).compile(dangerous_yaml)
+
+    events = [event async for event in compiled.run(checkpointer=checkpointer)]
+    assert events[-1].type == "workflow_end"
+    assert not any(event.type == "workflow_suspended" for event in events)
+
+    state = _final_state(compiled, checkpointer)
+    assert [record.type for record in state.decisions] == ["reject"]
+    assert state.decisions[0].by_role == "system"
+    assert state.decisions[0].args == {"reason": "bypass-immune: 无人值守自动拒绝"}
+
+
+async def test_auto_mode_reject_rejects_plain_gate():
+    """Task 7：auto_mode='reject' 一律自动拒绝且不挂起。"""
+    checkpointer = MemorySaver()
+    handler = make_gate_handler(auto_mode="reject")
+    compiled = WorkflowEngine(handlers={"gate": handler}).compile(SIMPLE_GATE_YAML)
+
+    events = [event async for event in compiled.run(checkpointer=checkpointer)]
+    assert events[-1].type == "workflow_end"
+    assert not any(event.type == "workflow_suspended" for event in events)
+
+    state = _final_state(compiled, checkpointer)
+    assert [record.type for record in state.decisions] == ["reject"]
+
+
+async def test_gate_override_dict_can_clear_bypass_immune():
+    """Task 7：dict 覆盖可将高风险门 bypass_immune 置 False，无人值守 accept 放行。"""
+    checkpointer = MemorySaver()
+    dangerous_yaml = """
+name: dangerous-gate-flow
+max_iterations: 10
+thread_id: "proj:demo:iter:1"
+nodes:
+  - {id: start, type: start}
+  - {id: tool_gate, type: gate, gate: dangerous_tool}
+  - {id: end, type: end}
+edges:
+  - {from: start, to: tool_gate}
+  - {from: tool_gate, to: end, on_accept: end, on_reject: end}
+"""
+    handler = make_gate_handler(gate={"kind": "dangerous_tool", "bypass_immune": False}, auto_mode="accept")
+    compiled = WorkflowEngine(handlers={"gate": handler}).compile(dangerous_yaml)
+    events = [event async for event in compiled.run(checkpointer=checkpointer)]
+    assert events[-1].type == "workflow_end"
+    state = _final_state(compiled, checkpointer)
+    assert [record.type for record in state.decisions] == ["accept"]
+
+
+def test_make_gate_handler_rejects_unknown_auto_mode():
+    with pytest.raises(GateError, match="未知的无人值守模式"):
+        make_gate_handler(auto_mode="maybe")
+
+
+async def test_gate_override_dict_kind_mismatch_raises():
+    handler = make_gate_handler(gate={"kind": "release"})
+    compiled = WorkflowEngine(handlers={"gate": handler}).compile(ROUTING_GATE_YAML)
+    with pytest.raises(GateError, match="不一致"):
+        _ = [event async for event in compiled.run()]
+
 async def test_gate_factory_uses_provided_interrupt_config():
     checkpointer = MemorySaver()
     gate_model = ApprovalGate(
diff --git a/tests/test_integration.py b/tests/test_integration.py
new file mode 100644
index 0000000..07a43fd
--- /dev/null
+++ b/tests/test_integration.py
@@ -0,0 +1,132 @@
+"""Task 7 集成测试：CLI 闭环（--yes 全流程）、交互审批、演示子命令与子进程冒烟。
+
+- 直接调用 ``cli.run_flow``（公开异步函数）跑 ``examples/flows/fullstack-sprint.yaml``，
+  断言事件流含全部会议/门/开发节点、终态任务可达、审批记录 ≥ 4（每门一条）、
+  流程以 ``workflow_end`` 结束且 ``--yes`` 永不挂起（无 interrupt）。
+- 直接调用 ``cli.main`` 验证 skills list / roles list / proposals demo / metrics demo
+  退出码为 0。
+- 子进程冒烟：``python -m agent_cluster --help`` 退出码 0。
+"""
+
+from __future__ import annotations
+
+import asyncio
+import subprocess
+import sys
+from pathlib import Path
+
+from agent_cluster.cli import main, run_flow
+from agent_cluster.models import GateKind, MeetingKind, TaskStatus
+
+REPO_ROOT = Path(__file__).resolve().parents[1]
+FLOW_PATH = REPO_ROOT / "examples" / "flows" / "fullstack-sprint.yaml"
+SKILLS_ROOT = REPO_ROOT / "examples" / "skills"
+
+
+def _node_starts(summary) -> list[str]:
+    """按执行顺序返回 node_start 事件的 actor 列表。"""
+    return [event.actor for event in summary.events if event.type == "node_start"]
+
+
+def test_cli_run_yes_full_flow_completes_without_hanging():
+    """--yes 全流程：事件齐全、无挂起、审批 4 条、终态任务可达。"""
+    summary = asyncio.run(run_flow(FLOW_PATH, project=str(REPO_ROOT), yes=True))
+
+    # 结束与无 interrupt
+    assert summary.events[-1].type == "workflow_end"
+    assert summary.suspended_count == 0
+    assert "workflow_suspended" not in [event.type for event in summary.events]
+
+    # 全部节点执行（含 parallel 与并行子节点）
+    expected_nodes = {
+        "start",
+        "requirement_review",
+        "requirement_gate",
+        "design",
+        "design_review",
+        "design_gate",
+        "develop_parallel",
+        "develop_frontend",
+        "develop_backend",
+        "code_review",
+        "test",
+        "iteration_gate",
+        "release",
+        "release_gate",
+        "end",
+    }
+    assert expected_nodes <= set(_node_starts(summary))
+
+    # 会议：需求评审 / 设计评审 / 代码评审
+    meetings_held = {event.actor for event in summary.events if event.type == "meeting_held"}
+    assert meetings_held == {"requirement_review", "design_review", "code_review"}
+
+    # agent 节点：design(frontend 之前)/frontend/backend/test/release
+    agent_actors = {event.actor for event in summary.events if event.type == "agent_step"}
+    assert agent_actors == {"architect", "frontend", "backend", "qa", "devops"}
+
+    # 终态
+    state = summary.state
+    assert state is not None
+    assert len(state.meetings) == 3
+    assert {meeting.kind for meeting in state.meetings} == {
+        MeetingKind.REQUIREMENT_REVIEW,
+        MeetingKind.DESIGN_REVIEW,
+        MeetingKind.CODE_REVIEW,
+    }
+
+    # 任务全部可达（状态为合法 TaskStatus）
+    assert state.tasks, "终态应包含任务"
+    assert all(task.status in set(TaskStatus) for task in state.tasks)
+    assert any(task.status == TaskStatus.DOING for task in state.tasks)  # agent 节点认领任务
+    assert any(task.status == TaskStatus.TODO for task in state.tasks)  # 会议行动项
+
+    # 审批记录：每门一条，共 4 条（decisions 通道为审计全量）
+    assert len(summary.decisions) >= 4
+    assert {record.type for record in summary.decisions} == {"accept"}
+    # gate_payloads 为「当前待审批」索引（替换语义），末门 release 应保留
+    assert GateKind.RELEASE in state.gate_payloads
+
+
+def test_cli_run_ask_mode_prompts_and_resumes():
+    """交互模式：4 次挂起、人工 accept 恢复、最终 workflow_end。"""
+    prompts = iter(["accept"] * 10)
+    summary = asyncio.run(run_flow(FLOW_PATH, yes=False, prompt=lambda _: next(prompts)))
+
+    assert summary.suspended_count == 4
+    assert summary.events[-1].type == "workflow_end"
+    assert len(summary.decisions) == 4
+    assert all(record.by_role == "human" for record in summary.decisions)
+    assert [record.type for record in summary.decisions] == ["accept"] * 4
+
+
+def test_cli_skills_list_exit_zero():
+    assert main(["skills", "list", "--root", str(SKILLS_ROOT)]) == 0
+
+
+def test_cli_roles_list_exit_zero():
+    assert main(["roles", "list"]) == 0
+
+
+def test_cli_proposals_demo_exit_zero():
+    assert main(["proposals", "demo"]) == 0
+
+
+def test_cli_metrics_demo_exit_zero():
+    assert main(["metrics", "demo"]) == 0
+
+
+def test_cli_help_via_python_module_subprocess():
+    """子进程冒烟：python -m agent_cluster --help 退出码 0。"""
+    result = subprocess.run(
+        [sys.executable, "-m", "agent_cluster", "--help"],
+        capture_output=True,
+        text=True,
+        encoding="utf-8",
+        timeout=120,
+        cwd=str(REPO_ROOT),
+    )
+    assert result.returncode == 0
+    combined = (result.stdout + result.stderr).lower()
+    assert "usage:" in combined
+    assert "run" in combined and "skills" in combined and "roles" in combined
\ No newline at end of file
```
