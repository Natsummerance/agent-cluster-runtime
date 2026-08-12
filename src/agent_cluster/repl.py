"""REPL（v0.4 T11.4）：``agent-cluster chat`` —— 连续多轮开发对话入口。

- 每轮用户指令 -> 关键词启发式选择岗位 -> 工具模式 ReAct 循环（真实工作区执行）。
- 跨轮上下文：模型消息历史（user/assistant 最终文本）在轮次间保留（最多 12 条）。
- 命令：``/status``（token 消耗/剩余）``/budget`` ``/skills`` ``/plugins`` ``/exit``。
- 审批：危险工具打印请求并 accept/reject；ask_user 自由文本；``--yes`` 下危险工具
  自动拒绝、澄清用缺省答案。
- hooks：``session_start`` / ``session_end`` / ``user_prompt_submit`` / ``stop`` 由
  PluginManager 自动执行（对齐 codex-cli 契约）。
- token 计量：每次模型调用经 TokenLedger 记账，``/status`` 展示预算/消耗/剩余。
"""

from __future__ import annotations

import asyncio
import json
import sys
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Sequence

from agent_cluster.mcp_client import StdioMCPClient, parse_server_command, register_mcp_resource_tool, register_mcp_tools
from agent_cluster.models import AgentConfig, ModelConfig, Role, TokenUsage
from agent_cluster.runtime import ChatModelFactory
from agent_cluster.session import DEFAULT_TOKEN_BUDGET, TokenLedger
from agent_cluster.skills import DisclosureLevel, SkillCatalog, SkillLoader, format_skill_context
from agent_cluster.tools import (
    ToolCall,
    ToolPermission,
    ToolResult,
    ToolSession,
    build_default_tools,
    load_agents_md,
)

__all__ = ["ReplSession", "ReplConfig", "choose_role_id", "DEFAULT_REPL_MODEL"]

DEFAULT_REPL_MODEL = "codex"
# 跨轮上下文保留的消息条数
MAX_CONTEXT_MESSAGES = 12
# 工具模式每轮最大 ReAct 循环次数
DEFAULT_MAX_ROUNDS = 6

# 指令关键词 -> 岗位 id 启发式
ROLE_KEYWORDS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("测试", "检查", "验收", "回归", "质检"), "qa"),
    (("部署", "发布", "上线", "docker", "镜像", "运维", "监控"), "devops"),
    (("文档", "手册", "README", "说明"), "docs"),
    (("架构", "设计评审", "方案", "技术选型", "数据库设计"), "architect"),
    (("前端", "页面", "UI", "界面", "组件", "样式", "交互"), "frontend"),
    (("算法", "模型", "训练", "推理", "特征", "数据"), "algorithm"),
    (("接口", "API", "后端", "数据库", "服务", "登录", "业务"), "backend"),
    (("需求", "PRD", "竞品", "产品", "用户"), "pm"),
)


def choose_role_id(instruction: str) -> str:
    """按指令关键词启发式选择岗位 id（未命中回落 backend）。"""
    for keywords, role_id in ROLE_KEYWORDS:
        if any(keyword in instruction for keyword in keywords):
            return role_id
    return "backend"


@dataclass
class ReplConfig:
    """chat 子命令配置（对应 CLI 参数）。"""

    workspace: str
    model: str = DEFAULT_REPL_MODEL
    budget: int | None = None
    max_rounds: int = DEFAULT_MAX_ROUNDS
    deterministic: bool = False
    yes: bool = False
    skills_root: str | None = None
    mcp_servers: list[str] = field(default_factory=list)
    tool_script: list[dict] | None = None
    sandbox: Any | None = None
    plugin_manager: Any | None = None


class ReplSession:
    """连续多轮开发会话：工具模式 + 跨轮上下文 + token 计量 + 命令处理。"""

    def __init__(
        self,
        *,
        workspace: str | Path,
        model: str = DEFAULT_REPL_MODEL,
        budget: int | None = None,
        max_rounds: int = DEFAULT_MAX_ROUNDS,
        deterministic: bool = False,
        yes: bool = False,
        skills_root: str | None = None,
        mcp_servers: Sequence[str] | None = None,
        tool_script: Sequence[dict] | None = None,
        sandbox: Any | None = None,
        plugin_manager: Any | None = None,
        prompt_fn: Callable[[str], str] | None = None,
        print_fn: Callable[[str], None] | None = None,
    ) -> None:
        self.workspace = Path(workspace).expanduser().resolve()
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.model = model or DEFAULT_REPL_MODEL
        self.deterministic = bool(deterministic)
        self.yes = bool(yes)
        self.max_rounds = max_rounds if max_rounds and max_rounds > 0 else DEFAULT_MAX_ROUNDS
        self.prompt_fn = prompt_fn if prompt_fn is not None else input
        self.print_fn = print_fn if print_fn is not None else print
        self.plugin_manager = plugin_manager
        self.session_id = uuid.uuid4().hex
        self.ledger = TokenLedger(budget=budget if budget is not None else DEFAULT_TOKEN_BUDGET)
        self.messages: list[dict] = []
        self._turn_count = 0

        # 技能目录（skills_root + 插件技能）
        self.catalog: SkillCatalog | None = None
        plugin_skills: list = []
        if self.plugin_manager is not None:
            try:
                plugin_skills = self.plugin_manager.list_skills()
            except Exception:  # noqa: BLE001 —— 插件技能失败不阻断 REPL
                plugin_skills = []
        if skills_root or plugin_skills:
            self.catalog = SkillCatalog()
            from agent_cluster.roles import RoleRegistry

            roles = RoleRegistry().list()
            skills = list(SkillLoader().list_skills(skills_root)) if skills_root else []
            skills = skills + list(plugin_skills)
            for role in roles:
                self.catalog.mount(role, skills)

        # 工具会话（工作区 + MCP）：MCP 连接延迟到 _setup（单一事件循环内）
        self.mcp_servers = list(mcp_servers or [])
        self.tool_script = list(tool_script or [])
        self.sandbox = sandbox
        self.tool_session: ToolSession | None = None
        self.client: Any | None = None
        self._mcp_clients: list[StdioMCPClient] = []

    # ------------------------------------------------------------------
    # 初始化（单一事件循环内执行）
    # ------------------------------------------------------------------

    async def _setup(self) -> None:
        """连接 MCP、构造工具会话与模型客户端（必须在 _run 的循环内调用一次）。"""
        registry = build_default_tools()
        for server_spec in self.mcp_servers:
            server_name, argv = parse_server_command(server_spec)
            client = StdioMCPClient(server_name, argv)
            self._mcp_clients.append(client)
            await client.connect()  # 连接失败 fail-fast（与 run/build 一致）
            await register_mcp_tools(registry, client, server_name)
            await register_mcp_resource_tool(registry, client, server_name)
        self.tool_session = ToolSession(
            self.workspace,
            registry=registry,
            sandbox=self.sandbox,
            agents_md=load_agents_md(self.workspace),
        )
        from agent_cluster.subagent import SubagentBroker, register_subagent_tool

        register_subagent_tool(
            self.tool_session.registry,
            SubagentBroker(
                client_factory=lambda role_id="backend": self.client,
                usage_hook=lambda usage, role: self._on_usage(usage, role),
            ),
        )
        effective_model = "deterministic" if self.deterministic else self.model
        config = AgentConfig(model=ModelConfig(model_name=effective_model))
        if self.tool_script:
            config.react.tool_script = list(self.tool_script)
        self.client = ChatModelFactory().create(config)

    async def _teardown(self) -> None:
        """关闭 MCP 子进程（幂等）。"""
        for client in self._mcp_clients:
            try:
                await client.close()
            except Exception:  # noqa: BLE001 —— 关闭失败不阻断退出
                pass

    # ------------------------------------------------------------------
    # 主循环
    # ------------------------------------------------------------------

    def run(self) -> int:
        """交互主循环（内部单一事件循环）；返回退出码（0 正常退出 / 1 运行失败）。"""
        return asyncio.run(self._run())

    async def _run(self) -> int:
        """异步主循环：setup -> 命令/轮次循环 -> teardown。"""
        await self._setup()
        self.print_fn("===== agent-cluster chat（连续多轮开发） =====")
        self.print_fn(f"工作区：{self.workspace}")
        self.print_fn("输入需求/指令开始开发；/status /budget /skills /plugins /exit 查看与控制。")
        if self.plugin_manager is not None:
            await self.plugin_manager.run_hooks(
                "session_start", workspace=str(self.workspace), session_id=self.session_id
            )
        try:
            while True:
                raw = str(self.prompt_fn("你> ")).strip()
                if not raw:
                    continue
                low = raw.lower()
                if low == "/exit":
                    self.print_fn("再见。")
                    return 0
                if low == "/status":
                    self.print_fn(self.status_text())
                    continue
                if low == "/budget":
                    self.print_fn(self.budget_text())
                    continue
                if low == "/skills":
                    self.print_fn(self.skills_text())
                    continue
                if low == "/plugins":
                    self.print_fn(self.plugins_text())
                    continue
                if low in ("/help", "/?"):
                    self.print_fn(self.help_text())
                    continue
                try:
                    await self.turn(raw)
                except Exception as exc:  # noqa: BLE001 —— 单轮失败不退出 REPL
                    self.print_fn(f"[本轮失败] {type(exc).__name__}: {exc}")
        finally:
            if self.plugin_manager is not None:
                await self.plugin_manager.run_hooks(
                    "session_end", workspace=str(self.workspace), session_id=self.session_id
                )
            await self._teardown()
        return 0

    # ------------------------------------------------------------------
    # 单轮执行
    # ------------------------------------------------------------------

    async def turn(self, instruction: str) -> dict:
        """执行一轮：选岗 -> ReAct 工具循环 -> 更新跨轮上下文。"""
        if self.plugin_manager is not None:
            await self.plugin_manager.run_hooks(
                "user_prompt_submit", workspace=str(self.workspace), session_id=self.session_id
            )
        self._turn_count += 1
        from agent_cluster.roles import RoleRegistry

        role = RoleRegistry().get(choose_role_id(instruction))
        allowed = self._allowed_tool_names(role)
        schemas = self.tool_session.registry.as_openai_schemas(names=allowed)
        system_parts = [
            f"{role.name}：{role.goal}",
            f"岗位背景：{role.backstory}",
            f"工作区根目录：{self.workspace}",
            f"可用工具：{', '.join(allowed) or '（无）'}",
        ]
        if self.catalog is not None:
            for skill in self.catalog.mounted_skills(role):
                system_parts.append(format_skill_context(skill, DisclosureLevel.LEVEL_2))
        if self.tool_session is not None and self.tool_session.agents_md:
            system_parts.append(f"项目记忆（AGENTS.md）：\n{self.tool_session.agents_md}")
        messages: list[dict] = [{"role": "system", "content": "\n".join(system_parts)}]
        messages.extend(self.messages)
        messages.append({"role": "user", "content": instruction})

        final_text = ""
        tokens_used = 0
        tool_calls = 0
        for _round in range(1, self.max_rounds + 1):
            response = await self.client.complete_with_tools(messages, schemas)
            self._on_usage(response.usage, role.id)
            if response.usage is not None:
                tokens_used += response.usage.total_tokens
            if not response.tool_calls:
                final_text = response.text or f"{role.name}：完成。"
                break
            for call in response.tool_calls:
                tool_calls += 1
                result = await self._execute_tool(call, role)
                self.print_fn(
                    f"  [{role.name}][工具] {call.name} ok={result.ok}"
                    + (f"（{result.output[:120]}）" if result.output else "")
                )
                messages.append(
                    {"role": "user", "content": f"[工具结果 {call.name} ok={result.ok}] {result.output[:1500]}"}
                )
        else:
            final_text = f"{role.name}：达到最大轮数（{self.max_rounds}），任务未完全收敛。"

        self.messages.append({"role": "user", "content": instruction})
        self.messages.append({"role": "assistant", "content": final_text})
        self.messages = self.messages[-MAX_CONTEXT_MESSAGES:]

        if self.plugin_manager is not None:
            await self.plugin_manager.run_hooks(
                "stop", workspace=str(self.workspace), session_id=self.session_id
            )
        self.print_fn(f"  [{role.name}] {final_text}")
        return {
            "role": role.id,
            "text": final_text,
            "tool_calls": tool_calls,
            "tokens_used": tokens_used,
            "round": self._turn_count,
        }

    def _allowed_tool_names(self, role: Role) -> list[str]:
        """岗位可用工具：技能目录交集 > 角色 tools 交集 > 只读兜底。"""
        if self.catalog is not None:
            allowed = set(self.catalog.allowed_tools(role))
        else:
            allowed = set(role.tools)
        names = set(self.tool_session.registry.names())
        filtered = sorted(allowed & names)
        if not filtered:
            read_only = {"list_dir", "read_file", "grep", "glob", "git_status", "git_diff"}
            filtered = sorted(read_only & names)
        return filtered

    async def _execute_tool(self, call: ToolCall, role: Role) -> ToolResult:
        """执行工具；危险/人工交互走 REPL 内联审批。"""
        result = await self.tool_session.execute(call)
        if not result.needs_approval:
            return result
        spec = self.tool_session.registry.get(call.name)
        if spec.permission == ToolPermission.HUMAN_INTERACTION:
            question = str(call.args.get("question") or "需求澄清问题")
            if self.yes:
                answer = "[自动] 未提供人工输入，按缺省判断继续。"
            else:
                raw = str(self.prompt_fn(f"[澄清] {question}（自由文本回答）\n> ")).strip()
                answer = raw or "[自动] 未提供人工输入，按缺省判断继续。"
            self.print_fn(f"  → {answer}")
            return ToolResult(id=call.id, name=call.name, ok=True, output=answer, args=dict(call.args))
        # 危险工具
        if self.yes:
            reason = f"危险工具 {call.name} 被拒绝（--yes 自动拒绝）"
        else:
            raw = str(
                self.prompt_fn(
                    f"[危险工具] {call.name}，参数：{json.dumps(call.args, ensure_ascii=False, default=str)[:300]}\n"
                    "请选择 [accept|reject]："
                )
            ).strip()
            if raw.lower().startswith("accept"):
                return await self.tool_session.execute_approved(call)
            reason = f"危险工具 {call.name} 被拒绝（{raw or '人工拒绝'}）"
        return ToolResult(
            id=call.id, name=call.name, ok=False, output=reason, error="rejected", args=dict(call.args)
        )

    def _on_usage(self, usage: TokenUsage | None, role: str) -> None:
        """token 记账（异常不中断）。"""
        if usage is None:
            return
        try:
            self.ledger.record(role=role, phase="chat", usage=usage)
        except Exception:  # noqa: BLE001 —— 记账异常不中断
            pass

    # ------------------------------------------------------------------
    # 命令输出
    # ------------------------------------------------------------------

    def status_text(self) -> str:
        """/status：会话与 token 状态。"""
        try:
            from agent_cluster.roles import RoleRegistry

            role_count = len(RoleRegistry().list())
        except Exception:  # noqa: BLE001
            role_count = 0
        return (
            f"会话：{self.session_id} | 轮次：{self._turn_count} | 工作区：{self.workspace}\n"
            f"模型：{self.client.model if hasattr(self.client, 'model') else self.model} | "
            f"岗位数：{role_count} | 上下文消息：{len(self.messages)}"
        )

    def budget_text(self) -> str:
        """/budget：token 预算。"""
        return (
            f"预算：{self.ledger.budget} | 已用：{self.ledger.total()} | "
            f"剩余：{self.ledger.remaining()} | 超限：{self.ledger.over_budget()}"
        )

    def skills_text(self) -> str:
        """/skills：已挂载技能清单。"""
        if self.catalog is None:
            return "（未挂载技能；可用 --skills-root 或插件提供）"
        seen: dict[str, str] = {}
        for role in self.catalog._mounted:
            for skill in self.catalog._mounted[role]:
                seen[skill.name] = skill.description
        if not seen:
            return "（无已挂载技能）"
        return "\n".join(f"  - {name}：{desc[:60]}" for name, desc in sorted(seen.items()))

    def plugins_text(self) -> str:
        """/plugins：已发现插件。"""
        if self.plugin_manager is None:
            return "（未启用插件；可用 --plugin-dir 指定）"
        try:
            manifests = self.plugin_manager.list_plugins()
        except Exception as exc:  # noqa: BLE001
            return f"（插件列表失败：{exc}）"
        if not manifests:
            return "（未发现插件）"
        return "\n".join(
            f"  - {m.name}@{m.version}：{(m.description or '')[:60]}" for m in manifests
        )

    def help_text(self) -> str:
        """/help：命令说明。"""
        return (
            "命令：\n"
            "  /status   会话与 token 状态\n"
            "  /budget   token 预算/消耗/剩余\n"
            "  /skills   已挂载技能\n"
            "  /plugins  已发现插件\n"
            "  /exit     退出 REPL"
        )
