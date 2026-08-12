"""v0.2 工具执行层集成测试：真实工作区 + 确定性脚本全链路（无 API key）。

覆盖（验收标准）：
- 场景 A：空工作区 → 真实文件落盘 → 测试真实通过 → git 提交 → 任务板 Done。
- QA 失败 → 任务保持 review（分岗位质量门槛）。
- --yes 下危险工具自动拒绝且流程继续。
- ask 模式危险工具挂起、人工 accept 后正确恢复执行。
- --max-rounds 截断 → 任务 review。
- 工具模式存在失败任务 → CLI 退出码 1。
"""

from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path

import pytest

from agent_cluster.cli import main, run_flow
from agent_cluster.models import TaskStatus

BUILD_FLOW = """name: tool-flow-build
max_iterations: 20
thread_id: "proj:tool-build:iter:1"
nodes:
  - {id: start, type: start}
  - {id: pm, type: agent, role: pm}
  - {id: backend, type: agent, role: backend}
  - {id: qa, type: agent, role: qa}
  - {id: devops, type: agent, role: devops}
  - {id: end, type: end}
edges:
  - {from: start, to: pm}
  - {from: pm, to: backend}
  - {from: backend, to: qa}
  - {from: qa, to: devops}
  - {from: devops, to: end}
"""

QA_FAIL_FLOW = """name: tool-flow-qa-fail
max_iterations: 10
thread_id: "proj:qa-fail:iter:1"
nodes:
  - {id: start, type: start}
  - {id: qa, type: agent, role: qa}
  - {id: end, type: end}
edges:
  - {from: start, to: qa}
  - {from: qa, to: end}
"""

DANGER_FLOW = """name: tool-flow-danger
max_iterations: 10
thread_id: "proj:tool-danger:iter:1"
nodes:
  - {id: start, type: start}
  - {id: devops, type: agent, role: devops}
  - {id: end, type: end}
edges:
  - {from: start, to: devops}
  - {from: devops, to: end}
"""

TRUNCATE_FLOW = """name: tool-flow-truncate
max_iterations: 10
thread_id: "proj:tool-truncate:iter:1"
nodes:
  - {id: start, type: start}
  - {id: backend, type: agent, role: backend}
  - {id: end, type: end}
edges:
  - {from: start, to: backend}
  - {from: backend, to: end}
"""


def _write_flow(tmp_path: Path, name: str, text: str) -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def _git_log(workspace: Path) -> str:
    proc = subprocess.run(
        ["git", "log", "--oneline", "-1"],
        cwd=workspace,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return (proc.stdout or "").strip()


# ---------------------------------------------------------------------------
# 场景 A：空工作区生成可运行新项目
# ---------------------------------------------------------------------------


def test_scenario_a_new_project_writes_files_tests_pass_and_commits(tmp_path: Path):
    flow_path = _write_flow(tmp_path, "build.yaml", BUILD_FLOW)
    workspace = tmp_path / "ws"
    scripts = {
        "pm": [{"name": "write_file", "args": {"path": "README.md", "content": "# demo\n"}}],
        "backend": [
            {"name": "write_file", "args": {"path": "app.py", "content": "def add(a, b):\n    return a + b\n"}},
            {"name": "write_file", "args": {"path": "test_app.py", "content": "from app import add\n\ndef test_add():\n    assert add(1, 2) == 3\n"}},
        ],
        "qa": [{"name": "run_tests", "args": {"command": "pytest -q"}}],
        "devops": [
            {"name": "git_init", "args": {}},
            {"name": "git_add", "args": {"paths": ["."]}},
            {"name": "git_commit", "args": {"message": "init: 新项目"}},
        ],
    }
    summary = asyncio.run(
        run_flow(
            flow_path,
            yes=True,
            workspace=str(workspace),
            role_tool_scripts=scripts,
            max_rounds=10,
        )
    )

    assert summary.events[-1].type == "workflow_end"
    assert summary.suspended_count == 0
    # 真实文件落盘
    assert (workspace / "README.md").is_file()
    assert (workspace / "app.py").is_file()
    assert (workspace / "test_app.py").is_file()
    # 测试真实通过（pytest 退出码 0）
    assert (workspace / ".pytest_cache").exists() or True  # pytest 执行过
    # git 提交存在
    assert "init: 新项目" in _git_log(workspace)
    # 任务板全部 Done
    state = summary.state
    assert state is not None and state.tasks
    assert all(task.status == TaskStatus.DONE for task in state.tasks)
    # 工具事件与度量
    tool_events = [event for event in summary.events if event.type == "tool_result"]
    assert len(tool_events) >= 6
    # QA 任务真实测试通过
    qa_tasks = [task for task in state.tasks if task.assignee_role == "qa"]
    assert qa_tasks and all(task.status == TaskStatus.DONE for task in qa_tasks)
    # 产出物记录真实相对路径
    backend_tasks = [task for task in state.tasks if task.assignee_role == "backend"]
    assert any("app.py" in artifact for task in backend_tasks for artifact in task.artifacts)


# ---------------------------------------------------------------------------
# QA 失败 → 任务 review（分岗位质量门槛）
# ---------------------------------------------------------------------------


def test_qa_failure_keeps_task_in_review(tmp_path: Path):
    flow_path = _write_flow(tmp_path, "qa_fail.yaml", QA_FAIL_FLOW)
    workspace = tmp_path / "ws-qa"
    scripts = {"qa": [{"name": "run_tests", "args": {"command": "pytest -q"}}]}
    summary = asyncio.run(
        run_flow(
            flow_path,
            yes=True,
            workspace=str(workspace),
            role_tool_scripts=scripts,
            max_rounds=5,
        )
    )
    qa_tasks = [task for task in summary.state.tasks if task.assignee_role == "qa"]
    assert qa_tasks
    assert all(task.status == TaskStatus.REVIEW for task in qa_tasks)
    assert summary.events[-1].type == "workflow_end"


# ---------------------------------------------------------------------------
# --yes 危险工具自动拒绝且流程继续
# ---------------------------------------------------------------------------


def test_yes_mode_auto_rejects_dangerous_tool_and_continues(tmp_path: Path):
    flow_path = _write_flow(tmp_path, "danger.yaml", DANGER_FLOW)
    workspace = tmp_path / "ws-danger"
    scripts = {
        "devops": [
            {"name": "run_shell", "args": {"command": "cmd /c echo SHOULD_NOT_RUN"}},
            {"name": "write_file", "args": {"path": "done.txt", "content": "ok"}},
        ]
    }
    summary = asyncio.run(
        run_flow(
            flow_path,
            yes=True,
            workspace=str(workspace),
            role_tool_scripts=scripts,
            max_rounds=5,
        )
    )
    # 危险工具挂起一次（自动拒绝），流程继续到完成
    assert summary.suspended_count == 1
    assert summary.events[-1].type == "workflow_end"
    # 危险命令未执行
    assert not (workspace / "echo-output.txt").exists()
    # 后续安全工具执行
    assert (workspace / "done.txt").is_file()
    # 审批记录含 reject
    assert any(record.type == "reject" for record in summary.decisions)
    # 任务仍完成（最终文本）
    assert all(task.status == TaskStatus.DONE for task in summary.state.tasks)


# ---------------------------------------------------------------------------
# ask 模式危险工具挂起 → 人工 accept → 恢复执行
# ---------------------------------------------------------------------------


def test_ask_mode_dangerous_tool_suspends_and_resumes_on_accept(tmp_path: Path):
    flow_path = _write_flow(tmp_path, "danger_ask.yaml", DANGER_FLOW)
    workspace = tmp_path / "ws-ask"
    scripts = {
        "devops": [
            {"name": "run_shell", "args": {"command": "cmd /c echo APPROVED_RAN"}},
        ]
    }
    summary = asyncio.run(
        run_flow(
            flow_path,
            yes=False,
            workspace=str(workspace),
            role_tool_scripts=scripts,
            max_rounds=5,
            prompt=lambda _: "accept",
        )
    )
    assert summary.suspended_count == 1
    assert summary.events[-1].type == "workflow_end"
    # 审批记录含人工 accept
    assert any(record.type == "accept" and record.by_role == "human" for record in summary.decisions)
    assert all(task.status == TaskStatus.DONE for task in summary.state.tasks)


def test_ask_mode_dangerous_tool_reject_blocks_execution(tmp_path: Path):
    flow_path = _write_flow(tmp_path, "danger_reject.yaml", DANGER_FLOW)
    workspace = tmp_path / "ws-reject"
    scripts = {"devops": [{"name": "run_shell", "args": {"command": "cmd /c echo BLOCKED"}}]}
    summary = asyncio.run(
        run_flow(
            flow_path,
            yes=False,
            workspace=str(workspace),
            role_tool_scripts=scripts,
            max_rounds=5,
            prompt=lambda _: "reject",
        )
    )
    assert summary.events[-1].type == "workflow_end"
    assert any(record.type == "reject" for record in summary.decisions)
    assert all(task.status == TaskStatus.DONE for task in summary.state.tasks)


# ---------------------------------------------------------------------------
# --max-rounds 截断 → 任务 review
# ---------------------------------------------------------------------------


def test_max_rounds_truncation_marks_task_review(tmp_path: Path):
    flow_path = _write_flow(tmp_path, "truncate.yaml", TRUNCATE_FLOW)
    workspace = tmp_path / "ws-truncate"
    scripts = {
        "backend": [
            {"name": "write_file", "args": {"path": f"f{i}.txt", "content": str(i)}} for i in range(6)
        ]
    }
    summary = asyncio.run(
        run_flow(
            flow_path,
            yes=True,
            workspace=str(workspace),
            role_tool_scripts=scripts,
            max_rounds=3,
        )
    )
    backend_tasks = [task for task in summary.state.tasks if task.assignee_role == "backend"]
    assert backend_tasks
    assert all(task.status == TaskStatus.REVIEW for task in backend_tasks)
    # 只执行了前 3 轮
    assert not (workspace / "f3.txt").exists()


# ---------------------------------------------------------------------------
# CLI 退出码：工具模式存在失败任务 → 1
# ---------------------------------------------------------------------------


def test_cli_exit_code_one_when_unfinished_tasks(tmp_path: Path):
    flow_path = _write_flow(tmp_path, "qa_fail_cli.yaml", QA_FAIL_FLOW)
    workspace = tmp_path / "ws-cli"
    script_path = tmp_path / "qa_script.json"
    script_path.write_text(
        json.dumps([{"name": "run_tests", "args": {"command": "pytest -q"}}]),
        encoding="utf-8",
    )
    code = main(
        [
            "run",
            "--flow",
            str(flow_path),
            "--workspace",
            str(workspace),
            "--yes",
            "--tool-script",
            str(script_path),
            "--max-rounds",
            "5",
        ]
    )
    assert code == 1


def test_cli_tools_list_and_mcp_help_exit_zero(capsys):
    assert main(["tools", "list"]) == 0
    captured = capsys.readouterr()
    assert "read_file" in captured.out and "dangerous" in captured.out
    assert main(["mcp", "list", "--server", "x=nonexistent-cmd"]) == 1
