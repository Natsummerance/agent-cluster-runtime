"""Task 14.11 审计合规导出：哈希链防篡改 + CSV/JSON/Markdown 三格式 + 保留策略 + serve 端点。

TDD 顺序：先定义契约（哈希链 / 三格式 / retention / 端点），再实现 trace.py 扩展。
"""

from __future__ import annotations

import csv
import io
import json
import threading
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

import agent_cluster.server as server_mod
from agent_cluster.server import ServerSession, WorkbenchHandler, WorkbenchServer
from agent_cluster.trace import (
    AuditChainError,
    apply_retention,
    build_audit_package,
    build_hash_chain,
    export_audit,
    hash_record,
    verify_hash_chain,
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _event(seq: int, type_: str, payload: dict | None = None, ts: str | None = None, actor: str = "admin") -> dict:
    return {"seq": seq, "type": type_, "payload": payload or {}, "ts": ts or _now_iso(), "actor": actor}


def _three_events() -> list[dict]:
    base = _now_iso()
    return [
        _event(1, "session.start", {"goal": "g"}, base),
        _event(2, "agent.response", {"text": "产出"}, base),
        _event(3, "approval.accepted", {"request_id": "r1"}, base),
    ]


# ---------------------------------------------------------------------------
# 哈希链
# ---------------------------------------------------------------------------


def test_hash_record_deterministic_and_chain_sensitive():
    assert hash_record({"a": 1}) == hash_record({"a": 1})
    assert hash_record({"a": 1}) != hash_record({"a": 2})
    # prev_hash 参与摘要：同内容不同前链必须不同
    assert hash_record({"a": 1}, prev_hash="x") != hash_record({"a": 1}, prev_hash="y")


def test_build_hash_chain_links_records():
    chain = build_hash_chain(_three_events())
    assert len(chain) == 3
    assert chain[0]["prev_hash"] == ""
    for record in chain:
        assert len(record["hash"]) == 64
    assert chain[1]["prev_hash"] == chain[0]["hash"]
    assert chain[2]["prev_hash"] == chain[1]["hash"]
    # 原始记录不被原地污染
    assert "hash" not in _three_events()[0]


def test_verify_hash_chain_accepts_built_chain():
    assert verify_hash_chain(build_hash_chain(_three_events())) is True


def test_verify_hash_chain_detects_tampering():
    chain = build_hash_chain(_three_events())
    tampered = [dict(r) for r in chain]
    tampered[1]["payload"] = {"text": "被篡改"}
    assert verify_hash_chain(tampered) is False
    broken = [dict(r) for r in chain]
    broken[0]["hash"] = "0" * 64
    assert verify_hash_chain(broken) is False
    missing = [dict(r) for r in chain]
    del missing[2]["hash"]
    assert verify_hash_chain(missing) is False
    # 顺序交换破坏前后链接
    swapped = [chain[1], chain[0], chain[2]]
    assert verify_hash_chain(swapped) is False


def test_verify_hash_chain_empty_is_valid():
    assert verify_hash_chain([]) is True


# ---------------------------------------------------------------------------
# 保留策略
# ---------------------------------------------------------------------------


def test_apply_retention_keeps_recent_window():
    now = datetime.now(timezone.utc)
    old = (now - timedelta(days=30)).isoformat(timespec="seconds")
    mid = (now - timedelta(days=2)).isoformat(timespec="seconds")
    fresh = now.isoformat(timespec="seconds")
    records = [_event(1, "old", ts=old), _event(2, "mid", ts=mid), _event(3, "fresh", ts=fresh)]
    kept = apply_retention(records, retention_days=7)
    assert [r["seq"] for r in kept] == [2, 3]
    assert [r["seq"] for r in apply_retention(records, retention_days=None)] == [1, 2, 3]
    assert [r["seq"] for r in apply_retention(records, retention_days=0)] == [1, 2, 3]


def test_apply_retention_keeps_unparseable_ts():
    records = [_event(1, "weird", ts="not-a-date"), _event(2, "old", ts=(datetime.now(timezone.utc) - timedelta(days=30)).isoformat())]
    kept = apply_retention(records, retention_days=7)
    assert [r["seq"] for r in kept] == [1]


# ---------------------------------------------------------------------------
# 三格式导出
# ---------------------------------------------------------------------------


def test_export_json_envelope_with_hashed_records():
    content = export_audit(_three_events(), fmt="json", session_id="s1", goal="目标")
    parsed = json.loads(content)
    assert parsed["session_id"] == "s1"
    assert parsed["goal"] == "目标"
    assert parsed["format"] == "json"
    assert parsed["verified"] is True
    assert parsed["count"] == 3
    assert len(parsed["records"]) == 3
    assert verify_hash_chain(parsed["records"]) is True
    assert parsed["records"][0]["prev_hash"] == ""


def test_export_csv_header_and_rows():
    content = export_audit(_three_events(), fmt="csv")
    rows = list(csv.reader(io.StringIO(content)))
    assert rows[0] == ["seq", "ts", "type", "actor", "prev_hash", "hash", "payload"]
    assert len(rows) == 4
    assert rows[1][4] == ""  # 首行 prev_hash 为空
    assert len(rows[1][5]) == 64


def test_export_markdown_timeline_and_chain_footer():
    content = export_audit(_three_events(), fmt="markdown", session_id="s1", goal="目标")
    assert "审计轨迹" in content
    assert "| # |" in content
    for record in _three_events():
        assert record["type"] in content
    assert "链尾 hash" in content
    assert "校验" in content


def test_export_invalid_format_raises():
    with pytest.raises(ValueError, match="format"):
        export_audit(_three_events(), fmt="xml")


def test_export_rejects_tampered_input_chain():
    chained = build_hash_chain(_three_events())
    chained[1]["payload"] = {"text": "被篡改"}
    with pytest.raises(AuditChainError, match="哈希链"):
        export_audit(chained, fmt="json")


def test_export_applies_retention_window():
    now = datetime.now(timezone.utc)
    records = [
        _event(1, "old", ts=(now - timedelta(days=30)).isoformat(timespec="seconds")),
        _event(2, "fresh", ts=now.isoformat(timespec="seconds")),
    ]
    parsed = json.loads(export_audit(records, fmt="json", retention_days=7))
    assert parsed["count"] == 1
    assert parsed["records"][0]["seq"] == 2
    assert parsed["retention_days"] == 7
    assert verify_hash_chain(parsed["records"]) is True


def test_build_audit_package_writes_requested_format(tmp_path):
    files = build_audit_package(
        workspace=tmp_path,
        session_id="s1",
        goal="目标",
        events=_three_events(),
        approvals=[],
        token_summary={},
        change_records=[],
        spans=[],
        export_format="csv",
    )
    assert "export" in files
    export_path = Path(files["export"])
    assert export_path.is_file()
    assert export_path.read_text(encoding="utf-8").splitlines()[0].startswith("seq,ts,type")


# ---------------------------------------------------------------------------
# serve 端点
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


@pytest.fixture()
def audit_server(tmp_path, monkeypatch):
    monkeypatch.setattr(server_mod, "INDEX_DIR", tmp_path / "home")
    workbench = WorkbenchServer(host="127.0.0.1", port=0, auth_token="")
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), WorkbenchHandler)
    httpd.workbench = workbench
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield port, workbench
    httpd.shutdown()
    httpd.server_close()


def _seed_session(workbench, tmp_path, sid="s-audit") -> ServerSession:
    session = ServerSession(sid, "proj-1", tmp_path / "ws-audit", {"goal": "审计目标"})
    session.log.append({"type": "session.start", "payload": {"goal": "审计目标"}})
    session.log.append({"type": "agent.response", "payload": {"text": "第一轮产出"}})
    session.log.append({"type": "approval.accepted", "payload": {"request_id": "r1"}})
    workbench.manager.sessions[sid] = session
    return session


def test_audit_export_endpoint_three_formats(audit_server, tmp_path):
    port, workbench = audit_server
    _seed_session(workbench, tmp_path)
    for fmt, probe in (
        ("csv", "seq,ts,type"),
        ("json", "records"),
        ("markdown", "审计轨迹"),
    ):
        status, body = _request(port, "GET", f"/api/v1/sessions/s-audit/audit/export?format={fmt}")
        assert status == 200, body
        assert body["ok"] is True
        assert body["data"]["format"] == fmt
        assert probe in body["data"]["content"]
        assert Path(body["data"]["files"]["export"]).is_file()
    status, body = _request(port, "GET", "/api/v1/sessions/s-audit/audit/export?format=json")
    parsed = json.loads(body["data"]["content"])
    assert verify_hash_chain(parsed["records"]) is True
    # 3 条种子事件 + 每次导出自身记入 audit.exported（既有行为）
    assert parsed["count"] >= 3
    assert [r["seq"] for r in parsed["records"][:3]] == [0, 1, 2]  # serve 事件 seq 0 基


def test_audit_export_endpoint_retention(audit_server, tmp_path):
    port, workbench = audit_server
    session = _seed_session(workbench, tmp_path)
    old = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat(timespec="seconds")
    session.log.append({"type": "session.end", "payload": {}})
    session.log.replay()[-1]["ts"] = old
    status, all_body = _request(port, "GET", "/api/v1/sessions/s-audit/audit/export?format=json")
    assert json.loads(all_body["data"]["content"])["count"] == 4
    status, kept = _request(port, "GET", "/api/v1/sessions/s-audit/audit/export?format=json&retention_days=7")
    assert status == 200, kept
    parsed = json.loads(kept["data"]["content"])
    # 3 条种子事件 + 上一次导出的 audit.exported（30 天前的 session.end 被裁剪）
    assert parsed["count"] == 4
    assert all(record["seq"] != 3 for record in parsed["records"])  # 0 基 seq=3 是 30 天前的 session.end
    assert parsed["retention_days"] == 7
    assert verify_hash_chain(parsed["records"]) is True


def test_audit_export_endpoint_errors(audit_server, tmp_path):
    port, workbench = audit_server
    _seed_session(workbench, tmp_path)
    status, body = _request(port, "GET", "/api/v1/sessions/s-audit/audit/export?format=xml")
    assert status == 400
    assert body.get("code") == "invalid_format"
    status, body = _request(port, "GET", "/api/v1/sessions/s-audit/audit/export?format=json&retention_days=abc")
    assert status == 400
    assert body.get("code") == "invalid_retention"
    status, body = _request(port, "GET", "/api/v1/sessions/no-such/audit/export?format=json")
    assert status == 404
