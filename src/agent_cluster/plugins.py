"""插件层（v0.4 T11.3）：双规范插件清单 + marketplace + hooks（对齐 codex-cli 契约）。

结构参考克隆的 ``fullstack-agents/codex-cli``（openai/codex）：
- ``PluginManifest``：合并 ``.codex-plugin/plugin.json`` 与 ``.claude-plugin/plugin.json``
  （name/version/description/keywords/skills/hooks/interface），对应 codex-rs
  ``plugin/src/manifest.rs`` 的 ``PluginManifestPaths{skills, mcp_servers, apps, hooks}``。
- hooks 事件对齐 codex-rs ``config/src/hook_config.rs`` 的 ``HookEventsToml`` 十一个事件：
  ``PreToolUse / PermissionRequest / PostToolUse / PreCompact / PostCompact /
  SessionStart / SessionEnd / UserPromptSubmit / SubagentStart / SubagentStop / Stop``
  （接受 PascalCase 与 snake_case 两种写法）。
- ``MatcherGroup``：``{matcher?, hooks: [HookHandlerConfig]}``；``HookHandlerConfig``
  按 ``type`` 区分 ``command / mcp_tool / prompt / agent``（对应 codex-rs 枚举）。
  本平台执行 ``command``（含 ``commandWindows`` 平台回退、timeout、async），
  其余类型记录为「不支持」不执行。
- hook 输入：子进程 stdin 写入 JSON payload（对齐 codex-rs ``HookPayload``：
  ``session_id / cwd / hook_event_name / model / permission_mode / source`` 等），
  同时注入 ``AGENT_CLUSTER_*`` 环境变量。
- 插件技能以 ``plugin:<插件名>:<技能名>`` 命名空间加载进 ``SkillRegistry``。
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

from pydantic import BaseModel, ConfigDict, Field

from agent_cluster.skills import Skill, SkillError, SkillLoader, SkillRegistry

def _platform_is_windows() -> bool:
    """当前是否为 Windows（commandWindows 平台回退判断；独立函数便于测试注入）。"""
    return os.name == "nt"

__all__ = [
    "PluginError",
    "HookSpec",
    "HookResult",
    "PluginManifest",
    "PluginManager",
    "default_plugin_search_dirs",
    "plugin_skill_namespace",
    "discover_plugin_roots",
    "parse_manifest",
    "HOOK_EVENTS",
]

# 默认插件搜索目录：Codex 插件缓存 + 环境变量追加目录
CODEX_PLUGIN_CACHE = Path.home() / ".codex" / "plugins" / "cache"
PLUGIN_DIRS_ENV = "AGENT_CLUSTER_PLUGIN_DIRS"

# codex-cli HookEventsToml 十一个事件（小写规范名 -> PascalCase 线上名）
HOOK_EVENTS: dict[str, str] = {
    "pre_tool_use": "PreToolUse",
    "permission_request": "PermissionRequest",
    "post_tool_use": "PostToolUse",
    "pre_compact": "PreCompact",
    "post_compact": "PostCompact",
    "session_start": "SessionStart",
    "session_end": "SessionEnd",
    "user_prompt_submit": "UserPromptSubmit",
    "subagent_start": "SubagentStart",
    "subagent_stop": "SubagentStop",
    "stop": "Stop",
}
# 事件名归一化：任意大小写/连字符/下划线 -> 规范小写名（含常用别名）
_HOOK_ALIASES: dict[str, str] = {
    "".join(ch for ch in key if ch.isalnum()).lower(): key for key in HOOK_EVENTS
}
_HOOK_ALIASES.update(
    {
        "turnstart": "user_prompt_submit",
        "turnend": "stop",
        "sessionstart": "session_start",
        "sessionend": "session_end",
    }
)


class PluginError(Exception):
    """插件层统一异常（解析失败、技能加载失败、钩子执行失败）。"""


def default_plugin_search_dirs() -> list[str]:
    """返回默认插件搜索目录（缓存目录 + 环境变量追加）。"""
    dirs = [str(CODEX_PLUGIN_CACHE)]
    env = os.environ.get(PLUGIN_DIRS_ENV, "")
    if env:
        dirs.extend(
            part.strip() for part in env.replace(";", os.pathsep).split(os.pathsep) if part.strip()
        )
    return dirs


def plugin_skill_namespace(plugin_name: str, skill_name: str) -> str:
    """插件技能命名空间：``plugin:<插件名>:<技能名>``。"""
    return f"plugin:{plugin_name}:{skill_name}"


def _normalize_hook_event(name: str) -> str | None:
    """把任意写法的钩子事件名归一化为规范小写名；未知返回 None。"""
    key = "".join(ch for ch in str(name) if ch.isalnum()).lower()
    return _HOOK_ALIASES.get(key)


@dataclass
class HookSpec:
    """一条钩子定义（对应 codex-rs HookHandlerConfig::Command）。"""

    command: str = ""
    handler_type: str = "command"
    shell: str | None = None
    async_: bool = False
    timeout: float = 60.0
    event: str = ""
    status_message: str = ""
    unsupported: str = ""  # 非空表示该类型不执行（mcp_tool/prompt/agent）


@dataclass
class HookResult:
    """一次钩子执行结果（失败不抛异常，记录输出）。"""

    plugin: str = ""
    event: str = ""
    command: str = ""
    ok: bool = False
    output: str = ""
    duration: float = 0.0
    error: str = ""


class PluginManifest(BaseModel):
    """合并 .codex-plugin / .claude-plugin / 根 plugin.json 的插件元数据。"""

    model_config = ConfigDict(extra="ignore")

    name: str = Field(description="插件名称")
    version: str = Field(default="0.1.0", description="插件版本（semver）")
    description: str = Field(default="", description="插件描述")
    author: dict = Field(default_factory=dict, description="作者信息")
    homepage: str = Field(default="", description="主页")
    repository: str = Field(default="", description="仓库地址")
    license: str = Field(default="", description="许可证")
    keywords: list[str] = Field(default_factory=list, description="关键词")
    skills: list[str] = Field(
        default_factory=list, description="技能包路径（相对插件根，目录或 SKILL.md 目录列表）"
    )
    hooks: dict[str, list[HookSpec]] = Field(default_factory=dict, description="事件 -> 钩子列表")
    interface: dict = Field(default_factory=dict, description="codex interface 字段（展示信息）")
    root: str = Field(default="", description="插件根目录")

    @property
    def skill_dirs(self) -> list[str]:
        """解析 skills 字段为包含 SKILL.md 的具体目录列表。"""
        resolved: list[str] = []
        root = Path(self.root)
        for raw in self.skills:
            if not raw:
                continue
            candidate = root / raw
            if candidate.is_dir():
                if (candidate / "SKILL.md").is_file():
                    resolved.append(str(candidate))
                else:
                    # 目录下的技能包（如 ./skills/ 含多个 SKILL.md 子目录）
                    for child in sorted(candidate.iterdir()):
                        if child.is_dir() and (child / "SKILL.md").is_file():
                            resolved.append(str(child))
            elif candidate.is_file() and candidate.name == "SKILL.md":
                resolved.append(str(candidate.parent))
        return sorted(set(resolved))


def _load_json(path: Path) -> dict | None:
    """读取 JSON 文件；缺失/损坏返回 None（容错）。"""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None


def _parse_handler_config(raw: Any, event: str) -> HookSpec | None:
    """解析一条 HookHandlerConfig（codex-rs 枚举：command/mcp_tool/prompt/agent）。"""
    if not isinstance(raw, dict):
        return None
    handler_type = str(raw.get("type") or "command")
    if handler_type == "command":
        command = str(raw.get("command") or "")
        if not command:
            return None
        # Windows 平台优先 commandWindows（codex-rs 同名字段回退）
        if _platform_is_windows() and str(raw.get("commandWindows") or "").strip():
            command = str(raw["commandWindows"])
        return HookSpec(
            command=command,
            handler_type="command",
            shell=str(raw.get("shell") or "") or None,
            async_=bool(raw.get("async") or False),
            timeout=float(raw.get("timeout") or 60.0),
            event=event,
            status_message=str(raw.get("statusMessage") or ""),
        )
    if handler_type in ("mcp_tool", "prompt", "agent"):
        return HookSpec(command="", handler_type=handler_type, event=event, unsupported=handler_type)
    return None


def _parse_hook_specs(raw: Any, event: str) -> list[HookSpec]:
    """解析某事件的值：单个 spec / spec 列表 / MatcherGroup 列表。"""
    specs: list[HookSpec] = []
    if isinstance(raw, dict):
        raw = [raw]
    if not isinstance(raw, list):
        return specs
    for item in raw:
        if not isinstance(item, dict):
            continue
        if isinstance(item.get("hooks"), list):
            # MatcherGroup：{matcher?, hooks: [HookHandlerConfig]}
            for inner in item["hooks"]:
                spec = _parse_handler_config(inner, event)
                if spec is not None:
                    specs.append(spec)
            continue
        spec = _parse_handler_config(item, event)
        if spec is not None:
            specs.append(spec)
    return specs


def _merge_hooks_into(manifest: PluginManifest, raw_hooks: Any, root: Path) -> None:
    """把 plugin.json hooks 字段（dict / HooksFile 列表 / 路径列表）并入清单。"""
    if isinstance(raw_hooks, dict):
        for event_raw, value in raw_hooks.items():
            event = _normalize_hook_event(str(event_raw))
            if event is None:
                continue
            manifest.hooks.setdefault(event, []).extend(_parse_hook_specs(value, event))
        return
    if not isinstance(raw_hooks, list):
        return
    for item in raw_hooks:
        if isinstance(item, str):
            # 路径指向 hooks.json（codex-rs PluginManifestHooks::Paths）
            hooks_path = root / item
            hooks_json = _load_json(hooks_path)
            if isinstance(hooks_json, dict):
                _merge_hooks_into(manifest, hooks_json.get("hooks"), root)
            continue
        if isinstance(item, dict) and isinstance(item.get("hooks"), dict):
            # HooksFile：{description?, hooks: {EventName: [MatcherGroup]}}
            _merge_hooks_into(manifest, item["hooks"], root)


def parse_manifest(root: str | Path) -> PluginManifest | None:
    """解析插件根目录的双规范清单；无任何清单返回 None。

    - 基础：``.codex-plugin/plugin.json``（name/version/description/author/skills/
      hooks/interface）；``.claude-plugin/plugin.json`` 合并 description/version/
      author/keywords。
    - 回退：根目录 ``plugin.json``（通用清单）。
    - hooks：plugin.json ``hooks`` 字段（dict / HooksFile 列表 / 路径列表）+
      根目录 ``hooks.json``（Claude Code 风格，事件名自动归一化）。
    """
    root_path = Path(root)
    if not root_path.is_dir():
        raise PluginError(f"插件目录不存在：{root_path}")
    codex = _load_json(root_path / ".codex-plugin" / "plugin.json")
    claude = _load_json(root_path / ".claude-plugin" / "plugin.json")
    generic = _load_json(root_path / "plugin.json") if codex is None and claude is None else None
    merged: dict[str, Any] = {}
    for source in (codex, claude, generic):
        if not isinstance(source, dict):
            continue
        for key, value in source.items():
            if key not in merged or merged[key] in (None, "", [], {}):
                merged[key] = value
    if not merged.get("name"):
        return None

    manifest = PluginManifest(
        name=str(merged["name"]),
        version=str(merged.get("version") or "0.1.0"),
        description=str(merged.get("description") or ""),
        author=dict(merged.get("author") or {}),
        homepage=str(merged.get("homepage") or ""),
        repository=str(merged.get("repository") or ""),
        license=str(merged.get("license") or ""),
        keywords=list(merged.get("keywords") or []),
        skills=_parse_manifest_skills(merged.get("skills"), root_path),
        interface=dict(merged.get("interface") or {}),
        root=str(root_path.resolve()),
    )
    # hooks：plugin.json hooks 字段（对齐 codex-rs Inline/Paths 两种形态）
    _merge_hooks_into(manifest, merged.get("hooks"), root_path)
    # hooks：根目录 hooks.json（Claude Code 风格）
    hooks_json = _load_json(root_path / "hooks.json")
    if isinstance(hooks_json, dict):
        _merge_hooks_into(manifest, hooks_json.get("hooks"), root_path)
    return manifest


def _parse_manifest_skills(raw: Any, root: Path) -> list[str]:
    """归一化 skills 字段：字符串目录或路径列表。"""
    if isinstance(raw, str):
        return [raw]
    if isinstance(raw, list):
        return [str(item) for item in raw if isinstance(item, str) and item.strip()]
    return []


def _is_plugin_root(path: Path) -> bool:
    """目录是否为插件根（含任一清单）。"""
    return any(
        (path / marker).exists()
        for marker in (".codex-plugin/plugin.json", ".claude-plugin/plugin.json", "plugin.json")
    )


def _is_marketplace_root(path: Path) -> bool:
    return (path / "marketplace.json").is_file()


def discover_plugin_roots(search_dirs: Sequence[str]) -> list[str]:
    """在搜索目录中发现插件根目录（深度 ≤3：org/plugin/version）与 marketplace 根。"""
    found: list[str] = []
    seen: set[str] = set()
    for raw in search_dirs:
        base = Path(raw).expanduser()
        if not base.is_dir():
            continue
        if _is_plugin_root(base) or _is_marketplace_root(base):
            key = str(base.resolve())
            if key not in seen:
                seen.add(key)
                found.append(key)
            continue
        # 递归浅扫（防止进入 .git 等深层目录）
        stack = [base]
        depth = 0
        while stack and depth <= 3:
            nxt: list[Path] = []
            for directory in stack:
                try:
                    children = sorted(d for d in directory.iterdir() if d.is_dir())
                except OSError:
                    continue
                for child in children:
                    if child.name in (".git", "__pycache__", "node_modules", ".venv"):
                        continue
                    if _is_plugin_root(child) or _is_marketplace_root(child):
                        key = str(child.resolve())
                        if key not in seen:
                            seen.add(key)
                            found.append(key)
                    else:
                        nxt.append(child)
            stack = nxt
            depth += 1
    return found


class PluginManager:
    """插件管理器：扫描/解析清单、加载技能、注册 hooks、全自动执行 hooks。"""

    def __init__(
        self,
        search_dirs: Sequence[str] | None = None,
        *,
        skill_registry: SkillRegistry | None = None,
    ) -> None:
        self.search_dirs = list(search_dirs) if search_dirs is not None else default_plugin_search_dirs()
        self.manifests: dict[str, PluginManifest] = {}
        self._skills: list[Skill] = []
        self._hooks: dict[str, list[tuple[str, HookSpec]]] = {event: [] for event in HOOK_EVENTS}
        self._skill_registry = skill_registry

    # ------------------------------------------------------------------
    # 扫描与解析
    # ------------------------------------------------------------------

    def scan(self) -> list[PluginManifest]:
        """扫描搜索目录并解析全部插件清单（含 marketplace）。"""
        self.manifests = {}
        self._hooks = {event: [] for event in HOOK_EVENTS}
        for root in discover_plugin_roots(self.search_dirs):
            root_path = Path(root)
            if _is_marketplace_root(root_path):
                self._parse_marketplace(root_path)
                continue
            try:
                manifest = parse_manifest(root_path)
            except PluginError:
                continue
            if manifest is None or manifest.name in self.manifests:
                continue
            self.manifests[manifest.name] = manifest
            for event, specs in manifest.hooks.items():
                for spec in specs:
                    self._hooks.setdefault(event, []).append((manifest.name, spec))
        return list(self.manifests.values())

    def _parse_marketplace(self, root: Path) -> None:
        """解析 marketplace.json：plugins[].skills 引用的技能目录注册为插件。"""
        data = _load_json(root / "marketplace.json")
        if not isinstance(data, dict):
            return
        for entry in data.get("plugins") or []:
            if not isinstance(entry, dict):
                continue
            name = str(entry.get("name") or "").strip()
            if not name:
                continue
            skills: list[str] = []
            for raw in entry.get("skills") or []:
                if not isinstance(raw, str):
                    continue
                candidate = root / raw
                if candidate.is_dir():
                    skills.append(str(candidate))
            manifest = PluginManifest(
                name=name,
                version="0.1.0",
                description=str(entry.get("description") or ""),
                skills=skills,
                root=str(root.resolve()),
            )
            self.manifests.setdefault(name, manifest)

    # ------------------------------------------------------------------
    # 技能
    # ------------------------------------------------------------------

    def load_skills(self, registry: SkillRegistry | None = None) -> list[Skill]:
        """加载全部插件技能（命名空间 ``plugin:<插件名>:<技能名>``）并注册。"""
        loader = SkillLoader()
        self._skills = []
        for plugin_name in sorted(self.manifests):
            manifest = self.manifests[plugin_name]
            for skill_dir in manifest.skill_dirs:
                try:
                    skill = loader.load(skill_dir)
                except SkillError:
                    continue
                skill = skill.model_copy(update={"name": plugin_skill_namespace(plugin_name, skill.name)})
                self._skills.append(skill)
                if registry is not None:
                    try:
                        registry.register(skill, source="@plugin")
                    except SkillError:
                        continue  # 重复注册跳过
        return list(self._skills)

    def list_skills(self) -> list[Skill]:
        """返回已加载的插件技能列表（未加载时先加载）。"""
        if not self._skills:
            self.load_skills(self._skill_registry)
        return list(self._skills)

    # ------------------------------------------------------------------
    # hooks
    # ------------------------------------------------------------------

    async def run_hooks(
        self,
        event: str,
        *,
        workspace: str = "",
        thread_id: str = "",
        session_id: str = "",
        env: dict | None = None,
        model: str = "",
        permission_mode: str = "default",
        source: str = "startup",
    ) -> list[HookResult]:
        """执行某事件的全部钩子（并行），返回结果列表（失败不抛异常）。

        - hook 输入：stdin 写入 JSON payload（对齐 codex-rs HookPayload：
          session_id / cwd / hook_event_name / model / permission_mode / source 等）。
        - 环境变量：``AGENT_CLUSTER_EVENT / AGENT_CLUSTER_PLUGIN /
          AGENT_CLUSTER_WORKSPACE / AGENT_CLUSTER_THREAD_ID / AGENT_CLUSTER_SESSION_ID``。
        - ``async: true`` 的钩子 fire-and-forget；超时 kill 子进程。
        """
        event = _normalize_hook_event(event) or event
        results: list[HookResult] = []
        specs = self._hooks.get(event, [])
        if not specs:
            return results
        base_env = dict(os.environ)
        if env:
            base_env.update(env)
        base_env["AGENT_CLUSTER_EVENT"] = event
        base_env["AGENT_CLUSTER_WORKSPACE"] = workspace
        base_env["AGENT_CLUSTER_THREAD_ID"] = thread_id
        base_env["AGENT_CLUSTER_SESSION_ID"] = session_id
        payload = {
            "session_id": session_id or uuid.uuid4().hex,
            "cwd": workspace,
            "hook_event_name": HOOK_EVENTS.get(event, event),
            "model": model,
            "permission_mode": permission_mode,
            "source": source,
            "transcript_path": None,
        }

        async def _run(plugin: str, spec: HookSpec) -> HookResult:
            import time

            if spec.unsupported:
                return HookResult(
                    plugin=plugin,
                    event=event,
                    command=spec.handler_type,
                    ok=False,
                    output="",
                    duration=0.0,
                    error=f"不支持的钩子类型 {spec.handler_type}（本平台仅执行 command）",
                )
            hook_env = dict(base_env)
            hook_env["AGENT_CLUSTER_PLUGIN"] = plugin
            started = time.monotonic()
            try:
                proc = await asyncio.create_subprocess_shell(
                    spec.command,
                    cwd=workspace or None,
                    env=hook_env,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                try:
                    stdout, stderr = await asyncio.wait_for(
                        proc.communicate(input=json.dumps(payload, ensure_ascii=False).encode("utf-8")),
                        timeout=spec.timeout,
                    )
                except asyncio.TimeoutError:
                    proc.kill()
                    await proc.wait()
                    raise
                output = (stdout or b"").decode("utf-8", errors="replace") + (stderr or b"").decode(
                    "utf-8", errors="replace"
                )
                ok = proc.returncode == 0
                return HookResult(
                    plugin=plugin,
                    event=event,
                    command=spec.command,
                    ok=ok,
                    output=output.strip(),
                    duration=time.monotonic() - started,
                    error="" if ok else f"exit={proc.returncode}",
                )
            except asyncio.TimeoutError:
                return HookResult(
                    plugin=plugin,
                    event=event,
                    command=spec.command,
                    ok=False,
                    output="",
                    duration=time.monotonic() - started,
                    error=f"超时（{spec.timeout}s）",
                )
            except (OSError, asyncio.CancelledError) as exc:
                return HookResult(
                    plugin=plugin,
                    event=event,
                    command=spec.command,
                    ok=False,
                    output="",
                    duration=time.monotonic() - started,
                    error=str(exc),
                )

        tasks: list[asyncio.Task] = []
        for plugin, spec in specs:
            if spec.async_:
                asyncio.create_task(_run(plugin, spec))
            else:
                tasks.append(asyncio.create_task(_run(plugin, spec)))
        if tasks:
            results = await asyncio.gather(*tasks)
        return results

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------

    def list_plugins(self) -> list[PluginManifest]:
        """返回已解析的插件清单（未扫描时先扫描）。"""
        if not self.manifests:
            self.scan()
        return list(self.manifests.values())

    def hooks_for(self, event: str) -> list[tuple[str, HookSpec]]:
        """返回某事件注册的 (plugin, spec) 列表。"""
        return list(self._hooks.get(_normalize_hook_event(event) or event, []))
