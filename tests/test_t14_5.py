"""Task 14.5 能力入接缝：credentials 契约 / guard 契约 / llm seam 接线测试。"""

from __future__ import annotations

import asyncio

import pytest

from agent_cluster.credentials import CredentialMissingError, CredentialResolver
from agent_cluster.guard import ToolGuard, ToolTimeoutError
from agent_cluster.runtime import AgentRuntime
from agent_cluster.seam import DuplicateProviderError


# --- credentials 契约（dsh：只存环境变量名引用、空值=不存在、env+file 分层） ---


def test_credentials_resolve_from_env(monkeypatch) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    resolver = CredentialResolver()
    assert resolver.resolve("DEEPSEEK_API_KEY") == "sk-test"


def test_credentials_unset_is_missing(monkeypatch) -> None:
    monkeypatch.delenv("MISSING_KEY", raising=False)
    resolver = CredentialResolver()
    with pytest.raises(CredentialMissingError):
        resolver.resolve("MISSING_KEY")


def test_credentials_empty_is_missing(monkeypatch) -> None:
    monkeypatch.setenv("EMPTY_KEY", "")
    resolver = CredentialResolver()
    with pytest.raises(CredentialMissingError):
        resolver.resolve("EMPTY_KEY")


def test_credentials_file_layering(tmp_path, monkeypatch) -> None:
    secret_file = tmp_path / "secrets.env"
    secret_file.write_text("TOKEN=file-value\n", encoding="utf-8")
    monkeypatch.setenv("TOKEN", "env-value")
    resolver = CredentialResolver(file_path=str(secret_file))
    # 裸引用：file 优先，env 兜底
    assert resolver.resolve("TOKEN") == "file-value"
    # 显式前缀
    assert resolver.resolve("env:TOKEN") == "env-value"
    assert resolver.resolve("file:TOKEN") == "file-value"
    monkeypatch.delenv("FILE_ONLY", raising=False)
    with pytest.raises(CredentialMissingError):
        resolver.resolve("file:FILE_ONLY")  # file 缺键=缺失


# --- guard 契约（dsh：合作式超时 TOOL_TIMEOUT + 重复调用提醒，只增强不授权） ---


def test_guard_timeout_raises_structured_error() -> None:
    guard = ToolGuard(default_timeout_s=0.05)

    async def slow() -> None:
        await asyncio.sleep(1)

    with pytest.raises(ToolTimeoutError) as exc_info:
        asyncio.run(guard.run_with_timeout("read", slow()))
    assert exc_info.value.code == "TOOL_TIMEOUT"
    assert exc_info.value.tool == "read"


def test_guard_completes_within_timeout() -> None:
    guard = ToolGuard(default_timeout_s=1)

    async def fast() -> str:
        return "ok"

    assert asyncio.run(guard.run_with_timeout("read", fast())) == "ok"


def test_guard_repeat_reminder() -> None:
    guard = ToolGuard(reminder_after=3)
    assert guard.check_repeat("apply_patch", "sig-1") is None
    assert guard.check_repeat("apply_patch", "sig-1") is None
    reminder = guard.check_repeat("apply_patch", "sig-1")
    assert reminder is not None and "apply_patch" in reminder
    # 换工具重置计数
    assert guard.check_repeat("read", "sig-2") is None


# --- llm 接缝接线（AgentRuntime 持有 SeamRegistry，llm provider = model factory） ---


def test_runtime_seams_resolve_llm() -> None:
    runtime = AgentRuntime()
    provider = runtime.seams.resolve("llm")
    assert provider is runtime._model_factory


def test_runtime_seams_duplicate_fails_loud() -> None:
    runtime = AgentRuntime()
    with pytest.raises(DuplicateProviderError):
        runtime.seams.register("llm", object())


def test_runtime_seams_effect_scope() -> None:
    runtime = AgentRuntime()
    with runtime.seams.effect_scope():
        runtime.seams.register("temp", object())
        assert runtime.seams.has("temp")
    assert not runtime.seams.has("temp")
    assert runtime.seams.has("llm")
