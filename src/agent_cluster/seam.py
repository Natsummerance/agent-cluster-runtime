"""能力接缝运行时（v0.7 Task 14.4，dsh capability seam 三角移植）。

- **Definition**：``Service`` 子类声明 ``name``/``description``（能力抽象）。
- **Provider**：接缝实现基类（换实现 = 卸载旧 + 注册新，配置驱动）。
- **Consumer**：``resolve()`` 获取当前 provider（缺省即抛，fail loud）。
- **effect 式注册**：``Registration`` 上下文管理器卸载即回滚；``effect_scope()``
  按注册**逆序**回滚（对照 Cordis fiber effect 语义）；同名 provider 重复加载
  fail-loud（``DuplicateProviderError``）。

契约出处见 ``docs/porting/2026-08-14-dsh-porting.md``（MIT，dsh ``47f943859b``）。
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

__all__ = [
    "DuplicateProviderError",
    "MissingProviderError",
    "Provider",
    "Registration",
    "SeamError",
    "SeamRegistry",
    "Service",
]


class SeamError(RuntimeError):
    """接缝运行时错误基类。"""


class DuplicateProviderError(SeamError):
    """同一名称重复注册 provider（fail loud，对照 dsh duplicate-service）。"""


class MissingProviderError(SeamError):
    """解析时 provider 不存在（Consumer 角缺省即抛）。"""


class Service:
    """能力接缝定义（三角的 Definition 角）：具名能力 + 语义描述。"""

    name: str = ""
    description: str = ""


class Provider:
    """接缝实现基类（三角的 Provider 角）。"""


class Registration:
    """一次注册的 effect 句柄：with 退出即按名回滚。"""

    def __init__(self, registry: SeamRegistry, name: str, provider: Provider) -> None:
        self.registry = registry
        self.name = name
        self.provider = provider

    def __enter__(self) -> Provider:
        return self.provider

    def __exit__(self, *exc_info: object) -> bool:
        self.registry.unregister(self.name, self.provider)
        return False


class SeamRegistry:
    """服务注册表（Cordis Context 服务仓库的轻量等价物）。"""

    def __init__(self) -> None:
        self._providers: dict[str, Provider] = {}
        self._order: list[str] = []

    def register(self, name: str, provider: Provider) -> Registration:
        if not name:
            raise ValueError("seam definition name must be non-empty")
        if name in self._providers:
            raise DuplicateProviderError(
                f"duplicate provider for seam {name!r} (already registered); "
                "unregister first to swap implementations"
            )
        self._providers[name] = provider
        self._order.append(name)
        return Registration(self, name, provider)

    def unregister(self, name: str, provider: Provider | None = None) -> None:
        if name in self._providers and (provider is None or self._providers[name] is provider):
            self._providers.pop(name, None)
            if name in self._order:
                self._order.remove(name)

    def resolve(self, name: str) -> Provider:
        provider = self._providers.get(name)
        if provider is None:
            raise MissingProviderError(f"no provider registered for seam {name!r}")
        return provider

    def has(self, name: str) -> bool:
        return name in self._providers

    def names(self) -> tuple[str, ...]:
        return tuple(self._order)

    @contextmanager
    def effect_scope(self) -> Iterator[SeamRegistry]:
        """effect 作用域：退出时按注册逆序回滚本作用域内全部注册。"""
        start = len(self._order)
        try:
            yield self
        finally:
            for name in reversed(self._order[start:]):
                self._providers.pop(name, None)
            del self._order[start:]
