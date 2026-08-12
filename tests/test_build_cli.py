"""T10.4 build CLI：确定性全流程交付 / 预算超限退出码 / QA 失败退出码 / resume 保护。"""

from __future__ import annotations

import subprocess
from pathlib import Path

from agent_cluster.cli import main

BUILD_FLOW = """name: t10.4-build
thread_id: "t:build"
nodes:
  - {id: start, type: start}
  - {id: kickoff, type: meeting, meeting: kickoff, participants: [pm, pmo]}
  - {id: requirements, type: agent, role: pm}
  - {id: requirement_gate, type: gate, gate: requirement_confirmation}
  - {id: end, type: end}
edges:
  - {from: start, to: kickoff}
  - {from: kickoff, to: requirements}
  - {from: requirements, to: requirement_gate}
  - {from: requirement_gate, to: end, on_accept: end, on_reject: requirements}
"""

QA_FAIL_FLOW = """name: t10.4-qa-fail
thread_id: "t:qafail"
nodes:
  - {id: start, type: start}
  - {id: test, type: agent, role: qa}
  - {id: end, type: end}
edges:
  - {from: start, to: test}
  - {from: test, to: end}
"""


def _write_flow(tmp_path: Path, name: str, text: str) -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def _git_log(workspace: Path) -> str:
    proc = subprocess.run(
        ["git", "log", "--oneline", "-1"],
        cwd=workspace, capture_output=True, text=True, encoding="utf-8",
    )
    return (proc.stdout or "").strip()


def test_build_yes_deterministic_full_delivery(tmp_path: Path):
    """build --deterministic --yes：交付包 + git 提交 + 退出码 0。"""
    flow = _write_flow(tmp_path, "build.yaml", BUILD_FLOW)
    ws = tmp_path / "ws"
    code = main(
        [
            "build",
            "--goal", "做一个待办事项网站",
            "--workspace", str(ws),
            "--flow", str(flow),
            "--model", "codex",
            "--deterministic",
            "--yes",
        ]
    )
    assert code == 0
    assert (ws / "DELIVERY.md").is_file()
    delivery = (ws / "DELIVERY.md").read_text(encoding="utf-8")
    assert "Token 计量表" in delivery
    assert "阶段消耗" in delivery
    assert "角色消耗" in delivery
    assert _git_log(ws) != ""


def test_build_budget_overrun_yes_ends_with_exit_3(tmp_path: Path):
    """--budget 极小 + --yes：预算超限升级自动结束（退出码 3）。"""
    flow = _write_flow(tmp_path, "build.yaml", BUILD_FLOW)
    ws = tmp_path / "ws-budget"
    code = main(
        [
            "build",
            "--goal", "做一个待办事项网站",
            "--workspace", str(ws),
            "--flow", str(flow),
            "--deterministic",
            "--yes",
            "--budget", "5",
        ]
    )
    assert code == 3
    assert (ws / ".agent-cluster" / "session.json").is_file()


def test_build_qa_failure_returns_exit_1(tmp_path: Path):
    """QA run_tests 失败（空目录 pytest 退出 5）→ 任务 review → 退出码 1。"""
    flow = _write_flow(tmp_path, "qafail.yaml", QA_FAIL_FLOW)
    ws = tmp_path / "ws-qa"
    script = tmp_path / "role-script.json"
    script.write_text(
        '{"qa": [{"name": "run_tests", "args": {"command": "pytest -q"}}]}',
        encoding="utf-8",
    )
    code = main(
        [
            "build",
            "--goal", "做一个待办事项网站",
            "--workspace", str(ws),
            "--flow", str(flow),
            "--deterministic",
            "--yes",
            "--role-tool-script", str(script),
        ]
    )
    assert code == 1


def test_build_resume_after_completed_rejected(tmp_path: Path):
    """会话已完成后再 --resume → CLI 报错返回 1。"""
    flow = _write_flow(tmp_path, "build.yaml", BUILD_FLOW)
    ws = tmp_path / "ws-resume"
    code1 = main(
        [
            "build",
            "--goal", "做一个待办事项网站",
            "--workspace", str(ws),
            "--flow", str(flow),
            "--deterministic",
            "--yes",
        ]
    )
    assert code1 == 0
    code2 = main(
        [
            "build",
            "--workspace", str(ws),
            "--flow", str(flow),
            "--deterministic",
            "--yes",
            "--resume",
        ]
    )
    assert code2 == 1


def test_build_missing_goal_without_resume_returns_2(tmp_path: Path):
    """无 --goal 且非 --resume → 退出码 2。"""
    flow = _write_flow(tmp_path, "build.yaml", BUILD_FLOW)
    ws = tmp_path / "ws-goal"
    code = main(
        [
            "build",
            "--workspace", str(ws),
            "--flow", str(flow),
            "--deterministic",
            "--yes",
        ]
    )
    assert code == 2
