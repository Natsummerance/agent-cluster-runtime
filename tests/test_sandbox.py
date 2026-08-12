"""T11.5 Docker 沙箱测试：可用性探测、命令构造、run_service 生命周期、
ToolSession 委托、CLI --sandbox docker 无 Docker 时的错误出口。

本机无 Docker：全部用 fake/monkeypatch 覆盖，真实 Docker 集成由用户安装后手测。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

import agent_cluster.sandbox as sb
from agent_cluster.cli import _build_sandbox, main
from agent_cluster.sandbox import SandboxRunner, SandboxUnavailableError
from agent_cluster.tools import ToolCall, ToolSession, build_default_tools


def test_docker_available_probe_cache_and_clear(monkeypatch):
    """docker_available 探测结果缓存 + clear_docker_cache 重置。"""
    monkeypatch.setattr(sb, "_probe_docker", lambda: True)
    sb.clear_docker_cache()
    assert sb.docker_available() is True
    assert sb.docker_available() is True  # 缓存命中
    monkeypatch.setattr(sb, "_probe_docker", lambda: False)
    assert sb.docker_available() is True  # 仍是缓存
    sb.clear_docker_cache()
    assert sb.docker_available() is False


def test_sandbox_runner_unavailable_raises(tmp_path, monkeypatch):
    """Docker 不可用时构造 SandboxRunner 抛 SandboxUnavailableError。"""
    monkeypatch.setattr(sb, "docker_available", lambda: False)
    with pytest.raises(SandboxUnavailableError):
        SandboxRunner(tmp_path / "ws")


def _runner_with_fake_docker(tmp_path, monkeypatch, scripted=None):
    """构造 SandboxRunner + 假 _docker（记录调用并返回脚本化结果）。"""
    monkeypatch.setattr(sb, "docker_available", lambda: True)
    runner = SandboxRunner(tmp_path / "ws")
    calls: list[list[str]] = []
    queue = list(scripted or [])

    async def fake(args, timeout):
        calls.append(args)
        if queue:
            return queue.pop(0)
        return {"ok": True, "output": "ok", "exit_code": 0}

    runner._docker = fake  # type: ignore[method-assign]
    return runner, calls


def test_run_shell_command_construction(tmp_path, monkeypatch):
    """run_shell：docker run --rm 挂载工作区 + sh -c。"""
    runner, calls = _runner_with_fake_docker(tmp_path, monkeypatch)
    result = asyncio.run(runner.run_shell("echo hi && pwd", timeout=30))
    assert result["ok"] is True
    args = calls[0]
    assert args[:3] == ["run", "--rm", "-v"]
    assert args[3] == f"{(tmp_path / 'ws').as_posix()}:/workspace"
    assert args[4:6] == ["-w", "/workspace"]
    assert args[-3:] == ["sh", "-c", "echo hi && pwd"]


def test_run_python_command_construction(tmp_path, monkeypatch):
    """run_python：容器内 python /workspace/<rel>。"""
    runner, calls = _runner_with_fake_docker(tmp_path, monkeypatch)
    result = asyncio.run(runner.run_python(".agent-cluster/tmp/run_x.py", timeout=30))
    assert result["ok"] is True
    args = calls[0]
    assert args[-2:] == ["python", "/workspace/.agent-cluster/tmp/run_x.py"]


def test_run_service_healthy_flow(tmp_path, monkeypatch):
    """run_service：run -d -> exec 健康检查（先失败后成功）-> rm -f 清理。"""
    runner, calls = _runner_with_fake_docker(
        tmp_path,
        monkeypatch,
        scripted=[
            {"ok": True, "output": "cid", "exit_code": 0},       # run -d
            {"ok": True, "output": "true", "exit_code": 0},      # inspect
            {"ok": False, "output": "not ready", "exit_code": 1},  # exec #1
            {"ok": True, "output": "true", "exit_code": 0},      # inspect
            {"ok": True, "output": "ready", "exit_code": 0},     # exec #2
            {"ok": True, "output": "", "exit_code": 0},          # rm -f
        ],
    )
    result = asyncio.run(
        runner.run_service(
            "python -m http.server 8000",
            "python -c 'import urllib.request; urllib.request.urlopen(\"http://localhost:8000/\")'",
            timeout=30,
            wait=0.05,
            max_attempts=5,
        )
    )
    assert result["ok"] is True
    assert "健康检查通过" in result["output"]
    # finally 里清理容器
    assert any(c[0] == "rm" and "-f" in c for c in calls)
    # 容器名统一
    names = {c[c.index("--name") + 1] for c in calls if "--name" in c}
    assert names == {calls[0][calls[0].index("--name") + 1]}


def test_run_service_container_exited(tmp_path, monkeypatch):
    """run_service：容器提前退出 -> 失败 + 取日志。"""
    runner, calls = _runner_with_fake_docker(
        tmp_path,
        monkeypatch,
        scripted=[
            {"ok": True, "output": "cid", "exit_code": 0},   # run -d
            {"ok": True, "output": "false", "exit_code": 0},  # inspect 未运行
            {"ok": True, "output": "traceback", "exit_code": 1},  # logs
            {"ok": True, "output": "", "exit_code": 0},      # rm -f
        ],
    )
    result = asyncio.run(
        runner.run_service("python -m bad", "python -c pass", timeout=30, wait=0.05, max_attempts=3)
    )
    assert result["ok"] is False
    assert "提前退出" in result["output"]
    assert any(c[0] == "logs" for c in calls)


class _FakeSandbox:
    """记录委托调用的假沙箱（与 SandboxRunner 方法签名一致）。"""

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    async def run_tests(self, command, *, timeout):
        self.calls.append(("run_tests", command, timeout))
        return {"ok": True, "output": "tests ok", "exit_code": 0}

    async def run_shell(self, command, *, timeout):
        self.calls.append(("run_shell", command, timeout))
        return {"ok": True, "output": "shell ok", "exit_code": 0}

    async def run_python(self, script_rel, *, timeout):
        self.calls.append(("run_python", script_rel, timeout))
        return {"ok": True, "output": "py ok", "exit_code": 0}

    async def run_service(self, command, health, *, timeout, wait, max_attempts):
        self.calls.append(("run_service", command, health, timeout, wait, max_attempts))
        return {"ok": True, "output": "svc ok", "exit_code": 0}


def test_tool_session_delegates_to_sandbox(tmp_path):
    """ToolSession 带 sandbox 时四个执行工具委托给沙箱；白名单仍生效。"""
    ws = tmp_path / "ws"
    fake = _FakeSandbox()
    session = ToolSession(ws, registry=build_default_tools(), sandbox=fake)
    registry = session.registry

    # run_tests（workspace_write，白名单内）直接委托
    result = asyncio.run(
        session.execute(ToolCall(id="1", name="run_tests", args={"command": "uv run pytest -q"}))
    )
    assert result.ok is True
    assert fake.calls[-1][0] == "run_tests"

    # run_tests 白名单外命令仍被拒绝（沙箱不绕过白名单；ToolError 转为 ok=False）
    result = asyncio.run(
        session.execute(ToolCall(id="2", name="run_tests", args={"command": "rm -rf *"}))
    )
    assert result.ok is False
    assert "白名单" in result.output
    assert len(fake.calls) == 1  # 未新增委托（仅第一次 run_tests）

    # 危险工具（run_shell/run_python/run_service）需 approved 才委托
    result = asyncio.run(
        session.execute(ToolCall(id="3", name="run_shell", args={"command": "echo hi"}), approved=True)
    )
    assert result.ok is True
    assert fake.calls[-1][0] == "run_shell"

    result = asyncio.run(
        session.execute(ToolCall(id="4", name="run_python", args={"code": "print(1)"}), approved=True)
    )
    assert result.ok is True
    assert fake.calls[-1][0] == "run_python"

    result = asyncio.run(
        session.execute(
            ToolCall(
                id="5",
                name="run_service",
                args={"command": "python -m http.server", "health": "python -c pass"},
            ),
            approved=True,
        )
    )
    assert result.ok is True
    assert fake.calls[-1][0] == "run_service"

    # 危险工具未批准不委托
    before = len(fake.calls)
    result = asyncio.run(
        session.execute(ToolCall(id="6", name="run_shell", args={"command": "echo hi"}))
    )
    assert result.needs_approval is True
    assert len(fake.calls) == before


def test_sandbox_mode_none_returns_none(tmp_path):
    """--sandbox none / 未指定 -> (None, None)。"""
    assert _build_sandbox(None, tmp_path / "ws") == (None, None)
    assert _build_sandbox("none", tmp_path / "ws") == (None, None)


def test_sandbox_mode_unknown(tmp_path):
    """未知沙箱模式 -> 错误信息。"""
    runner, err = _build_sandbox("podman", tmp_path / "ws")
    assert runner is None
    assert err is not None and "未知沙箱模式" in err


def test_sandbox_docker_requires_workspace(monkeypatch):
    """--sandbox docker 无 --workspace -> 错误信息。"""
    monkeypatch.setattr(sb, "docker_available", lambda: True)
    runner, err = _build_sandbox("docker", None)
    assert runner is None
    assert err is not None and "--workspace" in err


def test_cli_run_sandbox_docker_without_docker(tmp_path, monkeypatch):
    """CLI：--sandbox docker 且 Docker 不可用 -> 退出码 1（不执行流程）。"""
    monkeypatch.setattr(sb, "docker_available", lambda: False)
    flow = tmp_path / "f.yaml"
    flow.write_text(
        "name: t11.5-sandbox\n"
        "thread_id: 't:sandbox'\n"
        "nodes:\n"
        "  - {id: start, type: start}\n"
        "  - {id: end, type: end}\n"
        "edges:\n"
        "  - {from: start, to: end}\n",
        encoding="utf-8",
    )
    ws = tmp_path / "ws"
    code = main(["run", "--flow", str(flow), "--workspace", str(ws), "--sandbox", "docker", "--yes"])
    assert code == 1
    assert not (ws / "flag.txt").exists()


def test_sandbox_runner_available_constructs(tmp_path, monkeypatch):
    """Docker 可用时 SandboxRunner 构造成功并记录挂载卷。"""
    monkeypatch.setattr(sb, "docker_available", lambda: True)
    runner = SandboxRunner(tmp_path / "ws")
    assert runner.image == sb.DEFAULT_SANDBOX_IMAGE
    assert runner._volume == f"{(tmp_path / 'ws').as_posix()}:/workspace"
