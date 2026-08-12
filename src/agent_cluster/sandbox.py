"""Docker 沙箱执行器（v0.4 T11.5）：``--sandbox docker`` 时把 shell/python/
tests/service 放到容器内执行，工作区挂载 + 超时 kill + 服务容器清理。

- 参考设计（仅借鉴、不复用代码）：codex-cli 的 sandboxing（沙箱隔离思路）。
- 默认镜像 ``python:3.11-slim``（可用环境变量 ``AGENT_CLUSTER_SANDBOX_IMAGE``
  覆盖）；镜像内需自备测试工具（如 uv/pytest），否则命令以明确错误返回。
- 无 Docker 时 ``docker_available()`` 返回 False，构造 ``SandboxRunner`` 抛
  ``SandboxUnavailableError``；CLI 层给出安装指引并退出非零码。
- 权限语义不变：沙箱只改变执行位置，白名单/审批仍由 ToolSession 层负责。
"""

from __future__ import annotations

import asyncio
import os
import uuid
from pathlib import Path
from typing import Any

DEFAULT_SANDBOX_IMAGE = "python:3.11-slim"
CONTAINER_WORKSPACE = "/workspace"

_docker_available_cache: bool | None = None


class SandboxUnavailableError(RuntimeError):
    """Docker 不可用（docker version 失败 / 未安装）。"""


def clear_docker_cache() -> None:
    """清除 docker 可用性缓存（测试用）。"""
    global _docker_available_cache
    _docker_available_cache = None


def docker_available() -> bool:
    """探测 docker CLI 可用性（缓存结果，测试可清缓存）。"""
    global _docker_available_cache
    if _docker_available_cache is None:
        _docker_available_cache = _probe_docker()
    return _docker_available_cache


def _probe_docker() -> bool:
    try:
        import subprocess

        proc = subprocess.run(
            ["docker", "version", "--format", "{{.Server.Version}}"],
            capture_output=True,
            timeout=10,
        )
        return proc.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


class SandboxRunner:
    """容器化命令执行器：把工作区挂载到容器内执行命令。

    - ``run_shell`` / ``run_tests``：``sh -c <command>`` 在容器内执行。
    - ``run_python``：执行工作区内脚本（相对路径，容器内 /workspace/<rel>）。
    - ``run_service``：``docker run -d`` 启动 -> ``docker exec`` 轮询健康检查 ->
      ``docker rm -f`` 清理（不常驻）。
    - 所有 docker 子命令带超时；失败返回 ``{ok, output, exit_code}`` 字典，
      不向流程抛异常（与 ToolSession 契约一致）。
    """

    def __init__(self, workspace_root: str | Path, *, image: str | None = None) -> None:
        if not docker_available():
            raise SandboxUnavailableError(
                "Docker 不可用（docker version 失败）；请安装 Docker Desktop 并启动，"
                "或用 --sandbox none 在本机执行。"
            )
        self.workspace_root = Path(workspace_root).expanduser().resolve()
        self.image = image or os.environ.get("AGENT_CLUSTER_SANDBOX_IMAGE") or DEFAULT_SANDBOX_IMAGE
        # Windows 路径转 POSIX 风格供 docker -v 挂载（Docker Desktop 要求）
        self._volume = f"{self.workspace_root.as_posix()}:{CONTAINER_WORKSPACE}"

    # ------------------------------------------------------------------
    # 公共执行入口（与 ToolSession handler 返回契约一致：dict）
    # ------------------------------------------------------------------

    async def run_shell(self, command: str, *, timeout: int) -> dict[str, Any]:
        """容器内执行任意 shell 命令（sh -c，支持管道/重定向）。"""
        if not command.strip():
            return {"ok": False, "output": "run_shell 需要 command 参数", "exit_code": -3}
        return await self._docker(
            ["run", "--rm", "-v", self._volume, "-w", CONTAINER_WORKSPACE,
             self.image, "sh", "-c", command],
            timeout=timeout,
        )

    async def run_tests(self, command: str, *, timeout: int) -> dict[str, Any]:
        """容器内执行测试命令（与 run_shell 同容器语义，语义分层）。"""
        if not command.strip():
            return {"ok": False, "output": "run_tests 需要 command 参数", "exit_code": -3}
        return await self._docker(
            ["run", "--rm", "-v", self._volume, "-w", CONTAINER_WORKSPACE,
             self.image, "sh", "-c", command],
            timeout=timeout,
        )

    async def run_python(self, script_rel: str, *, timeout: int) -> dict[str, Any]:
        """容器内执行工作区相对路径的 Python 脚本。"""
        script_rel = script_rel.replace("\\", "/").lstrip("/")
        return await self._docker(
            ["run", "--rm", "-v", self._volume, "-w", CONTAINER_WORKSPACE,
             self.image, "python", f"{CONTAINER_WORKSPACE}/{script_rel}"],
            timeout=timeout,
        )

    async def run_service(
        self,
        command: str,
        health: str,
        *,
        timeout: int,
        wait: float = 2.0,
        max_attempts: int = 30,
    ) -> dict[str, Any]:
        """容器内启动服务并轮询健康检查，完成后强制清理容器。

        - ``docker run -d`` 启动（sh -c 包 command）；``docker exec`` 执行健康
          检查命令（镜像内需自备检查工具，如 python urllib）。
        - 健康检查成功即返回 ok；进程退出/超时返回失败；finally 恒 ``rm -f``。
        """
        if not command.strip() or not health.strip():
            return {"ok": False, "output": "run_service 需要 command 与 health 参数", "exit_code": -3}
        container = f"acs-svc-{uuid.uuid4().hex[:10]}"
        started = await self._docker(
            ["run", "-d", "--name", container, "-v", self._volume, "-w", CONTAINER_WORKSPACE,
             self.image, "sh", "-c", command],
            timeout=max(60, timeout),
        )
        if not started["ok"]:
            return {"ok": False, "output": f"服务容器启动失败：{started['output']}", "exit_code": started["exit_code"]}
        last_output = ""
        try:
            for attempt in range(max(1, max_attempts)):
                inspect = await self._docker(
                    ["inspect", "-f", "{{.State.Running}}", container], timeout=15
                )
                if not (inspect["ok"] and inspect["output"].strip().lower() == "true"):
                    logs = await self._docker(["logs", "--tail", "50", container], timeout=15)
                    return {
                        "ok": False,
                        "output": f"服务进程提前退出：{(logs['output'] or '(无日志)')[:500]}",
                        "error": "服务进程提前退出",
                    }
                check = await self._docker(
                    ["exec", container, "sh", "-c", health], timeout=max(5, int(wait))
                )
                last_output = check["output"]
                if check["ok"]:
                    return {
                        "ok": True,
                        "output": f"服务健康检查通过（尝试 {attempt + 1} 次）：{last_output[:500]}",
                    }
                await asyncio.sleep(wait)
            return {
                "ok": False,
                "output": f"服务健康检查未通过（{max_attempts} 次尝试）：{last_output[:500]}",
                "error": "服务未就绪",
            }
        finally:
            await self._docker(["rm", "-f", container], timeout=30)

    # ------------------------------------------------------------------
    # docker 子命令
    # ------------------------------------------------------------------

    async def _docker(self, args: list[str], timeout: int) -> dict[str, Any]:
        """执行 docker 子命令并捕获输出（超时 kill）。"""
        try:
            proc = await asyncio.create_subprocess_exec(
                "docker",
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except (OSError, ProcessLookupError):
                pass
            await proc.wait()
            return {"ok": False, "output": f"docker 命令超时（>{timeout}s）", "exit_code": -1}
        except OSError as exc:
            return {"ok": False, "output": f"docker 启动失败：{exc}", "exit_code": -2}
        output = (stdout.decode("utf-8", errors="replace") + stderr.decode("utf-8", errors="replace")).strip()
        return {"ok": proc.returncode == 0, "output": output or "(无输出)", "exit_code": proc.returncode}
