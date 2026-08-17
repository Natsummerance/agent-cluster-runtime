"""Deterministic organization projection built only from SessionEvent values."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping


@dataclass(frozen=True)
class OrganizationState:
    status: str = "idle"
    meetings: tuple[str, ...] = ()
    tasks: dict[str, str] = field(default_factory=dict)
    approvals: tuple[dict[str, Any], ...] = ()
    budget_reserved: int = 0
    budget_committed: int = 0
    evolution_proposals: int = 0
    memory_items: int = 0


@dataclass
class _MutableState:
    status: str = "idle"
    meetings: list[str] = field(default_factory=list)
    tasks: dict[str, str] = field(default_factory=dict)
    approvals: list[dict[str, Any]] = field(default_factory=list)
    budget_reserved: int = 0
    budget_committed: int = 0
    evolution_proposals: int = 0
    memory_items: int = 0


def _value(event: Any, name: str) -> Any:
    return event[name] if isinstance(event, Mapping) else getattr(event, name)


class OrganizationProjector:
    _TASK_TRANSITIONS = {
        "todo": {"in_progress"},
        "in_progress": {"review", "blocked"},
        "blocked": {"in_progress"},
        "review": {"in_progress", "done"},
        "done": set(),
    }

    def project(self, events: Iterable[Any]) -> OrganizationState:
        state = _MutableState()
        expected_seq = 1
        for event in events:
            seq = int(_value(event, "seq"))
            if seq != expected_seq:
                raise ValueError(f"non-contiguous organization event stream: expected {expected_seq}, got {seq}")
            expected_seq += 1
            type_ = str(_value(event, "type"))
            payload = dict(_value(event, "payload"))
            if type_ == "organization.transitioned":
                state.status = str(payload["status"])
            elif type_ == "organization.completed":
                state.status = "completed"
            elif type_ == "organization.failed":
                state.status = "failed"
            elif type_ == "meeting.completed":
                state.meetings.append(str(payload["meeting_id"]))
            elif type_ == "task.created":
                state.tasks[str(payload["task_id"])] = "todo"
            elif type_ == "task.transitioned":
                task_id = str(payload["task_id"])
                target = str(payload["status"])
                current = state.tasks.get(task_id)
                if current is None:
                    raise ValueError(f"task transition references unknown task: {task_id}")
                if target not in self._TASK_TRANSITIONS[current]:
                    raise ValueError(f"illegal task transition: {task_id} {current} -> {target}")
                state.tasks[task_id] = target
            elif type_ == "approval.resolved":
                state.approvals.append(payload)
            elif type_ == "budget.reserved":
                state.budget_reserved += int(payload["amount"])
            elif type_ == "budget.committed":
                state.budget_committed += int(payload["amount"])
            elif type_ == "evolution.proposed":
                state.evolution_proposals += 1
            elif type_ == "memory.captured":
                state.memory_items += 1
        return OrganizationState(
            status=state.status,
            meetings=tuple(state.meetings),
            tasks=dict(state.tasks),
            approvals=tuple(state.approvals),
            budget_reserved=state.budget_reserved,
            budget_committed=state.budget_committed,
            evolution_proposals=state.evolution_proposals,
            memory_items=state.memory_items,
        )
