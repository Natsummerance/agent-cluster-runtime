"""T13.5 SessionManager：worktree 默认隔离 / 显式冲突 / merge_back 与冲突保留 /
abort 丢弃 / cancel 唤醒 / 预算闸 / hook 注入 / shutdown 无孤儿。"""

from __future__ import annotations

import subprocess
import threading
import time
from pathlib import Path

import pytest

from agent_cluster.models import TokenUsage
from agent_cluster.projects import BudgetPoolExhaustedError, ProjectStore
from agent_cluster.session_manager import SessionManager, WorktreeConflictError

MINI_FLOW = """name: t13.5-mini
thread_id: "t:13.5"
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


@pytest.fixture(autouse=True)
def _git_identity(monkeypatch):
    monkeypatch.setenv("GIT_AUTHOR_NAME", "tester")
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", "tester@local")
    monkeypatch.setenv("GIT_COMMITTER_NAME", "tester")
    monkeypatch.setenv("GIT_COMMITTER_EMAIL", "tester@local")


def _make_manager(tmp_path: Path) -> tuple[SessionManager, ProjectStore, str, Path]:
    ws = tmp_path / "ws"
    ws.mkdir()
    store = ProjectStore(tmp_path / "root")
    project = store.create_project(name="p", workspace=ws)
    return SessionManager(store), store, project.project_id, ws


def _write_flow(tmp_path: Path) -> Path:
    flow = tmp_path / "flow.yaml"
    flow.write_text(MINI_FLOW, encoding="utf-8")
    return flow


def _waiting_spec(flow: Path) -> dict:
    return {"goal": "待办应用", "flow": str(flow), "model": "deterministic"}


def _finishing_spec(flow: Path) -> dict:
    return {
        "goal": "待办应用",
        "flow": str(flow),
        "model": "deterministic",
        "deterministic": True,
        "yes": True,
    }


def _wait_for(session, statuses, timeout=30.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if session.status in statuses:
            return
        time.sleep(0.05)
    raise AssertionError(f"会话未在 {timeout}s 内进入 {statuses}（当前 {session.status}）")


def _git(workspace: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(workspace), *args],
        capture_output=True, text=True, encoding="utf-8",
    )
    return (proc.stdout or "") + (proc.stderr or "")


# ---------------------------------------------------------------------------
# 并发判定与 worktree 分配（§8.2）
# ---------------------------------------------------------------------------


def test_second_session_auto_worktree(tmp_path):
    manager, store, pid, ws = _make_manager(tmp_path)
    flow = _write_flow(tmp_path)
    s1 = manager.start(pid, _waiting_spec(flow))
    _wait_for(s1, ("waiting_approval", "failed"))
    assert s1.status == "waiting_approval"

    s2 = manager.start(pid, _waiting_spec(flow))
    assert s2.worktree_path is not None
    expected = ws / ".agent-cluster" / "worktrees" / "sessions" / s2.session_id
    assert Path(s2.workspace) == expected
    assert expected.is_dir()
    assert manager.running_in(pid) >= 1
    manager.shutdown()


def test_explicit_no_worktree_conflict(tmp_path):
    manager, store, pid, ws = _make_manager(tmp_path)
    flow = _write_flow(tmp_path)
    s1 = manager.start(pid, _waiting_spec(flow))
    _wait_for(s1, ("waiting_approval", "failed"))

    spec = _waiting_spec(flow)
    spec["worktree"] = False
    with pytest.raises(WorktreeConflictError):
        manager.start(pid, spec)
    manager.shutdown()


def test_explicit_worktree_alone(tmp_path):
    manager, store, pid, ws = _make_manager(tmp_path)
    flow = _write_flow(tmp_path)
    spec = _waiting_spec(flow)
    spec["worktree"] = True
    s = manager.start(pid, spec)
    assert s.worktree_path is not None
    assert s.worktree_path.is_dir()
    manager.shutdown()


# ---------------------------------------------------------------------------
# 收尾：merge_back / 冲突保留 / abort 丢弃
# ---------------------------------------------------------------------------


def test_merge_back_success(tmp_path):
    manager, store, pid, ws = _make_manager(tmp_path)
    flow = _write_flow(tmp_path)
    spec = _finishing_spec(flow)
    spec["worktree"] = True
    s = manager.start(pid, spec)
    (s.worktree_path / "docs").mkdir(exist_ok=True)
    (s.worktree_path / "docs" / "PRD.md").write_text("# PRD\n", encoding="utf-8")
    _wait_for(s, ("completed", "failed"))
    assert s.thread is not None
    s.thread.join(timeout=30)
    assert s.status == "completed"
    assert s.exit_code == 0
    assert not s.worktree_path.exists()
    assert (ws / "docs" / "PRD.md").is_file()


def test_merge_conflict_keeps_worktree(tmp_path):
    manager, store, pid, ws = _make_manager(tmp_path)
    flow = _write_flow(tmp_path)
    spec = _waiting_spec(flow)
    spec["worktree"] = True
    s = manager.start(pid, spec)
    _wait_for(s, ("waiting_approval", "failed"))
    assert s.status == "waiting_approval"

    (ws / "docs").mkdir(exist_ok=True)
    (ws / "docs" / "PRD.md").write_text("main version\n", encoding="utf-8")
    _git(ws, "add", "-A")
    _git(ws, "commit", "-m", "main change")

    (s.worktree_path / "docs").mkdir(exist_ok=True)
    (s.worktree_path / "docs" / "PRD.md").write_text("worktree version\n", encoding="utf-8")
    _git(s.worktree_path, "add", "-A")
    _git(s.worktree_path, "commit", "-m", "session change")

    s.submit_answer("accept")
    _wait_for(s, ("completed", "failed"))
    assert s.thread is not None
    s.thread.join(timeout=30)
    assert s.status == "completed"
    assert s.merge_conflict is True
    assert s.worktree_path.is_dir()
    events = [event for event in s.log.replay() if event["type"] == "worktree.merge_conflict"]
    assert events
    # 人工清理现场（冲突 worktree 保留）
    _git(ws, "worktree", "remove", str(s.worktree_path), "--force")
    _git(ws, "branch", "-D", f"acs/session/{s.session_id}")


def test_abort_discards_worktree(tmp_path):
    manager, store, pid, ws = _make_manager(tmp_path)
    flow = _write_flow(tmp_path)
    spec = _waiting_spec(flow)
    spec["worktree"] = True
    s = manager.start(pid, spec)
    _wait_for(s, ("waiting_approval", "failed"))
    assert manager.cancel(s.session_id) is True
    _wait_for(s, ("completed", "failed"))
    assert s.thread is not None
    s.thread.join(timeout=30)
    assert s.exit_code != 0
    assert not s.worktree_path.exists()
    events = [event for event in s.log.replay() if event["type"] == "session.cancel"]
    assert events
    # checkpoint 保留
    checkpoints = store.session_dir(pid, s.session_id) / "checkpoints"
    assert any(checkpoints.glob("*.json"))
    assert manager.cancel("no-such-session") is False


def test_cancel_wakes_pending_prompt(tmp_path):
    manager, store, pid, ws = _make_manager(tmp_path)
    flow = _write_flow(tmp_path)
    s = manager.start(pid, _waiting_spec(flow))
    _wait_for(s, ("waiting_approval", "failed"))
    assert manager.cancel(s.session_id) is True
    deadline = time.time() + 15
    while time.time() < deadline and (s.thread is not None and s.thread.is_alive()):
        time.sleep(0.05)
    assert s.thread is not None and not s.thread.is_alive()


# ---------------------------------------------------------------------------
# 预算闸与 hook 注入（§5.2 / §5.4）
# ---------------------------------------------------------------------------


def test_budget_exhausted_blocks_start(tmp_path):
    manager, store, pid, ws = _make_manager(tmp_path)
    flow = _write_flow(tmp_path)
    fake = store.session_store(pid, "s-fake")
    fake.update(project_id=pid)
    fake.record.token_ledger.record(usage=TokenUsage(total_tokens=100))
    fake.save()
    store.update(pid, budget_pool={"hard_limit_tokens": 50})
    with pytest.raises(BudgetPoolExhaustedError):
        manager.start(pid, _waiting_spec(flow))
    store.unlock_budget(pid, additional_tokens=500, reason="解锁")
    s = manager.start(pid, _waiting_spec(flow))
    assert s.session_id
    manager.shutdown()


def test_hook_injected(tmp_path):
    manager, store, pid, ws = _make_manager(tmp_path)
    flow = _write_flow(tmp_path)
    store.update(pid, budget_pool={"hard_limit_tokens": 1000})
    s = manager.start(pid, _waiting_spec(flow))
    _wait_for(s, ("waiting_approval", "failed"))
    assert s.driver is not None
    assert s.driver.budget_pool_hook is not None
    s.driver.usage_hook("pm", TokenUsage(total_tokens=900))
    events = [event for event in s.log.replay() if event["type"] == "budget.warning"]
    assert events
    assert store.get(pid).budget_pool.warn_raised is True
    manager.shutdown()


# ---------------------------------------------------------------------------
# shutdown 无孤儿
# ---------------------------------------------------------------------------


def test_shutdown_no_orphans(tmp_path):
    manager, store, pid, ws = _make_manager(tmp_path)
    flow = _write_flow(tmp_path)
    s1 = manager.start(pid, _waiting_spec(flow))
    s2 = manager.start(pid, _waiting_spec(flow))
    _wait_for(s1, ("waiting_approval", "failed"))
    _wait_for(s2, ("waiting_approval", "failed"))
    assert manager.running_in(pid) == 2

    assert manager.shutdown() == 0
    assert manager.shutdown() == 0  # 幂等
    for session in (s1, s2):
        assert session.thread is None or not session.thread.is_alive()
    non_daemon = [
        thread.name
        for thread in threading.enumerate()
        if thread.name.startswith("session-") and not thread.daemon
    ]
    assert non_daemon == []
