"""Task 5 行为测试：MeetingHost 7 类会议模板 + meeting 节点 handler 契约。"""

from __future__ import annotations

import pytest

from agent_cluster.meetings import MeetingHost, make_meeting_handler
from agent_cluster.models import (
    ClusterState,
    Iteration,
    MeetingKind,
    MessageType,
    Project,
    TaskStatus,
)
from agent_cluster.roles import RoleRegistry
from agent_cluster.workflow import NodeContext, WorkflowEdge, WorkflowNode, WorkflowSpec

ALL_KINDS = [
    MeetingKind.KICKOFF,
    MeetingKind.REQUIREMENT_REVIEW,
    MeetingKind.DESIGN_REVIEW,
    MeetingKind.DAILY_STANDUP,
    MeetingKind.CODE_REVIEW,
    MeetingKind.RETRO,
    MeetingKind.RELEASE_REVIEW,
]


# ---------------------------------------------------------------------------
# MeetingHost.run：7 类会议模板
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("kind", ALL_KINDS)
async def test_run_produces_meeting_with_transcript_decisions_and_minutes(kind):
    host = MeetingHost()
    participants = ["pm", "architect", "backend"]
    agenda = ["议程一", "议程二"]
    meeting = await host.run(
        kind,
        agenda=agenda,
        participants=participants,
        project_id="proj1",
        state=None,
    )

    assert meeting.kind == kind
    assert meeting.project_id == "proj1"
    assert meeting.agenda == agenda
    assert meeting.id.startswith("meeting:")
    assert meeting.minutes_id.startswith(f"minutes:{kind.value}:")

    # transcript：每个议程条目 × 每位参与者一条 meeting_speech
    assert len(meeting.transcript) == len(agenda) * len(participants)
    for message in meeting.transcript:
        assert message.type == MessageType.MEETING_SPEECH
        assert message.source in participants
        assert message.payload["meeting"] == kind.value

    # decisions：每个议程条目一条，topic/conclusion/owner 齐全
    assert len(meeting.decisions) == len(agenda)
    for decision in meeting.decisions:
        assert decision.topic in agenda
        assert decision.conclusion
        assert decision.reason
        assert decision.owner in participants


@pytest.mark.parametrize("kind", ALL_KINDS)
async def test_run_is_deterministic(kind):
    host = MeetingHost()
    kwargs = dict(
        agenda=["议程一"],
        participants=["pm", "qa"],
        project_id="proj1",
        state=None,
    )
    first = await host.run(kind, **kwargs)
    second = await host.run(kind, **kwargs)
    assert [msg.payload["content"] for msg in first.transcript] == [
        msg.payload["content"] for msg in second.transcript
    ]
    assert [decision.conclusion for decision in first.decisions] == [
        decision.conclusion for decision in second.decisions
    ]


async def test_code_review_transcript_exercises_lgtm_and_lbtm_verdicts():
    host = MeetingHost()
    meeting = await host.run(
        MeetingKind.CODE_REVIEW,
        agenda=["代码可读性与结构"],
        participants=["backend", "frontend", "reviewer", "debugger"],
        project_id="proj1",
        state=None,
    )
    contents = [message.payload["content"] for message in meeting.transcript]
    assert any("LGTM" in content for content in contents)
    assert any("LBTM" in content for content in contents)


async def test_code_review_decision_matches_verdict():
    host = MeetingHost()
    # 显式 LBTM 发言者（debugger）参与 -> 决策为未通过
    fail_meeting = await host.run(
        MeetingKind.CODE_REVIEW,
        agenda=["代码可读性与结构", "安全性"],
        participants=["backend", "frontend", "reviewer", "debugger"],
        project_id="proj1",
        state=None,
    )
    assert len(fail_meeting.decisions) == 2
    assert all("LBTM" in decision.conclusion for decision in fail_meeting.decisions)
    assert all("未通过" in decision.conclusion for decision in fail_meeting.decisions)

    # 默认 3 位参与者（frontend/backend/reviewer）：评审人给出 LGTM -> 决策为通过
    default_meeting = await host.run(
        MeetingKind.CODE_REVIEW,
        agenda=["代码可读性与结构"],
        participants=["frontend", "backend", "reviewer"],
        project_id="proj1",
        state=None,
    )
    assert all("LGTM" in decision.conclusion for decision in default_meeting.decisions)
    assert all("通过" in decision.conclusion for decision in default_meeting.decisions)

    # 2 位参与者：全员 LGTM -> 决策为通过
    small_meeting = await host.run(
        MeetingKind.CODE_REVIEW,
        agenda=["代码可读性与结构"],
        participants=["backend", "reviewer"],
        project_id="proj1",
        state=None,
    )
    assert all("LGTM" in decision.conclusion for decision in small_meeting.decisions)
    assert all("通过" in decision.conclusion for decision in small_meeting.decisions)


async def test_select_speaker_round_robin():
    host = MeetingHost()
    await host.run(
        MeetingKind.DAILY_STANDUP,
        agenda=["昨日进展"],
        participants=["pm", "backend", "qa"],
        project_id="proj1",
        state=None,
    )
    from agent_cluster.models import Message

    thread: list[Message] = []
    assert await host.select_speaker(thread) == "pm"
    thread.append(Message(id="m1", thread_id="t", source="pm", target="", type=MessageType.MEETING_SPEECH))
    assert await host.select_speaker(thread) == "backend"
    thread.append(Message(id="m2", thread_id="t", source="backend", target="", type=MessageType.MEETING_SPEECH))
    assert await host.select_speaker(thread) == "qa"
    thread.append(Message(id="m3", thread_id="t", source="qa", target="", type=MessageType.MEETING_SPEECH))
    assert await host.select_speaker(thread) == "pm"  # 轮转回到第一位


# ---------------------------------------------------------------------------
# make_meeting_handler：meeting 节点 handler 契约
# ---------------------------------------------------------------------------


def _make_context(node: WorkflowNode) -> NodeContext:
    spec = WorkflowSpec(
        name="t5-meeting",
        max_iterations=4,
        thread_id="proj:demo:iter:1",
        nodes=[
            WorkflowNode(id="start", type="start"),
            node,
            WorkflowNode(id="end", type="end"),
        ],
        edges=[
            WorkflowEdge(from_="start", to=node.id),
            WorkflowEdge(from_=node.id, to="end"),
        ],
    )
    return NodeContext(node_id=node.id, spec=spec, events=[], run_id="run-t5", loop_count=1)


@pytest.mark.parametrize("kind", ALL_KINDS)
async def test_meeting_handler_adds_meeting_action_items_and_summary(kind):
    host = MeetingHost()
    registry = RoleRegistry()
    handler = make_meeting_handler(host, registry)
    state = ClusterState(
        project=Project(id="proj1", name="演示项目"),
        iterations=[Iteration(id="iter1", project_id="proj1", number=1)],
    )
    node = WorkflowNode(id=f"meeting_node_{kind.value}", type="meeting", meeting=kind)
    ctx = _make_context(node)

    updates = await handler(state, node, ctx)

    # 通道键契约：meetings / tasks / messages
    assert set(updates) == {"meetings", "tasks", "messages"}

    meetings = updates["meetings"]
    assert len(meetings) == 1
    meeting = meetings[0]
    assert meeting.kind == kind
    assert meeting.project_id == "proj1"
    assert meeting.transcript and meeting.decisions
    assert meeting.minutes_id.startswith(f"minutes:{kind.value}:")

    # 行动项任务：status=todo，assignee 来自会议参与者
    participants = registry.default_role_ids(kind)
    tasks = updates["tasks"]
    assert len(tasks) == len(meeting.decisions)
    for task in tasks:
        assert task.status == TaskStatus.TODO
        assert task.assignee_role in participants
        assert task.project_id == "proj1"
        assert task.iteration_id == "iter1"

    # 总结消息：meeting_speech 广播
    messages = updates["messages"]
    assert len(messages) == 1
    summary = messages[0]
    assert summary.type == MessageType.MEETING_SPEECH
    assert summary.payload["meeting_id"] == meeting.id

    # meeting_held 事件走 ctx.events
    assert len(ctx.events) == 1
    assert ctx.events[0].type == "meeting_held"
    assert ctx.events[0].actor == kind.value


async def test_meeting_handler_requires_meeting_kind():
    host = MeetingHost()
    registry = RoleRegistry()
    handler = make_meeting_handler(host, registry)
    state = ClusterState(project=Project(id="proj1", name="演示项目"))
    node = WorkflowNode(id="bad", type="meeting")
    ctx = _make_context(node)
    with pytest.raises(ValueError, match="meeting"):
        await handler(state, node, ctx)
