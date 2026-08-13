"""T12.11 一键演示 + serve 面板接线（plugins/skills/mcp）+ 版本 0.6.2。"""

from __future__ import annotations

import json
import subprocess
import sys
import threading
import urllib.request
import urllib.error
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

import agent_cluster.server as server_mod
from agent_cluster.server import WorkbenchHandler, WorkbenchServer

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def workbench(tmp_path, monkeypatch):
    monkeypatch.setattr(server_mod, "INDEX_DIR", tmp_path / "home")
    ws = WorkbenchServer(host="127.0.0.1", port=0, auth_token="", plugins_dir=[], mcp_servers=["fs=npx x"], mcp_http_servers=["remote=http://example.com/mcp"])
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), WorkbenchHandler)
    httpd.workbench = ws
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield port, ws
    httpd.shutdown()
    httpd.server_close()


def _get(port, path):
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def test_serve_plugins_endpoint(workbench):
    port, _ = workbench
    status, body = _get(port, "/api/v1/plugins")
    assert status == 200
    assert "plugins" in body["data"]
    assert isinstance(body["data"]["plugins"], list)


def test_serve_skills_endpoint(workbench):
    port, _ = workbench
    status, body = _get(port, "/api/v1/skills")
    assert status == 200
    assert "skills" in body["data"]
    assert isinstance(body["data"]["skills"], list)


def test_serve_mcp_endpoint_lists_configured_servers(workbench):
    port, _ = workbench
    status, body = _get(port, "/api/v1/mcp")
    assert status == 200
    assert body["data"]["stdio"] == ["fs=npx x"]
    assert body["data"]["http"] == ["remote=http://example.com/mcp"]


def test_serve_status_returns_version(workbench):
    port, _ = workbench
    status, body = _get(port, "/api/v1/status")
    assert status == 200
    assert body["data"]["version"] == "0.6.2"


def test_pyproject_version_is_060():
    text = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert 'version = "0.6.2"' in text


def test_demo_command_produces_delivery_package(tmp_path):
    """demo 子命令：确定性一键演示产出完整交付包 + git 提交 + token 报表。"""
    ws = tmp_path / "demo-ws"
    proc = subprocess.run(
        [sys.executable, "-m", "agent_cluster", "demo", "--workspace", str(ws)],
        capture_output=True, text=True, encoding="utf-8", timeout=240, cwd=REPO_ROOT,
    )
    assert proc.returncode == 0, proc.stderr[-800:]
    assert (ws / "DELIVERY.md").is_file()
    assert (ws / "app.py").is_file()
    assert (ws / "docs" / "PRD.md").is_file()
    assert (ws / "test_app.py").is_file()
    delivery = (ws / "DELIVERY.md").read_text(encoding="utf-8")
    assert "Token 计量表" in delivery
    # git 提交存在
    git_log = subprocess.run(
        ["git", "log", "--oneline", "-1"], cwd=ws, capture_output=True, text=True, encoding="utf-8"
    )
    assert (git_log.stdout or "").strip()

def test_serve_cors_preflight_allows_browser_origin(workbench):
    """浏览器（vite dev 5173 / 桌面 file://）跨域预检必须通过：ACAO=* + 允许 X-Auth-Token。"""
    port, _ = workbench
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/api/v1/projects",
        method="OPTIONS",
        headers={
            "Origin": "http://127.0.0.1:5173",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "x-auth-token,content-type",
        },
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        assert resp.status in (200, 204)
        assert resp.headers.get("Access-Control-Allow-Origin") == "*"
        allow = (resp.headers.get("Access-Control-Allow-Headers") or "").lower()
        assert "x-auth-token" in allow
        assert "content-type" in allow
        methods = (resp.headers.get("Access-Control-Allow-Methods") or "").upper()
        assert "GET" in methods and "POST" in methods


def test_serve_json_responses_carry_cors_header(workbench):
    port, _ = workbench
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/api/v1/status",
        headers={"Origin": "http://127.0.0.1:5173"},
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        assert resp.headers.get("Access-Control-Allow-Origin") == "*"

def test_serve_dashboard_empty_axes_for_index_only_project(workbench):
    """旧项目（全局索引有、ProjectStore 未双写）dashboard 返回空三轴而非 404。"""
    port, ws = workbench
    ws.index.add_project("legacy1", "旧项目", "C:/tmp/legacy1")
    status, body = _get(port, "/api/v1/projects/legacy1/dashboard")
    assert status == 200
    assert body["data"]["cost"]["status"] == "ok"
    assert body["data"]["progress"]["phases"] == {"total": 0, "done": 0}
    assert body["data"]["health"]["sessions"] == {}


def test_serve_dashboard_404_for_unknown_project(workbench):
    port, _ = workbench
    status, body = _get(port, "/api/v1/projects/doesnotexist/dashboard")
    assert status == 404
    assert body["code"] == "not_found"

def test_serve_tasks_empty_for_index_only_project(workbench):
    """旧项目（索引有、未双写）tasks 返回空列表而非 404。"""
    port, ws = workbench
    ws.index.add_project("legacy1", "旧项目", "C:/tmp/legacy1")
    status, body = _get(port, "/api/v1/projects/legacy1/tasks")
    assert status == 200
    assert body["data"] == []


def test_serve_budget_default_for_index_only_project(workbench):
    port, ws = workbench
    ws.index.add_project("legacy1", "旧项目", "C:/tmp/legacy1")
    status, body = _get(port, "/api/v1/projects/legacy1/budget")
    assert status == 200
    assert body["data"]["hard_limit_tokens"] == 0
    assert body["data"]["used"] == 0
    assert body["data"]["unlocks"] == []


def test_serve_workspace_tree_works_for_index_only_project(workbench, tmp_path):
    """旧项目 workspace 走全局索引路径：文件树可读（防回归：不得 500）。"""
    port, ws = workbench
    root = tmp_path / "legacy-ws"
    root.mkdir()
    (root / "readme.md").write_text("hi", encoding="utf-8")
    ws.index.add_project("legacy1", "旧项目", str(root))
    status, body = _get(port, "/api/v1/projects/legacy1/workspace/tree")
    assert status == 200
    names = [entry["name"] for entry in body["data"]["entries"]]
    assert "readme.md" in names


def test_serve_memory_empty_for_index_only_project(workbench, tmp_path):
    port, ws = workbench
    root = tmp_path / "legacy-ws"
    root.mkdir()
    ws.index.add_project("legacy1", "旧项目", str(root))
    status, body = _get(port, "/api/v1/projects/legacy1/memory")
    assert status == 200
    assert body["data"]["items"] == []