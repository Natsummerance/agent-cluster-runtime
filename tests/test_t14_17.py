from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

import agent_cluster.server as server_mod
from agent_cluster.events import KNOWN_SESSION_EVENT_TYPES, SessionEventLog
from agent_cluster.orchestration import (
    Orchestration,
    OwnerError,
    RoundLimitError,
    ScheduleValidationError,
    VersionConflictError,
    fold_goal,
    fold_plan_mode,
    fold_schedules,
)
from agent_cluster.server import WorkbenchHandler, WorkbenchServer
from agent_cluster.workflow import WorkflowEngine, WorkflowValidationError


# ---------------------------------------------------------------------------
# 事件词汇：plan/*、goal/*、job/*、schedule/* 只增不改既有
# ---------------------------------------------------------------------------


def test_event_vocabulary_adds_orchestration_types_only():
    required = {
        "plan/mode",
        "goal/change",
        "job/register",
        "job/settle",
        "schedule/change",
    }
    assert required <= KNOWN_SESSION_EVENT_TYPES
    # 既有词汇不受影响（surface 事件等仍存在）
    for existing in ("user/message", "assistant/message", "tool/result", "ledger/entry", "session/start"):
        assert existing in KNOWN_SESSION_EVENT_TYPES
    # 新增词汇可被 SessionEventLog.append 接受
    log = SessionEventLog("test")
    for etype in sorted(required):
        event = log.append(etype, {"id": "x"})
        assert event.type == etype


# ---------------------------------------------------------------------------
# Plan：mode 折叠（plan/mode 事件，whole-value-replace）
# ---------------------------------------------------------------------------


def test_plan_store_crud_and_mode_fold():
    orch = Orchestration()
    first = orch.plans.create_plan(name="发布计划")
    second = orch.plans.create_plan(name="迁移计划", mode="active")
    assert first.id and second.id and first.id != second.id
    assert first.mode == "inactive" and second.mode == "active"
    assert [p.id for p in orch.plans.list_plans()] == [first.id, second.id]
    assert orch.plans.get_plan(first.id) is first
    with pytest.raises(KeyError, match="nope"):
        orch.plans.get_plan("nope")
    # set_mode 翻转并更新折叠状态
    updated = orch.plans.set_mode(first.id, active=True)
    assert updated.mode == "active" and updated.updated_at >= first.updated_at
    assert orch.plans.get_plan(first.id).mode == "active"
    # 折叠：无事件默认 inactive；事件流末位决定（whole-value-replace）
    assert fold_plan_mode([]) is False
    log = SessionEventLog("plan-test")
    orch2 = Orchestration(log=log)
    plan = orch2.plans.create_plan(name="折叠演示")
    orch2.plans.set_mode(plan.id, active=True)
    orch2.plans.set_mode(plan.id, active=False)
    assert fold_plan_mode(log.events) is False
    orch2.plans.set_mode(plan.id, active=True)
    assert fold_plan_mode(log.events) is True


def test_plan_store_add_goal_and_job():
    orch = Orchestration()
    plan = orch.plans.create_plan()
    goal = orch.goals.create_goal(plan.id, "完成 14.17")
    job = orch.jobs.register(owner="alice", plan_id=plan.id)
    assert goal.id not in plan.goals
    plan = orch.plans.add_goal(plan.id, goal.id)
    plan = orch.plans.add_job(plan.id, job.id)
    assert plan.goals == [goal.id]
    assert plan.jobs == [job.id]
    with pytest.raises(KeyError):
        orch.plans.add_goal("nope", goal.id)


# ---------------------------------------------------------------------------
# Goal：快照 + CAS（expected_version）+ 轮次上限（默认 5，可配）
# ---------------------------------------------------------------------------


def test_goal_create_and_cas_change():
    orch = Orchestration()
    plan = orch.plans.create_plan()
    goal = orch.goals.create_goal(plan.id, "目标 A", max_rounds=3)
    assert goal.version == 1 and goal.rounds == 0 and goal.status == "active"
    assert goal.max_rounds == 3 and goal.plan_id == plan.id
    # CAS 成功：版本 +1
    changed = orch.goals.change_goal(goal.id, expected_version=1, objective="目标 A（修订）")
    assert changed.version == 2 and changed.objective == "目标 A（修订）"
    assert changed is orch.goals.get_goal(goal.id)
    # CAS 失败：版本不匹配 → VersionConflictError
    with pytest.raises(VersionConflictError, match="version"):
        orch.goals.change_goal(goal.id, expected_version=1)
    # 缺省 max_rounds = 5（可配）
    assert orch.goals.create_goal(plan.id, "默认轮次").max_rounds == 5
    assert Orchestration(max_rounds=2).goals.create_goal(plan.id, "配置轮次").max_rounds == 2


def test_goal_rounds_cap():
    orch = Orchestration()
    plan = orch.plans.create_plan()
    goal = orch.goals.create_goal(plan.id, "轮次上限", max_rounds=2)
    goal = orch.goals.change_goal(goal.id, expected_version=1, start_round=True)
    assert goal.rounds == 1 and goal.version == 2
    goal = orch.goals.change_goal(goal.id, expected_version=2, start_round=True)
    assert goal.rounds == 2 and goal.version == 3
    with pytest.raises(RoundLimitError, match="2"):
        orch.goals.change_goal(goal.id, expected_version=3, start_round=True)
    assert orch.goals.get_goal(goal.id).rounds == 2  # 失败不变状态
    with pytest.raises(KeyError):
        orch.goals.change_goal("nope", expected_version=1, start_round=True)


def test_goal_status_transition_and_blocked_reason():
    orch = Orchestration()
    plan = orch.plans.create_plan()
    goal = orch.goals.create_goal(plan.id, "阻塞演示")
    goal = orch.goals.change_goal(
        goal.id,
        expected_version=1,
        status="blocked",
        blocked_reason={"code": "missing_key", "message": "缺少凭据"},
    )
    assert goal.status == "blocked" and goal.blocked_reason["code"] == "missing_key"
    # 离开 blocked 时清空 reason
    goal = orch.goals.change_goal(goal.id, expected_version=2, status="active")
    assert goal.status == "active" and goal.blocked_reason is None
    with pytest.raises(ValueError, match="blocked_reason"):
        orch.goals.change_goal(goal.id, expected_version=3, status="blocked")
    with pytest.raises(ValueError, match="status"):
        orch.goals.change_goal(goal.id, expected_version=3, status="done")


def test_goal_events_and_fold():
    log = SessionEventLog("goal-test")
    orch = Orchestration(log=log)
    plan = orch.plans.create_plan()
    goal = orch.goals.create_goal(plan.id, "事件目标")
    orch.goals.change_goal(goal.id, expected_version=1, start_round=True)
    types = [event.type for event in log.events]
    assert "goal/change" in types
    # fold：按 goal id 取最后一个快照（含最新 version/rounds）
    snapshot = fold_goal(log.events, goal.id)
    assert snapshot is not None
    assert snapshot["version"] == 2 and snapshot["rounds"] == 1
    assert snapshot["objective"] == "事件目标"
    assert fold_goal(log.events, "nope") is None
    # 未接日志的服务不写事件
    assert Orchestration().goals.create_goal("p", "x").version == 1


# ---------------------------------------------------------------------------
# Jobs：first-wins settlement + owner 授权（幂等）
# ---------------------------------------------------------------------------


def test_job_register_settle_first_wins_and_owner():
    orch = Orchestration()
    job = orch.jobs.register(owner="alice")
    assert job.state == "pending" and job.owner == "alice"
    # 非 owner settle → OwnerError
    with pytest.raises(OwnerError, match="alice"):
        orch.jobs.settle(job.id, outcome="ok", caller="bob")
    assert orch.jobs.get_job(job.id).state == "pending"
    # owner first-wins
    settled, first = orch.jobs.settle(job.id, outcome="ok", caller="alice")
    assert first is True and settled.state == "settled" and settled.outcome == "ok"
    assert settled.settled_at
    # 幂等：重复 settle 返回当前状态（first=False，不覆盖 outcome）
    again, first = orch.jobs.settle(job.id, outcome="override", caller="alice")
    assert first is False and again is settled and again.outcome == "ok"
    with pytest.raises(KeyError, match="nope"):
        orch.jobs.settle("nope", outcome="ok", caller="alice")
    with pytest.raises(KeyError):
        orch.jobs.get_job("nope")


def test_job_events_logged():
    log = SessionEventLog("job-test")
    orch = Orchestration(log=log)
    job = orch.jobs.register(owner="alice")
    orch.jobs.settle(job.id, outcome="ok", caller="alice")
    types = [event.type for event in log.events]
    assert "job/register" in types and "job/settle" in types
    settle_event = next(event for event in log.events if event.type == "job/settle")
    assert settle_event.payload["job_id"] == job.id
    assert settle_event.payload["first"] is True


# ---------------------------------------------------------------------------
# Schedule：at/after/every，分钟粒度下限 5
# ---------------------------------------------------------------------------


def test_schedule_create_kinds_and_list():
    orch = Orchestration()
    at = orch.schedules.create_schedule("at", at="2030-01-01T09:00:00+00:00")
    after = orch.schedules.create_schedule("after", after_minutes=5)
    every = orch.schedules.create_schedule("every", every_minutes=10)
    assert at.kind == "at" and at.at == "2030-01-01T09:00:00+00:00"
    assert after.after_minutes == 5 and after.every_minutes is None
    assert every.every_minutes == 10
    assert [s.id for s in orch.schedules.list_schedules()] == [at.id, after.id, every.id]
    assert orch.schedules.get_schedule(every.id) is every
    with pytest.raises(KeyError, match="nope"):
        orch.schedules.get_schedule("nope")
    # 删除 + 事件
    orch.schedules.delete_schedule(after.id)
    assert [s.id for s in orch.schedules.list_schedules()] == [at.id, every.id]
    with pytest.raises(KeyError):
        orch.schedules.delete_schedule(after.id)


def test_schedule_minimum_interval_and_validation():
    orch = Orchestration()
    with pytest.raises(ScheduleValidationError, match="frequency_too_high"):
        orch.schedules.create_schedule("every", every_minutes=4)
    with pytest.raises(ScheduleValidationError, match="frequency_too_high"):
        orch.schedules.create_schedule("after", after_minutes=3)
    with pytest.raises(ScheduleValidationError, match="kind"):
        orch.schedules.create_schedule("daily")
    with pytest.raises(ScheduleValidationError, match="at"):
        orch.schedules.create_schedule("at", every_minutes=10)
    with pytest.raises(ScheduleValidationError, match="at"):
        orch.schedules.create_schedule("at", at="not-a-time")
    with pytest.raises(ScheduleValidationError, match="not_future"):
        orch.schedules.create_schedule("at", at="2020-01-01T09:00:00+00:00")
    with pytest.raises(ScheduleValidationError, match="after_minutes"):
        orch.schedules.create_schedule("after", every_minutes=10)


def test_schedule_events_and_fold():
    log = SessionEventLog("schedule-test")
    orch = Orchestration(log=log)
    at = orch.schedules.create_schedule("at", at="2030-01-01T09:00:00+00:00")
    every = orch.schedules.create_schedule("every", every_minutes=5)
    orch.schedules.delete_schedule(at.id)
    types = [event.type for event in log.events]
    assert all(t == "schedule/change" for t in types)
    assert len(types) == 3
    # fold：create 生效、delete 移除
    active = fold_schedules(log.events)
    assert [s["id"] for s in active] == [every.id]
    create_payload = next(
        e.payload
        for e in log.events
        if e.payload.get("operation") == "create" and "every_minutes" in e.payload.get("schedule", {})
    )
    assert create_payload["schedule"]["every_minutes"] == 5


# ---------------------------------------------------------------------------
# Workflow DSL 扩展：节点 resources + 边 depends_on（向后兼容）
# ---------------------------------------------------------------------------


EXTENDED_YAML = """
name: extended-flow
max_iterations: 20
thread_id: "proj:demo:iter:1"
nodes:
  - {id: start, type: start}
  - {id: train, type: agent, role: algorithm, resources: [gpu, dataset]}
  - {id: review, type: agent, role: qa, resources: [gpu]}
  - {id: end, type: end}
edges:
  - {from: start, to: train}
  - {from: train, to: review, depends_on: [train-model, eval-report]}
  - {from: review, to: end}
"""


def test_workflow_dsl_extension_parse_validate_compile():
    engine = WorkflowEngine(handlers={"agent": lambda *args: None})
    compiled = engine.compile(EXTENDED_YAML)
    spec = compiled.spec
    train = next(node for node in spec.nodes if node.id == "train")
    review = next(node for node in spec.nodes if node.id == "review")
    assert train.resources == ["gpu", "dataset"]
    assert review.resources == ["gpu"]
    edge = next(edge for edge in spec.edges if edge.from_ == "train")
    assert edge.depends_on == ["train-model", "eval-report"]
    # 编译三处同步：get_graph / resource_requirements / dependency_constraints
    graph = compiled.get_graph()
    train_dump = next(node for node in graph["nodes"] if node["id"] == "train")
    assert train_dump["resources"] == ["gpu", "dataset"]
    assert compiled.resource_requirements() == {"train": ["gpu", "dataset"], "review": ["gpu"]}
    constraints = compiled.dependency_constraints()
    assert constraints == [{"from": "train", "to": "review", "depends_on": ["train-model", "eval-report"]}]
    # 缺省字段的节点/边不受影响（向后兼容）
    start = next(node for node in spec.nodes if node.id == "start")
    assert start.resources is None
    assert next(edge for edge in spec.edges if edge.from_ == "start").depends_on is None


def test_workflow_dsl_extension_validation_fail_loud():
    engine = WorkflowEngine()
    base = """
name: ext-validate
max_iterations: 20
thread_id: "proj:demo:iter:1"
nodes:
  - {id: start, type: start}
  - {id: work, type: agent, role: backend}
  - {id: end, type: end}
edges:
  - {from: start, to: work}
  - {from: work, to: end}
"""
    # 空/空白 resources 条目
    bad = base.replace('role: backend}', 'role: backend, resources: ["gpu", ""]}')
    with pytest.raises(WorkflowValidationError, match="resources"):
        engine.compile(bad)
    # 重复 resources
    bad = base.replace('role: backend}', 'role: backend, resources: ["gpu", "gpu"]}')
    with pytest.raises(WorkflowValidationError, match="resources"):
        engine.compile(bad)
    # 空 depends_on 条目
    bad = base.replace("{from: work, to: end}", '{from: work, to: end, depends_on: ["t1", " "]}')
    with pytest.raises(WorkflowValidationError, match="depends_on"):
        engine.compile(bad)
    # 重复 depends_on
    bad = base.replace("{from: work, to: end}", '{from: work, to: end, depends_on: ["t1", "t1"]}')
    with pytest.raises(WorkflowValidationError, match="depends_on"):
        engine.compile(bad)
    # 旧 DSL（无扩展字段）仍编译通过
    engine.compile(base)


def test_workflow_dsl_extension_backward_compat_run():
    import asyncio

    yaml_text = """
name: compat-flow
max_iterations: 10
thread_id: "proj:demo:iter:1"
nodes:
  - {id: start, type: start}
  - {id: work, type: agent, role: backend}
  - {id: end, type: end}
edges:
  - {from: start, to: work}
  - {from: work, to: end}
"""

    async def fake_handler(state, node, ctx):
        return {}

    engine = WorkflowEngine(handlers={"agent": fake_handler})
    compiled = engine.compile(yaml_text)
    events = []

    async def run():
        async for event in compiled.run():
            events.append(event)

    asyncio.run(run())
    types = [event.type for event in events]
    assert "workflow_start" in types and "workflow_end" in types
    assert compiled.resource_requirements() == {}
    assert compiled.dependency_constraints() == []


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
def orchestration_server(tmp_path, monkeypatch):
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


def test_plan_endpoints_crud(orchestration_server):
    port, workbench = orchestration_server
    assert hasattr(workbench, "orchestration") and isinstance(workbench.orchestration, Orchestration)
    status, body = _request(port, "GET", "/api/v1/plans")
    assert status == 200 and body["ok"] is True
    assert body["data"]["plans"] == []
    status, created = _request(port, "POST", "/api/v1/plans", {"name": "发布计划", "mode": "active"})
    assert status == 201, created
    plan = created["data"]["plan"]
    assert plan["id"] and plan["name"] == "发布计划" and plan["mode"] == "active"
    assert plan["goals"] == [] and plan["jobs"] == []
    status, body = _request(port, "GET", "/api/v1/plans")
    assert [p["id"] for p in body["data"]["plans"]] == [plan["id"]]
    status, detail = _request(port, "GET", f"/api/v1/plans/{plan['id']}")
    assert status == 200 and detail["data"]["plan"]["id"] == plan["id"]
    assert detail["data"]["goals"] == [] and detail["data"]["jobs"] == []
    status, body = _request(port, "GET", "/api/v1/plans/nope")
    assert status == 404


def test_plan_goal_change_endpoints(orchestration_server):
    port, _ = orchestration_server
    status, created = _request(port, "POST", "/api/v1/plans", {"name": "目标计划"})
    plan = created["data"]["plan"]
    # 创建目标
    status, body = _request(
        port, "POST", f"/api/v1/plans/{plan['id']}/goals",
        {"objective": "完成 14.17", "max_rounds": 2},
    )
    assert status == 201, body
    goal = body["data"]["goal"]
    assert goal["version"] == 1 and goal["rounds"] == 0 and goal["max_rounds"] == 2
    assert goal["objective"] == "完成 14.17"
    # 计划详情包含目标
    status, detail = _request(port, "GET", f"/api/v1/plans/{plan['id']}")
    assert detail["data"]["plan"]["goals"] == [goal["id"]]
    assert detail["data"]["goals"][0]["id"] == goal["id"]
    # CAS 不匹配 → 409 version_conflict
    status, body = _request(port, "POST", f"/api/v1/goals/{goal['id']}/change", {"expected_version": 9})
    assert status == 409 and body.get("code") == "version_conflict"
    # 轮次推进 + 轮次上限 → 400 round_limit
    status, body = _request(
        port, "POST", f"/api/v1/goals/{goal['id']}/change",
        {"expected_version": 1, "start_round": True},
    )
    assert status == 200 and body["data"]["goal"]["rounds"] == 1
    status, body = _request(
        port, "POST", f"/api/v1/goals/{goal['id']}/change",
        {"expected_version": 2, "start_round": True},
    )
    assert status == 200 and body["data"]["goal"]["rounds"] == 2
    status, body = _request(
        port, "POST", f"/api/v1/goals/{goal['id']}/change",
        {"expected_version": 3, "start_round": True},
    )
    assert status == 400 and body.get("code") == "round_limit"
    # 缺 expected_version → 400
    status, body = _request(port, "POST", f"/api/v1/goals/{goal['id']}/change", {})
    assert status == 400 and body.get("code") == "bad_request"
    # 目标不存在 → 404
    status, body = _request(port, "POST", "/api/v1/goals/nope/change", {"expected_version": 1})
    assert status == 404


def test_job_settle_endpoint_owner_and_idempotent(orchestration_server):
    port, workbench = orchestration_server
    foreign = workbench.orchestration.jobs.register(owner="alice")
    # 非 owner（API 身份为 admin）→ 403 owner_required
    status, body = _request(port, "POST", f"/api/v1/jobs/{foreign.id}/settle", {"outcome": "ok"})
    assert status == 403 and body.get("code") == "owner_required"
    job = workbench.orchestration.jobs.register(owner="admin")
    # owner → first-wins
    status, body = _request(port, "POST", f"/api/v1/jobs/{job.id}/settle", {"outcome": "ok"})
    assert status == 200 and body["data"]["first"] is True
    assert body["data"]["job"]["state"] == "settled" and body["data"]["job"]["outcome"] == "ok"
    # 幂等：再次 settle 返回当前状态（first=False，不覆盖）
    status, body = _request(port, "POST", f"/api/v1/jobs/{job.id}/settle", {"outcome": "override"})
    assert status == 200 and body["data"]["first"] is False
    assert body["data"]["job"]["outcome"] == "ok"
    status, body = _request(port, "POST", "/api/v1/jobs/nope/settle", {"outcome": "ok"})
    assert status == 404


def test_schedule_endpoints(orchestration_server):
    port, _ = orchestration_server
    status, body = _request(port, "GET", "/api/v1/schedules")
    assert status == 200 and body["data"]["schedules"] == []
    # every < 5 → 400 frequency_too_high
    status, body = _request(port, "POST", "/api/v1/schedules", {"kind": "every", "every_minutes": 3})
    assert status == 400 and body.get("code") == "frequency_too_high"
    status, body = _request(port, "POST", "/api/v1/schedules", {"kind": "after", "after_minutes": 5})
    assert status == 201, body
    after = body["data"]["schedule"]
    assert after["kind"] == "after" and after["after_minutes"] == 5
    status, body = _request(port, "POST", "/api/v1/schedules", {"kind": "at", "at": "2030-01-01T09:00:00+00:00"})
    assert status == 201
    status, body = _request(port, "POST", "/api/v1/schedules", {"kind": "at", "at": "2020-01-01T09:00:00+00:00"})
    assert status == 400 and body.get("code") == "not_future"
    status, body = _request(port, "GET", "/api/v1/schedules")
    assert [s["kind"] for s in body["data"]["schedules"]] == ["after", "at"]


def test_orchestration_endpoints_auth_required(tmp_path, monkeypatch):
    monkeypatch.setattr(server_mod, "INDEX_DIR", tmp_path / "home")
    workbench = WorkbenchServer(host="127.0.0.1", port=0, auth_token="s3cret")
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), WorkbenchHandler)
    httpd.workbench = workbench
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        status, body = _request(port, "GET", "/api/v1/plans")
        assert status == 401 and body.get("code") == "not_authorized"
        status, body = _request(port, "POST", "/api/v1/plans", {"name": "x"})
        assert status == 401
        status, body = _request(port, "POST", "/api/v1/schedules", {"kind": "every", "every_minutes": 10})
        assert status == 401
        status, body = _request(port, "GET", "/api/v1/plans", token="s3cret")
        assert status == 200, body
    finally:
        httpd.shutdown()
        httpd.server_close()
