# DoAI Workbench v1：Cordis 双平面 Harness 重构

## 目标

将 v0.7.2 的多条运行主链收敛为 TypeScript Cordis Agent Plane 与 Python Organization Plane。Cordis Host 成为插件、会话、模型、工具、沙箱、权限与事件的唯一运行时所有者；Python 保留 DoAI 的组织化研发差异，但只通过版本化 RPC 请求 Host 能力。

本设计当前以 DeepSeek Harness `dsh-v0.1.0-rc.7` commit
`99f6f02fecdb7dff40c3fbc9470f5907c29f74ca` 为固定参考；Cordis 仍为 `4.0.1`。
不会自动追随上游预览版，基线升级受 [ADR-0004](../../adr/0004-upstream-baseline-rc7.md)
约束。公共契约详见 `docs/v1/runtime-contract.md`，能力目录详见 `docs/v1/capabilities.yaml`。

## 里程碑

### M0：规格、许可与基准冻结

落盘协议 schema、事件词汇、能力目录、状态机、ADR、移植溯源和三类黑盒基准。协议 schema 是 TypeScript/Pydantic 类型的唯一生成源。

### M1：Cordis Host 内核

实现 profile/bundle/patch、scope/effect、依赖解析、事务装载、诊断和五种事件语义；与固定 Cordis 夹具差分验证装卸、回滚、依赖 epoch 与配置失败。

### M2：单 Agent 纵向主链

迁移统一 session event store、模型适配器、Standard 工具管线、execution world、审批、恢复、fork/replay。真实既有仓修复通过后删除对应旧 EventBus、服务端日志和旁路执行链。

### M3：Python Organization Plane

实现 stdio JSON-RPC、监督器、握手、心跳、取消、幂等、重放和结构化故障；迁移 LangGraph、12 岗、7 会议、审批门、预算、任务板、记忆和进化。完整交付通过后删除旧 `SessionDriver/AgentRuntime` 主路径。

### M4：高级模式与插件生态

完成 Code-Python、Code-TypeScript、Minimal、Creator，插件脚手架、conformance kit、配置解释器、安装升级与来源/签名检查。

### M5：产品面与企业能力

React/Electron 改接生成式协议客户端和唯一事件流；多项目、RBAC、多租户、OAuth MCP、审计、资源日历、依赖图与自我进化成为官方插件。本地包内置 Node/Python runtime。

### M6：迁移、删除与发布

交付 `doai migrate --dry-run|--apply`，包含备份、转换、验证与回退。能力矩阵完备后删除 legacy runtime、重复类型、旧 CLI 和失效文档；全部质量门通过后发布 v1。

## 验收门

- 插件连续装卸 100 次后 provider、监听器、后台任务、进程与文件句柄回到基线。
- Python Plane 在请求前、执行中、提交后崩溃均不重复领域动作或丢 durable event。
- 单一日志确定性重建模型消息、组织状态、任务板、审批和 UI 投影。
- 覆盖路径逃逸、命令注入、凭据泄漏、审批绕过、Creator 越权、沙箱逃逸与租户隔离。
- 四种 preset、两种 Code runtime 和 Software Company 完整交付均有真实 E2E。
- 878 个后端与 161 个前端成熟行为必须保留或由新链的等价证据替代。
- 常规扩展只改一个插件包、组合配置与生成目录；触碰 Host 内核必须有架构评审。
- 发布门要求 Host、Python、前端、协议生成、安装包、E2E、迁移回滚和许可证 freshness 全绿，且不存在 legacy runtime import。
