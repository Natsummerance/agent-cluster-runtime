"""T10.3 ask_user / human_interaction：--yes 缺省留痕、脚本化问答、工具层审批缓存。"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from agent_cluster.models import GateKind
from agent_cluster.session import DEFAULT_ASK_DEFAULT, SessionDriver, SessionStore
from agent_cluster.tools import ToolCall, ToolPermission, ToolSession, build_default_tools

FLOW_ASK = """name: t10.3-ask
thread_id: "t:ask"
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

PM_ASK_SCRIPT = {
    "pm": [
        {"name": "ask_user", "args": {"question": "主要目标用户是谁？"}},
        {"name": "write_file", "args": {"path": "docs/PRD.md", "content": "# PRD\n目标用户：普通用户\n"}},
    ]
}


def _write(tmp_path: Path, name: str) -> tuple[Path, Path]:
    ws = tmp_path / name
    ws.mkdir()
    flow = tmp_path / f"{name}.yaml"
    flow.write_text(FLOW_ASK, encoding="utf-8")
    return ws, flow


def test_ask_user_yes_falls_back_to_default_with_trace(tmp_path):
    """--yes 非交互：PM ask_user → 缺省答案并留痕（transcript source=auto）。"""
    ws, flow = _write(tmp_path, "ws_yes")

    async def _run():
        driver = SessionDriver(
            workspace=ws,
            goal="做一个待办事项网站",
            flow=flow,
            model="codex",
            budget=100_000,
            deterministic=True,
            yes=True,
            role_tool_scripts=PM_ASK_SCRIPT,
            prompt_fn=lambda hint: "accept",
            print_fn=lambda s: None,
        )
        return await driver.run()

    result = asyncio.run(_run())
    assert result.exit_code == 0
    record = SessionStore(ws).record
    assert len(record.transcript) >= 1
    assert record.transcript[0].source == "auto"
    assert DEFAULT_ASK_DEFAULT in record.transcript[0].answer
    assert (ws / "docs" / "PRD.md").exists()


def test_ask_user_scripted_answer_recorded(tmp_path):
    """脚本化问答：qa_script 回答写入 transcript（source=script）且落到工具结果。"""
    ws, flow = _write(tmp_path, "ws_script")

    async def _run():
        driver = SessionDriver(
            workspace=ws,
            goal="做一个待办事项网站",
            flow=flow,
            model="codex",
            budget=100_000,
            deterministic=True,
            qa_script=["目标用户是上班族"],
            role_tool_scripts=PM_ASK_SCRIPT,
            prompt_fn=lambda hint: "accept",
            print_fn=lambda s: None,
        )
        return await driver.run()

    result = asyncio.run(_run())
    assert result.exit_code == 0
    record = SessionStore(ws).record
    assert len(record.transcript) >= 1
    assert record.transcript[0].source == "script"
    assert record.transcript[0].answer == "目标用户是上班族"


def test_ask_user_tool_permision_and_approval_cache(tmp_path):
    """工具层：ask_user 为 HUMAN_INTERACTION 权限；执行返回 needs_approval；缓存响应。"""
    registry = build_default_tools()
    spec = registry.get("ask_user")
    assert spec is not None
    assert spec.permission == ToolPermission.HUMAN_INTERACTION

    ws = tmp_path / "ws_tool"
    ws.mkdir()
    session = ToolSession(ws, registry=registry)
    call = ToolCall(name="ask_user", args={"question": "你叫什么？"})
    result = asyncio.run(session.execute(call))
    assert result.needs_approval is True
    # 缓存回答 → 幂等重跑不再重复询问
    session.remember_approval(call, "response:小明")
    cached = session.cached_approval(call)
    assert cached == "response:小明"
