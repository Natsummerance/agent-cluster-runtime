"""Task 5 行为测试：LedgerStore 账本读写 + TaskBoard 合法/非法流转与完成率。"""

from __future__ import annotations

import pytest

from agent_cluster.ledger import BLOCKED, COLUMNS, LedgerStore, TaskBoard, TaskBoardError
from agent_cluster.models import Ledger, ProgressEntry, Task, TaskStatus


# ---------------------------------------------------------------------------
# LedgerStore
# ---------------------------------------------------------------------------


def test_get_missing_raises_key_error():
    store = LedgerStore()
    with pytest.raises(KeyError, match="task-1"):
        store.get("task-1")


def test_append_fact_and_get():
    store = LedgerStore()
    store.append_fact("task-1", "需求已澄清")
    ledger = store.get("task-1")
    assert ledger.task_id == "task-1"
    assert ledger.facts == ["需求已澄清"]
    assert ledger.progress == []
    assert ledger.is_satisfied is False
    assert ledger.is_looping is False


def test_append_progress_and_update_upsert():
    store = LedgerStore()
    store.append_progress("task-1", ProgressEntry(role="architect", status="doing", verdict="ok", next_action="review"))
    entry = store.get("task-1").progress[-1]
    assert entry.role == "architect"
    assert entry.next_action == "review"

    # update 覆盖写入（upsert）
    replaced = Ledger(task_id="task-1", facts=["新事实"], plan=["步骤 1"])
    store.update(replaced)
    assert store.get("task-1").facts == ["新事实"]
    assert store.get("task-1").plan == ["步骤 1"]


def test_mark_satisfied_and_mark_looping():
    store = LedgerStore()
    store.mark_satisfied("task-1")
    store.mark_looping("task-1")
    ledger = store.get("task-1")
    assert ledger.is_satisfied is True
    assert ledger.is_looping is True


# ---------------------------------------------------------------------------
# TaskBoard
# ---------------------------------------------------------------------------


def _task(task_id: str, iteration_id: str = "iter-1") -> Task:
    return Task(
        id=task_id,
        project_id="proj1",
        iteration_id=iteration_id,
        title=f"任务 {task_id}",
        desc="描述",
        assignee_role="backend",
    )


def test_add_defaults_to_backlog():
    board = TaskBoard()
    board.add(_task("t1"))
    channels = board.to_state_channels()
    assert channels == {"tasks": [board.to_state_channels()["tasks"][0]]}
    assert channels["tasks"][0].id == "t1"
    assert channels["tasks"][0].status == TaskStatus.TODO  # Backlog -> todo


def test_legal_linear_transitions():
    board = TaskBoard()
    board.add(_task("t1"))
    board.move("t1", "Ready")
    board.move("t1", "InProgress")
    board.move("t1", "Review")
    board.move("t1", "Done")
    assert board.completion_rate("iter-1") == 1.0
    assert board.to_state_channels()["tasks"][0].status == TaskStatus.DONE


def test_any_to_blocked_and_back():
    board = TaskBoard()
    board.add(_task("t1"))
    board.move("t1", "Ready")
    board.move("t1", "InProgress")
    board.move("t1", "Blocked")
    assert board.to_state_channels()["tasks"][0].status == TaskStatus.BLOCKED
    board.move("t1", "InProgress")  # Blocked -> InProgress
    board.move("t1", "Blocked")
    board.move("t1", "Ready")  # Blocked -> Ready
    assert board.to_state_channels()["tasks"][0].status == TaskStatus.TODO


def test_illegal_transitions_raise():
    board = TaskBoard()
    board.add(_task("t1"))
    with pytest.raises(TaskBoardError, match="非法任务流转"):
        board.move("t1", "Done")  # Backlog -> Done 跳转
    with pytest.raises(TaskBoardError, match="非法任务流转"):
        board.move("t1", "Review")  # Backlog -> Review 跳转
    board.move("t1", "Ready")
    with pytest.raises(TaskBoardError, match="非法任务流转"):
        board.move("t1", "Review")  # Ready -> Review 跳转
    board.move("t1", "Blocked")
    with pytest.raises(TaskBoardError, match="非法任务流转"):
        board.move("t1", "Done")  # Blocked -> Done 非法


def test_move_unknown_task_raises():
    board = TaskBoard()
    with pytest.raises(TaskBoardError, match="任务不存在"):
        board.move("ghost", "Done")


def test_move_unknown_column_raises():
    board = TaskBoard()
    board.add(_task("t1"))
    with pytest.raises(TaskBoardError, match="未知看板列"):
        board.move("t1", "Shipped")


def test_move_case_insensitive_column():
    board = TaskBoard()
    board.add(_task("t1"))
    board.move("t1", "ready")
    board.move("t1", "in_progress")
    board.move("t1", "review")
    board.move("t1", "DONE")
    assert board.completion_rate("iter-1") == 1.0


def test_duplicate_add_raises():
    board = TaskBoard()
    board.add(_task("t1"))
    with pytest.raises(TaskBoardError, match="任务已存在"):
        board.add(_task("t1"))


def test_by_iteration_filters():
    board = TaskBoard()
    board.add(_task("t1", "iter-1"))
    board.add(_task("t2", "iter-1"))
    board.add(_task("t3", "iter-2"))
    assert [task.id for task in board.by_iteration("iter-1")] == ["t1", "t2"]
    assert [task.id for task in board.by_iteration("iter-2")] == ["t3"]
    assert board.by_iteration("iter-3") == []


def test_completion_rate_math():
    board = TaskBoard()
    board.add(_task("t1", "iter-1"))
    board.add(_task("t2", "iter-1"))
    board.add(_task("t3", "iter-1"))
    board.add(_task("t4", "iter-1"))
    board.move("t1", "Ready")
    board.move("t1", "InProgress")
    board.move("t1", "Review")
    board.move("t1", "Done")
    board.move("t2", "Blocked")  # 阻塞不算完成
    board.move("t3", "Ready")
    board.move("t3", "InProgress")
    assert board.completion_rate("iter-1") == 0.25  # 1/4
    assert board.completion_rate("iter-9") == 0.0  # 空迭代


def test_to_state_channels_maps_columns_to_statuses():
    board = TaskBoard()
    board.add(_task("t1"))
    board.move("t1", "Ready")
    board.add(_task("t2"))
    board.move("t2", "Ready")
    board.move("t2", "InProgress")
    board.add(_task("t3"))
    board.move("t3", "Ready")
    board.move("t3", "InProgress")
    board.move("t3", "Review")
    board.add(_task("t4"))
    board.move("t4", "Ready")
    board.move("t4", "InProgress")
    board.move("t4", "Review")
    board.move("t4", "Done")
    statuses = {task.id: task.status for task in board.to_state_channels()["tasks"]}
    assert statuses == {
        "t1": TaskStatus.TODO,  # Ready 无对应 TaskStatus，映射为 todo
        "t2": TaskStatus.DOING,
        "t3": TaskStatus.REVIEW,
        "t4": TaskStatus.DONE,
    }
