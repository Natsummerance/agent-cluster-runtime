"""git worktree 隔离管理器（v0.4 T11.6，参考 aider git-native 思路）。

- 每个开发角色/任务在独立 worktree 分支提交，节点完成后 ``merge_back`` 合并回
  主工作区，避免并行写冲突（结合 ToolSession 写锁与按角色会话隔离）。
- ``ensure_repo``：工作区不是 git 仓库时 ``git init`` + 空初始提交（worktree add
  需要 HEAD）；git 身份未配置时提交失败并给出明确错误。
- 越界校验：每个 worktree 会话的 ToolSession 以 worktree 路径为根，``_resolve_within``
  天然拒绝 ``../`` 逃逸回主工作区（测试覆盖）。
- 合并冲突时不删除 worktree（保留现场），返回失败信息由上层决定处理。
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

WORKTREE_SUBDIR = ".agent-cluster/worktrees"


class WorktreeError(RuntimeError):
    """git worktree 操作失败（含 git 身份未配置等）。"""


def _git(args: list[str], *, cwd: Path, timeout: int = 60) -> dict[str, Any]:
    """执行 git 子命令并捕获输出（Windows 无窗口）。"""
    env = dict(os.environ)
    env.update({"PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"})
    creationflags = 0
    if os.name == "nt":
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        proc = subprocess.run(
            ["git", "-C", str(cwd), *args],
            cwd=str(cwd),
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            creationflags=creationflags,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "output": f"git 命令超时（>{timeout}s）：{' '.join(args)}", "exit_code": -1}
    except OSError as exc:
        return {"ok": False, "output": f"git 启动失败：{exc}", "exit_code": -2}
    output = ((proc.stdout or "") + (proc.stderr or "")).strip() or "(无输出)"
    return {"ok": proc.returncode == 0, "output": output, "exit_code": proc.returncode}


class WorktreeManager:
    """按角色管理独立 git worktree。

    - ``session_for(role_id)``：懒创建 worktree 并返回绑定到该 worktree 的
      ``ToolSession``（注册表可注入，缺省 ``build_default_tools()``）。
    - ``merge_back(role_id)``：提交残留改动 -> ``--no-ff`` 合并回主工作区 ->
      移除 worktree；冲突时保留现场并返回失败。
    - ``close(role_id)``：放弃 worktree（不合并，强制移除）。
    - ``cleanup()``：清空全部残留 worktree（运行结束兜底）。
    """

    def __init__(self, workspace_root: str | Path, *, branch_prefix: str = "acs") -> None:
        self.workspace_root = Path(workspace_root).expanduser().resolve()
        self.workspace_root.mkdir(parents=True, exist_ok=True)
        self.branch_prefix = branch_prefix
        self._sessions: dict[str, Any] = {}
        self._branches: dict[str, str] = {}
        self._paths: dict[str, Path] = {}
        self._merged: list[str] = []

    # ------------------------------------------------------------------
    # 仓库准备
    # ------------------------------------------------------------------

    def ensure_repo(self) -> dict[str, Any]:
        """确保工作区是带 HEAD 的 git 仓库；缺失时初始化（幂等）。"""
        if not (self.workspace_root / ".git").exists():
            init = _git(["init"], cwd=self.workspace_root)
            if not init["ok"]:
                return {"ok": False, "output": f"git init 失败：{init['output']}"}
        head = _git(["rev-parse", "--verify", "HEAD"], cwd=self.workspace_root)
        if head["exit_code"] != 0:
            _git(["add", "-A"], cwd=self.workspace_root)
            commit = _git(["commit", "--allow-empty", "-m", "acs: initial"], cwd=self.workspace_root)
            if not commit["ok"]:
                return {
                    "ok": False,
                    "output": f"初始提交失败：{commit['output']}（请先配置 git user.name/user.email）",
                }
        return {"ok": True, "output": "仓库就绪"}

    # ------------------------------------------------------------------
    # worktree 生命周期
    # ------------------------------------------------------------------

    def session_for(self, role_id: str, registry: Any | None = None) -> Any:
        """返回绑定到该角色 worktree 的 ToolSession（懒创建，幂等）。"""
        if role_id in self._sessions:
            return self._sessions[role_id]
        branch = f"{self.branch_prefix}/{role_id}"
        path = self.workspace_root / WORKTREE_SUBDIR / role_id
        if path.exists():
            # 恢复现场：已有 worktree 路径（中断/重跑）直接复用
            add = _git(["worktree", "list"], cwd=self.workspace_root)
            if f"{path} " not in add["output"]:
                add = _git(["worktree", "add", str(path), branch], cwd=self.workspace_root)
                if not add["ok"]:
                    raise WorktreeError(f"worktree add 失败（{branch}）：{add['output']}")
        else:
            add = _git(["worktree", "add", "-b", branch, str(path)], cwd=self.workspace_root)
            if not add["ok"]:
                raise WorktreeError(f"worktree add 失败（{branch}）：{add['output']}")
        from agent_cluster.tools import ToolSession, build_default_tools

        session = ToolSession(path, registry=registry if registry is not None else build_default_tools())
        self._sessions[role_id] = session
        self._branches[role_id] = branch
        self._paths[role_id] = path
        return session

    def merge_back(self, role_id: str, *, message: str | None = None) -> dict[str, Any]:
        """提交残留改动并 --no-ff 合并回主工作区；成功后移除 worktree。"""
        if role_id not in self._branches:
            return {"ok": False, "output": f"无 worktree 记录：{role_id}"}
        branch = self._branches[role_id]
        path = self._paths[role_id]
        # 1) 提交残留改动（幂等：无改动时跳过）
        _git(["add", "-A"], cwd=path)
        status = _git(["status", "--porcelain"], cwd=path)
        if status["output"].strip() and status["ok"]:
            _git(["commit", "-m", f"acs: {role_id} worktree sync"], cwd=path)
        # 2) 合并回主工作区
        merged = _git(["merge", "--no-ff", branch, "-m", message or f"merge {role_id} worktree"], cwd=self.workspace_root)
        if not merged["ok"]:
            return {
                "ok": False,
                "output": f"合并 {branch} 失败（保留 worktree 现场）：{merged['output']}",
                "branch": branch,
                "path": str(path),
            }
        # 3) 移除 worktree
        _git(["worktree", "remove", str(path), "--force"], cwd=self.workspace_root)
        self._sessions.pop(role_id, None)
        self._branches.pop(role_id, None)
        self._paths.pop(role_id, None)
        self._merged.append(branch)
        return {"ok": True, "output": f"已合并 {branch} 并移除 worktree", "branch": branch}

    def close(self, role_id: str, *, force: bool = True) -> dict[str, Any]:
        """放弃 worktree（不合并），强制移除。"""
        if role_id not in self._paths:
            return {"ok": False, "output": f"无 worktree 记录：{role_id}"}
        path = self._paths[role_id]
        args = ["worktree", "remove", str(path)]
        if force:
            args.append("--force")
        res = _git(args, cwd=self.workspace_root)
        if res["ok"]:
            branch = self._branches.pop(role_id, None)
            self._sessions.pop(role_id, None)
            self._paths.pop(role_id, None)
            if branch:
                _git(["branch", "-D", branch], cwd=self.workspace_root)
        return res

    def cleanup(self) -> list[dict[str, Any]]:
        """清空全部残留 worktree（兜底，不合并）。"""
        results: list[dict[str, Any]] = []
        for role_id in list(self._paths):
            results.append(self.close(role_id))
        return results

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------

    @property
    def active(self) -> list[str]:
        """当前活跃（未合并）的 role_id 列表。"""
        return sorted(self._branches)

    @property
    def merged(self) -> list[str]:
        """已合并的分支列表。"""
        return list(self._merged)
