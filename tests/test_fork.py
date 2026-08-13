"""T13.4 fork-session：仅终态可派生 / checkpoint 复制续跑 / 血缘≤5 / 账本不双计 /
transcript 与变更历史 / worktree 继承 / goal+budget 覆盖 / 预算耗尽冲突。"""

from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path

import pytest

from agent_cluster.changes import ChangeHistory
from agent_cluster.projects import (
    BudgetPoolExhaustedError,
    ForkConflictError,
    ProjectStore,
    fork_session,
)
from agent_cluster.session import SessionDriver, SessionRecord, SessionStore

REPO_ROOT = Path(__file__).resolve().parents[1]
BUILD_FLOW = REPO_ROOT / "examples" / "flows" / "build-product.yaml"

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


@pytest.fixture(autouse=True)
def _git_identity(monkeypatch):
    """固定 git 提交身份（worktree add/commit 不依赖全局配置）。"""
    monkeypatch.setenv("GIT_AUTHOR_NAME", "tester")
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", "tester@local")
    monkeypatch.setenv("GIT_COMMITTER_NAME", "tester")
    monkeypatch.setenv("GIT_COMMITTER_EMAIL", "tester@local")


@pytest.fixture(scope="module")
def completed_ws(tmp_path_factory):
    """模块级：跑一次 deterministic 全流程至 completed（含 1 条变更历史与 git 提交）。"""
    ws = Path(tmp_path_factory.mktemp("fork-ws")) / "ws"

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
    assert result.exit_code == 0
    ChangeHistory(ws).record(text="注入需求变更")
    return ws


def _migrated_store(tmp_path: Path, completed_ws: Path) -> tuple[ProjectStore, str, str]:
    """每个测试独立 ProjectStore：create_project 自动迁移共享 completed 工作区。"""
    store = ProjectStore(tmp_path / "root")
    project = store.create_project(name="proj", workspace=completed_ws)
    pid = project.project_id
    entries = store.get(pid).sessions
    assert entries and entries[0].status == "completed"
    return store, pid, entries[0].session_id


def _read_session(store: ProjectStore, pid: str, sid: str) -> SessionRecord:
    path = store.session_dir(pid, sid) / "session.json"
    return SessionRecord.model_validate(json.loads(path.read_text(encoding="utf-8")))


# ---------------------------------------------------------------------------
# 前置校验：终态可派生 / active 冲突 / 血缘上限 / 预算耗尽
# ---------------------------------------------------------------------------


def test_fork_completed_and_aborted(tmp_path, completed_ws):
    store, pid, src_sid = _migrated_store(tmp_path, completed_ws)
    events: list[str] = []
    record = fork_session(store, source_session_id=src_sid, emit=lambda name, payload: events.append(name))
    assert record.parent_session_id == src_sid
    assert record.fork_depth == 1
    assert record.status == "active"
    assert record.project_id == pid
    assert events == ["session.forked", "session.start"]

    aborted = store.session_store(pid, "s-aborted")
    aborted.record = aborted.record.model_copy(
        update={"project_id": pid, "goal": "g", "status": "aborted", "workspace": str(completed_ws)}
    )
    aborted.save()
    record2 = fork_session(store, source_session_id="s-aborted", worktree=False)
    assert record2.parent_session_id == "s-aborted"
    assert record2.fork_depth == 1


def test_fork_active_conflict_and_missing_source(tmp_path, completed_ws):
    store, pid, src_sid = _migrated_store(tmp_path, completed_ws)
    active = store.session_store(pid, "s-active")
    active.record = active.record.model_copy(
        update={"project_id": pid, "goal": "g", "status": "active", "workspace": str(completed_ws)}
    )
    active.save()
    with pytest.raises(ForkConflictError):
        fork_session(store, source_session_id="s-active")
    with pytest.raises(ForkConflictError):
        fork_session(store, source_session_id="no-such-session")
    with pytest.raises(ForkConflictError):
        fork_session(store, source_session_id=src_sid, project_id="no-such-project")


def test_fork_depth_cap(tmp_path, completed_ws):
    store, pid, src_sid = _migrated_store(tmp_path, completed_ws)
    current = src_sid
    for depth in range(1, 6):
        record = fork_session(store, source_session_id=current, worktree=False)
        assert record.fork_depth == depth
        store.session_store(pid, record.session_id).update(status="completed")
        current = record.session_id
    with pytest.raises(ForkConflictError):
        fork_session(store, source_session_id=current, worktree=False)


def test_budget_exhausted_conflict(tmp_path, completed_ws):
    store, pid, src_sid = _migrated_store(tmp_path, completed_ws)
    store.update(pid, budget_pool={"hard_limit_tokens": 1})
    with pytest.raises(BudgetPoolExhaustedError):
        fork_session(store, source_session_id=src_sid)


# ---------------------------------------------------------------------------
# 派生产物
# ---------------------------------------------------------------------------


def test_checkpoint_copy_resume(tmp_path, completed_ws):
    store, pid, src_sid = _migrated_store(tmp_path, completed_ws)
    record = fork_session(store, source_session_id=src_sid)
    src_record = _read_session(store, pid, src_sid)
    assert record.thread_id != src_record.thread_id

    src_ckpt = store.session_dir(pid, src_sid) / "checkpoints" / f"{src_record.thread_id}.json"
    new_ckpt = store.session_dir(pid, record.session_id) / "checkpoints" / f"{record.thread_id}.json"
    assert src_ckpt.is_file()
    assert new_ckpt.is_file()
    assert new_ckpt.read_bytes() == src_ckpt.read_bytes()

    # resume=True 驱动可续跑：工作区会话记录就位后按新 thread 恢复检查点
    worktree_ws = Path(record.workspace)
    seeded = SessionStore(worktree_ws)
    seeded.record = record
    seeded.save()
    driver = SessionDriver(
        workspace=worktree_ws,
        goal=record.goal,
        flow=record.flow,
        resume=True,
        checkpoint_root=store.session_dir(pid, record.session_id) / "checkpoints",
    )
    assert driver.store.record.thread_id == record.thread_id
    tup = driver.checkpointer.get_tuple({"configurable": {"thread_id": record.thread_id}})
    assert tup is not None
    assert tup.checkpoint["id"] == src_ckpt_tail_checkpoint_id(src_ckpt)


def src_ckpt_tail_checkpoint_id(path: Path) -> str:
    data = json.loads(path.read_text(encoding="utf-8"))
    return data["checkpoints"][-1]["checkpoint_id"]


def test_ledger_not_double_counted(tmp_path, completed_ws):
    store, pid, src_sid = _migrated_store(tmp_path, completed_ws)
    src_total = SessionStore(completed_ws).record.token_ledger.total()
    assert src_total > 0
    record = fork_session(store, source_session_id=src_sid)
    assert record.token_ledger.total() == 0
    assert record.inherited_tokens == src_total
    assert store.aggregate_used_tokens(pid) == src_total


def test_transcript_and_changes(tmp_path, completed_ws):
    store, pid, src_sid = _migrated_store(tmp_path, completed_ws)
    record = fork_session(store, source_session_id=src_sid)
    assert len(record.transcript) == 1
    qa = record.transcript[0]
    assert qa.question == "fork"
    assert qa.answer == src_sid
    assert qa.source == "fork"

    expected = ChangeHistory(completed_ws).list()
    assert [item.version for item in record.inherited_changes] == [item.version for item in expected]
    # 新会话变更历史从 v1 重新计（fork 标记即 v1）
    assert ChangeHistory(Path(record.workspace)).latest_version() == 1


def test_worktree_inheritance(tmp_path, completed_ws):
    store, pid, src_sid = _migrated_store(tmp_path, completed_ws)
    record = fork_session(store, source_session_id=src_sid)
    wt = completed_ws / ".agent-cluster" / "worktrees" / "fork" / record.session_id
    assert wt.is_dir()
    proc = subprocess.run(
        ["git", "-C", str(completed_ws), "worktree", "list"],
        capture_output=True, text=True, encoding="utf-8",
    )
    assert wt.as_posix() in proc.stdout.replace("\\", "/")
    assert (wt / "docs" / "PRD.md").is_file()

    record2 = fork_session(store, source_session_id=src_sid, worktree=False)
    assert Path(record2.workspace) == completed_ws.resolve()
    assert not (completed_ws / ".agent-cluster" / "worktrees" / "fork" / record2.session_id).exists()


def test_goal_and_budget_override(tmp_path, completed_ws):
    store, pid, src_sid = _migrated_store(tmp_path, completed_ws)
    record = fork_session(
        store, source_session_id=src_sid, goal="新目标", budget=777, worktree=False
    )
    assert record.goal == "新目标"
    assert record.budget == 777
    assert record.token_ledger.budget == 777
