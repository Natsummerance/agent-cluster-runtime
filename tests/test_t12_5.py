"""T12.5 可观测性：span 追踪 + 审计导出 + 健康指标 + serve 端点接线。"""

from __future__ import annotations

import json
import threading
import time
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

import agent_cluster.server as server_mod
from agent_cluster.changes import ChangeRecord
from agent_cluster.models import TokenUsage
from agent_cluster.server import WorkbenchHandler, WorkbenchServer
from agent_cluster.session import GateDecisionRecord, TokenLedger
from agent_cluster.trace import (
    JsonlExporter,
    Span,
    Tracer,
    build_audit_package,
    compute_health,
)

MINI_FLOW = """name: t12.5-mini
thread_id: "t:12.5"
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
# Span / Tracer / Exporter
# ---------------------------------------------------------------------------


def test_span_duration_and_finish():
    span = Span(id="s1", name="model.call")
    assert span.duration_ms >= 0
    span.finish()
    data = span.to_dict()
    assert data["id"] == "s1"
    assert data["name"] == "model.call"
    assert data["kind"] == "span"
    assert "duration_ms" in data


def test_tracer_parent_child_nesting():
    captured = []

    class FakeExporter:
        def export(self, span):
            captured.append(span)

    tracer = Tracer(exporter=FakeExporter())
    parent = tracer.start_span("parent", kind="node")
    child = tracer.start_span("child", kind="tool")
    assert child.parent_id == parent.id
    tracer.end_span(child)
    tracer.end_span(parent)
    assert len(captured) == 2
    assert len(tracer.spans()) == 2


def test_jsonl_exporter_writes_file(tmp_path):
    tracer = Tracer(exporter=JsonlExporter(tmp_path))
    span = tracer.start_span("model.call", kind="model")
    tracer.end_span(span)
    lines = (tmp_path / ".agent-cluster" / "trace.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["name"] == "model.call"


# ---------------------------------------------------------------------------
# 审计导出
# ---------------------------------------------------------------------------


def _fake_gate_decision(node="design_gate", attempts=3, rejections=1) -> GateDecisionRecord:
    return GateDecisionRecord(node=node, attempts=attempts, rejections=rejections)


def _fake_change(version: int = 1) -> ChangeRecord:
    return ChangeRecord(version=version, text="增加登录", ts="2026-01-01T00:00:00Z", node="g", phase="requirements")


def test_build_audit_package_writes_files(tmp_path):
    tracer = Tracer(exporter=JsonlExporter(tmp_path))
    span = tracer.start_span("session.run", kind="session")
    tracer.end_span(span)
    files = build_audit_package(
        workspace=tmp_path,
        session_id="abc123",
        goal="待办应用",
        events=[{"type": "gate.waiting", "payload": {"node": "g"}}],
        approvals=[_fake_gate_decision()],
        token_summary={"budget": 1000, "used": 120, "remaining": 880},
        change_records=[_fake_change()],
        spans=tracer.spans(),
        cost={"by_model": {"deepseek-chat": 0.0001}, "total": 0.0001, "currency": "USD"},
    )
    md = Path(files["markdown"])
    js = Path(files["json"])
    assert md.exists() and js.exists()
    data = json.loads(js.read_text(encoding="utf-8"))
    assert data["session_id"] == "abc123"
    assert data["events"][0]["type"] == "gate.waiting"
    assert data["token_summary"]["used"] == 120
    assert data["changes"][0]["version"] == 1
    assert len(data["spans"]) == 1
    assert data["cost"]["total"] == 0.0001
    md_text = md.read_text(encoding="utf-8")
    assert "Token 计量" in md_text
    assert "审批记录" in md_text


# ---------------------------------------------------------------------------
# 健康指标
# ---------------------------------------------------------------------------


def test_compute_health_rework_rate_and_cost():
    ledger = TokenLedger(budget=1000)
    ledger.record(role="dev", phase="development", usage=None, source="tool")
    health = compute_health(
        token_ledger=ledger,
        gate_decisions=[_fake_gate_decision(attempts=4, rejections=2)],
        cost={"total": 0.5, "currency": "USD"},
    )
    assert health["rework_rate"] == pytest.approx(0.5)
    assert health["token_cost"]["used"] == 0
    assert health["token_cost"]["cost"] == 0.5


def test_compute_health_estimate_accuracy():
    ledger = TokenLedger(budget=0)
    ledger.record(
        role="pm",
        phase="requirements",
        usage=TokenUsage(
            prompt_tokens=80,
            completion_tokens=20,
            total_tokens=100,
            model="deepseek-chat",
            estimated=False,
            estimated_total=120,
        ),
    )
    health = compute_health(token_ledger=ledger, gate_decisions=[])
    assert health["estimate_accuracy"] == pytest.approx(0.8)


# ---------------------------------------------------------------------------
# Server：audit / audit/export / metrics 端点
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


def _run_mini_session(tmp_path, monkeypatch):
    monkeypatch.setattr(server_mod, "INDEX_DIR", tmp_path / "home")
    ws = tmp_path / "proj"
    ws.mkdir()
    server, httpd, port = _start_http(ws)
    try:
        created = _post(port, "/api/v1/projects", {"name": "p", "workspace": str(ws)})
        pid = created["data"]["id"]
        flow = tmp_path / "flow.yaml"
        flow.write_text(MINI_FLOW, encoding="utf-8")
        started = _post(
            port,
            f"/api/v1/projects/{pid}/sessions",
            {"goal": "待办应用", "flow": str(flow), "model": "deterministic", "deterministic": True, "yes": True},
        )
        sid = started["data"]["session_id"]
        deadline = time.time() + 60
        final = None
        while time.time() < deadline:
            body = _get(port, f"/api/v1/sessions/{sid}")
            final = body["data"]
            if final["status"] in ("completed", "failed"):
                break
            time.sleep(0.5)
        assert final is not None and final["status"] == "completed", final.get("error")
        return server, httpd, port, sid, ws
    except BaseException:
        httpd.shutdown()
        httpd.server_close()
        raise


def test_server_audit_endpoints(tmp_path, monkeypatch):
    server, httpd, port, sid, ws = _run_mini_session(tmp_path, monkeypatch)
    try:
        data = _get(port, f"/api/v1/sessions/{sid}/audit")["data"]
        assert data["session_id"] == sid
        assert "token_summary" in data
        assert isinstance(data["events"], list)
        span_names = [span["name"] for span in data["spans"]]
        assert "session.run" in span_names
        # 审计导出落盘
        exported = _post(port, f"/api/v1/sessions/{sid}/audit/export", {})
        assert exported["ok"] is True
        files = exported["data"]["files"]
        assert Path(files["json"]).exists()
        assert Path(files["markdown"]).exists()
        assert (ws / ".agent-cluster" / "trace.jsonl").exists()
        # 会话详情包含健康指标
        detail = _get(port, f"/api/v1/sessions/{sid}")["data"]
        assert "health" in detail
        assert detail["health"]["rework_rate"] >= 0.0
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_metrics_includes_health(tmp_path, monkeypatch):
    server, httpd, port, sid, ws = _run_mini_session(tmp_path, monkeypatch)
    try:
        metrics = _get(port, "/api/v1/metrics")["data"]
        assert metrics["total_tokens"] >= 0
        assert metrics["total_cost"] >= 0.0
        assert isinstance(metrics["health"], list)
        assert any(h["session_id"] == sid for h in metrics["health"])
    finally:
        httpd.shutdown()
        httpd.server_close()
