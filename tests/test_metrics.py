"""Task 6 行为测试：绩效度量采集（MetricsCollector）与阈值规则引擎（MetricRules）。"""

from __future__ import annotations

from datetime import datetime

from agent_cluster.evolution import Signal
from agent_cluster.metrics import (
    BUILTIN_METRICS,
    MetricPoint,
    MetricRules,
    MetricsCollector,
    MetricsSnapshot,
)


def _collector() -> MetricsCollector:
    collector = MetricsCollector()
    collector.record("review_pass_rate", 0.9)
    collector.record("rework_rate", 0.1, tags={"iteration": "iter-1"})
    collector.record("action_item_close_rate", 0.8)
    collector.record("loop_iterations", 1)
    collector.record("loop_iterations", 2)
    collector.record("loop_iterations", 3)
    collector.record("gate_wait_seconds", 60)
    return collector


# ---------------------------------------------------------------------------
# MetricsCollector：record / snapshot / reset
# ---------------------------------------------------------------------------


def test_record_snapshot_reset():
    collector = _collector()
    snapshot = collector.snapshot()
    assert set(snapshot.metrics) == set(BUILTIN_METRICS)
    assert snapshot.metrics["review_pass_rate"][0].value == 0.9
    assert snapshot.metrics["rework_rate"][0].tags == {"iteration": "iter-1"}

    collector.reset()
    assert collector.snapshot().metrics == {}


def test_snapshot_is_deep_copy():
    collector = _collector()
    snapshot = collector.snapshot()
    snapshot.metrics["review_pass_rate"][0].value = 0.0
    snapshot.metrics["extra"] = [MetricPoint(name="extra", value=1.0)]
    fresh = collector.snapshot()
    assert fresh.metrics["review_pass_rate"][0].value == 0.9
    assert "extra" not in fresh.metrics


def test_record_with_explicit_ts_and_tags():
    collector = MetricsCollector()
    ts = datetime(2026, 8, 1, 9, 0, 0)
    collector.record("rework_rate", 0.5, tags={"iteration": "iter-2"}, ts=ts)
    point = collector.snapshot().metrics["rework_rate"][0]
    assert point.ts == ts
    assert point.tags == {"iteration": "iter-2"}


# ---------------------------------------------------------------------------
# MetricRules：健康数据不触发
# ---------------------------------------------------------------------------


def test_healthy_data_returns_no_signals():
    signals = MetricRules.evaluate(_collector().snapshot())
    assert signals == []


# ---------------------------------------------------------------------------
# MetricRules：逐规则触发
# ---------------------------------------------------------------------------


def test_review_pass_rate_below_threshold_triggers_signal():
    collector = MetricsCollector()
    collector.record("review_pass_rate", 0.4)
    signals = MetricRules.evaluate(collector.snapshot())
    assert len(signals) == 1
    signal = signals[0]
    assert signal.type == "metric_threshold"
    assert signal.severity == "high"
    assert signal.evidence == ["review_pass_rate=0.4"]


def test_rework_rate_above_threshold_triggers_signal():
    collector = MetricsCollector()
    collector.record("rework_rate", 0.5)
    signals = MetricRules.evaluate(collector.snapshot())
    assert len(signals) == 1
    assert signals[0].severity == "high"
    assert signals[0].evidence == ["rework_rate=0.5"]


def test_rework_rate_uses_latest_iteration_window():
    collector = MetricsCollector()
    collector.record("rework_rate", 0.4, tags={"iteration": "iter-1"})
    collector.record("rework_rate", 0.5, tags={"iteration": "iter-2"})
    signals = MetricRules.evaluate(collector.snapshot())
    assert len(signals) == 1
    # 仅最新迭代窗口（iter-2）进入证据
    assert signals[0].evidence == ["rework_rate=0.5"]


def test_rework_rate_latest_window_healthy_no_signal():
    collector = MetricsCollector()
    collector.record("rework_rate", 0.4, tags={"iteration": "iter-1"})
    collector.record("rework_rate", 0.1, tags={"iteration": "iter-2"})
    assert MetricRules.evaluate(collector.snapshot()) == []


def test_action_item_close_rate_below_threshold_triggers_signal():
    collector = MetricsCollector()
    collector.record("action_item_close_rate", 0.3)
    signals = MetricRules.evaluate(collector.snapshot())
    assert len(signals) == 1
    assert signals[0].severity == "medium"
    assert signals[0].evidence == ["action_item_close_rate=0.3"]


def test_loop_iterations_spike_triggers_signal():
    collector = MetricsCollector()
    collector.record("loop_iterations", 1, ts=datetime(2026, 8, 1, 10, 0, 0))
    collector.record("loop_iterations", 2, ts=datetime(2026, 8, 1, 10, 1, 0))
    collector.record("loop_iterations", 10, ts=datetime(2026, 8, 1, 10, 2, 0))
    signals = MetricRules.evaluate(collector.snapshot())
    assert len(signals) == 1
    signal = signals[0]
    assert signal.type == "metric_threshold"
    assert signal.severity == "medium"
    assert signal.evidence == ["loop_iterations=1.0", "loop_iterations=2.0", "loop_iterations=10.0"]


def test_loop_iterations_needs_history_for_spike():
    collector = MetricsCollector()
    collector.record("loop_iterations", 10)
    assert MetricRules.evaluate(collector.snapshot()) == []


def test_loop_iterations_healthy_no_spike():
    collector = MetricsCollector()
    collector.record("loop_iterations", 1, ts=datetime(2026, 8, 1, 10, 0, 0))
    collector.record("loop_iterations", 2, ts=datetime(2026, 8, 1, 10, 1, 0))
    collector.record("loop_iterations", 4, ts=datetime(2026, 8, 1, 10, 2, 0))  # 4 > 3 * 1.5 = 4.5? 否
    assert MetricRules.evaluate(collector.snapshot()) == []


def test_gate_wait_seconds_above_threshold_triggers_signal():
    collector = MetricsCollector()
    collector.record("gate_wait_seconds", 90000)
    signals = MetricRules.evaluate(collector.snapshot())
    assert len(signals) == 1
    assert signals[0].severity == "medium"
    assert signals[0].evidence == ["gate_wait_seconds=90000.0"]


def test_evaluate_returns_signals_for_each_breach():
    collector = MetricsCollector()
    collector.record("review_pass_rate", 0.4)
    collector.record("rework_rate", 0.5)
    collector.record("action_item_close_rate", 0.3)
    collector.record("loop_iterations", 1, ts=datetime(2026, 8, 1, 10, 0, 0))
    collector.record("loop_iterations", 2, ts=datetime(2026, 8, 1, 10, 1, 0))
    collector.record("loop_iterations", 12, ts=datetime(2026, 8, 1, 10, 2, 0))
    collector.record("gate_wait_seconds", 90000)
    signals = MetricRules.evaluate(collector.snapshot())
    assert len(signals) == 5
    for signal in signals:
        assert isinstance(signal, Signal)
        assert signal.type == "metric_threshold"
        assert signal.source == "metric_rules"
        assert signal.evidence


# ---------------------------------------------------------------------------
# MetricRules：阈值边界
# ---------------------------------------------------------------------------


def test_review_pass_rate_boundary():
    healthy = MetricsSnapshot(metrics={"review_pass_rate": [MetricPoint(name="review_pass_rate", value=0.6)]})
    assert MetricRules.evaluate(healthy) == []
    breach = MetricsSnapshot(metrics={"review_pass_rate": [MetricPoint(name="review_pass_rate", value=0.599)]})
    assert len(MetricRules.evaluate(breach)) == 1


def test_rework_rate_boundary():
    healthy = MetricsSnapshot(metrics={"rework_rate": [MetricPoint(name="rework_rate", value=0.3)]})
    assert MetricRules.evaluate(healthy) == []
    breach = MetricsSnapshot(metrics={"rework_rate": [MetricPoint(name="rework_rate", value=0.301)]})
    assert len(MetricRules.evaluate(breach)) == 1


def test_action_item_close_rate_boundary():
    healthy = MetricsSnapshot(metrics={"action_item_close_rate": [MetricPoint(name="action_item_close_rate", value=0.5)]})
    assert MetricRules.evaluate(healthy) == []
    breach = MetricsSnapshot(metrics={"action_item_close_rate": [MetricPoint(name="action_item_close_rate", value=0.499)]})
    assert len(MetricRules.evaluate(breach)) == 1


def test_gate_wait_seconds_boundary():
    healthy = MetricsSnapshot(metrics={"gate_wait_seconds": [MetricPoint(name="gate_wait_seconds", value=86400.0)]})
    assert MetricRules.evaluate(healthy) == []
    breach = MetricsSnapshot(metrics={"gate_wait_seconds": [MetricPoint(name="gate_wait_seconds", value=86400.1)]})
    assert len(MetricRules.evaluate(breach)) == 1
