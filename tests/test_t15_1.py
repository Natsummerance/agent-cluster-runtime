"""Task 15.15 租户会话运行时隔离：会话数据流按 tenant_id 切分 + 全局端点按租户过滤。

§17 #1 验收：双租户互不可见断言 —— 租户 A 的会话/项目/事件对租户 B 完全不可见。
- 会话运行时携带 tenant_id（ServerSession.tenant_id，派生自项目归属）。
- 会话事件日志 payload 自动并入 tenant_id（session.start 等）。
- 全局端点（projects/sessions/metrics/memory/evolution）按 X-Tenant-Id 过滤。
- 租户项目会话读写走租户命名空间 ProjectStore（tenants/<tid>/projects）。
"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

import agent_cluster.server as server_mod
from agent_cluster.memory import MemoryStore
from agent_cluster.projects import ProjectStore
from agent_cluster.server import WorkbenchHandler, WorkbenchServer

GOAL = "租户隔离演示"


def _request(port, method, path, body=None, tenant=None):
    data = json.dumps(body or {}).encode("utf-8") if body is not None else None
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    if tenant:
        req.add_header("X-Tenant-Id", tenant)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
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
        workbench.manager.shutdown()
        httpd.shutdown()
        httpd.server_close()


def _add_tenant(port, tenant_id, name):
    status, body = _request(port, "POST", "/api/v1/tenants", {"id": tenant_id, "name": name})
    assert status == 201, body
    return body["data"]["tenant"]


def _create_project(port, tmp_path, name, tenant=None):
    ws = tmp_path / f"ws-{name}"
    ws.mkdir()
    status, body = _request(
        port, "POST", "/api/v1/projects", {"name": name, "workspace": str(ws)}, tenant=tenant
    )
    assert status == 201, body
    return body["data"]["id"]


def _start_session(port, project_id, tenant=None):
    status, body = _request(
        port,
        "POST",
        f"/api/v1/projects/{project_id}/sessions",
        {"goal": GOAL, "model": "deterministic", "deterministic": True, "yes": False},
        tenant=tenant,
    )
    assert status == 201, body
    return body["data"]["session_id"]


# ---------------------------------------------------------------------------
# 运行时隔离：会话携带 tenant_id + 事件 payload 带租户
# ---------------------------------------------------------------------------


def test_session_carries_tenant_and_event_payload(tenants_server, tmp_path):
    port, workbench = tenants_server
    _add_tenant(port, "alpha", "Alpha")
    _add_tenant(port, "beta", "Beta")
    pa = _create_project(port, tmp_path, "pa", tenant="alpha")
    pb = _create_project(port, tmp_path, "pb", tenant="beta")
    sa = _start_session(port, pa, tenant="alpha")
    sb = _start_session(port, pb, tenant="beta")

    session_a = workbench.get_session(sa)
    session_b = workbench.get_session(sb)
    assert session_a.tenant_id == "alpha"
    assert session_b.tenant_id == "beta"

    # 会话事件日志 payload 自动并入 tenant_id
    start_a = next(e for e in session_a.log.replay() if e["type"] == "session.start")
    assert start_a["payload"]["tenant_id"] == "alpha"
    start_b = next(e for e in session_b.log.replay() if e["type"] == "session.start")
    assert start_b["payload"]["tenant_id"] == "beta"

    # 会话注册记录落在租户命名空间（全局 ProjectStore 不可见）
    assert workbench.tenants.namespaced_project_store("alpha").get(pa) is not None
    assert workbench.tenants.namespaced_project_store("beta").get(pb) is not None
    with pytest.raises(KeyError):
        ProjectStore(root=workbench._project_store.root).get(pa)


# ---------------------------------------------------------------------------
# 全局端点按租户过滤（双租户互不可见）
# ---------------------------------------------------------------------------


def test_list_projects_filtered_by_tenant(tenants_server, tmp_path):
    port, _ = tenants_server
    _add_tenant(port, "alpha", "Alpha")
    _add_tenant(port, "beta", "Beta")
    pa = _create_project(port, tmp_path, "pa", tenant="alpha")
    pb = _create_project(port, tmp_path, "pb", tenant="beta")

    status, body = _request(port, "GET", "/api/v1/projects", tenant="alpha")
    assert status == 200
    assert [p["id"] for p in body["data"]] == [pa]
    status, body = _request(port, "GET", "/api/v1/projects", tenant="beta")
    assert [p["id"] for p in body["data"]] == [pb]
    # 无租户上下文（管理面）仍可见全部
    status, body = _request(port, "GET", "/api/v1/projects")
    assert {p["id"] for p in body["data"]} == {pa, pb}


def test_cross_tenant_project_and_session_invisible(tenants_server, tmp_path):
    port, _ = tenants_server
    _add_tenant(port, "alpha", "Alpha")
    _add_tenant(port, "beta", "Beta")
    pa = _create_project(port, tmp_path, "pa", tenant="alpha")
    sa = _start_session(port, pa, tenant="alpha")

    # 项目：跨租户 404（不泄露存在性），同租户 200
    status, body = _request(port, "GET", f"/api/v1/projects/{pa}", tenant="beta")
    assert status == 404 and body.get("code") == "not_found"
    status, body = _request(port, "GET", f"/api/v1/projects/{pa}", tenant="alpha")
    assert status == 200 and body["data"]["project_id"] == pa

    # 会话：跨租户 404，同租户 200
    status, _ = _request(port, "GET", f"/api/v1/sessions/{sa}", tenant="beta")
    assert status == 404
    status, body = _request(port, "GET", f"/api/v1/sessions/{sa}", tenant="alpha")
    assert status == 200 and body["data"]["session_id"] == sa

    # 项目子资源（会话列表/记忆/工作区）+ 会话事件/审计/导出：跨租户一律 404
    status, _ = _request(port, "GET", f"/api/v1/projects/{pa}/sessions", tenant="beta")
    assert status == 404
    status, _ = _request(port, "GET", f"/api/v1/projects/{pa}/memory", tenant="beta")
    assert status == 404
    status, _ = _request(port, "GET", f"/api/v1/projects/{pa}/workspace/tree", tenant="beta")
    assert status == 404
    status, _ = _request(port, "GET", f"/api/v1/sessions/{sa}/events", tenant="beta")
    assert status == 404
    status, _ = _request(port, "GET", f"/api/v1/sessions/{sa}/audit", tenant="beta")
    assert status == 404
    status, _ = _request(port, "GET", f"/api/v1/sessions/{sa}/audit/export?format=json", tenant="beta")
    assert status == 404


def test_start_session_requires_matching_tenant(tenants_server, tmp_path):
    port, workbench = tenants_server
    _add_tenant(port, "alpha", "Alpha")
    _add_tenant(port, "beta", "Beta")
    pa = _create_project(port, tmp_path, "pa", tenant="alpha")

    # 跨租户启动 → 404；同租户 → 201 且运行时归属正确
    status, _ = _request(
        port,
        "POST",
        f"/api/v1/projects/{pa}/sessions",
        {"goal": GOAL, "model": "deterministic", "deterministic": True, "yes": False},
        tenant="beta",
    )
    assert status == 404
    status, body = _request(
        port,
        "POST",
        f"/api/v1/projects/{pa}/sessions",
        {"goal": GOAL, "model": "deterministic", "deterministic": True, "yes": False},
        tenant="alpha",
    )
    assert status == 201, body
    assert workbench.get_session(body["data"]["session_id"]).tenant_id == "alpha"


def test_create_project_tenant_from_header(tenants_server, tmp_path):
    port, _ = tenants_server
    _add_tenant(port, "alpha", "Alpha")
    _add_tenant(port, "beta", "Beta")
    # 头部租户上下文缺省补位：不传 body.tenant_id 也归属头部租户
    ws = tmp_path / "ws-h"
    ws.mkdir()
    status, body = _request(
        port, "POST", "/api/v1/projects", {"name": "h", "workspace": str(ws)}, tenant="alpha"
    )
    assert status == 201, body
    assert body["data"]["tenant_id"] == "alpha"
    # body 与头部冲突 → 404（防跨租户冒建）
    ws2 = tmp_path / "ws-h2"
    ws2.mkdir()
    status, _ = _request(
        port,
        "POST",
        "/api/v1/projects",
        {"name": "h2", "workspace": str(ws2), "tenant_id": "beta"},
        tenant="alpha",
    )
    assert status == 404


def test_metrics_filtered_by_tenant(tenants_server, tmp_path):
    port, _ = tenants_server
    _add_tenant(port, "alpha", "Alpha")
    _add_tenant(port, "beta", "Beta")
    pa = _create_project(port, tmp_path, "pa", tenant="alpha")
    pb = _create_project(port, tmp_path, "pb", tenant="beta")
    sa = _start_session(port, pa, tenant="alpha")
    sb = _start_session(port, pb, tenant="beta")

    status, body = _request(port, "GET", "/api/v1/metrics", tenant="alpha")
    assert status == 200
    assert {s["session_id"] for s in body["data"]["sessions"]} == {sa}
    status, body = _request(port, "GET", "/api/v1/metrics", tenant="beta")
    assert {s["session_id"] for s in body["data"]["sessions"]} == {sb}
    status, body = _request(port, "GET", "/api/v1/metrics")
    assert {s["session_id"] for s in body["data"]["sessions"]} == {sa, sb}


# ---------------------------------------------------------------------------
# memory / evolution 数据流按租户命名空间切分
# ---------------------------------------------------------------------------


def test_memory_and_evolution_tenant_namespaced(tenants_server, tmp_path):
    port, workbench = tenants_server
    _add_tenant(port, "alpha", "Alpha")
    _add_tenant(port, "beta", "Beta")

    alpha_mem = workbench.memory_store(None, tenant_id="alpha")
    item_id = alpha_mem.save_session_summary(
        session_id="s1", title="Alpha 摘要", content="只属于 alpha 的记忆", source="test"
    )
    assert item_id
    # 同租户可见、异租户/全局不可见
    assert workbench.memory_store(None, tenant_id="alpha").get(item_id) is not None
    assert workbench.memory_store(None, tenant_id="beta").get(item_id) is None
    assert workbench.memory_store(None).get(item_id) is None
    assert MemoryStore(workbench._project_store.root).get(item_id) is None

    # evolution 桥根目录落在租户命名空间（提案文件互不重叠）
    alpha_bridge = workbench.evolution_bridge(None, tenant_id="alpha")
    beta_bridge = workbench.evolution_bridge(None, tenant_id="beta")
    assert alpha_bridge.root == workbench.tenants.tenant_dir("alpha").resolve()
    assert beta_bridge.root == workbench.tenants.tenant_dir("beta").resolve()
    assert alpha_bridge.proposals_path.parent != beta_bridge.proposals_path.parent
