# Task 6 报告：进化闭环与度量（Phase 3）

## 实现摘要

新增两个模块并接入包导出：

- `src/agent_cluster/evolution.py`：六步进化闭环（§6.2）+ 安全治理（§6.5）。
  - `Signal{id, type, source, evidence, severity, ts}`；`Candidate{category, target, change, evidence, expected_impact}`。
  - `EvolutionProposal`：含 title/change_diff/affected_roles/affected_workflows/risk_level/validation_plan/rollback_plan/owner/status/gray/effective_version/votes/created_ts/updated_ts；六态状态机 `draft→voting→approved/rejected→applied→rolled_back`；**缺 rollback_plan（空/空白）构造即 ValidationError**。
  - `EvolutionEngine`：`collect`（规则扫描事件流，含 metric_threshold / 重复评审驳回 LBTM / retro 根因 / 回滚事件，内容去重）→ `distill`（按 category+target 归并、过滤 severity=low 且无证据噪音）→ `propose`（强制回滚方案 + 类别推导风险等级 + 自我扩权校验）→ `review`（L3 组织流程 human_required + bypass-immune 自动驳回；记录 Vote）→ `apply`（版本自增 v0→v1、灰度标志 gray=True、审计事件 evolution_applied）→ `rollback`（审计事件 evolution_rolled_back，回滚本身进入下一轮 collect）。
  - `assert_no_self_empowerment`：变更命中 approval_scope/permissions/permission/权限/提权 即拒绝，在 propose 与 review 双处执行。
  - `EvolutionError`；公共辅助 `bump_version`。
- `src/agent_cluster/metrics.py`：§6.3 绩效度量。
  - `MetricsCollector.record/snapshot/reset`（内存存储，快照深拷贝）；`MetricsSnapshot`（metrics: dict[str, list[MetricPoint]]）；`MetricPoint{name, value, tags, ts}`。
  - `MetricRules.evaluate(snapshot) -> list[Signal]`：内置 5 条阈值规则（评审通过率 <0.6 → high；返工率 >0.3 最新迭代窗口 → high；行动项关闭率 <0.5 → medium；循环次数最新值 >3×历史均值 → medium；审批门等待 >86400s → medium），evidence 取自真实度量点。
- `src/agent_cluster/__init__.py`：导出 Signal/Candidate/EvolutionProposal/EvolutionEngine/EvolutionError/MetricsCollector/MetricsSnapshot/MetricPoint/MetricRules。
- 测试：`tests/test_evolution.py`（25 个）、`tests/test_metrics.py`（20 个）。

## 测试与命令输出

`uv run pytest -q`（最终全量，150 存量 + 45 新增 = 195）：

```
........................................................................ [ 36%]
........................................................................ [ 73%]
...................................................                      [100%]
195 passed in 1.70s
```

新增文件单独运行：

```
uv run pytest -q tests/test_evolution.py tests/test_metrics.py
.............................................                            [100%]
45 passed in 0.92s
```

覆盖点：六步闭环端到端（collect→distill→propose→review→apply→rollback 全流程 + 全程审计）；缺回滚方案在 propose 与模型构造两处被拒；自我扩权在 propose 与 review 两处被拒；L3 组织提案 auto_mode="accept" 自动驳回（bypass-immune 原因文案）；apply/rollback 前置状态校验；版本 v0→v1；apply/rollback 审计事件；collect 去重；distill 合并与噪音过滤；风险等级推导与升级；MetricsCollector record/snapshot/reset/深拷贝；5 条阈值规则逐条触发、健康数据为空、阈值边界（0.6/0.3/0.5/86400 含边界不触发）。

## 六步闭环 API 映射

| 步骤 | 方法 | 输入 → 输出 | 关键行为 |
|---|---|---|---|
| ① 收集 | `EvolutionEngine.collect(events: list[Event] \| EventBus) -> list[Signal]` | 事件流 → 信号 | 指标越界/重复评审驳回(LBTM)/复盘根因/回滚事件；内容去重 |
| ② 提炼 | `EvolutionEngine.distill(signals) -> list[Candidate]` | 信号池 → 候选 | 按 category+target 归并、证据合并去重、过滤噪音 |
| ③ 提案 | `EvolutionEngine.propose(candidate, *, author_role, title, rollback_plan, validation_plan="") -> EvolutionProposal` | 候选 → 提案(draft) | 缺回滚方案拒绝；类别推导风险等级；自我扩权校验 |
| ④ 评审门 | `EvolutionEngine.review(proposal, *, approver, human_required=False, auto_mode="ask", decision="approve", reason="") -> EvolutionProposal` | 提案 → approved/rejected | L3 人工标志 bypass-immune 自动驳回；记录 Vote |
| ⑤ 生效 | `EvolutionEngine.apply(proposal, *, event_bus=None) -> EvolutionProposal` | approved → applied | 版本自增 + gray=True；审计事件 evolution_applied |
| ⑥ 回滚 | `EvolutionEngine.rollback(proposal, *, reason, event_bus=None) -> EvolutionProposal` | applied → rolled_back | 审计事件 evolution_rolled_back（进入下一轮 ①） |

## 偏差说明

- 无偏离。按任务简报逐项实现；`collect` 额外识别 `evolution_rolled_back` 事件产出 `rollback_occurred` 信号，落实"回滚本身 feeds 下一轮 collect"要求（Signal.type 为 str，允许扩展）。
- 未创建 `cli.py`（属 Task 7）。

## 提交

- 提交信息：`Task 6: 进化闭环与度量`
- 提交 SHA：49afa69
