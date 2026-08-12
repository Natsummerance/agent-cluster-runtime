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


