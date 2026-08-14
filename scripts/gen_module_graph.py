"""生成 module-graph（v0.7 T14.8）：扫描 src/agent_cluster 模块与包内导入，输出 mermaid。

用法：scripts/gen_module_graph.py [out_path]；缺省打印到 stdout。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src/agent_cluster"
IMPORT_RE = re.compile(r"(?:from agent_cluster\.(\w+) import|import agent_cluster\.(\w+))")


def render() -> str:
    modules = sorted(p.stem for p in SRC.glob("*.py") if p.stem != "__init__")
    edges: set[tuple[str, str]] = set()
    for path in sorted(SRC.glob("*.py")):
        src = path.stem
        if src == "__init__":
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            match = IMPORT_RE.search(line)
            if match:
                target = match.group(1) or match.group(2)
                if target in modules and target != src:
                    edges.add((src, target))
    lines = [
        "# 模块依赖图（module-graph）",
        "",
        "> 由 `scripts/gen_module_graph.py` 生成，勿手改；`scripts/verify_module_graph.py` 校验 freshness。",
        "",
        "```mermaid",
        "flowchart LR",
    ]
    for module in modules:
        lines.append(f"    {module}[{module}]")
    for src, target in sorted(edges):
        lines.append(f"    {src} --> {target}")
    lines.append("```")
    return "\n".join(lines) + "\n"


def main(argv: list[str]) -> int:
    text = render()
    if len(argv) > 1:
        Path(argv[1]).write_text(text, encoding="utf-8")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
