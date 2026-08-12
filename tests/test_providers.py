"""模型供应商配置解析 + DeepSeekClient 测试（模型接入子任务）。

- ``load_codex_model_config``：显式路径解析 DeepSeek 供应商；缺失/损坏/缺键返回 None。
- ``resolve_deepseek_defaults``：DeepSeek 配置优先，否则回落官方默认值。
- ``DeepSeekClient``：缺 key 抛 RuntimeError；complete 经桩替换返回内容。
- 可选真实调用测试：仅当 ``DEEPSEEK_API_KEY`` 存在时执行（否则 skip）。
"""

from __future__ import annotations

import os

import pytest

from agent_cluster.providers import (
    CodexProviderConfig,
    load_codex_model_config,
    resolve_deepseek_defaults,
)
from agent_cluster.runtime import DeepSeekClient


def _write(path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


# ---------------------------------------------------------------------------
# load_codex_model_config
# ---------------------------------------------------------------------------


def test_load_codex_config_missing_file_returns_none(tmp_path):
    assert load_codex_model_config(tmp_path / "nope.toml") is None


def test_load_codex_config_parses_deepseek_provider(tmp_path):
    toml = tmp_path / "config.toml"
    _write(
        toml,
        'model = "deepseek-v4-flash"\n'
        'model_provider = "custom"\n'
        "\n"
        '[model_providers.custom]\n'
        'name = "DeepSeek"\n'
        'base_url = "https://api.deepseek.com"\n'
        'env_key = "DEEPSEEK_API_KEY"\n'
        'wire_api = "responses"\n',
    )
    config = load_codex_model_config(toml)
    assert config is not None
    assert config.model_name == "deepseek-v4-flash"
    assert config.provider == "custom"
    assert config.name == "DeepSeek"
    assert config.base_url == "https://api.deepseek.com"
    assert config.api_key_env == "DEEPSEEK_API_KEY"
    assert config.wire_api == "responses"
    assert config.is_deepseek


def test_load_codex_config_broken_toml_returns_none(tmp_path):
    toml = tmp_path / "config.toml"
    _write(toml, "this is not [ valid toml")
    assert load_codex_model_config(toml) is None


def test_load_codex_config_missing_provider_section_returns_none(tmp_path):
    toml = tmp_path / "config.toml"
    _write(toml, 'model = "deepseek-v4-flash"\nmodel_provider = "custom"\n')
    assert load_codex_model_config(toml) is None


def test_load_codex_config_missing_env_key_returns_none(tmp_path):
    toml = tmp_path / "config.toml"
    _write(
        toml,
        'model = "deepseek-v4-flash"\nmodel_provider = "custom"\n'
        '[model_providers.custom]\n'
        'base_url = "https://api.deepseek.com"\n',
    )
    assert load_codex_model_config(toml) is None


# ---------------------------------------------------------------------------
# resolve_deepseek_defaults
# ---------------------------------------------------------------------------


def test_resolve_deepseek_defaults_prefers_codex_config():
    config = CodexProviderConfig(
        name="DeepSeek",
        base_url="https://custom.example.com",
        api_key_env="MY_DEEPSEEK_KEY",
        model_name="deepseek-v4-flash",
    )
    base_url, env_key = resolve_deepseek_defaults(config)
    assert base_url == "https://custom.example.com"
    assert env_key == "MY_DEEPSEEK_KEY"


def test_resolve_deepseek_defaults_falls_back_to_official():
    base_url, env_key = resolve_deepseek_defaults(None)
    assert base_url == "https://api.deepseek.com"
    assert env_key == "DEEPSEEK_API_KEY"


# ---------------------------------------------------------------------------
# DeepSeekClient
# ---------------------------------------------------------------------------


def test_deepseek_client_requires_api_key(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="DEEPSEEK_API_KEY"):
        DeepSeekClient()


async def test_deepseek_client_complete_returns_content(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test-not-real")
    client = DeepSeekClient(api_base="https://api.deepseek.com")

    def fake_post(payload: dict) -> str:
        assert payload["model"] == "deepseek-v4-flash"
        assert payload["stream"] is False
        assert payload["messages"][-1]["content"] == "你好"
        return "模型输出内容"

    monkeypatch.setattr(client, "_post_chat_completion", fake_post)
    result = await client.complete([{"role": "user", "content": "你好"}])
    assert result == "模型输出内容"


async def test_deepseek_client_http_error_raises_runtime_error(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test-not-real")
    client = DeepSeekClient(api_base="https://api.deepseek.com")

    def boom(payload: dict) -> str:
        raise RuntimeError("DeepSeek API 请求失败（HTTP 401）：invalid key")

    monkeypatch.setattr(client, "_post_chat_completion", boom)
    with pytest.raises(RuntimeError, match="401"):
        await client.complete([{"role": "user", "content": "你好"}])


# ---------------------------------------------------------------------------
# 可选诚实测试：真实调用 DeepSeek 一次（有 key 才执行）
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not os.environ.get("DEEPSEEK_API_KEY"),
    reason="需要 DEEPSEEK_API_KEY 环境变量（真实调用）",
)
async def test_deepseek_live_single_call():
    """诚实测试：有 key 时真实调用 DeepSeek 一次，断言返回非空文本。"""
    client = DeepSeekClient(model="deepseek-v4-flash", max_tokens=256)
    content = await client.complete(
        [
            {"role": "system", "content": "你是测试助手，请用一句话回复。"},
            {"role": "user", "content": "回复：连接成功"},
        ]
    )
    assert isinstance(content, str) and content.strip(), "DeepSeek 应返回非空回复"
