"""会议子图（设计文档 §4）：MeetingHost 生成 7 类会议纪要 + meeting 节点 handler。

- ``MeetingHost.run(...)``：无 LLM 的确定性会议生成——按会议类型模板产出
  transcript（``meeting_speech`` 消息，每个议程条目 × 每位参与者一条）、
  decisions（每个议程条目一条，结论/负责人由议程与参与者确定性推导）、
  minutes_id（``minutes:<kind>:<ts>``）。
- ``MeetingHost.select_speaker(thread)``：按参与者轮转规则选下一位发言人
  （参与者取自最近一次 run 的 participants；thread 为空返回第一位）。
- ``make_meeting_handler(host, role_registry)``：注册进 ``WorkflowEngine`` 的
  "meeting" 节点 handler：运行会议、写回 ``state.meetings``、把会议决策提取为
  行动项 ``Task``（status todo，assignee 取决策 owner）、追加一条
  ``meeting_speech`` 总结消息。

meeting handler 通道契约（Task 7 CLI 依赖，勿变更）：
- 返回 LangGraph channel 更新字典，键固定为：
  - ``"meetings"``：``list[Meeting]``（本次会议记录）。
  - ``"tasks"``：``list[Task]``（从会议决策提取的行动项，status=todo）。
  - ``"messages"``：``list[Message]``（一条 ``meeting_speech`` 总结消息）。
- 会议决策留在 ``Meeting.decisions`` 内（不写入 ``decisions`` 通道——
  该通道是 ``list[ApprovalRecord]`` 审批记录，语义不同）；事件经 ``ctx.events``
  追加 ``type="meeting_held"``，不占通道键。

7 类会议模板（§4.1）：kickoff / requirement_review / design_review /
daily_standup / code_review / retro / release_review。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from agent_cluster.models import (
    ClusterState,
    Decision,
    Event,
    Meeting,
    MeetingKind,
    Message,
    MessageType,
    Task,
    TaskStatus,
)
from agent_cluster.workflow import NodeContext, NodeHandler, WorkflowNode

__all__ = ["MeetingHost", "make_meeting_handler"]


@dataclass(frozen=True)
class _MeetingTemplate:
    """会议模板：发言模板 + 决策结论模板（占位符 {agenda}/{participant}/{owner}）。

    ``decision_conclusion_reject`` / ``decision_reason_reject`` 为未通过变体
    （当前仅 code_review 使用，如 3 位以上参与者时第 3 位发言者给出 LBTM）。
    """

    speech: str
    decision_conclusion: str
    decision_reason: str
    decision_owner: str
    decision_conclusion_reject: str | None = None
    decision_reason_reject: str | None = None


# 7 类会议模板（§4.1：议程/决策门/产物）
_TEMPLATES: dict[MeetingKind, _MeetingTemplate] = {
    MeetingKind.KICKOFF: _MeetingTemplate(
        speech="【启动会】{participant} 讨论议程「{agenda}」：确认范围与 MVP 基线，认领职责并识别风险。",
        decision_conclusion="「{agenda}」已达成一致：纳入 MVP 范围基线，由 {owner} 负责落地。",
        decision_reason="启动会范围、MVP、职责与风险达成一致（通过=范围与 MVP 冻结）。",
        decision_owner="pm",
    ),
    MeetingKind.REQUIREMENT_REVIEW: _MeetingTemplate(
        speech="【需求评审】{participant} 评审「{agenda}」：提出澄清问题，确认以 Given/When/Then 形式可测的验收标准。",
        decision_conclusion="「{agenda}」需求澄清完成，验收标准定稿（无歧义且可测）。",
        decision_reason="逐条评审需求并确认验收标准（通过=无歧义+可测）。",
        decision_owner="pm",
    ),
    MeetingKind.DESIGN_REVIEW: _MeetingTemplate(
        speech="【设计评审】{participant} 评审「{agenda}」：确认设计决策与接口契约，标记开放问题。",
        decision_conclusion="「{agenda}」设计基线确认，接口契约与数据模型冻结；开放问题列入风险清单。",
        decision_reason="设计方案覆盖需求且复杂度可控（通过=覆盖需求+复杂度可控）。",
        decision_owner="architect",
    ),
    MeetingKind.DAILY_STANDUP: _MeetingTemplate(
        speech="【站会】{participant} 同步「{agenda}」：昨日=推进该项，今日=继续该项，阻塞=无。",
        decision_conclusion="「{agenda}」同步完成；阻塞项进入行动清单由 {owner} 跟进。",
        decision_reason="站会仅同步不决策；阻塞清单转行动项。",
        decision_owner="pmo",
    ),
    MeetingKind.CODE_REVIEW: _MeetingTemplate(
        speech="【代码评审】{participant} 按 6 条规范（可读性/边界/性能/安全/测试/文档）评审「{agenda}」：{verdict}。",
        decision_conclusion="「{agenda}」评审通过（LGTM）：无 P0/P1，注释完整且测试通过。",
        decision_reason="按 6 条评审规范逐条检查通过（通过=无 P0/P1+注释完整+测试过）。",
        decision_owner="reviewer",
        decision_conclusion_reject="「{agenda}」评审未通过（LBTM）：需修复高优问题后复审。",
        decision_reason_reject="存在 LBTM 意见：按 6 条评审规范未通过（存在高优问题）。",
    ),
    MeetingKind.RETRO: _MeetingTemplate(
        speech="【复盘】{participant} 复盘「{agenda}」：进展良好=完成项达标，不足=存在返工，"
        "根因=需求澄清不足，改进项=纳入下迭代 Backlog，进化信号=流程优化建议。",
        decision_conclusion="「{agenda}」根因与改进项已明确：改进项进入下迭代 Backlog，"
        "进化信号提交 evolution_apply 门。",
        decision_reason="复盘完成率、根因分析与改进项验证（通过=改进项可量化验证）。",
        decision_owner="pmo",
    ),
    MeetingKind.RELEASE_REVIEW: _MeetingTemplate(
        speech="【发布评审】{participant} 评审「{agenda}」：验收=测试全绿，风险=已评估，"
        "回滚预案=就绪，决策=Go。",
        decision_conclusion="「{agenda}」验收通过，回滚预案就绪，发布决策为 Go。",
        decision_reason="测试全绿、验收达标且发布窗口确认（通过=测试全绿+验收达标+窗口确认）。",
        decision_owner="devops",
    ),
}

# 各会议类型默认议程（§4.1 议程列；code_review 即 6 条评审规范）
_DEFAULT_AGENDAS: dict[MeetingKind, list[str]] = {
    MeetingKind.KICKOFF: ["项目愿景与目标", "范围与 MVP", "团队职责与排期", "风险识别"],
    MeetingKind.REQUIREMENT_REVIEW: ["需求逐条澄清", "验收标准确认"],
    MeetingKind.DESIGN_REVIEW: ["系统设计与技术选型", "API 契约与数据模型", "非功能需求"],
    MeetingKind.DAILY_STANDUP: ["昨日进展", "今日计划", "阻塞与求助"],
    MeetingKind.CODE_REVIEW: [
        "代码可读性与结构",
        "边界与错误处理",
        "性能与复杂度",
        "安全性",
        "测试覆盖",
        "文档与注释",
    ],
    MeetingKind.RETRO: ["迭代完成情况", "进展良好与不足", "根因分析", "改进项与进化提案"],
    MeetingKind.RELEASE_REVIEW: ["验收与回归结果", "风险与回滚预案", "发布窗口与 Go/No-Go"],
}


def _default_agenda(kind: MeetingKind) -> list[str]:
    """返回会议类型的默认议程条目。"""
    return list(_DEFAULT_AGENDAS[kind])


def _speech_verdict(participant_index: int) -> str:
    """code_review 发言裁决（确定性）：第 3 位（index%3==2）发言者给出 LBTM，其余 LGTM。"""
    return "LBTM（需修复高优问题）" if participant_index % 3 == 2 else "LGTM（通过）"


def _review_passed(participants: list[str]) -> bool:
    """code_review 是否通过：参与者 < 3 时无 LBTM 发言者，判定通过。"""
    return len(participants) < 3


def _now_stamp() -> str:
    """时间戳（会议 id / 纪要 id 用）。"""
    return datetime.now().strftime("%Y%m%d%H%M%S%f")


class MeetingHost:
    """会议主持人：确定性生成 7 类会议纪要（无需 LLM/API key）。

    ``run`` 记录 participants 供 ``select_speaker`` 轮转使用。
    ``state`` 参数为签名契约（会议上下文，如项目/迭代信息）；当前确定性实现
    不依赖其内容，仅透传给未来扩展。
    """

    def __init__(self) -> None:
        self._participants: list[str] = []

    async def run(
        self,
        kind: MeetingKind | str,
        *,
        agenda: list[str],
        participants: list[str],
        project_id: str,
        state: Any,
    ) -> Meeting:
        """生成会议：transcript + decisions + minutes_id，全部确定性模板。"""
        meeting_kind = MeetingKind(kind)
        self._participants = list(participants)
        template = _TEMPLATES[meeting_kind]
        ts = _now_stamp()
        thread_id = f"proj:{project_id}:meeting:{meeting_kind.value}"

        # transcript：每个议程条目 × 每位参与者一条 meeting_speech
        transcript: list[Message] = []
        for item in agenda:
            for index, participant in enumerate(participants):
                verdict = _speech_verdict(index) if meeting_kind == MeetingKind.CODE_REVIEW else ""
                content = template.speech.format(agenda=item, participant=participant, verdict=verdict)
                transcript.append(
                    Message(
                        id=uuid.uuid4().hex,
                        thread_id=thread_id,
                        source=participant,
                        target="",
                        type=MessageType.MEETING_SPEECH,
                        payload={"content": content, "agenda": item, "meeting": meeting_kind.value},
                    )
                )

        # decisions：每个议程条目一条，owner 由参与者轮转推导（确定性）
        # code_review 的结论与 transcript 实际裁决一致（LGTM 通过 / LBTM 未通过）
        decisions: list[Decision] = []
        for index, item in enumerate(agenda):
            owner = participants[index % len(participants)] if participants else template.decision_owner
            conclusion = template.decision_conclusion.format(agenda=item, owner=owner)
            reason = template.decision_reason
            if meeting_kind == MeetingKind.CODE_REVIEW and not _review_passed(participants):
                conclusion = (template.decision_conclusion_reject or conclusion).format(agenda=item, owner=owner)
                reason = template.decision_reason_reject or reason
            decisions.append(
                Decision(
                    id=uuid.uuid4().hex,
                    topic=item,
                    conclusion=conclusion,
                    reason=reason,
                    owner=owner,
                )
            )

        return Meeting(
            id=f"meeting:{meeting_kind.value}:{ts}",
            project_id=project_id,
            kind=meeting_kind,
            agenda=list(agenda),
            transcript=transcript,
            decisions=decisions,
            minutes_id=f"minutes:{meeting_kind.value}:{ts}",
        )

    async def select_speaker(self, thread: list[Message]) -> str:
        """按参与者轮转规则选下一位发言人。

        - thread 为空：返回第一位参与者。
        - 否则取最后一条消息 source 在参与者列表中的下一位（循环）。
        - 最近一次 run 未记录参与者或 source 不在列表中：返回第一位参与者。
        """
        if not self._participants:
            return ""
        if not thread:
            return self._participants[0]
        last_source = thread[-1].source
        try:
            index = self._participants.index(last_source)
        except ValueError:
            return self._participants[0]
        return self._participants[(index + 1) % len(self._participants)]


def make_meeting_handler(host: MeetingHost, role_registry: Any) -> NodeHandler:
    """构造注册进 ``WorkflowEngine`` 的 "meeting" 节点 handler。

    步骤：
    1. 按 ``node.meeting`` 取默认议程与默认参与岗位（role_registry）。
    2. ``host.run(...)`` 生成会议记录。
    3. 会议决策提取为行动项 ``Task``（status todo，assignee=决策 owner）。
    4. 追加一条 ``meeting_speech`` 总结消息到 messages 通道。
    5. 经 ``ctx.events`` 追加 ``Event(type="meeting_held")``。

    返回通道键（契约，勿变更）：``{"meetings", "tasks", "messages"}``。
    """
    async def handler(state: ClusterState, node: WorkflowNode, ctx: NodeContext) -> dict[str, Any]:
        if node.meeting is None:
            raise ValueError(f"meeting 节点 {node.id!r} 缺少 meeting 配置（node.meeting 为 None）")
        # 参与岗位：节点显式声明优先（用角色 id），缺省用 RoleRegistry 默认参与岗位
        participants = node.participants or role_registry.default_role_ids(node.meeting)
        project_id = state.project.id if state.project is not None else "demo"
        iteration_id = state.iterations[0].id if state.iterations else "iter:1"
        agenda = _default_agenda(node.meeting)
        thread_id = ctx.spec.thread_id or "default"

        meeting = await host.run(
            node.meeting,
            agenda=agenda,
            participants=participants,
            project_id=project_id,
            state=state,
        )

        # 行动项任务：会议决策 -> Task(status=todo, assignee=决策 owner)
        tasks: list[Task] = []
        for decision in meeting.decisions:
            tasks.append(
                Task(
                    id=uuid.uuid4().hex,
                    project_id=project_id,
                    iteration_id=iteration_id,
                    title=f"{node.meeting.value} 行动项：{decision.topic}",
                    desc=decision.conclusion,
                    assignee_role=decision.owner,
                    status=TaskStatus.TODO,
                    acceptance_criteria=[decision.conclusion],
                )
            )

        # 会议总结消息（type=meeting_speech，广播）
        summary = Message(
            id=uuid.uuid4().hex,
            thread_id=thread_id,
            source=node.meeting.value,
            target="",
            type=MessageType.MEETING_SPEECH,
            payload={
                "content": (
                    f"{node.meeting.value} 会议结束：{len(meeting.transcript)} 条发言，"
                    f"{len(meeting.decisions)} 项决策，纪要 {meeting.minutes_id}。"
                ),
                "meeting_id": meeting.id,
                "node": ctx.node_id,
            },
        )

        ctx.events.append(
            Event(
                id=uuid.uuid4().hex,
                run_id=ctx.run_id,
                thread_id=thread_id,
                type="meeting_held",
                actor=node.meeting.value,
                payload={"meeting": meeting.id, "decisions": len(meeting.decisions), "node": ctx.node_id},
            )
        )

        return {"meetings": [meeting], "tasks": tasks, "messages": [summary]}

    return handler
