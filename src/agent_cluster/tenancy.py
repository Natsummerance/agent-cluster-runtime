"""多租户隔离（v0.7 Task 14.12）。

- ``Tenant``：租户（id/name/项目配额/会话配额），配额 0=不设限。
- ``TenantStore``：租户 CRUD（fail loud 同 RbacStore 风格：重复 id 抛
  ValueError、缺失抛 KeyError）+ 命名空间存储工厂 + 用量统计 + 配额判定。
- 租户隔离三件套：存储命名空间前缀（``<root>/tenants/<tid>/projects``，
  经 ``ProjectStore(root, tenant_id=...)`` 接入）、per-tenant 配置分层
  （配置块 id 前缀 ``tenants.<tid>.`` 由 ``isolate_config`` 按租户过滤）、
  配额（项目数/会话数上限，``ensure_quota`` fail loud）。
- 事件日志：``tenant_payload`` 把 ``tenant_id`` 并入事件 payload
  （SessionEvent payload 可携带，事件词汇本身不动）。
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from agent_cluster.config_layers import ConfigEntry
from agent_cluster.projects import ProjectStore

__all__ = [
    "TENANT_CONFIG_PREFIX",
    "QuotaExceededError",
    "Tenant",
    "TenantStore",
    "isolate_config",
    "tenant_payload",
]

# per-tenant 配置块 id 前缀：``tenants.<tenant_id>.<key>``
TENANT_CONFIG_PREFIX = "tenants."


class QuotaExceededError(RuntimeError):
    """租户配额超限（fail loud，serve 层映射 409 quota_exceeded）。"""


@dataclass
class Tenant:
    """租户：id/名称 + 项目数/会话数配额（0=不设限）。"""

    id: str
    name: str
    project_limit: int = 0
    session_limit: int = 0
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds"))


class TenantStore:
    """租户存储（进程内记录 + 文件系统命名空间；serve 单进程模型下与全局索引同生命周期）。"""

    def __init__(self, root: str | Path | None = None) -> None:
        self._root = Path(root).expanduser().resolve() if root is not None else Path.home() / ".agent-cluster"
        self._tenants: dict[str, Tenant] = {}

    # ------------------------------------------------------------------
    # 路径
    # ------------------------------------------------------------------

    def tenant_dir(self, tenant_id: str) -> Path:
        """租户命名空间根：``<root>/tenants/<tenant_id>/``。"""
        return self._root / "tenants" / tenant_id

    def namespaced_project_store(self, tenant_id: str) -> ProjectStore:
        """返回指向 ``<root>/tenants/<tenant_id>/projects`` 的隔离 ProjectStore。"""
        return ProjectStore(root=self._root, tenant_id=tenant_id)

    # ------------------------------------------------------------------
    # CRUD（fail loud 同 RbacStore 风格）
    # ------------------------------------------------------------------

    def list_tenants(self) -> list[Tenant]:
        return [self._tenants[tid] for tid in sorted(self._tenants)]

    def get_tenant(self, tenant_id: str) -> Tenant:
        if tenant_id not in self._tenants:
            raise KeyError(f"未找到租户：{tenant_id!r}")
        return self._tenants[tenant_id]

    def add_tenant(self, *, id: str, name: str, project_limit: int = 0, session_limit: int = 0) -> Tenant:
        if not id or not name:
            raise ValueError("tenant id/name 不能为空")
        if project_limit < 0:
            raise ValueError(f"project_limit 不能为负：{project_limit}")
        if session_limit < 0:
            raise ValueError(f"session_limit 不能为负：{session_limit}")
        if id in self._tenants:
            raise ValueError(f"租户已存在：{id!r}（fail loud，禁止静默覆盖）")
        tenant = Tenant(id=id, name=name, project_limit=project_limit, session_limit=session_limit)
        self._tenants[id] = tenant
        return tenant

    def remove_tenant(self, tenant_id: str) -> None:
        """删除租户记录并清理命名空间目录（无目录则静默跳过）。"""
        self.get_tenant(tenant_id)
        del self._tenants[tenant_id]
        tenant_dir = self.tenant_dir(tenant_id)
        if tenant_dir.is_dir():
            shutil.rmtree(tenant_dir)

    # ------------------------------------------------------------------
    # 用量与配额
    # ------------------------------------------------------------------

    def usage(self, tenant_id: str) -> dict[str, int]:
        """当前用量：项目数（命名空间 ProjectStore）与会话数（项目会话注册表）。"""
        tenant = self.get_tenant(tenant_id)
        store = self.namespaced_project_store(tenant_id)
        projects = store.list()
        sessions = sum(len(project.sessions) for project in projects)
        return {
            "projects": len(projects),
            "sessions": sessions,
            "project_limit": tenant.project_limit,
            "session_limit": tenant.session_limit,
        }

    def ensure_quota(self, tenant_id: str, kind: str) -> bool:
        """配额判定（fail loud）：limit>0 且已用 >= limit 抛 QuotaExceededError。

        kind 取 ``projects`` / ``sessions``。
        """
        if kind not in ("projects", "sessions"):
            raise ValueError(f"未知配额维度：{kind!r}（取 projects/sessions）")
        usage = self.usage(tenant_id)
        limit_key = "project_limit" if kind == "projects" else "session_limit"
        limit = usage[limit_key]
        if limit <= 0:
            return True
        used = usage[kind]
        if used >= limit:
            label = "项目数" if kind == "projects" else "会话数"
            raise QuotaExceededError(
                f"租户 {tenant_id!r} {label}配额已满（{used}/{limit}），请先释放或调整配额"
            )
        return True


# ---------------------------------------------------------------------------
# per-tenant 配置分层
# ---------------------------------------------------------------------------


def isolate_config(entries: Sequence[ConfigEntry], tenant_id: str) -> list[ConfigEntry]:
    """按租户过滤配置块：``tenants.<tid>.*`` 只对对应租户可见（去前缀后与全局块合并）。

    全局块（无前缀）对全部租户可见；其它租户专属块被剔除。
    """
    prefix = f"{TENANT_CONFIG_PREFIX}{tenant_id}."
    tenant_blocks: dict[str, ConfigEntry] = {}
    globals_: list[ConfigEntry] = []
    for entry in entries:
        if entry.id.startswith(TENANT_CONFIG_PREFIX):
            if entry.id.startswith(prefix):
                tenant_blocks[entry.id[len(prefix):]] = entry
        else:
            globals_.append(entry)
    merged: list[ConfigEntry] = []
    seen: set[str] = set()
    for entry in globals_:
        merged.append(entry)
        if entry.id in tenant_blocks:
            tenant_entry = tenant_blocks.pop(entry.id)
            merged.append(
                ConfigEntry(id=entry.id, payload=dict(tenant_entry.payload), disabled=tenant_entry.disabled)
            )
    for block_id, entry in tenant_blocks.items():
        merged.append(ConfigEntry(id=block_id, payload=dict(entry.payload), disabled=entry.disabled))
    return merged


def tenant_payload(payload: Mapping[str, Any] | None, tenant_id: str) -> dict[str, Any]:
    """事件 payload 并入 tenant_id（SessionEvent payload 可携带租户字段）。"""
    merged = dict(payload or {})
    merged["tenant_id"] = tenant_id
    return merged
