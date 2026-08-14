"""Agent Notes 树校验（v0.7 T14.8，dsh .agents/notes 契约移植）。

约束：路径 {lifecycle}/{class}/yyyy-mm-dd-topic-title.md；lifecycle 四态封闭集；
class 六类封闭集；整棵树禁止 INDEX.md（不设中央索引）。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

LIFECYCLES = {"proposed", "implemented", "archived", "rejected"}
CLASSES = {"feature", "bug-fix", "simplification", "architecture", "process", "testing"}
FILENAME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}-[a-z0-9-]+\.md$")


def verify(root: Path) -> list[str]:
    errors: list[str] = []
    if not root.exists():
        return [f"notes tree missing: {root}"]
    for path in sorted(root.rglob("*.md")):
        rel = path.relative_to(root)
        parts = rel.parts
        if len(parts) < 3:
            continue
        lifecycle, cls = parts[0], parts[1]
        if lifecycle not in LIFECYCLES:
            errors.append(f"bad lifecycle {lifecycle!r}: {rel}")
        if cls not in CLASSES:
            errors.append(f"bad class {cls!r}: {rel}")
        if not FILENAME_RE.match(parts[-1]):
            errors.append(f"bad filename {parts[-1]!r}: {rel}")
    for index in sorted(root.rglob("INDEX.md")):
        errors.append(f"central INDEX forbidden: {index.relative_to(root)}")
    return errors


def main(argv: list[str]) -> int:
    root = Path(argv[1] if len(argv) > 1 else ".agents/notes")
    errors = verify(root)
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1
    print(f"agent notes tree OK: {root}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
