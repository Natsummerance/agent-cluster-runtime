"""T13.7 serve REST 扩展：错误码信封 / 项目与预算端点 / 恢复语义 / fork / stdin / cancel。

in-process WorkbenchServer + 随机端口 + deterministic 模型（沿用 test_t12_3 启动模式）。
"""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

import agent_cluster.server as server_mod
from agent_cluster.projects import GatePolicyConfig
from agent_cluster.server import WorkbenchHandler, WorkbenchServer

MINI_GATE_FLOW = """name: t13.7-mini
thread_id: "t:13.7"
nodes:
  - {id: start, type: start}
  - {id: requirements, type: agent, role: pm}
  - {id: requirement_gate, type: gate, gate: requirement_confirmation}
  - {id: end, type: end}
edges:
  - {from: start, to: requirements}
  - {from: requirements, to: requirement_gate}
  - {from: requirement_gate, to: end, on_accept: end, on_reject: requirements}
"""


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


def _request(port, method, path, body=None, token=None):
    data = json.dumps(body or {}).encode("utf-8") if body is not None else None
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("X-Auth-Token", token)
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def _get(port, path, token=None):
    return _request(port, "GET", path, None, token)


def _post(port, path, body=None, token=None):
    return _request(port, "POST", path, body, token)


def _patch(port, path, body=None, token=None):
    return _request(port, "PATCH", path, body, token)


def _create_project(port, tmp_path, name="p"):
    ws_dir = tmp_path / f"ws-{name}"
    ws_dir.mkdir()
    status, created = _post(port, "/api/v1/projects", {"name": name, "workspace": str(ws_dir)})
    assert status == 201, created
    return created["data"]["id"]


def _write_flow(tmp_path, flow_id="flow"):
    flow = tmp_path / f"{flow_id}.yaml"
    flow.write_text(MINI_GATE_FLOW, encoding="utf-8")
    return flow


def _wait_session(port, sid, statuses=("waiting_approval", "running"), timeout=30.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        status, body = _get(port, f"/api/v1/sessions/{sid}")
        if status == 200 and body["data"]["status"] in statuses:
            return body["data"]
        time.sleep(0.05)
    raise AssertionError(f"会话 {sid} 未在 {timeout}s 内进入 {statuses}")


def _start_waiting(port, pid, tmp_path):
    flow = _write_flow(tmp_path)
    status, started = _post(
        port,
        f"/api/v1/projects/{pid}/sessions",
        {"goal": "待办应用", "flow": str(flow), "model": "deterministic", "deterministic": True},
    )
    assert status == 201, started
    sid = started["data"]["session_id"]
    _wait_session(port, sid)
    return sid


def _start_finishing(port, pid, tmp_path):
    flow = _write_flow(tmp_path, flow_id="finish")
    status, started = _post(
        port,
        f"/api/v1/projects/{pid}/sessions",
        {
            "goal": "待办应用",
            "flow": str(flow),
            "model": "deterministic",
            "deterministic": True,
            "yes": True,
        },
    )
    assert status == 201, started
    sid = started["data"]["session_id"]
    _wait_session(port, sid, statuses=("completed", "failed"))
    return sid


# ---------------------------------------------------------------------------
# 错误码信封（§6.1）
# ---------------------------------------------------------------------------


def test_error_codes(server, tmp_path):
    port = server["port"]
    ws = server["ws"]
    pid = _create_project(port, tmp_path)
    waiting_sid = _start_waiting(port, pid, tmp_path)
    finished_sid = _start_finishing(port, pid, tmp_path)

    # 404 not_found
    status, body = _get(port, "/api/v1/projects/nope")
    assert status == 404 and body["code"] == "not_found"
    status, body = _post(port, "/api/v1/sessions/nope/cancel")
    assert status == 404 and body["code"] == "not_found"

    # 400 bad_request
    status, body = _post(port, f"/api/v1/projects/{pid}/sessions", {"goal": ""})
    assert status == 400 and body["code"] == "bad_request"
    status, body = _post(port, f"/api/v1/projects/{pid}/budget/unlock", {"additional_tokens": 0})
    assert status == 400 and body["code"] == "bad_request"

    # 409 conflict：已完成会话恢复启动
    status, body = _post(port, f"/api/v1/projects/{pid}/sessions", {"session_id": finished_sid, "goal": "x"})
    assert status == 409 and body["code"] == "conflict"

    # 409 budget_pool_exhausted：硬上限 = 1 且已有用量
    _patch(port, f"/api/v1/projects/{pid}", {"budget_pool": {"hard_limit_tokens": 1}})
    status, body = _post(port, f"/api/v1/projects/{pid}/sessions", {"goal": "再开一个"})
    assert status == 409 and body["code"] == "budget_pool_exhausted"

    # 409 fork_conflict：active 源
    status, body = _post(port, f"/api/v1/sessions/{waiting_sid}/fork", {"worktree": False})
    assert status == 409 and body["code"] == "fork_conflict"

    # 409 session_busy：终态会话注入 stdin
    status, body = _post(port, f"/api/v1/sessions/{finished_sid}/stdin", {"text": "注入"})
    assert status == 409 and body["code"] == "session_busy"

    # 401 not_authorized（独立认证实例）
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(server_mod, "INDEX_DIR", tmp_path / "home-auth")
    auth_ws = WorkbenchServer(host="127.0.0.1", port=0, auth_token="s3cret")
    auth_httpd = ThreadingHTTPServer(("127.0.0.1", 0), WorkbenchHandler)
    auth_httpd.workbench = auth_ws
    auth_port = auth_httpd.server_address[1]
    auth_thread = threading.Thread(target=auth_httpd.serve_forever, daemon=True)
    auth_thread.start()
    try:
        status, body = _get(auth_port, "/api/v1/status")
        assert status == 401 and body["code"] == "not_authorized"
    finally:
        auth_httpd.shutdown()
        auth_httpd.server_close()
        monkeypatch.undo()

    # 旧端点错误不含 code 时结构与 v0.5 相同（未知路由 / 旧 KeyError 出口）
    status, body = _get(port, "/api/v1/unknown-route")
    assert status == 404 and "code" not in body and body == {"ok": False, "error": body["error"]}
    status, body = _get(port, "/api/v1/sessions/nope/changes")
    assert status == 404 and "code" not in body


# ---------------------------------------------------------------------------
# 项目端点：详情 / PATCH / workspaces / budget
# ---------------------------------------------------------------------------


def test_project_endpoints(server, tmp_path):
    port = server["port"]
    ws = server["ws"]
    pid = _create_project(port, tmp_path)

    status, detail = _get(port, f"/api/v1/projects/{pid}")
    assert status == 200
    data = detail["data"]
    assert set(data) >= {"project_id", "name", "workspaces", "budget_pool", "gate_policy", "sessions", "metadata"}
    assert data["gate_policy"]["auto_review"] is True

    status, listed = _get(port, "/api/v1/projects")
    assert status == 200
    entry = listed["data"][0]
    for key in ("budget_pool", "session_count", "active_sessions", "dashboard"):
        assert key in entry
    assert set(entry["dashboard"]) == {"cost", "progress", "health", "updated_at"}

    # workspaces 注册（存在性校验 + 去重）
    extra = tmp_path / "extra"
    extra.mkdir()
    status, body = _post(port, f"/api/v1/projects/{pid}/workspaces", {"path": str(extra)})
    assert status == 200 and len(body["data"]["workspaces"]) == 2
    status, body = _post(port, f"/api/v1/projects/{pid}/workspaces", {"path": str(tmp_path / "missing")})
    assert status == 400 and body["code"] == "bad_request"

    # budget 快照字段
    status, budget = _get(port, f"/api/v1/projects/{pid}/budget")
    assert status == 200
    for key in ("hard_limit_tokens", "used", "remaining", "warn_raised", "unlocks"):
        assert key in budget["data"]

    # PATCH 合法更新
    status, body = _patch(port, f"/api/v1/projects/{pid}", {"name": "改名", "gate_policy": {"auto_review": False}})
    assert status == 200 and body["data"]["name"] == "改名"
    assert body["data"]["gate_policy"]["auto_review"] is False

    # PATCH 非法 gate_policy → 400 且 project.json 回退默认策略
    status, body = _patch(port, f"/api/v1/projects/{pid}", {"gate_policy": {"review_confidence_threshold": 1.7}})
    assert status == 400 and body["code"] == "bad_request"
    project_json = json.loads((ws._project_store.root / "projects" / pid / "project.json").read_text(encoding="utf-8"))
    assert project_json["gate_policy"] == GatePolicyConfig(auto_review=False).model_dump()
    # 非法 budget_pool 同样 400
    status, body = _patch(port, f"/api/v1/projects/{pid}", {"budget_pool": {"warn_ratio": 1.5}})
    assert status == 400 and body["code"] == "bad_request"
    # 空更新 400
    status, body = _patch(port, f"/api/v1/projects/{pid}", {"unknown_field": 1})
    assert status == 400 and body["code"] == "bad_request"


# ---------------------------------------------------------------------------
# 预算解锁：200 granted / 202 pending / approve / deny / 重复决 409
# ---------------------------------------------------------------------------


def test_unlock_200_202(server, tmp_path):
    port = server["port"]
    pid = _create_project(port, tmp_path)

    status, body = _post(port, f"/api/v1/projects/{pid}/budget/unlock", {"additional_tokens": 1000, "reason": "扩容"})
    assert status == 200 and body["data"]["status"] == "granted"
    status, budget = _get(port, f"/api/v1/projects/{pid}/budget")
    assert budget["data"]["hard_limit_tokens"] == 1000

    status, body = _patch(port, f"/api/v1/projects/{pid}", {"budget_pool": {"unlock_requires_approval": True}})
    assert status == 200
    status, body = _post(port, f"/api/v1/projects/{pid}/budget/unlock", {"additional_tokens": 500, "reason": "例外"})
    assert status == 202 and body["data"]["status"] == "pending"
    unlock_id = body["data"]["id"]
    status, budget = _get(port, f"/api/v1/projects/{pid}/budget")
    assert budget["data"]["hard_limit_tokens"] == 1000  # pending 不提额

    status, body = _post(port, f"/api/v1/projects/{pid}/budget/unlock/{unlock_id}/approve", {"decided_by": "pm"})
    assert status == 200 and body["data"]["status"] == "granted"
    status, budget = _get(port, f"/api/v1/projects/{pid}/budget")
    assert budget["data"]["hard_limit_tokens"] == 1500

    # 重复决 → 409
    status, body = _post(port, f"/api/v1/projects/{pid}/budget/unlock/{unlock_id}/deny", {})
    assert status == 409 and body["code"] == "conflict"

    # deny 另一条 pending
    status, body = _post(port, f"/api/v1/projects/{pid}/budget/unlock", {"additional_tokens": 200, "reason": "例外2"})
    unlock2 = body["data"]["id"]
    status, body = _post(port, f"/api/v1/projects/{pid}/budget/unlock/{unlock2}/deny", {})
    assert status == 200 and body["data"]["status"] == "denied"
    status, budget = _get(port, f"/api/v1/projects/{pid}/budget")
    assert budget["data"]["hard_limit_tokens"] == 1500  # deny 不提额


# ---------------------------------------------------------------------------
# dashboard 输出契约 + tasks 过滤/指派
# ---------------------------------------------------------------------------


def test_dashboard_and_tasks(server, tmp_path):
    port = server["port"]
    pid = _create_project(port, tmp_path)
    sid = _start_finishing(port, pid, tmp_path)

    status, body = _get(port, f"/api/v1/projects/{pid}/dashboard")
    assert status == 200
    dashboard = body["data"]
    assert set(dashboard) == {"cost", "progress", "health", "updated_at"}
    assert set(dashboard["cost"]) == {"used", "limit", "ratio", "score", "status", "estimated_usd"}
    assert dashboard["cost"]["status"] in ("ok", "warn", "critical")
    assert set(dashboard["progress"]) == {"score", "status", "phases"}
    assert set(dashboard["progress"]["phases"]) == {"total", "done"}
    assert set(dashboard["health"]) == {"score", "status", "sessions"}
    assert sid in dashboard["health"]["sessions"]

    # 注册表投影 + 过滤
    status, tasks = _get(port, f"/api/v1/projects/{pid}/tasks")
    assert status == 200
    entries = tasks["data"]
    assert any(entry["session_id"] == sid for entry in entries)
    status, filtered = _get(port, f"/api/v1/projects/{pid}/tasks?status=completed")
    assert any(entry["session_id"] == sid for entry in filtered["data"])
    status, filtered = _get(port, f"/api/v1/projects/{pid}/tasks?status=active")
    assert all(entry["session_id"] != sid for entry in filtered["data"])
    status, filtered = _get(port, f"/api/v1/projects/{pid}/tasks?q={urllib.parse.quote('待办应用')}")
    assert any(entry["session_id"] == sid for entry in filtered["data"])
    status, filtered = _get(port, f"/api/v1/projects/{pid}/tasks?q={urllib.parse.quote('zzz不存在')}")
    assert filtered["data"] == []

    # 指派 + 过滤 + 快照字段
    status, body = _patch(port, f"/api/v1/projects/{pid}/tasks/{sid}", {"assignee": "alice"})
    assert status == 200 and body["data"]["assignee"] == "alice"
    status, filtered = _get(port, f"/api/v1/projects/{pid}/tasks?assignee=alice")
    assert len(filtered["data"]) == 1 and filtered["data"][0]["session_id"] == sid
    status, filtered = _get(port, f"/api/v1/projects/{pid}/tasks?assignee=bob")
    assert filtered["data"] == []

    status, snapshots = _get(port, f"/api/v1/projects/{pid}/sessions")
    assert status == 200
    snapshot = next(item for item in snapshots["data"] if item["session_id"] == sid)
    for key in ("worktree", "merge_conflict", "assignee"):
        assert key in snapshot
    assert snapshot["assignee"] == "alice"


# ---------------------------------------------------------------------------
# start_session 恢复语义：active 恢复（线程复用）/ 404 / 409
# ---------------------------------------------------------------------------


def test_start_session_restore(server, tmp_path):
    port = server["port"]
    ws = server["ws"]
    pid = _create_project(port, tmp_path)
    sid = _start_waiting(port, pid, tmp_path)

    original = ws.manager.get(sid)
    thread1 = original.driver.store.record.thread_id
    with ws.manager._lock:
        ws.manager.sessions.pop(sid)  # 模拟服务重启后的进程内状态丢失

    status, body = _post(port, f"/api/v1/projects/{pid}/sessions", {"session_id": sid, "goal": "待办应用"})
    assert status == 201
    assert body["data"]["resumed"] is True
    assert body["data"]["session_id"] == sid

    deadline = time.time() + 60
    restored = None
    while time.time() < deadline:
        restored = ws.manager.sessions.get(sid)
        if restored is not None and restored.driver is not None and restored.status in ("running", "waiting_approval"):
            break
        if restored is not None and restored.status in ("failed", "completed"):
            break  # 终态：交由下方断言带状态/错误信息失败
        time.sleep(0.05)
    assert restored is not None and restored.driver is not None, (
        f"恢复未就绪：status={restored.status if restored else None} "
        f"error={restored.error if restored else ''!r}"
    )
    assert restored.driver.store.record.thread_id == thread1  # 复用 thread

    # 不存在 → 404
    status, body = _post(port, f"/api/v1/projects/{pid}/sessions", {"session_id": "nope", "goal": "x"})
    assert status == 404 and body["code"] == "not_found"

    # 已完成 → 409
    finished_sid = _start_finishing(port, pid, tmp_path)
    status, body = _post(port, f"/api/v1/projects/{pid}/sessions", {"session_id": finished_sid, "goal": "x"})
    assert status == 409 and body["code"] == "conflict"


# ---------------------------------------------------------------------------
# fork 端点：completed 源 200 / active 源 409 / 血缘超限 409 / dormant 登记
# ---------------------------------------------------------------------------


def test_fork_endpoint(server, tmp_path):
    port = server["port"]
    ws = server["ws"]
    pid = _create_project(port, tmp_path)
    sid = _start_finishing(port, pid, tmp_path)

    status, body = _post(port, f"/api/v1/sessions/{sid}/fork", {"goal": "衍生需求", "worktree": False})
    assert status == 200, body
    assert body["data"]["parent_session_id"] == sid
    assert body["data"]["fork_depth"] == 1
    child = body["data"]["session_id"]

    dormant = ws.manager.get(child)
    assert dormant.status == "dormant"
    assert dormant.thread is None
    record = ws._project_store.session_store(pid, child).record
    assert record.status == "active" and record.thread_id

    # active 源 → 409
    waiting_sid = _start_waiting(port, pid, tmp_path)
    status, body = _post(port, f"/api/v1/sessions/{waiting_sid}/fork", {"worktree": False})
    assert status == 409 and body["code"] == "fork_conflict"

    # 血缘超限：连续 fork 至 depth=5 后再派生 → 409（源须终态，§7）
    child_store = ws._project_store.session_store(pid, child)
    child_store.record = child_store.record.model_copy(update={"status": "completed"})
    child_store.save()
    current = child
    expected_depth = 2
    store = ws._project_store
    for _ in range(4):
        status, body = _post(port, f"/api/v1/sessions/{current}/fork", {"worktree": False})
        assert status == 200, body
        assert body["data"]["fork_depth"] == expected_depth
        current = body["data"]["session_id"]
        st = store.session_store(pid, current)
        st.record = st.record.model_copy(update={"status": "completed"})
        st.save()
        expected_depth += 1
    status, body = _post(port, f"/api/v1/sessions/{current}/fork", {"worktree": False})
    assert status == 409 and body["code"] == "fork_conflict"


# ---------------------------------------------------------------------------
# stdin / cancel 端点
# ---------------------------------------------------------------------------


def test_stdin_cancel_endpoints(server, tmp_path):
    port = server["port"]
    pid = _create_project(port, tmp_path)
    sid = _start_waiting(port, pid, tmp_path)

    status, body = _post(port, f"/api/v1/sessions/{sid}/stdin", {"text": "增加导出功能"})
    assert status == 202 and body["data"]["accepted"] == "增加导出功能"

    status, body = _post(port, f"/api/v1/sessions/{sid}/cancel")
    assert status == 202 and body["data"]["cancelled"] == "pending"

    _wait_session(port, sid, statuses=("completed", "failed"))
    status, body = _post(port, f"/api/v1/sessions/{sid}/stdin", {"text": "再来一条"})
    assert status == 409 and body["code"] == "session_busy"
