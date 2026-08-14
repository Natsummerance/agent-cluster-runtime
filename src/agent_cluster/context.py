"""头锚定上下文管理（v0.7 Task 14.6，dsh compaction-basic 移植）。

- 保头 + 定价尾部、中间剪枝：前缀（system+tools+最早消息）保持稳定，复用 provider KV 缓存。
- 裁剪边界不拆 tool-call/result 对（对照 ``selectCompactableRange`` 的配对平衡）。

契约出处见 ``docs/porting/2026-08-14-dsh-porting.md``（MIT，dsh ``47f943859b``）。
"""

from __future__ import annotations

from typing import Any

__all__ = ["head_anchored_trim"]


def head_anchored_trim(
    items: list[dict[str, Any]],
    max_items: int,
    retain_tail: int | None = None,
) -> list[dict[str, Any]]:
    """头锚定裁剪：保留头部 + 尾部，剪掉中间；不拆 tool 结果序列。

    - ``items``：模型可见消息（role 字典）。
    - ``max_items``：目标上限（>0）。
    - ``retain_tail``：保留尾部条数（缺省 ``max(1, max_items // 3)``）。
    """
    if max_items <= 0:
        raise ValueError("max_items must be > 0")
    if len(items) <= max_items:
        return list(items)
    tail = max(1, max_items // 3) if retain_tail is None else retain_tail
    if tail > max_items:
        tail = max_items
    head = max_items - tail
    # 尾起点前移到非 tool 消息：避免 tool 结果失去其 assistant 前缀（不拆对）
    start = len(items) - tail
    while start < len(items) and items[start].get("role") == "tool" and start > head:
        start += 1
    if start <= head:
        return list(items[-max_items:])  # 极端退化：尾部已占满，保尾
    return list(items[:head]) + list(items[start:])
