"""T10.1 Token 计量基础：估算器口径 / usage 解析 / TokenUsage 累加与预算判定。"""

from __future__ import annotations

import pytest

from agent_cluster.models import TokenUsage
from agent_cluster.runtime import (
    ChatResponse,
    DeepSeekClient,
    DeterministicClient,
    _extract_usage,
    estimate_tokens,
    estimate_usage,
)
from agent_cluster.tools import ToolCall


@pytest.fixture(autouse=True)
def _force_heuristic_estimator(monkeypatch):
    """强制使用内置启发式估算（不依赖可选的 tiktoken 精确后端）。"""
    import agent_cluster.runtime as runtime_mod

    monkeypatch.setattr(runtime_mod, "_TIKTOKEN_ENCODING", False)
    yield


# ---------------------------------------------------------------------------
# estimate_tokens 口径
# ---------------------------------------------------------------------------


def test_estimate_tokens_empty_is_zero():
    assert estimate_tokens("") == 0


def test_estimate_tokens_ascii_min_one():
    assert estimate_tokens("a") == 1
    assert estimate_tokens("abcd") == 1
    assert estimate_tokens("abcdefgh") == 2


def test_estimate_tokens_cjk_weighted():
    # 4 个汉字 → int(4*1.6)=6（中文按比例加权，避免按字节/4 严重低估）
    assert estimate_tokens("中文测试") == 6


def test_estimate_tokens_mixed():
    # "hello 世界"：ASCII 6 字符 → (6+3)//4=2，CJK 2 字符 → int(3.2)=3，合计 5
    assert estimate_tokens("hello 世界") == 5


# ---------------------------------------------------------------------------
# estimate_usage 口径
# ---------------------------------------------------------------------------


def test_estimate_usage_counts_prompt_and_completion():
    messages = [{"role": "user", "content": "你好世界"}]
    usage = estimate_usage(messages, "最终答案", None, model="deterministic")
    assert usage.estimated is True
    assert usage.model == "deterministic"
    # prompt = 内容估算(6) + 每消息 4 token 开销
    assert usage.prompt_tokens == 6 + 4
    # completion = 4 汉字 * 1.6 = int(6.4) = 6
    assert usage.completion_tokens == 6
    assert usage.total_tokens == usage.prompt_tokens + usage.completion_tokens


def test_estimate_usage_counts_tool_calls():
    messages = [{"role": "user", "content": "写文件"}]
    calls = [ToolCall(name="write_file", args={"path": "a.txt", "content": "内容"})]
    usage = estimate_usage(messages, "", calls, model="deterministic")
    # 工具调用参数计入 completion：name 4 字符/4=1+4，args 中文 2 字*1.6=4 + ASCII 约 8/4=2
    assert usage.completion_tokens > 0
    assert usage.total_tokens > usage.prompt_tokens


# ---------------------------------------------------------------------------
# TokenUsage 累加与预算判定
# ---------------------------------------------------------------------------


def test_token_usage_accumulation_and_budget():
    a = TokenUsage(prompt_tokens=10, completion_tokens=20, total_tokens=30, model="deepseek-v4-flash")
    b = TokenUsage(prompt_tokens=5, completion_tokens=5, total_tokens=10, model="deepseek-v4-flash")
    total = a.total_tokens + b.total_tokens
    assert total == 40
    assert total <= 50  # 预算 50K 内
    assert total > 35  # 超预算 35 触发升级


def test_token_usage_defaults_zero():
    usage = TokenUsage(model="m")
    assert usage.total_tokens == 0
    assert usage.estimated is False


# ---------------------------------------------------------------------------
# 客户端 usage 记录
# ---------------------------------------------------------------------------


async def test_deterministic_client_complete_records_last_usage():
    client = DeterministicClient()
    await client.complete([{"role": "user", "content": "你好"}])
    assert client.last_usage is not None
    assert client.last_usage.estimated is True
    assert client.last_usage.total_tokens > 0


async def test_deterministic_client_tool_script_records_usage_in_response():
    client = DeterministicClient(tool_script=[{"name": "write_file", "args": {"path": "a.txt"}}])
    response = await client.complete_with_tools(
        [{"role": "user", "content": "写文件"}], [{"type": "function", "function": {"name": "write_file"}}]
    )
    assert response.tool_calls
    assert response.usage is not None
    assert response.usage.estimated is True
    assert client.last_usage is not None
    assert client.last_usage.total_tokens == response.usage.total_tokens


# ---------------------------------------------------------------------------
# usage 提取（API 响应 fixture）
# ---------------------------------------------------------------------------


class _FakeOpenAIResponse:
    """伪造 OpenAI 风格响应（含 usage 与无 usage 两种形态）。"""

    def __init__(self, usage: dict | None) -> None:
        self.usage = type("Usage", (), {k: v for k, v in (usage or {}).items()})() if usage else None


def test_extract_usage_parses_api_usage():
    response = _FakeOpenAIResponse({"prompt_tokens": 12, "completion_tokens": 34, "total_tokens": 46})
    usage = _extract_usage(response, "gpt-4o-mini", [], "", None)
    assert usage.estimated is False
    assert usage.prompt_tokens == 12
    assert usage.completion_tokens == 34
    assert usage.total_tokens == 46
    assert usage.model == "gpt-4o-mini"


def test_extract_usage_falls_back_to_estimate():
    response = _FakeOpenAIResponse(None)
    usage = _extract_usage(response, "gpt-4o-mini", [{"role": "user", "content": "你好"}], "答案", None)
    assert usage.estimated is True
    assert usage.total_tokens > 0


async def test_deepseek_client_parses_usage_from_api(monkeypatch):
    """DeepSeek 响应带 usage 时，ChatResponse.usage 为真实值（estimated=False）。"""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test-not-real")

    class _FakeUrlopenResponse:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return (
                '{"choices": [{"message": {"content": "答案", "tool_calls": []}, '
                '"finish_reason": "stop"}], "usage": {"prompt_tokens": 10, '
                '"completion_tokens": 20, "total_tokens": 30}}'
            ).encode("utf-8")

    client = DeepSeekClient(api_base="https://api.deepseek.com", api_key_env="DEEPSEEK_API_KEY")
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: _FakeUrlopenResponse())
    response = await client.complete_with_tools(
        [{"role": "user", "content": "你好"}], [{"type": "function", "function": {"name": "x"}}]
    )
    assert response.usage is not None
    assert response.usage.estimated is False
    assert response.usage.total_tokens == 30
    assert client.last_usage is not None
    assert client.last_usage.total_tokens == 30


async def test_deepseek_client_plain_complete_records_usage(monkeypatch):
    """无工具路径（complete → _post_chat_completion）同样记录 last_usage。"""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test-not-real")

    class _FakeUrlopenResponse:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return (
                '{"choices": [{"message": {"content": "答案"}}], '
                '"usage": {"prompt_tokens": 7, "completion_tokens": 9, "total_tokens": 16}}'
            ).encode("utf-8")

    client = DeepSeekClient(api_base="https://api.deepseek.com", api_key_env="DEEPSEEK_API_KEY")
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: _FakeUrlopenResponse())
    text = await client.complete([{"role": "user", "content": "你好"}])
    assert text == "答案"
    assert client.last_usage is not None
    assert client.last_usage.total_tokens == 16
    assert client.last_usage.estimated is False


# ---------------------------------------------------------------------------
# ChatResponse.usage 透传
# ---------------------------------------------------------------------------


def test_chat_response_usage_defaults_none():
    assert ChatResponse(text="x").usage is None
