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


