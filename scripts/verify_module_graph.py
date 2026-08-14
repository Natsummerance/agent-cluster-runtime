"""校验 module-graph freshness（v0.7 T14.8）。"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main(argv: list[str]) -> int:
    target = Path(argv[1]) if len(argv) > 1 else ROOT / "docs/module-graph.md"
    with tempfile.TemporaryDirectory() as tmp:
        fresh = Path(tmp) / "fresh.md"
        subprocess.run([sys.executable, str(ROOT / "scripts/gen_module_graph.py"), str(fresh)], check=True)
        current = target.read_text(encoding="utf-8") if target.exists() else ""
        expected = fresh.read_text(encoding="utf-8")
    if current != expected:
        print(f"module-graph is STALE: {target} != generated (run scripts/gen_module_graph.py)", file=sys.stderr)
        return 1
    print(f"module-graph fresh: {target}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
