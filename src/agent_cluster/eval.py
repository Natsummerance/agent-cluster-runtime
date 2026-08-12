"""eval 回归集（v0.5 T12.6）：确定性场景回归 + 基线对比防退化。

- ``EvalScenario``：一个回归场景（流程 YAML + 目标 + 预期产物/退出码/门数）。
- ``run_suite``：逐个场景跑 ``SessionDriver``（deterministic + yes，无 key），
  汇总三项指标：``completion_rate``（场景通过率）、``tool_correctness``
  （任务完成率：DONE / 终态任务）、``test_pass_rate``（qa 任务 DONE 占比）。
- ``compare_to_baseline``：与基线 JSON 对比，任一指标相对下降超阈值即判定回归。
- 供 ``agent-cluster eval`` 使用：回归集可防开发退化（T12.6 质量门禁之一）。
"""

from __future__ import annotations

import asyncio
import json
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agent_cluster.models import TaskStatus
from agent_cluster.session import SessionDriver

__all__ = [
    "EvalScenario",
    "BUILTIN_SUITE",
    "run_scenario",
    "run_suite",
    "compare_to_baseline",
    "load_baseline",
    "save_baseline",
    "DEFAULT_REGRESSION_THRESHOLD",
]

DEFAULT_REGRESSION_THRESHOLD = 0.05

MINI_FLOW = """name: eval-mini
thread_id: "t:eval:mini"
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

DEV_QA_FLOW = """name: eval-dev-qa
thread_id: "t:eval:devqa"
nodes:
  - {id: start, type: start}
  - {id: kickoff, type: meeting, meeting: kickoff, participants: [pm, pmo, frontend, backend, qa]}
  - {id: requirements, type: agent, role: pm}
  - {id: develop, type: agent, role: frontend}
  - {id: test, type: agent, role: qa}
  - {id: iteration_gate, type: gate, gate: iteration_acceptance}
  - {id: end, type: end}
edges:
  - {from: start, to: kickoff}
  - {from: kickoff, to: requirements}
  - {from: requirements, to: develop}
  - {from: develop, to: test}
  - {from: test, to: iteration_gate}
  - {from: iteration_gate, to: end, on_accept: end, on_reject: test}
"""


@dataclass
class EvalScenario:
    """单个回归场景定义。"""

    name: str
    goal: str
    flow: str
    flow_is_path: bool = False
    expect_exit_code: int = 0
    expect_files: tuple[str, ...] = ()
    min_gate_decisions: int = 0
    seed_files: dict[str, str] = field(default_factory=dict)
    role_tool_scripts: dict[str, list[dict]] | None = None


def _build_flow_path() -> Path:
    return Path(__file__).resolve().parents[2] / "examples" / "flows" / "build-product.yaml"


_QA_TOOL_SCRIPT: dict[str, list[dict]] = {
    # 裸 pytest 解析到当前 venv（离线可用）；python -m pytest 会命中 uv 基础解释器（无 pytest）
    "qa": [{"name": "run_tests", "args": {"command": "pytest -q", "timeout": 120}}],
}


def _suite() -> list[EvalScenario]:
    return [
        EvalScenario(
            name="mini-pm-gate",
            goal="待办事项应用",
            flow=MINI_FLOW,
            min_gate_decisions=1,
        ),
        EvalScenario(
            name="dev-qa-gate",
            goal="记账本应用",
            flow=DEV_QA_FLOW,
            min_gate_decisions=1,
            seed_files={"tests/test_sample.py": _PASSING_TEST},
            role_tool_scripts=_QA_TOOL_SCRIPT,
        ),
        EvalScenario(
            name="full-build",
            goal="商城应用",
            flow=str(_build_flow_path()),
            flow_is_path=True,
            expect_files=("DELIVERY.md",),
            min_gate_decisions=3,
            seed_files={"tests/test_sample.py": _PASSING_TEST},
            role_tool_scripts=_QA_TOOL_SCRIPT,
        ),
    ]


_PASSING_TEST = '''"""确定性回归种子测试（QA 岗位 run_tests 真实通过）。"""

def test_eval_sample():
    assert 1 + 1 == 2
'''

BUILTIN_SUITE: list[EvalScenario] = _suite()


async def _run_one(workspace: Path, scenario: EvalScenario) -> dict[str, Any]:
    if scenario.flow_is_path:
        flow = scenario.flow
    else:
        flow_file = workspace / "flow.yaml"
        flow_file.write_text(scenario.flow, encoding="utf-8")
        flow = str(flow_file)
    for rel, content in (scenario.seed_files or {}).items():
        target = workspace / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    driver = SessionDriver(
        workspace=workspace,
        goal=scenario.goal,
        flow=flow,
        model="deterministic",
        deterministic=True,
        yes=True,
        prompt_fn=lambda hint: "/skip",
        print_fn=lambda text: None,
        role_tool_scripts=scenario.role_tool_scripts,
    )
    result = await driver.run()
    stats: dict[str, Any] = {"tasks_done": 0, "tasks_terminal": 0, "qa_done": 0, "qa_total": 0}
    if result.state is not None:
        for task in result.state.tasks:
            if task.status in (TaskStatus.DONE, TaskStatus.REVIEW, TaskStatus.BLOCKED):
                stats["tasks_terminal"] += 1
            if task.status == TaskStatus.DONE:
                stats["tasks_done"] += 1
            if task.assignee_role == "qa":
                stats["qa_total"] += 1
                if task.status == TaskStatus.DONE:
                    stats["qa_done"] += 1
    missing = [rel for rel in scenario.expect_files if not (workspace / rel).is_file()]
    return {
        "name": scenario.name,
        "goal": scenario.goal,
        "exit_code": result.exit_code,
        "expected_exit_code": scenario.expect_exit_code,
        "gate_decisions": len(result.decisions or []),
        "min_gate_decisions": scenario.min_gate_decisions,
        "missing_files": missing,
        "passed": (
            result.exit_code == scenario.expect_exit_code
            and len(result.decisions or []) >= scenario.min_gate_decisions
            and not missing
        ),
        "tokens_used": int((result.token_summary or {}).get("used", 0) or 0),
        "tasks": stats,
    }


def run_scenario(workspace: Path, scenario: EvalScenario) -> dict[str, Any]:
    """同步运行单个场景（独立事件循环）。"""
    return asyncio.run(_run_one(workspace, scenario))


def run_suite(root: str | Path | None = None, suite: list[EvalScenario] | None = None) -> dict[str, Any]:
    """运行全部场景并汇总三项指标。"""
    scenarios = suite if suite is not None else BUILTIN_SUITE
    base = Path(root).expanduser().resolve() if root else Path(tempfile.mkdtemp(prefix="agent-cluster-eval-"))
    base.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, Any]] = []
    total_tokens = 0
    for index, scenario in enumerate(scenarios):
        workspace = base / f"ws-{index}"
        workspace.mkdir(parents=True, exist_ok=True)
        entry = run_scenario(workspace, scenario)
        results.append(entry)
        total_tokens += int(entry.get("tokens_used", 0) or 0)

    passed = sum(1 for entry in results if entry["passed"])
    completion_rate = passed / len(results) if results else 0.0
    done = sum(entry["tasks"]["tasks_done"] for entry in results)
    terminal = sum(entry["tasks"]["tasks_terminal"] for entry in results)
    tool_correctness = done / terminal if terminal else 1.0
    qa_done = sum(entry["tasks"]["qa_done"] for entry in results)
    qa_total = sum(entry["tasks"]["qa_total"] for entry in results)
    test_pass_rate = qa_done / qa_total if qa_total else 1.0

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "scenarios": results,
        "metrics": {
            "completion_rate": round(completion_rate, 4),
            "tool_correctness": round(tool_correctness, 4),
            "test_pass_rate": round(test_pass_rate, 4),
        },
        "total_tokens": total_tokens,
    }


def load_baseline(path: str | Path) -> dict[str, Any] | None:
    """读取基线 JSON；缺失/损坏返回 None。"""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        metrics = data.get("metrics")
        if not isinstance(metrics, dict):
            return None
        return {"metrics": {k: float(v) for k, v in metrics.items()}, "source": str(path)}
    except (OSError, json.JSONDecodeError, ValueError, UnicodeDecodeError):
        return None


def save_baseline(report: dict[str, Any], path: str | Path) -> None:
    """保存本次报告为基线。"""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(
        json.dumps(
            {"generated_at": report["generated_at"], "metrics": report["metrics"], "total_tokens": report["total_tokens"]},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    import os

    os.replace(tmp, target)


def compare_to_baseline(report: dict[str, Any], baseline: dict[str, Any], threshold: float = DEFAULT_REGRESSION_THRESHOLD) -> list[str]:
    """对比指标与基线，返回回归说明列表（空 = 无回归）。"""
    issues: list[str] = []
    current = report["metrics"]
    expected = baseline.get("metrics") or {}
    for name, value in current.items():
        if name not in expected:
            continue
        base_value = float(expected[name])
        if base_value <= 0:
            continue
        if value < base_value * (1.0 - threshold):
            issues.append(
                f"{name} 回归：当前 {value:.1%} < 基线 {base_value:.1%}（阈值 -{threshold:.0%}）"
            )
    return issues
