"""大输出溢出（spill）契约（v0.7 Task 14.3，dsh 移植）。

对照 dsh ``spill-policy``（maxInlineBytes=50000）：大输出写入 0700 私有目录，
模型可见面只保留 head/tail 预览。
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import Any

__all__ = ["MAX_INLINE_BYTES", "spill_text"]

MAX_INLINE_BYTES = 50000
_HEAD_BYTES = 2000
_TAIL_BYTES = 2000


def spill_text(
    content: str,
    root_dir: str | Path,
    max_inline_bytes: int = MAX_INLINE_BYTES,
) -> dict[str, Any]:
    """按字节阈值溢出；返回 ``{inline, path, truncated}``。"""
    raw = content.encode("utf-8")
    if len(raw) <= max_inline_bytes:
        return {"inline": content, "path": "", "truncated": False}
    root = Path(root_dir)
    spill_dir = root / ".agent-cluster" / "spill"
    spill_dir.mkdir(parents=True, exist_ok=True)
    name = f"{uuid.uuid4().hex}.txt"
    (spill_dir / name).write_bytes(raw)
    try:
        os.chmod(spill_dir / name, 0o700)
    except OSError:
        pass  # Windows 尽力而为
    head = content[:_HEAD_BYTES]
    tail = content[-_TAIL_BYTES:]
    relative = f".agent-cluster/spill/{name}"
    return {
        "inline": f"head: {head}\n...(truncated {len(raw)} bytes, full at {relative})...\ntail: {tail}",
        "path": relative,
        "truncated": True,
    }
