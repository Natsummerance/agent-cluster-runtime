"""Task 14.12 多租户隔离：TenantStore CRUD、配额、命名空间存储、serve 端点与租户配置隔离。"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

import agent_cluster.server as server_mod
from agent_cluster.config_layers import ConfigEntry, merge_entries
from agent_cluster.events import SessionEventLog
from agent_cluster.projects import ProjectStore, SessionIndexEntry
from agent_cluster.rbac import PERMISSION_MATRIX, PERMISSIONS, RbacStore
from agent_cluster.server import WorkbenchHandler, WorkbenchServer
from agent_cluster.tenancy import (
    QuotaExceededError,
    TenantStore,
    isolate_config,
    tenant_payload,
)


# ---------------------------------------------------------------------------
# 权限：tenants.manage（只增不改既有权限）
# ---------------------------------------------------------------------------


def test_tenants_manage_permission_only_added():
    assert "tenants.manage" in PERMISSIONS
    # 既有权限集合保持不变（14.9 的 10 项全在）
    assert {"project.read", "project.write", "agent.run", "budget.manage", "gate.approve",
            "release.approve", "team.manage", "users.manage", "audit.read", "roles.read"} <= set(PERMISSIONS)


def test_tenants_manage_matrix_governance_only():
    assert "tenants.manage" in PERMISSION_MATRIX["governance"]
    for role_id, perms in PERMISSION_MATRIX.items():
        if role_id != "governance":
            assert "tenants.manage" not in perms, f"{role_id} 不应拥有 tenants.manage"
    store = RbacStore()
    assert store.require("admin", "tenants.manage") is True  # admin 直通


# ---------------------------------------------------------------------------
# TenantStore CRUD（fail loud 同 RbacStore 风格）
# ---------------------------------------------------------------------------


def test_tenant_crud_fail_loud(tmp_path):
    store = TenantStore(root=tmp_path)
    assert store.list_tenants() == []
    tenant = store.add_tenant(id="acme", name="Acme 租户")
    assert tenant.id == "acme" and tenant.name == "Acme 租户"
    assert tenant.project_limit == 0 and tenant.session_limit == 0  # 0=不设限
    assert store.get_tenant("acme") is tenant
    assert [t.id for t in store.list_tenants()] == ["acme"]
    with pytest.raises(ValueError, match="acme"):
        store.add_tenant(id="acme", name="重复")
    with pytest.raises(ValueError, match="id"):
        store.add_tenant(id="", name="x")
    with pytest.raises(ValueError, match="name"):
        store.add_tenant(id="x", name="")
    with pytest.raises(ValueError, match="project_limit"):
        store.add_tenant(id="bad", name="Bad", project_limit=-1)
    with pytest.raises(ValueError, match="session_limit"):
        store.add_tenant(id="bad2", name="Bad2", session_limit=-1)
    with pytest.raises(KeyError, match="nope"):
        store.get_tenant("nope")
    store.remove_tenant("acme")
    assert store.list_tenants() == []
    with pytest.raises(KeyError, match="acme"):
        store.remove_tenant("acme")


# ---------------------------------------------------------------------------
# 存储命名空间：ProjectStore 按租户隔离
# ---------------------------------------------------------------------------


def test_project_store_tenant_namespace_isolated(tmp_path):
    store = TenantStore(root=tmp_path)
    alpha = store.namespaced_project_store("alpha")
    beta = store.namespaced_project_store("beta")
    assert alpha.tenant_id == "alpha"
    assert alpha.projects_dir == (tmp_path / "tenants" / "alpha" / "projects").resolve()
    ws = tmp_path / "ws-a"
    ws.mkdir()
    alpha.create_project(name="A", workspace=ws)
    # 租户命名空间与默认/其他租户互不可见
    assert len(alpha.list()) == 1
    assert beta.list() == []
    assert ProjectStore(root=tmp_path).list() == []
    # 同一租户路径可重建访问
    assert ProjectStore(root=tmp_path, tenant_id="alpha").list()[0].name == "A"
    assert (tmp_path / "tenants" / "alpha" / "projects").is_dir()


def test_tenant_remove_purges_namespace(tmp_path):
    store = TenantStore(root=tmp_path)
    store.add_tenant(id="acme", name="Acme")
    ws = tmp_path / "ws-a"
    ws.mkdir()
    store.namespaced_project_store("acme").create_project(name="A", workspace=ws)
    assert (tmp_path / "tenants" / "acme").is_dir()
    store.remove_tenant("acme")
    assert not (tmp_path / "tenants" / "acme").exists()


# ---------------------------------------------------------------------------
# 配额（项目数/会话数上限）
# ---------------------------------------------------------------------------


def test_usage_counts_projects_and_sessions(tmp_path):
    store = TenantStore(root=tmp_path)
    store.add_tenant(id="acme", name="Acme")
    usage = store.usage("acme")
    assert usage["projects"] == 0 and usage["sessions"] == 0
    assert usage["project_limit"] == 0 and usage["session_limit"] == 0
    ws = tmp_path / "ws-a"
    ws.mkdir()
    pstore = store.namespaced_project_store("acme")
    project = pstore.create_project(name="A", workspace=ws)
    assert store.usage("acme")["projects"] == 1
    pstore.index_session(
        project.project_id,
        SessionIndexEntry(session_id="s1", goal="g1", status="active", workspace=str(ws)),
    )
    pstore.index_session(
        project.project_id,
        SessionIndexEntry(session_id="s2", goal="g2", status="active", workspace=str(ws)),
    )
    assert store.usage("acme")["sessions"] == 2
    with pytest.raises(KeyError, match="nope"):
        store.usage("nope")


def test_quota_enforced_fail_loud(tmp_path):
    store = TenantStore(root=tmp_path)
    store.add_tenant(id="acme", name="Acme", project_limit=1, session_limit=1)
    store.ensure_quota("acme", "projects")
    ws = tmp_path / "ws-a"
    ws.mkdir()
    pstore = store.namespaced_project_store("acme")
    project = pstore.create_project(name="A", workspace=ws)
    with pytest.raises(QuotaExceededError, match="项目"):
        store.ensure_quota("acme", "projects")
    store.ensure_quota("acme", "sessions")
    pstore.index_session(
        project.project_id,
        SessionIndexEntry(session_id="s1", goal="g1", status="active", workspace=str(ws)),
    )
    with pytest.raises(QuotaExceededError, match="会话"):
        store.ensure_quota("acme", "sessions")
    # 0=不设限：默认租户永不超限
    store.add_tenant(id="free", name="Free")
    store.ensure_quota("free", "projects")
    store.ensure_quota("free", "sessions")
    with pytest.raises(ValueError, match="bogus"):
        store.ensure_quota("free", "bogus")


# ---------------------------------------------------------------------------
# per-tenant 配置分层：配置块按 tenant id 隔离
# ---------------------------------------------------------------------------


def test_isolate_config_by_tenant_id():
    entries = merge_entries(
        [],
        [
            ConfigEntry(id="llm", payload={"provider": "deepseek", "model": "v4"}),
            ConfigEntry(id="tenants.alpha.llm", payload={"provider": "deepseek", "model": "v4-alpha"}),
            ConfigEntry(id="tenants.beta.llm", payload={"provider": "deepseek", "model": "v4-beta"}),
        ],
    )
    alpha = {e.id: dict(e.payload) for e in isolate_config(entries, "alpha")}
    assert alpha["llm"]["model"] == "v4-alpha"
    assert "tenants.alpha.llm" not in alpha
    beta = {e.id: dict(e.payload) for e in isolate_config(entries, "beta")}
    assert beta["llm"]["model"] == "v4-beta"
    gamma = {e.id: dict(e.payload) for e in isolate_config(entries, "gamma")}
    assert gamma["llm"]["model"] == "v4"  # 全局块对无覆盖租户可见
    assert isolate_config([], "alpha") == []


# ---------------------------------------------------------------------------
# 事件日志：tenant_id 字段（SessionEvent payload 可携带）
# ---------------------------------------------------------------------------


def test_session_event_payload_carries_tenant_id():
    log = SessionEventLog("s1")
    log.append("session/start", tenant_payload({"goal": "g"}, "alpha"))
    event = log.events[0]
    assert event.payload["tenant_id"] == "alpha"
    assert event.payload["goal"] == "g"
    assert event.to_dict()["payload"]["tenant_id"] == "alpha"
    # 派生消息不因 tenant 字段变化（session/start 非 surface 事件）
    assert log.derive_messages() == []


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
def tenants_server(tmp_path, monkeypatch):
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
        httpd.server_close()


def test_tenants_endpoints_crud_and_usage(tenants_server):
    port, _ = tenants_server
    status, body = _request(port, "GET", "/api/v1/tenants")
    assert status == 200 and body["ok"] is True
    assert body["data"]["tenants"] == []
    status, created = _request(port, "POST", "/api/v1/tenants", {"id": "acme", "name": "Acme"})
    assert status == 201, created
    assert created["data"]["tenant"]["id"] == "acme"
    status, body = _request(port, "GET", "/api/v1/tenants")
    assert [t["id"] for t in body["data"]["tenants"]] == ["acme"]
    status, usage = _request(port, "GET", "/api/v1/tenants/acme/usage")
    assert status == 200, usage
    assert usage["data"]["usage"]["projects"] == 0
    assert usage["data"]["usage"]["sessions"] == 0
    status, body = _request(port, "DELETE", "/api/v1/tenants/acme")
    assert status == 200 and body["data"]["removed"] == "acme"
    status, body = _request(port, "GET", "/api/v1/tenants")
    assert body["data"]["tenants"] == []
    status, body = _request(port, "GET", "/api/v1/tenants/acme/usage")
    assert status == 404


def test_tenants_manage_permission_enforced(tenants_server):
    port, workbench = tenants_server
    workbench.rbac.add_user(id="bob", name="鲍勃", role_ids=["frontend"])
    status, body = _request(port, "POST", "/api/v1/tenants", {"id": "x", "name": "X"}, user="bob")
    assert status == 403, body
    assert body.get("code") == "permission_denied"
    status, body = _request(port, "DELETE", "/api/v1/tenants/x", user="bob")
    assert status == 403
    # 读端点对任何已登录用户开放
    status, _ = _request(port, "GET", "/api/v1/tenants", user="bob")
    assert status == 200


def test_tenant_scoped_project_creation_and_quota(tenants_server, tmp_path):
    port, workbench = tenants_server
    status, body = _request(port, "POST", "/api/v1/tenants", {"id": "acme", "name": "Acme", "project_limit": 1})
    assert status == 201, body
    ws = tmp_path / "ws-acme"
    ws.mkdir()
    status, created = _request(port, "POST", "/api/v1/projects", {"name": "租户项目", "workspace": str(ws), "tenant_id": "acme"})
    assert status == 201, created
    assert created["data"]["tenant_id"] == "acme"
    assert workbench.tenants.usage("acme")["projects"] == 1
    # 配额超限 → 409 quota_exceeded
    ws2 = tmp_path / "ws-acme-2"
    ws2.mkdir()
    status, body = _request(port, "POST", "/api/v1/projects", {"name": "超限项目", "workspace": str(ws2), "tenant_id": "acme"})
    assert status == 409, body
    assert body.get("code") == "quota_exceeded"
    # 未知租户 → 404
    status, body = _request(port, "POST", "/api/v1/projects", {"name": "孤儿项目", "workspace": str(ws2), "tenant_id": "nope"})
    assert status == 404
