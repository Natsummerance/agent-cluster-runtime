# Standard Agent 唯一事件主链

- 日期：2026-08-17
- 类：architecture
- 状态：implemented

## 决策

Standard Agent 的输入、系统提示、模型请求/结果、工具请求/结果、审批与终态全部先写 `SessionEventStore`，再推进下一动作。JSONL provider 以 session revision 做 compare-and-append，并通过临时文件写入、fsync 和原子替换避免半行日志。fork 复制指定前缀、重写 session/seq，再追加包含来源坐标的 `session.forked`。

模型上下文只由 `projectModelMessages(events)` 投影，禁止从 LangGraph checkpoint、内存消息数组或服务端旁路日志恢复。OpenAI-compatible provider 只接收 opaque credential handle，经 Host `credential.resolve` 获取值；错误不回显上游 body 或凭据。

工具调用采用 JSON 参数和结构化 argv，`shell=false`；本地 execution world 校验 lexical path 与 realpath，阻止 `..` 和 symlink 逃逸。写文件和进程工具必须先持久化审批请求/结论，实际执行经过 Host 共享 onion interceptor 链。

## 证据

- 重启可恢复 revision，CAS 并发只有一个 winner，fork 与源 session 后续写入隔离。
- 单一日志确定性投影 system/user/assistant/tool/approval 内容。
- 真实临时仓夹具通过 `workspace.write` 修复错误，再用 `process.run` 执行测试并记录 exit code 0。
- Host 插件装配 E2E 只经 `session.event-store`、`model.generate`、`tool.*`、`approval.request` 和 `agent.invoke` 能力运行。
