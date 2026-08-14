"""Task 14.6 带 key 真实 DeepSeek 缓存命中率门槛测试（dsh with-key 政策，自跳过）。

- ``DEEPSEEK_API_KEY`` 未设置时整模块跳过（CI 无 key 即跳过）。
- 有 key 时：真实多轮调用，稳态（排除首轮冷启动）命中率 >= 0.98 为发布门槛。
"""

from __future__ import annotations

import asyncio
import os

import pytest

from agent_cluster.runtime import DeepSeekClient

pytestmark = pytest.mark.skipif(
    not os.environ.get("DEEPSEEK_API_KEY"),
    reason="DEEPSEEK_API_KEY 未设置：跳过真实 API 命中率测试（自跳过政策）",
)


def test_deepseek_steady_state_cache_hit_ratio() -> None:
    """真实 DeepSeek 多轮会话：稳态命中率 >= 98%（首轮冷启动不计）。

    关键机制（实测）：DeepSeek 缓存按 ~256 token 块粒度；每轮新内容所在块必 miss
    （~10 token/轮 + 块余量）。小前缀（<1 块）恒 0 命中；>=98% 需要稳定前缀足够大
    （~20KB 前缀实测稳态 99.7%）。本测试用大稳定前缀代表「system prompt + tools
    schema + 历史」的真实产品前缀。
    """
    client = DeepSeekClient(session_id="sess-t14-6")
    system = "你是一个严格按指令执行的测试助手。\n" * 1000  # ~20KB 稳定前缀
    messages: list[dict] = [{"role": "system", "content": system}]
    ratios: list[float] = []
    for turn in range(4):
        messages.append({"role": "user", "content": f"turn {turn}: reply OK"})
        text = asyncio.run(client.complete(list(messages)))
        messages.append({"role": "assistant", "content": text})
        usage = client.last_usage
        total = usage.cache_read_tokens + usage.cache_miss_tokens
        if total > 0:
            ratios.append(usage.cache_read_tokens / total)
    assert len(ratios) >= 2, f"缓存统计不足（可能 provider 未返回 cache 字段）：{ratios}"
    steady = ratios[1:]  # 排除首轮冷启动
    assert min(steady) >= 0.98, f"稳态缓存命中率未达 98% 门槛：{steady}"
