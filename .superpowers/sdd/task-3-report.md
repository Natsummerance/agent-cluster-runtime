# Task 3 报告：流程引擎（YAML→StateGraph 编译与事件流运行）

## 1. 实现摘要

- `src/agent_cluster/workflow.py`（新增，约 470 行）：
  - `WorkflowValidationError` / `WorkflowLoopError` 两个异常。
  - `WorkflowNode`（id/type/meeting/role/gate/children）、`WorkflowEdge`（`from_` 用 pydantic alias 映射 YAML 的 `from`；`to`/`on_accept`/`on_reject`/`on_edit`/`on_response`）、`WorkflowSpec`（name 必填，description 默认空串，max_iterations 默认 10 且 >0，thread_id 默认空串，nodes/edges 必填）。
  - `NodeContext`（node_id/spec/events/run_id/loop_count）；`NodeHandler` 类型别名。
  - `WorkflowEngine.compile(yaml_text)`：`yaml.safe_load` → `WorkflowSpec.model_validate`（pydantic ValidationError 包装为 WorkflowValidationError）→ `_validate_spec` → `CompiledWorkflow`（内部构建 `StateGraph(ClusterState)`）。
  - `CompiledWorkflow.run(initial=None, *, thread_id=None)`：`graph.astream(initial_state, config={"recursion_limit": max_iterations*4, "configurable": {"thread_id": ...}})`，产出 `workflow_start` → 每节点 `node_start`/`node_end` → `workflow_end`，全部累计进 `events`。
  - 编译校验：重复节点 id；边 from/to 及 on_* 目标引用不存在的节点；start/end 缺失或重复；start 必须有出边；end 不允许有出边；gate 必须至少一条出边；parallel 必须声明 children、子节点必须存在、必须有 fan-in 出边；边必须有 from/to（缺字段走 pydantic 校验）。
  - 节点语义：`start` 在初始状态缺 project/iterations 时补默认值（project id 从 `thread_id="proj:<id>:iter:<n>"` 推导，回退流程名；iteration id `{project.id}:iter:1`），走第一条出边；`end` 为终止节点（返回 None，接 `END`）；`agent`/`meeting`/`gate` 查 `handlers`（按节点类型注册），未注册走默认占位 handler（返回 `{}`，不改状态不发额外事件）；`parallel` 内置 fan-out/fan-in（见 §4）。
  - 防死循环：`run()` 内统计每次运行累计执行节点数，超过 `spec.max_iterations` 抛 `WorkflowLoopError`；LangGraph `GraphRecursionError`（recursion_limit 触顶）也转为 `WorkflowLoopError`。每条边按 on_reject/on_edit 等天然支持返工回环，无需额外机制。
- `src/agent_cluster/models.py`（Task 1  sanctioned retrofit，最小改动）：
  - `ClusterState` 五个 list 字段改为 `Annotated[list[X], operator.add]`：`iterations/tasks/meetings/decisions/messages`，LangGraph 频道追加而非覆盖。
  - `ActionRequest` 新增 `decisions: list[ApprovalRecord]`（default_factory=list，向后兼容）：Task 3 门路由契约的载荷载体（Task 1 模型没有该字段，简报路由描述"ActionRequest 的 .decisions"正是此意）。
  - 其余字段与模型不动；Task 1 的 33 个测试原样通过。
- `src/agent_cluster/__init__.py`：导出 `WorkflowEngine/CompiledWorkflow/WorkflowSpec/WorkflowNode/WorkflowEdge/WorkflowValidationError/WorkflowLoopError/NodeContext/NodeHandler`。
- `tests/test_workflow.py`（新增 21 个测试）。

## 2. 测试与命令输出

新增测试覆盖：合法 YAML（含 gate 条件路由 + parallel）编译与 `get_graph()` 断言；非法 YAML 逐项抛 `WorkflowValidationError`（重复 id、缺失边终点、无 start、双 start、gate 无出边、边缺 to、边起点悬空、parallel 缺 children、end 有出边、非映射顶层）；简单流程完整事件序列；顺序链；start 默认 Project/Iteration 与初始状态保留；gate 条件路由（fake handler 先 reject 走返工边、再 accept 到 end）；gate 无 handler 时按缺省 accept 路由；parallel 全部子节点运行；loop 超限抛 `WorkflowLoopError`；ClusterState reducer 注解契约；ActionRequest.decisions。

`uv run pytest -q` 全量输出（73 passed = 既有 52 + 新增 21）：

```
$ uv run pytest -q
........................................................................ [ 98%]
.                                                                        [100%]
73 passed in 0.94s
```

`uv run pytest tests/test_workflow.py -q`：

```
$ uv run pytest tests/test_workflow.py -q
.....................                                                    [100%]
21 passed in 0.81s
```

## 3. gate_payloads / 审批载荷契约（Task 4 gates.py 必须遵守）

- **存储位置与键**：`ClusterState.gate_payloads: dict[GateKind, ActionRequest]`（Task 1 已锁定键类型为 GateKind，键 = gate 节点的 `node.gate` 字段；简报原文写 `gate_payloads[node_id]`，因 Task 1 模型约束改为按 GateKind 键，见 §5 偏离说明）。
- **载荷**：`ActionRequest`（含 `decisions: list[ApprovalRecord]`，新增字段），其 `decisions[-1]` 为本次审批结论。
- **Task 4 的 "gate" handler 返回**（LangGraph channel 更新字典）：
  ```python
  async def gate_handler(state: ClusterState, node: WorkflowNode, ctx: NodeContext) -> dict:
      request = ActionRequest(
          id=..., kind=node.gate, title=..., description=...,
          evidence=..., risk_level=..., bypass_immune=...,
          decisions=[ApprovalRecord(by_role=..., type="reject", args=...)],
      )
      return {"gate_payloads": {node.gate: request}, "decisions": [ApprovalRecord(by_role=..., type="reject")]}
  ```
  （`decisions` 频道有 `operator.add` reducer，追加即可；`gate_payloads` 无 reducer，整体覆盖。）
- **路由表**（路由器读 `state.gate_payloads[node.gate].decisions[-1].type`）：
  | 结论 type | 目标 |
  |---|---|
  | `accept` | `on_accept`（缺省 `to`） |
  | `reject` | `on_reject`（缺省 `to`） |
  | `edit` | `on_edit`（缺省 `to`） |
  | `response` | `on_response`（缺省 `on_accept`→`to`） |
  | `ignore` 或载荷缺失/无 decisions | `on_accept`（缺省 `to`） |
- `ApprovalRecord.type` 的合法值为 `accept/reject/edit/response/ignore`（Task 1 定义）；注意 `HumanResponse.type` 只有 `accept/ignore/response/edit` 且无 reject，因此**载荷不用 HumanResponse**，统一用 ActionRequest.decisions 里的 ApprovalRecord。

## 4. parallel 并行方案

- 采用 **LangGraph `Send` API**（实测 langgraph 1.2.11 可用，无需 asyncio.gather）：
  - 编译期对 parallel 节点注册条件边：`add_conditional_edges(parallel_id, fan_out, list(children))`，`fan_out` 返回 `[Send(child_id, {}) for child_id in children]`。
  - 每个子节点仍以普通图节点注册（走同一套 wrapper，产出 `node_start`/`node_end`），并自动 `add_edge(child, fan_in_target)`，fan_in_target = parallel 节点的 `to` 出边目标；所有分支完成后 fan-in 节点只跑一次（LangGraph 原生等待 Send 分支合并）。
  - 子节点可为任意节点类型；children 不应自带出边（并行汇聚由 parallel 的 `to` 决定），已在报告/模块 docstring 说明。

## 5. 偏离与决策说明

1. **NodeHandler 返回类型**：简报给出 `Awaitable[ClusterState]` 并注明"或返回 dict of channel updates —— pick ONE, document it"。本实现选 **dict of channel updates**（`Callable[[ClusterState, WorkflowNode, NodeContext], Awaitable[dict[str, Any]]]`）：与 `operator.add` reducer 天然一致（handler 追加、绝不整体替换 list 频道），且与简报自述的 `handler writes {"gate_payloads": {...}}` 一致；返回 None 视为无更新，返回非 dict 抛 TypeError。Task 4/5 按此注册。
2. **gate_payloads 键**：简报写 `state.gate_payloads[node_id]`，但 Task 1 已锁定 `dict[GateKind, ActionRequest]`（test_models.py 断言 `gate_payloads[GateKind.RELEASE]`），"keep everything else unchanged" 为硬约束，故键改用 `node.gate`（GateKind）。局限：同流程内两个同 GateKind 的门会互相覆盖载荷，Task 7 编排示例时应避免；如需按 node_id 键可在后续任务演进模型（会破坏 Task 1 契约，需评审）。
3. **ActionRequest 新增 `decisions` 字段**：简报路由描述的前提（"ActionRequest 的 .decisions"）在 Task 1 模型中缺失，本任务以向后兼容的 default_factory 字段补齐，Task 1 的 33 个测试原样通过。
4. **loop 语义**：`max_iterations` = 单次运行累计**节点执行次数**上限（不是"轮数"）。这是对简报 "track executed-node count" 的字面实现，且能覆盖不经过 start 的 gate 返工回环（如 reject→rework→gate→…），无需额外机制；LangGraph recursion_limit=`max_iterations*4` 兜底。注意：线性流程的节点数必须 ≤ max_iterations（共享契约示例中 8 节点配 max_iterations: 5 需要 Task 7 建示例时调大，如 30）。
5. **node_start/node_end 事件**：由编译期 wrapper 对每个执行节点统一发出（含 start/end），默认占位 handler 不发额外事件——避免与 wrapper 事件重复；满足"未注册 handler 编译与运行不中断"与"每节点 node_start/node_end"双重约束。
6. **事件缓冲**：`NodeContext.events` 通过 `NodeContext.model_construct` 与内部事件缓冲保持同一列表引用（pydantic 构造默认会拷贝列表，直接构造会让 handler append 丢失）。
7. **无 checkpointer**：`run()` 未挂 MemorySaver（简报未要求）；`configurable.thread_id` 仅作元数据传入。Task 7 CLI 若需断点续跑/审批恢复，可自行挂 MemorySaver + interrupt。
8. 未创建 gates.py/roles.py/meetings.py/examples 示例（属 Task 4/5/7）；tests 用注入的 fake handler，不依赖这些模块。

## 6. 提交

- Commit SHA：`4179512`（`Task 3: 流程引擎 YAML→StateGraph`）
- 变更文件：`src/agent_cluster/workflow.py`（新增）、`tests/test_workflow.py`（新增）、`src/agent_cluster/models.py`、`src/agent_cluster/__init__.py`
- 工作区干净；`uv run pytest -q` 全绿（73 passed）。
