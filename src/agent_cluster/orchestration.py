"""高级编排（v0.7 Task 14.17，dsh plan/goal/jobs/schedule 语义移植）。

- ``PlanStore``：plan/mode 折叠状态（``plan/mode`` 事件 whole-value-replace，
  对照 dsh ``foldPlanMode``——状态恒为事件流折叠结果，无事件默认 inactive）。
- ``GoalService``：``goal/change`` 快照事件 + ``expected_version`` CAS + 轮次上限
  （默认 5，可配；对照 dsh GoalSnapshot/GoalRef/roundsStarted）。
- ``JobRegistry``：first-wins settlement（幂等）+ owner 作用域授权
  （对照 dsh jobs 注册表语义：已 settle 的 job 再次 settle 返回当前状态）。
- ``ScheduleStore``：at/after/every 三类排程，分钟粒度下限 5
  （``frequency_too_high``；``at`` 必须未来 ``not_future``；``schedule/change`` 事件）。

事件词汇：``plan/*``、``goal/*``、``job/*``、``schedule/*`` 只增不改既有
（见 ``events.py`` 的 ``KNOWN_SESSION_EVENT_TYPES``）。

契约出处见 ``docs/porting/2026-08-14-dsh-porting.md``（MIT，dsh ``47f943859b``）。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping

from agent_cluster.events import SessionEvent, SessionEventLog

__all__ = [
    "Orchestration",
    "OrchestrationError",
    "OwnerError",
    "Plan",
    "PlanStore",
    "RoundLimitError",
    "Schedule",
    "ScheduleStore",
    "ScheduleValidationError",
    "VersionConflictError",
    "Goal",
    "GoalService",
    "Job",
    "JobRegistry",
    "fold_goal",
    "fold_plan_mode",
    "fold_schedules",
]

# 契约常量
GOAL_PHASES: tuple[str, ...] = ("active", "paused", "blocked", "complete")
JOB_STATES: tuple[str, ...] = ("pending", "running", "settled")
SCHEDULE_KINDS: tuple[str, ...] = ("at", "after", "every")
MIN_SCHEDULE_MINUTES: int = 5
DEFAULT_MAX_ROUNDS: int = 5


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _new_id() -> str:
    return uuid.uuid4().hex[:12]


def _as_dict(record: Any) -> dict[str, Any]:
    """把 dataclass 记录转为 JSON 友好的 dict（去掉 None 值）。"""
    return {key: value for key, value in record.__dict__.items() if value is not None}


class OrchestrationError(RuntimeError):
    """编排域错误基类（serve 层按子类映射状态码）。"""


class VersionConflictError(OrchestrationError):
    """goal CAS 版本不匹配（serve 层映射 409 version_conflict）。"""


class RoundLimitError(OrchestrationError):
    """goal 轮次超出上限（serve 层映射 400 round_limit）。"""


class OwnerError(OrchestrationError):
    """job 操作越过 owner 作用域授权（serve 层映射 403 owner_required）。"""


class ScheduleValidationError(OrchestrationError):
    """排程参数非法；``code`` 携带稳定错误码（frequency_too_high/not_future/bad_request）。"""

    def __init__(self, message: str, code: str = "bad_request") -> None:
        super().__init__(message)
        self.code = code


# ---------------------------------------------------------------------------
# Plan：mode 折叠
# ---------------------------------------------------------------------------


@dataclass
class Plan:
    """计划：id/mode（折叠状态）/goals/jobs。"""

    id: str
    name: str
    mode: str = "inactive"
    goals: list[str] = field(default_factory=list)
    jobs: list[str] = field(default_factory=list)
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)

    def to_dict(self) -> dict[str, Any]:
        return _as_dict(self)


class PlanStore:
    """计划存储（进程内）；``plan/mode`` 事件为唯一 durable 折叠源。"""

    def __init__(self, log: SessionEventLog | None = None) -> None:
        self._plans: dict[str, Plan] = {}
        self._log = log

    def create_plan(self, *, name: str = "", mode: str = "inactive") -> Plan:
        """新建计划；mode 必须为 active/inactive（缺省 inactive）。"""
        mode = mode.strip()
        if mode not in ("active", "inactive"):
            raise ValueError(f"plan mode 仅支持 active/inactive，实际 {mode!r}")
        plan = Plan(id=_new_id(), name=name.strip(), mode=mode)
        self._plans[plan.id] = plan
        return plan

    def get_plan(self, plan_id: str) -> Plan:
        try:
            return self._plans[plan_id]
        except KeyError:
            raise KeyError(f"未找到计划：{plan_id!r}") from None

    def list_plans(self) -> list[Plan]:
        """按创建顺序返回全部计划。"""
        return list(self._plans.values())

    def set_mode(self, plan_id: str, active: bool) -> Plan:
        """翻转 plan mode（whole-value-replace）；写入 ``plan/mode`` 事件。"""
        plan = self.get_plan(plan_id)
        plan.mode = "active" if active else "inactive"
        plan.updated_at = _now()
        if self._log is not None:
            self._log.append("plan/mode", {"plan_id": plan_id, "active": bool(active)})
        return plan

    def add_goal(self, plan_id: str, goal_id: str) -> Plan:
        """把目标 id 挂到计划（重复挂载忽略）。"""
        plan = self.get_plan(plan_id)
        if goal_id not in plan.goals:
            plan.goals.append(goal_id)
            plan.updated_at = _now()
        return plan

    def add_job(self, plan_id: str, job_id: str) -> Plan:
        """把任务 id 挂到计划（重复挂载忽略）。"""
        plan = self.get_plan(plan_id)
        if job_id not in plan.jobs:
            plan.jobs.append(job_id)
            plan.updated_at = _now()
        return plan


def fold_plan_mode(events: list[SessionEvent] | tuple[SessionEvent, ...], end: int | None = None) -> bool:
    """折叠 plan/mode 事件：末位 active 决定（whole-value-replace）；无事件默认 inactive。

    对照 dsh ``foldPlanMode(events, end?)``。
    """
    active = False
    for event in events[:end]:
        if event.type == "plan/mode":
            active = bool(event.payload.get("active", False))
    return active


# ---------------------------------------------------------------------------
# Goal：快照 + CAS + 轮次上限
# ---------------------------------------------------------------------------


@dataclass
class Goal:
    """目标：快照（id/objective/status/rounds/version/max_rounds/blocked_reason）。"""

    id: str
    plan_id: str
    objective: str
    status: str = "active"
    rounds: int = 0
    version: int = 1
    max_rounds: int = DEFAULT_MAX_ROUNDS
    blocked_reason: dict[str, str] | None = None
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)

    def snapshot(self) -> dict[str, Any]:
        """完整快照（goal/change 事件载荷）。"""
        return _as_dict(self)

    def to_dict(self) -> dict[str, Any]:
        return self.snapshot()


class GoalService:
    """目标服务：create/change（CAS）+ 轮次推进；每次变更写 ``goal/change`` 快照事件。"""

    def __init__(self, max_rounds: int = DEFAULT_MAX_ROUNDS, log: SessionEventLog | None = None) -> None:
        self.max_rounds = max_rounds
        self._goals: dict[str, Goal] = {}
        self._log = log

    def create_goal(self, plan_id: str, objective: str, max_rounds: int | None = None) -> Goal:
        """新建目标（version=1，rounds=0）；max_rounds 缺省用服务配置。"""
        objective = objective.strip()
        if not objective:
            raise ValueError("objective 不能为空")
        if max_rounds is None:
            max_rounds = self.max_rounds
        if max_rounds < 1:
            raise ValueError("max_rounds 必须为正整数")
        goal = Goal(id=_new_id(), plan_id=plan_id, objective=objective, max_rounds=max_rounds)
        self._goals[goal.id] = goal
        self._append_goal_event("create", goal)
        return goal

    def get_goal(self, goal_id: str) -> Goal:
        try:
            return self._goals[goal_id]
        except KeyError:
            raise KeyError(f"未找到目标：{goal_id!r}") from None

    def list_goals(self) -> list[Goal]:
        return list(self._goals.values())

    def change_goal(
        self,
        goal_id: str,
        expected_version: int,
        *,
        objective: str | None = None,
        status: str | None = None,
        blocked_reason: Mapping[str, str] | None = None,
        start_round: bool = False,
    ) -> Goal:
        """CAS 变更：expected_version 必须等于当前 version，否则 VersionConflictError。

        - ``start_round``：轮次 +1（超上限抛 RoundLimitError，状态不变）。
        - ``status``：必须出自 GOAL_PHASES；置 blocked 必须携带 blocked_reason。
        """
        goal = self.get_goal(goal_id)
        if expected_version != goal.version:
            raise VersionConflictError(
                f"goal 版本冲突：expected_version={expected_version}，当前 version={goal.version}（goal {goal_id!r}）"
            )
        if status is not None and status not in GOAL_PHASES:
            raise ValueError(f"status 仅支持 {list(GOAL_PHASES)}，实际 {status!r}")
        if status == "blocked" and not blocked_reason:
            raise ValueError("status=blocked 必须携带 blocked_reason（code/message）")
        if status != "blocked":
            blocked_reason = None
        if start_round:
            if goal.rounds >= goal.max_rounds:
                raise RoundLimitError(
                    f"goal 轮次已达上限：rounds={goal.rounds}，max_rounds={goal.max_rounds}（goal {goal_id!r}）"
                )
            goal.rounds += 1
        if objective is not None:
            objective = objective.strip()
            if not objective:
                raise ValueError("objective 不能为空")
            goal.objective = objective
        if status is not None:
            goal.status = status
        goal.blocked_reason = dict(blocked_reason) if blocked_reason else None
        goal.version += 1
        goal.updated_at = _now()
        self._append_goal_event("update", goal)
        return goal

    def _append_goal_event(self, operation: str, goal: Goal) -> None:
        if self._log is not None:
            self._log.append(
                "goal/change",
                {"operation": operation, "goal": goal.snapshot(), "updated_at": goal.updated_at},
            )


def fold_goal(
    events: list[SessionEvent] | tuple[SessionEvent, ...],
    goal_id: str,
    end: int | None = None,
) -> dict[str, Any] | None:
    """折叠 goal/change 事件：返回该 goal 最后一个快照（末位胜出）；无则 None。"""
    snapshot: dict[str, Any] | None = None
    for event in events[:end]:
        if event.type != "goal/change":
            continue
        goal = event.payload.get("goal")
        if isinstance(goal, Mapping) and goal.get("id") == goal_id:
            snapshot = dict(goal)
    return snapshot


# ---------------------------------------------------------------------------
# Jobs：first-wins settlement + owner 授权
# ---------------------------------------------------------------------------


@dataclass
class Job:
    """任务：id/owner/state/settlement（first-wins）。"""

    id: str
    owner: str
    state: str = "pending"
    outcome: str = ""
    settled_at: str | None = None
    plan_id: str | None = None
    created_at: str = field(default_factory=_now)

    def to_dict(self) -> dict[str, Any]:
        return _as_dict(self)


class JobRegistry:
    """任务注册表：注册 + first-wins settlement（幂等）+ owner 作用域授权。"""

    def __init__(self, log: SessionEventLog | None = None) -> None:
        self._jobs: dict[str, Job] = {}
        self._log = log

    def register(self, owner: str, *, plan_id: str | None = None) -> Job:
        """注册任务（state=pending）；owner 必填。"""
        owner = owner.strip()
        if not owner:
            raise ValueError("owner 不能为空")
        job = Job(id=_new_id(), owner=owner, plan_id=plan_id)
        self._jobs[job.id] = job
        if self._log is not None:
            self._log.append("job/register", {"job_id": job.id, "owner": owner, "state": job.state})
        return job

    def get_job(self, job_id: str) -> Job:
        try:
            return self._jobs[job_id]
        except KeyError:
            raise KeyError(f"未找到任务：{job_id!r}") from None

    def list_jobs(self) -> list[Job]:
        return list(self._jobs.values())

    def settle(self, job_id: str, outcome: str, caller: str) -> tuple[Job, bool]:
        """结算任务（first-wins）：只有 owner 可结算；已 settle 再次 settle 幂等返回当前状态。

        返回 ``(job, first)``：``first=True`` 表示本次完成首次结算。
        """
        job = self.get_job(job_id)
        if caller != job.owner:
            raise OwnerError(f"任务 {job_id!r} 归 {job.owner!r} 所有，caller={caller!r} 无权结算")
        if job.state == "settled":
            return job, False
        job.state = "settled"
        job.outcome = outcome
        job.settled_at = _now()
        if self._log is not None:
            self._log.append(
                "job/settle",
                {"job_id": job_id, "owner": job.owner, "outcome": outcome, "state": job.state, "first": True},
            )
        return job, True


# ---------------------------------------------------------------------------
# Schedule：at/after/every（分钟粒度下限 5）
# ---------------------------------------------------------------------------


@dataclass
class Schedule:
    """排程：at（绝对时间）/after（相对分钟）/every（固定间隔分钟）。"""

    id: str
    kind: str
    at: str | None = None
    after_minutes: int | None = None
    every_minutes: int | None = None
    state: str = "active"
    created_at: str = field(default_factory=_now)

    def to_dict(self) -> dict[str, Any]:
        return _as_dict(self)


def _parse_iso(value: str) -> datetime:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ScheduleValidationError(f"at 必须是 ISO 8601 时间：{value!r}", code="bad_request") from None


class ScheduleStore:
    """排程存储：at/after/every 校验（every/after ≥5 分钟，at 必须未来）。"""

    def __init__(self, log: SessionEventLog | None = None) -> None:
        self._schedules: dict[str, Schedule] = {}
        self._log = log

    def create_schedule(
        self,
        kind: str,
        *,
        at: str | None = None,
        after_minutes: int | None = None,
        every_minutes: int | None = None,
    ) -> Schedule:
        """新建排程；kind 必须为 at/after/every，且携带对应字段。"""
        kind = kind.strip()
        if kind not in SCHEDULE_KINDS:
            raise ScheduleValidationError(
                f"kind 仅支持 {list(SCHEDULE_KINDS)}，实际 {kind!r}", code="bad_request"
            )
        schedule: Schedule
        if kind == "at":
            if not at:
                raise ScheduleValidationError("kind=at 必须提供 at（ISO 8601）", code="bad_request")
            parsed = _parse_iso(at)
            if parsed <= datetime.now(timezone.utc):
                raise ScheduleValidationError(f"not_future：at 必须指向未来：{at!r}", code="not_future")
            schedule = Schedule(id=_new_id(), kind=kind, at=at)
        elif kind == "after":
            if after_minutes is None:
                raise ScheduleValidationError("kind=after 必须提供 after_minutes", code="bad_request")
            if after_minutes < MIN_SCHEDULE_MINUTES:
                raise ScheduleValidationError(
                    f"frequency_too_high：after_minutes 不能小于 {MIN_SCHEDULE_MINUTES}：{after_minutes}",
                    code="frequency_too_high",
                )
            schedule = Schedule(id=_new_id(), kind=kind, after_minutes=int(after_minutes))
        else:  # every
            if every_minutes is None:
                raise ScheduleValidationError("kind=every 必须提供 every_minutes", code="bad_request")
            if every_minutes < MIN_SCHEDULE_MINUTES:
                raise ScheduleValidationError(
                    f"frequency_too_high：every_minutes 不能小于 {MIN_SCHEDULE_MINUTES}：{every_minutes}",
                    code="frequency_too_high",
                )
            schedule = Schedule(id=_new_id(), kind=kind, every_minutes=int(every_minutes))
        self._schedules[schedule.id] = schedule
        self._append_schedule_event("create", schedule=schedule)
        return schedule

    def get_schedule(self, schedule_id: str) -> Schedule:
        try:
            return self._schedules[schedule_id]
        except KeyError:
            raise KeyError(f"未找到排程：{schedule_id!r}") from None

    def list_schedules(self) -> list[Schedule]:
        """按创建顺序返回全部排程。"""
        return list(self._schedules.values())

    def delete_schedule(self, schedule_id: str) -> None:
        """删除排程（终态 id-only 事件）；缺失抛 KeyError。"""
        self.get_schedule(schedule_id)
        del self._schedules[schedule_id]
        self._append_schedule_event("delete", schedule_id=schedule_id)

    def _append_schedule_event(
        self,
        operation: str,
        *,
        schedule: Schedule | None = None,
        schedule_id: str | None = None,
    ) -> None:
        if self._log is None:
            return
        payload: dict[str, Any] = {"operation": operation}
        if operation == "create" and schedule is not None:
            payload["schedule"] = schedule.to_dict()
        else:
            payload["id"] = schedule_id
        self._log.append("schedule/change", payload)


def fold_schedules(events: list[SessionEvent] | tuple[SessionEvent, ...], end: int | None = None) -> list[dict[str, Any]]:
    """折叠 schedule/change 事件：create 生效、delete 移除、dispatch 保留记录（按创建顺序）。"""
    active: dict[str, dict[str, Any]] = {}
    for event in events[:end]:
        if event.type != "schedule/change":
            continue
        operation = event.payload.get("operation")
        if operation == "create":
            schedule = event.payload.get("schedule")
            if isinstance(schedule, Mapping) and schedule.get("id"):
                active[str(schedule["id"])] = dict(schedule)
        elif operation in ("delete", "dispatch"):
            sid = event.payload.get("id")
            if isinstance(sid, str):
                if operation == "delete":
                    active.pop(sid, None)
                elif sid in active:
                    active[sid] = dict(active[sid], state="dispatched")
    return list(active.values())


# ---------------------------------------------------------------------------
# 编排门面（serve 挂载点）
# ---------------------------------------------------------------------------


class Orchestration:
    """高级编排门面：PlanStore/GoalService/JobRegistry/ScheduleStore 组合。"""

    def __init__(
        self,
        log: SessionEventLog | None = None,
        max_rounds: int = DEFAULT_MAX_ROUNDS,
    ) -> None:
        self.plans = PlanStore(log=log)
        self.goals = GoalService(max_rounds=max_rounds, log=log)
        self.jobs = JobRegistry(log=log)
        self.schedules = ScheduleStore(log=log)
