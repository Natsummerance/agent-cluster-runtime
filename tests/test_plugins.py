"""T11.3 插件层测试：双清单解析/合并、hooks.json、marketplace、技能命名空间、hooks 执行。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from agent_cluster.plugins import (
    PluginManager,
    discover_plugin_roots,
    parse_manifest,
    plugin_skill_namespace,
)
from agent_cluster.skills import SkillLoader, SkillRegistry


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _make_plugin_root(tmp_path: Path, name: str = "demo", version: str = "1.2.3") -> Path:
    """构造含 .codex-plugin + .claude-plugin 双清单的插件根。"""
    root = tmp_path / name
    _write(
        root / ".codex-plugin" / "plugin.json",
        json.dumps(
            {
                "name": name,
                "version": version,
                "description": "示例插件",
                "author": {"name": "tester"},
                "skills": "./skills/",
                "hooks": {"session_start": [{"command": "echo start"}]},
                "interface": {"displayName": "Demo", "capabilities": ["Read", "Write"]},
            },
            ensure_ascii=False,
        ),
    )
    _write(
        root / ".claude-plugin" / "plugin.json",
        json.dumps({"name": name, "description": "示例插件 claude", "version": version, "keywords": ["demo"]}),
    )
    _write(
        root / "skills" / "alpha" / "SKILL.md",
        "---\nname: alpha\ndescription: 技能A\nversion: 0.1.0\n---\n正文A",
    )
    _write(
        root / "skills" / "beta" / "SKILL.md",
        "---\nname: beta\ndescription: 技能B\nversion: 0.1.0\n---\n正文B",
    )
    return root


def test_parse_manifest_codex_and_claude_merge(tmp_path):
    root = _make_plugin_root(tmp_path)
    manifest = parse_manifest(root)
    assert manifest is not None
    assert manifest.name == "demo"
    assert manifest.version == "1.2.3"
    assert manifest.license == ""
    assert manifest.interface["displayName"] == "Demo"
    assert manifest.skill_dirs == [
        str((root / "skills" / "alpha").resolve()),
        str((root / "skills" / "beta").resolve()),
    ]


def test_parse_manifest_generic_plugin_json(tmp_path):
    root = tmp_path / "generic"
    _write(root / "plugin.json", json.dumps({"name": "generic-p", "version": "0.5.0"}))
    manifest = parse_manifest(root)
    assert manifest is not None
    assert manifest.name == "generic-p"


def test_parse_manifest_returns_none_without_manifest(tmp_path):
    root = tmp_path / "empty"
    root.mkdir()
    assert parse_manifest(root) is None


def test_parse_manifest_hooks_codex_style(tmp_path):
    """codex-cli 契约：PascalCase 事件 + MatcherGroup{matcher, hooks}。"""
    root = tmp_path / "hooker"
    _write(
        root / "plugin.json",
        json.dumps(
            {
                "name": "hooker",
                "hooks": {
                    "SessionStart": [{"hooks": [{"type": "command", "command": "echo s"}]}],
                    "Stop": [{"matcher": ".*", "hooks": [{"type": "command", "command": "echo e"}]}],
                    "UserPromptSubmit": [{"type": "command", "command": "echo u"}],
                },
            }
        ),
    )
    manifest = parse_manifest(root)
    assert manifest is not None
    assert [s.command for s in manifest.hooks["session_start"]] == ["echo s"]
    assert [s.command for s in manifest.hooks["stop"]] == ["echo e"]
    assert [s.command for s in manifest.hooks["user_prompt_submit"]] == ["echo u"]


def test_parse_manifest_hooks_alias_names(tmp_path):
    """别名（turn_start/turn_end/session_start 等）归一化到规范事件。"""
    root = tmp_path / "alias-hooker"
    _write(
        root / "plugin.json",
        json.dumps({"name": "alias-hooker", "hooks": {"turn_start": [{"command": "echo a"}]}}),
    )
    manifest = parse_manifest(root)
    assert manifest is not None
    assert [s.command for s in manifest.hooks["user_prompt_submit"]] == ["echo a"]


def test_parse_manifest_hooks_file_path(tmp_path):
    """codex-rs PluginManifestHooks::Paths：hooks 字段为 hooks.json 路径列表。"""
    root = tmp_path / "path-hooker"
    _write(
        root / "hooks" / "hooks.json",
        json.dumps({"hooks": {"SessionStart": [{"hooks": [{"type": "command", "command": "echo p"}]}]}}),
    )
    _write(root / "plugin.json", json.dumps({"name": "path-hooker", "hooks": ["./hooks/hooks.json"]}))
    manifest = parse_manifest(root)
    assert manifest is not None
    assert [s.command for s in manifest.hooks["session_start"]] == ["echo p"]


def test_parse_manifest_hooks_command_windows_fallback(tmp_path, monkeypatch):
    """Windows 优先 commandWindows；非 Windows 用 command。"""
    monkeypatch.setattr("agent_cluster.plugins._platform_is_windows", lambda: True)
    root = tmp_path / "win-hooker"
    _write(
        root / "plugin.json",
        json.dumps(
            {
                "name": "win-hooker",
                "hooks": {
                    "SessionStart": [
                        {"type": "command", "command": "echo unix", "commandWindows": "echo win"}
                    ]
                },
            }
        ),
    )
    manifest = parse_manifest(root)
    assert manifest is not None
    assert manifest.hooks["session_start"][0].command == "echo win"


def test_parse_manifest_hooks_unsupported_types_recorded(tmp_path):
    """mcp_tool/prompt/agent 类型记录为不支持，不执行。"""
    root = tmp_path / "unsup"
    _write(
        root / "plugin.json",
        json.dumps(
            {
                "name": "unsup",
                "hooks": {
                    "SessionStart": [
                        {"type": "mcp_tool", "server": "s", "tool": "t", "input": {}},
                        {"type": "command", "command": "echo ok"},
                    ]
                },
            }
        ),
    )
    manifest = parse_manifest(root)
    assert manifest is not None
    specs = manifest.hooks["session_start"]
    assert specs[0].unsupported == "mcp_tool"
    assert specs[1].command == "echo ok"


async def test_run_hooks_unsupported_type_reported(tmp_path):
    root = tmp_path / "unsup-run"
    _write(
        root / "plugin.json",
        json.dumps(
            {"name": "unsup-run", "hooks": {"SessionStart": [{"type": "prompt"}]}}
        ),
    )
    manager = PluginManager(search_dirs=[str(root)])
    manager.scan()
    results = await manager.run_hooks("session_start")
    assert len(results) == 1
    assert not results[0].ok
    assert "不支持的钩子类型 prompt" in results[0].error


def test_discover_plugin_roots_finds_nested(tmp_path):
    cache = tmp_path / "cache"
    _make_plugin_root(cache / "org-a" / "super" / "1.0.0", name="super")
    _make_plugin_root(cache / "org-b" / "mini" / "0.9.0", name="mini")
    roots = discover_plugin_roots([str(cache)])
    assert len(roots) == 2


def test_discover_plugin_roots_direct_plugin_root(tmp_path):
    root = _make_plugin_root(tmp_path / "cache" / "org" / "p" / "1.0.0")
    roots = discover_plugin_roots([str(root)])
    assert roots == [str(root.resolve())]


def test_marketplace_skills_loaded(tmp_path):
    market = tmp_path / "market"
    _write(
        market / "marketplace.json",
        json.dumps(
            {
                "name": "demo-market",
                "plugins": [
                    {
                        "name": "doc-skills",
                        "description": "文档技能",
                        "source": "./",
                        "skills": ["./skills/xlsx", "./skills/pdf"],
                    }
                ],
            },
            ensure_ascii=False,
        ),
    )
    _write(market / "skills" / "xlsx" / "SKILL.md", "---\nname: xlsx\ndescription: Excel 技能\n---\n正文")
    _write(market / "skills" / "pdf" / "SKILL.md", "---\nname: pdf\ndescription: PDF 技能\n---\n正文")
    manager = PluginManager(search_dirs=[str(market)])
    plugins = manager.list_plugins()
    assert len(plugins) == 1
    skills = manager.list_skills()
    names = {s.name for s in skills}
    assert names == {plugin_skill_namespace("doc-skills", "xlsx"), plugin_skill_namespace("doc-skills", "pdf")}


def test_plugin_manager_scan_and_skills_namespace(tmp_path):
    root = _make_plugin_root(tmp_path / "cache" / "org" / "demo" / "1.0.0")
    registry = SkillRegistry()
    manager = PluginManager(search_dirs=[str(root)], skill_registry=registry)
    plugins = manager.list_plugins()
    assert len(plugins) == 1
    skills = manager.load_skills(registry)
    names = {s.name for s in skills}
    assert names == {plugin_skill_namespace("demo", "alpha"), plugin_skill_namespace("demo", "beta")}
    # 已注册进 SkillRegistry（source=@plugin）
    assert len(registry.list()) == 2


async def test_run_hooks_executes_and_reports(tmp_path):
    root = _make_plugin_root(tmp_path / "p", name="hooker")
    script = tmp_path / "hook_script.py"
    script.write_text(
        "import os, pathlib\n"
        "pathlib.Path(os.environ['HOOK_OUT']).write_text("
        "os.environ.get('AGENT_CLUSTER_EVENT', 'none') + ':' + os.environ.get('AGENT_CLUSTER_PLUGIN', ''), encoding='utf-8')\n",
        encoding="utf-8",
    )
    _write(
        root / ".codex-plugin" / "plugin.json",
        json.dumps(
            {
                "name": "hooker",
                "hooks": {"session_start": [{"command": f"{sys.executable} {script}"}]},
            }
        ),
    )
    manager = PluginManager(search_dirs=[str(root)])
    manager.scan()
    out = tmp_path / "out.txt"
    results = await manager.run_hooks("session_start", workspace=str(tmp_path), env={"HOOK_OUT": str(out)})
    assert len(results) == 1
    assert results[0].ok
    assert out.read_text(encoding="utf-8") == "session_start:hooker"


async def test_run_hooks_payload_on_stdin(tmp_path):
    """hook 从 stdin 收到 codex-cli 风格 JSON payload（session_id/cwd/hook_event_name）。"""
    root = tmp_path / "stdin-hook"
    script = tmp_path / "stdin_reader.py"
    script.write_text(
        "import json, os, pathlib\n"
        "payload = json.loads(sys.stdin.read())\n"
        "out = os.environ.get('HOOK_OUT', '')\n"
        "pathlib.Path(out).write_text(json.dumps(payload), encoding='utf-8')\n".replace("sys.stdin.read()", "open(0, encoding='utf-8').read()"),
        encoding="utf-8",
    )
    _write(
        root / "plugin.json",
        json.dumps({"name": "stdin-hook", "hooks": {"SessionStart": [{"type": "command", "command": f"{sys.executable} {script}"}]}}),
    )
    manager = PluginManager(search_dirs=[str(root)])
    manager.scan()
    out = tmp_path / "payload.json"
    results = await manager.run_hooks(
        "session_start",
        workspace=str(tmp_path),
        thread_id="t-1",
        session_id="s-1",
        env={"HOOK_OUT": str(out)},
        model="deepseek-v4-flash",
        source="startup",
    )
    assert results[0].ok
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["session_id"] == "s-1"
    assert payload["hook_event_name"] == "SessionStart"
    assert payload["cwd"] == str(tmp_path)
    assert payload["source"] == "startup"


async def test_run_hooks_failure_recorded_not_raised(tmp_path):
    root = tmp_path / "fail"
    _write(root / "plugin.json", json.dumps({"name": "fail", "hooks": {"session_start": [{"command": "exit 3"}]}}))
    manager = PluginManager(search_dirs=[str(root)])
    manager.scan()
    results = await manager.run_hooks("session_start")
    assert len(results) == 1
    assert not results[0].ok
    assert "3" in results[0].error


def _build_flow(tmp_path: Path) -> Path:
    path = tmp_path / "build.yaml"
    path.write_text(
        "name: t11.3-build\n"
        "thread_id: \"t:plugins\"\n"
        "nodes:\n"
        "  - {id: start, type: start}\n"
        "  - {id: requirements, type: agent, role: pm}\n"
        "  - {id: requirement_gate, type: gate, gate: requirement_confirmation}\n"
        "  - {id: end, type: end}\n"
        "edges:\n"
        "  - {from: start, to: requirements}\n"
        "  - {from: requirements, to: requirement_gate}\n"
        "  - {from: requirement_gate, to: end, on_accept: end, on_reject: requirements}\n",
        encoding="utf-8",
    )
    return path


def test_build_session_runs_plugin_hooks(tmp_path):
    """build --deterministic：插件 session_start/session_end 钩子自动执行并写标记文件。"""
    from agent_cluster.cli import main

    plugin_root = tmp_path / "hookp"
    script = tmp_path / "mark.py"
    script.write_text(
        "import os, pathlib\n"
        "ws = os.environ.get('AGENT_CLUSTER_WORKSPACE', '')\n"
        "pathlib.Path(ws, 'hook-' + os.environ.get('AGENT_CLUSTER_EVENT', '') + '.txt').write_text("
        "os.environ.get('AGENT_CLUSTER_PLUGIN', ''), encoding='utf-8')\n",
        encoding="utf-8",
    )
    _write(
        plugin_root / "plugin.json",
        json.dumps(
            {
                "name": "hookp",
                "hooks": {
                    "session_start": [{"command": f"{sys.executable} {script}"}],
                    "session_end": [{"command": f"{sys.executable} {script}"}],
                },
            }
        ),
    )
    flow = _build_flow(tmp_path)
    ws = tmp_path / "ws"
    code = main(
        [
            "build",
            "--goal", "做一个演示应用",
            "--workspace", str(ws),
            "--flow", str(flow),
            "--deterministic",
            "--yes",
            "--plugin-dir", str(plugin_root),
        ]
    )
    assert code == 0
    assert (ws / "hook-session_start.txt").read_text(encoding="utf-8") == "hookp"
    assert (ws / "hook-session_end.txt").read_text(encoding="utf-8") == "hookp"


async def test_run_hooks_timeout(tmp_path):
    root = tmp_path / "slow"
    _write(
        root / "plugin.json",
        json.dumps({"name": "slow", "hooks": {"session_start": [{"command": "python -c \"import time; time.sleep(5)\"", "timeout": 0.5}]}}),
    )
    manager = PluginManager(search_dirs=[str(root)])
    manager.scan()
    results = await manager.run_hooks("session_start")
    assert len(results) == 1
    assert not results[0].ok
    assert "超时" in results[0].error
