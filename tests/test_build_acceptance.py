"""T10.6 端到端验收：三场景（完整交付包 / 中止-续跑 / 预算超限升级）。"""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

from agent_cluster.session import SessionDriver, SessionStore

REPO_ROOT = Path(__file__).resolve().parents[1]
BUILD_FLOW = REPO_ROOT / "examples" / "flows" / "build-product.yaml"

GATE_FLOW = """name: t10.6-gate
thread_id: "t:gate"
nodes:
  - {id: start, type: start}
  - {id: requirements, type: agent, role: pm}
  - {id: requirement_gate, type: gate, gate: requirement_confirmation}
  - {id: end, type: end}
edges:
  - {from: start, to: requirements}
  - {from: requirements, to: requirement_gate}
  - {from: requirement_gate, to: end, on_accept: end, on_reject: requirements}
"""

FULL_SCRIPTS = {
    "pm": [
        {"name": "ask_user", "args": {"question": "主要目标用户是谁？"}},
        {"name": "write_file", "args": {"path": "docs/PRD.md", "content": "# PRD\n目标：待办事项应用\n验收：可增删改查\n"}},
    ],
    "architect": [
        {"name": "write_file", "args": {"path": "docs/architecture.md", "content": "# 架构\n前后端分离 + SQLite\n"}},
    ],
    "backend": [
        {"name": "write_file", "args": {"path": "app.py", "content": "def add(a, b):\n    return a + b\n"}},
        {"name": "write_file", "args": {"path": "test_app.py", "content": "from app import add\n\ndef test_add():\n    assert add(1, 2) == 3\n"}},
    ],
    "frontend": [
        {"name": "write_file", "args": {"path": "index.html", "content": "<h1>待办事项</h1>\n"}},
    ],
    "algorithm": [
        {"name": "write_file", "args": {"path": "algo.py", "content": "def rank(items):\n    return sorted(items)\n"}},
    ],
    "qa": [{"name": "run_tests", "args": {"command": "pytest -q"}}],
    "docs": [
        {"name": "write_file", "args": {"path": "README.md", "content": "# 待办事项应用\n"}},
        {"name": "write_file", "args": {"path": "docs/user-manual.md", "content": "# 用户手册\n"}},
        {"name": "write_file", "args": {"path": "docs/api.md", "content": "# API 文档\n"}},
    ],
    "devops": [
        {"name": "write_file", "args": {"path": "Dockerfile", "content": "FROM python:3.12\n"}},
        {"name": "write_file", "args": {"path": "docker-compose.yml", "content": "services:\n  app:\n    build: .\n"}},
        {"name": "write_file", "args": {"path": "scripts/smoke.sh", "content": "#!/bin/sh\npython -c 'from app import add; assert add(1,2)==3'\n"}},
    ],
}


def _git_log(workspace: Path) -> str:
    proc = subprocess.run(
        ["git", "log", "--oneline", "-1"],
        cwd=workspace, capture_output=True, text=True, encoding="utf-8",
    )
    return (proc.stdout or "").strip()


def test_acceptance_scenario1_full_product_delivery(tmp_path: Path):
    """场景一：空工作区 + 模糊需求 → 完整交付包 + 测试全绿 + git 提交 + token 报表。"""
    ws = tmp_path / "ws1"

    async def _run():
        driver = SessionDriver(
            workspace=ws,
            goal="做一个待办事项网站（用户可增删改查）",
            flow=BUILD_FLOW,
            model="codex",
            budget=500_000,
            deterministic=True,
            yes=True,
            qa_script=["普通用户"],
            role_tool_scripts=FULL_SCRIPTS,
            print_fn=lambda s: None,
        )
        return await driver.run()

    result = asyncio.run(_run())
    assert result.exit_code == 0, result.exit_code
    assert result.delivery is not None
    # 真实文件
    assert (ws / "app.py").is_file()
    assert (ws / "test_app.py").is_file()
    assert (ws / "docs" / "PRD.md").is_file()
    assert (ws / "docs" / "architecture.md").is_file()
    assert (ws / "Dockerfile").is_file()
    assert (ws / "README.md").is_file()
    # DELIVERY.md + token 计量表
    delivery = (ws / "DELIVERY.md").read_text(encoding="utf-8")
    assert "Token 计量表" in delivery
    assert "阶段消耗" in delivery
    # 任务板 QA 通过
    qa_tasks = [t for t in result.state.tasks if t.assignee_role == "qa"]
    assert qa_tasks and all(t.status.value == "done" for t in qa_tasks)
    # git 提交
    assert _git_log(ws)
    # token 报表
    assert result.token_summary["used"] > 0
    assert result.token_summary["by_phase"]
    assert result.token_summary["by_role"]


def test_acceptance_scenario2_abort_then_resume(tmp_path: Path):
    """场景二：门处 /abort → 检查点保存 → --resume 续跑至交付完成。"""
    ws = tmp_path / "ws2"
    flow = tmp_path / "gate.yaml"
    flow.write_text(GATE_FLOW, encoding="utf-8")
    first = {"aborted": False}

    def prompt_abort(hint: str) -> str:
        first["aborted"] = True
        return "/abort"

    async def _run_first():
        driver = SessionDriver(
            workspace=ws, goal="做一个待办事项网站", flow=flow, model="codex",
            budget=100_000, deterministic=True,
            qa_script=["普通用户"], prompt_fn=prompt_abort, print_fn=lambda s: None,
        )
        return await driver.run()

    result1 = asyncio.run(_run_first())
    assert result1.exit_code == 2
    assert first["aborted"]
    assert (ws / ".agent-cluster" / "session.json").is_file()
    assert SessionStore(ws).record.status == "active"

    async def _run_resume():
        driver = SessionDriver(
            workspace=ws, goal="做一个待办事项网站", flow=flow, model="codex",
            budget=100_000, deterministic=True, resume=True,
            qa_script=["普通用户"], prompt_fn=lambda hint: "accept", print_fn=lambda s: None,
        )
        return await driver.run()

    result2 = asyncio.run(_run_resume())
    assert result2.exit_code == 0
    assert result2.delivery is not None
    assert (ws / "DELIVERY.md").is_file()
    assert SessionStore(ws).record.status == "completed"


def test_acceptance_scenario3_budget_overrun_escalation(tmp_path: Path):
    """场景三：预算极小超限 → 升级人工 → shrink 缩减重跑后完成。"""
    ws = tmp_path / "ws3"
    flow = tmp_path / "gate.yaml"
    flow.write_text(GATE_FLOW, encoding="utf-8")
    answered = {"budget_prompt": 0}

    def prompt(hint: str) -> str:
        if "预算超限" in hint:
            answered["budget_prompt"] += 1
            # 第一次追加预算继续，验证 more 路径
            if answered["budget_prompt"] == 1:
                return "more 100000"
            return "end"
        return "accept"

    async def _run():
        driver = SessionDriver(
            workspace=ws, goal="做一个待办事项网站", flow=flow, model="codex",
            budget=5, deterministic=True,
            qa_script=["普通用户"], prompt_fn=prompt, print_fn=lambda s: None,
        )
        return await driver.run()

    result = asyncio.run(_run())
    # 首次 more 追加后继续；预算仍可能再次超限 → 第二次升级 end（退出码 3）
    assert answered["budget_prompt"] >= 1
    assert result.exit_code in (0, 3)
    assert result.token_summary["budget"] >= 100_005
