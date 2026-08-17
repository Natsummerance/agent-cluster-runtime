# DoAI Workbench v1 运行时契约

## 产品不变量

DoAI 仍是“可自我进化的组织化软件研发集群”。Cordis 是运行时底座，不替代 Software Company 的 12 岗位、7 类会议、审批、预算、任务板、记忆与进化闭环。

## 组合模型

启动输入由 `profile + bundles + patches` 构成。Profile 选择产品外壳（Workbench、Web Server、Headless、Python SDK）；Agent preset 选择 Standard、Code、Minimal 或 Creator；bundle 提供可组合能力，`software-company` 是官方 bundle，而不是特殊内核分支。

装载顺序为 profile → bundles（声明顺序）→ patches。合并后先做 schema、依赖、provider 唯一性、权限与凭据预检，再在影子 scope 中启动。全部健康检查通过后原子切换；失败则逆序释放所有 effect 并保留旧 scope。

## Host 公共 SPI

插件入口固定为 `PluginManifest + apply(context, config)`。Manifest 声明版本、API 版本、依赖、提供能力、配置 schema，以及网络、文件、进程和凭据权限。

Context 只公开：

- `resolve(capability)`：显式解析当前 scope 中的能力；缺失或冲突时失败。
- `provide(capability, provider)`：注册由当前 fiber 所有的 provider。
- `on(event, listener, mode)`：注册带生命周期的监听器。
- `effect(start)`：注册资源及其释放函数；释放是可等待且幂等的。
- `scope(overrides)`：创建隔离子 scope，用于租户、会话和影子装载。

未知配置、依赖缺失、重复 provider、凭据缺失和插件启动失败均为带 JSON Pointer、插件路径、scope 与修复建议的诊断，禁止静默降级。

## Host–Organization RPC

本地 transport 使用逐行 JSON-RPC 2.0 over stdio。Host 监督 Python 子进程并负责握手、心跳、取消与重启。未来 WebSocket/HTTP 复用完全相同的 envelope。

双方首先交换 `ProtocolHello`，协商 `protocol_version`、`event_schema_version` 和 capability 集。变更请求携带 `MutationMeta`。提交成功的响应只有在对应 durable event 已追加后才能发出；相同 idempotency key 重放必须返回原结果。

Host 提供 `agent.invoke`、`approval.request`、`artifact.put`、`session.append`、`session.read`。Organization Plane 提供 `organization.run`、`organization.cancel`、`organization.project` 与健康检查。

## 状态机

Host 插件：`declared → validating → shadow-starting → active → draining → disposed`；任一步失败进入 `failed`，释放影子 scope 后可回到 `declared`。只有原子切换能让 shadow scope 成为 active。

Organization run：`requested → clarifying → planning → awaiting-approval → executing → verifying → releasing → retrospective → completed`。取消可从除终态外任一状态进入 `cancelling → cancelled`；可恢复错误进入 `suspended`，重放日志后回到最近合法状态；不可恢复错误进入 `failed`。

## 安全边界

凭据只以 opaque handle 进入调用，插件按 manifest 权限请求使用。工具执行统一经过 onion 链：身份/租户 → 能力策略 → 审批 → 沙箱 → 执行 → 脱敏 → 审计。Creator 只能在临时 scope 试装和导出 bundle，正式安装仍须来源/签名/权限审计。
