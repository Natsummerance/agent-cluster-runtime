"""T11.9 v0.4 验收测试：空工作区全流程 / 既有 git 仓库功能开发修复。

- 场景 A：空工作区 + 全流程（build 确定性驱动）→ 真实文件落盘 + 测试真实通过 +
  git 提交 + 退出码 0（完整交付包含 DELIVERY.md 与 token 计量表）。
- 场景 B：既有 git 仓库（含一个失败测试）→ 工具模式修复 + QA 真实测试通过 +
  git 提交；无 Docker 时本机执行（缺省沙箱）并对 --sandbox docker 请求给出
  「本机执行」指引（退出非零码）。

全部使用确定性后端（DeterministicClient），无需任何 API key；工作区用 pytest
tmp_path 临时目录，不污染仓库；不依赖 Docker。
"""

from __future__ import annotations

import asyncio
import shutil
import subprocess
import sys
from pathlib import Path

import agent_cluster.sandbox as sb
from agent_cluster.cli import _build_sandbox, run_flow
from agent_cluster.session import SessionDriver

REPO_ROOT = Path(__file__).resolve().parents[1]
BUILD_FLOW = REPO_ROOT / "examples" / "flows" / "build-product.yaml"

# 场景 B 最小流程：既有仓库修复（backend 修代码+提交 -> QA 跑测试 -> end）
FIX_FLOW = """name: t11.9-fix-repo
thread_id: "t11.9:fix"
nodes:
  - {id: start, type: start}
  - {id: fix, type: agent, role: backend}
  - {id: test, type: agent, role: qa}
  - {id: end, type: end}
edges:
  - {from: start, to: fix}
  - {from: fix, to: test}
  - {from: test, to: end}
"""

# 场景 B：确定性工具脚本（修复 add 函数符号错误 + git 提交；QA 跑真实测试）
FIX_SCRIPTS = {
    "backend": [
        {"name": "edit_file", "args": {"path": "app.py", "edits": [
            {"old": "def add(a, b):\n    return a - b\n", "new": "def add(a, b):\n    return a + b\n"},
        ]}},
        {"name": "git_add", "args": {"paths": ["app.py"]}},
        {"name": "git_commit", "args": {"message": "fix: 修复 add 函数符号错误"}},
    ],
    "qa": [{"name": "run_tests", "args": {"command": "pytest -q"}}],
}

# 场景 A：build 全流程确定性脚本（覆盖 build-product.yaml 全部岗位）
BUILD_SCRIPTS = {
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
    ],
    "devops": [
        {"name": "write_file", "args": {"path": "Dockerfile", "content": "FROM python:3.12\n"}},
        {"name": "write_file", "args": {"path": "scripts/smoke.sh", "content": "#!/bin/sh\npython -c 'from app import add; assert add(1,2)==3'\n"}},
    ],
}


def _git(ws: Path, args: list[str]) -> str:
    """在工作区执行 git 子命令并断言成功，返回 stdout。"""
    proc = subprocess.run(
        ["git", *args], cwd=ws, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=60,
    )
    assert proc.returncode == 0, (proc.stdout or "") + (proc.stderr or "")
    return (proc.stdout or "").strip()


def _git_log(ws: Path) -> str:
    proc = subprocess.run(
        ["git", "log", "--oneline", "-1"], cwd=ws, capture_output=True,
        text=True, encoding="utf-8", errors="replace", timeout=60,
    )
    return (proc.stdout or "").strip()


def _run_pytest(ws: Path) -> int:
    """用当前解释器在临时工作区真实运行 pytest，返回退出码。"""
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q"], cwd=ws, capture_output=True,
        text=True, encoding="utf-8", errors="replace", timeout=180,
    )
    return proc.returncode




def _run_tests_outputs(summary) -> str:
    """提取 QA run_tests 工具结果（失败诊断用）。"""
    lines: list[str] = []
    for msg in summary.state.messages:
        payload = msg.payload or {}
        if msg.type.value == "tool_result" and payload.get("tool") == "run_tests":
            lines.append(f"ok={payload.get('ok')} output={str(payload.get('output', ''))[:800]}")
    return "\n".join(lines) or "(无 run_tests 工具结果)"


def _run_tests_outputs(summary) -> str:
    """提取 QA run_tests 工具结果（失败诊断用）。"""
    lines: list[str] = []
    for msg in summary.state.messages:
        payload = msg.payload or {}
        if msg.type.value == "tool_result" and payload.get("tool") == "run_tests":
            lines.append(f"ok={payload.get('ok')} output={str(payload.get('output', ''))[:800]}")
    return "\n".join(lines) or "(无 run_tests 工具结果)"

def test_acceptance_v04_scenario_a_empty_workspace_full_flow(tmp_path: Path):
    """场景 A：空工作区 + 全流程 → 可运行项目 + 测试真实通过 + git 提交 + 退出码 0。"""
    ws = tmp_path / "ws-a"

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
            role_tool_scripts=BUILD_SCRIPTS,
            print_fn=lambda s: None,
        )
        return await driver.run()

    result = asyncio.run(_run())

    # 退出码 0 + 完整交付
    assert result.exit_code == 0, result.exit_code
    assert result.delivery is not None
    # 真实文件落盘
    assert (ws / "app.py").is_file()
    assert (ws / "test_app.py").is_file()
    assert (ws / "docs" / "PRD.md").is_file()
    assert (ws / "docs" / "architecture.md").is_file()
    assert (ws / "Dockerfile").is_file()
    assert (ws / "README.md").is_file()
    # 测试真实通过：QA 任务全部 done，且独立重跑 pytest 退出码 0
    qa_tasks = [t for t in result.state.tasks if t.assignee_role == "qa"]
    assert qa_tasks and all(t.status.value == "done" for t in qa_tasks), (
        f"QA 任务未全部完成；run_tests 输出：\n{_run_tests_outputs(result)}"
    )
    assert _run_pytest(ws) == 0
    # git 提交存在
    assert _git_log(ws)
    # 交付包：DELIVERY.md + token 计量
    delivery = (ws / "DELIVERY.md").read_text(encoding="utf-8")
    assert "Token 计量表" in delivery
    assert "阶段消耗" in delivery
    assert result.token_summary["used"] > 0


def test_acceptance_v04_scenario_b_existing_repo_fix(tmp_path: Path, monkeypatch):
    """场景 B：既有 git 仓库（含失败测试）→ 修复后测试通过 + git 提交；无 Docker 时本机执行并提示。"""
    ws = tmp_path / "ws-b"
    ws.mkdir()
    _git(ws, ["init", "-q"])
    _git(ws, ["config", "user.email", "agent-cluster@local"])
    _git(ws, ["config", "user.name", "agent-cluster"])
    (ws / "app.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
    (ws / "test_app.py").write_text(
        "from app import add\n\ndef test_add():\n    assert add(1, 2) == 3\n",
        encoding="utf-8",
    )
    _git(ws, ["add", "-A"])
    _git(ws, ["commit", "-m", "baseline: 含失败测试"])

    # 基线确认：修复前测试真实失败
    assert _run_pytest(ws) != 0
    # 基线运行生成字节码缓存：同秒内同长度改写源文件时，Python 按整秒校验 pyc，
    # QA 子进程可能命中陈旧字节码（CI 曾偶发）。清掉缓存保证 QA 读到修复后源码。
    for cache_dir in (ws / "__pycache__", ws / ".pytest_cache"):
        shutil.rmtree(cache_dir, ignore_errors=True)

    flow = tmp_path / "fix.yaml"
    flow.write_text(FIX_FLOW, encoding="utf-8")
    summary = asyncio.run(
        run_flow(flow, workspace=str(ws), yes=True, role_tool_scripts=FIX_SCRIPTS, max_rounds=8)
    )

    # 流程正常结束；修复落盘；QA 真实测试通过
    assert summary.events[-1].type == "workflow_end"
    assert (ws / "app.py").read_text(encoding="utf-8") == "def add(a, b):\n    return a + b\n"
    qa_tasks = [t for t in summary.state.tasks if t.assignee_role == "qa"]
    assert qa_tasks and all(t.status.value == "done" for t in qa_tasks), (
        f"QA 任务未全部完成；run_tests 输出：\n{_run_tests_outputs(summary)}"
    )
    assert not [t for t in summary.state.tasks if t.status.value in ("review", "blocked")]
    assert _run_pytest(ws) == 0
    # git：基线提交 + 修复提交（≥2），最近提交为修复
    assert int(_git(ws, ["rev-list", "--count", "HEAD"])) >= 2
    assert "fix:" in _git_log(ws)

    # 无 Docker 时本机执行并提示：缺省沙箱本机执行（上文已跑通）；
    # 请求 --sandbox docker 且 Docker 不可用时给出「本机执行」指引，不执行流程。
    monkeypatch.setattr(sb, "docker_available", lambda: False)
    runner, hint = _build_sandbox("docker", ws)
    assert runner is None
    assert hint and "Docker" in hint and "本机执行" in hint
