"""Task 14.8 工程化基建测试（Agent Notes 校验 / 生成器 freshness / postmortem 约定）。"""

from __future__ import annotations

import json
import subprocess
import sys

import pytest

from agent_cluster.config_layers import BUILTIN_PROFILES


def _run_script(script: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, script, *args],
        capture_output=True,
        text=True,
        cwd=".",  # 仓库根
    )


# --- Agent Notes 校验 ---


def test_verify_agent_notes_passes_on_clean_tree() -> None:
    result = _run_script("scripts/verify_agent_notes.py")
    assert result.returncode == 0, result.stderr


def test_verify_agent_notes_rejects_bad_class(tmp_path) -> None:
    bad = tmp_path / "implemented" / "not-a-class" / "2026-08-14-x.md"
    bad.parent.mkdir(parents=True)
    bad.write_text("# x\n", encoding="utf-8")
    result = _run_script("scripts/verify_agent_notes.py", str(tmp_path))
    assert result.returncode != 0
    assert "not-a-class" in result.stderr


def test_verify_agent_notes_rejects_bad_lifecycle(tmp_path) -> None:
    bad = tmp_path / "unknown" / "architecture" / "2026-08-14-x.md"
    bad.parent.mkdir(parents=True)
    bad.write_text("# x\n", encoding="utf-8")
    result = _run_script("scripts/verify_agent_notes.py", str(tmp_path))
    assert result.returncode != 0


# --- 生成器 + freshness 校验器 ---


def test_gen_config_catalog_contains_profiles() -> None:
    result = _run_script("scripts/gen_config_catalog.py")
    assert result.returncode == 0, result.stderr
    text = result.stdout
    for profile in BUILTIN_PROFILES:
        assert profile in text


def test_verify_config_catalog_fresh_after_gen(tmp_path) -> None:
    catalog = tmp_path / "config-catalog.md"
    result = _run_script("scripts/gen_config_catalog.py", str(catalog))
    assert result.returncode == 0
    verify = _run_script("scripts/verify_config_catalog.py", str(catalog))
    assert verify.returncode == 0, verify.stderr


def test_verify_config_catalog_rejects_stale(tmp_path) -> None:
    stale = tmp_path / "config-catalog.md"
    stale.write_text("# stale\n", encoding="utf-8")
    verify = _run_script("scripts/verify_config_catalog.py", str(stale))
    assert verify.returncode != 0


def test_gen_module_graph_outputs_mermaid(tmp_path) -> None:
    out = tmp_path / "module-graph.md"
    result = _run_script("scripts/gen_module_graph.py", str(out))
    assert result.returncode == 0, result.stderr
    text = out.read_text(encoding="utf-8")
    assert "```mermaid" in text and "flowchart" in text


def test_verify_module_graph_fresh(tmp_path) -> None:
    out = tmp_path / "module-graph.md"
    _run_script("scripts/gen_module_graph.py", str(out))
    verify = _run_script("scripts/verify_module_graph.py", str(out))
    assert verify.returncode == 0, verify.stderr


# --- 仓库内生成物 freshness（提交物即当前生成结果） ---


def test_committed_config_catalog_is_fresh() -> None:
    verify = _run_script("scripts/verify_config_catalog.py", "docs/config-catalog.md")
    assert verify.returncode == 0, verify.stderr


def test_committed_module_graph_is_fresh() -> None:
    verify = _run_script("scripts/verify_module_graph.py", "docs/module-graph.md")
    assert verify.returncode == 0, verify.stderr
