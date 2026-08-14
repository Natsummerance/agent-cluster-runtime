from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

import agent_cluster.server as server_mod
from agent_cluster.dependency_graph import CycleError, DependencyEdge, DependencyGraph
from agent_cluster.server import WorkbenchHandler, WorkbenchServer


# ---------------------------------------------------------------------------
# DependencyEdge / DependencyGraph CRUD（fail loud）
# ---------------------------------------------------------------------------


def test_dependency_graph_add_list_get_remove():
    graph = DependencyGraph()
    first = graph.add_edge(from_project="payments", to_project="ledger", from_task="t1", to_task="t2", type="build")
    second = graph.add_edge(from_project="payments", to_project="auth", type="runtime")
    assert first.id and second.id and first.id != second.id
    assert first.from_project == "payments" and first.to_project == "ledger"
    assert first.from_task == "t1" and first.to_task == "t2" and first.type == "build"
    assert second.from_task == "" and second.to_task == "" and second.type == "runtime"
    assert first.created_at
    assert [edge.id for edge in graph.list_edges()] == [first.id, second.id]
    assert graph.get_edge(first.id) is first
    with pytest.raises(KeyError, match="nope"):
        graph.get_edge("nope")
    graph.remove_edge(first.id)
    assert [edge.id for edge in graph.list_edges()] == [second.id]
    with pytest.raises(KeyError, match=first.id):
        graph.remove_edge(first.id)


def test_dependency_graph_add_fail_loud_invalid_input():
    graph = DependencyGraph()
    with pytest.raises(ValueError, match="from_project"):
        graph.add_edge(from_project="", to_project="ledger")
    with pytest.raises(ValueError, match="to_project"):
        graph.add_edge(from_project="payments", to_project="")
    # 自环即环 → CycleError（fail loud）
    with pytest.raises(CycleError):
        graph.add_edge(from_project="payments", to_project="payments")
    # 完全重复的边拒绝（fail loud）；不同 type/任务的平行边允许
    graph.add_edge(from_project="payments", to_project="ledger")
    with pytest.raises(ValueError, match="已存在"):
        graph.add_edge(from_project="payments", to_project="ledger")
    graph.add_edge(from_project="payments", to_project="ledger", type="runtime")
    assert len(graph.list_edges()) == 2


def test_dependency_graph_cycle_detection_fail_loud():
    graph = DependencyGraph()
    graph.add_edge(from_project="a", to_project="b")
    graph.add_edge(from_project="b", to_project="c")
    with pytest.raises(CycleError) as excinfo:
        graph.add_edge(from_project="c", to_project="a")
    message = str(excinfo.value)
    assert "c" in message and "a" in message and "b" in message
    assert "->" in message
    # 失败后图不变（边未写入）
    assert len(graph.list_edges()) == 2
    # 间接环同样拒绝：d -> a 后 a -> d 成环
    graph.add_edge(from_project="d", to_project="a")
    with pytest.raises(CycleError):
        graph.add_edge(from_project="a", to_project="d")
    assert len(graph.list_edges()) == 3


def test_dependency_graph_impact_bfs_downstream_closure():
    graph = DependencyGraph()
    graph.add_edge(from_project="payments", to_project="ledger")   # payments 依赖 ledger
    graph.add_edge(from_project="payments", to_project="auth")     # payments 依赖 auth
    graph.add_edge(from_project="checkout", to_project="payments") # checkout 依赖 payments
    graph.add_edge(from_project="ledger", to_project="db")         # ledger 依赖 db
    # ledger 的变更影响 payments、checkout（BFS 下游闭包）
    assert graph.impact_of("ledger") == {"payments", "checkout"}
    # db 的变更影响 ledger、payments、checkout
    assert graph.impact_of("db") == {"ledger", "payments", "checkout"}
    # 最下游项目无下游
    assert graph.impact_of("checkout") == set()
    assert graph.impact_of("unknown") == set()


def test_dependency_graph_schedule_order_topological():
    graph = DependencyGraph()
    # 依赖先行的确定性拓扑序：db -> auth/ledger -> payments -> checkout
    graph.add_edge(from_project="payments", to_project="ledger")
    graph.add_edge(from_project="payments", to_project="auth")
    graph.add_edge(from_project="checkout", to_project="payments")
    graph.add_edge(from_project="ledger", to_project="db")
    order = graph.schedule_order()
    assert set(order) == {"payments", "ledger", "auth", "checkout", "db"}
    assert order.index("db") < order.index("ledger") < order.index("payments") < order.index("checkout")
    assert order.index("auth") < order.index("payments")
    # 空图与孤立节点
    assert DependencyGraph().schedule_order() == []
    lonely = DependencyGraph()
    lonely.add_edge(from_project="standalone", to_project="z")
    assert "standalone" in lonely.schedule_order() and "z" in lonely.schedule_order()
    assert lonely.schedule_order().index("z") < lonely.schedule_order().index("standalone")


def test_dependency_graph_schedule_order_cycle_raises():
    # 直接注入成环边（add_edge 会拦截环，仅用于恢复/内部构造路径的健壮性测试）
    edges = [
        DependencyEdge(id="e1", from_project="a", to_project="b"),
        DependencyEdge(id="e2", from_project="b", to_project="c"),
        DependencyEdge(id="e3", from_project="c", to_project="a"),
    ]
    graph = DependencyGraph(edges=edges)
    with pytest.raises(CycleError) as excinfo:
        graph.schedule_order()
    assert "a" in str(excinfo.value) and "c" in str(excinfo.value)
    # 注入的边仍可正常列出/删除
    assert len(graph.list_edges()) == 3
    graph.remove_edge("e3")
    assert graph.schedule_order()  # 去掉环边后可排


# ---------------------------------------------------------------------------
# serve 端点
# ---------------------------------------------------------------------------


def _request(port, method, path, body=None, token=None):
    data = json.dumps(body or {}).encode("utf-8") if body is not None else None
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    if token is not None:
        req.add_header("X-Auth-Token", token)
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


@pytest.fixture()
def dependency_server(tmp_path, monkeypatch):
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


def test_dependency_endpoints_crud(dependency_server):
    port, workbench = dependency_server
    assert hasattr(workbench, "dependencies") and isinstance(workbench.dependencies, DependencyGraph)
    status, body = _request(port, "GET", "/api/v1/dependencies")
    assert status == 200 and body["ok"] is True
    assert body["data"]["edges"] == []
    status, created = _request(
        port, "POST", "/api/v1/dependencies",
        {"from_project": "payments", "to_project": "ledger", "from_task": "t1", "to_task": "t2", "type": "build"},
    )
    assert status == 201, created
    edge = created["data"]["edge"]
    assert edge["from_project"] == "payments" and edge["to_project"] == "ledger"
    assert edge["type"] == "build" and edge["id"]
    status, body = _request(port, "GET", "/api/v1/dependencies")
    assert [e["id"] for e in body["data"]["edges"]] == [edge["id"]]
    # 删除
    status, body = _request(port, "DELETE", f"/api/v1/dependencies/{edge['id']}")
    assert status == 200 and body["data"]["removed"] == edge["id"]
    status, body = _request(port, "GET", "/api/v1/dependencies")
    assert body["data"]["edges"] == []
    status, body = _request(port, "DELETE", f"/api/v1/dependencies/{edge['id']}")
    assert status == 404


def test_dependency_endpoints_cycle_conflict(dependency_server):
    port, _ = dependency_server
    ok = _request(port, "POST", "/api/v1/dependencies", {"from_project": "a", "to_project": "b"})
    assert ok[0] == 201
    ok = _request(port, "POST", "/api/v1/dependencies", {"from_project": "b", "to_project": "c"})
    assert ok[0] == 201
    status, body = _request(port, "POST", "/api/v1/dependencies", {"from_project": "c", "to_project": "a"})
    assert status == 409, body
    assert body.get("code") == "cycle_detected"
    assert "->" in body.get("error", "")
    # 非法输入 → 400 bad_request
    status, body = _request(port, "POST", "/api/v1/dependencies", {"from_project": "", "to_project": "b"})
    assert status == 400 and body.get("code") == "bad_request"
    status, body = _request(port, "POST", "/api/v1/dependencies", {"from_project": "a", "to_project": "b"})
    assert status == 400  # 重复边


def test_dependency_endpoints_impact(dependency_server):
    port, _ = dependency_server
    for from_, to in [("payments", "ledger"), ("payments", "auth"), ("checkout", "payments")]:
        assert _request(port, "POST", "/api/v1/dependencies", {"from_project": from_, "to_project": to})[0] == 201
    status, body = _request(port, "GET", "/api/v1/dependencies/impact?project_id=ledger")
    assert status == 200, body
    assert set(body["data"]["impact"]) == {"payments", "checkout"}
    status, body = _request(port, "GET", "/api/v1/dependencies/impact?project_id=checkout")
    assert body["data"]["impact"] == []
    status, body = _request(port, "GET", "/api/v1/dependencies/impact")
    assert status == 400 and body.get("code") == "bad_request"


def test_dependency_endpoints_auth_required(tmp_path, monkeypatch):
    monkeypatch.setattr(server_mod, "INDEX_DIR", tmp_path / "home")
    workbench = WorkbenchServer(host="127.0.0.1", port=0, auth_token="s3cret")
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), WorkbenchHandler)
    httpd.workbench = workbench
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        status, body = _request(port, "GET", "/api/v1/dependencies")
        assert status == 401 and body.get("code") == "not_authorized"
        status, body = _request(port, "POST", "/api/v1/dependencies", {"from_project": "a", "to_project": "b"})
        assert status == 401
        status, body = _request(port, "GET", "/api/v1/dependencies", token="s3cret")
        assert status == 200, body
    finally:
        httpd.shutdown()
        httpd.server_close()
