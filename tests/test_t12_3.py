"""T12.3 serve 后端：REST+SSE + 全局索引 + 认证 + 会话生命周期。"""

from __future__ import annotations

import json
import queue
import threading
import time
import urllib.request
import urllib.error
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

import agent_cluster.server as server_mod
from agent_cluster.server import GlobalIndex, SessionEventLog, WorkbenchHandler, WorkbenchServer


@pytest.fixture()
def server(tmp_path, monkeypatch):
    monkeypatch.setattr(server_mod, "INDEX_DIR", tmp_path / "home")
    ws = WorkbenchServer(host="127.0.0.1", port=0, auth_token="")
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), WorkbenchHandler)
    httpd.workbench = ws
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield {"ws": ws, "httpd": httpd, "port": port, "workspace": tmp_path / "proj-a"}
    httpd.shutdown()
    httpd.server_close()


def _get(port, path, token=None):
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}")
    if token:
        req.add_header("X-Auth-Token", token)
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def _get_raw(port, path, token=None):
    """返回 (status, 原始文本)（SSE 等多行响应）。"""
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}")
    if token:
        req.add_header("X-Auth-Token", token)
    with urllib.request.urlopen(req, timeout=10) as resp:
        return resp.status, resp.read().decode("utf-8")


def _post(port, path, body=None, token=None):
    data = json.dumps(body or {}).encode("utf-8")
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("X-Auth-Token", token)
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


# ---------------------------------------------------------------------------
# 事件日志
# ---------------------------------------------------------------------------


def test_event_log_replay_and_subscribe():
    log = SessionEventLog()
    assert log.append({"type": "a"}) == 0
    assert log.append({"type": "b"}) == 1
    replayed = log.replay(since=1)
    assert [e["type"] for e in replayed] == ["b"]
    assert replayed[0]["seq"] == 1
    sub = log.subscribe()
    log.append({"type": "c"})
    event = sub.get(timeout=2)
    assert event["type"] == "c"
    log.unsubscribe(sub)


# ---------------------------------------------------------------------------
# 全局索引
# ---------------------------------------------------------------------------


def test_global_index_persistence(tmp_path, monkeypatch):
    monkeypatch.setattr(server_mod, "INDEX_DIR", tmp_path / "home")
    index = GlobalIndex()
    index.add_project("p1", "项目A", str(tmp_path / "ws"))
    index.add_session("s1", "p1", str(tmp_path / "ws"), "目标", "codex")
    index2 = GlobalIndex()
    assert index2.projects["p1"]["name"] == "项目A"
    assert index2.sessions["s1"]["goal"] == "目标"


# ---------------------------------------------------------------------------
# HTTP 端点
# ---------------------------------------------------------------------------


def test_status_endpoint(server):
    status, body = _get(server["port"], "/api/v1/status")
    assert status == 200
    assert body["ok"] is True
    assert body["data"]["projects"] == 0


def test_project_lifecycle_via_api(server):
    port = server["port"]
    ws = server["workspace"]
    status, created = _post(port, "/api/v1/projects", {"name": "待办应用", "workspace": str(ws)})
    assert status == 201
    project_id = created["data"]["id"]
    status, listed = _get(port, "/api/v1/projects")
    assert status == 200
    assert any(p["id"] == project_id for p in listed["data"])


def test_workspace_tree_and_file(server):
    port = server["port"]
    ws = server["workspace"]
    (ws / "docs").mkdir(parents=True)
    (ws / "docs" / "README.md").write_text("# 你好", encoding="utf-8")
    (ws / "app.py").write_text("print(1)", encoding="utf-8")
    status, created = _post(port, "/api/v1/projects", {"name": "p", "workspace": str(ws)})
    pid = created["data"]["id"]
    status, tree = _get(port, f"/api/v1/projects/{pid}/workspace/tree?path=docs")
    assert status == 200
    assert tree["data"]["entries"][0]["name"] == "README.md"
    status, file_body = _get(port, f"/api/v1/projects/{pid}/workspace/file?path=docs/README.md")
    assert status == 200
    assert "你好" in file_body["data"]["text"]


def test_workspace_path_traversal_rejected(server):
    port = server["port"]
    ws = server["workspace"]
    outside = server["workspace"].parent / "secret.txt"
    outside.write_text("top secret", encoding="utf-8")
    status, created = _post(port, "/api/v1/projects", {"name": "p", "workspace": str(ws)})
    pid = created["data"]["id"]
    status, body = _get(port, f"/api/v1/projects/{pid}/workspace/file?path=../secret.txt")
    assert status == 500
    assert body["ok"] is False


def test_auth_token_required(tmp_path, monkeypatch):
    monkeypatch.setattr(server_mod, "INDEX_DIR", tmp_path / "home")
    ws = WorkbenchServer(host="127.0.0.1", port=0, auth_token="s3cret")
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), WorkbenchHandler)
    httpd.workbench = ws
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        status, body = _get(port, "/api/v1/status")
        assert status == 401
        status, body = _get(port, "/api/v1/status", token="s3cret")
        assert status == 200
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_memory_endpoints(server):
    port = server["port"]
    ws = server["workspace"]
    status, created = _post(port, "/api/v1/projects", {"name": "p", "workspace": str(ws)})
    pid = created["data"]["id"]
    status, body = _get(port, f"/api/v1/projects/{pid}/memory")
    assert status == 200
    assert body["ok"] is True


# ---------------------------------------------------------------------------
# 会话生命周期（确定性 + --yes，无 key）
# ---------------------------------------------------------------------------


def test_session_lifecycle_via_api(server):
    port = server["port"]
    ws = server["workspace"]
    ws.mkdir(parents=True, exist_ok=True)
    status, created = _post(port, "/api/v1/projects", {"name": "会话项目", "workspace": str(ws)})
    pid = created["data"]["id"]
    flow = Path("examples/flows/build-new-project.yaml").resolve()
    status, started = _post(
        port,
        f"/api/v1/projects/{pid}/sessions",
        {
            "goal": "做一个待办事项应用",
            "flow": str(flow),
            "model": "deterministic",
            "deterministic": True,
            "yes": True,
        },
    )
    assert status == 201
    sid = started["data"]["session_id"]

    deadline = time.time() + 90
    final = None
    while time.time() < deadline:
        status, body = _get(port, f"/api/v1/sessions/{sid}")
        final = body["data"]
        if final["status"] in ("completed", "failed"):
            break
        time.sleep(0.5)
    assert final is not None
    assert final["status"] == "completed", f"会话未完成：{final.get('error')}"
    assert final["exit_code"] in (0, 1)  # 确定性无真实测试时 QA 保持 review -> 1
    assert final["token"]["used"] >= 0

    # SSE 事件可重放
    status, sse = _get_raw(port, f"/api/v1/sessions/{sid}/events?since=0")
    assert status == 200
    events = [json.loads(line[5:]) for line in sse.splitlines() if line.startswith("data:")]
    assert any(e["type"] == "session.end" for e in events)
    assert any(e["type"] == "approval.pending" for e in events) or any(
        e["type"] in ("node", "log") for e in events
    )
