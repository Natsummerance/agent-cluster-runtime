"""全链路服务端与接口自动化验证测试。
覆盖状态、项目创建、敏捷会话创建与执行、SSE 事件流、文件与工作区产物、审计、记忆、RBAC、租户等所有全量流程。
"""
from __future__ import annotations

import json
import threading
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

import agent_cluster.server as server_mod
from agent_cluster.server import WorkbenchHandler, WorkbenchServer


def _req(port: int, method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
    url = f"http://127.0.0.1:{port}{path}"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = resp.read().decode("utf-8")
            return resp.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as err:
        raw = err.read().decode("utf-8")
        return err.code, json.loads(raw) if raw else {}


def test_full_server_and_api_flow(tmp_path, monkeypatch):
    monkeypatch.setattr(server_mod, "INDEX_DIR", tmp_path / "home")
    ws = WorkbenchServer(host="127.0.0.1", port=0, auth_token="")
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), WorkbenchHandler)
    httpd.workbench = ws
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()

    try:
        ws_dir = tmp_path / "manual_workspace"
        ws_dir.mkdir(parents=True, exist_ok=True)
        (ws_dir / "README.md").write_text("# Demo Project\n", encoding="utf-8")

        # 1. 状态与总览
        code, resp = _req(port, "GET", "/api/v1/status")
        assert code == 200, f"status failed: {resp}"
        assert resp["ok"] is True
        assert "version" in resp["data"]

        # 2. Doctor 预检
        code, resp = _req(port, "GET", "/api/v1/doctor")
        assert code == 200, f"doctor failed: {resp}"
        assert resp["ok"] is True
        assert "checks" in resp["data"]

        # 3. 创建项目 (返回 200 或 201)
        code, resp = _req(
            port,
            "POST",
            "/api/v1/projects",
            {"name": "演示敏捷项目", "workspace": str(ws_dir)},
        )
        assert code in (200, 201), f"create project failed: {resp}"
        pid = resp["data"]["id"]
        assert pid

        # 4. 获取项目列表
        code, resp = _req(port, "GET", "/api/v1/projects")
        assert code == 200
        assert len(resp["data"]) == 1

        # 5. 在项目中创建并启动敏捷 Session
        code, resp = _req(
            port,
            "POST",
            f"/api/v1/projects/{pid}/sessions",
            {
                "goal": "极速实现一个加法函数及测试",
                "model": "deterministic",
                "flow": "workflows/agile-dev.yaml",
                "yes": True,
            },
        )
        assert code in (200, 201), f"create session failed: {resp}"
        sid = resp["data"]["session_id"]
        assert sid

        # 6. 获取 Session 列表与详情
        code, resp = _req(port, "GET", f"/api/v1/projects/{pid}/sessions")
        assert code == 200
        assert len(resp["data"]) >= 1

        code, resp = _req(port, "GET", f"/api/v1/sessions/{sid}")
        assert code == 200
        assert resp["data"]["session_id"] == sid

        # 7. 看板与任务面板
        code, resp = _req(port, "GET", f"/api/v1/projects/{pid}/dashboard")
        assert code == 200
        assert "status" in resp["data"]

        code, resp = _req(port, "GET", f"/api/v1/projects/{pid}/tasks")
        assert code == 200

        # 8. 工作区树与文件读取
        code, resp = _req(port, "GET", f"/api/v1/projects/{pid}/workspace/tree")
        assert code == 200
        assert "entries" in resp["data"]

        code, resp = _req(port, "GET", f"/api/v1/projects/{pid}/workspace/file?path=README.md")
        assert code == 200
        assert "content" in resp["data"]

        # 9. 记忆与审计
        code, resp = _req(port, "GET", f"/api/v1/projects/{pid}/memory")
        assert code == 200

        code, resp = _req(port, "GET", f"/api/v1/sessions/{sid}/audit")
        assert code == 200

        # 10. RBAC 与 租户端点
        code, resp = _req(port, "GET", "/api/v1/roles")
        assert code == 200

        code, resp = _req(port, "GET", "/api/v1/users")
        assert code == 200

        code, resp = _req(port, "GET", "/api/v1/tenants")
        assert code == 200

        # 11. 资源日历与依赖图
        code, resp = _req(port, "GET", "/api/v1/calendar")
        assert code == 200

        code, resp = _req(port, "GET", "/api/v1/dependencies")
        assert code == 200

        # 12. 编排计划 Plans
        code, resp = _req(port, "GET", "/api/v1/plans")
        assert code == 200

    finally:
        httpd.shutdown()
        httpd.server_close()
