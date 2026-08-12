"""T12.8 进化集成：记忆失败模式 → 自动提案 → 复盘报告 → 纪要提炼/SOP 建议。"""

from __future__ import annotations

from datetime import datetime

import pytest

from agent_cluster.evolution_integration import (
    DEFAULT_ROLLBACK_PLAN,
    EvolutionBridge,
    SOP_FILENAME,
)
from agent_cluster.memory import MemoryStatus, MemoryStore, Tier
from agent_cluster.models import Event


@pytest.fixture()
def bridge(tmp_path):
    return EvolutionBridge(tmp_path)


def _event(event_type: str, payload: dict, actor: str = "reviewer") -> Event:
    return Event(
        id=f"evt-{event_type}-{actor}-{id(payload)}",
        run_id="run-1",
        thread_id="thread-1",
        type=event_type,
        actor=actor,
        payload=payload,
        ts=datetime(2026, 8, 1, 12, 0, 0),
    )


# ---------------------------------------------------------------------------
# ① 记忆 → 自动提案
# ---------------------------------------------------------------------------


def test_generate_from_memory_creates_proposals(bridge):
    store = bridge.memory
    store.add_candidate(
        title="需求歧义导致返工",
        content="PRD 未定义验收标准，导致设计阶段返工 2 次。",
        source="pm",
        meta={"kind": "failure"},
    )
    store.add_candidate(
        title="测试环境缺少依赖",
        content="CI 未安装 pytest，测试全部失败。",
        source="qa",
        meta={"kind": "gotcha", "category": "skill"},
    )
    result = bridge.generate_from_memory()
    assert len(result["created"]) == 2
    assert result["skipped"] == []
    for proposal in result["created"]:
        assert proposal["status"] == "draft"
        assert proposal["rollback_plan"] == DEFAULT_ROLLBACK_PLAN
        assert proposal["change_diff"]["memory_id"]
    # 幂等：再次生成不重复
    again = bridge.generate_from_memory()
    assert again["created"] == []
    assert sorted(bridge.list_proposals(), key=lambda p: p["id"]) == sorted(result["created"], key=lambda p: p["id"])


def test_generate_from_memory_evidence_threshold(bridge):
    store = bridge.memory
    item_id = store.add_candidate(title="低频经验", content="偶尔命中的经验。")
    store.add_evidence(item_id, "s1")
    # 证据不足且非学习类型 -> 不生成
    assert bridge.generate_from_memory()["created"] == []
    store.add_evidence(item_id, "s2")
    assert bridge.generate_from_memory()["created"] == []
    store.promote(item_id)
    created = bridge.generate_from_memory()["created"]
    assert len(created) == 1


def test_generate_from_memory_self_empowerment_skipped(bridge):
    store = bridge.memory
    store.add_candidate(
        title="权限配置",
        content="建议扩大 approval_scope 以便自动放行。",
        source="pm",
        meta={"kind": "sop_suggestion"},
    )
    result = bridge.generate_from_memory()
    assert result["created"] == []
    assert len(result["skipped"]) == 1
    assert "自我扩权" in result["skipped"][0]["reason"] or "权限" in result["skipped"][0]["reason"]


def test_category_mapping(bridge):
    store = bridge.memory
    store.add_candidate(title="SOP 改进", content="改进测试流程。", source="pm", meta={"kind": "sop_suggestion"})
    store.add_candidate(title="跨项目坑", content="Redis 大 key 阻塞。", source="dev", meta={"kind": "gotcha"})
    store.add_candidate(title="QA 经验", content="断言应先写。", source="qa", meta={"kind": "lesson"})
    created = bridge.generate_from_memory()["created"]
    by_target = {item["target"]: item["category"] for item in created}
    assert by_target["SOP 改进"] == "process"
    assert by_target["跨项目坑"] == "knowledge"
    assert by_target["QA 经验"] == "skill"


# ---------------------------------------------------------------------------
# ② 自动复盘报告
# ---------------------------------------------------------------------------


def test_retro_report_written(bridge):
    store = bridge.memory
    store.add_candidate(
        title="返工根因", content="需求歧义。", source="retro", meta={"kind": "retro"}
    )
    path = bridge.generate_retro_report(
        goal="构建待办应用",
        session_id="sess-1",
        token_summary={
            "budget": 500000,
            "used": 12000,
            "remaining": 488000,
            "by_phase": {"开发": 8000, "需求": 4000},
            "by_role": {"dev": 8000, "pm": 4000},
            "estimate_accuracy": 0.9,
        },
        gate_decisions=[
            {"node": "requirement_gate", "kind": "requirement_confirmation", "attempts": 2, "rejections": 1, "escalated": False}
        ],
        events=[
            _event("review_result", {"verdict": "reject", "target": "frontend-design"}),
            _event("retro", {"root_cause": "验收标准缺失"}, actor="pm"),
        ],
    )
    assert path.exists()
    text = path.read_text(encoding="utf-8")
    assert "# 自动复盘报告" in text
    assert "构建待办应用" in text
    assert "requirement_gate" in text
    assert "12000" in text
    assert "返工根因" in text
    assert "评审驳回：frontend-design" in text
    assert "验收标准缺失" in text


def test_retro_report_empty_inputs(bridge):
    path = bridge.generate_retro_report()
    text = path.read_text(encoding="utf-8")
    assert "（本次无门决策记录）" in text
    assert "（无 token 计量数据）" in text


# ---------------------------------------------------------------------------
# ③ 会议纪要提炼 + SOP 建议
# ---------------------------------------------------------------------------


def test_capture_meeting_notes_and_sop(bridge):
    result = bridge.capture_session_learnings(
        meeting_notes=[
            "启动会确认 MVP 范围。",
            "建议：需求评审前必须完成验收标准初稿。",
            "改进建议：QA 应在开发中提前编写测试用例。",
        ],
        session_id="sess-1",
        source="meeting",
    )
    assert len(result["notes"]) == 3
    assert len(result["sop_suggestions"]) == 2
    assert len(result["proposals"]) == 2
    items = bridge.memory.list_items(status=MemoryStatus.CANDIDATE)
    kinds = {str(item.meta.get("kind")) for item in items}
    assert "meeting_note" in kinds
    assert "sop_suggestion" in kinds
    # 证据已计入（同一会话一次）
    sop_items = [item for item in items if str(item.meta.get("kind")) == "sop_suggestion"]
    assert all(item.evidence_count == 1 for item in sop_items)
    # 晋升提案存在
    assert len(bridge.memory.list_proposals()) == 2


def test_capture_failure_events(bridge):
    result = bridge.capture_session_learnings(
        session_id="sess-2",
        events=[
            _event("review_result", {"verdict": "lbtm", "target": "backend-api", "reason": "缺鉴权"}),
            _event("retro", {"root_cause": ["需求模糊", "验收标准缺失"]}, actor="pm"),
        ],
    )
    assert len(result["failures"]) == 3  # 1 个评审驳回 + 2 个复盘根因
    items = bridge.memory.list_items(status=MemoryStatus.CANDIDATE)
    kinds = {str(item.meta.get("kind")) for item in items}
    assert "failure" in kinds
    assert "retro" in kinds


# ---------------------------------------------------------------------------
# ④ 评审门 → 生效 / 回滚 + SOP 同步
# ---------------------------------------------------------------------------


def test_apply_and_sync_sop(bridge):
    bridge.memory.add_candidate(title="流程改进", content="内容。", source="pm", meta={"kind": "sop_suggestion"})
    (proposal,) = bridge.generate_from_memory()["created"]
    applied = bridge.apply_proposal(proposal["id"])
    assert applied["status"] == "applied"
    assert applied["effective_version"] == "v1"
    assert applied["gray"] is True
    # process 类 -> SOP.md 已同步
    sop = (bridge.root / ".agent-cluster" / SOP_FILENAME).read_text(encoding="utf-8")
    assert "# SOP 变更记录" in sop
    assert "v1" in sop
    assert "回滚" in sop


def test_apply_bypass_immune_for_organization(bridge):
    bridge.memory.add_candidate(
        title="组织流程变更",
        content="变更审批流程。",
        source="governance",
        meta={"kind": "sop_suggestion", "category": "organization"},
    )
    (proposal,) = bridge.generate_from_memory()["created"]
    applied = bridge.apply_proposal(proposal["id"], human_required=True, auto_mode="auto")
    assert applied["status"] == "rejected"
    assert "bypass-immune" in applied["votes"][-1]["reason"]
    assert not (bridge.root / ".agent-cluster" / SOP_FILENAME).exists()


def test_rollback_applied_proposal(bridge):
    bridge.memory.add_candidate(title="经验沉淀", content="内容。", source="qa", meta={"kind": "gotcha", "category": "skill"})
    (proposal,) = bridge.generate_from_memory()["created"]
    bridge.apply_proposal(proposal["id"])
    rolled = bridge.rollback_proposal(proposal["id"], reason="灰度回归")
    assert rolled["status"] == "rolled_back"


def test_apply_unknown_proposal_raises(bridge):
    with pytest.raises(KeyError):
        bridge.apply_proposal("missing-id")


def test_list_proposals_filter(bridge):
    store = bridge.memory
    store.add_candidate(title="A", content="内容。", source="qa", meta={"kind": "failure"})
    store.add_candidate(title="B", content="内容。", source="qa", meta={"kind": "gotcha"})
    bridge.generate_from_memory()
    assert len(bridge.list_proposals()) == 2
    assert len(bridge.list_proposals(status="draft")) == 2
    assert bridge.list_proposals(status="applied") == []


def test_proposals_persist_across_instances(tmp_path):
    first = EvolutionBridge(tmp_path)
    first.memory.add_candidate(title="持久化", content="内容。", source="qa", meta={"kind": "failure"})
    first.generate_from_memory()
    second = EvolutionBridge(tmp_path)
    assert len(second.list_proposals()) == 1
# ---------------------------------------------------------------------------
# serve 端点接线
# ---------------------------------------------------------------------------


def _make_server(tmp_path, monkeypatch):
    import json
    import queue
    import threading
    import urllib.error
    import urllib.request
    from http.server import ThreadingHTTPServer

    import agent_cluster.server as server_mod
    from agent_cluster.server import WorkbenchHandler, WorkbenchServer

    monkeypatch.setattr(server_mod, "INDEX_DIR", tmp_path / "home")
    ws = WorkbenchServer(host="127.0.0.1", port=0, auth_token="")
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), WorkbenchHandler)
    httpd.workbench = ws
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()

    def call(method, path, body=None):
        data = json.dumps(body or {}).encode("utf-8") if body is not None else None
        req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=data, method=method)
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))

    yield call, ws
    httpd.shutdown()
    httpd.server_close()


def test_serve_evolution_endpoints(tmp_path, monkeypatch):
    gen = _make_server(tmp_path, monkeypatch)
    call, _ = next(gen)
    # 建项目 + 种子失败模式记忆
    status, body = call("POST", "/api/v1/projects", {"name": "evo", "workspace": str(tmp_path / "proj")})
    assert status == 201
    pid = body["data"]["id"]
    store = MemoryStore(tmp_path / "proj")
    store.add_candidate(title="返工根因", content="验收标准缺失。", source="pm", meta={"kind": "retro", "category": "knowledge"})
    # generate
    status, body = call("POST", "/api/v1/evolution/generate", {"project_id": pid, "min_evidence": 1})
    assert status == 200
    assert len(body["data"]["created"]) == 1
    # list（全局空、项目 1 条）
    assert len(call("GET", "/api/v1/evolution/proposals")[1]["data"]["proposals"]) == 0
    scoped = call("GET", f"/api/v1/evolution/proposals?project_id={pid}")
    assert len(scoped[1]["data"]["proposals"]) == 1
    proposal_id = scoped[1]["data"]["proposals"][0]["id"]
    # apply
    status, body = call("POST", f"/api/v1/evolution/proposals/{proposal_id}/apply", {"project_id": pid})
    assert status == 200
    assert body["data"]["proposal"]["status"] == "applied"
    assert body["data"]["proposal"]["effective_version"] == "v1"
    # retro
    status, body = call("POST", "/api/v1/evolution/retro", {"project_id": pid})
    assert status == 200
    assert (tmp_path / "proj" / "docs").exists()
    # rollback
    status, body = call("POST", f"/api/v1/evolution/proposals/{proposal_id}/rollback", {"project_id": pid, "reason": "回归"})
    assert body["data"]["proposal"]["status"] == "rolled_back"


def test_serve_evolution_unknown_route(tmp_path, monkeypatch):
    gen = _make_server(tmp_path, monkeypatch)
    call, _ = next(gen)
    status, body = call("POST", "/api/v1/evolution/proposals/nope/apply", {})
    assert status == 404
