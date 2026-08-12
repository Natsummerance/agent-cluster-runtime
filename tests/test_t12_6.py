"""T12.6 质量门禁：LLM-as-judge + eval 回归集 + SessionDriver 集成。"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from agent_cluster.eval import (
    BUILTIN_SUITE,
    compare_to_baseline,
    load_baseline,
    run_suite,
    save_baseline,
)
from agent_cluster.judge import JudgeVerdict, LLMJudge, GATE_ARTIFACT_MAP, parse_verdict, read_artifact
from agent_cluster.models import TokenUsage
from agent_cluster.runtime import ChatResponse, DeterministicClient
from agent_cluster.session import SessionDriver

MINI_FLOW = """name: t12.6-mini
thread_id: "t:12.6"
nodes:
  - {id: start, type: start}
  - {id: requirements, type: agent, role: pm}
  - {id: requirement_gate, type: gate, gate: requirement_confirmation}
  - {id: end, type: end}
edges:
  - {from: start, to: requirements}
  - {from: requirements, to: requirement_gate}
  - {from: requirement_gate, to: end, on_accept: end, on_reject: requirements}
"""


# ---------------------------------------------------------------------------
# judge：解析 / 产物映射 / 模型调用 / 线程隔离
# ---------------------------------------------------------------------------


def test_parse_verdict_valid():
    verdict = parse_verdict('```json\n{"verdict": "revise", "reason": "缺验收标准", "suggestions": ["补充验收", "加用例"]}\n```')
    assert verdict.verdict == "revise"
    assert "验收" in verdict.reason
    assert verdict.suggestions == ["补充验收", "加用例"]


def test_parse_verdict_invalid_defaults_pass():
    verdict = parse_verdict("评审输出无法解析")
    assert verdict.verdict == "pass"
    assert "无法解析" in verdict.reason


def test_artifact_map_and_read(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "PRD.md").write_text("# PRD 内容", encoding="utf-8")
    assert read_artifact("requirement_confirmation", tmp_path) == "# PRD 内容"
    assert read_artifact("design_review", tmp_path) is None
    assert GATE_ARTIFACT_MAP["requirement_confirmation"] == "docs/PRD.md"


def test_judge_evaluate_with_fake_client_and_usage():
    usage_collected = []

    class FakeClient(DeterministicClient):
        async def complete_with_tools(self, messages, tools):
            text = json.dumps({"verdict": "pass", "reason": "OK", "suggestions": []}, ensure_ascii=False)
            usage = TokenUsage(prompt_tokens=50, completion_tokens=10, total_tokens=60, model="deterministic", estimated=True)
            return ChatResponse(text=text, tool_calls=[], usage=usage)

    judge = LLMJudge(client=FakeClient(), on_usage=usage_collected.append)
    verdict = judge.evaluate("requirement_confirmation", ".")
    assert verdict.verdict == "pass"
    assert usage_collected and usage_collected[0].total_tokens == 60


def test_judge_evaluate_inside_running_loop(tmp_path):
    """evaluate 在运行中的 asyncio 事件循环内调用（server 会话线程场景）必须不抛错。"""
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "PRD.md").write_text("# PRD", encoding="utf-8")

    class FakeClient(DeterministicClient):
        async def complete_with_tools(self, messages, tools):
            text = json.dumps({"verdict": "revise", "reason": "需补充边界", "suggestions": ["补边界"]}, ensure_ascii=False)
            return ChatResponse(text=text, tool_calls=[], usage=None)

    async def main():
        judge = LLMJudge(client=FakeClient())
        return judge.evaluate("requirement_confirmation", tmp_path)

    verdict = asyncio.run(main())
    assert verdict.verdict == "revise"


# ---------------------------------------------------------------------------
# eval：回归套件 / 基线对比
# ---------------------------------------------------------------------------


def test_eval_run_mini_scenario(tmp_path):
    suite = [item for item in BUILTIN_SUITE if item.name == "mini-pm-gate"]
    report = run_suite(root=tmp_path, suite=suite)
    assert report["metrics"]["completion_rate"] == 1.0
    assert report["scenarios"][0]["passed"] is True
    assert report["total_tokens"] > 0


def test_compare_to_baseline_no_regression():
    report = {"metrics": {"completion_rate": 1.0, "tool_correctness": 1.0, "test_pass_rate": 1.0}}
    baseline = {"metrics": {"completion_rate": 0.9, "tool_correctness": 0.9, "test_pass_rate": 0.9}}
    assert compare_to_baseline(report, baseline, threshold=0.05) == []


def test_compare_to_baseline_detects_regression():
    report = {"metrics": {"completion_rate": 0.5, "tool_correctness": 1.0, "test_pass_rate": 1.0}}
    baseline = {"metrics": {"completion_rate": 1.0, "tool_correctness": 1.0, "test_pass_rate": 1.0}}
    issues = compare_to_baseline(report, baseline, threshold=0.05)
    assert len(issues) == 1
    assert "completion_rate" in issues[0]


def test_baseline_save_load_roundtrip(tmp_path):
    report = {
        "generated_at": "2026-01-01T00:00:00Z",
        "metrics": {"completion_rate": 1.0, "tool_correctness": 1.0, "test_pass_rate": 1.0},
        "total_tokens": 100,
    }
    path = tmp_path / "baseline.json"
    save_baseline(report, path)
    loaded = load_baseline(path)
    assert loaded is not None
    assert loaded["metrics"]["completion_rate"] == 1.0
    assert load_baseline(tmp_path / "missing.json") is None


# ---------------------------------------------------------------------------
# SessionDriver：judge 集成（门提示含评审 + 用量入账本）
# ---------------------------------------------------------------------------


def test_session_driver_gate_uses_judge(tmp_path):
    flow = tmp_path / "flow.yaml"
    flow.write_text(MINI_FLOW, encoding="utf-8")
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "docs").mkdir()
    (ws / "docs" / "PRD.md").write_text("# PRD", encoding="utf-8")
    hints: list[str] = []
    judge = _FakeJudge(JudgeVerdict(verdict="revise", reason="缺少验收标准", suggestions=["补充验收"]))

    async def main():
        driver = SessionDriver(
            workspace=ws, goal="待办应用", flow=str(flow), deterministic=True, yes=False,
            prompt_fn=lambda hint: hints.append(hint) or "/skip",
            print_fn=lambda text: None,
            judge=judge,
        )
        return await driver.run()

    result = asyncio.run(main())
    assert result.exit_code == 0
    assert judge.calls, "judge 应被调用"
    assert judge.calls[0][0] == "requirement_confirmation"
    joined = "\n".join(hints)
    assert "[LLM 评审]" in joined
    assert "revise" in joined
    summary = result.token_summary or {}
    assert summary.get("by_role", {}).get("judge", 0) == 0  # 假 judge 无用量；验证链路不报错


class _FakeJudge:
    """可注入 SessionDriver 的假 judge（不产生真实模型调用）。"""

    def __init__(self, verdict: JudgeVerdict) -> None:
        self.verdict = verdict
        self.on_usage = None
        self.calls: list[tuple] = []

    def evaluate(self, kind: str, workspace, context: str = "") -> JudgeVerdict:
        self.calls.append((kind, str(workspace), context))
        return self.verdict


# ---------------------------------------------------------------------------
# CLI：eval / --no-judge 解析
# ---------------------------------------------------------------------------


def test_cli_parser_eval_and_no_judge():
    from agent_cluster.cli import build_parser

    parser = build_parser()
    args = parser.parse_args(["eval", "--scenario", "mini-pm-gate", "--save-baseline"])
    assert args.command == "eval"
    assert args.scenario == "mini-pm-gate"
    assert args.save_baseline is True
    args2 = parser.parse_args(["build", "--goal", "x", "--no-judge"])
    assert args2.no_judge is True
