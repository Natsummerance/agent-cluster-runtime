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


