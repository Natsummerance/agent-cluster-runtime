"""生成 config-catalog（v0.7 T14.8）：从 BUILTIN_PROFILES 渲染，永不腐烂。

用法：scripts/gen_config_catalog.py [out_path]；缺省打印到 stdout。
"""

from __future__ import annotations

import sys
from pathlib import Path

from agent_cluster.config_layers import BUILTIN_PROFILES, dump_config_entries


def render() -> str:
    lines = [
        "# 配置目录（config-catalog）",
        "",
        "> 由 `scripts/gen_config_catalog.py` 生成，勿手改；`scripts/verify_config_catalog.py` 校验 freshness。",
        "> 语义：profile 行按 id 整块替换 + disabled（对照 dsh 配置分层契约，见 `docs/porting/`）。",
        "",
    ]
    for profile, entries in BUILTIN_PROFILES.items():
        lines.append(f"## profile: {profile}")
        lines.append("")
        lines.append("| id | disabled | 配置 |")
        lines.append("|---|---|---|")
        for entry in dump_config_entries(entries):
            payload = ", ".join(f"{k}={v}" for k, v in entry.items() if k not in ("id", "disabled"))
            lines.append(f"| {entry['id']} | {entry['disabled']} | `{payload}` |")
        lines.append("")
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    text = render()
    if len(argv) > 1:
        Path(argv[1]).write_text(text, encoding="utf-8")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
