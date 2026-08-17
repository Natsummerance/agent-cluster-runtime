# Cordis Host 内核与影子 scope 事务

- 日期：2026-08-17
- 类：architecture
- 状态：implemented

## 决策

v1 Host 直接固定依赖 `@deepseek-ai/cordis@4.0.1`。每次激活先在一个父 Cordis fiber 下创建影子 scope；插件 fiber、provider、listener、onion interceptor 和后台资源均作为该父 fiber 的可逆 effect。只有配置、依赖、provider 与启动检查全部通过后才替换 active 指针，随后 drain 旧 scope。

DoAI 对插件只公开 `resolve/provide/on/intercept/effect/scope`，不暴露 Cordis root 或 registry。Capability provider 存在于每个影子 scope 的独立 registry 中；`exactly_one` 冲突在启动前诊断，动态实际注册数在切换前再次校验。

## 证据

- Cordis `serial/bail` 与 DoAI `serial/first` 使用隔离 Context 的差分夹具。
- 启动失败保留旧 scope，成功 epoch 才递增。
- 缺失/版本不符依赖、未知/重复插件、无效配置、缺失/重复/未声明 provider 均结构化失败。
- 连续 100 次装载/卸载后 live provider、effect 和测试资源计数归零。

## 有意偏差

Cordis `waterfall` 保持可用作上游语义参考；DoAI 另设显式异步 onion 链，禁止 `next()` 重入，用于工具执行的授权、审批、沙箱、脱敏与审计包裹。
