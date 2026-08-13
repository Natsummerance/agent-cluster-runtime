"""v0.2 工具执行层单元测试：权限分层、路径越界、edit_file 多 hunk、shell 白名单、git happy path。"""

from __future__ import annotations

import asyncio
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from agent_cluster.tools import (
    ToolCall,
    ToolError,
    ToolPermission,
    ToolResult,
    ToolSession,
    apply_text_edits,
    build_default_tools,
)

@pytest.fixture()
def session(tmp_path: Path) -> ToolSession:
    return ToolSession(tmp_path)


def _init_git(ws: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=ws, check=True)
    subprocess.run(["git", "config", "user.email", "t@test"], cwd=ws, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=ws, check=True)


# ---------------------------------------------------------------------------
# 注册表与权限分层
# ---------------------------------------------------------------------------


def test_registry_has_builtin_tools():
    registry = build_default_tools()
    names = registry.names()
    assert "read_file" in names and "write_file" in names and "edit_file" in names
    assert "run_tests" in names and "run_shell" in names and "git_commit" in names


def test_registry_duplicate_registration_rejected():
    registry = build_default_tools()
    spec = registry.get("read_file")
    with pytest.raises(ToolError, match="已注册"):
        registry.register(spec.model_copy(update={"name": "read_file"}))


def test_registry_get_unknown_raises():
    with pytest.raises(ToolError, match="未知工具"):
        build_default_tools().get("no_such_tool")


def test_permission_tiers_cover_three_levels():
    registry = build_default_tools()
    assert registry.get("read_file").permission == ToolPermission.READ
    assert registry.get("write_file").permission == ToolPermission.WORKSPACE_WRITE
    assert registry.get("run_shell").permission == ToolPermission.DANGEROUS
    assert registry.get("delete_file").permission == ToolPermission.DANGEROUS
    assert registry.get("run_tests").permission == ToolPermission.WORKSPACE_WRITE


def test_openai_schemas_include_name_and_parameters():
    schemas = build_default_tools().as_openai_schemas(names=["write_file"])
    assert len(schemas) == 1
    assert schemas[0]["function"]["name"] == "write_file"
    assert schemas[0]["function"]["parameters"]["type"] == "object"


# ---------------------------------------------------------------------------
# 路径越界防护
# ---------------------------------------------------------------------------


async def test_path_traversal_rejected(session: ToolSession):
    result = await session.execute(ToolCall(name="read_file", args={"path": "../escape.txt"}))
    assert not result.ok
    assert "越界" in result.output


async def test_absolute_path_outside_workspace_rejected(session: ToolSession, tmp_path: Path):
    outside = tmp_path.parent / "outside-secret.txt"
    outside.write_text("secret", encoding="utf-8")
    result = await session.execute(ToolCall(name="read_file", args={"path": str(outside)}))
    assert not result.ok
    assert "越界" in result.output


async def test_junction_symlink_escape_rejected(session: ToolSession, tmp_path: Path):
    """符号链接/junction 外逃：resolve() 跟随链接后必须拦截。"""
    outside = tmp_path.parent / f"out-{tmp_path.name}"
    outside.mkdir(exist_ok=True)
    (outside / "secret.txt").write_text("secret", encoding="utf-8")
    link = tmp_path / "link"
    if sys.platform != "win32":
        pytest.skip("junction 仅 Windows 支持")
    proc = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(outside)],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        pytest.skip("无法创建 junction（权限不足）")
    result = await session.execute(ToolCall(name="read_file", args={"path": "link/secret.txt"}))
    assert not result.ok
    assert "越界" in result.output


async def test_write_file_and_read_roundtrip(session: ToolSession):
    write = await session.execute(
        ToolCall(name="write_file", args={"path": "a/b.txt", "content": "你好\nworld\n"})
    )
    assert write.ok and "a/b.txt" in write.output
    read = await session.execute(ToolCall(name="read_file", args={"path": "a/b.txt"}))
    assert read.ok and "你好" in read.output


async def test_write_file_absolute_inside_workspace_allowed(session: ToolSession, tmp_path: Path):
    target = tmp_path / "sub" / "f.txt"
    result = await session.execute(
        ToolCall(name="write_file", args={"path": str(target), "content": "x"})
    )
    assert result.ok
    assert target.read_text(encoding="utf-8") == "x"


# ---------------------------------------------------------------------------
# edit_file 多 hunk
# ---------------------------------------------------------------------------


def test_apply_text_edits_multi_hunk():
    text = "hello\nworld\nhello\n"
    updated = apply_text_edits(text, [{"old": "hello", "new": "hi"}, {"old": "world", "new": "earth"}])
    assert updated == "hi\nearth\nhello\n"


def test_apply_text_edits_count_parameter():
    text = "a a a"
    assert apply_text_edits(text, [{"old": "a", "new": "b", "count": 2}]) == "b b a"


def test_apply_text_edits_missing_old_raises():
    with pytest.raises(ToolError, match="未找到 old"):
        apply_text_edits("abc", [{"old": "zzz", "new": "x"}])


def test_apply_text_edits_empty_old_raises():
    with pytest.raises(ToolError, match="非空"):
        apply_text_edits("abc", [{"old": "", "new": "x"}])


async def test_edit_file_applies_and_preserves_atomicity(session: ToolSession):
    await session.execute(ToolCall(name="write_file", args={"path": "f.txt", "content": "alpha\nbeta\n"}))
    ok = await session.execute(
        ToolCall(
            name="edit_file",
            args={"path": "f.txt", "edits": [{"old": "alpha", "new": "ALPHA"}, {"old": "beta", "new": "BETA"}]},
        )
    )
    assert ok.ok
    content = (session.workspace_root / "f.txt").read_text(encoding="utf-8")
    assert content == "ALPHA\nBETA\n"
    # 原子性：第二个 hunk 缺失时整体不修改
    failed = await session.execute(
        ToolCall(name="edit_file", args={"path": "f.txt", "edits": [{"old": "ALPHA", "new": "x"}, {"old": "NOPE", "new": "y"}]})
    )
    assert not failed.ok
    assert (session.workspace_root / "f.txt").read_text(encoding="utf-8") == "ALPHA\nBETA\n"


# ---------------------------------------------------------------------------
# shell 白名单与危险工具审批
# ---------------------------------------------------------------------------


async def test_run_tests_whitelisted_command_allowed(session: ToolSession):
    result = await session.execute(ToolCall(name="run_tests", args={"command": "pytest --version"}))
    assert result.ok
    assert "pytest" in result.output.lower() or "usage" in result.output.lower()


async def test_run_tests_non_whitelist_rejected(session: ToolSession):
    result = await session.execute(ToolCall(name="run_tests", args={"command": "del /f C:\\Windows"}))
    assert not result.ok
    assert "白名单" in result.output


async def test_dangerous_tool_requires_approval_and_does_not_execute(session: ToolSession):
    result = await session.execute(ToolCall(name="run_shell", args={"command": "cmd /c echo SHOW"}))
    assert not result.ok
    assert result.needs_approval
    assert "SHOW" not in result.output


async def test_dangerous_tool_executes_after_approval(session: ToolSession):
    result = await session.execute(
        ToolCall(name="run_shell", args={"command": "cmd /c echo APPROVED_OK"}), approved=True
    )
    assert result.ok
    assert "APPROVED_OK" in result.output


async def test_run_python_executes_code(session: ToolSession):
    result = await session.execute(
        ToolCall(name="run_python", args={"code": "print(6 * 7)"}), approved=True
    )
    assert result.ok
    assert "42" in result.output


async def test_delete_file_requires_approval_then_deletes(session: ToolSession):
    await session.execute(ToolCall(name="write_file", args={"path": "del.txt", "content": "x"}))
    blocked = await session.execute(ToolCall(name="delete_file", args={"path": "del.txt"}))
    assert blocked.needs_approval
    assert (session.workspace_root / "del.txt").exists()
    deleted = await session.execute(ToolCall(name="delete_file", args={"path": "del.txt"}), approved=True)
    assert deleted.ok
    assert not (session.workspace_root / "del.txt").exists()


# ---------------------------------------------------------------------------
# git 工具 happy path
# ---------------------------------------------------------------------------


async def test_git_lifecycle_commit_and_status(session: ToolSession, tmp_path: Path):
    _init_git(tmp_path)
    await session.execute(ToolCall(name="write_file", args={"path": "app.py", "content": "print('hi')\n"}))
    add = await session.execute(ToolCall(name="git_add", args={"paths": ["."]}))
    assert add.ok
    status = await session.execute(ToolCall(name="git_status", args={}))
    assert status.ok and "app.py" in status.output
    commit = await session.execute(ToolCall(name="git_commit", args={"message": "init"}))
    assert commit.ok and "root-commit" in commit.output
    clean = await session.execute(ToolCall(name="git_status", args={}))
    assert clean.ok and "app.py" not in clean.output


async def test_git_revert_restores_changes(session: ToolSession, tmp_path: Path):
    _init_git(tmp_path)
    (tmp_path / "keep.txt").write_text("v1", encoding="utf-8")
    await session.execute(ToolCall(name="git_add", args={"paths": ["."]}))
    await session.execute(ToolCall(name="git_commit", args={"message": "init"}))
    (tmp_path / "keep.txt").write_text("v2-broken", encoding="utf-8")
    revert = await session.execute(ToolCall(name="git_revert", args={"files": ["keep.txt"]}))
    assert revert.ok
    assert (tmp_path / "keep.txt").read_text(encoding="utf-8") == "v1"


async def test_replay_cache_deduplicates_commit_on_replay(session: ToolSession, tmp_path: Path):
    """中断恢复重放：完全相同的 git_commit 调用返回缓存结果而非重复执行报错。"""
    _init_git(tmp_path)
    await session.execute(ToolCall(name="write_file", args={"path": "a.txt", "content": "x"}))
    await session.execute(ToolCall(name="git_add", args={"paths": ["."]}))
    first = await session.execute(ToolCall(name="git_commit", args={"message": "init"}))
    assert first.ok
    replay = await session.execute(ToolCall(name="git_commit", args={"message": "init"}))
    assert replay.ok  # 缓存命中，不会报 "nothing to commit"


async def test_read_is_not_poisoned_by_replay_cache(session: ToolSession):
    """只读调用不缓存：编辑后再次读取必须返回最新内容。"""
    await session.execute(ToolCall(name="write_file", args={"path": "f.txt", "content": "one"}))
    await session.execute(ToolCall(name="read_file", args={"path": "f.txt"}))
    await session.execute(ToolCall(name="edit_file", args={"path": "f.txt", "edits": [{"old": "one", "new": "two"}]}))
    fresh = await session.execute(ToolCall(name="read_file", args={"path": "f.txt"}))
    assert "two" in fresh.output and "one" not in fresh.output


# ---------------------------------------------------------------------------
# 审计
# ---------------------------------------------------------------------------


async def test_audit_records_tool_calls(session: ToolSession):
    await session.execute(ToolCall(name="write_file", args={"path": "a.txt", "content": "x"}))
    await session.execute(ToolCall(name="run_shell", args={"command": "cmd /c echo x"}), approved=True)
    assert len(session.audit) == 2
    assert session.audit[0]["tool"] == "write_file"
    assert session.audit[0]["permission"] == ToolPermission.WORKSPACE_WRITE.value
    assert session.audit[1]["ok"] is True
    assert "duration" in session.audit[1]


# ---------------------------------------------------------------------------
# v0.3 工具：run_service / count_tokens / ask_user
# ---------------------------------------------------------------------------


def test_v03_tool_permissions():
    registry = build_default_tools()
    assert registry.get("run_service").permission == ToolPermission.DANGEROUS
    assert registry.get("count_tokens").permission == ToolPermission.READ
    assert registry.get("ask_user").permission == ToolPermission.HUMAN_INTERACTION
    assert "run_service" in registry.names()
    assert "count_tokens" in registry.names()
    assert "ask_user" in registry.names()


async def test_run_service_requires_approval_and_smokes(session: ToolSession):
    (session.workspace_root / "index.html").write_text("<h1>ok</h1>", encoding="utf-8")
    call = ToolCall(
        name="run_service",
        args={
            "command": "python -m http.server 8871",
            "health": "curl -sf http://localhost:8871/",
            "wait": 1,
            "attempts": 10,
        },
    )
    blocked = await session.execute(call)
    assert blocked.needs_approval
    assert "ok" not in blocked.output
    approved = await session.execute(call, approved=True)
    assert approved.ok
    assert "健康检查通过" in approved.output


async def test_run_service_unhealthy_fails(session: ToolSession):
    call = ToolCall(
        name="run_service",
        args={
            "command": "python -m http.server 8872",
            "health": "curl -sf http://localhost:1/",  # 不可达端口
            "wait": 1,
            "attempts": 2,
        },
    )
    result = await session.execute(call, approved=True)
    assert not result.ok
    assert "未通过" in result.output


async def test_count_tokens_file_and_dir(session: ToolSession):
    await session.execute(
        ToolCall(name="write_file", args={"path": "a.txt", "content": "hello world 中文"})
    )
    await session.execute(ToolCall(name="mkdir", args={"path": "sub"}))
    file_result = await session.execute(ToolCall(name="count_tokens", args={"path": "a.txt"}))
    assert file_result.ok
    assert "tokens" in file_result.output
    dir_result = await session.execute(ToolCall(name="count_tokens", args={"path": "."}))
    assert dir_result.ok
