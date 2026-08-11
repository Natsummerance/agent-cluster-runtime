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


