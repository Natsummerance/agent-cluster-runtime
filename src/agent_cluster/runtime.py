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
import os
import uuid
from abc import ABC, abstractmethod
from typing import Any

from agent_cluster.models import (
    Agent,
    AgentConfig,
    ClusterState,
    Event,
    Ledger,
    Message,
    MessageType,
    ModelConfig,
    ProgressEntry,
    Role,
    Task,
    TaskStatus,
)
from agent_cluster.providers import (
    CodexProviderConfig,
    DEEPSEEK_MAX_TOKENS,
    load_codex_model_config,
    resolve_deepseek_defaults,
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
]


class ChatModelClient(ABC):
    """模型接入抽象：统一 ``complete(messages) -> str`` 异步接口。"""

    @abstractmethod
    async def complete(self, messages: list[dict]) -> str:
        """按消息列表（含 role/content）生成回复文本。"""


class DeterministicClient(ChatModelClient):
    """确定性后端：按消息内容与 persona 规则生成回复，无外部依赖。

    规则：空消息 -> persona 就绪语；否则回显最后一条消息内容并声明按确定性
    规则处理。同一输入恒得同一输出。
    """

    def __init__(self, persona: str = "确定性助手") -> None:
        self.persona = persona

    async def complete(self, messages: list[dict]) -> str:
        """返回基于最后一条消息内容的确定性回复。"""
        if not messages:
            return f"{self.persona}：收到空消息，准备就绪。"
        content = str(messages[-1].get("content", "")).strip()
        if not content:
            return f"{self.persona}：已确认消息序列（{len(messages)} 条），无待处理内容。"
        return f"{self.persona}：已收到「{content}」，按确定性规则完成处理。"


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
        return response.choices[0].message.content or ""


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
            choices = data.get("choices") or []
            if not choices:
                raise RuntimeError(f"DeepSeek API 响应缺少 choices：{str(data)[:500]}")
            choice = choices[0]
            content = (choice.get("message") or {}).get("content") or ""
            if content:
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
        if not model_name or model_name == "deterministic":
            return DeterministicClient()
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
    ) -> None:
        self._model_factory = model_factory if model_factory is not None else ChatModelFactory()
        self.event_bus = event_bus if event_bus is not None else EventBus()
        self._default_model = default_model

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

    async def complete_for(self, role: Role, task: Task | None = None) -> str:
        """公开模型入口：经公开工厂构造客户端，返回岗位任务的模型完成文本。

        - ``task`` 缺省时按角色画像生成提示；否则附任务标题/描述上下文。
        - 模型选择优先级：岗位偏好模型 > 运行时 ``default_model`` > deterministic。
        - ``make_agent_handler`` 通过本方法执行岗位步骤，避免触碰私有成员。
        """
        client = self._model_factory.create(AgentConfig(model=self._model_config_for(role)))
        return await client.complete(_model_messages_for_task(role, task))

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
) -> NodeHandler:
    """构造注册进 ``WorkflowEngine`` 的 "agent" 节点 handler（确定性岗位步骤）。

    步骤（对每个 agent 节点）：
    1. 按 ``node.role`` 从 ``role_registry`` 加载 ``Role``。
    2. 新建 ``Task``（status=done：确定性后端创建即完成，并携带产出物路径
       ``artifacts/<role_id>/<task_id>.md``；见模块 docstring 关于追加 reducer
       的说明，不做复用以免通道重复）。
    3. 用确定性模型产出执行摘要文本，追加 ``Message(type=text)``。
    4. 经 ``ctx.events`` 追加 ``Event(type="agent_step", actor=role.id)``。
    5. 更新当前任务账本（``Ledger``）追加 ``ProgressEntry``。

    返回通道键（契约，勿变更）：``{"tasks", "messages", "ledger"}``。
    ``catalog``（SkillCatalog）预留参数：本任务不参与执行逻辑，仅为签名契约。
    """
    async def handler(state: ClusterState, node: WorkflowNode, ctx: NodeContext) -> dict[str, Any]:
        if node.role is None:
            raise ValueError(f"agent 节点 {node.id!r} 缺少 role 配置（node.role 为 None）")
        role = role_registry.get(node.role)
        project_id = state.project.id if state.project is not None else "demo"
        iteration_id = state.iterations[0].id if state.iterations else "iter:1"
        thread_id = ctx.spec.thread_id or "default"

        # 1) 新建任务（status=done：确定性后端创建即完成，附产出物路径）
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

        # 2) 经运行时公开方法 complete_for 产出确定性执行摘要（不触碰私有成员）
        content = await runtime.complete_for(role, task)
        output = f"{role.name} 完成节点 {ctx.node_id} 的执行：{content}"

        # 3) 追加 text 消息
        message = Message(
            id=uuid.uuid4().hex,
            thread_id=thread_id,
            source=role.id,
            target="",
            type=MessageType.TEXT,
            payload={"content": output, "node": ctx.node_id, "task": task.id},
        )

        # 4) 追加 agent_step 事件（走 ctx.events，不占通道键）
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

        # 5) 更新当前任务账本
        ledger = state.ledger if state.ledger is not None and state.ledger.task_id == task.id else Ledger(task_id=task.id)
        ledger.progress.append(
            ProgressEntry(role=role.id, status="doing", verdict="ok", next_action="review")
        )

        return {"tasks": [task], "messages": [message], "ledger": ledger}

    return handler
