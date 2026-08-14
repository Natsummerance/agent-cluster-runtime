"""Task 14.4 能力接缝运行时测试（dsh capability seam 三角移植）。"""

from __future__ import annotations

import pytest

from agent_cluster.seam import (
    DuplicateProviderError,
    MissingProviderError,
    Provider,
    Registration,
    SeamRegistry,
    Service,
)


class FakeLlm(Service):
    name = "llm"
    description = "LLM adapter registry"


class DeepSeekBackend(Provider):
    kind = "deepseek"


class OpenAIBackend(Provider):
    kind = "openai"


def test_register_and_resolve() -> None:
    reg = SeamRegistry()
    backend = DeepSeekBackend()
    reg.register(FakeLlm.name, backend)
    assert reg.resolve("llm") is backend


def test_duplicate_provider_fails_loud() -> None:
    reg = SeamRegistry()
    reg.register("llm", DeepSeekBackend())
    with pytest.raises(DuplicateProviderError):
        reg.register("llm", OpenAIBackend())


def test_resolve_missing_raises() -> None:
    reg = SeamRegistry()
    with pytest.raises(MissingProviderError):
        reg.resolve("nope")


def test_registration_context_manager_unregisters() -> None:
    reg = SeamRegistry()
    with reg.register("llm", DeepSeekBackend()) as provider:
        assert reg.resolve("llm") is provider
    assert not reg.has("llm")
    with pytest.raises(MissingProviderError):
        reg.resolve("llm")


def test_effect_scope_unwinds_reverse_order() -> None:
    """effect 式注册：scope 退出按注册逆序回滚（对照 Cordis effect 逆序回滚）。"""
    reg = SeamRegistry()
    order: list[str] = []
    with reg.effect_scope():
        reg.register("a", Provider())
        order.append("a")
        with reg.effect_scope():
            reg.register("b", Provider())
            order.append("b")
            reg.register("c", Provider())
            order.append("c")
            assert set(reg.names()) == {"a", "b", "c"}
        # 内层 scope 退出：c、b 逆序回滚
        assert set(reg.names()) == {"a"}
    assert set(reg.names()) == set()
    assert order == ["a", "b", "c"]


def test_provider_swap_via_unregister() -> None:
    """换实现 = 卸载旧 provider + 注册新 provider（配置驱动热换的等价物）。"""
    reg = SeamRegistry()
    old = DeepSeekBackend()
    reg.register("llm", old)
    reg.unregister("llm")
    new = OpenAIBackend()
    reg.register("llm", new)
    assert reg.resolve("llm") is new


def test_registry_style_and_singleton_style_coexist() -> None:
    """注册表式 seam（多名称并存）与单例式 seam（单名称）不冲突。"""
    reg = SeamRegistry()
    reg.register("subagents", Provider())
    reg.register("skills", Provider())
    reg.register("bash", Provider())  # 单例式：独占名称
    assert set(reg.names()) == {"subagents", "skills", "bash"}


def test_definition_name_discipline() -> None:
    """Definition 必须声明非空 name（能力接缝三角的 Definition 角契约）。"""
    class Bad(Service):
        pass

    reg = SeamRegistry()
    with pytest.raises(ValueError):
        reg.register(Bad.name, Provider())


def test_registration_object_shape() -> None:
    reg = SeamRegistry()
    r = reg.register("llm", DeepSeekBackend())
    assert isinstance(r, Registration)
    assert r.name == "llm"
