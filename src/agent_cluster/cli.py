"""CLI 入口（Task 7）：agent-cluster 命令（多 agent 组织型全栈开发集群运行时）。

子命令：
- ``run``：编译并运行 YAML 流程；遇审批门打印 ActionRequest 并交互读取
  ``accept/reject/response <内容>/edit <内容>`` 恢复运行；``--yes`` 无人值守
  模式自动接受（bypass-immune 高风险门自动转为拒绝），结束后打印运行摘要。
  ``--model`` 指定岗位模型后端：``deterministic``（缺省）/ ``deepseek-*`` /
  ``codex``（解析当前 Codex 配置）；缺省时也可用环境变量 ``DEEPSEEK_MODEL``。
- ``skills list``：列出技能目录（name/version/description）。
- ``roles list``：列出 12 岗位（id/name/kind/approval_scope）。
- ``proposals demo``：进化闭环演示（collect→distill→propose→review→apply→rollback）。
- ``metrics demo``：度量采集与信号触发演示。

``main()`` 返回 int 退出码；``python -m agent_cluster`` 等价于 agent-cluster。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import TextIO

import yaml
from langgraph.checkpoint.memory import MemorySaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from pydantic import BaseModel

from agent_cluster import models
from agent_cluster.doctor import run_doctor
from agent_cluster.evolution import Candidate, EvolutionEngine, EvolutionError
from agent_cluster.gates import approval_pending, make_gate_handler, resolve_auto_response
from agent_cluster.mcp_client import (
    StdioMCPClient,
    parse_server_command,
    register_mcp_resource_tool,
    register_mcp_tools,
)
from agent_cluster.meetings import MeetingHost, make_meeting_handler
from agent_cluster.plugins import PluginManager, default_plugin_search_dirs
from agent_cluster.repl import ReplSession
from agent_cluster.metrics import MetricRules, MetricsCollector
from agent_cluster.models import (
    ActionRequest,
    ApprovalRecord,
    ClusterState,
    Event,
    HumanResponse,
    Iteration,
    Project,
    Task,
    TaskStatus,
)
from agent_cluster.roles import RoleRegistry, build_role_catalog
from agent_cluster.runtime import AgentRuntime, ChatModelFactory, make_agent_handler
from agent_cluster.server import WorkbenchServer, serve_main
from agent_cluster.session import BuildResult, SessionDriver
from agent_cluster.skills import SkillCatalog, SkillLoader
from agent_cluster.tools import ToolSession, build_default_tools, load_agents_md
from agent_cluster.workflow import WorkflowEngine

__all__ = ["main", "run_flow", "RunSummary"]

# 审批交互提示文案
PROMPT_HINT = "请选择审批结论 [accept|reject|response <内容>|edit <内容>]："


def _collect_state_model_names() -> tuple[str, ...]:
    """枚举 agent_cluster.models 中参与状态序列化的公开类名（BaseModel 子类与 StrEnum）。

    - 供 ``MemorySaver(serde=JsonPlusSerializer(...))`` 的 allowed_msgpack_modules
      使用，避免每次 agent-cluster run 打印 langgraph 的「未注册类型」告警。
    - 覆盖 ClusterState 各通道实际出现的模型/枚举（Project/Iteration/Task/Meeting/
      Message/ActionRequest/ApprovalRecord/Ledger 及 GateKind/MeetingKind/
      MessageType/TaskStatus 等）；多列出的类不会序列化，无副作用。
    """
    names: list[str] = []
    for name, obj in vars(models).items():
        if name.startswith("_") or getattr(obj, "__module__", "") != models.__name__:
            continue
        if isinstance(obj, type) and (issubclass(obj, BaseModel) or issubclass(obj, StrEnum)):
            names.append(name)
    return tuple(sorted(names))


MODEL_NAMES = _collect_state_model_names()


@dataclass
class RunSummary:
    """一次 CLI run 会话的汇总结果（供测试与摘要打印）。"""

    thread_id: str
    events: list[Event] = field(default_factory=list)
    state: ClusterState | None = None
    decisions: list[ApprovalRecord] = field(default_factory=list)
    suspended_count: int = 0


# ---------------------------------------------------------------------------
# run 子命令核心逻辑（公开，供集成测试直接调用）
# ---------------------------------------------------------------------------


async def run_flow(
    flow_path: str | os.PathLike[str],
    *,
    project: str | None = None,
    yes: bool = False,
    thread_id: str | None = None,
    model: str | None = None,
    print_event: Callable[[Event], None] | None = None,
    print_request: Callable[[ActionRequest], None] | None = None,
    prompt: Callable[[str], str] | None = None,
    workspace: str | None = None,
    mcp_servers: Sequence[str] | None = None,
    max_rounds: int | None = None,
    tool_script: list[dict] | None = None,
    skills_root: str | None = None,
    role_tool_scripts: dict[str, list[dict]] | None = None,
    sandbox: Any | None = None,
    worktrees: bool = False,
) -> RunSummary:
    """编译并运行 YAML 流程，处理审批门挂起/恢复，返回汇总结果。

    - 编译 handlers：agent（AgentRuntime+RoleRegistry）、meeting
      （MeetingHost+RoleRegistry）、gate（make_gate_handler，``--yes`` 时
      auto_mode="accept"，否则 "ask" 交互挂起）。
    - ``MemorySaver`` 检查点；初始状态含 Project（来自 --project 目录名或流程名）、
      Iteration 与空列表。
    - 挂起时经 ``approval_pending`` 读取 ActionRequest：``yes=True`` 用
      ``resolve_auto_response(req, "accept")``（bypass-immune 自动拒绝），否则调用
      ``prompt`` 读取人工结论后 ``resume``；循环至 ``workflow_end``。
    """
    yaml_text = Path(flow_path).read_text(encoding="utf-8")
    flow_data = yaml.safe_load(yaml_text)
    spec_name = str((flow_data or {}).get("name") or "demo-flow")
    spec_thread = str((flow_data or {}).get("thread_id") or "")
    resolved_thread = thread_id or spec_thread or "default"

    role_registry = RoleRegistry()
    resolved_model = model or os.environ.get("DEEPSEEK_MODEL") or None
    default_model = (
        models.ModelConfig(model_name=resolved_model) if resolved_model else None
    )
    runtime = AgentRuntime(
        default_model=default_model,
        tool_script=tool_script,
        role_tool_scripts=role_tool_scripts,
    )
    host = MeetingHost()

    # 技能目录（可选 --skills-root）：工具模式注入岗位技能上下文
    catalog = None
    if skills_root:
        loader = SkillLoader()
        catalog = SkillCatalog()
        skills = loader.list_skills(skills_root)
        for role in role_registry.list():
            catalog.mount(role, skills)

    # 工具模式（--workspace）：受限工作区执行 + 可选 MCP 外部工具
    tool_session = None
    if workspace:
        workspace_path = Path(workspace).expanduser().resolve()
        workspace_path.mkdir(parents=True, exist_ok=True)
        registry = build_default_tools()
        for server_spec in mcp_servers or []:
            server_name, argv = parse_server_command(server_spec)
            mcp_client = StdioMCPClient(server_name, argv)
            await mcp_client.connect()  # fail-fast：连不上立即报错
            await register_mcp_tools(registry, mcp_client, server_name)
            await register_mcp_resource_tool(registry, mcp_client, server_name)
        tool_session = ToolSession(
            workspace_path,
            registry=registry,
            sandbox=sandbox,
            agents_md=load_agents_md(workspace_path),
        )
        from agent_cluster.subagent import SubagentBroker, register_subagent_tool

        register_subagent_tool(
            tool_session.registry,
            SubagentBroker(
                client_factory=lambda role_id="backend": runtime.client_for(role_registry.get(role_id)),
                usage_hook=runtime.report_usage,
            ),
        )

    worktree_manager = None
    if worktrees and workspace:
        from agent_cluster.worktree import WorktreeManager

        worktree_manager = WorktreeManager(workspace_path)
        prepared = worktree_manager.ensure_repo()
        if not prepared["ok"]:
            raise ValueError(f"worktree 初始化失败：{prepared['output']}")

    engine = WorkflowEngine(
        handlers={
            "agent": make_agent_handler(
                runtime,
                role_registry,
                catalog=catalog,
                tool_session=tool_session,
                max_rounds=max_rounds,
                worktree_manager=worktree_manager,
            ),
            "meeting": make_meeting_handler(host, role_registry),
            "gate": make_gate_handler(auto_mode="accept" if yes else "ask"),
        }
    )
    compiled = engine.compile(yaml_text)

    if project:
        project_name = os.path.basename(os.path.abspath(project))
    else:
        project_name = spec_name
    initial = {
        "project": Project(id=project_name, name=project_name, vision="多 agent 全栈 MVP 演示"),
        "iterations": [
            Iteration(id="iter:1", project_id=project_name, number=1, goal="交付可运行 MVP", status="in_progress")
        ],
        "tasks": [],
        "meetings": [],
        "messages": [],
        "decisions": [],
        "gate_payloads": {},
    }

    checkpointer = MemorySaver(
        serde=JsonPlusSerializer(
            allowed_msgpack_modules={("agent_cluster.models", name) for name in MODEL_NAMES}
        )
    )
    graph = compiled.compile_graph(checkpointer=checkpointer)
    prompt_fn = prompt if prompt is not None else input
    events: list[Event] = []
    suspended_count = 0
    first_run = True

    while True:
        if first_run:
            stream = compiled.run(
                initial=initial, thread_id=resolved_thread, checkpointer=checkpointer
            )
            first_run = False
        else:
            request = approval_pending(graph, resolved_thread)
            if request is None:
                raise RuntimeError("流程挂起但未从检查点找到待审批请求")
            if print_request is not None:
                print_request(request)
            if yes:
                response: HumanResponse = resolve_auto_response(request, "accept")
            else:
                response = _prompt_human(request, prompt_fn)
            stream = compiled.resume(resolved_thread, response, checkpointer=checkpointer)

        iteration_events = [event async for event in stream]
        for event in iteration_events:
            events.append(event)
            if print_event is not None:
                print_event(event)

        if not iteration_events or iteration_events[-1].type != "workflow_suspended":
            break
        suspended_count += 1

    snapshot = graph.get_state({"configurable": {"thread_id": resolved_thread}})
    raw_state = ClusterState.model_validate(snapshot.values)
    # 工具模式下保留真实任务状态（review/blocked 驱动退出码），不做归档
    final_state = raw_state if tool_session is not None else _finalize_tasks(raw_state)
    return RunSummary(
        thread_id=resolved_thread,
        events=events,
        state=final_state,
        decisions=list(final_state.decisions),
        suspended_count=suspended_count,
    )


def _finalize_tasks(state: ClusterState) -> ClusterState:
    """任务板归档（确定性演示收尾）：全部任务置为 done 并保证每条任务 ≥1 产出物。

    - agent 节点产出任务在创建时即 status=done 且携带产出物路径
      （``artifacts/<role_id>/<task_id>.md``，见 runtime.make_agent_handler）。
    - 会议行动项（todo）在确定性演示中没有真实跟进步骤，收尾时统一标记为已关闭
      （Done）并补齐产出物占位路径，使任务板满足「全部 Done、产出物存在」验收。
    """
    finalized: list[Task] = []
    for task in state.tasks:
        artifacts = list(task.artifacts)
        if not artifacts:
            artifacts.append(f"artifacts/{task.assignee_role or 'team'}/{task.id}.md")
        finalized.append(task.model_copy(update={"status": TaskStatus.DONE, "artifacts": artifacts}))
    return state.model_copy(update={"tasks": finalized})


def _prompt_human(request: ActionRequest, prompt_fn: Callable[[str], str]) -> HumanResponse:
    """交互读取人工审批结论，返回对应 HumanResponse。"""
    while True:
        raw = prompt_fn(PROMPT_HINT).strip()
        if not raw:
            continue
        parts = raw.split(maxsplit=1)
        kind = parts[0].lower()
        arg = parts[1] if len(parts) > 1 else None
        if kind == "accept":
            return HumanResponse(type="accept")
        if kind == "reject":
            return HumanResponse(type="reject")
        if kind in ("response", "edit"):
            if arg is None:
                print(f"  提示：{kind} 需要提供内容，例如：{kind} 请补充验收标准")
                continue
            return HumanResponse(type=kind, args={"text": arg})
        print(f"  无效输入：{raw!r}（支持 accept / reject / response <内容> / edit <内容>）")


# ---------------------------------------------------------------------------
# 事件 / 请求 / 摘要打印
# ---------------------------------------------------------------------------


def _print_event(event: Event, out: TextIO) -> None:
    """按事件类型打印一行中文描述。"""
    if event.type == "node_start":
        print(f"[节点开始] {event.actor}", file=out)
    elif event.type == "node_end":
        print(f"[节点结束] {event.actor}", file=out)
    elif event.type == "meeting_held":
        print(f"[会议] {event.actor} 完成（决策 {event.payload.get('decisions', 0)} 项）", file=out)
    elif event.type == "agent_step":
        print(f"[执行] {event.actor}（节点 {event.payload.get('node', '')}）", file=out)
    elif event.type == "tool_result":
        payload = event.payload
        print(
            f"[工具] {event.actor}：{payload.get('tool')} ok={payload.get('ok')} "
            f"（{payload.get('duration', 0)}s）",
            file=out,
        )
    elif event.type == "workflow_suspended":
        print(f"[挂起] 流程在节点 {event.payload.get('node_id', '')} 等待审批", file=out)
    elif event.type == "workflow_start":
        print(f"[开始] 流程「{event.payload.get('name', '')}」运行", file=out)
    elif event.type == "workflow_end":
        print("[完成] 流程运行结束", file=out)
    else:
        print(f"[{event.type}] {event.actor}", file=out)


def _print_request(request: ActionRequest, out: TextIO) -> None:
    """打印待审批 ActionRequest 的要点。"""
    print(f"  待审批请求：{request.title}", file=out)
    print(
        f"    类别：{request.kind.value} | 风险：{request.risk_level} | "
        f"bypass-immune：{request.bypass_immune}",
        file=out,
    )
    print(f"    说明：{request.description}", file=out)


def _print_summary(summary: RunSummary, out: TextIO) -> None:
    """打印运行摘要：会议/任务/审批/事件统计。"""
    state = summary.state
    print("\n===== 运行摘要 =====", file=out)
    print(f"线程：{summary.thread_id}", file=out)
    print(f"事件总数：{len(summary.events)}", file=out)
    print(f"挂起次数：{summary.suspended_count}", file=out)
    if state is None:
        return
    print(f"会议数：{len(state.meetings)}", file=out)
    statuses = Counter(task.status.value for task in state.tasks)
    print(f"任务数：{len(state.tasks)}（状态分布：{dict(statuses)}）", file=out)
    print(f"审批记录数：{len(summary.decisions)}", file=out)
    for record in summary.decisions:
        print(f"  - {record.type}（by {record.by_role}）", file=out)
    artifacts = [artifact for task in state.tasks for artifact in task.artifacts]
    print(f"产出物：{len(artifacts)} 个", file=out)
    for artifact in artifacts:
        print(f"  - {artifact}", file=out)


# ---------------------------------------------------------------------------
# 子命令实现
# ---------------------------------------------------------------------------


def _cmd_run(args: argparse.Namespace) -> int:
    """run 子命令：编译并运行流程。

    - 若指定了 ``--model``/``DEEPSEEK_MODEL``，先构造一次模型客户端做启动校验
      （fail-fast）：key 缺失、Codex 配置无法解析、模型名未知时立即报错返回 1，
      而不是等首个岗位节点才暴露。
    """
    out = sys.stdout
    model_name = args.model or os.environ.get("DEEPSEEK_MODEL") or None
    if model_name:
        try:
            ChatModelFactory().create(
                models.AgentConfig(model=models.ModelConfig(model_name=model_name))
            )
        except Exception as exc:  # noqa: BLE001 —— CLI 顶层统一错误出口
            print(f"模型配置无效（{model_name}）：{exc}", file=sys.stderr)
            return 1
    tool_script = None
    if args.tool_script:
        try:
            raw = Path(args.tool_script).read_text(encoding="utf-8")
            tool_script = json.loads(raw)
            if not isinstance(tool_script, list):
                raise ValueError("tool_script 必须是 JSON 数组")
        except Exception as exc:  # noqa: BLE001 —— CLI 顶层统一错误出口
            print(f"tool_script 加载失败（{args.tool_script}）：{exc}", file=sys.stderr)
            return 1
    sandbox, sandbox_err = _build_sandbox(
        args.sandbox, Path(args.workspace).expanduser().resolve() if args.workspace else None
    )
    if sandbox_err:
        print(sandbox_err, file=sys.stderr)
        return 1
    if args.worktrees and not args.workspace:
        print("--worktrees 需要 --workspace（git worktree 基于工作区仓库）", file=sys.stderr)
        return 1
    try:
        summary = asyncio.run(
            run_flow(
                args.flow,
                project=args.project,
                yes=args.yes,
                thread_id=args.thread,
                model=args.model,
                print_event=lambda event: _print_event(event, out),
                print_request=lambda request: _print_request(request, out),
                workspace=args.workspace,
                mcp_servers=list(args.mcp or []),
                max_rounds=args.max_rounds,
                tool_script=tool_script,
                skills_root=args.skills_root,
                sandbox=sandbox,
                worktrees=args.worktrees,
            )
        )
    except Exception as exc:  # noqa: BLE001 —— CLI 顶层统一错误出口
        print(f"运行失败：{exc}", file=sys.stderr)
        return 1
    _print_summary(summary, out)
    # 工具模式验收：存在验收未通过的岗位任务（review/blocked）→ 退出码 1。
    # 会议生成的 todo 行动项属于积压清单，不视为失败。
    if args.workspace and summary.state is not None:
        failed = [
            task for task in summary.state.tasks
            if task.status in (TaskStatus.REVIEW, TaskStatus.BLOCKED)
        ]
        if failed:
            print(
                f"存在验收未通过的岗位任务（{len(failed)} 个："
                f"{', '.join(task.status.value for task in failed[:5])}），退出码 1",
                file=sys.stderr,
            )
            return 1
    return 0


def _cmd_skills_list(args: argparse.Namespace) -> int:
    """skills list 子命令：列出技能目录。"""
    try:
        skills = SkillLoader().list_skills(args.root)
    except Exception as exc:  # noqa: BLE001 —— CLI 顶层统一错误出口
        print(f"技能列表失败：{exc}", file=sys.stderr)
        return 1
    print(f"共 {len(skills)} 个技能：")
    for skill in skills:
        print(f"  - {skill.name}@{skill.version}：{skill.description}")
    return 0


def _cmd_roles_list(args: argparse.Namespace) -> int:
    """roles list 子命令：列出 12 岗位。"""
    roles = RoleRegistry(build_role_catalog()).list()
    print(f"共 {len(roles)} 个岗位：")
    for role in roles:
        scope = ",".join(gate.value for gate in role.approval_scope) or "-"
        print(
            f"  - {role.id}（{role.name}）| 类别：{role.kind.value} | 审批范围：{scope}"
        )
    return 0


def _cmd_proposals_demo(args: argparse.Namespace) -> int:
    """proposals demo 子命令：六步进化闭环演示。"""
    engine = EvolutionEngine()
    fabricated_events = [
        Event(
            id="ev-metric-1",
            run_id="demo",
            thread_id="demo",
            type="metric_threshold",
            actor="metric_rules",
            payload={"source": "rework_rate", "evidence": ["rework_rate=0.45@iter=1"], "severity": "high"},
        ),
        Event(
            id="ev-review-1",
            run_id="demo",
            thread_id="demo",
            type="review_result",
            actor="reviewer",
            payload={"verdict": "reject", "target": "frontend-design"},
        ),
        Event(
            id="ev-review-2",
            run_id="demo",
            thread_id="demo",
            type="review_result",
            actor="reviewer",
            payload={"verdict": "reject", "target": "frontend-design"},
        ),
        Event(
            id="ev-retro-1",
            run_id="demo",
            thread_id="demo",
            type="retro",
            actor="pm",
            payload={"root_cause": "需求歧义导致返工"},
        ),
    ]

    print("① 收集信号：")
    signals = engine.collect(fabricated_events)
    for signal in signals:
        print(f"  - {signal.type} | severity={signal.severity} | source={signal.source}")
    if not signals:
        print("  未收集到信号")
        return 0

    print("② 提炼候选：")
    candidates = engine.distill(signals)
    for candidate in candidates:
        print(f"  - {candidate.category} → {candidate.target}（{len(candidate.evidence)} 条证据）")
    if not candidates:
        print("  无可提炼候选")
        return 0

    print("③ 提案：")
    chosen = candidates[0]
    proposal = engine.propose(
        chosen,
        author_role="pm",
        title=f"改进 {chosen.target}（{chosen.category}）",
        rollback_plan="回滚到上一版本并恢复目录",
        validation_plan="灰度 1 个迭代验证后再全量",
    )
    print(
        f"  - {proposal.title} | 类别：{proposal.category} | 风险：{proposal.risk_level} | "
        f"状态：{proposal.status} | 回滚方案：{proposal.rollback_plan}"
    )

    print("④ 评审：")
    engine.review(proposal, approver="governance", decision="approve", reason="演示评审通过")
    print(f"  - 状态：{proposal.status}（approver=governance）")

    print("⑤ 生效：")
    engine.apply(proposal)
    print(
        f"  - 状态：{proposal.status} | 版本：{proposal.effective_version} | "
        f"灰度：{proposal.gray}"
    )

    print("⑥ 回滚：")
    engine.rollback(proposal, reason="演示回滚（观察期发现回归）")
    print(f"  - 状态：{proposal.status} | 审计事件：{len(engine.audit_events)} 条")
    return 0


def _cmd_proposals_submit(args: argparse.Namespace) -> int:
    """proposals submit 子命令：构造进化提案并自动评审（演示 CLI）。

    - ``--title`` / ``--rollback-plan`` 必填；缺回滚方案（缺失或空白）时
      打印清晰错误并以非零退出码结束。
    - 经 EvolutionEngine.propose 构造提案（含 rollback_plan 强制校验），
      打印提案 id/状态/版本；随后自动评审（approver=governance，记录 Vote）。
    """
    rollback_plan = (args.rollback_plan or "").strip()
    if not rollback_plan:
        print("提案失败：缺少 --rollback-plan（回滚方案为必填项，不可为空）", file=sys.stderr)
        return 1
    engine = EvolutionEngine()
    candidate = Candidate(
        category=args.category,
        target=args.title,
        change={"kind": "improve", "target": args.title},
        evidence=["cli: proposals submit"],
        expected_impact="改善流程/技能（CLI 提交演示）",
    )
    try:
        proposal = engine.propose(
            candidate,
            author_role=args.author_role,
            title=args.title,
            rollback_plan=rollback_plan,
            validation_plan="灰度 1 个迭代验证后再全量",
        )
    except EvolutionError as exc:
        print(f"提案失败：{exc}", file=sys.stderr)
        return 1
    print(f"已提交提案：{proposal.id}")
    print(
        f"  标题：{proposal.title} | 类别：{proposal.category} | 风险：{proposal.risk_level}"
    )
    print(
        f"  状态：{proposal.status} | 版本：{proposal.effective_version} | "
        f"回滚方案：{rollback_plan}"
    )
    engine.review(proposal, approver="governance", decision="approve", reason="CLI 提交演示自动评审")
    print(f"评审结果：{proposal.status}（approver=governance，Vote {len(proposal.votes)} 条）")
    return 0


def _cmd_metrics_demo(args: argparse.Namespace) -> int:
    """metrics demo 子命令：度量采集 + 阈值规则信号演示。"""
    collector = MetricsCollector()
    print("采集度量点：")
    points = [
        ("review_pass_rate", 0.45, {"iteration": "iter-1"}),
        ("rework_rate", 0.40, {"iteration": "iter-1"}),
        ("rework_rate", 0.55, {"iteration": "iter-2"}),
        ("action_item_close_rate", 0.60, {"iteration": "iter-2"}),
        ("loop_iterations", 6, {"iteration": "iter-2"}),
        ("gate_wait_seconds", 96000, {"iteration": "iter-2"}),
    ]
    for name, value, tags in points:
        collector.record(name, value, tags=tags)
        print(f"  - {name}={value}（tags={tags}）")

    snapshot = collector.snapshot()
    print(f"快照指标数：{len(snapshot.metrics)}")
    signals = MetricRules.evaluate(snapshot)
    print(f"触发信号数：{len(signals)}")
    for signal in signals:
        print(
            f"  - {signal.type} | severity={signal.severity} | "
            f"evidence={signal.evidence}"
        )
    return 0


def _cmd_serve(args: argparse.Namespace) -> int:
    """serve 子命令（v0.5）：启动本地 Web 工作台后端（REST+SSE）。

    - 全局索引 ``~/.agent-cluster/index.json``；会话在独立线程运行，HITL 经 API 桥接。
    - 默认仅监听 127.0.0.1；--auth-token 开启认证（X-Auth-Token 头）。
    - 退出码：0 正常停止 / 1 启动失败。
    """
    try:
        return serve_main(args)
    except OSError as exc:
        print(f"serve 启动失败：{exc}", file=sys.stderr)
        return 1


def _cmd_doctor(args: argparse.Namespace) -> int:
    """doctor 子命令：环境预检（Python/git/Docker 硬依赖 + 模型/工作区/插件/MCP 信息性）。"""
    report = run_doctor(
        model=args.model,
        workspace=args.workspace,
        plugin_dirs=list(args.plugin_dir or []),
        mcp_servers=list(args.mcp or []),
        skip_docker_check=args.skip_docker_check,
    )
    print(report.render())
    if not report.ok:
        print(
            "存在阻塞项：请按上方指引修复（Docker 缺失可 --skip-docker-check 临时跳过）。",
            file=sys.stderr,
        )
        return 1
    return 0


def _cmd_tools_list(args: argparse.Namespace) -> int:
    """tools list 子命令：列出内置工具与权限分层。"""
    registry = build_default_tools()
    specs = registry.list()
    print(f"共 {len(specs)} 个内置工具：")
    for spec in specs:
        print(f"  - {spec.name} | 权限：{spec.permission.value} | {spec.description[:70]}")
    return 0


def _cmd_mcp_list(args: argparse.Namespace) -> int:
    """mcp list 子命令：连接 MCP stdio 服务器并列出其工具。"""
    try:
        server_name, argv = parse_server_command(args.server)

        async def _list() -> list[dict]:
            client = StdioMCPClient(server_name, argv)
            try:
                await client.connect()
                tools = await client.list_tools()
                return tools
            finally:
                await client.close()

        tools = asyncio.run(_list())
    except Exception as exc:  # noqa: BLE001 —— CLI 顶层统一错误出口
        print(f"MCP 服务器列表失败：{exc}", file=sys.stderr)
        return 1
    print(f"MCP 服务器 {server_name} 共 {len(tools)} 个工具：")
    for tool in tools:
        print(f"  - {tool.get('name')}：{(tool.get('description') or '')[:70]}")
    return 0


def _cmd_plugins_list(args: argparse.Namespace) -> int:
    """plugins list 子命令：列出发现的插件、技能与 hooks。"""
    from agent_cluster.plugins import PluginManager, default_plugin_search_dirs

    search_dirs = list(args.plugin_dir or []) + default_plugin_search_dirs()
    manager = PluginManager(search_dirs=search_dirs)
    try:
        plugins = manager.list_plugins()
        skills = manager.load_skills()
    except Exception as exc:  # noqa: BLE001 —— CLI 顶层统一错误出口
        print(f"插件列表失败：{exc}", file=sys.stderr)
        return 1
    print(f"共 {len(plugins)} 个插件（搜索目录：{len(search_dirs)} 个）：")
    for manifest in plugins:
        hook_events = [event for event, specs in manifest.hooks.items() if specs]
        print(
            f"  - {manifest.name}@{manifest.version}：{(manifest.description or '')[:60]}"
            f" | 技能：{len(manifest.skill_dirs)} | hooks：{', '.join(hook_events) or '-'}"
        )
    print(f"共 {len(skills)} 个插件技能：")
    for skill in skills:
        print(f"  - {skill.name}@{skill.version}：{skill.description[:60]}")
    return 0


# ---------------------------------------------------------------------------
# build 子命令（v0.3 会话式产品构建）
# ---------------------------------------------------------------------------

DEFAULT_BUILD_FLOW = "examples/flows/build-product.yaml"


def _build_plugin_manager(plugin_dirs: Sequence[str]) -> PluginManager | None:
    """构造插件管理器：显式目录 + 默认搜索目录；扫描失败返回 None（不阻断会话）。"""
    search_dirs = list(plugin_dirs or []) + default_plugin_search_dirs()
    if not search_dirs:
        return None
    manager = PluginManager(search_dirs=search_dirs)
    try:
        manager.scan()
        manager.load_skills()
    except Exception:  # noqa: BLE001 —— 插件扫描失败不阻断主流程
        return None
    return manager




def _build_sandbox(mode: str | None, workspace: Path | None) -> tuple[Any | None, str | None]:
    """构造 Docker 沙箱执行器；``--sandbox docker`` 且不可用时返回错误信息。

    - mode 非 docker -> (None, None)（本机执行，向后兼容）。
    - 需要 ``--workspace``（沙箱挂载工作区）；Docker 不可用返回安装指引。
    - 返回 ``(runner, error)``：error 非空时调用方打印并退出非零码。
    """
    if not mode or mode == "none":
        return None, None
    if mode != "docker":
        return None, f"未知沙箱模式：{mode!r}（支持 none / docker）"
    if workspace is None:
        return None, "--sandbox docker 需要 --workspace（沙箱把工作区挂载进容器）"
    from agent_cluster.sandbox import SandboxRunner, SandboxUnavailableError, docker_available

    if not docker_available():
        return None, (
            "--sandbox docker 需要 Docker（docker version 失败）；请安装 Docker Desktop "
            "并启动，或用 --sandbox none 在本机执行。"
        )
    try:
        return SandboxRunner(workspace), None
    except SandboxUnavailableError as exc:
        return None, str(exc)
    except Exception as exc:  # noqa: BLE001 —— CLI 顶层统一错误出口
        return None, f"沙箱初始化失败：{exc}"


def _slug(goal: str) -> str:
    """把需求目标转成安全的目录名（保留中文/字母数字，其余转 -）。"""
    safe = re.sub(r"[^\w\u4e00-\u9fff-]+", "-", goal.strip())
    return (safe or "build")[:40].strip("-") or "build"


def _cmd_build(args: argparse.Namespace) -> int:
    """build 子命令：输入一个需求，向导交互全生命周期产出完整交付包。

    - 默认真实 LLM（--model codex 解析当前对话模型）；--deterministic 仅供演示。
    - token 制规划与计量：--budget 全局预算、阶段预算按比例、超限升级人工。
    - 交互命令：/status /budget /skip /abort；--resume 断点续跑。
    - 退出码：0 成功 / 1 存在验收未通过任务 / 2 用户中止（检查点已保存）/
      3 升级结束（保存现状）。
    """
    out = sys.stdout
    if not args.goal and not args.resume:
        print("build 需要 --goal（除非使用 --resume 续跑已有会话）", file=sys.stderr)
        return 2
    flow = args.flow or DEFAULT_BUILD_FLOW
    flow_path = Path(flow)
    if not flow_path.is_file():
        print(f"流程文件不存在：{flow_path}（可用 --flow 指定自定义流程）", file=sys.stderr)
        return 1
    workspace = args.workspace or str(Path.cwd() / "build-out" / _slug(args.goal or "resume"))
    _sandbox, sandbox_err = _build_sandbox(args.sandbox, Path(workspace))
    if sandbox_err:
        print(sandbox_err, file=sys.stderr)
        return 1

    qa_script: list[str] | None = None
    if args.qa_script:
        try:
            raw = Path(args.qa_script).read_text(encoding="utf-8")
            qa_script = json.loads(raw)
            if not isinstance(qa_script, list) or not all(isinstance(x, str) for x in qa_script):
                raise ValueError("qa_script 必须是字符串数组")
        except Exception as exc:  # noqa: BLE001 —— CLI 顶层统一错误出口
            print(f"qa_script 加载失败（{args.qa_script}）：{exc}", file=sys.stderr)
            return 1
    judge = None
    if not args.deterministic and not args.no_judge:
        from agent_cluster.judge import LLMJudge

        judge = LLMJudge(model=args.model or "codex")
    tool_script: list[dict] | None = None
    if args.tool_script:
        try:
            raw = Path(args.tool_script).read_text(encoding="utf-8")
            tool_script = json.loads(raw)
            if not isinstance(tool_script, list):
                raise ValueError("tool_script 必须是 JSON 数组")
        except Exception as exc:  # noqa: BLE001 —— CLI 顶层统一错误出口
            print(f"tool_script 加载失败（{args.tool_script}）：{exc}", file=sys.stderr)
            return 1
    role_tool_scripts: dict[str, list[dict]] | None = None
    if args.role_tool_script:
        try:
            raw = Path(args.role_tool_script).read_text(encoding="utf-8")
            role_tool_scripts = json.loads(raw)
            if not isinstance(role_tool_scripts, dict):
                raise ValueError("role_tool_script 必须是 {role: [tool_call, ...]} JSON 对象")
        except Exception as exc:  # noqa: BLE001 —— CLI 顶层统一错误出口
            print(f"role_tool_script 加载失败（{args.role_tool_script}）：{exc}", file=sys.stderr)
            return 1

    try:
        result: BuildResult = asyncio.run(
            SessionDriver(
                workspace=workspace,
                goal=args.goal or "",
                flow=flow_path,
                model=args.model or "codex",
                budget=args.budget,
                rework_limit=args.max_rework,
                yes=args.yes,
                deterministic=args.deterministic,
                resume=args.resume,
                qa_script=qa_script,
                tool_script=tool_script,
                role_tool_scripts=role_tool_scripts,
                skills_root=args.skills_root,
                mcp_servers=list(args.mcp or []),
                max_rounds=args.max_rounds,
                print_fn=lambda s: print(s, file=out),
                plugin_manager=_build_plugin_manager(list(args.plugin_dir or [])),
                sandbox=_sandbox,
                judge=judge,
            ).run()
        )
    except Exception as exc:  # noqa: BLE001 —— CLI 顶层统一错误出口
        print(f"build 失败：{exc}", file=sys.stderr)
        return 1

    _print_build_summary(result, out)
    return result.exit_code


def _cmd_eval(args: argparse.Namespace) -> int:
    """eval 子命令：确定性回归集 + 基线对比防退化（T12.6 质量门禁）。

    - 运行内置场景（mini-pm-gate / dev-qa-gate / full-build），汇总三项指标；
    - 与 --baseline 对比（相对下降超 --threshold 判定回归，退出码 1）；
    - --save-baseline 把本次报告存为基线；--scenario 可过滤单个场景。
    """
    from agent_cluster.eval import (
        BUILTIN_SUITE,
        compare_to_baseline,
        load_baseline,
        run_suite,
        save_baseline,
    )
    suite = [item for item in BUILTIN_SUITE if args.scenario in (None, "", item.name)]
    if not suite:
        print(
            f"未找到场景：{args.scenario}（可用：{', '.join(item.name for item in BUILTIN_SUITE)}）",
            file=sys.stderr,
        )
        return 2
    try:
        report = run_suite(root=args.workspace, suite=suite)
    except Exception as exc:  # noqa: BLE001 —— CLI 顶层统一错误出口
        print(f"eval 运行失败：{exc}", file=sys.stderr)
        return 1
    print("===== eval 回归集 =====", file=sys.stdout)
    for entry in report["scenarios"]:
        mark = "PASS" if entry["passed"] else "FAIL"
        print(
            f"  [{mark}] {entry['name']}：exit={entry['exit_code']} "
            f"门={entry['gate_decisions']} 缺失={entry['missing_files'] or '-'} tokens={entry['tokens_used']}",
            file=sys.stdout,
        )
    metrics = report["metrics"]
    print(
        f"指标：完成率 {metrics['completion_rate']:.1%} | "
        f"工具正确性 {metrics['tool_correctness']:.1%} | "
        f"测试通过率 {metrics['test_pass_rate']:.1%} | 总 tokens {report['total_tokens']}",
        file=sys.stdout,
    )
    if args.save_baseline:
        save_baseline(report, args.baseline)
        print(f"基线已保存：{args.baseline}", file=sys.stdout)
        return 0
    baseline = load_baseline(args.baseline)
    if baseline is None:
        print(f"无基线（{args.baseline}），本次结果未对比；用 --save-baseline 建立基线。", file=sys.stdout)
        return 0
    issues = compare_to_baseline(report, baseline, threshold=args.threshold)
    if issues:
        for issue in issues:
            print(f"回归：{issue}", file=sys.stderr)
        return 1
    print("与基线对比：无回归。", file=sys.stdout)
    return 0


def _print_build_summary(result: BuildResult, out: TextIO) -> None:
    """打印 build 结果摘要（含 token 计量报表与交付物索引）。"""
    print("\n===== build 结果 =====", file=out)
    print(f"会话：{result.session_id} | 线程：{result.thread_id}", file=out)
    print(f"目标：{result.goal}", file=out)
    print(f"工作区：{result.workspace}", file=out)
    print(f"挂起交互：{result.suspended_count} 次 | 事件：{len(result.events)} | 退出码：{result.exit_code}", file=out)
    if result.token_summary:
        summary = result.token_summary
        print("\n--- token 计量报表 ---", file=out)
        print(
            f"预算 {summary['budget']} | 已用 {summary['used']} | 剩余 {summary['remaining']} | "
            f"超限 {'是' if summary['over_budget'] else '否'}",
            file=out,
        )
        print(f"按阶段：{summary['by_phase'] or '（无）'}", file=out)
        print(f"按角色：{summary['by_role'] or '（无）'}", file=out)
        accuracy = summary.get("estimate_accuracy")
        print(f"预估准确率：{accuracy:.1%}" if accuracy is not None else "预估准确率：（纯估算模式）", file=out)
    if result.delivery:
        print("\n--- 交付包 ---", file=out)
        print(f"交付说明：{result.delivery['delivery_path']}", file=out)
        artifacts = result.delivery.get("artifacts") or []
        print(f"产物 {len(artifacts)} 个：", file=out)
        for artifact in artifacts[:20]:
            print(f"  - {artifact}", file=out)
        if len(artifacts) > 20:
            print(f"  ... 等共 {len(artifacts)} 个", file=out)


def _cmd_chat(args: argparse.Namespace) -> int:
    """chat 子命令（v0.4）：连续多轮开发 REPL。

    - 工具模式：每轮指令按关键词选岗 -> ReAct 工具循环（真实工作区执行）。
    - 插件：``--plugin-dir`` 发现插件，session_start/end 等 hooks 全自动执行。
    - token 计量：每次模型调用经 TokenLedger 记账，``/status``/``/budget`` 展示。
    - 退出码：0 正常退出 / 1 运行失败 / 2 中断。
    """
    workspace = args.workspace or str(Path.cwd())
    _sandbox, sandbox_err = _build_sandbox(args.sandbox, Path(workspace))
    if sandbox_err:
        print(sandbox_err, file=sys.stderr)
        return 1
    session = ReplSession(
        workspace=workspace,
        model=args.model or "codex",
        budget=args.budget,
        max_rounds=args.max_rounds,
        deterministic=args.deterministic,
        yes=args.yes,
        skills_root=args.skills_root,
        mcp_servers=list(args.mcp or []),
        plugin_manager=_build_plugin_manager(list(args.plugin_dir or [])),
        sandbox=_sandbox,
    )
    try:
        return session.run()
    except KeyboardInterrupt:
        print("已中断（chat 退出）。", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001 —— CLI 顶层统一错误出口
        print(f"chat 失败：{exc}", file=sys.stderr)
        return 1


# ---------------------------------------------------------------------------
# argparse 装配与入口
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    """构造 CLI 参数解析器（全部子命令中文帮助）。"""
    parser = argparse.ArgumentParser(
        prog="agent-cluster",
        description="多 agent 组织型全栈开发集群运行时（Python + LangGraph）",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="编译并运行 YAML 流程（含审批交互）")
    run_parser.add_argument("--flow", required=True, help="流程 YAML 文件路径")
    run_parser.add_argument("--project", default=None, help="项目目录（生成项目名，缺省用流程名）")
    run_parser.add_argument("--yes", action="store_true", help="无人值守：自动接受全部审批（bypass-immune 自动拒绝）")
    run_parser.add_argument("--thread", default=None, help="线程 id（缺省用流程 YAML 的 thread_id）")
    run_parser.add_argument(
        "--model",
        default=None,
        help="岗位模型后端：deterministic（缺省，无需 key）/ deepseek-*（DeepSeek API，"
        "读取 DEEPSEEK_API_KEY）/ codex（解析当前 Codex 配置）；缺省也可用环境变量 DEEPSEEK_MODEL",
    )
    run_parser.add_argument(
        "--workspace",
        default=None,
        help="工作区目录（启用工具模式：空目录=新项目，已有 git 目录=既有仓库功能开发）",
    )
    run_parser.add_argument(
        "--sandbox",
        default=None,
        choices=["none", "docker"],
        help="执行沙箱：none（本机，缺省）/ docker（容器内执行 shell/python/tests/service）",
    )
    run_parser.add_argument(
        "--worktrees",
        action="store_true",
        help="git worktree 隔离：每开发角色独立 worktree 提交，节点完成后合并回主工作区",
    )
    run_parser.add_argument(
        "--mcp",
        action="append",
        default=[],
        metavar="NAME=COMMAND",
        help="MCP stdio 服务器（可重复）：name=command，工具注册为 mcp_<name>_<tool>（危险权限）",
    )
    run_parser.add_argument(
        "--max-rounds",
        type=int,
        default=None,
        help="工具模式 ReAct 最大轮数（缺省 6）",
    )
    run_parser.add_argument(
        "--tool-script",
        default=None,
        help="确定性演示工具脚本 JSON 文件（[{name, args}, ...]，无 API key 跑通工具全链路）",
    )
    run_parser.add_argument(
        "--skills-root",
        default=None,
        help="技能根目录（挂载岗位技能上下文到工具模式 system prompt）",
    )
    run_parser.set_defaults(func=_cmd_run)

    build_parser = subparsers.add_parser(
        "build",
        help="会话式产品构建（v0.3）：一个需求 → 澄清 → 里程碑门 → 完整交付包，token 制规划与计量",
    )
    build_parser.add_argument("--goal", default=None, help="产品需求目标（--resume 时可省略）")
    build_parser.add_argument("--workspace", default=None, help="工作区目录（缺省 build-out/<目标>）")
    build_parser.add_argument("--sandbox", default=None, choices=["none", "docker"], help="执行沙箱：none（本机，缺省）/ docker（容器内执行）")
    build_parser.add_argument("--flow", default=None, help="生命周期流程 YAML（缺省 examples/flows/build-product.yaml）")
    build_parser.add_argument(
        "--model",
        default="codex",
        help="模型后端：codex（缺省，解析当前对话模型）/ deterministic（演示）/ deepseek-*",
    )
    build_parser.add_argument("--resume", action="store_true", help="断点续跑（沿用已保存会话与检查点）")
    build_parser.add_argument("--budget", type=int, default=None, help="全局 token 预算（缺省 500000）")
    build_parser.add_argument("--max-rework", type=int, default=None, help="单门返工上限（缺省 3，超过升级人工）")
    build_parser.add_argument("--deterministic", action="store_true", help="确定性演示模式（无需 API key）")
    build_parser.add_argument(
        "--no-judge", action="store_true",
        help="关闭 LLM-as-judge 门禁评审（默认真实 LLM 模式开启；确定性模式自动关闭）",
    )
    build_parser.add_argument("--yes", action="store_true", help="无人值守：门自动接受、澄清用缺省答案并留痕")
    build_parser.add_argument("--qa-script", default=None, help="脚本化澄清问答 JSON 文件（字符串数组）")
    build_parser.add_argument("--tool-script", default=None, help="确定性工具脚本 JSON 文件")
    build_parser.add_argument("--role-tool-script", default=None, help="按岗位工具脚本 JSON 文件（{role: [tool_call]}）")
    build_parser.add_argument("--skills-root", default=None, help="技能根目录（挂载岗位技能上下文）")
    build_parser.add_argument(
        "--plugin-dir", action="append", default=[], metavar="DIR",
        help="插件搜索目录（可重复；缺省包含 ~/.codex/plugins/cache），插件技能与 hooks 自动接入",
    )
    build_parser.add_argument(
        "--mcp", action="append", default=[], metavar="NAME=COMMAND",
        help="MCP stdio 服务器（可重复），外部工具一律危险权限",
    )
    build_parser.add_argument("--max-rounds", type=int, default=None, help="工具模式 ReAct 最大轮数（缺省 6）")
    build_parser.set_defaults(func=_cmd_build)

    chat_parser = subparsers.add_parser(
        "chat",
        help="连续多轮开发 REPL（v0.4）：工具模式 + 插件 hooks + token 计量，多轮上下文保持",
    )
    chat_parser.add_argument("--workspace", default=None, help="工作区目录（缺省当前目录）")
    chat_parser.add_argument("--sandbox", default=None, choices=["none", "docker"], help="执行沙箱：none（本机，缺省）/ docker（容器内执行）")
    chat_parser.add_argument("--model", default="codex", help="模型后端：codex（缺省）/ deterministic / deepseek-*")
    chat_parser.add_argument("--budget", type=int, default=None, help="全局 token 预算（缺省 500000）")
    chat_parser.add_argument("--max-rounds", type=int, default=None, help="单轮 ReAct 最大轮数（缺省 6）")
    chat_parser.add_argument("--deterministic", action="store_true", help="确定性演示模式（无需 API key）")
    chat_parser.add_argument("--yes", action="store_true", help="无人值守：危险工具自动拒绝、澄清用缺省答案")
    chat_parser.add_argument("--skills-root", default=None, help="技能根目录（挂载岗位技能上下文）")
    chat_parser.add_argument(
        "--plugin-dir", action="append", default=[], metavar="DIR",
        help="插件搜索目录（可重复；缺省包含 ~/.codex/plugins/cache），插件技能与 hooks 自动接入",
    )
    chat_parser.add_argument(
        "--mcp", action="append", default=[], metavar="NAME=COMMAND",
        help="MCP stdio 服务器（可重复），外部工具一律危险权限",
    )
    chat_parser.set_defaults(func=_cmd_chat)

    serve_parser = subparsers.add_parser(
        "serve", help="启动本地 Web 工作台后端（v0.5：REST+SSE，会话/项目/记忆/度量）"
    )
    serve_parser.add_argument("--host", default="127.0.0.1", help="监听地址（默认仅本机）")
    serve_parser.add_argument("--port", type=int, default=8765, help="监听端口（默认 8765）")
    serve_parser.add_argument("--auth-token", default="", help="可选认证 token（请求头 X-Auth-Token）")
    serve_parser.add_argument("--plugin-dir", action="append", default=[], help="插件目录（可重复）")
    serve_parser.set_defaults(func=_cmd_serve)

    doctor_parser = subparsers.add_parser("doctor", help="环境预检（Python/git/Docker/模型/工作区/插件/MCP）")
    doctor_parser.add_argument("--model", default=None, help="检查模型配置可构造客户端（信息性）")
    doctor_parser.add_argument("--workspace", default=None, help="检查工作区目录可写（信息性）")
    doctor_parser.add_argument(
        "--plugin-dir", action="append", default=[], metavar="DIR",
        help="检查插件目录存在（信息性，可重复）",
    )
    doctor_parser.add_argument(
        "--mcp", action="append", default=[], metavar="NAME=COMMAND",
        help="检查 MCP 服务器参数可解析（信息性，可重复）",
    )
    doctor_parser.add_argument(
        "--skip-docker-check", action="store_true",
        help="跳过 Docker 硬依赖检查（沙箱功能将不可用）",
    )
    doctor_parser.set_defaults(func=_cmd_doctor)

    tools_parser = subparsers.add_parser("tools", help="工具管理")
    tools_sub = tools_parser.add_subparsers(dest="tools_command", required=True)
    tools_list = tools_sub.add_parser("list", help="列出内置工具与权限分层")
    tools_list.set_defaults(func=_cmd_tools_list)

    mcp_parser = subparsers.add_parser("mcp", help="MCP 服务器管理")
    mcp_sub = mcp_parser.add_subparsers(dest="mcp_command", required=True)
    mcp_list = mcp_sub.add_parser("list", help="连接 MCP stdio 服务器并列出其工具")
    mcp_list.add_argument(
        "--server",
        required=True,
        help="name=command 格式的 MCP stdio 服务器（如 fs='npx -y @modelcontextprotocol/server-filesystem C:\\tmp'）",
    )
    mcp_list.set_defaults(func=_cmd_mcp_list)

    plugins_parser = subparsers.add_parser("plugins", help="插件管理（双规范清单 + marketplace + hooks）")
    plugins_sub = plugins_parser.add_subparsers(dest="plugins_command", required=True)
    plugins_list = plugins_sub.add_parser("list", help="列出发现的插件、技能与 hooks")
    plugins_list.add_argument(
        "--plugin-dir", action="append", default=[], metavar="DIR",
        help="插件搜索目录（可重复；缺省包含 ~/.codex/plugins/cache 与 AGENT_CLUSTER_PLUGIN_DIRS）",
    )
    plugins_list.set_defaults(func=_cmd_plugins_list)

    skills_parser = subparsers.add_parser("skills", help="技能管理")
    skills_sub = skills_parser.add_subparsers(dest="skills_command", required=True)
    skills_list = skills_sub.add_parser("list", help="列出技能目录")
    skills_list.add_argument("--root", required=True, help="技能根目录")
    skills_list.set_defaults(func=_cmd_skills_list)

    roles_parser = subparsers.add_parser("roles", help="岗位管理")
    roles_sub = roles_parser.add_subparsers(dest="roles_command", required=True)
    roles_list = roles_sub.add_parser("list", help="列出 12 岗位")
    roles_list.set_defaults(func=_cmd_roles_list)

    proposals_parser = subparsers.add_parser("proposals", help="进化提案（六步闭环演示）")
    proposals_sub = proposals_parser.add_subparsers(dest="proposals_command", required=True)
    proposals_demo = proposals_sub.add_parser("demo", help="进化闭环演示（收集→提炼→提案→评审→生效→回滚）")
    proposals_demo.set_defaults(func=_cmd_proposals_demo)
    proposals_submit = proposals_sub.add_parser("submit", help="提交进化提案并自动评审（演示）")
    proposals_submit.add_argument("--title", required=True, help="提案标题")
    proposals_submit.add_argument("--rollback-plan", required=True, help="回滚方案（必填，不可为空）")
    proposals_submit.add_argument("--author-role", default="pm", help="提案人岗位 id（缺省 pm）")
    proposals_submit.add_argument(
        "--category",
        default="skill",
        choices=["skill", "knowledge", "process", "organization"],
        help="进化对象类别（缺省 skill）",
    )
    proposals_submit.set_defaults(func=_cmd_proposals_submit)

    metrics_parser = subparsers.add_parser("metrics", help="绩效度量")
    metrics_sub = metrics_parser.add_subparsers(dest="metrics_command", required=True)
    metrics_demo = metrics_sub.add_parser("demo", help="度量采集与信号触发演示")
    metrics_demo.set_defaults(func=_cmd_metrics_demo)

    eval_parser = subparsers.add_parser(
        "eval", help="确定性回归集 + 基线对比防退化（T12.6 质量门禁）"
    )
    eval_parser.add_argument("--workspace", default=None, help="临时工作区根目录（缺省系统临时目录）")
    eval_parser.add_argument("--scenario", default=None, help="只运行指定场景（mini-pm-gate / dev-qa-gate / full-build）")
    eval_parser.add_argument("--baseline", default="eval-baseline.json", help="基线文件（缺省 eval-baseline.json）")
    eval_parser.add_argument("--save-baseline", action="store_true", help="保存本次结果为基线（不做对比）")
    eval_parser.add_argument("--threshold", type=float, default=0.05, help="回归判定阈值（缺省 0.05 = 相对下降 5%）")
    eval_parser.set_defaults(func=_cmd_eval)

    return parser


def _configure_utf8_stdio() -> None:
    """把 stdout/stderr 重配置为 UTF-8，保证管道/重定向输出编码稳定（仓库约定 UTF-8）。"""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8")
        except (ValueError, OSError):
            pass


def main(argv: Sequence[str] | None = None) -> int:
    """CLI 入口：解析参数并分发子命令，返回 int 退出码。"""
    _configure_utf8_stdio()
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())