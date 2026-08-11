"""Task 1 数据模型行为测试。

覆盖：模型构造默认值、必填字段校验、枚举合法性、ClusterState 字段类型，
以及若干有意义的行为（Task 状态校验、Message 往返、Proposal 状态枚举等）。
"""

from datetime import datetime

import pytest
from pydantic import ValidationError

from agent_cluster.models import (
    ActionRequest,
    Agent,
    AgentConfig,
    ApprovalGate,
    ApprovalRecord,
    ClusterState,
    Decision,
    Event,
    GateKind,
    HumanInterruptConfig,
    HumanResponse,
    Iteration,
    Ledger,
    Meeting,
    MeetingKind,
    Message,
    MessageType,
    ProgressEntry,
    Project,
    Proposal,
    ProposalStatus,
    ProposalTarget,
    Role,
    RoleKind,
    Skill,
    Task,
    TaskStatus,
    Vote,
)


# ---------------------------------------------------------------------------
# 枚举合法性
# ---------------------------------------------------------------------------


def test_role_kind_membership():
    assert len(RoleKind) == 8
    assert set(RoleKind) == {
        RoleKind.PM,
        RoleKind.PMO,
        RoleKind.ARCH,
        RoleKind.FRONTEND,
        RoleKind.BACKEND,
        RoleKind.ALGORITHM,
        RoleKind.QA,
        RoleKind.DEVOPS,
    }
    assert RoleKind("pm") is RoleKind.PM


def test_gate_kind_membership():
    assert len(GateKind) == 6
    assert {kind.value for kind in GateKind} == {
        "requirement_confirmation",
        "design_review",
        "iteration_acceptance",
        "release",
        "evolution_apply",
        "dangerous_tool",
    }


def test_meeting_kind_membership():
    assert len(MeetingKind) == 7
    assert {kind.value for kind in MeetingKind} == {
        "kickoff",
        "requirement_review",
        "design_review",
        "daily_standup",
        "code_review",
        "retro",
        "release_review",
    }


def test_task_status_membership():
    assert len(TaskStatus) == 5
    assert {status.value for status in TaskStatus} == {"todo", "doing", "review", "done", "blocked"}


def test_message_type_membership():
    assert len(MessageType) == 8
    assert {msg_type.value for msg_type in MessageType} == {
        "text",
        "handoff",
        "meeting_speech",
        "proposal",
        "approval",
        "tool_call",
        "tool_result",
        "stop",
    }


def test_proposal_status_and_target_membership():
    assert {status.value for status in ProposalStatus} == {
        "draft",
        "voting",
        "approved",
        "rejected",
        "applied",
    }
    assert {target.value for target in ProposalTarget} == {"skill", "knowledge", "process", "organization"}


# ---------------------------------------------------------------------------
# 模型构造默认值
# ---------------------------------------------------------------------------


def test_role_defaults():
    role = Role(
        id="pm",
        name="产品经理",
        kind=RoleKind.PM,
        goal="需求澄清与 PRD 编写",
        backstory="资深产品经理",
    )
    assert role.skills == []
    assert role.tools == []
    assert role.model is None
    assert role.approval_scope == []
    assert role.kind == RoleKind.PM


def test_agent_config_defaults():
    config = AgentConfig()
    assert config.model.model_name == "deterministic"
    assert config.model.temperature == 0.0
    assert config.react.max_rounds >= 1
    assert config.injection.inject_system is True
    assert config.context.window >= 1


def test_agent_defaults():
    agent = Agent(id="a1", role_id="pm", name="PM Agent", system_prompt="你是产品经理。")
    assert agent.state.messages == []
    assert agent.skills == []
    assert agent.tools == []
    assert agent.config.model.model_name == "deterministic"


def test_task_defaults():
    task = Task(id="t1", project_id="p1", iteration_id="i1", title="实现登录", desc="完成登录页。")
    assert task.status == TaskStatus.TODO
    assert task.acceptance_criteria == []
    assert task.assignee_role == ""
    assert task.depends_on == []
    assert task.artifacts == []
    assert task.output_schema == {}


def test_human_interrupt_config_defaults():
    config = HumanInterruptConfig()
    assert config.allow_ignore is True
    assert config.allow_respond is True
    assert config.allow_edit is True
    assert config.allow_accept is True


def test_skill_defaults():
    skill = Skill(name="writing", version="0.1.0")
    assert skill.description == ""
    assert skill.license is None
    assert skill.allowed_tools is None
    assert skill.dir == ""
    assert skill.markdown == ""
    assert skill.disclosure_level == 1
    assert skill.resource_files == {}


def test_ledger_defaults():
    ledger = Ledger(task_id="t1")
    assert ledger.facts == []
    assert ledger.plan == []
    assert ledger.progress == []
    assert ledger.is_satisfied is False
    assert ledger.is_looping is False


def test_vote_and_decision_defaults():
    vote = Vote(by_role="arch", verdict="approve")
    assert vote.reason == ""
    assert isinstance(vote.ts, datetime)
    decision = Decision(id="d1", topic="API 设计", conclusion="采用 REST")
    assert decision.reason == ""
    assert decision.owner == ""
    assert isinstance(decision.ts, datetime)


def test_event_defaults():
    event = Event(id="e1", run_id="r1", thread_id="th1", type="node_start", actor="arch")
    assert event.payload == {}
    assert isinstance(event.ts, datetime)


# ---------------------------------------------------------------------------
# 必填字段校验
# ---------------------------------------------------------------------------


def test_role_requires_core_fields():
    with pytest.raises(ValidationError):
        Role(name="x", kind=RoleKind.PM, goal="g", backstory="b")
    with pytest.raises(ValidationError):
        Role(id="x", kind=RoleKind.PM, goal="g", backstory="b")
    with pytest.raises(ValidationError):
        Role(id="x", name="x", goal="g", backstory="b")


def test_meeting_requires_kind():
    with pytest.raises(ValidationError):
        Meeting(id="m1", project_id="p1")


def test_task_requires_core_fields():
    with pytest.raises(ValidationError):
        Task(id="t1", project_id="p1", iteration_id="i1", desc="d")
    with pytest.raises(ValidationError):
        Task(id="t1", project_id="p1", iteration_id="i1", title="t")


def test_message_requires_type():
    with pytest.raises(ValidationError):
        Message(id="msg1", thread_id="th1", source="pm", target="arch")


def test_approval_gate_requires_payload():
    with pytest.raises(ValidationError):
        ApprovalGate(id="g1", kind=GateKind.RELEASE)


# ---------------------------------------------------------------------------
# 有意义的行为
# ---------------------------------------------------------------------------


def test_task_status_literal_validation():
    task = Task(
        id="t2",
        project_id="p1",
        iteration_id="i1",
        title="修复缺陷",
        desc="",
        status="done",
    )
    assert task.status == TaskStatus.DONE
    with pytest.raises(ValidationError):
        Task(id="t3", project_id="p1", iteration_id="i1", title="x", desc="", status="in-progress")


def test_message_round_trip():
    message = Message(
        id="m1",
        thread_id="th1",
        source="pm",
        target="arch",
        type=MessageType.HANDOFF,
        payload={"task": "t1"},
    )
    restored = Message.model_validate(message.model_dump())
    assert restored == message
    assert restored.type == MessageType.HANDOFF


def test_message_rejects_unknown_type():
    with pytest.raises(ValidationError):
        Message(id="m1", thread_id="th1", source="a", target="b", type="shout")


def test_proposal_status_enum():
    proposal = Proposal(
        id="pr1",
        author_role="arch",
        target=ProposalTarget.PROCESS,
        change={"nodes": ["start", "end"]},
        rationale="简化流程",
    )
    assert proposal.status == ProposalStatus.DRAFT
    proposal.status = ProposalStatus.APPLIED
    assert proposal.status == ProposalStatus.APPLIED
    assert proposal.effective_version == ""


def test_proposal_rejects_invalid_status_and_target():
    with pytest.raises(ValidationError):
        Proposal(id="pr2", author_role="arch", target="website", change={}, rationale="x")
    with pytest.raises(ValidationError):
        Proposal(
            id="pr3",
            author_role="arch",
            target=ProposalTarget.SKILL,
            change={},
            rationale="x",
            status="unknown",
        )


def test_human_response_type_validation():
    for response_type in ("accept", "ignore", "response", "edit"):
        response = HumanResponse(type=response_type, args={"text": "ok"})
        assert response.type == response_type
    with pytest.raises(ValidationError):
        HumanResponse(type="maybe")


def test_skill_disclosure_level_validation():
    skill = Skill(name="writing", disclosure_level=3)
    assert skill.disclosure_level == 3
    with pytest.raises(ValidationError):
        Skill(name="writing", disclosure_level=4)


def test_ledger_progress_entries():
    ledger = Ledger(task_id="t1")
    entry = ProgressEntry(role="backend", status="doing", verdict="进行中", next_action="编写接口")
    ledger.progress.append(entry)
    assert ledger.progress[0].role == "backend"
    assert ledger.progress[0].next_action == "编写接口"
    ledger.is_satisfied = True
    assert ledger.is_satisfied is True


def test_approval_gate_with_payload():
    gate = ApprovalGate(
        id="g1",
        kind=GateKind.RELEASE,
        node="release_gate",
        payload=ActionRequest(id="ar1", kind=GateKind.RELEASE, title="发布审批"),
    )
    assert gate.interrupt_config.allow_accept is True
    assert gate.decisions == []
    assert gate.payload.title == "发布审批"


def test_approval_record_types():
    record = ApprovalRecord(by_role="pm", type="accept", args={"note": "同意"})
    assert record.type == "accept"
    assert isinstance(record.ts, datetime)
    with pytest.raises(ValidationError):
        ApprovalRecord(by_role="pm", type="maybe")


# ---------------------------------------------------------------------------
# ClusterState 字段类型
# ---------------------------------------------------------------------------


def test_cluster_state_defaults():
    state = ClusterState()
    assert state.project is None
    assert state.ledger is None
    assert state.iterations == []
    assert state.tasks == []
    assert state.meetings == []
    assert state.gate_payloads == {}
    assert state.decisions == []
    assert state.skill_catalog == {}
    assert state.messages == []


def test_cluster_state_field_types():
    project = Project(id="p1", name="示例项目", vision="打造开发集群")
    iteration = Iteration(id="i1", project_id="p1", number=1)
    task = Task(id="t1", project_id="p1", iteration_id="i1", title="x", desc="")
    meeting = Meeting(id="m1", project_id="p1", kind=MeetingKind.KICKOFF)
    ledger = Ledger(task_id="t1")
    request = ActionRequest(id="ar1", kind=GateKind.RELEASE, title="发布审批")
    skill = Skill(name="writing", version="0.1.0")
    message = Message(id="m1", thread_id="th1", source="pm", target="all", type=MessageType.TEXT)

    state = ClusterState(
        project=project,
        iterations=[iteration],
        tasks=[task],
        meetings=[meeting],
        ledger=ledger,
        gate_payloads={GateKind.RELEASE: request},
        decisions=[ApprovalRecord(by_role="pm", type="accept")],
        skill_catalog={"writing@0.1.0": skill},
        messages=[message],
    )

    assert state.project == project
    assert state.iterations[0] == iteration
    assert state.tasks[0] == task
    assert state.meetings[0] == meeting
    assert state.ledger == ledger
    assert state.gate_payloads[GateKind.RELEASE] == request
    assert state.decisions[0].type == "accept"
    assert state.skill_catalog["writing@0.1.0"].name == "writing"
    assert state.messages[0].type == MessageType.TEXT


def test_cluster_state_round_trip():
    state = ClusterState(
        project=Project(id="p1", name="示例", vision="v"),
        tasks=[Task(id="t1", project_id="p1", iteration_id="i1", title="x", desc="")],
        messages=[Message(id="m1", thread_id="th1", source="a", target="b", type=MessageType.TEXT)],
    )
    restored = ClusterState.model_validate(state.model_dump())
    assert restored == state
