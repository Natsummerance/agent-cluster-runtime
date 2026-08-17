from __future__ import annotations

from collections import Counter
from copy import deepcopy
from typing import Any

import pytest

from doai_organization.catalog import MEETINGS, ROLES
from doai_organization.projector import OrganizationProjector
from doai_organization.service import OrganizationService, RpcFault
from doai_protocol import MutationMeta, RpcRequest


class FakeHost:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []
        self.idempotency: dict[str, Any] = {}
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def call(
        self,
        method: str,
        params: dict[str, Any],
        mutation: dict[str, Any] | None = None,
    ) -> Any:
        self.calls.append((method, deepcopy(params)))
        if mutation is not None and mutation["idempotency_key"] in self.idempotency:
            return self.idempotency[mutation["idempotency_key"]]
        if method == "session.idempotency.get":
            return self.idempotency.get(params["idempotency_key"])
        if method == "session.append":
            assert mutation is not None
            key = mutation["idempotency_key"]
            if key in self.idempotency:
                return self.idempotency[key]
            assert mutation["session_revision"] == len(self.events)
            event = {
                "schema_version": "1.0",
                "session_id": params["session_id"],
                "seq": len(self.events) + 1,
                "type": params["type"],
                "ts": "2026-08-17T00:00:00Z",
                "scope": params["scope"],
                "payload": params.get("payload", {}),
                "ignorable": False,
            }
            self.events.append(event)
            result = {"event": event}
            self.idempotency[key] = result
            return result
        if method == "agent.invoke":
            result = {"content": f"{params['role_id']} completed"}
            if mutation is not None:
                self.idempotency[mutation["idempotency_key"]] = result
            return result
        if method == "approval.request":
            result = {"approved": True, "reason": "test approval"}
            if mutation is not None:
                self.idempotency[mutation["idempotency_key"]] = result
            return result
        raise AssertionError(f"unexpected host call: {method}")


def mutation(key: str = "run-1") -> MutationMeta:
    return MutationMeta(request_id="request-1", idempotency_key=key, session_revision=0)


class SimulatedProcessCrash(BaseException):
    pass


class CrashOnceHost(FakeHost):
    def __init__(self, crash_on_call: int, *, after_commit: bool) -> None:
        super().__init__()
        self.crash_on_call = crash_on_call
        self.after_commit = after_commit
        self.call_count = 0
        self.crashed = False

    async def call(
        self,
        method: str,
        params: dict[str, Any],
        mutation: dict[str, Any] | None = None,
    ) -> Any:
        self.call_count += 1
        should_crash = self.call_count == self.crash_on_call and not self.crashed
        if should_crash and not self.after_commit:
            self.crashed = True
            raise SimulatedProcessCrash()
        result = await super().call(method, params, mutation)
        if should_crash:
            self.crashed = True
            raise SimulatedProcessCrash()
        return result


def test_catalog_preserves_twelve_roles_and_seven_meetings() -> None:
    assert len(ROLES) == 12
    assert len(MEETINGS) == 7
    assert {role.id for role in ROLES} == {
        "pm", "pmo", "frontend", "backend", "algorithm", "architect",
        "qa", "devops", "docs", "reviewer", "debugger", "governance",
    }
    assert all(meeting.participants for meeting in MEETINGS)
    assert {role for meeting in MEETINGS for role in meeting.participants} == {role.id for role in ROLES}


@pytest.mark.asyncio
async def test_full_organization_run_is_durable_and_idempotent() -> None:
    host = FakeHost()
    service = OrganizationService(host)
    request = RpcRequest(
        jsonrpc="2.0",
        id="rpc-1",
        method="organization.run",
        params={
            "session_id": "session-1",
            "scope": {"tenant_id": "tenant", "project_id": "project"},
            "requirement": "Ship a reviewed feature",
            "budget": 100_000,
        },
        mutation=mutation(),
    )

    first = await service.dispatch(request)
    before = deepcopy(host.events)
    second = await service.dispatch(request)

    assert first == second
    assert host.events == before
    counts = Counter(event["type"] for event in host.events)
    assert counts["meeting.started"] == 7
    assert counts["meeting.completed"] == 7
    assert counts["task.created"] == 12
    assert counts["approval.resolved"] == 4
    assert counts["organization.completed"] == 1
    assert counts["evolution.proposed"] == 1
    invoked_roles = {
        params["role_id"] for method, params in host.calls if method == "agent.invoke"
    }
    assert invoked_roles == {role.id for role in ROLES}


@pytest.mark.asyncio
async def test_projection_rebuilds_organization_state_from_events_only() -> None:
    host = FakeHost()
    service = OrganizationService(host)
    await service.dispatch(RpcRequest(
        jsonrpc="2.0",
        id="rpc-2",
        method="organization.run",
        params={
            "session_id": "session-2",
            "scope": {"tenant_id": "tenant", "project_id": "project"},
            "requirement": "Build",
            "budget": 50_000,
        },
        mutation=mutation("run-2"),
    ))

    state = OrganizationProjector().project(host.events)

    assert state.status == "completed"
    assert len(state.meetings) == 7
    assert len(state.tasks) == 12
    assert set(state.tasks.values()) == {"done"}
    assert len(state.approvals) == 4
    assert state.budget_reserved == 50_000
    assert state.evolution_proposals == 1
    assert state.memory_items == 1
    assert OrganizationProjector().project(host.events) == state


@pytest.mark.asyncio
async def test_mutation_and_protocol_mismatch_fail_structurally() -> None:
    service = OrganizationService(FakeHost())
    with pytest.raises(RpcFault) as missing:
        await service.dispatch(RpcRequest(
            jsonrpc="2.0", id="x", method="organization.run",
            params={"session_id": "s"},
        ))
    assert missing.value.code == "MUTATION_META_REQUIRED"

    with pytest.raises(RpcFault) as version:
        await service.dispatch(RpcRequest(
            jsonrpc="2.0", id="hello", method="protocol.hello",
            params={"protocol_version": "9.0", "event_schema_version": "1.0", "capabilities": []},
        ))
    assert version.value.code == "PROTOCOL_VERSION_UNSUPPORTED"


@pytest.mark.asyncio
async def test_cancel_is_observed_at_the_next_graph_boundary() -> None:
    host = FakeHost()
    service = OrganizationService(host)
    await service.dispatch(RpcRequest(
        jsonrpc="2.0", id="cancel", method="organization.cancel",
        params={"session_id": "cancelled-session"},
    ))

    with pytest.raises(RpcFault) as cancelled:
        await service.dispatch(RpcRequest(
            jsonrpc="2.0", id="run", method="organization.run",
            params={
                "session_id": "cancelled-session",
                "scope": {"tenant_id": "tenant", "project_id": "project"},
                "requirement": "cancel me",
                "budget": 1_000,
            },
            mutation=mutation("cancel-run"),
        ))

    assert cancelled.value.code == "RUN_CANCELLED"
    assert host.events[-1]["payload"]["status"] == "cancelled"
    assert not any(event["type"] == "meeting.completed" for event in host.events)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("crash_on_call", "after_commit"),
    [(1, False), (20, False), (25, True)],
    ids=["before-request", "during-execution", "after-commit"],
)
async def test_crash_replay_does_not_duplicate_domain_actions(
    crash_on_call: int,
    after_commit: bool,
) -> None:
    host = CrashOnceHost(crash_on_call, after_commit=after_commit)
    request = RpcRequest(
        jsonrpc="2.0", id="crash",
        method="organization.run",
        params={
            "session_id": "crash-session",
            "scope": {"tenant_id": "tenant", "project_id": "project"},
            "requirement": "Recover exactly once",
            "budget": 42_000,
        },
        mutation=mutation("crash-run"),
    )
    with pytest.raises(SimulatedProcessCrash):
        await OrganizationService(host).dispatch(request)

    result = await OrganizationService(host).dispatch(request)

    assert result["event"]["type"] == "organization.completed"
    counts = Counter(event["type"] for event in host.events)
    assert counts["organization.started"] == 1
    assert counts["meeting.completed"] == 7
    assert counts["task.created"] == 12
    assert counts["organization.completed"] == 1
    assert [event["seq"] for event in host.events] == list(range(1, len(host.events) + 1))
