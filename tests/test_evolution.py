"""Task 6 行为测试：六步进化闭环（collect->distill->propose->review->apply->rollback）+ 安全治理。"""

from __future__ import annotations

from datetime import datetime

import pytest
from pydantic import ValidationError

from agent_cluster.evolution import (
    Candidate,
    EvolutionEngine,
    EvolutionError,
    EvolutionProposal,
    Signal,
    bump_version,
)
from agent_cluster.models import Event
from agent_cluster.runtime import EventBus

BYPASS_IMMUNE_REASON = "bypass-immune: 组织流程变更必须人工审批"


def _event(
    event_type: str,
    *,
    payload: dict | None = None,
    actor: str = "qa",
    ts: datetime | None = None,
) -> Event:
    return Event(
        id=f"evt-{event_type}-{actor}-{id(payload)}",
        run_id="run-1",
        thread_id="thread-1",
        type=event_type,
        actor=actor,
        payload=payload or {},
        ts=ts or datetime(2026, 8, 1, 12, 0, 0),
    )


def _fabricated_events() -> list[Event]:
    """构造闭环①输入事件：指标越界 + 2 次同类评审驳回 + 复盘根因。"""
    return [
        _event(
            "metric_threshold",
            payload={
                "metric": "review_pass_rate",
                "evidence": ["review_pass_rate=0.42"],
                "severity": "high",
                "source": "metrics:review_pass_rate",
            },
            actor="metrics",
        ),
        _event("review_result", payload={"verdict": "lbtm", "target": "qa_testing"}, actor="reviewer"),
        _event("review_result", payload={"verdict": "reject", "target": "qa_testing"}, actor="reviewer"),
        _event(
            "retro",
            payload={"root_cause": ["测试用例覆盖不足", "缺乏边界样例"]},
            actor="retro_agent",
        ),
    ]


# ---------------------------------------------------------------------------
# ① 收集
# ---------------------------------------------------------------------------


def test_collect_produces_signals_from_events():
    engine = EvolutionEngine()
    signals = engine.collect(_fabricated_events())
    types = {signal.type for signal in signals}
    assert types == {"metric_threshold", "review_failure", "retro_root_cause"}
    metric = next(signal for signal in signals if signal.type == "metric_threshold")
    assert metric.source == "metrics:review_pass_rate"
    assert metric.severity == "high"
    assert metric.evidence == ["review_pass_rate=0.42"]


def test_collect_accepts_event_bus_and_dedupes_identical_signals():
    bus = EventBus()
    for event in _fabricated_events():
        bus.publish(event)
    # 再补一条内容完全相同的指标越界事件 -> 应被去重
    bus.publish(_event("metric_threshold", payload={"metric": "review_pass_rate", "evidence": ["review_pass_rate=0.42"], "severity": "high", "source": "metrics:review_pass_rate"}, actor="metrics"))
    engine = EvolutionEngine()
    signals = engine.collect(bus)
    metric_signals = [signal for signal in signals if signal.type == "metric_threshold"]
    assert len(metric_signals) == 1


def test_collect_repeated_rejection_threshold_not_reached():
    engine = EvolutionEngine()
    events = [_event("review_result", payload={"verdict": "lbtm", "target": "qa_testing"}, actor="reviewer")]
    signals = engine.collect(events)
    assert [signal.type for signal in signals] == []


def test_collect_rollback_event_feeds_next_round():
    engine = EvolutionEngine()
    bus = EventBus()
    proposal = _approved_proposal(engine)
    engine.apply(proposal, event_bus=bus)
    engine.rollback(proposal, reason="指标恶化", event_bus=bus)
    signals = engine.collect(bus)
    rollback_signals = [signal for signal in signals if signal.type == "rollback_occurred"]
    assert len(rollback_signals) == 1
    assert rollback_signals[0].evidence == ["指标恶化"]


# ---------------------------------------------------------------------------
# ② 提炼
# ---------------------------------------------------------------------------


def test_distill_merges_and_drops_noise():
    engine = EvolutionEngine()
    signals = [
        Signal(
            id="s1",
            type="review_failure",
            source="qa_testing",
            evidence=["target=qa_testing", "review_failure:lbtm"],
            severity="medium",
            ts=datetime(2026, 8, 1, 12, 0, 0),
        ),
        Signal(
            id="s2",
            type="review_failure",
            source="qa_testing",
            evidence=["target=qa_testing", "review_failure:reject"],
            severity="high",
            ts=datetime(2026, 8, 1, 13, 0, 0),
        ),
        Signal(
            id="s3",
            type="metric_threshold",
            source="noise",
            evidence=[],
            severity="low",
            ts=datetime(2026, 8, 1, 12, 0, 0),
        ),
    ]
    candidates = engine.distill(signals)
    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.category == "skill"
    assert candidate.target == "qa_testing"
    assert candidate.evidence == ["target=qa_testing", "review_failure:lbtm", "review_failure:reject"]
    assert "2 个" in candidate.expected_impact
    assert "high" in candidate.expected_impact


def test_distill_no_signals_returns_empty():
    engine = EvolutionEngine()
    assert engine.distill([]) == []


# ---------------------------------------------------------------------------
# ③ 提案
# ---------------------------------------------------------------------------


def _skill_candidate() -> Candidate:
    return Candidate(
        category="skill",
        target="qa_testing",
        change={"skill": "qa-testing", "patch": "补充边界样例模板"},
        evidence=["target=qa_testing", "role=qa"],
        expected_impact="降低 LBTM 驳回率",
    )


def test_propose_requires_rollback_plan():
    engine = EvolutionEngine()
    candidate = _skill_candidate()
    with pytest.raises(EvolutionError, match="rollback_plan"):
        engine.propose(candidate, author_role="qa", title="改善测试技能", rollback_plan="")
    with pytest.raises(EvolutionError, match="rollback_plan"):
        engine.propose(candidate, author_role="qa", title="改善测试技能", rollback_plan="   ")


def test_proposal_model_rejects_missing_rollback_plan():
    with pytest.raises(ValidationError, match="rollback_plan"):
        EvolutionProposal(
            id="p-empty",
            title="缺回滚方案",
            author_role="qa",
            category="skill",
            target="qa_testing",
            change_diff={"skill": "qa-testing"},
            risk_level="low",
            rollback_plan="",
        )
    with pytest.raises(ValidationError, match="rollback_plan"):
        EvolutionProposal(
            id="p-blank",
            title="空白回滚方案",
            author_role="qa",
            category="skill",
            target="qa_testing",
            change_diff={"skill": "qa-testing"},
            risk_level="low",
            rollback_plan=" \t ",
        )


def test_propose_builds_draft_proposal_with_derived_fields():
    engine = EvolutionEngine()
    proposal = engine.propose(
        _skill_candidate(),
        author_role="qa",
        title="改善测试技能",
        rollback_plan="回滚到 skill 版本 v0",
        validation_plan="灰度 1 个 agent 观察 1 个迭代",
    )
    assert proposal.status == "draft"
    assert proposal.category == "skill"
    assert proposal.risk_level == "low"
    assert proposal.effective_version == "v0"
    assert proposal.gray is False
    assert proposal.owner == "qa"
    assert proposal.affected_roles == ["qa"]
    assert proposal.change_diff == {"skill": "qa-testing", "patch": "补充边界样例模板"}
    assert proposal.validation_plan == "灰度 1 个 agent 观察 1 个迭代"
    assert proposal.rollback_plan == "回滚到 skill 版本 v0"


def test_risk_level_derived_from_category():
    engine = EvolutionEngine()
    assert engine.propose(_skill_candidate(), author_role="qa", title="t", rollback_plan="r").risk_level == "low"
    knowledge = Candidate(
        category="knowledge",
        target="坑位库",
        change={"knowledge": "新增坑位"},
        evidence=["target=坑位库"],
        expected_impact="减少重复踩坑",
    )
    assert engine.propose(knowledge, author_role="qa", title="t", rollback_plan="r").risk_level == "low"
    process = Candidate(
        category="process",
        target="fullstack-sprint",
        change={"process": "新增返工边"},
        evidence=["target=fullstack-sprint"],
        expected_impact="降低返工率",
    )
    assert engine.propose(process, author_role="pmo", title="t", rollback_plan="r").risk_level == "medium"
    organization = Candidate(
        category="organization",
        target="meeting_frequency",
        change={"meeting_frequency": "daily"},
        evidence=["target=meeting_frequency"],
        expected_impact="提升同步效率",
    )
    assert engine.propose(organization, author_role="governance", title="t", rollback_plan="r").risk_level == "high"


def test_risk_level_escalated_by_severity_evidence():
    engine = EvolutionEngine()
    escalated = _skill_candidate().model_copy(
        update={"evidence": ["target=qa_testing", "severity=critical"]}
    )
    assert engine.propose(escalated, author_role="qa", title="t", rollback_plan="r").risk_level == "medium"


# ---------------------------------------------------------------------------
# 安全约束：自我扩权
# ---------------------------------------------------------------------------


def test_self_empowerment_rejected_at_propose():
    engine = EvolutionEngine()
    candidate = Candidate(
        category="organization",
        target="governance",
        change={"approval_scope": {"governance": ["release"]}},
        evidence=["target=governance"],
        expected_impact="x",
    )
    with pytest.raises(EvolutionError, match="自我扩权"):
        engine.propose(candidate, author_role="governance", title="扩权", rollback_plan="回滚")


def test_self_empowerment_rejected_at_review():
    engine = EvolutionEngine()
    proposal = EvolutionProposal(
        id="p-self",
        title="自我扩权",
        author_role="qa",
        category="process",
        target="gate",
        change_diff="为 qa 岗位增加 permissions: [release]",
        affected_roles=["qa"],
        risk_level="medium",
        rollback_plan="撤销权限变更",
        owner="qa",
    )
    with pytest.raises(EvolutionError, match="自我扩权"):
        engine.review(proposal, approver="governance", decision="approve")


# ---------------------------------------------------------------------------
# ④ 评审门
# ---------------------------------------------------------------------------


def _approved_proposal(engine: EvolutionEngine) -> EvolutionProposal:
    proposal = engine.propose(
        _skill_candidate(),
        author_role="qa",
        title="改善测试技能",
        rollback_plan="回滚到 skill 版本 v0",
    )
    return engine.review(proposal, approver="governance", decision="approve", reason="LGTM")


def test_review_approve_records_vote():
    engine = EvolutionEngine()
    proposal = engine.propose(
        _skill_candidate(),
        author_role="qa",
        title="改善测试技能",
        rollback_plan="回滚到 skill 版本 v0",
    )
    reviewed = engine.review(proposal, approver="governance", decision="approve", reason="LGTM")
    assert reviewed.status == "approved"
    assert len(reviewed.votes) == 1
    assert reviewed.votes[0].by_role == "governance"
    assert reviewed.votes[0].verdict == "approve"
    assert reviewed.votes[0].reason == "LGTM"


def test_review_reject_sets_status_and_reason():
    engine = EvolutionEngine()
    proposal = engine.propose(
        _skill_candidate(),
        author_role="qa",
        title="改善测试技能",
        rollback_plan="回滚到 skill 版本 v0",
    )
    reviewed = engine.review(proposal, approver="governance", decision="reject", reason="证据不足")
    assert reviewed.status == "rejected"
    assert reviewed.votes[0].verdict == "reject"
    assert reviewed.votes[0].reason == "证据不足"


def test_review_rejects_unknown_decision():
    engine = EvolutionEngine()
    proposal = engine.propose(
        _skill_candidate(),
        author_role="qa",
        title="改善测试技能",
        rollback_plan="回滚到 skill 版本 v0",
    )
    with pytest.raises(EvolutionError, match="评审结论"):
        engine.review(proposal, approver="governance", decision="maybe")


def test_review_requires_draft_or_voting_status():
    engine = EvolutionEngine()
    proposal = _approved_proposal(engine)
    with pytest.raises(EvolutionError, match="draft/voting"):
        engine.review(proposal, approver="governance", decision="approve")


def test_l3_organization_auto_mode_accept_auto_rejects():
    engine = EvolutionEngine()
    organization = Candidate(
        category="organization",
        target="meeting_frequency",
        change={"meeting_frequency": "weekly -> daily"},
        evidence=["target=meeting_frequency"],
        expected_impact="提升同步效率",
    )
    proposal = engine.propose(
        organization,
        author_role="governance",
        title="调整站会频率",
        rollback_plan="恢复 weekly",
    )
    reviewed = engine.review(
        proposal,
        approver="governance",
        human_required=True,
        auto_mode="accept",
        decision="approve",
    )
    assert reviewed.status == "rejected"
    assert reviewed.votes[-1].verdict == "reject"
    assert reviewed.votes[-1].reason == BYPASS_IMMUNE_REASON


def test_l3_organization_human_review_can_approve():
    engine = EvolutionEngine()
    organization = Candidate(
        category="organization",
        target="meeting_frequency",
        change={"meeting_frequency": "weekly -> daily"},
        evidence=["target=meeting_frequency"],
        expected_impact="提升同步效率",
    )
    proposal = engine.propose(
        organization,
        author_role="governance",
        title="调整站会频率",
        rollback_plan="恢复 weekly",
    )
    reviewed = engine.review(
        proposal,
        approver="governance",
        human_required=True,
        auto_mode="ask",
        decision="approve",
        reason="人工审批通过",
    )
    assert reviewed.status == "approved"
    assert reviewed.votes[-1].verdict == "approve"


# ---------------------------------------------------------------------------
# ⑤ 生效 / ⑥ 回滚
# ---------------------------------------------------------------------------


def test_apply_requires_approved():
    engine = EvolutionEngine()
    proposal = engine.propose(
        _skill_candidate(),
        author_role="qa",
        title="改善测试技能",
        rollback_plan="回滚到 skill 版本 v0",
    )
    with pytest.raises(EvolutionError, match="approved"):
        engine.apply(proposal)


def test_apply_bumps_version_and_sets_gray():
    engine = EvolutionEngine()
    proposal = _approved_proposal(engine)
    applied = engine.apply(proposal)
    assert applied.status == "applied"
    assert applied.effective_version == "v1"
    assert applied.gray is True


def test_bump_version_helper():
    assert bump_version("v0") == "v1"
    assert bump_version("v1") == "v2"
    assert bump_version("9") == "v10"
    with pytest.raises(EvolutionError, match="版本号"):
        bump_version("abc")


def test_rollback_requires_applied():
    engine = EvolutionEngine()
    draft = engine.propose(
        _skill_candidate(),
        author_role="qa",
        title="改善测试技能",
        rollback_plan="回滚到 skill 版本 v0",
    )
    with pytest.raises(EvolutionError, match="applied"):
        engine.rollback(draft, reason="不需要了")
    approved = _approved_proposal(engine)
    with pytest.raises(EvolutionError, match="applied"):
        engine.rollback(approved, reason="不需要了")


def test_rollback_sets_status_rolled_back():
    engine = EvolutionEngine()
    proposal = _approved_proposal(engine)
    engine.apply(proposal)
    rolled = engine.rollback(proposal, reason="指标恶化")
    assert rolled.status == "rolled_back"
    assert rolled.effective_version == "v1"


# ---------------------------------------------------------------------------
# 审计事件
# ---------------------------------------------------------------------------


def test_apply_and_rollback_emit_audit_events():
    engine = EvolutionEngine()
    bus = EventBus()
    proposal = _approved_proposal(engine)
    engine.apply(proposal, event_bus=bus)
    engine.rollback(proposal, reason="指标恶化", event_bus=bus)

    applied_events = bus.query(type="evolution_applied")
    assert len(applied_events) == 1
    assert applied_events[0].payload["proposal_id"] == proposal.id
    assert applied_events[0].payload["effective_version"] == "v1"
    assert applied_events[0].payload["gray"] is True

    rolled_events = bus.query(type="evolution_rolled_back")
    assert len(rolled_events) == 1
    assert rolled_events[0].payload["proposal_id"] == proposal.id
    assert rolled_events[0].payload["reason"] == "指标恶化"

    # 引擎内部审计轨迹同样保留两条
    assert [event.type for event in engine.audit_events] == [
        "evolution_applied",
        "evolution_rolled_back",
    ]


def test_apply_uses_engine_level_event_bus():
    bus = EventBus()
    engine = EvolutionEngine(event_bus=bus)
    proposal = _approved_proposal(engine)
    engine.apply(proposal)
    assert len(bus.query(type="evolution_applied")) == 1


# ---------------------------------------------------------------------------
# 六步闭环端到端
# ---------------------------------------------------------------------------


def test_full_six_step_loop_end_to_end():
    engine = EvolutionEngine()
    bus = EventBus()
    for event in _fabricated_events():
        bus.publish(event)

    # ① 收集
    signals = engine.collect(bus)
    assert len(signals) >= 3

    # ② 提炼
    candidates = engine.distill(signals)
    assert candidates
    skill_candidates = [candidate for candidate in candidates if candidate.category == "skill"]
    assert any(candidate.target == "qa_testing" for candidate in skill_candidates)

    # ③ 提案
    target = next(candidate for candidate in candidates if candidate.category == "skill" and candidate.target == "qa_testing")
    proposal = engine.propose(
        target,
        author_role="qa",
        title="改善测试技能",
        rollback_plan="回滚到 skill 版本 v0",
        validation_plan="灰度 1 个 agent 观察 1 个迭代",
    )
    assert proposal.status == "draft"

    # ④ 评审门（approve）
    engine.review(proposal, approver="governance", decision="approve", reason="LGTM")
    assert proposal.status == "approved"

    # ⑤ 生效（灰度 + 版本化）
    engine.apply(proposal, event_bus=bus)
    assert proposal.status == "applied"
    assert proposal.effective_version == "v1"
    assert proposal.gray is True

    # ⑥ 回滚（写回滚日志，进入下一轮收集）
    engine.rollback(proposal, reason="灰度窗口指标恶化", event_bus=bus)
    assert proposal.status == "rolled_back"

    # 全程审计：apply + rollback 各一条事件
    assert len(bus.query(type="evolution_applied")) == 1
    assert len(bus.query(type="evolution_rolled_back")) == 1
    # 下一轮收集能看到回滚信号（闭环自食）
    next_signals = engine.collect(bus)
    assert any(signal.type == "rollback_occurred" for signal in next_signals)
