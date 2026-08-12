"""绩效度量模块：设计文档 §6.3 度量采集 + 阈值规则引擎。

组件：
- ``MetricsCollector``：内存度量存储，``record(name, value, tags)`` 追加，
  ``snapshot()`` 产出不可变快照 ``MetricsSnapshot``，``reset()`` 清空。
- ``MetricPoint``：单条度量点（name/value/tags/ts）。
- ``MetricsSnapshot``：按指标名分组的度量点快照。
- ``MetricRules``：阈值规则引擎，``evaluate(snapshot) -> list[Signal]``。

内置指标名（§6.3）：``review_pass_rate`` / ``rework_rate`` /
``action_item_close_rate`` / ``loop_iterations`` / ``gate_wait_seconds``
（辅助，不作为规划依据）；v0.3 token 制新增 ``tokens_per_role`` /
``tokens_per_phase`` / ``tokens_per_artifact`` / ``budget_remaining`` /
``estimate_accuracy``（预估 vs 实际，规划与计量一律按 token）。

阈值规则（每条产出 ``type="metric_threshold"`` 信号，evidence 取自真实度量点）：

- ``review_pass_rate < 0.6``：评审通过率过低（high）；
- ``rework_rate`` 最新连续 2 个迭代窗口均 ``> 0.3``：返工率过高（high），
  单个迭代噪音不触发（无 ``iteration`` 标签时取最新连续 2 个点作为窗口）；
- ``action_item_close_rate < 0.5``：行动项关闭率过低（medium）；
- ``loop_iterations`` 最新值 > 3 × 历史均值：循环次数激增（medium）；
- ``gate_wait_seconds > 86400``：审批门等待超时（medium）。
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from agent_cluster.evolution import Signal

__all__ = [
    "MetricPoint",
    "MetricsSnapshot",
    "MetricsCollector",
    "MetricRules",
    "BUILTIN_METRICS",
]

# 内置指标名（§6.3）
BUILTIN_METRICS: tuple[str, ...] = (
    "review_pass_rate",
    "rework_rate",
    "action_item_close_rate",
    "loop_iterations",
    "gate_wait_seconds",
    "tokens_per_role",
    "tokens_per_phase",
    "tokens_per_artifact",
    "budget_remaining",
    "estimate_accuracy",
)

# 阈值常量
REVIEW_PASS_RATE_THRESHOLD: float = 0.6
REWORK_RATE_THRESHOLD: float = 0.3
ACTION_ITEM_CLOSE_RATE_THRESHOLD: float = 0.5
LOOP_ITERATIONS_SPIKE_FACTOR: float = 3.0
GATE_WAIT_THRESHOLD_SECONDS: float = 86400.0


class MetricPoint(BaseModel):
    """单条度量点。"""

    model_config = ConfigDict(extra="ignore")

    name: str = Field(description="指标名")
    value: float = Field(description="指标值")
    tags: dict[str, str] = Field(default_factory=dict, description="标签（如 iteration=iter-3）")
    ts: datetime = Field(default_factory=datetime.now, description="采集时间")


class MetricsSnapshot(BaseModel):
    """度量快照：按指标名分组存储度量点。"""

    model_config = ConfigDict(extra="ignore")

    metrics: dict[str, list[MetricPoint]] = Field(
        default_factory=dict, description="指标名 -> 度量点列表"
    )


class MetricsCollector:
    """内存度量采集器：record / snapshot / reset。"""

    def __init__(self) -> None:
        self._store: dict[str, list[MetricPoint]] = {}

    def record(
        self,
        name: str,
        value: float,
        *,
        tags: dict | None = None,
        ts: datetime | None = None,
    ) -> None:
        """记录一条度量点；``tags`` 与 ``ts`` 可选。"""
        self._store.setdefault(name, []).append(
            MetricPoint(
                name=name,
                value=value,
                tags=dict(tags or {}),
                ts=ts if ts is not None else datetime.now(),
            )
        )

    def snapshot(self) -> MetricsSnapshot:
        """产出当前快照（深拷贝，后续 record 不影响已产出快照）。"""
        copied = {name: [point.model_copy(deep=True) for point in points] for name, points in self._store.items()}
        return MetricsSnapshot(metrics=copied)

    def reset(self) -> None:
        """清空所有度量数据。"""
        self._store.clear()


class MetricRules:
    """阈值规则引擎：``evaluate(snapshot)`` 产出 ``type="metric_threshold"`` 信号。"""

    @staticmethod
    def evaluate(snapshot: MetricsSnapshot) -> list[Signal]:
        """评估快照，命中阈值即产出一条信号（每条规则至多一条，按最新窗口）。"""
        signals: list[Signal] = []
        metrics = snapshot.metrics

        review_points = metrics.get("review_pass_rate", [])
        if review_points and MetricRules._latest_value(review_points) < REVIEW_PASS_RATE_THRESHOLD:
            signals.append(
                MetricRules._build_signal("review_pass_rate", review_points, "high")
            )

        rework_points = metrics.get("rework_rate", [])
        rework_signal = MetricRules._rework_breach_signal(rework_points)
        if rework_signal is not None:
            signals.append(rework_signal)

        close_points = metrics.get("action_item_close_rate", [])
        if close_points and MetricRules._latest_value(close_points) < ACTION_ITEM_CLOSE_RATE_THRESHOLD:
            signals.append(
                MetricRules._build_signal("action_item_close_rate", close_points, "medium")
            )

        loop_points = metrics.get("loop_iterations", [])
        loop_signal = MetricRules._loop_spike_signal(loop_points)
        if loop_signal is not None:
            signals.append(loop_signal)

        gate_points = metrics.get("gate_wait_seconds", [])
        if gate_points and MetricRules._latest_value(gate_points) > GATE_WAIT_THRESHOLD_SECONDS:
            signals.append(
                MetricRules._build_signal("gate_wait_seconds", gate_points, "medium")
            )

        return signals

    # ------------------------------------------------------------------
    # 内部辅助
    # ------------------------------------------------------------------

    @staticmethod
    def _latest_value(points: list[MetricPoint]) -> float:
        """取最新一个度量点的值（按 ts，相同时取最后记录的点）。"""
        return sorted(points, key=lambda point: point.ts)[-1].value

    @staticmethod
    def _iteration_sort_key(iteration: str) -> tuple[int, int, str]:
        """迭代标签自然排序键：``iter-10 > iter-9 > iter-2``（数字后缀按数值比较，
        避免字典序 ``iter-10 < iter-2`` 的误判）；无数字后缀回退字符串并排最前。"""
        match = re.search(r"(\d+)\s*$", iteration)
        if match:
            return (1, int(match.group(1)), iteration)
        return (0, 0, iteration)

    @staticmethod
    def _windows(points: list[MetricPoint]) -> list[list[MetricPoint]]:
        """把度量点分组为迭代窗口（按迭代标签自然排序升序）；无迭代标签时每个点视为一个窗口。"""
        if not points:
            return []
        tagged = [point for point in points if point.tags.get("iteration")]
        if tagged:
            grouped: dict[str, list[MetricPoint]] = {}
            for point in points:
                grouped.setdefault(point.tags.get("iteration", ""), []).append(point)
            ordered = sorted(grouped.items(), key=lambda item: MetricRules._iteration_sort_key(item[0]))
            return [window for _, window in ordered]
        return [[point] for point in sorted(points, key=lambda point: point.ts)]

    @staticmethod
    def _rework_breach_signal(points: list[MetricPoint]) -> Signal | None:
        """返工率规则：最新连续 2 个窗口（迭代）均严格 ``> 0.3`` 才触发；
        evidence 同时包含两个窗口的实际度量值（含迭代标签）。"""
        windows = MetricRules._windows(points)
        if len(windows) < 2:
            return None
        latest_windows = windows[-2:]
        for window in latest_windows:
            if MetricRules._latest_value(window) <= REWORK_RATE_THRESHOLD:
                return None
        evidence: list[str] = []
        for window in latest_windows:
            for point in window:
                iteration = point.tags.get("iteration")
                if iteration:
                    evidence.append(f"{point.name}={point.value}@iter={iteration}")
                else:
                    evidence.append(f"{point.name}={point.value}")
        return Signal(
            id=uuid.uuid4().hex,
            type="metric_threshold",
            source="metric_rules",
            evidence=evidence,
            severity="high",
            ts=sorted(points, key=lambda point: point.ts)[-1].ts,
        )

    @staticmethod
    def _build_signal(name: str, points: list[MetricPoint], severity: Literal["medium", "high"]) -> Signal:
        """由实际度量点构造指标越界信号（evidence 含指标名与值）。"""
        return Signal(
            id=uuid.uuid4().hex,
            type="metric_threshold",
            source="metric_rules",
            evidence=[f"{point.name}={point.value}" for point in points],
            severity=severity,
            ts=sorted(points, key=lambda point: point.ts)[-1].ts,
        )

    @staticmethod
    def _loop_spike_signal(points: list[MetricPoint]) -> Signal | None:
        """循环次数激增：最新值 > 3 × 历史均值（至少有一个历史点）。"""
        if len(points) < 2:
            return None
        ordered = sorted(points, key=lambda point: point.ts)
        latest_value = ordered[-1].value
        previous = ordered[:-1]
        previous_average = sum(point.value for point in previous) / len(previous)
        if latest_value > LOOP_ITERATIONS_SPIKE_FACTOR * previous_average:
            return MetricRules._build_signal("loop_iterations", ordered, "medium")
        return None
