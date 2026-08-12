"""T12.1 Token 制升级：自适应预算 / 价格表金额折算 / 前缀缓存判定。"""

from __future__ import annotations

import json

import pytest

from agent_cluster.budget import (
    BUDGET_MAX,
    BUDGET_MIN,
    CACHE_MIN_TOKENS,
    BudgetProposal,
    cache_eligible_tokens,
    estimate_budget,
)
from agent_cluster.models import TokenUsage
from agent_cluster.pricing import (
    CostLedger,
    PRICE_TABLE,
    cost_for,
    format_cost,
    load_price_overrides,
    resolve_price,
)


@pytest.fixture(autouse=True)
def _force_heuristic(monkeypatch):
    import agent_cluster.tokens as tokens_mod

    monkeypatch.setattr(tokens_mod, "_TIKTOKEN_ENCODING", False)
    yield


# ---------------------------------------------------------------------------
# 自适应预算
# ---------------------------------------------------------------------------


def test_estimate_budget_basic_bounds():
    proposal = estimate_budget("做一个待办事项应用", "")
    assert isinstance(proposal, BudgetProposal)
    assert BUDGET_MIN <= proposal.budget <= BUDGET_MAX
    assert proposal.budget == proposal.calibrated
    assert 0.0 <= proposal.confidence <= 1.0


def test_estimate_budget_grows_with_complexity():
    simple = estimate_budget("简单记事本", "").budget
    complex_goal = "高并发微服务，含认证支付，重构迁移到 docker 与消息队列，多租户实时大模型算法"
    complex_budget = estimate_budget(complex_goal, "").budget
    assert complex_budget >= simple


def test_estimate_budget_plan_lines_count():
    plan = "1. 需求\n2. 设计\n3. 开发\n4. 测试\n5. 部署"
    with_plan = estimate_budget("目标", plan).budget
    without_plan = estimate_budget("目标", "").budget
    assert with_plan > without_plan


def test_estimate_budget_history_calibration():
    # 历史实际消耗是预估的 2 倍 → 校准后预算翻倍（夹在上下限内）
    proposal = estimate_budget("中等复杂度项目", "", history=[(100_000, 200_000)])
    assert proposal.confidence > 0.5
    assert proposal.calibrated >= proposal.base_estimate


def test_estimate_budget_history_underestimate_correction():
    proposal = estimate_budget("小项目", "", history=[(100_000, 50_000)])
    assert proposal.calibrated <= proposal.base_estimate


def test_cache_eligible_tokens_threshold():
    short = cache_eligible_tokens("short prefix")
    assert short == 0
    long_prefix = "系统提示与角色画像" * 300  # 远超 1024 字符
    tokens = cache_eligible_tokens(long_prefix)
    assert tokens >= CACHE_MIN_TOKENS


# ---------------------------------------------------------------------------
# 价格表与金额折算
# ---------------------------------------------------------------------------


def test_resolve_price_exact_and_alias():
    assert resolve_price("deepseek-chat") is not None
    assert resolve_price("deepseek-v3").input_per_m == PRICE_TABLE["deepseek-chat"].input_per_m
    assert resolve_price("gpt-5.1").input_per_m == PRICE_TABLE["gpt-5"].input_per_m


def test_resolve_price_prefix_and_unknown():
    assert resolve_price("deepseek-chat-v4-flash") is not None
    assert resolve_price("claude-opus-5") is not None
    assert resolve_price("unknown-model-xyz") is None


def test_cost_for_known_model():
    usage = TokenUsage(
        prompt_tokens=1_000_000,
        completion_tokens=1_000_000,
        total_tokens=2_000_000,
        model="deepseek-chat",
    )
    cost = cost_for(usage)
    assert cost is not None
    assert cost == pytest.approx(0.27 + 1.10)


def test_cost_for_cache_read_cheaper():
    usage = TokenUsage(
        prompt_tokens=1_000_000,
        completion_tokens=0,
        total_tokens=1_000_000,
        model="deepseek-chat",
        cache_read_tokens=1_000_000,
    )
    cost = cost_for(usage)
    assert cost is not None
    assert cost == pytest.approx(0.07)


def test_cost_for_unknown_model_none():
    assert cost_for(TokenUsage(total_tokens=100, model="nope")) is None


def test_load_price_overrides_json(tmp_path):
    path = tmp_path / "pricing.json"
    path.write_text(
        json.dumps({"my-model": {"input_per_m": 1.0, "output_per_m": 2.0}}), encoding="utf-8"
    )
    overrides = load_price_overrides(path)
    assert overrides["my-model"].input_per_m == 1.0
    assert resolve_price("my-model", overrides).input_per_m == 1.0


def test_load_price_overrides_missing_file_returns_empty():
    assert load_price_overrides("no-such-file.json") == {}


def test_format_cost():
    assert format_cost(None) == "-"
    assert format_cost(0.0005).startswith("0.0005")


def test_cost_ledger_aggregates_by_model():
    ledger = CostLedger()
    ledger.record(TokenUsage(prompt_tokens=1_000_000, completion_tokens=0, total_tokens=1_000_000, model="deepseek-chat"))
    ledger.record(TokenUsage(prompt_tokens=1_000_000, completion_tokens=0, total_tokens=1_000_000, model="deepseek-chat"))
    assert ledger.entries["deepseek-chat"] == pytest.approx(0.54)
    assert ledger.total == pytest.approx(0.54)
    assert ledger.summary()["currency"] == "USD"
