# AGENTS.md — agent-cluster-runtime

多 Agent 组织型全栈开发集群运行时（Python 3.11 + LangGraph + pydantic v2 + React/Vite + Electron 桌面工作台）。
本文件是给 Codex/Claude 等编码代理的常驻指令；**每次开工先读**。

## 开工前必读（按顺序）
1. `docs/LEARNINGS.md` —— 项目长期经验库（踩坑/根因/预防，越迭代越聪明）。遇到非平凡问题解决后必须补一条。
2. `docs/superpowers/handoff/` 下**最新**交接文档 —— 当前版本事实、提交链、续跑入口。
3. `docs/superpowers/plans|specs|research/` —— 对应版本的 plan / design / research（写测试前先看「关键契约索引」）。

## 硬约束
- 前端**严禁伪造/硬编码数据**，全面接真实后端；`frontend/e2e/mock-api.ts` 仅 Playwright 拦截式测试合法。
- 流程：TDD（RED→GREEN）+ verification-before-completion；完成声明必须附当次新鲜测试证据。
- 每个任务独立 `git commit` + `git push`；提交前缀：v0.6.x 维护 `Task 13.N:`，v0.7 起 `Task 14.N:`。
- 版本升级四文件同步（见 LEARNINGS §6），`tests/test_t12_11.py` 有版本一致性断言。
- 不派发子智能体（用户要求主线程直改）。

## 环境（Windows 开发机，PowerShell）
- `apply_patch` 报 Access denied → 用 `[System.IO.File]::WriteAllText`（UTF-8 无 BOM）写文件。
- 单条命令过长 / `Remove-Item` 会被策略拦截 → 拆段执行，删除用 .NET API。
- 用 `uv run pytest`（venv 3.11）；**禁止** `uv run --python 3.11`（会重建 venv）。
- git `autocrlf=true`，CRLF/LF 警告无害；各文件行尾约定见 LEARNINGS §1。
- 常用验证：后端 `uv run pytest -q`（650 passed）；前端 `cd frontend && npm run build / npm test / npm run i18n:check`；
  e2e-real 16 条（先起真实 serve 再 `npm run e2e:real`）。