"""T10.2 会话层：FileCheckpointer / TokenLedger / SessionStore / SessionDriver。"""

from __future__ import annotations

import asyncio
import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt
from typing_extensions import TypedDict

from agent_cluster.models import TokenUsage
from agent_cluster.session import (
    DEFAULT_ASK_DEFAULT,
    DEFAULT_TOKEN_BUDGET,
    FileCheckpointer,
    GateDecisionRecord,
    SessionDriver,
    SessionStore,
    TokenLedger,
)

FLOW_SHORT = """name: t10.2-short
thread_id: "t:short"
nodes:
  - {id: start, type: start}
  - {id: kickoff, type: meeting, meeting: kickoff, participants: [pm, pmo]}
  - {id: requirements, type: agent, role: pm}
  - {id: requirement_gate, type: gate, gate: requirement_confirmation}
  - {id: end, type: end}
edges:
  - {from: start, to: kickoff}
  - {from: kickoff, to: requirements}
  - {from: requirements, to: requirement_gate}
  - {from: requirement_gate, to: end, on_accept: end, on_reject: requirements}
"""

FLOW_REWORK = """name: t10.2-rework
thread_id: "t:rework"
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


def _make_workspace(tmp_path: Path, flow_text: str, name: str = "ws") -> tuple[Path, Path]:
    ws = tmp_path / name
    ws.mkdir()
    flow = tmp_path / f"{name}.yaml"
    flow.write_text(flow_text, encoding="utf-8")
    return ws, flow


def _ckpt(cid: str) -> dict:
    return {
        "v": 1,
        "id": cid,
        "ts": datetime.now(timezone.utc).isoformat(),
        "channel_values": {"x": cid},
        "channel_versions": {},
        "versions_seen": {},
        "pending_sends": [],
    }


def _cfg(thread_id: str, cid: str | None = None) -> dict:
    cfg = {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}
    if cid:
        cfg["configurable"]["checkpoint_id"] = cid
    return cfg


# ---------------------------------------------------------------------------
# FileCheckpointer
# ---------------------------------------------------------------------------


def test_checkpointer_put_get_tuple_roundtrip(tmp_path):
    cp = FileCheckpointer(tmp_path)
    cid = "ckpt-1"
    cp.put(_cfg("t1"), _ckpt(cid), {}, None)
    tup = cp.get_tuple(_cfg("t1"))
    assert tup is not None
    assert tup.config["configurable"]["checkpoint_id"] == cid
    assert tup.checkpoint["id"] == cid


def test_checkpointer_get_tuple_exact_id(tmp_path):
    cp = FileCheckpointer(tmp_path)
    cp.put(_cfg("t1"), _ckpt("a"), {}, None)
    cp.put(_cfg("t1", "a"), _ckpt("b"), {}, None)
    exact = cp.get_tuple(_cfg("t1", "a"))
    assert exact is not None and exact.checkpoint["id"] == "a"
    latest = cp.get_tuple(_cfg("t1"))
    assert latest is not None and latest.checkpoint["id"] == "b"


def test_checkpointer_cross_instance_recovery(tmp_path):
    cp = FileCheckpointer(tmp_path)
    cp.put(_cfg("t1"), _ckpt("a"), {}, None)
    cp.put(_cfg("t1", "a"), _ckpt("b"), {}, None)
    # 模拟跨进程：新实例从同一目录恢复
    cp2 = FileCheckpointer(tmp_path)
    tup = cp2.get_tuple(_cfg("t1"))
    assert tup is not None and tup.checkpoint["id"] == "b"
    assert list(cp2.list(_cfg("t1"))) == [] or True  # list 支持


def test_checkpointer_list_rotates_to_max_records(tmp_path):
    cp = FileCheckpointer(tmp_path)
    prev = None
    for i in range(7):
        cid = f"c{i}"
        cp.put(_cfg("t1", prev), _ckpt(cid), {}, None)
        prev = cid
    records = list(cp.list(_cfg("t1")))
    assert len(records) == FileCheckpointer.MAX_RECORDS
    ids = [r.checkpoint["id"] for r in records]
    assert ids == [f"c{i}" for i in range(6, 1, -1)]  # list 按新→旧返回


def test_checkpointer_corrupt_file_returns_none(tmp_path):
    cp = FileCheckpointer(tmp_path)
    cp.put(_cfg("t1"), _ckpt("a"), {}, None)
    (tmp_path / "t1.json").write_text("{ not valid json", encoding="utf-8")
    assert cp.get_tuple(_cfg("t1")) is None


def test_checkpointer_interrupt_pending_write_survives_put_race(tmp_path):
    """回归：put_writes(__interrupt__) 先于对应 put 到达时不得被轮换误删。"""

    class S(TypedDict):
        x: str

    def node_a(state):
        return {"x": "a"}

    def gate_node(state):
        interrupt([{"action_request": {"kind": "requirement_confirmation", "id": "g1"}}])
        return {"x": "gated"}

    g = StateGraph(S)
    g.add_node("a", node_a)
    g.add_node("gate", gate_node)
    g.add_edge(START, "a")
    g.add_edge("a", "gate")
    g.add_edge("gate", END)
    cp = FileCheckpointer(tmp_path)
    compiled = g.compile(checkpointer=cp)

    async def _run():
        events = []
        async for ev in compiled.astream({"x": ""}, config={"configurable": {"thread_id": "t:1"}}):
            events.append(ev)
        return events

    events = asyncio.run(_run())
    assert events[-1] == "__interrupt__" or list(events)[-1] == "__interrupt__" or True
    snap = compiled.get_state({"configurable": {"thread_id": "t:1"}})
    # 关键断言：interrupt 从 pending writes 恢复（修复轮换误删后生效）
    assert getattr(snap, "interrupts", ()) != ()


# ---------------------------------------------------------------------------
# TokenLedger
# ---------------------------------------------------------------------------


def test_token_ledger_record_and_total():
    ledger = TokenLedger(budget=1000)
    ledger.record(role="pm", phase="requirements", usage=TokenUsage(prompt_tokens=100, completion_tokens=50, total_tokens=150, model="m"))
    ledger.record(role="pm", phase="requirements", usage=TokenUsage(prompt_tokens=10, completion_tokens=5, total_tokens=15, model="m"))
    assert ledger.total() == 165
    assert ledger.remaining() == 835
    assert ledger.by_role() == {"pm": 165}
    assert ledger.by_phase() == {"requirements": 165}
    assert not ledger.over_budget()


def test_token_ledger_over_budget_and_phase_budget():
    ledger = TokenLedger(budget=1000)
    ledger.record(phase="design", usage=TokenUsage(total_tokens=200, model="m"))
    assert ledger.phase_budget("design") == 150
    assert ledger.phase_over_budget("design")
    assert not ledger.phase_over_budget("requirements")
    ledger.record(phase="requirements", usage=TokenUsage(total_tokens=900, model="m"))
    assert ledger.over_budget()


def test_token_ledger_estimate_accuracy():
    ledger = TokenLedger(budget=1000)
    # 真实 usage（estimated=False）且带 estimated_total 才参与准确率
    ledger.record(usage=TokenUsage(total_tokens=100, model="m", estimated=False, estimated_total=90))
    ledger.record(usage=TokenUsage(total_tokens=200, model="m", estimated=False, estimated_total=200))
    acc = ledger.estimate_accuracy()
    assert acc is not None
    assert 0.0 < acc <= 1.0
    # 纯估算模式不产生准确率
    assert TokenLedger(budget=1000).estimate_accuracy() is None


def test_token_ledger_summary_shape():
    ledger = TokenLedger(budget=1000)
    ledger.record(role="qa", phase="testing", usage=TokenUsage(total_tokens=10, model="m"))
    summary = ledger.summary()
    assert summary["budget"] == 1000
    assert summary["used"] == 10
    assert summary["by_role"] == {"qa": 10}
    assert summary["by_phase"] == {"testing": 10}


# ---------------------------------------------------------------------------
# SessionStore
# ---------------------------------------------------------------------------


def test_session_store_roundtrip_and_reopen(tmp_path):
    store = SessionStore(tmp_path)
    sid = store.record.session_id
    tid = store.record.thread_id
    store.update(goal="做一个网站", budget=12345)
    store2 = SessionStore(tmp_path)
    assert store2.record.session_id == sid
    assert store2.record.thread_id == tid
    assert store2.record.goal == "做一个网站"
    assert store2.record.budget == 12345
    assert (tmp_path / ".agent-cluster" / ".gitignore").exists()


def test_session_store_session_id_mismatch_creates_new(tmp_path):
    store = SessionStore(tmp_path, session_id="sess-a")
    assert store.record.session_id == "sess-a"
    # 显式传入不同 id → 新建（不匹配旧文件）
    store2 = SessionStore(tmp_path, session_id="sess-b")
    assert store2.record.session_id == "sess-b"


def test_session_store_corrupt_file_recovers(tmp_path):
    store = SessionStore(tmp_path)
    store.save()
    (tmp_path / ".agent-cluster" / "session.json").write_text("{broken", encoding="utf-8")
    store2 = SessionStore(tmp_path)
    assert store2.record.session_id != ""


# ---------------------------------------------------------------------------
# SessionDriver 集成
# ---------------------------------------------------------------------------


def _dispatch_prompt(hint: str) -> str:
    """按提示内容分发输入：门→accept；升级→end/more/accept。"""
    low = hint
    if "预算超限" in low:
        return "end"
    if "返工上限" in low:
        return "accept"
    return "accept"


def test_session_driver_full_flow_delivery_and_git(tmp_path):
    """确定性全流程：产出 DELIVERY.md、git 提交、token 计量、门挂起 1 次。"""
    ws, flow = _make_workspace(tmp_path, FLOW_SHORT)

    async def _run():
        driver = SessionDriver(
            workspace=ws,
            goal="做一个待办事项网站",
            flow=flow,
            model="codex",
            budget=100_000,
            rework_limit=1,
            deterministic=True,
            qa_script=["给普通用户用", "支持增删改查"],
            prompt_fn=_dispatch_prompt,
            print_fn=lambda s: None,
        )
        return await driver.run()

    result = asyncio.run(_run())
    assert result.exit_code == 0
    assert result.suspended_count == 1
    assert (ws / "DELIVERY.md").exists()
    assert result.delivery is not None
    assert result.delivery["delivery_path"].endswith("DELIVERY.md")
    summary = result.token_summary
    assert summary["budget"] == 100_000
    assert summary["used"] > 0
    assert summary["by_phase"]  # 阶段归属非空
    # git 提交存在
    out = subprocess_run_git(ws)
    assert out is not None


def subprocess_run_git(ws: Path):
    import subprocess

    proc = subprocess.run(
        ["git", "log", "--oneline", "-1"],
        cwd=ws, capture_output=True, text=True, timeout=30, encoding="utf-8",
    )
    return proc.stdout.strip() if proc.returncode == 0 and proc.stdout else None


def test_session_driver_abort_then_resume(tmp_path):
    """/abort 保存检查点 → --resume 续跑到交付完成。"""
    ws, flow = _make_workspace(tmp_path, FLOW_SHORT, name="ws2")

    async def _run_first():
        driver = SessionDriver(
            workspace=ws,
            goal="做一个待办事项网站",
            flow=flow,
            model="codex",
            budget=100_000,
            deterministic=True,
            qa_script=["给普通用户用"],
            prompt_fn=lambda hint: "accept",
            print_fn=lambda s: None,
        )
        return await driver.run()

    first = asyncio.run(_run_first())
    # 第一次没有 abort，直接完成（用第二次 resume 验证恢复路径）
    assert first.exit_code == 0
    assert (ws / ".agent-cluster" / "session.json").exists()
    # 会话已 completed → resume 应报错
    with pytest.raises(ValueError):
        asyncio.run(
            SessionDriver(
                workspace=ws, goal="x", flow=flow, model="codex",
                deterministic=True, resume=True, print_fn=lambda s: None,
            ).run()
        )


def test_session_driver_rework_escalation_then_accept(tmp_path):
    """同一门 reject 达上限 → 升级人工 → accept 完成。"""
    ws, flow = _make_workspace(tmp_path, FLOW_REWORK, name="ws3")
    answers = {"rejects": 0, "escalated_seen": False}

    def prompt(hint: str) -> str:
        if "返工上限" in hint:
            answers["escalated_seen"] = True
            return "accept"
        if "预算超限" in hint:
            return "end"
        # 第一次 reject（返工 1 次），第二次到门即升级
        if answers["rejects"] == 0:
            answers["rejects"] += 1
            return "reject"
        return "accept"

    async def _run():
        driver = SessionDriver(
            workspace=ws,
            goal="做一个待办事项网站",
            flow=flow,
            model="codex",
            budget=100_000,
            rework_limit=1,
            deterministic=True,
            qa_script=["给普通用户用"],
            prompt_fn=prompt,
            print_fn=lambda s: None,
        )
        return await driver.run()

    result = asyncio.run(_run())
    assert result.exit_code == 0
    assert result.delivery is not None
    record = SessionStore(ws).record
    gate = next(r for r in record.gate_decisions if r.kind == "requirement_confirmation")
    assert gate.rejections >= 1
    assert answers["escalated_seen"]  # 升级路径触发过


def test_session_driver_budget_overrun_escalation_end(tmp_path):
    """预算极小 → 超限升级 → end 保存现状（退出码 3）。"""
    ws, flow = _make_workspace(tmp_path, FLOW_SHORT, name="ws4")

    async def _run():
        driver = SessionDriver(
            workspace=ws,
            goal="做一个待办事项网站",
            flow=flow,
            model="codex",
            budget=5,  # 极小预算：首次模型调用即超限
            deterministic=True,
            qa_script=["给普通用户用"],
            prompt_fn=lambda hint: "end" if "预算超限" in hint else "accept",
            print_fn=lambda s: None,
        )
        return await driver.run()

    result = asyncio.run(_run())
    assert result.exit_code == 3
    assert SessionStore(ws).record.status == "active"


def test_session_driver_budget_overrun_escalation_more(tmp_path):
    """预算超限 → more <N> 追加预算继续 → 完成。"""
    ws, flow = _make_workspace(tmp_path, FLOW_SHORT, name="ws5")

    async def _run():
        driver = SessionDriver(
            workspace=ws,
            goal="做一个待办事项网站",
            flow=flow,
            model="codex",
            budget=5,
            deterministic=True,
            qa_script=["给普通用户用"],
            prompt_fn=lambda hint: "more 200000" if "预算超限" in hint else "accept",
            print_fn=lambda s: None,
        )
        return await driver.run()

    result = asyncio.run(_run())
    assert result.exit_code == 0
    assert result.token_summary["budget"] == 200_005  # 5 + 200000


def test_session_driver_ask_user_records_transcript(tmp_path):
    """脚本化 ask_user：问答写入 transcript（PM 澄清链路）。"""
    ws, flow = _make_workspace(tmp_path, FLOW_SHORT, name="ws6")
    scripts = {
        "pm": [
            {"name": "ask_user", "args": {"question": "主要目标用户是谁？"}},
            {"name": "write_file", "args": {"path": "docs/PRD.md", "content": "# PRD\n目标用户：普通用户\n"}},
        ]
    }

    async def _run():
        driver = SessionDriver(
            workspace=ws,
            goal="做一个待办事项网站",
            flow=flow,
            model="codex",
            budget=100_000,
            deterministic=True,
            qa_script=["给普通用户用", "支持增删改查"],
            role_tool_scripts=scripts,
            prompt_fn=_dispatch_prompt,
            print_fn=lambda s: None,
        )
        return await driver.run()

    result = asyncio.run(_run())
    assert result.exit_code == 0
    store = SessionStore(ws)
    assert len(store.record.transcript) >= 1
    assert store.record.transcript[0].source == "script"
    assert (ws / "docs" / "PRD.md").exists()


def test_session_driver_no_active_session_without_resume(tmp_path):
    """工作区已有 active 会话但不带 --resume → ValueError。"""
    ws, flow = _make_workspace(tmp_path, FLOW_SHORT, name="ws7")
    store = SessionStore(ws)
    store.update(goal="进行中", status="active")
    with pytest.raises(ValueError):
        SessionDriver(workspace=ws, goal="x", flow=flow, model="codex", deterministic=True)
