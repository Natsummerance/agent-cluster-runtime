"""T12.12 端到端验收 + 新增验收测试（v0.7.2）。

覆盖：
- a) 面板 API 驱动流程：真实 serve 子进程（随机高位端口，隔离 USERPROFILE/HOME
  避免污染全局索引），REST 全链路 status → projects → sessions（deterministic，
  不依赖真实 LLM）→ interrupt（需求变更）→ 变更历史 → rollback → audit/export
  （POST）→ SSE 事件流可连接；另以 deterministic+yes 会话验证流程完整跑通与
  完成后审计导出。
- b) frontend 构建产物存在性：frontend/dist/index.html。
- c) Electron 冒烟可重复性：desktop/node_modules/.bin/electron 存在时以
  subprocess 跑 `electron . --smoke`（120s 超时）；不存在则 pytest.skip。

回归（T12.12 修复）：serve 的 approve/reject/edit/response 的 POST 路由曾存在
历史 bug——server.py do_POST 分支误用 len(parts)==4 且 sid, action =
parts[2], parts[3]（正确为 len(parts)==5、parts[3]/parts[4]），导致前端契约
的 POST /api/v1/sessions/{id}/approve 等 5 段路径返回 404；本文件下方
test_serve_post_approve_resumes_session 与
test_serve_post_reject_edit_response_routes 为回归用例（approve 返回 200
且会话越过首道审批门继续；reject/edit/response 按契约返回 submitted）。
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
DESKTOP_DIR = REPO_ROOT / "desktop"
ELECTRON_BIN = DESKTOP_DIR / "node_modules" / ".bin" / ("electron.cmd" if os.name == "nt" else "electron")
FRONTEND_INDEX = REPO_ROOT / "frontend" / "dist" / "index.html"


def _free_port() -> int:
    """取一个随机高位空闲端口（避免与并行子智能体抢占固定端口）。"""
    sock = socket.socket()
    try:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])
    finally:
        sock.close()


def _request(method: str, url: str, body: dict | None = None, timeout: int = 15) -> tuple[int, dict]:
    """urllib 最小 REST 客户端：返回 (status, json body)。"""
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        url, data=data, method=method, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


@pytest.fixture()
def serve_process(tmp_path):
    """启动真实 serve 子进程（隔离用户目录，避免写入真实 ~/.agent-cluster）。"""
    env = dict(os.environ)
    env["USERPROFILE"] = str(tmp_path / "home")
    env["HOME"] = str(tmp_path / "home")
    port = _free_port()
    proc = subprocess.Popen(
        [sys.executable, "-m", "agent_cluster", "serve", "--port", str(port)],
        cwd=str(REPO_ROOT),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.STDOUT,
        env=env,
    )
    base = f"http://127.0.0.1:{port}"
    deadline = time.time() + 30
    last_error = ""
    while True:
        try:
            status, body = _request("GET", f"{base}/api/v1/status", timeout=3)
            if status == 200:
                break
            last_error = f"status={status} body={body}"
        except Exception as exc:  # noqa: BLE001 —— 启动探活期间异常
            last_error = str(exc)
        if time.time() > deadline:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
            raise RuntimeError(f"serve 30s 内未就绪：{last_error}")
        time.sleep(0.3)
    try:
        yield base
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


def _wait_session(base: str, session_id: str, target: set[str], timeout: int = 60) -> dict:
    """轮询会话快照直到 status 命中 target（或进入终态），返回快照。"""
    deadline = time.time() + timeout
    last = {}
    while time.time() < deadline:
        status, body = _request("GET", f"{base}/api/v1/sessions/{session_id}")
        last = body["data"]
        if last["status"] in target or last["status"] in ("completed", "failed"):
            return last
        time.sleep(0.3)
    raise AssertionError(f"会话未在 {timeout}s 内到达 {target}：status={last.get('status')}")


def _sse_chunk(base: str, session_id: str) -> tuple[int, str, str]:
    """连接 SSE 端点并读取一段事件（会话已产生事件，缓冲可立即读出）。"""
    url = f"{base}/api/v1/sessions/{session_id}/events?since=0"
    with urllib.request.urlopen(url, timeout=10) as resp:
        return resp.status, resp.headers.get("Content-Type", ""), resp.read(2048).decode("utf-8", errors="replace")


# ---------------------------------------------------------------------------
# a) 面板 API 驱动流程（serve 子进程 + deterministic，无真实 LLM）
# ---------------------------------------------------------------------------


def test_serve_panel_api_flow(serve_process, tmp_path):
    """REST 全链路：status/projects/sessions → interrupt → changes → rollback → audit/SSE。"""
    base = serve_process
    workspace = tmp_path / "workspace-a"
    workspace.mkdir()

    status, body = _request("GET", f"{base}/api/v1/status")
    assert status == 200 and body["ok"] is True
    assert body["data"]["version"] == "0.7.2"

    status, body = _request("POST", f"{base}/api/v1/projects", {"name": "t12-12", "workspace": str(workspace)})
    assert status == 201, body
    project_id = body["data"]["id"]

    status, body = _request(
        "POST",
        f"{base}/api/v1/projects/{project_id}/sessions",
        {"goal": "验收：确定性构建最小 CLI 工具 minitool", "deterministic": True, "model": "deterministic"},
    )
    assert status == 201, body
    session_id = body["data"]["session_id"]

    # 会话应挂起在首个审批门（HITL 桥接生效，等待人工审批）
    snapshot = _wait_session(base, session_id, {"waiting_approval"}, timeout=60)
    assert snapshot["status"] == "waiting_approval"
    assert "审批门" in (snapshot["pending_hint"] or "")

    # 触发 interrupt（需求变更）→ 202，随后变更历史出现 v1
    status, body = _request(
        "POST", f"{base}/api/v1/sessions/{session_id}/interrupt", {"text": "增加 --json 输出参数"}
    )
    assert status == 202, body
    records: list[dict] = []
    deadline = time.time() + 60
    while time.time() < deadline:
        status, body = _request("GET", f"{base}/api/v1/sessions/{session_id}/changes")
        records = body["data"]["records"]
        if records:
            break
        time.sleep(0.5)
    assert records, "interrupt 后应产生至少一条需求变更记录"
    assert body["data"]["summary"]["count"] >= 1
    version = records[-1]["version"]

    # rollback 指定版本
    status, body = _request("POST", f"{base}/api/v1/sessions/{session_id}/rollback", {"version": version})
    assert status == 200, body
    assert body["data"]["rolled_back"] == version

    # 审计数据（GET）与审计导出（POST /audit/export）
    status, body = _request("GET", f"{base}/api/v1/sessions/{session_id}/audit")
    assert status == 200, body
    for key in ("events", "approvals", "token_summary", "changes", "spans", "cost"):
        assert key in body["data"], f"audit 缺字段 {key}"
    status, body = _request("POST", f"{base}/api/v1/sessions/{session_id}/audit/export")
    assert status == 200, body
    files = body["data"]["files"]
    assert files.get("stamp")
    for key in ("markdown", "json"):
        assert files.get(key) and Path(files[key]).is_file(), f"导出文件缺失：{key}"

    # SSE 端点可连接且可读到事件（会话未结束，读到缓冲后关闭连接）
    status_code, content_type, chunk = _sse_chunk(base, session_id)
    assert status_code == 200
    assert content_type.startswith("text/event-stream")
    assert "session.start" in chunk


def test_serve_session_completes_with_yes(serve_process, tmp_path):
    """deterministic+yes 会话可完整跑通（无需人工审批），完成后审计导出可用。"""
    base = serve_process
    workspace = tmp_path / "workspace-b"
    workspace.mkdir()

    status, body = _request("POST", f"{base}/api/v1/projects", {"name": "t12-12-yes", "workspace": str(workspace)})
    assert status == 201, body
    project_id = body["data"]["id"]

    status, body = _request(
        "POST",
        f"{base}/api/v1/projects/{project_id}/sessions",
        {"goal": "验收：确定性构建最小 CLI 工具（自动审批）", "deterministic": True, "yes": True, "model": "deterministic"},
    )
    assert status == 201, body
    session_id = body["data"]["session_id"]

    snapshot = _wait_session(base, session_id, {"completed"}, timeout=120)
    assert snapshot["status"] == "completed", snapshot
    # 占位产物模式下允许 exit_code=1（存在验收未过任务），流程本身已完整结束
    assert snapshot["exit_code"] in (0, 1), snapshot
    assert (workspace / "DELIVERY.md").is_file()

    status, body = _request("POST", f"{base}/api/v1/sessions/{session_id}/audit/export")
    assert status == 200, body
    assert body["data"]["files"].get("json")


def test_serve_post_approve_resumes_session(serve_process, tmp_path):
    """回归 T12.12：POST approve 应 200（submitted=accept）且会话越过首道审批门继续。"""
    base = serve_process
    workspace = tmp_path / "workspace-approve"
    workspace.mkdir()

    status, body = _request("POST", f"{base}/api/v1/projects", {"name": "t12-12-approve", "workspace": str(workspace)})
    assert status == 201, body
    project_id = body["data"]["id"]

    status, body = _request(
        "POST",
        f"{base}/api/v1/projects/{project_id}/sessions",
        {"goal": "验收：确定性构建最小 CLI 工具（人工审批）", "deterministic": True, "model": "deterministic"},
    )
    assert status == 201, body
    session_id = body["data"]["session_id"]

    snapshot = _wait_session(base, session_id, {"waiting_approval"}, timeout=60)
    assert snapshot["status"] == "waiting_approval"
    assert snapshot["gate_count"] >= 1, snapshot

    status, body = _request("POST", f"{base}/api/v1/sessions/{session_id}/approve")
    assert status == 200, body
    assert body["ok"] is True
    assert body["data"]["submitted"] == "accept"

    # 会话继续：approve 被消费后应推进到下一道审批门（requirement → design_review）
    deadline = time.time() + 90
    last = snapshot
    while time.time() < deadline:
        status, body = _request("GET", f"{base}/api/v1/sessions/{session_id}")
        last = body["data"]
        if last["gate_count"] >= 2 or last["status"] in ("completed", "failed"):
            break
        time.sleep(0.3)
    assert last["gate_count"] >= 2, f"approve 后未推进到下一道审批门：{last}"
    assert "requirement_confirmation" not in (last["pending_hint"] or "")


def test_serve_post_reject_edit_response_routes(serve_process, tmp_path):
    """回归 T12.12：reject/edit/response 的 5 段路径应 200 且按契约提交答案（不再 404）。"""
    base = serve_process
    workspace = tmp_path / "workspace-answers"
    workspace.mkdir()

    status, body = _request("POST", f"{base}/api/v1/projects", {"name": "t12-12-answers", "workspace": str(workspace)})
    assert status == 201, body
    project_id = body["data"]["id"]

    status, body = _request(
        "POST",
        f"{base}/api/v1/projects/{project_id}/sessions",
        {"goal": "验收：确定性构建最小 CLI 工具（答案路由）", "deterministic": True, "model": "deterministic"},
    )
    assert status == 201, body
    session_id = body["data"]["session_id"]

    snapshot = _wait_session(base, session_id, {"waiting_approval"}, timeout=60)
    assert snapshot["status"] == "waiting_approval"

    # response 携带 text → 200 且 submitted 为 "response <text>"，并推进到下一道门
    status, body = _request(
        "POST", f"{base}/api/v1/sessions/{session_id}/response", {"text": "补充验收标准"}
    )
    assert status == 200, body
    assert body["data"]["submitted"] == "response 补充验收标准"
    deadline = time.time() + 90
    last = snapshot
    while time.time() < deadline:
        status, body = _request("GET", f"{base}/api/v1/sessions/{session_id}")
        last = body["data"]
        if last["gate_count"] >= 2 or last["status"] in ("completed", "failed"):
            break
        time.sleep(0.3)
    assert last["gate_count"] >= 2, f"response 后未推进到下一道审批门：{last}"

    # edit 携带 text → 200 且 submitted 为 "edit <text>"（编辑触发返工，不做推进断言）
    status, body = _request(
        "POST", f"{base}/api/v1/sessions/{session_id}/edit", {"text": "调整输出格式"}
    )
    assert status == 200, body
    assert body["data"]["submitted"] == "edit 调整输出格式"

    # reject → 200 且 submitted 为 "reject"
    status, body = _request("POST", f"{base}/api/v1/sessions/{session_id}/reject")
    assert status == 200, body
    assert body["data"]["submitted"] == "reject"


# ---------------------------------------------------------------------------
# b) frontend 构建产物存在性
# ---------------------------------------------------------------------------


def test_frontend_build_artifact_exists():
    """React 工作台构建产物 frontend/dist/index.html 必须存在。"""
    if not FRONTEND_INDEX.is_file():
        pytest.skip("frontend/dist 缺失（未执行前端构建，该检查由 frontend-test job 覆盖）")
    assert FRONTEND_INDEX.is_file(), "frontend/dist/index.html 缺失（先执行前端构建）"
    assert FRONTEND_INDEX.stat().st_size > 0


# ---------------------------------------------------------------------------
# c) Electron 冒烟可重复性（未安装 electron 时 pytest.skip，见模块 docstring）
# ---------------------------------------------------------------------------


def test_electron_smoke_repeatable():
    """Electron 壳 --smoke 冒烟：后端就绪即退出；重复执行应稳定 exit 0。"""
    if not ELECTRON_BIN.exists():
        pytest.skip("desktop 未安装 electron（desktop/node_modules/.bin/electron 不存在）")
    # 注意：不覆盖 USERPROFILE/HOME——Electron/Chromium 在 Windows 上需要真实用户目录，
    # 覆盖会导致进程以 0x80000003 立即崩溃；冒烟仅启动后端并退出，不影响真实索引数据。
    proc = subprocess.run(
        [str(ELECTRON_BIN), ".", "--smoke"],
        cwd=str(DESKTOP_DIR),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
    )
    output = (proc.stdout or "") + (proc.stderr or "")
    assert proc.returncode == 0, f"electron --smoke 退出码 {proc.returncode}：{output[-800:]}"
    assert "SMOKE OK" in output
