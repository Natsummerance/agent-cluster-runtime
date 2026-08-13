"""T13.16 端到端验收：in-process serve 全链路。

覆盖 §16「收尾验收」行 + 新功能专项：v0.5 迁移 → deterministic 会话至
completed → stdin 注入落 transcript/PRD → fork 终态派生血缘 → 预算池
warning/exhausted/unlock 审批 → dashboard/tasks 契约 → audit 新事件类型
→ SSE 哨兵与 Last-Event-ID 重放。
"""

from __future__ import annotations

import json
import socket
import threading
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

import agent_cluster.server as server_mod
from agent_cluster.server import WorkbenchHandler, WorkbenchServer

MINI_GATE_FLOW = """name: t13.16-mini
thread_id: "t:13.16"
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
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield {"ws": ws, "httpd": httpd, "port": port, "root": tmp_path}
    httpd.shutdown()
    httpd.server_close()


def _request(port, method, path, body=None):
    data = json.dumps(body or {}).encode("utf-8") if body is not None else None
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def _get(port, path):
    return _request(port, "GET", path)


def _post(port, path, body=None):
    return _request(port, "POST", path, body)


def _patch(port, path, body=None):
    return _request(port, "PATCH", path, body)


def _create_project(port, root, name="p"):
    ws_dir = root / f"ws-{name}"
    ws_dir.mkdir()
    status, created = _post(port, "/api/v1/projects", {"name": name, "workspace": str(ws_dir)})
    assert status == 201, created
    return created["data"]["id"]


def _write_flow(root, flow_id="flow"):
    flow = root / f"{flow_id}.yaml"
    flow.write_text(MINI_GATE_FLOW, encoding="utf-8")
    return flow


def _start_finishing(port, pid, root):
    flow = _write_flow(root, flow_id=f"finish-{time.time_ns()}")
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
    return started["data"]["session_id"]


def _start_waiting(port, pid, root):
    flow = _write_flow(root, flow_id=f"wait-{time.time_ns()}")
    status, started = _post(
        port,
        f"/api/v1/projects/{pid}/sessions",
        {"goal": "待办应用", "flow": str(flow), "model": "deterministic", "deterministic": True},
    )
    assert status == 201, started
    return started["data"]["session_id"]


def _wait_status(port, sid, statuses, timeout=30.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        status, body = _get(port, f"/api/v1/sessions/{sid}")
        if status == 200 and body["data"]["status"] in statuses:
            return body["data"]
        time.sleep(0.05)
    raise AssertionError(f"会话 {sid} 未在 {timeout}s 内进入 {statuses}")


def _wait_terminal(port, sid, timeout=30.0):
    return _wait_status(port, sid, ("completed", "failed"), timeout)


class SseConnection:
    """原始 socket SSE 读取器（支持 Last-Event-ID 头与增量读帧）。"""

    def __init__(self, port: int, path: str, *, last_event_id: str | None = None):
        self.sock = socket.create_connection(("127.0.0.1", port), timeout=3)
        request = f"GET {path} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nAccept: text/event-stream\r\n"
        if last_event_id is not None:
            request += f"Last-Event-ID: {last_event_id}\r\n"
        request += "Connection: close\r\n\r\n"
        self.sock.sendall(request.encode("ascii"))
        self._buf = b""
        header_block = b""
        while b"\r\n\r\n" not in header_block:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise AssertionError("SSE 连接在响应头阶段被关闭")
            header_block += chunk
        header_text, rest = header_block.split(b"\r\n\r\n", 1)
        self._buf = rest
        status_line = header_text.split(b"\r\n", 1)[0].decode("ascii", "replace")
        assert " 200 " in status_line, status_line

    def read_line(self) -> str | None:
        while b"\n" not in self._buf:
            chunk = self.sock.recv(4096)
            if not chunk:
                if self._buf:
                    line, self._buf = self._buf, b""
                    return line.rstrip(b"\r\n").decode("utf-8", "replace")
                return None
            self._buf += chunk
        line, self._buf = self._buf.split(b"\n", 1)
        return line.rstrip(b"\r").decode("utf-8", "replace")

    def read_frame(self) -> dict:
        fields: dict = {"event": None, "id": None, "retry": None, "data": [], "comments": [], "eof": False}
        saw = False
        while True:
            line = self.read_line()
            if line is None:
                fields["eof"] = not saw
                return fields
            if line == "":
                return fields
            saw = True
            if line.startswith(":"):
                fields["comments"].append(line[1:].lstrip(" "))
                continue
            name, _, value = line.partition(":")
            value = value.lstrip(" ")
            if name == "event":
                fields["event"] = value
            elif name == "id":
                fields["id"] = int(value)
            elif name == "retry":
                fields["retry"] = int(value)
            elif name == "data":
                fields["data"].append(value)

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass


def _read_all(port: int, path: str, *, last_event_id: str | None = None) -> list[dict]:
    conn = SseConnection(port, path, last_event_id=last_event_id)
    frames: list[dict] = []
    try:
        while True:
            frame = conn.read_frame()
            if frame["eof"]:
                return frames
            frames.append(frame)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# ① v0.5 遗留迁移：创建项目即迁移为首个会话并登记
# ---------------------------------------------------------------------------


def test_v05_migration_registry(server):
    port = server["port"]
    root: Path = server["root"]
    legacy_sid = "v05-abc123"
    ws_dir = root / "ws-legacy"
    agent_dir = ws_dir / ".agent-cluster"
    agent_dir.mkdir(parents=True)
    (agent_dir / "session.json").write_text(
        json.dumps(
            {
                "session_id": legacy_sid,
                "thread_id": "t:legacy",
                "goal": "遗留待办",
                "status": "completed",
                "workspace": str(ws_dir),
            }
        ),
        encoding="utf-8",
    )
    status, created = _post(port, "/api/v1/projects", {"name": "迁移项目", "workspace": str(ws_dir)})
    assert status == 201, created
    pid = created["data"]["id"]
    status, detail = _get(port, f"/api/v1/projects/{pid}")
    assert status == 200
    assert any(s["session_id"] == legacy_sid and s["status"] == "completed" for s in detail["data"]["sessions"])
    assert (agent_dir / ".migrated.json").is_file()
    status, tasks = _get(port, f"/api/v1/projects/{pid}/tasks")
    assert status == 200
    assert any(t["session_id"] == legacy_sid for t in tasks["data"])


# ---------------------------------------------------------------------------
# ② 全链路：deterministic 完成 → stdin 落盘 → fork 血缘 → 恢复 409
# ---------------------------------------------------------------------------


def test_chain_session_stdin_fork(server):
    port = server["port"]
    ws = server["ws"]
    root: Path = server["root"]
    pid = _create_project(port, root, "p1")

    # deterministic 会话至 completed（yes=true 自动放行 requirement 门）
    sid = _start_finishing(port, pid, root)
    snap = _wait_terminal(port, sid)
    assert snap["status"] == "completed"
    status, audit = _get(port, f"/api/v1/sessions/{sid}/audit")
    assert status == 200
    event_types = [e["type"] for e in audit["data"]["events"]]
    assert "session.start" in event_types and "session.end" in event_types
    assert isinstance(audit["data"]["token_summary"]["used"], int)

    # stdin 注入：挂起作答 → approve 继续 → transcript/PRD/变更历史/事件落盘
    sid2 = _start_waiting(port, pid, root)
    _wait_status(port, sid2, ("waiting_approval", "running"))
    ws_dir = root / "ws-p1"
    docs = ws_dir / "docs"
    docs.mkdir()
    (docs / "PRD.md").write_text("# PRD v1\n", encoding="utf-8")
    status, body = _post(port, f"/api/v1/sessions/{sid2}/stdin", {"text": "改用邮箱验证码登录"})
    assert status == 202 and body["data"]["accepted"] == "改用邮箱验证码登录"
    status, body = _post(port, f"/api/v1/sessions/{sid2}/approve")
    assert status == 200 and body["data"]["submitted"] == "accept"
    _wait_terminal(port, sid2)

    record_path = ws._project_store.session_dir(pid, sid2) / "session.json"
    record = json.loads(record_path.read_text(encoding="utf-8"))
    stdin_qas = [qa for qa in record["transcript"] if qa["source"] == "stdin"]
    assert len(stdin_qas) == 1
    assert stdin_qas[0]["question"] == "[stdin]"
    assert stdin_qas[0]["answer"] == "改用邮箱验证码登录"
    prd_text = (docs / "PRD.md").read_text(encoding="utf-8")
    assert "## 补充输入（v1）" in prd_text and "改用邮箱验证码登录" in prd_text

    status, changes = _get(port, f"/api/v1/sessions/{sid2}/changes")
    assert status == 200
    assert any(c["text"] == "改用邮箱验证码登录" for c in changes["data"]["records"])
    status, audit2 = _get(port, f"/api/v1/sessions/{sid2}/audit")
    assert status == 200
    assert any(
        e["type"] == "stdin.applied" and e["payload"]["text"] == "改用邮箱验证码登录"
        for e in audit2["data"]["events"]
    )
    assert any(
        a["kind"] == "requirement_confirmation" and a["last_decision"] == "accept"
        for a in audit2["data"]["approvals"]
    )

    # fork：终态派生 + 血缘字段 + dormant 登记 + 账本聚合不双计
    status, budget_before = _get(port, f"/api/v1/projects/{pid}/budget")
    assert status == 200
    status, forked = _post(port, f"/api/v1/sessions/{sid2}/fork", {"goal": "衍生需求", "worktree": False})
    assert status == 200, forked
    child = forked["data"]["session_id"]
    assert forked["data"]["parent_session_id"] == sid2
    assert forked["data"]["fork_depth"] == 1
    assert child != sid2
    status, budget_after = _get(port, f"/api/v1/projects/{pid}/budget")
    assert status == 200
    assert budget_after["data"]["used"] == budget_before["data"]["used"]
    assert ws.manager.get(child).status == "dormant"
    status, audit_parent = _get(port, f"/api/v1/sessions/{sid2}/audit")
    assert status == 200
    assert any(
        e["type"] == "session.forked" and e["payload"].get("child_session_id") == child
        for e in audit_parent["data"]["events"]
    )
    status, audit_child = _get(port, f"/api/v1/sessions/{child}/audit")
    assert status == 200
    assert any(
        e["type"] == "session.start" and e["payload"].get("forked_from") == sid2
        for e in audit_child["data"]["events"]
    )

    # 已完成会话恢复启动 → 409 conflict
    status, body = _post(port, f"/api/v1/projects/{pid}/sessions", {"session_id": sid, "goal": "x"})
    assert status == 409 and body["code"] == "conflict"


# ---------------------------------------------------------------------------
# ③ 预算池：warning/exhausted 事件 + 解锁 200/202/审批 + 硬上限 409 + dashboard/tasks
# ---------------------------------------------------------------------------


def test_budget_events_unlock_dashboard_tasks(server):
    port = server["port"]
    ws = server["ws"]
    root: Path = server["root"]
    pid = _create_project(port, root, "budget")

    sid = _start_finishing(port, pid, root)
    _wait_terminal(port, sid)
    status, budget = _get(port, f"/api/v1/projects/{pid}/budget")
    assert status == 200
    used = budget["data"]["used"]
    assert used > 0

    # 硬上限 = 当前用量：下一会话触发 warning 与 exhausted 事件
    status, _ = _patch(port, f"/api/v1/projects/{pid}", {"budget_pool": {"hard_limit_tokens": used}})
    assert status == 200
    sid2 = _start_finishing(port, pid, root)
    _wait_terminal(port, sid2)
    status, audit = _get(port, f"/api/v1/sessions/{sid2}/audit")
    assert status == 200
    event_types = [e["type"] for e in audit["data"]["events"]]
    assert "budget.warning" in event_types, event_types
    assert "budget.exhausted" in event_types, event_types
    status, budget2 = _get(port, f"/api/v1/projects/{pid}/budget")
    assert status == 200
    assert budget2["data"]["warn_raised"] is True
    used2 = budget2["data"]["used"]
    assert used2 > used

    # 硬上限 → 新会话 409 budget_pool_exhausted
    status, body = _post(port, f"/api/v1/projects/{pid}/sessions", {"goal": "再开一个"})
    assert status == 409 and body["code"] == "budget_pool_exhausted"

    # 自服务解锁 200 granted + budget.unlocked 事件
    status, body = _post(
        port, f"/api/v1/projects/{pid}/budget/unlock", {"additional_tokens": 1000, "reason": "扩容"}
    )
    assert status == 200 and body["data"]["status"] == "granted"
    assert any(
        e["type"] == "budget.unlocked" and e.get("unlock_id") == body["data"]["id"]
        for e in ws.manager.events
    )

    # 审批模式：202 pending → approve granted → 重复决 409
    status, _ = _patch(port, f"/api/v1/projects/{pid}", {"budget_pool": {"unlock_requires_approval": True}})
    assert status == 200
    status, body = _post(
        port, f"/api/v1/projects/{pid}/budget/unlock", {"additional_tokens": 500, "reason": "例外"}
    )
    assert status == 202 and body["data"]["status"] == "pending"
    unlock_id = body["data"]["id"]
    status, body = _post(
        port, f"/api/v1/projects/{pid}/budget/unlock/{unlock_id}/approve", {"decided_by": "pm"}
    )
    assert status == 200 and body["data"]["status"] == "granted"
    status, body = _post(port, f"/api/v1/projects/{pid}/budget/unlock/{unlock_id}/deny", {})
    assert status == 409 and body["code"] == "conflict"

    # dashboard 三轴数值契约 + tasks 过滤/指派
    status, dash = _get(port, f"/api/v1/projects/{pid}/dashboard")
    assert status == 200
    data = dash["data"]
    assert set(data) == {"cost", "progress", "health", "updated_at"}
    assert set(data["cost"]) == {"used", "limit", "ratio", "score", "status", "estimated_usd"}
    assert data["cost"]["used"] == used2
    assert data["cost"]["limit"] == used + 1500
    assert data["cost"]["ratio"] == pytest.approx(used2 / (used + 1500), abs=1e-4)
    assert data["cost"]["status"] in ("ok", "warn", "critical")
    assert sid2 in data["health"]["sessions"]

    status, tasks = _get(port, f"/api/v1/projects/{pid}/tasks?status=completed")
    assert status == 200
    assert any(t["session_id"] == sid for t in tasks["data"])
    status, body = _patch(port, f"/api/v1/projects/{pid}/tasks/{sid}", {"assignee": "alice"})
    assert status == 200 and body["data"]["assignee"] == "alice"
    status, filtered = _get(port, f"/api/v1/projects/{pid}/tasks?assignee=alice")
    assert status == 200
    assert len(filtered["data"]) == 1 and filtered["data"][0]["session_id"] == sid
    status, snapshots = _get(port, f"/api/v1/projects/{pid}/sessions")
    assert status == 200
    snap = next(item for item in snapshots["data"] if item["session_id"] == sid)
    assert snap["assignee"] == "alice"


# ---------------------------------------------------------------------------
# ④ SSE：session.end 哨兵 + Last-Event-ID 重放不丢不重
# ---------------------------------------------------------------------------


def test_sse_sentinel_replay(server):
    port = server["port"]
    root: Path = server["root"]
    pid = _create_project(port, root, "sse")
    sid = _start_finishing(port, pid, root)
    _wait_terminal(port, sid)

    frames = _read_all(port, f"/api/v1/sessions/{sid}/events?since=0")
    sentinels = [f for f in frames if f["event"] == "session.end"]
    assert sentinels, frames
    assert json.loads(sentinels[-1]["data"][0])["status"] == "completed"

    seqs = sorted({f["id"] for f in frames if f["data"]})
    assert len(seqs) >= 4, seqs
    marker = seqs[-3]
    frames = _read_all(port, f"/api/v1/sessions/{sid}/events", last_event_id=str(marker))
    got = [f["id"] for f in frames if f["data"] and f["event"] is None]
    assert got == [s for s in seqs if s > marker], got