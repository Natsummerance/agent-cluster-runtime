# Task 6 Review Package

Base: 278652e
Head: 49afa69

## Diff stat

```
 src/agent_cluster/__init__.py  |  26 +-
 src/agent_cluster/evolution.py | 538 ++++++++++++++++++++++++++++++++++++++
 src/agent_cluster/metrics.py   | 195 ++++++++++++++
 tests/test_evolution.py        | 568 +++++++++++++++++++++++++++++++++++++++++
 tests/test_metrics.py          | 209 +++++++++++++++
 5 files changed, 1534 insertions(+), 2 deletions(-)
```

## Full diff

```diff
diff --git a/src/agent_cluster/__init__.py b/src/agent_cluster/__init__.py
index dd10837..331c6bd 100644
--- a/src/agent_cluster/__init__.py
+++ b/src/agent_cluster/__init__.py
@@ -2,8 +2,8 @@
 
 当前阶段覆盖：数据模型层（models.py）、技能层（skills.py）、流程引擎
 （workflow.py）、审批门（gates.py）、组织角色（roles.py）、角色执行运行时
-（runtime.py）、会议（meetings.py）与账本/任务板（ledger.py）；后续任务将
-加入进化闭环、度量与 CLI。
+（runtime.py）、会议（meetings.py）、账本/任务板（ledger.py）、进化闭环
+（evolution.py）与绩效度量（metrics.py）；后续任务将加入 CLI。
 """
 
 from agent_cluster.models import (
@@ -70,6 +70,19 @@ from agent_cluster.runtime import (
 )
 from agent_cluster.meetings import MeetingHost, make_meeting_handler
 from agent_cluster.ledger import BLOCKED, COLUMNS, LedgerStore, TaskBoard, TaskBoardError
+from agent_cluster.evolution import (
+    Candidate,
+    EvolutionEngine,
+    EvolutionError,
+    EvolutionProposal,
+    Signal,
+)
+from agent_cluster.metrics import (
+    MetricPoint,
+    MetricRules,
+    MetricsCollector,
+    MetricsSnapshot,
+)
 from agent_cluster.skills import (
     DisclosureLevel,
     SkillCatalog,
@@ -149,6 +162,15 @@ __all__ = [
     "WorkflowValidationError",
     "__version__",
     "format_skill_context",
+    "Candidate",
+    "EvolutionEngine",
+    "EvolutionError",
+    "EvolutionProposal",
+    "Signal",
+    "MetricPoint",
+    "MetricRules",
+    "MetricsCollector",
+    "MetricsSnapshot",
     "GateError",
     "approval_pending",
     "make_gate_handler",
diff --git a/src/agent_cluster/evolution.py b/src/agent_cluster/evolution.py
new file mode 100644
index 0000000..d7348fe
--- /dev/null
+++ b/src/agent_cluster/evolution.py
@@ -0,0 +1,538 @@
+"""进化闭环模块：设计文档 §6.2 六步进化闭环 + §6.5 安全治理。
+
+六步闭环 API 映射（collect -> distill -> propose -> review -> apply -> rollback）：
+
+① 收集 ``collect(events) -> list[Signal]``
+   规则扫描事件流（EventBus / list[Event]），产出信号：指标越界
+   （metric_threshold）、评审重复驳回（review_failure，LBTM）、复盘根因
+   （retro_root_cause）、回滚事件（rollback_occurred）；相同信号去重。
+
+② 提炼 ``distill(signals) -> list[Candidate]``
+   按 category+target 归并同类信号、合并去重证据、过滤噪音
+   （severity=low 且无 evidence 的信号直接丢弃）。
+
+③ 提案 ``propose(candidate, *, author_role, title, rollback_plan, validation_plan)``
+   强制"理由+结论"双字段：缺 rollback_plan（空/空白）直接拒绝；
+   按类别推导风险等级（organization=high / process=medium /
+   skill/knowledge=low，证据含 severity=high|critical 时升一级）；
+   执行自我扩权校验。
+
+④ 评审门 ``review(proposal, *, approver, human_required, auto_mode, decision, reason)``
+   L3 组织流程变更必须人工审批：human_required=True 且 auto_mode != "ask"
+   时自动驳回（bypass-immune: 组织流程变更必须人工审批）；
+   其余按 approver 的 decision 置为 approved / rejected 并记录 Vote。
+
+⑤ 生效 ``apply(proposal, *, event_bus)``
+   仅 approved 可生效：effective_version 自增（v0->v1->...）、
+   置灰度标志 gray=True、状态 applied，并写审计事件 evolution_applied。
+
+⑥ 回滚 ``rollback(proposal, *, reason, event_bus)``
+   仅 applied 可回滚：状态置 rolled_back，写审计事件 evolution_rolled_back
+   （回滚本身进入下一轮 ① 的 collect 输入）。
+
+安全约束（§6.5）：``assert_no_self_empowerment`` 禁止提案变更修改自身岗位
+的审批范围/权限（approval_scope / permissions / 权限 / 提权），在
+``propose`` 与 ``review`` 两处均执行校验。
+"""
+
+from __future__ import annotations
+
+import json
+import uuid
+from datetime import datetime
+from typing import Literal
+
+from pydantic import BaseModel, ConfigDict, Field, field_validator
+
+from agent_cluster.models import Event, Vote
+from agent_cluster.runtime import EventBus
+
+__all__ = [
+    "Signal",
+    "Candidate",
+    "EvolutionProposal",
+    "EvolutionEngine",
+    "EvolutionError",
+    "bump_version",
+]
+
+# 信号类型 -> 进化对象类别（§6.1 四类）默认映射
+SIGNAL_TYPE_CATEGORY: dict[str, str] = {
+    "metric_threshold": "process",
+    "review_failure": "skill",
+    "retro_root_cause": "knowledge",
+}
+
+# 自我扩权校验关键词（命中即拒绝）
+SELF_EMPOWERMENT_KEYWORDS: tuple[str, ...] = (
+    "approval_scope",
+    "permissions",
+    "permission",
+    "提权",
+    "权限",
+)
+
+# 风险等级排序（用于证据提示升级）
+RISK_RANK: dict[str, int] = {"low": 0, "medium": 1, "high": 2}
+SEVERITY_RANK: dict[str, int] = {"low": 0, "medium": 1, "high": 2, "critical": 3}
+
+
+class EvolutionError(Exception):
+    """进化闭环业务错误（回滚方案缺失 / 状态机越权 / 自我扩权等）。"""
+
+
+def bump_version(version: str) -> str:
+    """版本自增：``v0 -> v1 -> v2 ...``；兼容不带 ``v`` 前缀的纯数字版本。"""
+    core = version[1:] if version.startswith("v") else version
+    try:
+        number = int(core)
+    except ValueError as exc:
+        raise EvolutionError(f"无法解析版本号：{version!r}（期望形如 v0）") from exc
+    return f"v{number + 1}"
+
+
+def _dedupe(items: list[str]) -> list[str]:
+    """保序去重字符串列表。"""
+    seen: set[str] = set()
+    result: list[str] = []
+    for item in items:
+        if item not in seen:
+            seen.add(item)
+            result.append(item)
+    return result
+
+
+def _change_to_text(change_diff: dict | str) -> str:
+    """把 change_diff（dict 或 str）统一序列化为可检索文本。"""
+    if isinstance(change_diff, str):
+        return change_diff
+    return json.dumps(change_diff, ensure_ascii=False)
+
+
+def _tag_value(entries: list[str], tag: str) -> str | None:
+    """从 evidence 条目中解析 ``tag=value`` 形式的标签（如 target=xxx / category=xxx / role=xxx）。"""
+    for entry in entries:
+        if entry.startswith(f"{tag}="):
+            return entry.split("=", 1)[1]
+    return None
+
+
+class Signal(BaseModel):
+    """进化信号（闭环①的输出：可观测性聚合产物）。"""
+
+    model_config = ConfigDict(extra="ignore")
+
+    id: str = Field(description="信号唯一标识")
+    type: str = Field(description="信号类型，如 metric_threshold / review_failure / retro_root_cause")
+    source: str = Field(description="信号来源（指标名 / 目标 / 复盘来源等）")
+    evidence: list[str] = Field(default_factory=list, description="证据条目列表")
+    severity: Literal["low", "medium", "high", "critical"] = Field(
+        default="medium", description="信号严重度"
+    )
+    ts: datetime = Field(default_factory=datetime.now, description="信号产生时间")
+
+
+class Candidate(BaseModel):
+    """进化候选（闭环②的输出：去重合并后的分诊结果）。"""
+
+    model_config = ConfigDict(extra="ignore")
+
+    category: Literal["skill", "knowledge", "process", "organization"] = Field(
+        description="进化对象类别（§6.1 四类）"
+    )
+    target: str = Field(description="进化目标（技能名 / 流程名 / 角色分工等）")
+    change: dict = Field(description="变更内容（diff 骨架，propose 时形式化为 change_diff）")
+    evidence: list[str] = Field(default_factory=list, description="支撑证据")
+    expected_impact: str = Field(default="", description="预期影响")
+
+
+class EvolutionProposal(BaseModel):
+    """进化提案（闭环③的输出，进入评审门）。
+
+    校验规则：``rollback_plan`` 为空/空白时构造即抛 ValidationError；
+    ``propose`` 同样显式拒绝缺回滚方案的候选。
+    """
+
+    model_config = ConfigDict(extra="ignore")
+
+    id: str = Field(description="提案唯一标识")
+    title: str = Field(description="提案标题")
+    author_role: str = Field(description="提案人岗位 id")
+    category: Literal["skill", "knowledge", "process", "organization"] = Field(
+        description="进化对象类别（§6.1 四类）"
+    )
+    target: str = Field(description="进化目标")
+    change_diff: dict | str = Field(description="变更 diff（dict 或文本 diff）")
+    affected_roles: list[str] = Field(default_factory=list, description="受影响岗位")
+    affected_workflows: list[str] = Field(default_factory=list, description="受影响流程")
+    risk_level: Literal["low", "medium", "high"] = Field(description="风险等级")
+    validation_plan: str = Field(default="", description="验证方案")
+    rollback_plan: str = Field(description="回滚方案（强制，缺省校验失败）")
+    owner: str = Field(default="", description="负责人")
+    status: Literal["draft", "voting", "approved", "rejected", "applied", "rolled_back"] = Field(
+        default="draft", description="提案状态（六态状态机）"
+    )
+    gray: bool = Field(default=False, description="灰度标志：生效时置 True（试点观察）")
+    effective_version: str = Field(default="v0", description="生效版本号，自增 v0->v1...")
+    votes: list[Vote] = Field(default_factory=list, description="评审投票记录")
+    created_ts: datetime = Field(default_factory=datetime.now, description="创建时间")
+    updated_ts: datetime = Field(default_factory=datetime.now, description="更新时间")
+
+    @field_validator("rollback_plan")
+    @classmethod
+    def _rollback_plan_must_not_be_empty(cls, value: str) -> str:
+        if not value or not value.strip():
+            raise ValueError("提案必须提供 rollback_plan（回滚方案）")
+        return value
+
+
+class EvolutionEngine:
+    """六步进化闭环引擎（§6.2）：collect -> distill -> propose -> review -> apply -> rollback。
+
+    - 内部维护审计轨迹（``audit_events``），apply/rollback 同时写入
+      传入的 EventBus（若提供），保证"每次进化落审计"。
+    """
+
+    def __init__(
+        self,
+        *,
+        event_bus: EventBus | None = None,
+        review_rejection_threshold: int = 2,
+    ) -> None:
+        self._event_bus: EventBus | None = event_bus
+        self.review_rejection_threshold: int = review_rejection_threshold
+        self._audit_events: list[Event] = []
+
+    # ------------------------------------------------------------------
+    # ① 收集
+    # ------------------------------------------------------------------
+
+    def collect(self, events: list[Event] | EventBus) -> list[Signal]:
+        """规则扫描事件流，产出 Signals（相同信号去重）。
+
+        规则：
+        - ``metric_threshold`` 事件 -> 指标越界信号（type=metric_threshold）；
+        - ``review_result`` 事件 verdict 为 reject/lbtm，同一 target 累计
+          达 ``review_rejection_threshold`` 次 -> 评审失败信号（review_failure）；
+        - ``retro`` 事件携带 root_cause -> 复盘根因信号（retro_root_cause）；
+        - ``evolution_rolled_back`` 事件 -> 回滚信号（rollback_occurred），
+          回滚本身进入下一轮 ① 的收集输入。
+        """
+        event_list = events.events if isinstance(events, EventBus) else list(events)
+        signals: list[Signal] = []
+        rejections: dict[str, list[Event]] = {}
+        for event in event_list:
+            payload = event.payload or {}
+            if event.type == "metric_threshold":
+                signals.append(
+                    Signal(
+                        id=uuid.uuid4().hex,
+                        type="metric_threshold",
+                        source=payload.get("source") or event.actor or "metrics",
+                        evidence=payload.get("evidence") or [str(payload.get("metric", "metric_threshold"))],
+                        severity=payload.get("severity", "medium"),
+                        ts=event.ts,
+                    )
+                )
+            elif event.type == "review_result":
+                verdict = str(payload.get("verdict", "")).lower()
+                if verdict in ("reject", "rejected", "lbtm"):
+                    target = payload.get("target") or event.thread_id or event.actor
+                    rejections.setdefault(target, []).append(event)
+            elif event.type == "retro":
+                root_cause = payload.get("root_cause")
+                if root_cause:
+                    causes = root_cause if isinstance(root_cause, list) else [root_cause]
+                    for cause in causes:
+                        signals.append(
+                            Signal(
+                                id=uuid.uuid4().hex,
+                                type="retro_root_cause",
+                                source=payload.get("source") or event.actor or "retro",
+                                evidence=[str(cause)],
+                                severity="medium",
+                                ts=event.ts,
+                            )
+                        )
+            elif event.type == "evolution_rolled_back":
+                reason = payload.get("reason", "")
+                evidence = [reason] if reason else []
+                signals.append(
+                    Signal(
+                        id=uuid.uuid4().hex,
+                        type="rollback_occurred",
+                        source=payload.get("proposal_id") or event.actor or "evolution",
+                        evidence=evidence,
+                        severity="medium",
+                        ts=event.ts,
+                    )
+                )
+        for target, target_events in rejections.items():
+            if len(target_events) >= self.review_rejection_threshold:
+                signals.append(
+                    Signal(
+                        id=uuid.uuid4().hex,
+                        type="review_failure",
+                        source=target,
+                        evidence=[
+                            f"{item.type}:{item.payload.get('verdict', '')}:{item.payload.get('target', '')}"
+                            for item in target_events
+                        ],
+                        severity="high" if len(target_events) >= 3 else "medium",
+                        ts=target_events[-1].ts,
+                    )
+                )
+        return self._dedupe_signals(signals)
+
+    @staticmethod
+    def _dedupe_signals(signals: list[Signal]) -> list[Signal]:
+        """按内容（type+source+severity+evidence）去重，保留首个。"""
+        seen: set[tuple] = set()
+        result: list[Signal] = []
+        for signal in signals:
+            key = (signal.type, signal.source, signal.severity, tuple(signal.evidence))
+            if key in seen:
+                continue
+            seen.add(key)
+            result.append(signal)
+        return result
+
+    # ------------------------------------------------------------------
+    # ② 提炼
+    # ------------------------------------------------------------------
+
+    def distill(self, signals: list[Signal]) -> list[Candidate]:
+        """按 category+target 归并信号 -> Candidates；过滤噪音信号。"""
+        groups: dict[tuple[str, str], list[Signal]] = {}
+        for signal in signals:
+            if signal.severity == "low" and not signal.evidence:
+                continue  # 噪音：低严重度且无证据
+            category = self._category_for(signal)
+            target = self._target_for(signal)
+            groups.setdefault((category, target), []).append(signal)
+
+        candidates: list[Candidate] = []
+        for (category, target), group in groups.items():
+            evidence = _dedupe([entry for signal in group for entry in signal.evidence])
+            top_severity = max((SEVERITY_RANK.get(signal.severity, 0) for signal in group), default=0)
+            severity_label = next(
+                (label for label, rank in SEVERITY_RANK.items() if rank == top_severity), "low"
+            )
+            candidates.append(
+                Candidate(
+                    category=category,  # type: ignore[arg-type]
+                    target=target,
+                    change={"kind": "improve", "target": target},
+                    evidence=evidence,
+                    expected_impact=(
+                        f"改善 {target} 的失败模式（聚合信号 {len(group)} 个，"
+                        f"最高严重度 {severity_label}）"
+                    ),
+                )
+            )
+        return candidates
+
+    @staticmethod
+    def _category_for(signal: Signal) -> str:
+        """信号 -> 进化对象类别：evidence 标签 category= 优先，否则按信号类型映射。"""
+        tagged = _tag_value(signal.evidence, "category")
+        if tagged in ("skill", "knowledge", "process", "organization"):
+            return tagged
+        return SIGNAL_TYPE_CATEGORY.get(signal.type, "process")
+
+    @staticmethod
+    def _target_for(signal: Signal) -> str:
+        """信号 -> 进化目标：evidence 标签 target= 优先，否则取信号来源。"""
+        tagged = _tag_value(signal.evidence, "target")
+        return tagged or signal.source or signal.type
+
+    # ------------------------------------------------------------------
+    # ③ 提案
+    # ------------------------------------------------------------------
+
+    def propose(
+        self,
+        candidate: Candidate,
+        *,
+        author_role: str,
+        title: str,
+        rollback_plan: str,
+        validation_plan: str = "",
+    ) -> EvolutionProposal:
+        """候选 -> 提案：强制回滚方案 + 风险等级推导 + 自我扩权校验。"""
+        if not rollback_plan or not rollback_plan.strip():
+            raise EvolutionError("提案必须提供 rollback_plan（回滚方案），拒绝提交")
+        now = datetime.now()
+        proposal = EvolutionProposal(
+            id=uuid.uuid4().hex,
+            title=title,
+            author_role=author_role,
+            category=candidate.category,
+            target=candidate.target,
+            change_diff=candidate.change,
+            affected_roles=self._affected_roles_for(candidate, author_role),
+            affected_workflows=self._affected_workflows_for(candidate),
+            risk_level=self._risk_level_for(candidate),
+            validation_plan=validation_plan,
+            rollback_plan=rollback_plan.strip(),
+            owner=author_role,
+            status="draft",
+            created_ts=now,
+            updated_ts=now,
+        )
+        self.assert_no_self_empowerment(proposal)
+        return proposal
+
+    @staticmethod
+    def _risk_level_for(candidate: Candidate) -> str:
+        """按类别推导风险等级；证据含 severity=high/critical 时升一级。"""
+        base = {"organization": "high", "process": "medium", "skill": "low", "knowledge": "low"}[
+            candidate.category
+        ]
+        rank = RISK_RANK[base]
+        if any("severity=high" in entry or "severity=critical" in entry for entry in candidate.evidence):
+            rank = min(rank + 1, RISK_RANK["high"])
+        return next(label for label, value in RISK_RANK.items() if value == rank)
+
+    @staticmethod
+    def _affected_roles_for(candidate: Candidate, author_role: str) -> list[str]:
+        """受影响岗位：evidence 中 role= 标签优先，缺省为提案人岗位。"""
+        roles = [entry.split("=", 1)[1] for entry in candidate.evidence if entry.startswith("role=")]
+        return _dedupe(roles) or [author_role]
+
+    @staticmethod
+    def _affected_workflows_for(candidate: Candidate) -> list[str]:
+        """受影响流程：evidence 中 workflow= 标签列表。"""
+        workflows = [
+            entry.split("=", 1)[1] for entry in candidate.evidence if entry.startswith("workflow=")
+        ]
+        return _dedupe(workflows)
+
+    # ------------------------------------------------------------------
+    # ④ 评审门
+    # ------------------------------------------------------------------
+
+    def review(
+        self,
+        proposal: EvolutionProposal,
+        *,
+        approver: str,
+        human_required: bool = False,
+        auto_mode: str = "ask",
+        decision: str = "approve",
+        reason: str = "",
+    ) -> EvolutionProposal:
+        """评审门：L3 组织流程必须人工；无人值守自动驳回；其余按 approver 决策。
+
+        - ``human_required=True`` 且 ``auto_mode != "ask"``：自动驳回
+          （bypass-immune），即使 decision=approve 也不放行；
+        - 否则依据 ``decision``（approve/reject）置状态并记录 Vote。
+        """
+        if proposal.status not in ("draft", "voting"):
+            raise EvolutionError(f"仅 draft/voting 状态提案可评审，当前状态：{proposal.status}")
+        self.assert_no_self_empowerment(proposal)
+        if human_required and auto_mode != "ask":
+            auto_reason = "bypass-immune: 组织流程变更必须人工审批"
+            proposal.status = "rejected"
+            proposal.votes.append(
+                Vote(by_role=approver, verdict="reject", reason=auto_reason, ts=datetime.now())
+            )
+            proposal.updated_ts = datetime.now()
+            return proposal
+        if decision not in ("approve", "reject"):
+            raise EvolutionError(f"未知评审结论：{decision!r}（仅支持 approve/reject）")
+        proposal.status = "approved" if decision == "approve" else "rejected"
+        proposal.votes.append(
+            Vote(by_role=approver, verdict=decision, reason=reason, ts=datetime.now())
+        )
+        proposal.updated_ts = datetime.now()
+        return proposal
+
+    # ------------------------------------------------------------------
+    # ⑤ 生效 / ⑥ 回滚
+    # ------------------------------------------------------------------
+
+    def apply(
+        self,
+        proposal: EvolutionProposal,
+        *,
+        event_bus: EventBus | None = None,
+    ) -> EvolutionProposal:
+        """生效：仅 approved 可应用；版本自增 + 灰度标志；写审计事件。"""
+        if proposal.status != "approved":
+            raise EvolutionError(f"仅 approved 提案可生效，当前状态：{proposal.status}")
+        proposal.effective_version = bump_version(proposal.effective_version)
+        proposal.gray = True
+        proposal.status = "applied"
+        proposal.updated_ts = datetime.now()
+        self._emit(
+            event_type="evolution_applied",
+            proposal=proposal,
+            event_bus=event_bus,
+            extra={
+                "effective_version": proposal.effective_version,
+                "gray": proposal.gray,
+            },
+        )
+        return proposal
+
+    def rollback(
+        self,
+        proposal: EvolutionProposal,
+        *,
+        reason: str,
+        event_bus: EventBus | None = None,
+    ) -> EvolutionProposal:
+        """回滚：仅 applied 可回滚；写审计事件（回滚本身进入下一轮 collect）。"""
+        if proposal.status != "applied":
+            raise EvolutionError(f"仅 applied 提案可回滚，当前状态：{proposal.status}")
+        proposal.status = "rolled_back"
+        proposal.updated_ts = datetime.now()
+        self._emit(
+            event_type="evolution_rolled_back",
+            proposal=proposal,
+            event_bus=event_bus,
+            extra={"reason": reason},
+        )
+        return proposal
+
+    def _emit(
+        self,
+        *,
+        event_type: str,
+        proposal: EvolutionProposal,
+        event_bus: EventBus | None,
+        extra: dict,
+    ) -> None:
+        """构造审计 Event：优先写外部 event_bus（参数 > 引擎构造传入），同时留存内部审计轨迹。"""
+        event = Event(
+            id=uuid.uuid4().hex,
+            run_id="",
+            thread_id="",
+            type=event_type,
+            actor=proposal.owner,
+            payload={"proposal_id": proposal.id, "title": proposal.title, **extra},
+        )
+        self._audit_events.append(event)
+        target_bus = event_bus if event_bus is not None else self._event_bus
+        if target_bus is not None:
+            target_bus.publish(event)
+
+    @property
+    def audit_events(self) -> list[Event]:
+        """引擎内部审计轨迹（不可变拷贝）。"""
+        return list(self._audit_events)
+
+    # ------------------------------------------------------------------
+    # 安全约束（§6.5）
+    # ------------------------------------------------------------------
+
+    def assert_no_self_empowerment(self, proposal: EvolutionProposal) -> None:
+        """自我扩权校验：变更内容命中权限类关键词（approval_scope/permissions/权限/提权）即拒绝。"""
+        change_text = _change_to_text(proposal.change_diff).lower()
+        for keyword in SELF_EMPOWERMENT_KEYWORDS:
+            if keyword.lower() in change_text:
+                raise EvolutionError(
+                    f"自我扩权校验失败：提案（{proposal.id}）变更不得修改自身岗位权限，"
+                    f"命中关键词 {keyword!r}"
+                )
diff --git a/src/agent_cluster/metrics.py b/src/agent_cluster/metrics.py
new file mode 100644
index 0000000..55fb9e1
--- /dev/null
+++ b/src/agent_cluster/metrics.py
@@ -0,0 +1,195 @@
+"""绩效度量模块：设计文档 §6.3 度量采集 + 阈值规则引擎。
+
+组件：
+- ``MetricsCollector``：内存度量存储，``record(name, value, tags)`` 追加，
+  ``snapshot()`` 产出不可变快照 ``MetricsSnapshot``，``reset()`` 清空。
+- ``MetricPoint``：单条度量点（name/value/tags/ts）。
+- ``MetricsSnapshot``：按指标名分组的度量点快照。
+- ``MetricRules``：阈值规则引擎，``evaluate(snapshot) -> list[Signal]``。
+
+内置指标名（§6.3）：``review_pass_rate`` / ``rework_rate`` /
+``action_item_close_rate`` / ``loop_iterations`` / ``gate_wait_seconds``。
+
+阈值规则（每条产出 ``type="metric_threshold"`` 信号，evidence 取自真实度量点）：
+
+- ``review_pass_rate < 0.6``：评审通过率过低（high）；
+- ``rework_rate > 0.3``：返工率过高（high），取"最新迭代窗口"
+  （有 ``iteration`` 标签时取最新迭代的一组点，否则取最新一个点）；
+- ``action_item_close_rate < 0.5``：行动项关闭率过低（medium）；
+- ``loop_iterations`` 最新值 > 3 × 历史均值：循环次数激增（medium）；
+- ``gate_wait_seconds > 86400``：审批门等待超时（medium）。
+"""
+
+from __future__ import annotations
+
+import uuid
+from datetime import datetime
+from typing import Literal
+
+from pydantic import BaseModel, ConfigDict, Field
+
+from agent_cluster.evolution import Signal
+
+__all__ = [
+    "MetricPoint",
+    "MetricsSnapshot",
+    "MetricsCollector",
+    "MetricRules",
+    "BUILTIN_METRICS",
+]
+
+# 内置指标名（§6.3）
+BUILTIN_METRICS: tuple[str, ...] = (
+    "review_pass_rate",
+    "rework_rate",
+    "action_item_close_rate",
+    "loop_iterations",
+    "gate_wait_seconds",
+)
+
+# 阈值常量
+REVIEW_PASS_RATE_THRESHOLD: float = 0.6
+REWORK_RATE_THRESHOLD: float = 0.3
+ACTION_ITEM_CLOSE_RATE_THRESHOLD: float = 0.5
+LOOP_ITERATIONS_SPIKE_FACTOR: float = 3.0
+GATE_WAIT_THRESHOLD_SECONDS: float = 86400.0
+
+
+class MetricPoint(BaseModel):
+    """单条度量点。"""
+
+    model_config = ConfigDict(extra="ignore")
+
+    name: str = Field(description="指标名")
+    value: float = Field(description="指标值")
+    tags: dict[str, str] = Field(default_factory=dict, description="标签（如 iteration=iter-3）")
+    ts: datetime = Field(default_factory=datetime.now, description="采集时间")
+
+
+class MetricsSnapshot(BaseModel):
+    """度量快照：按指标名分组存储度量点。"""
+
+    model_config = ConfigDict(extra="ignore")
+
+    metrics: dict[str, list[MetricPoint]] = Field(
+        default_factory=dict, description="指标名 -> 度量点列表"
+    )
+
+
+class MetricsCollector:
+    """内存度量采集器：record / snapshot / reset。"""
+
+    def __init__(self) -> None:
+        self._store: dict[str, list[MetricPoint]] = {}
+
+    def record(
+        self,
+        name: str,
+        value: float,
+        *,
+        tags: dict | None = None,
+        ts: datetime | None = None,
+    ) -> None:
+        """记录一条度量点；``tags`` 与 ``ts`` 可选。"""
+        self._store.setdefault(name, []).append(
+            MetricPoint(
+                name=name,
+                value=value,
+                tags=dict(tags or {}),
+                ts=ts if ts is not None else datetime.now(),
+            )
+        )
+
+    def snapshot(self) -> MetricsSnapshot:
+        """产出当前快照（深拷贝，后续 record 不影响已产出快照）。"""
+        copied = {name: [point.model_copy(deep=True) for point in points] for name, points in self._store.items()}
+        return MetricsSnapshot(metrics=copied)
+
+    def reset(self) -> None:
+        """清空所有度量数据。"""
+        self._store.clear()
+
+
+class MetricRules:
+    """阈值规则引擎：``evaluate(snapshot)`` 产出 ``type="metric_threshold"`` 信号。"""
+
+    @staticmethod
+    def evaluate(snapshot: MetricsSnapshot) -> list[Signal]:
+        """评估快照，命中阈值即产出一条信号（每条规则至多一条，按最新窗口）。"""
+        signals: list[Signal] = []
+        metrics = snapshot.metrics
+
+        review_points = metrics.get("review_pass_rate", [])
+        if review_points and MetricRules._latest_value(review_points) < REVIEW_PASS_RATE_THRESHOLD:
+            signals.append(
+                MetricRules._build_signal("review_pass_rate", review_points, "high")
+            )
+
+        rework_points = metrics.get("rework_rate", [])
+        rework_window = MetricRules._latest_window(rework_points)
+        if rework_window and MetricRules._latest_value(rework_window) > REWORK_RATE_THRESHOLD:
+            signals.append(MetricRules._build_signal("rework_rate", rework_window, "high"))
+
+        close_points = metrics.get("action_item_close_rate", [])
+        if close_points and MetricRules._latest_value(close_points) < ACTION_ITEM_CLOSE_RATE_THRESHOLD:
+            signals.append(
+                MetricRules._build_signal("action_item_close_rate", close_points, "medium")
+            )
+
+        loop_points = metrics.get("loop_iterations", [])
+        loop_signal = MetricRules._loop_spike_signal(loop_points)
+        if loop_signal is not None:
+            signals.append(loop_signal)
+
+        gate_points = metrics.get("gate_wait_seconds", [])
+        if gate_points and MetricRules._latest_value(gate_points) > GATE_WAIT_THRESHOLD_SECONDS:
+            signals.append(
+                MetricRules._build_signal("gate_wait_seconds", gate_points, "medium")
+            )
+
+        return signals
+
+    # ------------------------------------------------------------------
+    # 内部辅助
+    # ------------------------------------------------------------------
+
+    @staticmethod
+    def _latest_value(points: list[MetricPoint]) -> float:
+        """取最新一个度量点的值（按 ts，相同时取最后记录的点）。"""
+        return sorted(points, key=lambda point: point.ts)[-1].value
+
+    @staticmethod
+    def _latest_window(points: list[MetricPoint]) -> list[MetricPoint]:
+        """最新迭代窗口：有 ``iteration`` 标签时取最新迭代的全部点，否则取最新一个点。"""
+        if not points:
+            return []
+        tagged = [point for point in points if point.tags.get("iteration")]
+        if tagged:
+            latest_iteration = max(point.tags["iteration"] for point in tagged)
+            return [point for point in points if point.tags.get("iteration") == latest_iteration]
+        return [sorted(points, key=lambda point: point.ts)[-1]]
+
+    @staticmethod
+    def _build_signal(name: str, points: list[MetricPoint], severity: Literal["medium", "high"]) -> Signal:
+        """由实际度量点构造指标越界信号（evidence 含指标名与值）。"""
+        return Signal(
+            id=uuid.uuid4().hex,
+            type="metric_threshold",
+            source="metric_rules",
+            evidence=[f"{point.name}={point.value}" for point in points],
+            severity=severity,
+            ts=sorted(points, key=lambda point: point.ts)[-1].ts,
+        )
+
+    @staticmethod
+    def _loop_spike_signal(points: list[MetricPoint]) -> Signal | None:
+        """循环次数激增：最新值 > 3 × 历史均值（至少有一个历史点）。"""
+        if len(points) < 2:
+            return None
+        ordered = sorted(points, key=lambda point: point.ts)
+        latest_value = ordered[-1].value
+        previous = ordered[:-1]
+        previous_average = sum(point.value for point in previous) / len(previous)
+        if latest_value > LOOP_ITERATIONS_SPIKE_FACTOR * previous_average:
+            return MetricRules._build_signal("loop_iterations", ordered, "medium")
+        return None
diff --git a/tests/test_evolution.py b/tests/test_evolution.py
new file mode 100644
index 0000000..37d1ad7
--- /dev/null
+++ b/tests/test_evolution.py
@@ -0,0 +1,568 @@
+"""Task 6 行为测试：六步进化闭环（collect->distill->propose->review->apply->rollback）+ 安全治理。"""
+
+from __future__ import annotations
+
+from datetime import datetime
+
+import pytest
+from pydantic import ValidationError
+
+from agent_cluster.evolution import (
+    Candidate,
+    EvolutionEngine,
+    EvolutionError,
+    EvolutionProposal,
+    Signal,
+    bump_version,
+)
+from agent_cluster.models import Event
+from agent_cluster.runtime import EventBus
+
+BYPASS_IMMUNE_REASON = "bypass-immune: 组织流程变更必须人工审批"
+
+
+def _event(
+    event_type: str,
+    *,
+    payload: dict | None = None,
+    actor: str = "qa",
+    ts: datetime | None = None,
+) -> Event:
+    return Event(
+        id=f"evt-{event_type}-{actor}-{id(payload)}",
+        run_id="run-1",
+        thread_id="thread-1",
+        type=event_type,
+        actor=actor,
+        payload=payload or {},
+        ts=ts or datetime(2026, 8, 1, 12, 0, 0),
+    )
+
+
+def _fabricated_events() -> list[Event]:
+    """构造闭环①输入事件：指标越界 + 2 次同类评审驳回 + 复盘根因。"""
+    return [
+        _event(
+            "metric_threshold",
+            payload={
+                "metric": "review_pass_rate",
+                "evidence": ["review_pass_rate=0.42"],
+                "severity": "high",
+                "source": "metrics:review_pass_rate",
+            },
+            actor="metrics",
+        ),
+        _event("review_result", payload={"verdict": "lbtm", "target": "qa_testing"}, actor="reviewer"),
+        _event("review_result", payload={"verdict": "reject", "target": "qa_testing"}, actor="reviewer"),
+        _event(
+            "retro",
+            payload={"root_cause": ["测试用例覆盖不足", "缺乏边界样例"]},
+            actor="retro_agent",
+        ),
+    ]
+
+
+# ---------------------------------------------------------------------------
+# ① 收集
+# ---------------------------------------------------------------------------
+
+
+def test_collect_produces_signals_from_events():
+    engine = EvolutionEngine()
+    signals = engine.collect(_fabricated_events())
+    types = {signal.type for signal in signals}
+    assert types == {"metric_threshold", "review_failure", "retro_root_cause"}
+    metric = next(signal for signal in signals if signal.type == "metric_threshold")
+    assert metric.source == "metrics:review_pass_rate"
+    assert metric.severity == "high"
+    assert metric.evidence == ["review_pass_rate=0.42"]
+
+
+def test_collect_accepts_event_bus_and_dedupes_identical_signals():
+    bus = EventBus()
+    for event in _fabricated_events():
+        bus.publish(event)
+    # 再补一条内容完全相同的指标越界事件 -> 应被去重
+    bus.publish(_event("metric_threshold", payload={"metric": "review_pass_rate", "evidence": ["review_pass_rate=0.42"], "severity": "high", "source": "metrics:review_pass_rate"}, actor="metrics"))
+    engine = EvolutionEngine()
+    signals = engine.collect(bus)
+    metric_signals = [signal for signal in signals if signal.type == "metric_threshold"]
+    assert len(metric_signals) == 1
+
+
+def test_collect_repeated_rejection_threshold_not_reached():
+    engine = EvolutionEngine()
+    events = [_event("review_result", payload={"verdict": "lbtm", "target": "qa_testing"}, actor="reviewer")]
+    signals = engine.collect(events)
+    assert [signal.type for signal in signals] == []
+
+
+def test_collect_rollback_event_feeds_next_round():
+    engine = EvolutionEngine()
+    bus = EventBus()
+    proposal = _approved_proposal(engine)
+    engine.apply(proposal, event_bus=bus)
+    engine.rollback(proposal, reason="指标恶化", event_bus=bus)
+    signals = engine.collect(bus)
+    rollback_signals = [signal for signal in signals if signal.type == "rollback_occurred"]
+    assert len(rollback_signals) == 1
+    assert rollback_signals[0].evidence == ["指标恶化"]
+
+
+# ---------------------------------------------------------------------------
+# ② 提炼
+# ---------------------------------------------------------------------------
+
+
+def test_distill_merges_and_drops_noise():
+    engine = EvolutionEngine()
+    signals = [
+        Signal(
+            id="s1",
+            type="review_failure",
+            source="qa_testing",
+            evidence=["target=qa_testing", "review_failure:lbtm"],
+            severity="medium",
+            ts=datetime(2026, 8, 1, 12, 0, 0),
+        ),
+        Signal(
+            id="s2",
+            type="review_failure",
+            source="qa_testing",
+            evidence=["target=qa_testing", "review_failure:reject"],
+            severity="high",
+            ts=datetime(2026, 8, 1, 13, 0, 0),
+        ),
+        Signal(
+            id="s3",
+            type="metric_threshold",
+            source="noise",
+            evidence=[],
+            severity="low",
+            ts=datetime(2026, 8, 1, 12, 0, 0),
+        ),
+    ]
+    candidates = engine.distill(signals)
+    assert len(candidates) == 1
+    candidate = candidates[0]
+    assert candidate.category == "skill"
+    assert candidate.target == "qa_testing"
+    assert candidate.evidence == ["target=qa_testing", "review_failure:lbtm", "review_failure:reject"]
+    assert "2 个" in candidate.expected_impact
+    assert "high" in candidate.expected_impact
+
+
+def test_distill_no_signals_returns_empty():
+    engine = EvolutionEngine()
+    assert engine.distill([]) == []
+
+
+# ---------------------------------------------------------------------------
+# ③ 提案
+# ---------------------------------------------------------------------------
+
+
+def _skill_candidate() -> Candidate:
+    return Candidate(
+        category="skill",
+        target="qa_testing",
+        change={"skill": "qa-testing", "patch": "补充边界样例模板"},
+        evidence=["target=qa_testing", "role=qa"],
+        expected_impact="降低 LBTM 驳回率",
+    )
+
+
+def test_propose_requires_rollback_plan():
+    engine = EvolutionEngine()
+    candidate = _skill_candidate()
+    with pytest.raises(EvolutionError, match="rollback_plan"):
+        engine.propose(candidate, author_role="qa", title="改善测试技能", rollback_plan="")
+    with pytest.raises(EvolutionError, match="rollback_plan"):
+        engine.propose(candidate, author_role="qa", title="改善测试技能", rollback_plan="   ")
+
+
+def test_proposal_model_rejects_missing_rollback_plan():
+    with pytest.raises(ValidationError, match="rollback_plan"):
+        EvolutionProposal(
+            id="p-empty",
+            title="缺回滚方案",
+            author_role="qa",
+            category="skill",
+            target="qa_testing",
+            change_diff={"skill": "qa-testing"},
+            risk_level="low",
+            rollback_plan="",
+        )
+    with pytest.raises(ValidationError, match="rollback_plan"):
+        EvolutionProposal(
+            id="p-blank",
+            title="空白回滚方案",
+            author_role="qa",
+            category="skill",
+            target="qa_testing",
+            change_diff={"skill": "qa-testing"},
+            risk_level="low",
+            rollback_plan=" \t ",
+        )
+
+
+def test_propose_builds_draft_proposal_with_derived_fields():
+    engine = EvolutionEngine()
+    proposal = engine.propose(
+        _skill_candidate(),
+        author_role="qa",
+        title="改善测试技能",
+        rollback_plan="回滚到 skill 版本 v0",
+        validation_plan="灰度 1 个 agent 观察 1 个迭代",
+    )
+    assert proposal.status == "draft"
+    assert proposal.category == "skill"
+    assert proposal.risk_level == "low"
+    assert proposal.effective_version == "v0"
+    assert proposal.gray is False
+    assert proposal.owner == "qa"
+    assert proposal.affected_roles == ["qa"]
+    assert proposal.change_diff == {"skill": "qa-testing", "patch": "补充边界样例模板"}
+    assert proposal.validation_plan == "灰度 1 个 agent 观察 1 个迭代"
+    assert proposal.rollback_plan == "回滚到 skill 版本 v0"
+
+
+def test_risk_level_derived_from_category():
+    engine = EvolutionEngine()
+    assert engine.propose(_skill_candidate(), author_role="qa", title="t", rollback_plan="r").risk_level == "low"
+    knowledge = Candidate(
+        category="knowledge",
+        target="坑位库",
+        change={"knowledge": "新增坑位"},
+        evidence=["target=坑位库"],
+        expected_impact="减少重复踩坑",
+    )
+    assert engine.propose(knowledge, author_role="qa", title="t", rollback_plan="r").risk_level == "low"
+    process = Candidate(
+        category="process",
+        target="fullstack-sprint",
+        change={"process": "新增返工边"},
+        evidence=["target=fullstack-sprint"],
+        expected_impact="降低返工率",
+    )
+    assert engine.propose(process, author_role="pmo", title="t", rollback_plan="r").risk_level == "medium"
+    organization = Candidate(
+        category="organization",
+        target="meeting_frequency",
+        change={"meeting_frequency": "daily"},
+        evidence=["target=meeting_frequency"],
+        expected_impact="提升同步效率",
+    )
+    assert engine.propose(organization, author_role="governance", title="t", rollback_plan="r").risk_level == "high"
+
+
+def test_risk_level_escalated_by_severity_evidence():
+    engine = EvolutionEngine()
+    escalated = _skill_candidate().model_copy(
+        update={"evidence": ["target=qa_testing", "severity=critical"]}
+    )
+    assert engine.propose(escalated, author_role="qa", title="t", rollback_plan="r").risk_level == "medium"
+
+
+# ---------------------------------------------------------------------------
+# 安全约束：自我扩权
+# ---------------------------------------------------------------------------
+
+
+def test_self_empowerment_rejected_at_propose():
+    engine = EvolutionEngine()
+    candidate = Candidate(
+        category="organization",
+        target="governance",
+        change={"approval_scope": {"governance": ["release"]}},
+        evidence=["target=governance"],
+        expected_impact="x",
+    )
+    with pytest.raises(EvolutionError, match="自我扩权"):
+        engine.propose(candidate, author_role="governance", title="扩权", rollback_plan="回滚")
+
+
+def test_self_empowerment_rejected_at_review():
+    engine = EvolutionEngine()
+    proposal = EvolutionProposal(
+        id="p-self",
+        title="自我扩权",
+        author_role="qa",
+        category="process",
+        target="gate",
+        change_diff="为 qa 岗位增加 permissions: [release]",
+        affected_roles=["qa"],
+        risk_level="medium",
+        rollback_plan="撤销权限变更",
+        owner="qa",
+    )
+    with pytest.raises(EvolutionError, match="自我扩权"):
+        engine.review(proposal, approver="governance", decision="approve")
+
+
+# ---------------------------------------------------------------------------
+# ④ 评审门
+# ---------------------------------------------------------------------------
+
+
+def _approved_proposal(engine: EvolutionEngine) -> EvolutionProposal:
+    proposal = engine.propose(
+        _skill_candidate(),
+        author_role="qa",
+        title="改善测试技能",
+        rollback_plan="回滚到 skill 版本 v0",
+    )
+    return engine.review(proposal, approver="governance", decision="approve", reason="LGTM")
+
+
+def test_review_approve_records_vote():
+    engine = EvolutionEngine()
+    proposal = engine.propose(
+        _skill_candidate(),
+        author_role="qa",
+        title="改善测试技能",
+        rollback_plan="回滚到 skill 版本 v0",
+    )
+    reviewed = engine.review(proposal, approver="governance", decision="approve", reason="LGTM")
+    assert reviewed.status == "approved"
+    assert len(reviewed.votes) == 1
+    assert reviewed.votes[0].by_role == "governance"
+    assert reviewed.votes[0].verdict == "approve"
+    assert reviewed.votes[0].reason == "LGTM"
+
+
+def test_review_reject_sets_status_and_reason():
+    engine = EvolutionEngine()
+    proposal = engine.propose(
+        _skill_candidate(),
+        author_role="qa",
+        title="改善测试技能",
+        rollback_plan="回滚到 skill 版本 v0",
+    )
+    reviewed = engine.review(proposal, approver="governance", decision="reject", reason="证据不足")
+    assert reviewed.status == "rejected"
+    assert reviewed.votes[0].verdict == "reject"
+    assert reviewed.votes[0].reason == "证据不足"
+
+
+def test_review_rejects_unknown_decision():
+    engine = EvolutionEngine()
+    proposal = engine.propose(
+        _skill_candidate(),
+        author_role="qa",
+        title="改善测试技能",
+        rollback_plan="回滚到 skill 版本 v0",
+    )
+    with pytest.raises(EvolutionError, match="评审结论"):
+        engine.review(proposal, approver="governance", decision="maybe")
+
+
+def test_review_requires_draft_or_voting_status():
+    engine = EvolutionEngine()
+    proposal = _approved_proposal(engine)
+    with pytest.raises(EvolutionError, match="draft/voting"):
+        engine.review(proposal, approver="governance", decision="approve")
+
+
+def test_l3_organization_auto_mode_accept_auto_rejects():
+    engine = EvolutionEngine()
+    organization = Candidate(
+        category="organization",
+        target="meeting_frequency",
+        change={"meeting_frequency": "weekly -> daily"},
+        evidence=["target=meeting_frequency"],
+        expected_impact="提升同步效率",
+    )
+    proposal = engine.propose(
+        organization,
+        author_role="governance",
+        title="调整站会频率",
+        rollback_plan="恢复 weekly",
+    )
+    reviewed = engine.review(
+        proposal,
+        approver="governance",
+        human_required=True,
+        auto_mode="accept",
+        decision="approve",
+    )
+    assert reviewed.status == "rejected"
+    assert reviewed.votes[-1].verdict == "reject"
+    assert reviewed.votes[-1].reason == BYPASS_IMMUNE_REASON
+
+
+def test_l3_organization_human_review_can_approve():
+    engine = EvolutionEngine()
+    organization = Candidate(
+        category="organization",
+        target="meeting_frequency",
+        change={"meeting_frequency": "weekly -> daily"},
+        evidence=["target=meeting_frequency"],
+        expected_impact="提升同步效率",
+    )
+    proposal = engine.propose(
+        organization,
+        author_role="governance",
+        title="调整站会频率",
+        rollback_plan="恢复 weekly",
+    )
+    reviewed = engine.review(
+        proposal,
+        approver="governance",
+        human_required=True,
+        auto_mode="ask",
+        decision="approve",
+        reason="人工审批通过",
+    )
+    assert reviewed.status == "approved"
+    assert reviewed.votes[-1].verdict == "approve"
+
+
+# ---------------------------------------------------------------------------
+# ⑤ 生效 / ⑥ 回滚
+# ---------------------------------------------------------------------------
+
+
+def test_apply_requires_approved():
+    engine = EvolutionEngine()
+    proposal = engine.propose(
+        _skill_candidate(),
+        author_role="qa",
+        title="改善测试技能",
+        rollback_plan="回滚到 skill 版本 v0",
+    )
+    with pytest.raises(EvolutionError, match="approved"):
+        engine.apply(proposal)
+
+
+def test_apply_bumps_version_and_sets_gray():
+    engine = EvolutionEngine()
+    proposal = _approved_proposal(engine)
+    applied = engine.apply(proposal)
+    assert applied.status == "applied"
+    assert applied.effective_version == "v1"
+    assert applied.gray is True
+
+
+def test_bump_version_helper():
+    assert bump_version("v0") == "v1"
+    assert bump_version("v1") == "v2"
+    assert bump_version("9") == "v10"
+    with pytest.raises(EvolutionError, match="版本号"):
+        bump_version("abc")
+
+
+def test_rollback_requires_applied():
+    engine = EvolutionEngine()
+    draft = engine.propose(
+        _skill_candidate(),
+        author_role="qa",
+        title="改善测试技能",
+        rollback_plan="回滚到 skill 版本 v0",
+    )
+    with pytest.raises(EvolutionError, match="applied"):
+        engine.rollback(draft, reason="不需要了")
+    approved = _approved_proposal(engine)
+    with pytest.raises(EvolutionError, match="applied"):
+        engine.rollback(approved, reason="不需要了")
+
+
+def test_rollback_sets_status_rolled_back():
+    engine = EvolutionEngine()
+    proposal = _approved_proposal(engine)
+    engine.apply(proposal)
+    rolled = engine.rollback(proposal, reason="指标恶化")
+    assert rolled.status == "rolled_back"
+    assert rolled.effective_version == "v1"
+
+
+# ---------------------------------------------------------------------------
+# 审计事件
+# ---------------------------------------------------------------------------
+
+
+def test_apply_and_rollback_emit_audit_events():
+    engine = EvolutionEngine()
+    bus = EventBus()
+    proposal = _approved_proposal(engine)
+    engine.apply(proposal, event_bus=bus)
+    engine.rollback(proposal, reason="指标恶化", event_bus=bus)
+
+    applied_events = bus.query(type="evolution_applied")
+    assert len(applied_events) == 1
+    assert applied_events[0].payload["proposal_id"] == proposal.id
+    assert applied_events[0].payload["effective_version"] == "v1"
+    assert applied_events[0].payload["gray"] is True
+
+    rolled_events = bus.query(type="evolution_rolled_back")
+    assert len(rolled_events) == 1
+    assert rolled_events[0].payload["proposal_id"] == proposal.id
+    assert rolled_events[0].payload["reason"] == "指标恶化"
+
+    # 引擎内部审计轨迹同样保留两条
+    assert [event.type for event in engine.audit_events] == [
+        "evolution_applied",
+        "evolution_rolled_back",
+    ]
+
+
+def test_apply_uses_engine_level_event_bus():
+    bus = EventBus()
+    engine = EvolutionEngine(event_bus=bus)
+    proposal = _approved_proposal(engine)
+    engine.apply(proposal)
+    assert len(bus.query(type="evolution_applied")) == 1
+
+
+# ---------------------------------------------------------------------------
+# 六步闭环端到端
+# ---------------------------------------------------------------------------
+
+
+def test_full_six_step_loop_end_to_end():
+    engine = EvolutionEngine()
+    bus = EventBus()
+    for event in _fabricated_events():
+        bus.publish(event)
+
+    # ① 收集
+    signals = engine.collect(bus)
+    assert len(signals) >= 3
+
+    # ② 提炼
+    candidates = engine.distill(signals)
+    assert candidates
+    skill_candidates = [candidate for candidate in candidates if candidate.category == "skill"]
+    assert any(candidate.target == "qa_testing" for candidate in skill_candidates)
+
+    # ③ 提案
+    target = next(candidate for candidate in candidates if candidate.category == "skill" and candidate.target == "qa_testing")
+    proposal = engine.propose(
+        target,
+        author_role="qa",
+        title="改善测试技能",
+        rollback_plan="回滚到 skill 版本 v0",
+        validation_plan="灰度 1 个 agent 观察 1 个迭代",
+    )
+    assert proposal.status == "draft"
+
+    # ④ 评审门（approve）
+    engine.review(proposal, approver="governance", decision="approve", reason="LGTM")
+    assert proposal.status == "approved"
+
+    # ⑤ 生效（灰度 + 版本化）
+    engine.apply(proposal, event_bus=bus)
+    assert proposal.status == "applied"
+    assert proposal.effective_version == "v1"
+    assert proposal.gray is True
+
+    # ⑥ 回滚（写回滚日志，进入下一轮收集）
+    engine.rollback(proposal, reason="灰度窗口指标恶化", event_bus=bus)
+    assert proposal.status == "rolled_back"
+
+    # 全程审计：apply + rollback 各一条事件
+    assert len(bus.query(type="evolution_applied")) == 1
+    assert len(bus.query(type="evolution_rolled_back")) == 1
+    # 下一轮收集能看到回滚信号（闭环自食）
+    next_signals = engine.collect(bus)
+    assert any(signal.type == "rollback_occurred" for signal in next_signals)
diff --git a/tests/test_metrics.py b/tests/test_metrics.py
new file mode 100644
index 0000000..08a74a7
--- /dev/null
+++ b/tests/test_metrics.py
@@ -0,0 +1,209 @@
+"""Task 6 行为测试：绩效度量采集（MetricsCollector）与阈值规则引擎（MetricRules）。"""
+
+from __future__ import annotations
+
+from datetime import datetime
+
+from agent_cluster.evolution import Signal
+from agent_cluster.metrics import (
+    BUILTIN_METRICS,
+    MetricPoint,
+    MetricRules,
+    MetricsCollector,
+    MetricsSnapshot,
+)
+
+
+def _collector() -> MetricsCollector:
+    collector = MetricsCollector()
+    collector.record("review_pass_rate", 0.9)
+    collector.record("rework_rate", 0.1, tags={"iteration": "iter-1"})
+    collector.record("action_item_close_rate", 0.8)
+    collector.record("loop_iterations", 1)
+    collector.record("loop_iterations", 2)
+    collector.record("loop_iterations", 3)
+    collector.record("gate_wait_seconds", 60)
+    return collector
+
+
+# ---------------------------------------------------------------------------
+# MetricsCollector：record / snapshot / reset
+# ---------------------------------------------------------------------------
+
+
+def test_record_snapshot_reset():
+    collector = _collector()
+    snapshot = collector.snapshot()
+    assert set(snapshot.metrics) == set(BUILTIN_METRICS)
+    assert snapshot.metrics["review_pass_rate"][0].value == 0.9
+    assert snapshot.metrics["rework_rate"][0].tags == {"iteration": "iter-1"}
+
+    collector.reset()
+    assert collector.snapshot().metrics == {}
+
+
+def test_snapshot_is_deep_copy():
+    collector = _collector()
+    snapshot = collector.snapshot()
+    snapshot.metrics["review_pass_rate"][0].value = 0.0
+    snapshot.metrics["extra"] = [MetricPoint(name="extra", value=1.0)]
+    fresh = collector.snapshot()
+    assert fresh.metrics["review_pass_rate"][0].value == 0.9
+    assert "extra" not in fresh.metrics
+
+
+def test_record_with_explicit_ts_and_tags():
+    collector = MetricsCollector()
+    ts = datetime(2026, 8, 1, 9, 0, 0)
+    collector.record("rework_rate", 0.5, tags={"iteration": "iter-2"}, ts=ts)
+    point = collector.snapshot().metrics["rework_rate"][0]
+    assert point.ts == ts
+    assert point.tags == {"iteration": "iter-2"}
+
+
+# ---------------------------------------------------------------------------
+# MetricRules：健康数据不触发
+# ---------------------------------------------------------------------------
+
+
+def test_healthy_data_returns_no_signals():
+    signals = MetricRules.evaluate(_collector().snapshot())
+    assert signals == []
+
+
+# ---------------------------------------------------------------------------
+# MetricRules：逐规则触发
+# ---------------------------------------------------------------------------
+
+
+def test_review_pass_rate_below_threshold_triggers_signal():
+    collector = MetricsCollector()
+    collector.record("review_pass_rate", 0.4)
+    signals = MetricRules.evaluate(collector.snapshot())
+    assert len(signals) == 1
+    signal = signals[0]
+    assert signal.type == "metric_threshold"
+    assert signal.severity == "high"
+    assert signal.evidence == ["review_pass_rate=0.4"]
+
+
+def test_rework_rate_above_threshold_triggers_signal():
+    collector = MetricsCollector()
+    collector.record("rework_rate", 0.5)
+    signals = MetricRules.evaluate(collector.snapshot())
+    assert len(signals) == 1
+    assert signals[0].severity == "high"
+    assert signals[0].evidence == ["rework_rate=0.5"]
+
+
+def test_rework_rate_uses_latest_iteration_window():
+    collector = MetricsCollector()
+    collector.record("rework_rate", 0.4, tags={"iteration": "iter-1"})
+    collector.record("rework_rate", 0.5, tags={"iteration": "iter-2"})
+    signals = MetricRules.evaluate(collector.snapshot())
+    assert len(signals) == 1
+    # 仅最新迭代窗口（iter-2）进入证据
+    assert signals[0].evidence == ["rework_rate=0.5"]
+
+
+def test_rework_rate_latest_window_healthy_no_signal():
+    collector = MetricsCollector()
+    collector.record("rework_rate", 0.4, tags={"iteration": "iter-1"})
+    collector.record("rework_rate", 0.1, tags={"iteration": "iter-2"})
+    assert MetricRules.evaluate(collector.snapshot()) == []
+
+
+def test_action_item_close_rate_below_threshold_triggers_signal():
+    collector = MetricsCollector()
+    collector.record("action_item_close_rate", 0.3)
+    signals = MetricRules.evaluate(collector.snapshot())
+    assert len(signals) == 1
+    assert signals[0].severity == "medium"
+    assert signals[0].evidence == ["action_item_close_rate=0.3"]
+
+
+def test_loop_iterations_spike_triggers_signal():
+    collector = MetricsCollector()
+    collector.record("loop_iterations", 1, ts=datetime(2026, 8, 1, 10, 0, 0))
+    collector.record("loop_iterations", 2, ts=datetime(2026, 8, 1, 10, 1, 0))
+    collector.record("loop_iterations", 10, ts=datetime(2026, 8, 1, 10, 2, 0))
+    signals = MetricRules.evaluate(collector.snapshot())
+    assert len(signals) == 1
+    signal = signals[0]
+    assert signal.type == "metric_threshold"
+    assert signal.severity == "medium"
+    assert signal.evidence == ["loop_iterations=1.0", "loop_iterations=2.0", "loop_iterations=10.0"]
+
+
+def test_loop_iterations_needs_history_for_spike():
+    collector = MetricsCollector()
+    collector.record("loop_iterations", 10)
+    assert MetricRules.evaluate(collector.snapshot()) == []
+
+
+def test_loop_iterations_healthy_no_spike():
+    collector = MetricsCollector()
+    collector.record("loop_iterations", 1, ts=datetime(2026, 8, 1, 10, 0, 0))
+    collector.record("loop_iterations", 2, ts=datetime(2026, 8, 1, 10, 1, 0))
+    collector.record("loop_iterations", 4, ts=datetime(2026, 8, 1, 10, 2, 0))  # 4 > 3 * 1.5 = 4.5? 否
+    assert MetricRules.evaluate(collector.snapshot()) == []
+
+
+def test_gate_wait_seconds_above_threshold_triggers_signal():
+    collector = MetricsCollector()
+    collector.record("gate_wait_seconds", 90000)
+    signals = MetricRules.evaluate(collector.snapshot())
+    assert len(signals) == 1
+    assert signals[0].severity == "medium"
+    assert signals[0].evidence == ["gate_wait_seconds=90000.0"]
+
+
+def test_evaluate_returns_signals_for_each_breach():
+    collector = MetricsCollector()
+    collector.record("review_pass_rate", 0.4)
+    collector.record("rework_rate", 0.5)
+    collector.record("action_item_close_rate", 0.3)
+    collector.record("loop_iterations", 1, ts=datetime(2026, 8, 1, 10, 0, 0))
+    collector.record("loop_iterations", 2, ts=datetime(2026, 8, 1, 10, 1, 0))
+    collector.record("loop_iterations", 12, ts=datetime(2026, 8, 1, 10, 2, 0))
+    collector.record("gate_wait_seconds", 90000)
+    signals = MetricRules.evaluate(collector.snapshot())
+    assert len(signals) == 5
+    for signal in signals:
+        assert isinstance(signal, Signal)
+        assert signal.type == "metric_threshold"
+        assert signal.source == "metric_rules"
+        assert signal.evidence
+
+
+# ---------------------------------------------------------------------------
+# MetricRules：阈值边界
+# ---------------------------------------------------------------------------
+
+
+def test_review_pass_rate_boundary():
+    healthy = MetricsSnapshot(metrics={"review_pass_rate": [MetricPoint(name="review_pass_rate", value=0.6)]})
+    assert MetricRules.evaluate(healthy) == []
+    breach = MetricsSnapshot(metrics={"review_pass_rate": [MetricPoint(name="review_pass_rate", value=0.599)]})
+    assert len(MetricRules.evaluate(breach)) == 1
+
+
+def test_rework_rate_boundary():
+    healthy = MetricsSnapshot(metrics={"rework_rate": [MetricPoint(name="rework_rate", value=0.3)]})
+    assert MetricRules.evaluate(healthy) == []
+    breach = MetricsSnapshot(metrics={"rework_rate": [MetricPoint(name="rework_rate", value=0.301)]})
+    assert len(MetricRules.evaluate(breach)) == 1
+
+
+def test_action_item_close_rate_boundary():
+    healthy = MetricsSnapshot(metrics={"action_item_close_rate": [MetricPoint(name="action_item_close_rate", value=0.5)]})
+    assert MetricRules.evaluate(healthy) == []
+    breach = MetricsSnapshot(metrics={"action_item_close_rate": [MetricPoint(name="action_item_close_rate", value=0.499)]})
+    assert len(MetricRules.evaluate(breach)) == 1
+
+
+def test_gate_wait_seconds_boundary():
+    healthy = MetricsSnapshot(metrics={"gate_wait_seconds": [MetricPoint(name="gate_wait_seconds", value=86400.0)]})
+    assert MetricRules.evaluate(healthy) == []
+    breach = MetricsSnapshot(metrics={"gate_wait_seconds": [MetricPoint(name="gate_wait_seconds", value=86400.1)]})
+    assert len(MetricRules.evaluate(breach)) == 1
```
