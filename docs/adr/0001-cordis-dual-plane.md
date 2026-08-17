# ADR-0001: Cordis 双平面运行时

- 状态：Accepted
- 日期：2026-08-17
- 决策者：DoAI maintainers

## 背景

v0.7.2 同时存在服务端私有事件日志、未驱动真实启动的配置层、局部 provider seam，以及直接持有模型、工具和状态的 Python 主链。这让插件无法完整卸载，恢复语义不唯一，新增能力经常同时修改运行时、API 与前端。

## 决策

DoAI v1 采用两个职责严格分离的平面：

1. TypeScript Cordis Host（Agent Plane）拥有唯一插件树、配置装载、模型/工具/沙箱、凭据边界、进程监督和会话事件序号。
2. Python/LangGraph Organization Plane 只拥有 12 岗位、7 类会议、任务板、预算、审批编排、记忆和自我进化等领域决策。
3. 两平面只通过版本化 JSON-RPC 和持久 SessionEvent 通信；Python 不直接访问模型、凭据、工具或会话存储。
4. LangGraph checkpoint 是可丢弃加速缓存，SessionEvent 日志是恢复、fork、审计、模型上下文和 UI 投影的唯一事实源。
5. 不提供旧运行时兼容层。迁移期按里程碑删除已替换主链，最终只提供一次性迁移器。

## 结果

- 新模型、工具、角色、会议、存储、工作流和 UI 卡片应局部落在插件包、组合配置与生成目录。
- Host 内核变更属于架构变更，必须附 ADR 和契约测试。
- transport 可从本地 stdio 替换为 WebSocket/HTTP，但消息模型不变。
- 迁移规模增大，但消除了永久双轨和隐式状态所有权。
