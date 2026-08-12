"""预检模块（v0.4 T11.1）：`agent-cluster doctor` —— 环境/配置/工具链检查。

检查项：
- ``python``：运行时版本 ≥ 3.11（硬性要求，``requires-python`` 约定）。
- ``model``：模型配置可构造客户端（Codex config.toml 或显式模型名；信息性，
  缺 key 不视为失败——用户可回落 deterministic）。
- ``git``：git 可用（工具模式依赖；硬性要求）。
- ``docker``：Docker 可用（v0.4 硬依赖；缺失给出安装指引并以非零码退出，
  ``--skip-docker-check`` 可跳过；``docker_available()`` 供测试 skipif 门控）。
- ``workspace``：工作区目录可写（信息性，传参时检查）。
- ``plugin_dirs``：插件目录存在（信息性，传参时检查）。
- ``mcp``：MCP 服务器参数可解析（信息性，传参时检查）。

``run_doctor`` 返回 ``DoctorReport``（checks 列表 + required 汇总 + 渲染文本）；
CLI ``doctor`` 子命令按 ``report.ok`` 决定退出码（0/1）。
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

__all__ = [
    "CheckResult",
    "DoctorReport",
    "run_doctor",
    "docker_available",
    "docker_cli_present",
    "docker_daemon_ok",
]


@dataclass(frozen=True)
class CheckResult:
    """单项检查结果。"""

    name: str = ""
    ok: bool = False
    detail: str = ""
    required: bool = True


@dataclass
class DoctorReport:
    """预检报告：检查项列表 + 汇总。"""

    checks: list[CheckResult] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """全部 required 检查通过才算通过（信息性检查不阻断）。"""
        return all(check.ok for check in self.checks if check.required)

    def render(self) -> str:
        """渲染人类可读报告文本。"""
        lines = ["===== agent-cluster doctor ====="]
        for check in self.checks:
            mark = "通过" if check.ok else ("警告" if not check.required else "失败")
            lines.append(f"[{mark}] {check.name}: {check.detail or ('OK' if check.ok else '未通过')}")
        passed = sum(1 for check in self.checks if check.ok)
        lines.append(f"===== 结论：{passed}/{len(self.checks)} 通过" + ("（全部就绪）" if self.ok else "（存在阻塞项）") + " =====")
        return "\n".join(lines)


def docker_cli_present() -> bool:
    """Docker CLI 是否在 PATH 中。"""
    return shutil.which("docker") is not None


def docker_daemon_ok() -> bool:
    """Docker 守护进程是否响应（``docker info`` 返回 0）。"""
    if not docker_cli_present():
        return False
    try:
        result = subprocess.run(
            ["docker", "info"],
            capture_output=True,
            timeout=15,
            check=False,
        )
        return result.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def docker_available() -> bool:
    """Docker 完整可用（CLI + 守护进程）——供测试 skipif 门控与沙箱模块复用。"""
    return docker_cli_present() and docker_daemon_ok()


def _check_python() -> CheckResult:
    version = sys.version_info
    ok = version >= (3, 11)
    detail = f"Python {version.major}.{version.minor}.{version.micro}（{'满足' if ok else '不满足'} >=3.11）"
    return CheckResult(name="python", ok=ok, detail=detail, required=True)


def _check_git() -> CheckResult:
    if shutil.which("git") is None:
        return CheckResult(name="git", ok=False, detail="git 未安装（工具模式依赖）", required=True)
    try:
        result = subprocess.run(["git", "--version"], capture_output=True, timeout=10, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        return CheckResult(name="git", ok=False, detail=f"git 执行失败：{exc}", required=True)
    version = (result.stdout or result.stderr).decode("utf-8", errors="replace").strip()
    return CheckResult(name="git", ok=result.returncode == 0, detail=version or "git 可用", required=True)


def _check_docker(skip: bool) -> CheckResult:
    if skip:
        return CheckResult(name="docker", ok=True, detail="已跳过（--skip-docker-check）", required=False)
    if not docker_cli_present():
        return CheckResult(
            name="docker",
            ok=False,
            detail=(
                "Docker 未安装（v0.4 沙箱硬依赖）。安装指引：Windows 安装 Docker Desktop "
                "https://docs.docker.com/desktop/install/windows/ ，安装后启动 Docker Desktop 并确认 "
                "`docker info` 可用；或使用 --skip-docker-check 跳过本检查。"
            ),
            required=True,
        )
    if not docker_daemon_ok():
        return CheckResult(
            name="docker",
            ok=False,
            detail="Docker CLI 存在但守护进程不可用：请启动 Docker Desktop 后重试，或 --skip-docker-check 跳过。",
            required=True,
        )
    return CheckResult(name="docker", ok=True, detail="Docker 可用", required=True)


def _check_model(model_name: str | None) -> CheckResult:
    """模型配置可构造客户端（信息性：缺 key/无法解析仅提示，不阻断）。"""
    if not model_name:
        return CheckResult(name="model", ok=True, detail="未指定模型（默认 deterministic）", required=False)
    from agent_cluster.models import AgentConfig, ModelConfig
    from agent_cluster.runtime import ChatModelFactory

    try:
        ChatModelFactory().create(AgentConfig(model=ModelConfig(model_name=model_name)))
    except Exception as exc:  # noqa: BLE001 —— 预检信息性输出
        return CheckResult(
            name="model",
            ok=False,
            detail=f"模型 {model_name} 配置无效：{exc}（可回落 deterministic）",
            required=False,
        )
    return CheckResult(name="model", ok=True, detail=f"模型 {model_name} 可构造客户端", required=False)


def _check_workspace(path: str | None) -> CheckResult:
    """工作区目录可写（信息性）。"""
    if not path:
        return CheckResult(name="workspace", ok=True, detail="未指定工作区", required=False)
    target = Path(path).expanduser().resolve()
    if target.exists():
        ok = target.is_dir() and (target.stat().st_mode & 0o200 != 0)
        detail = f"{target}（{'可写' if ok else '不可写' if target.is_dir() else '不是目录'}）"
    else:
        # 目标不存在：向上找最近的已存在祖先判断可写性（嵌套路径可自动创建）
        ancestor = target
        while not ancestor.exists() and ancestor != ancestor.parent:
            ancestor = ancestor.parent
        ok = ancestor.exists() and ancestor.is_dir() and (ancestor.stat().st_mode & 0o200 != 0)
        detail = f"{target}（不存在，最近祖先 {ancestor} {'可写' if ok else '不可写'}，将自动创建）"
    return CheckResult(name="workspace", ok=ok, detail=detail, required=False)


def _check_plugin_dirs(dirs: Sequence[str]) -> CheckResult:
    """插件目录存在性检查（信息性）。"""
    if not dirs:
        return CheckResult(name="plugins", ok=True, detail="未指定插件目录", required=False)
    details: list[str] = []
    all_ok = True
    for raw in dirs:
        path = Path(raw).expanduser()
        if not path.exists() or not path.is_dir():
            all_ok = False
            details.append(f"{raw}（不存在）")
            continue
        markers = [name for name in (".codex-plugin", ".claude-plugin", "plugin.json", "marketplace.json", "SKILL.md") if (path / name).exists()]
        details.append(f"{raw}（{'含 ' + ', '.join(markers) if markers else '目录存在，未识别到清单'}）")
    return CheckResult(
        name="plugins",
        ok=all_ok,
        detail="；".join(details),
        required=False,
    )


def _check_mcp(specs: Sequence[str]) -> CheckResult:
    """MCP 服务器参数可解析（信息性）。"""
    if not specs:
        return CheckResult(name="mcp", ok=True, detail="未指定 MCP 服务器", required=False)
    from agent_cluster.mcp_client import MCPError, parse_server_command

    details: list[str] = []
    all_ok = True
    for spec in specs:
        try:
            name, argv = parse_server_command(spec)
            details.append(f"{name} -> {' '.join(argv)}")
        except MCPError as exc:
            all_ok = False
            details.append(f"{spec}（{exc}）")
    return CheckResult(name="mcp", ok=all_ok, detail="；".join(details), required=False)


def run_doctor(
    *,
    model: str | None = None,
    workspace: str | None = None,
    plugin_dirs: Sequence[str] | None = None,
    mcp_servers: Sequence[str] | None = None,
    skip_docker_check: bool = False,
) -> DoctorReport:
    """执行全部预检并返回报告（按固定顺序 python/git/docker/model/workspace/plugins/mcp）。"""
    report = DoctorReport()
    report.checks.append(_check_python())
    report.checks.append(_check_git())
    report.checks.append(_check_docker(skip_docker_check))
    report.checks.append(_check_model(model))
    report.checks.append(_check_workspace(workspace))
    report.checks.append(_check_plugin_dirs(plugin_dirs or []))
    report.checks.append(_check_mcp(mcp_servers or []))
    return report
