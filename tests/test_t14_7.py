"""Task 14.7 配置分层（profile/bundle/patch）测试（dsh 契约移植）。"""

from __future__ import annotations

import json

import pytest

from agent_cluster.config_layers import (
    BUILTIN_PROFILES,
    ConfigActivationError,
    ConfigEntry,
    assert_activated,
    dump_config_entries,
    merge_entries,
)


def _base() -> list[ConfigEntry]:
    return [
        ConfigEntry(id="server", payload={"port": 8765, "host": "127.0.0.1"}),
        ConfigEntry(id="llm", payload={"provider": "deepseek", "model": "deepseek-v4-flash"}),
        ConfigEntry(id="tools", payload={"enabled": True}),
    ]


def test_patch_replaces_whole_config_by_id() -> None:
    patch = [ConfigEntry(id="llm", payload={"provider": "deterministic"})]
    merged = merge_entries(_base(), patch)
    assert {e.id: e.payload for e in merged}["llm"] == {"provider": "deterministic"}  # 整块替换
    assert len(merged) == 3


def test_patch_inserts_new_ids_after_base() -> None:
    patch = [ConfigEntry(id="audit", payload={"enabled": True})]
    merged = merge_entries(_base(), patch)
    assert [e.id for e in merged] == ["server", "llm", "tools", "audit"]
    assert merged[-1].payload == {"enabled": True}


def test_patch_disables_entry_keeps_it() -> None:
    patch = [ConfigEntry(id="tools", payload={"enabled": False}, disabled=True)]
    merged = merge_entries(_base(), patch)
    entry = next(e for e in merged if e.id == "tools")
    assert entry.disabled is True  # disabled 而非删除


def test_merge_is_immutable() -> None:
    base = _base()
    merged = merge_entries(base, [ConfigEntry(id="llm", payload={"provider": "x"})])
    assert base[1].payload["provider"] == "deepseek"  # 原列表不被修改


def test_render_deterministic() -> None:
    merged = merge_entries(_base(), [ConfigEntry(id="audit", payload={"enabled": True})])
    render = dump_config_entries(merged)
    again = dump_config_entries(merge_entries(_base(), [ConfigEntry(id="audit", payload={"enabled": True})]))
    assert json.dumps(render, ensure_ascii=False) == json.dumps(again, ensure_ascii=False)
    assert render[0]["id"] == "server" and render[0]["port"] == 8765


def test_assert_activated_passes() -> None:
    merged = merge_entries(_base(), [])
    assert_activated(merged, {"server", "llm", "tools"})


def test_assert_activated_fails_loud_on_missing() -> None:
    merged = merge_entries(_base(), [])
    with pytest.raises(ConfigActivationError):
        assert_activated(merged, {"server"})


def test_assert_activated_exempts_disabled() -> None:
    merged = merge_entries(_base(), [ConfigEntry(id="tools", payload={}, disabled=True)])
    assert_activated(merged, {"server", "llm"})  # disabled 条目免激活


def test_builtin_profiles_cover_three_modes() -> None:
    assert set(BUILTIN_PROFILES) == {"serve", "chat", "headless"}
    for profile in BUILTIN_PROFILES.values():
        assert all(isinstance(e, ConfigEntry) for e in profile)


def test_dump_config_offline_render(capsys) -> None:
    from agent_cluster.cli import main

    code = main(["dump-config", "--profile", "serve"])
    out = capsys.readouterr().out
    assert code == 0
    rendered = json.loads(out)
    assert isinstance(rendered, list) and rendered
    assert rendered[0]["id"] == "server"
