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


## Review 修复报告（2026-08-12）：返工率连续窗口与迭代排序

### 背景

Task 6 review 在 `src/agent_cluster/metrics.py` 发现 2 个 Important 问题：

1. **返工率规则丢失"连续 2 迭代"限定**：原规则取最新一个迭代窗口、>0.3 即触发；单个噪音迭代会产生误报信号并引出无谓提案。设计文档 §6.3 要求连续 2 个迭代越界才触发进化。
2. **迭代窗口按字典序选择**：`max(point.tags["iteration"])` 为字典序，导致 `iter-10 < iter-2 < iter-9`；迭代数达到 10 后"最新"窗口会静默选错标签。

### 改动内容（仅 metrics.py + tests/test_metrics.py）

- **Finding 1**：`evaluate` 中返工率规则改为调用新增的 `_rework_breach_signal`：按迭代标签分组为窗口（无迭代标签时每个度量点视为一个窗口），取最新连续 2 个窗口，**两个窗口都必须严格 `> 0.3`** 才产出信号；任一窗口 `<= 0.3` 不触发。证据同时包含两个窗口的实际度量值（有迭代标签时形如 `rework_rate=0.4@iter=iter-1`）。
- **Finding 2**：新增 `_iteration_sort_key`（正则提取尾部数字后缀按数值比较，`iter-10 > iter-9 > iter-2`；无数字后缀回退字符串并排最前）与 `_windows`（按该键自然排序分组）。删除了字典序实现 `_latest_window`。
- 文档：模块 docstring 的返工率规则描述同步更新。

### 覆盖测试（tests/test_metrics.py 新增/改写 5 个，共 23 个）

- `test_rework_rate_single_window_breach_does_not_fire`：无标签单点 >0.3 不触发。
- `test_rework_rate_single_iteration_breach_does_not_fire`：单个迭代越界不触发。
- `test_rework_rate_two_consecutive_windows_trigger_signal` / `test_rework_rate_two_consecutive_iterations_trigger_signal`：连续 2 个窗口越界触发，证据含两窗口值。
- `test_rework_rate_previous_window_healthy_no_signal` / `test_rework_rate_latest_window_healthy_no_signal`：任一窗口健康不触发。
- `test_rework_rate_uses_natural_iteration_order`：iter-1..iter-10 中仅 iter-9/iter-10 越界 → 触发且证据为 iter-9/iter-10。
- `test_rework_rate_latest_iteration_selected_naturally`：回归测试，字典序会误选 iter-9 而误报；数值序选 iter-10（健康）→ 不触发。
- `test_rework_rate_boundary`：任一窗口恰为 0.3（严格 > 边界）不触发；两窗口 0.301/0.4 触发。
- `test_evaluate_returns_signals_for_each_breach`：返工率补录 2 个窗口值，仍期望 5 条信号。

### 测试命令与输出

全量套件：

```
uv run pytest -q
........................................................................ [ 72%]
........................................................                 [100%]
200 passed in 1.74s
```

度量模块单测：

```
uv run pytest -q tests/test_metrics.py
.......................                                                  [100%]
23 passed in 0.66s
```

### 提交

- `git add -A && git commit -m "Task 6: 修复返工率连续窗口与迭代排序"`：a48ee88
