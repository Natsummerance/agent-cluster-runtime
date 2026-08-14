# 安装包后端随包分发修复（Task 15.1）

- 日期：2026-08-14
- 类：architecture
- 状态：implemented

## 问题
v0.7.0 安装包下载后「后端启动失败 / 后端未启动，请重启工作台」：
electron-builder 打包产物不含后端运行时（`desktop/resources/` 不存在、
无 `agent-cluster-backend.exe`），`main.js resolveBackendLaunch()` 回退
`uv run agent-cluster serve`，最终用户机器无 uv/项目源码 →
`error: Failed to spawn: 'agent-cluster' / program not found` → 后端退出
code=2 → 30s 后 `connect ECONNREFUSED` → 弹「后端启动失败」。
（在模拟安装目录用 v0.7.0 win-unpacked + `--smoke` 实测复现，日志为证。）

## 修复
- `desktop/main.js`：`resolveBackendLaunch()` 增加「随包 venv+源码」候选——
  `resources/backend/venv/Scripts/python.exe`（win）/ `bin/python`（posix），
  以 `-m agent_cluster.cli serve` 启动，`cwd=resources/backend`，
  `env.PYTHONPATH=resources/backend/src`（editable .pth 在用户机失效，
  PYTHONPATH 显式指向打包源码）；`agent-cluster-backend.exe` 候选保留向后兼容；
  `uv run` 仅作开发模式回退。
- `desktop/electron-builder.yml`：新增 `extraResources`，把
  `../.venv-pack`（精简 venv，无 dev 依赖）→ `backend/venv`、
  `../src` → `backend/src`、`../pyproject.toml` → `backend/pyproject.toml`。
- `.github/workflows/ci.yml` `package` job：新增 setup-uv +
  `UV_PROJECT_ENVIRONMENT=.venv-pack uv sync --no-dev --frozen`（矩阵
  `uv_sync_extra` 列，mac-x64 在 arm64 runner 上加
  `--python-platform x86_64-apple-darwin` 保证架构匹配）。

## 验证（新鲜证据）
- 打包后布局独立运行：`python.exe -m agent_cluster.cli serve` → 探活
  `200 {"ok":true,...version":"0.7.0"}`。
- 本地 `npx electron-builder --win nsis --x64 --publish never` 成功，
  安装包 82.6MB→89.7MB（+7MB）；`win-unpacked/resources/backend/` 含
  venv/src/pyproject.toml。
- 模拟安装目录（`Program\test` 布局）`AgentClusterWorkbench.exe --smoke`：
  `使用随包后端运行时 → agent-cluster serve 已启动 → 后端就绪 → SMOKE OK`。
- 数据目录不依赖 cwd（`Path.home()/.agent-cluster`），Program Files 只读无碍。

## 后续
用户需新发布（v0.7.1）才能拿到修复包；electron-updater 平滑升级链路不变。