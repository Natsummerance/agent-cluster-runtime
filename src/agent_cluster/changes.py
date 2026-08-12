"""需求变更版本化（v0.5 T12.4）：实时打断 + 变更历史 + 回滚。

- ``ChangeRecord``：一次需求变更（版本号递增 / 文本 / 节点 / 阶段 / 快照目录）。
- ``ChangeHistory``：``<workspace>/.agent-cluster/change-history/`` 下的版本记录
  （session.json 记录 + snapshots/v<N>/ 快照目录）。
- 快照范围：``docs/**`` 与工作区顶层 ``*.md``（PRD/架构/手册等关键产物）；
  回滚 = 把某版本快照还原回工作区（可 git 追踪差异）。
"""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

__all__ = ["ChangeRecord", "ChangeHistory"]

_SNAPSHOT_INCLUDE_DIRS = ("docs",)
_SNAPSHOT_INCLUDE_TOP_MD = True


class ChangeRecord(BaseModel):
    """一次需求变更记录。"""

    model_config = ConfigDict(extra="ignore")

    version: int = Field(description="变更版本（从 1 递增）")
    text: str = Field(description="变更内容（用户输入）")
    ts: str = Field(description="变更时间")
    node: str = Field(default="", description="生效时的流程节点")
    phase: str = Field(default="", description="生效时的阶段")
    snapshot_dir: str = Field(default="", description="快照相对路径")



class ChangeHistory:
    """会话级需求变更历史（磁盘持久化）。"""

    def __init__(self, workspace: str | Path) -> None:
        self.workspace = Path(workspace).expanduser().resolve()
        self.dir = self.workspace / ".agent-cluster" / "change-history"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.snapshots_dir = self.dir / "snapshots"
        self.snapshots_dir.mkdir(parents=True, exist_ok=True)
        self.file = self.dir / "session.json"
        self._records = self._load()

    def _load(self) -> list[ChangeRecord]:
        try:
            data = json.loads(self.file.read_text(encoding="utf-8"))
            return [ChangeRecord.model_validate(item) for item in data.get("records", [])]
        except (OSError, json.JSONDecodeError, UnicodeDecodeError, ValueError):
            return []

    def _save(self) -> None:
        tmp = self.file.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps({"records": [r.model_dump() for r in self._records]}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        import os

        os.replace(tmp, self.file)

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------

    def list(self) -> list[ChangeRecord]:
        return list(self._records)

    def latest_version(self) -> int:
        return self._records[-1].version if self._records else 0

    def get(self, version: int) -> ChangeRecord | None:
        for record in self._records:
            if record.version == version:
                return record
        return None

    # ------------------------------------------------------------------
    # 快照与记录
    # ------------------------------------------------------------------

    def _snapshot(self, version: int) -> str:
        """把 docs/** 与顶层 *.md 复制到 snapshots/v<N>/，返回相对路径。"""
        target = self.snapshots_dir / f"v{version}"
        target.mkdir(parents=True, exist_ok=True)
        copied = 0
        for dir_name in _SNAPSHOT_INCLUDE_DIRS:
            src = self.workspace / dir_name
            if src.is_dir():
                dst = target / dir_name
                if dst.exists():
                    shutil.rmtree(dst)
                shutil.copytree(src, dst)
                copied += 1
        if _SNAPSHOT_INCLUDE_TOP_MD:
            for md in self.workspace.glob("*.md"):
                shutil.copy2(md, target / md.name)
                copied += 1
        if copied == 0:
            (target / "README.md").write_text("（无产物快照）\n", encoding="utf-8")
        return str(target.relative_to(self.workspace)).replace("\\", "/")

    def record(self, *, text: str, node: str = "", phase: str = "") -> ChangeRecord:
        """记录一次需求变更（自动快照 + 版本 +1）。"""
        version = self.latest_version() + 1
        snapshot_dir = self._snapshot(version)
        record = ChangeRecord(
            version=version,
            text=text,
            ts=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            node=node,
            phase=phase,
            snapshot_dir=snapshot_dir,
        )
        self._records.append(record)
        self._save()
        return record

    def rollback(self, version: int) -> bool:
        """回滚：把 v<version> 快照还原回工作区（覆盖同名文件）。"""
        record = self.get(version)
        if record is None:
            return False
        snapshot_root = self.workspace / record.snapshot_dir
        if not snapshot_root.is_dir():
            return False
        restored = 0
        for child in snapshot_root.iterdir():
            if child.is_dir():
                dst = self.workspace / child.name
                if dst.exists():
                    shutil.rmtree(dst)
                shutil.copytree(child, dst)
                restored += 1
            elif child.is_file():
                shutil.copy2(child, self.workspace / child.name)
                restored += 1
        return restored > 0

    def summary(self) -> dict[str, Any]:
        return {"count": len(self._records), "latest_version": self.latest_version()}
