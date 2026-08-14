# agent-cluster-runtime 后端镜像（Task 14.13）
# 多阶段：builder 用 uv 按 uv.lock 冻结安装依赖 -> runtime 精简运行。
# 健康检查：GET /api/v1/status；启用认证时经 AGENT_CLUSTER_AUTH_TOKEN 环境变量带 X-Auth-Token。

# ---------- 构建阶段 ----------
FROM ghcr.io/astral-sh/uv:python3.11 AS builder
WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY src ./src
RUN uv sync --frozen --no-dev

# ---------- 运行阶段 ----------
FROM python:3.11-slim AS runtime
ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH"
EXPOSE 8765
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD ["sh", "-c", "python -c 'import os,sys,urllib.request as u; q=u.Request(\"http://127.0.0.1:8765/api/v1/status\"); t=os.environ.get(\"AGENT_CLUSTER_AUTH_TOKEN\") or \"\"; t and q.add_header(\"X-Auth-Token\", t); sys.exit(0 if u.urlopen(q, timeout=3).status == 200 else 1)'"]
CMD ["agent-cluster", "serve", "--host", "0.0.0.0", "--port", "8765"]
