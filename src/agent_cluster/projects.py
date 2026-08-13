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

- 本任务边界（13.1）：v0.5 旧会话迁移（13.2）、预算预警判定（13.3）、
  fork（13.4）与 serve 端点（13.7）均不在此实现。
"""

from __future__ import annotations

import json
import os
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from agent_cluster.memory import MemoryStore
from agent_cluster.session import DEFAULT_REWORK_LIMIT, SessionRecord, SessionStore

__all__ = [
    "DEFAULT_FLOW",
    "BudgetUnlockRecord",
    "BudgetPoolRecord",
    "GatePolicyConfig",
    "SessionIndexEntry",
    "ProjectRecord",
    "ProjectStore",
]

# 新建会话默认流程（与 server.py 现值一致；server.py 的引用统一在 13.7 切换到本处）。
DEFAULT_FLOW = "examples/flows/build-product.yaml"


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
    """项目层存储（权威数据源；默认根 ~/.agent-cluster/projects/）。"""

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
    ) -> ProjectRecord:
        """创建项目（§4 v0.5 旧会话迁移由 Task 13.2 挂接，本任务不调用）。"""
        # TODO(13.2): workspace 含 v0.5 session.json 时执行 §4 旧会话迁移。
        project = ProjectRecord(
            project_id=uuid.uuid4().hex[:12],
            name=name,
            description=description,
            workspaces=[str(Path(workspace).expanduser().resolve())],
            default_flow=default_flow,
            metadata=metadata or {},
        )
        with _project_lock(project.project_id):
            self._save(project)
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
    ) -> BudgetUnlockRecord:
        """自服务解锁（granted 路径）：hard_limit_tokens += additional_tokens + append-only 审计。"""
        if additional_tokens <= 0:
            raise ValueError("additional_tokens 必须大于 0")
        with _project_lock(project_id):
            record = self.get(project_id)
            if record.budget_pool.unlock_requires_approval:
                # TODO(13.3): 预算判定任务接入 pending 审批分支。
                raise NotImplementedError("unlock_requires_approval 的 pending 路径由 Task 13.3 实现")
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
