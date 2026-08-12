## Task 5: 组织角色、运行时、会议与账本

- 目标：实现 §3 组织架构与 §5.1 角色执行层/记忆层：12 岗位 catalog、AgentRuntime（reply/observe）、可插拔 ChatModelClient、7 类会议子图、Ledger 账本与任务板。
- 产出：
  - `src/agent_cluster/roles.py`：`build_role_catalog() -> dict[str, Role]` 返回 12 岗位（§3.1 字段对齐：goal/backstory/skills/tools/approval_scope）；`RoleRegistry`（get/list/filter_by_kind）。
  - `src/agent_cluster/runtime.py`：
    - `ChatModelClient`（抽象基类：`async def complete(messages) -> str`）；`DeterministicClient`（默认后端：按消息内容与角色规则生成确定性回复，无需 API key，用于测试与演示）；`OpenAIClient`（可选：`OPENAI_API_KEY` 存在时可用，`chat.completions` 实现；缺 key 时构造抛错或回退说明）。`ChatModelFactory.create(config) -> ChatModelClient`。
    - `AgentRuntime`：`async reply(agent, msgs) -> Message`、`async observe(agent, msgs) -> None`（更新 agent.state）；`run_agent_node(role_id, state, context) -> ClusterState`（执行岗位任务：更新 task 状态、写事件、产出 artifacts）。
    - `EventBus`：append-only 事件列表 + `publish(Event)` + `query(thread_id=..., type=...)`。
  - `src/agent_cluster/meetings.py`：
    - `MeetingHost`：`async run(meeting_kind, agenda, participants, state) -> Meeting`（生成 transcript/decisions/行动项；无 LLM 时按议程模板产生结构化纪要）；`select_speaker(thread)`（轮转/主持人优先规则）。
    - 7 类会议模板：`kickoff/requirement_review/design_review/daily_standup/code_review/retro/release_review`，各自定义议程模板、产出（如站会=阻塞清单+行动项、复盘=根因+改进项+进化提案）。
  - `src/agent_cluster/ledger.py`：`LedgerStore`（按 task_id 读写 Ledger，facts/plan/progress/is_satisfied/is_looping）；`TaskBoard`（Backlog/Ready/InProgress/Review/Done 状态流转 + `move(task_id, to)` 校验合法迁移 + 按迭代聚合完成率）。
  - `tests/test_roles.py`、`tests/test_runtime.py`、`tests/test_meetings.py`、`tests/test_ledger.py`：岗位数=12、DeterministicClient 返回确定性内容、AgentRuntime 走完 reply→observe 状态更新、MeetingHost 产出 7 类会议纪要含决策/行动项、TaskBoard 合法/非法迁移。
- 验收：测试全绿；无 API key 环境可完整跑通会议与角色执行。


