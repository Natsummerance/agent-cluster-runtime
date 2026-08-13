"""记忆/经验库（v0.5 T12.2）：SQLite 四级晋升 + 提议制写入 + 检索。

四级记忆（调研采纳的分级晋升模型，检索本地优先）：
- ``session``：会话摘要（当前会话上下文，tier 1）
- ``project``：项目学习（本项目内的决策/约束/失败模式，tier 2）
- ``gotcha``：跨项目经验（踩坑/反模式，tier 3，跨项目复用价值最高）
- ``domain``：领域知识（综合笔记，tier 4）

写入 = 提议制：任何节点可产生候选（candidate）→ 多次会话证据（evidence_count >=
min_evidence）或人工确认后晋升（promote）为 active；防止自动写入污染全局记忆。
内容以 Markdown 文件存放（``<workspace>/.agent-cluster/memory/<tier>/<id>.md``），
SQLite 只存索引与证据计数——文件可备份/版本化，DB 可重建。

线程安全：``sqlite3`` 连接按调用建立（短连接），写操作加 ``threading.Lock``。
"""

from __future__ import annotations

import json
import re
import sqlite3
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Any, Sequence

__all__ = [
    "Tier",
    "MemoryStatus",
    "MemoryItem",
    "MemoryProposal",
    "MemoryStore",
    "TIER_ORDER",
]

TIER_ORDER: tuple[str, ...] = ("session", "project", "gotcha", "domain")


class Tier(StrEnum):
    """记忆层级（检索顺序 = 定义顺序，本地优先）。"""

    SESSION = "session"
    PROJECT = "project"
    GOTCHA = "gotcha"
    DOMAIN = "domain"


class MemoryStatus(StrEnum):
    """记忆条目状态：候选 → 生效 → 归档。"""

    CANDIDATE = "candidate"
    ACTIVE = "active"
    ARCHIVED = "archived"


@dataclass
class MemoryItem:
    """记忆条目（索引视图 + 内容引用）。"""

    id: str
    tier: str
    title: str
    content_ref: str
    source: str = ""
    status: str = MemoryStatus.CANDIDATE
    evidence_count: int = 0
    session_ids: list[str] = field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""
    meta: dict[str, Any] = field(default_factory=dict)

    def content(self, root: Path) -> str:
        """读取关联的 Markdown 内容文件（缺失返回空串）。"""
        try:
            return (root / self.content_ref).read_text(encoding="utf-8")
        except OSError:
            return ""


@dataclass
class MemoryProposal:
    """记忆晋升提案（供进化/面板展示与审批）。"""

    id: str
    item_id: str
    target_tier: str
    reason: str
    status: str = "open"
    created_at: str = ""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class MemoryStore:
    """工作区级记忆库（``<root>/.agent-cluster/memory.db`` + 内容目录）。"""

    def __init__(self, root: str | Path, *, base_dir: str | Path | None = None) -> None:
        self.root = Path(root).expanduser().resolve()
        if base_dir is None:
            # v0.5 路径：<root>/.agent-cluster/memory.db + <root>/.agent-cluster/memory/
            self.dir = self.root / ".agent-cluster" / "memory"
            self.db_path = self.root / ".agent-cluster" / "memory.db"
        else:
            # v0.6 项目路径：<base_dir>/memory.db + <base_dir>/（ProjectStore.memory_store 传入）
            self.dir = Path(base_dir).expanduser().resolve()
            self.db_path = self.dir / "memory.db"
        self.dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._init_schema()

    # ------------------------------------------------------------------
    # 基础设施
    # ------------------------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    def _init_schema(self) -> None:
        with self._lock, self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS memory_items (
                    id TEXT PRIMARY KEY,
                    tier TEXT NOT NULL,
                    title TEXT NOT NULL,
                    content_ref TEXT NOT NULL,
                    source TEXT DEFAULT '',
                    status TEXT DEFAULT 'candidate',
                    evidence_count INTEGER DEFAULT 0,
                    session_ids TEXT DEFAULT '[]',
                    meta TEXT DEFAULT '{}',
                    created_at TEXT,
                    updated_at TEXT
                );
                CREATE TABLE IF NOT EXISTS memory_evidence (
                    item_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    note TEXT DEFAULT '',
                    ts TEXT,
                    PRIMARY KEY (item_id, session_id)
                );
                CREATE TABLE IF NOT EXISTS memory_proposals (
                    id TEXT PRIMARY KEY,
                    item_id TEXT NOT NULL,
                    target_tier TEXT NOT NULL,
                    reason TEXT DEFAULT '',
                    status TEXT DEFAULT 'open',
                    created_at TEXT
                );
                """
            )

    def _content_path(self, tier: str, item_id: str) -> Path:
        tier_dir = self.dir / tier
        tier_dir.mkdir(parents=True, exist_ok=True)
        return tier_dir / f"{item_id}.md"

    # ------------------------------------------------------------------
    # 写入（提议制）
    # ------------------------------------------------------------------

    def add_candidate(
        self,
        *,
        title: str,
        content: str,
        source: str = "",
        tier: str | Tier = Tier.PROJECT,
        session_id: str = "",
        meta: dict[str, Any] | None = None,
    ) -> str:
        """新增候选记忆（status=candidate）。返回 item id。"""
        tier = Tier(tier).value if isinstance(tier, Tier) else str(tier)
        if tier not in TIER_ORDER:
            tier = Tier.PROJECT.value
        item_id = uuid.uuid4().hex[:12]
        content_path = self._content_path(tier, item_id)
        content_path.write_text(content, encoding="utf-8")
        ts = _now()
        session_ids = [session_id] if session_id else []
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO memory_items
                    (id, tier, title, content_ref, source, status, evidence_count,
                     session_ids, meta, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, 'candidate', ?, ?, ?, ?, ?)
                """,
                (
                    item_id,
                    tier,
                    title,
                    str(content_path.relative_to(self.root)).replace("\\", "/")
                    if content_path.is_relative_to(self.root)
                    else str(content_path),
                    source,
                    len(session_ids),
                    json.dumps(session_ids, ensure_ascii=False),
                    json.dumps(meta or {}, ensure_ascii=False),
                    ts,
                    ts,
                ),
            )
        return item_id

    def add_evidence(self, item_id: str, session_id: str, note: str = "") -> int:
        """追加一次证据（同一会话只计一次，返回累计 evidence_count）。"""
        ts = _now()
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO memory_evidence (item_id, session_id, note, ts) VALUES (?, ?, ?, ?)",
                (item_id, session_id, note, ts),
            )
            conn.execute(
                """
                UPDATE memory_items
                SET evidence_count = (SELECT COUNT(*) FROM memory_evidence WHERE item_id = ?),
                    session_ids = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (item_id, json.dumps(self._session_ids(item_id), ensure_ascii=False), ts, item_id),
            )
            row = conn.execute(
                "SELECT evidence_count FROM memory_items WHERE id = ?", (item_id,)
            ).fetchone()
        return int(row["evidence_count"]) if row else 0

    def _session_ids(self, item_id: str) -> list[str]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT session_ids FROM memory_items WHERE id = ?", (item_id,)
            ).fetchone()
        if not row:
            return []
        try:
            return list(json.loads(row["session_ids"]))
        except (json.JSONDecodeError, TypeError):
            return []

    def promote(
        self,
        item_id: str,
        *,
        target_tier: str | Tier | None = None,
        min_evidence: int = 2,
        human_confirm: bool = False,
    ) -> bool:
        """晋升候选 → active（可提升 tier）。

        - ``min_evidence``：默认需 >=2 次会话证据；``human_confirm=True`` 绕过证据门槛。
        - ``target_tier`` 为 None 时保持原 tier 仅激活。
        """
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT tier, content_ref, status, evidence_count FROM memory_items WHERE id = ?", (item_id,)
            ).fetchone()
            if not row:
                return False
            current_tier = str(row["tier"])
            new_tier = (
                Tier(target_tier).value
                if isinstance(target_tier, Tier)
                else str(target_tier or current_tier)
            )
            if new_tier not in TIER_ORDER:
                new_tier = current_tier
            evidence = int(row["evidence_count"])
            if not human_confirm and evidence < min_evidence:
                return False
            # 提升 tier 时移动内容文件到目标层级目录
            if new_tier != current_tier:
                src = self.root / str(row["content_ref"])
                dst = self._content_path(new_tier, item_id)
                if src.exists():
                    dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
                    try:
                        src.unlink()
                    except OSError:
                        pass
                content_ref = str(dst.relative_to(self.root)).replace("\\", "/")
            else:
                content_ref = str(row["content_ref"])
            conn.execute(
                """
                UPDATE memory_items
                SET tier = ?, content_ref = ?, status = 'active', updated_at = ?
                WHERE id = ?
                """,
                (new_tier, content_ref, _now(), item_id),
            )
        return True

    def archive(self, item_id: str) -> bool:
        """归档条目（status=archived，不再参与检索）。"""
        with self._lock, self._connect() as conn:
            cur = conn.execute(
                "UPDATE memory_items SET status = 'archived', updated_at = ? WHERE id = ?",
                (_now(), item_id),
            )
        return cur.rowcount > 0

    # ------------------------------------------------------------------
    # 提案（进化/面板）
    # ------------------------------------------------------------------

    def create_proposal(self, item_id: str, target_tier: str, reason: str = "") -> str:
        """为候选创建晋升提案（面板/进化可审批）。返回提案 id。"""
        proposal_id = uuid.uuid4().hex[:12]
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO memory_proposals (id, item_id, target_tier, reason, status, created_at)
                VALUES (?, ?, ?, ?, 'open', ?)
                """,
                (proposal_id, item_id, target_tier, reason, _now()),
            )
        return proposal_id

    def resolve_proposal(self, proposal_id: str, approved: bool) -> bool:
        """处理提案：approved -> 强制晋升；否则标记 rejected。"""
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT item_id, target_tier FROM memory_proposals WHERE id = ? AND status = 'open'",
                (proposal_id,),
            ).fetchone()
            if not row:
                return False
            conn.execute(
                "UPDATE memory_proposals SET status = ? WHERE id = ?",
                ("approved" if approved else "rejected", proposal_id),
            )
        if approved:
            return self.promote(str(row["item_id"]), target_tier=str(row["target_tier"]), human_confirm=True)
        return True

    def list_proposals(self, status: str = "open") -> list[MemoryProposal]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM memory_proposals WHERE status = ? ORDER BY created_at DESC", (status,)
            ).fetchall()
        return [
            MemoryProposal(
                id=str(r["id"]),
                item_id=str(r["item_id"]),
                target_tier=str(r["target_tier"]),
                reason=str(r["reason"]),
                status=str(r["status"]),
                created_at=str(r["created_at"]),
            )
            for r in rows
        ]

    # ------------------------------------------------------------------
    # 读取与检索
    # ------------------------------------------------------------------

    def list_items(
        self,
        tier: str | None = None,
        status: str | None = None,
        limit: int = 100,
    ) -> list[MemoryItem]:
        where: list[str] = []
        params: list[Any] = []
        if tier:
            where.append("tier = ?")
            params.append(tier)
        if status:
            where.append("status = ?")
            params.append(status)
        sql = "SELECT * FROM memory_items"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY updated_at DESC LIMIT ?"
        params.append(limit)
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [self._row_to_item(row) for row in rows]

    def get(self, item_id: str) -> MemoryItem | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM memory_items WHERE id = ?", (item_id,)).fetchone()
        return self._row_to_item(row) if row else None

    def search(self, query: str, limit: int = 10) -> list[MemoryItem]:
        """关键词检索：标题+内容匹配打分，按 tier 本地优先排序，只返回 active+candidate。"""
        if not query.strip():
            return []
        tokens = [token.lower() for token in re.findall(r"[\\w\\u4e00-\\u9fff]+", query)]
        if not tokens:
            return []
        results: list[tuple[float, MemoryItem]] = []
        for item in self.list_items(limit=500):
            if item.status == MemoryStatus.ARCHIVED:
                continue
            haystack = f"{item.title}\\n{item.content(self.root)}".lower()
            score = sum(1 for token in tokens if token in haystack)
            if score <= 0:
                continue
            tier_rank = TIER_ORDER.index(item.tier) if item.tier in TIER_ORDER else 99
            results.append((score * 10 - tier_rank, item))
        results.sort(key=lambda pair: pair[0], reverse=True)
        return [item for _, item in results[:limit]]

    def session_summary(self, session_id: str) -> str:
        """取会话摘要内容（tier=session 且 session_ids 含该会话的条目）。"""
        for item in self.list_items(tier=Tier.SESSION.value, limit=200):
            if session_id in item.session_ids:
                content = item.content(self.root)
                if content:
                    return content
        return ""

    def save_session_summary(self, *, session_id: str, title: str, content: str, source: str = "") -> str:
        """写入/更新会话摘要（tier=session）。返回 item id。"""
        for item in self.list_items(tier=Tier.SESSION.value, limit=200):
            if session_id in item.session_ids:
                content_path = self.root / item.content_ref
                content_path.write_text(content, encoding="utf-8")
                with self._lock, self._connect() as conn:
                    conn.execute(
                        "UPDATE memory_items SET title = ?, updated_at = ? WHERE id = ?",
                        (title, _now(), item.id),
                    )
                return item.id
        return self.add_candidate(
            title=title, content=content, source=source, tier=Tier.SESSION, session_id=session_id
        )

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    def _row_to_item(self, row: sqlite3.Row) -> MemoryItem:
        try:
            meta = json.loads(str(row["meta"]) or "{}")
        except json.JSONDecodeError:
            meta = {}
        try:
            session_ids = list(json.loads(str(row["session_ids"]) or "[]"))
        except json.JSONDecodeError:
            session_ids = []
        return MemoryItem(
            id=str(row["id"]),
            tier=str(row["tier"]),
            title=str(row["title"]),
            content_ref=str(row["content_ref"]),
            source=str(row["source"]),
            status=str(row["status"]),
            evidence_count=int(row["evidence_count"]),
            session_ids=session_ids,
            created_at=str(row["created_at"] or ""),
            updated_at=str(row["updated_at"] or ""),
            meta=meta,
        )
