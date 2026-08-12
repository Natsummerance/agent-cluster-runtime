"""T11.6 git worktree 隔离测试：仓库准备、按角色 worktree 隔离、合并回主工作区、
放弃清理、越界拒绝、CLI --worktrees 校验与端到端双角色流程。

依赖真实 git（本机已装）；测试内不依赖 Docker。git 身份用环境变量固定。
"""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

import pytest

from agent_cluster.cli import main, run_flow
from agent_cluster.tools import ToolCall, ToolSession, build_default_tools
from agent_cluster.worktree import WorktreeManager


@pytest.fixture(autouse=True)
def _git_identity(monkeypatch):
    """固定 git 提交身份（避免依赖全局配置）。"""
    monkeypatch.setenv("GIT_AUTHOR_NAME", "tester")
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", "tester@local")
    monkeypatch.setenv("GIT_COMMITTER_NAME", "tester")
    monkeypatch.setenv("GIT_COMMITTER_EMAIL", "tester@local")


def _git(workspace: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(workspace), *args],
        capture_output=True, text=True, encoding="utf-8",
    )
    return (proc.stdout or "") + (proc.stderr or "")


def test_ensure_repo_inits_and_has_head(tmp_path: Path):
    """非 git 目录：ensure_repo 自动 init + 初始提交。"""
    ws = tmp_path / "ws"
    manager = WorktreeManager(ws)
    result = manager.ensure_repo()
    assert result["ok"] is True
    assert (ws / ".git").exists()
    assert _git(ws, "rev-parse", "--verify", "HEAD").strip() != ""


def test_session_for_creates_isolated_worktree(tmp_path: Path):
    """session_for 创建独立 worktree；写入文件不进主工作区。"""
    ws = tmp_path / "ws"
    manager = WorktreeManager(ws)
    assert manager.ensure_repo()["ok"] is True
    session = manager.session_for("frontend")
    assert session.workspace_root.is_relative_to(ws)
    assert session.workspace_root != ws
    assert "worktrees" in session.workspace_root.as_posix()

    # worktree 内写文件 + git 提交
    result = asyncio.run(
        session.execute(ToolCall(id="1", name="write_file", args={"path": "fe.txt", "content": "fe"}))
    )
    assert result.ok is True
    result = asyncio.run(session.execute(ToolCall(id="2", name="git_add", args={"paths": ["."]})))
    assert result.ok is True
    result = asyncio.run(session.execute(ToolCall(id="3", name="git_commit", args={"message": "fe"})))
    assert result.ok is True

    assert (session.workspace_root / "fe.txt").exists()
    assert not (ws / "fe.txt").exists()  # 隔离：主工作区无文件
    assert _git(ws, "log", "--oneline", "-1").strip() != "fe"  # 主分支无 fe 提交
    assert manager.active == ["frontend"]


def test_merge_back_merges_into_main(tmp_path: Path):
    """merge_back：--no-ff 合并回主工作区并移除 worktree。"""
    ws = tmp_path / "ws"
    manager = WorktreeManager(ws)
    manager.ensure_repo()
    session = manager.session_for("backend")
    asyncio.run(session.execute(ToolCall(id="1", name="write_file", args={"path": "be.txt", "content": "be"})))
    asyncio.run(session.execute(ToolCall(id="2", name="git_add", args={"paths": ["."]})))
    asyncio.run(session.execute(ToolCall(id="3", name="git_commit", args={"message": "be"})))
    before = _git(ws, "rev-parse", "HEAD").strip()

    result = manager.merge_back("backend")
    assert result["ok"] is True
    assert (ws / "be.txt").exists()  # 文件回到主工作区
    assert _git(ws, "rev-parse", "HEAD").strip() != before  # 有合并提交
    assert "merge" in _git(ws, "log", "--oneline", "--merges", "-1").lower()
    assert manager.active == []
    assert manager.merged == ["acs/backend"]
    # worktree 目录已移除
    assert not (ws / ".agent-cluster" / "worktrees" / "backend").exists()


def test_merge_back_no_changes(tmp_path: Path):
    """merge_back 无改动：不产生多余提交，仍成功。"""
    ws = tmp_path / "ws"
    manager = WorktreeManager(ws)
    manager.ensure_repo()
    manager.session_for("qa")
    result = manager.merge_back("qa")
    assert result["ok"] is True
    assert manager.active == []


def test_close_discards_worktree(tmp_path: Path):
    """close：不合并直接放弃（改动丢弃）。"""
    ws = tmp_path / "ws"
    manager = WorktreeManager(ws)
    manager.ensure_repo()
    session = manager.session_for("devops")
    asyncio.run(session.execute(ToolCall(id="1", name="write_file", args={"path": "x.txt", "content": "x"})))
    result = manager.close("devops")
    assert result["ok"] is True
    assert not (ws / ".agent-cluster" / "worktrees" / "devops").exists()
    assert not (ws / "x.txt").exists()
    assert manager.active == []


def test_worktree_session_rejects_escape(tmp_path: Path):
    """worktree 会话越界拒绝：../ 逃逸回主工作区被拦截。"""
    ws = tmp_path / "ws"
    manager = WorktreeManager(ws)
    manager.ensure_repo()
    session = manager.session_for("frontend")
    result = asyncio.run(
        session.execute(ToolCall(id="1", name="write_file", args={"path": "../../../escape.txt", "content": "x"}))
    )
    assert result.ok is False
    assert not (ws / "escape.txt").exists()
    assert "工作区" in result.output or "越界" in result.output or "拒绝" in result.output


def test_cli_worktrees_requires_workspace(tmp_path: Path):
    """CLI：--worktrees 无 --workspace -> 退出码 1。"""
    flow = tmp_path / "f.yaml"
    flow.write_text(
        "name: t11.6\n"
        "thread_id: 't:wt'\n"
        "nodes:\n"
        "  - {id: start, type: start}\n"
        "  - {id: end, type: end}\n"
        "edges:\n"
        "  - {from: start, to: end}\n",
        encoding="utf-8",
    )
    code = main(["run", "--flow", str(flow), "--worktrees"])
    assert code == 1


def test_run_flow_worktrees_two_roles(tmp_path: Path):
    """端到端：frontend/backend 两角色在独立 worktree 提交，全部合并回主工作区。"""
    flow = tmp_path / "f.yaml"
    flow.write_text(
        "name: t11.6-e2e\n"
        "thread_id: 't:wte2e'\n"
        "nodes:\n"
        "  - {id: start, type: start}\n"
        "  - {id: fe, type: agent, role: frontend}\n"
        "  - {id: be, type: agent, role: backend}\n"
        "  - {id: end, type: end}\n"
        "edges:\n"
        "  - {from: start, to: fe}\n"
        "  - {from: fe, to: be}\n"
        "  - {from: be, to: end}\n",
        encoding="utf-8",
    )
    ws = tmp_path / "ws"
    role_tool_scripts = {
        "frontend": [
            {"name": "write_file", "args": {"path": "fe.txt", "content": "fe"}},
            {"name": "git_add", "args": {"paths": ["."]}},
            {"name": "git_commit", "args": {"message": "fe"}},
        ],
        "backend": [
            {"name": "write_file", "args": {"path": "be.txt", "content": "be"}},
            {"name": "git_add", "args": {"paths": ["."]}},
            {"name": "git_commit", "args": {"message": "be"}},
        ],
    }
    summary = asyncio.run(
        run_flow(
            flow,
            workspace=str(ws),
            role_tool_scripts=role_tool_scripts,
            worktrees=True,
        )
    )
    assert summary is not None
    assert (ws / "fe.txt").read_text(encoding="utf-8") == "fe"
    assert (ws / "be.txt").read_text(encoding="utf-8") == "be"
    # 主分支有合并记录
    assert "merge" in _git(ws, "log", "--oneline", "--merges", "-2").lower()
    # worktree 目录清理完毕
    wt_dir = ws / ".agent-cluster" / "worktrees"
    if wt_dir.exists():
        assert list(wt_dir.iterdir()) == []
