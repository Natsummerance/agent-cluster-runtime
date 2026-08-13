# docs/lessons —— 项目经验库（索引式，按需加载）

> **用法（token 节省铁律）**：开工/排查前**只读本索引**，按当前任务场景加载对应模块；
> **不要一次性读取全部模块**，避免污染上下文。模块 ≤3KB，头部注明「何时加载」。
> 每次解决非平凡问题后，把教训补进对应模块（连同提交一起提交）。

## 场景 → 模块索引

| 场景 | 加载模块 |
|---|---|
| 任何 Windows/PowerShell/uv/git 操作、命令被拦截、写文件失败 | `01-environment` |
| 写/改测试、跑 pytest、QA 偶发失败、超时类失败 | `02-testing` |
| 改 CI、e2e 失败、端口冲突、缓存问题 | `03-ci` |
| electron-builder 打包、Release 上传、metadata 补丁、资产核验 | `04-release` |
| 前端开发/测试/i18n/Playwright 定位 | `05-frontend` |
| 版本升级、发布前同步 | `06-versioning` |
| **任何修复尝试**（先读此模块！） | `07-debugging` |
| 排查耗时问题、大日志/大产物、token 快用完时 | `08-token-economy` |

## 硬规则（摘要，细节见模块）
- **3 次即停**：同一问题修复尝试 ≤3 次；第 3 次仍失败 → 停止 → 跑 `scripts/troubleshoot.ps1` 收集证据 → 对照本库定位根因 → 才允许下一次修复（`07-debugging`）。
- **版本替换用精确字符串**，禁止全文替换（`0.6.0` 是 `10.6.0`/`>=0.6.0` 子串，曾误伤 lock）（`06-versioning`）。
- **探活/探测必须带认证头**（`X-Auth-Token: ci`），否则 401 假超时（`03-ci`）。
- 大日志先过滤关键行，不下载全量；先看 run/jobs 摘要再拉日志（`08-token-economy`）。
- 完整总纲见 `docs/LEARNINGS.md`（各模块一行摘要）；最新交接见 `docs/superpowers/handoff/`。