"""校验 config-catalog freshness（v0.7 T14.8）。"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main(argv: list[str]) -> int:
    target = Path(argv[1]) if len(argv) > 1 else ROOT / "docs/config-catalog.md"
    with tempfile.TemporaryDirectory() as tmp:
        fresh = Path(tmp) / "fresh.md"
        subprocess.run([sys.executable, str(ROOT / "scripts/gen_config_catalog.py"), str(fresh)], check=True)
        current = target.read_text(encoding="utf-8") if target.exists() else ""
        expected = fresh.read_text(encoding="utf-8")
    if current != expected:
        print(f"config-catalog is STALE: {target} != generated (run scripts/gen_config_catalog.py)", file=sys.stderr)
        return 1
    print(f"config-catalog fresh: {target}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
