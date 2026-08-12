"""进化闭环模块：设计文档 §6.2 六步进化闭环 + §6.5 安全治理。

六步闭环 API 映射（collect -> distill -> propose -> review -> apply -> rollback）：

① 收集 ``collect(events) -> list[Signal]``
   规则扫描事件流（EventBus / list[Event]），产出信号：指标越界
   （metric_threshold）、评审重复驳回（review_failure，LBTM）、复盘根因
   （retro_root_cause）、回滚事件（rollback_occurred）；相同信号去重。

② 提炼 ``distill(signals) -> list[Candidate]``
   按 category+target 归并同类信号、合并去重证据、过滤噪音
   （severity=low 且无 evidence 的信号直接丢弃）。

③ 提案 ``propose(candidate, *, author_role, title, rollback_plan, validation_plan)``
   强制"理由+结论"双字段：缺 rollback_plan（空/空白）直接拒绝；
   按类别推导风险等级（organization=high / process=medium /
   skill/knowledge=low，证据含 severity=high|critical 时升一级）；
   执行自我扩权校验。

④ 评审门 ``review(proposal, *, approver, human_required, auto_mode, decision, reason)``
   L3 组织流程变更必须人工审批：human_required=True 且 auto_mode != "ask"
   时自动驳回（bypass-immune: 组织流程变更必须人工审批）；
   其余按 approver 的 decision 置为 approved / rejected 并记录 Vote。

⑤ 生效 ``apply(proposal, *, event_bus)``
   仅 approved 可生效：effective_version 自增（v0->v1->...）、
   置灰度标志 gray=True、状态 applied，并写审计事件 evolution_applied。

⑥ 回滚 ``rollback(proposal, *, reason, event_bus)``
   仅 applied 可回滚：状态置 rolled_back，写审计事件 evolution_rolled_back
   （回滚本身进入下一轮 ① 的 collect 输入）。

安全约束（§6.5）：``assert_no_self_empowerment`` 禁止提案变更修改自身岗位
的审批范围/权限（approval_scope / permissions / 权限 / 提权），在
``propose`` 与 ``review`` 两处均执行校验。
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from agent_cluster.models import Event, Vote
from agent_cluster.runtime import EventBus

__all__ = [
    "Signal",
    "Candidate",
    "EvolutionProposal",
    "EvolutionEngine",
    "EvolutionError",
    "bump_version",
]

# 信号类型 -> 进化对象类别（§6.1 四类）默认映射
SIGNAL_TYPE_CATEGORY: dict[str, str] = {
    "metric_threshold": "process",
    "review_failure": "skill",
    "retro_root_cause": "knowledge",
}

# 自我扩权校验关键词（命中即拒绝）
SELF_EMPOWERMENT_KEYWORDS: tuple[str, ...] = (
    "approval_scope",
    "permissions",
    "permission",
    "提权",
    "权限",
)

# 风险等级排序（用于证据提示升级）
RISK_RANK: dict[str, int] = {"low": 0, "medium": 1, "high": 2}
SEVERITY_RANK: dict[str, int] = {"low": 0, "medium": 1, "high": 2, "critical": 3}


class EvolutionError(Exception):
    """进化闭环业务错误（回滚方案缺失 / 状态机越权 / 自我扩权等）。"""


def bump_version(version: str) -> str:
    """版本自增：``v0 -> v1 -> v2 ...``；兼容不带 ``v`` 前缀的纯数字版本。"""
    core = version[1:] if version.startswith("v") else version
    try:
        number = int(core)
    except ValueError as exc:
        raise EvolutionError(f"无法解析版本号：{version!r}（期望形如 v0）") from exc
    return f"v{number + 1}"


def _dedupe(items: list[str]) -> list[str]:
    """保序去重字符串列表。"""
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result


def _change_to_text(change_diff: dict | str) -> str:
    """把 change_diff（dict 或 str）统一序列化为可检索文本。"""
    if isinstance(change_diff, str):
        return change_diff
    return json.dumps(change_diff, ensure_ascii=False)


def _tag_value(entries: list[str], tag: str) -> str | None:
    """从 evidence 条目中解析 ``tag=value`` 形式的标签（如 target=xxx / category=xxx / role=xxx）。"""
    for entry in entries:
        if entry.startswith(f"{tag}="):
            return entry.split("=", 1)[1]
    return None


class Signal(BaseModel):
    """进化信号（闭环①的输出：可观测性聚合产物）。"""

    model_config = ConfigDict(extra="ignore")

    id: str = Field(description="信号唯一标识")
    type: str = Field(description="信号类型，如 metric_threshold / review_failure / retro_root_cause")
    source: str = Field(description="信号来源（指标名 / 目标 / 复盘来源等）")
    evidence: list[str] = Field(default_factory=list, description="证据条目列表")
    severity: Literal["low", "medium", "high", "critical"] = Field(
        default="medium", description="信号严重度"
    )
    ts: datetime = Field(default_factory=datetime.now, description="信号产生时间")


class Candidate(BaseModel):
    """进化候选（闭环②的输出：去重合并后的分诊结果）。"""

    model_config = ConfigDict(extra="ignore")

    category: Literal["skill", "knowledge", "process", "organization"] = Field(
        description="进化对象类别（§6.1 四类）"
    )
    target: str = Field(description="进化目标（技能名 / 流程名 / 角色分工等）")
    change: dict = Field(description="变更内容（diff 骨架，propose 时形式化为 change_diff）")
    evidence: list[str] = Field(default_factory=list, description="支撑证据")
    expected_impact: str = Field(default="", description="预期影响")


class EvolutionProposal(BaseModel):
    """进化提案（闭环③的输出，进入评审门）。

    校验规则：``rollback_plan`` 为空/空白时构造即抛 ValidationError；
    ``propose`` 同样显式拒绝缺回滚方案的候选。
    """

    model_config = ConfigDict(extra="ignore")

    id: str = Field(description="提案唯一标识")
    title: str = Field(description="提案标题")
    author_role: str = Field(description="提案人岗位 id")
    category: Literal["skill", "knowledge", "process", "organization"] = Field(
        description="进化对象类别（§6.1 四类）"
    )
    target: str = Field(description="进化目标")
    change_diff: dict | str = Field(description="变更 diff（dict 或文本 diff）")
    affected_roles: list[str] = Field(default_factory=list, description="受影响岗位")
    affected_workflows: list[str] = Field(default_factory=list, description="受影响流程")
    risk_level: Literal["low", "medium", "high"] = Field(description="风险等级")
    validation_plan: str = Field(default="", description="验证方案")
    rollback_plan: str = Field(description="回滚方案（强制，缺省校验失败）")
    owner: str = Field(default="", description="负责人")
    status: Literal["draft", "voting", "approved", "rejected", "applied", "rolled_back"] = Field(
        default="draft", description="提案状态（六态状态机）"
    )
    gray: bool = Field(default=False, description="灰度标志：生效时置 True（试点观察）")
    effective_version: str = Field(default="v0", description="生效版本号，自增 v0->v1...")
    votes: list[Vote] = Field(default_factory=list, description="评审投票记录")
    created_ts: datetime = Field(default_factory=datetime.now, description="创建时间")
    updated_ts: datetime = Field(default_factory=datetime.now, description="更新时间")

    @field_validator("rollback_plan")
    @classmethod
    def _rollback_plan_must_not_be_empty(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("提案必须提供 rollback_plan（回滚方案）")
        return value


class EvolutionEngine:
    """六步进化闭环引擎（§6.2）：collect -> distill -> propose -> review -> apply -> rollback。

    - 内部维护审计轨迹（``audit_events``），apply/rollback 同时写入
      传入的 EventBus（若提供），保证"每次进化落审计"。
    """

    def __init__(
        self,
        *,
        event_bus: EventBus | None = None,
        review_rejection_threshold: int = 2,
    ) -> None:
        self._event_bus: EventBus | None = event_bus
        self.review_rejection_threshold: int = review_rejection_threshold
        self._audit_events: list[Event] = []

    # ------------------------------------------------------------------
    # ① 收集
    # ------------------------------------------------------------------

    def collect(self, events: list[Event] | EventBus) -> list[Signal]:
        """规则扫描事件流，产出 Signals（相同信号去重）。

        规则：
        - ``metric_threshold`` 事件 -> 指标越界信号（type=metric_threshold）；
        - ``review_result`` 事件 verdict 为 reject/lbtm，同一 target 累计
          达 ``review_rejection_threshold`` 次 -> 评审失败信号（review_failure）；
        - ``retro`` 事件携带 root_cause -> 复盘根因信号（retro_root_cause）；
        - ``evolution_rolled_back`` 事件 -> 回滚信号（rollback_occurred），
          回滚本身进入下一轮 ① 的收集输入。
        """
        event_list = events.events if isinstance(events, EventBus) else list(events)
        signals: list[Signal] = []
        rejections: dict[str, list[Event]] = {}
        for event in event_list:
            payload = event.payload or {}
            if event.type == "metric_threshold":
                signals.append(
                    Signal(
                        id=uuid.uuid4().hex,
                        type="metric_threshold",
                        source=payload.get("source") or event.actor or "metrics",
                        evidence=payload.get("evidence") or [str(payload.get("metric", "metric_threshold"))],
                        severity=payload.get("severity", "medium"),
                        ts=event.ts,
                    )
                )
            elif event.type == "review_result":
                verdict = str(payload.get("verdict", "")).lower()
                if verdict in ("reject", "rejected", "lbtm"):
                    target = payload.get("target") or event.thread_id or event.actor
                    rejections.setdefault(target, []).append(event)
            elif event.type == "retro":
                root_cause = payload.get("root_cause")
                if root_cause:
                    causes = root_cause if isinstance(root_cause, list) else [root_cause]
                    for cause in causes:
                        signals.append(
                            Signal(
                                id=uuid.uuid4().hex,
                                type="retro_root_cause",
                                source=payload.get("source") or event.actor or "retro",
                                evidence=[str(cause)],
                                severity="medium",
                                ts=event.ts,
                            )
                        )
            elif event.type == "evolution_rolled_back":
                reason = payload.get("reason", "")
                evidence = [reason] if reason else []
                signals.append(
                    Signal(
                        id=uuid.uuid4().hex,
                        type="rollback_occurred",
                        source=payload.get("proposal_id") or event.actor or "evolution",
                        evidence=evidence,
                        severity="medium",
                        ts=event.ts,
                    )
                )
        for target, target_events in rejections.items():
            if len(target_events) >= self.review_rejection_threshold:
                signals.append(
                    Signal(
                        id=uuid.uuid4().hex,
                        type="review_failure",
                        source=target,
                        evidence=[
                            f"{item.type}:{item.payload.get('verdict', '')}:{item.payload.get('target', '')}"
                            for item in target_events
                        ],
                        severity="high" if len(target_events) >= 3 else "medium",
                        ts=target_events[-1].ts,
                    )
                )
        return self._dedupe_signals(signals)

    @staticmethod
    def _dedupe_signals(signals: list[Signal]) -> list[Signal]:
        """按内容（type+source+severity+evidence）去重，保留首个。"""
        seen: set[tuple] = set()
        result: list[Signal] = []
        for signal in signals:
            key = (signal.type, signal.source, signal.severity, tuple(signal.evidence))
            if key in seen:
                continue
            seen.add(key)
            result.append(signal)
        return result

    # ------------------------------------------------------------------
    # ② 提炼
    # ------------------------------------------------------------------

    def distill(self, signals: list[Signal]) -> list[Candidate]:
        """按 category+target 归并信号 -> Candidates；过滤噪音信号。"""
        groups: dict[tuple[str, str], list[Signal]] = {}
        for signal in signals:
            if signal.severity == "low" and not signal.evidence:
                continue  # 噪音：低严重度且无证据
            category = self._category_for(signal)
            target = self._target_for(signal)
            groups.setdefault((category, target), []).append(signal)

        candidates: list[Candidate] = []
        for (category, target), group in groups.items():
            evidence = _dedupe([entry for signal in group for entry in signal.evidence])
            top_severity = max((SEVERITY_RANK.get(signal.severity, 0) for signal in group), default=0)
            severity_label = next(
                (label for label, rank in SEVERITY_RANK.items() if rank == top_severity), "low"
            )
            candidates.append(
                Candidate(
                    category=category,  # type: ignore[arg-type]
                    target=target,
                    change={"kind": "improve", "target": target},
                    evidence=evidence,
                    expected_impact=(
                        f"改善 {target} 的失败模式（聚合信号 {len(group)} 个，"
                        f"最高严重度 {severity_label}）"
                    ),
                )
            )
        return candidates

    @staticmethod
    def _category_for(signal: Signal) -> str:
        """信号 -> 进化对象类别：evidence 标签 category= 优先，否则按信号类型映射。"""
        tagged = _tag_value(signal.evidence, "category")
        if tagged in ("skill", "knowledge", "process", "organization"):
            return tagged
        return SIGNAL_TYPE_CATEGORY.get(signal.type, "process")

    @staticmethod
    def _target_for(signal: Signal) -> str:
        """信号 -> 进化目标：evidence 标签 target= 优先，否则取信号来源。"""
        tagged = _tag_value(signal.evidence, "target")
        return tagged or signal.source or signal.type

    # ------------------------------------------------------------------
    # ③ 提案
    # ------------------------------------------------------------------

    def propose(
        self,
        candidate: Candidate,
        *,
        author_role: str,
        title: str,
        rollback_plan: str,
        validation_plan: str = "",
    ) -> EvolutionProposal:
        """候选 -> 提案：强制回滚方案 + 风险等级推导 + 自我扩权校验。"""
        if not rollback_plan or not rollback_plan.strip():
            raise EvolutionError("提案必须提供 rollback_plan（回滚方案），拒绝提交")
        now = datetime.now()
        proposal = EvolutionProposal(
            id=uuid.uuid4().hex,
            title=title,
            author_role=author_role,
            category=candidate.category,
            target=candidate.target,
            change_diff=candidate.change,
            affected_roles=self._affected_roles_for(candidate, author_role),
            affected_workflows=self._affected_workflows_for(candidate),
            risk_level=self._risk_level_for(candidate),
            validation_plan=validation_plan,
            rollback_plan=rollback_plan.strip(),
            owner=author_role,
            status="draft",
            created_ts=now,
            updated_ts=now,
        )
        self.assert_no_self_empowerment(proposal)
        return proposal

    @staticmethod
    def _risk_level_for(candidate: Candidate) -> str:
        """按类别推导风险等级；证据含 severity=high/critical 时升一级。"""
        base = {"organization": "high", "process": "medium", "skill": "low", "knowledge": "low"}[
            candidate.category
        ]
        rank = RISK_RANK[base]
        if any("severity=high" in entry or "severity=critical" in entry for entry in candidate.evidence):
            rank = min(rank + 1, RISK_RANK["high"])
        return next(label for label, value in RISK_RANK.items() if value == rank)

    @staticmethod
    def _affected_roles_for(candidate: Candidate, author_role: str) -> list[str]:
        """受影响岗位：evidence 中 role= 标签优先，缺省为提案人岗位。"""
        roles = [entry.split("=", 1)[1] for entry in candidate.evidence if entry.startswith("role=")]
        return _dedupe(roles) or [author_role]

    @staticmethod
    def _affected_workflows_for(candidate: Candidate) -> list[str]:
        """受影响流程：evidence 中 workflow= 标签列表。"""
        workflows = [
            entry.split("=", 1)[1] for entry in candidate.evidence if entry.startswith("workflow=")
        ]
        return _dedupe(workflows)

    # ------------------------------------------------------------------
    # ④ 评审门
    # ------------------------------------------------------------------

    def review(
        self,
        proposal: EvolutionProposal,
        *,
        approver: str,
        human_required: bool = False,
        auto_mode: str = "ask",
        decision: str = "approve",
        reason: str = "",
    ) -> EvolutionProposal:
        """评审门：L3 组织流程必须人工；无人值守自动驳回；其余按 approver 决策。

        - ``human_required=True`` 且 ``auto_mode != "ask"``：自动驳回
          （bypass-immune），即使 decision=approve 也不放行；
        - 否则依据 ``decision``（approve/reject）置状态并记录 Vote。
        """
        if proposal.status not in ("draft", "voting"):
            raise EvolutionError(f"仅 draft/voting 状态提案可评审，当前状态：{proposal.status}")
        self.assert_no_self_empowerment(proposal)
        if human_required and auto_mode != "ask":
            auto_reason = "bypass-immune: 组织流程变更必须人工审批"
            proposal.status = "rejected"
            proposal.votes.append(
                Vote(by_role=approver, verdict="reject", reason=auto_reason, ts=datetime.now())
            )
            proposal.updated_ts = datetime.now()
            return proposal
        if decision not in ("approve", "reject"):
            raise EvolutionError(f"未知评审结论：{decision!r}（仅支持 approve/reject）")
        proposal.status = "approved" if decision == "approve" else "rejected"
        proposal.votes.append(
            Vote(by_role=approver, verdict=decision, reason=reason, ts=datetime.now())
        )
        proposal.updated_ts = datetime.now()
        return proposal

    # ------------------------------------------------------------------
    # ⑤ 生效 / ⑥ 回滚
    # ------------------------------------------------------------------

    def apply(
        self,
        proposal: EvolutionProposal,
        *,
        event_bus: EventBus | None = None,
    ) -> EvolutionProposal:
        """生效：仅 approved 可应用；版本自增 + 灰度标志；写审计事件。"""
        if proposal.status != "approved":
            raise EvolutionError(f"仅 approved 提案可生效，当前状态：{proposal.status}")
        proposal.effective_version = bump_version(proposal.effective_version)
        proposal.gray = True
        proposal.status = "applied"
        proposal.updated_ts = datetime.now()
        self._emit(
            event_type="evolution_applied",
            proposal=proposal,
            event_bus=event_bus,
            extra={
                "effective_version": proposal.effective_version,
                "gray": proposal.gray,
            },
        )
        return proposal

    def rollback(
        self,
        proposal: EvolutionProposal,
        *,
        reason: str,
        event_bus: EventBus | None = None,
    ) -> EvolutionProposal:
        """回滚：仅 applied 可回滚；写审计事件（回滚本身进入下一轮 collect）。"""
        if proposal.status != "applied":
            raise EvolutionError(f"仅 applied 提案可回滚，当前状态：{proposal.status}")
        proposal.status = "rolled_back"
        proposal.updated_ts = datetime.now()
        self._emit(
            event_type="evolution_rolled_back",
            proposal=proposal,
            event_bus=event_bus,
            extra={"reason": reason},
        )
        return proposal

    def _emit(
        self,
        *,
        event_type: str,
        proposal: EvolutionProposal,
        event_bus: EventBus | None,
        extra: dict,
    ) -> None:
        """构造审计 Event：优先写外部 event_bus（参数 > 引擎构造传入），同时留存内部审计轨迹。"""
        event = Event(
            id=uuid.uuid4().hex,
            run_id="",
            thread_id="",
            type=event_type,
            actor=proposal.owner,
            payload={"proposal_id": proposal.id, "title": proposal.title, **extra},
        )
        self._audit_events.append(event)
        target_bus = event_bus if event_bus is not None else self._event_bus
        if target_bus is not None:
            target_bus.publish(event)

    @property
    def audit_events(self) -> list[Event]:
        """引擎内部审计轨迹（不可变拷贝）。"""
        return list(self._audit_events)

    # ------------------------------------------------------------------
    # 安全约束（§6.5）
    # ------------------------------------------------------------------

    def assert_no_self_empowerment(self, proposal: EvolutionProposal) -> None:
        """自我扩权校验：变更内容命中权限类关键词（approval_scope/permissions/权限/提权）即拒绝。"""
        change_text = _change_to_text(proposal.change_diff).lower()
        for keyword in SELF_EMPOWERMENT_KEYWORDS:
            if keyword.lower() in change_text:
                raise EvolutionError(
                    f"自我扩权校验失败：提案（{proposal.id}）变更不得修改自身岗位权限，"
                    f"命中关键词 {keyword!r}"
                )
