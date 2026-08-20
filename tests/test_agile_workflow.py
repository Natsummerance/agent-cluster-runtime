"""测试轻量敏捷开发工作流（workflows/agile-dev.yaml）。"""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_cluster.cli import run_flow
from agent_cluster.workflow import WorkflowEngine, WorkflowSpec


import yaml

def test_agile_dev_workflow_compiles():
    flow_file = Path("workflows/agile-dev.yaml")
    assert flow_file.is_file()
    data = yaml.safe_load(flow_file.read_text(encoding="utf-8"))
    spec = WorkflowSpec.model_validate(data)
    assert spec.name == "agile-dev"
    assert len(spec.nodes) == 6
    engine = WorkflowEngine(handlers={"agent": lambda s, n, c: {}, "gate": lambda s, n, c: {}})
    compiled = engine.compile(flow_file.read_text(encoding="utf-8"))
    assert compiled.spec.name == "agile-dev"


@pytest.mark.anyio()
async def test_agile_dev_workflow_runs_yes_mode(tmp_path: Path):
    flow_file = Path("workflows/agile-dev.yaml")
    summary = await run_flow(
        flow_path=str(flow_file),
        yes=True,
        workspace=str(tmp_path),
    )
    assert summary.thread_id == "agile-dev-thread"
    assert summary.state is not None
    # 验证节点事件中包含 start, pm_plan, dev_build, qa_verify, acceptance_gate, end
    event_types = [e.type for e in summary.events]
    assert "workflow_start" in event_types
    assert "workflow_end" in event_types
