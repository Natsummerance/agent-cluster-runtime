"""Task 5 行为测试：12 岗位目录、RoleKind 映射与 RoleRegistry 查询。"""

from __future__ import annotations

import pytest

from agent_cluster.models import GateKind, MeetingKind, Role, RoleKind
from agent_cluster.roles import RoleRegistry, build_role_catalog

EXPECTED_ROLE_IDS = [
    "pm",
    "pmo",
    "frontend",
    "backend",
    "algorithm",
    "architect",
    "qa",
    "devops",
    "docs",
    "reviewer",
    "debugger",
    "governance",
]


def test_catalog_has_12_roles_with_expected_ids():
    catalog = build_role_catalog()
    assert len(catalog) == 12
    assert set(catalog) == set(EXPECTED_ROLE_IDS)
    assert all(isinstance(role, Role) for role in catalog.values())


def test_every_role_has_required_fields():
    catalog = build_role_catalog()
    for role in catalog.values():
        assert role.id, f"{role.id} 缺少 id"
        assert role.name, f"{role.id} 缺少 name"
        assert isinstance(role.kind, RoleKind), f"{role.id} 的 kind 非法"
        assert role.goal, f"{role.id} 缺少 goal"
        assert role.backstory, f"{role.id} 缺少 backstory"
        assert isinstance(role.skills, list) and role.skills, f"{role.id} 缺少 skills"
        assert all(isinstance(item, str) and "@" in item for item in role.skills), f"{role.id} skills 应为 name@version"
        assert isinstance(role.tools, list) and role.tools, f"{role.id} 缺少 tools"
        assert isinstance(role.approval_scope, list), f"{role.id} 缺少 approval_scope"
        assert all(isinstance(gate, GateKind) for gate in role.approval_scope)


def test_architect_maps_to_role_kind_arch():
    role = build_role_catalog()["architect"]
    assert role.kind == RoleKind.ARCH


def test_role_kind_mapping_for_auxiliary_roles():
    """辅助/门禁四岗的 RoleKind 归类契约（文档化映射）。"""
    catalog = build_role_catalog()
    assert catalog["docs"].kind == RoleKind.PMO
    assert catalog["reviewer"].kind == RoleKind.QA
    assert catalog["debugger"].kind == RoleKind.QA
    assert catalog["governance"].kind == RoleKind.PM


def test_approval_scope_contract():
    catalog = build_role_catalog()
    assert GateKind.REQUIREMENT_CONFIRMATION in catalog["pm"].approval_scope
    assert GateKind.DESIGN_REVIEW in catalog["architect"].approval_scope
    assert GateKind.ITERATION_ACCEPTANCE in catalog["qa"].approval_scope
    assert GateKind.ITERATION_ACCEPTANCE in catalog["pm"].approval_scope
    assert GateKind.RELEASE in catalog["devops"].approval_scope
    assert GateKind.RELEASE in catalog["pm"].approval_scope
    assert GateKind.EVOLUTION_APPLY in catalog["governance"].approval_scope


def test_algorithm_role_approval_scope_consistent_with_backstory():
    role = build_role_catalog()["algorithm"]
    assert role.approval_scope == []
    # backstory 不再声称算法可批准（审批范围为空，经设计评审门把关）
    assert "可批准" not in role.backstory
    assert "设计评审门" in role.backstory


def test_registry_get_and_list():
    registry = RoleRegistry()
    role = registry.get("architect")
    assert role.id == "architect"
    listed = registry.list()
    assert len(listed) == 12
    assert [item.id for item in listed] == sorted(EXPECTED_ROLE_IDS)


def test_registry_get_missing_raises_key_error():
    with pytest.raises(KeyError, match="not-a-role"):
        RoleRegistry().get("not-a-role")


def test_registry_filter_by_kind():
    registry = RoleRegistry()
    qa_roles = registry.filter_by_kind(RoleKind.QA)
    assert {role.id for role in qa_roles} == {"qa", "reviewer", "debugger"}
    arch_roles = registry.filter_by_kind(RoleKind.ARCH)
    assert [role.id for role in arch_roles] == ["architect"]


def test_registry_default_role_ids_for_meetings():
    registry = RoleRegistry()
    kickoff = registry.default_role_ids(MeetingKind.KICKOFF)
    assert "pm" in kickoff and "architect" in kickoff
    code_review = registry.default_role_ids("code_review")
    assert code_review == ["frontend", "backend", "reviewer"]
    assert all(role_id in EXPECTED_ROLE_IDS for role_id in kickoff)
