"""工具与执行层（v0.2，设计文档 §5.1 工具层）：模型注册表 + 受限工作区执行会话。

组件：
- ``ToolPermission``：权限分层（read / workspace_write / dangerous）。
- ``ToolSpec``：工具描述（name/description/JSON Schema/permission/handler）。
- ``ToolCall`` / ``ToolResult``：一次工具调用的请求与结果（含 needs_approval）。
- ``ToolRegistry``：工具注册 / 解析；``build_default_tools()`` 注册内置工具。
- ``ToolSession``：绑定工作区根目录的受限执行会话——统一执行入口
  （越界路径拦截 + 权限判定 + 写操作 asyncio.Lock 串行 + 审计事件 +
  结果缓存用于中断恢复后的幂等重放）。

内置工具（``build_default_tools()``）：
- 只读：``list_dir`` / ``read_file`` / ``grep`` / ``glob`` / ``git_status`` / ``git_diff``。
- 写工作区（自动执行 + 审计）：``write_file`` / ``edit_file``（apply_text_edits
  多 hunk）/ ``apply_patch``（codex 风格补丁块）/ ``mkdir`` / ``git_add`` /
  ``git_commit`` / ``git_revert`` / ``run_tests``（默认 ``uv run pytest -q``，
  shell 白名单最小集）。
- 危险（需人工审批，``--yes`` 自动拒绝）：``run_shell``（非白名单）/
  ``run_python`` / ``delete_file`` / ``git_push`` / ``http_fetch``（出网抓取）。

安全约束（§6.5 与 v0.2 决策）：
- 所有路径先 ``resolve()``（跟随符号链接）再校验位于工作区内，越界即拒绝。
- ``workspace_write`` / ``dangerous(已批准)`` 经 ``asyncio.Lock`` 串行执行，
  保证 parallel 并行节点共享同一工作区时的写安全。
- ``run_shell`` / ``run_tests`` 走白名单匹配（默认测试/构建命令前缀），
  其余 shell 一律按危险工具审批。
- 每次执行写审计事件（时间/权限/参数摘要/成败），供 metrics 与回放。
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shlex
import subprocess
import sys
import time
import uuid
import urllib.parse
import urllib.request
from collections.abc import Awaitable, Callable
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from agent_cluster.tokens import estimate_tokens

__all__ = [
    "ToolPermission",
    "ToolCall",
    "ToolResult",
    "ToolSpec",
    "ToolHandler",
    "ToolRegistry",
    "ToolError",
    "ToolSession",
    "build_default_tools",
    "apply_text_edits",
    "apply_patch_text",
    "result_ok",
    "DEFAULT_SHELL_WHITELIST",
    "load_agents_md",
    "MCP_TOOL_PREFIX",
]

# 危险工具（非白名单 shell）在无人值守（--yes）下自动拒绝，永不自动执行
MCP_TOOL_PREFIX = "mcp_"

# 默认 shell 白名单：测试/构建命令前缀（v0.2 最小集，可配置扩展）
DEFAULT_SHELL_WHITELIST: tuple[str, ...] = (
    "uv run pytest",
    "uv run python -m pytest",
    "pytest",
    "npm test",
    "npm run test",
    "npm run build",
    "npm run lint",
    "uv run python",
    "python -m pytest",
    "docker compose config",
    "docker-compose config",
)

# 单次工具输出截断上限（防止模型上下文被刷爆）
MAX_OUTPUT_CHARS = 20000
# 单文件读取上限
MAX_READ_BYTES = 512 * 1024
# 目录列举上限
MAX_DIR_ENTRIES = 200
# grep 命中上限
MAX_GREP_MATCHES = 100
# 子进程默认超时（秒）
DEFAULT_TIMEOUT = 300
# http_fetch：只读网络工具默认值（DANGEROUS，出网需人工审批）
HTTP_FETCH_DEFAULT_TIMEOUT = 15.0
HTTP_FETCH_DEFAULT_MAX_BYTES = 200_000
# AGENTS.md 项目记忆注入上限（防止上下文被刷爆）
MAX_AGENTS_MD_CHARS = 20000


class ToolPermission(StrEnum):
    """工具权限分层（v0.2：只读自动 / 写工作区自动+审计 / 危险走审批门；
    v0.3 新增 human_interaction：PM 需求澄清向人工提问并等待自由文本）。"""

    READ = "read"
    WORKSPACE_WRITE = "workspace_write"
    DANGEROUS = "dangerous"
    HUMAN_INTERACTION = "human_interaction"


class ToolError(Exception):
    """工具执行错误（越界、非法参数、命令失败等），消息面向模型与用户。"""


class ToolCall(BaseModel):
    """一次工具调用请求。"""

    model_config = ConfigDict(extra="ignore")

    id: str = Field(default_factory=lambda: uuid.uuid4().hex, description="调用唯一标识")
    name: str = Field(description="工具名")
    args: dict = Field(default_factory=dict, description="调用参数")


class ToolResult(BaseModel):
    """一次工具调用结果。"""

    model_config = ConfigDict(extra="ignore")

    id: str = Field(description="对应 ToolCall.id")
    name: str = Field(description="工具名")
    ok: bool = Field(description="是否成功")
    output: str = Field(default="", description="输出文本（面向模型）")
    duration: float = Field(default=0.0, description="耗时（秒）")
    needs_approval: bool = Field(default=False, description="是否需人工审批（危险工具）")
    error: str | None = Field(default=None, description="异常摘要，None 表示无异常")
    args: dict = Field(default_factory=dict, description="参数摘要（审计用）")


class ToolSpec(BaseModel):
    """工具描述（name/description/JSON Schema/permission/handler）。"""

    model_config = ConfigDict(extra="ignore")

    name: str = Field(description="工具名（唯一）")
    description: str = Field(description="工具描述（面向模型）")
    permission: ToolPermission = Field(description="权限分层")
    parameters: dict = Field(default_factory=dict, description="参数 JSON Schema")
    handler: Any = Field(default=None, description="异步 handler(session, args) -> dict")
    mcp_server: str | None = Field(default=None, description="来源 MCP 服务器名，None 表示内置工具")
    replayable: bool = Field(
        default=False,
        description="是否可重放缓存（幂等文件/git 操作；run_tests/run_shell 等副作用工具禁止）",
    )


class ToolRegistry:
    """工具注册表：注册 / 按名解析 / 列表。"""

    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> ToolSpec:
        """注册工具；同名重复注册抛 ToolError。"""
        if spec.name in self._tools:
            raise ToolError(f"工具已注册（名称去重）：{spec.name}")
        self._tools[spec.name] = spec
        return spec

    def get(self, name: str) -> ToolSpec:
        """按名解析工具；未注册抛 ToolError。"""
        spec = self._tools.get(name)
        if spec is None:
            raise ToolError(f"未知工具：{name!r}")
        return spec

    def list(self) -> list[ToolSpec]:
        """按名称排序返回全部工具。"""
        return [self._tools[name] for name in sorted(self._tools)]

    def names(self) -> list[str]:
        """返回全部工具名（排序）。"""
        return sorted(self._tools)

    def as_openai_schemas(self, names: list[str] | None = None) -> list[dict]:
        """把工具转成 OpenAI function calling 的 ``tools`` 参数格式。"""
        result: list[dict] = []
        for spec in self.list():
            if names is not None and spec.name not in names:
                continue
            result.append(
                {
                    "type": "function",
                    "function": {
                        "name": spec.name,
                        "description": spec.description,
                        "parameters": spec.parameters or {"type": "object", "properties": {}},
                    },
                }
            )
        return result



# ---------------------------------------------------------------------------
# 工具执行会话
# ---------------------------------------------------------------------------


def _resolve_within(workspace: Path, raw_path: str) -> Path:
    """把路径解析到工作区内绝对路径；越界（含符号链接外逃）抛 ToolError。

    - 相对路径以工作区为基准；绝对路径直接解析。
    - ``resolve()`` 跟随符号链接，可识别链接外逃。
    """
    if not raw_path:
        raise ToolError("路径不能为空")
    candidate = Path(raw_path)
    if not candidate.is_absolute():
        candidate = workspace / candidate
    try:
        resolved = candidate.resolve()
    except OSError as exc:
        raise ToolError(f"路径解析失败：{raw_path}（{exc}）") from exc
    workspace_resolved = workspace.resolve()
    try:
        inside = resolved.is_relative_to(workspace_resolved)
    except ValueError:
        inside = False
    if not inside:
        raise ToolError(f"路径越界：{raw_path!r} 解析为 {resolved}，不在工作区内")
    return resolved


def _relative(workspace: Path, path: Path) -> str:
    """返回相对工作区的正斜杠路径（展示/记录用）。"""
    try:
        return path.relative_to(workspace.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def load_agents_md(workspace: str | Path) -> str:
    """读取工作区 AGENTS.md 项目记忆（工具模式注入 system 上下文）。

    - 仅读取 ``<workspace>/AGENTS.md``；文件不存在或读取失败返回空串（跳过）。
    - 超过 ``MAX_AGENTS_MD_CHARS`` 截断并附注，防止上下文被刷爆。
    """
    agents_md = Path(workspace).expanduser() / "AGENTS.md"
    if not agents_md.is_file():
        return ""
    try:
        text = agents_md.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    if len(text) > MAX_AGENTS_MD_CHARS:
        text = text[:MAX_AGENTS_MD_CHARS] + "\n...(AGENTS.md 内容过长已截断)"
    return text


def _run_subprocess(
    cmd: list[str],
    *,
    cwd: Path,
    timeout: int,
    env_extra: dict[str, str] | None = None,
) -> dict[str, Any]:
    """运行子进程并捕获输出；返回 {ok, output, exit_code}。

    - 显式 UTF-8 编码（``PYTHONUTF8=1``/``PYTHONIOENCODING=utf-8``）保证中文输出稳定。
    - Windows 下 ``CREATE_NO_WINDOW`` 避免弹出控制台窗口。
    """
    env = dict(os.environ)
    env.update({"PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"})
    if env_extra:
        env.update(env_extra)
    creationflags = 0
    if os.name == "nt":
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(cwd),
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            creationflags=creationflags,
        )
    except subprocess.TimeoutExpired as exc:
        return {"ok": False, "output": f"命令超时（>{timeout}s）：{' '.join(cmd)}", "exit_code": -1}
    except OSError as exc:
        return {"ok": False, "output": f"命令启动失败：{exc}", "exit_code": -2}
    combined = (proc.stdout or "") + (proc.stderr or "")
    return {"ok": proc.returncode == 0, "output": combined or "(无输出)", "exit_code": proc.returncode}


class ToolSession:
    """受限工作区工具执行会话。

    - ``execute(call, approved=False)`` 统一入口：解析 → 权限判定 →（写锁）执行 →
      审计。危险工具未批准时返回 ``needs_approval=True`` 且不执行。
    - 写操作（workspace_write / 已批准 dangerous）经 ``asyncio.Lock`` 串行。
    - 结果缓存（参数指纹）：中断恢复后 ReAct 循环重放，完全相同的历史成功调用
      直接返回缓存结果（幂等），避免 git_commit 等命令重复执行报错。
    """

    def __init__(
        self,
        workspace_root: str | Path,
        *,
        registry: ToolRegistry | None = None,
        shell_whitelist: tuple[str, ...] | None = None,
        enable_replay_cache: bool = True,
        sandbox: Any | None = None,
        agents_md: str | None = None,
    ) -> None:
        root = Path(workspace_root).expanduser()
        self.workspace_root = root.resolve()
        self.workspace_root.mkdir(parents=True, exist_ok=True)
        self.registry = registry if registry is not None else build_default_tools()
        self.shell_whitelist: tuple[str, ...] = tuple(shell_whitelist or DEFAULT_SHELL_WHITELIST)
        self.sandbox: Any = sandbox
        # AGENTS.md 项目记忆（工具模式注入 system 上下文；无文件时为 None）
        self.agents_md: str | None = agents_md
        self._write_lock = asyncio.Lock()
        self.audit: list[dict[str, Any]] = []
        self._replay: dict[str, ToolResult] = {}
        self._replay_enabled = enable_replay_cache
        # 危险工具审批决策缓存（fingerprint -> accept/reject）：
        # interrupt 恢复后节点整体重跑，同一危险调用再次请求时直接套用上次决策，
        # 避免「挂起-恢复-再挂起」死循环。
        self.approval_cache: dict[str, str] = {}
        self.cwd: Path = self.workspace_root

    # ------------------------------------------------------------------
    # 统一执行入口
    # ------------------------------------------------------------------

    async def execute(self, call: ToolCall, *, approved: bool = False) -> ToolResult:
        """执行一次工具调用（含权限判定 / 越界拦截 / 审计 / 缓存）。"""
        start = time.monotonic()
        try:
            spec = self.registry.get(call.name)
        except ToolError as exc:
            return self._fail(call, str(exc), start, needs_approval=False)
        if spec.permission in (ToolPermission.DANGEROUS, ToolPermission.HUMAN_INTERACTION) and not approved:
            label = "危险工具" if spec.permission == ToolPermission.DANGEROUS else "人工交互"
            return self._fail(
                call,
                f"{label} {call.name} 需要人工介入（v0.2：危险工具 --yes 自动拒绝；v0.3：ask_user 自由文本）。",
                start,
                needs_approval=True,
            )
        # 重放缓存仅用于可变更操作（写/危险）：中断恢复后幂等重放；
        # 只读调用始终实时执行，避免编辑后被缓存的旧读结果污染。
        fingerprint = self._fingerprint(call)
        if (
            spec.replayable
            and self._replay_enabled
            and fingerprint in self._replay
            and result_ok(self._replay[fingerprint])
        ):
            return self._replay[fingerprint].model_copy(deep=True)
        try:
            if spec.permission == ToolPermission.READ:
                result = await self._dispatch(spec, call)
            else:
                async with self._write_lock:
                    result = await self._dispatch(spec, call)
        except ToolError as exc:
            result = self._fail(call, str(exc), start, needs_approval=False)
        except Exception as exc:  # noqa: BLE001 —— 统一转成 ToolResult，不向流程抛异常
            result = self._fail(call, f"工具执行异常：{type(exc).__name__}: {exc}", start, needs_approval=False)
        result.duration = time.monotonic() - start
        self._record_audit(spec, call, result)
        if result.ok and self._replay_enabled and spec.replayable:
            self._replay[self._fingerprint(call)] = result.model_copy(deep=True)
        return result

    async def execute_approved(self, call: ToolCall) -> ToolResult:
        """人工批准后的危险工具执行（调用方先拿到 needs_approval 结果再批准）。"""
        return await self.execute(call, approved=True)

    # ------------------------------------------------------------------
    # 内部实现
    # ------------------------------------------------------------------

    async def _dispatch(self, spec: ToolSpec, call: ToolCall) -> ToolResult:
        handler: Any = spec.handler
        if handler is None:
            raise ToolError(f"工具 {spec.name} 未绑定 handler（可能来自 MCP 注册错误）")
        outcome = await handler(self, call.args)
        if not isinstance(outcome, dict):
            raise ToolError(f"工具 {spec.name} 的 handler 必须返回 dict，实际 {type(outcome).__name__}")
        ok = bool(outcome.get("ok", True))
        output = str(outcome.get("output", "") or "")
        error = outcome.get("error")
        return ToolResult(
            id=call.id,
            name=call.name,
            ok=ok,
            output=output,
            error=str(error) if error else (None if ok else output[:2000]),
            args=dict(call.args),
        )

    def _fail(self, call: ToolCall, message: str, start: float, *, needs_approval: bool) -> ToolResult:
        return ToolResult(
            id=call.id,
            name=call.name,
            ok=False,
            output=message,
            duration=time.monotonic() - start,
            needs_approval=needs_approval,
            error=message,
            args=dict(call.args),
        )

    @staticmethod
    def _fingerprint(call: ToolCall) -> str:
        """参数指纹：工具名 + 稳定序列化的参数。"""
        try:
            args_text = json.dumps(call.args, sort_keys=True, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            args_text = repr(sorted(call.args.items()))
        return f"{call.name}|{args_text}"

    def _record_audit(self, spec: ToolSpec, call: ToolCall, result: ToolResult) -> None:
        """审计事件：工具名/权限/参数摘要/成败/耗时。"""
        safe_args = dict(call.args)
        for key in ("content", "code", "command"):
            if key in safe_args and isinstance(safe_args[key], str) and len(safe_args[key]) > 200:
                safe_args[key] = safe_args[key][:200] + "...(截断)"
        self.audit.append(
            {
                "tool": call.name,
                "permission": spec.permission.value,
                "args": safe_args,
                "ok": result.ok,
                "duration": round(result.duration, 3),
                "ts": time.time(),
            }
        )

    def approval_fingerprint(self, call: ToolCall) -> str:
        """危险工具审批缓存键（与结果缓存同指纹）。"""
        return self._fingerprint(call)

    def cached_approval(self, call: ToolCall) -> str | None:
        """返回已缓存的审批决策（accept/reject），无则 None。"""
        return self.approval_cache.get(self.approval_fingerprint(call))

    def remember_approval(self, call: ToolCall, decision: str) -> None:
        """记录审批决策（accept/reject），供节点重跑时幂等套用。"""
        self.approval_cache[self.approval_fingerprint(call)] = decision

    def replay_count(self) -> int:
        """返回缓存条目数（测试/度量用）。"""
        return len(self._replay)

    def clear_replay(self) -> None:
        """清空结果缓存（新节点或新运行开始）。"""
        self._replay.clear()


ToolHandler = Callable[["ToolSession", dict[str, Any]], Awaitable[dict[str, Any]]]


def result_ok(result: ToolResult) -> bool:
    """工具结果是否成功（缓存命中判据）。"""
    return result.ok



# ---------------------------------------------------------------------------
# 内置工具实现
# ---------------------------------------------------------------------------


def _schema(properties: dict, required: list[str] | None = None) -> dict:
    """构造 JSON Schema 参数定义。"""
    return {"type": "object", "properties": properties, "required": required or []}


async def _tool_list_dir(session: ToolSession, args: dict) -> dict[str, Any]:
    path = _resolve_within(session.workspace_root, str(args.get("path", ".")))
    if not path.is_dir():
        raise ToolError(f"目录不存在：{_relative(session.workspace_root, path)}")
    entries = sorted(path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
    lines: list[str] = []
    for entry in entries[:MAX_DIR_ENTRIES]:
        suffix = "/" if entry.is_dir() else ""
        lines.append(f"{entry.name}{suffix}")
    if len(entries) > MAX_DIR_ENTRIES:
        lines.append(f"...(共 {len(entries)} 项，仅显示前 {MAX_DIR_ENTRIES})")
    return {"ok": True, "output": "\n".join(lines) or "(空目录)"}


async def _tool_read_file(session: ToolSession, args: dict) -> dict[str, Any]:
    path = _resolve_within(session.workspace_root, str(args.get("path", "")))
    if not path.is_file():
        raise ToolError(f"文件不存在：{_relative(session.workspace_root, path)}")
    stat = path.stat()
    if stat.st_size > MAX_READ_BYTES:
        raise ToolError(f"文件过大（{stat.st_size} 字节 > {MAX_READ_BYTES}），请用 grep/offset 分段读取")
    text = path.read_text(encoding="utf-8", errors="replace")
    offset = int(args.get("offset") or 1)
    limit = int(args.get("limit") or 500)
    if offset < 1:
        raise ToolError("offset 从 1 开始")
    lines = text.splitlines()
    selected = lines[offset - 1 : offset - 1 + limit]
    head = "\n".join(selected)
    prefix = f"文件：{_relative(session.workspace_root, path)}（共 {len(lines)} 行，显示 {offset}-{offset + len(selected) - 1}）"
    return {"ok": True, "output": f"{prefix}\n{head}"}


async def _tool_grep(session: ToolSession, args: dict) -> dict[str, Any]:
    pattern = str(args.get("pattern", ""))
    if not pattern:
        raise ToolError("grep 需要 pattern 参数")
    root = _resolve_within(session.workspace_root, str(args.get("path", ".")))
    if not root.is_dir():
        raise ToolError(f"目录不存在：{_relative(session.workspace_root, root)}")
    try:
        regex = re.compile(pattern)
    except re.error as exc:
        raise ToolError(f"非法正则：{exc}") from exc
    skip_dirs = {".git", "node_modules", "__pycache__", ".venv", ".pytest_cache", ".mypy_cache", ".ruff_cache", "dist", "build"}
    matches: list[str] = []
    for path in root.rglob("*"):
        if path.is_dir():
            continue
        try:
            rel = path.relative_to(root)
            if any(part in skip_dirs for part in rel.parts):
                continue
            if path.stat().st_size > MAX_READ_BYTES:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line_no, line in enumerate(text.splitlines(), start=1):
            if regex.search(line):
                matches.append(f"{_relative(session.workspace_root, path)}:{line_no}: {line.strip()[:200]}")
                if len(matches) >= MAX_GREP_MATCHES:
                    break
        if len(matches) >= MAX_GREP_MATCHES:
            break
    if not matches:
        return {"ok": True, "output": f"未匹配到 {pattern!r}"}
    return {"ok": True, "output": "\n".join(matches) + f"\n(共 {len(matches)} 条)"}


async def _tool_glob(session: ToolSession, args: dict) -> dict[str, Any]:
    pattern = str(args.get("pattern", ""))
    if not pattern:
        raise ToolError("glob 需要 pattern 参数")
    root = _resolve_within(session.workspace_root, str(args.get("path", ".")))
    matched = sorted(str(p.relative_to(root)).replace("\\", "/") for p in root.glob(pattern) if p.is_file())
    if not matched:
        return {"ok": True, "output": f"未匹配到 {pattern!r}"}
    return {"ok": True, "output": "\n".join(matched[:200]) + (f"\n(共 {len(matched)} 个)" if len(matched) > 200 else "")}


async def _git_run(session: ToolSession, cmd: list[str]) -> dict[str, Any]:
    return _run_subprocess(cmd, cwd=session.workspace_root, timeout=DEFAULT_TIMEOUT)


async def _tool_git_init(session: ToolSession, args: dict) -> dict[str, Any]:
    """git init（工作区写）：初始化仓库并设置本地 user 配置（提交必需）。"""
    init = await _git_run(session, ["git", "init", "-q"])
    if init["exit_code"] != 0:
        return {"ok": False, "output": init["output"], "error": "git init 失败"}
    messages: list[str] = ["已初始化 git 仓库（git init）"]
    for key, value in (("user.email", "agent-cluster@local"), ("user.name", "agent-cluster")):
        cfg = await _git_run(session, ["git", "config", key, value])
        if cfg["exit_code"] == 0:
            messages.append(f"已设置本地 {key}")
    return {"ok": True, "output": "\n".join(messages)}


async def _tool_git_status(session: ToolSession, args: dict) -> dict[str, Any]:
    result = await _git_run(session, ["git", "status", "--short"])
    if result["exit_code"] != 0:
        return {"ok": True, "output": "（当前目录不是 git 仓库或无改动）"}
    return {"ok": True, "output": result["output"] or "(工作区干净)"}


async def _tool_git_diff(session: ToolSession, args: dict) -> dict[str, Any]:
    cmd = ["git", "diff", "--cached"] if args.get("staged") else ["git", "diff"]
    result = await _git_run(session, cmd)
    output = result["output"]
    if len(output) > MAX_OUTPUT_CHARS:
        output = output[:MAX_OUTPUT_CHARS] + "\n...(输出截断)"
    return {"ok": True, "output": output or "(无差异)"}


async def _tool_write_file(session: ToolSession, args: dict) -> dict[str, Any]:
    path = _resolve_within(session.workspace_root, str(args.get("path", "")))
    content = str(args.get("content", ""))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return {"ok": True, "output": f"已写入 {_relative(session.workspace_root, path)}（{len(content)} 字符）"}


async def _tool_mkdir(session: ToolSession, args: dict) -> dict[str, Any]:
    path = _resolve_within(session.workspace_root, str(args.get("path", "")))
    path.mkdir(parents=True, exist_ok=True)
    return {"ok": True, "output": f"已创建目录 {_relative(session.workspace_root, path)}"}


def apply_text_edits(text: str, edits: list[dict]) -> str:
    """apply_text_edits 多 hunk：顺序替换首次出现的 ``old`` 为 ``new``。

    - 每个 edit 必须含非空 ``old``；``new`` 缺省为空串。
    - ``count`` 可选：替换前 N 次出现（缺省 1）。
    - 任一 edit 未找到 ``old`` 即抛 ToolError，且不产生部分修改
      （先整体校验再应用，保证原子性）。
    """
    if not edits:
        raise ToolError("edit_file 需要至少一个 edit（{old, new}）")
    for index, edit in enumerate(edits):
        old = edit.get("old", "")
        if not isinstance(old, str) or not old:
            raise ToolError(f"edit[{index}] 的 old 必须为非空字符串")
        if "new" in edit and not isinstance(edit["new"], str):
            raise ToolError(f"edit[{index}] 的 new 必须是字符串")
    for index, edit in enumerate(edits):
        if text.count(edit["old"]) == 0:
            raise ToolError(f"edit[{index}] 未找到 old 文本：{edit['old'][:80]!r}")
    result = text
    for index, edit in enumerate(edits):
        old = edit["old"]
        new = edit.get("new", "")
        count = int(edit.get("count") or 1)
        occurrences = result.count(old)
        if count > occurrences:
            raise ToolError(f"edit[{index}] 需要替换 {count} 次，但 old 仅出现 {occurrences} 次")
        result = result.replace(old, new, count)
    return result


# codex 风格补丁块标记
PATCH_BEGIN_MARKER = "*** Begin Patch"
PATCH_END_MARKER = "*** End Patch"
PATCH_FILE_MARKER = "*** Update File:"


def _parse_patch_blocks(
    patch_text: str,
) -> list[tuple[str, list[tuple[str | None, list[tuple[str, str]]]]]]:
    """解析 codex 风格补丁块为 [(文件标题, [(@@头部, [(符号, 内容)])])]。

    - 支持 ``*** Begin Patch`` / ``*** Update File: <rel>`` / ``@@`` 多 hunk /
      ``-`` 删除行 / ``+`` 新增行 / 空格上下文行 / ``*** End Patch``。
    - 格式错误（缺标记/非法行）抛 ToolError；空行与 ``@@`` 行作为 hunk 分隔。
    """
    lines = patch_text.splitlines()
    begin = end = None
    for index, line in enumerate(lines):
        if line.strip() == PATCH_BEGIN_MARKER:
            begin = index
        elif line.strip() == PATCH_END_MARKER:
            end = index
    if begin is None:
        raise ToolError("apply_patch 补丁缺少 *** Begin Patch 标记")
    if end is None:
        raise ToolError("apply_patch 补丁缺少 *** End Patch 标记")
    if end <= begin:
        raise ToolError("apply_patch 补丁格式错误：*** End Patch 必须在 *** Begin Patch 之后")

    blocks: list[tuple[str, list[tuple[str | None, list[tuple[str, str]]]]]] = []
    current_file = ""
    hunks: list[tuple[str | None, list[tuple[str, str]]]] = []
    current: list[tuple[str, str]] = []
    header: str | None = None
    for line in lines[begin + 1 : end]:
        stripped = line.strip()
        if stripped.startswith(PATCH_FILE_MARKER):
            if current_file or hunks or current:
                blocks.append((current_file, hunks + ([(header, current)] if current else [])))
            current_file = stripped[len(PATCH_FILE_MARKER) :].strip()
            hunks, current, header = [], [], None
            continue
        if stripped.startswith("@@"):
            if current:
                hunks.append((header, current))
                current = []
            header = stripped
            continue
        if not stripped:
            if current:
                hunks.append((header, current))
                current = []
            header = None
            continue
        if line.startswith(("-", "+", " ")):
            current.append((line[0], line[1:]))
            continue
        raise ToolError(f"apply_patch 非法补丁行：{line!r}")
    if current_file or hunks or current:
        blocks.append((current_file, hunks + ([(header, current)] if current else [])))
    return blocks


def _find_sequence(lines: list[str], sequence: list[str]) -> int | None:
    """在行列表中定位子序列的首次出现位置；找不到返回 None。"""
    if not sequence:
        return 0
    first = sequence[0]
    limit = len(lines) - len(sequence) + 1
    for index in range(limit):
        if lines[index] != first:
            continue
        if lines[index : index + len(sequence)] == sequence:
            return index
    return None


def _patch_mismatch_message(
    file_title: str, hunk_index: int, header: str | None, old_seq: list[str], lines: list[str]
) -> str:
    """构造 hunk 未匹配的错误消息（含定位信息：hunk 序号/@@ 头/首锚行与近似位置）。"""
    anchor = old_seq[0]
    located = ""
    for index, line in enumerate(lines, start=1):
        if line == anchor:
            located = f"；锚点行在文件第 {index} 行单独出现过，但前后文不匹配"
            break
    head = header or f"hunk {hunk_index}"
    return (
        f"apply_patch 补丁未匹配：文件 {file_title} 的 {head} 找不到 "
        f"删除行 {anchor!r}{located}"
    )


def apply_patch_text(text: str, patch_text: str) -> str:
    """应用 codex 风格补丁块（apply_patch）：多文件块、每块多 hunk。

    - hunk 匹配规则：``-`` 删除行与空格上下文行按顺序构成匹配序列，``+``
      新增行替换对应位置；逐 hunk 顺序在文本中定位首次出现。
    - 任一 hunk 未匹配（或纯新增无锚点）抛 ToolError 并给出定位信息；
      先整体校验再应用，保证原子性（不产生部分修改）。
    - 返回应用后的完整文本。
    """
    blocks = _parse_patch_blocks(patch_text)
    if not blocks:
        raise ToolError("apply_patch 补丁为空（缺少 *** Update File 块）")
    for file_title, _hunks in blocks:
        if not file_title:
            raise ToolError("apply_patch 补丁块缺少 *** Update File: <相对路径> 行")
    # 校验阶段：在副本上模拟全部 hunk，未匹配立即报错（不产生部分修改）
    probe = text.splitlines()
    for file_title, hunks in blocks:
        for hunk_index, (header, hunk) in enumerate(hunks, start=1):
            old_seq = [content for sign, content in hunk if sign in ("-", " ")]
            new_seq = [content for sign, content in hunk if sign in ("+", " ")]
            if not old_seq:
                raise ToolError(
                    f"apply_patch hunk {hunk_index}（{file_title}）只有新增行，缺少可匹配的删除/上下文行"
                )
            position = _find_sequence(probe, old_seq)
            if position is None:
                raise ToolError(_patch_mismatch_message(file_title, hunk_index, header, old_seq, probe))
            probe = probe[:position] + new_seq + probe[position + len(old_seq) :]
    # 应用阶段：与校验阶段结果一致，顺序应用
    result_lines = text.splitlines()
    for file_title, hunks in blocks:
        for _hunk_index, (_header, hunk) in enumerate(hunks, start=1):
            old_seq = [content for sign, content in hunk if sign in ("-", " ")]
            new_seq = [content for sign, content in hunk if sign in ("+", " ")]
            position = _find_sequence(result_lines, old_seq)
            result_lines = result_lines[:position] + new_seq + result_lines[position + len(old_seq) :]
    result = "\n".join(result_lines)
    # splitlines() 会丢弃末尾空行/换行符：原文以换行结尾时保留
    if text.endswith("\n") and not result.endswith("\n"):
        result += "\n"
    return result


def _patch_file_titles(patch_text: str) -> list[str]:
    """返回补丁引用的文件相对路径列表（工具层校验 path 一致性用）。"""
    return [title for title, _hunks in _parse_patch_blocks(patch_text)]


async def _tool_edit_file(session: ToolSession, args: dict) -> dict[str, Any]:
    path = _resolve_within(session.workspace_root, str(args.get("path", "")))
    if not path.is_file():
        raise ToolError(f"文件不存在：{_relative(session.workspace_root, path)}")
    edits = args.get("edits")
    if not isinstance(edits, list) or not edits:
        raise ToolError("edit_file 需要 edits 参数（[{old, new}, ...]）")
    original = path.read_text(encoding="utf-8", errors="replace")
    updated = apply_text_edits(original, edits)
    if updated == original:
        return {"ok": True, "output": "无需修改（edits 未改变文件内容）"}
    path.write_text(updated, encoding="utf-8")
    changed = sum(1 for edit in edits if edit.get("new", "") != edit["old"])
    return {"ok": True, "output": f"已编辑 {_relative(session.workspace_root, path)}（{len(edits)} 个 hunk，{changed} 处变更）"}


def _match_whitelist(command: str, whitelist: tuple[str, ...]) -> bool:
    """白名单匹配：命令以任一白名单前缀开头（词边界）。"""
    stripped = command.strip()
    lowered = stripped.lower()
    for prefix in whitelist:
        normalized = prefix.strip().lower()
        if lowered == normalized or lowered.startswith(normalized + " "):
            return True
    return False


async def _tool_run_tests(session: ToolSession, args: dict) -> dict[str, Any]:
    command = str(args.get("command") or "").strip()
    if not command:
        command = "uv run pytest -q"
    if not _match_whitelist(command, session.shell_whitelist):
        raise ToolError(
            f"测试命令不在白名单：{command!r}（白名单前缀：{', '.join(session.shell_whitelist)}；"
            "其他命令请使用 run_shell 并走人工审批）"
        )
    timeout = int(args.get("timeout") or DEFAULT_TIMEOUT)
    if session.sandbox is not None:
        result = await session.sandbox.run_tests(command, timeout=timeout)
    else:
        result = _run_subprocess(command.split(), cwd=session.workspace_root, timeout=timeout)
    output = result["output"]
    if len(output) > MAX_OUTPUT_CHARS:
        output = output[:MAX_OUTPUT_CHARS] + "\n...(输出截断)"
    return {"ok": result["ok"], "output": output, "error": None if result["ok"] else "测试未通过"}


async def _tool_run_shell(session: ToolSession, args: dict) -> dict[str, Any]:
    command = str(args.get("command", "")).strip()
    if not command:
        raise ToolError("run_shell 需要 command 参数")
    timeout = int(args.get("timeout") or DEFAULT_TIMEOUT)
    if session.sandbox is not None:
        result = await session.sandbox.run_shell(command, timeout=timeout)
    else:
        result = _run_subprocess(command.split(), cwd=session.workspace_root, timeout=timeout)
    output = result["output"]
    if len(output) > MAX_OUTPUT_CHARS:
        output = output[:MAX_OUTPUT_CHARS] + "\n...(输出截断)"
    return {"ok": result["ok"], "output": output, "error": None if result["ok"] else f"命令退出码 {result['exit_code']}"}



async def _tool_run_python(session: ToolSession, args: dict) -> dict[str, Any]:
    code = str(args.get("code", "")).strip()
    if not code:
        raise ToolError("run_python 需要 code 参数")
    timeout = int(args.get("timeout") or DEFAULT_TIMEOUT)
    tmp_dir = session.workspace_root / ".agent-cluster" / "tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    script = tmp_dir / f"run_{uuid.uuid4().hex[:8]}.py"
    script.write_text(code, encoding="utf-8")
    if session.sandbox is not None:
        rel = script.relative_to(session.workspace_root).as_posix()
        result = await session.sandbox.run_python(rel, timeout=timeout)
    else:
        result = _run_subprocess([sys.executable, str(script)], cwd=session.workspace_root, timeout=timeout)
    output = result["output"]
    if len(output) > MAX_OUTPUT_CHARS:
        output = output[:MAX_OUTPUT_CHARS] + "\n...(输出截断)"
    try:
        script.unlink()
    except OSError:
        pass
    return {"ok": result["ok"], "output": output, "error": None if result["ok"] else f"Python 退出码 {result['exit_code']}"}


async def _tool_run_service(session: ToolSession, args: dict) -> dict[str, Any]:
    """devops 冒烟：启动服务 → 轮询健康检查 → 关闭进程（不常驻）。

    - command：服务启动命令（如 ``python -m http.server 8000``）。
    - health：健康检查命令（如 ``curl -sf http://localhost:8000/``），成功即就绪。
    - 进程提前退出或健康检查超时视为失败；无论成败都会关闭启动的进程。
    - 危险权限：启动任意进程需人工审批（--yes 自动拒绝即跳过冒烟）。
    """
    command = str(args.get("command") or "").strip()
    health = str(args.get("health") or args.get("health_check") or "").strip()
    if not command or not health:
        raise ToolError("run_service 需要 command（启动命令）与 health（健康检查命令）参数")
    timeout = int(args.get("timeout") or DEFAULT_TIMEOUT)
    wait = max(float(args.get("wait") or 2.0), 0.1)
    max_attempts = int(args.get("attempts") or max(1, int(timeout / wait)))
    if session.sandbox is not None:
        result = await session.sandbox.run_service(
            command, health, timeout=timeout, wait=wait, max_attempts=max_attempts
        )
        output = result["output"]
        if len(output) > MAX_OUTPUT_CHARS:
            output = output[:MAX_OUTPUT_CHARS] + "\n...(输出截断)"
        return {"ok": result["ok"], "output": output, "error": result.get("error")}
    env = dict(os.environ)
    env.update({"PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"})
    creationflags = 0
    if os.name == "nt":
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    proc = None
    start = time.monotonic()
    last_output = ""
    healthy = False
    try:
        proc = subprocess.Popen(
            shlex.split(command),
            cwd=str(session.workspace_root),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=creationflags,
        )
        for attempt in range(max_attempts):
            if proc.poll() is not None:
                try:
                    last_output = (proc.stdout.read(2000) if proc.stdout else "") or "(服务进程提前退出，无输出)"
                except Exception:  # noqa: BLE001
                    last_output = "(服务进程提前退出，输出不可读)"
                break
            check = _run_subprocess(shlex.split(health), cwd=session.workspace_root, timeout=max(5, int(wait)))
            last_output = check["output"]
            if check["ok"]:
                healthy = True
                break
            await asyncio.sleep(wait)
        duration = round(time.monotonic() - start, 3)
        if healthy:
            return {
                "ok": True,
                "output": f"服务健康检查通过（{duration}s，尝试 {attempt + 1} 次）：{last_output[:500]}",
            }
        return {
            "ok": False,
            "output": (f"服务健康检查未通过（{duration}s，{max_attempts} 次尝试）：{last_output[:500]}"),
            "error": "服务未就绪或进程提前退出",
        }
    finally:
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()


async def _tool_count_tokens(session: ToolSession, args: dict) -> dict[str, Any]:
    """统计文本/文件/目录的 token 大小（交付组装与 DELIVERY.md 计量表用）。"""
    text_arg = str(args.get("text") or "")
    if text_arg:
        return {"ok": True, "output": str(estimate_tokens(text_arg)), "tokens": estimate_tokens(text_arg)}
    raw_path = str(args.get("path") or "")
    if not raw_path:
        raise ToolError("count_tokens 需要 text 或 path 参数")
    target = _resolve_within(session.workspace_root, raw_path)
    if target.is_file():
        try:
            content = target.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            raise ToolError(f"读取文件失败：{exc}") from exc
        return {
            "ok": True,
            "output": f"{_relative(session.workspace_root, target)} = {estimate_tokens(content)} tokens",
            "tokens": estimate_tokens(content),
        }
    if target.is_dir():
        total = 0
        files = 0
        for path in target.rglob("*"):
            if path.is_file():
                files += 1
                try:
                    total += estimate_tokens(path.read_text(encoding="utf-8", errors="replace"))
                except OSError:
                    continue
        return {
            "ok": True,
            "output": f"目录 {_relative(session.workspace_root, target)}：{files} 个文件，约 {total} tokens",
            "tokens": total,
        }
    raise ToolError(f"路径不存在：{_relative(session.workspace_root, target)}")


async def _tool_ask_user(session: ToolSession, args: dict) -> dict[str, Any]:
    """人工交互占位 handler：真正回答由中断恢复注入（本路径仅在已批准时兜底）。"""
    question = str(args.get("question") or "")
    return {
        "ok": True,
        "output": f"[待人工回答] {question}",
    }


async def _tool_delete_file(session: ToolSession, args: dict) -> dict[str, Any]:
    path = _resolve_within(session.workspace_root, str(args.get("path", "")))
    if path == session.workspace_root or path == session.workspace_root.resolve():
        raise ToolError("不允许删除工作区根目录")
    if path.is_file():
        path.unlink()
        return {"ok": True, "output": f"已删除文件 {_relative(session.workspace_root, path)}"}
    if path.is_dir():
        try:
            path.rmdir()
        except OSError as exc:
            raise ToolError(f"仅允许删除空目录：{exc}") from exc
        return {"ok": True, "output": f"已删除空目录 {_relative(session.workspace_root, path)}"}
    raise ToolError(f"路径不存在：{_relative(session.workspace_root, path)}")


async def _tool_apply_patch(session: ToolSession, args: dict) -> dict[str, Any]:
    """apply_patch：按 codex 风格补丁块修改文件（workspace_write）。

    - path 必须已存在且经 ``_resolve_within`` 校验；补丁 ``*** Update File``
      引用的文件必须与 path 一致（防错改其他文件）。
    - 任一 hunk 未匹配抛 ToolError（含定位信息），不产生部分修改。
    """
    path = _resolve_within(session.workspace_root, str(args.get("path", "")))
    if not path.is_file():
        raise ToolError(f"文件不存在：{_relative(session.workspace_root, path)}")
    patch_text = str(args.get("patch_text") or "")
    if not patch_text.strip():
        raise ToolError("apply_patch 需要 patch_text 参数（codex 风格补丁块）")
    for rel in _patch_file_titles(patch_text):
        try:
            resolved_ref = _resolve_within(session.workspace_root, rel)
        except ToolError as exc:
            raise ToolError(f"apply_patch 补丁文件越界：{rel!r}（{exc}）") from exc
        if resolved_ref != path:
            raise ToolError(
                f"apply_patch 补丁引用 {rel!r}，与 path 参数 {_relative(session.workspace_root, path)!r} 不一致"
            )
    original = path.read_text(encoding="utf-8", errors="replace")
    updated = apply_patch_text(original, patch_text)
    if updated == original:
        return {"ok": True, "output": "无需修改（补丁未改变文件内容）"}
    path.write_text(updated, encoding="utf-8")
    hunk_count = sum(len(hunks) for _title, hunks in _parse_patch_blocks(patch_text))
    return {"ok": True, "output": f"已应用补丁 {_relative(session.workspace_root, path)}（{hunk_count} 个 hunk）"}


async def _tool_http_fetch(session: ToolSession, args: dict) -> dict[str, Any]:
    """http_fetch：只读网络抓取（DANGEROUS：出网需人工审批，--yes 自动拒绝）。

    - stdlib ``urllib.request`` GET，默认超时 15s、默认响应上限 200_000 字节；
      超限截断并在输出标注。
    - 仅允许 http/https；网络异常统一转为失败 ToolResult。
    """
    url = str(args.get("url") or "").strip()
    if not url:
        raise ToolError("http_fetch 需要 url 参数")
    timeout = float(args.get("timeout") or HTTP_FETCH_DEFAULT_TIMEOUT)
    max_bytes = int(args.get("max_bytes") or HTTP_FETCH_DEFAULT_MAX_BYTES)
    if timeout <= 0:
        raise ToolError("timeout 必须为正数")
    if max_bytes <= 0:
        raise ToolError("max_bytes 必须为正整数")
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ToolError(f"http_fetch 仅允许 http/https URL（收到 {parsed.scheme or '无协议'}）")
    status = 0
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            status = getattr(response, "status", 200)
            data = response.read(max_bytes + 1)
    except Exception as exc:  # noqa: BLE001 —— 网络异常统一转失败 ToolResult
        message = f"HTTP 请求失败：{type(exc).__name__}: {exc}"
        return {"ok": False, "output": message, "error": message}
    truncated = len(data) > max_bytes
    body = data[:max_bytes].decode("utf-8", errors="replace")
    if len(body) > MAX_OUTPUT_CHARS:
        body = body[:MAX_OUTPUT_CHARS] + "\n...(输出截断)"
        truncated = True
    notice = f"（响应已截断至 {max_bytes} 字节）" if truncated else ""
    return {"ok": True, "output": f"HTTP {status} {url}{notice}\n{body}"}


async def _tool_git_add(session: ToolSession, args: dict) -> dict[str, Any]:
    raw = args.get("paths")
    if isinstance(raw, str):
        paths = [raw]
    elif isinstance(raw, list) and raw:
        paths = [str(item) for item in raw]
    else:
        paths = ["."]
    for item in paths:
        _resolve_within(session.workspace_root, item)
    result = await _git_run(session, ["git", "add", "--", *paths])
    if not result["ok"]:
        return {"ok": False, "output": result["output"], "error": "git add 失败"}
    return {"ok": True, "output": f"已暂存 {len(paths)} 个路径：{', '.join(paths)}"}


async def _tool_git_commit(session: ToolSession, args: dict) -> dict[str, Any]:
    message = str(args.get("message", "")).strip()
    if not message:
        raise ToolError("git_commit 需要 message 参数")
    cmd = ["git", "commit", "-m", message]
    if args.get("allow_empty"):
        cmd.append("--allow-empty")
    result = await _git_run(session, cmd)
    if not result["ok"]:
        return {"ok": False, "output": result["output"], "error": "git commit 失败（可能没有暂存改动）"}
    return {"ok": True, "output": result["output"]}


async def _tool_git_revert(session: ToolSession, args: dict) -> dict[str, Any]:
    raw = args.get("files")
    if raw:
        files = [str(item) for item in (raw if isinstance(raw, list) else [raw])]
        for item in files:
            _resolve_within(session.workspace_root, item)
        cmd = ["git", "restore", "--", *files]
    else:
        cmd = ["git", "restore", "."]
    result = await _git_run(session, cmd)
    if not result["ok"]:
        return {"ok": False, "output": result["output"], "error": "git restore 失败"}
    return {"ok": True, "output": "已还原工作区改动（git restore）"}


async def _tool_git_push(session: ToolSession, args: dict) -> dict[str, Any]:
    remote = str(args.get("remote") or "origin")
    branch = str(args.get("branch") or "").strip()
    cmd = ["git", "push", remote] + ([branch] if branch else [])
    result = await _git_run(session, cmd)
    if not result["ok"]:
        return {"ok": False, "output": result["output"], "error": "git push 失败"}
    return {"ok": True, "output": result["output"]}


def build_default_tools() -> ToolRegistry:
    """注册内置工具并返回注册表。"""
    registry = ToolRegistry()
    registry.register(ToolSpec(
        name="list_dir",
        description="列出目录条目（工作区内）。参数：path（相对工作区，缺省 .）",
        permission=ToolPermission.READ,
        parameters=_schema({"path": {"type": "string"}}),
        handler=_tool_list_dir,
    ))
    registry.register(ToolSpec(
        name="read_file",
        description="读取文本文件内容。参数：path（必填）、offset（起始行，1 起）、limit（最大行数，缺省 500）",
        permission=ToolPermission.READ,
        parameters=_schema({"path": {"type": "string"}, "offset": {"type": "integer"}, "limit": {"type": "integer"}}, required=["path"]),
        handler=_tool_read_file,
    ))
    registry.register(ToolSpec(
        name="grep",
        description="在工作区内按正则搜索文本。参数：pattern（必填）、path（目录，缺省 .）",
        permission=ToolPermission.READ,
        parameters=_schema({"pattern": {"type": "string"}, "path": {"type": "string"}}, required=["pattern"]),
        handler=_tool_grep,
    ))
    registry.register(ToolSpec(
        name="glob",
        description="按 glob 模式列出工作区文件。参数：pattern（必填）、path（基准目录，缺省 .）",
        permission=ToolPermission.READ,
        parameters=_schema({"pattern": {"type": "string"}, "path": {"type": "string"}}, required=["pattern"]),
        handler=_tool_glob,
    ))
    registry.register(ToolSpec(
        name="git_init",
        replayable=True,
        description="初始化 git 仓库（git init，并设置本地 user 配置）。新项目场景由 devops 调用",
        permission=ToolPermission.WORKSPACE_WRITE,
        parameters=_schema({}),
        handler=_tool_git_init,
    ))
    registry.register(ToolSpec(
        name="git_status",
        description="查看 git 工作区状态（git status --short）。",
        permission=ToolPermission.READ,
        parameters=_schema({}),
        handler=_tool_git_status,
    ))
    registry.register(ToolSpec(
        name="git_diff",
        description="查看未暂存差异（git diff）。参数：staged（true 查看暂存区差异）",
        permission=ToolPermission.READ,
        parameters=_schema({"staged": {"type": "boolean"}}),
        handler=_tool_git_diff,
    ))
    registry.register(ToolSpec(
        name="write_file",
        replayable=True,
        description="写入文件（UTF-8，自动创建父目录）。参数：path（必填，相对工作区）、content（必填）",
        permission=ToolPermission.WORKSPACE_WRITE,
        parameters=_schema({"path": {"type": "string"}, "content": {"type": "string"}}, required=["path", "content"]),
        handler=_tool_write_file,
    ))
    registry.register(ToolSpec(
        name="edit_file",
        replayable=True,
        description="多 hunk 文本编辑。参数：path（必填）、edits（必填，[{old, new, count?}]，old 必须存在且非空）",
        permission=ToolPermission.WORKSPACE_WRITE,
        parameters=_schema({
            "path": {"type": "string"},
            "edits": {"type": "array", "items": {"type": "object",
                "properties": {"old": {"type": "string"}, "new": {"type": "string"}, "count": {"type": "integer"}},
                "required": ["old"]}},
        }, required=["path", "edits"]),
        handler=_tool_edit_file,
    ))
    registry.register(ToolSpec(
        name="apply_patch",
        replayable=True,
        description="按 codex 风格补丁块修改文件（多 hunk）。参数：path（必填，相对工作区）、patch_text（必填：*** Begin Patch / *** Update File: <rel> / @@ / - 旧行 / + 新行 / *** End Patch；文件必须已存在）",
        permission=ToolPermission.WORKSPACE_WRITE,
        parameters=_schema({"path": {"type": "string"}, "patch_text": {"type": "string"}}, required=["path", "patch_text"]),
        handler=_tool_apply_patch,
    ))
    registry.register(ToolSpec(
        name="mkdir",
        replayable=True,
        description="创建目录（含父目录）。参数：path（必填）",
        permission=ToolPermission.WORKSPACE_WRITE,
        parameters=_schema({"path": {"type": "string"}}, required=["path"]),
        handler=_tool_mkdir,
    ))
    registry.register(ToolSpec(
        name="git_add",
        replayable=True,
        description="git add 暂存文件。参数：paths（字符串或列表，缺省 .）",
        permission=ToolPermission.WORKSPACE_WRITE,
        parameters=_schema({"paths": {"type": ["string", "array"], "items": {"type": "string"}}}),
        handler=_tool_git_add,
    ))
    registry.register(ToolSpec(
        name="git_commit",
        replayable=True,
        description="git commit 提交暂存内容。参数：message（必填）、allow_empty（可选）",
        permission=ToolPermission.WORKSPACE_WRITE,
        parameters=_schema({"message": {"type": "string"}, "allow_empty": {"type": "boolean"}}, required=["message"]),
        handler=_tool_git_commit,
    ))
    registry.register(ToolSpec(
        name="git_revert",
        replayable=True,
        description="还原工作区未提交改动（git restore）。参数：files（可选，缺省还原全部）",
        permission=ToolPermission.WORKSPACE_WRITE,
        parameters=_schema({"files": {"type": ["string", "array"], "items": {"type": "string"}}}),
        handler=_tool_git_revert,
    ))
    registry.register(ToolSpec(
        name="run_tests",
        description="运行测试命令（白名单：pytest/npm test 等）。参数：command（可选，缺省 uv run pytest -q）、timeout（秒，缺省 300）",
        permission=ToolPermission.WORKSPACE_WRITE,
        parameters=_schema({"command": {"type": "string"}, "timeout": {"type": "integer"}}),
        handler=_tool_run_tests,
    ))
    registry.register(ToolSpec(
        name="run_shell",
        description="执行任意 shell 命令（危险工具，需人工审批）。参数：command（必填）、timeout（秒）",
        permission=ToolPermission.DANGEROUS,
        parameters=_schema({"command": {"type": "string"}, "timeout": {"type": "integer"}}, required=["command"]),
        handler=_tool_run_shell,
    ))
    registry.register(ToolSpec(
        name="run_python",
        description="执行 Python 代码片段（危险工具，需人工审批；cwd=工作区）。参数：code（必填）、timeout（秒）",
        permission=ToolPermission.DANGEROUS,
        parameters=_schema({"code": {"type": "string"}, "timeout": {"type": "integer"}}, required=["code"]),
        handler=_tool_run_python,
    ))
    registry.register(ToolSpec(
        name="delete_file",
        description="删除文件或空目录（危险工具，需人工审批）。参数：path（必填）",
        permission=ToolPermission.DANGEROUS,
        parameters=_schema({"path": {"type": "string"}}, required=["path"]),
        handler=_tool_delete_file,
    ))
    registry.register(ToolSpec(
        name="git_push",
        description="git push 到远程（危险工具，需人工审批）。参数：remote（缺省 origin）、branch（可选）",
        permission=ToolPermission.DANGEROUS,
        parameters=_schema({"remote": {"type": "string"}, "branch": {"type": "string"}}),
        handler=_tool_git_push,
    ))
    registry.register(ToolSpec(
        name="run_service",
        description="本地冒烟：启动服务 → 轮询健康检查 → 关闭进程（危险工具，需人工审批；--yes 自动拒绝即跳过冒烟）。参数：command（启动命令）、health（健康检查命令）、wait（秒）、attempts、timeout",
        permission=ToolPermission.DANGEROUS,
        parameters=_schema({
            "command": {"type": "string"},
            "health": {"type": "string"},
            "wait": {"type": "number"},
            "attempts": {"type": "integer"},
            "timeout": {"type": "integer"},
        }, required=["command", "health"]),
        handler=_tool_run_service,
    ))
    registry.register(ToolSpec(
        name="count_tokens",
        description="统计文本/文件/目录的 token 大小（read 权限）。参数：text（可选）或 path（可选，相对工作区）",
        permission=ToolPermission.READ,
        parameters=_schema({"text": {"type": "string"}, "path": {"type": "string"}}),
        handler=_tool_count_tokens,
    ))
    registry.register(ToolSpec(
        name="http_fetch",
        description="抓取 http/https URL 文本内容（只读网络工具；DANGEROUS：出网需人工审批，--yes 自动拒绝）。参数：url（必填）、timeout（秒，缺省 15）、max_bytes（响应上限字节，缺省 200000）",
        permission=ToolPermission.DANGEROUS,
        parameters=_schema({
            "url": {"type": "string"},
            "timeout": {"type": "number"},
            "max_bytes": {"type": "integer"},
        }, required=["url"]),
        handler=_tool_http_fetch,
    ))
    registry.register(ToolSpec(
        name="ask_user",
        description="向人工用户提出需求澄清问题并等待自由文本回答（PM/文档岗可用；回答作为工具结果返回）",
        permission=ToolPermission.HUMAN_INTERACTION,
        parameters=_schema({
            "question": {"type": "string", "description": "要问的问题（简洁明确）"},
            "hint": {"type": "string", "description": "可选提示/背景，帮助用户作答"},
        }, required=["question"]),
        handler=_tool_ask_user,
    ))
    return registry
