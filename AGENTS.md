# AGENTS.md — agent-cluster-runtime

多 Agent 组织型全栈开发集群运行时（Python 3.11 + LangGraph + pydantic v2 + React/Vite + Electron 桌面工作台）。

## 加载协议（token 节省）
1. 先读 `docs/lessons/README.md`（索引表），**按当前任务只加载对应模块**，勿整库读取。
2. 任何修复尝试前先读 `docs/lessons/07-debugging.md`（3 次即停协议）。
3. 版本升级读 `docs/lessons/06-versioning.md`（12 处同步清单）。
4. handoff：只读 `docs/superpowers/handoff/` 下最新一份；plan/design 按需看对应版本。

## 硬约束
- 前端**严禁伪造/硬编码数据**，全面接真实后端；`frontend/e2e/mock-api.ts` 仅拦截式测试合法。
- **3 次即停**：同一问题修复尝试 ≤3 次；第 3 次仍失败 → 跑 `scripts/troubleshoot.ps1` 收集证据 → 对照经验库定位根因 → 才允许再修。
- TDD + verification-before-completion；完成声明附当次新鲜证据。
- 每任务独立 `git commit` + `git push`；前缀 `Task 13.N:`（v0.6.x）/ `Task 14.N:`（v0.7）。
- 不派发子智能体（主线程直改）。

## 环境速记（细节见 lessons/01）
- `apply_patch` 报 Access denied → `[System.IO.File]::WriteAllText`（UTF-8 无 BOM）；长命令/`Remove-Item` 被拦 → 拆段 + .NET API。
- 用 `uv run pytest`（venv 3.11）；禁 `uv run --python 3.11`。
- git `autocrlf=true`，CRLF/LF 警告无害。
- 验证速查：后端 `uv run pytest -q`（650）；前端 `cd frontend && npm run build / npm test / npm run i18n:check`；e2e-real 16 条。