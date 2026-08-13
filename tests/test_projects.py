"""T13.1 项目层：数据模型 / ProjectStore CRUD 与布局 / 并发与原子写 /
SessionRecord v0.5 兼容 / SessionStore 项目路径解析 / 记忆库项目目录与 WAL。"""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path

import pytest
from pydantic import ValidationError

from agent_cluster.projects import (
    DEFAULT_FLOW,
    BudgetPoolRecord,
    BudgetUnlockRecord,
    GatePolicyConfig,
    ProjectRecord,
    ProjectStore,
    SessionIndexEntry,
)
from agent_cluster.session import (
    DEFAULT_REWORK_LIMIT,
    SessionRecord,
    SessionStore,
    TokenLedgerEntry,
)


# ---------------------------------------------------------------------------
# 模型默认值与校验（§3.1）
# ---------------------------------------------------------------------------


def test_model_defaults_and_validation():
    pool = BudgetPoolRecord()
    assert pool.hard_limit_tokens == 0
    assert pool.warn_ratio == 0.8
    assert pool.warn_reenable_ratio == 0.7
    assert pool.unlock_requires_approval is False
    assert pool.unlocks == []
    assert pool.warn_raised is False
    assert pool.last_warned_at is None

    policy = GatePolicyConfig()
    assert policy.auto_review is True
    assert policy.auto_kinds == ["design_review", "code_review", "iteration_acceptance"]
    assert policy.human_kinds == [
        "requirement_confirmation",
        "release",
        "dangerous_tool",
        "evolution_apply",
    ]
    assert policy.review_confidence_threshold == 0.7
    assert policy.rework_escalation == DEFAULT_REWORK_LIMIT
    assert policy.review_prompt == ""

    entry = SessionIndexEntry(session_id="s1")
    assert entry.goal == ""
    assert entry.status == "active"
    assert entry.assignee == ""
    assert entry.workspace == ""
    assert entry.worktree is False
    assert entry.metadata == {}

    unlock = BudgetUnlockRecord(additional_tokens=100)
    assert unlock.session_id == ""
    assert unlock.reason == ""
    assert unlock.status == "granted"
    assert unlock.decided_by == "self"
    assert unlock.decided_at is None

    project = ProjectRecord(project_id="p1", name="项目", workspaces=["/tmp/ws"])
    assert project.description == ""
    assert project.default_flow == DEFAULT_FLOW
    assert project.status == "active"
    assert project.sessions == []
    assert project.metadata == {}

    with pytest.raises(ValidationError):
        ProjectRecord(project_id="p1", name="", workspaces=["/tmp/ws"])
    with pytest.raises(ValidationError):
        BudgetPoolRecord(warn_ratio=1.5)
    with pytest.raises(ValidationError):
        BudgetUnlockRecord(additional_tokens=0)

    parsed = ProjectRecord.model_validate(
        {"project_id": "p1", "name": "x", "workspaces": ["/a"], "unknown_field": 123}
    )
    assert "unknown_field" not in parsed.model_dump()


# ---------------------------------------------------------------------------
# SessionRecord 对 v0.5 session.json 的向后兼容（§3.2 红线：只增不改）
# ---------------------------------------------------------------------------


def test_session_record_v05_json_compat():
    v05 = {
        "session_id": "s-v05",
        "thread_id": "t-v05",
        "goal": "构建一个 CLI",
        "flow": "examples/flows/build-product.yaml",
        "model": "codex",
        "workspace": "/tmp/ws",
        "created_at": "2026-08-01T00:00:00+00:00",
        "updated_at": "2026-08-01T00:00:00+00:00",
        "status": "completed",
        "transcript": [
            {"id": "q1", "question": "范围？", "answer": "A", "source": "human", "node": "req"}
        ],
        "gate_decisions": [
            {
                "node": "gate1",
                "kind": "design_review",
                "attempts": 1,
                "rejections": 0,
                "last_decision": "accept",
            }
        ],
        "phases": {"requirements": {"name": "requirements", "status": "done", "tokens_used": 10}},
        "token_ledger": {
            "budget": 100000,
            "entries": [
                {
                    "role": "pm",
                    "phase": "requirements",
                    "model": "codex",
                    "prompt_tokens": 20,
                    "completion_tokens": 30,
                    "total_tokens": 50,
                }
            ],
        },
        "budget": 100000,
        "rework_limit": 3,
    }
    record = SessionRecord.model_validate(v05)
    assert record.session_id == "s-v05"
    assert record.status == "completed"
    assert record.token_ledger.total() == 50
    assert record.phases["requirements"].status == "done"
    # 6 个增量字段取默认值
    assert record.project_id == ""
    assert record.parent_session_id == ""
    assert record.fork_depth == 0
    assert record.inherited_tokens == 0
    assert record.inherited_changes == []
    assert record.metadata == {}


# ---------------------------------------------------------------------------
# ProjectStore CRUD 与目录布局（§3.4）
# ---------------------------------------------------------------------------


def test_project_crud_and_layout(tmp_path):
    ws = tmp_path / "workspace"
    ws.mkdir()
    store = ProjectStore(tmp_path)
    project = store.create_project(
        name="demo", workspace=ws, description="desc", metadata={"team": "A"}
    )
    pid = project.project_id
    assert len(pid) == 12
    assert project.status == "active"
    assert project.default_flow == DEFAULT_FLOW

    path = tmp_path / "projects" / pid / "project.json"
    assert path.is_file()
    loaded = store.get(pid)
    assert loaded == project
    assert loaded.workspaces == [str(ws.resolve())]
    assert loaded.metadata == {"team": "A"}
    assert [item.project_id for item in store.list()] == [pid]

    updated = store.update(pid, name="demo2", description="d2")
    assert updated.name == "demo2"
    assert updated.description == "d2"
    assert store.get(pid).project_id == pid
    assert store.get(pid).updated_at >= project.updated_at

    with pytest.raises(KeyError):
        store.get("missing-pid")

    archived = store.archive(pid)
    assert archived.status == "archived"
    assert store.get(pid).status == "archived"

    with pytest.raises(ValueError):
        store.update(pid, name="")


# ---------------------------------------------------------------------------
# 原子写 + 并发写锁
# ---------------------------------------------------------------------------


def test_atomic_write_and_concurrent_update(tmp_path):
    ws = tmp_path / "workspace"
    ws.mkdir()
    store = ProjectStore(tmp_path)
    project = store.create_project(name="race", workspace=ws)
    pid = project.project_id
    path = tmp_path / "projects" / pid / "project.json"
    errors: list[BaseException] = []

    def writer(label: str) -> None:
        try:
            for index in range(25):
                store.update(pid, description=f"{label}-{index}")
        except BaseException as exc:  # noqa: BLE001 - 收集线程异常用于断言
            errors.append(exc)

    threads = [threading.Thread(target=writer, args=(label,)) for label in ("a", "b")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    record = ProjectRecord.model_validate(json.loads(path.read_text(encoding="utf-8")))
    assert record.description.startswith(("a-", "b-"))
    assert not (path.parent / "project.json.tmp").exists()


# ---------------------------------------------------------------------------
# add_workspace：存在性校验 + 去重
# ---------------------------------------------------------------------------


def test_add_workspace_validation(tmp_path):
    ws = tmp_path / "workspace"
    ws.mkdir()
    store = ProjectStore(tmp_path)
    project = store.create_project(name="ws", workspace=ws)
    pid = project.project_id

    with pytest.raises(ValueError):
        store.add_workspace(pid, tmp_path / "missing-dir")

    unchanged = store.add_workspace(pid, ws)
    assert len(unchanged.workspaces) == 1

    ws2 = tmp_path / "workspace2"
    ws2.mkdir()
    updated = store.add_workspace(pid, ws2)
    assert updated.workspaces == [str(ws.resolve()), str(ws2.resolve())]
    assert updated.workspaces[0] == str(ws.resolve())


# ---------------------------------------------------------------------------
# SessionStore 项目路径解析 + v0.5 缺省路径不回归
# ---------------------------------------------------------------------------


def test_session_store_project_resolution(tmp_path):
    ws = tmp_path / "workspace"
    ws.mkdir()
    store = ProjectStore(tmp_path)
    project = store.create_project(name="sess", workspace=ws)
    pid, sid = project.project_id, "sid-1"

    session_store = store.session_store(pid, sid)
    expected_dir = tmp_path / "projects" / pid / "sessions" / sid
    assert store.session_dir(pid, sid) == expected_dir
    assert session_store.path == expected_dir / "session.json"
    assert (expected_dir / "checkpoints").is_dir()
    assert (expected_dir / ".gitignore").exists()

    session_store.update(goal="项目会话目标")
    assert session_store.path.is_file()
    assert not (ws / ".agent-cluster" / "session.json").exists()

    reopened = SessionStore(ws, session_id=sid, project_id=pid, root=tmp_path)
    assert reopened.path == session_store.path
    assert reopened.record.goal == "项目会话目标"

    legacy = SessionStore(ws)
    assert legacy.path == ws / ".agent-cluster" / "session.json"
    assert legacy.record.goal == ""


# ---------------------------------------------------------------------------
# 会话注册表 upsert + token 聚合（权威归因读 session.json）
# ---------------------------------------------------------------------------


def test_index_and_aggregate(tmp_path):
    ws = tmp_path / "workspace"
    ws.mkdir()
    store = ProjectStore(tmp_path)
    project = store.create_project(name="agg", workspace=ws)
    pid = project.project_id

    def seed_session(sid: str, tokens: int) -> SessionStore:
        session_store = store.session_store(pid, sid)
        session_store.record.token_ledger.entries.append(
            TokenLedgerEntry(total_tokens=tokens, role="pm", phase="develop")
        )
        session_store.save()
        return session_store

    seed_session("s1", 120)
    seed_session("s2", 80)

    store.index_session(pid, SessionIndexEntry(session_id="s1", goal="g1"))
    assert len(store.get(pid).sessions) == 1

    store.index_session(pid, SessionIndexEntry(session_id="s1", goal="g1-updated", assignee="alice"))
    sessions = store.get(pid).sessions
    assert len(sessions) == 1
    assert sessions[0].goal == "g1-updated"
    assert sessions[0].assignee == "alice"

    store.index_session(pid, SessionIndexEntry(session_id="s2", goal="g2"))
    assert {entry.session_id for entry in store.get(pid).sessions} == {"s1", "s2"}

    assert store.aggregate_used_tokens(pid) == 200
    assert store.aggregate_used_tokens("missing-pid") == 0


# ---------------------------------------------------------------------------
# 预算解锁：granted 提额 + append-only 审计 + decide_unlock 状态机
# ---------------------------------------------------------------------------


def test_unlock_and_decide_mechanics(tmp_path):
    ws = tmp_path / "workspace"
    ws.mkdir()
    store = ProjectStore(tmp_path)
    project = store.create_project(name="budget", workspace=ws)
    pid = project.project_id
    store.update(pid, budget_pool={"hard_limit_tokens": 1000})

    unlock = store.unlock_budget(pid, additional_tokens=500, reason="需要更多预算", session_id="s1")
    assert unlock.status == "granted"
    assert unlock.decided_by == "self"
    assert unlock.decided_at is None
    pool = store.get(pid).budget_pool
    assert pool.hard_limit_tokens == 1500
    assert [item.id for item in pool.unlocks] == [unlock.id]

    with pytest.raises(ValueError):
        store.unlock_budget(pid, additional_tokens=0, reason="x")

    with pytest.raises(ValueError):
        store.decide_unlock(pid, unlock.id, approved=True, decided_by="reviewer")

    with pytest.raises(ValueError):
        store.decide_unlock(pid, "nope", approved=True, decided_by="reviewer")

    pending = BudgetUnlockRecord(
        session_id="s2", additional_tokens=300, reason="例外", status="pending"
    )
    store.update(pid, budget_pool={"unlocks": pool.unlocks + [pending]})
    denied = store.decide_unlock(pid, pending.id, approved=False, decided_by="reviewer")
    assert denied.status == "denied"
    assert denied.decided_by == "reviewer"
    assert denied.decided_at is not None
    assert store.get(pid).budget_pool.hard_limit_tokens == 1500

    pending2 = BudgetUnlockRecord(
        session_id="s3", additional_tokens=200, reason="例外2", status="pending"
    )
    store.update(pid, budget_pool={"unlocks": store.get(pid).budget_pool.unlocks + [pending2]})
    approved = store.decide_unlock(pid, pending2.id, approved=True, decided_by="reviewer")
    assert approved.status == "granted"
    assert store.get(pid).budget_pool.hard_limit_tokens == 1700
    assert store.get(pid).budget_pool.unlocks[-1].decided_by == "reviewer"


# ---------------------------------------------------------------------------
# 项目级记忆库：目录 + WAL + 并发读写
# ---------------------------------------------------------------------------


def test_memory_store_project_dir_and_wal(tmp_path):
    ws = tmp_path / "workspace"
    ws.mkdir()
    store = ProjectStore(tmp_path)
    project = store.create_project(name="mem", workspace=ws)
    pid = project.project_id
    memory = store.memory_store(pid)
    expected_dir = tmp_path / "projects" / pid / "memory"
    assert memory.dir == expected_dir
    assert memory.db_path == expected_dir / "memory.db"

    item_id = memory.add_candidate(title="项目决策", content="采用 SQLite WAL", tier="project")
    assert (expected_dir / "project" / f"{item_id}.md").exists()
    with sqlite3.connect(memory.db_path) as conn:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"

    def writer(label: str) -> None:
        memory.add_candidate(title=f"线程{label}", content="并发写入", tier="gotcha")

    threads = [threading.Thread(target=writer, args=(label,)) for label in ("a", "b")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(memory.list_items(tier="gotcha")) == 2
