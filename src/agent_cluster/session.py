"""会话层（v0.3）：文件检查点 + 会话存储 + TokenLedger + 会话驱动。

- ``FileCheckpointer``：JSON 文件检查点（``<workspace>/.agent-cluster/checkpoints/``），
  断点续跑、跨进程、损坏容错、轮换（每线程最多保留最近 N 条）。
- ``TokenLedger``：token 计量（预算 / 剩余 / 超限 / 按角色 / 按阶段 / 预估准确率）。
- ``QARecord`` / ``GateDecisionRecord`` / ``PhaseStatus`` / ``SessionRecord``：
  会话状态（goal/flow/model/workspace/问答 transcript/门决策与返工计数/阶段状态）。
- ``SessionStore``：``<workspace>/.agent-cluster/session.json`` 原子读写。
- ``SessionDriver``：`build` 会话驱动——start → run → 挂起交互 → resume；
  事件/交互回调可注入（Web 面板预留接口）。交互协议：门审批 accept/reject/
  response <内容>/edit <内容>；命令 /status /budget /skip /abort；自由文本
  回答 PM 澄清问题。token 预算超限与返工上限均升级人工（继续/缩减/结束）。
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import subprocess
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator, Literal, Sequence

import yaml
from langgraph.checkpoint.base import (
    BaseCheckpointSaver,
    Checkpoint,
    CheckpointMetadata,
    CheckpointTuple,
    get_checkpoint_id,
    get_checkpoint_metadata,
)
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from pydantic import BaseModel, ConfigDict, Field

from agent_cluster.changes import ChangeHistory
from agent_cluster.gates import approval_pending, make_gate_handler, resolve_auto_response
from agent_cluster.mcp_client import StdioMCPClient, parse_server_command, register_mcp_resource_tool, register_mcp_tools
from agent_cluster.meetings import MeetingHost, make_meeting_handler
from agent_cluster.models import (
    ActionRequest,
    ApprovalRecord,
    ClusterState,
    Event,
    GateKind,
    HumanResponse,
    Iteration,
    ModelConfig,
    Project,
    TaskStatus,
    TokenUsage,
)
from agent_cluster.roles import RoleRegistry
from agent_cluster.runtime import AgentRuntime, make_agent_handler
from agent_cluster.skills import SkillCatalog, SkillLoader
from agent_cluster.tokens import estimate_tokens
from agent_cluster.tools import ToolSession, build_default_tools, load_agents_md
from agent_cluster.workflow import NodeContext, NodeHandler, WorkflowEngine

__all__ = [
    "DEFAULT_TOKEN_BUDGET",
    "DEFAULT_REWORK_LIMIT",
    "PHASE_BUDGET_RATIOS",
    "FileCheckpointer",
    "TokenLedgerEntry",
    "TokenLedger",
    "QARecord",
    "GateDecisionRecord",
    "PhaseStatus",
    "SessionRecord",
    "SessionStore",
    "SessionDriver",
    "BuildResult",
    "DEFAULT_ASK_DEFAULT",
]

# v0.3 决策：token 制规划与计量（不按时间）
DEFAULT_TOKEN_BUDGET = 500_000
DEFAULT_REWORK_LIMIT = 3
# 阶段预算比例（需求/设计/开发/测试/文档+交付 = 10/15/50/15/10）
PHASE_BUDGET_RATIOS: dict[str, float] = {
    "requirements": 0.10,
    "design": 0.15,
    "develop": 0.50,
    "testing": 0.15,
    "docs": 0.05,
    "delivery": 0.05,
}
# 非交互/跳过时的缺省澄清答案（留痕）
DEFAULT_ASK_DEFAULT = "[自动] 未提供人工输入，按 PM 缺省判断继续。"


# ---------------------------------------------------------------------------
# 序列化辅助（JsonPlusSerializer + base64）
# ---------------------------------------------------------------------------


def _serde_encode(serde: Any, obj: Any) -> dict[str, str]:
    """把对象序列化为 {type, data_b64} 记录（msgpack/json/pickle）。"""
    type_str, data = serde.dumps_typed(obj)
    return {"type": type_str, "data": base64.b64encode(data).decode("ascii")}


def _serde_decode(serde: Any, record: dict[str, str]) -> Any:
    """从 {type, data_b64} 记录反序列化。"""
    return serde.loads_typed((record["type"], base64.b64decode(record["data"])))


# ---------------------------------------------------------------------------
# 文件检查点（断点续跑）
# ---------------------------------------------------------------------------


class FileCheckpointer(BaseCheckpointSaver):
    """JSON 文件检查点：每线程一个文件，含检查点记录与独立的中途写（轮换、损坏容错）。

    - 存储：``<root>/<sanitized_thread_id>.json``，结构
      ``{"checkpoints": [...], "writes": {checkpoint_id: [[task_id, channel, serde]]}}``；
      ``put_writes``（interrupt 等中途写）可能先于 ``put`` 到达，因此写与检查点分离存储。
    - 写入原子化（临时文件 + os.replace）并经线程锁串行（aput/put_writes 走 to_thread）。
    - ``get_tuple``：指定 checkpoint_id 精确匹配，否则取最新一条；损坏记录跳过。
    """

    MAX_RECORDS = 5
    # 已淘汰检查点 id 的保留上限（仅用于判定哪些中途写可安全清理，不影响恢复）
    MAX_REMOVED = 64

    def __init__(
        self,
        root: str | Path,
        *,
        serde: JsonPlusSerializer | None = None,
    ) -> None:
        super().__init__(
            serde=serde
            or JsonPlusSerializer(
                allowed_msgpack_modules={("agent_cluster.models", name) for name in _MODEL_NAMES()}
            )
        )
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        # 写锁：aput/put_writes 经 to_thread 并发执行时串行化读改写，避免 Windows 文件占用
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # 内部文件读写
    # ------------------------------------------------------------------

    def _file_for(self, thread_id: str) -> Path:
        safe = re.sub(r"[^A-Za-z0-9._-]", "_", thread_id) or "default"
        return self.root / f"{safe}.json"

    def _load_state(self, path: Path) -> dict[str, Any]:
        """读取检查点状态 {checkpoints, writes}；文件缺失/损坏返回空状态（容错）。"""
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            return {"checkpoints": [], "writes": {}}
        if not isinstance(data, dict):
            return {"checkpoints": [], "writes": {}, "removed": []}
        checkpoints = [record for record in data.get("checkpoints") or [] if isinstance(record, dict)]
        writes = data.get("writes") or {}
        if not isinstance(writes, dict):
            writes = {}
        removed = data.get("removed") or []
        if not isinstance(removed, list):
            removed = []
        return {"checkpoints": checkpoints, "writes": writes, "removed": removed}

    def _write_state(self, path: Path, state: dict[str, Any]) -> None:
        """原子写入检查点状态（调用方需持锁）。"""
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)

    # ------------------------------------------------------------------
    # BaseCheckpointSaver 接口
    # ------------------------------------------------------------------

    def put(
        self,
        config: dict[str, Any],
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: Any,
    ) -> dict[str, Any]:
        thread_id = config["configurable"]["thread_id"]
        checkpoint_ns = config["configurable"].get("checkpoint_ns", "")
        checkpoint_id = checkpoint["id"]
        path = self._file_for(thread_id)
        with self._lock:
            state = self._load_state(path)
            records = state["checkpoints"]
            records = [
                record
                for record in records
                if not (
                    record.get("checkpoint_id") == checkpoint_id
                    and record.get("checkpoint_ns") == checkpoint_ns
                )
            ]
            records.append(
                {
                    "checkpoint_ns": checkpoint_ns,
                    "checkpoint_id": checkpoint_id,
                    "checkpoint": _serde_encode(self.serde, checkpoint),
                    "metadata": _serde_encode(self.serde, get_checkpoint_metadata(config, metadata)),
                    "parent_checkpoint_id": config["configurable"].get("checkpoint_id"),
                }
            )
            # 轮换：每线程只保留最近 MAX_RECORDS 条；只清理"已被淘汰记录"的中途写。
            # put_writes（interrupt 等）可能先于对应 put 到达（挂起检查点尚未入库），
            # 因此不能按"是否在 kept_ids"过滤——否则 __interrupt__ 写会被误删。
            removed = list(state.get("removed") or [])
            if len(records) > self.MAX_RECORDS:
                dropped = records[: len(records) - self.MAX_RECORDS]
                removed.extend(record["checkpoint_id"] for record in dropped)
                removed = removed[-self.MAX_REMOVED :]
                records = records[-self.MAX_RECORDS :]
            removed_set = set(removed)
            state["checkpoints"] = records
            state["writes"] = {
                cid: entry for cid, entry in state["writes"].items() if cid not in removed_set
            }
            state["removed"] = removed
            self._write_state(path, state)
        return {
            "configurable": {
                "thread_id": thread_id,
                "checkpoint_ns": checkpoint_ns,
                "checkpoint_id": checkpoint_id,
            }
        }

    def put_writes(
        self,
        config: dict[str, Any],
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        thread_id = config["configurable"]["thread_id"]
        checkpoint_id = str(config["configurable"].get("checkpoint_id") or "")
        path = self._file_for(thread_id)
        with self._lock:
            state = self._load_state(path)
            pending = state["writes"].setdefault(checkpoint_id, [])
            for channel, value in writes:
                pending.append([task_id, channel, _serde_encode(self.serde, value)])
            self._write_state(path, state)

    def get_tuple(self, config: dict[str, Any]) -> CheckpointTuple | None:
        thread_id = config["configurable"]["thread_id"]
        checkpoint_ns = config["configurable"].get("checkpoint_ns", "")
        checkpoint_id = get_checkpoint_id(config)
        state = self._load_state(self._file_for(thread_id))
        candidates = [record for record in state["checkpoints"] if record.get("checkpoint_ns") == checkpoint_ns]
        if checkpoint_id:
            candidates = [record for record in candidates if record.get("checkpoint_id") == checkpoint_id]
            if not candidates:
                return None
            record = candidates[0]
        else:
            if not candidates:
                return None
            record = candidates[-1]
        return self._to_tuple(thread_id, checkpoint_ns, record, state["writes"])

    def list(
        self,
        config: dict[str, Any] | None,
        *,
        filter: dict[str, Any] | None = None,
        before: dict[str, Any] | None = None,
        limit: int | None = None,
    ) -> Iterator[CheckpointTuple]:
        if config is None:
            for path in sorted(self.root.glob("*.json")):
                thread_id = path.stem
                state = self._load_state(path)
                for record in reversed(state["checkpoints"]):
                    checkpoint_ns = str(record.get("checkpoint_ns") or "")
                    if before and (before_cid := get_checkpoint_id(before)) and record.get("checkpoint_id", "") >= before_cid:
                        continue
                    if filter:
                        try:
                            metadata = _serde_decode(self.serde, record["metadata"])
                        except Exception:  # noqa: BLE001 —— 损坏记录跳过
                            continue
                        if not all(metadata.get(key) == value for key, value in filter.items()):
                            continue
                    yield self._to_tuple(thread_id, checkpoint_ns, record, state["writes"])
                    if limit is not None:
                        limit -= 1
                        if limit <= 0:
                            return
            return
        thread_id = config["configurable"]["thread_id"]
        checkpoint_ns = config["configurable"].get("checkpoint_ns", "")
        state = self._load_state(self._file_for(thread_id))
        for record in reversed(state["checkpoints"]):
            if record.get("checkpoint_ns") != checkpoint_ns:
                continue
            if before and (before_cid := get_checkpoint_id(before)) and record.get("checkpoint_id", "") >= before_cid:
                continue
            if filter:
                try:
                    metadata = _serde_decode(self.serde, record["metadata"])
                except Exception:  # noqa: BLE001 —— 损坏记录跳过
                    continue
                if not all(metadata.get(key) == value for key, value in filter.items()):
                    continue
            yield self._to_tuple(thread_id, checkpoint_ns, record, state["writes"])
            if limit is not None:
                limit -= 1
                if limit <= 0:
                    return

    async def aget_tuple(self, config: dict[str, Any]) -> CheckpointTuple | None:
        """异步取检查点（委托同步实现，经 to_thread 避免阻塞事件循环）。"""
        return await asyncio.to_thread(self.get_tuple, config)

    async def aput(
        self,
        config: dict[str, Any],
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: Any,
    ) -> dict[str, Any]:
        """异步保存检查点。"""
        return await asyncio.to_thread(self.put, config, checkpoint, metadata, new_versions)

    async def aput_writes(
        self,
        config: dict[str, Any],
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        """异步保存中间写。"""
        await asyncio.to_thread(self.put_writes, config, writes, task_id, task_path)

    async def alist(
        self,
        config: dict[str, Any] | None,
        *,
        filter: dict[str, Any] | None = None,
        before: dict[str, Any] | None = None,
        limit: int | None = None,
    ) -> Any:
        """异步列出检查点（同步 list 是生成器，逐项转异步）。"""

        def _collect() -> list[CheckpointTuple]:
            return list(self.list(config, filter=filter, before=before, limit=limit))

        return await asyncio.to_thread(_collect)

    def _to_tuple(
        self,
        thread_id: str,
        checkpoint_ns: str,
        record: dict[str, Any],
        writes_store: dict[str, Any] | None = None,
    ) -> CheckpointTuple | None:
        """把一条记录还原为 CheckpointTuple；损坏记录返回 None（容错跳过）。"""
        try:
            checkpoint = _serde_decode(self.serde, record["checkpoint"])
            metadata = _serde_decode(self.serde, record["metadata"])
            checkpoint_id = str(record.get("checkpoint_id") or checkpoint.get("id") or "")
            raw_writes = (writes_store or {}).get(checkpoint_id) or []
            pending_writes = [
                (item[0], item[1], _serde_decode(self.serde, item[2]))
                for item in raw_writes
                if isinstance(item, list) and len(item) == 3
            ]
        except Exception:  # noqa: BLE001 —— 损坏记录跳过
            return None
        parent_checkpoint_id = record.get("parent_checkpoint_id")
        return CheckpointTuple(
            config={
                "configurable": {
                    "thread_id": thread_id,
                    "checkpoint_ns": checkpoint_ns,
                    "checkpoint_id": checkpoint_id,
                }
            },
            checkpoint=checkpoint,
            metadata=metadata,
            pending_writes=pending_writes,
            parent_config=(
                {
                    "configurable": {
                        "thread_id": thread_id,
                        "checkpoint_ns": checkpoint_ns,
                        "checkpoint_id": parent_checkpoint_id,
                    }
                }
                if parent_checkpoint_id
                else None
            ),
        )


def _MODEL_NAMES() -> set[str]:
    """枚举 agent_cluster.models 中参与状态序列化的公开类名（与 cli 同源逻辑）。"""
    import agent_cluster.models as models
    from pydantic import BaseModel as _BM
    from enum import StrEnum as _SE

    names: set[str] = set()
    for name, obj in vars(models).items():
        if name.startswith("_") or getattr(obj, "__module__", "") != models.__name__:
            continue
        if isinstance(obj, type) and (issubclass(obj, _BM) or issubclass(obj, _SE)):
            names.add(name)
    return names


# ---------------------------------------------------------------------------
# Token 计量
# ---------------------------------------------------------------------------


class TokenLedgerEntry(BaseModel):
    """一次模型调用/工具执行的 token 用量记录。"""

    model_config = ConfigDict(extra="ignore")

    ts: datetime = Field(default_factory=lambda: datetime.now(timezone.utc), description="记录时间")
    role: str = Field(default="", description="岗位 id")
    phase: str = Field(default="", description="阶段名")
    model: str = Field(default="", description="模型名")
    prompt_tokens: int = Field(default=0, ge=0)
    completion_tokens: int = Field(default=0, ge=0)
    total_tokens: int = Field(default=0, ge=0)
    estimated: bool = Field(default=False, description="是否估算值")
    estimated_total: int | None = Field(default=None, description="同次调用估算总 token（供准确率）")
    source: str = Field(default="model_call", description="来源：model_call / tool / artifact")


class TokenLedger(BaseModel):
    """token 账本：预算 / 消耗 / 剩余 / 超限 / 按角色 / 按阶段 / 预估准确率。"""

    model_config = ConfigDict(extra="ignore")

    budget: int = Field(default=DEFAULT_TOKEN_BUDGET, ge=0, description="全局 token 预算")
    entries: list[TokenLedgerEntry] = Field(default_factory=list, description="用量记录（append-only）")

    def record(
        self,
        *,
        role: str = "",
        phase: str = "",
        usage: TokenUsage | None = None,
        source: str = "model_call",
    ) -> TokenLedgerEntry:
        """记录一次用量（usage 为空时记 0，用于工具执行占位）。"""
        entry = TokenLedgerEntry(
            role=role,
            phase=phase,
            model=usage.model if usage is not None else "",
            prompt_tokens=usage.prompt_tokens if usage is not None else 0,
            completion_tokens=usage.completion_tokens if usage is not None else 0,
            total_tokens=usage.total_tokens if usage is not None else 0,
            estimated=usage.estimated if usage is not None else True,
            estimated_total=usage.estimated_total if usage is not None else None,
            source=source,
        )
        self.entries.append(entry)
        return entry

    def total(self) -> int:
        """累计消耗 token。"""
        return sum(entry.total_tokens for entry in self.entries)

    def remaining(self) -> int:
        """剩余预算（可为负）。"""
        return self.budget - self.total()

    def over_budget(self) -> bool:
        """全局是否超预算。"""
        return self.budget > 0 and self.total() > self.budget

    def phase_used(self, phase: str) -> int:
        """某阶段累计消耗。"""
        return sum(entry.total_tokens for entry in self.entries if entry.phase == phase)

    def phase_budget(self, phase: str) -> int:
        """某阶段预算 = 全局预算 × 比例（未配置比例返回 0 = 不限）。"""
        ratio = PHASE_BUDGET_RATIOS.get(phase)
        if ratio is None:
            return 0
        return int(self.budget * ratio)

    def phase_over_budget(self, phase: str) -> bool:
        """某阶段是否超预算（阶段预算为 0 表示不限）。"""
        budget = self.phase_budget(phase)
        return budget > 0 and self.phase_used(phase) > budget

    def by_role(self) -> dict[str, int]:
        """按岗位累计消耗。"""
        result: dict[str, int] = {}
        for entry in self.entries:
            result[entry.role] = result.get(entry.role, 0) + entry.total_tokens
        return result

    def by_phase(self) -> dict[str, int]:
        """按阶段累计消耗。"""
        result: dict[str, int] = {}
        for entry in self.entries:
            result[entry.phase] = result.get(entry.phase, 0) + entry.total_tokens
        return result

    def estimate_accuracy(self) -> float | None:
        """预估 vs 实际准确率：对带 estimated_total 的真实调用取 1-|est-real|/real 的均值。"""
        ratios: list[float] = []
        for entry in self.entries:
            if entry.estimated or entry.estimated_total is None or entry.total_tokens <= 0:
                continue
            ratios.append(
                max(0.0, 1.0 - abs(entry.estimated_total - entry.total_tokens) / entry.total_tokens)
            )
        if not ratios:
            return None
        return sum(ratios) / len(ratios)

    def summary(self) -> dict[str, Any]:
        """预算总览（DELIVERY.md 计量表用）。"""
        return {
            "budget": self.budget,
            "used": self.total(),
            "remaining": self.remaining(),
            "over_budget": self.over_budget(),
            "by_role": self.by_role(),
            "by_phase": self.by_phase(),
            "estimate_accuracy": self.estimate_accuracy(),
        }



# ---------------------------------------------------------------------------
# 会话状态模型
# ---------------------------------------------------------------------------


class QARecord(BaseModel):
    """一次 PM 澄清问答记录。"""

    model_config = ConfigDict(extra="ignore")

    id: str = Field(default_factory=lambda: uuid.uuid4().hex, description="记录 id")
    ts: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    question: str = Field(default="")
    answer: str = Field(default="")
    source: str = Field(default="human", description="回答来源：human / script / auto")
    node: str = Field(default="", description="提问节点 id")


class GateDecisionRecord(BaseModel):
    """单个审批门的决策与返工计数。"""

    model_config = ConfigDict(extra="ignore")

    node: str = Field(description="门节点 id")
    kind: str = Field(default="", description="门类别 value")
    attempts: int = Field(default=0, description="该门已尝试次数")
    rejections: int = Field(default=0, description="返工次数（reject 累计）")
    last_decision: str = Field(default="", description="最近一次结论")
    escalated: bool = Field(default=False, description="是否已升级人工（返工上限）")


class PhaseStatus(BaseModel):
    """阶段状态与 token 消耗。"""

    model_config = ConfigDict(extra="ignore")

    name: str = Field(description="阶段名")
    status: Literal["pending", "in_progress", "done", "skipped"] = Field(
        default="pending", description="阶段状态"
    )
    tokens_used: int = Field(default=0, ge=0, description="阶段已消耗 token")


class SessionRecord(BaseModel):
    """会话状态（持久化到 session.json）。"""

    model_config = ConfigDict(extra="ignore")

    session_id: str = Field(description="会话 id")
    thread_id: str = Field(description="LangGraph 线程 id")
    goal: str = Field(default="", description="用户需求目标")
    flow: str = Field(default="", description="流程 YAML 路径")
    model: str = Field(default="codex", description="模型后端")
    workspace: str = Field(default="", description="工作区绝对路径")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    status: Literal["active", "aborted", "completed"] = Field(default="active")
    transcript: list[QARecord] = Field(default_factory=list, description="问答 transcript")
    gate_decisions: list[GateDecisionRecord] = Field(default_factory=list, description="门决策与返工计数")
    phases: dict[str, PhaseStatus] = Field(default_factory=dict, description="阶段状态")
    token_ledger: TokenLedger = Field(default_factory=TokenLedger, description="token 账本")
    budget: int = Field(default=DEFAULT_TOKEN_BUDGET, ge=0, description="全局预算（覆盖 ledger.budget）")
    rework_limit: int = Field(default=DEFAULT_REWORK_LIMIT, gt=0, description="返工上限（超过升级人工）")


# ---------------------------------------------------------------------------
# 会话存储
# ---------------------------------------------------------------------------


class SessionStore:
    """会话状态读写：``<workspace>/.agent-cluster/session.json``。

    - 目录内自带 ``.gitignore``（``*``），保证会话/检查点不入库。
    - 文件损坏/缺失时返回全新会话（不抛异常）。
    """

    def __init__(
        self,
        workspace_root: str | Path,
        *,
        session_id: str | None = None,
    ) -> None:
        self.workspace_root = Path(workspace_root).expanduser().resolve()
        self.workspace_root.mkdir(parents=True, exist_ok=True)
        self.dir = self.workspace_root / ".agent-cluster"
        self.dir.mkdir(parents=True, exist_ok=True)
        (self.dir / ".gitignore").write_text("*\n", encoding="utf-8")
        self.path = self.dir / "session.json"
        self.record = self._load_or_new(session_id)

    def _load_or_new(self, session_id: str | None) -> SessionRecord:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            record = SessionRecord.model_validate(data)
            if session_id and record.session_id != session_id:
                raise ValueError("session_id 不匹配")
            return record
        except (OSError, json.JSONDecodeError, UnicodeDecodeError, ValueError):
            thread_id = uuid.uuid4().hex
            return SessionRecord(
                session_id=session_id or uuid.uuid4().hex,
                thread_id=thread_id,
            )

    def save(self) -> None:
        """原子写入会话状态（临时文件 + os.replace）。"""
        self.record.updated_at = datetime.now(timezone.utc)
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(self.record.model_dump_json(indent=2), encoding="utf-8")
        os.replace(tmp, self.path)

    def update(self, **fields: Any) -> SessionRecord:
        """更新会话字段并保存。"""
        self.record = self.record.model_copy(update=fields)
        self.save()
        return self.record



# ---------------------------------------------------------------------------
# 会话驱动（build）
# ---------------------------------------------------------------------------


@dataclass
class BuildResult:
    """一次 build 会话的运行结果。"""

    session_id: str
    thread_id: str
    goal: str
    workspace: str
    events: list[Event] = field(default_factory=list)
    state: ClusterState | None = None
    decisions: list[ApprovalRecord] = field(default_factory=list)
    suspended_count: int = 0
    exit_code: int = 0
    delivery: dict | None = None
    token_summary: dict | None = None


class SessionDriver:
    """`build` 会话驱动：编译流程 + 交互循环 + token 计量 + 交付组装。

    - ``usage_hook`` 注入 AgentRuntime：每次模型调用按（role, phase）记账。
    - 挂起交互：门审批 / 危险工具 / ask_user 自由文本；支持 /status /budget
      /skip /abort；预算超限与返工上限升级人工（继续/缩减/结束）。
    - ``print_fn`` / ``prompt_fn`` / ``event_printer`` 可注入（Web 预留接口）。
    """

    def __init__(
        self,
        *,
        workspace: str | Path,
        goal: str,
        flow: str | Path,
        model: str = "codex",
        budget: int | None = None,
        rework_limit: int | None = None,
        yes: bool = False,
        deterministic: bool = False,
        resume: bool = False,
        qa_script: Sequence[str] | None = None,
        tool_script: Sequence[dict] | None = None,
        role_tool_scripts: dict[str, list[dict]] | None = None,
        skills_root: str | None = None,
        mcp_servers: Sequence[str] | None = None,
        max_rounds: int | None = None,
        prompt_fn: Callable[[str], str] | None = None,
        print_fn: Callable[[str], None] | None = None,
        event_printer: Callable[[Event], None] | None = None,
        phase_map: dict[str, str] | None = None,
        plugin_manager: Any | None = None,
        sandbox: Any | None = None,
    ) -> None:
        self.workspace = Path(workspace).expanduser().resolve()
        self.goal = goal.strip()
        self.flow = str(flow)
        self.model = model
        self.yes = bool(yes)
        self.deterministic = bool(deterministic)
        self.resume = bool(resume)
        self.qa_script = list(qa_script or [])
        self.tool_script = list(tool_script or [])
        self.role_tool_scripts = {k: list(v) for k, v in (role_tool_scripts or {}).items()}
        self.skills_root = skills_root
        self.mcp_servers = list(mcp_servers or [])
        self.max_rounds = max_rounds
        self.prompt_fn = prompt_fn if prompt_fn is not None else input
        self.print_fn = print_fn if print_fn is not None else print
        self.event_printer = event_printer
        self.plugin_manager = plugin_manager
        self.sandbox = sandbox
        self.phase_map = dict(
            phase_map
            or {
                "start": "start",
                "kickoff": "requirements",
                "requirements": "requirements",
                "requirement_review": "requirements",
                "requirement_gate": "requirements",
                "design": "design",
                "design_review": "design",
                "design_gate": "design",
                "develop": "develop",
                "code_review": "develop",
                "test": "testing",
                "iteration_gate": "testing",
                "docs": "docs",
                "devops": "delivery",
                "release_gate": "delivery",
                "delivery": "delivery",
            }
        )
        self.store = SessionStore(self.workspace)
        record = self.store.record
        if self.resume:
            if record.status != "active":
                raise ValueError(
                    f"工作区 {self.workspace} 没有可恢复的进行中会话（status={record.status}）；"
                    "请勿对已完成会话使用 --resume"
                )
            self.print_fn(f"[恢复] 会话 {record.session_id}（线程 {record.thread_id}）")
        elif record.status == "active" and (record.workspace or record.goal):
            raise ValueError(
                f"工作区 {self.workspace} 存在进行中会话（{record.session_id}，目标：{record.goal or '(空)'}）；"
                "请使用 --resume 续跑，或清理 .agent-cluster/session.json 后重新开始"
            )
        # 初始化/更新会话字段
        effective_budget = budget if budget is not None else (record.budget or DEFAULT_TOKEN_BUDGET)
        effective_rework = rework_limit if rework_limit is not None else (record.rework_limit or DEFAULT_REWORK_LIMIT)
        self.store.update(
            goal=self.goal or record.goal,
            flow=self.flow,
            model=self.model,
            workspace=str(self.workspace),
            budget=effective_budget,
            rework_limit=effective_rework,
        )
        self.store.record.token_ledger.budget = effective_budget
        self.store.save()

        # 需求变更基建（v0.5 T12.4：实时打断 + 版本化 + 回滚）
        self.change_history = ChangeHistory(self.workspace)
        self._injected_changes: list[str] = []
        self._change_lock = threading.Lock()

        self.current_node = ""
        self.current_phase = "start"
        self._graph: Any = None
        self._thread_id: str = record.thread_id
        self._tool_session: ToolSession | None = None

    # ------------------------------------------------------------------
    # token 记账 / 事件跟踪 / 状态文本
    # ------------------------------------------------------------------

    def usage_hook(self, role: str, usage: TokenUsage) -> None:
        """AgentRuntime usage 钩子：按（role, phase）写入 TokenLedger。"""
        self.store.record.token_ledger.record(
            role=role, phase=self.current_phase, usage=usage, source="model_call"
        )
        phase = self.store.record.phases.setdefault(
            self.current_phase, PhaseStatus(name=self.current_phase)
        )
        phase.tokens_used = self.store.record.token_ledger.phase_used(self.current_phase)
        self.store.save()

    # ------------------------------------------------------------------
    # 需求变更（v0.5 T12.4：实时打断 + 版本化 + 回滚）
    # ------------------------------------------------------------------

    def inject_change(self, text: str) -> bool:
        """线程安全地注入一条需求变更（在下一个门/挂起点生效并驱动返工重规划）。"""
        text = str(text).strip()
        if not text:
            return False
        with self._change_lock:
            self._injected_changes.append(text)
        return True

    def _drain_change(self) -> str | None:
        """取出一条待处理的变更（线程安全）。"""
        with self._change_lock:
            if not self._injected_changes:
                return None
            return self._injected_changes.pop(0)

    def _apply_change(self, text: str) -> None:
        """记录变更（快照 + 版本 +1）并发布事件；不抛异常。"""
        try:
            record = self.change_history.record(
                text=text, node=self.current_node or "", phase=self.current_phase
            )
            self.print_fn(f"[需求变更] v{record.version} 已记录：{text}")
        except Exception as exc:  # noqa: BLE001 —— 变更记录失败不阻断流程
            self.print_fn(f"[需求变更] 记录失败：{exc}")
            record = None
        if record is not None:
            self.on_event(
                Event(
                    id=uuid.uuid4().hex,
                    run_id=self.store.record.thread_id,
                    thread_id=self.store.record.thread_id,
                    type="change.applied",
                    actor="human",
                    payload={"version": record.version, "text": text, "phase": self.current_phase},
                )
            )

    def record_tool_usage(self, total_tokens: int = 0, role: str = "") -> None:
        """工具执行占位记账（可选，用于工具耗时折算；默认不计入模型预算）。"""
        if total_tokens <= 0:
            return
        self.store.record.token_ledger.record(
            role=role, phase=self.current_phase, source="tool", total=total_tokens
        )

    def on_event(self, event: Event) -> None:
        """跟踪节点/阶段/会话状态（事件回调，Web 面板可复用）。"""
        if event.type == "node_start":
            node_id = str(event.payload.get("node_id") or "")
            if node_id:
                self.current_node = node_id
                phase = self.phase_for(node_id)
                self.current_phase = phase
                phase_status = self.store.record.phases.setdefault(
                    phase, PhaseStatus(name=phase)
                )
                if phase_status.status == "pending":
                    phase_status.status = "in_progress"
        elif event.type == "workflow_end":
            self.store.record.status = "completed"
            self.current_phase = "delivery"
        self.store.save()

    def phase_for(self, node_id: str) -> str:
        """节点 → 阶段映射（精确匹配优先，其次前缀匹配）。"""
        if node_id in self.phase_map:
            return self.phase_map[node_id]
        for prefix, phase in self.phase_map.items():
            if prefix and node_id.startswith(prefix):
                return phase
        return "develop"

    def _gate_record(self, node_id: str, kind: str) -> GateDecisionRecord:
        for record in self.store.record.gate_decisions:
            if record.node == node_id:
                record.kind = record.kind or kind
                return record
        record = GateDecisionRecord(node=node_id, kind=kind)
        self.store.record.gate_decisions.append(record)
        return record

    def record_qa(self, question: str, answer: str, source: str) -> None:
        """记录一次澄清问答（transcript）。"""
        self.store.record.transcript.append(
            QARecord(question=question, answer=answer, source=source, node=self.current_node)
        )
        self.store.save()

    def status_text(self) -> str:
        """会话状态摘要（/status）。"""
        record = self.store.record
        ledger = record.token_ledger
        lines = [
            f"会话：{record.session_id} | 线程：{record.thread_id}",
            f"目标：{record.goal or '(未设置)'}",
            f"当前阶段：{self.current_phase}（节点 {self.current_node or '-'}）",
            f"token：已用 {ledger.total()} / 预算 {ledger.budget} / 剩余 {ledger.remaining()}",
            f"阶段消耗：{ {k: v.tokens_used for k, v in record.phases.items()} or '（尚无）' }",
            f"问答记录：{len(record.transcript)} 条 | 门决策：{len(record.gate_decisions)} 条",
        ]
        if self._graph is not None:
            try:
                snapshot = self._graph.get_state(
                    {"configurable": {"thread_id": self._thread_id}}
                )
                state = ClusterState.model_validate(snapshot.values)
                statuses = {}
                for task in state.tasks:
                    statuses[task.status.value] = statuses.get(task.status.value, 0) + 1
                lines.append(f"任务：{len(state.tasks)}（{statuses or '无'}）| 会议：{len(state.meetings)}")
            except Exception:  # noqa: BLE001 —— 状态不可读时省略任务行
                pass
        return "\n".join(lines)

    def budget_text(self) -> str:
        """预算摘要（/budget）。"""
        ledger = self.store.record.token_ledger
        phases = ", ".join(
            f"{phase}={used}/{ledger.phase_budget(phase) or '∞'}"
            for phase, used in sorted(ledger.by_phase().items())
        )
        return (
            f"预算：{ledger.budget} | 已用：{ledger.total()} | 剩余：{ledger.remaining()} | "
            f"超限：{ledger.over_budget()}\n阶段：{phases or '（尚无消耗）'}"
        )



    # ------------------------------------------------------------------
    # 交互
    # ------------------------------------------------------------------

    def _prompt(self, hint: str) -> str:
        """读取一行输入，支持 /status /budget /abort（返回原样命令）。"""
        while True:
            change = self._drain_change()
            if change:
                self._apply_change(change)
                return "__change__"
            raw = str(self.prompt_fn(hint)).strip()
            if not raw:
                continue
            low = raw.lower()
            if low == "/status":
                self.print_fn(self.status_text())
                continue
            if low == "/budget":
                self.print_fn(self.budget_text())
                continue
            return raw

    def decide_response(self, request: ActionRequest) -> HumanResponse | str:
        """按请求类别决定人工响应（返回 "/abort" 表示中止，"/skip" 表示跳过）。"""
        change = self._drain_change()
        if change:
            self._apply_change(change)
            if request.kind != GateKind.HUMAN_INTERACTION:
                # 门/危险工具：以 reject 驱动返工重规划（变更在下一个阶段重跑中生效）
                return HumanResponse(type="reject", args={"reason": f"需求变更：{change}"})
        if self.yes:
            if request.kind == GateKind.HUMAN_INTERACTION:
                # 非交互 --yes：缺省答案并留痕（transcript source=auto）
                question = request.title or request.description
                self.record_qa(question, DEFAULT_ASK_DEFAULT, source="auto")
                return HumanResponse(type="response", args={"text": DEFAULT_ASK_DEFAULT})
            return resolve_auto_response(request, "accept")
        if request.kind == GateKind.HUMAN_INTERACTION:
            return self._ask_user_response(request)
        if request.kind == GateKind.DANGEROUS_TOOL:
            return self._dangerous_response(request)
        return self._gate_response(request)

    def _ask_user_response(self, request: ActionRequest) -> HumanResponse | str:
        """PM 澄清问题：自由文本回答（/skip 缺省，/abort 中止）。"""
        question = request.title or request.description
        if self.deterministic and self.qa_script:
            answer = self.qa_script.pop(0)
            self.record_qa(question, answer, source="script")
            self.print_fn(f"[PM 澄清] {question}\n  → {answer}（脚本化）")
            return HumanResponse(type="response", args={"text": answer})
        hint = f"[PM 澄清] {question}\n（自由文本回答；/skip 采用缺省；/abort 中止）\n> "
        raw = self._prompt(hint)
        if raw == "/abort":
            return "/abort"
        if raw == "__change__":
            answer = DEFAULT_ASK_DEFAULT
            self.print_fn("  （需求变更已记录，当前澄清采用缺省答案）")
        elif raw.lower() == "/skip":
            answer = DEFAULT_ASK_DEFAULT
        else:
            answer = raw
        self.record_qa(question, answer, source="human")
        self.print_fn(f"  → {answer}")
        return HumanResponse(type="response", args={"text": answer})

    def _dangerous_response(self, request: ActionRequest) -> HumanResponse | str:
        """危险工具审批：accept / reject。"""
        hint = (
            f"[危险工具] {request.title}\n  {request.description}\n"
            "请选择 [accept|reject|/status|/budget|/abort]："
        )
        raw = self._prompt(hint)
        if raw == "/abort":
            return "/abort"
        if raw == "__change__":
            return HumanResponse(type="reject", args={"reason": "需求变更：跳过该危险工具"})
        kind = raw.lower()
        if kind.startswith("accept"):
            return HumanResponse(type="accept")
        return HumanResponse(type="reject", args={"reason": raw})

    def _gate_response(self, request: ActionRequest) -> HumanResponse | str:
        """审批门：accept/reject/response/edit + 返工计数。"""
        node_id = self.current_node or request.evidence.get("node") or ""
        record = self._gate_record(node_id, request.kind.value)
        record.attempts += 1
        hint = (
            f"[审批门] {request.title}（{request.kind.value}，风险 {request.risk_level}）\n"
            f"  {request.description}\n"
            "请选择 [accept|reject|response <内容>|edit <内容>|/status|/budget|/skip|/abort]："
        )
        raw = self._prompt(hint)
        if raw == "/abort":
            return "/abort"
        if raw == "__change__":
            record.rejections += 1
            record.last_decision = "reject"
            record.escalated = True
            self.store.save()
            return HumanResponse(type="reject", args={"reason": "需求变更：返工重规划"})
        if raw.lower() == "/skip":
            record.last_decision = "accept"
            self.store.save()
            return HumanResponse(type="accept")
        kind, _, arg = raw.partition(" ")
        if kind == "accept":
            record.last_decision = "accept"
            record.escalated = False
            self.store.save()
            return HumanResponse(type="accept")
        if kind == "reject":
            record.rejections += 1
            record.last_decision = "reject"
            self.store.save()
            return HumanResponse(type="reject")
        if kind in ("response", "edit") and arg.strip():
            record.last_decision = kind
            self.store.save()
            return HumanResponse(type=kind, args={"text": arg.strip()})
        self.print_fn(f"  无效输入：{raw!r}（支持 accept / reject / response <内容> / edit <内容>）")
        return self._gate_response(request)

    # ------------------------------------------------------------------
    # 升级（预算超限 / 返工上限）
    # ------------------------------------------------------------------

    def _escalation(self, request: ActionRequest) -> tuple[str, Any] | None:
        """挂起点升级检查：返回 (类别, 上下文) 或 None。"""
        if request.kind in (GateKind.HUMAN_INTERACTION, GateKind.DANGEROUS_TOOL):
            return None
        ledger = self.store.record.token_ledger
        if ledger.over_budget():
            return ("budget", None)
        if self.current_phase and ledger.phase_over_budget(self.current_phase):
            return ("budget_phase", self.current_phase)
        node_id = self.current_node or ""
        record = self._gate_record(node_id, request.kind.value)
        if record.rejections >= self.store.record.rework_limit and not record.escalated:
            return ("rework", record)
        return None

    def _handle_escalation(self, escalation: tuple[str, Any]) -> HumanResponse | str:
        """处理升级（返回 HumanResponse 或 "end"/"abort"）。"""
        if self.yes:
            # --yes 无人值守：升级（预算超限/返工上限）一律结束保存现状（退出码 3），
            # 避免在非交互模式下卡住等待人工输入。
            self.print_fn("  --yes 无人值守：升级自动结束（保存现状，使用 --resume 继续）")
            return "end"
        kind, context = escalation
        ledger = self.store.record.token_ledger
        if kind in ("budget", "budget_phase"):
            phase_label = f"（阶段 {context}）" if kind == "budget_phase" else ""
            self.print_fn(self.budget_text())
            hint = (
                f"[预算超限{phase_label}] 已用 {ledger.total()} / 预算 {ledger.budget}。\n"
                "选择：more <N>（追加预算继续）/ shrink（缩减范围重跑）/ end（结束保存现状）："
            )
            raw = self._prompt(hint)
            if raw == "/abort":
                return "abort"
            if raw == "__change__":
                return HumanResponse(type="reject", args={"reason": "需求变更：缩减范围重跑"})
            low = raw.lower()
            if low == "end":
                return "end"
            if low == "shrink":
                return HumanResponse(type="reject", args={"reason": "预算超限：缩减范围重跑"})
            if low.startswith("more"):
                parts = raw.split(maxsplit=1)
                try:
                    extra = int(parts[1]) if len(parts) > 1 else DEFAULT_TOKEN_BUDGET
                except ValueError:
                    extra = DEFAULT_TOKEN_BUDGET
                ledger.budget += max(0, extra)
                self.store.record.budget = ledger.budget
                self.store.save()
                self.print_fn(f"  已追加预算 {extra}，新预算 {ledger.budget}")
                return HumanResponse(type="accept", args={"reason": "预算追加后继续"})
            self.print_fn("  无效输入（more <N> / shrink / end）")
            return self._handle_escalation(escalation)
        # rework
        record: GateDecisionRecord = context
        hint = (
            f"[返工上限] 门 {record.node} 已返工 {record.rejections} 次（上限 "
            f"{self.store.record.rework_limit}）。\n"
            "选择：continue（再返工一轮）/ accept（接受当前结果）/ end（结束保存现状）："
        )
        raw = self._prompt(hint)
        if raw == "/abort":
            return "abort"
        low = raw.lower()
        if low == "end":
            return "end"
        if low == "accept":
            record.escalated = False
            record.last_decision = "accept"
            self.store.save()
            return HumanResponse(type="accept")
        if low == "continue":
            record.escalated = True
            record.last_decision = "reject"
            self.store.save()
            return HumanResponse(type="reject", args={"reason": "升级后继续返工"})
        self.print_fn("  无效输入（continue / accept / end）")
        return self._handle_escalation(escalation)



    # ------------------------------------------------------------------
    # 主循环
    # ------------------------------------------------------------------

    async def run(self) -> BuildResult:
        """执行 build 会话：编译 → 运行 → 挂起交互 → resume → 交付组装。"""
        flow_text = Path(self.flow).read_text(encoding="utf-8")
        flow_data = yaml.safe_load(flow_text) or {}
        spec_name = str(flow_data.get("name") or "build-product")
        spec_thread = str(flow_data.get("thread_id") or "")
        thread_id = self.store.record.thread_id or spec_thread or "default"
        self._thread_id = thread_id

        await self._run_plugin_hooks("session_start", thread_id)

        effective_model = (
            "deterministic"
            if self.deterministic
            else (self.model or os.environ.get("DEEPSEEK_MODEL") or None)
        )
        default_model = ModelConfig(model_name=effective_model) if effective_model else None
        runtime = AgentRuntime(
            default_model=default_model,
            tool_script=self.tool_script,
            role_tool_scripts=self.role_tool_scripts,
            usage_hook=self.usage_hook,
        )
        role_registry = RoleRegistry()
        host = MeetingHost()

        catalog = None
        plugin_skills: list = []
        if self.plugin_manager is not None:
            try:
                plugin_skills = self.plugin_manager.list_skills()
            except Exception as exc:  # noqa: BLE001 —— 插件技能失败不阻断会话
                self.print_fn(f"[插件] 技能加载失败：{exc}")
        if self.skills_root or plugin_skills:
            loader = SkillLoader()
            catalog = SkillCatalog()
            skills = loader.list_skills(self.skills_root) if self.skills_root else []
            skills = list(skills) + list(plugin_skills)
            for role in role_registry.list():
                catalog.mount(role, skills)

        tool_session: ToolSession | None = None
        if self.workspace:
            registry = build_default_tools()
            for server_spec in self.mcp_servers:
                server_name, argv = parse_server_command(server_spec)
                mcp_client = StdioMCPClient(server_name, argv)
                await mcp_client.connect()
                await register_mcp_tools(registry, mcp_client, server_name)
                await register_mcp_resource_tool(registry, mcp_client, server_name)
            tool_session = ToolSession(
                self.workspace,
                registry=registry,
                sandbox=self.sandbox,
                agents_md=load_agents_md(self.workspace),
            )
            from agent_cluster.subagent import SubagentBroker, register_subagent_tool

            register_subagent_tool(
                tool_session.registry,
                SubagentBroker(
                    client_factory=lambda role_id="backend": runtime.client_for(role_registry.get(role_id)),
                    usage_hook=runtime.report_usage,
                ),
            )
            self._tool_session = tool_session

        base_handlers = {
            "agent": make_agent_handler(
                runtime,
                role_registry,
                catalog=catalog,
                tool_session=tool_session,
                max_rounds=self.max_rounds,
            ),
            "meeting": make_meeting_handler(host, role_registry),
            "gate": make_gate_handler(auto_mode="ask"),
        }

        def _phase_tracked(node_type: str) -> NodeHandler:
            """包一层节点 handler：执行前同步置位 current_node/current_phase。

            usage_hook 在节点执行期间触发（早于事件被排空），因此阶段归属必须在
            handler 调用前确定，否则全部记入上一阶段（如 start）。
            """
            base = base_handlers[node_type]

            async def wrapped(state: ClusterState, node: Any, ctx: NodeContext) -> Any:
                self.current_node = node.id
                self.current_phase = self.phase_for(node.id)
                phase_status = self.store.record.phases.setdefault(
                    self.current_phase, PhaseStatus(name=self.current_phase)
                )
                if phase_status.status == "pending":
                    phase_status.status = "in_progress"
                return await base(state, node, ctx)

            return wrapped

        engine = WorkflowEngine(
            handlers={
                "agent": _phase_tracked("agent"),
                "meeting": _phase_tracked("meeting"),
                "gate": _phase_tracked("gate"),
            }
        )
        compiled = engine.compile(flow_text)
        project_name = self.workspace.name or spec_name
        initial = {
            "project": Project(id=project_name, name=project_name, vision=self.goal or "build 会话"),
            "iterations": [
                Iteration(
                    id="iter:1",
                    project_id=project_name,
                    number=1,
                    goal=self.goal,
                    status="in_progress",
                    token_budget=self.store.record.budget,
                )
            ],
            "tasks": [],
            "meetings": [],
            "messages": [],
            "decisions": [],
            "gate_payloads": {},
        }
        checkpointer = FileCheckpointer(self.workspace / ".agent-cluster" / "checkpoints")
        graph = compiled.compile_graph(checkpointer=checkpointer)
        self._graph = graph

        events: list[Event] = []
        decisions: list[ApprovalRecord] = []
        suspended_count = 0
        exit_code = 0
        first = True
        request = approval_pending(graph, thread_id) if self.resume else None

        while True:
            if request is None and first:
                stream = compiled.run(
                    initial=initial, thread_id=thread_id, checkpointer=checkpointer
                )
                first = False
            else:
                if request is None:
                    request = approval_pending(graph, thread_id)
                if request is None:
                    raise RuntimeError("流程挂起但未从检查点找到待审批请求")
                self._print_request(request)
                escalation = self._escalation(request)
                if escalation is not None:
                    response = self._handle_escalation(escalation)
                    if response == "end":
                        exit_code = 3
                        break
                    if response == "abort":
                        exit_code = 2
                        break
                else:
                    response = self.decide_response(request)
                    if response == "/abort":
                        exit_code = 2
                        break
                    if response == "/skip":
                        response = HumanResponse(type="accept")
                stream = compiled.resume(thread_id, response, checkpointer=checkpointer)

            iteration_events = [event async for event in stream]
            for event in iteration_events:
                events.append(event)
                self.on_event(event)
                if self.event_printer is not None:
                    self.event_printer(event)

            if not iteration_events or iteration_events[-1].type != "workflow_suspended":
                break
            suspended_count += 1
            request = approval_pending(graph, thread_id)

        state: ClusterState | None = None
        try:
            snapshot = graph.get_state({"configurable": {"thread_id": thread_id}})
            state = ClusterState.model_validate(snapshot.values)
        except Exception:  # noqa: BLE001 —— 状态不可读时保持 None
            state = None
        decisions = list(state.decisions) if state is not None else []

        delivery = None
        token_summary = self.store.record.token_ledger.summary()
        if exit_code == 0 and state is not None:
            delivery = self._assemble_delivery(state, events, thread_id)

        if exit_code == 0 and state is not None:
            failed = [
                task
                for task in state.tasks
                if task.status in (TaskStatus.REVIEW, TaskStatus.BLOCKED)
            ]
            if failed:
                exit_code = 1
                self.print_fn(
                    f"存在验收未通过的岗位任务（{len(failed)} 个），退出码 1"
                )

        if exit_code == 2:
            self.store.update(status="active")
            self.print_fn("已保存检查点（/abort）。使用 --resume 续跑。")
        elif exit_code == 3:
            self.store.update(status="active")
            self.print_fn("已保存现状（升级结束）。使用 --resume 继续。")
        else:
            self.store.update(status="completed")

        await self._run_plugin_hooks("session_end", thread_id)

        return BuildResult(
            session_id=self.store.record.session_id,
            thread_id=thread_id,
            goal=self.goal,
            workspace=str(self.workspace),
            events=events,
            state=state,
            decisions=decisions,
            suspended_count=suspended_count,
            exit_code=exit_code,
            delivery=delivery,
            token_summary=token_summary,
        )



    async def _run_plugin_hooks(self, event: str, thread_id: str) -> None:
        """执行插件生命周期钩子（失败仅打印，不中断会话）。"""
        if self.plugin_manager is None:
            return
        try:
            results = await self.plugin_manager.run_hooks(
                event,
                workspace=str(self.workspace),
                thread_id=thread_id,
                session_id=self.store.record.session_id,
            )
        except Exception as exc:  # noqa: BLE001 —— 钩子失败不阻断会话
            self.print_fn(f"[插件] {event} 钩子执行失败：{exc}")
            return
        for result in results:
            if not result.ok:
                self.print_fn(
                    f"[插件] {result.plugin} {event} 钩子失败（{result.command}）："
                    f"{result.error or result.output[:200]}"
                )


    # ------------------------------------------------------------------
    # 交付组装
    # ------------------------------------------------------------------

    def _assemble_delivery(self, state: ClusterState, events: list[Event], thread_id: str) -> dict:
        """生成 DELIVERY.md（需求→PRD→代码→测试→部署→手册勾连 + token 计量表）并 git 提交。"""
        record = self.store.record
        ledger = record.token_ledger
        artifact_rows: list[str] = []
        artifact_sizes: dict[str, int] = {}
        seen: set[str] = set()
        for task in state.tasks:
            for artifact in task.artifacts:
                if artifact in seen:
                    continue
                seen.add(artifact)
                path = self.workspace / artifact
                tokens = 0
                if path.is_file():
                    try:
                        tokens = estimate_tokens(path.read_text(encoding="utf-8", errors="replace"))
                    except OSError:
                        tokens = -1
                artifact_sizes[artifact] = tokens
                artifact_rows.append(
                    f"| {artifact} | {tokens if tokens >= 0 else '（不可读）'} | {task.tokens_used} | {task.title} |"
                )

        summary = ledger.summary()
        phase_rows = "\n".join(
            f"| {phase} | {used} | {ledger.phase_budget(phase) or '不限'} | "
            f"{ledger.phase_budget(phase) - used if ledger.phase_budget(phase) else '-'} |"
            for phase, used in sorted(summary["by_phase"].items())
        ) or "| （尚无消耗） | - | - | - |"
        role_rows = "\n".join(
            f"| {role or '(未标记)'} | {used} |" for role, used in sorted(summary["by_role"].items())
        ) or "| （尚无消耗） | - |"

        qa_lines = "\n".join(
            f"- Q：{item.question}\n  A：{item.answer}（来源：{item.source}）"
            for item in record.transcript
        ) or "- （无澄清问答）"
        gate_lines = "\n".join(
            f"- 门 {item.node}（{item.kind}）：尝试 {item.attempts} 次 / 返工 {item.rejections} 次 / "
            f"最近结论 {item.last_decision or '-'}"
            for item in record.gate_decisions
        ) or "- （无门决策）"

        prd_exists = (self.workspace / "docs" / "PRD.md").exists()
        accuracy = summary.get("estimate_accuracy")
        accuracy_text = f"{accuracy:.1%}" if accuracy is not None else "（纯估算模式）"

        status_counts = {k: 0 for k in ("todo", "doing", "review", "done", "blocked")}
        for task in state.tasks:
            status_counts[task.status.value] = status_counts.get(task.status.value, 0) + 1

        content = f"""# 交付说明（DELIVERY.md）

生成时间：{datetime.now(timezone.utc).isoformat()}
会话：{record.session_id} | 线程：{thread_id}
模型：{record.model} | 预算：{ledger.budget} tokens

## 需求
- 目标：{self.goal}
- PRD：{('docs/PRD.md（已生成）' if prd_exists else 'docs/PRD.md（缺失）')}

## 交付物清单（token 计量：产物大小 / 产生消耗）
| 产物 | token 大小 | 产生消耗 token | 来源任务 |
|---|---|---:|---|
{chr(10).join(artifact_rows) or '| （无产物） | - | - | - |'}

## Token 计量表
### 阶段消耗
| 阶段 | 消耗 token | 阶段预算 | 剩余 |
|---|---:|---:|---:|
{phase_rows}

### 角色消耗
| 角色 | 消耗 token |
|---|---:|
{role_rows}

### 预算总览
- 预算：{ledger.budget}
- 已用：{summary['used']}
- 剩余：{summary['remaining']}
- 超限：{'是' if summary['over_budget'] else '否'}
- 预估准确率：{accuracy_text}

## 澄清问答 transcript
{qa_lines}

## 门决策与返工记录
{gate_lines}

## 测试与验收
- 任务板：{len(state.tasks)} 个任务（状态分布：{status_counts}）
- 会议：{len(state.meetings)} 次 | 审批记录：{len(state.decisions)} 条
"""
        delivery_path = self.workspace / "DELIVERY.md"
        delivery_path.write_text(content, encoding="utf-8")
        self._git_commit(self.workspace, "docs: 交付说明与 token 计量表（DELIVERY.md）")
        return {
            "delivery_path": str(delivery_path),
            "artifacts": sorted(seen),
            "artifact_sizes": artifact_sizes,
            "token_summary": summary,
        }

    @staticmethod
    def _git_commit(workspace: Path, message: str) -> None:
        """工作区 git 提交（不存在仓库时初始化；不推送到远程）。"""
        try:
            subprocess.run(
                ["git", "rev-parse", "--is-inside-work-tree"],
                cwd=workspace, capture_output=True, timeout=30, check=True,
            )
        except (subprocess.CalledProcessError, OSError):
            subprocess.run(
                ["git", "init"], cwd=workspace, capture_output=True, timeout=60, check=False
            )
        subprocess.run(
            ["git", "config", "user.name", "agent-cluster"],
            cwd=workspace, capture_output=True, timeout=30, check=False,
        )
        subprocess.run(
            ["git", "config", "user.email", "agent-cluster@local"],
            cwd=workspace, capture_output=True, timeout=30, check=False,
        )
        subprocess.run(
            ["git", "add", "-A"], cwd=workspace, capture_output=True, timeout=60, check=False
        )
        subprocess.run(
            ["git", "commit", "-m", message, "--allow-empty"],
            cwd=workspace, capture_output=True, timeout=60, check=False,
        )

    # ------------------------------------------------------------------
    # 打印
    # ------------------------------------------------------------------

    def _print_request(self, request: ActionRequest) -> None:
        """打印待审批请求要点。"""
        self.print_fn(f"  待审批请求：{request.title}")
        self.print_fn(
            f"    类别：{request.kind.value} | 风险：{request.risk_level} | "
            f"bypass-immune：{request.bypass_immune}"
        )
        self.print_fn(f"    说明：{request.description}")
