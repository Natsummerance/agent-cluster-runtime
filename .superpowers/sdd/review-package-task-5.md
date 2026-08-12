# Task 5 Review Package

Base: 4a07d43
Head: 485c762

## Diff stat

```
 src/agent_cluster/__init__.py |  34 ++++-
 src/agent_cluster/ledger.py   | 178 +++++++++++++++++++++++
 src/agent_cluster/meetings.py | 300 +++++++++++++++++++++++++++++++++++++++
 src/agent_cluster/roles.py    | 217 ++++++++++++++++++++++++++++
 src/agent_cluster/runtime.py  | 321 ++++++++++++++++++++++++++++++++++++++++++
 tests/test_ledger.py          | 199 ++++++++++++++++++++++++++
 tests/test_meetings.py        | 203 ++++++++++++++++++++++++++
 tests/test_roles.py           | 101 +++++++++++++
 tests/test_runtime.py         | 225 +++++++++++++++++++++++++++++
 9 files changed, 1776 insertions(+), 2 deletions(-)
```

## Full diff

```diff
diff --git a/src/agent_cluster/__init__.py b/src/agent_cluster/__init__.py
index c9cb26b..dd10837 100644
--- a/src/agent_cluster/__init__.py
+++ b/src/agent_cluster/__init__.py
@@ -1,7 +1,9 @@
 """agent_cluster — 多 agent 组织型全栈开发集群运行时（Python + LangGraph）。
 
-当前阶段提供数据模型层（models.py）与技能层（skills.py）；后续任务将逐步
-加入流程引擎、审批门、组织角色、运行时、会议、进化闭环与 CLI。
+当前阶段覆盖：数据模型层（models.py）、技能层（skills.py）、流程引擎
+（workflow.py）、审批门（gates.py）、组织角色（roles.py）、角色执行运行时
+（runtime.py）、会议（meetings.py）与账本/任务板（ledger.py）；后续任务将
+加入进化闭环、度量与 CLI。
 """
 
 from agent_cluster.models import (
@@ -56,6 +58,18 @@ from agent_cluster.gates import (
     make_gate_handler,
     resolve_auto_response,
 )
+from agent_cluster.roles import RoleRegistry, build_role_catalog
+from agent_cluster.runtime import (
+    AgentRuntime,
+    ChatModelClient,
+    ChatModelFactory,
+    DeterministicClient,
+    EventBus,
+    OpenAIClient,
+    make_agent_handler,
+)
+from agent_cluster.meetings import MeetingHost, make_meeting_handler
+from agent_cluster.ledger import BLOCKED, COLUMNS, LedgerStore, TaskBoard, TaskBoardError
 from agent_cluster.skills import (
     DisclosureLevel,
     SkillCatalog,
@@ -108,6 +122,22 @@ __all__ = [
     "Task",
     "TaskStatus",
     "Vote",
+    "AgentRuntime",
+    "ChatModelClient",
+    "ChatModelFactory",
+    "DeterministicClient",
+    "EventBus",
+    "OpenAIClient",
+    "make_agent_handler",
+    "MeetingHost",
+    "make_meeting_handler",
+    "LedgerStore",
+    "TaskBoard",
+    "TaskBoardError",
+    "COLUMNS",
+    "BLOCKED",
+    "RoleRegistry",
+    "build_role_catalog",
     "CompiledWorkflow",
     "NodeContext",
     "NodeHandler",
diff --git a/src/agent_cluster/ledger.py b/src/agent_cluster/ledger.py
new file mode 100644
index 0000000..02e4f1c
--- /dev/null
+++ b/src/agent_cluster/ledger.py
@@ -0,0 +1,178 @@
+"""账本与任务板（设计文档 §4.2 / §5.6）：LedgerStore（Magentic-One 心智）与 TaskBoard。
+
+- ``LedgerStore``：按 task_id 读写 ``Ledger``（facts/plan/progress/is_satisfied/
+  is_looping）的内存 dict 存储；后续可无缝替换为持久化实现（文档化约定：
+  存储层仅通过本类访问，不直接操作 dict）。
+  - ``get(task_id)``：不存在抛 ``KeyError``（含任务清单）。
+  - ``update(ledger)``：按 ledger.task_id 覆盖写入（upsert）。
+  - ``append_fact`` / ``append_progress``：不存在时自动建账本后追加。
+  - ``mark_satisfied`` / ``mark_looping``：不存在时自动建账本后置位。
+- ``TaskBoard``：五列（Backlog/Ready/InProgress/Review/Done）+ Blocked 标记列；
+  ``move(task_id, to)`` 校验合法流转，非法跳转抛 ``TaskBoardError``。
+  合法流转（契约）：
+  - 线性：Backlog→Ready→InProgress→Review→Done。
+  - 任意列→Blocked；Blocked→InProgress / Blocked→Ready。
+  - 同列移动视为无操作（合法）。
+  - 其余（如 Backlog→Done、Ready→Review、Blocked→Done）一律拒绝。
+  ``to_state_channels()`` 把看板列映射回 ``Task.status`` 返回 ``{"tasks": [...]}``
+  供接入 ``ClusterState.tasks``（ready 列在 TaskStatus 中无对应值，映射为 todo）。
+"""
+
+from __future__ import annotations
+
+from collections.abc import Iterable
+
+from agent_cluster.models import Ledger, ProgressEntry, Task, TaskStatus
+
+__all__ = ["TaskBoardError", "LedgerStore", "TaskBoard", "COLUMNS", "BLOCKED"]
+
+# 看板五列 + Blocked 标记列（契约：列名精确匹配，move 时大小写不敏感归一化）
+COLUMNS: tuple[str, ...] = ("Backlog", "Ready", "InProgress", "Review", "Done")
+BLOCKED: str = "Blocked"
+
+# 列名归一化表（小写 -> 规范列名）
+_COLUMN_ALIASES: dict[str, str] = {
+    "backlog": "Backlog",
+    "ready": "Ready",
+    "inprogress": "InProgress",
+    "in_progress": "InProgress",
+    "review": "Review",
+    "done": "Done",
+    "blocked": "Blocked",
+}
+
+# 列 -> TaskStatus 映射（导出通道用；ready 无对应 TaskStatus，映射为 todo）
+_COLUMN_TO_STATUS: dict[str, TaskStatus] = {
+    "Backlog": TaskStatus.TODO,
+    "Ready": TaskStatus.TODO,
+    "InProgress": TaskStatus.DOING,
+    "Review": TaskStatus.REVIEW,
+    "Done": TaskStatus.DONE,
+    "Blocked": TaskStatus.BLOCKED,
+}
+
+# 合法流转表（current -> 允许的 target 集合；同列移动恒合法）
+# 「任意列 -> Blocked」为全局规则，在 move() 内单独放行。
+_LEGAL_TRANSITIONS: dict[str, set[str]] = {
+    "Backlog": {"Ready"},
+    "Ready": {"InProgress"},
+    "InProgress": {"Review"},
+    "Review": {"Done"},
+    "Blocked": {"InProgress", "Ready"},
+}
+
+
+class TaskBoardError(Exception):
+    """任务板非法操作：任务不存在、未知列名、非法状态流转。"""
+
+
+class LedgerStore:
+    """任务账本存储（内存实现，文档化：后续可替换为持久化后端）。"""
+
+    def __init__(self) -> None:
+        self._ledgers: dict[str, Ledger] = {}
+
+    def get(self, task_id: str) -> Ledger:
+        """按任务 id 读取账本；不存在抛 KeyError（含已知任务清单）。"""
+        try:
+            return self._ledgers[task_id]
+        except KeyError:
+            raise KeyError(f"账本不存在：task_id={task_id!r}（已知任务：{sorted(self._ledgers)}）") from None
+
+    def update(self, ledger: Ledger) -> None:
+        """按 ledger.task_id 覆盖写入（upsert）。"""
+        self._ledgers[ledger.task_id] = ledger
+
+    def append_fact(self, task_id: str, fact: str) -> None:
+        """追加事实（不存在时自动建账本）。"""
+        ledger = self._get_or_create(task_id)
+        ledger.facts.append(fact)
+
+    def append_progress(self, task_id: str, entry: ProgressEntry) -> None:
+        """追加进度条目（不存在时自动建账本）。"""
+        ledger = self._get_or_create(task_id)
+        ledger.progress.append(entry)
+
+    def mark_satisfied(self, task_id: str) -> None:
+        """标记任务已满足（不存在时自动建账本）。"""
+        ledger = self._get_or_create(task_id)
+        ledger.is_satisfied = True
+
+    def mark_looping(self, task_id: str) -> None:
+        """标记任务检测到死循环（不存在时自动建账本）。"""
+        ledger = self._get_or_create(task_id)
+        ledger.is_looping = True
+
+    def _get_or_create(self, task_id: str) -> Ledger:
+        """读取账本；不存在时创建空账本并写入存储。"""
+        ledger = self._ledgers.get(task_id)
+        if ledger is None:
+            ledger = Ledger(task_id=task_id)
+            self._ledgers[task_id] = ledger
+        return ledger
+
+
+class TaskBoard:
+    """任务板：五列 + Blocked 标记列，按迭代聚合完成率。
+
+    看板列与 ``Task.status`` 相互独立（看板自行维护列），导出时经
+    ``to_state_channels()`` 映射回 ``TaskStatus``。
+    """
+
+    def __init__(self, tasks: Iterable[Task] | None = None) -> None:
+        self._tasks: dict[str, Task] = {}
+        self._columns: dict[str, str] = {}
+        for task in tasks or []:
+            self.add(task)
+
+    def add(self, task: Task) -> None:
+        """把任务加入 Backlog 列；重复 id 抛 TaskBoardError。"""
+        if task.id in self._tasks:
+            raise TaskBoardError(f"任务已存在：{task.id!r}")
+        self._tasks[task.id] = task
+        self._columns[task.id] = COLUMNS[0]
+
+    def move(self, task_id: str, to: str) -> Task:
+        """把任务移动到目标列；非法流转/未知列抛 TaskBoardError。"""
+        if task_id not in self._tasks:
+            raise TaskBoardError(f"任务不存在：{task_id!r}")
+        target = self._normalize_column(to)
+        current = self._columns[task_id]
+        if current != target:
+            # 任意列 -> Blocked 恒合法；其余必须命中合法流转表
+            legal = target == BLOCKED or target in _LEGAL_TRANSITIONS.get(current, set())
+            if not legal:
+                raise TaskBoardError(f"非法任务流转：{current} → {target}（任务 {task_id!r}）")
+        self._columns[task_id] = target
+        return self._tasks[task_id]
+
+    def by_iteration(self, iteration_id: str) -> list[Task]:
+        """返回指定迭代的任务列表（按任务 id 排序，确定性）。"""
+        return sorted(
+            (task for task in self._tasks.values() if task.iteration_id == iteration_id),
+            key=lambda task: task.id,
+        )
+
+    def completion_rate(self, iteration_id: str) -> float:
+        """返回迭代完成率：Done 列任务数 / 迭代任务总数；无任务返回 0.0。"""
+        iteration_tasks = self.by_iteration(iteration_id)
+        if not iteration_tasks:
+            return 0.0
+        done_count = sum(1 for task in iteration_tasks if self._columns.get(task.id) == "Done")
+        return done_count / len(iteration_tasks)
+
+    def to_state_channels(self) -> dict[str, list[Task]]:
+        """导出 LangGraph 通道更新：``{"tasks": [...]}``，状态按看板列映射。"""
+        tasks = [
+            task.model_copy(update={"status": _COLUMN_TO_STATUS[self._columns[task.id]]})
+            for task in self._tasks.values()
+        ]
+        return {"tasks": tasks}
+
+    @staticmethod
+    def _normalize_column(name: str) -> str:
+        """把列名归一化为规范列名（大小写不敏感）；未知列抛 TaskBoardError。"""
+        canonical = _COLUMN_ALIASES.get(name.strip().lower())
+        if canonical is None:
+            raise TaskBoardError(f"未知看板列：{name!r}（支持：{list(_COLUMN_ALIASES)}）")
+        return canonical
diff --git a/src/agent_cluster/meetings.py b/src/agent_cluster/meetings.py
new file mode 100644
index 0000000..fcc4874
--- /dev/null
+++ b/src/agent_cluster/meetings.py
@@ -0,0 +1,300 @@
+"""会议子图（设计文档 §4）：MeetingHost 生成 7 类会议纪要 + meeting 节点 handler。
+
+- ``MeetingHost.run(...)``：无 LLM 的确定性会议生成——按会议类型模板产出
+  transcript（``meeting_speech`` 消息，每个议程条目 × 每位参与者一条）、
+  decisions（每个议程条目一条，结论/负责人由议程与参与者确定性推导）、
+  minutes_id（``minutes:<kind>:<ts>``）。
+- ``MeetingHost.select_speaker(thread)``：按参与者轮转规则选下一位发言人
+  （参与者取自最近一次 run 的 participants；thread 为空返回第一位）。
+- ``make_meeting_handler(host, role_registry)``：注册进 ``WorkflowEngine`` 的
+  "meeting" 节点 handler：运行会议、写回 ``state.meetings``、把会议决策提取为
+  行动项 ``Task``（status todo，assignee 取决策 owner）、追加一条
+  ``meeting_speech`` 总结消息。
+
+meeting handler 通道契约（Task 7 CLI 依赖，勿变更）：
+- 返回 LangGraph channel 更新字典，键固定为：
+  - ``"meetings"``：``list[Meeting]``（本次会议记录）。
+  - ``"tasks"``：``list[Task]``（从会议决策提取的行动项，status=todo）。
+  - ``"messages"``：``list[Message]``（一条 ``meeting_speech`` 总结消息）。
+- 会议决策留在 ``Meeting.decisions`` 内（不写入 ``decisions`` 通道——
+  该通道是 ``list[ApprovalRecord]`` 审批记录，语义不同）；事件经 ``ctx.events``
+  追加 ``type="meeting_held"``，不占通道键。
+
+7 类会议模板（§4.1）：kickoff / requirement_review / design_review /
+daily_standup / code_review / retro / release_review。
+"""
+
+from __future__ import annotations
+
+import uuid
+from dataclasses import dataclass
+from datetime import datetime
+from typing import Any
+
+from agent_cluster.models import (
+    ClusterState,
+    Decision,
+    Event,
+    Meeting,
+    MeetingKind,
+    Message,
+    MessageType,
+    Task,
+    TaskStatus,
+)
+from agent_cluster.workflow import NodeContext, NodeHandler, WorkflowNode
+
+__all__ = ["MeetingHost", "make_meeting_handler"]
+
+
+@dataclass(frozen=True)
+class _MeetingTemplate:
+    """会议模板：发言模板 + 决策结论模板（占位符 {agenda}/{participant}/{owner}）。"""
+
+    speech: str
+    decision_conclusion: str
+    decision_reason: str
+    decision_owner: str
+
+
+# 7 类会议模板（§4.1：议程/决策门/产物）
+_TEMPLATES: dict[MeetingKind, _MeetingTemplate] = {
+    MeetingKind.KICKOFF: _MeetingTemplate(
+        speech="【启动会】{participant} 讨论议程「{agenda}」：确认范围与 MVP 基线，认领职责并识别风险。",
+        decision_conclusion="「{agenda}」已达成一致：纳入 MVP 范围基线，由 {owner} 负责落地。",
+        decision_reason="启动会范围、MVP、职责与风险达成一致（通过=范围与 MVP 冻结）。",
+        decision_owner="pm",
+    ),
+    MeetingKind.REQUIREMENT_REVIEW: _MeetingTemplate(
+        speech="【需求评审】{participant} 评审「{agenda}」：提出澄清问题，确认以 Given/When/Then 形式可测的验收标准。",
+        decision_conclusion="「{agenda}」需求澄清完成，验收标准定稿（无歧义且可测）。",
+        decision_reason="逐条评审需求并确认验收标准（通过=无歧义+可测）。",
+        decision_owner="pm",
+    ),
+    MeetingKind.DESIGN_REVIEW: _MeetingTemplate(
+        speech="【设计评审】{participant} 评审「{agenda}」：确认设计决策与接口契约，标记开放问题。",
+        decision_conclusion="「{agenda}」设计基线确认，接口契约与数据模型冻结；开放问题列入风险清单。",
+        decision_reason="设计方案覆盖需求且复杂度可控（通过=覆盖需求+复杂度可控）。",
+        decision_owner="architect",
+    ),
+    MeetingKind.DAILY_STANDUP: _MeetingTemplate(
+        speech="【站会】{participant} 同步「{agenda}」：昨日=推进该项，今日=继续该项，阻塞=无。",
+        decision_conclusion="「{agenda}」同步完成；阻塞项进入行动清单由 {owner} 跟进。",
+        decision_reason="站会仅同步不决策；阻塞清单转行动项。",
+        decision_owner="pmo",
+    ),
+    MeetingKind.CODE_REVIEW: _MeetingTemplate(
+        speech="【代码评审】{participant} 按 6 条规范（可读性/边界/性能/安全/测试/文档）评审「{agenda}」：{verdict}。",
+        decision_conclusion="「{agenda}」评审通过（LGTM）：无 P0/P1，注释完整且测试通过。",
+        decision_reason="按 6 条评审规范逐条检查通过（通过=无 P0/P1+注释完整+测试过）。",
+        decision_owner="reviewer",
+    ),
+    MeetingKind.RETRO: _MeetingTemplate(
+        speech="【复盘】{participant} 复盘「{agenda}」：进展良好=完成项达标，不足=存在返工，"
+        "根因=需求澄清不足，改进项=纳入下迭代 Backlog，进化信号=流程优化建议。",
+        decision_conclusion="「{agenda}」根因与改进项已明确：改进项进入下迭代 Backlog，"
+        "进化信号提交 evolution_apply 门。",
+        decision_reason="复盘完成率、根因分析与改进项验证（通过=改进项可量化验证）。",
+        decision_owner="pmo",
+    ),
+    MeetingKind.RELEASE_REVIEW: _MeetingTemplate(
+        speech="【发布评审】{participant} 评审「{agenda}」：验收=测试全绿，风险=已评估，"
+        "回滚预案=就绪，决策=Go。",
+        decision_conclusion="「{agenda}」验收通过，回滚预案就绪，发布决策为 Go。",
+        decision_reason="测试全绿、验收达标且发布窗口确认（通过=测试全绿+验收达标+窗口确认）。",
+        decision_owner="devops",
+    ),
+}
+
+# 各会议类型默认议程（§4.1 议程列；code_review 即 6 条评审规范）
+_DEFAULT_AGENDAS: dict[MeetingKind, list[str]] = {
+    MeetingKind.KICKOFF: ["项目愿景与目标", "范围与 MVP", "团队职责与排期", "风险识别"],
+    MeetingKind.REQUIREMENT_REVIEW: ["需求逐条澄清", "验收标准确认"],
+    MeetingKind.DESIGN_REVIEW: ["系统设计与技术选型", "API 契约与数据模型", "非功能需求"],
+    MeetingKind.DAILY_STANDUP: ["昨日进展", "今日计划", "阻塞与求助"],
+    MeetingKind.CODE_REVIEW: [
+        "代码可读性与结构",
+        "边界与错误处理",
+        "性能与复杂度",
+        "安全性",
+        "测试覆盖",
+        "文档与注释",
+    ],
+    MeetingKind.RETRO: ["迭代完成情况", "进展良好与不足", "根因分析", "改进项与进化提案"],
+    MeetingKind.RELEASE_REVIEW: ["验收与回归结果", "风险与回滚预案", "发布窗口与 Go/No-Go"],
+}
+
+
+def _default_agenda(kind: MeetingKind) -> list[str]:
+    """返回会议类型的默认议程条目。"""
+    return list(_DEFAULT_AGENDAS[kind])
+
+
+def _now_stamp() -> str:
+    """时间戳（会议 id / 纪要 id 用）。"""
+    return datetime.now().strftime("%Y%m%d%H%M%S%f")
+
+
+class MeetingHost:
+    """会议主持人：确定性生成 7 类会议纪要（无需 LLM/API key）。
+
+    ``run`` 记录 participants 供 ``select_speaker`` 轮转使用。
+    ``state`` 参数为签名契约（会议上下文，如项目/迭代信息）；当前确定性实现
+    不依赖其内容，仅透传给未来扩展。
+    """
+
+    def __init__(self) -> None:
+        self._participants: list[str] = []
+
+    async def run(
+        self,
+        kind: MeetingKind | str,
+        *,
+        agenda: list[str],
+        participants: list[str],
+        project_id: str,
+        state: Any,
+    ) -> Meeting:
+        """生成会议：transcript + decisions + minutes_id，全部确定性模板。"""
+        meeting_kind = MeetingKind(kind)
+        self._participants = list(participants)
+        template = _TEMPLATES[meeting_kind]
+        ts = _now_stamp()
+        thread_id = f"proj:{project_id}:meeting:{meeting_kind.value}"
+
+        # transcript：每个议程条目 × 每位参与者一条 meeting_speech
+        transcript: list[Message] = []
+        for item in agenda:
+            for index, participant in enumerate(participants):
+                verdict = "LBTM（需修复高优问题）" if meeting_kind == MeetingKind.CODE_REVIEW and index % 3 == 2 else "LGTM（通过）"
+                content = template.speech.format(agenda=item, participant=participant, verdict=verdict)
+                transcript.append(
+                    Message(
+                        id=uuid.uuid4().hex,
+                        thread_id=thread_id,
+                        source=participant,
+                        target="",
+                        type=MessageType.MEETING_SPEECH,
+                        payload={"content": content, "agenda": item, "meeting": meeting_kind.value},
+                    )
+                )
+
+        # decisions：每个议程条目一条，owner 由参与者轮转推导（确定性）
+        decisions: list[Decision] = []
+        for index, item in enumerate(agenda):
+            owner = participants[index % len(participants)] if participants else template.decision_owner
+            decisions.append(
+                Decision(
+                    id=uuid.uuid4().hex,
+                    topic=item,
+                    conclusion=template.decision_conclusion.format(agenda=item, owner=owner),
+                    reason=template.decision_reason,
+                    owner=owner,
+                )
+            )
+
+        return Meeting(
+            id=f"meeting:{meeting_kind.value}:{ts}",
+            project_id=project_id,
+            kind=meeting_kind,
+            agenda=list(agenda),
+            transcript=transcript,
+            decisions=decisions,
+            minutes_id=f"minutes:{meeting_kind.value}:{ts}",
+        )
+
+    async def select_speaker(self, thread: list[Message]) -> str:
+        """按参与者轮转规则选下一位发言人。
+
+        - thread 为空：返回第一位参与者。
+        - 否则取最后一条消息 source 在参与者列表中的下一位（循环）。
+        - 最近一次 run 未记录参与者或 source 不在列表中：返回第一位参与者。
+        """
+        if not self._participants:
+            return ""
+        if not thread:
+            return self._participants[0]
+        last_source = thread[-1].source
+        try:
+            index = self._participants.index(last_source)
+        except ValueError:
+            return self._participants[0]
+        return self._participants[(index + 1) % len(self._participants)]
+
+
+def make_meeting_handler(host: MeetingHost, role_registry: Any) -> NodeHandler:
+    """构造注册进 ``WorkflowEngine`` 的 "meeting" 节点 handler。
+
+    步骤：
+    1. 按 ``node.meeting`` 取默认议程与默认参与岗位（role_registry）。
+    2. ``host.run(...)`` 生成会议记录。
+    3. 会议决策提取为行动项 ``Task``（status todo，assignee=决策 owner）。
+    4. 追加一条 ``meeting_speech`` 总结消息到 messages 通道。
+    5. 经 ``ctx.events`` 追加 ``Event(type="meeting_held")``。
+
+    返回通道键（契约，勿变更）：``{"meetings", "tasks", "messages"}``。
+    """
+    async def handler(state: ClusterState, node: WorkflowNode, ctx: NodeContext) -> dict[str, Any]:
+        if node.meeting is None:
+            raise ValueError(f"meeting 节点 {node.id!r} 缺少 meeting 配置（node.meeting 为 None）")
+        participants = role_registry.default_role_ids(node.meeting)
+        project_id = state.project.id if state.project is not None else "demo"
+        iteration_id = state.iterations[0].id if state.iterations else "iter:1"
+        agenda = _default_agenda(node.meeting)
+        thread_id = ctx.spec.thread_id or "default"
+
+        meeting = await host.run(
+            node.meeting,
+            agenda=agenda,
+            participants=participants,
+            project_id=project_id,
+            state=state,
+        )
+
+        # 行动项任务：会议决策 -> Task(status=todo, assignee=决策 owner)
+        tasks: list[Task] = []
+        for decision in meeting.decisions:
+            tasks.append(
+                Task(
+                    id=uuid.uuid4().hex,
+                    project_id=project_id,
+                    iteration_id=iteration_id,
+                    title=f"{node.meeting.value} 行动项：{decision.topic}",
+                    desc=decision.conclusion,
+                    assignee_role=decision.owner,
+                    status=TaskStatus.TODO,
+                    acceptance_criteria=[decision.conclusion],
+                )
+            )
+
+        # 会议总结消息（type=meeting_speech，广播）
+        summary = Message(
+            id=uuid.uuid4().hex,
+            thread_id=thread_id,
+            source=node.meeting.value,
+            target="",
+            type=MessageType.MEETING_SPEECH,
+            payload={
+                "content": (
+                    f"{node.meeting.value} 会议结束：{len(meeting.transcript)} 条发言，"
+                    f"{len(meeting.decisions)} 项决策，纪要 {meeting.minutes_id}。"
+                ),
+                "meeting_id": meeting.id,
+                "node": ctx.node_id,
+            },
+        )
+
+        ctx.events.append(
+            Event(
+                id=uuid.uuid4().hex,
+                run_id=ctx.run_id,
+                thread_id=thread_id,
+                type="meeting_held",
+                actor=node.meeting.value,
+                payload={"meeting": meeting.id, "decisions": len(meeting.decisions), "node": ctx.node_id},
+            )
+        )
+
+        return {"meetings": [meeting], "tasks": tasks, "messages": [summary]}
+
+    return handler
diff --git a/src/agent_cluster/roles.py b/src/agent_cluster/roles.py
new file mode 100644
index 0000000..3266a5d
--- /dev/null
+++ b/src/agent_cluster/roles.py
@@ -0,0 +1,217 @@
+"""组织角色层（设计文档 §3.1）：12 岗位目录与岗位注册表。
+
+- ``build_role_catalog()`` 返回 12 个岗位的 ``Role`` 定义（pm/pmo/frontend/backend/
+  algorithm/architect/qa/devops/docs/reviewer/debugger/governance），字段对齐
+  §3.1：goal/backstory/skills/tools/approval_scope。
+- ``RoleRegistry`` 提供 ``get``/``list``/``filter_by_kind`` 与各会议类型的默认
+  参与岗位（§4.1 参与者列，Task 5 meeting handler 据此确定 participants）。
+
+RoleKind 八类与 12 岗的映射（目录内文档化契约）：
+- pm→PM、pmo→PMO、frontend→FRONTEND、backend→BACKEND、algorithm→ALGORITHM、
+  architect→ARCH、qa→QA、devops→DEVOPS；
+- 辅助/门禁四岗归入相近类别：docs→PMO（规格文档/流程辅助）、reviewer→QA、
+  debugger→QA（缺陷排查归质量保障域）、governance→PM（治理/流程 agent 归决策层）；
+- ``RoleKind.ARCH`` 对应岗位 id ``"architect"``。
+
+技能清单为 ``name@version`` 字符串：优先引用 ``examples/skills`` 中已存在的
+技能（requirement-analysis@1.0.0、backend-api-design@2.1.0），其余为按 §3.1
+技能挂载列声明的占位技能（字符串契约，允许尚未创建）。
+"""
+
+from __future__ import annotations
+
+from agent_cluster.models import GateKind, MeetingKind, Role, RoleKind
+
+__all__ = ["build_role_catalog", "RoleRegistry"]
+
+
+def build_role_catalog() -> dict[str, Role]:
+    """返回 12 岗位目录（岗位 id -> Role），按 §3.1 岗位清单构建。"""
+    roles: list[Role] = [
+        Role(
+            id="pm",
+            name="产品经理",
+            kind=RoleKind.PM,
+            goal="收集并澄清需求，输出 PRD 与可验证的验收标准，冻结需求范围。",
+            backstory="产品经理负责需求收集与澄清、竞品与市场分析、PRD 编写与验收标准定义；"
+            "属于决策层，可批准「需求范围冻结」「迭代验收」「发布」。",
+            skills=["requirement-analysis@1.0.0", "competitor-research@0.1.0", "prd-writing@0.1.0"],
+            tools=["read_file", "write_file", "review", "publish"],
+            approval_scope=[
+                GateKind.REQUIREMENT_CONFIRMATION,
+                GateKind.ITERATION_ACCEPTANCE,
+                GateKind.RELEASE,
+            ],
+        ),
+        Role(
+            id="pmo",
+            name="项目经理",
+            kind=RoleKind.PMO,
+            goal="拆分任务与依赖、制定排期、主持会议并跟踪进度与风险，关闭迭代范围与任务。",
+            backstory="项目经理（PMO / Scrum Master）负责任务拆分与依赖分析、排期、会议主持、"
+            "进度与风险跟踪；属于管理层，可批准「迭代范围与任务关闭」。",
+            skills=["task-breakdown@0.1.0", "agile-scrum@0.1.0", "meeting-facilitation@0.1.0"],
+            tools=["read_file", "write_file", "review", "publish"],
+            approval_scope=[GateKind.ITERATION_ACCEPTANCE],
+        ),
+        Role(
+            id="frontend",
+            name="前端开发工程师",
+            kind=RoleKind.FRONTEND,
+            goal="按设计稿与 API 契约实现 UI、组件与交互，并保证构建与前端测试通过。",
+            backstory="前端开发属于执行层：负责 UI 还原、前端架构与组件库、页面与交互；"
+            "可运行构建与前端测试。",
+            skills=["frontend-design@1.0.0", "webapp-testing@0.1.0"],
+            tools=["file_edit", "run_tests", "execute_code", "review", "build"],
+        ),
+        Role(
+            id="backend",
+            name="后端开发工程师",
+            kind=RoleKind.BACKEND,
+            goal="实现 API、数据模型与业务逻辑，编写测试并保证服务集成可用。",
+            backstory="后端开发属于执行层：负责 API、数据模型、业务逻辑、服务集成；"
+            "可写代码、跑测试，产出数据库脚本与接口契约。",
+            skills=["backend-api-design@2.1.0", "database-schema@0.1.0", "unit-testing@0.1.0"],
+            tools=["file_edit", "run_tests", "execute_code", "review", "build"],
+        ),
+        Role(
+            id="algorithm",
+            name="算法工程师",
+            kind=RoleKind.ALGORITHM,
+            goal="设计算法方案、处理数据、训练/推理并评估优化效果。",
+            backstory="算法工程师属于执行层：负责算法方案、数据处理、训练与推理、评估优化；"
+            "可批准「算法方案与评估标准」。",
+            skills=["ml-engineering@0.1.0", "model-evaluation@0.1.0", "data-prep@0.1.0"],
+            tools=["file_edit", "run_tests", "execute_code", "review"],
+        ),
+        Role(
+            id="architect",
+            name="架构师",
+            kind=RoleKind.ARCH,
+            goal="输出系统设计、技术选型、模块划分与接口契约，冻结架构基线。",
+            backstory="架构工程师属于管理层：负责系统设计、技术选型、模块划分、接口契约与"
+            "非功能需求；可批准「架构基线」（design_review 门）。",
+            skills=["system-design@0.1.0", "api-contract@0.1.0", "security-review@0.1.0"],
+            tools=["file_edit", "review", "run_tests", "execute_code"],
+            approval_scope=[GateKind.DESIGN_REVIEW],
+        ),
+        Role(
+            id="qa",
+            name="测试开发工程师",
+            kind=RoleKind.QA,
+            goal="编写测试计划与用例、执行自动化测试、跟踪缺陷与回归，把关质量门。",
+            backstory="测试开发（QA）属于执行层：负责测试计划/用例/自动化、缺陷与回归；"
+            "可批准「质量门」（迭代验收）。",
+            skills=["test-planning@0.1.0", "automated-testing@0.1.0", "bug-hunting@0.1.0"],
+            tools=["run_tests", "execute_code", "review", "publish"],
+            approval_scope=[GateKind.ITERATION_ACCEPTANCE],
+        ),
+        Role(
+            id="devops",
+            name="运维工程师",
+            kind=RoleKind.DEVOPS,
+            goal="搭建 CI/CD 与监控告警、执行部署与发布、处理故障恢复。",
+            backstory="运维维护（SRE）属于执行层：负责部署、CI/CD、监控告警、故障恢复与"
+            "发布执行；可批准「发布窗口」（release 门）。",
+            skills=["ci-cd@0.1.0", "deployment@0.1.0", "observability@0.1.0", "incident-response@0.1.0"],
+            tools=["deploy", "run_tests", "execute_code", "publish"],
+            approval_scope=[GateKind.RELEASE],
+        ),
+        Role(
+            id="docs",
+            name="规格文档写手",
+            kind=RoleKind.PMO,
+            goal="把 PRD 与设计转化为开发规格、API 文档与 README。",
+            backstory="规格文档写手（SpecWriter）属于辅助层：负责把 PRD 转成开发规格、"
+            "接口文档与 README，属于管理与流程辅助域。",
+            skills=["doc-writing@0.1.0", "api-docs@0.1.0"],
+            tools=["file_edit", "review", "publish"],
+        ),
+        Role(
+            id="reviewer",
+            name="代码评审员",
+            kind=RoleKind.QA,
+            goal="按评审规范逐条检查代码，输出最高优先级修改意见。",
+            backstory="代码评审员属于辅助层：按评审规范逐条检查 PR 代码，输出评审意见与"
+            "修改指令；归入质量保障域（QA 类别）。",
+            skills=["code-review@0.1.0", "best-practices@0.1.0"],
+            tools=["review", "run_tests", "execute_code"],
+        ),
+        Role(
+            id="debugger",
+            name="缺陷排查工程师",
+            kind=RoleKind.QA,
+            goal="复现缺陷、定位根因并生成修复建议，聚焦「定位」而非直接修复。",
+            backstory="缺陷排查员（Troubleshooter）属于辅助层：负责复现、根因分析与修复"
+            "建议；归入质量保障域（QA 类别）。",
+            skills=["root-cause-analysis@0.1.0", "repro-steps@0.1.0"],
+            tools=["execute_code", "run_tests", "review", "file_edit"],
+        ),
+        Role(
+            id="governance",
+            name="治理与流程 Agent",
+            kind=RoleKind.PM,
+            goal="维护流程规范与治理策略，审计变更并批准进化提案生效。",
+            backstory="治理与流程 Agent 属于决策层：负责流程规范、治理策略与审计，"
+            "可批准「进化生效」（evolution_apply 门）；归入决策层（PM 类别）。",
+            skills=["process-governance@0.1.0", "audit-log@0.1.0", "policy-review@0.1.0"],
+            tools=["review", "publish", "deploy"],
+            approval_scope=[GateKind.EVOLUTION_APPLY],
+        ),
+    ]
+    return {role.id: role for role in roles}
+
+
+class RoleRegistry:
+    """岗位注册表：按岗位 id 查询/列举/按类别过滤，并提供会议默认参与岗位。
+
+    - ``get(role_id)``：不存在时抛 ``KeyError``（消息含可用岗位清单）。
+    - ``list()``：按岗位 id 排序返回全部岗位。
+    - ``filter_by_kind(kind)``：返回指定 ``RoleKind`` 的岗位列表。
+    - ``default_role_ids(meeting_kind)``：返回某类会议的默认参与岗位 id
+      （§4.1 参与者列），供 meeting handler 使用。
+    """
+
+    # §4.1 各会议类型的默认参与岗位
+    _MEETING_PARTICIPANTS: dict[MeetingKind, list[str]] = {
+        MeetingKind.KICKOFF: [
+            "pm", "pmo", "frontend", "backend", "algorithm", "architect",
+            "qa", "devops", "docs", "reviewer", "debugger", "governance",
+        ],
+        MeetingKind.REQUIREMENT_REVIEW: ["pm", "architect", "frontend", "backend", "algorithm", "qa"],
+        MeetingKind.DESIGN_REVIEW: ["architect", "pmo", "frontend", "backend", "qa", "devops"],
+        MeetingKind.DAILY_STANDUP: [
+            "pm", "pmo", "frontend", "backend", "algorithm", "qa",
+            "devops", "docs", "reviewer", "debugger",
+        ],
+        MeetingKind.CODE_REVIEW: ["frontend", "backend", "reviewer"],
+        MeetingKind.RETRO: [
+            "pm", "pmo", "frontend", "backend", "algorithm", "architect",
+            "qa", "devops", "docs", "reviewer", "debugger", "governance",
+        ],
+        MeetingKind.RELEASE_REVIEW: ["pm", "architect", "qa", "devops", "frontend", "backend"],
+    }
+
+    def __init__(self, roles: dict[str, Role] | None = None) -> None:
+        """使用给定目录；缺省使用 ``build_role_catalog()``。"""
+        self._roles: dict[str, Role] = dict(roles) if roles is not None else build_role_catalog()
+
+    def get(self, role_id: str) -> Role:
+        """按岗位 id 查询；不存在时抛 KeyError（含可用岗位清单）。"""
+        try:
+            return self._roles[role_id]
+        except KeyError:
+            raise KeyError(f"未注册岗位：{role_id!r}（可用岗位：{sorted(self._roles)}）") from None
+
+    def list(self) -> list[Role]:
+        """按岗位 id 排序返回全部岗位。"""
+        return [self._roles[role_id] for role_id in sorted(self._roles)]
+
+    def filter_by_kind(self, kind: RoleKind) -> list[Role]:
+        """返回指定 ``RoleKind`` 的岗位列表（按岗位 id 排序）。"""
+        return [role for role in self.list() if role.kind == kind]
+
+    def default_role_ids(self, meeting_kind: MeetingKind | str) -> list[str]:
+        """返回某类会议（§4.1）的默认参与岗位 id 列表。"""
+        kind = MeetingKind(meeting_kind)
+        return list(self._MEETING_PARTICIPANTS[kind])
diff --git a/src/agent_cluster/runtime.py b/src/agent_cluster/runtime.py
new file mode 100644
index 0000000..0024653
--- /dev/null
+++ b/src/agent_cluster/runtime.py
@@ -0,0 +1,321 @@
+"""角色执行层（设计文档 §5.1）：可插拔 ChatModelClient、AgentRuntime、EventBus 与 agent 节点 handler。
+
+组件：
+- ``ChatModelClient``：统一 ``async complete(messages) -> str`` 抽象（多供应商 + fallback）。
+- ``DeterministicClient``：默认确定性后端——按消息内容与 persona 生成规则回复，
+  同一输入恒得同一输出，无需 API key，用于测试与演示。
+- ``OpenAIClient``：可选 OpenAI ``chat.completions`` 实现；构造时若环境变量
+  ``OPENAI_API_KEY`` 缺失立即抛 ``RuntimeError``（构造期检查），
+  ``openai`` 包未安装时在 ``complete()`` 内抛清晰错误，确保测试永不崩溃。
+- ``ChatModelFactory``：按 ``AgentConfig`` 的 ``model.model_name`` 选择后端；
+  缺省/``deterministic`` -> ``DeterministicClient``，``openai``/``gpt-*`` -> ``OpenAIClient``，
+  其他未知名称抛 ``ValueError``。
+- ``EventBus``：append-only 事件列表：``publish(event)`` 追加，
+  ``query(thread_id=..., type=...)`` 过滤查询（可选条件）。
+- ``AgentRuntime``：``reply(agent, messages)`` 经模型客户端产出 ``Message(text)`` 并
+  发布 ``agent_reply`` 事件；``observe(agent, messages)`` 把观察到的消息摘要写入
+  ``agent.state``（``AgentState.messages`` 记忆，按 ``context.max_messages`` 截断）。
+- ``make_agent_handler(runtime, role_registry, catalog=None)``：注册进
+  ``WorkflowEngine`` 的 "agent" 节点 handler，执行确定性岗位步骤。
+
+agent handler 通道契约（Task 7 CLI 依赖，勿变更）：
+- 返回 LangGraph channel 更新字典，键固定为：
+  - ``"tasks"``：``list[Task]``（该节点执行的任务，状态=doing；每个 agent 节点
+    新建一个任务，表达 todo→doing 的认领语义）。
+  - ``"messages"``：``list[Message]``（一条 ``text`` 消息，source=岗位 id）。
+  - ``"ledger"``：``Ledger``（当前任务账本，追加一条 ``ProgressEntry``；替换
+    ``state.ledger`` 通道，语义为「当前任务账本」）。
+- 事件不占通道键：通过 ``ctx.events`` 追加 ``type="agent_step"`` 的 ``Event``。
+- 为何每次新建任务：``ClusterState.tasks`` 使用 ``operator.add`` 追加 reducer，
+  若复用通道中已存在的任务对象并回写，会再次追加造成重复；因此每个 agent 节点
+  恒定创建一个新任务（meeting 行动项作为 todo 留在通道，构成待办 backlog）。
+"""
+
+from __future__ import annotations
+
+import os
+import uuid
+from abc import ABC, abstractmethod
+from typing import Any
+
+from agent_cluster.models import (
+    Agent,
+    AgentConfig,
+    ClusterState,
+    Event,
+    Ledger,
+    Message,
+    MessageType,
+    ModelConfig,
+    ProgressEntry,
+    Role,
+    Task,
+    TaskStatus,
+)
+from agent_cluster.workflow import NodeContext, NodeHandler, WorkflowNode
+
+__all__ = [
+    "ChatModelClient",
+    "DeterministicClient",
+    "OpenAIClient",
+    "ChatModelFactory",
+    "EventBus",
+    "AgentRuntime",
+    "make_agent_handler",
+]
+
+
+class ChatModelClient(ABC):
+    """模型接入抽象：统一 ``complete(messages) -> str`` 异步接口。"""
+
+    @abstractmethod
+    async def complete(self, messages: list[dict]) -> str:
+        """按消息列表（含 role/content）生成回复文本。"""
+
+
+class DeterministicClient(ChatModelClient):
+    """确定性后端：按消息内容与 persona 规则生成回复，无外部依赖。
+
+    规则：空消息 -> persona 就绪语；否则回显最后一条消息内容并声明按确定性
+    规则处理。同一输入恒得同一输出。
+    """
+
+    def __init__(self, persona: str = "确定性助手") -> None:
+        self.persona = persona
+
+    async def complete(self, messages: list[dict]) -> str:
+        """返回基于最后一条消息内容的确定性回复。"""
+        if not messages:
+            return f"{self.persona}：收到空消息，准备就绪。"
+        content = str(messages[-1].get("content", "")).strip()
+        if not content:
+            return f"{self.persona}：已确认消息序列（{len(messages)} 条），无待处理内容。"
+        return f"{self.persona}：已收到「{content}」，按确定性规则完成处理。"
+
+
+class OpenAIClient(ChatModelClient):
+    """可选 OpenAI 后端：``chat.completions`` 实现。
+
+    - 构造期检查：环境变量（缺省 ``OPENAI_API_KEY``）缺失立即抛 ``RuntimeError``，
+      避免运行时才发现缺 key；无 API key 环境请改用 ``DeterministicClient``。
+    - ``openai`` 包未安装时，``complete()`` 抛清晰 ``RuntimeError``（测试不依赖）。
+    """
+
+    def __init__(
+        self,
+        model: str = "gpt-4o-mini",
+        api_key_env: str = "OPENAI_API_KEY",
+        api_base: str | None = None,
+    ) -> None:
+        api_key = os.environ.get(api_key_env, "")
+        if not api_key:
+            raise RuntimeError(
+                f"OpenAIClient 需要环境变量 {api_key_env}（当前未设置）；"
+                "无 API key 环境请使用 DeterministicClient。"
+            )
+        self.model = model
+        self.api_key_env = api_key_env
+        self.api_base = api_base
+        self._api_key = api_key
+
+    async def complete(self, messages: list[dict]) -> str:
+        """调用 OpenAI chat.completions 并返回首个回复文本。"""
+        try:
+            import openai
+        except ImportError as exc:
+            raise RuntimeError(
+                "OpenAIClient 需要安装 openai 包（uv add openai）；未安装时请使用 DeterministicClient。"
+            ) from exc
+        client = openai.OpenAI(api_key=self._api_key, base_url=self.api_base)
+        response = client.chat.completions.create(model=self.model, messages=messages)
+        return response.choices[0].message.content or ""
+
+
+class ChatModelFactory:
+    """按 ``AgentConfig`` 选择模型后端。
+
+    - ``create(None)`` / ``model_name`` 为空或 ``"deterministic"`` -> ``DeterministicClient``。
+    - ``model_name`` 以 ``gpt-``/``o1``/``o3`` 开头或等于 ``"openai"`` -> ``OpenAIClient``。
+    - 其他未知 ``model_name`` 抛 ``ValueError``（明确提示改用 deterministic）。
+    """
+
+    def create(self, config: AgentConfig | dict | None = None) -> ChatModelClient:
+        """构造模型客户端；缺省返回 ``DeterministicClient``。"""
+        if config is None:
+            return DeterministicClient()
+        cfg = config if isinstance(config, AgentConfig) else AgentConfig.model_validate(config)
+        model_name = (cfg.model.model_name or "").strip().lower()
+        if not model_name or model_name == "deterministic":
+            return DeterministicClient()
+        if model_name == "openai" or model_name.startswith(("gpt-", "o1", "o3")):
+            return OpenAIClient(
+                model=cfg.model.model_name,
+                api_key_env=cfg.model.api_key_env or "OPENAI_API_KEY",
+                api_base=cfg.model.api_base,
+            )
+        raise ValueError(
+            f"未知模型名称：{cfg.model.model_name!r}（支持 deterministic / openai / gpt-*）；"
+            "无 API key 环境请使用 deterministic。"
+        )
+
+
+class EventBus:
+    """append-only 事件总线：``publish`` 追加，``query`` 按条件过滤查询。"""
+
+    def __init__(self, events: list[Event] | None = None) -> None:
+        self._events: list[Event] = list(events or [])
+
+    def publish(self, event: Event) -> None:
+        """追加一条事件（append-only，不提供删除/修改）。"""
+        self._events.append(event)
+
+    def query(self, *, thread_id: str | None = None, type: str | None = None) -> list[Event]:
+        """按 thread_id / type 过滤查询（可选条件，均缺省返回全部）。"""
+        results = list(self._events)
+        if thread_id is not None:
+            results = [event for event in results if event.thread_id == thread_id]
+        if type is not None:
+            results = [event for event in results if event.type == type]
+        return results
+
+    @property
+    def events(self) -> list[Event]:
+        """返回事件列表快照（不可变拷贝）。"""
+        return list(self._events)
+
+
+class AgentRuntime:
+    """岗位 Agent 运行时：统一 ``reply`` / ``observe`` 异步接口 + 事件总线。"""
+
+    def __init__(
+        self,
+        model_factory: ChatModelFactory | None = None,
+        event_bus: EventBus | None = None,
+    ) -> None:
+        self._model_factory = model_factory if model_factory is not None else ChatModelFactory()
+        self.event_bus = event_bus if event_bus is not None else EventBus()
+
+    async def reply(self, agent: Agent, messages: list[Message]) -> Message:
+        """调用 Agent 的模型客户端，产出 ``Message(text)`` 并发布 ``agent_reply`` 事件。
+
+        - thread_id 取最后一条消息的 thread_id，缺省用 agent.id。
+        - 确定性客户端恒返回 ``MessageType.TEXT``；若未来模型决策 handoff，
+          由客户端约定（本任务确定性后端不产出 handoff）。
+        """
+        client = self._model_factory.create(agent.config)
+        thread_id = messages[-1].thread_id if messages else agent.id
+        model_messages: list[dict] = [{"role": "system", "content": agent.system_prompt}]
+        for message in messages:
+            content = message.payload.get("content") or message.payload.get("text") or ""
+            model_messages.append({"role": "user", "content": str(content)})
+        content = await client.complete(model_messages)
+        reply_message = Message(
+            id=uuid.uuid4().hex,
+            thread_id=thread_id,
+            source=agent.id,
+            target="",
+            type=MessageType.TEXT,
+            payload={"content": content},
+        )
+        self.event_bus.publish(
+            Event(
+                id=uuid.uuid4().hex,
+                run_id=agent.id,
+                thread_id=thread_id,
+                type="agent_reply",
+                actor=agent.id,
+                payload={"message_id": reply_message.id},
+            )
+        )
+        return reply_message
+
+    async def observe(self, agent: Agent, messages: list[Message]) -> None:
+        """把观察到的消息写入 ``agent.state`` 记忆（摘要=消息本身），按上限截断。"""
+        max_messages = agent.config.context.max_messages
+        merged = list(agent.state.messages) + list(messages)
+        agent.state.messages = merged[-max_messages:]
+
+
+def _model_messages_for_task(role: Role, task: Task) -> list[dict]:
+    """构造 deterministic 模型输入：角色画像 + 任务上下文。"""
+    return [
+        {"role": "system", "content": f"{role.name}：{role.goal}"},
+        {"role": "user", "content": f"执行任务 {task.id}：{task.title}（{task.desc}）"},
+    ]
+
+
+def make_agent_handler(
+    runtime: AgentRuntime,
+    role_registry: Any,
+    catalog: Any = None,
+) -> NodeHandler:
+    """构造注册进 ``WorkflowEngine`` 的 "agent" 节点 handler（确定性岗位步骤）。
+
+    步骤（对每个 agent 节点）：
+    1. 按 ``node.role`` 从 ``role_registry`` 加载 ``Role``。
+    2. 新建 ``Task``（status=doing，表达 todo→doing 认领；见模块 docstring
+       关于追加 reducer 的说明，不做复用以免通道重复）。
+    3. 用确定性模型产出执行摘要文本，追加 ``Message(type=text)``。
+    4. 经 ``ctx.events`` 追加 ``Event(type="agent_step", actor=role.id)``。
+    5. 更新当前任务账本（``Ledger``）追加 ``ProgressEntry``。
+
+    返回通道键（契约，勿变更）：``{"tasks", "messages", "ledger"}``。
+    ``catalog``（SkillCatalog）预留参数：本任务不参与执行逻辑，仅为签名契约。
+    """
+    async def handler(state: ClusterState, node: WorkflowNode, ctx: NodeContext) -> dict[str, Any]:
+        if node.role is None:
+            raise ValueError(f"agent 节点 {node.id!r} 缺少 role 配置（node.role 为 None）")
+        role = role_registry.get(node.role)
+        project_id = state.project.id if state.project is not None else "demo"
+        iteration_id = state.iterations[0].id if state.iterations else "iter:1"
+        thread_id = ctx.spec.thread_id or "default"
+
+        # 1) 新建任务（status=doing，todo→doing 认领语义）
+        task = Task(
+            id=uuid.uuid4().hex,
+            project_id=project_id,
+            iteration_id=iteration_id,
+            title=f"节点 {ctx.node_id}（{role.name}）",
+            desc=role.goal,
+            assignee_role=role.id,
+            status=TaskStatus.DOING,
+        )
+
+        # 2) 经运行时模型工厂产出确定性执行摘要（role.model 缺省走 deterministic）
+        client = runtime._model_factory.create(
+            AgentConfig(model=ModelConfig(model_name=role.model or "deterministic"))
+        )
+        content = await client.complete(_model_messages_for_task(role, task))
+        output = f"{role.name} 完成节点 {ctx.node_id} 的执行：{content}"
+
+        # 3) 追加 text 消息
+        message = Message(
+            id=uuid.uuid4().hex,
+            thread_id=thread_id,
+            source=role.id,
+            target="",
+            type=MessageType.TEXT,
+            payload={"content": output, "node": ctx.node_id, "task": task.id},
+        )
+
+        # 4) 追加 agent_step 事件（走 ctx.events，不占通道键）
+        ctx.events.append(
+            Event(
+                id=uuid.uuid4().hex,
+                run_id=ctx.run_id,
+                thread_id=thread_id,
+                type="agent_step",
+                actor=role.id,
+                payload={"task": task.id, "output": output, "node": ctx.node_id},
+            )
+        )
+
+        # 5) 更新当前任务账本
+        ledger = state.ledger if state.ledger is not None and state.ledger.task_id == task.id else Ledger(task_id=task.id)
+        ledger.progress.append(
+            ProgressEntry(role=role.id, status="doing", verdict="ok", next_action="review")
+        )
+
+        return {"tasks": [task], "messages": [message], "ledger": ledger}
+
+    return handler
diff --git a/tests/test_ledger.py b/tests/test_ledger.py
new file mode 100644
index 0000000..8373148
--- /dev/null
+++ b/tests/test_ledger.py
@@ -0,0 +1,199 @@
+"""Task 5 行为测试：LedgerStore 账本读写 + TaskBoard 合法/非法流转与完成率。"""
+
+from __future__ import annotations
+
+import pytest
+
+from agent_cluster.ledger import BLOCKED, COLUMNS, LedgerStore, TaskBoard, TaskBoardError
+from agent_cluster.models import Ledger, ProgressEntry, Task, TaskStatus
+
+
+# ---------------------------------------------------------------------------
+# LedgerStore
+# ---------------------------------------------------------------------------
+
+
+def test_get_missing_raises_key_error():
+    store = LedgerStore()
+    with pytest.raises(KeyError, match="task-1"):
+        store.get("task-1")
+
+
+def test_append_fact_and_get():
+    store = LedgerStore()
+    store.append_fact("task-1", "需求已澄清")
+    ledger = store.get("task-1")
+    assert ledger.task_id == "task-1"
+    assert ledger.facts == ["需求已澄清"]
+    assert ledger.progress == []
+    assert ledger.is_satisfied is False
+    assert ledger.is_looping is False
+
+
+def test_append_progress_and_update_upsert():
+    store = LedgerStore()
+    store.append_progress("task-1", ProgressEntry(role="architect", status="doing", verdict="ok", next_action="review"))
+    entry = store.get("task-1").progress[-1]
+    assert entry.role == "architect"
+    assert entry.next_action == "review"
+
+    # update 覆盖写入（upsert）
+    replaced = Ledger(task_id="task-1", facts=["新事实"], plan=["步骤 1"])
+    store.update(replaced)
+    assert store.get("task-1").facts == ["新事实"]
+    assert store.get("task-1").plan == ["步骤 1"]
+
+
+def test_mark_satisfied_and_mark_looping():
+    store = LedgerStore()
+    store.mark_satisfied("task-1")
+    store.mark_looping("task-1")
+    ledger = store.get("task-1")
+    assert ledger.is_satisfied is True
+    assert ledger.is_looping is True
+
+
+# ---------------------------------------------------------------------------
+# TaskBoard
+# ---------------------------------------------------------------------------
+
+
+def _task(task_id: str, iteration_id: str = "iter-1") -> Task:
+    return Task(
+        id=task_id,
+        project_id="proj1",
+        iteration_id=iteration_id,
+        title=f"任务 {task_id}",
+        desc="描述",
+        assignee_role="backend",
+    )
+
+
+def test_add_defaults_to_backlog():
+    board = TaskBoard()
+    board.add(_task("t1"))
+    channels = board.to_state_channels()
+    assert channels == {"tasks": [board.to_state_channels()["tasks"][0]]}
+    assert channels["tasks"][0].id == "t1"
+    assert channels["tasks"][0].status == TaskStatus.TODO  # Backlog -> todo
+
+
+def test_legal_linear_transitions():
+    board = TaskBoard()
+    board.add(_task("t1"))
+    board.move("t1", "Ready")
+    board.move("t1", "InProgress")
+    board.move("t1", "Review")
+    board.move("t1", "Done")
+    assert board.completion_rate("iter-1") == 1.0
+    assert board.to_state_channels()["tasks"][0].status == TaskStatus.DONE
+
+
+def test_any_to_blocked_and_back():
+    board = TaskBoard()
+    board.add(_task("t1"))
+    board.move("t1", "Ready")
+    board.move("t1", "InProgress")
+    board.move("t1", "Blocked")
+    assert board.to_state_channels()["tasks"][0].status == TaskStatus.BLOCKED
+    board.move("t1", "InProgress")  # Blocked -> InProgress
+    board.move("t1", "Blocked")
+    board.move("t1", "Ready")  # Blocked -> Ready
+    assert board.to_state_channels()["tasks"][0].status == TaskStatus.TODO
+
+
+def test_illegal_transitions_raise():
+    board = TaskBoard()
+    board.add(_task("t1"))
+    with pytest.raises(TaskBoardError, match="非法任务流转"):
+        board.move("t1", "Done")  # Backlog -> Done 跳转
+    with pytest.raises(TaskBoardError, match="非法任务流转"):
+        board.move("t1", "Review")  # Backlog -> Review 跳转
+    board.move("t1", "Ready")
+    with pytest.raises(TaskBoardError, match="非法任务流转"):
+        board.move("t1", "Review")  # Ready -> Review 跳转
+    board.move("t1", "Blocked")
+    with pytest.raises(TaskBoardError, match="非法任务流转"):
+        board.move("t1", "Done")  # Blocked -> Done 非法
+
+
+def test_move_unknown_task_raises():
+    board = TaskBoard()
+    with pytest.raises(TaskBoardError, match="任务不存在"):
+        board.move("ghost", "Done")
+
+
+def test_move_unknown_column_raises():
+    board = TaskBoard()
+    board.add(_task("t1"))
+    with pytest.raises(TaskBoardError, match="未知看板列"):
+        board.move("t1", "Shipped")
+
+
+def test_move_case_insensitive_column():
+    board = TaskBoard()
+    board.add(_task("t1"))
+    board.move("t1", "ready")
+    board.move("t1", "in_progress")
+    board.move("t1", "review")
+    board.move("t1", "DONE")
+    assert board.completion_rate("iter-1") == 1.0
+
+
+def test_duplicate_add_raises():
+    board = TaskBoard()
+    board.add(_task("t1"))
+    with pytest.raises(TaskBoardError, match="任务已存在"):
+        board.add(_task("t1"))
+
+
+def test_by_iteration_filters():
+    board = TaskBoard()
+    board.add(_task("t1", "iter-1"))
+    board.add(_task("t2", "iter-1"))
+    board.add(_task("t3", "iter-2"))
+    assert [task.id for task in board.by_iteration("iter-1")] == ["t1", "t2"]
+    assert [task.id for task in board.by_iteration("iter-2")] == ["t3"]
+    assert board.by_iteration("iter-3") == []
+
+
+def test_completion_rate_math():
+    board = TaskBoard()
+    board.add(_task("t1", "iter-1"))
+    board.add(_task("t2", "iter-1"))
+    board.add(_task("t3", "iter-1"))
+    board.add(_task("t4", "iter-1"))
+    board.move("t1", "Ready")
+    board.move("t1", "InProgress")
+    board.move("t1", "Review")
+    board.move("t1", "Done")
+    board.move("t2", "Blocked")  # 阻塞不算完成
+    board.move("t3", "Ready")
+    board.move("t3", "InProgress")
+    assert board.completion_rate("iter-1") == 0.25  # 1/4
+    assert board.completion_rate("iter-9") == 0.0  # 空迭代
+
+
+def test_to_state_channels_maps_columns_to_statuses():
+    board = TaskBoard()
+    board.add(_task("t1"))
+    board.move("t1", "Ready")
+    board.add(_task("t2"))
+    board.move("t2", "Ready")
+    board.move("t2", "InProgress")
+    board.add(_task("t3"))
+    board.move("t3", "Ready")
+    board.move("t3", "InProgress")
+    board.move("t3", "Review")
+    board.add(_task("t4"))
+    board.move("t4", "Ready")
+    board.move("t4", "InProgress")
+    board.move("t4", "Review")
+    board.move("t4", "Done")
+    statuses = {task.id: task.status for task in board.to_state_channels()["tasks"]}
+    assert statuses == {
+        "t1": TaskStatus.TODO,  # Ready 无对应 TaskStatus，映射为 todo
+        "t2": TaskStatus.DOING,
+        "t3": TaskStatus.REVIEW,
+        "t4": TaskStatus.DONE,
+    }
diff --git a/tests/test_meetings.py b/tests/test_meetings.py
new file mode 100644
index 0000000..8bcd02b
--- /dev/null
+++ b/tests/test_meetings.py
@@ -0,0 +1,203 @@
+"""Task 5 行为测试：MeetingHost 7 类会议模板 + meeting 节点 handler 契约。"""
+
+from __future__ import annotations
+
+import pytest
+
+from agent_cluster.meetings import MeetingHost, make_meeting_handler
+from agent_cluster.models import (
+    ClusterState,
+    Iteration,
+    MeetingKind,
+    MessageType,
+    Project,
+    TaskStatus,
+)
+from agent_cluster.roles import RoleRegistry
+from agent_cluster.workflow import NodeContext, WorkflowEdge, WorkflowNode, WorkflowSpec
+
+ALL_KINDS = [
+    MeetingKind.KICKOFF,
+    MeetingKind.REQUIREMENT_REVIEW,
+    MeetingKind.DESIGN_REVIEW,
+    MeetingKind.DAILY_STANDUP,
+    MeetingKind.CODE_REVIEW,
+    MeetingKind.RETRO,
+    MeetingKind.RELEASE_REVIEW,
+]
+
+
+# ---------------------------------------------------------------------------
+# MeetingHost.run：7 类会议模板
+# ---------------------------------------------------------------------------
+
+
+@pytest.mark.parametrize("kind", ALL_KINDS)
+async def test_run_produces_meeting_with_transcript_decisions_and_minutes(kind):
+    host = MeetingHost()
+    participants = ["pm", "architect", "backend"]
+    agenda = ["议程一", "议程二"]
+    meeting = await host.run(
+        kind,
+        agenda=agenda,
+        participants=participants,
+        project_id="proj1",
+        state=None,
+    )
+
+    assert meeting.kind == kind
+    assert meeting.project_id == "proj1"
+    assert meeting.agenda == agenda
+    assert meeting.id.startswith("meeting:")
+    assert meeting.minutes_id.startswith(f"minutes:{kind.value}:")
+
+    # transcript：每个议程条目 × 每位参与者一条 meeting_speech
+    assert len(meeting.transcript) == len(agenda) * len(participants)
+    for message in meeting.transcript:
+        assert message.type == MessageType.MEETING_SPEECH
+        assert message.source in participants
+        assert message.payload["meeting"] == kind.value
+
+    # decisions：每个议程条目一条，topic/conclusion/owner 齐全
+    assert len(meeting.decisions) == len(agenda)
+    for decision in meeting.decisions:
+        assert decision.topic in agenda
+        assert decision.conclusion
+        assert decision.reason
+        assert decision.owner in participants
+
+
+@pytest.mark.parametrize("kind", ALL_KINDS)
+async def test_run_is_deterministic(kind):
+    host = MeetingHost()
+    kwargs = dict(
+        agenda=["议程一"],
+        participants=["pm", "qa"],
+        project_id="proj1",
+        state=None,
+    )
+    first = await host.run(kind, **kwargs)
+    second = await host.run(kind, **kwargs)
+    assert [msg.payload["content"] for msg in first.transcript] == [
+        msg.payload["content"] for msg in second.transcript
+    ]
+    assert [decision.conclusion for decision in first.decisions] == [
+        decision.conclusion for decision in second.decisions
+    ]
+
+
+async def test_code_review_transcript_exercises_lgtm_and_lbtm_verdicts():
+    host = MeetingHost()
+    meeting = await host.run(
+        MeetingKind.CODE_REVIEW,
+        agenda=["代码可读性与结构"],
+        participants=["backend", "frontend", "reviewer"],
+        project_id="proj1",
+        state=None,
+    )
+    contents = [message.payload["content"] for message in meeting.transcript]
+    assert any("LGTM" in content for content in contents)
+    assert any("LBTM" in content for content in contents)
+
+
+async def test_select_speaker_round_robin():
+    host = MeetingHost()
+    await host.run(
+        MeetingKind.DAILY_STANDUP,
+        agenda=["昨日进展"],
+        participants=["pm", "backend", "qa"],
+        project_id="proj1",
+        state=None,
+    )
+    from agent_cluster.models import Message
+
+    thread: list[Message] = []
+    assert await host.select_speaker(thread) == "pm"
+    thread.append(Message(id="m1", thread_id="t", source="pm", target="", type=MessageType.MEETING_SPEECH))
+    assert await host.select_speaker(thread) == "backend"
+    thread.append(Message(id="m2", thread_id="t", source="backend", target="", type=MessageType.MEETING_SPEECH))
+    assert await host.select_speaker(thread) == "qa"
+    thread.append(Message(id="m3", thread_id="t", source="qa", target="", type=MessageType.MEETING_SPEECH))
+    assert await host.select_speaker(thread) == "pm"  # 轮转回到第一位
+
+
+# ---------------------------------------------------------------------------
+# make_meeting_handler：meeting 节点 handler 契约
+# ---------------------------------------------------------------------------
+
+
+def _make_context(node: WorkflowNode) -> NodeContext:
+    spec = WorkflowSpec(
+        name="t5-meeting",
+        max_iterations=4,
+        thread_id="proj:demo:iter:1",
+        nodes=[
+            WorkflowNode(id="start", type="start"),
+            node,
+            WorkflowNode(id="end", type="end"),
+        ],
+        edges=[
+            WorkflowEdge(from_="start", to=node.id),
+            WorkflowEdge(from_=node.id, to="end"),
+        ],
+    )
+    return NodeContext(node_id=node.id, spec=spec, events=[], run_id="run-t5", loop_count=1)
+
+
+@pytest.mark.parametrize("kind", ALL_KINDS)
+async def test_meeting_handler_adds_meeting_action_items_and_summary(kind):
+    host = MeetingHost()
+    registry = RoleRegistry()
+    handler = make_meeting_handler(host, registry)
+    state = ClusterState(
+        project=Project(id="proj1", name="演示项目"),
+        iterations=[Iteration(id="iter1", project_id="proj1", number=1)],
+    )
+    node = WorkflowNode(id=f"meeting_node_{kind.value}", type="meeting", meeting=kind)
+    ctx = _make_context(node)
+
+    updates = await handler(state, node, ctx)
+
+    # 通道键契约：meetings / tasks / messages
+    assert set(updates) == {"meetings", "tasks", "messages"}
+
+    meetings = updates["meetings"]
+    assert len(meetings) == 1
+    meeting = meetings[0]
+    assert meeting.kind == kind
+    assert meeting.project_id == "proj1"
+    assert meeting.transcript and meeting.decisions
+    assert meeting.minutes_id.startswith(f"minutes:{kind.value}:")
+
+    # 行动项任务：status=todo，assignee 来自会议参与者
+    participants = registry.default_role_ids(kind)
+    tasks = updates["tasks"]
+    assert len(tasks) == len(meeting.decisions)
+    for task in tasks:
+        assert task.status == TaskStatus.TODO
+        assert task.assignee_role in participants
+        assert task.project_id == "proj1"
+        assert task.iteration_id == "iter1"
+
+    # 总结消息：meeting_speech 广播
+    messages = updates["messages"]
+    assert len(messages) == 1
+    summary = messages[0]
+    assert summary.type == MessageType.MEETING_SPEECH
+    assert summary.payload["meeting_id"] == meeting.id
+
+    # meeting_held 事件走 ctx.events
+    assert len(ctx.events) == 1
+    assert ctx.events[0].type == "meeting_held"
+    assert ctx.events[0].actor == kind.value
+
+
+async def test_meeting_handler_requires_meeting_kind():
+    host = MeetingHost()
+    registry = RoleRegistry()
+    handler = make_meeting_handler(host, registry)
+    state = ClusterState(project=Project(id="proj1", name="演示项目"))
+    node = WorkflowNode(id="bad", type="meeting")
+    ctx = _make_context(node)
+    with pytest.raises(ValueError, match="meeting"):
+        await handler(state, node, ctx)
diff --git a/tests/test_roles.py b/tests/test_roles.py
new file mode 100644
index 0000000..6ee761e
--- /dev/null
+++ b/tests/test_roles.py
@@ -0,0 +1,101 @@
+"""Task 5 行为测试：12 岗位目录、RoleKind 映射与 RoleRegistry 查询。"""
+
+from __future__ import annotations
+
+import pytest
+
+from agent_cluster.models import GateKind, MeetingKind, Role, RoleKind
+from agent_cluster.roles import RoleRegistry, build_role_catalog
+
+EXPECTED_ROLE_IDS = [
+    "pm",
+    "pmo",
+    "frontend",
+    "backend",
+    "algorithm",
+    "architect",
+    "qa",
+    "devops",
+    "docs",
+    "reviewer",
+    "debugger",
+    "governance",
+]
+
+
+def test_catalog_has_12_roles_with_expected_ids():
+    catalog = build_role_catalog()
+    assert len(catalog) == 12
+    assert set(catalog) == set(EXPECTED_ROLE_IDS)
+    assert all(isinstance(role, Role) for role in catalog.values())
+
+
+def test_every_role_has_required_fields():
+    catalog = build_role_catalog()
+    for role in catalog.values():
+        assert role.id, f"{role.id} 缺少 id"
+        assert role.name, f"{role.id} 缺少 name"
+        assert isinstance(role.kind, RoleKind), f"{role.id} 的 kind 非法"
+        assert role.goal, f"{role.id} 缺少 goal"
+        assert role.backstory, f"{role.id} 缺少 backstory"
+        assert isinstance(role.skills, list) and role.skills, f"{role.id} 缺少 skills"
+        assert all(isinstance(item, str) and "@" in item for item in role.skills), f"{role.id} skills 应为 name@version"
+        assert isinstance(role.tools, list) and role.tools, f"{role.id} 缺少 tools"
+        assert isinstance(role.approval_scope, list), f"{role.id} 缺少 approval_scope"
+        assert all(isinstance(gate, GateKind) for gate in role.approval_scope)
+
+
+def test_architect_maps_to_role_kind_arch():
+    role = build_role_catalog()["architect"]
+    assert role.kind == RoleKind.ARCH
+
+
+def test_role_kind_mapping_for_auxiliary_roles():
+    """辅助/门禁四岗的 RoleKind 归类契约（文档化映射）。"""
+    catalog = build_role_catalog()
+    assert catalog["docs"].kind == RoleKind.PMO
+    assert catalog["reviewer"].kind == RoleKind.QA
+    assert catalog["debugger"].kind == RoleKind.QA
+    assert catalog["governance"].kind == RoleKind.PM
+
+
+def test_approval_scope_contract():
+    catalog = build_role_catalog()
+    assert GateKind.REQUIREMENT_CONFIRMATION in catalog["pm"].approval_scope
+    assert GateKind.DESIGN_REVIEW in catalog["architect"].approval_scope
+    assert GateKind.ITERATION_ACCEPTANCE in catalog["qa"].approval_scope
+    assert GateKind.ITERATION_ACCEPTANCE in catalog["pm"].approval_scope
+    assert GateKind.RELEASE in catalog["devops"].approval_scope
+    assert GateKind.RELEASE in catalog["pm"].approval_scope
+    assert GateKind.EVOLUTION_APPLY in catalog["governance"].approval_scope
+
+
+def test_registry_get_and_list():
+    registry = RoleRegistry()
+    role = registry.get("architect")
+    assert role.id == "architect"
+    listed = registry.list()
+    assert len(listed) == 12
+    assert [item.id for item in listed] == sorted(EXPECTED_ROLE_IDS)
+
+
+def test_registry_get_missing_raises_key_error():
+    with pytest.raises(KeyError, match="not-a-role"):
+        RoleRegistry().get("not-a-role")
+
+
+def test_registry_filter_by_kind():
+    registry = RoleRegistry()
+    qa_roles = registry.filter_by_kind(RoleKind.QA)
+    assert {role.id for role in qa_roles} == {"qa", "reviewer", "debugger"}
+    arch_roles = registry.filter_by_kind(RoleKind.ARCH)
+    assert [role.id for role in arch_roles] == ["architect"]
+
+
+def test_registry_default_role_ids_for_meetings():
+    registry = RoleRegistry()
+    kickoff = registry.default_role_ids(MeetingKind.KICKOFF)
+    assert "pm" in kickoff and "architect" in kickoff
+    code_review = registry.default_role_ids("code_review")
+    assert code_review == ["frontend", "backend", "reviewer"]
+    assert all(role_id in EXPECTED_ROLE_IDS for role_id in kickoff)
diff --git a/tests/test_runtime.py b/tests/test_runtime.py
new file mode 100644
index 0000000..9e03611
--- /dev/null
+++ b/tests/test_runtime.py
@@ -0,0 +1,225 @@
+"""Task 5 行为测试：模型客户端、ChatModelFactory、EventBus 与 AgentRuntime / agent handler。"""
+
+from __future__ import annotations
+
+import pytest
+
+from agent_cluster.models import (
+    Agent,
+    AgentConfig,
+    ClusterState,
+    Iteration,
+    Message,
+    MessageType,
+    ModelConfig,
+    Project,
+    TaskStatus,
+)
+from agent_cluster.roles import RoleRegistry
+from agent_cluster.runtime import (
+    AgentRuntime,
+    ChatModelFactory,
+    DeterministicClient,
+    EventBus,
+    OpenAIClient,
+    make_agent_handler,
+)
+from agent_cluster.workflow import NodeContext, WorkflowEdge, WorkflowNode, WorkflowSpec
+
+
+# ---------------------------------------------------------------------------
+# DeterministicClient
+# ---------------------------------------------------------------------------
+
+
+async def test_deterministic_client_returns_deterministic_output():
+    client = DeterministicClient(persona="测试工程师")
+    messages = [
+        {"role": "system", "content": "你是测试工程师"},
+        {"role": "user", "content": "请执行任务 A"},
+    ]
+    first = await client.complete(messages)
+    second = await client.complete(messages)
+    assert first == second  # 同一输入恒得同一输出
+    assert "测试工程师" in first
+    assert "任务 A" in first
+
+
+async def test_deterministic_client_handles_empty_messages():
+    client = DeterministicClient()
+    reply = await client.complete([])
+    assert "就绪" in reply
+
+
+def test_openai_client_requires_api_key(monkeypatch):
+    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
+    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
+        OpenAIClient()
+
+
+def test_factory_defaults_to_deterministic():
+    assert isinstance(ChatModelFactory().create(), DeterministicClient)
+    assert isinstance(
+        ChatModelFactory().create(AgentConfig(model=ModelConfig(model_name="deterministic"))),
+        DeterministicClient,
+    )
+
+
+def test_factory_rejects_unknown_model():
+    with pytest.raises(ValueError, match="未知模型名称"):
+        ChatModelFactory().create(AgentConfig(model=ModelConfig(model_name="llama-3")))
+
+
+# ---------------------------------------------------------------------------
+# EventBus
+# ---------------------------------------------------------------------------
+
+
+def test_event_bus_publish_and_query():
+    bus = EventBus()
+    event_one = _event(type="agent_step", thread_id="t1")
+    event_two = _event(type="meeting_held", thread_id="t2")
+    event_three = _event(type="agent_step", thread_id="t2")
+    for event in (event_one, event_two, event_three):
+        bus.publish(event)
+    assert len(bus.events) == 3
+    assert len(bus.query(type="agent_step")) == 2
+    assert len(bus.query(thread_id="t2")) == 2
+    assert len(bus.query(thread_id="t1", type="agent_step")) == 1
+    assert len(bus.query(thread_id="t1", type="meeting_held")) == 0
+    assert len(bus.query()) == 3
+
+
+def _event(type: str, thread_id: str):
+    from agent_cluster.models import Event
+
+    return Event(id=f"e-{type}-{thread_id}", run_id="run1", thread_id=thread_id, type=type)
+
+
+# ---------------------------------------------------------------------------
+# AgentRuntime.reply / observe
+# ---------------------------------------------------------------------------
+
+
+def _make_agent() -> Agent:
+    return Agent(
+        id="agent-architect",
+        role_id="architect",
+        name="架构师",
+        system_prompt="你是架构师，负责系统设计。",
+    )
+
+
+def _make_text_message(thread_id: str, content: str) -> Message:
+    return Message(
+        id="m1",
+        thread_id=thread_id,
+        source="pmo",
+        target="agent-architect",
+        type=MessageType.TEXT,
+        payload={"content": content},
+    )
+
+
+async def test_reply_produces_text_message_from_agent():
+    runtime = AgentRuntime()
+    agent = _make_agent()
+    reply = await runtime.reply(agent, [_make_text_message("proj:demo:iter:1", "请输出系统设计")])
+    assert reply.source == agent.id
+    assert reply.type == MessageType.TEXT
+    assert reply.target == ""
+    assert "请输出系统设计" in reply.payload["content"]
+    # reply 事件已发布到总线
+    assert len(runtime.event_bus.query(type="agent_reply")) == 1
+
+
+async def test_observe_updates_agent_state():
+    runtime = AgentRuntime()
+    agent = _make_agent()
+    observed = [_make_text_message("proj:demo:iter:1", "观察内容 A")]
+    await runtime.observe(agent, observed)
+    assert agent.state.messages == observed
+    await runtime.observe(agent, [_make_text_message("proj:demo:iter:1", "观察内容 B")])
+    assert [message.payload["content"] for message in agent.state.messages] == ["观察内容 A", "观察内容 B"]
+
+
+# ---------------------------------------------------------------------------
+# make_agent_handler（agent 节点 handler 契约）
+# ---------------------------------------------------------------------------
+
+
+def _make_context(node: WorkflowNode) -> NodeContext:
+    spec = WorkflowSpec(
+        name="t5-agent",
+        max_iterations=4,
+        thread_id="proj:demo:iter:1",
+        nodes=[
+            WorkflowNode(id="start", type="start"),
+            node,
+            WorkflowNode(id="end", type="end"),
+        ],
+        edges=[
+            WorkflowEdge(from_="start", to=node.id),
+            WorkflowEdge(from_=node.id, to="end"),
+        ],
+    )
+    return NodeContext(node_id=node.id, spec=spec, events=[], run_id="run-t5", loop_count=1)
+
+
+async def test_agent_handler_updates_tasks_messages_and_ledger():
+    runtime = AgentRuntime()
+    registry = RoleRegistry()
+    handler = make_agent_handler(runtime, registry)
+    state = ClusterState(
+        project=Project(id="proj1", name="演示项目"),
+        iterations=[Iteration(id="iter1", project_id="proj1", number=1)],
+    )
+    node = WorkflowNode(id="design", type="agent", role="architect")
+    ctx = _make_context(node)
+
+    updates = await handler(state, node, ctx)
+
+    # 通道键契约：tasks / messages / ledger；事件走 ctx.events
+    assert set(updates) == {"tasks", "messages", "ledger"}
+    tasks = updates["tasks"]
+    assert len(tasks) == 1
+    task = tasks[0]
+    assert task.assignee_role == "architect"
+    assert task.status == TaskStatus.DOING  # todo→doing
+    assert task.project_id == "proj1"
+    assert task.iteration_id == "iter1"
+
+    messages = updates["messages"]
+    assert len(messages) == 1
+    assert messages[0].source == "architect"
+    assert messages[0].type == MessageType.TEXT
+    assert messages[0].payload["task"] == task.id
+
+    ledger = updates["ledger"]
+    assert ledger.task_id == task.id
+    assert ledger.progress[-1].role == "architect"
+    assert ledger.progress[-1].status == "doing"
+
+    # 事件追加到 ctx.events（不占通道键）
+    assert len(ctx.events) == 1
+    event = ctx.events[0]
+    assert event.type == "agent_step"
+    assert event.actor == "architect"
+    assert event.payload["task"] == task.id
+
+
+async def test_agent_handler_creates_fresh_task_per_invocation():
+    """每次调用新建任务（tasks 通道为 operator.add 追加，复用会重复——契约）。"""
+    runtime = AgentRuntime()
+    registry = RoleRegistry()
+    handler = make_agent_handler(runtime, registry)
+    state = ClusterState(project=Project(id="proj1", name="演示项目"))
+    node = WorkflowNode(id="design", type="agent", role="architect")
+
+    first = await handler(state, node, _make_context(node))
+    second = await handler(state, node, _make_context(node))
+    assert first["tasks"][0].id != second["tasks"][0].id
+    assert first["tasks"][0].status == TaskStatus.DOING
+    assert second["tasks"][0].status == TaskStatus.DOING
+    # 通道内既有任务不受影响，返回的任务为新增实例
+    assert state.tasks == []
```
