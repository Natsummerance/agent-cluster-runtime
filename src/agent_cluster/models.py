"""数据模型层：设计文档 §5.6 核心数据模型 + §5.3 消息/状态模型。

所有模型均为 pydantic v2 风格（BaseModel + Field + ConfigDict），
字段名与设计文档 §5.6 对齐，复杂字段带 Field(description=...) 说明。
额外为运行时可扩展性统一使用 ``extra="ignore"``。
"""

from __future__ import annotations

import operator

from datetime import date, datetime
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "RoleKind",
    "GateKind",
    "MessageType",
    "MeetingKind",
    "TaskStatus",
    "ProposalStatus",
    "ProposalTarget",
    "ModelConfig",
    "ReActConfig",
    "InjectionConfig",
    "ContextConfig",
    "AgentConfig",
    "AgentState",
    "Role",
    "Agent",
    "Task",
    "Decision",
    "Vote",
    "Proposal",
    "Skill",
    "ProgressEntry",
    "Ledger",
    "HumanInterruptConfig",
    "HumanResponse",
    "ActionRequest",
    "ApprovalRecord",
    "ApprovalGate",
    "Message",
    "Event",
    "Meeting",
    "Project",
    "Iteration",
    "ClusterState",
]


# ---------------------------------------------------------------------------
# 枚举（值即契约，pydantic 字段可直接接收字符串并校验）
# ---------------------------------------------------------------------------


class RoleKind(StrEnum):
    """岗位类别（八类）。

    设计文档 §5.6 定义七类（pm/arch/frontend/backend/algorithm/qa/devops），
    按任务简报要求增补 pmo（项目经理 / Scrum Master）凑足八类。
    """

    PM = "pm"
    PMO = "pmo"
    ARCH = "arch"
    FRONTEND = "frontend"
    BACKEND = "backend"
    ALGORITHM = "algorithm"
    QA = "qa"
    DEVOPS = "devops"


class GateKind(StrEnum):
    """审批门类别（六类，设计文档 §5.4）。"""

    REQUIREMENT_CONFIRMATION = "requirement_confirmation"
    DESIGN_REVIEW = "design_review"
    ITERATION_ACCEPTANCE = "iteration_acceptance"
    RELEASE = "release"
    EVOLUTION_APPLY = "evolution_apply"
    DANGEROUS_TOOL = "dangerous_tool"


class MessageType(StrEnum):
    """消息类型（设计文档 §5.3）。"""

    TEXT = "text"
    HANDOFF = "handoff"
    MEETING_SPEECH = "meeting_speech"
    PROPOSAL = "proposal"
    APPROVAL = "approval"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    STOP = "stop"


class MeetingKind(StrEnum):
    """会议类型（七类，设计文档 §4.1）。"""

    KICKOFF = "kickoff"
    REQUIREMENT_REVIEW = "requirement_review"
    DESIGN_REVIEW = "design_review"
    DAILY_STANDUP = "daily_standup"
    CODE_REVIEW = "code_review"
    RETRO = "retro"
    RELEASE_REVIEW = "release_review"


class TaskStatus(StrEnum):
    """任务状态（设计文档 §5.6）。"""

    TODO = "todo"
    DOING = "doing"
    REVIEW = "review"
    DONE = "done"
    BLOCKED = "blocked"


class ProposalStatus(StrEnum):
    """进化提案状态（设计文档 §5.6 五态）。"""

    DRAFT = "draft"
    VOTING = "voting"
    APPROVED = "approved"
    REJECTED = "rejected"
    APPLIED = "applied"


class ProposalTarget(StrEnum):
    """进化提案目标类别（四类，对齐设计文档 §6.1 进化对象分类）。

    设计文档 §5.6 代码示例为五值列表（process/skill/tool/role/workflow_yaml），
    任务简报要求四类，此处采用 §6.1 的四类进化对象。
    """

    SKILL = "skill"
    KNOWLEDGE = "knowledge"
    PROCESS = "process"
    ORGANIZATION = "organization"


# ---------------------------------------------------------------------------
# Agent 配置（Model / ReAct / Injection / Context 四件套，字段取合理默认）
# ---------------------------------------------------------------------------


class ModelConfig(BaseModel):
    """模型接入配置（对齐 AgentScope ModelConfig 思路，字段简化）。"""

    model_config = ConfigDict(extra="ignore")

    model_name: str = Field(default="deterministic", description="模型名称；默认确定性后端，无需 API key")
    temperature: float = Field(default=0.0, ge=0.0, le=2.0, description="采样温度")
    max_tokens: int = Field(default=2048, gt=0, description="单次生成最大 token 数")
    api_base: str | None = Field(default=None, description="API 地址覆盖，None 表示使用默认")
    api_key_env: str | None = Field(default=None, description="读取 API key 的环境变量名")


class ReActConfig(BaseModel):
    """ReAct 推理-行动循环配置。"""

    model_config = ConfigDict(extra="ignore")

    max_rounds: int = Field(default=5, gt=0, description="最大推理-行动轮数，防死循环")
    verbose: bool = Field(default=False, description="是否打印中间推理过程")


class InjectionConfig(BaseModel):
    """上下文注入配置。"""

    model_config = ConfigDict(extra="ignore")

    inject_system: bool = Field(default=True, description="是否注入系统提示")
    inject_skills: bool = Field(default=True, description="是否注入技能上下文")
    inject_ledger: bool = Field(default=True, description="是否注入账本上下文")
    inject_tools: bool = Field(default=True, description="是否注入工具描述")
    max_context_chars: int = Field(default=12000, gt=0, description="注入上下文截断上限（字符）")


class ContextConfig(BaseModel):
    """会话上下文配置。"""

    model_config = ConfigDict(extra="ignore")

    window: int = Field(default=16, gt=0, description="保留最近 N 条消息")
    max_messages: int = Field(default=64, gt=0, description="上下文最大消息数")
    max_chars: int = Field(default=20000, gt=0, description="上下文最大字符数")


class AgentConfig(BaseModel):
    """Agent 运行配置四件套（Model / ReAct / Injection / Context）。"""

    model_config = ConfigDict(extra="ignore")

    model: ModelConfig = Field(default_factory=ModelConfig, description="模型接入配置")
    react: ReActConfig = Field(default_factory=ReActConfig, description="ReAct 循环配置")
    injection: InjectionConfig = Field(default_factory=InjectionConfig, description="上下文注入配置")
    context: ContextConfig = Field(default_factory=ContextConfig, description="会话上下文配置")


# ---------------------------------------------------------------------------
# 角色与 Agent
# ---------------------------------------------------------------------------


class Role(BaseModel):
    """岗位定义（CrewAI 角色画像 + 工具/技能/审批范围）。"""

    model_config = ConfigDict(extra="ignore")

    id: str = Field(description="岗位唯一标识")
    name: str = Field(description="岗位展示名称")
    kind: RoleKind = Field(description="岗位类别（八类）")
    goal: str = Field(description="岗位目标")
    backstory: str = Field(description="岗位背景设定")
    skills: list[str] = Field(default_factory=list, description="技能挂载清单，格式 name@version")
    tools: list[str] = Field(default_factory=list, description="允许使用的工具清单")
    model: str | None = Field(default=None, description="偏好模型标识，None 表示使用默认")
    approval_scope: list[GateKind] = Field(default_factory=list, description="可审批的门类别")


class AgentState(BaseModel):
    """Agent 会话状态（由 reply/observe 维护）。"""

    model_config = ConfigDict(extra="ignore")

    messages: list[Message] = Field(default_factory=list, description="会话消息历史")


class Agent(BaseModel):
    """运行中的 Agent 实例。"""

    model_config = ConfigDict(extra="ignore")

    id: str = Field(description="Agent 唯一标识")
    role_id: str = Field(description="所属岗位 id")
    name: str = Field(description="Agent 名称")
    system_prompt: str = Field(description="系统提示词")
    state: AgentState = Field(default_factory=AgentState, description="会话状态")
    skills: list[Skill] = Field(default_factory=list, description="挂载的技能对象")
    tools: list[str] = Field(default_factory=list, description="工具清单")
    config: AgentConfig = Field(default_factory=AgentConfig, description="运行配置")


# ---------------------------------------------------------------------------
# 任务
# ---------------------------------------------------------------------------


class Task(BaseModel):
    """开发任务。"""

    model_config = ConfigDict(extra="ignore")

    id: str = Field(description="任务唯一标识")
    project_id: str = Field(description="所属项目 id")
    iteration_id: str = Field(description="所属迭代 id")
    title: str = Field(description="任务标题")
    desc: str = Field(description="任务描述")
    acceptance_criteria: list[str] = Field(default_factory=list, description="验收标准列表")
    assignee_role: str = Field(default="", description="负责岗位 id，空表示未分配")
    depends_on: list[str] = Field(default_factory=list, description="依赖的任务 id 列表")
    status: TaskStatus = Field(default=TaskStatus.TODO, description="任务状态")
    artifacts: list[str] = Field(default_factory=list, description="产出物路径列表")
    output_schema: dict = Field(default_factory=dict, description="输出结构约束")


# ---------------------------------------------------------------------------
# 会议与决策
# ---------------------------------------------------------------------------


class Decision(BaseModel):
    """会议决策。"""

    model_config = ConfigDict(extra="ignore")

    id: str = Field(description="决策唯一标识")
    topic: str = Field(description="决策主题")
    conclusion: str = Field(description="决策结论")
    reason: str = Field(default="", description="决策理由")
    owner: str = Field(default="", description="负责人岗位 id，空表示未指定")
    ts: datetime = Field(default_factory=datetime.now, description="决策时间")


class Meeting(BaseModel):
    """会议记录。"""

    model_config = ConfigDict(extra="ignore")

    id: str = Field(description="会议唯一标识")
    project_id: str = Field(description="所属项目 id")
    kind: MeetingKind = Field(description="会议类型（七类）")
    agenda: list[str] = Field(default_factory=list, description="议程条目")
    transcript: list[Message] = Field(default_factory=list, description="会议发言消息流")
    decisions: list[Decision] = Field(default_factory=list, description="会议决策")
    minutes_id: str = Field(default="", description="会议纪要文档 id，空表示未生成")


# ---------------------------------------------------------------------------
# 进化提案
# ---------------------------------------------------------------------------


class Vote(BaseModel):
    """提案投票。"""

    model_config = ConfigDict(extra="ignore")

    by_role: str = Field(description="投票岗位 id")
    verdict: Literal["approve", "reject", "abstain"] = Field(description="投票结论")
    reason: str = Field(default="", description="投票理由")
    ts: datetime = Field(default_factory=datetime.now, description="投票时间")


class Proposal(BaseModel):
    """自我进化提案载体。"""

    model_config = ConfigDict(extra="ignore")

    id: str = Field(description="提案唯一标识")
    author_role: str = Field(description="提案人岗位 id")
    target: ProposalTarget = Field(description="进化目标类别（四类）")
    change: dict = Field(description="变更内容")
    rationale: str = Field(description="提案理由")
    impact: str = Field(default="", description="影响说明")
    status: ProposalStatus = Field(default=ProposalStatus.DRAFT, description="提案状态（五态）")
    votes: list[Vote] = Field(default_factory=list, description="投票记录")
    effective_version: str = Field(default="", description="生效版本号，空表示未生效")


# ---------------------------------------------------------------------------
# 技能
# ---------------------------------------------------------------------------


class Skill(BaseModel):
    """技能包（SKILL.md 解析产物）。"""

    model_config = ConfigDict(extra="ignore")

    name: str = Field(description="技能名称（小写连字符）")
    version: str = Field(default="0.1.0", description="技能版本（semver）")
    description: str = Field(default="", description="技能描述")
    license: str | None = Field(default=None, description="许可证，None 表示未声明")
    compatibility: str | None = Field(
        default=None, description="平台版本约束（如 >=0.1.0），None 表示不限制"
    )
    allowed_tools: list[str] | None = Field(default=None, description="工具白名单，None 表示不限制")
    dir: str = Field(default="", description="技能包目录路径")
    markdown: str = Field(default="", description="SKILL.md 正文内容")
    disclosure_level: Literal[1, 2, 3] = Field(
        default=1, description="渐进披露级别：1 仅 frontmatter / 2 正文 / 3 资源清单"
    )
    resource_files: dict[str, list[str]] = Field(
        default_factory=dict, description="资源文件：scripts/references/assets 分类清单"
    )


# ---------------------------------------------------------------------------
# 账本（Magentic-One 风格）
# ---------------------------------------------------------------------------


class ProgressEntry(BaseModel):
    """账本进度条目。"""

    model_config = ConfigDict(extra="ignore")

    role: str = Field(description="负责岗位 id")
    status: str = Field(default="", description="进度状态")
    verdict: str = Field(default="", description="结论")
    next_action: str = Field(default="", description="下一步行动")


class Ledger(BaseModel):
    """任务账本：事实 / 计划 / 进度 / 满意度 / 循环检测。"""

    model_config = ConfigDict(extra="ignore")

    task_id: str = Field(description="关联任务 id")
    facts: list[str] = Field(default_factory=list, description="事实清单")
    plan: list[str] = Field(default_factory=list, description="计划步骤")
    progress: list[ProgressEntry] = Field(default_factory=list, description="进度条目")
    is_satisfied: bool = Field(default=False, description="是否已满足")
    is_looping: bool = Field(default=False, description="是否检测到死循环")


# ---------------------------------------------------------------------------
# 审批门（HITL interrupt）
# ---------------------------------------------------------------------------


class HumanInterruptConfig(BaseModel):
    """人工中断响应选项配置。"""

    model_config = ConfigDict(extra="ignore")

    allow_ignore: bool = Field(default=True, description="是否允许忽略")
    allow_respond: bool = Field(default=True, description="是否允许回复说明")
    allow_edit: bool = Field(default=True, description="是否允许编辑内容")
    allow_accept: bool = Field(default=True, description="是否允许直接接受")


class HumanResponse(BaseModel):
    """人工对审批门的响应。"""

    model_config = ConfigDict(extra="ignore")

    type: Literal["accept", "ignore", "response", "edit", "reject"] = Field(description="响应类型")
    args: Any = Field(default=None, description="响应参数，任意类型")


class ActionRequest(BaseModel):
    """待审批动作请求。"""

    model_config = ConfigDict(extra="ignore")

    id: str = Field(description="请求唯一标识")
    kind: GateKind = Field(description="审批门类别")
    title: str = Field(default="", description="请求标题")
    description: str = Field(default="", description="请求描述")
    evidence: dict = Field(default_factory=dict, description="证据 / 上下文")
    risk_level: Literal["low", "medium", "high", "critical"] = Field(default="medium", description="风险级别")
    bypass_immune: bool = Field(default=False, description="无人值守时是否禁止自动放行")
    decisions: list[ApprovalRecord] = Field(
        default_factory=list, description="审批记录，最后一条为当前结论（Task 3 门路由契约）"
    )


class ApprovalRecord(BaseModel):
    """审批记录（落盘审计）。"""

    model_config = ConfigDict(extra="ignore")

    by_role: str = Field(default="", description="审批者岗位 id，空表示系统")
    type: Literal["accept", "reject", "edit", "response", "ignore"] = Field(description="审批结论")
    args: Any = Field(default=None, description="审批参数，任意类型")
    ts: datetime = Field(default_factory=datetime.now, description="审批时间")


class ApprovalGate(BaseModel):
    """审批门节点状态。"""

    model_config = ConfigDict(extra="ignore")

    id: str = Field(description="门唯一标识")
    kind: GateKind = Field(description="门类别")
    node: str = Field(default="", description="所属图节点 id，空表示未绑定")
    interrupt_config: HumanInterruptConfig = Field(default_factory=HumanInterruptConfig, description="中断选项")
    payload: ActionRequest = Field(description="待审批动作请求")
    decisions: list[ApprovalRecord] = Field(default_factory=list, description="审批记录")


# ---------------------------------------------------------------------------
# 消息与事件（§5.3 / §5.7）
# ---------------------------------------------------------------------------


class Message(BaseModel):
    """消息（Agent 间 / 会议 / 工具事件载体）。"""

    model_config = ConfigDict(extra="ignore")

    id: str = Field(description="消息唯一标识")
    thread_id: str = Field(description="所属线程 id")
    source: str = Field(description="发送方")
    target: str = Field(description="接收方；空表示广播")
    type: MessageType = Field(description="消息类型")
    payload: dict = Field(default_factory=dict, description="消息负载")
    ts: datetime = Field(default_factory=datetime.now, description="消息时间")


class Event(BaseModel):
    """审计事件（append-only 事件流条目）。"""

    model_config = ConfigDict(extra="ignore")

    id: str = Field(description="事件唯一标识")
    run_id: str = Field(description="运行 id")
    thread_id: str = Field(description="线程 id")
    type: str = Field(description="事件类型")
    actor: str = Field(default="", description="行为者")
    payload: dict = Field(default_factory=dict, description="事件负载")
    ts: datetime = Field(default_factory=datetime.now, description="事件时间")


# ---------------------------------------------------------------------------
# 项目 / 迭代 / 共享状态
# ---------------------------------------------------------------------------


class Project(BaseModel):
    """项目。"""

    model_config = ConfigDict(extra="ignore")

    id: str = Field(description="项目唯一标识")
    name: str = Field(description="项目名称")
    vision: str = Field(default="", description="项目愿景")
    status: Literal["active", "completed", "archived"] = Field(default="active", description="项目状态")
    created_at: datetime = Field(default_factory=datetime.now, description="创建时间")


class Iteration(BaseModel):
    """迭代。"""

    model_config = ConfigDict(extra="ignore")

    id: str = Field(description="迭代唯一标识")
    project_id: str = Field(description="所属项目 id")
    number: int = Field(gt=0, description="迭代序号")
    goal: str = Field(default="", description="迭代目标")
    start_date: date | None = Field(default=None, description="开始日期")
    end_date: date | None = Field(default=None, description="结束日期")
    status: Literal["planning", "in_progress", "completed", "cancelled"] = Field(
        default="planning", description="迭代状态"
    )


def _last_ledger(current: Ledger | None, update: Ledger | None) -> Ledger | None:
    """``ledger`` 通道 reducer：保留最后一次写入的账本。

    parallel 并行子节点在同一超步并发写 ``ledger``（LangGraph 要求带 reducer 的
    通道才能并发更新），取最后一次写入（后写者胜），顺序执行时等价于整体替换。
    """
    return update if update is not None else current


class ClusterState(BaseModel):
    """LangGraph 共享状态（§5.3），list/dict 字段默认空。

    注：``skill_catalog`` 在 Task 2 实现 ``SkillCatalog`` 前先用
    ``dict[str, Skill]``（name@version -> Skill）表达。
    """

    model_config = ConfigDict(extra="ignore")

    project: Project | None = Field(default=None, description="当前项目")
    iterations: Annotated[list[Iteration], operator.add] = Field(default_factory=list, description="迭代列表")
    tasks: Annotated[list[Task], operator.add] = Field(default_factory=list, description="任务列表")
    meetings: Annotated[list[Meeting], operator.add] = Field(default_factory=list, description="会议记录列表")
    ledger: Annotated[Ledger | None, _last_ledger] = Field(default=None, description="当前任务账本")
    gate_payloads: dict[GateKind, ActionRequest] = Field(default_factory=dict, description="待审批请求，按门类别索引")
    decisions: Annotated[list[ApprovalRecord], operator.add] = Field(default_factory=list, description="审批记录")
    skill_catalog: dict[str, Skill] = Field(default_factory=dict, description="技能目录：name@version -> Skill")
    messages: Annotated[list[Message], operator.add] = Field(default_factory=list, description="消息流")
