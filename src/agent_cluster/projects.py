"""项目层（v0.6 T13.1）：项目数据模型 + ProjectStore 权威存储。

- 模型：``BudgetUnlockRecord / BudgetPoolRecord / GatePolicyConfig /
  SessionIndexEntry / ProjectRecord``（pydantic v2，``extra="ignore"``）。
- ``ProjectStore``：``<root>/projects/<pid>/project.json`` 权威读写——
  原子写（临时文件 + ``os.replace``）、每项目一把线程锁串行写、
  预算解锁审计 append-only、会话注册表 upsert 与 token 聚合。
- 目录布局（设计 §3.4）::

    <root>/projects/<pid>/
        ├── project.json
        ├── memory/                # 项目级 MemoryStore
        └── sessions/<sid>/
            ├── session.json       # v0.6 权威会话存储
            └── checkpoints/

- 增量（13.3/13.4）：预算预警判定与解锁、fork-session 领域逻辑在此实现；
  serve 端点（13.7）与 SessionManager（13.5）不在本文件。
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from agent_cluster.changes import ChangeHistory
from agent_cluster.memory import TIER_ORDER, MemoryStore
from agent_cluster.session import (
    DEFAULT_REWORK_LIMIT,
    FileCheckpointer,
    QARecord,
    SessionRecord,
    SessionStore,
    TokenLedger,
)
from agent_cluster.worktree import WorktreeManager

__all__ = [
    "DEFAULT_FLOW",
    "BudgetPoolExhaustedError",
    "BudgetUnlockRecord",
    "BudgetPoolRecord",
    "GatePolicyConfig",
    "SessionIndexEntry",
    "ForkConflictError",
    "ProjectRecord",
    "ProjectStore",
    "fork_session",
    "make_budget_pool_hook",
]

# 新建会话默认流程（与 server.py 现值一致；server.py 的引用统一在 13.7 切换到本处）。
DEFAULT_FLOW = "examples/flows/build-product.yaml"

logger = logging.getLogger("agent-cluster")


class ForkConflictError(RuntimeError):
    """fork 前置校验冲突（源非终态 / 血缘超限 / 源不存在 / 无项目归属）。"""


class BudgetPoolExhaustedError(RuntimeError):
    """项目预算硬上限耗尽，拒绝新建会话（设计 §5.2-3）。"""


# ---------------------------------------------------------------------------
# 模型（设计 §3.1）
# ---------------------------------------------------------------------------


class BudgetUnlockRecord(BaseModel):
    """一次预算池解锁的审计记录（自服务解锁与例外审批共用）。"""

    model_config = ConfigDict(extra="ignore")

    id: str = Field(default_factory=lambda: uuid.uuid4().hex, description="解锁记录 id")
    session_id: str = Field(default="", description="触发解锁的会话 id（空=项目级操作）")
    additional_tokens: int = Field(gt=0, description="申请追加的 token 数")
    reason: str = Field(default="", description="解锁理由（非空语义由 API 层校验）")
    status: Literal["granted", "pending", "denied"] = Field(default="granted", description="解锁状态")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc), description="申请时间")
    decided_at: datetime | None = Field(default=None, description="审批决策时间（仅 pending 流转时写入）")
    decided_by: str = Field(default="self", description="决策者：self 或审批人标识")


class BudgetPoolRecord(BaseModel):
    """项目级预算池：静态分配（会话独立预算延续现状 + 项目硬上限与阈值预警）。"""

    model_config = ConfigDict(extra="ignore")

    hard_limit_tokens: int = Field(
        default=0,
        ge=0,
        description="项目硬上限 token（0=不设限；与 TokenLedger 的 budget>0 判定约定一致）",
    )
    warn_ratio: float = Field(
        default=0.8,
        gt=0.0,
        le=1.0,
        description="预警阈值比例：项目已用 >= hard_limit×warn_ratio 触发 budget.warning",
    )
    warn_reenable_ratio: float = Field(
        default=0.7,
        gt=0.0,
        le=1.0,
        description="预警滞回解除比例：已用回落到该比例以下才允许再次预警",
    )
    unlock_requires_approval: bool = Field(
        default=False, description="解锁是否需人工审批（例外开关；False=自服务直接生效）"
    )
    unlocks: list[BudgetUnlockRecord] = Field(default_factory=list, description="解锁审计记录（append-only）")
    warn_raised: bool = Field(default=False, description="预警是否已触发（滞回防抖状态）")
    last_warned_at: datetime | None = Field(default=None, description="最近一次预警时间")


class GatePolicyConfig(BaseModel):
    """自动 reviewer 门策略（内嵌 project.json，可经 API 局部覆盖）。"""

    model_config = ConfigDict(extra="ignore")

    auto_review: bool = Field(default=True, description="启用自动评审；False=回到 v0.5 ask 人工模式")
    auto_kinds: list[str] = Field(
        default_factory=lambda: ["design_review", "code_review", "iteration_acceptance"],
        description="自动评审白名单（常规变更门类别）",
    )
    human_kinds: list[str] = Field(
        default_factory=lambda: [
            "requirement_confirmation",
            "release",
            "dangerous_tool",
            "evolution_apply",
        ],
        description="恒人工黑名单（边界动作门类别）",
    )
    review_confidence_threshold: float = Field(
        default=0.7, gt=0.0, le=1.0, description="自动评审置信度低于该值升级人工"
    )
    rework_escalation: int = Field(
        default=DEFAULT_REWORK_LIMIT,
        gt=0,
        description="返工上限：同门 reject/edit 累计达到即升级人工（与 rework_limit 同语义）",
    )
    review_prompt: str = Field(default="", description="自动评审提示词覆盖（空=内置默认）")


class SessionIndexEntry(BaseModel):
    """项目会话注册表条目（任务面板与项目聚合的数据源；运行时状态以 ServerSession 为准）。"""

    model_config = ConfigDict(extra="ignore")

    session_id: str = Field(description="会话 id")
    goal: str = Field(default="", description="会话目标")
    status: Literal["active", "completed", "aborted"] = Field(
        default="active", description="会话终态（与 SessionRecord.status 一致）"
    )
    assignee: str = Field(default="", description="任务面板指派（空=未指派）")
    workspace: str = Field(default="", description="会话工作区绝对路径（worktree 开启时为检出路径）")
    worktree: bool = Field(default=False, description="是否运行在隔离 worktree")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc), description="创建时间")
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc), description="更新时间")
    metadata: dict[str, str] = Field(
        default_factory=dict, description="可过滤元数据（对标 LangSmith threads 的 metadata 过滤）"
    )


class ProjectRecord(BaseModel):
    """项目容器：多工作区 + 多会话 + 预算池 + 门策略。"""

    model_config = ConfigDict(extra="ignore")

    project_id: str = Field(description="项目 id（uuid4().hex[:12]，沿用 v0.5 格式）")
    name: str = Field(min_length=1, description="项目名")
    description: str = Field(default="", description="项目描述")
    workspaces: list[str] = Field(min_length=1, description="工作区绝对路径列表（首项为主工作区，为集成/合并目标）")
    default_flow: str = Field(default=DEFAULT_FLOW, description="新建会话默认流程 YAML 路径")
    status: Literal["active", "archived"] = Field(default="active", description="项目状态")
    budget_pool: BudgetPoolRecord = Field(default_factory=BudgetPoolRecord, description="项目预算池")
    gate_policy: GatePolicyConfig = Field(default_factory=GatePolicyConfig, description="门策略（自动 reviewer 配置）")
    sessions: list[SessionIndexEntry] = Field(
        default_factory=list, description="会话注册表（索引；账本/阶段等权威数据在各会话 session.json）"
    )
    metadata: dict[str, str] = Field(default_factory=dict, description="项目元数据（过滤/归类）")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc), description="创建时间")
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc), description="更新时间")


# ---------------------------------------------------------------------------
# 写锁：模块级注册表 + 每项目一把锁（project.json 写入串行）
# ---------------------------------------------------------------------------

_LOCKS_GUARD = threading.Lock()
_PROJECT_LOCKS: dict[str, threading.Lock] = {}


def _project_lock(project_id: str) -> threading.Lock:
    """获取（或注册）指定项目的写锁。"""
    with _LOCKS_GUARD:
        lock = _PROJECT_LOCKS.get(project_id)
        if lock is None:
            lock = threading.Lock()
            _PROJECT_LOCKS[project_id] = lock
        return lock


def _atomic_write_json(path: Path, text: str) -> None:
    """原子写入：临时文件 + os.replace（同目录内替换，读方只看到旧/新完整文件）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


# ---------------------------------------------------------------------------
# ProjectStore（设计 §3.3，除 migrate_legacy_session 由 Task 13.2 落地）
# ---------------------------------------------------------------------------


class ProjectStore:
    """项目层存储（权威数据源；默认根 ~/.agent-cluster/projects/；§4 迁移见
    :meth:`migrate_legacy_session`）。"""

    def __init__(self, root: str | Path | None = None) -> None:
        self.root = Path(root).expanduser().resolve() if root is not None else Path.home() / ".agent-cluster"
        self._projects_dir = self.root / "projects"
        self._projects_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # 路径与文件 IO
    # ------------------------------------------------------------------

    def _project_dir(self, project_id: str) -> Path:
        return self._projects_dir / project_id

    def _project_path(self, project_id: str) -> Path:
        return self._project_dir(project_id) / "project.json"

    def _load(self, project_id: str) -> ProjectRecord | None:
        """读取项目记录；缺失/损坏返回 None（发现层容错，权威读方自行判定）。"""
        try:
            data = json.loads(self._project_path(project_id).read_text(encoding="utf-8"))
            return ProjectRecord.model_validate(data)
        except (OSError, json.JSONDecodeError, UnicodeDecodeError, ValueError):
            return None

    def _save(self, record: ProjectRecord) -> None:
        _atomic_write_json(self._project_path(record.project_id), record.model_dump_json(indent=2))

    def _touch(self, record: ProjectRecord) -> ProjectRecord:
        """刷新 updated_at（写前调用；模型直接赋值，值恒为合法 datetime）。"""
        record.updated_at = datetime.now(timezone.utc)
        return record

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def create_project(
        self,
        *,
        name: str,
        workspace: str | Path,
        description: str = "",
        default_flow: str = DEFAULT_FLOW,
        metadata: dict[str, str] | None = None,
        project_id: str | None = None,
    ) -> ProjectRecord:
        """创建项目；workspace 含 v0.5 session.json 时执行 §4 迁移为项目首个会话。

        ``project_id`` 缺省自动生成（T13.5：serve 双写全局索引时传入同值）。
        """
        workspace_path = Path(workspace).expanduser().resolve()
        project = ProjectRecord(
            project_id=project_id or uuid.uuid4().hex[:12],
            name=name,
            description=description,
            workspaces=[str(workspace_path)],
            default_flow=default_flow,
            metadata=metadata or {},
        )
        with _project_lock(project.project_id):
            self._save(project)
        # §4.1 触发时机：存在 v0.5 session.json → 立即迁移为项目首个会话
        if (workspace_path / ".agent-cluster" / "session.json").is_file():
            if self.migrate_legacy_session(project.project_id, workspace_path) is not None:
                return self.get(project.project_id)
        return project

    def get(self, project_id: str) -> ProjectRecord:
        record = self._load(project_id)
        if record is None:
            raise KeyError(f"项目不存在或损坏: {project_id}")
        return record

    def list(self) -> list[ProjectRecord]:
        records: list[ProjectRecord] = []
        if not self._projects_dir.is_dir():
            return records
        for child in sorted(self._projects_dir.iterdir(), key=lambda path: path.name):
            if not child.is_dir():
                continue
            record = self._load(child.name)
            if record is not None:
                records.append(record)
        return records

    def update(self, project_id: str, **fields: Any) -> ProjectRecord:
        """局部更新（含 gate_policy/budget_pool 子模型的 dict 覆盖；非法值抛 ValueError）。"""
        with _project_lock(project_id):
            record = self.get(project_id)
            updates: dict[str, Any] = dict(fields)
            for sub_model in ("gate_policy", "budget_pool"):
                if sub_model in updates:
                    override = updates[sub_model]
                    if not isinstance(override, dict):
                        raise ValueError(f"{sub_model} 必须以 dict 覆盖")
                    updates[sub_model] = getattr(record, sub_model).model_copy(update=override)
            try:
                merged = record.model_dump()
                merged.update(updates)
                updated = ProjectRecord.model_validate(merged)
            except ValidationError as exc:
                raise ValueError(f"更新字段非法: {exc}") from exc
            self._save(self._touch(updated))
            return updated

    def archive(self, project_id: str) -> ProjectRecord:
        with _project_lock(project_id):
            record = self.get(project_id)
            updated = record.model_copy(update={"status": "archived"})
            self._save(self._touch(updated))
            return updated

    def add_workspace(self, project_id: str, path: str | Path) -> ProjectRecord:
        """追加工作区（存在性校验 + 去重；首项仍为主工作区）。"""
        resolved = Path(path).expanduser().resolve()
        if not resolved.is_dir():
            raise ValueError(f"工作区路径不存在: {resolved}")
        with _project_lock(project_id):
            record = self.get(project_id)
            if str(resolved) in record.workspaces:
                return record
            updated = record.model_copy(update={"workspaces": record.workspaces + [str(resolved)]})
            self._save(self._touch(updated))
            return updated

    # ------------------------------------------------------------------
    # 会话目录与会话注册表（§4.1 / §3.4）
    # ------------------------------------------------------------------

    def session_store(self, project_id: str, session_id: str) -> SessionStore:
        """返回指向 projects/<pid>/sessions/<sid>/ 的 SessionStore（v0.6 权威会话存储）。"""
        project = self.get(project_id)
        return SessionStore(
            project.workspaces[0],
            session_id=session_id,
            project_id=project_id,
            root=self.root,
        )

    def session_dir(self, project_id: str, session_id: str) -> Path:
        return self._project_dir(project_id) / "sessions" / session_id

    def index_session(self, project_id: str, entry: SessionIndexEntry) -> None:
        """会话注册表 upsert（按 session_id；不修改会话权威数据）。"""
        with _project_lock(project_id):
            record = self.get(project_id)
            entry = entry.model_copy(update={"updated_at": datetime.now(timezone.utc)})
            sessions = list(record.sessions)
            for index, existing in enumerate(sessions):
                if existing.session_id == entry.session_id:
                    sessions[index] = entry
                    break
            else:
                sessions.append(entry)
            updated = record.model_copy(update={"sessions": sessions})
            self._save(self._touch(updated))

    def aggregate_used_tokens(self, project_id: str) -> int:
        """Σ 各会话 ledger.total()（权威归因：扫描会话 session.json，不读注册表投影）。"""
        sessions_dir = self._project_dir(project_id) / "sessions"
        total = 0
        if not sessions_dir.is_dir():
            return total
        for session_dir in sessions_dir.iterdir():
            if not session_dir.is_dir():
                continue
            try:
                data = json.loads((session_dir / "session.json").read_text(encoding="utf-8"))
                record = SessionRecord.model_validate(data)
            except (OSError, json.JSONDecodeError, UnicodeDecodeError, ValueError):
                continue
            total += record.token_ledger.total()
        return total

    def is_budget_exhausted(self, project_id: str) -> bool:
        """项目硬上限判定（设计 §5.2-3）：hard_limit > 0 且聚合用量 > 上限（0=不设限，永不耗尽）。"""
        pool = self.get(project_id).budget_pool
        if pool.hard_limit_tokens <= 0:
            return False
        return self.aggregate_used_tokens(project_id) > pool.hard_limit_tokens

    def budget_status(self, project_id: str) -> dict[str, Any]:
        """预算快照（设计 §5.1）：聚合用量实时计算，project.json 不冗余存用量；13.7 BudgetSnapshot 直接序列化本字典。"""
        pool = self.get(project_id).budget_pool
        used = self.aggregate_used_tokens(project_id)
        remaining: int | None = None
        if pool.hard_limit_tokens > 0:
            remaining = max(0, pool.hard_limit_tokens - used)
        return {
            "hard_limit_tokens": pool.hard_limit_tokens,
            "used": used,
            "remaining": remaining,
            "warn_raised": pool.warn_raised,
            "last_warned_at": pool.last_warned_at.isoformat() if pool.last_warned_at else None,
            "unlocks": [unlock.model_dump(mode="json") for unlock in pool.unlocks],
        }

    # ------------------------------------------------------------------
    # v0.5 → 项目目录存储迁移（§4.2：无损、幂等、失败回退）
    # ------------------------------------------------------------------

    def migrate_legacy_session(self, project_id: str, workspace: str | Path) -> SessionRecord | None:
        """9 步迁移算法（设计 §4.2）。

        1 幂等短路 → 2 读源 → 3 校验拷贝 → 4 备份先行 → 5 原子写目标
        session.json → 6 复制 checkpoints → 7 记忆合并 → 8 写 .migrated.json
        → 9 登记 SessionIndexEntry。

        - 第 2/3 步失败（源缺失/损坏）→ 静默返回 None（与 v0.5「损坏即新
          会话」容错一致）。
        - 第 4–9 步任一异常 → ``[migration]`` 警告日志后返回 None（不抛出），
          源文件零改动；因未写标记，下次调用自动重试。
        - 无损：永不删除/改写源 session.json；checkpoint/记忆均为复制合并。
        """
        workspace_path = Path(workspace).expanduser().resolve()
        source_dir = workspace_path / ".agent-cluster"
        source_path = source_dir / "session.json"
        marker_path = source_dir / ".migrated.json"

        # 1. 幂等短路：标记存在且其 sid 对应本项目的目标 session.json 存在
        marker: dict[str, Any] = {}
        if marker_path.is_file():
            try:
                marker = json.loads(marker_path.read_text(encoding="utf-8"))
                if not isinstance(marker, dict):
                    marker = {}
            except (OSError, json.JSONDecodeError, UnicodeDecodeError):
                marker = {}
        short_sid = str(marker.get("session_id") or "")
        if short_sid and str(marker.get("project_id") or "") == project_id:
            short_target = self.session_dir(project_id, short_sid) / "session.json"
            if short_target.is_file():
                try:
                    return SessionRecord.model_validate(
                        json.loads(short_target.read_text(encoding="utf-8"))
                    )
                except (OSError, json.JSONDecodeError, UnicodeDecodeError, ValueError):
                    pass  # 目标损坏 → 落入重迁移路径

        # 2. 读源：缺失/损坏 → 不迁移
        try:
            data = json.loads(source_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            return None

        # 3. 校验 + 拷贝（不改写源文件）
        try:
            record = SessionRecord.model_validate(data)
        except ValueError:
            return None
        if not record.session_id:
            return None
        sid = record.session_id
        target_record = record.model_copy(update={"project_id": project_id})

        try:
            # 4. 备份先行（永远先于任何新位置写入）
            backups_dir = source_dir / "backups"
            backups_dir.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
            backup_path = backups_dir / f"session.v0.5.{timestamp}.json"
            shutil.copy2(source_path, backup_path)

            # 5. 原子写目标 session.json
            target_dir = self.session_dir(project_id, sid)
            _atomic_write_json(target_dir / "session.json", target_record.model_dump_json(indent=2))

            # 6. 复制 checkpoints（thread_id 持久游标不断，resume 可用）
            source_checkpoints = source_dir / "checkpoints"
            if source_checkpoints.is_dir():
                target_checkpoints = target_dir / "checkpoints"
                target_checkpoints.mkdir(parents=True, exist_ok=True)
                thread_file = FileCheckpointer(source_checkpoints)._file_for(record.thread_id)
                if thread_file.is_file():
                    shutil.copy2(thread_file, target_checkpoints / thread_file.name)

            # 7. 记忆合并（session/project → 项目库；gotcha/domain → 全局库）
            self._merge_memory(project_id, workspace_path)

            # 8. 写迁移标记
            _atomic_write_json(
                marker_path,
                json.dumps(
                    {
                        "migrated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                        "project_id": project_id,
                        "session_id": sid,
                        "source": "session.json",
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
            )

            # 9. 登记 SessionIndexEntry（status/goal 取源记录）
            self.index_session(
                project_id,
                SessionIndexEntry(
                    session_id=sid,
                    goal=record.goal,
                    status=record.status,
                    workspace=str(workspace_path),
                    created_at=record.created_at,
                    updated_at=record.updated_at,
                ),
            )
        except Exception as exc:  # noqa: BLE001 —— §4.2 失败回退：告警不抛出
            logger.warning(
                "[migration] v0.5 会话迁移失败（project_id=%s workspace=%s）：%s",
                project_id,
                workspace_path,
                exc,
            )
            return None
        return target_record

    def _merge_memory(self, project_id: str, workspace: Path) -> None:
        """§4.2 第 7 步：按 item id 幂等合并 workspace 级记忆。

        session/project 层 → 项目记忆库；gotcha/domain 层 → 全局记忆库
        （``<root>``）。经 MemoryStore 读取 API 遍历、目标库写入 API 落库
        （不直接跨库拼 SQL）；源文件一律保留不删除；重复 id 跳过。
        """
        source = MemoryStore(workspace)
        project_memory = self.memory_store(project_id)
        global_memory = MemoryStore(self.root)
        for tier in TIER_ORDER:
            target = project_memory if tier in ("session", "project") else global_memory
            for item in source.list_items(tier=tier, limit=10_000):
                target.import_item(item, item.content(source.root))

    # ------------------------------------------------------------------
    # 预算池解锁（§5 静态分配；预警判定在 13.3）
    # ------------------------------------------------------------------

    def unlock_budget(
        self,
        project_id: str,
        *,
        additional_tokens: int,
        reason: str,
        session_id: str = "",
        emit: Callable[[str, dict], None] | None = None,
    ) -> BudgetUnlockRecord:
        """解锁（设计 §5.3）：自服务 granted 直接提额；unlock_requires_approval 时走 pending 审批。"""
        if additional_tokens <= 0:
            raise ValueError("additional_tokens 必须大于 0")
        with _project_lock(project_id):
            record = self.get(project_id)
            if record.budget_pool.unlock_requires_approval:
                # 设计 §5.3 例外审批：追加 pending 审计记录、不提高硬上限；decide_unlock 完成后续流转。
                unlock = BudgetUnlockRecord(
                    session_id=session_id,
                    additional_tokens=additional_tokens,
                    reason=reason,
                    status="pending",
                    decided_at=None,
                    decided_by="",
                )
                pool = record.budget_pool.model_copy(
                    update={"unlocks": record.budget_pool.unlocks + [unlock]}
                )
                self._save(self._touch(record.model_copy(update={"budget_pool": pool})))
                return unlock
            unlock = BudgetUnlockRecord(
                session_id=session_id,
                additional_tokens=additional_tokens,
                reason=reason,
                status="granted",
                decided_by="self",
            )
            pool = record.budget_pool.model_copy(
                update={
                    "hard_limit_tokens": record.budget_pool.hard_limit_tokens + additional_tokens,
                    "unlocks": record.budget_pool.unlocks + [unlock],
                }
            )
            self._save(self._touch(record.model_copy(update={"budget_pool": pool})))
            if emit is not None:
                try:
                    emit(
                        "budget.unlocked",
                        {
                            "project_id": project_id,
                            "unlock_id": unlock.id,
                            "additional_tokens": additional_tokens,
                            "hard_limit_tokens": pool.hard_limit_tokens,
                            "decided_by": unlock.decided_by,
                        },
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning("[budget] 事件发送失败：%s", exc)
            return unlock

    def decide_unlock(self, project_id: str, unlock_id: str, *, approved: bool, decided_by: str) -> BudgetUnlockRecord:
        """审批 pending 解锁（仅 pending 可决）：approved 提额并 granted，否则 denied 不提额。"""
        with _project_lock(project_id):
            record = self.get(project_id)
            unlocks = list(record.budget_pool.unlocks)
            target_index = -1
            target: BudgetUnlockRecord | None = None
            for index, unlock in enumerate(unlocks):
                if unlock.id == unlock_id:
                    target = unlock
                    target_index = index
                    break
            if target is None:
                raise ValueError(f"解锁记录不存在: {unlock_id}")
            if target.status != "pending":
                raise ValueError(f"仅 pending 解锁记录可审批，当前状态: {target.status}")
            now = datetime.now(timezone.utc)
            if approved:
                new_status = "granted"
                new_hard_limit = record.budget_pool.hard_limit_tokens + target.additional_tokens
            else:
                new_status = "denied"
                new_hard_limit = record.budget_pool.hard_limit_tokens
            decided = target.model_copy(
                update={"status": new_status, "decided_at": now, "decided_by": decided_by}
            )
            unlocks[target_index] = decided
            pool = record.budget_pool.model_copy(
                update={"hard_limit_tokens": new_hard_limit, "unlocks": unlocks}
            )
            self._save(self._touch(record.model_copy(update={"budget_pool": pool})))
            return decided

    # ------------------------------------------------------------------
    # 记忆库（§4.3：项目级 MemoryStore，四级结构不变）
    # ------------------------------------------------------------------

    def memory_store(self, project_id: str) -> MemoryStore:
        return MemoryStore(self.root, base_dir=self._project_dir(project_id) / "memory")



def make_budget_pool_hook(
    project_store: ProjectStore,
    emit: Callable[[str, dict], None] | None = None,
) -> Callable[[SessionRecord], None]:
    """构造项目预算池钩子（设计 §5.4）：聚合用量 → 预警滞回 → 硬上限事件。

    - 会话级 ``ledger.over_budget()`` 判定由 v0.5 既有逻辑处理，本钩子不重复判定；
    - 预警滞回（§5.2-2）：``used >= hard_limit × warn_ratio`` 且未触发 → 置
      ``warn_raised=True`` + ``budget.warning``；``used < hard_limit × warn_reenable_ratio``
      时复位（``budget.warn_reset``）；
    - 硬上限（§5.2-3）：``used > hard_limit`` → ``budget.exhausted``（挂起/拒绝由 13.5 消费）；
    - ``hard_limit_tokens=0`` 一律不触发；
    - 运行中会话挂起与新建拒绝属 SessionManager（13.5），本函数只交付纯判定与事件。
    """

    def _emit(name: str, payload: dict[str, Any]) -> None:
        if emit is None:
            return
        try:
            emit(name, payload)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[budget] 事件发送失败：%s", exc)

    def hook(record: SessionRecord) -> None:
        project_id = record.project_id
        if not project_id:
            return
        pool = project_store.get(project_id).budget_pool
        hard_limit = pool.hard_limit_tokens
        if hard_limit <= 0:
            return
        used = project_store.aggregate_used_tokens(project_id)
        updates: dict[str, Any] = {}
        if used >= hard_limit * pool.warn_ratio:
            if not pool.warn_raised:
                updates["warn_raised"] = True
                updates["last_warned_at"] = datetime.now(timezone.utc)
                _emit(
                    "budget.warning",
                    {
                        "project_id": project_id,
                        "session_id": record.session_id,
                        "used": used,
                        "hard_limit_tokens": hard_limit,
                        "warn_ratio": pool.warn_ratio,
                    },
                )
        elif used < hard_limit * pool.warn_reenable_ratio:
            if pool.warn_raised:
                updates["warn_raised"] = False
                updates["last_warned_at"] = None
                _emit(
                    "budget.warn_reset",
                    {
                        "project_id": project_id,
                        "session_id": record.session_id,
                        "used": used,
                        "hard_limit_tokens": hard_limit,
                    },
                )
        if updates:
            project_store.update(project_id, budget_pool=updates)
        if used > hard_limit:
            _emit(
                "budget.exhausted",
                {
                    "project_id": project_id,
                    "session_id": record.session_id,
                    "used": used,
                    "hard_limit_tokens": hard_limit,
                },
            )

    return hook



# ---------------------------------------------------------------------------
# fork-session（设计 §7；13.7 端点直接调用本函数）
# ---------------------------------------------------------------------------


def _read_session_record(
    project_store: ProjectStore, project_id: str, session_id: str
) -> SessionRecord | None:
    """读取项目会话目录内的 session.json；缺失/损坏返回 None（容错）。"""
    path = project_store.session_dir(project_id, session_id) / "session.json"
    if not path.is_file():
        return None
    try:
        return SessionRecord.model_validate(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError, ValueError):
        return None


def fork_session(
    project_store: ProjectStore,
    *,
    source_session_id: str,
    goal: str | None = None,
    project_id: str | None = None,
    worktree: bool = True,
    budget: int | None = None,
    emit: Callable[[str, dict], None] | None = None,
) -> SessionRecord:
    """从终态会话派生新会话（设计 §7；13.7 端点直接调用，emit=None 时纯库调用）。

    前置校验顺序：源会话存在 → 项目归属 → 状态允许（completed/aborted）→
    血缘 ``fork_depth + 1 <= 5`` → 项目预算硬上限未耗尽（§5.2）。
    派生产物：新 session_id/thread_id、checkpoint 原样复制（源只读不删）、
    transcript 仅 1 条 fork 血缘记录、inherited_changes 只读拷贝、
    空账本 + ``inherited_tokens`` 归因（不双计项目聚合）。
    """
    source: SessionRecord | None = None
    resolved_project_id = project_id or ""
    if resolved_project_id:
        source = _read_session_record(project_store, resolved_project_id, source_session_id)
        if source is None:
            raise ForkConflictError(f"源会话不存在: {source_session_id}")
    else:
        for record in project_store.list():
            candidate = _read_session_record(project_store, record.project_id, source_session_id)
            if candidate is not None:
                source = candidate
                resolved_project_id = record.project_id
                break
        if source is None:
            raise ForkConflictError(f"源会话不存在: {source_session_id}")
    if not resolved_project_id:
        resolved_project_id = source.project_id
    if not resolved_project_id:
        raise ForkConflictError("fork 是项目层专属能力：源会话无项目归属")
    if source.status == "active":
        raise ForkConflictError(
            f"运行中会话禁止派生（status={source.status}），请先 cancel 至 aborted 再派生"
        )
    if source.fork_depth + 1 > 5:
        raise ForkConflictError(f"fork 血缘深度超限（{source.fork_depth + 1} > 5）")
    if project_store.is_budget_exhausted(resolved_project_id):
        raise BudgetPoolExhaustedError(
            f"项目 {resolved_project_id} 预算硬上限已耗尽，请先解锁再派生"
        )

    project = project_store.get(resolved_project_id)
    main_workspace = Path(project.workspaces[0])
    new_sid = uuid.uuid4().hex[:12]
    new_thread = uuid.uuid4().hex
    new_budget = budget if budget is not None else source.budget

    session_workspace = main_workspace
    worktree_enabled = bool(worktree)
    if worktree_enabled:
        manager = WorktreeManager(main_workspace)
        manager.ensure_repo()
        manager.session_for(f"fork/{new_sid}")
        session_workspace = main_workspace / ".agent-cluster" / "worktrees" / "fork" / new_sid

    # checkpoint 原样复制（含 writes 中途写；源文件只读不删）
    src_checkpoint = (
        project_store.session_dir(resolved_project_id, source.session_id)
        / "checkpoints"
        / f"{source.thread_id}.json"
    )
    dst_checkpoint = (
        project_store.session_dir(resolved_project_id, new_sid) / "checkpoints" / f"{new_thread}.json"
    )
    if src_checkpoint.is_file():
        dst_checkpoint.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src_checkpoint, dst_checkpoint)

    source_changes = ChangeHistory(source.workspace).list() if source.workspace else []
    new_record = SessionRecord(
        session_id=new_sid,
        thread_id=new_thread,
        goal=goal if goal is not None else source.goal,
        flow=source.flow,
        model=source.model,
        workspace=str(session_workspace),
        status="active",
        transcript=[QARecord(question="fork", answer=source.session_id, source="fork")],
        token_ledger=TokenLedger(budget=new_budget),
        budget=new_budget,
        rework_limit=source.rework_limit,
        project_id=resolved_project_id,
        parent_session_id=source.session_id,
        fork_depth=source.fork_depth + 1,
        inherited_tokens=source.token_ledger.total(),
        inherited_changes=[item.model_copy(deep=True) for item in source_changes],
        metadata=dict(source.metadata),
    )
    session_store = project_store.session_store(resolved_project_id, new_sid)
    session_store.record = new_record
    session_store.save()
    # 新会话变更历史从 v1 重新计（fork 标记即 v1）
    ChangeHistory(session_workspace).record(text=f"fork from {source.session_id}")
    project_store.index_session(
        resolved_project_id,
        SessionIndexEntry(
            session_id=new_sid,
            goal=new_record.goal,
            status=new_record.status,
            workspace=new_record.workspace,
            worktree=worktree_enabled,
        ),
    )

    def _emit_event(name: str, payload: dict[str, Any]) -> None:
        if emit is None:
            return
        try:
            emit(name, payload)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[fork] 事件发送失败：%s", exc)

    _emit_event(
        "session.forked",
        {
            "project_id": resolved_project_id,
            "session_id": source.session_id,
            "forked_session_id": new_sid,
        },
    )
    _emit_event(
        "session.start",
        {
            "project_id": resolved_project_id,
            "session_id": new_sid,
            "parent_session_id": source.session_id,
            "fork_depth": new_record.fork_depth,
            "workspace": new_record.workspace,
            "worktree": worktree_enabled,
        },
    )
    return new_record
