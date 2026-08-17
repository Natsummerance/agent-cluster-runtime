# Python Organization Plane 与双向 RPC

- 日期：2026-08-17
- 类：architecture
- 状态：implemented

## 决策

Python Plane 作为受 Host 监督的无特权子进程运行，stdio 每行承载一个 JSON-RPC 2.0 消息。独立 reader thread 持续分发响应与反向 Host 请求，async server 并发处理请求，因此长运行期间仍可响应 heartbeat 和 cancel。握手固定协商 protocol/event schema `1.0`；断流会拒绝全部 pending 请求，下一次调用自动启动新进程。

Software Company 目录在新包中固定为 12 岗与 7 类会议。LangGraph 只驱动会议状态机，是可丢弃执行缓存；角色输出、会议、任务板合法流转、审批、预算、记忆、进化和组织终态都由 Host `session.append` 持久化，再由 `OrganizationProjector` 确定性重建。

## 恰好一次边界

每个变更 RPC 携带 request id、idempotency key 和 session revision。Host event store 将 mutation 元数据写进 durable event；相同 key 返回原 event。Standard Agent 的内部事件使用派生 key，`agent.completed` 使用调用 key。Python 在请求前、执行中或 Host 提交后崩溃时，新进程重放相同调用，不重复任务、会议、审批或组织终态。

## 安全边界

Supervisor 仅向 Python 传递 PATH/系统临时目录、UTF-8 设置和显式配置环境，不继承模型凭据。Python 只可请求握手声明的 `session.*`、`agent.invoke` 和 `approval.request`；未知反向方法 fail loud。模型、工具、凭据和文件系统仍由 Agent Plane 所有。
