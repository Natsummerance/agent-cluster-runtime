"""角色执行层（设计文档 §5.1）：可插拔 ChatModelClient、AgentRuntime、EventBus 与 agent 节点 handler。

组件：
- ``ChatModelClient``：统一 ``async complete(messages) -> str`` 抽象（多供应商 + fallback）。
- ``DeterministicClient``：默认确定性后端——按消息内容与 persona 生成规则回复，
  同一输入恒得同一输出，无需 API key，用于测试与演示。
- ``OpenAIClient``：可选 OpenAI ``chat.completions`` 实现；构造时若环境变量
  ``OPENAI_API_KEY`` 缺失立即抛 ``RuntimeError``（构造期检查），
  ``openai`` 包未安装时在 ``complete()`` 内抛清晰错误，确保测试永不崩溃。
- ``DeepSeekClient``：DeepSeek ``chat.completions`` 直连实现（stdlib urllib + 线程池，
  无新依赖）；默认 base_url / env_key 解析自 Codex 配置或回落官方默认值。
- ``ChatModelFactory``：按 ``AgentConfig`` 的 ``model.model_name`` 选择后端；
  缺省/``deterministic`` -> ``DeterministicClient``，``openai``/``gpt-*`` -> ``OpenAIClient``，
  ``deepseek-*`` -> ``DeepSeekClient``，``codex``/``custom`` -> 解析当前 Codex 配置，
  其他未知名称抛 ``ValueError``。
- ``EventBus``：append-only 事件列表：``publish(event)`` 追加，
  ``query(thread_id=..., type=...)`` 过滤查询（可选条件）。
- ``AgentRuntime``：``reply(agent, messages)`` 经模型客户端产出 ``Message(text)`` 并
  发布 ``agent_reply`` 事件；``observe(agent, messages)`` 把观察到的消息摘要写入
  ``agent.state``（``AgentState.messages`` 记忆，按 ``context.max_messages`` 截断）；
  ``complete_for(role, task=None)`` 为公开模型入口（经工厂构造客户端返回完成文本），
  ``make_agent_handler`` 通过它执行岗位任务，不触碰运行时私有成员。
- ``make_agent_handler(runtime, role_registry, catalog=None)``：注册进
  ``WorkflowEngine`` 的 "agent" 节点 handler，执行确定性岗位步骤。

agent handler 通道契约（Task 7 CLI 依赖，勿变更）：
- 返回 LangGraph channel 更新字典，键固定为：
  - ``"tasks"``：``list[Task]``（该节点执行的任务，状态=done；确定性后端在
    创建时即视为完成，每个 agent 节点新建一个任务并携带产出物路径
    ``artifacts/<role_id>/<task_id>.md``，满足「任务板全部 Done、产出物存在」验收）。
  - ``"messages"``：``list[Message]``（一条 ``text`` 消息，source=岗位 id）。
  - ``"ledger"``：``Ledger``（当前任务账本，追加一条 ``ProgressEntry``；替换
    ``state.ledger`` 通道，语义为「当前任务账本」）。
- 事件不占通道键：通过 ``ctx.events`` 追加 ``type="agent_step"`` 的 ``Event``。
- 为何每次新建任务：``ClusterState.tasks`` 使用 ``operator.add`` 追加 reducer，
  若复用通道中已存在的任务对象并回写，会再次追加造成重复；因此每个 agent 节点
  恒定创建一个新任务（meeting 行动项作为 todo 留在通道，构成待办 backlog，
  由 CLI 演示收尾时统一归档）。
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import uuid
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import Any

from agent_cluster.models import (
    ActionRequest,
    Agent,
    AgentConfig,
    ApprovalRecord,
    ClusterState,
    Event,
    GateKind,
    HumanInterruptConfig,
    HumanResponse,
    Ledger,
    Message,
    MessageType,
    ModelConfig,
    ProgressEntry,
    Role,
    Task,
    TaskStatus,
    TokenUsage,
)
from agent_cluster.providers import (
    CodexProviderConfig,
    DEEPSEEK_MAX_TOKENS,
    load_codex_model_config,
    resolve_deepseek_defaults,
)
from agent_cluster.skills import DisclosureLevel, format_skill_context
from agent_cluster.tokens import estimate_tokens, estimate_usage
from agent_cluster.tools import (
    ToolCall,
    ToolPermission,
    ToolResult,
    ToolSession,
)
from agent_cluster.workflow import NodeContext, NodeHandler, WorkflowNode

__all__ = [
    "ChatModelClient",
    "DeterministicClient",
    "OpenAIClient",
    "DeepSeekClient",
    "ChatModelFactory",
    "EventBus",
    "AgentRuntime",
    "make_agent_handler",
    "ChatResponse",
    "parse_tool_calls_from_text",
    "estimate_tokens",
    "estimate_usage",
]


@dataclass
class ChatResponse:
    """模型一次回复：文本 + 工具调用列表（双轨协议的统一产物）。

    - 原生 function calling 客户端（DeepSeek/OpenAI）解析 ``tool_calls`` 填入；
    - 文本回退路径（Deterministic / 默认实现）从 fenced JSON action 解析。
    """

    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: TokenUsage | None = None


def _normalize_tool_payload(data: Any) -> list[ToolCall]:
    """把 JSON action 负载归一化为 ToolCall 列表（兼容多种写法）。"""
    if isinstance(data, list):
        calls: list[ToolCall] = []
        for item in data:
            if isinstance(item, dict) and (item.get("name") or item.get("tool")):
                calls.append(
                    ToolCall(
                        name=str(item.get("name") or item.get("tool")),
                        args=dict(item.get("args") or item.get("arguments") or {}),
                    )
                )
        return calls
    if isinstance(data, dict):
        if isinstance(data.get("action"), dict):
            data = data["action"]
        name = data.get("name") or data.get("tool")
        if name:
            return [
                ToolCall(
                    name=str(name),
                    args=dict(data.get("args") or data.get("arguments") or {}),
                )
            ]
    return []


def parse_tool_calls_from_text(text: str) -> list[ToolCall]:
    """从模型文本中提取工具调用（fenced JSON action 回退协议）。

    支持格式：
    - ```json {"action": {"name": ..., "args": {...}}} ``` 或
      ```json {"name": ..., "args": {...}} ``` / ```json [{name, args}, ...] ```；
    - 整段文本本身即为 JSON（无 fence）。
    未解析出任何调用返回空列表（视为纯文本回复）。
    """
    if not text:
        return []
    fenced = re.findall(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    candidates = [candidate for candidate in fenced if candidate.strip()] or [text.strip()]
    for candidate in candidates:
        if not candidate.startswith("{"):
            continue
        try:
            data = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        calls = _normalize_tool_payload(data)
        if calls:
            return calls
    return []


class ChatModelClient(ABC):
    """模型接入抽象：统一 ``complete(messages) -> str`` 异步接口。"""

    def __init__(self) -> None:
        # 最近一次调用的 token 用量（真实 API usage 或估算值），供运行时上报账本
        self.last_usage: TokenUsage | None = None

    def _record_usage(self, usage: TokenUsage | None) -> TokenUsage | None:
        """记录最近一次调用的 token 用量并返回（供链式调用透传）。"""
        self.last_usage = usage
        return usage

    @abstractmethod
    async def complete(self, messages: list[dict]) -> str:
        """按消息列表（含 role/content）生成回复文本。"""

    async def complete_with_tools(self, messages: list[dict], tools: list[dict]) -> ChatResponse:
        """带工具调用的回复（默认实现 = 文本 + fenced JSON action 回退解析）。

        - 原生 function calling 客户端（DeepSeek/OpenAI）重写本方法；
        - 默认路径：调用 ``complete()`` 后从文本解析 JSON action，解析失败
          视为纯文本回复（``tool_calls=[]``）。
        """
        text = await self.complete(messages)
        calls = parse_tool_calls_from_text(text)
        usage = estimate_usage(messages, text, calls, getattr(self, "model", ""))
        return self._record_usage(ChatResponse(text=text, tool_calls=calls, usage=usage))


class DeterministicClient(ChatModelClient):
    """确定性后端：按消息内容与 persona 规则生成回复，无外部依赖。

    规则：空消息 -> persona 就绪语；否则回显最后一条消息内容并声明按确定性
    规则处理。同一输入恒得同一输出。
    """

    def __init__(
        self,
        persona: str = "确定性助手",
        tool_script: list[dict] | None = None,
    ) -> None:
        super().__init__()
        self.persona = persona
        self.model = "deterministic"
        # 工具调用脚本（测试注入）：逐轮弹出，弹尽后返回完成文本
        self.tool_script = list(tool_script or [])

    async def complete(self, messages: list[dict]) -> str:
        """返回基于最后一条消息内容的确定性回复（并记录估算 token 用量）。"""
        if not messages:
            text = f"{self.persona}：收到空消息，准备就绪。"
        else:
            content = str(messages[-1].get("content", "")).strip()
            if not content:
                text = f"{self.persona}：已确认消息序列（{len(messages)} 条），无待处理内容。"
            else:
                text = f"{self.persona}：已收到「{content}」，按确定性规则完成处理。"
        self._record_usage(estimate_usage(messages, text, None, self.model))
        return text

    async def complete_with_tools(self, messages: list[dict], tools: list[dict]) -> ChatResponse:
        """确定性工具模式：按 tool_script 依次返回工具调用，脚本耗尽返回完成文本。

        - 提供 ``tool_script`` 时逐轮弹出下一个调用（无 API key 可跑通工具全链路）；
        - 未提供脚本时回落基类文本 + JSON action 解析。
        """
        if self.tool_script:
            script = list(self.tool_script)
            call = script.pop(0)
            self.tool_script = script
            if call is None:
                response = ChatResponse(text=f"{self.persona}：任务完成（工具脚本已耗尽）。", tool_calls=[])
            else:
                response = ChatResponse(
                    text="",
                    tool_calls=[
                        ToolCall(name=str(call["name"]), args=dict(call.get("args") or {}))
                    ],
                )
            usage = estimate_usage(messages, response.text, response.tool_calls, self.model)
            self._record_usage(usage)
            return replace(response, usage=usage)
        return await super().complete_with_tools(messages, tools)


def _extract_usage(
    response: Any,
    model: str,
    messages: list[dict],
    text: str,
    calls: list[ToolCall] | None,
) -> TokenUsage:
    """从 OpenAI 风格响应提取 usage（缺省时回落统一估算）。"""
    usage = getattr(response, "usage", None)
    if usage is not None:
        return TokenUsage(
            prompt_tokens=int(getattr(usage, "prompt_tokens", 0) or 0),
            completion_tokens=int(getattr(usage, "completion_tokens", 0) or 0),
            total_tokens=int(getattr(usage, "total_tokens", 0) or 0),
            model=model,
            estimated_total=estimate_usage(messages, text, calls, model).total_tokens,
        )
    return estimate_usage(messages, text, calls, model)


class OpenAIClient(ChatModelClient):
    """可选 OpenAI 后端：``chat.completions`` 实现。

    - 构造期检查：环境变量（缺省 ``OPENAI_API_KEY``）缺失立即抛 ``RuntimeError``，
      避免运行时才发现缺 key；无 API key 环境请改用 ``DeterministicClient``。
    - ``openai`` 包未安装时，``complete()`` 抛清晰 ``RuntimeError``（测试不依赖）。
    """

    def __init__(
        self,
        model: str = "gpt-4o-mini",
        api_key_env: str = "OPENAI_API_KEY",
        api_base: str | None = None,
    ) -> None:
        super().__init__()
        api_key = os.environ.get(api_key_env, "")
        if not api_key:
            raise RuntimeError(
                f"OpenAIClient 需要环境变量 {api_key_env}（当前未设置）；"
                "无 API key 环境请使用 DeterministicClient。"
            )
        self.model = model
        self.api_key_env = api_key_env
        self.api_base = api_base
        self._api_key = api_key

    async def complete(self, messages: list[dict]) -> str:
        """调用 OpenAI chat.completions 并返回首个回复文本。"""
        try:
            import openai
        except ImportError as exc:
            raise RuntimeError(
                "OpenAIClient 需要安装 openai 包（uv add openai）；未安装时请使用 DeterministicClient。"
            ) from exc
        client = openai.OpenAI(api_key=self._api_key, base_url=self.api_base)
        response = client.chat.completions.create(model=self.model, messages=messages)
        text = response.choices[0].message.content or ""
        self._record_usage(_extract_usage(response, self.model, messages, text, None))
        return text

    async def complete_with_tools(self, messages: list[dict], tools: list[dict]) -> ChatResponse:
        """OpenAI 原生 function calling：请求带 ``tools``，解析 ``tool_calls``。"""
        try:
            import openai
        except ImportError as exc:
            raise RuntimeError(
                "OpenAIClient 需要安装 openai 包（uv add openai）；未安装时请使用 DeterministicClient。"
            ) from exc
        client = openai.OpenAI(api_key=self._api_key, base_url=self.api_base)
        response = client.chat.completions.create(model=self.model, messages=messages, tools=tools)
        message = response.choices[0].message
        text = message.content or ""
        calls: list[ToolCall] = []
        for raw_call in message.tool_calls or []:
            function = getattr(raw_call, "function", None)
            if function is None or not getattr(function, "name", ""):
                continue
            try:
                parsed_args = json.loads(function.arguments or "{}")
            except json.JSONDecodeError:
                parsed_args = {}
            calls.append(
                ToolCall(
                    id=getattr(raw_call, "id", None) or uuid.uuid4().hex,
                    name=function.name,
                    args=dict(parsed_args),
                )
            )
        usage = _extract_usage(response, self.model, messages, text, calls)
        return self._record_usage(ChatResponse(text=text, tool_calls=calls, usage=usage))


class DeepSeekClient(ChatModelClient):
    """DeepSeek 后端：``chat.completions`` 直连实现（stdlib urllib + 线程池，无新依赖）。

    - 构造期检查：环境变量（缺省 ``DEEPSEEK_API_KEY``）缺失立即抛 ``RuntimeError``。
    - 默认 base_url / env_key 优先取 Codex 配置（DeepSeek 供应商），否则回落
      ``https://api.deepseek.com`` + ``DEEPSEEK_API_KEY``。
    - 请求经 ``asyncio.to_thread`` 在后台线程执行，不阻塞事件循环；回复只取
      ``choices[0].message.content``（不回退思维链）；content 为空且因 token 预算
      截断时自动扩容重试一次。
    - API key 只从环境变量读取，绝不写入仓库、日志或检查点。
    """

    def __init__(
        self,
        model: str = "deepseek-v4-flash",
        api_key_env: str | None = None,
        api_base: str | None = None,
        temperature: float = 0.0,
        max_tokens: int = DEEPSEEK_MAX_TOKENS,
    ) -> None:
        super().__init__()
        # 仅当调用方未显式给出 api_base/api_key_env 时才解析 Codex 配置（避免重复 I/O）
        codex = load_codex_model_config() if (api_base is None or api_key_env is None) else None
        default_base, default_env = resolve_deepseek_defaults(codex)
        env_key = api_key_env or default_env
        api_key = os.environ.get(env_key, "")
        if not api_key:
            raise RuntimeError(
                f"DeepSeekClient 需要环境变量 {env_key}（当前未设置）；"
                "无 API key 环境请使用 DeterministicClient。"
            )
        self.model = model
        self.api_key_env = env_key
        self.api_base = (api_base or default_base).rstrip("/")
        self.temperature = temperature
        self.max_tokens = max_tokens
        self._api_key = api_key

    async def complete(self, messages: list[dict]) -> str:
        """调用 DeepSeek chat.completions（线程池内同步请求）并返回首个回复文本。

        - content 为空且因 token 预算截断（finish_reason="length"）时，在
          ``_post_chat_completion`` 内自动扩容重试一次（最多 ``DEEPSEEK_MAX_TOKENS``）。
        """
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "stream": False,
        }
        return await asyncio.to_thread(self._post_chat_completion, payload)

    def _post_chat_completion(self, payload: dict) -> str:
        """同步 POST chat/completions（urllib），返回回复文本或抛 ``RuntimeError``。

        - HTTP/网络/JSON 解码错误统一转为 ``RuntimeError``（遵守本模块「清晰错误」约定）。
        - 只返回 ``choices[0].message.content``：reasoning 模型的 ``reasoning_content``
          （思维链）不作为任务输出返回。
        - content 为空且因 token 预算截断（``finish_reason="length"``，reasoning 模型
          推理吃满预算）时扩容重试一次；重试后仍为空才抛 ``RuntimeError``。
        """
        import json
        import urllib.error
        import urllib.request

        url = f"{self.api_base}/chat/completions"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._api_key}",
        }
        for attempt in range(2):
            request = urllib.request.Request(
                url,
                data=json.dumps(payload).encode("utf-8"),
                method="POST",
                headers=headers,
            )
            try:
                with urllib.request.urlopen(request, timeout=180) as response:
                    data = json.loads(response.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace")[:500]
                raise RuntimeError(f"DeepSeek API 请求失败（HTTP {exc.code}）：{detail}") from exc
            except OSError as exc:
                raise RuntimeError(f"DeepSeek API 请求失败：{exc}") from exc
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                raise RuntimeError(f"DeepSeek API 响应不是合法 JSON：{exc}") from exc
            raw_usage = data.get("usage") or {}
            self.last_usage = TokenUsage(
                prompt_tokens=int(raw_usage.get("prompt_tokens") or 0),
                completion_tokens=int(raw_usage.get("completion_tokens") or 0),
                total_tokens=int(raw_usage.get("total_tokens") or 0),
                model=self.model,
            )
            choices = data.get("choices") or []
            if not choices:
                raise RuntimeError(f"DeepSeek API 响应缺少 choices：{str(data)[:500]}")
            choice = choices[0]
            content = (choice.get("message") or {}).get("content") or ""
            if content:
                self.last_usage.estimated_total = estimate_usage(
                    payload.get("messages") or [], str(content), None, self.model
                ).total_tokens
                return str(content)
            truncated = choice.get("finish_reason") == "length" and attempt == 0
            if truncated and payload.get("max_tokens", 0) < DEEPSEEK_MAX_TOKENS:
                payload["max_tokens"] = min(payload["max_tokens"] * 4, DEEPSEEK_MAX_TOKENS)
                continue
            raise RuntimeError(
                "DeepSeek API 回复 content 为空（reasoning 模型的思维链不作为任务输出）；"
                "请检查模型名、增大 max_tokens 或改用非推理模型。"
            )
        raise RuntimeError("DeepSeek API 回复 content 为空：扩容重试后仍未产出最终回答。")

    async def complete_with_tools(self, messages: list[dict], tools: list[dict]) -> ChatResponse:
        """DeepSeek 原生 function calling：请求带 ``tools``，解析 ``tool_calls``。

        - 保持思维链不外泄：只取 ``message.content``（文本）与
          ``message.tool_calls``（结构化调用），reasoning_content 不作为输出。
        - 截断（finish_reason="length"）时扩容重试一次，语义与 ``complete`` 一致。
        """
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "stream": False,
            "tools": tools,
        }
        return await asyncio.to_thread(self._post_chat_completion_with_tools, payload)

    def _post_chat_completion_with_tools(self, payload: dict) -> ChatResponse:
        """同步 POST chat/completions（urllib），解析文本与 tool_calls。"""
        import urllib.error
        import urllib.request

        url = f"{self.api_base}/chat/completions"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._api_key}",
        }
        for attempt in range(2):
            request = urllib.request.Request(
                url,
                data=json.dumps(payload).encode("utf-8"),
                method="POST",
                headers=headers,
            )
            try:
                with urllib.request.urlopen(request, timeout=180) as response:
                    data = json.loads(response.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace")[:500]
                raise RuntimeError(f"DeepSeek API 请求失败（HTTP {exc.code}）：{detail}") from exc
            except OSError as exc:
                raise RuntimeError(f"DeepSeek API 请求失败：{exc}") from exc
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                raise RuntimeError(f"DeepSeek API 响应不是合法 JSON：{exc}") from exc
            choices = data.get("choices") or []
            if not choices:
                raise RuntimeError(f"DeepSeek API 响应缺少 choices：{str(data)[:500]}")
            choice = choices[0]
            message = choice.get("message") or {}
            content = str(message.get("content") or "")
            raw_calls = message.get("tool_calls") or []
            calls: list[ToolCall] = []
            for raw_call in raw_calls:
                function = raw_call.get("function") or {}
                name = str(function.get("name") or "")
                if not name:
                    continue
                arguments = function.get("arguments") or "{}"
                try:
                    parsed_args = json.loads(arguments) if isinstance(arguments, str) else dict(arguments)
                except json.JSONDecodeError:
                    parsed_args = {}
                calls.append(ToolCall(id=str(raw_call.get("id") or uuid.uuid4().hex), name=name, args=dict(parsed_args)))
            if content or calls:
                raw_usage = data.get("usage") or {}
                usage = TokenUsage(
                    prompt_tokens=int(raw_usage.get("prompt_tokens") or 0),
                    completion_tokens=int(raw_usage.get("completion_tokens") or 0),
                    total_tokens=int(raw_usage.get("total_tokens") or 0),
                    model=self.model,
                    estimated_total=estimate_usage(payload.get("messages") or [], content, calls, self.model).total_tokens,
                )
                self.last_usage = usage
                return ChatResponse(text=content, tool_calls=calls, usage=usage)
            truncated = choice.get("finish_reason") == "length" and attempt == 0
            if truncated and payload.get("max_tokens", 0) < DEEPSEEK_MAX_TOKENS:
                payload["max_tokens"] = min(payload["max_tokens"] * 4, DEEPSEEK_MAX_TOKENS)
                continue
            raise RuntimeError(
                "DeepSeek API 回复 content/tool_calls 均为空（reasoning 模型的思维链不作为输出）；"
                "请检查模型名、增大 max_tokens 或改用非推理模型。"
            )
        raise RuntimeError("DeepSeek API 回复为空：扩容重试后仍未产出最终回答。")


class ChatModelFactory:
    """按 ``AgentConfig`` 选择模型后端。

    - ``create(None)`` / ``model_name`` 为空或 ``"deterministic"`` -> ``DeterministicClient``。
    - ``model_name`` 以 ``deepseek`` 开头或等于 ``"deepseek"`` -> ``DeepSeekClient``。
    - ``model_name`` 等于 ``"codex"``/``"custom"`` -> 解析当前 Codex 配置（config.toml），
      DeepSeek 供应商接入 ``DeepSeekClient``，OpenAI 系接入 ``OpenAIClient``。
    - ``model_name`` 以 ``gpt-``/``o1``/``o3`` 开头或等于 ``"openai"`` -> ``OpenAIClient``。
    - 其他未知 ``model_name`` 抛 ``ValueError``（明确提示改用 deterministic）。
    """

    def create(self, config: AgentConfig | dict | None = None) -> ChatModelClient:
        """构造模型客户端；缺省返回 ``DeterministicClient``。"""
        if config is None:
            return DeterministicClient()
        cfg = config if isinstance(config, AgentConfig) else AgentConfig.model_validate(config)
        model_name = (cfg.model.model_name or "").strip().lower()
        wire_api = (cfg.model.wire_api or "chat").lower()
        if wire_api not in ("chat", "responses", "anthropic"):
            raise ValueError(f"未知 wire_api：{wire_api!r}（支持 chat / responses / anthropic）")
        if not model_name or model_name == "deterministic":
            if cfg.react.tool_script:
                return DeterministicClient(tool_script=list(cfg.react.tool_script))
            return DeterministicClient()
        if wire_api != "chat":
            # T11.1 守卫：responses/anthropic 客户端由 T11.2 接入
            raise ValueError(f"wire_api={wire_api} 客户端尚未接入（T11.2 提供）")
        if model_name in ("codex", "custom"):
            return self._client_from_codex_config(cfg)
        if model_name == "deepseek" or model_name.startswith("deepseek-"):
            return self._deepseek_client(cfg, load_codex_model_config())
        if model_name == "openai" or model_name.startswith(("gpt-", "o1", "o3")):
            return OpenAIClient(
                model=cfg.model.model_name,
                api_key_env=cfg.model.api_key_env or "OPENAI_API_KEY",
                api_base=cfg.model.api_base,
            )
        raise ValueError(
            f"未知模型名称：{cfg.model.model_name!r}（支持 deterministic / openai / gpt-* / "
            "deepseek-* / codex）；无 API key 环境请使用 deterministic。"
        )

    def _deepseek_client(self, cfg: AgentConfig, codex: CodexProviderConfig | None) -> DeepSeekClient:
        """构造 DeepSeek 客户端：显式配置优先，否则取 Codex 配置或官方默认值。"""
        base_url, env_key = resolve_deepseek_defaults(codex)
        return DeepSeekClient(
            model=cfg.model.model_name,
            api_key_env=cfg.model.api_key_env or env_key,
            api_base=cfg.model.api_base or base_url,
            temperature=cfg.model.temperature,
            max_tokens=cfg.model.max_tokens,
        )

    def _client_from_codex_config(self, cfg: AgentConfig) -> ChatModelClient:
        """按当前 Codex 配置（config.toml）接入对话所用模型。"""
        codex = load_codex_model_config()
        if codex is None:
            raise ValueError(
                "无法解析 Codex 配置（~/.codex/config.toml 缺失或未配置 model_providers）；"
                "请直接指定模型名，例如 deepseek-v4-flash 或 openai/gpt-4o-mini。"
            )
        if codex.is_deepseek or codex.model_name.lower().startswith("deepseek"):
            return DeepSeekClient(
                model=codex.model_name or "deepseek-v4-flash",
                api_key_env=cfg.model.api_key_env or codex.api_key_env,
                api_base=cfg.model.api_base or codex.base_url or None,
                temperature=cfg.model.temperature,
                max_tokens=cfg.model.max_tokens,
            )
        if codex.model_name.startswith(("gpt-", "o1", "o3")):
            return OpenAIClient(
                model=codex.model_name,
                api_key_env=cfg.model.api_key_env or codex.api_key_env or "OPENAI_API_KEY",
                api_base=cfg.model.api_base or codex.base_url or None,
            )
        raise ValueError(
            f"Codex 配置的模型 {codex.model_name!r} 暂不支持自动接入；"
            "请直接指定模型名（deepseek-* / openai / gpt-*）。"
        )


class EventBus:
    """append-only 事件总线：``publish`` 追加，``query`` 按条件过滤查询。"""

    def __init__(self, events: list[Event] | None = None) -> None:
        self._events: list[Event] = list(events or [])

    def publish(self, event: Event) -> None:
        """追加一条事件（append-only，不提供删除/修改）。"""
        self._events.append(event)

    def query(self, *, thread_id: str | None = None, type: str | None = None) -> list[Event]:
        """按 thread_id / type 过滤查询（可选条件，均缺省返回全部）。"""
        results = list(self._events)
        if thread_id is not None:
            results = [event for event in results if event.thread_id == thread_id]
        if type is not None:
            results = [event for event in results if event.type == type]
        return results

    @property
    def events(self) -> list[Event]:
        """返回事件列表快照（不可变拷贝）。"""
        return list(self._events)


class AgentRuntime:
    """岗位 Agent 运行时：统一 ``reply`` / ``observe`` 异步接口 + 事件总线。"""

    def __init__(
        self,
        model_factory: ChatModelFactory | None = None,
        event_bus: EventBus | None = None,
        default_model: ModelConfig | None = None,
        tool_script: list[dict] | None = None,
        role_tool_scripts: dict[str, list[dict]] | None = None,
        usage_hook: Callable[[str, TokenUsage], None] | None = None,
    ) -> None:
        self._model_factory = model_factory if model_factory is not None else ChatModelFactory()
        self.event_bus = event_bus if event_bus is not None else EventBus()
        self._default_model = default_model
        self._tool_script = list(tool_script or [])
        self._role_tool_scripts = {
            role_id: list(script) for role_id, script in (role_tool_scripts or {}).items()
        }
        # token 用量上报：每次模型调用后回调（role_id, TokenUsage），供 TokenLedger 按角色记账
        self._usage_hook = usage_hook
        self.last_usage: TokenUsage | None = None

    async def reply(self, agent: Agent, messages: list[Message]) -> Message:
        """调用 Agent 的模型客户端，产出 ``Message(text)`` 并发布 ``agent_reply`` 事件。

        - thread_id 取最后一条消息的 thread_id，缺省用 agent.id。
        - 确定性客户端恒返回 ``MessageType.TEXT``；若未来模型决策 handoff，
          由客户端约定（本任务确定性后端不产出 handoff）。
        """
        client = self._model_factory.create(agent.config)
        thread_id = messages[-1].thread_id if messages else agent.id
        model_messages: list[dict] = [{"role": "system", "content": agent.system_prompt}]
        for message in messages:
            content = message.payload.get("content") or message.payload.get("text") or ""
            model_messages.append({"role": "user", "content": str(content)})
        content = await client.complete(model_messages)
        self._emit_usage(client.last_usage, agent.role_id)
        reply_message = Message(
            id=uuid.uuid4().hex,
            thread_id=thread_id,
            source=agent.id,
            target="",
            type=MessageType.TEXT,
            payload={"content": content},
        )
        self.event_bus.publish(
            Event(
                id=uuid.uuid4().hex,
                run_id=agent.id,
                thread_id=thread_id,
                type="agent_reply",
                actor=agent.id,
                payload={"message_id": reply_message.id},
            )
        )
        return reply_message

    async def observe(self, agent: Agent, messages: list[Message]) -> None:
        """把观察到的消息写入 ``agent.state`` 记忆（摘要=消息本身），按上限截断。"""
        max_messages = agent.config.context.max_messages
        merged = list(agent.state.messages) + list(messages)
        agent.state.messages = merged[-max_messages:]

    def client_for(self, role: Role) -> ChatModelClient:
        """构造岗位模型客户端（模型选择优先级：岗位偏好 > default_model > deterministic）。

        - 运行时 ``tool_script``（确定性演示脚本）注入客户端，无 API key 可全链路。
        - 真实模型（deepseek/openai）忽略 tool_script。
        """
        config = AgentConfig(model=self._model_config_for(role))
        role_script = self._role_tool_scripts.get(role.id)
        if role_script is not None:
            config.react.tool_script = list(role_script)
        elif self._tool_script:
            config.react.tool_script = list(self._tool_script)
        return self._model_factory.create(config)

    async def complete_for(self, role: Role, task: Task | None = None) -> str:
        """公开模型入口：经公开工厂构造客户端，返回岗位任务的模型完成文本。

        - ``task`` 缺省时按角色画像生成提示；否则附任务标题/描述上下文。
        - 模型选择优先级：岗位偏好模型 > 运行时 ``default_model`` > deterministic。
        - ``make_agent_handler`` 通过本方法执行岗位步骤，避免触碰私有成员。
        """
        client = self.client_for(role)
        content = await client.complete(_model_messages_for_task(role, task))
        self._emit_usage(client.last_usage, role.id)
        return content

    async def complete_for_with_tools(
        self,
        role: Role,
        task: Task | None,
        tools: list[dict],
    ) -> ChatResponse:
        """公开模型入口（工具模式）：岗位任务 + 工具 schema 的回复。

        ``make_agent_handler`` 工具模式循环首轮经本方法获取带工具调用的回复，
        后续轮次由 handler 直接持有客户端（``client_for``）继续对话。
        """
        client = self.client_for(role)
        response = await client.complete_with_tools(_model_messages_for_task(role, task), tools)
        self._emit_usage(response.usage, role.id)
        return response

    def report_usage(self, usage: TokenUsage | None, role: str = "") -> None:
        """上报一次模型调用用量（公开接口，供工具循环后续轮次直接调用）。"""
        self._emit_usage(usage, role)

    def _emit_usage(self, usage: TokenUsage | None, role: str = "") -> None:
        """记录最近用量并回调钩子（记账异常不中断流程）。"""
        if usage is None:
            return
        self.last_usage = usage
        if self._usage_hook is not None:
            try:
                self._usage_hook(role, usage)
            except Exception:  # noqa: BLE001 —— 记账钩子异常不中断流程
                pass

    def _model_config_for(self, role: Role) -> ModelConfig:
        """确定岗位模型配置：岗位偏好 > 运行时默认 > deterministic。"""
        if role.model:
            return ModelConfig(model_name=role.model)
        if self._default_model is not None:
            return self._default_model
        return ModelConfig(model_name="deterministic")


def _model_messages_for_task(role: Role, task: Task | None) -> list[dict]:
    """构造 deterministic 模型输入：角色画像 + 任务上下文（task 可缺省）。"""
    if task is None:
        return [
            {"role": "system", "content": f"{role.name}：{role.goal}"},
            {"role": "user", "content": f"请以 {role.name} 身份输出确定性执行摘要。"},
        ]
    return [
        {"role": "system", "content": f"{role.name}：{role.goal}"},
        {"role": "user", "content": f"执行任务 {task.id}：{task.title}（{task.desc}）"},
    ]


def make_agent_handler(
    runtime: AgentRuntime,
    role_registry: Any,
    catalog: Any = None,
    *,
    tool_session: ToolSession | None = None,
    max_rounds: int | None = None,
    interrupt_fn: Callable | None = None,
) -> NodeHandler:
    """构造注册进 ``WorkflowEngine`` 的 "agent" 节点 handler。

    双路径（v0.2）：
    - 确定性路径（``tool_session=None``，旧行为不变）：创建任务即完成，附产出物
      占位路径 ``artifacts/<role_id>/<task_id>.md``，248 测试保持全绿。
    - 工具路径（``tool_session`` 提供）：ReAct 工具循环——角色画像 + 技能上下文 +
      工具 schema → ``complete_with_tools`` → 逐条执行工具 → 追加 tool_result 消息
      → 循环至 ``max_rounds`` 或最终文本。危险工具经 ``interrupt_fn``（缺省
      ``langgraph.types.interrupt``）挂起走审批门；``--yes`` 下自动拒绝。
    - 分岗位质量门槛：QA 岗 ``run_tests`` 真实通过才 DONE，否则保持 ``review``；
      开发岗产出真实文件或最终文本即 DONE，循环耗尽/模型失败进入 ``review``。

    返回通道键（契约不变）：``{"tasks", "messages", "ledger"}``，危险工具审批
    追加 ``"decisions"``（``ApprovalRecord`` 审计记录）。
    """
    if interrupt_fn is None:
        from langgraph.types import interrupt as langgraph_interrupt

        interrupt_fn = langgraph_interrupt

    async def handler(state: ClusterState, node: WorkflowNode, ctx: NodeContext) -> dict[str, Any]:
        if node.role is None:
            raise ValueError(f"agent 节点 {node.id!r} 缺少 role 配置（node.role 为 None）")
        role = role_registry.get(node.role)
        project_id = state.project.id if state.project is not None else "demo"
        iteration_id = state.iterations[0].id if state.iterations else "iter:1"
        thread_id = ctx.spec.thread_id or "default"

        if tool_session is None:
            return await _deterministic_agent_step(runtime, role, node, ctx, project_id, iteration_id, thread_id, state)
        return await _tool_mode_agent_step(
            runtime,
            role,
            node,
            ctx,
            project_id,
            iteration_id,
            thread_id,
            state,
            tool_session,
            max_rounds,
            catalog,
            interrupt_fn,
        )

    return handler


async def _deterministic_agent_step(
    runtime: AgentRuntime,
    role: Role,
    node: WorkflowNode,
    ctx: NodeContext,
    project_id: str,
    iteration_id: str,
    thread_id: str,
    state: ClusterState,
) -> dict[str, Any]:
    """确定性路径（旧行为）：创建即完成，附产出物占位路径。"""
    task_id = uuid.uuid4().hex
    task = Task(
        id=task_id,
        project_id=project_id,
        iteration_id=iteration_id,
        title=f"节点 {ctx.node_id}（{role.name}）",
        desc=role.goal,
        assignee_role=role.id,
        status=TaskStatus.DONE,
        artifacts=[f"artifacts/{role.id}/{task_id}.md"],
    )
    content = await runtime.complete_for(role, task)
    last_usage = getattr(runtime, "last_usage", None)
    if last_usage is not None:
        task = task.model_copy(update={"tokens_used": last_usage.total_tokens})
    output = f"{role.name} 完成节点 {ctx.node_id} 的执行：{content}"
    message = Message(
        id=uuid.uuid4().hex,
        thread_id=thread_id,
        source=role.id,
        target="",
        type=MessageType.TEXT,
        payload={"content": output, "node": ctx.node_id, "task": task.id},
    )
    ctx.events.append(
        Event(
            id=uuid.uuid4().hex,
            run_id=ctx.run_id,
            thread_id=thread_id,
            type="agent_step",
            actor=role.id,
            payload={"task": task.id, "output": output, "node": ctx.node_id},
        )
    )
    ledger = (
        state.ledger
        if state.ledger is not None and state.ledger.task_id == task.id
        else Ledger(task_id=task.id)
    )
    ledger.progress.append(ProgressEntry(role=role.id, status="doing", verdict="ok", next_action="review"))
    return {"tasks": [task], "messages": [message], "ledger": ledger}


def _allowed_tool_names(role: Role, catalog: Any, registry: Any) -> list[str]:
    """确定岗位可用工具：技能目录交集 > 角色 tools 交集 > 只读兜底。"""
    if catalog is not None:
        allowed = set(catalog.allowed_tools(role))
    else:
        allowed = set(role.tools)
    names = set(registry.names())
    filtered = sorted(allowed & names)
    if not filtered:
        read_only = {"list_dir", "read_file", "grep", "glob", "git_status", "git_diff"}
        filtered = sorted(read_only & names)
    return filtered


def _tool_task_prompt(role: Role, task: Task) -> str:
    """工具模式的用户提示：任务 + 工具协议说明（工作区根目录已在 system 注入）。"""
    parts = [
        f"请以 {role.name} 身份在真实工作区执行任务 {task.id}：{task.title}（{task.desc}）。",
        "工作方式：需要修改文件/跑测试/git 时通过工具调用完成；全部工作结束后返回最终文本总结。"
        "危险工具（run_shell/run_python/delete_file/git_push）会自动走人工审批，请优先使用安全工具。",
    ]
    return "\n".join(parts)


async def _tool_mode_agent_step(
    runtime: AgentRuntime,
    role: Role,
    node: WorkflowNode,
    ctx: NodeContext,
    project_id: str,
    iteration_id: str,
    thread_id: str,
    state: ClusterState,
    session: ToolSession,
    max_rounds: int | None,
    catalog: Any,
    interrupt_fn: Callable,
) -> dict[str, Any]:
    """工具模式 ReAct 循环：真实工作区执行 + 危险工具审批门。"""
    # 节点隔离：重放缓存只在本节点生效（避免跨节点副作用工具被缓存）
    session.clear_replay()
    rounds = max_rounds if max_rounds is not None and max_rounds > 0 else 6
    allowed = _allowed_tool_names(role, catalog, session.registry)
    schemas = session.registry.as_openai_schemas(names=allowed)

    system_parts = [
        f"{role.name}：{role.goal}",
        f"岗位背景：{role.backstory}",
        f"工作区根目录：{session.workspace_root}",
        f"可用工具：{', '.join(allowed) or '（无）'}",
    ]
    if catalog is not None:
        for skill in catalog.mounted_skills(role):
            system_parts.append(format_skill_context(skill, DisclosureLevel.LEVEL_2))
    task = Task(
        id=uuid.uuid4().hex,
        project_id=project_id,
        iteration_id=iteration_id,
        title=f"节点 {ctx.node_id}（{role.name}）",
        desc=role.goal,
        assignee_role=role.id,
        status=TaskStatus.DOING,
        output_schema={"node": ctx.node_id, "mode": "tools"},
    )
    messages: list[dict] = [
        {"role": "system", "content": "\n".join(system_parts)},
        {"role": "user", "content": _tool_task_prompt(role, task)},
    ]
    client = runtime.client_for(role)

    out_messages: list[Message] = []
    approvals: list[ApprovalRecord] = []
    ledger = Ledger(task_id=task.id)
    written_paths: list[str] = []
    test_passed = False
    final_text = ""
    tool_calls_count = 0
    tokens_used = 0
    loop_error: str | None = None
    exhausted = True

    for _round in range(1, rounds + 1):
        try:
            response = await client.complete_with_tools(messages, schemas)
        except Exception as exc:  # noqa: BLE001 —— 模型故障不中断流程，任务进入 review
            loop_error = f"模型调用失败：{type(exc).__name__}: {exc}"
            exhausted = False
            break
        runtime.report_usage(response.usage, role.id)
        if response.usage is not None:
            tokens_used += response.usage.total_tokens
        if not response.tool_calls:
            final_text = response.text or f"{role.name}：任务完成。"
            exhausted = False
            break
        for call in response.tool_calls:
            tool_calls_count += 1
            out_messages.append(
                Message(
                    id=uuid.uuid4().hex,
                    thread_id=thread_id,
                    source=role.id,
                    target="",
                    type=MessageType.TOOL_CALL,
                    payload={"tool": call.name, "args": call.args, "round": _round, "task": task.id},
                )
            )
            result = await session.execute(call)
            if result.needs_approval:
                spec = session.registry.get(call.name)
                if spec.permission == ToolPermission.HUMAN_INTERACTION:
                    answer, approval = await _ask_user_interrupt(
                        session, call, role, node, ctx, thread_id, interrupt_fn
                    )
                    if approval is not None:
                        approvals.append(approval)
                    result = ToolResult(
                        id=call.id,
                        name=call.name,
                        ok=True,
                        output=answer,
                        duration=0.0,
                        args=dict(call.args),
                    )
                else:
                    result, approval = await _approve_dangerous_tool(
                        session, call, role, node, ctx, thread_id, interrupt_fn
                    )
                    if approval is not None:
                        approvals.append(approval)
            out_messages.append(
                Message(
                    id=uuid.uuid4().hex,
                    thread_id=thread_id,
                    source=role.id,
                    target="",
                    type=MessageType.TOOL_RESULT,
                    payload={
                        "tool": call.name,
                        "ok": result.ok,
                        "output": result.output[:2000],
                        "needs_approval": result.needs_approval,
                        "duration": round(result.duration, 3),
                        "task": task.id,
                    },
                )
            )
            ctx.events.append(
                Event(
                    id=uuid.uuid4().hex,
                    run_id=ctx.run_id,
                    thread_id=thread_id,
                    type="tool_result",
                    actor=role.id,
                    payload={
                        "tool": call.name,
                        "ok": result.ok,
                        "duration": round(result.duration, 3),
                        "node": ctx.node_id,
                        "task": task.id,
                    },
                )
            )
            if result.ok and call.name in ("write_file", "edit_file", "mkdir"):
                rel = str(call.args.get("path", "")).strip()
                if rel and rel not in written_paths:
                    written_paths.append(rel)
            if call.name == "run_tests" and result.ok:
                test_passed = True
            messages.append(
                {"role": "user", "content": f"[工具结果 {call.name} ok={result.ok}] {result.output[:1500]}"}
            )

    # ---- 分岗位质量门槛 ----
    qa_like = role.id in ("qa", "reviewer", "debugger") or role.kind.value in ("qa",)
    if qa_like:
        task_status = TaskStatus.DONE if test_passed else TaskStatus.REVIEW
    else:
        produced = bool(written_paths) or bool(final_text)
        task_status = (
            TaskStatus.DONE if (produced and loop_error is None and not exhausted) else TaskStatus.REVIEW
        )

    task = task.model_copy(
        update={"status": task_status, "artifacts": written_paths, "tokens_used": tokens_used}
    )
    ledger.progress.append(
        ProgressEntry(
            role=role.id,
            status=task_status.value,
            verdict="ok" if task_status == TaskStatus.DONE else "failed",
            next_action="review",
        )
    )
    final_summary = final_text or loop_error or f"{role.name}：工具循环耗尽（{rounds} 轮）未产出最终文本。"
    out_messages.append(
        Message(
            id=uuid.uuid4().hex,
            thread_id=thread_id,
            source=role.id,
            target="",
            type=MessageType.TEXT,
            payload={
                "content": final_summary,
                "node": ctx.node_id,
                "task": task.id,
                "status": task_status.value,
                "written": written_paths,
                "test_passed": test_passed,
                "tool_calls": tool_calls_count,
            },
        )
    )
    ctx.events.append(
        Event(
            id=uuid.uuid4().hex,
            run_id=ctx.run_id,
            thread_id=thread_id,
            type="agent_step",
            actor=role.id,
            payload={
                "task": task.id,
                "node": ctx.node_id,
                "status": task_status.value,
                "tool_calls": tool_calls_count,
                "written": len(written_paths),
                "test_passed": test_passed,
            },
        )
    )
    updates: dict[str, Any] = {"tasks": [task], "messages": out_messages, "ledger": ledger}
    if approvals:
        updates["decisions"] = approvals
    return updates


async def _approve_dangerous_tool(
    session: ToolSession,
    call: ToolCall,
    role: Role,
    node: WorkflowNode,
    ctx: NodeContext,
    thread_id: str,
    interrupt_fn: Callable,
) -> tuple[ToolResult, ApprovalRecord | None]:
    """危险工具审批：interrupt 挂起 → 人工 accept/reject → 执行或拒绝。

    - ``interrupt_fn`` 缺省为 langgraph interrupt（挂起流程，CLI 恢复）；
      无审批通道（直接调用测试）时自动拒绝，保证永不静默执行危险工具。
    - 恢复时 ``interrupt()`` 返回值可能是 list（首挂起语义）或
      ``HumanResponse``（恢复语义），统一归一化。
    """
    from datetime import datetime, timezone

    request = ActionRequest(
        id=f"tool-{call.id}",
        kind=GateKind.DANGEROUS_TOOL,
        title=f"危险工具调用：{call.name}",
        description=f"{role.name}（节点 {node.id}）请求执行危险工具 {call.name}，参数：{_safe_json_args(call.args)}",
        evidence={"node": node.id, "tool": call.name, "args": call.args, "run_id": ctx.run_id},
        risk_level="high",
        bypass_immune=True,
    )
    config = HumanInterruptConfig()
    payload = {
        "action_request": request,
        "config": config.model_dump(),
        "description": request.description,
    }
    # 中断恢复后节点整体重跑：同一危险调用已有审批决策时直接套用（幂等）
    cached = session.cached_approval(call)
    if cached is not None:
        decision = HumanResponse(type=cached, args={"reason": "复用上次审批决策（节点重跑）"})
    else:
        resumed = interrupt_fn([payload])  # langgraph interrupt 为同步函数（挂起/恢复都不 await）
        decision = resumed[0] if isinstance(resumed, list) else resumed
        if not isinstance(decision, HumanResponse):
            decision = HumanResponse.model_validate(decision)

    if decision.type == "accept":
        session.remember_approval(call, "accept")
        approved = await session.execute_approved(call)
        record = ApprovalRecord(by_role="human", type="accept", ts=datetime.now(timezone.utc))
        return approved, record
    session.remember_approval(call, "reject")
    reason = f"危险工具 {call.name} 被拒绝（人工/自动 {decision.type}）"
    if decision.args:
        reason += f"：{decision.args}"
    denied = ToolResult(
        id=call.id,
        name=call.name,
        ok=False,
        output=reason,
        needs_approval=False,
        error="rejected",
        args=call.args,
    )
    record = ApprovalRecord(
        by_role="human",
        type="reject",
        args={"reason": reason},
        ts=datetime.now(timezone.utc),
    )
    return denied, record


async def _ask_user_interrupt(
    session: ToolSession,
    call: ToolCall,
    role: Role,
    node: WorkflowNode,
    ctx: NodeContext,
    thread_id: str,
    interrupt_fn: Callable,
) -> tuple[str, ApprovalRecord | None]:
    """人工交互工具（ask_user）：interrupt 挂起 → 人工自由文本回答 → 返回回答文本。

    - ``decision.type == "response"`` 且 ``args.text`` 为回答；拒绝/其他视为未回答。
    - 节点重跑幂等：回答缓存在 ``session.approval_cache``（前缀 ``response:``）。
    """
    from datetime import datetime, timezone

    question = str(call.args.get("question") or "需求澄清问题")
    hint = str(call.args.get("hint") or "")
    request = ActionRequest(
        id=f"ask-{call.id}",
        kind=GateKind.HUMAN_INTERACTION,
        title=question,
        description=hint or question,
        evidence={"node": node.id, "tool": call.name, "run_id": ctx.run_id, "args": _safe_json_args(call.args)},
        risk_level="low",
        bypass_immune=False,
    )
    payload = {
        "action_request": request,
        "config": HumanInterruptConfig().model_dump(),
        "description": request.description,
    }
    cached = session.cached_approval(call)
    if cached is not None and cached.startswith("response:"):
        answer = cached[len("response:") :]
        decision = HumanResponse(type="response", args={"text": answer})
    else:
        resumed = interrupt_fn([payload])  # langgraph interrupt 同步（挂起/恢复都不 await）
        decision = resumed[0] if isinstance(resumed, list) else resumed
        if not isinstance(decision, HumanResponse):
            decision = HumanResponse.model_validate(decision)

    if decision.type == "response" and isinstance(decision.args, dict) and str(decision.args.get("text") or "").strip():
        answer = str(decision.args["text"]).strip()
        session.remember_approval(call, "response:" + answer)
        record = ApprovalRecord(
            by_role="human", type="response", args={"text": answer}, ts=datetime.now(timezone.utc)
        )
        return answer, record
    reason = f"问题未回答（{decision.type}）"
    session.remember_approval(call, "reject")
    record = ApprovalRecord(
        by_role="human", type="reject", args={"reason": reason}, ts=datetime.now(timezone.utc)
    )
    return reason, record


def _safe_json_args(args: dict) -> str:
    """参数的安全 JSON 摘要（截断长内容，避免审批界面刷屏）。"""
    safe = dict(args)
    for key in ("content", "code", "command"):
        if key in safe and isinstance(safe[key], str) and len(safe[key]) > 120:
            safe[key] = safe[key][:120] + "...(截断)"
    try:
        return json.dumps(safe, ensure_ascii=False, default=str)[:800]
    except (TypeError, ValueError):
        return repr(safe)[:800]
