"""T11.2 模型三协议：OpenAIResponsesClient / AnthropicClient 与 wire_api 路由。

- Responses：/v1/responses 解析 text/function_call、跳过 reasoning、usage 解析、
  空回复扩容重试、缺 key 报错。
- Anthropic：/v1/messages 协议转换（system 提取、tool_result 配对、tools schema）、
  text/tool_use 解析、缺 key 报错。
- 工厂：wire_api=responses/anthropic 路由 + Codex 配置 wire_api 路由。
"""

from __future__ import annotations

import json

import pytest

from agent_cluster.models import AgentConfig, ModelConfig
from agent_cluster.providers import CodexProviderConfig
from agent_cluster.runtime import (
    AnthropicClient,
    ChatModelFactory,
    OpenAIResponsesClient,
)


class _FakeUrlopenResponse:
    """伪造 urllib.request.urlopen 返回值。"""

    def __init__(self, body: bytes) -> None:
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> bool:
        return False

    def read(self) -> bytes:
        return self._body


def _responses_client(monkeypatch, *, max_tokens: int = 2048) -> OpenAIResponsesClient:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-real")
    return OpenAIResponsesClient(model="gpt-4o-mini", max_tokens=max_tokens)


def _anthropic_client(monkeypatch, *, max_tokens: int = 4096) -> AnthropicClient:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-not-real")
    return AnthropicClient(model="claude-sonnet-4-5", max_tokens=max_tokens)


# ---------------------------------------------------------------------------
# OpenAIResponsesClient
# ---------------------------------------------------------------------------


def test_responses_client_requires_api_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
        OpenAIResponsesClient()


async def test_responses_client_complete_returns_text(monkeypatch):
    client = _responses_client(monkeypatch)
    body = json.dumps(
        {
            "output": [{"type": "message", "content": [{"type": "output_text", "text": "你好"}]}],
            "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
        }
    ).encode("utf-8")
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: _FakeUrlopenResponse(body))
    result = await client.complete([{"role": "user", "content": "你好"}])
    assert result == "你好"


async def test_responses_client_parses_function_call_and_skips_reasoning(monkeypatch):
    client = _responses_client(monkeypatch)
    body = json.dumps(
        {
            "output": [
                {"type": "reasoning", "summary": [{"type": "summary_text", "text": "内部思考"}]},
                {
                    "type": "function_call",
                    "id": "fc-1",
                    "name": "read_file",
                    "arguments": "{\"path\": \"src/main.py\"}",
                },
            ],
            "usage": {"input_tokens": 8, "output_tokens": 6, "total_tokens": 14},
        }
    ).encode("utf-8")
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: _FakeUrlopenResponse(body))
    response = await client.complete_with_tools(
        [{"role": "user", "content": "读文件"}],
        [{"type": "function", "function": {"name": "read_file", "description": "", "parameters": {}}}],
    )
    assert response.text == ""
    assert len(response.tool_calls) == 1
    assert response.tool_calls[0].name == "read_file"
    assert response.tool_calls[0].args == {"path": "src/main.py"}
    assert response.usage is not None and response.usage.prompt_tokens == 8


async def test_responses_client_retries_on_empty_with_expansion(monkeypatch):
    import urllib.request

    client = _responses_client(monkeypatch, max_tokens=2048)
    calls: list[bytes] = []

    def fake_urlopen(request, timeout=180):  # noqa: ANN001, ANN003
        calls.append(request.data)
        if len(calls) == 1:
            return _FakeUrlopenResponse(
                json.dumps({"output": [], "incomplete_details": {"reason": "max_output_tokens"}}).encode("utf-8")
            )
        return _FakeUrlopenResponse(
            json.dumps({"output": [{"type": "message", "content": [{"type": "output_text", "text": "完成"}]}]}).encode(
                "utf-8"
            )
        )

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    result = await client.complete([{"role": "user", "content": "hi"}])
    assert result == "完成"
    assert len(calls) == 2
    first = json.loads(calls[0].decode("utf-8"))
    second = json.loads(calls[1].decode("utf-8"))
    assert first["max_output_tokens"] == 2048
    assert second["max_output_tokens"] == 8192


async def test_responses_client_non_json_raises(monkeypatch):
    client = _responses_client(monkeypatch)
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: _FakeUrlopenResponse(b"<html>error</html>"))
    with pytest.raises(RuntimeError, match="合法 JSON"):
        await client.complete([{"role": "user", "content": "你好"}])


# ---------------------------------------------------------------------------
# AnthropicClient
# ---------------------------------------------------------------------------


def test_anthropic_client_requires_api_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        AnthropicClient()


def test_anthropic_convert_tools_maps_schema():
    client = AnthropicClient.__new__(AnthropicClient)
    converted = client._convert_tools(
        [
            {"type": "function", "function": {"name": "read_file", "description": "读文件", "parameters": {"type": "object"}}}
        ]
    )
    assert converted == [
        {"name": "read_file", "description": "读文件", "input_schema": {"type": "object"}}
    ]


def test_anthropic_convert_history_system_and_tool_pairing(monkeypatch):
    client = _anthropic_client(monkeypatch)
    client._pending_tool_use_ids = ["toolu-1"]
    system, messages = client._convert_history(
        [
            {"role": "system", "content": "你是助手"},
            {"role": "user", "content": "请读文件"},
            {"role": "user", "content": "[工具结果 read_file ok=True] 内容"},
        ]
    )
    assert system == "你是助手"
    assert messages == [
        {"role": "user", "content": "请读文件"},
        {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "toolu-1", "content": "[工具结果 read_file ok=True] 内容"}],
        },
    ]
    assert client._pending_tool_use_ids == []


def test_anthropic_convert_history_tool_role_block(monkeypatch):
    client = _anthropic_client(monkeypatch)
    system, messages = client._convert_history(
        [
            {"role": "system", "content": "s"},
            {"role": "tool", "tool_call_id": "t-9", "content": "结果"},
        ]
    )
    assert messages == [
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t-9", "content": "结果"}]}
    ]


async def test_anthropic_client_parses_text_and_tool_use(monkeypatch):
    client = _anthropic_client(monkeypatch)
    body = json.dumps(
        {
            "content": [
                {"type": "text", "text": "开始"},
                {"type": "tool_use", "id": "toolu-42", "name": "write_file", "input": {"path": "a.txt", "content": "x"}},
            ],
            "usage": {"input_tokens": 20, "output_tokens": 12},
        }
    ).encode("utf-8")
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: _FakeUrlopenResponse(body))
    response = await client.complete_with_tools(
        [{"role": "user", "content": "写文件"}],
        [{"type": "function", "function": {"name": "write_file", "description": "", "parameters": {}}}],
    )
    assert response.text == "开始"
    assert len(response.tool_calls) == 1
    assert response.tool_calls[0].id == "toolu-42"
    assert response.tool_calls[0].name == "write_file"
    assert response.tool_calls[0].args == {"path": "a.txt", "content": "x"}
    assert client._pending_tool_use_ids == ["toolu-42"]


async def test_anthropic_client_empty_content_raises(monkeypatch):
    client = _anthropic_client(monkeypatch)
    body = json.dumps({"content": [], "usage": {"input_tokens": 1, "output_tokens": 1}}).encode("utf-8")
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: _FakeUrlopenResponse(body))
    with pytest.raises(RuntimeError, match="均为空"):
        await client.complete_with_tools([{"role": "user", "content": "hi"}], [])


# ---------------------------------------------------------------------------
# 工厂 wire_api 路由
# ---------------------------------------------------------------------------


def test_factory_routes_responses_wire_api(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    cfg = AgentConfig(model=ModelConfig(model_name="gpt-4o-mini", wire_api="responses"))
    client = ChatModelFactory().create(cfg)
    assert isinstance(client, OpenAIResponsesClient)


def test_factory_routes_anthropic_wire_api(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    cfg = AgentConfig(model=ModelConfig(model_name="claude-sonnet-4-5", wire_api="anthropic"))
    client = ChatModelFactory().create(cfg)
    assert isinstance(client, AnthropicClient)


def test_factory_chat_default_uses_existing_openai_client(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    cfg = AgentConfig(model=ModelConfig(model_name="gpt-4o-mini", wire_api="chat"))
    from agent_cluster.runtime import OpenAIClient

    assert isinstance(ChatModelFactory().create(cfg), OpenAIClient)


def test_factory_codex_config_wire_api_responses(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    codex = CodexProviderConfig(
        name="OpenAI",
        base_url="https://api.openai.com/v1",
        api_key_env="OPENAI_API_KEY",
        wire_api="responses",
        model_name="gpt-4o-mini",
    )
    monkeypatch.setattr("agent_cluster.runtime.load_codex_model_config", lambda: codex)
    cfg = AgentConfig(model=ModelConfig(model_name="codex"))
    client = ChatModelFactory().create(cfg)
    assert isinstance(client, OpenAIResponsesClient)
    assert client.model == "gpt-4o-mini"


def test_factory_codex_config_wire_api_anthropic(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    codex = CodexProviderConfig(
        name="Anthropic",
        base_url="https://api.anthropic.com",
        api_key_env="ANTHROPIC_API_KEY",
        wire_api="anthropic",
        model_name="claude-sonnet-4-5",
    )
    monkeypatch.setattr("agent_cluster.runtime.load_codex_model_config", lambda: codex)
    cfg = AgentConfig(model=ModelConfig(model_name="codex"))
    client = ChatModelFactory().create(cfg)
    assert isinstance(client, AnthropicClient)
