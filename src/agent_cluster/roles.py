"""组织角色层（设计文档 §3.1）：12 岗位目录与岗位注册表。

- ``build_role_catalog()`` 返回 12 个岗位的 ``Role`` 定义（pm/pmo/frontend/backend/
  algorithm/architect/qa/devops/docs/reviewer/debugger/governance），字段对齐
  §3.1：goal/backstory/skills/tools/approval_scope。
- ``RoleRegistry`` 提供 ``get``/``list``/``filter_by_kind`` 与各会议类型的默认
  参与岗位（§4.1 参与者列，Task 5 meeting handler 据此确定 participants）。

RoleKind 八类与 12 岗的映射（目录内文档化契约）：
- pm→PM、pmo→PMO、frontend→FRONTEND、backend→BACKEND、algorithm→ALGORITHM、
  architect→ARCH、qa→QA、devops→DEVOPS；
- 辅助/门禁四岗归入相近类别：docs→PMO（规格文档/流程辅助）、reviewer→QA、
  debugger→QA（缺陷排查归质量保障域）、governance→PM（治理/流程 agent 归决策层）；
- ``RoleKind.ARCH`` 对应岗位 id ``"architect"``。

技能清单为 ``name@version`` 字符串：优先引用 ``examples/skills`` 中已存在的
技能（requirement-analysis@1.0.0、backend-api-design@2.1.0），其余为按 §3.1
技能挂载列声明的占位技能（字符串契约，允许尚未创建）。
"""

from __future__ import annotations

from agent_cluster.models import GateKind, MeetingKind, Role, RoleKind

__all__ = ["build_role_catalog", "RoleRegistry"]


def build_role_catalog() -> dict[str, Role]:
    """返回 12 岗位目录（岗位 id -> Role），按 §3.1 岗位清单构建。"""
    roles: list[Role] = [
        Role(
            id="pm",
            name="产品经理",
            kind=RoleKind.PM,
            goal="收集并澄清需求，输出 PRD 与可验证的验收标准，冻结需求范围。",
            backstory="产品经理负责需求收集与澄清、竞品与市场分析、PRD 编写与验收标准定义；"
            "属于决策层，可批准「需求范围冻结」「迭代验收」「发布」。",
            skills=["requirement-analysis@1.0.0", "competitor-research@0.1.0", "prd-writing@0.1.0"],
            tools=["read_file", "write_file", "edit_file", "mkdir", "list_dir", "grep", "glob"],
            approval_scope=[
                GateKind.REQUIREMENT_CONFIRMATION,
                GateKind.ITERATION_ACCEPTANCE,
                GateKind.RELEASE,
            ],
        ),
        Role(
            id="pmo",
            name="项目经理",
            kind=RoleKind.PMO,
            goal="拆分任务与依赖、制定排期、主持会议并跟踪进度与风险，关闭迭代范围与任务。",
            backstory="项目经理（PMO / Scrum Master）负责任务拆分与依赖分析、排期、会议主持、"
            "进度与风险跟踪；属于管理层，可批准「迭代范围与任务关闭」。",
            skills=["task-breakdown@0.1.0", "agile-scrum@0.1.0", "meeting-facilitation@0.1.0"],
            tools=["read_file", "write_file", "edit_file", "mkdir", "list_dir", "grep", "glob"],
            approval_scope=[GateKind.ITERATION_ACCEPTANCE],
        ),
        Role(
            id="frontend",
            name="前端开发工程师",
            kind=RoleKind.FRONTEND,
            goal="按设计稿与 API 契约实现 UI、组件与交互，并保证构建与前端测试通过。",
            backstory="前端开发属于执行层：负责 UI 还原、前端架构与组件库、页面与交互；"
            "可运行构建与前端测试。",
            skills=["frontend-design@1.0.0", "webapp-testing@0.1.0"],
            tools=["read_file", "write_file", "edit_file", "mkdir", "list_dir", "grep", "glob", "run_tests", "run_python", "git_status", "git_diff", "git_add", "git_commit", "git_revert"],
        ),
        Role(
            id="backend",
            name="后端开发工程师",
            kind=RoleKind.BACKEND,
            goal="实现 API、数据模型与业务逻辑，编写测试并保证服务集成可用。",
            backstory="后端开发属于执行层：负责 API、数据模型、业务逻辑、服务集成；"
            "可写代码、跑测试，产出数据库脚本与接口契约。",
            skills=["backend-api-design@2.1.0", "database-schema@0.1.0", "unit-testing@0.1.0"],
            tools=["read_file", "write_file", "edit_file", "mkdir", "list_dir", "grep", "glob", "run_tests", "run_python", "git_status", "git_diff", "git_add", "git_commit", "git_revert"],
        ),
        Role(
            id="algorithm",
            name="算法工程师",
            kind=RoleKind.ALGORITHM,
            goal="设计算法方案、处理数据、训练/推理并评估优化效果。",
            backstory="算法工程师属于执行层：负责算法方案、数据处理、训练与推理、评估优化；"
            "算法方案与评估标准经设计评审门（architect/qa/pm 审批范围）把关。",
            skills=["ml-engineering@0.1.0", "model-evaluation@0.1.0", "data-prep@0.1.0"],
            tools=["read_file", "write_file", "edit_file", "mkdir", "list_dir", "grep", "glob", "run_tests", "run_python", "git_status", "git_diff"],
        ),
        Role(
            id="architect",
            name="架构师",
            kind=RoleKind.ARCH,
            goal="输出系统设计、技术选型、模块划分与接口契约，冻结架构基线。",
            backstory="架构工程师属于管理层：负责系统设计、技术选型、模块划分、接口契约与"
            "非功能需求；可批准「架构基线」（design_review 门）。",
            skills=["system-design@0.1.0", "api-contract@0.1.0", "security-review@0.1.0"],
            tools=["read_file", "write_file", "edit_file", "mkdir", "list_dir", "grep", "glob", "git_status", "git_diff", "run_tests"],
            approval_scope=[GateKind.DESIGN_REVIEW],
        ),
        Role(
            id="qa",
            name="测试开发工程师",
            kind=RoleKind.QA,
            goal="编写测试计划与用例、执行自动化测试、跟踪缺陷与回归，把关质量门。",
            backstory="测试开发（QA）属于执行层：负责测试计划/用例/自动化、缺陷与回归；"
            "可批准「质量门」（迭代验收）。",
            skills=["test-planning@0.1.0", "automated-testing@0.1.0", "bug-hunting@0.1.0"],
            tools=["read_file", "list_dir", "grep", "glob", "git_status", "git_diff", "run_tests", "run_python", "git_revert"],
            approval_scope=[GateKind.ITERATION_ACCEPTANCE],
        ),
        Role(
            id="devops",
            name="运维工程师",
            kind=RoleKind.DEVOPS,
            goal="搭建 CI/CD 与监控告警、执行部署与发布、处理故障恢复。",
            backstory="运维维护（SRE）属于执行层：负责部署、CI/CD、监控告警、故障恢复与"
            "发布执行；可批准「发布窗口」（release 门）。",
            skills=["ci-cd@0.1.0", "deployment@0.1.0", "observability@0.1.0", "incident-response@0.1.0"],
            tools=["read_file", "write_file", "edit_file", "mkdir", "list_dir", "grep", "glob", "run_shell", "git_init", "git_status", "git_diff", "git_add", "git_commit", "git_revert", "git_push", "delete_file"],
            approval_scope=[GateKind.RELEASE],
        ),
        Role(
            id="docs",
            name="规格文档写手",
            kind=RoleKind.PMO,
            goal="把 PRD 与设计转化为开发规格、API 文档与 README。",
            backstory="规格文档写手（SpecWriter）属于辅助层：负责把 PRD 转成开发规格、"
            "接口文档与 README，属于管理与流程辅助域。",
            skills=["doc-writing@0.1.0", "api-docs@0.1.0"],
            tools=["read_file", "write_file", "edit_file", "mkdir", "list_dir", "grep", "glob"],
        ),
        Role(
            id="reviewer",
            name="代码评审员",
            kind=RoleKind.QA,
            goal="按评审规范逐条检查代码，输出最高优先级修改意见。",
            backstory="代码评审员属于辅助层：按评审规范逐条检查 PR 代码，输出评审意见与"
            "修改指令；归入质量保障域（QA 类别）。",
            skills=["code-review@0.1.0", "best-practices@0.1.0"],
            tools=["read_file", "list_dir", "grep", "glob", "git_status", "git_diff", "run_tests"],
        ),
        Role(
            id="debugger",
            name="缺陷排查工程师",
            kind=RoleKind.QA,
            goal="复现缺陷、定位根因并生成修复建议，聚焦「定位」而非直接修复。",
            backstory="缺陷排查员（Troubleshooter）属于辅助层：负责复现、根因分析与修复"
            "建议；归入质量保障域（QA 类别）。",
            skills=["root-cause-analysis@0.1.0", "repro-steps@0.1.0"],
            tools=["read_file", "list_dir", "grep", "glob", "git_status", "git_diff", "run_tests", "run_python"],
        ),
        Role(
            id="governance",
            name="治理与流程 Agent",
            kind=RoleKind.PM,
            goal="维护流程规范与治理策略，审计变更并批准进化提案生效。",
            backstory="治理与流程 Agent 属于决策层：负责流程规范、治理策略与审计，"
            "可批准「进化生效」（evolution_apply 门）；归入决策层（PM 类别）。",
            skills=["process-governance@0.1.0", "audit-log@0.1.0", "policy-review@0.1.0"],
            tools=["read_file", "list_dir", "grep", "glob"],
            approval_scope=[GateKind.EVOLUTION_APPLY],
        ),
    ]
    return {role.id: role for role in roles}


class RoleRegistry:
    """岗位注册表：按岗位 id 查询/列举/按类别过滤，并提供会议默认参与岗位。

    - ``get(role_id)``：不存在时抛 ``KeyError``（消息含可用岗位清单）。
    - ``list()``：按岗位 id 排序返回全部岗位。
    - ``filter_by_kind(kind)``：返回指定 ``RoleKind`` 的岗位列表。
    - ``default_role_ids(meeting_kind)``：返回某类会议的默认参与岗位 id
      （§4.1 参与者列），供 meeting handler 使用。
    """

    # §4.1 各会议类型的默认参与岗位
    _MEETING_PARTICIPANTS: dict[MeetingKind, list[str]] = {
        MeetingKind.KICKOFF: [
            "pm", "pmo", "frontend", "backend", "algorithm", "architect",
            "qa", "devops", "docs", "reviewer", "debugger", "governance",
        ],
        MeetingKind.REQUIREMENT_REVIEW: ["pm", "architect", "frontend", "backend", "algorithm", "qa"],
        MeetingKind.DESIGN_REVIEW: ["architect", "pmo", "frontend", "backend", "qa", "devops"],
        MeetingKind.DAILY_STANDUP: [
            "pm", "pmo", "frontend", "backend", "algorithm", "qa",
            "devops", "docs", "reviewer", "debugger",
        ],
        MeetingKind.CODE_REVIEW: ["frontend", "backend", "reviewer"],
        MeetingKind.RETRO: [
            "pm", "pmo", "frontend", "backend", "algorithm", "architect",
            "qa", "devops", "docs", "reviewer", "debugger", "governance",
        ],
        MeetingKind.RELEASE_REVIEW: ["pm", "architect", "qa", "devops", "frontend", "backend"],
    }

    def __init__(self, roles: dict[str, Role] | None = None) -> None:
        """使用给定目录；缺省使用 ``build_role_catalog()``。"""
        self._roles: dict[str, Role] = dict(roles) if roles is not None else build_role_catalog()

    def get(self, role_id: str) -> Role:
        """按岗位 id 查询；不存在时抛 KeyError（含可用岗位清单）。"""
        try:
            return self._roles[role_id]
        except KeyError:
            raise KeyError(f"未注册岗位：{role_id!r}（可用岗位：{sorted(self._roles)}）") from None

    def list(self) -> list[Role]:
        """按岗位 id 排序返回全部岗位。"""
        return [self._roles[role_id] for role_id in sorted(self._roles)]

    def filter_by_kind(self, kind: RoleKind) -> list[Role]:
        """返回指定 ``RoleKind`` 的岗位列表（按岗位 id 排序）。"""
        return [role for role in self.list() if role.kind == kind]

    def default_role_ids(self, meeting_kind: MeetingKind | str) -> list[str]:
        """返回某类会议（§4.1）的默认参与岗位 id 列表。"""
        kind = MeetingKind(meeting_kind)
        return list(self._MEETING_PARTICIPANTS[kind])
