"""T12.2 记忆库：SQLite 四级晋升 + 提议制写入 + 检索 + 会话摘要。"""

from __future__ import annotations

import pytest

from agent_cluster.memory import MemoryItem, MemoryStore, MemoryStatus, Tier


@pytest.fixture()
def store(tmp_path):
    return MemoryStore(tmp_path)


def test_add_candidate_and_get(store):
    item_id = store.add_candidate(title="深拷贝陷阱", content="Python 深拷贝注意嵌套可变对象", source="qa")
    item = store.get(item_id)
    assert item is not None
    assert item.status == MemoryStatus.CANDIDATE
    assert item.tier == Tier.PROJECT.value
    assert item.content(store.root) == "Python 深拷贝注意嵌套可变对象"


def test_promote_requires_evidence(store):
    item_id = store.add_candidate(title="t", content="c")
    assert store.promote(item_id) is False  # 证据不足
    store.add_evidence(item_id, "session-1", "命中")
    assert store.promote(item_id) is False
    store.add_evidence(item_id, "session-2", "再次命中")
    assert store.promote(item_id) is True
    item = store.get(item_id)
    assert item.status == MemoryStatus.ACTIVE
    assert item.evidence_count == 2


def test_promote_with_human_confirm(store):
    item_id = store.add_candidate(title="t", content="c")
    assert store.promote(item_id, human_confirm=True) is True


def test_promote_changes_tier_and_moves_file(store):
    item_id = store.add_candidate(title="跨项目坑", content="内容", tier=Tier.PROJECT)
    store.add_evidence(item_id, "s1")
    store.add_evidence(item_id, "s2")
    assert store.promote(item_id, target_tier=Tier.GOTCHA) is True
    item = store.get(item_id)
    assert item.tier == Tier.GOTCHA.value
    assert "/gotcha/" in item.content_ref
    assert item.content(store.root) == "内容"


def test_evidence_same_session_counts_once(store):
    item_id = store.add_candidate(title="t", content="c")
    store.add_evidence(item_id, "s1")
    store.add_evidence(item_id, "s1")
    assert store.get(item_id).evidence_count == 1


def test_search_local_first_order(store):
    gotcha_id = store.add_candidate(title="Redis 大 key", content="大 key 会阻塞", tier=Tier.GOTCHA)
    domain_id = store.add_candidate(title="Redis 缓存设计", content="大 key 与过期策略", tier=Tier.DOMAIN)
    store.add_evidence(gotcha_id, "s1")
    store.add_evidence(gotcha_id, "s2")
    store.promote(gotcha_id)
    results = store.search("大 key")
    assert len(results) >= 1
    assert results[0].id == gotcha_id  # gotcha 层级优先于 domain
    assert domain_id in [item.id for item in results]


def test_archived_excluded_from_search(store):
    item_id = store.add_candidate(title="噪声", content="无关内容 abcxyz")
    store.archive(item_id)
    assert store.search("abcxyz") == []


def test_proposal_flow(store):
    item_id = store.add_candidate(title="t", content="c", tier=Tier.PROJECT)
    proposal_id = store.create_proposal(item_id, Tier.GOTCHA, "跨项目复用价值高")
    proposals = store.list_proposals()
    assert len(proposals) == 1
    assert proposals[0].status == "open"
    assert store.resolve_proposal(proposal_id, approved=True) is True
    assert store.get(item_id).tier == Tier.GOTCHA.value
    assert store.list_proposals(status="approved")[0].status == "approved"


def test_session_summary_roundtrip(store):
    store.save_session_summary(session_id="th-1", title="会话摘要", content="决策：用 SQLite")
    assert "SQLite" in store.session_summary("th-1")
    store.save_session_summary(session_id="th-1", title="会话摘要", content="决策：改用向量库")
    assert "向量库" in store.session_summary("th-1")


def test_persistence_across_instances(tmp_path):
    store1 = MemoryStore(tmp_path)
    item_id = store1.add_candidate(title="t", content="c")
    store2 = MemoryStore(tmp_path)
    assert store2.get(item_id) is not None
    assert store2.get(item_id).content(store2.root) == "c"
