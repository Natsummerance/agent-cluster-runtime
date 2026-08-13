# LEARNINGS.md —— 项目经验总纲（索引）

> 详细经验按模块存放于 `docs/lessons/`（**按需加载，勿整库读取**；索引见 `docs/lessons/README.md`）。
> 本文件只保留每类一句话摘要与模块指针，避免双源维护。

| 类别 | 一句话 | 详细模块 |
|---|---|---|
| 开发环境 | `apply_patch` 不可用 → WriteAllText；长命令/Remove-Item 被拦截 → 拆段 + .NET API；禁 `uv run --python`；serve 冷启动慢 | `docs/lessons/01-environment.md` |
| 测试 | 陈旧 pyc 竞态→基线后清缓存；测试超时 > 子进程内部超时；跨平台禁 cmd /c；全量测试留最后 | `docs/lessons/02-testing.md` |
| CI/CD | e2e 端口竞态已根治（reuseExistingServer:true + 探活带认证头）；失败先看摘要再拉日志 | `docs/lessons/03-ci.md` |
| Release | mac 架构由 CLI 决定（勿在 target 固定 arch）；gh CLI 上传；metadata 补丁 + 核验清单 | `docs/lessons/04-release.md` |
| 前端 | 严禁伪造数据；AntD 下拉定位；i18n 约定；e2e-real 本地流程 | `docs/lessons/05-frontend.md` |
| 版本 | **精确字符串替换**（全文替换会误伤依赖版本）；12 处同步清单；验证命令 | `docs/lessons/06-versioning.md` |
| 调试 | **3 次即停协议**：失败 3 次必须停止→诊断脚本→对照经验库→书面根因后才可再修 | `docs/lessons/07-debugging.md` |
| Token 节省 | 大日志先过滤；先摘要后下载；全量测试留最后；诊断 artifact 优先 | `docs/lessons/08-token-economy.md` |

## 硬规则（违反即返工）
1. 前端严禁伪造/硬编码数据（mock-api.ts 仅测试合法）。
2. 同一问题修复尝试 ≤3 次，第 3 次失败先诊断（`scripts/troubleshoot.ps1`）再动手。
3. 版本替换用精确字符串，禁止全文替换。
4. 探活/探测必须带认证头（`X-Auth-Token: ci`）。
5. 每任务独立 commit + push；提交前缀 `Task 13.N:`（v0.6.x）/ `Task 14.N:`（v0.7）。
6. 版本升级走 `docs/lessons/06-versioning.md` 的 12 处清单。