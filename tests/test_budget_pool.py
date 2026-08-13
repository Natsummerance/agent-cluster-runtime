"""T13.3 项目预算池：hook 挂接时序 / 预警滞回 / 硬上限与解锁 / 自服务 vs 审批 / 聚合归因 / 判定顺序。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_cluster.models import TokenUsage
from agent_cluster.projects import ProjectStore, make_budget_pool_hook
from agent_cluster.session import SessionDriver, SessionRecord, TokenLedgerEntry


def _make_store_and_project(tmp_path: Path) -> tuple[ProjectStore, str]:
    ws = tmp_path / "workspace"
    ws.mkdir()
    store = ProjectStore(tmp_path / "root")
    project = store.create_project(name="proj", workspace=ws)
    return store, project.project_id


# ---------------------------------------------------------------------------
# hook 挂接：先 save 后 hook；hook 抛异常不阻断记账
# ---------------------------------------------------------------------------


def test_hook_fires_after_save(tmp_path):
    store, pid = _make_store_and_project(tmp_path)
    calls: list[tuple[int, int]] = []

    driver: SessionDriver | None = None

    def hook(record):
        assert driver is not None
        persisted = SessionRecord.model_validate(
            json.loads(driver.store.path.read_text(encoding="utf-8"))
        )
        calls.append((record.token_ledger.total(), persisted.token_ledger.total()))

    driver = SessionDriver(
        workspace=tmp_path / "workspace",
        goal="g",
        flow="examples/flows/build-product.yaml",
        deterministic=True,
        budget_pool_hook=hook,
    )
    driver.usage_hook("backend", TokenUsage(total_tokens=123))
    # 记账先落盘（persisted 已含 123）、hook 随后收到同一 record
    assert calls == [(123, 123)]


def test_hook_exception_does_not_break_usage(tmp_path):
    def broken(record):
        raise RuntimeError("boom")

    driver = SessionDriver(
        workspace=tmp_path / "workspace",
        goal="g",
        flow="examples/flows/build-product.yaml",
        deterministic=True,
        budget_pool_hook=broken,
    )
    driver.usage_hook("backend", TokenUsage(total_tokens=1))
    assert driver.store.record.token_ledger.total() == 1


# ---------------------------------------------------------------------------
# 预警滞回（§5.2-2）
# ---------------------------------------------------------------------------


def test_warn_hysteresis(tmp_path):
    store, pid = _make_store_and_project(tmp_path)
    store.update(
        pid,
        budget_pool={"hard_limit_tokens": 1000, "warn_ratio": 0.8, "warn_reenable_ratio": 0.7},
    )
    events: list[str] = []
    hook = make_budget_pool_hook(store, lambda name, payload: events.append(name))
    session_store = store.session_store(pid, "s1")
    session_store.update(project_id=pid)
    record = session_store.record

    def charge(tokens: int) -> None:
        record.token_ledger.record(usage=TokenUsage(total_tokens=tokens))
        session_store.save()
        hook(record)

    charge(800)  # 800 >= 1000×0.8：首次预警
    assert events == ["budget.warning"]
    assert store.get(pid).budget_pool.warn_raised is True
    charge(100)  # 900：已触发，不重复
    assert events == ["budget.warning"]
    # 回落至 699 < 1000×0.7：复位
    record.token_ledger.entries = [TokenLedgerEntry(total_tokens=699)]
    session_store.save()
    hook(record)
    assert events == ["budget.warning", "budget.warn_reset"]
    assert store.get(pid).budget_pool.warn_raised is False
    charge(101)  # 800：二次预警
    assert events == ["budget.warning", "budget.warn_reset", "budget.warning"]


# ---------------------------------------------------------------------------
# 硬上限 + 解锁恢复（§5.2-3 / §5.3）
# ---------------------------------------------------------------------------


def test_exhausted_and_unlock(tmp_path):
    store, pid = _make_store_and_project(tmp_path)
    store.update(pid, budget_pool={"hard_limit_tokens": 1000})
    events: list[str] = []
    hook = make_budget_pool_hook(store, lambda name, payload: events.append(name))
    session_store = store.session_store(pid, "s1")
    session_store.update(project_id=pid)
    record = session_store.record
    record.token_ledger.record(usage=TokenUsage(total_tokens=1001))
    session_store.save()
    hook(record)
    assert events == ["budget.warning", "budget.exhausted"]
    assert store.is_budget_exhausted(pid) is True

    unlock_events: list[str] = []
    unlock = store.unlock_budget(
        pid,
        additional_tokens=1000,
        reason="超限豁免",
        emit=lambda name, payload: unlock_events.append(name),
    )
    assert unlock.status == "granted"
    assert unlock_events == ["budget.unlocked"]
    assert store.is_budget_exhausted(pid) is False
    status = store.budget_status(pid)
    assert status["hard_limit_tokens"] == 2000
    assert status["used"] == 1001
    assert status["remaining"] == 999
    assert status["warn_raised"] is True
    assert len(status["unlocks"]) == 1


# ---------------------------------------------------------------------------
# 自服务 vs 例外审批（§5.3）
# ---------------------------------------------------------------------------


def test_self_service_vs_approval(tmp_path):
    store, pid = _make_store_and_project(tmp_path)

    granted = store.unlock_budget(pid, additional_tokens=500, reason="自服务")
    assert granted.status == "granted"
    assert granted.decided_by == "self"
    assert granted.decided_at is None
    assert store.get(pid).budget_pool.hard_limit_tokens == 500

    store.update(pid, budget_pool={"unlock_requires_approval": True})
    pending = store.unlock_budget(pid, additional_tokens=300, reason="需要审批")
    assert pending.status == "pending"
    assert pending.decided_by == ""
    assert pending.decided_at is None
    assert store.get(pid).budget_pool.hard_limit_tokens == 500  # 不提额

    approved = store.decide_unlock(pid, pending.id, approved=True, decided_by="alice")
    assert approved.status == "granted"
    assert approved.decided_by == "alice"
    assert approved.decided_at is not None
    assert store.get(pid).budget_pool.hard_limit_tokens == 800

    second = store.unlock_budget(pid, additional_tokens=100, reason="再申请")
    assert second.status == "pending"
    denied = store.decide_unlock(pid, second.id, approved=False, decided_by="bob")
    assert denied.status == "denied"
    assert denied.decided_by == "bob"
    assert store.get(pid).budget_pool.hard_limit_tokens == 800

    with pytest.raises(ValueError):
        store.decide_unlock(pid, approved.id, approved=False, decided_by="carol")


# ---------------------------------------------------------------------------
# 聚合归因（§5.1）与判定顺序（§5.2）
# ---------------------------------------------------------------------------


def test_aggregate_equals_sum_and_judgement_uses_aggregate(tmp_path):
    store, pid = _make_store_and_project(tmp_path)
    store.update(pid, budget_pool={"hard_limit_tokens": 1000})

    s1 = store.session_store(pid, "s1")
    s1.update(project_id=pid)
    s1.record.token_ledger.record(usage=TokenUsage(total_tokens=600))
    s1.save()
    s2 = store.session_store(pid, "s2")
    s2.update(project_id=pid)
    s2.record.token_ledger.record(usage=TokenUsage(total_tokens=300))
    s2.save()

    assert store.aggregate_used_tokens(pid) == 900
    status = store.budget_status(pid)
    assert status["used"] == 900

    events: list[str] = []
    hook = make_budget_pool_hook(store, lambda name, payload: events.append(name))
    hook(s1.record)
    # 两个会话各自低于阈值，但项目判定只认聚合值：900 >= 800
    assert events == ["budget.warning"]
    # 会话账本不被 hook 改动（不双计）
    assert s1.record.token_ledger.total() == 600
    assert s2.record.token_ledger.total() == 300


def test_judgement_order_session_level_unchanged(tmp_path):
    store, pid = _make_store_and_project(tmp_path)
    store.update(pid, budget_pool={"hard_limit_tokens": 1000})
    events: list[str] = []
    hook = make_budget_pool_hook(store, lambda name, payload: events.append(name))

    s = store.session_store(pid, "s1")
    s.update(project_id=pid)
    s.record.token_ledger.budget = 500
    s.record.token_ledger.record(usage=TokenUsage(total_tokens=600))
    s.save()
    assert s.record.token_ledger.over_budget() is True  # v0.5 会话级判定不变
    hook(s.record)
    assert "budget.exhausted" not in events  # 项目判定只认聚合值（600 < 1000）
    assert s.record.token_ledger.total() == 600


# ---------------------------------------------------------------------------
# hard_limit_tokens=0：一律不触发（§5.2）
# ---------------------------------------------------------------------------


def test_hard_limit_zero_inert(tmp_path):
    store, pid = _make_store_and_project(tmp_path)
    events: list[str] = []
    hook = make_budget_pool_hook(store, lambda name, payload: events.append(name))

    s = store.session_store(pid, "s1")
    s.update(project_id=pid)
    s.record.token_ledger.record(usage=TokenUsage(total_tokens=10000))
    s.save()
    hook(s.record)

    assert events == []
    assert store.is_budget_exhausted(pid) is False
    assert store.get(pid).budget_pool.warn_raised is False
    assert store.budget_status(pid)["remaining"] is None
