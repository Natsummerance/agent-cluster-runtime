"""配置分层（v0.7 Task 14.7，dsh profile/bundle/patch 契约移植）。

- **patch 按 id 整块替换**（无深合并）：同 id patch 覆盖 base 整条配置；
  新 id 追加在 base 之后；``disabled`` 标记保留条目而非删除。
- ``dump_config_entries``：确定性离线渲染（不 eval 配置代码，对照 dsh ``--dump-config``）。
- ``assert_activated``：启动 fail-loud 审计——所有非 disabled 条目必须被激活
  （对照 dsh ``assertEntriesActivated``）。
- 内置 profile：``serve`` / ``chat`` / ``headless``（headless 不监听端口）。

契约出处见 ``docs/porting/2026-08-14-dsh-porting.md``（MIT，dsh ``47f943859b``）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

__all__ = [
    "BUILTIN_PROFILES",
    "ConfigActivationError",
    "ConfigEntry",
    "assert_activated",
    "dump_config_entries",
    "merge_entries",
]


@dataclass(frozen=True)
class ConfigEntry:
    """一条配置行：id + 整块 payload + disabled 标记。"""

    id: str
    payload: Mapping[str, Any]
    disabled: bool = False


class ConfigActivationError(RuntimeError):
    """启动审计：存在未激活的非 disabled 配置条目。"""


BUILTIN_PROFILES: dict[str, list[ConfigEntry]] = {
    "serve": [
        ConfigEntry(id="server", payload={"host": "127.0.0.1", "port": 8765}),
        ConfigEntry(id="api", payload={"enabled": True, "auth": "local"}),
        ConfigEntry(id="ui", payload={"enabled": True}),
        ConfigEntry(id="llm", payload={"provider": "deepseek", "model": "deepseek-v4-flash"}),
        ConfigEntry(id="tools", payload={"enabled": True}),
        ConfigEntry(id="persistence", payload={"backend": "jsonl"}),
        ConfigEntry(id="audit", payload={"enabled": True}),
    ],
    "chat": [
        ConfigEntry(id="repl", payload={"enabled": True}),
        ConfigEntry(id="llm", payload={"provider": "deepseek", "model": "deepseek-v4-flash"}),
        ConfigEntry(id="tools", payload={"enabled": True}),
        ConfigEntry(id="persistence", payload={"backend": "memory"}),
    ],
    "headless": [
        ConfigEntry(id="runner", payload={"enabled": True}),
        ConfigEntry(id="code-runtime", payload={"enabled": True}),
        ConfigEntry(id="llm", payload={"provider": "deepseek", "model": "deepseek-v4-flash"}),
        ConfigEntry(id="server", payload={"enabled": False}, disabled=True),  # 无监听端口
    ],
}


def merge_entries(
    base: Sequence[ConfigEntry],
    patch: Sequence[ConfigEntry],
) -> list[ConfigEntry]:
    """按 id 合并：patch 整块替换 base 同 id 条目，新 id 按 patch 顺序追加。

    不改动 base/patch 本身（不可变语义）。
    """
    base_by_id = {e.id: e for e in base}
    patch_by_id = {e.id: e for e in patch}
    merged: list[ConfigEntry] = []
    for entry in base:
        merged.append(patch_by_id.get(entry.id, entry))
    for entry in patch:
        if entry.id not in base_by_id:
            merged.append(entry)
    return merged


def dump_config_entries(entries: Sequence[ConfigEntry]) -> list[dict[str, Any]]:
    """确定性离线渲染：id + disabled + payload（顺序 = 合并顺序）。"""
    return [
        {"id": e.id, "disabled": e.disabled, **dict(e.payload)}
        for e in entries
    ]


def dump_config(profile: str, patches: Sequence[ConfigEntry] | None = None) -> str:
    """渲染指定 profile 的合并配置为 JSON 文本。"""
    if profile not in BUILTIN_PROFILES:
        raise ValueError(f"unknown profile {profile!r} (builtin: {sorted(BUILTIN_PROFILES)})")
    merged = merge_entries(BUILTIN_PROFILES[profile], patches or [])
    return json.dumps(dump_config_entries(merged), ensure_ascii=False, indent=2)


def assert_activated(entries: Sequence[ConfigEntry], active_ids: set[str]) -> None:
    """fail-loud：非 disabled 条目必须全部出现在 active_ids。"""
    missing = [e.id for e in entries if not e.disabled and e.id not in active_ids]
    if missing:
        raise ConfigActivationError(
            f"config entries not activated: {sorted(missing)}"
        )
