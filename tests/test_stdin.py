"""T13.9 实时 stdin 注入：挂起作答 / 节点边界自由输入 / 写入规则 / 终态拒绝 / CLI 子命令。"""

from __future__ import annotations

import asyncio
import json
import threading
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

import agent_cluster.server as server_mod
from agent_cluster.cli import main
from agent_cluster.models import ActionRequest, GateKind
from agent_cluster.server import WorkbenchHandler, WorkbenchServer
from agent_cluster.session import SessionDriver, SessionStore

MINI_FLOW = """name: t13.9-stdin
thread_id: "t:13.9"
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


def _make_driver(
    tmp_path: Path,
    *,
    ws_name: str = "ws",
    yes: bool = True,
    cancel_event: threading.Event | None = None,
    prompt_fn=None,
    event_printer=None,
) -> tuple[SessionDriver, Path]:
    flow = tmp_path / f"{ws_name}.yaml"
    flow.write_text(MINI_FLOW, encoding="utf-8")
    ws = tmp_path / ws_name
    ws.mkdir()
    driver = SessionDriver(
        workspace=ws,
        goal="待办应用",
        flow=str(flow),
        deterministic=True,
        yes=yes,
        prompt_fn=prompt_fn or (lambda hint: "accept"),
        print_fn=lambda s: None,
        cancel_event=cancel_event,
        event_printer=event_printer,
    )
    return driver, ws


# ---------------------------------------------------------------------------
# 消费语义（§11）
# ---------------------------------------------------------------------------


def test_pending_prompt_consumed_as_answer(tmp_path):
    """挂起中注入：stdin 行优先作为该 prompt 的回答（等价 response，避免挂死）。"""
    driver, ws = _make_driver(
        tmp_path, yes=False, prompt_fn=lambda hint: "DEFAULT-ANSWER"
    )
    assert driver.inject_stdin("目标用户是上班族") is True
    request = ActionRequest(
        id="r1", kind=GateKind.HUMAN_INTERACTION, title="澄清", description="目标用户是谁？"
    )
    response = driver.decide_response(request)
    assert response.type == "response"
    assert response.args == {"text": "目标用户是上班族"}
    record = SessionStore(ws).record
    stdin_qas = [qa for qa in record.transcript if qa.source == "stdin"]
    assert len(stdin_qas) == 1
    assert stdin_qas[0].question == "[stdin]"
    assert stdin_qas[0].answer == "目标用户是上班族"
    assert driver.change_history.latest_version() == 1


def test_free_input_at_boundary(tmp_path):
    """非挂起时注入：下一节点边界（node_end）作自由输入消费，transcript/变更历史/事件齐全。"""
    captured = []
    driver, ws = _make_driver(tmp_path, yes=True, event_printer=lambda ev: captured.append(ev))
    assert driver.inject_stdin("补充：支持导出 CSV") is True
    result = asyncio.run(driver.run())
    assert result.exit_code == 0
    record = SessionStore(ws).record
    stdin_qas = [qa for qa in record.transcript if qa.source == "stdin"]
    assert len(stdin_qas) == 1
    assert stdin_qas[0].answer == "补充：支持导出 CSV"
    assert stdin_qas[0].node
    records = driver.change_history.list()
    assert any(record_item.text == "补充：支持导出 CSV" for record_item in records)
    assert any(ev.type == "stdin.applied" for ev in captured)


# ---------------------------------------------------------------------------
# 写入规则（§11 四步）
# ---------------------------------------------------------------------------


def test_write_rules_prd(tmp_path):
    """工作区含 docs/PRD.md → 追加 ## 补充输入（v<n>）；不含 → 不新建文档。"""
    driver, ws = _make_driver(tmp_path, ws_name="ws_prd")
    (ws / "docs").mkdir()
    (ws / "docs" / "PRD.md").write_text("# PRD v1\n", encoding="utf-8")
    assert driver.inject_stdin("改用邮箱验证码登录") is True
    assert driver._drain_stdin() == "改用邮箱验证码登录"
    prd_text = (ws / "docs" / "PRD.md").read_text(encoding="utf-8")
    assert "## 补充输入（v1）" in prd_text
    assert "改用邮箱验证码登录" in prd_text

    driver2, ws2 = _make_driver(tmp_path, ws_name="ws_noprd")
    assert driver2.inject_stdin("无 PRD 注入") is True
    assert driver2._drain_stdin() == "无 PRD 注入"
    assert not (ws2 / "docs" / "PRD.md").exists()
    assert driver2.change_history.latest_version() == 1
    record2 = SessionStore(ws2).record
    assert any(qa.source == "stdin" and qa.answer == "无 PRD 注入" for qa in record2.transcript)


# ---------------------------------------------------------------------------
# 终态 / 取消拒绝（409 session_busy 语义）
# ---------------------------------------------------------------------------


def test_terminal_reject(tmp_path):
    """completed/aborted 会话 inject_stdin → False；空文本 → False。"""
    driver, _ = _make_driver(tmp_path, ws_name="ws_done")
    driver.store.update(status="completed")
    assert driver.inject_stdin("已结束") is False
    driver.store.update(status="aborted")
    assert driver.inject_stdin("已中止") is False
    assert driver.inject_stdin("   ") is False
    assert driver.inject_stdin("") is False


def test_cancelled_reject(tmp_path):
    """cancel_event 已置 → inject_stdin → False。"""
    cancel_event = threading.Event()
    driver, _ = _make_driver(tmp_path, ws_name="ws_cancel", cancel_event=cancel_event)
    cancel_event.set()
    assert driver.inject_stdin("取消后注入") is False


# ---------------------------------------------------------------------------
# CLI stdin 子命令（入口 3：经本地 serve REST 中转）
# ---------------------------------------------------------------------------


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


def test_cli_stdin_subcommand(tmp_path, monkeypatch):
    """in-process serve + main(["stdin", sid, --text, --port])：202 且 transcript 出现；不存在 sid → 1。"""
    monkeypatch.setattr(server_mod, "INDEX_DIR", tmp_path / "home")
    workbench = WorkbenchServer(host="127.0.0.1", port=0, auth_token="")
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), WorkbenchHandler)
    httpd.workbench = workbench
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        ws_dir = tmp_path / "ws-cli"
        ws_dir.mkdir()
        status, created = _post(port, "/api/v1/projects", {"name": "p", "workspace": str(ws_dir)})
        assert status == 201, created
        pid = created["data"]["id"]
        flow = tmp_path / "cli-flow.yaml"
        flow.write_text(MINI_FLOW, encoding="utf-8")
        status, started = _post(
            port,
            f"/api/v1/projects/{pid}/sessions",
            {"goal": "待办应用", "flow": str(flow), "model": "deterministic", "deterministic": True, "yes": False},
        )
        assert status == 201, started
        sid = started["data"]["session_id"]
        deadline = time.time() + 30
        snapshot_status = "starting"
        while time.time() < deadline:
            status, body = _get(port, f"/api/v1/sessions/{sid}")
            snapshot_status = body["data"]["status"]
            if snapshot_status == "waiting_approval":
                break
            time.sleep(0.1)
        assert snapshot_status == "waiting_approval", snapshot_status

        assert main(["stdin", sid, "--text", "hi", "--port", str(port)]) == 0

        record_path = workbench._project_store.session_dir(pid, sid) / "session.json"
        deadline = time.time() + 10
        found = False
        while time.time() < deadline:
            try:
                data = json.loads(record_path.read_text(encoding="utf-8"))
                found = any(
                    qa.get("source") == "stdin" and qa.get("answer") == "hi"
                    for qa in data.get("transcript", [])
                )
            except (OSError, json.JSONDecodeError):
                found = False
            if found:
                break
            time.sleep(0.2)
        assert found, "transcript 未出现 source=stdin 的注入记录"

        assert main(["stdin", "nosuchsid", "--text", "x", "--port", str(port)]) == 1
    finally:
        workbench.manager.shutdown()
        httpd.shutdown()
        httpd.server_close()
