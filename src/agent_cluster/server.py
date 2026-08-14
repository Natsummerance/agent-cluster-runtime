"""serve 子命令后端（v0.5 T12.3）：stdlib ThreadingHTTPServer + SSE + 全局索引。

架构：
- 每会话运行在独立线程（``asyncio.run(SessionDriver.run())``），HITL 经
  ``prompt_fn`` 桥接：会话进入门/危险工具/澄清时阻塞等待 API 提交答案。
- HTTP 事件循环线程处理 REST + SSE；SSE 长连接通过线程安全 ``queue.Queue``
  订阅会话事件日志（事件可重放：``?since=<seq>``）。
- 全局索引（``~/.agent-cluster/index.json``）登记项目与会话（各工作区存储，
  全局索引只做发现；工作区文件浏览限制在已登记的工作区根内）。

安全：默认仅监听 127.0.0.1；``--auth-token`` 开启后所有 /api 请求需
``X-Auth-Token`` 头。

前端契约（前端工作台按此实现，勿随意变更）：
- 列表/详情/操作端点见各 handler；响应统一 ``{"ok": true, "data": ...}``。
"""

from __future__ import annotations

import asyncio
import json
import logging
import mimetypes
import os
import queue
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from agent_cluster.evolution_integration import EvolutionBridge
from agent_cluster.memory import MemoryStore
from agent_cluster.models import TokenUsage
from agent_cluster.pricing import CostLedger
from agent_cluster.projects import (
    DEFAULT_FLOW,
    BudgetPoolExhaustedError,
    ForkConflictError,
    GatePolicyConfig,
    ProjectStore,
    SessionIndexEntry,
    fork_session,
    make_budget_pool_hook,
)
from agent_cluster.session import SessionDriver, SessionRecord
from agent_cluster.session_manager import SessionManager, SessionWorktree, WorktreeConflictError
from agent_cluster.tenancy import QuotaExceededError, TenantStore
from agent_cluster.calendar import OverlapError, ResourceCalendar
from agent_cluster.dependency_graph import CycleError, DependencyGraph
from agent_cluster.oauth_mcp import OAuthAuthorizationServer, OAuthError
from agent_cluster.worktree import WorktreeError
from agent_cluster.auth import TokenService
from agent_cluster.rbac import AUTHZ_SEAM, AuthzProvider, PermissionDenied, RbacStore
from agent_cluster.seam import SeamRegistry
from agent_cluster.ws import WebSocketPeer, handle_ws
from agent_cluster.trace import (
    JsonlExporter,
    Tracer,
    build_audit_package,
    compute_health,
    export_audit,
)

__all__ = [
    "SessionEventLog",
    "GlobalIndex",
    "ServerSession",
    "WorkbenchServer",
    "serve_main",
]

logger = logging.getLogger("agent-cluster")

INDEX_DIR = Path.home() / ".agent-cluster"
MAX_FILE_BYTES = 2 * 1024 * 1024  # 文件预览上限 2MB


class NotFoundError(Exception):
    """13.7 REST：资源不存在（404 not_found）。"""


class ConflictError(Exception):
    """13.7 REST：状态冲突（409 conflict）。"""


def _package_version() -> str:
    """读取安装版本（importlib.metadata，开发模式回退 0.6.4-dev）。"""
    try:
        from importlib import metadata

        return metadata.version("agent-cluster")
    except Exception:  # noqa: BLE001
        return "0.6.4-dev"


def _now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class SessionEventLog:
    """线程安全的事件日志（append + replay + 订阅队列）。"""

    def __init__(self) -> None:
        self._events: list[dict] = []
        self._subscribers: list[queue.Queue] = []
        self._lock = threading.Lock()

    def append(self, event: dict) -> int:
        with self._lock:
            seq = len(self._events)
            payload = dict(event)
            payload.setdefault("seq", seq)
            payload.setdefault("ts", _now_iso())
            self._events.append(payload)
            for sub in list(self._subscribers):
                sub.put(payload)
        return seq

    def replay(self, since: int = 0) -> list[dict]:
        with self._lock:
            return list(self._events[since:])

    def subscribe(self) -> queue.Queue:
        sub: queue.Queue = queue.Queue(maxsize=500)
        with self._lock:
            self._subscribers.append(sub)
        return sub

    def unsubscribe(self, sub: queue.Queue) -> None:
        with self._lock:
            if sub in self._subscribers:
                self._subscribers.remove(sub)


class GlobalIndex:
    """项目/会话全局索引（``~/.agent-cluster/index.json``）。"""

    def __init__(self) -> None:
        INDEX_DIR.mkdir(parents=True, exist_ok=True)
        self.path = INDEX_DIR / "index.json"
        self._lock = threading.Lock()
        self.projects: dict[str, dict] = {}
        self.sessions: dict[str, dict] = {}
        self._load()

    def _load(self) -> None:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self.projects = dict(data.get("projects") or {})
            self.sessions = dict(data.get("sessions") or {})
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            self.projects, self.sessions = {}, {}

    def save(self) -> None:
        with self._lock:
            tmp = self.path.with_suffix(".json.tmp")
            tmp.write_text(
                json.dumps({"projects": self.projects, "sessions": self.sessions}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            os.replace(tmp, self.path)

    def add_project(self, project_id: str, name: str, workspace: str) -> dict:
        entry = {
            "id": project_id,
            "name": name,
            "workspace": workspace,
            "status": "active",
            "created_at": _now_iso(),
        }
        with self._lock:
            self.projects[project_id] = entry
        self.save()
        return entry

    def add_session(self, session_id: str, project_id: str, workspace: str, goal: str, model: str) -> dict:
        entry = {
            "id": session_id,
            "project_id": project_id,
            "workspace": workspace,
            "goal": goal,
            "model": model,
            "status": "running",
            "created_at": _now_iso(),
        }
        with self._lock:
            self.sessions[session_id] = entry
        self.save()
        return entry

    def update_session(self, session_id: str, **fields: Any) -> None:
        with self._lock:
            if session_id in self.sessions:
                self.sessions[session_id].update(fields)
        self.save()


class ServerSession:
    """运行中的会话句柄：线程 + 事件日志 + HITL 桥接。"""

    def __init__(
        self,
        session_id: str,
        project_id: str,
        workspace: Path,
        spec: dict,
        *,
        worktree_path: Path | None = None,
        main_workspace: Path | None = None,
        store_root: Path | None = None,
        checkpoint_root: Path | None = None,
        budget_pool_hook: Any | None = None,
    ) -> None:
        self.session_id = session_id
        self.project_id = project_id
        self.workspace = workspace
        self.spec = spec
        self.log = SessionEventLog()
        self.status = "starting"
        # v0.6 T13.5 增量字段（§8.1：取消 / stdin / worktree / 合并冲突）
        self.cancel_event = threading.Event()
        self.stdin_queue: queue.Queue[str] = queue.Queue()
        self.worktree_path = worktree_path
        self.main_workspace = main_workspace
        self.merge_conflict = False
        self.store_root = store_root
        self.checkpoint_root = checkpoint_root
        self.budget_pool_hook = budget_pool_hook
        self.assignee = str(spec.get("assignee") or "")
        self.pending_hint: str = ""
        self.current_phase: str = ""
        self.current_node: str = ""
        self.error: str = ""
        self.thread: threading.Thread | None = None
        self.driver: SessionDriver | None = None
        self.exit_code: int = 0
        self._answer_queue: queue.Queue[str] = queue.Queue()
        self._change_queue: queue.Queue[str] = queue.Queue()
        self.token_summary: dict[str, Any] = {}
        self.delivery: dict | None = None
        self.tracer = Tracer(JsonlExporter(workspace))

    # ------------------------------------------------------------------
    # HITL 桥接（driver.prompt_fn）
    # ------------------------------------------------------------------

    def _prompt_fn(self, hint: str) -> str:
        """会话线程阻塞等待 API 提交的答案；需求变更由 driver 在下一个挂起点消费。"""
        self.pending_hint = hint
        self.status = "waiting_approval"
        self.log.append({"type": "approval.pending", "session_id": self.session_id, "payload": {"hint": hint}})
        span = self.tracer.start_span("approval.wait", kind="approval", hint=hint)
        try:
            while True:
                if self.cancel_event.is_set():
                    return "/abort"
                answer = None
                driver = self.driver
                if driver is not None:
                    # §11：挂起中注入的实时输入直接作答（消费即执行写入规则）
                    answer = driver._drain_stdin()
                if answer is None:
                    try:
                        answer = self._answer_queue.get(timeout=0.25)
                    except queue.Empty:
                        continue
                return answer
        finally:
            self.tracer.end_span(span)

    def _print_fn(self, text: str) -> None:
        self.log.append({"type": "log", "session_id": self.session_id, "payload": {"text": text}})

    def _event_printer(self, event: Any) -> None:
        payload = {
            "type": getattr(event, "type", "event"),
            "session_id": self.session_id,
            "payload": {
                "node_id": getattr(event, "payload", {}).get("node_id", ""),
                "text": getattr(event, "payload", {}).get("text", ""),
            },
        }
        # §9 自动评审：门被自动放行时不经过 prompt_fn，旧 pending_hint 会滞留；
        # 任何流程推进事件都表示上一次人工挂起已被消费，先清除再等下一个门提示。
        if self.status == "waiting_approval":
            self.pending_hint = ""
        self.log.append(payload)

    def submit_answer(self, text: str) -> None:
        self._answer_queue.put(text)

    def inject_change(self, text: str) -> None:
        """注入需求变更：写入 driver（下一个门/挂起点生效），并唤醒阻塞的 prompt。"""
        import time as _time

        deadline = _time.time() + 5.0
        while self.driver is None and _time.time() < deadline:
            _time.sleep(0.05)
        if self.driver is not None:
            self.driver.inject_change(text)
        else:
            self._change_queue.put(text)
        # 唤醒等待中的 prompt（返回 reject，driver 在下一个 decide_response 消费变更并返工）
        self._answer_queue.put("reject")

    # ------------------------------------------------------------------
    # 启动
    # ------------------------------------------------------------------

    def start(self) -> None:
        def _run() -> None:
            span = self.tracer.start_span(
                "session.run", kind="session", goal=str(self.spec.get("goal") or "")
            )
            try:
                driver = SessionDriver(
                    workspace=self.workspace,
                    goal=str(self.spec.get("goal") or ""),
                    flow=str(self.spec.get("flow") or DEFAULT_FLOW),
                    model=str(self.spec.get("model") or "codex"),
                    budget=self.spec.get("budget"),
                    yes=bool(self.spec.get("yes")),
                    deterministic=bool(self.spec.get("deterministic")),
                    resume=bool(self.spec.get("resume")),
                    print_fn=self._print_fn,
                    prompt_fn=self._prompt_fn,
                    event_printer=self._event_printer,
                    project_id=self.project_id,
                    session_id=self.session_id,
                    store_root=self.store_root,
                    checkpoint_root=self.checkpoint_root,
                    budget_pool_hook=self.budget_pool_hook,
                    gate_policy=self._load_gate_policy(),
                    cancel_event=self.cancel_event,
                )
                self.driver = driver
                # v0.6 T13.9：driver 就绪后绑定同一 stdin 队列（启动窗口期入队的行迁移过去）
                buffered_queue = self.stdin_queue
                self.stdin_queue = driver._stdin_queue
                while True:
                    try:
                        buffered = buffered_queue.get_nowait()
                    except queue.Empty:
                        break
                    driver._stdin_queue.put(buffered)
                self.status = "running"
                result = asyncio.run(driver.run())
                self.token_summary = result.token_summary or {}
                self.delivery = result.delivery
                self.exit_code = result.exit_code
                self.status = "completed"  # 运行至流程结束；exit_code 表达验收结果
            except Exception as exc:  # noqa: BLE001 —— 会话线程顶层错误出口
                self.status = "failed"
                self.error = str(exc)
                self.log.append(
                    {"type": "session.error", "session_id": self.session_id, "payload": {"error": str(exc)}}
                )
            finally:
                # §6.3 三异常态修复②：completed/failed/cancelled 三态必写 session.end 哨兵再收尾
                self._append_session_end()
                self.tracer.end_span(span)
                self._update_registry()
                self._finish_worktree()

        self.thread = threading.Thread(target=_run, name=f"session-{self.session_id}", daemon=True)
        self.thread.start()

    def _load_gate_policy(self) -> GatePolicyConfig | None:
        """§9：从项目 project.json 读取门策略（serve 入口接入自动 reviewer）。"""
        if self.store_root is None or not self.project_id:
            return None
        try:
            return ProjectStore(root=self.store_root).get(self.project_id).gate_policy
        except Exception:  # noqa: BLE001 —— 项目缺失/损坏时不启用自动评审
            return None

    def _append_session_end(self) -> None:
        """会话终态哨兵（§6.3）：completed/failed/cancelled 三态必写，SSE 据此关连接。"""
        if any(event.get("type") == "session.end" for event in self.log.replay()):
            return
        if self.status == "failed":
            end_status = "failed"
        elif self.exit_code in (2, 3):
            end_status = "cancelled"
        else:
            end_status = "completed"
        self.log.append(
            {
                "type": "session.end",
                "session_id": self.session_id,
                "payload": {"status": end_status, "exit_code": self.exit_code},
            }
        )

    def _update_registry(self) -> None:
        """把终态同步进项目会话注册表（任务面板数据源；失败不影响会话收尾）。"""
        if self.store_root is None or not self.project_id:
            return
        try:
            store = ProjectStore(root=self.store_root)
            record = store.session_store(self.project_id, self.session_id).record
            store.index_session(
                self.project_id,
                SessionIndexEntry(
                    session_id=self.session_id,
                    goal=record.goal or str(self.spec.get("goal") or ""),
                    status=record.status,
                    assignee=self.assignee,
                    workspace=str(self.workspace),
                    worktree=self.worktree_path is not None,
                ),
            )
        except Exception:  # noqa: BLE001 —— 注册表失败不影响会话收尾
            pass

    def _finish_worktree(self) -> None:
        """§8.2 收尾：完成（exit_code=0）→ merge_back；冲突保留现场；其余丢弃不合并。"""
        if self.worktree_path is None or self.main_workspace is None:
            return
        try:
            helper = SessionWorktree(self.main_workspace, self.session_id)
            if self.status == "completed" and self.exit_code == 0:
                merged = helper.merge_back()
                if not merged["ok"]:
                    self.merge_conflict = True
                    self.log.append(
                        {"type": "worktree.merge_conflict", "session_id": self.session_id, "payload": merged}
                    )
            else:
                helper.close()
        except Exception as exc:  # noqa: BLE001 —— 收尾失败不吞会话结果，审计落日志
            self.log.append(
                {"type": "worktree.finish_error", "session_id": self.session_id, "payload": {"error": str(exc)}}
            )

    def snapshot(self) -> dict[str, Any]:
        driver = self.driver
        ledger_summary: dict[str, Any] = {}
        phases: dict[str, Any] = {}
        transcript_count = 0
        gate_count = 0
        if driver is not None:
            record = driver.store.record
            ledger = record.token_ledger
            ledger_summary = {
                "budget": ledger.budget,
                "used": ledger.total(),
                "remaining": ledger.remaining(),
                "over_budget": ledger.over_budget(),
                "by_phase": ledger.by_phase(),
                "by_role": ledger.by_role(),
            }
            phases = {name: phase.model_dump() for name, phase in record.phases.items()}
            transcript_count = len(record.transcript)
            gate_count = len(record.gate_decisions)
            self.current_phase = driver.current_phase
            self.current_node = driver.current_node
        return {
            "session_id": self.session_id,
            "project_id": self.project_id,
            "workspace": str(self.workspace),
            "goal": self.spec.get("goal", ""),
            "model": self.spec.get("model", "codex"),
            "status": self.status,
            "pending_hint": self.pending_hint,
            "current_phase": self.current_phase,
            "current_node": self.current_node,
            "token": ledger_summary,
            "phases": phases,
            "transcript_count": transcript_count,
            "gate_count": gate_count,
            "health": self.health_snapshot(),
            "error": self.error,
            "exit_code": self.exit_code,
            "worktree": self.worktree_path is not None,
            "merge_conflict": self.merge_conflict,
            "assignee": self.assignee,
        }


    # ------------------------------------------------------------------
    # 可观测性：审计数据 + 健康指标
    # ------------------------------------------------------------------

    def audit_data(self) -> dict[str, Any]:
        """收集审计数据（事件/审批/token/变更/span/成本），供 GET 与导出。"""
        driver = self.driver
        events = self.log.replay()
        approvals: list[Any] = []
        token_summary: dict[str, Any] = {}
        changes: list[Any] = []
        spans = self.tracer.spans()
        cost: dict[str, Any] = {"by_model": {}, "total": 0.0, "currency": "USD"}
        if driver is not None:
            record = driver.store.record
            approvals = [item.model_dump() for item in record.gate_decisions]
            token_summary = record.token_ledger.summary()
            changes = [item.model_dump() for item in driver.change_history.list()]
            ledger = CostLedger()
            for entry in record.token_ledger.entries:
                ledger.record(
                    TokenUsage(
                        model=entry.model,
                        prompt_tokens=entry.prompt_tokens,
                        completion_tokens=entry.completion_tokens,
                        total_tokens=entry.total_tokens,
                        estimated=entry.estimated,
                    )
                )
            cost = ledger.summary()
        return {
            "session_id": self.session_id,
            "goal": str(self.spec.get("goal") or ""),
            "events": events,
            "approvals": approvals,
            "token_summary": token_summary,
            "changes": changes,
            "spans": [span.to_dict() for span in spans],
            "cost": cost,
        }

    def health_snapshot(self) -> dict[str, Any]:
        """四类健康指标（eval 趋势 / token 成本 / 预估准确率 / 返工率）。"""
        driver = self.driver
        empty = {
            "eval_pass_rate_trend": None,
            "token_cost": {"used": 0, "budget": 0, "cost": 0.0, "currency": "USD"},
            "estimate_accuracy": None,
            "rework_rate": 0.0,
        }
        if driver is None:
            return empty
        record = driver.store.record
        ledger = CostLedger()
        for entry in record.token_ledger.entries:
            ledger.record(
                TokenUsage(
                    model=entry.model,
                    prompt_tokens=entry.prompt_tokens,
                    completion_tokens=entry.completion_tokens,
                    total_tokens=entry.total_tokens,
                    estimated=entry.estimated,
                )
            )
        return compute_health(
            token_ledger=record.token_ledger,
            gate_decisions=record.gate_decisions,
            cost=ledger.summary(),
        )


class WorkbenchServer:
    """serve 后端：全局索引 + 会话注册表 + HTTP 服务。"""

    def __init__(
        self,
        *,
        host: str = "127.0.0.1",
        port: int = 8765,
        auth_token: str = "",
        auth_provider: Any = None,
        auth_secret: str = "",
        oauth_server: Any = None,
        oauth_issuer: str = "",
        plugins_dir: list[str] | None = None,
        mcp_servers: list[str] | None = None,
        mcp_http_servers: list[str] | None = None,
        heartbeat_seconds: float = 15.0,
    ) -> None:
        self.host = host
        self.port = port
        self.auth_token = auth_token
        self.heartbeat_seconds = heartbeat_seconds
        self.auth_provider = auth_provider
        self.auth_enabled = auth_provider is not None
        if self.auth_enabled and not auth_secret:
            raise ValueError("启用认证必须提供 auth_secret（--auth-secret 或 AGENT_CLUSTER_AUTH_SECRET）")
        self.tokens = TokenService(secret=auth_secret) if self.auth_enabled else None
        self.index = GlobalIndex()
        self._lock = threading.Lock()
        # v0.6 T13.5：会话注册表委托 SessionManager（ProjectStore 与全局索引同根，测试经 INDEX_DIR 隔离）
        self._project_store = ProjectStore(root=INDEX_DIR)
        self.manager = SessionManager(self._project_store)
        self._plugins_dir = list(plugins_dir or [])
        self.mcp_servers = list(mcp_servers or [])
        self.mcp_http_servers = list(mcp_http_servers or [])
        self._plugin_manager: Any = self._build_plugin_manager()
        self._skills_loader: Any = None
        self.rbac = RbacStore()
        self._seams = SeamRegistry()
        self._authz_registration = self._seams.register(AUTHZ_SEAM, AuthzProvider(self.rbac))
        self.tenants = TenantStore(root=INDEX_DIR)
        self.calendar = ResourceCalendar(root=INDEX_DIR)
        self.dependencies = DependencyGraph(root=INDEX_DIR)
        base_url = oauth_issuer or f"http://{host}:{port}"
        self.oauth = oauth_server or OAuthAuthorizationServer(
            issuer=base_url,
            resource=base_url,
            authorization_servers=[base_url],
            token_service=self.tokens,
        )
        if oauth_server is None:
            # 默认工作台客户端：OAuth MCP 端点开箱可用（可再注册更多客户端）
            self.oauth.register_client(
                "agent-cluster",
                ["http://127.0.0.1/callback"],
                name="agent-cluster 默认工作台客户端",
            )

    @property
    def sessions(self) -> dict[str, ServerSession]:
        """v0.5 兼容出口：委托 SessionManager 注册表（公开行为不变）。"""
        return self.manager.sessions

    def _build_plugin_manager(self) -> Any:
        """扫描插件目录（失败返回 None，不阻断 serve 启动）。"""
        try:
            from agent_cluster.plugins import PluginManager, default_plugin_search_dirs

            search_dirs = list(self._plugins_dir) + default_plugin_search_dirs()
            if not search_dirs:
                return None
            manager = PluginManager(search_dirs=search_dirs)
            manager.scan()
            manager.load_skills()
            return manager
        except Exception:  # noqa: BLE001 —— 插件扫描失败不影响 serve 主流程
            return None

    def _skills(self) -> list[Any]:
        """技能列表：插件技能 + 默认 Codex 技能目录（~/.codex/skills）。"""
        from agent_cluster.skills import SkillLoader

        result: list[Any] = []
        seen: set[str] = set()
        if self._plugin_manager is not None:
            try:
                for skill in self._plugin_manager.load_skills():
                    if skill.name not in seen:
                        seen.add(skill.name)
                        result.append(skill)
            except Exception:  # noqa: BLE001
                pass
        roots = [Path.home() / ".codex" / "skills", Path.home() / ".claude" / "skills"]
        loader = SkillLoader()
        for root in roots:
            if not root.is_dir():
                continue
            try:
                for skill in loader.list_skills(str(root)):
                    if skill.name not in seen:
                        seen.add(skill.name)
                        result.append(skill)
            except Exception:  # noqa: BLE001
                continue
        return result

    # ------------------------------------------------------------------
    # 项目/会话生命周期
    # ------------------------------------------------------------------

    def create_project(self, name: str, workspace: str, tenant_id: str | None = None) -> dict:
        project_id = uuid.uuid4().hex[:12]
        root = Path(workspace).expanduser().resolve()
        root.mkdir(parents=True, exist_ok=True)
        if tenant_id:
            self.tenants.get_tenant(tenant_id)
            self.tenants.ensure_quota(tenant_id, "projects")
            store = self.tenants.namespaced_project_store(tenant_id)
        else:
            store = self._project_store
        entry = self.index.add_project(project_id, name, str(root))
        # v0.6 T13.5：同 pid 双写 ProjectStore（SessionManager 数据源；失败不阻断 v0.5 行为）
        try:
            metadata = {"tenant_id": tenant_id} if tenant_id else None
            store.create_project(name=name, workspace=root, project_id=project_id, metadata=metadata)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[serve] ProjectStore 双写项目失败：%s", exc)
        if tenant_id:
            entry = dict(entry)
            entry["tenant_id"] = tenant_id
        return entry

    def list_projects(self) -> list[dict]:
        entries: list[dict] = []
        for entry in self.index.projects.values():
            item = dict(entry)
            pid = item["id"]
            try:
                project = self._project_store.get(pid)
                item["description"] = project.description
                item["metadata"] = project.metadata
                item["budget_pool"] = self._project_store.budget_status(pid)
                item["session_count"] = len(project.sessions)
                item["active_sessions"] = self.manager.running_in(pid)
                item["dashboard"] = self._dashboard(pid)
            except KeyError:
                item.setdefault("description", "")
                item.setdefault("metadata", {})
                item.setdefault("budget_pool", {})
                item.setdefault("session_count", 0)
                item.setdefault("active_sessions", 0)
                item.setdefault("dashboard", {})
            entries.append(item)
        return entries

    def start_session(self, project_id: str, spec: dict) -> dict:
        spec.setdefault("goal", "")
        session_id = str(spec.get("session_id") or "")
        if session_id:
            return self._resume_session(project_id, session_id, spec)
        if not spec.get("goal"):
            raise ValueError("goal 不能为空")
        # v0.6 T13.5：委托 SessionManager（预算 → 并发判定 → worktree → 线程启动）
        server_session = self.manager.start(project_id, spec)
        self.index.add_session(
            server_session.session_id,
            project_id,
            str(server_session.workspace),
            spec["goal"],
            str(spec.get("model") or "codex"),
        )
        self._index_live_session(project_id, server_session)
        return {
            "session_id": server_session.session_id,
            "project_id": project_id,
            "workspace": str(server_session.workspace),
            "worktree": server_session.worktree_path is not None,
            "resumed": False,
        }

    def _index_live_session(self, project_id: str, server_session: ServerSession) -> None:
        """新会话登记进项目会话注册表（任务面板数据源；失败不阻断启动）。"""
        try:
            self._project_store.index_session(
                project_id,
                SessionIndexEntry(
                    session_id=server_session.session_id,
                    goal=str(server_session.spec.get("goal") or ""),
                    status="active",
                    assignee=server_session.assignee,
                    workspace=str(server_session.workspace),
                    worktree=server_session.worktree_path is not None,
                    metadata=dict(server_session.spec.get("metadata") or {}),
                ),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("[serve] 会话注册表登记失败：%s", exc)

    def _resume_session(self, project_id: str, session_id: str, spec: dict) -> dict:
        """§6.2 恢复语义：spec.session_id 且会话在项目内、status=active → resume=True 复用 thread。

        惰性迁移兜底（§4.1）：项目会话目录缺失但项目任一工作区仍存未迁移
        v0.5 session.json → 先 migrate_legacy_session 再决定新建/恢复。
        """
        try:
            project = self._project_store.get(project_id)
        except KeyError:
            raise NotFoundError(f"项目不存在：{project_id}") from None
        target = self._project_store.session_dir(project_id, session_id) / "session.json"
        if not target.is_file():
            migrated = False
            for workspace in project.workspaces:
                legacy = Path(workspace) / ".agent-cluster" / "session.json"
                if legacy.is_file():
                    if self._project_store.migrate_legacy_session(project_id, workspace) is not None:
                        migrated = True
                        break
            if not migrated:
                raise NotFoundError(f"会话不存在：{session_id}")
        record = self._project_store.session_store(project_id, session_id).record
        if record.status != "active":
            raise ConflictError(f"会话已终态（status={record.status}），不能恢复启动")
        existing = self.manager.sessions.get(session_id)
        if existing is not None and existing.status in ("starting", "running", "waiting_approval"):
            raise ConflictError(f"会话已在运行（status={existing.status}）")
        main_workspace = Path(project.workspaces[0]).expanduser().resolve()
        workspace = Path(record.workspace).expanduser().resolve() if record.workspace else main_workspace
        # fork 产物工作区（worktrees/fork/<sid>）暂不自动 merge-back（v0.7 PPM 统一处理）
        fork_worktree = "worktrees" in workspace.parts and "fork" in workspace.parts
        worktree_path = None if fork_worktree else (workspace if workspace != main_workspace else None)
        resume_spec = dict(spec)
        resume_spec["resume"] = True
        checkpoint_root = self._project_store.session_dir(project_id, session_id) / "checkpoints"
        server_session = ServerSession(
            session_id,
            project_id,
            workspace,
            resume_spec,
            worktree_path=worktree_path,
            main_workspace=main_workspace if worktree_path is not None else None,
            store_root=self._project_store.root,
            checkpoint_root=checkpoint_root,
            budget_pool_hook=make_budget_pool_hook(self._project_store, self.manager._emit),
        )
        with self.manager._lock:
            self.manager.sessions[session_id] = server_session
        server_session.log.append(
            {
                "type": "session.start",
                "session_id": session_id,
                "payload": {"goal": str(spec.get("goal") or record.goal or ""), "resumed": True},
            }
        )
        server_session.start()
        self.index.add_session(
            session_id,
            project_id,
            str(workspace),
            str(spec.get("goal") or record.goal or ""),
            str(spec.get("model") or "codex"),
        )
        self._index_live_session(project_id, server_session)
        return {
            "session_id": session_id,
            "project_id": project_id,
            "workspace": str(workspace),
            "worktree": worktree_path is not None,
            "resumed": True,
        }

    def project_detail(self, project_id: str) -> dict:
        project = self._project_store.get(project_id)
        return {
            "project_id": project.project_id,
            "name": project.name,
            "description": project.description,
            "workspaces": project.workspaces,
            "default_flow": project.default_flow,
            "status": project.status,
            "budget_pool": project.budget_pool.model_dump(mode="json"),
            "gate_policy": project.gate_policy.model_dump(),
            "sessions": [entry.model_dump(mode="json") for entry in project.sessions],
            "metadata": project.metadata,
            "created_at": project.created_at.isoformat(),
            "updated_at": project.updated_at.isoformat(),
        }

    def task_entries(self, project_id: str) -> list[dict]:
        if project_id not in self.index.projects:
            raise KeyError(f"项目不存在：{project_id}")
        try:
            project = self._project_store.get(project_id)
        except KeyError:
            return []  # 旧项目（索引有、未双写）：无任务数据
        live = {sid: session for sid, session in self.manager.sessions.items() if session.project_id == project_id}
        entries: list[dict] = []
        for entry in project.sessions:
            item = entry.model_dump(mode="json")
            session = live.get(entry.session_id)
            if session is not None:
                item["runtime_status"] = session.status
                item["current_phase"] = session.current_phase
                item["current_node"] = session.current_node
            entries.append(item)
        return entries

    def _project_session_records(self, project_id: str) -> list[tuple[str, SessionRecord]]:
        sessions_dir = self._project_store.root / "projects" / project_id / "sessions"
        records: list[tuple[str, SessionRecord]] = []
        if not sessions_dir.is_dir():
            return records
        for session_dir in sorted(sessions_dir.iterdir()):
            if not session_dir.is_dir():
                continue
            path = session_dir / "session.json"
            try:
                record = SessionRecord.model_validate(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, json.JSONDecodeError, UnicodeDecodeError, ValueError):
                continue
            records.append((session_dir.name, record))
        return records

    def _dashboard(self, project_id: str) -> dict[str, Any]:
        """§10.1 三轴仪表盘（唯一计算式：cost/progress/health；状态枚举 ok|warn|critical）。"""
        project = self._project_store.get(project_id)
        pool = project.budget_pool
        used = self._project_store.aggregate_used_tokens(project_id)
        limit = pool.hard_limit_tokens
        ratio = (used / limit) if limit > 0 else 0.0
        cost_score = max(0.0, 1.0 - ratio)
        if limit > 0 and ratio >= 1:
            cost_status = "critical"
        elif limit > 0 and ratio >= pool.warn_ratio:
            cost_status = "warn"
        else:
            cost_status = "ok"
        cost_ledger = CostLedger()
        records = self._project_session_records(project_id)
        for _, record in records:
            for entry in record.token_ledger.entries:
                cost_ledger.record(
                    TokenUsage(
                        model=entry.model,
                        prompt_tokens=entry.prompt_tokens,
                        completion_tokens=entry.completion_tokens,
                        total_tokens=entry.total_tokens,
                        estimated=entry.estimated,
                    )
                )
        cost = cost_ledger.summary()
        phases_total = 0
        phases_done = 0
        progress_values: list[float] = []
        health_sessions: dict[str, Any] = {}
        health_values: list[float] = []
        any_critical = False
        for session_id, record in records:
            phases = record.phases
            if phases:
                done = sum(1 for phase in phases.values() if phase.status == "done")
                ratio_phase = done / len(phases)
            else:
                ratio_phase = 1.0 if record.status == "completed" else 0.0
            progress_values.append(ratio_phase)
            phases_total += len(phases)
            phases_done += sum(1 for phase in phases.values() if phase.status == "done")
            health = compute_health(token_ledger=record.token_ledger, gate_decisions=record.gate_decisions, cost=cost)
            health_sessions[session_id] = health
            if record.token_ledger.over_budget():
                any_critical = True
            if float(health.get("rework_rate") or 0.0) >= 0.5:
                any_critical = True
            sub_scores: list[float] = []
            trend = health.get("eval_pass_rate_trend") or {}
            latest = trend.get("latest") if isinstance(trend, dict) else None
            if isinstance(latest, (int, float)):
                sub_scores.append(min(1.0, max(0.0, float(latest))))
            token_cost = health.get("token_cost") or {}
            budget_value = int(token_cost.get("budget", 0) or 0)
            if budget_value > 0:
                sub_scores.append(max(0.0, 1.0 - int(token_cost.get("used", 0) or 0) / budget_value))
            accuracy = health.get("estimate_accuracy")
            if isinstance(accuracy, (int, float)):
                sub_scores.append(min(1.0, max(0.0, float(accuracy))))
            sub_scores.append(max(0.0, 1.0 - float(health.get("rework_rate") or 0.0)))
            health_values.append(sum(sub_scores) / len(sub_scores))
        progress_score = round(sum(progress_values) / len(progress_values), 4) if progress_values else 0.0
        if progress_values and progress_score >= 0.7:
            progress_status = "ok"
        elif progress_values and progress_score >= 0.4:
            progress_status = "warn"
        else:
            progress_status = "critical" if progress_values else "ok"
        health_score = round(sum(health_values) / len(health_values), 4) if health_values else 0.0
        if any_critical or (health_values and health_score < 0.4):
            health_status = "critical"
        elif health_values and health_score < 0.7:
            health_status = "warn"
        else:
            health_status = "ok"
        return {
            "cost": {
                "used": used,
                "limit": limit,
                "ratio": round(ratio, 4),
                "score": round(cost_score, 4),
                "status": cost_status,
                "estimated_usd": round(float(cost.get("total", 0.0) or 0.0), 6),
            },
            "progress": {
                "score": progress_score,
                "status": progress_status,
                "phases": {"total": phases_total, "done": phases_done},
            },
            "health": {"score": health_score, "status": health_status, "sessions": health_sessions},
            "updated_at": _now_iso(),
        }

    def fork_session(self, session_id: str, spec: dict) -> dict:
        """§7：终态会话派生；产物为「待启动」会话（记录 status=active、线程为空，
        后续经 spec.session_id 恢复启动）。"""
        project_id = str(spec.get("project_id") or "")
        record = fork_session(
            self._project_store,
            source_session_id=session_id,
            goal=spec.get("goal"),
            project_id=project_id or None,
            worktree=bool(spec.get("worktree", True)),
            budget=spec.get("budget"),
            emit=self.manager._emit,
        )
        source = self.manager.sessions.get(session_id)
        if source is not None:
            source.log.append(
                {
                    "type": "session.forked",
                    "session_id": session_id,
                    "payload": {"child_session_id": record.session_id},
                }
            )
        dormant = ServerSession(
            record.session_id,
            record.project_id,
            Path(record.workspace),
            {
                "goal": record.goal,
                "model": record.model,
                "parent_session_id": record.parent_session_id,
            },
            worktree_path=None,
            main_workspace=None,
            store_root=self._project_store.root,
            checkpoint_root=self._project_store.session_dir(record.project_id, record.session_id) / "checkpoints",
        )
        dormant.status = "dormant"
        dormant.assignee = str(spec.get("assignee") or "")
        dormant.log.append(
            {
                "type": "session.start",
                "session_id": record.session_id,
                "payload": {"goal": record.goal, "forked_from": session_id},
            }
        )
        with self.manager._lock:
            self.manager.sessions[record.session_id] = dormant
        self.index.add_session(record.session_id, record.project_id, record.workspace, record.goal, record.model)
        return {
            "session_id": record.session_id,
            "parent_session_id": record.parent_session_id,
            "fork_depth": record.fork_depth,
        }

    def get_session(self, session_id: str) -> ServerSession:
        return self.manager.get(session_id)

    def project_workspace(self, project_id: str) -> Path:
        project = self.index.projects.get(project_id)
        if not project:
            raise KeyError(f"项目不存在：{project_id}")
        return Path(project["workspace"]).expanduser().resolve()

    def memory_store(self, project_id: str | None = None) -> MemoryStore:
        if project_id:
            return MemoryStore(self.project_workspace(project_id))
        return MemoryStore(INDEX_DIR)

    def evolution_bridge(self, project_id: str | None = None) -> EvolutionBridge:
        """进化集成桥（全局或项目工作区）。"""
        if project_id:
            return EvolutionBridge(self.project_workspace(project_id))
        return EvolutionBridge(INDEX_DIR)
    def memory_store(self, project_id: str | None = None) -> MemoryStore:
        if project_id:
            return MemoryStore(self.project_workspace(project_id))
        return MemoryStore(INDEX_DIR)

    def metrics_snapshot(self) -> dict[str, Any]:
        total_tokens = 0
        total_cost = 0.0
        sessions_info: list[dict] = []
        health: list[dict] = []
        for session in self.sessions.values():
            token = session.token_summary or {}
            total_tokens += int(token.get("used", 0) or 0)
            data = session.audit_data()
            total_cost += float((data["cost"] or {}).get("total", 0.0) or 0.0)
            sessions_info.append(
                {"session_id": session.session_id, "status": session.status, "goal": session.spec.get("goal", "")}
            )
            health.append(
                {"session_id": session.session_id, "status": session.status, **session.health_snapshot()}
            )
        return {
            "sessions": sessions_info,
            "active": sum(1 for s in self.sessions.values() if s.status in ("running", "waiting_approval")),
            "total_tokens": total_tokens,
            "total_cost": round(total_cost, 6),
            "health": health,
            "updated_at": _now_iso(),
        }


def _send_json(handler: BaseHTTPRequestHandler, status: int, payload: dict) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(body)


def _send_error(handler: BaseHTTPRequestHandler, status: int, message: str, code: str = "") -> None:
    """§6.1 错误信封：code 可选；旧端点不传 code 时输出结构与 v0.5 相同。"""
    payload: dict = {"ok": False, "error": message}
    if code:
        payload["code"] = code
    _send_json(handler, status, payload)


def _send_redirect(handler: BaseHTTPRequestHandler, location: str) -> None:
    """OAuth authorize 重定向（RFC 6749 §4.1.2）。"""
    handler.send_response(302)
    handler.send_header("Location", location)
    handler.send_header("Content-Length", "0")
    handler.end_headers()


class WorkbenchHandler(BaseHTTPRequestHandler):
    # §6.3 三异常态修复③：客户端在响应收尾（最终 flush）断开时静默退出，
    # 不发脏 traceback（Windows 常见 ConnectionResetError(10054)/ConnectionAbortedError(10053)）。
    def handle_one_request(self) -> None:  # noqa: N802
        try:
            super().handle_one_request()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            self.close_connection = True

    """HTTP 路由：REST + SSE。"""

    workbench: WorkbenchServer  # type: ignore[assignment]

    # ------------------------------------------------------------------
    # 基础设施
    # ------------------------------------------------------------------

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        # 静默访问日志（面板轮询会刷屏）
        return

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return {}

    def _read_form(self) -> dict[str, str]:
        """读取 OAuth 端点请求体：application/x-www-form-urlencoded（兼容 JSON）。"""
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        raw = self.rfile.read(length).decode("utf-8", errors="replace")
        content_type = (self.headers.get("Content-Type") or "").lower()
        if "application/json" in content_type:
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                return {}
            if not isinstance(data, dict):
                return {}
            return {str(key): str(value) for key, value in data.items()}
        return {key: values[0] for key, values in parse_qs(raw).items()}

    def _check_auth(self) -> bool:
        workbench = self.server.workbench
        if workbench.auth_enabled:
            scheme, _, token = self.headers.get("Authorization", "").partition(" ")
            if scheme.lower() == "bearer" and token:
                user_id = workbench.tokens.verify_access(token)
                if user_id:
                    self._auth_user_id = user_id
                    return True
            return False
        token = workbench.auth_token
        if not token:
            return True
        return self.headers.get("X-Auth-Token") == token

    def _path_parts(self) -> list[str]:
        parsed = urlparse(self.path)
        return [unquote(part) for part in parsed.path.split("/") if part]

    def _query(self) -> dict[str, str]:
        parsed = urlparse(self.path)
        return {key: values[0] for key, values in parse_qs(parsed.query).items()}

    # ------------------------------------------------------------------
    # 入口
    # ------------------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802
        parts = self._path_parts()
        if parts[:3] == ["api", "v1", "ws"]:
            return self._handle_ws_upgrade()
        # OAuth MCP（RFC 8844）端点：公开，不参与 API 认证
        if parts[:2] == [".well-known", "oauth-protected-resource"]:
            return self._handle_oauth_well_known()
        if parts[:2] == [".well-known", "oauth-authorization-server"]:
            return self._handle_oauth_server_metadata()
        if parts[:2] == ["oauth", "authorize"]:
            return self._handle_oauth_authorize()
        if not self._check_auth():
            _send_error(self, 401, "未授权（需要 X-Auth-Token）", "not_authorized")
            return
        try:
            if parts[:3] == ["api", "v1", "status"]:
                return self._handle_status()
            if parts[:3] == ["api", "v1", "doctor"]:
                return self._handle_doctor()
            if parts[:3] == ["api", "v1", "projects"] and len(parts) == 3:
                return self._handle_list_projects()
            if parts[:3] == ["api", "v1", "projects"] and len(parts) == 4:
                return self._handle_project_detail(parts[3])
            if len(parts) == 5 and parts[:3] == ["api", "v1", "projects"]:
                if parts[4] == "sessions":
                    return self._handle_project_sessions(parts[3])
                if parts[4] == "budget":
                    return self._handle_budget(parts[3])
                if parts[4] == "dashboard":
                    return self._handle_dashboard(parts[3])
                if parts[4] == "tasks":
                    return self._handle_tasks(parts[3])
                if parts[4] == "memory":
                    return self._handle_memory(parts[3])
            if (
                len(parts) == 6
                and parts[:3] == ["api", "v1", "projects"]
                and parts[4] == "workspace"
                and parts[5] in ("tree", "file")
            ):
                if parts[5] == "tree":
                    return self._handle_workspace_tree(parts[3])
                return self._handle_workspace_file(parts[3])
            if parts[:3] == ["api", "v1", "evolution"] and len(parts) == 4 and parts[3] == "proposals":
                return self._handle_evolution_proposals()
            if (
                parts[:3] == ["api", "v1", "sessions"]
                and len(parts) == 5
                and parts[4] == "events"
            ):
                return self._handle_sse(parts[3])
            if parts[:3] == ["api", "v1", "sessions"] and len(parts) == 4 and parts[3] != "events":
                return self._handle_session_detail(parts[3])
            if parts[:3] == ["api", "v1", "sessions"] and len(parts) == 5 and parts[4] == "changes":
                return self._handle_changes(parts[3])
            if parts[:3] == ["api", "v1", "sessions"] and len(parts) == 5 and parts[4] == "audit":
                return self._handle_audit(parts[3])
            if (
                parts[:3] == ["api", "v1", "sessions"]
                and len(parts) == 6
                and parts[4] == "audit"
                and parts[5] == "export"
            ):
                return self._handle_audit_export(parts[3])
            if parts[:3] == ["api", "v1", "auth"] and len(parts) == 4 and parts[3] == "me":
                return self._handle_auth_me()
            if parts[:3] == ["api", "v1", "roles"]:
                return self._handle_roles()
            if parts[:3] == ["api", "v1", "users"] and len(parts) == 3:
                return self._handle_users()
            if parts[:3] == ["api", "v1", "users"] and len(parts) == 4:
                return self._handle_user_detail(parts[3])
            if parts[:3] == ["api", "v1", "teams"] and len(parts) == 3:
                return self._handle_teams()
            if parts[:3] == ["api", "v1", "tenants"] and len(parts) == 3:
                return self._handle_tenants()
            if (
                parts[:3] == ["api", "v1", "tenants"]
                and len(parts) == 5
                and parts[4] == "usage"
            ):
                return self._handle_tenant_usage(parts[3])
            if parts[:3] == ["api", "v1", "calendar"] and len(parts) == 3:
                return self._handle_calendar()
            if parts[:3] == ["api", "v1", "dependencies"] and len(parts) == 3:
                return self._handle_dependencies()
            if parts[:3] == ["api", "v1", "dependencies"] and len(parts) == 4 and parts[3] == "impact":
                return self._handle_dependency_impact()
            if parts[:3] == ["api", "v1", "metrics"]:
                return self._handle_metrics()
            if parts[:3] == ["api", "v1", "plugins"]:
                return self._handle_plugins()
            if parts[:3] == ["api", "v1", "skills"]:
                return self._handle_skills()
            if parts[:3] == ["api", "v1", "mcp"]:
                return self._handle_mcp()
            _send_json(self, 404, {"ok": False, "error": f"未知路由：{self.path}"})
        except KeyError as exc:
            _send_json(self, 404, {"ok": False, "error": str(exc)})
        except PermissionDenied as exc:
            _send_error(self, 403, str(exc), "permission_denied")
        except Exception as exc:  # noqa: BLE001 —— 路由顶层统一错误出口
            _send_json(self, 500, {"ok": False, "error": str(exc)})

    def do_OPTIONS(self) -> None:  # noqa: N802
        """CORS 预检（浏览器 dev 5173 / 桌面 file:// 跨域 8765 必须放行）。"""
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, PATCH, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "X-Auth-Token, Content-Type")
        self.send_header("Access-Control-Max-Age", "86400")
        self.end_headers()
    def do_POST(self) -> None:  # noqa: N802
        parts = self._path_parts()
        if parts[:3] == ["api", "v1", "auth"] and len(parts) == 4 and parts[3] in ("login", "refresh"):
            if parts[3] == "login":
                return self._handle_auth_login(self._read_json())
            return self._handle_auth_refresh(self._read_json())
        # OAuth MCP（RFC 8844）端点：公开，不参与 API 认证
        if parts[:2] == ["oauth", "authorize"]:
            return self._handle_oauth_authorize()
        if parts[:2] == ["oauth", "token"]:
            return self._handle_oauth_token()
        if not self._check_auth():
            _send_error(self, 401, "未授权（需要 X-Auth-Token）", "not_authorized")
            return
        try:
            if parts[:3] == ["api", "v1", "doctor"] and len(parts) == 4 and parts[3] == "fix-docker":
                return self._handle_doctor_fix_docker()
            if parts[:3] == ["api", "v1", "projects"] and len(parts) == 3:
                return self._handle_create_project(self._read_json())
            if len(parts) == 5 and parts[:3] == ["api", "v1", "projects"]:
                if parts[4] == "sessions":
                    return self._handle_start_session(parts[3], self._read_json())
                if parts[4] == "workspaces":
                    return self._handle_workspaces(parts[3], self._read_json())
            if (
                len(parts) == 6
                and parts[:3] == ["api", "v1", "projects"]
                and parts[4] == "budget"
                and parts[5] == "unlock"
            ):
                return self._handle_budget_unlock(parts[3], self._read_json())
            if (
                len(parts) == 8
                and parts[:3] == ["api", "v1", "projects"]
                and parts[4] == "budget"
                and parts[5] == "unlock"
                and parts[7] in ("approve", "deny")
            ):
                return self._handle_budget_unlock_decide(parts[3], parts[6], parts[7], self._read_json())
            if (
                parts[:3] == ["api", "v1", "sessions"]
                and len(parts) == 5
                and parts[4] in ("approve", "reject", "edit", "response")
            ):
                sid, action = parts[3], parts[4]
                if action == "approve":
                    return self._handle_answer(sid, "accept")
                if action == "reject":
                    return self._handle_answer(sid, "reject")
                if action in ("edit", "response"):
                    return self._handle_answer(sid, f"{action} {self._read_json().get('text', '')}")
            if parts[:3] == ["api", "v1", "sessions"] and len(parts) == 5 and parts[4] == "fork":
                return self._handle_fork(parts[3], self._read_json())
            if parts[:3] == ["api", "v1", "sessions"] and len(parts) == 5 and parts[4] == "stdin":
                return self._handle_stdin(parts[3], self._read_json())
            if parts[:3] == ["api", "v1", "sessions"] and len(parts) == 5 and parts[4] == "cancel":
                return self._handle_cancel(parts[3])
            if (
                parts[:3] == ["api", "v1", "sessions"]
                and len(parts) == 5
                and parts[4] == "interrupt"
            ):
                return self._handle_interrupt(parts[3], self._read_json())
            if (
                parts[:3] == ["api", "v1", "sessions"]
                and len(parts) == 5
                and parts[4] == "rollback"
            ):
                return self._handle_rollback(parts[3], self._read_json())
            if (
                parts[:3] == ["api", "v1", "sessions"]
                and len(parts) == 6
                and parts[4] == "audit"
                and parts[5] == "export"
            ):
                return self._handle_audit_export(parts[3])
            if (
                len(parts) == 5
                and parts[:3] == ["api", "v1", "memory"]
                and parts[4] == "promote"
            ):
                return self._handle_memory_promote(parts[3])
            if parts[:3] == ["api", "v1", "evolution"] and len(parts) == 4 and parts[3] == "proposals":
                return self._handle_evolution_proposals()
            if (
                parts[:3] == ["api", "v1", "evolution"]
                and len(parts) == 6
                and parts[3] == "proposals"
                and parts[5] in ("apply", "rollback")
            ):
                return self._handle_evolution_action(parts[4], parts[5], self._read_json())
            if parts[:3] == ["api", "v1", "evolution"] and len(parts) == 4 and parts[3] == "generate":
                return self._handle_evolution_generate(self._read_json())
            if parts[:3] == ["api", "v1", "evolution"] and len(parts) == 4 and parts[3] == "retro":
                return self._handle_evolution_retro(self._read_json())
            if parts[:3] == ["api", "v1", "users"] and len(parts) == 3:
                return self._handle_create_user(self._read_json())
            if parts[:3] == ["api", "v1", "teams"] and len(parts) == 3:
                return self._handle_create_team(self._read_json())
            if parts[:3] == ["api", "v1", "tenants"] and len(parts) == 3:
                return self._handle_create_tenant(self._read_json())
            if parts[:3] == ["api", "v1", "calendar"] and len(parts) == 3:
                return self._handle_create_availability(self._read_json())
            if parts[:3] == ["api", "v1", "dependencies"] and len(parts) == 3:
                return self._handle_create_dependency(self._read_json())
            if parts[:3] == ["api", "v1", "teams"] and len(parts) == 5 and parts[4] == "members":
                return self._handle_team_members(parts[3], self._read_json())
            _send_json(self, 404, {"ok": False, "error": f"未知路由：{self.path}"})
        except KeyError as exc:
            _send_json(self, 404, {"ok": False, "error": str(exc)})
        except PermissionDenied as exc:
            _send_error(self, 403, str(exc), "permission_denied")
        except Exception as exc:  # noqa: BLE001 —— 路由顶层统一错误出口
            _send_json(self, 500, {"ok": False, "error": str(exc)})

    def do_PATCH(self) -> None:  # noqa: N802
        if not self._check_auth():
            _send_error(self, 401, "未授权（需要 X-Auth-Token）", "not_authorized")
            return
        parts = self._path_parts()
        try:
            if parts[:3] == ["api", "v1", "projects"] and len(parts) == 4:
                return self._handle_patch_project(parts[3], self._read_json())
            if parts[:3] == ["api", "v1", "users"] and len(parts) == 4:
                return self._handle_update_user(parts[3], self._read_json())
            if (
                len(parts) == 6
                and parts[:3] == ["api", "v1", "projects"]
                and parts[4] == "tasks"
            ):
                return self._handle_task_assign(parts[3], parts[5], self._read_json())
            _send_json(self, 404, {"ok": False, "error": f"未知路由：{self.path}"})
        except KeyError as exc:
            _send_json(self, 404, {"ok": False, "error": str(exc)})
        except PermissionDenied as exc:
            _send_error(self, 403, str(exc), "permission_denied")
        except Exception as exc:  # noqa: BLE001
            _send_json(self, 500, {"ok": False, "error": str(exc)})

    def do_DELETE(self) -> None:  # noqa: N802
        if not self._check_auth():
            _send_error(self, 401, "未授权（需要 X-Auth-Token）", "not_authorized")
            return
        parts = self._path_parts()
        try:
            if parts[:3] == ["api", "v1", "users"] and len(parts) == 4:
                return self._handle_delete_user(parts[3])
            if parts[:3] == ["api", "v1", "teams"] and len(parts) == 4:
                return self._handle_delete_team(parts[3])
            if parts[:3] == ["api", "v1", "tenants"] and len(parts) == 4:
                return self._handle_delete_tenant(parts[3])
            if parts[:3] == ["api", "v1", "calendar"] and len(parts) == 4:
                return self._handle_delete_availability(parts[3])
            if parts[:3] == ["api", "v1", "dependencies"] and len(parts) == 4:
                return self._handle_delete_dependency(parts[3])
            _send_json(self, 404, {"ok": False, "error": f"未知路由：{self.path}"})
        except KeyError as exc:
            _send_json(self, 404, {"ok": False, "error": str(exc)})
        except PermissionDenied as exc:
            _send_error(self, 403, str(exc), "permission_denied")
        except Exception as exc:  # noqa: BLE001
            _send_json(self, 500, {"ok": False, "error": str(exc)})

    # ------------------------------------------------------------------
    # handlers
    # ------------------------------------------------------------------

    def _handle_status(self) -> None:
        server = self.server.workbench
        _send_json(
            self,
            200,
            {
                "ok": True,
                "data": {
                    "version": _package_version(),
                    "projects": len(server.index.projects),
                    "sessions": len(server.sessions),
                    "active_sessions": sum(
                        1 for s in server.sessions.values() if s.status in ("running", "waiting_approval")
                    ),
                    "uptime": _now_iso(),
                    "auth": {
                        "enabled": self.server.workbench.auth_enabled,
                        "user": self._auth_user() if self.server.workbench.auth_enabled else None,
                    },
                },
            },
        )

    def _handle_doctor(self) -> None:
        """GET /api/v1/doctor：环境预检报告（含 docker 检查的 action 修复指引）。"""
        from agent_cluster.doctor import run_doctor

        report = run_doctor()
        _send_json(
            self,
            200,
            {
                "ok": True,
                "data": {
                    "ok": report.ok,
                    "checks": [
                        {
                            "name": check.name,
                            "ok": check.ok,
                            "required": check.required,
                            "detail": check.detail,
                            "action": check.action,
                        }
                        for check in report.checks
                    ],
                },
            },
        )

    def _handle_doctor_fix_docker(self) -> None:
        """POST /api/v1/doctor/fix-docker：执行 Docker 修复脚本后重查，返回执行结果与最新报告。"""
        from agent_cluster.doctor import run_doctor

        report = run_doctor(fix_docker=True)
        fix = None
        if report.fix_result is not None:
            exit_code, output = report.fix_result
            fix = {"ran": True, "exit_code": exit_code, "output": (output or "")[-4000:]}
        _send_json(
            self,
            200,
            {
                "ok": True,
                "data": {
                    "ok": report.ok,
                    "fix": fix,
                    "checks": [
                        {
                            "name": check.name,
                            "ok": check.ok,
                            "required": check.required,
                            "detail": check.detail,
                            "action": check.action,
                        }
                        for check in report.checks
                    ],
                },
            },
        )

    def _handle_list_projects(self) -> None:
        _send_json(self, 200, {"ok": True, "data": self.server.workbench.list_projects()})

    def _handle_project_detail(self, project_id: str) -> None:
        try:
            data = self.server.workbench.project_detail(project_id)
        except KeyError as exc:
            _send_error(self, 404, str(exc), "not_found")
            return
        _send_json(self, 200, {"ok": True, "data": data})

    def _handle_patch_project(self, project_id: str, body: dict) -> None:
        allowed = {"name", "description", "metadata", "gate_policy", "budget_pool"}
        updates = {key: value for key, value in body.items() if key in allowed}
        if not updates:
            _send_error(self, 400, "没有可更新字段（支持 name/description/metadata/gate_policy/budget_pool）", "bad_request")
            return
        try:
            self.server.workbench._project_store.update(project_id, **updates)
        except KeyError as exc:
            _send_error(self, 404, str(exc), "not_found")
            return
        except ValueError as exc:
            # 非法值：存储未落盘（保持原策略/原值）→ 400
            _send_error(self, 400, str(exc), "bad_request")
            return
        self._handle_project_detail(project_id)

    def _handle_workspaces(self, project_id: str, body: dict) -> None:
        path = str(body.get("path") or "").strip()
        if not path:
            _send_error(self, 400, "path 必填", "bad_request")
            return
        try:
            updated = self.server.workbench._project_store.add_workspace(project_id, path)
        except KeyError as exc:
            _send_error(self, 404, str(exc), "not_found")
            return
        except ValueError as exc:
            _send_error(self, 400, str(exc), "bad_request")
            return
        _send_json(self, 200, {"ok": True, "data": {"project_id": updated.project_id, "workspaces": updated.workspaces}})

    def _handle_budget(self, project_id: str) -> None:
        workbench = self.server.workbench
        if project_id not in workbench.index.projects:
            _send_error(self, 404, f"项目不存在: {project_id}", "not_found")
            return
        try:
            data = workbench._project_store.budget_status(project_id)
        except KeyError:
            # 旧项目（索引有、未双写）：返回默认预算结构而非 404
            data = {
                "hard_limit_tokens": 0,
                "used": 0,
                "remaining": None,
                "warn_raised": False,
                "last_warned_at": None,
                "unlocks": [],
            }
        _send_json(self, 200, {"ok": True, "data": data})

    def _handle_budget_unlock(self, project_id: str, body: dict) -> None:
        try:
            additional_tokens = int(body.get("additional_tokens") or 0)
        except (TypeError, ValueError):
            additional_tokens = 0
        reason = str(body.get("reason") or "").strip()
        session_id = str(body.get("session_id") or "")
        try:
            unlock = self.server.workbench._project_store.unlock_budget(
                project_id,
                additional_tokens=additional_tokens,
                reason=reason,
                session_id=session_id,
                emit=self.server.workbench.manager._emit,
            )
        except KeyError as exc:
            _send_error(self, 404, str(exc), "not_found")
            return
        except ValueError as exc:
            _send_error(self, 400, str(exc), "bad_request")
            return
        status = 200 if unlock.status == "granted" else 202
        _send_json(self, status, {"ok": True, "data": unlock.model_dump(mode="json")})

    def _handle_budget_unlock_decide(self, project_id: str, unlock_id: str, action: str, body: dict) -> None:
        try:
            project = self.server.workbench._project_store.get(project_id)
        except KeyError as exc:
            _send_error(self, 404, str(exc), "not_found")
            return
        target = None
        for unlock in project.budget_pool.unlocks:
            if unlock.id == unlock_id:
                target = unlock
                break
        if target is None:
            _send_error(self, 404, f"解锁记录不存在：{unlock_id}", "not_found")
            return
        if target.status != "pending":
            _send_error(self, 409, f"仅 pending 解锁记录可审批（当前 {target.status}）", "conflict")
            return
        try:
            decided = self.server.workbench._project_store.decide_unlock(
                project_id,
                unlock_id,
                approved=(action == "approve"),
                decided_by=str(body.get("decided_by") or "human"),
            )
        except ValueError as exc:
            _send_error(self, 409, str(exc), "conflict")
            return
        _send_json(self, 200, {"ok": True, "data": decided.model_dump(mode="json")})

    def _handle_dashboard(self, project_id: str) -> None:
        workbench = self.server.workbench
        if project_id not in workbench.index.projects:
            _send_error(self, 404, f"项目不存在: {project_id}", "not_found")
            return
        try:
            data = workbench._dashboard(project_id)
        except KeyError:
            # 全局索引存在但 ProjectStore 未双写（v0.6 T13.5 前旧项目）：返回空三轴而非 404
            data = {
                "cost": {"used": 0, "limit": 0, "ratio": 0.0, "score": 1.0, "status": "ok", "estimated_usd": 0.0},
                "progress": {"score": 0.0, "status": "ok", "phases": {"total": 0, "done": 0}},
                "health": {"score": 0.0, "status": "ok", "sessions": {}},
                "updated_at": _now_iso(),
            }
        _send_json(self, 200, {"ok": True, "data": data})

    def _handle_tasks(self, project_id: str) -> None:
        try:
            entries = self.server.workbench.task_entries(project_id)
        except KeyError as exc:
            _send_error(self, 404, str(exc), "not_found")
            return
        query = self._query()
        status_filter = (query.get("status") or "").strip()
        assignee_filter = (query.get("assignee") or "").strip()
        q = (query.get("q") or "").strip().lower()
        filtered: list[dict] = []
        for entry in entries:
            if status_filter and entry.get("status") != status_filter:
                continue
            if assignee_filter and entry.get("assignee", "") != assignee_filter:
                continue
            if q:
                haystack = " ".join(
                    [
                        str(entry.get("goal") or ""),
                        " ".join(str(value) for value in (entry.get("metadata") or {}).values()),
                    ]
                ).lower()
                if q not in haystack:
                    continue
            filtered.append(entry)
        _send_json(self, 200, {"ok": True, "data": filtered})

    def _handle_task_assign(self, project_id: str, session_id: str, body: dict) -> None:
        assignee = str(body.get("assignee") or "").strip()
        if not assignee:
            _send_error(self, 400, "assignee 必填（非空字符串）", "bad_request")
            return
        try:
            project = self.server.workbench._project_store.get(project_id)
        except KeyError as exc:
            _send_error(self, 404, str(exc), "not_found")
            return
        entry = None
        for item in project.sessions:
            if item.session_id == session_id:
                entry = item
                break
        if entry is None:
            _send_error(self, 404, f"会话不存在：{session_id}", "not_found")
            return
        updated_entry = entry.model_copy(update={"assignee": assignee})
        self.server.workbench._project_store.index_session(project_id, updated_entry)
        live = self.server.workbench.manager.sessions.get(session_id)
        if live is not None and live.project_id == project_id:
            live.assignee = assignee
        _send_json(self, 200, {"ok": True, "data": updated_entry.model_dump(mode="json")})

    def _handle_fork(self, session_id: str, body: dict) -> None:
        try:
            result = self.server.workbench.fork_session(session_id, body)
        except KeyError as exc:
            _send_error(self, 404, str(exc), "not_found")
            return
        except ForkConflictError as exc:
            _send_error(self, 409, str(exc), "fork_conflict")
            return
        except BudgetPoolExhaustedError as exc:
            _send_error(self, 409, str(exc), "budget_pool_exhausted")
            return
        except ValueError as exc:
            _send_error(self, 400, str(exc), "bad_request")
            return
        _send_json(self, 200, {"ok": True, "data": result})

    def _handle_stdin(self, session_id: str, body: dict) -> None:
        text = str(body.get("text") or "").strip()
        if not text:
            _send_error(self, 400, "text 必填（非空字符串）", "bad_request")
            return
        try:
            session = self.server.workbench.get_session(session_id)
        except KeyError as exc:
            _send_error(self, 404, str(exc), "not_found")
            return
        if session.status in ("completed", "failed"):
            _send_error(self, 409, f"会话已终态（{session.status}），拒绝注入", "session_busy")
            return
        if not self.server.workbench.manager.submit_stdin(session_id, text):
            _send_error(self, 409, "会话已取消或终态，拒绝注入", "session_busy")
            return
        _send_json(self, 202, {"ok": True, "data": {"accepted": text}})

    def _handle_cancel(self, session_id: str) -> None:
        if not self.server.workbench.manager.cancel(session_id):
            _send_error(self, 404, f"会话不存在：{session_id}", "not_found")
            return
        _send_json(self, 202, {"ok": True, "data": {"cancelled": "pending"}})

    def _handle_project_sessions(self, project_id: str) -> None:
        sessions = [
            self.server.workbench.get_session(sid).snapshot()
            for sid, session in self.server.workbench.sessions.items()
            if session.project_id == project_id
        ]
        _send_json(self, 200, {"ok": True, "data": sessions})

    def _handle_session_detail(self, session_id: str) -> None:
        _send_json(self, 200, {"ok": True, "data": self.server.workbench.get_session(session_id).snapshot()})

    def _handle_ws_upgrade(self) -> None:
        """§6.4：WebSocket 握手升级。认证 = X-Auth-Token 头（优先）或 token 查询参数。"""
        server = self.server.workbench
        if server.auth_token:
            token = self.headers.get("X-Auth-Token") or self._query().get("token") or ""
            if token != server.auth_token:
                _send_error(self, 401, "未授权（需要 X-Auth-Token 或 token 查询参数）", "not_authorized")
                return
        key = self.headers.get("Sec-WebSocket-Key") or ""
        if not key:
            _send_error(self, 400, "缺少 Sec-WebSocket-Key 头", "bad_request")
            return
        self.send_response(101)
        self.send_header("Upgrade", "websocket")
        self.send_header("Connection", "Upgrade")
        self.send_header("Sec-WebSocket-Accept", WebSocketPeer.accept_key(key.strip()))
        self.end_headers()
        try:
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            return
        self.close_connection = True
        handle_ws(self.connection, server, session_id=self._query().get("session_id") or None)

    def _handle_sse(self, session_id: str) -> None:
        server = self.server.workbench
        session = server.get_session(session_id)
        query = self._query()
        since = int(query.get("since", "0") or 0)
        header_value = self.headers.get("Last-Event-ID")
        if header_value:
            try:
                since = max(since, int(header_value.strip()) + 1)
            except ValueError:
                pass
        finished = session.status in ("completed", "failed")
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close" if finished else "keep-alive")
        self.end_headers()
        try:
            self.wfile.write(b"retry: 3000\n\n")
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            return
        last_seq = since - 1
        for event in session.log.replay(since):
            try:
                self._write_sse_event(event)
                last_seq = max(last_seq, int(event.get("seq", last_seq)))
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                return
        if finished:
            self._write_sse_sentinel(session)
            return
        sub = session.log.subscribe()
        # 订阅注册竞态兜底：注册前追加的事件经 replay 补齐，保证 seq 无缺口
        for event in session.log.replay(last_seq + 1):
            try:
                self._write_sse_event(event)
                last_seq = max(last_seq, int(event.get("seq", last_seq)))
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                session.log.unsubscribe(sub)
                return
        try:
            while True:
                try:
                    event = sub.get(timeout=server.heartbeat_seconds)
                except queue.Empty:
                    if session.status in ("completed", "failed"):
                        break
                    # §6.3 心跳：连接未挂死可被双方检出
                    try:
                        self.wfile.write(b": ping\n\n")
                        self.wfile.flush()
                    except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                        return
                    continue
                try:
                    self._write_sse_event(event)
                    last_seq = max(last_seq, int(event.get("seq", last_seq)))
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                    return
        finally:
            session.log.unsubscribe(sub)
        if session.status in ("completed", "failed"):
            for event in session.log.replay(last_seq + 1):
                try:
                    self._write_sse_event(event)
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                    return
            self._write_sse_sentinel(session)

    def _write_sse_event(self, event: dict) -> None:
        """SSE 事件帧：id 行承载 seq（Last-Event-ID 续传依据）。"""
        seq = int(event.get("seq", 0))
        data = json.dumps(event, ensure_ascii=False)
        self.wfile.write(f"id: {seq}\ndata: {data}\n\n".encode("utf-8"))
        self.wfile.flush()

    def _write_sse_sentinel(self, session: ServerSession) -> None:
        """终止哨兵（§6.3）：event: session.end + status；随后关连接。"""
        if session.status == "completed" and session.exit_code in (2, 3):
            status = "cancelled"
        else:
            status = session.status if session.status in ("completed", "failed") else "failed"
        events = session.log.replay()
        seq = int(events[-1].get("seq", 0)) if events else 0
        payload = {"type": "session.end", "status": status, "seq": seq, "session_id": session.session_id}
        data = json.dumps(payload, ensure_ascii=False)
        try:
            self.wfile.write(f"event: session.end\nid: {seq}\ndata: {data}\n\n".encode("utf-8"))
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass

    def _handle_create_project(self, body: dict) -> None:
        name = str(body.get("name") or "").strip()
        workspace = str(body.get("workspace") or "").strip()
        if not name or not workspace:
            _send_json(self, 400, {"ok": False, "error": "name 与 workspace 必填"})
            return
        tenant_id = str(body.get("tenant_id") or "").strip() or None
        if tenant_id:
            try:
                self.server.workbench.tenants.get_tenant(tenant_id)
                self.server.workbench.tenants.ensure_quota(tenant_id, "projects")
            except KeyError:
                _send_error(self, 404, f"未找到租户：{tenant_id!r}")
                return
            except QuotaExceededError as exc:
                _send_error(self, 409, str(exc), "quota_exceeded")
                return
        entry = self.server.workbench.create_project(name, workspace, tenant_id=tenant_id)
        _send_json(self, 201, {"ok": True, "data": entry})

    def _handle_start_session(self, project_id: str, body: dict) -> None:
        try:
            result = self.server.workbench.start_session(project_id, body)
        except NotFoundError as exc:
            _send_error(self, 404, str(exc), "not_found")
            return
        except ConflictError as exc:
            _send_error(self, 409, str(exc), "conflict")
            return
        except BudgetPoolExhaustedError as exc:
            _send_error(self, 409, str(exc), "budget_pool_exhausted")
            return
        except (WorktreeConflictError, WorktreeError) as exc:
            _send_error(self, 409, str(exc), "conflict")
            return
        except ValueError as exc:
            _send_error(self, 400, str(exc), "bad_request")
            return
        except KeyError as exc:
            _send_error(self, 404, str(exc), "not_found")
            return
        _send_json(self, 201, {"ok": True, "data": result})

    def _handle_answer(self, session_id: str, answer: str) -> None:
        session = self.server.workbench.get_session(session_id)
        session.submit_answer(answer)
        _send_json(self, 200, {"ok": True, "data": {"submitted": answer}})

    def _handle_interrupt(self, session_id: str, body: dict) -> None:
        text = str(body.get("text") or "").strip()
        if not text:
            _send_json(self, 400, {"ok": False, "error": "text 必填（需求变更内容）"})
            return
        session = self.server.workbench.get_session(session_id)
        session.inject_change(text)
        _send_json(self, 202, {"ok": True, "data": {"queued": text}})

    def _handle_changes(self, session_id: str) -> None:
        session = self.server.workbench.get_session(session_id)
        if session.driver is None:
            _send_json(self, 200, {"ok": True, "data": {"records": [], "summary": {"count": 0}}})
            return
        records = [r.model_dump() for r in session.driver.change_history.list()]
        _send_json(
            self,
            200,
            {"ok": True, "data": {"records": records, "summary": session.driver.change_history.summary()}},
        )

    def _handle_rollback(self, session_id: str, body: dict) -> None:
        session = self.server.workbench.get_session(session_id)
        if session.driver is None:
            _send_json(self, 409, {"ok": False, "error": "会话尚未就绪"})
            return
        version = int(body.get("version") or 0)
        ok = session.driver.change_history.rollback(version)
        if not ok:
            _send_json(self, 404, {"ok": False, "error": f"版本 {version} 不存在或快照缺失"})
            return
        session.log.append(
            {"type": "change.rollback", "session_id": session_id, "payload": {"version": version}}
        )
        _send_json(self, 200, {"ok": True, "data": {"rolled_back": version}})

    def _resolve_workspace_path(self, project_id: str, rel_path: str) -> Path:
        root = self.server.workbench.project_workspace(project_id)
        target = (root / rel_path).resolve()
        if not target.is_relative_to(root):
            raise ValueError("路径越界：仅允许访问项目工作区内文件")
        return target

    def _handle_workspace_tree(self, project_id: str) -> None:
        rel = self._query().get("path", "")
        target = self._resolve_workspace_path(project_id, rel)
        if not target.exists():
            _send_json(self, 404, {"ok": False, "error": f"路径不存在：{rel}"})
            return
        if target.is_dir():
            entries = []
            for child in sorted(target.iterdir()):
                try:
                    entries.append(
                        {
                            "name": child.name,
                            "type": "dir" if child.is_dir() else "file",
                            "size": child.stat().st_size if child.is_file() else 0,
                        }
                    )
                except OSError:
                    continue
            _send_json(self, 200, {"ok": True, "data": {"path": str(target), "entries": entries}})
        else:
            _send_json(
                self,
                200,
                {"ok": True, "data": {"path": str(target), "file": {"name": target.name, "size": target.stat().st_size}}},
            )

    def _handle_workspace_file(self, project_id: str) -> None:
        rel = self._query().get("path", "")
        target = self._resolve_workspace_path(project_id, rel)
        if not target.is_file():
            _send_json(self, 404, {"ok": False, "error": f"文件不存在：{rel}"})
            return
        if target.stat().st_size > MAX_FILE_BYTES:
            _send_json(self, 413, {"ok": False, "error": f"文件过大（>{MAX_FILE_BYTES} 字节），仅支持预览"})
            return
        try:
            text = target.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            _send_json(self, 500, {"ok": False, "error": str(exc)})
            return
        mime, _ = mimetypes.guess_type(target.name)
        _send_json(
            self,
            200,
            {"ok": True, "data": {"path": str(target), "name": target.name, "mime": mime or "text/plain", "text": text}},
        )

    def _handle_metrics(self) -> None:
        _send_json(self, 200, {"ok": True, "data": self.server.workbench.metrics_snapshot()})

    def _auth_user(self) -> str:
        return getattr(self, "_auth_user_id", None) or self.headers.get("X-Auth-User", "admin") or "admin"

    def _require_permission(self, permission: str, project_id: str | None = None) -> None:
        self.server.workbench.rbac.require(self._auth_user(), permission, project_id=project_id)

    def _handle_auth_login(self, body: dict) -> None:
        workbench = self.server.workbench
        if not workbench.auth_enabled:
            _send_error(self, 404, "认证未启用（serve 未配置 auth provider）", "auth_disabled")
            return
        username = str(body.get("username") or "")
        password = str(body.get("password") or "")
        user_id = workbench.auth_provider.authenticate(username, password)
        if not user_id:
            _send_error(self, 401, "用户名或密码错误", "invalid_credentials")
            return
        tokens = workbench.tokens.issue(user_id)
        _send_json(self, 200, {"ok": True, "data": {"user": user_id, **tokens}})

    def _handle_auth_refresh(self, body: dict) -> None:
        workbench = self.server.workbench
        if not workbench.auth_enabled:
            _send_error(self, 404, "认证未启用（serve 未配置 auth provider）", "auth_disabled")
            return
        try:
            tokens = workbench.tokens.refresh(str(body.get("refresh_token") or ""))
        except ValueError:
            _send_error(self, 401, "无效或过期的 refresh token", "invalid_refresh_token")
            return
        user_id = workbench.tokens.verify_access(tokens["access_token"])
        _send_json(self, 200, {"ok": True, "data": {"user": user_id, **tokens}})

    def _handle_auth_me(self) -> None:
        _send_json(self, 200, {"ok": True, "data": {"user": self._auth_user()}})

    def _handle_oauth_well_known(self) -> None:
        """RFC 8844 §3.1：保护资源元数据（公开）。"""
        _send_json(
            self,
            200,
            {"ok": True, "data": self.server.workbench.oauth.protected_resource_metadata()},
        )

    def _handle_oauth_server_metadata(self) -> None:
        """RFC 8414：授权服务器元数据（公开）。"""
        _send_json(
            self,
            200,
            {"ok": True, "data": self.server.workbench.oauth.authorization_server_metadata()},
        )

    def _handle_oauth_authorize(self) -> None:
        """GET/POST /oauth/authorize：PKCE 校验 + 签发一次性授权码（公开）。

        成功 302 到 redirect_uri（code+state）；注册过回调的失败也走重定向
        （RFC 6749 §4.1.2.1），否则 400 JSON 错误。
        """
        workbench = self.server.workbench
        params = self._read_form() if self.command == "POST" else self._query()
        try:
            location = workbench.oauth.authorize_redirect(params)
        except OAuthError as exc:
            if workbench.oauth.can_redirect_to(
                params.get("client_id", ""),
                params.get("redirect_uri", ""),
            ):
                _send_redirect(self, workbench.oauth.error_redirect(params["redirect_uri"], exc.code, exc.description))
            else:
                _send_json(self, exc.status, {"error": exc.code, "error_description": exc.description})
            return
        _send_redirect(self, location)

    def _handle_oauth_token(self) -> None:
        """POST /oauth/token：authorization_code/refresh_token 交换（公开，PKCE S256）。"""
        try:
            tokens = self.server.workbench.oauth.token_exchange(self._read_form())
        except OAuthError as exc:
            _send_json(self, exc.status, {"error": exc.code, "error_description": exc.description})
            return
        _send_json(self, 200, {"ok": True, "data": tokens})

    def _handle_roles(self) -> None:
        _send_json(self, 200, {"ok": True, "data": {"roles": self.server.workbench.rbac.roles_catalog()}})

    def _handle_users(self) -> None:
        users = [user.__dict__ for user in self.server.workbench.rbac.list_users()]
        _send_json(self, 200, {"ok": True, "data": {"users": users}})

    def _handle_user_detail(self, user_id: str) -> None:
        user = self.server.workbench.rbac.get_user(user_id)
        _send_json(self, 200, {"ok": True, "data": {"user": user.__dict__}})

    def _handle_create_user(self, body: dict) -> None:
        self._require_permission("users.manage")
        user = self.server.workbench.rbac.add_user(
            id=str(body.get("id") or ""),
            name=str(body.get("name") or ""),
            role_ids=[str(role) for role in (body.get("role_ids") or [])],
            scopes=[str(scope) for scope in (body.get("scopes") or [])],
        )
        _send_json(self, 201, {"ok": True, "data": {"user": user.__dict__}})

    def _handle_update_user(self, user_id: str, body: dict) -> None:
        self._require_permission("users.manage")
        user = self.server.workbench.rbac.update_user(
            user_id,
            name=body.get("name"),
            role_ids=[str(role) for role in body["role_ids"]] if "role_ids" in body else None,
            scopes=[str(scope) for scope in body["scopes"]] if "scopes" in body else None,
        )
        _send_json(self, 200, {"ok": True, "data": {"user": user.__dict__}})

    def _handle_delete_user(self, user_id: str) -> None:
        self._require_permission("users.manage")
        self.server.workbench.rbac.remove_user(user_id)
        _send_json(self, 200, {"ok": True, "data": {"removed": user_id}})

    def _handle_teams(self) -> None:
        teams = [team.__dict__ for team in self.server.workbench.rbac.list_teams()]
        _send_json(self, 200, {"ok": True, "data": {"teams": teams}})

    def _handle_create_team(self, body: dict) -> None:
        self._require_permission("team.manage")
        team = self.server.workbench.rbac.add_team(id=str(body.get("id") or ""), name=str(body.get("name") or ""))
        _send_json(self, 201, {"ok": True, "data": {"team": team.__dict__}})

    def _handle_delete_team(self, team_id: str) -> None:
        self._require_permission("team.manage")
        self.server.workbench.rbac.remove_team(team_id)
        _send_json(self, 200, {"ok": True, "data": {"removed": team_id}})

    def _handle_team_members(self, team_id: str, body: dict) -> None:
        self._require_permission("team.manage")
        user_id = str(body.get("user_id") or "")
        action = str(body.get("action") or "add")
        if action == "add":
            self.server.workbench.rbac.add_member(team_id, user_id)
        elif action == "remove":
            self.server.workbench.rbac.remove_member(team_id, user_id)
        else:
            raise ValueError(f"未知成员操作：{action!r}")
        team = self.server.workbench.rbac.get_team(team_id)
        _send_json(self, 200, {"ok": True, "data": {"team": team.__dict__}})

    def _handle_tenants(self) -> None:
        tenants = [tenant.__dict__ for tenant in self.server.workbench.tenants.list_tenants()]
        _send_json(self, 200, {"ok": True, "data": {"tenants": tenants}})

    def _handle_create_tenant(self, body: dict) -> None:
        self._require_permission("tenants.manage")
        tenant = self.server.workbench.tenants.add_tenant(
            id=str(body.get("id") or ""),
            name=str(body.get("name") or ""),
            project_limit=int(body.get("project_limit") or 0),
            session_limit=int(body.get("session_limit") or 0),
        )
        _send_json(self, 201, {"ok": True, "data": {"tenant": tenant.__dict__}})

    def _handle_delete_tenant(self, tenant_id: str) -> None:
        self._require_permission("tenants.manage")
        self.server.workbench.tenants.remove_tenant(tenant_id)
        _send_json(self, 200, {"ok": True, "data": {"removed": tenant_id}})

    def _handle_tenant_usage(self, tenant_id: str) -> None:
        usage = self.server.workbench.tenants.usage(tenant_id)
        _send_json(self, 200, {"ok": True, "data": {"usage": usage}})

    def _handle_calendar(self) -> None:
        query = self._query()
        role_id = (query.get("role_id") or "").strip() or None
        from_iso = (query.get("from") or "").strip() or None
        to_iso = (query.get("to") or "").strip() or None
        items = self.server.workbench.calendar.list_availability(
            role_id=role_id, from_=from_iso, to=to_iso
        )
        _send_json(
            self,
            200,
            {"ok": True, "data": {"availability": [item.__dict__ for item in items]}},
        )

    def _handle_create_availability(self, body: dict) -> None:
        try:
            item = self.server.workbench.calendar.add_availability(
                role_id=str(body.get("role_id") or ""),
                start=str(body.get("start") or ""),
                end=str(body.get("end") or ""),
                note=str(body.get("note") or ""),
            )
        except OverlapError as exc:
            _send_error(self, 409, str(exc), "overlap")
            return
        except ValueError as exc:
            _send_error(self, 400, str(exc), "bad_request")
            return
        _send_json(self, 201, {"ok": True, "data": {"availability": item.__dict__}})

    def _handle_delete_availability(self, availability_id: str) -> None:
        self.server.workbench.calendar.remove_availability(availability_id)
        _send_json(self, 200, {"ok": True, "data": {"removed": availability_id}})

    def _handle_dependencies(self) -> None:
        self._require_permission("project.read")
        edges = self.server.workbench.dependencies.list_edges()
        _send_json(
            self,
            200,
            {"ok": True, "data": {"edges": [edge.__dict__ for edge in edges]}},
        )

    def _handle_dependency_impact(self) -> None:
        self._require_permission("project.read")
        project_id = (self._query().get("project_id") or "").strip()
        if not project_id:
            _send_error(self, 400, "缺少 project_id 查询参数", "bad_request")
            return
        impact = self.server.workbench.dependencies.impact_of(project_id)
        _send_json(
            self,
            200,
            {"ok": True, "data": {"project_id": project_id, "impact": sorted(impact)}},
        )

    def _handle_create_dependency(self, body: dict) -> None:
        self._require_permission("project.write")
        try:
            edge = self.server.workbench.dependencies.add_edge(
                from_project=str(body.get("from_project") or ""),
                to_project=str(body.get("to_project") or ""),
                from_task=str(body.get("from_task") or ""),
                to_task=str(body.get("to_task") or ""),
                type=str(body.get("type") or ""),
            )
        except CycleError as exc:
            _send_error(self, 409, str(exc), "cycle_detected")
            return
        except ValueError as exc:
            _send_error(self, 400, str(exc), "bad_request")
            return
        _send_json(self, 201, {"ok": True, "data": {"edge": edge.__dict__}})

    def _handle_delete_dependency(self, edge_id: str) -> None:
        self._require_permission("project.write")
        self.server.workbench.dependencies.remove_edge(edge_id)
        _send_json(self, 200, {"ok": True, "data": {"removed": edge_id}})


    def _handle_memory(self, project_id: str) -> None:
        store = self.server.workbench.memory_store(project_id)
        items = [item.__dict__ for item in store.list_items(limit=200)]
        _send_json(self, 200, {"ok": True, "data": {"items": items, "proposals": [p.__dict__ for p in store.list_proposals()]}})

    def _handle_evolution_proposals(self) -> None:
        project_id = self._query().get("project_id")
        bridge = self.server.workbench.evolution_bridge(project_id)
        _send_json(self, 200, {"ok": True, "data": {"proposals": bridge.list_proposals()}})

    def _handle_evolution_generate(self, body: dict) -> None:
        bridge = self.server.workbench.evolution_bridge(body.get("project_id"))
        result = bridge.generate_from_memory(
            min_evidence=int(body.get("min_evidence", 2) or 2),
            limit=int(body.get("limit", 20) or 20),
        )
        _send_json(self, 200, {"ok": True, "data": result})

    def _handle_evolution_action(self, proposal_id: str, action: str, body: dict) -> None:
        bridge = self.server.workbench.evolution_bridge(body.get("project_id"))
        if action == "apply":
            proposal = bridge.apply_proposal(
                proposal_id,
                approver=str(body.get("approver", "governance")),
                human_required=bool(body.get("human_required", False)),
                auto_mode=str(body.get("auto_mode", "ask")),
                reason=str(body.get("reason", "进化提案自动评审通过")),
            )
        else:
            proposal = bridge.rollback_proposal(
                proposal_id, reason=str(body.get("reason", "观察期发现回归，回滚该进化提案"))
            )
        _send_json(self, 200, {"ok": True, "data": {"proposal": proposal}})

    def _handle_evolution_retro(self, body: dict) -> None:
        session_id = str(body.get("session_id") or "")
        if session_id:
            session = self.server.workbench.get_session(session_id)
            data = session.audit_data()
            bridge = EvolutionBridge(session.workspace)
            path = bridge.generate_retro_report(
                goal=str(data.get("goal") or ""),
                session_id=session_id,
                token_summary=data.get("token_summary") or {},
                gate_decisions=data.get("approvals") or [],
                events=data.get("events") or [],
            )
        else:
            bridge = self.server.workbench.evolution_bridge(body.get("project_id"))
            path = bridge.generate_retro_report()
        _send_json(self, 200, {"ok": True, "data": {"report": str(path)}})

    def _handle_memory_promote(self, item_id: str) -> None:
        # 无项目上下文时用全局记忆库
        store = self.server.workbench.memory_store(None)
        ok = store.promote(item_id, human_confirm=True)
        _send_json(self, 200, {"ok": ok, "data": {"promoted": ok}})

    def _handle_audit(self, session_id: str) -> None:
        session = self.server.workbench.get_session(session_id)
        _send_json(self, 200, {"ok": True, "data": session.audit_data()})

    def _handle_audit_export(self, session_id: str) -> None:
        session = self.server.workbench.get_session(session_id)
        query = self._query()
        fmt = (query.get("format") or "json").strip().lower()
        if fmt not in ("csv", "json", "markdown"):
            _send_error(self, 400, "format 仅支持 csv|json|markdown", "invalid_format")
            return
        retention_days: int | None = None
        raw_retention = (query.get("retention_days") or "").strip()
        if raw_retention:
            try:
                retention_days = int(raw_retention)
            except ValueError:
                _send_error(self, 400, "retention_days 必须为整数", "invalid_retention")
                return
        data = session.audit_data()
        records = list(data["events"])
        content = export_audit(
            records,
            fmt=fmt,
            retention_days=retention_days,
            session_id=session.session_id,
            goal=str(data.get("goal") or ""),
        )
        files = build_audit_package(
            workspace=session.workspace,
            session_id=session.session_id,
            goal=data["goal"],
            events=records,
            approvals=data["approvals"],
            token_summary=data["token_summary"],
            change_records=data["changes"],
            spans=session.tracer.spans(),
            cost=data["cost"],
            export_format=fmt,
            retention_days=retention_days,
        )
        session.log.append({"type": "audit.exported", "session_id": session.session_id, "payload": files})
        _send_json(
            self,
            200,
            {
                "ok": True,
                "data": {
                    "session_id": session.session_id,
                    "format": fmt,
                    "retention_days": retention_days,
                    "content": content,
                    "files": files,
                },
            },
        )

    def _handle_plugins(self) -> None:
        workbench = self.server.workbench
        if workbench._plugin_manager is None:
            _send_json(self, 200, {"ok": True, "data": {"plugins": [], "note": "未配置插件目录"}})
            return
        plugins = []
        for manifest in workbench._plugin_manager.list_plugins():
            plugins.append(
                {
                    "name": manifest.name,
                    "version": manifest.version,
                    "description": manifest.description or "",
                    "skill_dirs": len(manifest.skill_dirs or []),
                    "hooks": sorted({event for event, specs in (manifest.hooks or {}).items() if specs}),
                }
            )
        skills = [
            {"name": skill.name, "version": skill.version, "description": skill.description or ""}
            for skill in workbench._skills()
        ]
        _send_json(self, 200, {"ok": True, "data": {"plugins": plugins, "skills": skills}})

    def _handle_skills(self) -> None:
        skills = [
            {"name": skill.name, "version": skill.version, "description": skill.description or ""}
            for skill in self.server.workbench._skills()
        ]
        _send_json(self, 200, {"ok": True, "data": {"skills": skills, "count": len(skills)}})

    def _handle_mcp(self) -> None:
        workbench = self.server.workbench
        _send_json(
            self,
            200,
            {
                "ok": True,
                "data": {
                    "stdio": workbench.mcp_servers,
                    "http": workbench.mcp_http_servers,
                    "note": "MCP 工具在会话启动时注册为 mcp_<server>_<tool>（危险权限）",
                },
            },
        )


def build_server(args: Any) -> tuple[WorkbenchServer, ThreadingHTTPServer]:
    """构造 serve 实例（先跑认证/监听守卫，再绑定端口）。"""
    host = args.host or "127.0.0.1"
    auth_provider = getattr(args, "auth_provider", None)
    auth_secret = getattr(args, "auth_secret", "") or os.environ.get("AGENT_CLUSTER_AUTH_SECRET", "")
    if host not in ("127.0.0.1", "localhost") and not auth_provider:
        raise RuntimeError(
            "监听非本机地址（0.0.0.0 等）必须先启用认证（--auth-mode local/ldap/oidc），否则任何机器都可访问工作台"
        )
    server = WorkbenchServer(
        host=host,
        port=args.port,
        auth_token=getattr(args, "auth_token", "") or "",
        auth_provider=auth_provider,
        auth_secret=auth_secret,
        plugins_dir=list(getattr(args, "plugin_dir", None) or []),
        mcp_servers=list(getattr(args, "mcp", None) or []),
        mcp_http_servers=list(getattr(args, "mcp_http", None) or []),
    )
    httpd = ThreadingHTTPServer((server.host, server.port), WorkbenchHandler)
    httpd.workbench = server  # type: ignore[attr-defined]
    return server, httpd


def serve_main(args: Any) -> int:
    """serve 子命令入口（由 cli.py 调用）。"""
    server, httpd = build_server(args)
    url = f"http://{server.host}:{server.port}"
    print(f"agent-cluster serve 已启动：{url}（Ctrl+C 停止）")
    if server.auth_token:
        print("认证：已启用（请求头 X-Auth-Token）")
    if server.auth_enabled:
        print("认证：已启用（/api/v1/auth/login 获取 Bearer token）")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nserve 已停止。")
    finally:
        httpd.server_close()
    return 0
