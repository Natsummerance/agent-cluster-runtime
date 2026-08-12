"""账本与任务板（设计文档 §4.2 / §5.6）：LedgerStore（Magentic-One 心智）与 TaskBoard。

- ``LedgerStore``：按 task_id 读写 ``Ledger``（facts/plan/progress/is_satisfied/
  is_looping）的内存 dict 存储；后续可无缝替换为持久化实现（文档化约定：
  存储层仅通过本类访问，不直接操作 dict）。
  - ``get(task_id)``：不存在抛 ``KeyError``（含任务清单）。
  - ``update(ledger)``：按 ledger.task_id 覆盖写入（upsert）。
  - ``append_fact`` / ``append_progress``：不存在时自动建账本后追加。
  - ``mark_satisfied`` / ``mark_looping``：不存在时自动建账本后置位。
- ``TaskBoard``：五列（Backlog/Ready/InProgress/Review/Done）+ Blocked 标记列；
  ``move(task_id, to)`` 校验合法流转，非法跳转抛 ``TaskBoardError``。
  合法流转（契约）：
  - 线性：Backlog→Ready→InProgress→Review→Done。
  - 任意列→Blocked；Blocked→InProgress / Blocked→Ready。
  - 同列移动视为无操作（合法）。
  - 其余（如 Backlog→Done、Ready→Review、Blocked→Done）一律拒绝。
  ``to_state_channels()`` 把看板列映射回 ``Task.status`` 返回 ``{"tasks": [...]}``
  供接入 ``ClusterState.tasks``（ready 列在 TaskStatus 中无对应值，映射为 todo）。
"""

from __future__ import annotations

from collections.abc import Iterable

from agent_cluster.models import Ledger, ProgressEntry, Task, TaskStatus

__all__ = ["TaskBoardError", "LedgerStore", "TaskBoard", "COLUMNS", "BLOCKED"]

# 看板五列 + Blocked 标记列（契约：列名精确匹配，move 时大小写不敏感归一化）
COLUMNS: tuple[str, ...] = ("Backlog", "Ready", "InProgress", "Review", "Done")
BLOCKED: str = "Blocked"

# 列名归一化表（小写 -> 规范列名）
_COLUMN_ALIASES: dict[str, str] = {
    "backlog": "Backlog",
    "ready": "Ready",
    "inprogress": "InProgress",
    "in_progress": "InProgress",
    "review": "Review",
    "done": "Done",
    "blocked": "Blocked",
}

# 列 -> TaskStatus 映射（导出通道用；ready 无对应 TaskStatus，映射为 todo）
_COLUMN_TO_STATUS: dict[str, TaskStatus] = {
    "Backlog": TaskStatus.TODO,
    "Ready": TaskStatus.TODO,
    "InProgress": TaskStatus.DOING,
    "Review": TaskStatus.REVIEW,
    "Done": TaskStatus.DONE,
    "Blocked": TaskStatus.BLOCKED,
}

# 合法流转表（current -> 允许的 target 集合；同列移动恒合法）
# 「任意列 -> Blocked」为全局规则，在 move() 内单独放行。
_LEGAL_TRANSITIONS: dict[str, set[str]] = {
    "Backlog": {"Ready"},
    "Ready": {"InProgress"},
    "InProgress": {"Review"},
    "Review": {"Done"},
    "Blocked": {"InProgress", "Ready"},
}


class TaskBoardError(Exception):
    """任务板非法操作：任务不存在、未知列名、非法状态流转。"""


class LedgerStore:
    """任务账本存储（内存实现，文档化：后续可替换为持久化后端）。"""

    def __init__(self) -> None:
        self._ledgers: dict[str, Ledger] = {}

    def get(self, task_id: str) -> Ledger:
        """按任务 id 读取账本；不存在抛 KeyError（含已知任务清单）。"""
        try:
            return self._ledgers[task_id]
        except KeyError:
            raise KeyError(f"账本不存在：task_id={task_id!r}（已知任务：{sorted(self._ledgers)}）") from None

    def update(self, ledger: Ledger) -> None:
        """按 ledger.task_id 覆盖写入（upsert）。"""
        self._ledgers[ledger.task_id] = ledger

    def append_fact(self, task_id: str, fact: str) -> None:
        """追加事实（不存在时自动建账本）。"""
        ledger = self._get_or_create(task_id)
        ledger.facts.append(fact)

    def append_progress(self, task_id: str, entry: ProgressEntry) -> None:
        """追加进度条目（不存在时自动建账本）。"""
        ledger = self._get_or_create(task_id)
        ledger.progress.append(entry)

    def mark_satisfied(self, task_id: str) -> None:
        """标记任务已满足（不存在时自动建账本）。"""
        ledger = self._get_or_create(task_id)
        ledger.is_satisfied = True

    def mark_looping(self, task_id: str) -> None:
        """标记任务检测到死循环（不存在时自动建账本）。"""
        ledger = self._get_or_create(task_id)
        ledger.is_looping = True

    def _get_or_create(self, task_id: str) -> Ledger:
        """读取账本；不存在时创建空账本并写入存储。"""
        ledger = self._ledgers.get(task_id)
        if ledger is None:
            ledger = Ledger(task_id=task_id)
            self._ledgers[task_id] = ledger
        return ledger


class TaskBoard:
    """任务板：五列 + Blocked 标记列，按迭代聚合完成率。

    看板列与 ``Task.status`` 相互独立（看板自行维护列），导出时经
    ``to_state_channels()`` 映射回 ``TaskStatus``。
    """

    def __init__(self, tasks: Iterable[Task] | None = None) -> None:
        self._tasks: dict[str, Task] = {}
        self._columns: dict[str, str] = {}
        for task in tasks or []:
            self.add(task)

    def add(self, task: Task) -> None:
        """把任务加入 Backlog 列；重复 id 抛 TaskBoardError。"""
        if task.id in self._tasks:
            raise TaskBoardError(f"任务已存在：{task.id!r}")
        self._tasks[task.id] = task
        self._columns[task.id] = COLUMNS[0]

    def move(self, task_id: str, to: str) -> Task:
        """把任务移动到目标列；非法流转/未知列抛 TaskBoardError。"""
        if task_id not in self._tasks:
            raise TaskBoardError(f"任务不存在：{task_id!r}")
        target = self._normalize_column(to)
        current = self._columns[task_id]
        if current != target:
            # 任意列 -> Blocked 恒合法；其余必须命中合法流转表
            legal = target == BLOCKED or target in _LEGAL_TRANSITIONS.get(current, set())
            if not legal:
                raise TaskBoardError(f"非法任务流转：{current} → {target}（任务 {task_id!r}）")
        self._columns[task_id] = target
        return self._tasks[task_id]

    def by_iteration(self, iteration_id: str) -> list[Task]:
        """返回指定迭代的任务列表（按任务 id 排序，确定性）。"""
        return sorted(
            (task for task in self._tasks.values() if task.iteration_id == iteration_id),
            key=lambda task: task.id,
        )

    def completion_rate(self, iteration_id: str) -> float:
        """返回迭代完成率：Done 列任务数 / 迭代任务总数；无任务返回 0.0。"""
        iteration_tasks = self.by_iteration(iteration_id)
        if not iteration_tasks:
            return 0.0
        done_count = sum(1 for task in iteration_tasks if self._columns.get(task.id) == "Done")
        return done_count / len(iteration_tasks)

    def to_state_channels(self) -> dict[str, list[Task]]:
        """导出 LangGraph 通道更新：``{"tasks": [...]}``，状态按看板列映射。"""
        tasks = [
            task.model_copy(update={"status": _COLUMN_TO_STATUS[self._columns[task.id]]})
            for task in self._tasks.values()
        ]
        return {"tasks": tasks}

    @staticmethod
    def _normalize_column(name: str) -> str:
        """把列名归一化为规范列名（大小写不敏感）；未知列抛 TaskBoardError。"""
        canonical = _COLUMN_ALIASES.get(name.strip().lower())
        if canonical is None:
            raise TaskBoardError(f"未知看板列：{name!r}（支持：{list(_COLUMN_ALIASES)}）")
        return canonical
