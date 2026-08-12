# Task 6 Fix Review Package

Fix base: 49afa69
Head: a48ee88

## Diff stat

```
 .superpowers/sdd/task-6-report.md |  59 ++++++++++++++++++++++
 src/agent_cluster/metrics.py      |  61 +++++++++++++++++++----
 tests/test_metrics.py             | 100 +++++++++++++++++++++++++++++++++++---
 3 files changed, 202 insertions(+), 18 deletions(-)
```

## Full diff

```diff
diff --git a/.superpowers/sdd/task-6-report.md b/.superpowers/sdd/task-6-report.md
new file mode 100644
index 0000000..94bf26a
--- /dev/null
+++ b/.superpowers/sdd/task-6-report.md
@@ -0,0 +1,59 @@
+# Task 6 报告：进化闭环与度量（Phase 3）
+
+## 实现摘要
+
+新增两个模块并接入包导出：
+
+- `src/agent_cluster/evolution.py`：六步进化闭环（§6.2）+ 安全治理（§6.5）。
+  - `Signal{id, type, source, evidence, severity, ts}`；`Candidate{category, target, change, evidence, expected_impact}`。
+  - `EvolutionProposal`：含 title/change_diff/affected_roles/affected_workflows/risk_level/validation_plan/rollback_plan/owner/status/gray/effective_version/votes/created_ts/updated_ts；六态状态机 `draft→voting→approved/rejected→applied→rolled_back`；**缺 rollback_plan（空/空白）构造即 ValidationError**。
+  - `EvolutionEngine`：`collect`（规则扫描事件流，含 metric_threshold / 重复评审驳回 LBTM / retro 根因 / 回滚事件，内容去重）→ `distill`（按 category+target 归并、过滤 severity=low 且无证据噪音）→ `propose`（强制回滚方案 + 类别推导风险等级 + 自我扩权校验）→ `review`（L3 组织流程 human_required + bypass-immune 自动驳回；记录 Vote）→ `apply`（版本自增 v0→v1、灰度标志 gray=True、审计事件 evolution_applied）→ `rollback`（审计事件 evolution_rolled_back，回滚本身进入下一轮 collect）。
+  - `assert_no_self_empowerment`：变更命中 approval_scope/permissions/permission/权限/提权 即拒绝，在 propose 与 review 双处执行。
+  - `EvolutionError`；公共辅助 `bump_version`。
+- `src/agent_cluster/metrics.py`：§6.3 绩效度量。
+  - `MetricsCollector.record/snapshot/reset`（内存存储，快照深拷贝）；`MetricsSnapshot`（metrics: dict[str, list[MetricPoint]]）；`MetricPoint{name, value, tags, ts}`。
+  - `MetricRules.evaluate(snapshot) -> list[Signal]`：内置 5 条阈值规则（评审通过率 <0.6 → high；返工率 >0.3 最新迭代窗口 → high；行动项关闭率 <0.5 → medium；循环次数最新值 >3×历史均值 → medium；审批门等待 >86400s → medium），evidence 取自真实度量点。
+- `src/agent_cluster/__init__.py`：导出 Signal/Candidate/EvolutionProposal/EvolutionEngine/EvolutionError/MetricsCollector/MetricsSnapshot/MetricPoint/MetricRules。
+- 测试：`tests/test_evolution.py`（25 个）、`tests/test_metrics.py`（20 个）。
+
+## 测试与命令输出
+
+`uv run pytest -q`（最终全量，150 存量 + 45 新增 = 195）：
+
+```
+........................................................................ [ 36%]
+........................................................................ [ 73%]
+...................................................                      [100%]
+195 passed in 1.70s
+```
+
+新增文件单独运行：
+
+```
+uv run pytest -q tests/test_evolution.py tests/test_metrics.py
+.............................................                            [100%]
+45 passed in 0.92s
+```
+
+覆盖点：六步闭环端到端（collect→distill→propose→review→apply→rollback 全流程 + 全程审计）；缺回滚方案在 propose 与模型构造两处被拒；自我扩权在 propose 与 review 两处被拒；L3 组织提案 auto_mode="accept" 自动驳回（bypass-immune 原因文案）；apply/rollback 前置状态校验；版本 v0→v1；apply/rollback 审计事件；collect 去重；distill 合并与噪音过滤；风险等级推导与升级；MetricsCollector record/snapshot/reset/深拷贝；5 条阈值规则逐条触发、健康数据为空、阈值边界（0.6/0.3/0.5/86400 含边界不触发）。
+
+## 六步闭环 API 映射
+
+| 步骤 | 方法 | 输入 → 输出 | 关键行为 |
+|---|---|---|---|
+| ① 收集 | `EvolutionEngine.collect(events: list[Event] \| EventBus) -> list[Signal]` | 事件流 → 信号 | 指标越界/重复评审驳回(LBTM)/复盘根因/回滚事件；内容去重 |
+| ② 提炼 | `EvolutionEngine.distill(signals) -> list[Candidate]` | 信号池 → 候选 | 按 category+target 归并、证据合并去重、过滤噪音 |
+| ③ 提案 | `EvolutionEngine.propose(candidate, *, author_role, title, rollback_plan, validation_plan="") -> EvolutionProposal` | 候选 → 提案(draft) | 缺回滚方案拒绝；类别推导风险等级；自我扩权校验 |
+| ④ 评审门 | `EvolutionEngine.review(proposal, *, approver, human_required=False, auto_mode="ask", decision="approve", reason="") -> EvolutionProposal` | 提案 → approved/rejected | L3 人工标志 bypass-immune 自动驳回；记录 Vote |
+| ⑤ 生效 | `EvolutionEngine.apply(proposal, *, event_bus=None) -> EvolutionProposal` | approved → applied | 版本自增 + gray=True；审计事件 evolution_applied |
+| ⑥ 回滚 | `EvolutionEngine.rollback(proposal, *, reason, event_bus=None) -> EvolutionProposal` | applied → rolled_back | 审计事件 evolution_rolled_back（进入下一轮 ①） |
+
+## 偏差说明
+
+- 无偏离。按任务简报逐项实现；`collect` 额外识别 `evolution_rolled_back` 事件产出 `rollback_occurred` 信号，落实"回滚本身 feeds 下一轮 collect"要求（Signal.type 为 str，允许扩展）。
+- 未创建 `cli.py`（属 Task 7）。
+
+## 提交
+
+- 提交信息：`Task 6: 进化闭环与度量`
+- 提交 SHA：49afa69
diff --git a/src/agent_cluster/metrics.py b/src/agent_cluster/metrics.py
index 55fb9e1..91ee1a6 100644
--- a/src/agent_cluster/metrics.py
+++ b/src/agent_cluster/metrics.py
@@ -13,8 +13,8 @@
 阈值规则（每条产出 ``type="metric_threshold"`` 信号，evidence 取自真实度量点）：
 
 - ``review_pass_rate < 0.6``：评审通过率过低（high）；
-- ``rework_rate > 0.3``：返工率过高（high），取"最新迭代窗口"
-  （有 ``iteration`` 标签时取最新迭代的一组点，否则取最新一个点）；
+- ``rework_rate`` 最新连续 2 个迭代窗口均 ``> 0.3``：返工率过高（high），
+  单个迭代噪音不触发（无 ``iteration`` 标签时取最新连续 2 个点作为窗口）；
 - ``action_item_close_rate < 0.5``：行动项关闭率过低（medium）；
 - ``loop_iterations`` 最新值 > 3 × 历史均值：循环次数激增（medium）；
 - ``gate_wait_seconds > 86400``：审批门等待超时（medium）。
@@ -22,6 +22,7 @@
 
 from __future__ import annotations
 
+import re
 import uuid
 from datetime import datetime
 from typing import Literal
@@ -126,9 +127,9 @@ class MetricRules:
             )
 
         rework_points = metrics.get("rework_rate", [])
-        rework_window = MetricRules._latest_window(rework_points)
-        if rework_window and MetricRules._latest_value(rework_window) > REWORK_RATE_THRESHOLD:
-            signals.append(MetricRules._build_signal("rework_rate", rework_window, "high"))
+        rework_signal = MetricRules._rework_breach_signal(rework_points)
+        if rework_signal is not None:
+            signals.append(rework_signal)
 
         close_points = metrics.get("action_item_close_rate", [])
         if close_points and MetricRules._latest_value(close_points) < ACTION_ITEM_CLOSE_RATE_THRESHOLD:
@@ -159,15 +160,55 @@ class MetricRules:
         return sorted(points, key=lambda point: point.ts)[-1].value
 
     @staticmethod
-    def _latest_window(points: list[MetricPoint]) -> list[MetricPoint]:
-        """最新迭代窗口：有 ``iteration`` 标签时取最新迭代的全部点，否则取最新一个点。"""
+    def _iteration_sort_key(iteration: str) -> tuple[int, int, str]:
+        """迭代标签自然排序键：``iter-10 > iter-9 > iter-2``（数字后缀按数值比较，
+        避免字典序 ``iter-10 < iter-2`` 的误判）；无数字后缀回退字符串并排最前。"""
+        match = re.search(r"(\d+)\s*$", iteration)
+        if match:
+            return (1, int(match.group(1)), iteration)
+        return (0, 0, iteration)
+
+    @staticmethod
+    def _windows(points: list[MetricPoint]) -> list[list[MetricPoint]]:
+        """把度量点分组为迭代窗口（按迭代标签自然排序升序）；无迭代标签时每个点视为一个窗口。"""
         if not points:
             return []
         tagged = [point for point in points if point.tags.get("iteration")]
         if tagged:
-            latest_iteration = max(point.tags["iteration"] for point in tagged)
-            return [point for point in points if point.tags.get("iteration") == latest_iteration]
-        return [sorted(points, key=lambda point: point.ts)[-1]]
+            grouped: dict[str, list[MetricPoint]] = {}
+            for point in points:
+                grouped.setdefault(point.tags.get("iteration", ""), []).append(point)
+            ordered = sorted(grouped.items(), key=lambda item: MetricRules._iteration_sort_key(item[0]))
+            return [window for _, window in ordered]
+        return [[point] for point in sorted(points, key=lambda point: point.ts)]
+
+    @staticmethod
+    def _rework_breach_signal(points: list[MetricPoint]) -> Signal | None:
+        """返工率规则：最新连续 2 个窗口（迭代）均严格 ``> 0.3`` 才触发；
+        evidence 同时包含两个窗口的实际度量值（含迭代标签）。"""
+        windows = MetricRules._windows(points)
+        if len(windows) < 2:
+            return None
+        latest_windows = windows[-2:]
+        for window in latest_windows:
+            if MetricRules._latest_value(window) <= REWORK_RATE_THRESHOLD:
+                return None
+        evidence: list[str] = []
+        for window in latest_windows:
+            for point in window:
+                iteration = point.tags.get("iteration")
+                if iteration:
+                    evidence.append(f"{point.name}={point.value}@iter={iteration}")
+                else:
+                    evidence.append(f"{point.name}={point.value}")
+        return Signal(
+            id=uuid.uuid4().hex,
+            type="metric_threshold",
+            source="metric_rules",
+            evidence=evidence,
+            severity="high",
+            ts=sorted(points, key=lambda point: point.ts)[-1].ts,
+        )
 
     @staticmethod
     def _build_signal(name: str, points: list[MetricPoint], severity: Literal["medium", "high"]) -> Signal:
diff --git a/tests/test_metrics.py b/tests/test_metrics.py
index 08a74a7..b608867 100644
--- a/tests/test_metrics.py
+++ b/tests/test_metrics.py
@@ -87,23 +87,48 @@ def test_review_pass_rate_below_threshold_triggers_signal():
     assert signal.evidence == ["review_pass_rate=0.4"]
 
 
-def test_rework_rate_above_threshold_triggers_signal():
+def test_rework_rate_single_window_breach_does_not_fire():
+    # 无迭代标签：单点（单窗口）即使 >0.3 也不触发，需连续 2 个窗口
     collector = MetricsCollector()
     collector.record("rework_rate", 0.5)
+    assert MetricRules.evaluate(collector.snapshot()) == []
+
+
+def test_rework_rate_single_iteration_breach_does_not_fire():
+    # 单个迭代越界属于噪音，不得触发进化信号
+    collector = MetricsCollector()
+    collector.record("rework_rate", 0.5, tags={"iteration": "iter-1"})
+    assert MetricRules.evaluate(collector.snapshot()) == []
+
+
+def test_rework_rate_two_consecutive_windows_trigger_signal():
+    collector = MetricsCollector()
+    collector.record("rework_rate", 0.4)
+    collector.record("rework_rate", 0.5)
     signals = MetricRules.evaluate(collector.snapshot())
     assert len(signals) == 1
     assert signals[0].severity == "high"
-    assert signals[0].evidence == ["rework_rate=0.5"]
+    assert signals[0].evidence == ["rework_rate=0.4", "rework_rate=0.5"]
 
 
-def test_rework_rate_uses_latest_iteration_window():
+def test_rework_rate_two_consecutive_iterations_trigger_signal():
     collector = MetricsCollector()
     collector.record("rework_rate", 0.4, tags={"iteration": "iter-1"})
     collector.record("rework_rate", 0.5, tags={"iteration": "iter-2"})
     signals = MetricRules.evaluate(collector.snapshot())
     assert len(signals) == 1
-    # 仅最新迭代窗口（iter-2）进入证据
-    assert signals[0].evidence == ["rework_rate=0.5"]
+    # 两个迭代窗口的实际值都进入证据（含迭代标签）
+    assert signals[0].evidence == [
+        "rework_rate=0.4@iter=iter-1",
+        "rework_rate=0.5@iter=iter-2",
+    ]
+
+
+def test_rework_rate_previous_window_healthy_no_signal():
+    collector = MetricsCollector()
+    collector.record("rework_rate", 0.1, tags={"iteration": "iter-1"})
+    collector.record("rework_rate", 0.5, tags={"iteration": "iter-2"})
+    assert MetricRules.evaluate(collector.snapshot()) == []
 
 
 def test_rework_rate_latest_window_healthy_no_signal():
@@ -113,6 +138,31 @@ def test_rework_rate_latest_window_healthy_no_signal():
     assert MetricRules.evaluate(collector.snapshot()) == []
 
 
+def test_rework_rate_uses_natural_iteration_order():
+    # 迭代标签按数值自然排序：iter-10 才是最新窗口（字典序会误判 iter-9）
+    collector = MetricsCollector()
+    for iteration in (
+        "iter-1", "iter-2", "iter-3", "iter-4", "iter-5",
+        "iter-6", "iter-7", "iter-8", "iter-9", "iter-10",
+    ):
+        value = 0.5 if iteration in ("iter-9", "iter-10") else 0.1
+        collector.record("rework_rate", value, tags={"iteration": iteration})
+    signals = MetricRules.evaluate(collector.snapshot())
+    assert len(signals) == 1
+    assert signals[0].evidence == [
+        "rework_rate=0.5@iter=iter-9",
+        "rework_rate=0.5@iter=iter-10",
+    ]
+
+
+def test_rework_rate_latest_iteration_selected_naturally():
+    # 回归：字典序会误选 iter-9 为"最新"而误报；数值序选 iter-10（健康）→ 不触发
+    collector = MetricsCollector()
+    collector.record("rework_rate", 0.5, tags={"iteration": "iter-9"})
+    collector.record("rework_rate", 0.1, tags={"iteration": "iter-10"})
+    assert MetricRules.evaluate(collector.snapshot()) == []
+
+
 def test_action_item_close_rate_below_threshold_triggers_signal():
     collector = MetricsCollector()
     collector.record("action_item_close_rate", 0.3)
@@ -162,6 +212,7 @@ def test_evaluate_returns_signals_for_each_breach():
     collector = MetricsCollector()
     collector.record("review_pass_rate", 0.4)
     collector.record("rework_rate", 0.5)
+    collector.record("rework_rate", 0.6)
     collector.record("action_item_close_rate", 0.3)
     collector.record("loop_iterations", 1, ts=datetime(2026, 8, 1, 10, 0, 0))
     collector.record("loop_iterations", 2, ts=datetime(2026, 8, 1, 10, 1, 0))
@@ -189,9 +240,42 @@ def test_review_pass_rate_boundary():
 
 
 def test_rework_rate_boundary():
-    healthy = MetricsSnapshot(metrics={"rework_rate": [MetricPoint(name="rework_rate", value=0.3)]})
-    assert MetricRules.evaluate(healthy) == []
-    breach = MetricsSnapshot(metrics={"rework_rate": [MetricPoint(name="rework_rate", value=0.301)]})
+    # 严格 > 0.3：任一窗口恰为 0.3 不构成越界
+    both_at_threshold = MetricsSnapshot(
+        metrics={
+            "rework_rate": [
+                MetricPoint(name="rework_rate", value=0.3, tags={"iteration": "iter-1"}),
+                MetricPoint(name="rework_rate", value=0.3, tags={"iteration": "iter-2"}),
+            ]
+        }
+    )
+    assert MetricRules.evaluate(both_at_threshold) == []
+    previous_at_threshold = MetricsSnapshot(
+        metrics={
+            "rework_rate": [
+                MetricPoint(name="rework_rate", value=0.3, tags={"iteration": "iter-1"}),
+                MetricPoint(name="rework_rate", value=0.5, tags={"iteration": "iter-2"}),
+            ]
+        }
+    )
+    assert MetricRules.evaluate(previous_at_threshold) == []
+    latest_at_threshold = MetricsSnapshot(
+        metrics={
+            "rework_rate": [
+                MetricPoint(name="rework_rate", value=0.4, tags={"iteration": "iter-1"}),
+                MetricPoint(name="rework_rate", value=0.3, tags={"iteration": "iter-2"}),
+            ]
+        }
+    )
+    assert MetricRules.evaluate(latest_at_threshold) == []
+    breach = MetricsSnapshot(
+        metrics={
+            "rework_rate": [
+                MetricPoint(name="rework_rate", value=0.301, tags={"iteration": "iter-1"}),
+                MetricPoint(name="rework_rate", value=0.4, tags={"iteration": "iter-2"}),
+            ]
+        }
+    )
     assert len(MetricRules.evaluate(breach)) == 1
 
 
```
