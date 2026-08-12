"""T11.4 REPL 测试：chat 子命令注册、岗位选择、多轮上下文、工具模式落盘、
--yes 拒绝危险工具、插件 hooks（session_start/end）、斜杠命令与 token 记账。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from agent_cluster.cli import _cmd_chat, build_parser
from agent_cluster.plugins import PluginManager
from agent_cluster.repl import ReplSession, choose_role_id


def _scripted_prompts(*answers: str):
    """返回 scripted prompt_fn：依次弹出答案，耗尽后返回 /exit。"""
    queue = list(answers)

    def prompt(_prompt: str) -> str:
        return queue.pop(0) if queue else "/exit"

    return prompt


def test_chat_subcommand_registered(tmp_path: Path):
    """chat 子命令参数解析：--workspace/--model/--budget/--yes/--plugin-dir/--mcp 等。"""
    parser = build_parser()
    args = parser.parse_args(
        [
            "chat",
            "--workspace", str(tmp_path / "ws"),
            "--model", "deterministic",
            "--budget", "1234",
            "--max-rounds", "3",
            "--yes",
            "--deterministic",
            "--plugin-dir", str(tmp_path / "p1"),
            "--plugin-dir", str(tmp_path / "p2"),
            "--mcp", "x=cmd",
            "--skills-root", str(tmp_path / "s"),
        ]
    )
    assert args.func == _cmd_chat
    assert args.workspace == str(tmp_path / "ws")
    assert args.model == "deterministic"
    assert args.budget == 1234
    assert args.max_rounds == 3
    assert args.yes is True
    assert args.deterministic is True
    assert args.plugin_dir == [str(tmp_path / "p1"), str(tmp_path / "p2")]
    assert args.mcp == ["x=cmd"]
    assert args.skills_root == str(tmp_path / "s")


def test_choose_role_id_keywords():
    """指令关键词 -> 岗位选择启发式。"""
    assert choose_role_id("帮我测试一下这个接口") == "qa"
    assert choose_role_id("部署 docker 镜像并监控") == "devops"
    assert choose_role_id("写一份 README 文档") == "docs"
    assert choose_role_id("做技术选型和系统架构设计") == "architect"
    assert choose_role_id("做前端页面和组件") == "frontend"
    assert choose_role_id("训练一个推荐模型") == "algorithm"
    assert choose_role_id("实现登录接口和业务逻辑") == "backend"
    assert choose_role_id("收集需求写 PRD") == "pm"
    assert choose_role_id("随便什么指令") == "backend"


def test_repl_multi_turn_context_and_ledger(tmp_path: Path):
    """多轮连续：轮次计数、token 记账增长、跨轮上下文保留。"""
    ws = tmp_path / "ws"
    session = ReplSession(
        workspace=ws,
        model="deterministic",
        deterministic=True,
        budget=100_000,
        max_rounds=2,
        prompt_fn=_scripted_prompts("做一个待办事项网站的后端接口", "写一份 README 文档"),
    )
    code = session.run()
    assert code == 0
    assert ws.is_dir()
    assert session._turn_count == 2
    assert session.ledger.total() > 0
    assert session.ledger.remaining() < session.ledger.budget
    assert session.ledger.over_budget() is False
    # 跨轮上下文保留：前一轮指令仍出现在消息历史中
    assert any("待办事项" in str(m.get("content", "")) for m in session.messages)


def test_repl_tool_mode_writes_file(tmp_path: Path):
    """工具模式：确定性 tool_script 驱动 write_file 真实落盘。"""
    ws = tmp_path / "ws"
    session = ReplSession(
        workspace=ws,
        model="deterministic",
        deterministic=True,
        max_rounds=4,
        tool_script=[
            {"name": "write_file", "args": {"path": "hello.txt", "content": "hello repl"}}
        ],
        prompt_fn=_scripted_prompts("在项目里创建一个 hello.txt"),
    )
    code = session.run()
    assert code == 0
    assert (ws / "hello.txt").read_text(encoding="utf-8") == "hello repl"
    # 工具结果进入消息历史
    assert any("[工具结果 write_file ok=True]" in str(m.get("content", "")) for m in session.messages)


def test_repl_yes_rejects_dangerous_tool(tmp_path: Path):
    """--yes：危险工具自动拒绝且流程继续（不执行、不崩溃）。"""
    ws = tmp_path / "ws"
    session = ReplSession(
        workspace=ws,
        model="deterministic",
        deterministic=True,
        yes=True,
        max_rounds=4,
        tool_script=[{"name": "run_python", "args": {"code": "print(1)"}}],
        prompt_fn=_scripted_prompts("跑一段 python 脚本"),
    )
    code = session.run()
    assert code == 0
    assert any("ok=False" in str(m.get("content", "")) for m in session.messages)
    assert any("被拒绝" in str(m.get("content", "")) for m in session.messages)


def test_repl_plugin_hooks_session_start_end(tmp_path: Path):
    """插件 hooks：session_start / session_end 自动执行并落盘标记。"""
    hook_script = tmp_path / "hook_echo.py"
    hook_script.write_text(
        "import os, pathlib\n"
        "ws = pathlib.Path(os.environ['AGENT_CLUSTER_WORKSPACE'])\n"
        "ev = os.environ['AGENT_CLUSTER_EVENT']\n"
        "(ws / ('hook-' + ev + '.txt')).write_text(ev)\n",
        encoding="utf-8",
    )
    plugin_dir = tmp_path / "plugins" / "hookdemo"
    (plugin_dir / ".codex-plugin").mkdir(parents=True)
    manifest = {
        "name": "hookdemo",
        "version": "1.0.0",
        "description": "hooks demo",
        "author": {"name": "tester"},
        "hooks": {
            "session_start": [{"command": f'"{sys.executable}" "{hook_script}"'}],
            "session_end": [{"command": f'"{sys.executable}" "{hook_script}"'}],
        },
    }
    (plugin_dir / ".codex-plugin" / "plugin.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )
    manager = PluginManager(search_dirs=[str(plugin_dir)])
    manager.scan()

    ws = tmp_path / "ws"
    session = ReplSession(
        workspace=ws,
        model="deterministic",
        deterministic=True,
        plugin_manager=manager,
        prompt_fn=_scripted_prompts("写一个 hello"),
    )
    code = session.run()
    assert code == 0
    assert (ws / "hook-session_start.txt").read_text(encoding="utf-8") == "session_start"
    assert (ws / "hook-session_end.txt").read_text(encoding="utf-8") == "session_end"


def test_repl_slash_commands(tmp_path: Path):
    """/status /budget /skills /plugins /help 命令可执行且输出正常。"""
    out: list[str] = []
    session = ReplSession(
        workspace=tmp_path / "ws",
        model="deterministic",
        deterministic=True,
        budget=50_000,
        prompt_fn=_scripted_prompts("/status", "/budget", "/skills", "/plugins", "/help"),
        print_fn=out.append,
    )
    code = session.run()
    assert code == 0
    text = "\n".join(out)
    assert "轮次：0" in text
    assert "预算：50000" in text
    assert "（未挂载技能" in text
    assert "（未启用插件" in text
    assert "/exit" in text
