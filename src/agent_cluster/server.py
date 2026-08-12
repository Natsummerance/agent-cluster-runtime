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
import mimetypes
import os
import queue
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from agent_cluster.memory import MemoryStore
from agent_cluster.evolution_integration import EvolutionBridge
from agent_cluster.memory import MemoryStore
from agent_cluster.models import TokenUsage
from agent_cluster.pricing import CostLedger
from agent_cluster.session import SessionDriver
from agent_cluster.trace import (
    JsonlExporter,
    Tracer,
    build_audit_package,
    compute_health,
)

__all__ = [
    "SessionEventLog",
    "GlobalIndex",
    "ServerSession",
    "WorkbenchServer",
    "serve_main",
]

INDEX_DIR = Path.home() / ".agent-cluster"
MAX_FILE_BYTES = 2 * 1024 * 1024  # 文件预览上限 2MB
DEFAULT_FLOW = "examples/flows/build-product.yaml"


def _package_version() -> str:
    """读取安装版本（importlib.metadata，开发模式回退 0.5.0-dev）。"""
    try:
        from importlib import metadata

        return metadata.version("agent-cluster")
    except Exception:  # noqa: BLE001
        return "0.5.0-dev"


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

    def __init__(self, session_id: str, project_id: str, workspace: Path, spec: dict) -> None:
        self.session_id = session_id
        self.project_id = project_id
        self.workspace = workspace
        self.spec = spec
        self.log = SessionEventLog()
        self.status = "starting"
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
                try:
                    answer = self._answer_queue.get(timeout=30)
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
                )
                self.driver = driver
                self.status = "running"
                result = asyncio.run(driver.run())
                self.token_summary = result.token_summary or {}
                self.delivery = result.delivery
                self.exit_code = result.exit_code
                self.status = "completed"  # 运行至流程结束；exit_code 表达验收结果
                self.log.append(
                    {"type": "session.end", "session_id": self.session_id, "payload": {"exit_code": result.exit_code}}
                )
            except Exception as exc:  # noqa: BLE001 —— 会话线程顶层错误出口
                self.status = "failed"
                self.error = str(exc)
                self.log.append(
                    {"type": "session.error", "session_id": self.session_id, "payload": {"error": str(exc)}}
                )
            finally:
                self.tracer.end_span(span)

        self.thread = threading.Thread(target=_run, name=f"session-{self.session_id}", daemon=True)
        self.thread.start()

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
        plugins_dir: list[str] | None = None,
        mcp_servers: list[str] | None = None,
        mcp_http_servers: list[str] | None = None,
    ) -> None:
        self.host = host
        self.port = port
        self.auth_token = auth_token
        self.index = GlobalIndex()
        self.sessions: dict[str, ServerSession] = {}
        self._lock = threading.Lock()
        self._plugins_dir = list(plugins_dir or [])
        self.mcp_servers = list(mcp_servers or [])
        self.mcp_http_servers = list(mcp_http_servers or [])
        self._plugin_manager: Any = self._build_plugin_manager()
        self._skills_loader: Any = None

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

    def create_project(self, name: str, workspace: str) -> dict:
        project_id = uuid.uuid4().hex[:12]
        root = Path(workspace).expanduser().resolve()
        root.mkdir(parents=True, exist_ok=True)
        return self.index.add_project(project_id, name, str(root))

    def list_projects(self) -> list[dict]:
        return list(self.index.projects.values())

    def start_session(self, project_id: str, spec: dict) -> dict:
        project = self.index.projects.get(project_id)
        if not project:
            raise KeyError(f"项目不存在：{project_id}")
        session_id = uuid.uuid4().hex[:12]
        workspace = Path(project["workspace"]).expanduser().resolve()
        spec.setdefault("goal", "")
        if not spec.get("goal"):
            raise ValueError("goal 不能为空")
        server_session = ServerSession(session_id, project_id, workspace, spec)
        with self._lock:
            self.sessions[session_id] = server_session
        self.index.add_session(session_id, project_id, str(workspace), spec["goal"], str(spec.get("model") or "codex"))
        server_session.log.append(
            {"type": "session.start", "session_id": session_id, "payload": {"goal": spec["goal"]}}
        )
        server_session.start()
        return {"session_id": session_id, "project_id": project_id, "workspace": str(workspace)}

    def get_session(self, session_id: str) -> ServerSession:
        session = self.sessions.get(session_id)
        if not session:
            raise KeyError(f"会话不存在：{session_id}")
        return session

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
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(body)


class WorkbenchHandler(BaseHTTPRequestHandler):
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

    def _check_auth(self) -> bool:
        token = self.server.workbench.auth_token
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
        if not self._check_auth():
            _send_json(self, 401, {"ok": False, "error": "未授权（需要 X-Auth-Token）"})
            return
        parts = self._path_parts()
        try:
            if parts[:3] == ["api", "v1", "status"]:
                return self._handle_status()
            if parts[:3] == ["api", "v1", "projects"] and len(parts) == 3:
                return self._handle_list_projects()
            if (
                len(parts) == 5
                and parts[:3] == ["api", "v1", "projects"]
                and parts[4] == "sessions"
            ):
                return self._handle_project_sessions(parts[3])
            if (
                len(parts) == 6
                and parts[:3] == ["api", "v1", "projects"]
                and parts[4] == "workspace"
                and parts[5] in ("tree", "file")
            ):
                if parts[5] == "tree":
                    return self._handle_workspace_tree(parts[3])
                return self._handle_workspace_file(parts[3])
            if (
                len(parts) == 5
                and parts[:3] == ["api", "v1", "projects"]
                and parts[4] == "memory"
            ):
                return self._handle_memory(parts[3])
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
                return self._handle_changes(parts[3])
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
        except Exception as exc:  # noqa: BLE001 —— 路由顶层统一错误出口
            _send_json(self, 500, {"ok": False, "error": str(exc)})

    def do_POST(self) -> None:  # noqa: N802
        if not self._check_auth():
            _send_json(self, 401, {"ok": False, "error": "未授权（需要 X-Auth-Token）"})
            return
        parts = self._path_parts()
        try:
            if parts[:3] == ["api", "v1", "projects"] and len(parts) == 3:
                return self._handle_create_project(self._read_json())
            if (
                len(parts) == 5
                and parts[:3] == ["api", "v1", "projects"]
                and parts[4] == "sessions"
            ):
                return self._handle_start_session(parts[3], self._read_json())
            if parts[:3] == ["api", "v1", "sessions"] and len(parts) == 4:
                sid, action = parts[2], parts[3]
                if action == "approve":
                    return self._handle_answer(sid, "accept")
                if action == "reject":
                    return self._handle_answer(sid, "reject")
                if action in ("edit", "response"):
                    return self._handle_answer(sid, f"{action} {self._read_json().get('text', '')}")
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
            _send_json(self, 404, {"ok": False, "error": f"未知路由：{self.path}"})
        except KeyError as exc:
            _send_json(self, 404, {"ok": False, "error": str(exc)})
        except Exception as exc:  # noqa: BLE001 —— 路由顶层统一错误出口
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
                },
            },
        )

    def _handle_list_projects(self) -> None:
        _send_json(self, 200, {"ok": True, "data": self.server.workbench.list_projects()})

    def _handle_project_sessions(self, project_id: str) -> None:
        sessions = [
            self.server.workbench.get_session(sid).snapshot()
            for sid, session in self.server.workbench.sessions.items()
            if session.project_id == project_id
        ]
        _send_json(self, 200, {"ok": True, "data": sessions})

    def _handle_session_detail(self, session_id: str) -> None:
        _send_json(self, 200, {"ok": True, "data": self.server.workbench.get_session(session_id).snapshot()})

    def _handle_sse(self, session_id: str) -> None:
        session = self.server.workbench.get_session(session_id)
        since = int(self._query().get("since", "0") or 0)
        finished = session.status in ("completed", "failed")
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        # 已结束会话重放后即关闭连接（前端切快照轮询）；运行中会话保持长连接
        self.send_header("Connection", "close" if finished else "keep-alive")
        self.end_headers()
        for event in session.log.replay(since):
            try:
                self.wfile.write(f"data: {json.dumps(event, ensure_ascii=False)}\n\n".encode("utf-8"))
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                return
        if finished:
            return
        sub = session.log.subscribe()
        try:
            while True:
                try:
                    event = sub.get(timeout=20)
                except queue.Empty:
                    # 心跳，维持连接
                    try:
                        self.wfile.write(b": ping\n\n")
                        self.wfile.flush()
                    except (BrokenPipeError, ConnectionResetError):
                        return
                    continue
                try:
                    self.wfile.write(f"data: {json.dumps(event, ensure_ascii=False)}\n\n".encode("utf-8"))
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    return
        finally:
            session.log.unsubscribe(sub)

    def _handle_create_project(self, body: dict) -> None:
        name = str(body.get("name") or "").strip()
        workspace = str(body.get("workspace") or "").strip()
        if not name or not workspace:
            _send_json(self, 400, {"ok": False, "error": "name 与 workspace 必填"})
            return
        entry = self.server.workbench.create_project(name, workspace)
        _send_json(self, 201, {"ok": True, "data": entry})

    def _handle_start_session(self, project_id: str, body: dict) -> None:
        try:
            result = self.server.workbench.start_session(project_id, body)
        except ValueError as exc:
            _send_json(self, 400, {"ok": False, "error": str(exc)})
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
        data = session.audit_data()
        files = build_audit_package(
            workspace=session.workspace,
            session_id=session.session_id,
            goal=data["goal"],
            events=data["events"],
            approvals=data["approvals"],
            token_summary=data["token_summary"],
            change_records=data["changes"],
            spans=session.tracer.spans(),
            cost=data["cost"],
        )
        session.log.append({"type": "audit.exported", "session_id": session.session_id, "payload": files})
        _send_json(self, 200, {"ok": True, "data": {"files": files}})

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


def serve_main(args: Any) -> int:
    """serve 子命令入口（由 cli.py 调用）。"""
    server = WorkbenchServer(
        host=args.host,
        port=args.port,
        auth_token=args.auth_token or "",
        plugins_dir=list(args.plugin_dir or []),
        mcp_servers=list(args.mcp or []),
        mcp_http_servers=list(args.mcp_http or []),
    )
    httpd = ThreadingHTTPServer((server.host, server.port), WorkbenchHandler)
    httpd.workbench = server  # type: ignore[attr-defined]
    url = f"http://{server.host}:{server.port}"
    print(f"agent-cluster serve 已启动：{url}（Ctrl+C 停止）")
    if server.auth_token:
        print("认证：已启用（请求头 X-Auth-Token）")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nserve 已停止。")
    finally:
        httpd.server_close()
    return 0
