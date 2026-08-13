"""T13.6 门策略自动 reviewer：GatePolicyConfig 判定顺序 / 置信度升级 / deterministic 审计 / 兼容回归。

- 直接调用 ``SessionDriver._gate_response`` 验证决策分支（不跑真实图，无模型调用）；
- 唯一全流程用例验证 deterministic-accept 无人值守语义（§9.3）；
- ``gate_policy=None`` 走 v0.5 原路径（提示词 + 人工），既有 test_gates / test_t12_12 原样全绿为回归底线。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from agent_cluster.gates import AUTO_DENY_REASON
from agent_cluster.judge import JudgeVerdict, LLMJudge, parse_verdict
from agent_cluster.models import ActionRequest, GateKind, HumanResponse, TokenUsage
from agent_cluster.projects import GatePolicyConfig
from agent_cluster.runtime import ChatResponse, DeterministicClient
from agent_cluster.session import SessionDriver

GATE_FLOW = """name: t13.6-mini
thread_id: "t:13.6"
nodes:
  - {id: start, type: start}
  - {id: requirements, type: agent, role: pm}
  - {id: design_gate, type: gate, gate: design_review}
  - {id: end, type: end}
edges:
  - {from: start, to: requirements}
  - {from: requirements, to: design_gate}
  - {from: design_gate, to: end, on_accept: end, on_reject: requirements, on_edit: requirements, on_response: end}
"""


class StubJudge:
    """可注入假 judge：记录调用参数，返回指定 verdict/confidence。"""

    def __init__(self, verdict: JudgeVerdict | None = None) -> None:
        self.verdict = verdict or JudgeVerdict(verdict="pass", reason="OK", suggestions=[])
        self.on_usage = None
        self.calls: list[tuple] = []
        self.received_prompt: str | None = None

    def evaluate(self, kind: str, workspace, context: str = "", review_prompt: str | None = None) -> JudgeVerdict:
        self.calls.append((kind, str(workspace), context))
        if review_prompt is not None:
            self.received_prompt = review_prompt
        return self.verdict


class _CaptureClient(DeterministicClient):
    """捕获评审 system 提示词的假模型客户端。"""

    def __init__(self) -> None:
        self.systems: list[str] = []

    async def complete_with_tools(self, messages, tools):
        self.systems.append(messages[0]["content"])
        return ChatResponse(
            text=json.dumps({"verdict": "pass", "reason": "ok", "suggestions": []}, ensure_ascii=False),
            tool_calls=[],
            usage=TokenUsage(prompt_tokens=1, completion_tokens=1, total_tokens=2, model="fake", estimated=True),
        )


def _request(kind: GateKind, node: str = "design_gate") -> ActionRequest:
    return ActionRequest(
        id=node,
        kind=kind,
        title=f"{kind.value} 审批",
        description="等待审批",
        evidence={"node": node},
    )


def _driver(tmp_path, *, gate_policy=None, judge=None, yes=False, rework_limit=None, ws_name="ws"):
    """构造未跑图的 driver（直接测 _gate_response），并 spy on_event 捕获审计事件。"""
    ws = tmp_path / ws_name
    hints: list[str] = []
    captured: list = []
    driver = SessionDriver(
        workspace=ws,
        goal="待办应用",
        flow=str(tmp_path / "flow.yaml"),
        deterministic=False,
        yes=yes,
        judge=judge,
        gate_policy=gate_policy,
        rework_limit=rework_limit,
        prompt_fn=lambda hint: hints.append(hint) or "/skip",
        print_fn=lambda text: None,
    )
    driver.current_node = "design_gate"
    real_on_event = driver.on_event

    def spy(event):
        captured.append(event)
        real_on_event(event)

    driver.on_event = spy
    return driver, hints, captured


# ---------------------------------------------------------------------------
# parse_verdict：confidence 解析与 clamp
# ---------------------------------------------------------------------------


def test_judge_verdict_confidence_parse():
    assert parse_verdict(json.dumps({"verdict": "pass", "confidence": 0.3}, ensure_ascii=False)).confidence == 0.3
    assert parse_verdict(json.dumps({"verdict": "pass"}, ensure_ascii=False)).confidence == 1.0
    assert parse_verdict(json.dumps({"verdict": "pass", "confidence": 1.7}, ensure_ascii=False)).confidence == 1.0
    assert parse_verdict(json.dumps({"verdict": "pass", "confidence": -0.2}, ensure_ascii=False)).confidence == 0.0
    assert parse_verdict(json.dumps({"verdict": "pass", "confidence": "nope"}, ensure_ascii=False)).confidence == 1.0
    assert parse_verdict("评审输出无法解析").confidence == 1.0


# ---------------------------------------------------------------------------
# judge：review_prompt 覆盖（LLM 提示词透传 + driver 传参）
# ---------------------------------------------------------------------------


def test_judge_review_prompt_override_unit(tmp_path):
    client = _CaptureClient()
    judge = LLMJudge(client=client)
    verdict = judge.evaluate("design_review", tmp_path, context="", review_prompt="自定义评审提示词")
    assert verdict.verdict == "pass"
    assert client.systems == ["自定义评审提示词"]

    client2 = _CaptureClient()
    judge2 = LLMJudge(client=client2)
    judge2.evaluate("design_review", tmp_path)
    assert "资深评审工程师" in client2.systems[0]


def test_review_prompt_override(tmp_path):
    judge = StubJudge(JudgeVerdict(verdict="pass", reason="OK", suggestions=[], confidence=1.0))
    policy = GatePolicyConfig(review_prompt="自定义评审提示词")
    driver, hints, captured = _driver(tmp_path, gate_policy=policy, judge=judge)
    response = driver._gate_response(_request(GateKind.DESIGN_REVIEW))
    assert isinstance(response, HumanResponse) and response.type == "accept"
    assert judge.received_prompt == "自定义评审提示词"
    assert hints == [] and len(driver._auto_audit) == 1


# ---------------------------------------------------------------------------
# 判定顺序（§9）：白名单自动 / 黑名单恒人工 / 开关 / 置信度升级
# ---------------------------------------------------------------------------


def test_auto_accept(tmp_path):
    judge = StubJudge(JudgeVerdict(verdict="pass", reason="结构合理", suggestions=[], confidence=1.0))
    driver, hints, captured = _driver(tmp_path, gate_policy=GatePolicyConfig(), judge=judge)
    response = driver._gate_response(_request(GateKind.DESIGN_REVIEW))
    assert isinstance(response, HumanResponse)
    assert response.type == "accept"
    assert hints == [], "自动放行不应提示人工"
    record = driver.store.record.gate_decisions[0]
    assert record.last_decision == "accept"
    assert record.rejections == 0
    audit = driver._auto_audit[0]
    assert audit.by_role == "auto"
    assert audit.type == "accept"
    assert audit.args["source"] == "auto-review"
    decisions = [event for event in captured if event.type == "review.auto_decision"]
    assert len(decisions) == 1
    assert decisions[0].payload["decision"] == "accept"
    assert decisions[0].payload["kind"] == "design_review"


def test_auto_edit_routes_rework(tmp_path):
    judge = StubJudge(
        JudgeVerdict(verdict="revise", reason="缺验收标准", suggestions=["补充验收标准", "补用例"], confidence=0.9)
    )
    driver, hints, captured = _driver(tmp_path, gate_policy=GatePolicyConfig(), judge=judge)
    response = driver._gate_response(_request(GateKind.DESIGN_REVIEW))
    assert isinstance(response, HumanResponse)
    assert response.type == "edit"
    assert "补充验收标准" in response.args["text"]
    record = driver.store.record.gate_decisions[0]
    assert record.rejections == 1
    assert record.last_decision == "edit"
    assert driver._auto_audit[0].type == "edit"
    assert [e for e in captured if e.type == "review.auto_decision"][0].payload["decision"] == "edit"


def test_low_confidence_escalates(tmp_path):
    judge = StubJudge(JudgeVerdict(verdict="pass", reason="勉强", suggestions=[], confidence=0.5))
    driver, hints, captured = _driver(tmp_path, gate_policy=GatePolicyConfig(), judge=judge)
    response = driver._gate_response(_request(GateKind.DESIGN_REVIEW))
    assert isinstance(response, HumanResponse)
    assert response.type == "accept"  # prompt_fn 回答 /skip
    assert hints, "低置信度应升级人工（出现审批提示）"
    assert "[审批门]" in hints[0]
    assert driver._auto_audit == []
    assert captured == []
    assert len(judge.calls) == 2  # 自动路径 1 次 + v0.5 提示路径评审 1 次


def test_blacklist_always_human(tmp_path):
    judge = StubJudge(JudgeVerdict(verdict="pass", reason="OK", suggestions=[], confidence=1.0))
    driver, hints, captured = _driver(tmp_path, gate_policy=GatePolicyConfig(), judge=judge)
    driver.current_node = "requirement_gate"
    response = driver._gate_response(_request(GateKind.REQUIREMENT_CONFIRMATION, node="requirement_gate"))
    assert isinstance(response, HumanResponse)
    assert response.type == "accept"
    assert hints and "[审批门]" in hints[0], "黑名单门恒人工"
    assert driver._auto_audit == []
    assert captured == []


def test_auto_review_disabled(tmp_path):
    policy = GatePolicyConfig(auto_review=False)
    judge = StubJudge(JudgeVerdict(verdict="pass", reason="OK", suggestions=[], confidence=1.0))
    driver, hints, captured = _driver(tmp_path, gate_policy=policy, judge=judge)
    assert driver._gate_policy_active("design_review") is False
    response = driver._gate_response(_request(GateKind.DESIGN_REVIEW))
    assert isinstance(response, HumanResponse)
    assert response.type == "accept"
    assert hints and "[审批门]" in hints[0], "auto_review=False 应回到人工"
    assert driver._auto_audit == []
    assert captured == []


# ---------------------------------------------------------------------------
# 升级：返工阈值（接入既有 _escalation 路径）
# ---------------------------------------------------------------------------


def test_rework_escalation_threshold(tmp_path):
    judge = StubJudge(JudgeVerdict(verdict="revise", reason="再改", suggestions=["改"], confidence=0.9))
    policy = GatePolicyConfig(rework_escalation=2)
    driver, hints, captured = _driver(tmp_path, gate_policy=policy, judge=judge)
    first = driver._gate_response(_request(GateKind.DESIGN_REVIEW))
    second = driver._gate_response(_request(GateKind.DESIGN_REVIEW))
    assert first.type == "edit" and second.type == "edit"
    record = driver.store.record.gate_decisions[0]
    assert record.rejections == 2
    assert driver._escalation(_request(GateKind.DESIGN_REVIEW))[0] == "rework"

    judge2 = StubJudge(JudgeVerdict(verdict="revise", reason="再改", suggestions=["改"], confidence=0.9))
    driver2, _, _ = _driver(tmp_path, gate_policy=GatePolicyConfig(), judge=judge2, ws_name="ws2")
    driver2._gate_response(_request(GateKind.DESIGN_REVIEW))
    driver2._gate_response(_request(GateKind.DESIGN_REVIEW))
    assert driver2._escalation(_request(GateKind.DESIGN_REVIEW)) is None  # 默认阈值 3

    driver3, hints3, _ = _driver(tmp_path, gate_policy=None, rework_limit=2, ws_name="ws3")
    driver3.prompt_fn = lambda hint: hints3.append(hint) or "reject"
    driver3._gate_response(_request(GateKind.DESIGN_REVIEW))
    driver3._gate_response(_request(GateKind.DESIGN_REVIEW))
    record3 = driver3.store.record.gate_decisions[0]
    assert record3.rejections == 2
    assert driver3._escalation(_request(GateKind.DESIGN_REVIEW))[0] == "rework"  # v0.5 rework_limit 语义不变


# ---------------------------------------------------------------------------
# deterministic-accept 审计（全流程无人值守）+ --yes bypass-immune 回归
# ---------------------------------------------------------------------------


def test_deterministic_accept_audit(tmp_path):
    flow = tmp_path / "flow.yaml"
    flow.write_text(GATE_FLOW, encoding="utf-8")
    ws = tmp_path / "ws"
    captured: list = []
    judge = StubJudge()

    async def main():
        driver = SessionDriver(
            workspace=ws,
            goal="待办应用",
            flow=str(flow),
            deterministic=True,
            judge=judge,
            gate_policy=GatePolicyConfig(),
            prompt_fn=lambda hint: (_ for _ in ()).throw(AssertionError("deterministic-accept 不应提示人工")),
            print_fn=lambda text: None,
        )
        real = driver.on_event

        def spy(event):
            captured.append(event)
            real(event)

        driver.on_event = spy
        return await driver.run()

    result = asyncio.run(main())
    assert result.exit_code == 0
    assert judge.calls == [], "deterministic-accept 不应调用 LLM"
    auto = [record for record in result.decisions if getattr(record, "by_role", "") == "auto"]
    assert len(auto) == 1
    assert auto[0].type == "accept"
    assert auto[0].args["source"] == "deterministic-accept"
    assert auto[0].args["kind"] == "design_review"
    events = [event for event in captured if event.type == "review.auto_decision"]
    assert len(events) == 1
    assert events[0].payload["source"] == "deterministic-accept"


def test_yes_bypass_immune_regression(tmp_path):
    driver, hints, captured = _driver(tmp_path, gate_policy=GatePolicyConfig(), yes=True)
    request = _request(GateKind.DANGEROUS_TOOL, node="tool_gate")
    request.bypass_immune = True
    response = driver.decide_response(request)
    assert isinstance(response, HumanResponse)
    assert response.type == "reject"
    assert AUTO_DENY_REASON in str(response.args.get("reason") or "")
    assert driver._auto_audit == []


# ---------------------------------------------------------------------------
# gate_policy=None：v0.5 原路径逐字节一致（提示 + LLM 评审附注 + 人工）
# ---------------------------------------------------------------------------


def test_gate_policy_none_identical(tmp_path):
    judge = StubJudge(JudgeVerdict(verdict="revise", reason="缺验收", suggestions=["补"], confidence=0.9))
    driver, hints, captured = _driver(tmp_path, gate_policy=None, judge=judge)
    assert driver._gate_policy_active("design_review") is False
    response = driver._gate_response(_request(GateKind.DESIGN_REVIEW))
    assert isinstance(response, HumanResponse)
    assert response.type == "accept"  # /skip
    assert hints and "[审批门]" in hints[0]
    assert "[LLM 评审]" in hints[0], "v0.5 路径提示应附 LLM 评审"
    assert judge.received_prompt is None, "v0.5 路径不应传 review_prompt"
    assert len(judge.calls) == 1
    assert driver._auto_audit == []
    assert captured == []
