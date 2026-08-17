# 生成式 UI 客户端与企业插件边界

- 日期：2026-08-17
- 类：architecture
- 状态：implemented

## 决策

协议生成器同时产出 Host SDK 与 `frontend/src/api/v1.generated.ts`，Workbench 的新客户端只接受 canonical `SessionEvent` 和 JSON-RPC envelope。UI projector 对 seq 缺口、混入不同 session 的事件立即失败，并从同一日志计算终态、任务、会议与审批；不再把断线当成终态。

多项目、RBAC、多租户、OAuth MCP、审计、资源日历、依赖图、进化与 UI 卡片以九个普通官方插件注册，不进入 Host 条件分支。所有服务 key 强制包含 tenant id；OAuth MCP 和审计插件在 manifest 中显式声明网络、凭据与文件权限。

## 证据

- 前端 generated types 受 protocol freshness 测试保护；v1 client 投影、事件缺口和结构化 RPC fault 有 Vitest。
- 现有 Workbench production build 保持通过。
- 九个企业插件可在同一影子 scope 事务装载，tenant-a/tenant-b 同 id 数据互不可见，卸载由 Cordis fiber 回收。
