# 四预设、双 Code runtime 与 Creator 边界

- 日期：2026-08-17
- 类：architecture
- 状态：implemented

## 决策

v1 公开 Standard、Code、Minimal、Creator 四个预设；Code 通过配置选择 Python 或 TypeScript provider，而不是复制一套 agent loop。两种 runtime 共享 `{code, bindings} -> JsonValue` 契约，并作为 `runtime.python` / `runtime.typescript` 能力注入 `code.*` 工具。

Python 代码在 `-I -S -B` 子进程、受限 builtins 和精简环境中执行；TypeScript 在父进程转译后进入独立 Node 子进程的 VM context，不暴露 `process` 或 `require`。两者均有超时、输出上限和 JSON-only 结果。它们是本地隔离 provider；更强的 OS sandbox 仍由后续平台插件叠加。

Creator 只能调用 conformance kit：候选插件在一次性影子 Host 中启动、检查、卸载和导出 bundle。正式 install/upgrade 必须另经 PermissionAuditor、SHA-256 及可选 Ed25519 签名校验；Creator 本身不能绕过这些检查。

## 证据

- Standard 由 M2 真实修复 E2E 覆盖；Code-Python、Code-TypeScript、Minimal、Creator 均通过 Host capability E2E。
- Candidate 卸载后 provider/effect 回到零；未授权网络权限被拒绝。
- 插件脚手架、bundle 导出、来源 hash、签名入口和升级版本检查均有稳定公共 API。
