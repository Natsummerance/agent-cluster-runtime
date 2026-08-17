"""Organization-plane JSON-RPC service with Host-only side effects."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from doai_protocol import MutationMeta, RpcRequest

from .catalog import MEETINGS, ROLES
from .projector import OrganizationProjector
from .workflow import run_meeting_workflow


class HostPort(Protocol):
    async def call(
        self,
        method: str,
        params: dict[str, Any],
        mutation: dict[str, Any] | None = None,
    ) -> Any: ...


@dataclass
class RpcFault(Exception):
    code: str
    message: str
    retryable: bool = False
    details: dict[str, Any] | None = None

    def __str__(self) -> str:
        return self.message


class OrganizationService:
    def __init__(self, host: HostPort) -> None:
        self.host = host
        self._cancelled: set[str] = set()

    async def dispatch(self, request: RpcRequest) -> Any:
        if request.method == "protocol.hello":
            return self._hello(dict(request.params))
        if request.method == "health":
            return {"status": "ok", "protocol_version": "1.0"}
        if request.method == "organization.project":
            return OrganizationProjector().project(request.params.get("events", [])).__dict__
        if request.method == "organization.cancel":
            self._cancelled.add(str(request.params["session_id"]))
            return {"cancelled": True}
        if request.method == "organization.run":
            if request.mutation is None:
                raise RpcFault("MUTATION_META_REQUIRED", "organization.run requires mutation metadata")
            return await self._run(dict(request.params), request.mutation)
        raise RpcFault("METHOD_NOT_FOUND", f"unknown organization method: {request.method}")

    @staticmethod
    def _hello(params: dict[str, Any]) -> dict[str, Any]:
        if params.get("protocol_version") != "1.0":
            raise RpcFault(
                "PROTOCOL_VERSION_UNSUPPORTED",
                f"unsupported protocol version: {params.get('protocol_version')}",
                details={"supported": ["1.0"]},
            )
        if params.get("event_schema_version") != "1.0":
            raise RpcFault(
                "EVENT_SCHEMA_VERSION_UNSUPPORTED",
                f"unsupported event schema version: {params.get('event_schema_version')}",
                details={"supported": ["1.0"]},
            )
        return {
            "protocol_version": "1.0",
            "event_schema_version": "1.0",
            "capabilities": ["organization.run", "organization.project", "organization.cancel", "health"],
        }

    async def _run(self, params: dict[str, Any], mutation: MutationMeta) -> Any:
        session_id = str(params["session_id"])
        scope = dict(params["scope"])
        requirement = str(params["requirement"])
        budget = int(params.get("budget", 500_000))
        if budget <= 0:
            raise RpcFault("BUDGET_INVALID", "budget must be greater than zero")
        existing = await self.host.call(
            "session.idempotency.get",
            {"session_id": session_id, "idempotency_key": mutation.idempotency_key},
        )
        if existing is not None:
            return existing

        revision = mutation.session_revision
        event_index = 0

        def child_mutation(key: str) -> dict[str, Any]:
            return {
                "request_id": mutation.request_id,
                "idempotency_key": key,
                "session_revision": revision,
            }

        async def append(type_: str, payload: dict[str, Any], *, final: bool = False) -> Any:
            nonlocal revision, event_index
            key = mutation.idempotency_key if final else f"{mutation.idempotency_key}:event:{event_index}"
            event_index += 1
            result = await self.host.call(
                "session.append",
                {"session_id": session_id, "scope": scope, "type": type_, "payload": payload, "ignorable": False},
                child_mutation(key),
            )
            revision = int(result["event"]["seq"])
            return result

        async def transition(status: str) -> None:
            await append("organization.transitioned", {"status": status})

        async def ensure_not_cancelled() -> None:
            if session_id in self._cancelled:
                self._cancelled.discard(session_id)
                await append("organization.transitioned", {"status": "cancelled"})
                raise RpcFault("RUN_CANCELLED", f"organization run cancelled: {session_id}")

        try:
            await append("organization.started", {"requirement": requirement})
            await append("budget.reserved", {"amount": budget})
            await transition("clarifying")
            invoked: set[str] = set()
            task_index = 0
            approval_index = 0

            async def run_meeting(meeting: Any) -> None:
                nonlocal revision, task_index, approval_index
                await ensure_not_cancelled()
                if meeting.id == "design_review":
                    await transition("planning")
                elif meeting.id == "daily_standup":
                    await transition("executing")
                elif meeting.id == "code_review":
                    await transition("verifying")
                elif meeting.id == "release_review":
                    await transition("releasing")
                elif meeting.id == "retro":
                    await transition("retrospective")

                await append("meeting.started", {
                    "meeting_id": meeting.id,
                    "name": meeting.name,
                    "participants": list(meeting.participants),
                })
                for role_id in meeting.participants:
                    if role_id in invoked:
                        continue
                    role = next(item for item in ROLES if item.id == role_id)
                    response = await self.host.call(
                        "agent.invoke",
                        {
                            "session_id": session_id,
                            "scope": scope,
                            "role_id": role_id,
                            "input": requirement,
                            "system_prompt": f"你是{role.name}。职责：{role.mission}",
                        },
                        child_mutation(f"{mutation.idempotency_key}:agent:{role_id}"),
                    )
                    if "revision" in response:
                        revision = int(response["revision"])
                    invoked.add(role_id)
                    await append("meeting.message", {
                        "meeting_id": meeting.id,
                        "role_id": role_id,
                        "content": str(response["content"]),
                    })
                    await append("task.created", {
                        "task_id": f"task-{task_index + 1}",
                        "assignee_role": role_id,
                        "title": f"{role.name}交付项",
                    })
                    task_index += 1
                await append("meeting.completed", {"meeting_id": meeting.id})

                if meeting.approval_gate is not None:
                    await transition("awaiting-approval")
                    approval_key = f"{mutation.idempotency_key}:approval:{approval_index}"
                    approval_index += 1
                    await append("approval.requested", {
                        "gate": meeting.approval_gate,
                        "meeting_id": meeting.id,
                    })
                    decision = await self.host.call(
                        "approval.request",
                        {
                            "session_id": session_id,
                            "gate": meeting.approval_gate,
                            "summary": f"Approve {meeting.name}",
                        },
                        child_mutation(approval_key),
                    )
                    await append("approval.resolved", {
                        "gate": meeting.approval_gate,
                        "approved": bool(decision["approved"]),
                        "reason": str(decision.get("reason", "")),
                    })
                    if not decision["approved"]:
                        raise RpcFault("APPROVAL_DENIED", f"{meeting.approval_gate} approval denied")

            await run_meeting_workflow(run_meeting)

            for index in range(task_index):
                task_id = f"task-{index + 1}"
                await append("task.transitioned", {"task_id": task_id, "status": "in_progress"})
                await append("task.transitioned", {"task_id": task_id, "status": "review"})
                await append("task.transitioned", {"task_id": task_id, "status": "done"})

            await append("memory.captured", {
                "kind": "retrospective-learning",
                "source": "retro",
                "content": "复盘证据已进入组织记忆，等待后续检索与治理。",
            })
            await append("evolution.proposed", {
                "kind": "process-improvement",
                "evidence": ["retro"],
                "requires_gate": "evolution",
            })
            await append("budget.committed", {"amount": budget})
            return await append(
                "organization.completed",
                {"status": "completed", "roles": len(invoked), "meetings": len(MEETINGS), "tasks": task_index},
                final=True,
            )
        except RpcFault:
            raise
        except Exception as error:
            try:
                await append("organization.failed", {"error": str(error)})
            except Exception:
                pass
            raise RpcFault("ORGANIZATION_RUN_FAILED", str(error), retryable=True) from error
