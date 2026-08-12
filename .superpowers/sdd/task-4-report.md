# Task 4 报告：审批门（HITL interrupt）

- 提交：`81a1639efef9479814573bd8415301dbba8f19b4`（`Task 4: 审批门 HITL`）
- 状态：完成，全部测试绿（87 passed）

## 实现摘要

### `src/agent_cluster/gates.py`（新增）

- `GateError(Exception)`：审批门配置错误（gate 节点缺 `gate` 类别、`ApprovalGate.kind` 与节点类别不一致、无人值守模式非法等）。
- `make_gate_handler(role_scope=None, *, gate=None) -> NodeHandler`：
  - 工厂签名首参为 `role_scope: dict[str, GateKind] | None`（岗位→可审批门映射，本任务作为治理元信息接收，供 Task 6/7 使用）；可选 `gate: ApprovalGate` 提供 `interrupt_config`（缺省 `HumanInterruptConfig()` 全 True）。
  - 从 gate 节点构造 `ActionRequest`：`id=node.id`、`kind=node.gate`、`title`/`description` 取节点/流程规格、`risk_level="medium"`、`bypass_immune=False`。
  - `interrupt([{action_request, config, description}])` 挂起（首次执行不返回，`run()` 产出 `workflow_suspended`）；恢复后 `interrupt()` 返回 `HumanResponse`，写 `ApprovalRecord(by_role="human", type, args, ts=now_utc)`，返回 channel 更新 `{"gate_payloads": {node.gate: req}, "decisions": [record]}`（与 Task 3 门路由契约一致：`gate_payloads[node.gate].decisions[-1].type` 驱动 on_accept/on_reject/on_edit/on_response）。
- `approval_pending(graph, thread_id) -> ActionRequest | None`：见下节。
- `resolve_auto_response(req, auto_mode) -> HumanResponse`：`accept`（bypass_immune 时转 `reject`，原因 `"bypass-immune: 无人值守自动拒绝"`）、`reject`、`ask`（抛 `GateError`，必须人工）。

### `src/agent_cluster/models.py`（1 行修正）

- `HumanResponse.type` Literal 增加 `"reject"`。Task 4 契约要求 `resolve_auto_response` 返回 `HumanResponse(type="reject")`（§6.5 自动 DENY），而 Task 1 的 Literal 缺 `reject`；`workflow.py` 路由本就支持 reject 结论，此为必要的一致性修正（`test_human_response_type_validation` 不受影响）。

### `src/agent_cluster/__init__.py`

- 导出 `make_gate_handler`、`approval_pending`、`resolve_auto_response`、`GateError`。

## 测试（tests/test_gates.py，9 个，真实 interrupt/resume，不 mock）

1. `test_first_run_suspends_and_approval_pending_returns_request`：`run()` 挂起产出 `workflow_suspended`（payload node_id/thread_id），`approval_pending` 返回 `kind=release` 的 ActionRequest。
2. `test_accept_resume_completes_flow_and_records_decision`：`resume(HumanResponse(type="accept"))` 走完（`workflow_end`），事件序列 `release_gate → end`，`decisions` 恰一条 `accept` 记录。
3. `test_reject_resume_routes_to_rework_and_re_gates`：reject → `quality_gate → rework → quality_gate` 再次挂起；再 accept 完成；最终 `decisions` 为 `[reject, accept]`。
4. `test_edit_resume_routes_to_rework_branch`：edit → 返工分支，记录 args 落盘。
5. `test_bypass_immune_auto_reject_policy`：bypass-immune accept→reject（带原因）；reject→reject；非 immune accept→accept；`ask`/未知模式抛 `GateError`。
6. `test_audit_trail_record_ts_and_args`：ts 有值且带时区、args 完整落盘。
7. `test_approval_pending_returns_none_after_completion`：流程完成后返回 None。
8. `test_gate_handler_rejects_gate_node_without_kind`：缺类别 gate 节点抛 `GateError`。
9. `test_gate_factory_uses_provided_interrupt_config`：`ApprovalGate.interrupt_config` 透传到挂起载荷。

测试通过 `WorkflowEngine(handlers={"gate": make_gate_handler()})` + `CompiledWorkflow.run/resume` + `MemorySaver` 驱动（Task 3 契约优先路径）。

### 命令输出

```
> uv run pytest -q tests/test_gates.py
.........                                                                [100%]
9 passed in 1.01s

> uv run pytest -q
........................................................................ [ 82%]
...............                                                          [100%]
87 passed in 1.42s
```

## `approval_pending` 如何读取 `__interrupt__`

- 按任务契约先查 `graph.get_state({"configurable": {"thread_id": thread_id}}).values.get("__interrupt__")`（langgraph 0.2.x 存放位置：HumanInterrupt 列表）。
- installed langgraph 1.2.11 中挂起状态不在 `values` 里，而在 `StateSnapshot.interrupts`（`Interrupt` 元组，`Interrupt.value` 为传给 `interrupt()` 的载荷）——`values` 无 `__interrupt__` 键时回退到 `snapshot.interrupts`。
- 取首项：`Interrupt` 对象则解包 `.value`（list 取 `[0]`），0.2.x dict 直接使用；从载荷取 `action_request` 并用 `ActionRequest.model_validate` 还原；无挂起/无 `action_request` 返回 None。

## 偏差与说明

- `interrupt([payload])` 的返回归一化：langgraph 1.2.11 恢复时 `interrupt()` 原样返回 `Command(resume=...)` 的值（`HumanResponse` 对象，非 list），故不能直接 `[0]`；实现为 `decision = resumed[0] if isinstance(resumed, list) else resumed`，并兼容 dict 响应（`HumanResponse.model_validate`）。
- `HumanResponse.type` Literal 增加 `"reject"`（见上，任务契约要求的 §6.5 自动 DENY 响应类型）。
- `approval_pending` 增加 `snapshot.interrupts` 回退（任务注明 installed 1.2.11 的 `__interrupt__` 流步差异）。
- `HumanInterrupt` 载荷以同构 dict（`action_request/config/description`）构造：langgraph 1.2.11 中 `HumanInterrupt` 为 deprecated TypedDict（已迁移至 langchain），不引入已废弃类型。
- 测试读取挂起/终态使用 `CompiledWorkflow._compile_graph(checkpointer=...)`（私有方法）：`get_compiled_graph()` 返回的图未绑 checkpointer，`get_state` 会抛 "No checkpointer set"；这是 Task 3 API 下获取「绑定 checkpointer 的编译图」的唯一途径。
- 未创建 `roles.py`/`meetings.py`（Task 5 范围）；未实现任务未要求的额外功能。