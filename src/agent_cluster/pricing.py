"""token 价格表与金额折算（v0.5 T12.1）。

- ``ModelPrice``：单模型单价（每 1M token 的输入/输出/缓存命中价，单位 USD）。
- ``PRICE_TABLE``：内置常见模型价目（DeepSeek/OpenAI/Claude，价格可能随供应商调整，
  可通过 ``load_price_overrides`` 覆盖；未核实的价格一律按"参考价"处理）。
- ``cost_for(usage, model)``：按用量折算金额；未知模型返回 ``None``。
- ``CostLedger``：一次会话的累计金额（按模型聚合，供报表与仪表盘）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agent_cluster.models import TokenUsage

__all__ = [
    "ModelPrice",
    "PRICE_TABLE",
    "load_price_overrides",
    "resolve_price",
    "cost_for",
    "CostLedger",
    "format_cost",
]


@dataclass(frozen=True)
class ModelPrice:
    """模型单价：每 1M token 的美元价。"""

    input_per_m: float
    output_per_m: float
    cache_read_per_m: float = 0.0
    currency: str = "USD"
    source: str = "builtin"


# 内置参考价目（USD / 1M tokens）。价格随供应商调整，均按"参考价"处理，
# 用户可通过配置文件或环境变量覆盖（load_price_overrides）。
PRICE_TABLE: dict[str, ModelPrice] = {
    "deepseek-chat": ModelPrice(input_per_m=0.27, output_per_m=1.10, cache_read_per_m=0.07),
    "deepseek-reasoner": ModelPrice(input_per_m=0.55, output_per_m=2.19, cache_read_per_m=0.14),
    "gpt-5": ModelPrice(input_per_m=1.25, output_per_m=10.00, cache_read_per_m=0.125),
    "gpt-5-mini": ModelPrice(input_per_m=0.25, output_per_m=2.00, cache_read_per_m=0.025),
    "gpt-4o": ModelPrice(input_per_m=2.50, output_per_m=10.00, cache_read_per_m=0.25),
    "claude-sonnet-4-5": ModelPrice(input_per_m=3.00, output_per_m=15.00, cache_read_per_m=0.30),
    "claude-opus-4-5": ModelPrice(input_per_m=5.00, output_per_m=25.00, cache_read_per_m=0.50),
    "claude-haiku-4-5": ModelPrice(input_per_m=1.00, output_per_m=5.00, cache_read_per_m=0.10),
}

# 模型别名（供应商前缀匹配，避免写全名）
_MODEL_ALIASES: dict[str, str] = {
    "deepseek-v3": "deepseek-chat",
    "deepseek-r1": "deepseek-reasoner",
    "gpt-5.1": "gpt-5",
    "claude-sonnet": "claude-sonnet-4-5",
    "claude-opus": "claude-opus-4-5",
    "claude-haiku": "claude-haiku-4-5",
}


def load_price_overrides(path: str | Path | None = None) -> dict[str, ModelPrice]:
    """加载用户价格覆盖（JSON：``{"model": {"input_per_m": .., "output_per_m": ..}}``）。

    - 显式 ``path`` 或 ``AGENT_CLUSTER_PRICING`` 环境变量指向的 JSON 文件；
    - 不存在/解析失败返回空字典（容错）。
    """
    if path is None:
        import os

        env_path = os.environ.get("AGENT_CLUSTER_PRICING")
        path = env_path or ""
    if not path:
        return {}
    try:
        import json

        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return {}
    overrides: dict[str, ModelPrice] = {}
    for name, price in data.items():
        if isinstance(price, dict):
            overrides[name.lower()] = ModelPrice(
                input_per_m=float(price.get("input_per_m", 0.0)),
                output_per_m=float(price.get("output_per_m", 0.0)),
                cache_read_per_m=float(price.get("cache_read_per_m", 0.0)),
                currency=str(price.get("currency", "USD")),
                source="override",
            )
    return overrides


def resolve_price(model: str, overrides: dict[str, ModelPrice] | None = None) -> ModelPrice | None:
    """解析模型单价：精确名 > 别名 > 供应商前缀 > 覆盖表 > 内置表。

    - ``overrides`` 优先于内置表（用户自定义单价）。
    """
    if not model:
        return None
    key = model.lower()
    table = dict(overrides or {})
    table.update(PRICE_TABLE)
    if key in table:
        return table[key]
    if key in _MODEL_ALIASES:
        alias = _MODEL_ALIASES[key]
        if alias in table:
            return table[alias]
    # 供应商前缀匹配（deepseek-* / gpt-* / claude-*）
    for prefix, name in (
        ("deepseek", "deepseek-chat"),
        ("gpt", "gpt-5"),
        ("claude", "claude-sonnet-4-5"),
        ("openai", "gpt-5"),
    ):
        if key.startswith(prefix) and name in table:
            return table[name]
    return None


def cost_for(usage: TokenUsage, overrides: dict[str, ModelPrice] | None = None) -> float | None:
    """按用量折算金额（USD）。未知模型或无用量返回 ``None``。"""
    price = resolve_price(usage.model, overrides)
    if price is None or usage.total_tokens <= 0:
        return None
    cache_tokens = max(0, getattr(usage, "cache_read_tokens", 0) or 0)
    input_tokens = max(0, usage.prompt_tokens - cache_tokens)
    return (
        input_tokens / 1_000_000 * price.input_per_m
        + cache_tokens / 1_000_000 * price.cache_read_per_m
        + usage.completion_tokens / 1_000_000 * price.output_per_m
    )


def format_cost(cost: float | None, currency: str = "USD") -> str:
    """金额格式化：None -> '-'；<0.01 显示为 '~0.00'。"""
    if cost is None:
        return "-"
    return f"{cost:.4f} {currency}"


@dataclass
class CostLedger:
    """会话级金额累计（按模型聚合）。"""

    entries: dict[str, float] = field(default_factory=dict)

    def record(self, usage: TokenUsage, overrides: dict[str, ModelPrice] | None = None) -> float | None:
        """记录一次用量的金额并返回本次成本（未知模型返回 None）。"""
        cost = cost_for(usage, overrides)
        if cost is None:
            return None
        key = usage.model or "unknown"
        self.entries[key] = self.entries.get(key, 0.0) + cost
        return cost

    @property
    def total(self) -> float:
        return sum(self.entries.values())

    def summary(self) -> dict[str, Any]:
        return {"by_model": dict(self.entries), "total": self.total, "currency": "USD"}
