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

## v0.7 工程约定（Task 14.8 起，dsh 契约移植）
- **registrations are effects**：一切注册（监听器/服务/子插件/定时器）都是 effect，
  作用域退出按注册逆序回滚（`agent_cluster/seam.py` 的 `effect_scope`）。
- **fail loud**：重复 provider、未知事件类型、缺失凭据、未激活配置条目一律立即抛错，
  禁止静默降级。
- **explicit > implicit**：`resolve(request) -> spec` 风格，禁止隐式全局状态。
- **model-visible ⟺ logged**：模型请求必须能从会话事件日志重建（`events.py` 不变量），
  违反即抛 `InvariantViolationError`。
- Agent Notes：非平凡变更必须写 `.agents/notes/`（四态生命周期，`scripts/verify_agent_notes.py` 校验）。
- 生成物目录（`docs/config-catalog.md`、`docs/module-graph.md`）：只由 `scripts/gen_*` 生成，
  改配置/模块后必须重跑生成器，freshness 由 `scripts/verify_*` 门禁校验。

## 环境速记（细节见 lessons/01）
- `apply_patch` 报 Access denied → `[System.IO.File]::WriteAllText`（UTF-8 无 BOM）；长命令/`Remove-Item` 被拦 → 拆段 + .NET API。
- 用 `uv run pytest`（venv 3.11）；禁 `uv run --python 3.11`。
- git `autocrlf=true`，CRLF/LF 警告无害。
- 验证速查：后端 `uv run pytest -q`（650）；前端 `cd frontend && npm run build / npm test / npm run i18n:check`；e2e-real 16 条。