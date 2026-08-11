# 智能体集群运行时实施计划（Phase 1-3 全面实施）

> 依据：`T:\Programming\Project\codex\agent\agent-clusters\智能体集群设计方案.md`（v1.0）
> 目标：在 `T:\Programming\Project\codex\agent\agent-cluster-runtime\` 从零实现一个可运行的“多 agent 组织型全栈开发集群”运行时（Python + LangGraph），覆盖方案 §5 技术架构、§6 进化闭环、§8 路线图 Phase 1（MVP）+ Phase 2（组织化）+ Phase 3（进化化）核心。
> 参考仓库 `T:\Programming\Project\codex\agent\agent-clusters\` 只读，仅供设计借鉴，禁止复制其运行时代码。

## Global Constraints（所有任务必须遵守）

- 语言与运行时：Python 3.11+，包名 `agent_cluster`，`src/` 布局；依赖管理用 `uv`（`pyproject.toml` + `uv.lock`）。
- 依赖清单：`pydantic>=2.7`、`langgraph>=0.2.60`、`langgraph-checkpoint>=2.0`、`PyYAML>=6`；dev：`pytest>=8`、`pytest-asyncio`。
- 所有数据模型一律 pydantic v2（`from pydantic import BaseModel, Field, ConfigDict`），字段名与方案 §5.6 一致；复杂字段带 `Field(description=...)`。
- 测试框架 pytest + pytest-asyncio；测试文件在 `tests/`，命名 `test_*.py`；每个任务必须写行为测试（不 mock 关键逻辑），并 `uv run pytest` 全绿。
- 安装依赖：任务内使用 `uv add <pkg>` / `uv sync`（网络可用）；运行测试用 `uv run pytest -q`。
- 中文用户界面：模块 docstring、CLI 输出、README 用中文；代码标识符与提交信息用英文（提交信息可中英混合）。
- 编码 UTF-8；Windows 环境，所有 shell 命令在 PowerShell 下运行。
- 提交纪律：每个任务完成后 `git add -A && git commit`，提交信息以 `Task N: <summary>` 开头。
- 禁止引入参考仓库代码文件（仅可参考其设计思想）；gpt-pilot（自定义许可）与 autogen（CC-BY-4.0）仅作设计参考。

## 共享设计契约（后续任务引用）

- 模块文件规划（`src/agent_cluster/`）：
  - `models.py`：全部 pydantic 数据模型（Role/Agent/Task/Meeting/Proposal/Skill/Ledger/ApprovalGate/Message/ClusterState/Event 等）+ `GateKind` + 常量。
  - `skills.py`：SkillLoader / SkillRegistry / SkillCatalog + SKILL.md frontmatter 解析 + 渐进披露。
  - `workflow.py`：YAML 流程 DSL 解析 + `WorkflowEngine`（编译为 LangGraph StateGraph）+ 事件流运行。
  - `gates.py`：审批门实现（interrupt HITL）+ ApprovalRecord + 条件路由。
  - `roles.py`：12 岗位 catalog（Role 定义）。
  - `runtime.py`：AgentRuntime（reply/observe）+ ChatModelClient 抽象（默认确定性 backend）+ EventBus。
  - `meetings.py`：MeetingHost + 7 类会议子图。
  - `ledger.py`：Ledger 账本 + 任务板 Board。
  - `evolution.py`：Signal/Candidate/EvolutionProposal + 六步进化闭环 + MetricsCollector。
  - `cli.py`：CLI 入口（`agent-cluster`）。
- 包公开导出：`src/agent_cluster/__init__.py` 导出主要类；`src/agent_cluster/__main__.py` 支持 `python -m agent_cluster`。
- 消息模型（§5.3）：`Message{id, thread_id, source, target, type, payload, ts}`；type 枚举：`text/handoff/meeting_speech/proposal/approval/tool_call/tool_result/stop`。
- 共享状态（§5.3）：`ClusterState` 字段含 `project/iterations/tasks/meetings/ledger/gate_payloads/decisions/skill_catalog/messages`，list 字段全部用 reducer 追加（LangGraph `add` 或自定义 reducer）。
- 审批门清单（§5.4）：`GateKind = requirement_confirmation / design_review / iteration_acceptance / release / evolution_apply / dangerous_tool`。
- 会议类型（§4.1）：`kickoff / requirement_review / design_review / daily_standup / code_review / retro / release_review`。
- 岗位（§3.1，12 岗）：`pm / pmo / frontend / backend / algorithm / architect / qa / devops / docs / reviewer / debugger / governance`；每岗含 goal/backstory/skills/tools/approval_scope。
- YAML 流程 DSL（ChatDev 风格，`WorkflowEngine` 编译为图）：
  ```yaml
  name: <流程名>
  max_iterations: 20           # 防死循环：总节点执行上限，编译期校验必须 ≥ 节点总数（ChatDev loop_counter 思路）
  thread_id: "proj:demo:iter:1"
  nodes:
    - {id: start, type: start}
    - {id: requirement_review, type: meeting, meeting: requirement_review}
    - {id: requirement_gate, type: gate, gate: requirement_confirmation}
    - {id: design, type: agent, role: architect}
    - {id: code_review, type: meeting, meeting: code_review}
    - {id: test, type: agent, role: qa}
    - {id: release_gate, type: gate, gate: release}
    - {id: end, type: end}
  edges:
    - {from: start, to: requirement_review}
    - {from: requirement_gate, to: design, on_accept: design, on_reject: requirement_review, on_edit: design_review}
    ...
  ```
  - 节点类型：`start/end/agent/meeting/gate/parallel`；`agent` 节点执行指定岗位（走 AgentRuntime）；`meeting` 节点跑会议子图；`gate` 节点触发 interrupt 审批；`parallel` 节点并行跑多个子节点（fan-out/fan-in）。
  - 边：`from/to`；gate 后允许 `on_accept/on_reject/on_edit/on_response` 条件路由（缺省回落到 `to`）；其余边默认顺序流转。
  - 语义：`max_iterations` = 单次运行总节点执行上限，编译期校验必须 ≥ 节点总数；线性流程节点数不得大于该值，运行时累计执行节点数超过即抛 `WorkflowLoopError`。
  - 编译规则：非法节点引用/缺边/重复 id 一律抛 `WorkflowValidationError`（含精确报错信息）。
- 事件模型（§5.7）：`Event{id, run_id, thread_id, type, actor, payload, ts}`；type：`node_start/node_end/meeting/approval_created/approval_resolved/tool_call/metrics/evolution_*`；EventBus 为 append-only 列表。
- 运行方式：`WorkflowEngine.compile(yaml_text) -> CompiledWorkflow`；`CompiledWorkflow.run(initial_state) -> AsyncIterator[Event]`；审批通过 `WorkflowEngine.resume(thread_id, decision)` 恢复。


## Task 1: 工程骨架与数据模型

- 目标：建立可安装的 Python 工程骨架，实现 §5.6 全部数据模型与 §5.3 消息/状态模型，并保证测试全绿。
- 产出：
  - `pyproject.toml`（uv 工程，包 `agent_cluster`，`[build-system]` 用 hatchling 或 setuptools；含上述依赖与 pytest 配置 `[tool.pytest.ini_options]`：`asyncio_mode=auto`、`testpaths=["tests"]`）。
  - `src/agent_cluster/__init__.py`、`src/agent_cluster/__main__.py`（打印版本与用法占位）。
  - `src/agent_cluster/models.py`：实现以下 pydantic v2 模型（字段与方案 §5.6 一致，可增补）：
    - `Role`（id/name/kind/goal/backstory/skills/tools/model/approval_scope，kind 为 Literal 八类）
    - `AgentConfig`（model/rea ct/injection/context 四件套，用 `ModelConfig`/`ReActConfig`/`InjectionConfig`/`ContextConfig` 子模型，字段合理默认）
    - `Agent`（id/role_id/name/system_prompt/state/skills/tools/config）
    - `Task`（id/project_id/iteration_id/title/desc/acceptance_criteria/assignee_role/depends_on/status/artifacts/output_schema）
    - `Meeting`（id/project_id/kind/agenda/transcript/decisions/minutes_id）
    - `Decision`（id/topic/conclusion/reason/owner/ts）
    - `Proposal`（id/author_role/target/change/rationale/impact/status/votes/effective_version；target Literal 四类，status Literal 五态）
    - `Vote`（by_role/verdict/reason/ts）
    - `Skill`（name/version/description/license/allowed_tools/dir/markdown/disclosure_level/resource_files）
    - `Ledger`（task_id/facts/plan/progress/is_satisfied/is_looping；progress 为 `ProgressEntry{role,status,verdict,next_action}`）
    - `ApprovalGate`（id/kind/node/interrupt_config/payload/decisions）
    - `ApprovalRecord`（by_role/type/args/ts）
    - `ActionRequest`（id/kind/title/description/evidence/risk_level/bypass_immune）
    - `HumanInterruptConfig`（allow_ignore/allow_respond/allow_edit/allow_accept 均 bool 默认 True）
    - `HumanResponse`（type Literal["accept","ignore","response","edit"]，args 任意）
    - `Message`（id/thread_id/source/target/type/payload/ts；type Literal 含 handoff/meeting_speech/proposal/approval/tool_call/tool_result/stop）
    - `Event`（id/run_id/thread_id/type/actor/payload/ts）
    - `ClusterState`（project/iterations/tasks/meetings/ledger/gate_payloads/decisions/skill_catalog/messages，list 字段默认空；提供 `Project{id,name,vision,status,created_at}` 与 `Iteration{id,project_id,number,goal,start_date,end_date,status}`）
    - `GateKind` 枚举（六类）、`MessageType` 枚举、`MeetingKind` 枚举（七类）、`TaskStatus` 枚举、`ProposalStatus`/`ProposalTarget` 枚举、`RoleKind` 枚举（八类）。
  - `tests/test_models.py`：覆盖模型构造默认值、必填字段校验、枚举合法性、`ClusterState` 字段类型。
- 验收：`uv sync` 成功；`uv run pytest -q` 全绿；`uv run python -c "import agent_cluster.models"` 可导入。

## Task 2: 技能层（SKILL.md 加载与渐进披露）

- 目标：实现 §5.5 技能注册与加载：目录扫描、frontmatter 解析、版本/兼容、按角色挂载、三级渐进披露。
- 产出：
  - `src/agent_cluster/skills.py`：
    - `SkillFrontmatter`（pydantic：name/description 必填，license/compatibility/allowed_tools/version 可选）。
    - `SkillLoader`：`list_skills(root) -> list[Skill]` 递归扫描目录树，识别 `SKILL.md`；`load(dir) -> Skill` 解析 frontmatter（用 `PyYAML` safe_load 解析 `---` 块）+ 正文 markdown；资源文件按 `scripts/references/assets` 子目录分类；非法 frontmatter 抛 `SkillError`。
    - `SkillRegistry`：`register(skill, source)`、`get(name, version=None)`、`list()`；支持 `@org/name` 源前缀；`name+version` 去重（同版本覆盖报错或按规则告警）；`compatibility` 约束。
    - `SkillCatalog`：按角色挂载——`mount(role, skills)` 只挂载 `Role.skills` 指定的 `name@version`；`allowed_tools(role)` 返回 技能 allowed_tools ∩ 角色 tools。
    - 渐进披露：`DisclosureLevel`（1=仅 frontmatter 建目录，2=加载正文，3=登记资源文件）；`format_skill_context(skill, level)` 输出 `<skill name="...">` 锚块，level 2/3 追加正文与资源清单。
  - `examples/skills/`：至少 2 个示例技能包（如 `requirement-analysis/SKILL.md`、`backend-api-design/SKILL.md`），frontmatter 含 name/description/version/allowed_tools。
  - `tests/test_skills.py`：解析示例技能、缺 name 报错、版本去重、按角色挂载交集、三级披露内容差异。
- 验收：测试全绿；可从 `examples/skills` 加载出 ≥2 个技能；`format_skill_context` 三级输出逐级增加内容。

## Task 3: 流程引擎（YAML→StateGraph 编译与事件流运行）

- 目标：实现 §5.1/§5.8 `WorkflowEngine`：把 §共享设计契约 的 YAML DSL 编译为 LangGraph `StateGraph`，支持 start/end/agent/meeting/gate/parallel 节点与条件路由、loop_counter 防死循环、事件流输出。
- 产出：
  - `src/agent_cluster/workflow.py`：
    - `WorkflowNode`/`WorkflowEdge`/`WorkflowSpec`（pydantic，字段对齐 DSL）。
    - `WorkflowValidationError`（含节点/边/字段级报错）。
    - `WorkflowEngine.compile(yaml_text: str) -> CompiledWorkflow`：
      - 解析 YAML → 校验（重复 id、引用不存在的节点、start/end 缺失或重复、边必须 from→to 存在、gate 节点必须有后续边）→ 构建 `StateGraph(ClusterState)`。
      - `start` 节点初始化运行；`end` 节点终止；`agent` 节点调用角色执行器（见 Task 5，先留可注入的 `node_handlers` 字典接口）；`meeting` 节点调用会议执行器（Task 5 注入）；`gate` 节点调用审批门（Task 4 注入）；`parallel` 节点 fan-out 并行子节点 + fan-in 合并。
      - 未实现 handler 的节点类型可先提供默认占位实现（写 Event 后返回原状态），保证编译与运行不中断。
      - `loop_counter`：记录每轮主循环次数，超过 `max_iterations` 抛 `WorkflowLoopError`。
    - `CompiledWorkflow.run(initial: dict) -> AsyncIterator[Event]`：`astream` 运行，产出 `node_start/node_end/meeting/gate_created/...` 事件；`CompiledWorkflow.get_graph()` 返回图描述（节点/边列表）供测试断言。
  - `tests/test_workflow.py`：编译合法 YAML（含 gate 条件路由与 parallel）、编译非法 YAML 逐项抛错、运行一条简单流程产生完整事件序列、loop_counter 超限抛错。
- 验收：测试全绿；`examples/flows/fullstack-sprint.yaml`（见 Task 7，可先建最小版）可编译。

## Task 4: 审批门（HITL interrupt）

- 目标：实现 §5.4 审批门：`interrupt` 挂起、`HumanResponse` 恢复、条件路由、审批记录落状态、bypass-immune 无人值守自动拒绝。
- 产出：
  - `src/agent_cluster/gates.py`：
    - `GateNode`：在 gate 节点内构造 `HumanInterrupt{action_request, config, description}` 并 `interrupt([req])[0]`；恢复后写 `ApprovalRecord` 进 `decisions`，把响应写入 `gate_payloads`。
    - `resume_decision(thread_id, response: HumanResponse)`：用 `Command(resume=response)` 恢复图；`on_accept/on_reject/on_edit/on_response` 路由由编译期从边配置解析。
    - `approval_pending(thread_id) -> ActionRequest | None`：查询当前挂起的审批（供 CLI/测试）。
    - 安全：`bypass_immune=True` 且无人值守（`allow_ignore` 且无人工响应）→ 自动 DENY（返回 reject 响应并记录原因）。
  - `tests/test_gates.py`：用 `MemorySaver` 跑一条含 gate 的流程——首次运行中断（`approval_pending` 返回 ActionRequest）、`accept` 恢复走 on_accept 分支、`reject` 恢复走 on_reject 分支、`edit` 恢复走 on_edit 分支、审批记录完整落盘、bypass-immune 自动拒绝。
- 验收：测试全绿；gate 节点前后状态与事件可审计。

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

## Task 6: 进化闭环与度量（Phase 3）

- 目标：实现 §6.2 六步进化闭环（收集→提炼→提案→评审门→生效→回滚）+ §6.3 绩效度量 + 权限治理。
- 产出：
  - `src/agent_cluster/evolution.py`：
    - `Signal{id, type, source, evidence, severity, ts}`；`Candidate{category, target, change, evidence, expected_impact}`；`EvolutionProposal`（含 title/change_diff/affected_roles/affected_workflows/risk_level/validation_plan/rollback_plan/owner/status，**缺 rollback_plan 时提交校验失败**）。
    - `EvolutionEngine`：状态机 `draft→voting→approved/rejected→applied→rolled_back`；六步闭环方法：`collect(events)->list[Signal]`（规则：返工率>阈值、评审通过率<阈值等）→ `distill(signals)->list[Candidate]`（去重合并）→ `propose(candidate)->Proposal`（强制 diff+回滚方案）→ `review(proposal, approver) -> approved/rejected`（L3 组织流程必须人工标志；bypass-immune 无人值守自动拒绝）→ `apply(proposal)`（版本化：`effective_version` 自增；灰度标志）→ `rollback(proposal, reason)`（回滚日志写入事件总线）。
    - 安全约束：`proposal.change` 不得含“修改自身权限”类操作（校验函数）；`apply`/`rollback` 均写审计 Event。
  - `src/agent_cluster/metrics.py`：`MetricsCollector`：`record(metric_name, value, tags)` + `snapshot() -> MetricsSnapshot`；内置指标：`review_pass_rate / rework_rate / action_item_close_rate / loop_iterations / gate_wait_seconds`；`evaluate(snapshot) -> list[Signal]` 阈值规则（返工率>30% 连续 2 迭代、评审通过率<60% 等）。
  - `tests/test_evolution.py`、`tests/test_metrics.py`：闭环全流程（含缺回滚方案被拒、自我扩权被拒、L3 人工标志、灰度应用、回滚写日志）、度量阈值触发 Signal。
- 验收：测试全绿；闭环每步产出结构化对象并全程审计。

## Task 7: CLI、示例流程与集成（Phase 1 闭环打通）

- 目标：把 Task 1-6 集成成可运行的 CLI，跑通「需求评审→设计评审→开发→代码评审→测试→发布评审」一条链（含审批交互），产出 README 与示例。
- 产出：
  - `src/agent_cluster/cli.py`（入口 `agent-cluster`，`pyproject.toml` 配 `[project.scripts]`）：
    - `agent-cluster run --flow examples/flows/fullstack-sprint.yaml --project <dir>`：编译流程、`MemorySaver` 运行、遇 gate 时打印 ActionRequest 并交互读取 `accept/reject/edit <内容>/response <内容>`，恢复运行；结束后打印事件摘要与产出物。
    - `agent-cluster skills list --root examples/skills`：列出技能目录。
    - `agent-cluster roles list`：列出 12 岗位。
    - `agent-cluster proposals submit --title ... --rollback-plan ...`：进化提案提交/审批/回滚演示。
    - `agent-cluster metrics demo`：度量采集与信号触发演示。
    - 无交互模式：`--yes`（自动 accept 所有审批，用于集成测试与演示）。
  - `examples/flows/fullstack-sprint.yaml`：完整 MVP 链（含 start/end、需求评审 meeting、需求确认 gate、设计 agent、设计评审 meeting、设计 gate、前后端并行开发 parallel、代码评审 meeting、测试 agent、迭代验收 gate、发布 agent、发布 gate；带 on_reject 返工边）。
  - `examples/skills/`：补齐 3-4 个岗位技能包（frontend-design、backend-api-design、requirement-analysis、qa-testing）。
  - `README.md`：项目简介、架构图（mermaid）、安装运行、CLI 用法、示例流程说明、与参考项目映射表（8 行）、许可说明（gpt-pilot/autogen 仅参考）。
  - `tests/test_integration.py`：无 LLM 环境 `--yes` 跑完整流程：事件序列包含全部会议/门/开发节点、任务板全部 Done、审批记录数量正确、产出物存在。
- 验收：`uv run pytest -q` 全绿；`uv run agent-cluster --help` 与各子命令可运行；集成测试跑通完整 MVP 闭环。

## 收尾要求（全部任务完成后由 controller 执行）

- 最终整体评审（code-reviewer）：一致性、规范符合、无残留 TODO/FIXME、测试干净。
- 使用 finishing-a-development-branch 流程收尾（本地仓库无 remote：验证全量测试 → 保持 main 分支现状，向用户汇报）。
