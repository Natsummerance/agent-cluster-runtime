"""Stable Software Company role and meeting catalog."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RoleDefinition:
    id: str
    name: str
    mission: str


@dataclass(frozen=True)
class MeetingDefinition:
    id: str
    name: str
    participants: tuple[str, ...]
    approval_gate: str | None = None


ROLES: tuple[RoleDefinition, ...] = (
    RoleDefinition("pm", "产品经理", "澄清目标、范围与验收标准"),
    RoleDefinition("pmo", "项目管理", "规划里程碑、依赖、风险与交付节奏"),
    RoleDefinition("frontend", "前端工程师", "交付可用、可访问、可维护的产品界面"),
    RoleDefinition("backend", "后端工程师", "交付可靠服务、领域逻辑与数据契约"),
    RoleDefinition("algorithm", "算法工程师", "设计和验证智能算法与评测"),
    RoleDefinition("architect", "架构师", "维护系统边界、质量属性与长期演进"),
    RoleDefinition("qa", "质量工程师", "建立测试策略、验收证据与回归保障"),
    RoleDefinition("devops", "DevOps 工程师", "构建、发布、观测与回滚"),
    RoleDefinition("docs", "文档工程师", "维护用户、运维与开发者文档"),
    RoleDefinition("reviewer", "代码评审员", "审查正确性、安全、测试与可维护性"),
    RoleDefinition("debugger", "缺陷排查员", "复现问题、定位根因并验证修复"),
    RoleDefinition("governance", "治理与流程 Agent", "执行审计、策略和进化审批"),
)


MEETINGS: tuple[MeetingDefinition, ...] = (
    MeetingDefinition("kickoff", "项目启动会", tuple(role.id for role in ROLES)),
    MeetingDefinition(
        "requirement_review", "需求评审",
        ("pm", "architect", "frontend", "backend", "algorithm", "qa"),
        "requirement",
    ),
    MeetingDefinition(
        "design_review", "设计评审",
        ("architect", "pmo", "frontend", "backend", "qa", "devops"),
        "design",
    ),
    MeetingDefinition(
        "daily_standup", "每日站会",
        ("pm", "pmo", "frontend", "backend", "algorithm", "qa", "devops", "docs", "reviewer", "debugger"),
    ),
    MeetingDefinition("code_review", "代码评审", ("frontend", "backend", "reviewer")),
    MeetingDefinition(
        "release_review", "发布评审",
        ("pm", "architect", "qa", "devops", "frontend", "backend"),
        "release",
    ),
    MeetingDefinition(
        "retro", "迭代复盘",
        tuple(role.id for role in ROLES),
        "evolution",
    ),
)
