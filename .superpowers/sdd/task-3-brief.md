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


