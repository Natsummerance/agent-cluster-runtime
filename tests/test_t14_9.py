"""Task 14.9 RBAC：权限矩阵（12 岗→权限）、用户/团队存储、serve 端点与接缝拦截。"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

import agent_cluster.server as server_mod
from agent_cluster.rbac import (
    AUTHZ_SEAM,
    PERMISSION_MATRIX,
    PERMISSIONS,
    AuthzProvider,
    PermissionDenied,
    RbacStore,
)
from agent_cluster.roles import build_role_catalog
from agent_cluster.seam import DuplicateProviderError, SeamRegistry
from agent_cluster.server import WorkbenchHandler, WorkbenchServer

ALL_ROLE_IDS = set(build_role_catalog())


# ---------------------------------------------------------------------------
# 权限矩阵
# ---------------------------------------------------------------------------


def test_matrix_covers_all_roles_and_known_permissions():
    assert set(PERMISSION_MATRIX) == ALL_ROLE_IDS
    known = set(PERMISSIONS)
    for role_id, perms in PERMISSION_MATRIX.items():
        assert perms, f"{role_id} 权限集不能为空"
        assert perms <= known, f"{role_id} 含未知权限：{perms - known}"


def test_matrix_role_semantics():
    gov = PERMISSION_MATRIX["governance"]
    assert {"users.manage", "team.manage", "audit.read", "release.approve"} <= gov
    assert "users.manage" in PERMISSION_MATRIX["pm"]
    assert {"budget.manage", "release.approve"} <= PERMISSION_MATRIX["pm"]
    assert "release.approve" in PERMISSION_MATRIX["devops"]
    assert "agent.run" in PERMISSION_MATRIX["backend"]
    assert "users.manage" not in PERMISSION_MATRIX["frontend"]
    assert "project.read" in PERMISSION_MATRIX["docs"]
    assert "gate.approve" in PERMISSION_MATRIX["architect"]


# ---------------------------------------------------------------------------
# 存储：用户 / 团队 / 权限判定
# ---------------------------------------------------------------------------


def test_admin_bypasses_all_permissions():
    store = RbacStore()
    assert store.admin_user_id == "admin"
    assert store.require("admin", "users.manage") is True
    assert store.require("admin", "project.write", project_id="any") is True


def test_role_permission_granted_with_scope():
    store = RbacStore()
    store.add_user(id="dev-a", name="开发甲", role_ids=["backend"], scopes=["proj-1"])
    assert "project.write" in store.permissions_for("dev-a", project_id="proj-1")
    assert "project.write" not in store.permissions_for("dev-a", project_id="proj-2")
    # 全局权限不受项目作用域限制
    assert "roles.read" in store.permissions_for("dev-a")
    store.add_user(id="gov-b", name="治理乙", role_ids=["governance"])
    assert "users.manage" in store.permissions_for("gov-b", project_id="proj-9")


def test_require_denies_without_permission():
    store = RbacStore()
    store.add_user(id="reader", name="读者", role_ids=["docs"], scopes=["proj-1"])
    with pytest.raises(PermissionDenied, match="users.manage"):
        store.require("reader", "users.manage")
    with pytest.raises(PermissionDenied, match="project.write"):
        store.require("reader", "project.write", project_id="other")
    assert "project.read" in store.permissions_for("reader", project_id="proj-1")


def test_unknown_user_denied():
    store = RbacStore()
    with pytest.raises(PermissionDenied, match="reader-x"):
        store.require("reader-x", "project.read")


def test_user_crud_fail_loud():
    store = RbacStore()
    store.add_user(id="u1", name="一号", role_ids=["qa"], scopes=["p1"])
    assert any(u.id == "u1" for u in store.list_users())
    with pytest.raises(ValueError, match="u1"):
        store.add_user(id="u1", name="重复", role_ids=["pm"])
    store.update_user("u1", role_ids=["pm", "qa"])
    assert set(store.get_user("u1").role_ids) == {"pm", "qa"}
    store.remove_user("u1")
    assert not any(u.id == "u1" for u in store.list_users())
    with pytest.raises(KeyError, match="u1"):
        store.get_user("u1")


def test_team_crud_and_members():
    store = RbacStore()
    store.add_team(id="team-web", name="前端组")
    team = store.get_team("team-web")
    assert team.name == "前端组" and team.member_ids == []
    with pytest.raises(ValueError, match="team-web"):
        store.add_team(id="team-web", name="重复")
    store.add_user(id="u2", name="二号", role_ids=["backend"])
    store.add_member("team-web", "admin")
    store.add_member("team-web", "u2")
    assert store.get_team("team-web").member_ids == ["admin", "u2"]
    with pytest.raises(ValueError, match="u2"):
        store.add_member("team-web", "u2")
    store.remove_member("team-web", "admin")
    assert store.get_team("team-web").member_ids == ["u2"]
    store.remove_team("team-web")
    with pytest.raises(KeyError, match="team-web"):
        store.get_team("team-web")


def test_roles_catalog_includes_permissions():
    store = RbacStore()
    catalog = store.roles_catalog()
    assert len(catalog) == 12
    by_id = {entry["id"]: entry for entry in catalog}
    assert by_id["backend"]["permissions"] == sorted(PERMISSION_MATRIX["backend"])
    assert "name" in by_id["pm"] and "kind" in by_id["pm"]


def test_authz_seam_registration_and_duplicate_fail_loud():
    registry = SeamRegistry()
    store = RbacStore()
    with registry.register(AUTHZ_SEAM, AuthzProvider(store)):
        provider = registry.resolve(AUTHZ_SEAM)
        assert isinstance(provider, AuthzProvider)
        assert provider.store is store
        assert provider.check("admin", "users.manage") is True
        assert provider.check("nobody", "project.read") is False
        with pytest.raises(DuplicateProviderError):
            registry.register(AUTHZ_SEAM, AuthzProvider(RbacStore()))
    assert not registry.has(AUTHZ_SEAM)


# ---------------------------------------------------------------------------
# serve 端点
# ---------------------------------------------------------------------------


def _request(port, method, path, body=None, user="admin"):
    data = json.dumps(body or {}).encode("utf-8") if body is not None else None
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    if user:
        req.add_header("X-Auth-User", user)
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


@pytest.fixture()
def rbac_server(tmp_path, monkeypatch):
    monkeypatch.setattr(server_mod, "INDEX_DIR", tmp_path / "home")
    workbench = WorkbenchServer(host="127.0.0.1", port=0, auth_token="")
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), WorkbenchHandler)
    httpd.workbench = workbench
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        yield port, workbench
    finally:
        httpd.shutdown()


def test_get_roles_users_teams(rbac_server):
    port, workbench = rbac_server
    status, roles = _request(port, "GET", "/api/v1/roles")
    assert status == 200 and roles["ok"] is True
    assert len(roles["data"]["roles"]) == 12
    status, users = _request(port, "GET", "/api/v1/users")
    assert status == 200
    assert any(u["id"] == "admin" for u in users["data"]["users"])
    status, teams = _request(port, "GET", "/api/v1/teams")
    assert status == 200
    assert teams["data"]["teams"] == []


def test_users_crud_endpoints(rbac_server):
    port, workbench = rbac_server
    status, created = _request(
        port, "POST", "/api/v1/users", {"id": "dev-1", "name": "开发一", "role_ids": ["backend"], "scopes": ["p1"]}
    )
    assert status == 201, created
    assert created["data"]["user"]["id"] == "dev-1"
    status, updated = _request(port, "PATCH", "/api/v1/users/dev-1", {"role_ids": ["backend", "qa"]})
    assert status == 200
    assert set(updated["data"]["user"]["role_ids"]) == {"backend", "qa"}
    status, _ = _request(port, "DELETE", "/api/v1/users/dev-1")
    assert status == 200
    status, users = _request(port, "GET", "/api/v1/users")
    assert all(u["id"] != "dev-1" for u in users["data"]["users"])


def test_teams_endpoints_with_members(rbac_server):
    port, _ = rbac_server
    status, created = _request(port, "POST", "/api/v1/teams", {"id": "t-web", "name": "前端组"})
    assert status == 201, created
    status, _ = _request(port, "POST", "/api/v1/teams/t-web/members", {"user_id": "admin"})
    assert status == 200
    status, teams = _request(port, "GET", "/api/v1/teams")
    assert teams["data"]["teams"][0]["member_ids"] == ["admin"]
    status, _ = _request(port, "DELETE", "/api/v1/teams/t-web")
    assert status == 200
    status, teams = _request(port, "GET", "/api/v1/teams")
    assert teams["data"]["teams"] == []


def test_permission_enforced_on_mutations(rbac_server):
    port, workbench = rbac_server
    workbench.rbac.add_user(id="bob", name="鲍勃", role_ids=["frontend"])
    status, body = _request(port, "POST", "/api/v1/users", {"id": "x", "name": "x", "role_ids": ["pm"]}, user="bob")
    assert status == 403, body
    assert body.get("code") == "permission_denied"
    status, body = _request(port, "POST", "/api/v1/teams", {"id": "t", "name": "T"}, user="bob")
    assert status == 403
    # 读端点对任何已登录用户开放
    status, _ = _request(port, "GET", "/api/v1/roles", user="bob")
    assert status == 200
