"""CLI 入口（Task 7）：agent-cluster 命令（多 agent 组织型全栈开发集群运行时）。

子命令：
- ``run``：编译并运行 YAML 流程；遇审批门打印 ActionRequest 并交互读取
  ``accept/reject/response <内容>/edit <内容>`` 恢复运行；``--yes`` 无人值守
  模式自动接受（bypass-immune 高风险门自动转为拒绝），结束后打印运行摘要。
- ``skills list``：列出技能目录（name/version/description）。
- ``roles list``：列出 12 岗位（id/name/kind/approval_scope）。
- ``proposals demo``：进化闭环演示（collect→distill→propose→review→apply→rollback）。
- ``metrics demo``：度量采集与信号触发演示。

``main()`` 返回 int 退出码；``python -m agent_cluster`` 等价于 agent-cluster。
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TextIO

import yaml
from langgraph.checkpoint.memory import MemorySaver

from agent_cluster.evolution import EvolutionEngine
from agent_cluster.gates import approval_pending, make_gate_handler, resolve_auto_response
from agent_cluster.meetings import MeetingHost, make_meeting_handler
from agent_cluster.metrics import MetricRules, MetricsCollector
from agent_cluster.models import (
    ActionRequest,
    ApprovalRecord,
    ClusterState,
    Event,
    HumanResponse,
    Iteration,
    Project,
)
from agent_cluster.roles import RoleRegistry, build_role_catalog
from agent_cluster.runtime import AgentRuntime, make_agent_handler
from agent_cluster.skills import SkillLoader
from agent_cluster.workflow import WorkflowEngine

__all__ = ["main", "run_flow", "RunSummary"]

# 审批交互提示文案
PROMPT_HINT = "请选择审批结论 [accept|reject|response <内容>|edit <内容>]："


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
    print_event: Callable[[Event], None] | None = None,
    print_request: Callable[[ActionRequest], None] | None = None,
    prompt: Callable[[str], str] | None = None,
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
    runtime = AgentRuntime()
    host = MeetingHost()
    engine = WorkflowEngine(
        handlers={
            "agent": make_agent_handler(runtime, role_registry),
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

    checkpointer = MemorySaver()
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
    final_state = ClusterState.model_validate(snapshot.values)
    return RunSummary(
        thread_id=resolved_thread,
        events=events,
        state=final_state,
        decisions=list(final_state.decisions),
        suspended_count=suspended_count,
    )


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


# ---------------------------------------------------------------------------
# 子命令实现
# ---------------------------------------------------------------------------


def _cmd_run(args: argparse.Namespace) -> int:
    """run 子命令：编译并运行流程。"""
    out = sys.stdout
    try:
        summary = asyncio.run(
            run_flow(
                args.flow,
                project=args.project,
                yes=args.yes,
                thread_id=args.thread,
                print_event=lambda event: _print_event(event, out),
                print_request=lambda request: _print_request(request, out),
            )
        )
    except Exception as exc:  # noqa: BLE001 —— CLI 顶层统一错误出口
        print(f"运行失败：{exc}", file=sys.stderr)
        return 1
    _print_summary(summary, out)
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
    run_parser.set_defaults(func=_cmd_run)

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

    metrics_parser = subparsers.add_parser("metrics", help="绩效度量")
    metrics_sub = metrics_parser.add_subparsers(dest="metrics_command", required=True)
    metrics_demo = metrics_sub.add_parser("demo", help="度量采集与信号触发演示")
    metrics_demo.set_defaults(func=_cmd_metrics_demo)

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