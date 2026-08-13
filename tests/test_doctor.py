"""T11.1 预检（doctor）与 wire_api 配置测试。

- ``ModelConfig.wire_api`` 字段：默认 chat，接受 responses/anthropic。
- ``ChatModelFactory`` 路由守卫：wire_api=responses/anthropic 在 T11.1 明确报错。
- ``doctor``：python/git/docker 硬依赖 + model/workspace/plugins/mcp 信息性；
  Docker 缺失非零退出、--skip-docker-check 放行；docker_available 供 skipif。
"""

from __future__ import annotations

import pytest

from agent_cluster.cli import main
from agent_cluster.doctor import (
    CheckResult,
    DoctorReport,
    docker_cli_present,
    docker_daemon_ok,
    docker_available,
    run_doctor,
)
from agent_cluster.models import AgentConfig, ModelConfig
from agent_cluster.runtime import ChatModelFactory


# ---------------------------------------------------------------------------
# ModelConfig.wire_api
# ---------------------------------------------------------------------------


def test_model_config_wire_api_defaults_to_chat():
    cfg = ModelConfig(model_name="deterministic")
    assert cfg.wire_api == "chat"


def test_model_config_wire_api_accepts_all_protocols():
    for wire in ("chat", "responses", "anthropic"):
        assert ModelConfig(model_name="gpt-4o-mini", wire_api=wire).wire_api == wire


def test_model_config_wire_api_rejects_unknown():
    with pytest.raises(Exception):
        ModelConfig(model_name="gpt-4o-mini", wire_api="grpc")


def test_factory_deterministic_ignores_wire_api():
    cfg = AgentConfig(model=ModelConfig(model_name="deterministic", wire_api="responses"))
    client = ChatModelFactory().create(cfg)
    from agent_cluster.runtime import DeterministicClient

    assert isinstance(client, DeterministicClient)


# ---------------------------------------------------------------------------
# doctor 检查项
# ---------------------------------------------------------------------------


def test_doctor_report_ok_requires_required_checks():
    report = DoctorReport()
    report.checks.append(CheckResult(name="a", ok=True, required=True))
    report.checks.append(CheckResult(name="b", ok=False, required=False))
    assert report.ok  # 信息性失败不阻断


def test_doctor_report_fails_on_required_check():
    report = DoctorReport()
    report.checks.append(CheckResult(name="a", ok=True, required=True))
    report.checks.append(CheckResult(name="b", ok=False, required=True))
    assert not report.ok


def test_docker_cli_present_detects_missing(monkeypatch):
    monkeypatch.setattr("agent_cluster.doctor.shutil.which", lambda name: None if name == "docker" else "/usr/bin/git")
    assert not docker_cli_present()


def test_docker_available_requires_cli_and_daemon(monkeypatch):
    monkeypatch.setattr("agent_cluster.doctor.docker_cli_present", lambda: True)
    monkeypatch.setattr("agent_cluster.doctor.docker_daemon_ok", lambda: True)
    assert docker_available()
    monkeypatch.setattr("agent_cluster.doctor.docker_daemon_ok", lambda: False)
    assert not docker_available()
    monkeypatch.setattr("agent_cluster.doctor.docker_cli_present", lambda: False)
    assert not docker_available()


def test_docker_daemon_ok_false_when_cli_missing(monkeypatch):
    monkeypatch.setattr("agent_cluster.doctor.docker_cli_present", lambda: False)
    assert not docker_daemon_ok()


def test_docker_daemon_ok_calls_docker_info(monkeypatch):
    import subprocess

    monkeypatch.setattr("agent_cluster.doctor.docker_cli_present", lambda: True)
    monkeypatch.setattr(
        "agent_cluster.doctor.subprocess.run",
        lambda *a, **k: type("R", (), {"returncode": 0})(),
    )
    assert docker_daemon_ok()


def test_run_doctor_docker_missing_fails_with_guidance(monkeypatch):
    monkeypatch.setattr("agent_cluster.doctor.docker_cli_present", lambda: False)
    report = run_doctor()
    docker = next(c for c in report.checks if c.name == "docker")
    assert not docker.ok and docker.required
    assert "Docker Desktop" in docker.detail
    assert not report.ok


def test_run_doctor_skip_docker_check_passes(monkeypatch):
    monkeypatch.setattr("agent_cluster.doctor.docker_cli_present", lambda: False)
    report = run_doctor(skip_docker_check=True)
    docker = next(c for c in report.checks if c.name == "docker")
    assert docker.ok


def test_run_doctor_git_missing_is_required(monkeypatch):
    monkeypatch.setattr("agent_cluster.doctor.shutil.which", lambda name: None if name == "git" else "/usr/bin/docker")
    monkeypatch.setattr("agent_cluster.doctor.docker_cli_present", lambda: True)
    monkeypatch.setattr("agent_cluster.doctor.docker_daemon_ok", lambda: True)
    report = run_doctor()
    git = next(c for c in report.checks if c.name == "git")
    assert not git.ok and git.required


def test_run_doctor_model_warning_not_blocking(monkeypatch):
    monkeypatch.setattr("agent_cluster.doctor.docker_cli_present", lambda: True)
    monkeypatch.setattr("agent_cluster.doctor.docker_daemon_ok", lambda: True)
    report = run_doctor(model="definitely-unknown-model-xyz")
    model = next(c for c in report.checks if c.name == "model")
    assert not model.ok and not model.required


def test_run_doctor_workspace_missing_parent_ok(monkeypatch, tmp_path):
    monkeypatch.setattr("agent_cluster.doctor.docker_cli_present", lambda: True)
    monkeypatch.setattr("agent_cluster.doctor.docker_daemon_ok", lambda: True)
    target = tmp_path / "not-there" / "ws"
    report = run_doctor(workspace=str(target))
    ws = next(c for c in report.checks if c.name == "workspace")
    assert ws.ok


def test_run_doctor_plugin_dir_missing_warns(monkeypatch, tmp_path):
    monkeypatch.setattr("agent_cluster.doctor.docker_cli_present", lambda: True)
    monkeypatch.setattr("agent_cluster.doctor.docker_daemon_ok", lambda: True)
    report = run_doctor(plugin_dirs=[str(tmp_path / "nope")])
    plugins = next(c for c in report.checks if c.name == "plugins")
    assert not plugins.ok and not plugins.required


def test_run_doctor_mcp_bad_spec_warns(monkeypatch):
    monkeypatch.setattr("agent_cluster.doctor.docker_cli_present", lambda: True)
    monkeypatch.setattr("agent_cluster.doctor.docker_daemon_ok", lambda: True)
    report = run_doctor(mcp_servers=["bad-spec-no-equals"])
    mcp = next(c for c in report.checks if c.name == "mcp")
    assert not mcp.ok and not mcp.required


def test_doctor_render_contains_checks():
    report = DoctorReport()
    report.checks.append(CheckResult(name="python", ok=True, detail="Python 3.11"))
    report.checks.append(CheckResult(name="docker", ok=False, detail="未安装", required=True))
    text = report.render()
    assert "[通过] python" in text
    assert "[失败] docker" in text
    assert "结论" in text


# ---------------------------------------------------------------------------
# CLI doctor 退出码
# ---------------------------------------------------------------------------


def test_cli_doctor_returns_1_when_docker_missing(monkeypatch):
    monkeypatch.setattr("agent_cluster.doctor.docker_cli_present", lambda: False)
    assert main(["doctor"]) == 1


def test_cli_doctor_returns_0_when_all_required_ok(monkeypatch):
    monkeypatch.setattr("agent_cluster.doctor.docker_cli_present", lambda: True)
    monkeypatch.setattr("agent_cluster.doctor.docker_daemon_ok", lambda: True)
    assert main(["doctor", "--model", "deterministic"]) == 0


# ---------------------------------------------------------------------------
# T13.13 Docker 自动安装联动（action 字段 + doctor --fix-docker）
# ---------------------------------------------------------------------------


def test_docker_action_field_when_unavailable(monkeypatch):
    """Docker 不可用时 docker 检查项带 action（平台脚本路径），报告 render 含该路径。"""
    monkeypatch.setattr("agent_cluster.doctor.docker_cli_present", lambda: False)
    report = run_doctor()
    docker = next(c for c in report.checks if c.name == "docker")
    assert not docker.ok
    assert docker.action
    assert docker.action.startswith("scripts/install-docker")
    assert docker.action in report.render()


def test_fix_docker_runner_runs_then_rechecks(monkeypatch):
    """注入 fake runner：先执行脚本、后重查 docker；runner 成功时报告保留修复结果。"""
    monkeypatch.setattr("agent_cluster.doctor.docker_cli_present", lambda: False)
    calls: list[str] = []

    def fake_runner(command: str):
        calls.append(command)
        return (0, "installed ok")

    report = run_doctor(fix_docker=True, script_runner=fake_runner)
    assert len(calls) == 1
    assert "install-docker" in calls[0]
    assert report.fix_result == (0, "installed ok")
    docker = next(c for c in report.checks if c.name == "docker")
    assert not docker.ok  # 重查仍失败（环境未变），但已执行修复
    assert docker.action


def test_fix_docker_runner_failure_reported(monkeypatch):
    """runner 失败 → 报告 not ok 且含 stderr，fix_result 透传。"""
    monkeypatch.setattr("agent_cluster.doctor.docker_cli_present", lambda: False)
    report = run_doctor(fix_docker=True, script_runner=lambda command: (1, "install failed: boom"))
    assert not report.ok
    assert report.fix_result == (1, "install failed: boom")


def test_fix_docker_idempotent_when_ok(monkeypatch):
    """Docker 已可用时 fix 直接短路（不调 runner）。"""
    monkeypatch.setattr("agent_cluster.doctor.docker_cli_present", lambda: True)
    monkeypatch.setattr("agent_cluster.doctor.docker_daemon_ok", lambda: True)

    def unexpected_runner(_command):
        raise AssertionError("已可用时不应执行修复脚本")

    report = run_doctor(fix_docker=True, script_runner=unexpected_runner)
    assert report.ok
    assert report.fix_result is None
    docker = next(c for c in report.checks if c.name == "docker")
    assert docker.ok


def test_fix_docker_rejects_non_whitelist_script(monkeypatch):
    """fix 只执行白名单脚本；非白名单路径拒绝且不调用 runner。"""
    monkeypatch.setattr("agent_cluster.doctor.docker_cli_present", lambda: False)
    monkeypatch.setattr("agent_cluster.doctor.docker_action_for_platform", lambda: "scripts/evil.sh")
    calls: list[str] = []
    report = run_doctor(fix_docker=True, script_runner=lambda command: calls.append(command) or (0, ""))
    assert calls == []
    assert report.fix_result is not None
    assert report.fix_result[0] != 0
    assert "白名单" in report.fix_result[1]


# ---------------------------------------------------------------------------
# T13.13 serve 端点：GET /api/v1/doctor 与 POST /api/v1/doctor/fix-docker
# ---------------------------------------------------------------------------


def _doctor_server(tmp_path, monkeypatch, auth_token=""):
    import json
    import threading
    import urllib.error
    import urllib.request
    from http.server import ThreadingHTTPServer

    from agent_cluster.server import WorkbenchHandler, WorkbenchServer

    monkeypatch.setattr("agent_cluster.server.INDEX_DIR", tmp_path / "home")
    ws = WorkbenchServer(host="127.0.0.1", port=0, auth_token=auth_token)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), WorkbenchHandler)
    httpd.workbench = ws
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()

    def request(method, path, token=None):
        req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", method=method)
        if token:
            req.add_header("X-Auth-Token", token)
        try:
            with urllib.request.urlopen(req, timeout=8) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))

    yield request
    httpd.shutdown()
    httpd.server_close()


def test_doctor_get_endpoint_returns_report_with_action(tmp_path, monkeypatch):
    for request in _doctor_server(tmp_path, monkeypatch):
        status, payload = request("GET", "/api/v1/doctor")
        assert status == 200 and payload["ok"] is True
        checks = payload["data"]["checks"]
        assert checks
        docker = next(c for c in checks if c["name"] == "docker")
        assert "action" in docker
        assert docker["required"] is True
        return


def test_doctor_fix_endpoint_auth_and_short_circuit(tmp_path, monkeypatch):
    # Docker 已可用 → fix 短路（不执行真实安装脚本），带正确 token 返回 200 报告
    monkeypatch.setattr("agent_cluster.doctor.docker_cli_present", lambda: True)
    monkeypatch.setattr("agent_cluster.doctor.docker_daemon_ok", lambda: True)
    for request in _doctor_server(tmp_path, monkeypatch, auth_token="secret"):
        status, payload = request("POST", "/api/v1/doctor/fix-docker", token="secret")
        assert status == 200 and payload["ok"] is True
        assert payload["data"]["fix"] is None
        assert payload["data"]["ok"] is True
        # 错 token → 401
        status, payload = request("POST", "/api/v1/doctor/fix-docker", token="wrong")
        assert status == 401
        return
