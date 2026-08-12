"""Task 5 行为测试：模型客户端、ChatModelFactory、EventBus 与 AgentRuntime / agent handler。"""

from __future__ import annotations

import pytest

from agent_cluster.models import (
    Agent,
    AgentConfig,
    ClusterState,
    Iteration,
    Message,
    MessageType,
    ModelConfig,
    Project,
    Task,
    TaskStatus,
)
from agent_cluster.roles import RoleRegistry
from agent_cluster.runtime import (
    AgentRuntime,
    ChatModelFactory,
    DeterministicClient,
    EventBus,
    OpenAIClient,
    make_agent_handler,
)
from agent_cluster.workflow import NodeContext, WorkflowEdge, WorkflowNode, WorkflowSpec


# ---------------------------------------------------------------------------
# DeterministicClient
# ---------------------------------------------------------------------------


async def test_deterministic_client_returns_deterministic_output():
    client = DeterministicClient(persona="测试工程师")
    messages = [
        {"role": "system", "content": "你是测试工程师"},
        {"role": "user", "content": "请执行任务 A"},
    ]
    first = await client.complete(messages)
    second = await client.complete(messages)
    assert first == second  # 同一输入恒得同一输出
    assert "测试工程师" in first
    assert "任务 A" in first


async def test_deterministic_client_handles_empty_messages():
    client = DeterministicClient()
    reply = await client.complete([])
    assert "就绪" in reply


def test_openai_client_requires_api_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
        OpenAIClient()


def test_factory_defaults_to_deterministic():
    assert isinstance(ChatModelFactory().create(), DeterministicClient)
    assert isinstance(
        ChatModelFactory().create(AgentConfig(model=ModelConfig(model_name="deterministic"))),
        DeterministicClient,
    )


def test_factory_rejects_unknown_model():
    with pytest.raises(ValueError, match="未知模型名称"):
        ChatModelFactory().create(AgentConfig(model=ModelConfig(model_name="llama-3")))


# ---------------------------------------------------------------------------
# EventBus
# ---------------------------------------------------------------------------


def test_event_bus_publish_and_query():
    bus = EventBus()
    event_one = _event(type="agent_step", thread_id="t1")
    event_two = _event(type="meeting_held", thread_id="t2")
    event_three = _event(type="agent_step", thread_id="t2")
    for event in (event_one, event_two, event_three):
        bus.publish(event)
    assert len(bus.events) == 3
    assert len(bus.query(type="agent_step")) == 2
    assert len(bus.query(thread_id="t2")) == 2
    assert len(bus.query(thread_id="t1", type="agent_step")) == 1
    assert len(bus.query(thread_id="t1", type="meeting_held")) == 0
    assert len(bus.query()) == 3


def _event(type: str, thread_id: str):
    from agent_cluster.models import Event

    return Event(id=f"e-{type}-{thread_id}", run_id="run1", thread_id=thread_id, type=type)


# ---------------------------------------------------------------------------
# AgentRuntime.reply / observe
# ---------------------------------------------------------------------------


def _make_agent() -> Agent:
    return Agent(
        id="agent-architect",
        role_id="architect",
        name="架构师",
        system_prompt="你是架构师，负责系统设计。",
    )


def _make_text_message(thread_id: str, content: str) -> Message:
    return Message(
        id="m1",
        thread_id=thread_id,
        source="pmo",
        target="agent-architect",
        type=MessageType.TEXT,
        payload={"content": content},
    )


async def test_reply_produces_text_message_from_agent():
    runtime = AgentRuntime()
    agent = _make_agent()
    reply = await runtime.reply(agent, [_make_text_message("proj:demo:iter:1", "请输出系统设计")])
    assert reply.source == agent.id
    assert reply.type == MessageType.TEXT
    assert reply.target == ""
    assert "请输出系统设计" in reply.payload["content"]
    # reply 事件已发布到总线
    assert len(runtime.event_bus.query(type="agent_reply")) == 1


async def test_observe_updates_agent_state():
    runtime = AgentRuntime()
    agent = _make_agent()
    observed = [_make_text_message("proj:demo:iter:1", "观察内容 A")]
    await runtime.observe(agent, observed)
    assert agent.state.messages == observed
    await runtime.observe(agent, [_make_text_message("proj:demo:iter:1", "观察内容 B")])
    assert [message.payload["content"] for message in agent.state.messages] == ["观察内容 A", "观察内容 B"]


async def test_complete_for_returns_deterministic_completion_with_task():
    runtime = AgentRuntime()
    role = RoleRegistry().get("architect")
    task = Task(
        id="t1",
        project_id="proj1",
        iteration_id="iter1",
        title="系统设计",
        desc="设计",
        assignee_role="architect",
    )
    content = await runtime.complete_for(role, task)
    # 确定性后端回显最后一条用户消息（含任务上下文）
    assert "执行任务 t1" in content
    assert "系统设计" in content


async def test_complete_for_works_without_task():
    runtime = AgentRuntime()
    role = RoleRegistry().get("pm")
    content = await runtime.complete_for(role)
    # 无任务时按角色画像生成提示，回显中包含角色名
    assert "产品经理" in content


# ---------------------------------------------------------------------------
# make_agent_handler（agent 节点 handler 契约）
# ---------------------------------------------------------------------------


def _make_context(node: WorkflowNode) -> NodeContext:
    spec = WorkflowSpec(
        name="t5-agent",
        max_iterations=4,
        thread_id="proj:demo:iter:1",
        nodes=[
            WorkflowNode(id="start", type="start"),
            node,
            WorkflowNode(id="end", type="end"),
        ],
        edges=[
            WorkflowEdge(from_="start", to=node.id),
            WorkflowEdge(from_=node.id, to="end"),
        ],
    )
    return NodeContext(node_id=node.id, spec=spec, events=[], run_id="run-t5", loop_count=1)


async def test_agent_handler_updates_tasks_messages_and_ledger():
    runtime = AgentRuntime()
    registry = RoleRegistry()
    handler = make_agent_handler(runtime, registry)
    state = ClusterState(
        project=Project(id="proj1", name="演示项目"),
        iterations=[Iteration(id="iter1", project_id="proj1", number=1)],
    )
    node = WorkflowNode(id="design", type="agent", role="architect")
    ctx = _make_context(node)

    updates = await handler(state, node, ctx)

    # 通道键契约：tasks / messages / ledger；事件走 ctx.events
    assert set(updates) == {"tasks", "messages", "ledger"}
    tasks = updates["tasks"]
    assert len(tasks) == 1
    task = tasks[0]
    assert task.assignee_role == "architect"
    assert task.status == TaskStatus.DONE  # 确定性后端创建即完成
    assert task.project_id == "proj1"
    assert task.iteration_id == "iter1"

    messages = updates["messages"]
    assert len(messages) == 1
    assert messages[0].source == "architect"
    assert messages[0].type == MessageType.TEXT
    assert messages[0].payload["task"] == task.id

    ledger = updates["ledger"]
    assert ledger.task_id == task.id
    assert ledger.progress[-1].role == "architect"
    assert ledger.progress[-1].status == "doing"

    # 事件追加到 ctx.events（不占通道键）
    assert len(ctx.events) == 1
    event = ctx.events[0]
    assert event.type == "agent_step"
    assert event.actor == "architect"
    assert event.payload["task"] == task.id


class _PoisonFactory:
    """一旦被访问即失败的工厂：证明 handler 不触碰运行时私有 _model_factory。"""

    def create(self, *args, **kwargs):  # noqa: ANN002, ANN003
        raise AssertionError("handler 不得直接访问 _model_factory")


class _PublicApiRuntime(AgentRuntime):
    """记录 complete_for 调用的运行时（私有工厂被毒化，handler 只能走公开 API）。"""

    def __init__(self) -> None:
        super().__init__(model_factory=_PoisonFactory())  # type: ignore[arg-type]
        self.completed: list[tuple[str, str | None]] = []

    async def complete_for(self, role, task=None) -> str:  # noqa: ANN001
        self.completed.append((role.id, task.id if task is not None else None))
        return "确定性完成摘要"


async def test_agent_handler_uses_public_complete_for_method():
    runtime = _PublicApiRuntime()
    registry = RoleRegistry()
    handler = make_agent_handler(runtime, registry)
    state = ClusterState(project=Project(id="proj1", name="演示项目"))
    node = WorkflowNode(id="design", type="agent", role="architect")
    ctx = _make_context(node)

    updates = await handler(state, node, ctx)

    # handler 只经公开方法获取模型完成（毒化工厂未触发）
    assert len(runtime.completed) == 1
    role_id, task_id = runtime.completed[0]
    assert role_id == "architect"
    assert task_id == updates["tasks"][0].id
    assert updates["messages"][0].payload["content"].endswith("确定性完成摘要")


async def test_agent_handler_creates_fresh_task_per_invocation():
    """每次调用新建任务（tasks 通道为 operator.add 追加，复用会重复——契约）。"""
    runtime = AgentRuntime()
    registry = RoleRegistry()
    handler = make_agent_handler(runtime, registry)
    state = ClusterState(project=Project(id="proj1", name="演示项目"))
    node = WorkflowNode(id="design", type="agent", role="architect")

    first = await handler(state, node, _make_context(node))
    second = await handler(state, node, _make_context(node))
    assert first["tasks"][0].id != second["tasks"][0].id
    assert first["tasks"][0].status == TaskStatus.DONE
    assert second["tasks"][0].status == TaskStatus.DONE
    # 通道内既有任务不受影响，返回的任务为新增实例
    assert state.tasks == []
