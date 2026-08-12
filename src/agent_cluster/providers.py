"""模型供应商配置解析层：读取 Codex 配置以复用「当前对话模型」。

- ``CodexProviderConfig``：从 Codex 配置文件（``config.toml``）解析出的模型供应商信息
  （名称 / base_url / env_key / wire_api / 模型名）。
- ``load_codex_model_config(path=None)``：解析 ``~/.codex/config.toml`` 的顶层 ``model``、
  ``model_provider`` 与 ``[model_providers.<provider>]`` 节；文件缺失或键不完整时返回
  ``None``（容错，不抛异常）。
- ``resolve_deepseek_defaults(config=None)``：确定 DeepSeek 接入默认 ``(base_url, env_key)``
  ——优先取 Codex 配置中 DeepSeek 供应商的值，否则回落 ``https://api.deepseek.com`` +
  ``DEEPSEEK_API_KEY``。

本模块不依赖运行时任何组件（避免循环导入），供 ``runtime.ChatModelFactory`` 使用。
API key 只从环境变量或 Codex 配置读取，绝不写入仓库或日志。
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "CodexProviderConfig",
    "DEEPSEEK_DEFAULT_BASE_URL",
    "DEEPSEEK_DEFAULT_ENV_KEY",
    "load_codex_model_config",
    "resolve_deepseek_defaults",
]

DEEPSEEK_DEFAULT_BASE_URL = "https://api.deepseek.com"
DEEPSEEK_DEFAULT_ENV_KEY = "DEEPSEEK_API_KEY"


@dataclass(frozen=True)
class CodexProviderConfig:
    """从 Codex config.toml 解析出的模型供应商配置。"""

    name: str = ""
    base_url: str = ""
    api_key_env: str = ""
    wire_api: str = ""
    model_name: str = ""
    provider: str = ""

    @property
    def is_deepseek(self) -> bool:
        """供应商是否为 DeepSeek（按名称或 base_url 判断，大小写不敏感）。"""
        probe = f"{self.name} {self.base_url}".lower()
        return "deepseek" in probe


def _codex_config_path(path: str | os.PathLike[str] | None) -> Path | None:
    """确定 Codex 配置文件路径：显式 path > CODEX_HOME > ~/.codex。"""
    if path is not None:
        return Path(path)
    home = os.environ.get("CODEX_HOME") or str(Path.home() / ".codex")
    return Path(home) / "config.toml"


def load_codex_model_config(path: str | os.PathLike[str] | None = None) -> CodexProviderConfig | None:
    """解析 Codex config.toml 顶层模型与模型供应商配置。

    - 读取 ``model``（模型名）、``model_provider``（供应商 id）与
      ``[model_providers.<provider>]`` 节（name/base_url/env_key/wire_api）。
    - 文件不存在 / 解析失败 / 键不完整时返回 ``None``（调用方容错回落默认值）。
    """
    config_path = _codex_config_path(path)
    try:
        raw = config_path.read_bytes()
    except OSError:
        return None
    try:
        data = tomllib.loads(raw.decode("utf-8", errors="replace"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError):
        return None

    provider_id = str(data.get("model_provider") or "").strip()
    sections = data.get("model_providers") or {}
    provider = sections.get(provider_id) if isinstance(sections, dict) else None
    if not isinstance(provider, dict):
        return None
    base_url = str(provider.get("base_url") or "").strip()
    env_key = str(provider.get("env_key") or "").strip()
    if not base_url or not env_key:
        return None
    return CodexProviderConfig(
        name=str(provider.get("name") or provider_id),
        base_url=base_url,
        api_key_env=env_key,
        wire_api=str(provider.get("wire_api") or "").strip(),
        model_name=str(data.get("model") or "").strip(),
        provider=provider_id,
    )


def resolve_deepseek_defaults(config: CodexProviderConfig | None = None) -> tuple[str, str]:
    """返回 DeepSeek 接入默认 ``(base_url, api_key_env)``。

    - 传入的 Codex 配置是 DeepSeek 供应商时，取其 base_url / env_key；
    - 否则回落 ``https://api.deepseek.com`` + ``DEEPSEEK_API_KEY``。
    """
    if config is not None and config.is_deepseek and config.base_url and config.api_key_env:
        return config.base_url, config.api_key_env
    return DEEPSEEK_DEFAULT_BASE_URL, DEEPSEEK_DEFAULT_ENV_KEY
