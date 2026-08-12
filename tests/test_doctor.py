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
