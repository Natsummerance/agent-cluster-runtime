"""会话管理（v0.6 T13.5）：项目级并发/预算/事件编排 + worktree 默认隔离。

- ``SessionManager``：serve 内会话注册表（替换 WorkbenchServer 直管 sessions
  dict 的内部实现）。``start`` 顺序（设计 §8.1 + §5.2 + §8.2）：预算硬上限检查
  → 并发判定 → worktree 分配 → ``SessionDriver`` 组装 → daemon 线程启动。
- ``SessionWorktree``：会话级 worktree（分支 ``acs/session/<sid>``，路径
  ``<主>/.agent-cluster/worktrees/sessions/<sid>``），复用
  ``agent_cluster.worktree._git`` 执行器。
- 取消语义：``cancel`` 在下一挂起点/门等待点生效；``shutdown`` 无孤儿线程。
"""

from __future__ import annotations

import queue
import threading
import uuid
from pathlib import Path
from typing import Any

from agent_cluster.projects import (
    BudgetPoolExhaustedError,
    ProjectStore,
    make_budget_pool_hook,
)
from agent_cluster.worktree import WORKTREE_SUBDIR, WorktreeError, _git

__all__ = ["SessionManager", "SessionWorktree", "WorktreeConflictError"]


class WorktreeConflictError(RuntimeError):
    """并发运行中显式 worktree=false 的冲突（§8.2；13.7 映射 400 + code conflict）。"""


class SessionWorktree:
    """会话级 git worktree（§8.2 精确路径与分支，不复用 WorktreeManager）。"""

    def __init__(self, main_workspace: str | Path, session_id: str) -> None:
        self.main = Path(main_workspace).expanduser().resolve()
        self.session_id = session_id
        self.path = self.main / WORKTREE_SUBDIR / "sessions" / session_id
        self.branch = f"acs/session/{session_id}"

    def ensure_repo(self) -> dict[str, Any]:
        """确保主工作区是带 HEAD 的 git 仓库（幂等；身份缺失时明确报错）。"""
        if not (self.main / ".git").exists():
            init = _git(["init"], cwd=self.main)
            if not init["ok"]:
                return {"ok": False, "output": f"git init 失败：{init['output']}"}
        head = _git(["rev-parse", "--verify", "HEAD"], cwd=self.main)
        if head["exit_code"] != 0:
            commit = _git(["commit", "--allow-empty", "-m", "acs: initial"], cwd=self.main)
            if not commit["ok"]:
                return {
                    "ok": False,
                    "output": f"初始提交失败：{commit['output']}（请先配置 git user.name/user.email）",
                }
        return {"ok": True, "output": "仓库就绪"}

    def add(self) -> dict[str, Any]:
        """懒创建 worktree（幂等：路径存在且已登记则复用）。"""
        ready = self.ensure_repo()
        if not ready["ok"]:
            return ready
        if self.path.exists():
            listed = _git(["worktree", "list"], cwd=self.main)
            if f"{self.path} " in listed["output"]:
                return {"ok": True, "output": "reuse"}
            return _git(["worktree", "add", str(self.path), self.branch], cwd=self.main)
        return _git(["worktree", "add", "-b", self.branch, str(self.path)], cwd=self.main)

    def merge_back(self, *, message: str | None = None) -> dict[str, Any]:
        """提交残留改动 → merge --no-ff 回主工作区 → 移除 worktree；冲突保留现场。"""
        _git(["add", "-A"], cwd=self.path)
        status = _git(["status", "--porcelain"], cwd=self.path)
        pending = status["ok"] and status["output"].strip() and status["output"].strip() != "(无输出)"
        if pending:
            _git(["commit", "-m", f"acs: {self.session_id} session sync"], cwd=self.path)
        merged = _git(
            ["merge", "--no-ff", self.branch, "-m", message or f"merge session {self.session_id} worktree"],
            cwd=self.main,
        )
        if not merged["ok"]:
            return {
                "ok": False,
                "output": f"合并 {self.branch} 失败（保留 worktree 现场）：{merged['output']}",
                "branch": self.branch,
                "path": str(self.path),
            }
        _git(["worktree", "remove", str(self.path), "--force"], cwd=self.main)
        return {"ok": True, "output": f"已合并 {self.branch} 并移除 worktree", "branch": self.branch}

    def close(self) -> dict[str, Any]:
        """放弃 worktree（不合并），强制移除并删除分支。"""
        result = _git(["worktree", "remove", str(self.path), "--force"], cwd=self.main)
        if result["ok"]:
            _git(["branch", "-D", self.branch], cwd=self.main)
        return result


class SessionManager:
    """会话注册表 + 项目级并发/预算/事件编排（§8.1）。"""

    def __init__(self, project_store: ProjectStore) -> None:
        self.project_store = project_store
        self.sessions: dict[str, Any] = {}
        self.events: list[dict[str, Any]] = []
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # 注册表
    # ------------------------------------------------------------------

    def get(self, session_id: str) -> Any:
        session = self.sessions.get(session_id)
        if session is None:
            raise KeyError(f"会话不存在：{session_id}")
        return session

    def list_for(self, project_id: str) -> list[Any]:
        with self._lock:
            return [session for session in self.sessions.values() if session.project_id == project_id]

    def running_in(self, project_id: str) -> int:
        return sum(
            1
            for session in self.list_for(project_id)
            if session.status in ("starting", "running", "waiting_approval")
        )

    # ------------------------------------------------------------------
    # 启动（§8.1：预算 → 并发 → worktree → 线程）
    # ------------------------------------------------------------------

    def start(self, project_id: str, spec: dict, *, store: ProjectStore | None = None) -> Any:
        from agent_cluster.server import ServerSession  # 惰性导入避免循环

        store = store or self.project_store
        if store.is_budget_exhausted(project_id):
            raise BudgetPoolExhaustedError(f"项目 {project_id} 预算硬上限已耗尽，请先解锁")
        try:
            project = store.get(project_id)
        except KeyError:
            raise KeyError(f"项目不存在：{project_id}") from None
        # 15.15：会话运行时携带租户归属（派生自项目记录 metadata）
        tenant_id = str((project.metadata or {}).get("tenant_id") or "")
        main_workspace = Path(project.workspaces[0])
        spec.setdefault("goal", "")

        concurrent = self.running_in(project_id) > 0
        worktree_flag = spec.get("worktree")
        if worktree_flag is None:
            use_worktree = concurrent
        elif bool(worktree_flag):
            use_worktree = True
        else:
            if concurrent:
                raise WorktreeConflictError(
                    "项目内已有运行中会话，显式 worktree=false 会共享主工作区写，已拒绝"
                )
            use_worktree = False

        session_id = uuid.uuid4().hex[:12]
        workspace = main_workspace
        worktree_path: Path | None = None
        if use_worktree:
            helper = SessionWorktree(main_workspace, session_id)
            added = helper.add()
            if not added["ok"]:
                raise WorktreeError(f"会话 worktree 创建失败：{added['output']}")
            workspace = helper.path
            worktree_path = helper.path

        checkpoint_root = store.session_dir(project_id, session_id) / "checkpoints"
        server_session = ServerSession(
            session_id,
            project_id,
            workspace,
            spec,
            worktree_path=worktree_path,
            main_workspace=main_workspace if worktree_path is not None else None,
            store_root=store.root,
            checkpoint_root=checkpoint_root,
            budget_pool_hook=make_budget_pool_hook(store, self._emit),
            tenant_id=tenant_id,
        )
        with self._lock:
            self.sessions[session_id] = server_session
        server_session.log_event(
            {"type": "session.start", "session_id": session_id, "payload": {"goal": spec.get("goal", "")}}
        )
        server_session.start()
        return server_session

    # ------------------------------------------------------------------
    # 事件 / 取消 / stdin / 关停
    # ------------------------------------------------------------------

    def _emit(self, name: str, payload: dict[str, Any]) -> None:
        """预算池事件出口：写入触发会话日志 + 管理器级事件流（13.7 总线消费）。"""
        session_id = str(payload.get("session_id") or "")
        session = self.sessions.get(session_id)
        if session is not None:
            session.log_event({"type": name, "session_id": session_id, "payload": payload})
        with self._lock:
            self.events.append({"type": name, **payload})

    def cancel(self, session_id: str) -> bool:
        """置 cancel_event + 唤醒挂起 prompt（/abort）；下一挂起点生效（§8.1）。"""
        try:
            session = self.get(session_id)
        except KeyError:
            return False
        session.cancel_event.set()
        session._answer_queue.put("/abort")
        session.log_event(
            {"type": "session.cancel", "session_id": session_id, "payload": {"cancelled": "pending"}}
        )
        return True

    def submit_stdin(self, session_id: str, text: str) -> bool:
        """投递 stdin 行（13.9：driver 就绪后走 inject_stdin；否则入队等待启动后绑定）。"""
        try:
            session = self.get(session_id)
        except KeyError:
            return False
        if session.status in ("completed", "failed"):
            return False
        driver = session.driver
        if driver is not None:
            return driver.inject_stdin(text)
        session.stdin_queue.put(text)
        return True

    def shutdown(self) -> int:
        """全部会话 cancel → join(≤10s) → daemon 兜底；返回未退出会话数（幂等）。"""
        with self._lock:
            sessions = list(self.sessions.values())
        for session in sessions:
            session.cancel_event.set()
            session._answer_queue.put("/abort")
        for session in sessions:
            if session.thread is not None and session.thread.is_alive():
                session.thread.join(timeout=10)
        return sum(
            1 for session in sessions if session.thread is not None and session.thread.is_alive()
        )
