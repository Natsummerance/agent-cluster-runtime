"""Task 7 集成测试：CLI 闭环（--yes 全流程）、交互审批、演示子命令与子进程冒烟。

- 直接调用 ``cli.run_flow``（公开异步函数）跑 ``examples/flows/fullstack-sprint.yaml``，
  断言事件流含全部会议/门/开发节点、终态任务可达、审批记录 ≥ 4（每门一条）、
  流程以 ``workflow_end`` 结束且 ``--yes`` 永不挂起（无 interrupt）。
- 直接调用 ``cli.main`` 验证 skills list / roles list / proposals demo / metrics demo
  退出码为 0。
- 子进程冒烟：``python -m agent_cluster --help`` 退出码 0。
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
from pathlib import Path

from agent_cluster.cli import main, run_flow
from agent_cluster.models import GateKind, MeetingKind, TaskStatus

REPO_ROOT = Path(__file__).resolve().parents[1]
FLOW_PATH = REPO_ROOT / "examples" / "flows" / "fullstack-sprint.yaml"
SKILLS_ROOT = REPO_ROOT / "examples" / "skills"


def _node_starts(summary) -> list[str]:
    """按执行顺序返回 node_start 事件的 actor 列表。"""
    return [event.actor for event in summary.events if event.type == "node_start"]


def test_cli_run_yes_full_flow_completes_without_hanging():
    """--yes 全流程：事件齐全、无挂起、审批 4 条、终态任务可达。"""
    summary = asyncio.run(run_flow(FLOW_PATH, project=str(REPO_ROOT), yes=True))

    # 结束与无 interrupt
    assert summary.events[-1].type == "workflow_end"
    assert summary.suspended_count == 0
    assert "workflow_suspended" not in [event.type for event in summary.events]

    # 全部节点执行（含 parallel 与并行子节点）
    expected_nodes = {
        "start",
        "requirement_review",
        "requirement_gate",
        "design",
        "design_review",
        "design_gate",
        "develop_parallel",
        "develop_frontend",
        "develop_backend",
        "code_review",
        "test",
        "iteration_gate",
        "release",
        "release_gate",
        "end",
    }
    assert expected_nodes <= set(_node_starts(summary))

    # 会议：需求评审 / 设计评审 / 代码评审
    meetings_held = {event.actor for event in summary.events if event.type == "meeting_held"}
    assert meetings_held == {"requirement_review", "design_review", "code_review"}

    # agent 节点：design(frontend 之前)/frontend/backend/test/release
    agent_actors = {event.actor for event in summary.events if event.type == "agent_step"}
    assert agent_actors == {"architect", "frontend", "backend", "qa", "devops"}

    # 终态
    state = summary.state
    assert state is not None
    assert len(state.meetings) == 3
    assert {meeting.kind for meeting in state.meetings} == {
        MeetingKind.REQUIREMENT_REVIEW,
        MeetingKind.DESIGN_REVIEW,
        MeetingKind.CODE_REVIEW,
    }

    # 任务全部可达（状态为合法 TaskStatus）
    assert state.tasks, "终态应包含任务"
    assert all(task.status in set(TaskStatus) for task in state.tasks)
    assert any(task.status == TaskStatus.DOING for task in state.tasks)  # agent 节点认领任务
    assert any(task.status == TaskStatus.TODO for task in state.tasks)  # 会议行动项

    # 审批记录：每门一条，共 4 条（decisions 通道为审计全量）
    assert len(summary.decisions) >= 4
    assert {record.type for record in summary.decisions} == {"accept"}
    # gate_payloads 为「当前待审批」索引（替换语义），末门 release 应保留
    assert GateKind.RELEASE in state.gate_payloads


def test_cli_run_ask_mode_prompts_and_resumes():
    """交互模式：4 次挂起、人工 accept 恢复、最终 workflow_end。"""
    prompts = iter(["accept"] * 10)
    summary = asyncio.run(run_flow(FLOW_PATH, yes=False, prompt=lambda _: next(prompts)))

    assert summary.suspended_count == 4
    assert summary.events[-1].type == "workflow_end"
    assert len(summary.decisions) == 4
    assert all(record.by_role == "human" for record in summary.decisions)
    assert [record.type for record in summary.decisions] == ["accept"] * 4


def test_cli_skills_list_exit_zero():
    assert main(["skills", "list", "--root", str(SKILLS_ROOT)]) == 0


def test_cli_roles_list_exit_zero():
    assert main(["roles", "list"]) == 0


def test_cli_proposals_demo_exit_zero():
    assert main(["proposals", "demo"]) == 0


def test_cli_metrics_demo_exit_zero():
    assert main(["metrics", "demo"]) == 0


def test_cli_help_via_python_module_subprocess():
    """子进程冒烟：python -m agent_cluster --help 退出码 0。"""
    result = subprocess.run(
        [sys.executable, "-m", "agent_cluster", "--help"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
        cwd=str(REPO_ROOT),
    )
    assert result.returncode == 0
    combined = (result.stdout + result.stderr).lower()
    assert "usage:" in combined
    assert "run" in combined and "skills" in combined and "roles" in combined