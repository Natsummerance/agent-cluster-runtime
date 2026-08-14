"""Task 14.13 K8s/Helm + GHCR 镜像发布：Dockerfile 多阶段、Helm chart 模板、CI GHCR job 静态断言。

约定：不真跑 docker；helm template 需本机 helm（CI backend-test 已装 azure/setup-helm）。
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
DOCKERFILE = REPO_ROOT / "Dockerfile"
CHART_DIR = REPO_ROOT / "deploy" / "helm" / "agent-cluster"
CI_YML = REPO_ROOT / ".github" / "workflows" / "ci.yml"

IMAGE_REF = "ghcr.io/natsummerance/agent-cluster-runtime"

requires_helm = pytest.mark.skipif(
    shutil.which("helm") is None,
    reason="本机未安装 helm，跳过 helm template 断言（本地验证需 helm）",
)


def _helm_template(*extra: str) -> str:
    """helm template 渲染 chart，返回 stdout 文本。"""
    proc = subprocess.run(
        ["helm", "template", "agent-cluster", str(CHART_DIR), *extra],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, f"helm template 失败：\n{proc.stderr}"
    return proc.stdout


# ---------------------------------------------------------------------------
# Dockerfile：多阶段 + 健康检查
# ---------------------------------------------------------------------------


def test_dockerfile_exists_and_is_multi_stage():
    text = DOCKERFILE.read_text(encoding="utf-8")
    stages = [ln for ln in text.splitlines() if ln.strip().upper().startswith("FROM ")]
    assert len(stages) >= 2, "Dockerfile 必须是多阶段（builder/runtime 至少两段）"
    assert any("as builder" in ln.lower() for ln in stages), "缺少 builder 阶段"
    assert any("as runtime" in ln.lower() for ln in stages), "缺少 runtime 阶段"
    assert ("python3.11" in text) or ("python:3.11" in text), "后端镜像应基于 Python 3.11"


def test_dockerfile_healthcheck_and_serve_command():
    text = DOCKERFILE.read_text(encoding="utf-8")
    assert "HEALTHCHECK" in text, "缺少 HEALTHCHECK"
    assert "/api/v1/status" in text, "健康检查必须探测 GET /api/v1/status"
    assert "EXPOSE 8765" in text, "缺少 EXPOSE 8765"
    assert 'CMD ["agent-cluster", "serve"' in text, "默认命令必须是 agent-cluster serve"
    assert "--host" in text and "0.0.0.0" in text, "容器内 serve 必须监听 0.0.0.0"


def test_dockerfile_frozen_uv_install():
    text = DOCKERFILE.read_text(encoding="utf-8")
    assert "uv sync --frozen" in text, "依赖安装必须按 uv.lock 冻结（uv sync --frozen）"
    assert "pyproject.toml" in text and "uv.lock" in text


# ---------------------------------------------------------------------------
# Helm chart：文件齐全 + 元数据
# ---------------------------------------------------------------------------


def test_chart_files_present():
    chart = CHART_DIR / "Chart.yaml"
    assert chart.is_file(), "缺少 Chart.yaml"
    meta = chart.read_text(encoding="utf-8")
    assert "apiVersion: v2" in meta
    assert "name: agent-cluster" in meta
    assert 'appVersion: "0.7.1"' in meta
    assert (CHART_DIR / "values.yaml").is_file()
    for tmpl in ("deployment", "service", "ingress", "secrets", "pvc"):
        assert (CHART_DIR / "templates" / f"{tmpl}.yaml").is_file(), f"缺少 templates/{tmpl}.yaml"
    assert (CHART_DIR / "templates" / "_helpers.tpl").is_file()


@requires_helm
def test_helm_template_renders_deployment_service_ingress():
    out = _helm_template()
    assert "kind: Deployment" in out
    assert "kind: Service" in out
    assert f"{IMAGE_REF}:v0.7.1" in out, "默认镜像 tag 应为 v0.7.1"
    assert "livenessProbe:" in out and "readinessProbe:" in out
    assert "path: /api/v1/status" in out, "探针必须探测 /api/v1/status"
    assert "containerPort: 8765" in out
    assert "0.0.0.0" in out, "deployment 必须传 --host 0.0.0.0"
    # ingress/pvc 默认关闭（values 惯例）；显式启用后必须渲染
    with_ingress = _helm_template("--set", "ingress.enabled=true")
    assert "kind: Ingress" in with_ingress
    assert "agent-cluster.local" in with_ingress
    with_pvc = _helm_template("--set", "persistence.enabled=true")
    assert "kind: PersistentVolumeClaim" in with_pvc
    assert "mountPath: /data" in with_pvc


@requires_helm
def test_helm_template_auth_token_creates_secret_and_probe_header():
    out = _helm_template("--set", "auth.token=s3cr3t-t0ken")
    assert "kind: Secret" in out, "设置 auth.token 必须渲染 Secret"
    assert 'auth-token: "s3cr3t-t0ken"' in out
    assert "--auth-token" in out, "deployment 必须带 --auth-token 参数"
    assert "AGENT_CLUSTER_AUTH_TOKEN" in out, "token 必须经 env 注入"
    assert "X-Auth-Token" in out, "启用认证后探针必须带 X-Auth-Token 头"


@requires_helm
def test_helm_template_custom_image_tag():
    out = _helm_template("--set", "image.tag=v9.9.9")
    assert f"{IMAGE_REF}:v9.9.9" in out, "image.tag 覆盖后镜像引用应更新"


# ---------------------------------------------------------------------------
# CI：GHCR 发布 job
# ---------------------------------------------------------------------------


def test_ci_ghcr_publish_job_present():
    text = CI_YML.read_text(encoding="utf-8")
    assert "publish-ghcr:" in text, "ci.yml 缺少 publish-ghcr job"
    assert "startsWith(github.ref, 'refs/tags/v')" in text, "GHCR job 必须 tag v* 触发"
    assert "docker/login-action@v3" in text, "缺少 GHCR 登录"
    assert "registry: ghcr.io" in text
    assert "docker/build-push-action@v6" in text, "缺少 build-push"
    assert IMAGE_REF in text, "推送目标必须是 ghcr.io/natsummerance/agent-cluster-runtime"
    assert "packages: write" in text, "GHCR job 需要 packages: write 权限"
    assert "GITHUB_TOKEN" in text, "登录必须用 GITHUB_TOKEN"


def test_ci_backend_test_installs_helm():
    text = CI_YML.read_text(encoding="utf-8")
    assert "azure/setup-helm@v4" in text, "backend-test job 必须安装 helm 供 chart 测试"
