"""LLM 上下文缓存统计（v0.7 Task 14.6，dsh llm-deepseek 缓存优化移植）。

- ``extract_cache_tokens``：从 OpenAI/DeepSeek 风格（``prompt_cache_hit|miss_tokens``）
  与 Anthropic 风格（``cache_read|creation_input_tokens``）usage 提取缓存统计。
- ``cache_hit_ratio``：命中占比（首轮/无缓存时返回 None，不算入稳态）。
- ``cache_summary``：``llm/cache`` 事件载荷。

契约出处见 ``docs/porting/2026-08-14-dsh-porting.md``（MIT，dsh ``47f943859b``）。
"""

from __future__ import annotations

from typing import Any

from agent_cluster.models import TokenUsage

__all__ = ["cache_hit_ratio", "cache_summary", "extract_cache_tokens"]


def extract_cache_tokens(usage: Any) -> tuple[int, int]:
    """返回 (cache_read_tokens, cache_miss_tokens)；缺省为 (0, 0)。

    支持 OpenAI/DeepSeek 风格（``prompt_cache_hit_tokens`` /
    ``prompt_cache_miss_tokens``，对象属性或 dict）与 Anthropic 风格
    （``cache_read_input_tokens`` / ``cache_creation_input_tokens``）。
    """
    if usage is None:
        return 0, 0
    read = getattr(usage, "prompt_cache_hit_tokens", None)
    miss = getattr(usage, "prompt_cache_miss_tokens", None)
    if isinstance(usage, dict):
        read = usage.get("prompt_cache_hit_tokens", read)
        miss = usage.get("prompt_cache_miss_tokens", miss)
    if read is None:
        read = getattr(usage, "cache_read_input_tokens", None)
        if isinstance(usage, dict):
            read = usage.get("cache_read_input_tokens", read)
    if miss is None:
        miss = getattr(usage, "cache_creation_input_tokens", None)
        if isinstance(usage, dict):
            miss = usage.get("cache_creation_input_tokens", miss)
    return int(read or 0), int(miss or 0)


def cache_hit_ratio(cache_read_tokens: int, cache_miss_tokens: int) -> float | None:
    """命中占比；无缓存读写（首轮/不支持）返回 None，不计入稳态统计。"""
    total = cache_read_tokens + cache_miss_tokens
    if total <= 0:
        return None
    return cache_read_tokens / total


def cache_summary(usage: TokenUsage) -> dict[str, Any]:
    """构造 ``llm/cache`` 事件载荷。"""
    ratio = cache_hit_ratio(usage.cache_read_tokens, usage.cache_miss_tokens)
    return {
        "read_tokens": usage.cache_read_tokens,
        "miss_tokens": usage.cache_miss_tokens,
        "hit_ratio": ratio,
        "model": usage.model,
        "prompt_tokens": usage.prompt_tokens,
    }
