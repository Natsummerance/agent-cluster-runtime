"""资源日历（v0.7 Task 14.15）。

- ``Availability``：岗位可用块（role_id/开始/结束/备注），ISO 时间字符串。
- ``ResourceCalendar``：岗位×时间段可用性 CRUD（fail loud 同 RbacStore/TenantStore
  风格：非法输入抛 ValueError、缺失抛 KeyError、同岗位时间段重叠抛 OverlapError）。
- budget/ledger 集成点：``is_available`` / ``assert_available`` / ``conflicts``
  供预算与台账在排程/占用岗位前做可用性判定（冲突 fail loud）。
- 进程内记录 + 可选 root 命名空间（serve 单进程模型下与全局索引同生命周期）。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping

from agent_cluster.roles import RoleRegistry, build_role_catalog

__all__ = [
    "Availability",
    "OverlapError",
    "ResourceCalendar",
]


def _parse_iso(value: str) -> datetime:
    """解析 ISO 时间（容忍裸/带时区）；缺时区按 UTC 归一化，保证比较安全。"""
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError(f"非法时间格式：{value!r}（需 ISO 8601，如 2026-08-14T09:00:00）") from None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


class OverlapError(RuntimeError):
    """同岗位时间段重叠（fail loud，serve 层映射 409 overlap）。"""


@dataclass
class Availability:
    """岗位可用块：role_id + [start, end) 时间段 + 备注。"""

    id: str
    role_id: str
    start: str
    end: str
    note: str = ""
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds"))


class ResourceCalendar:
    """岗位×时间段可用性存储（进程内记录）。"""

    def __init__(self, root: str | Path | None = None, roles: Mapping[str, object] | None = None) -> None:
        self._root = Path(root).expanduser().resolve() if root is not None else Path.home() / ".agent-cluster"
        self._roles = RoleRegistry(dict(roles)) if roles is not None else RoleRegistry(build_role_catalog())
        self._availability: dict[str, Availability] = {}

    # ------------------------------------------------------------------
    # CRUD（fail loud）
    # ------------------------------------------------------------------

    def add_availability(
        self,
        *,
        role_id: str,
        start: str,
        end: str,
        note: str = "",
    ) -> Availability:
        """新建可用块；校验岗位/时间并检查同岗位重叠（冲突 fail loud）。"""
        if not role_id:
            raise ValueError("role_id 不能为空")
        try:
            self._roles.get(role_id)
        except KeyError as exc:
            raise ValueError(str(exc)) from None
        start_dt = _parse_iso(start)
        end_dt = _parse_iso(end)
        if end_dt <= start_dt:
            raise ValueError(f"时间段非法：end（{end!r}）必须晚于 start（{start!r}）")
        for existing in self._availability.values():
            if existing.role_id != role_id:
                continue
            existing_start = _parse_iso(existing.start)
            existing_end = _parse_iso(existing.end)
            if start_dt < existing_end and existing_start < end_dt:
                raise OverlapError(
                    f"岗位 {role_id!r} 时间段冲突：{start!r}~{end!r} 与既有可用块 "
                    f"{existing.id!r}（{existing.start}~{existing.end}）重叠"
                )
        item = Availability(
            id=uuid.uuid4().hex[:12],
            role_id=role_id,
            start=start,
            end=end,
            note=note,
        )
        self._availability[item.id] = item
        return item

    def get_availability(self, availability_id: str) -> Availability:
        if availability_id not in self._availability:
            raise KeyError(f"未找到可用块：{availability_id!r}")
        return self._availability[availability_id]

    def list_availability(
        self,
        role_id: str | None = None,
        from_: str | None = None,
        to: str | None = None,
    ) -> list[Availability]:
        """列出可用块（按开始时间排序）；支持岗位与时间窗口相交过滤。"""
        from_dt = _parse_iso(from_) if from_ else None
        to_dt = _parse_iso(to) if to else None
        if from_dt is not None and to_dt is not None and from_dt > to_dt:
            raise ValueError(f"时间窗口非法：from（{from_!r}）晚于 to（{to!r}）")
        items = []
        for item in self._availability.values():
            if role_id and item.role_id != role_id:
                continue
            if from_dt is not None and not (_parse_iso(item.end) > from_dt):
                continue
            if to_dt is not None and not (_parse_iso(item.start) < to_dt):
                continue
            items.append(item)
        return sorted(items, key=lambda item: (item.start, item.id))

    def remove_availability(self, availability_id: str) -> None:
        """删除可用块；缺失抛 KeyError（fail loud）。"""
        self.get_availability(availability_id)
        del self._availability[availability_id]

    # ------------------------------------------------------------------
    # budget/ledger 集成点：排程前可用性判定（冲突 fail loud）
    # ------------------------------------------------------------------

    def conflicts(self, role_id: str, start: str, end: str) -> list[Availability]:
        """返回与 [start, end) 重叠的同岗位可用块列表（无冲突返回空列表）。"""
        start_dt = _parse_iso(start)
        end_dt = _parse_iso(end)
        if end_dt <= start_dt:
            raise ValueError(f"时间段非法：end（{end!r}）必须晚于 start（{start!r}）")
        return [
            item
            for item in self._availability.values()
            if item.role_id == role_id and start_dt < _parse_iso(item.end) and _parse_iso(item.start) < end_dt
        ]

    def is_available(self, role_id: str, start: str, end: str) -> bool:
        """岗位在 [start, end) 内是否可用（与任何可用块无冲突）。"""
        return not self.conflicts(role_id, start, end)

    def assert_available(self, role_id: str, start: str, end: str) -> bool:
        """集成点：占用/排程前断言岗位可用；冲突抛 OverlapError（fail loud）。"""
        blocked = self.conflicts(role_id, start, end)
        if blocked:
            detail = "、".join(f"{item.id!r}（{item.start}~{item.end}）" for item in blocked)
            raise OverlapError(
                f"岗位 {role_id!r} 在 {start!r}~{end!r} 不可用：冲突可用块 {detail}"
            )
        return True