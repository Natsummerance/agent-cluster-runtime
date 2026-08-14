"""RBAC 权限层（v0.7 Task 14.9，dsh permission/team 语义的轻量 Python 等价）。

- 权限矩阵：12 岗位 -> 权限集合（``PERMISSION_MATRIX``），每权限可全局或按项目
  作用域判定（``project.*`` 类权限受 scopes 约束，其余为全局权限）。
- ``RbacStore``：用户/团队存储（内存，admin 内置直通），fail-loud 语义与
  v0.7 工程约定一致（重复 id 抛 ValueError、缺失抛 KeyError）。
- 接缝拦截：``AuthzProvider`` 注册为 ``authz`` seam（``AUTHZ_SEAM``），
  serve 变更端点经 ``require`` 校验，未授权返回 403 ``permission_denied``。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from agent_cluster.seam import Provider

__all__ = [
    "AUTHZ_SEAM",
    "AuthzProvider",
    "PERMISSION_MATRIX",
    "PERMISSIONS",
    "PermissionDenied",
    "RbacStore",
    "Team",
    "User",
]

# 全部已知权限（矩阵取值只能出自此处）
PERMISSIONS: tuple[str, ...] = (
    "project.read",
    "project.write",
    "agent.run",
    "budget.manage",
    "gate.approve",
    "release.approve",
    "team.manage",
    "users.manage",
    "audit.read",
    "roles.read",
    "tenants.manage",
)

# 12 岗位 -> 权限矩阵（角色语义来自 roles.py 岗位画像与审批范围）
PERMISSION_MATRIX: dict[str, set[str]] = {
    "pm": {"project.read", "project.write", "budget.manage", "gate.approve", "release.approve", "team.manage", "users.manage", "audit.read", "roles.read"},
    "pmo": {"project.read", "project.write", "budget.manage", "gate.approve", "team.manage", "audit.read", "roles.read"},
    "frontend": {"project.read", "project.write", "agent.run", "roles.read"},
    "backend": {"project.read", "project.write", "agent.run", "roles.read"},
    "algorithm": {"project.read", "project.write", "agent.run", "roles.read"},
    "architect": {"project.read", "project.write", "agent.run", "gate.approve", "audit.read", "roles.read"},
    "qa": {"project.read", "project.write", "agent.run", "gate.approve", "roles.read"},
    "devops": {"project.read", "project.write", "agent.run", "gate.approve", "release.approve", "roles.read"},
    "docs": {"project.read", "project.write", "agent.run", "roles.read"},
    "reviewer": {"project.read", "project.write", "agent.run", "gate.approve", "roles.read"},
    "debugger": {"project.read", "project.write", "agent.run", "roles.read"},
    "governance": {"project.read", "project.write", "agent.run", "gate.approve", "release.approve", "team.manage", "users.manage", "audit.read", "roles.read", "tenants.manage"},
}

PROJECT_SCOPED_PERMISSIONS: frozenset[str] = frozenset({"project.read", "project.write", "agent.run", "budget.manage", "gate.approve"})

ALL_SCOPES = "*"


class PermissionDenied(RuntimeError):
    """权限不足（fail loud，serve 层映射为 403 permission_denied）。"""


@dataclass
class User:
    """用户：id/名称/岗位集合/项目作用域（``*`` 表示全部项目）。"""

    id: str
    name: str
    role_ids: list[str] = field(default_factory=list)
    scopes: list[str] = field(default_factory=list)
    is_admin: bool = False


@dataclass
class Team:
    """团队：id/名称/成员用户 id 列表（有序去重）。"""

    id: str
    name: str
    member_ids: list[str] = field(default_factory=list)


class RbacStore:
    """用户/团队/权限判定存储（进程内；serve 单进程模型下与全局索引同生命周期）。"""

    def __init__(self) -> None:
        self._users: dict[str, User] = {}
        self._teams: dict[str, Team] = {}
        self._users["admin"] = User(id="admin", name="系统管理员", role_ids=["governance"], scopes=[ALL_SCOPES], is_admin=True)
        self._teams: dict[str, Team] = {}

    @property
    def admin_user_id(self) -> str:
        return "admin"

    # ---- 用户 ----

    def list_users(self) -> list[User]:
        return [self._users[uid] for uid in sorted(self._users)]

    def get_user(self, user_id: str) -> User:
        if user_id not in self._users:
            raise KeyError(f"未找到用户：{user_id!r}")
        return self._users[user_id]

    def add_user(self, *, id: str, name: str, role_ids: list[str] | None = None, scopes: list[str] | None = None) -> User:
        if not id or not name:
            raise ValueError("user id/name 不能为空")
        if id in self._users:
            raise ValueError(f"用户已存在：{id!r}（fail loud，禁止静默覆盖）")
        user = User(id=id, name=name, role_ids=list(role_ids or []), scopes=list(scopes or []))
        self._users[id] = user
        return user

    def update_user(self, user_id: str, *, name: str | None = None, role_ids: list[str] | None = None, scopes: list[str] | None = None) -> User:
        user = self.get_user(user_id)
        if user.is_admin:
            raise ValueError("内置 admin 用户不可修改")
        if name is not None:
            user.name = name
        if role_ids is not None:
            unknown = set(role_ids) - set(PERMISSION_MATRIX)
            if unknown:
                raise ValueError(f"未知岗位：{sorted(unknown)}")
            user.role_ids = list(role_ids)
        if scopes is not None:
            user.scopes = list(scopes)
        return user

    def remove_user(self, user_id: str) -> None:
        user = self.get_user(user_id)
        if user.is_admin:
            raise ValueError("内置 admin 用户不可删除")
        del self._users[user_id]
        for team in self._teams.values():
            team.member_ids = [uid for uid in team.member_ids if uid != user_id]

    # ---- 团队 ----

    def list_teams(self) -> list[Team]:
        return [self._teams[tid] for tid in sorted(self._teams)]

    def get_team(self, team_id: str) -> Team:
        if team_id not in self._teams:
            raise KeyError(f"未找到团队：{team_id!r}")
        return self._teams[team_id]

    def add_team(self, *, id: str, name: str) -> Team:
        if not id or not name:
            raise ValueError("team id/name 不能为空")
        if id in self._teams:
            raise ValueError(f"团队已存在：{id!r}（fail loud，禁止静默覆盖）")
        team = Team(id=id, name=name)
        self._teams[id] = team
        return team

    def remove_team(self, team_id: str) -> None:
        self.get_team(team_id)
        del self._teams[team_id]

    def add_member(self, team_id: str, user_id: str) -> None:
        team = self.get_team(team_id)
        self.get_user(user_id)
        if user_id in team.member_ids:
            raise ValueError(f"成员已存在：{user_id!r}（fail loud）")
        team.member_ids.append(user_id)

    def remove_member(self, team_id: str, user_id: str) -> None:
        team = self.get_team(team_id)
        if user_id not in team.member_ids:
            raise ValueError(f"成员不存在：{user_id!r}")
        team.member_ids.remove(user_id)

    # ---- 权限判定 ----

    def permissions_for(self, user_id: str, project_id: str | None = None) -> set[str]:
        """返回用户在（可选）项目作用域下拥有的权限集合。"""
        if user_id not in self._users:
            return set()
        user = self._users[user_id]
        if user.is_admin:
            return set(PERMISSIONS)
        perms: set[str] = set()
        for role_id in user.role_ids:
            perms |= PERMISSION_MATRIX.get(role_id, set())
        if project_id is not None:
            in_scope = user.scopes == [ALL_SCOPES] or ALL_SCOPES in user.scopes or project_id in user.scopes
            if not in_scope:
                perms -= PROJECT_SCOPED_PERMISSIONS
        return perms

    def check(self, user_id: str, permission: str, project_id: str | None = None) -> bool:
        return permission in self.permissions_for(user_id, project_id=project_id)

    def require(self, user_id: str, permission: str, project_id: str | None = None) -> bool:
        if not self.check(user_id, permission, project_id=project_id):
            raise PermissionDenied(
                f"用户 {user_id!r} 缺少权限 {permission!r}" + (f"（项目 {project_id!r}）" if project_id else "")
            )
        return True

    def roles_catalog(self) -> list[dict]:
        from agent_cluster.roles import RoleRegistry

        registry = RoleRegistry()
        entries = []
        for role in registry.list():
            entries.append(
                {
                    "id": role.id,
                    "name": role.name,
                    "kind": role.kind.value,
                    "permissions": sorted(PERMISSION_MATRIX[role.id]),
                }
            )
        return entries


class AuthzProvider(Provider):
    """``authz`` 接缝 provider：包装 RbacStore 的权限判定。"""

    def __init__(self, store: RbacStore) -> None:
        self.store = store

    def check(self, user_id: str, permission: str, project_id: str | None = None) -> bool:
        return self.store.check(user_id, permission, project_id=project_id)

    def require(self, user_id: str, permission: str, project_id: str | None = None) -> bool:
        return self.store.require(user_id, permission, project_id=project_id)


AUTHZ_SEAM = "authz"
