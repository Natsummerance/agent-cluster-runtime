"""T13.2 v0.5 → 项目目录存储迁移：9 步无损幂等算法 / 失败回退 /
checkpoint 续跑 / 记忆合并去重 / create_project 挂接。"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

import agent_cluster.projects as projects_module
from agent_cluster.memory import MemoryStore
from agent_cluster.projects import ProjectStore
from agent_cluster.session import (
    FileCheckpointer,
    GateDecisionRecord,
    PhaseStatus,
    QARecord,
    SessionDriver,
    SessionRecord,
    TokenLedgerEntry,
)


# ---------------------------------------------------------------------------
# fixture 构造器：完整 v0.5 布局
# ---------------------------------------------------------------------------


def _build_v05_layout(
    tmp_path: Path, *, name: str = "workspace", status: str = "completed"
) -> tuple[Path, dict]:
    """在 tmp 工作区构造完整 v0.5 布局并返回 (workspace, 元数据)。

    - ``session.json``：completed（或指定状态）+ 非空 ledger/transcript/phases
    - ``checkpoints/<thread_id>.json``：经真实 ``FileCheckpointer.put`` 写入
    - workspace 记忆：session/project/gotcha/domain 四层各一个 item
    """
    ws = tmp_path / name
    ws.mkdir()
    agent_dir = ws / ".agent-cluster"
    agent_dir.mkdir()
    (agent_dir / ".gitignore").write_text("*\n", encoding="utf-8")

    sid = "v05-session-0001"
    thread_id = "v05-thread-0001"
    record = SessionRecord(
        session_id=sid,
        thread_id=thread_id,
        goal="构建一个 CLI",
        flow="examples/flows/build-product.yaml",
        model="codex",
        workspace=str(ws),
        status=status,
    )
    record.transcript.append(
        QARecord(id="qa-1", question="范围？", answer="MVP", source="human", node="requirements")
    )
    record.gate_decisions.append(
        GateDecisionRecord(
            node="design_gate", kind="design_review", attempts=1, last_decision="accept"
        )
    )
    record.phases["requirements"] = PhaseStatus(name="requirements", status="done", tokens_used=50)
    record.token_ledger.budget = 100_000
    record.token_ledger.entries.append(
        TokenLedgerEntry(role="pm", phase="requirements", total_tokens=50)
    )
    (agent_dir / "session.json").write_text(record.model_dump_json(indent=2), encoding="utf-8")

    # checkpoints/<thread_id>.json 经真实 FileCheckpointer.put 写入
    checkpointer = FileCheckpointer(agent_dir / "checkpoints")
    checkpointer.put(
        {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}},
        {
            "v": 1,
            "id": "ckpt-1",
            "ts": datetime.now(timezone.utc).isoformat(),
            "channel_values": {"x": "hello"},
            "channel_versions": {},
            "versions_seen": {},
            "pending_sends": [],
        },
        {},
        None,
    )

    # workspace 记忆：四层各一个 item
    memory = MemoryStore(ws)
    memory_ids = {
        "session": memory.add_candidate(
            title="会话要点", content="本次会话结论", tier="session", session_id=sid
        ),
        "project": memory.add_candidate(
            title="项目决策", content="采用 WAL", tier="project", session_id=sid
        ),
        "gotcha": memory.add_candidate(
            title="踩坑", content="Windows 路径坑", tier="gotcha", session_id=sid
        ),
        "domain": memory.add_candidate(
            title="领域知识", content="领域笔记", tier="domain", session_id=sid
        ),
    }

    return ws, {
        "session_id": sid,
        "thread_id": thread_id,
        "record": record,
        "memory_ids": memory_ids,
    }


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _make_store_and_project(tmp_path: Path, workspace: Path) -> tuple[ProjectStore, str]:
    """在独立空工作区创建项目（不触发迁移），返回 (store, project_id)。"""
    empty = tmp_path / "project-ws"
    empty.mkdir()
    store = ProjectStore(tmp_path / "root")
    project = store.create_project(name="proj", workspace=empty)
    return store, project.project_id


# ---------------------------------------------------------------------------
# 迁移成功 + 无损
# ---------------------------------------------------------------------------


def test_migrate_success(tmp_path):
    ws, layout = _build_v05_layout(tmp_path)
    store, pid = _make_store_and_project(tmp_path, ws)

    source_path = ws / ".agent-cluster" / "session.json"
    source_bytes = _sha256(source_path)

    migrated = store.migrate_legacy_session(pid, ws)
    assert migrated is not None
    assert migrated.project_id == pid
    assert migrated.session_id == layout["session_id"]
    assert migrated.token_ledger.total() > 0

    # 目标目录精确存在 + project_id 已写入
    sid = layout["session_id"]
    session_dir = store.session_dir(pid, sid)
    target = session_dir / "session.json"
    assert target.is_file()
    on_disk = SessionRecord.model_validate(json.loads(target.read_text(encoding="utf-8")))
    assert on_disk.session_id == sid
    assert on_disk.project_id == pid
    assert on_disk.thread_id == layout["thread_id"]
    assert (session_dir / "checkpoints" / f"{layout['thread_id']}.json").is_file()

    # 备份存在且与源字节一致；源 session.json 字节不变
    backups = list((ws / ".agent-cluster" / "backups").glob("session.v0.5.*.json"))
    assert len(backups) == 1
    assert backups[0].read_bytes() == source_path.read_bytes()
    assert _sha256(source_path) == source_bytes

    # 标记与注册表
    marker = json.loads((ws / ".agent-cluster" / ".migrated.json").read_text(encoding="utf-8"))
    assert marker["project_id"] == pid
    assert marker["session_id"] == sid
    assert marker["source"] == "session.json"
    entries = store.get(pid).sessions
    assert [entry.session_id for entry in entries] == [sid]
    assert entries[0].status == "completed"
    assert entries[0].goal == layout["record"].goal


def test_checkpoint_resume_after_migration(tmp_path):
    ws, layout = _build_v05_layout(tmp_path, status="active")
    store, pid = _make_store_and_project(tmp_path, ws)

    migrated = store.migrate_legacy_session(pid, ws)
    assert migrated is not None

    driver = SessionDriver(
        workspace=ws,
        goal="续跑目标",
        flow="examples/flows/build-product.yaml",
        resume=True,
        checkpoint_root=store.session_dir(pid, layout["session_id"]) / "checkpoints",
    )
    tup = driver.checkpointer.get_tuple(
        {"configurable": {"thread_id": layout["thread_id"]}}
    )
    assert tup is not None
    assert tup.config["configurable"]["thread_id"] == layout["thread_id"]
    assert tup.checkpoint["id"] == "ckpt-1"


# ---------------------------------------------------------------------------
# 幂等
# ---------------------------------------------------------------------------


def test_migrate_idempotent(tmp_path):
    ws, layout = _build_v05_layout(tmp_path)
    store, pid = _make_store_and_project(tmp_path, ws)

    first = store.migrate_legacy_session(pid, ws)
    assert first is not None
    backups_before = list((ws / ".agent-cluster" / "backups").glob("session.v0.5.*.json"))
    target = store.session_dir(pid, layout["session_id"]) / "session.json"
    target_bytes = target.read_bytes()

    second = store.migrate_legacy_session(pid, ws)
    assert second is not None
    assert second.session_id == first.session_id
    assert second.project_id == pid
    assert len(list((ws / ".agent-cluster" / "backups").glob("session.v0.5.*.json"))) == len(
        backups_before
    )
    assert target.read_bytes() == target_bytes
    assert len(store.get(pid).sessions) == 1


# ---------------------------------------------------------------------------
# 损坏源回退
# ---------------------------------------------------------------------------


def test_migrate_corrupted_source(tmp_path):
    ws, _ = _build_v05_layout(tmp_path)
    (ws / ".agent-cluster" / "session.json").write_text("{ not valid json", encoding="utf-8")

    store = ProjectStore(tmp_path / "root")
    project = store.create_project(name="proj", workspace=ws)
    pid = project.project_id

    assert store.migrate_legacy_session(pid, ws) is None
    assert not (ws / ".agent-cluster" / ".migrated.json").exists()
    assert not (tmp_path / "root" / "projects" / pid / "sessions").exists()
    assert store.get(pid).sessions == []


# ---------------------------------------------------------------------------
# 失败回退（第 4–9 步异常 → 告警返回 None，可恢复重试）
# ---------------------------------------------------------------------------


def test_migrate_failure_rollback(tmp_path, monkeypatch):
    ws, layout = _build_v05_layout(tmp_path)
    store, pid = _make_store_and_project(tmp_path, ws)

    source_path = ws / ".agent-cluster" / "session.json"
    source_bytes = _sha256(source_path)
    original = projects_module._atomic_write_json

    def failing_write(path: Path, text: str) -> None:
        if path.name == "session.json":
            raise OSError("simulated disk failure")
        original(path, text)

    monkeypatch.setattr(projects_module, "_atomic_write_json", failing_write)
    assert store.migrate_legacy_session(pid, ws) is None
    assert _sha256(source_path) == source_bytes
    assert not (ws / ".agent-cluster" / ".migrated.json").exists()
    assert not (store.session_dir(pid, layout["session_id"]) / "session.json").exists()
    assert store.get(pid).sessions == []

    monkeypatch.setattr(projects_module, "_atomic_write_json", original)
    recovered = store.migrate_legacy_session(pid, ws)
    assert recovered is not None
    assert (store.session_dir(pid, layout["session_id"]) / "session.json").is_file()
    assert (ws / ".agent-cluster" / ".migrated.json").is_file()
    assert len(store.get(pid).sessions) == 1


# ---------------------------------------------------------------------------
# 记忆合并：分层归属 + item id 幂等去重 + 源文件保留
# ---------------------------------------------------------------------------


def test_memory_merge_dedupe(tmp_path):
    ws, layout = _build_v05_layout(tmp_path)
    store, pid = _make_store_and_project(tmp_path, ws)

    first = store.migrate_legacy_session(pid, ws)
    assert first is not None

    project_memory = store.memory_store(pid)
    global_memory = MemoryStore(tmp_path / "root")

    def ids(memory, tier: str) -> set[str]:
        return {item.id for item in memory.list_items(tier=tier, limit=1000)}

    session_id = layout["memory_ids"]["session"]
    project_item_id = layout["memory_ids"]["project"]
    gotcha_id = layout["memory_ids"]["gotcha"]
    domain_id = layout["memory_ids"]["domain"]

    # 分层归属：session/project → 项目库；gotcha/domain → 全局库
    assert session_id in ids(project_memory, "session")
    assert project_item_id in ids(project_memory, "project")
    assert gotcha_id in ids(global_memory, "gotcha")
    assert domain_id in ids(global_memory, "domain")
    assert gotcha_id not in ids(project_memory, "gotcha")
    assert session_id not in ids(global_memory, "session")

    # 模拟第 8 步前的失败重试：删除标记后重跑，第 7 步再次执行 → 按 item id 去重
    (ws / ".agent-cluster" / ".migrated.json").unlink()
    second = store.migrate_legacy_session(pid, ws)
    assert second is not None
    assert second.session_id == layout["session_id"]
    assert len(project_memory.list_items(tier="session", limit=1000)) == 1
    assert len(project_memory.list_items(tier="project", limit=1000)) == 1
    assert len(global_memory.list_items(tier="gotcha", limit=1000)) == 1
    assert len(global_memory.list_items(tier="domain", limit=1000)) == 1
    assert len(store.get(pid).sessions) == 1

    # 源记忆文件保留不删除
    assert (ws / ".agent-cluster" / "memory" / "gotcha" / f"{gotcha_id}.md").is_file()
    assert (ws / ".agent-cluster" / "memory" / "session" / f"{session_id}.md").is_file()


# ---------------------------------------------------------------------------
# create_project 挂接迁移
# ---------------------------------------------------------------------------


def test_create_project_triggers_migration(tmp_path):
    ws, layout = _build_v05_layout(tmp_path)
    store = ProjectStore(tmp_path / "root")

    project = store.create_project(name="proj", workspace=ws)
    pid = project.project_id

    # 返回的 project 已含迁移登记的首会话
    entries = project.sessions
    assert [entry.session_id for entry in entries] == [layout["session_id"]]
    assert entries[0].status == "completed"
    assert entries[0].goal == layout["record"].goal

    target = store.session_dir(pid, layout["session_id"]) / "session.json"
    assert target.is_file()
    on_disk = SessionRecord.model_validate(json.loads(target.read_text(encoding="utf-8")))
    assert on_disk.project_id == pid
    assert (ws / ".agent-cluster" / ".migrated.json").is_file()
