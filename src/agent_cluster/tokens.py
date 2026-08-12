"""统一 token 估算与用量计算（v0.3，独立模块避免 tools/runtime 循环导入）。

- ``estimate_tokens``：单文本 token 估算（tiktoken 可选精确，否则 CJK 加权启发式）。
- ``estimate_usage``：一次模型调用的用量估算（prompt + completion + 工具调用）。
"""

from __future__ import annotations

import json
from typing import Any

from agent_cluster.models import TokenUsage

__all__ = ["estimate_tokens", "estimate_usage"]

_TIKTOKEN_ENCODING: Any | None = None  # 惰性缓存：None 未探测 / False 不可用 / 对象可用


def _tiktoken_encoding() -> Any | None:
    """可选精确 tokenizer（tiktoken 未安装/加载失败时返回 None，走内置估算器）。"""
    global _TIKTOKEN_ENCODING
    if _TIKTOKEN_ENCODING is None:
        try:
            import tiktoken

            _TIKTOKEN_ENCODING = tiktoken.get_encoding("o200k_base")
        except Exception:  # noqa: BLE001 —— 可选依赖缺失/加载失败均回退启发式估算
            _TIKTOKEN_ENCODING = False
    return _TIKTOKEN_ENCODING or None


def estimate_tokens(text: str, model: str = "") -> int:
    """统一 token 估算器（混合口径）：tiktoken 可用时精确，否则启发式。

    - 启发式：CJK 字符约 1.6 token/字（中文按比例加权避免低估），
      其余字符按 4 字符/token，最少 1。
    - ``model`` 仅作签名兼容（tiktoken 按模型选编码的扩展位）。
    """
    if not text:
        return 0
    encoding = _tiktoken_encoding()
    if encoding is not None:
        try:
            return len(encoding.encode(text))
        except Exception:  # noqa: BLE001 —— 编码失败回退启发式
            pass
    cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    other = max(0, len(text) - cjk)
    return max(1, int(cjk * 1.6) + (other + 3) // 4)


def estimate_usage(
    messages: list[dict],
    text: str,
    tool_calls: list[Any] | None = None,
    model: str = "",
) -> TokenUsage:
    """统一估算一次调用的 token 用量（无 API usage 时的回退口径）。

    - prompt = 各消息内容估算 + 每消息 4 token 协议开销；
    - completion = 回复文本 + 各工具调用参数/名称估算。
    """
    prompt = sum(estimate_tokens(str(message.get("content") or ""), model) for message in messages)
    prompt += 4 * len(messages)
    completion = estimate_tokens(text, model)
    for call in tool_calls or []:
        completion += estimate_tokens(
            json.dumps(call.args, ensure_ascii=False, default=str), model
        )
        completion += len(call.name) // 4 + 4
    return TokenUsage(
        prompt_tokens=prompt,
        completion_tokens=completion,
        total_tokens=prompt + completion,
        model=model,
        estimated=True,
    )
