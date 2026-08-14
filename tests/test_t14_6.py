"""Task 14.6 LLM 上下文缓存优化测试（usage 回填 / 命中率 / 头锚定 / 请求头 / 指标）。"""

from __future__ import annotations

import os

import pytest

from agent_cluster.cache import cache_hit_ratio, cache_summary, extract_cache_tokens
from agent_cluster.context import head_anchored_trim
from agent_cluster.models import TokenUsage
from agent_cluster.runtime import AgentRuntime, DeepSeekClient


# --- usage 提取（OpenAI/DeepSeek 风格 + Anthropic 风格） ---


def test_extract_openai_style_cache_tokens() -> None:
    usage = {"prompt_tokens": 100, "prompt_cache_hit_tokens": 80, "prompt_cache_miss_tokens": 20}
    assert extract_cache_tokens(usage) == (80, 20)


def test_extract_openai_style_object() -> None:
    class Usage:
        prompt_cache_hit_tokens = 90
        prompt_cache_miss_tokens = 10

    assert extract_cache_tokens(Usage()) == (90, 10)


def test_extract_anthropic_style_cache_tokens() -> None:
    usage = {"input_tokens": 100, "cache_read_input_tokens": 70, "cache_creation_input_tokens": 30}
    assert extract_cache_tokens(usage) == (70, 30)


def test_extract_missing_cache_tokens_is_zero() -> None:
    assert extract_cache_tokens({"prompt_tokens": 5}) == (0, 0)
    assert extract_cache_tokens(None) == (0, 0)


# --- 命中率 ---


def test_cache_hit_ratio_math() -> None:
    assert cache_hit_ratio(98, 2) == pytest.approx(0.98)
    assert cache_hit_ratio(0, 0) is None


def test_cache_summary_shape() -> None:
    usage = TokenUsage(
        prompt_tokens=100,
        completion_tokens=10,
        total_tokens=110,
        model="deepseek-v4-flash",
        cache_read_tokens=80,
        cache_miss_tokens=20,
    )
    summary = cache_summary(usage)
    assert summary["read_tokens"] == 80
    assert summary["miss_tokens"] == 20
    assert summary["hit_ratio"] == pytest.approx(0.8)
    assert summary["model"] == "deepseek-v4-flash"


# --- 头锚定上下文管理（不拆 tool 对） ---


def _messages(n: int, with_tools: bool = False) -> list[dict]:
    msgs: list[dict] = []
    for i in range(n):
        if with_tools and i % 2 == 1:
            msgs.append({"role": "tool", "tool_call_id": f"c{i}", "content": f"r{i}"})
        else:
            msgs.append({"role": "user" if i % 2 == 0 else "assistant", "content": f"m{i}"})
    return msgs


def test_head_anchored_noop_within_limit() -> None:
    msgs = _messages(5)
    assert head_anchored_trim(msgs, max_items=10) == msgs


def test_head_anchored_keeps_head_and_tail() -> None:
    msgs = _messages(20)
    trimmed = head_anchored_trim(msgs, max_items=10)
    assert len(trimmed) == 10
    assert trimmed[0] == msgs[0]  # 保头
    assert trimmed[-1] == msgs[-1]  # 保尾
    assert trimmed[1]["content"] == msgs[1]["content"]


def test_head_anchored_never_splits_tool_pair() -> None:
    msgs = _messages(20, with_tools=True)
    trimmed = head_anchored_trim(msgs, max_items=10)
    for i, msg in enumerate(trimmed):
        if msg["role"] == "tool":
            # tool 的 assistant 必须在头部保留区内（裁剪边界不落在 tool 序列中间）
            assert i > 0 and trimmed[i - 1]["role"] != "tool"


# --- DeepSeek 专属请求头（无匿名 user-id） ---


def test_deepseek_headers_include_session_id(monkeypatch) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-x")
    client = DeepSeekClient(session_id="sess-1")
    headers = client._request_headers()
    assert headers["x-deepseek-harness-session-id"] == "sess-1"
    assert "x-deepseek-harness-user-id" not in headers  # 隐私：不搬运匿名头


def test_deepseek_headers_compact_flag(monkeypatch) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-x")
    assert DeepSeekClient()._request_headers().get("x-deepseek-harness-compact") is None
    assert DeepSeekClient(compact=True)._request_headers()["x-deepseek-harness-compact"] == "1"


def test_deepseek_headers_base(monkeypatch) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-x")
    headers = DeepSeekClient()._request_headers()
    assert headers["Authorization"] == "Bearer sk-x"
    assert headers["Content-Type"] == "application/json"


# --- runtime：observe 头锚定 + llm/cache 事件 ---


async def test_observe_uses_head_anchored_trim() -> None:
    from agent_cluster.models import AgentState
    from agent_cluster.runtime import AgentRuntime, AgentConfig, Agent

    runtime = AgentRuntime()
    agent = Agent(
        id="a1",
        role_id="backend",
        name="t",
        system_prompt="sys",
        config=AgentConfig(context={"max_messages": 6}),
        state=AgentState(),
    )
    from agent_cluster.models import Message, MessageType

    msgs = [Message(id=f"m{i}", thread_id="t", source="u", target="", type=MessageType.TEXT, payload={"content": f"c{i}"}) for i in range(12)]
    await runtime.observe(agent, msgs)
    contents = [m.payload["content"] for m in agent.state.messages]
    assert len(contents) == 6
    assert contents[0] == "c0"  # 保头
    assert contents[-1] == "c11"  # 保尾


async def test_runtime_emits_llm_cache_event() -> None:
    from agent_cluster.runtime import ChatModelClient
    from agent_cluster.models import Message, MessageType

    class CachedClient(ChatModelClient):
        async def complete(self, messages):
            self.last_usage = TokenUsage(
                prompt_tokens=10, completion_tokens=1, total_tokens=11,
                model="deepseek-v4-flash", cache_read_tokens=8, cache_miss_tokens=2,
            )
            return "ok"

    from agent_cluster.models import AgentState
    from agent_cluster.runtime import ChatModelFactory, Agent, AgentConfig

    factory = ChatModelFactory()
    factory.create = lambda config=None: CachedClient()  # type: ignore[assignment]
    runtime = AgentRuntime(model_factory=factory)
    agent = Agent(
        id="a1", role_id="backend", name="t", system_prompt="sys",
        config=AgentConfig(), state=AgentState(),
    )
    await runtime.reply(agent, [Message(id="m1", thread_id="t", source="u", target="", type=MessageType.TEXT, payload={"content": "hi"})])
    types = [e.type for e in runtime.event_bus._events]
    assert "llm/cache" in types
