"""自适应 token 预算估算（v0.5 T12.1，规划按 token 不按时间）。

- ``estimate_budget(goal, plan_text="", history=None)``：启发式 + 历史校准。
  - 启发式：基础 50K + 需求文本长度加权 + 计划产物/任务数 + 复杂度信号；
  - 校准：用历史 ``(estimated, actual)`` 的比率中位数修正，避免系统性偏差；
  - 结果夹在 ``[BUDGET_MIN, BUDGET_MAX]``。
- ``BudgetProposal``：建议预算 + 依据 + 置信度（面板展示"为什么是这个值"）。
- ``cache_eligible_tokens(prefix)``：前缀缓存适用判定（>=1024 tokens 才有效）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from agent_cluster.tokens import estimate_tokens

__all__ = [
    "BUDGET_MIN",
    "BUDGET_MAX",
    "DEFAULT_BUDGET",
    "CACHE_MIN_TOKENS",
    "BudgetProposal",
    "estimate_budget",
    "cache_eligible_tokens",
]

BUDGET_MIN = 20_000
BUDGET_MAX = 2_000_000
DEFAULT_BUDGET = 500_000
CACHE_MIN_TOKENS = 1024

# 启发式权重（经验值，随 estimate_accuracy 历史自动校准）
_BASE = 50_000
_PER_CHAR = 80  # 每字符/汉字的基础开销
_PER_TASK = 8_000  # 每任务/产物项
_COMPLEXITY_MARKERS = (
    "微服务",
    "分布式",
    "多租户",
    "高并发",
    "数据库",
    "认证",
    "支付",
    "实时",
    "大模型",
    "算法",
    "重构",
    "迁移",
    "kubernetes",
    "docker",
    "oauth",
    "websocket",
    "离线",
    "缓存",
    "消息队列",
)


@dataclass(frozen=True)
class BudgetProposal:
    """自适应预算建议（供面板/CLI 展示与覆盖）。"""

    budget: int
    rationale: str
    confidence: float
    base_estimate: int
    calibrated: int


def _complexity_score(text: str) -> int:
    """复杂度信号：命中标记 +1，上限 +8。"""
    low = text.lower()
    return min(8, sum(1 for marker in _COMPLEXITY_MARKERS if marker in low))


def _calibration_ratio(history: Sequence[tuple[int, int]]) -> float | None:
    """历史 ``(estimated, actual)`` 的比率 ``actual/estimated`` 中位数。"""
    ratios = [
        actual / estimated if estimated > 0 else 1.0
        for estimated, actual in history
    ]
    if not ratios:
        return None
    ratios.sort()
    return ratios[len(ratios) // 2]


def estimate_budget(
    goal: str,
    plan_text: str = "",
    history: Sequence[tuple[int, int]] | None = None,
) -> BudgetProposal:
    """估算建议预算。

    - ``goal``：需求文本；``plan_text``：计划/产物清单文本（可空）。
    - ``history``：历史样本 ``[(estimated, actual), ...]``，用于校准
      （estimate_accuracy 的反馈回路）。
    """
    combined = f"{goal}\n{plan_text}"
    goal_tokens = estimate_tokens(combined)
    tasks_guess = max(1, len([line for line in plan_text.splitlines() if line.strip()]) if plan_text else 1)
    complexity = _complexity_score(combined)

    base_estimate = int(
        _BASE
        + goal_tokens * _PER_CHAR / 4
        + tasks_guess * _PER_TASK
        + complexity * _PER_TASK
    )
    base_estimate = max(BUDGET_MIN, min(BUDGET_MAX, base_estimate))

    calibrated = base_estimate
    confidence = 0.5
    ratio = _calibration_ratio(history or [])
    if ratio is not None:
        calibrated = int(base_estimate * ratio)
        calibrated = max(BUDGET_MIN, min(BUDGET_MAX, calibrated))
        confidence = min(0.95, 0.5 + 0.05 * len(history or []))

    rationale = (
        f"需求文本 {goal_tokens} token、任务项约 {tasks_guess}、复杂度信号 {complexity}；"
        + (
            f"历史 {len(history or [])} 条校准系数 {ratio:.2f}"
            if ratio is not None
            else "无历史校准（默认置信度 0.5）"
        )
    )
    return BudgetProposal(
        budget=calibrated,
        rationale=rationale,
        confidence=confidence,
        base_estimate=base_estimate,
        calibrated=calibrated,
    )


def cache_eligible_tokens(prefix: str) -> int:
    """前缀缓存适用判定：返回可缓存前缀的估算 token 数（< CACHE_MIN_TOKENS 返回 0）。"""
    if not prefix:
        return 0
    tokens = estimate_tokens(prefix)
    return tokens if tokens >= CACHE_MIN_TOKENS else 0
