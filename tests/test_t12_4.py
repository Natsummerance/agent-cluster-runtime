"""T12.4 交互升级：实时打断 + 变更版本化/回滚 + 阶段重规划（reject 驱动）。"""

from __future__ import annotations

import json
import threading
import time
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

import agent_cluster.server as server_mod
from agent_cluster.changes import ChangeHistory, ChangeRecord
from agent_cluster.models import ActionRequest, GateKind
from agent_cluster.server import WorkbenchHandler, WorkbenchServer
from agent_cluster.session import SessionDriver

MINI_FLOW = """name: t12.4-mini
thread_id: "t:12.4"
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


# ---------------------------------------------------------------------------
# ChangeHistory：快照 + 版本 + 回滚
# ---------------------------------------------------------------------------


def test_change_history_record_and_list(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "PRD.md").write_text("# PRD v1", encoding="utf-8")
    (tmp_path / "README.md").write_text("# 项目", encoding="utf-8")
    history = ChangeHistory(tmp_path)
    assert history.latest_version() == 0
    record = history.record(text="增加登录功能", node="requirement_gate", phase="requirements")
    assert record.version == 1
    assert record.phase == "requirements"
    assert history.latest_version() == 1
    assert history.list()[0].text == "增加登录功能"
    assert (tmp_path / record.snapshot_dir / "docs" / "PRD.md").exists()


def test_change_history_rollback_restores_files(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "PRD.md").write_text("# PRD v1", encoding="utf-8")
    history = ChangeHistory(tmp_path)
    history.record(text="变更一", node="g1")
    # 修改工作区产物
    (tmp_path / "docs" / "PRD.md").write_text("# PRD 被改坏", encoding="utf-8")
    assert history.rollback(1) is True
    assert "# PRD v1" in (tmp_path / "docs" / "PRD.md").read_text(encoding="utf-8")


def test_change_history_rollback_unknown_version(tmp_path):
    history = ChangeHistory(tmp_path)
    assert history.rollback(99) is False


def test_change_history_persists(tmp_path):
    (tmp_path / "docs").mkdir()
    history = ChangeHistory(tmp_path)
    history.record(text="变更", node="g")
    history2 = ChangeHistory(tmp_path)
    assert len(history2.list()) == 1
    assert isinstance(history2.list()[0], ChangeRecord)


# ---------------------------------------------------------------------------
# SessionDriver：inject_change 在门挂起点消费并 reject 驱动重规划
# ---------------------------------------------------------------------------


@pytest.fixture()
def driver(tmp_path):
    flow = tmp_path / "flow.yaml"
    flow.write_text(MINI_FLOW, encoding="utf-8")
    ws = tmp_path / "ws"
    ws.mkdir()
    drv = SessionDriver(
        workspace=ws, goal="待办应用", flow=str(flow), deterministic=True, yes=False,
        prompt_fn=lambda hint: "/skip",
    )
    drv.current_node = "requirement_gate"
    drv.current_phase = "requirements"
    return drv


def test_inject_change_records_and_rejects_gate(driver):
    assert driver.inject_change("增加登录功能") is True
    request = ActionRequest(
        id="r1", kind=GateKind.REQUIREMENT_CONFIRMATION, title="需求确认", description="确认需求范围"
    )
    response = driver.decide_response(request)
    assert response.type == "reject"
    assert "登录" in (response.args or {}).get("reason", "")
    records = driver.change_history.list()
    assert len(records) == 1
    assert records[0].version == 1
    assert driver._injected_changes == []


def test_inject_change_empty_text_ignored(driver):
    assert driver.inject_change("   ") is False
    assert driver.inject_change("") is False


def test_inject_change_keeps_ask_user_non_reject(driver):
    driver.inject_change("改需求")
    request = ActionRequest(id="r2", kind=GateKind.HUMAN_INTERACTION, title="澄清", description="问题")
    response = driver.decide_response(request)
    # HUMAN_INTERACTION 不 reject；变更已记录，留给下一个门生效
    assert response.type != "reject"
    assert len(driver.change_history.list()) == 1


# ---------------------------------------------------------------------------
# Server：interrupt 端点 → 会话内生效（确定性 + --yes）
# ---------------------------------------------------------------------------


def _start_http(ws):
    server = WorkbenchServer(host="127.0.0.1", port=0, auth_token="")
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), WorkbenchHandler)
    httpd.workbench = server
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return server, httpd, port


def _post(port, path, body):
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=json.dumps(body).encode(), method="POST")
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode())


def _get(port, path):
    with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=10) as resp:
        return json.loads(resp.read().decode())


def test_server_interrupt_records_change_and_completes(tmp_path, monkeypatch):
    monkeypatch.setattr(server_mod, "INDEX_DIR", tmp_path / "home")
    ws = tmp_path / "proj"
    ws.mkdir()
    server, httpd, port = _start_http(ws)
    try:
        created = _post(port, "/api/v1/projects", {"name": "p", "workspace": str(ws)})
        pid = created["data"]["id"]
        flow = Path("examples/flows/build-product.yaml").resolve()
        started = _post(
            port,
            f"/api/v1/projects/{pid}/sessions",
            {"goal": "待办事项应用", "flow": str(flow), "model": "deterministic", "deterministic": True, "yes": True},
        )
        sid = started["data"]["session_id"]
        # 立即注入需求变更
        interrupted = _post(port, f"/api/v1/sessions/{sid}/interrupt", {"text": "增加用户登录功能"})
        assert interrupted["ok"] is True

        deadline = time.time() + 120
        final = None
        while time.time() < deadline:
            body = _get(port, f"/api/v1/sessions/{sid}")
            final = body["data"]
            if final["status"] in ("completed", "failed"):
                break
            time.sleep(0.5)
        assert final is not None
        assert final["status"] == "completed", final.get("error")
        changes = _get(port, f"/api/v1/sessions/{sid}/changes")
        assert changes["ok"] is True
        assert changes["data"]["summary"]["count"] >= 1
    finally:
        httpd.shutdown()
        httpd.server_close()
