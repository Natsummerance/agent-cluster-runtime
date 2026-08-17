# ADR-0002: SessionEvent 是唯一事实源

- 状态：Accepted
- 日期：2026-08-17

## 决策

所有 durable 状态变化先追加为：

`SessionEvent{schema_version, session_id, seq, type, ts, scope, payload, ignorable}`

Host 为每个 session 分配严格递增的 `seq`。投影器只能消费日志，不能创建旁路真相。任何提交型 RPC 必须携带 `request_id`、`idempotency_key` 与调用方观察到的 `session_revision`。

模型可见的系统提示、用户消息、工具请求/结果、审批结论和组织决策必须可从日志确定性重建。诊断性事件只有在 `ignorable=true` 时才允许旧消费者跳过；领域事件未知时必须停止恢复并给出 schema 诊断。

## 事件语义

- broadcast：同步广播，不等待结果。
- parallel：并行等待所有监听器，聚合失败。
- serial：按序等待，首个非空/非 false 结果停止。
- first：同步首胜。
- onion：中间件式前后包裹，用于授权、审批、审计与工具执行。

Cordis 的 `emit/parallel/serial/bail/waterfall` 是前四种语义的参考；DoAI 的 onion 是明确扩展，必须有专项测试而不伪装成上游一致行为。
