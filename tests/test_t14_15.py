"""Task 14.15 资源日历：Availability/ResourceCalendar CRUD、重叠冲突 fail loud、serve 端点。"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

import agent_cluster.server as server_mod
from agent_cluster.calendar import OverlapError, ResourceCalendar
from agent_cluster.server import WorkbenchHandler, WorkbenchServer


# ---------------------------------------------------------------------------
# Availability 与 ResourceCalendar CRUD（fail loud）
# ---------------------------------------------------------------------------


def test_calendar_add_and_list_sorted():
    cal = ResourceCalendar()
    first = cal.add_availability(role_id="backend", start="2026-08-14T09:00:00+00:00", end="2026-08-14T12:00:00+00:00", note="上午")
    second = cal.add_availability(role_id="backend", start="2026-08-15T09:00:00+00:00", end="2026-08-15T11:00:00+00:00")
    assert first.id and second.id and first.id != second.id
    assert first.role_id == "backend" and first.note == "上午"
    assert second.note == ""
    assert first.created_at
    # list 按开始时间排序
    assert [item.id for item in cal.list_availability()] == [first.id, second.id]
    assert cal.get_availability(first.id) is first
    with pytest.raises(KeyError, match="nope"):
        cal.get_availability("nope")


def test_calendar_add_fail_loud_invalid_input():
    cal = ResourceCalendar()
    with pytest.raises(ValueError, match="role_id"):
        cal.add_availability(role_id="", start="2026-08-14T09:00:00", end="2026-08-14T12:00:00")
    with pytest.raises(ValueError, match="未注册岗位"):
        cal.add_availability(role_id="sre", start="2026-08-14T09:00:00", end="2026-08-14T12:00:00")
    with pytest.raises(ValueError, match="ISO 8601"):
        cal.add_availability(role_id="backend", start="not-a-time", end="2026-08-14T12:00:00")
    with pytest.raises(ValueError, match="end"):
        cal.add_availability(role_id="backend", start="2026-08-14T09:00:00", end="2026-08-14T09:00:00")
        cal.add_availability(role_id="backend", start="2026-08-14T12:00:00", end="2026-08-14T09:00:00")
    # 裸/带时区 ISO 均可，且不做时区感知冲突（统一归一化到 UTC 语义）
    cal.add_availability(role_id="backend", start="2026-08-14T09:00:00", end="2026-08-14T12:00:00")
    cal.add_availability(role_id="backend", start="2026-08-14T13:00:00Z", end="2026-08-14T15:00:00+00:00")


def test_calendar_overlap_conflict_fail_loud():
    cal = ResourceCalendar()
    block = cal.add_availability(role_id="backend", start="2026-08-14T09:00:00", end="2026-08-14T12:00:00")
    # 同岗位重叠：包含、部分、完全相同 一律 OverlapError，消息带冲突块 id
    for start, end in [
        ("2026-08-14T08:00:00", "2026-08-14T10:00:00"),
        ("2026-08-14T11:00:00", "2026-08-14T13:00:00"),
        ("2026-08-14T09:00:00", "2026-08-14T12:00:00"),
        ("2026-08-14T09:30:00", "2026-08-14T11:30:00"),
    ]:
        with pytest.raises(OverlapError) as excinfo:
            cal.add_availability(role_id="backend", start=start, end=end)
        assert block.id in str(excinfo.value)
    # 相邻（end == start）不冲突
    cal.add_availability(role_id="backend", start="2026-08-14T12:00:00", end="2026-08-14T14:00:00")
    # 不同岗位同一时间段不冲突
    cal.add_availability(role_id="qa", start="2026-08-14T09:00:00", end="2026-08-14T12:00:00")
    assert len(cal.list_availability()) == 3


def test_calendar_list_filter_role_and_range():
    cal = ResourceCalendar()
    cal.add_availability(role_id="backend", start="2026-08-14T09:00:00", end="2026-08-14T12:00:00")
    cal.add_availability(role_id="backend", start="2026-08-16T09:00:00", end="2026-08-16T12:00:00")
    cal.add_availability(role_id="qa", start="2026-08-15T09:00:00", end="2026-08-15T12:00:00")
    # 按岗位过滤
    assert [a.role_id for a in cal.list_availability(role_id="qa")] == ["qa"]
    # 时间窗口相交过滤（含跨边界块）
    window = cal.list_availability(from_="2026-08-14T08:00:00", to="2026-08-15T10:00:00")
    assert [a.role_id for a in window] == ["backend", "qa"]
    both = cal.list_availability(role_id="backend", from_="2026-08-16T00:00:00", to="2026-08-16T23:59:59")
    assert [a.id for a in both] == [cal.list_availability(role_id="backend")[1].id]
    with pytest.raises(ValueError, match="ISO 8601"):
        cal.list_availability(from_="bad", to="2026-08-15T10:00:00")
    with pytest.raises(ValueError, match="晚于"):
        cal.list_availability(from_="2026-08-16T10:00:00", to="2026-08-15T10:00:00")


def test_calendar_remove_fail_loud():
    cal = ResourceCalendar()
    block = cal.add_availability(role_id="backend", start="2026-08-14T09:00:00", end="2026-08-14T12:00:00")
    cal.remove_availability(block.id)
    assert cal.list_availability() == []
    with pytest.raises(KeyError, match=block.id):
        cal.remove_availability(block.id)


# ---------------------------------------------------------------------------
# budget/ledger 集成点：可用性判定
# ---------------------------------------------------------------------------


def test_calendar_availability_integration_for_budget_ledger():
    cal = ResourceCalendar()
    block = cal.add_availability(role_id="backend", start="2026-08-14T09:00:00", end="2026-08-14T12:00:00")
    assert cal.is_available("backend", "2026-08-14T10:00:00", "2026-08-14T11:00:00") is False
    assert cal.is_available("backend", "2026-08-14T12:00:00", "2026-08-14T14:00:00") is True
    assert cal.is_available("qa", "2026-08-14T10:00:00", "2026-08-14T11:00:00") is True
    assert [c.id for c in cal.conflicts("backend", "2026-08-14T10:00:00", "2026-08-14T11:00:00")] == [block.id]
    assert cal.conflicts("backend", "2026-08-14T12:00:00", "2026-08-14T14:00:00") == []
    with pytest.raises(OverlapError, match=block.id):
        cal.assert_available("backend", "2026-08-14T10:00:00", "2026-08-14T11:00:00")
    cal.assert_available("backend", "2026-08-14T12:00:00", "2026-08-14T14:00:00")  # 不抛


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
def calendar_server(tmp_path, monkeypatch):
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


def test_calendar_endpoints_crud(calendar_server):
    port, workbench = calendar_server
    assert hasattr(workbench, "calendar") and isinstance(workbench.calendar, ResourceCalendar)
    status, body = _request(port, "GET", "/api/v1/calendar")
    assert status == 200 and body["ok"] is True
    assert body["data"]["availability"] == []
    status, created = _request(
        port, "POST", "/api/v1/calendar",
        {"role_id": "backend", "start": "2026-08-14T09:00:00", "end": "2026-08-14T12:00:00", "note": "上午"},
    )
    assert status == 201, created
    item = created["data"]["availability"]
    assert item["role_id"] == "backend" and item["note"] == "上午"
    assert item["id"] and item["created_at"]
    status, body = _request(port, "GET", "/api/v1/calendar")
    assert [a["id"] for a in body["data"]["availability"]] == [item["id"]]
    # 按岗位 + 时间窗口过滤
    status, body = _request(port, "GET", "/api/v1/calendar?role_id=qa")
    assert body["data"]["availability"] == []
    status, body = _request(port, "GET", "/api/v1/calendar?role_id=backend&from=2026-08-14T08:00:00&to=2026-08-14T11:00:00")
    assert [a["id"] for a in body["data"]["availability"]] == [item["id"]]
    # 删除
    status, body = _request(port, "DELETE", f"/api/v1/calendar/{item['id']}")
    assert status == 200 and body["data"]["removed"] == item["id"]
    status, body = _request(port, "GET", "/api/v1/calendar")
    assert body["data"]["availability"] == []
    status, body = _request(port, "DELETE", f"/api/v1/calendar/{item['id']}")
    assert status == 404


def test_calendar_endpoints_fail_loud(calendar_server):
    port, _ = calendar_server
    # 非法输入 → 400 bad_request
    status, body = _request(port, "POST", "/api/v1/calendar", {"role_id": "sre", "start": "2026-08-14T09:00:00", "end": "2026-08-14T12:00:00"})
    assert status == 400, body
    assert body.get("code") == "bad_request"
    status, body = _request(port, "POST", "/api/v1/calendar", {"role_id": "backend", "start": "2026-08-14T12:00:00", "end": "2026-08-14T09:00:00"})
    assert status == 400
    # 重叠冲突 → 409 overlap
    ok = _request(port, "POST", "/api/v1/calendar", {"role_id": "backend", "start": "2026-08-14T09:00:00", "end": "2026-08-14T12:00:00"})
    assert ok[0] == 201
    status, body = _request(port, "POST", "/api/v1/calendar", {"role_id": "backend", "start": "2026-08-14T10:00:00", "end": "2026-08-14T11:00:00"})
    assert status == 409, body
    assert body.get("code") == "overlap"


def test_calendar_endpoints_auth_required(tmp_path, monkeypatch):
    monkeypatch.setattr(server_mod, "INDEX_DIR", tmp_path / "home")
    workbench = WorkbenchServer(host="127.0.0.1", port=0, auth_token="s3cret")
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), WorkbenchHandler)
    httpd.workbench = workbench
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        status, body = _request(port, "GET", "/api/v1/calendar")
        assert status == 401 and body.get("code") == "not_authorized"
        status, body = _request(port, "POST", "/api/v1/calendar", {"role_id": "backend", "start": "2026-08-14T09:00:00", "end": "2026-08-14T12:00:00"}, token=None)
        assert status == 401
        status, body = _request(port, "GET", "/api/v1/calendar", token="s3cret")
        assert status == 200, body
    finally:
        httpd.shutdown()
        httpd.server_close()