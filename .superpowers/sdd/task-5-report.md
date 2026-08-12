# Task 5 报告：组织角色、运行时、会议与账本

- 提交：`485c7629fb360ec063978cf5c1847041c7b0e5d1`（`Task 5: 组织角色与会议运行时`）
- 状态：完成，全部测试绿（145 passed = 87 既有 + 58 新增）

## 实现摘要

### `src/agent_cluster/roles.py`（新增）

- `build_role_catalog() -> dict[str, Role]`：返回 12 岗位（pm/pmo/frontend/backend/algorithm/architect/qa/devops/docs/reviewer/debugger/governance），字段对齐 §3.1：goal（岗位目标）、backstory（岗位画像）、skills（`name@version` 字符串，优先引用 `examples/skills` 已存在的 `requirement-analysis@1.0.0` / `backend-api-design@2.1.0`，其余为占位技能）、tools、approval_scope（pm=需求确认+迭代验收+发布；architect=设计评审；qa=迭代验收；devops=发布；governance=进化生效；pmo=迭代验收）。
- `RoleRegistry`：`get(role_id)`（缺失抛 KeyError 含可用岗位）、`list()`（按 id 排序）、`filter_by_kind(kind)`、`default_role_ids(meeting_kind)`（§4.1 各会议默认参与岗位，meeting handler 据此确定 participants）。
- RoleKind 映射契约（模块 docstring 文档化）：pm→PM、pmo→PMO、frontend→FRONTEND、backend→BACKEND、algorithm→ALGORITHM、**architect→ARCH**、qa→QA、devops→DEVOPS；RoleKind 仅 8 类，辅助/门禁四岗归入相近类别：docs→PMO、reviewer→QA、debugger→QA、governance→PM。

### `src/agent_cluster/runtime.py`（新增）

- `ChatModelClient`（ABC：`async complete(messages: list[dict]) -> str`）。
- `DeterministicClient`：默认确定性后端——按消息内容与 persona 规则回显回复，同一输入恒得同一输出，无 API key，测试/演示默认。
- `OpenAIClient`：可选 OpenAI `chat.completions`；**构造期检查** `OPENAI_API_KEY`（缺省环境变量名，可经 `api_key_env` 覆盖），缺失立即抛 `RuntimeError`；`openai` 包未安装时 `complete()` 抛清晰错误（测试不依赖）。
- `ChatModelFactory.create(config: AgentConfig | dict | None) -> ChatModelClient`：缺省/`deterministic`→DeterministicClient；`openai`/`gpt-*`/`o1`/`o3`→OpenAIClient；未知名称抛 `ValueError`。
- `EventBus`：append-only `list[Event]`，`publish(event)` 追加，`query(*, thread_id=None, type=None)` 过滤查询，`events` 属性返回快照。
- `AgentRuntime`：`reply(agent, messages) -> Message`（经工厂创建模型客户端，产出 `Message(type=text, source=agent.id)` 并发布 `agent_reply` 事件）；`observe(agent, messages) -> None`（把观察到的消息写入 `agent.state.messages` 记忆，按 `context.max_messages` 截断）。
- `make_agent_handler(runtime, role_registry, catalog=None) -> NodeHandler`：确定性岗位步骤——按 `node.role` 加载 Role、新建 Task（status=doing）、确定性模型产出摘要文本、追加 `text` 消息、`ctx.events` 追加 `agent_step` 事件、更新当前任务账本并追加 `ProgressEntry`。`catalog` 为预留签名参数。

### `src/agent_cluster/meetings.py`（新增）

- `MeetingHost.run(kind, *, agenda, participants, project_id, state) -> Meeting`：7 类会议模板（§4.1）确定性生成——transcript（每个议程条目 × 每位参与者一条 `meeting_speech`）、decisions（每个议程条目一条，owner 由参与者轮转推导）、minutes_id（`minutes:<kind>:<ts>`）。
- 模板要点：kickoff（范围/MVP/职责/风险）、requirement_review（澄清+Given/When/Then 验收标准）、design_review（设计基线+开放问题）、daily_standup（昨日/今日/阻塞）、code_review（6 条规范 + LGTM/LBTM 结论，第 3 位发言者 LBTM 以便两结论都被覆盖）、retro（良好/不足/根因/改进项/进化信号）、release_review（验收摘要/回滚预案/Go-No-Go）。
- `MeetingHost.select_speaker(thread) -> str`：参与者轮转（thread 为空返回第一位；取最后发言人下一位循环）。
- `make_meeting_handler(host, role_registry) -> NodeHandler`：运行会议、行动项 Task（status=todo，assignee=决策 owner）、追加 `meeting_speech` 总结消息、`ctx.events` 追加 `meeting_held` 事件。

### `src/agent_cluster/ledger.py`（新增）

- `LedgerStore`：内存 dict 存储（文档化：后续可替换持久化）；`get(task_id)`（缺失抛 KeyError）、`update(ledger)`（upsert）、`append_fact`/`append_progress`/`mark_satisfied`/`mark_looping`（缺失自动建账本）。
- `TaskBoard`：五列（Backlog/Ready/InProgress/Review/Done）+ Blocked 标记列；`add(task)`（入 Backlog）、`move(task_id, to)`（列名大小写不敏感；合法流转：Backlog→Ready→InProgress→Review→Done；任意列→Blocked；Blocked→InProgress/Ready；同列无操作；非法跳转抛 `TaskBoardError`）、`by_iteration(iteration_id)`、`completion_rate(iteration_id)`（Done 数/总数，空迭代 0.0）、`to_state_channels() -> {"tasks": [...]}`（列映射回 TaskStatus：Backlog/Ready→todo、InProgress→doing、Review→review、Done→done、Blocked→blocked）。

### `src/agent_cluster/__init__.py`

- 导出：`RoleRegistry`、`build_role_catalog`；`ChatModelClient`、`DeterministicClient`、`OpenAIClient`、`ChatModelFactory`、`EventBus`、`AgentRuntime`、`make_agent_handler`；`MeetingHost`、`make_meeting_handler`；`LedgerStore`、`TaskBoard`、`TaskBoardError`、`COLUMNS`、`BLOCKED`。

## handler 通道契约（Task 7 CLI 依赖，勿变更）

- **agent handler** 返回 LangGraph channel 更新字典，键固定为：
  - `"tasks"`：`list[Task]`（该节点执行的任务，status=doing，每个 agent 节点新建一个）。
  - `"messages"`：`list[Message]`（一条 `text` 消息，source=岗位 id）。
  - `"ledger"`：`Ledger`（当前任务账本，追加一条 `ProgressEntry`；替换 `state.ledger` 通道，语义为「当前任务账本」）。
  - 事件经 `ctx.events` 追加 `Event(type="agent_step", actor=role.id, payload={task, output, node})`，不占通道键。
- **meeting handler** 返回 LangGraph channel 更新字典，键固定为：
  - `"meetings"`：`list[Meeting]`（本次会议记录）。
  - `"tasks"`：`list[Task]`（从会议决策提取的行动项，status=todo，assignee=决策 owner）。
  - `"messages"`：`list[Message]`（一条 `meeting_speech` 总结消息）。
  - 会议决策留在 `Meeting.decisions` 内，**不**写入 `decisions` 通道（该通道是 `list[ApprovalRecord]` 审批记录，语义不同）；事件经 `ctx.events` 追加 `Event(type="meeting_held")`。
- 端到端验证：`WorkflowEngine` + 两个真实 handler + `MemorySaver` 跑通 start→需求评审→架构 agent→代码评审→后端 agent→end，事件序列含 `meeting_held`×2/`agent_step`×2/`workflow_end`，终态通道 tasks 无重复（10 unique）。

## 测试

`tests/test_roles.py`（10）：12 岗位及 id 集合、每岗必填字段（含 skills 为 `name@version`）、architect→ARCH、辅助四岗 RoleKind 映射、审批范围契约、Registry get/list/缺岗 KeyError/filter_by_kind/default_role_ids。

`tests/test_runtime.py`（12）：DeterministicClient 确定性输出与空消息、OpenAIClient 缺 key 构造抛错、Factory 默认/未知模型、EventBus publish/query、AgentRuntime.reply 产出正确 source/type、observe 更新 agent.state、agent handler 通道键 `{"tasks","messages","ledger"}` 与事件追加、每次调用新建任务（无通道重复）。

`tests/test_meetings.py`（16）：7 类会议模板（参数化）transcript/decisions/minutes_id、确定性（两次 run 内容一致）、code_review 同时覆盖 LGTM/LBTM、select_speaker 轮转、meeting handler 通道键 `{"meetings","tasks","messages"}`、行动项 todo+assignee、`meeting_held` 事件、缺 meeting 类别抛错。

`tests/test_ledger.py`（20）：LedgerStore get 缺失 KeyError、append_fact/append_progress/update upsert、mark_satisfied/mark_looping；TaskBoard 线性流转、任意→Blocked→InProgress/Ready、非法跳转抛错、未知任务/未知列/重复 add 抛错、大小写不敏感列名、by_iteration、completion_rate 数学、to_state_channels 列映射。

### 命令输出

```
> uv run pytest -q tests/test_roles.py tests/test_runtime.py tests/test_meetings.py tests/test_ledger.py
58 passed in 0.88s

> uv run pytest -q
........................................................................ [ 49%]
........................................................................ [ 99%]
.                                                                        [100%]
145 passed in 1.62s
```

## 偏差与说明

- `make_agent_handler` 每次调用**新建** Task（status=doing），不复用通道中既有任务：`ClusterState.tasks` 使用 `operator.add` 追加 reducer，复用已存在对象并回写会重复追加（端到端验证确认该问题）；新建语义表达 todo→doing 认领，通道契约键恒定。meeting 行动项作为 todo 留在通道，构成待办 backlog。
- RoleKind 仅 8 类，docs/reviewer/debugger/governance 无对应枚举值，按职责归入 PMO/QA/QA/PM（模块 docstring + 测试固化）。
- meeting 决策留在 `Meeting.decisions`，不写入 `decisions` 通道（后者为审批记录语义，由 Task 4 gates 使用）。
- TaskBoard 的 Ready 列在 `TaskStatus` 中无对应值，`to_state_channels()` 导出时映射为 `todo`（文档化）。
- `LedgerStore.get` 对缺失任务抛 KeyError，而 append/mark 系列自动建账本（存储层语义，文档化）。
- `MeetingHost.run` 的 `state` 参数为签名契约（会议上下文），当前确定性实现不依赖其内容。
- 未创建 `evolution.py` / `metrics.py`（Task 6 范围）；未实现任务未要求的额外功能。
