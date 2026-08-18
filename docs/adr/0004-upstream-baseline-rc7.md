# ADR-0004: 升级 DeepSeek Harness 参考基线至 rc.7

- 状态：Accepted
- 日期：2026-08-18
- 仅取代的活动决定：[ADR-0003](0003-upstream-baseline.md) 中的 active upstream baseline

## 背景

ADR-0003 将 `deepseek-ai/deepseek-harness@47f943859bef60e4160492346772ded9b24f765a`
（`0.1.0-rc.5`）冻结为 DoAI v1 的参考基线。该 ADR 仍是真实的历史决策和已有 Cordis
imports 的溯源依据，但不再描述当前活动基线。
[ADR-0001](0001-cordis-dual-plane.md) 的双平面职责与
[ADR-0002](0002-event-source-of-truth.md) 的单一事件事实源继续有效；本次升级不重开或替代这些架构决定。

本次对本地只读上游执行了固定范围审查：
`47f943859bef60e4160492346772ded9b24f765a..99f6f02fecdb7dff40c3fbc9470f5907c29f74ca`。
使用 `git rev-parse`、`git tag --points-at`、`git rev-list --count`、`git diff --shortstat`、
`git diff --name-status` 和逐提交 `git show` 核验发布身份、统计、源码与测试证据，并单独核验
base/head 关键路径以及 head final tree 中的 LICENSE、Cordis 与 Windows/Python 打包边界。该范围为
111 commits、539 files、8,183
insertions、1,625 deletions。

## 决策

DoAI 的当前固定参考基线升级为
`deepseek-ai/deepseek-harness@99f6f02fecdb7dff40c3fbc9470f5907c29f74ca`
（tag `dsh-v0.1.0-rc.7`）。`@deepseek-ai/cordis` 仍为 `4.0.1`，且 `vendor/cordis`
在该差分范围没有源码变化。

上游 LICENSE 未变化，仍为 MIT；新 HEAD 的 LICENSE SHA-256 为
`EBB4F09972AEE8608BE255DEBAF78451A68E95C290F55C240DEC2ECFA16EA6BE`。
活动元数据由 `docs/porting/dsh-provenance.yaml` 机器检查；原 imports 条目的 source commit
继续指向其真实审查来源 `47f943859bef60e4160492346772ded9b24f765a`，不得伪写为 rc.7。

本次只同步规格、溯源、许可元数据、采用矩阵和后续计划，不自动移植运行时代码。
Task 16.11 不重写、不取消；仅在其原有事务语义下增加“主 activation/health 失败与 rollback
failure 同时保真、递归脱敏、epoch 不推进”的回归要求。详细采用设计见
`docs/porting/2026-08-18-dsh-rc7-delta.md`，依赖有序实施边界见
`docs/superpowers/plans/2026-08-18-dsh-rc7-sync-implementation.md`。

## 后果

- ADR-0003 和历史 handoff/porting 文档保留旧 commit，作为当时决策和 imports 的历史记录；
  ADR-0004 与 provenance 是当前活动基线的权威来源。
- 上游未来 preview/release **不会自动跟踪**。再次升级必须有新的 ADR、固定差分范围、许可哈希、
  采用/偏差审查和机器契约测试。
- rc.7 不提供 Windows Python single-exe，不能据此降低三平台双 runtime installed-artifact 发布门。
- 未具备 typed events、durable store、唯一 artifact owner、租户/RBAC 或安全 PTY 边界前，
  不得抢跑相应上游功能。
