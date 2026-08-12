"""T11.7 有界子代理测试：ReAct 循环完成、token 预算截断、轮数截断、
无审批权工具跳过、run_subagent 工具注册与 broker 委托、run_flow 接线。"""

from __future__ import annotations

import asyncio
from pathlib import Path

from agent_cluster.cli import run_flow
from agent_cluster.runtime import DeterministicClient
from agent_cluster.subagent import (
    BoundedSubagent,
    SubagentBroker,
    register_subagent_tool,
)
from agent_cluster.tools import ToolCall, ToolPermission, ToolSession, build_default_tools


def test_bounded_subagent_completes_task(tmp_path: Path):
    """子代理 ReAct 循环：工具脚本写文件 -> 最终文本，ok=True。"""
    ws = tmp_path / "ws"
    session = ToolSession(ws, registry=build_default_tools())
    client = DeterministicClient(
        tool_script=[
            {"name": "write_file", "args": {"path": "sub.txt", "content": "sub"}},
        ]
    )
    sub = BoundedSubagent(client=client, session=session, max_rounds=3, token_budget=100_000)
    result = asyncio.run(sub.run("在项目里创建 sub.txt"))
    assert result.ok is True
    assert result.truncated is False
    assert result.tool_calls == 1
    assert result.tokens_used > 0
    assert (ws / "sub.txt").read_text(encoding="utf-8") == "sub"


def test_bounded_subagent_budget_truncated(tmp_path: Path):
    """token 预算超限：提前结束且 truncated=True。"""
    ws = tmp_path / "ws"
    session = ToolSession(ws, registry=build_default_tools())
    client = DeterministicClient(
        tool_script=[
            {"name": "write_file", "args": {"path": "a.txt", "content": "a"}},
            {"name": "write_file", "args": {"path": "b.txt", "content": "b"}},
        ]
    )
    sub = BoundedSubagent(client=client, session=session, max_rounds=5, token_budget=1)
    result = asyncio.run(sub.run("写文件"))
    assert result.truncated is True
    assert result.ok is False
    assert "预算" in result.text


def test_bounded_subagent_max_rounds_truncated(tmp_path: Path):
    """轮数耗尽：truncated=True，rounds == max_rounds。"""
    ws = tmp_path / "ws"
    session = ToolSession(ws, registry=build_default_tools())
    client = DeterministicClient(
        tool_script=[
            {"name": "write_file", "args": {"path": f"f{i}.txt", "content": str(i)}} for i in range(5)
        ]
    )
    sub = BoundedSubagent(client=client, session=session, max_rounds=2, token_budget=100_000)
    result = asyncio.run(sub.run("连续写文件"))
    assert result.truncated is True
    assert result.rounds == 2
    assert result.tool_calls == 2


def test_bounded_subagent_skips_approval_required(tmp_path: Path):
    """子代理无审批权：危险工具被跳过但不中断循环。"""
    ws = tmp_path / "ws"
    session = ToolSession(ws, registry=build_default_tools())
    client = DeterministicClient(
        tool_script=[
            {"name": "run_python", "args": {"code": "print(1)"}},
        ]
    )
    sub = BoundedSubagent(client=client, session=session, max_rounds=3, token_budget=100_000)
    result = asyncio.run(sub.run("跑脚本"))
    assert result.ok is True  # 跳过后续完成
    assert result.tool_calls == 1


def test_bounded_subagent_model_failure(tmp_path: Path):
    """模型调用抛异常：ok=False 且携带错误信息。"""

    class Boom:
        model = "boom"

        async def complete_with_tools(self, messages, tools):
            raise RuntimeError("mock 模型故障")

    ws = tmp_path / "ws"
    session = ToolSession(ws, registry=build_default_tools())
    sub = BoundedSubagent(client=Boom(), session=session, max_rounds=3, token_budget=100_000)
    result = asyncio.run(sub.run("任意任务"))
    assert result.ok is False
    assert "模型调用失败" in result.text


def test_register_subagent_tool_and_broker(tmp_path: Path):
    """run_subagent 注册为危险工具；broker 委托子代理写文件。"""
    ws = tmp_path / "ws"
    session = ToolSession(ws, registry=build_default_tools())
    broker = SubagentBroker(
        client_factory=lambda role_id: DeterministicClient(
            tool_script=[{"name": "write_file", "args": {"path": "sub.txt", "content": "sub"}}]
        ),
        usage_hook=None,
    )
    register_subagent_tool(session.registry, broker)
    spec = session.registry.get("run_subagent")
    assert spec.permission == ToolPermission.DANGEROUS

    # 未批准：needs_approval
    result = asyncio.run(
        session.execute(ToolCall(id="1", name="run_subagent", args={"task": "实现模块"}))
    )
    assert result.needs_approval is True
    assert not (ws / "sub.txt").exists()

    # 批准后：子代理真实执行
    result = asyncio.run(
        session.execute(ToolCall(id="2", name="run_subagent", args={"task": "实现模块"}), approved=True)
    )
    assert result.ok is True
    assert (ws / "sub.txt").read_text(encoding="utf-8") == "sub"


def test_register_subagent_tool_idempotent(tmp_path: Path):
    """重复注册幂等。"""
    registry = build_default_tools()
    broker = SubagentBroker(client_factory=lambda role_id: DeterministicClient())
    register_subagent_tool(registry, broker)
    register_subagent_tool(registry, broker)
    assert registry.names().count("run_subagent") == 1


def test_run_flow_registers_subagent_tool(tmp_path: Path):
    """run_flow 工具模式：run_subagent 已注册（--yes 下自动拒绝、流程不崩）。"""
    flow = tmp_path / "f.yaml"
    flow.write_text(
        "name: t11.7\n"
        "thread_id: 't:sub'\n"
        "nodes:\n"
        "  - {id: start, type: start}\n"
        "  - {id: dev, type: agent, role: backend}\n"
        "  - {id: end, type: end}\n"
        "edges:\n"
        "  - {from: start, to: dev}\n"
        "  - {from: dev, to: end}\n",
        encoding="utf-8",
    )
    ws = tmp_path / "ws"
    summary = asyncio.run(
        run_flow(
            flow,
            workspace=str(ws),
            yes=True,
            role_tool_scripts={
                "backend": [{"name": "run_subagent", "args": {"task": "实现一个工具函数"}}]
            },
        )
    )
    assert summary.state is not None
    # 消息通道出现 run_subagent 工具调用记录（注册成功 + 被 --yes 拒绝但流程继续）
    tool_names = {
        msg.payload.get("tool")
        for msg in (summary.state.messages or [])
        if msg.payload.get("tool") == "run_subagent"
    }
    assert "run_subagent" in tool_names
    tasks = summary.state.tasks or []
    assert tasks and all(task.status.value in ("done", "review") for task in tasks)
