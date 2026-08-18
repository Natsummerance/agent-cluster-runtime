# DoAI Workbench v1 Cordis 双平面续作交接（2026-08-17）

> 本文件是当前最新 handoff。它记录 `v1-cordis-dual-plane` 的真实完成度、审查证据和
> 后续实施顺序。v1 当前是可测试原型，不是可发布产品；不得用测试总数替代发布证据。

## 0. 活动上游基线（2026-08-18 更新）

- 当前固定参考：DeepSeek Harness `dsh-v0.1.0-rc.7`，commit
  `99f6f02fecdb7dff40c3fbc9470f5907c29f74ca`；Cordis 仍为 `4.0.1`。
- 原 `47f943859bef60e4160492346772ded9b24f765a`（`0.1.0-rc.5`）是历史基线；已有 imports
  继续保留真实 source commit。
- 决策、差分矩阵和后续提交边界分别见 `docs/adr/0004-upstream-baseline-rc7.md`、
  `docs/porting/2026-08-18-dsh-rc7-delta.md` 和
  `docs/superpowers/plans/2026-08-18-dsh-rc7-sync-implementation.md`。
- 固定基线不会自动跟踪未来 preview。rc.7 没有 Windows Python single-exe，三平台双 runtime
  installed-artifact 发布门不变。

## 1. 开工状态

- 分支：`v1-cordis-dual-plane`
- 审查 HEAD：`707d4b623d3ccbe19670639c05fe81458e8f2790`
- `origin/main` merge-base：`10131b59c48a02cfe9d7f60200ea1293d49a7348`
- 审查范围：`git diff origin/main...707d4b6`，99 files，约 7516 行新增。
- 版本：根 pnpm workspace 为 `1.0.0-alpha.0`；v0.7.2 产品仍是当前生产主链。
- 本轮审查前工作树干净；旧 handoff 保留为历史记录。

提交链：

| SHA | 内容 |
|---|---|
| `e923347` | 16.1 冻结架构、协议、ADR、基准描述与溯源 |
| `e8c8e34` | 16.2 Cordis Host 原型 |
| `584d221` | 16.3 Standard Agent 与 JSONL 事件主链 |
| `5b2cd7e` | 16.4 Python Organization Plane 与 stdio bridge |
| `4f53608` | 16.5 Preset、Code runtime 与 Creator 原型 |
| `827476b` | 16.6 生成式前端客户端与企业插件骨架 |
| `f194fb3` | 16.7 v1 CLI 命令面与一次性迁移原型 |
| `707d4b6` | 16.8 v1-core CI 与初版 release gate |

## 2. 里程碑真实完成度

| 里程碑 | 状态 | 已有证据 | 尚缺 |
|---|---|---|---|
| M0 规格/许可/基准 | 部分完成 | schema、ADR、能力/事件目录、三类场景描述 | benchmark runner/结果、许可全文、provenance freshness、固定 commit 夹具 |
| M1 Cordis Host | 部分完成 | 组合、依赖排序、effect、五种事件 API、失败保留旧 scope | 真 scope 隔离、权限/凭据预检、健康检查、dependency epoch、固定基线差分 |
| M2 单 Agent | 部分完成 | Standard loop、模型投影、工具、审批、JSONL、fork | 可靠 sandbox、artifact/policy、流式模型、durable batch/CAS、生产入口 |
| M3 Organization | 原型 | stdio RPC、12 岗/7 会事件、投影、基础幂等 | 可恢复领域状态机、真实任务/预算/记忆/进化、真实进程崩溃恢复、贯穿取消 |
| M4 高级模式 | 原型 | 四种 preset、两种 Code runtime、Creator 校验 API | OS 隔离、真实安装/升级/回滚、信任根、完整 conformance 资源审计 |
| M5 产品/企业 | 未闭环 | 生成类型与未接线 v1Client、九个 manifest | Host transport、全前端迁移、真实企业实现、Electron 双运行时包 |
| M6 迁移/发布 | 未闭环 | dry-run/apply、备份、基础回滚、v1-core CI | 无损迁移、legacy 删除、安装包/E2E/许可门、正式版本同步 |

## 3. 双轴深度审查

### 3.1 Standards：工程契约问题

#### S0-1 Scope 与影子激活隔离不成立

`PluginContext.scope()`继续复用同一个 `ScopeProviders`；`EventHub` 又通过 Cordis root 共享
interceptor map。影子 scope 的 interceptor 可在切换前影响旧活动 scope，子 scope 的 provider
也没有租户/会话隔离语义。这违反“shadow 验证后原子切换”和“scope 隔离”。

证据：`packages/host/src/host.ts` 的 `PluginContext.scope`；
`packages/host/src/events.ts` 的 root-scoped `interceptorStores`。

#### S0-2 registrations are effects 尚未贯彻

`createCodeToolPlugin`直接调用共享 `ToolRuntime.register()`，该方法不返回 disposer。插件卸载后
工具仍留在 provider 对象内；当前 100 次装卸测试只统计 Cordis provider/effect，没有覆盖这种
外部对象残留、子进程和文件句柄。

#### S0-3 Manifest 权限只是声明

Host 只验证能力目录、配置和依赖，没有验证 manifest schema/API/version，没有 permission grant、
credential availability、网络/文件/进程范围或启动健康检查。Creator 的 `PermissionAuditor` 也
没有接入 Host 激活事务，因此安全策略可被普通插件绕过。

#### S1-1 事件存储仍是单进程原型

- 每个 append 重写完整 JSONL，长会话为 O(n²)。
- 锁只存在于当前 JS 对象，多个 Host/进程可同时写坏日志。
- 文件 fsync 后未 fsync 父目录；没有 append batch 或 durable mutation result。
- 幂等重放只比较 event type，同 key、同 type、不同 payload 会被当作成功重放。
- replay 只检查 envelope 三个字段，不校验事件词汇、payload、owner、ignorable 或状态机。

#### S1-2 企业插件是同构占位器

项目、RBAC、租户、OAuth MCP、审计、日历、依赖图、进化和 UI card 全部由同一个内存 Map
factory 生成。它证明了 manifest 可装载，没有证明任何成熟企业行为被迁移。

#### S1-3 测试证据命名过强

Organization “E2E”确实启动 Python 子进程，但模型固定返回文本、审批自动通过；Code 测试只
检查 `open/process/require` 不直接可见。这些属于集成冒烟，不是完整交付或沙箱逃逸证明。

### 3.2 Spec：计划符合度问题

#### M0

- `benchmarks/v1/scenarios.yaml` 没有 runner、golden result 或 CI job。
- provenance 声称来源为 `vendor/cordis`，但目录不存在；测试只核对字符串与 npm 版本。
- `THIRD_PARTY_NOTICES.md` 明示“开始源码复用时再包含许可全文”，尚不满足发布 freshness 门。

#### M1

- 没有 dependency epoch 数据结构或依赖变更后的 consumer 重启证明。
- 没有权限/凭据预检、插件健康检查和真正原子的隔离 shadow scope。
- “差分测试”直接比较同一 npm Cordis 实例的 API 行为，不是固定 commit 夹具输出比较。

#### M2

- `artifact.put`、`policy.authorize` 没有 Host provider；安全 onion 链没有注册为必经路径。
- `process.run` 虽禁 shell string，但允许任意解释器与参数，并非 execution sandbox。
- `workspace.write` 只验证父目录，既有目标若是指向工作区外的 symlink，仍可能越界写入。
- 没有真实模型/流式响应 E2E；旧 EventBus、服务端日志与执行入口尚未删除。

#### M3

- workflow 依次调用 12 个角色、创建通用任务，随后无条件将任务推进为 done；没有真实澄清、
  实现、测试、修复、发布或 artifact 行为。
- budget 只全额 reserve/commit，不计实际 token/tool cost；失败、拒绝和取消没有 release。
- memory/evolution 只追加固定文本和 proposal，没有检索、审批、apply 或 rollback。
- crash 测试使用 FakeHost 抛异常。真实 Agent 若在 `agent.completed` 前崩溃，重试会从旧 revision
  追加并与已有部分事件冲突，尚无 exactly-once 恢复证据。
- cancellation 只在会议边界检查，未传到当前 Agent、模型、工具或进程树。
- Python 发起 Host RPC 的 `response_queue.get()` 没有 timeout；Host timeout 也不向对端发送取消。

#### M4

- Creator catalog 仅保存在内存，不写磁盘、不维护来源、不做影子升级切换或失败回滚。
- 签名是可选的，没有信任根、签名者策略、撤销或来源 pinning。
- conformance 只观察 Host 计数，无法发现共享 ToolRuntime 修改、孤儿进程或句柄泄漏。
- Python/TypeScript runtime 是普通本机子进程；`node:vm` 不是安全边界，且没有 CPU、内存、
  网络、文件系统、用户身份或进程树限制。

#### M5

- React 页面/store 仍使用旧 `api/endpoints`、`api/types` 和旧 SSE reducer；`v1Client` 只有单测。
- 没有生产 `/api/v1/rpc`、session streaming 或可替换 HTTP/WebSocket transport。
- Electron 仍监督 `agent-cluster serve`，安装包没有 Cordis Host 和 Python Plane 组合启动。
- 企业插件未迁移 v0.7.2 的真实 RBAC/OAuth/审计/日历/依赖/进化实现。

#### M6

- `doai run/web/config/session` 明确失败关闭；只有 doctor、plugin scaffold、migrate 可执行。
- 迁移仅保留 goal/status/workspace、问答和门决定；丢失 flow/model、checkpoint、token ledger、
  task board、changes、memory、artifacts、fork lineage 和治理记录。
- 目标文件存在时直接跳过，没有验证目标是否有效或与源数据一致。
- CI 的 real E2E、package、release 和 Docker 路径仍以 legacy Python backend 为产品主链。
- `src/agent_cluster`、旧 CLI、旧 DTO、旧事件词汇与旧文档都仍被生产代码引用。

## 4. 当前证据及其边界

| 套件 | 当前结果 | 能证明 | 不能证明 |
|---|---:|---|---|
| legacy Python | 893 passed / 4 skipped | v0.7.2 成熟行为未被新增文件破坏 | 行为已迁移到 v1 |
| legacy React | 164 passed + build | 旧 UI 继续工作 | UI 已连接 Cordis Host |
| v1 TypeScript | 47 passed + typecheck | 原型模块内部契约 | 生产 transport、真实安全和安装包 |
| protocol/organization | 16 passed | schema freshness、内存恢复算法 | 真实子进程中途崩溃 exactly-once |

不得再把以上总数写成“v1 全量通过”。新增验收必须明确 fake、integration、real E2E、installed
artifact 四个证据等级。

## 5. 不得删除清单

以下替代证据出现前，不得删除对应 legacy 模块：

- Workbench 全页面真实 Host E2E 前，不删旧 server、REST/SSE/WS、DTO 和前端 stores。
- Software Company 真实仓交付、恢复、审批、预算、artifact 全绿前，不删 SessionDriver、
  AgentRuntime、workflow、meetings、ledger、budget、memory 和 evolution。
- 企业插件语义对照通过前，不删 RBAC、tenancy、OAuth MCP、audit、calendar、dependency graph。
- 安装包 smoke 覆盖双运行时前，不删 Electron 的旧 backend 启动路径。
- 无损迁移对照通过前，不删旧 session/checkpoint/memory/artifact 读逻辑。

删除必须独立提交；每个删除提交引用替代测试。最终增加零 legacy import 门才允许版本升为 1.0.0。

## 6. 后续实施队列

### P0 基础契约（必须先完成）

1. **16.10 Scope 隔离回归测试与修复**：先写 shadow/active interceptor 串扰、子 scope provider
   串扰、Code tool 卸载残留的失败测试；让每个 scope 拥有独立 registry，所有注册返回 effect。
2. **16.11 Host activation policy**：验证完整 manifest、API/semver、permission grants、credentials、
   health checks；记录 dependency epoch，并在切换失败时保持旧 epoch。Task 不重写；增加主
   activation/health 失败与 rollback failure 同时保真、递归脱敏、旧 epoch 不推进的回归。
   其后用独立测试提交固定每次真实 model request 的消息与 tool schema 可见面。
3. **16.12 Typed events**：schema 生成 discriminated event union；append/RPC/replay 统一校验 owner、
   ignorable、payload 与状态转换，禁止手写重复事件类型。
4. **16.13 Durable store**：增加 append transaction/batch、严格请求摘要、恢复 cursor、单写者或
   跨进程锁策略；Windows 替换与目录 durability 做专项测试。
5. **16.14 Real crash matrix**：杀死真实 Python 子进程，覆盖请求前、Agent 中途、提交后；重启后
   从日志恢复且不重复模型、工具、审批或领域事件。

### P1 真实双平面能力

6. 将 Organization run 改为事件驱动可恢复状态机，每个 action 以 durable intent/result 定界。
7. 迁移真实 task board、token/cost budget、approval、artifact、memory retrieval 和完整 evolution。
8. cancellation 贯穿 RPC、Agent、模型、工具和子进程树；超时必须留下结构化 durable failure。
9. 将 v0.7.2 企业实现逐个迁移为插件，每个插件独立存储、权限、租户和对照测试。
10. Code runtime 只通过 sandbox provider 启动，默认禁网、只读基础镜像、声明式工作区挂载和
    CPU/内存/进程/输出限制；无可用 sandbox 时 fail loud。

### P2 产品入口

11. 实现 Workbench/Web Server/Headless/Python SDK profiles 与 `software-company` bundle 文件。
12. 实现 Host HTTP/WebSocket transport：RPC、事件订阅、认证、租户 scope、背压和重连 cursor。
13. 让七个 CLI command 操作同一 profile loader；Codex MCP 仅作为 Host adapter。
14. 逐 store/page 迁移 React；删除页面级旧 DTO 前先补真实 Host E2E。
15. Electron 只监督 Cordis Host，由 Host 监督 Python；安装包内置两个 runtime。

### P3 迁移、删除与发布

16. 无损迁移完整数据树，生成 manifest/hash，整目录备份，验证投影后才提交，失败整批回退。
17. 为三个 benchmark 编写真实 runner、golden evidence 和 CI 门。
18. Windows/macOS/Linux installed-artifact smoke 验证无系统 Node/Python、恢复、迁移和进程清理。
19. 按“不得删除清单”逐模块删 legacy，并启用零 import/旧 DTO/旧事件门。
20. 许可证 freshness 与全部质量门全绿后，按版本清单同步 `1.0.0`、tag 和发布资产。

## 7. 公共接口预定变更

- `SessionEvent`：从开放字符串和通用 payload 改为生成式 discriminated union。
- `MutationMeta`：保留 request/idempotency/revision，并持久化 request digest 与 durable result。
- Event store：增加原子 `appendBatch`、`resumeCursor` 和 mutation result 查询。
- Host scope：显式 scope identity/parent/epoch；provider 和 interceptor 查找遵循 scope 链但注册归属
  当前 fiber。
- Host activation：输入 `PermissionGrantSet`、CredentialProbe 和 HealthCheck；成功返回 epoch report。
- Organization RPC：run 返回稳定的 `run_id/status/revision/result_event_seq`，重放返回同一结果形状。
- Host transport：stdio/HTTP/WebSocket/MCP 共用生成的 request/result/fault types。

## 8. 测试与安全矩阵

- 生命周期：shadow 与 active 并存；provider/listener/interceptor/tool/process/handle 装卸 100 次。
- 事件：未知必需事件、错误 owner、非法状态迁移、payload 污染、批量提交崩溃和并发 writer。
- 安全：write symlink/TOCTOU、解释器逃逸、命令注入、网络访问、凭据泄漏、审批绕过、Creator
  越权、双租户串扰。
- 恢复：真实 peer kill、Host kill、工具完成响应丢失、approval commit 响应丢失、重复 cancel。
- 产品：Standard、Code-Python、Code-TypeScript、Minimal、Creator real E2E；Software Company 修改
  真实仓、运行测试、产生 artifact，并从同一日志得到一致投影。
- 迁移：对比任务、审批、预算、记忆、artifact、fork、changes、checkpoint 和 UI projection。

## 9. 每阶段验收命令

基础回归：

```powershell
uv run pytest -q
pnpm typecheck:host
pnpm typecheck:agent
pnpm typecheck:organization
pnpm typecheck:presets
pnpm typecheck:enterprise
pnpm typecheck:cli
pnpm test:host
pnpm test:agent
pnpm test:organization
pnpm test:presets
pnpm test:enterprise
pnpm test:cli
uv run python scripts/generate_protocol.py --check
uv run python scripts/verify_agent_notes.py
```

产品门：

```powershell
Set-Location frontend
npm test -- --run
npm run build
npm run i18n:check
npm run e2e:real
```

发布阶段还必须运行新增的 benchmark、security、installed-artifact、migration rollback、license
freshness 与 zero-legacy-import 命令；这些命令应随对应任务实现，不能预先伪造为空壳门禁。

## 10. 已知风险

- Windows `rename`/文件占用语义与 POSIX 不同，durable store 必须做平台专项验证。
- `workspace.write` 的 symlink TOCTOU 和任意解释器执行目前是直接安全风险。
- Python RPC pending call 无 timeout；Host heartbeat 吞掉错误，不会主动隔离失效 peer。
- 同 session 并发 organization run 没有 durable lease，可能交错事件。
- 事件和错误尚无统一脱敏，模型供应商错误或工具输出可能泄漏凭据。
- enterprise Map key 只能证明基本 namespace，不能证明租户授权和存储隔离。

## 11. 下一位实现者第一步

从 Task 16.10 开始，严格先红后绿：

1. 在 Host 生命周期测试中构造 active 与 failing shadow 两套同名 onion interceptor，证明 shadow
   在切换前不可观察。
2. 激活/卸载 Code tool plugin 100 次，证明工具注册数回到基线且可再次注册。
3. 构造 tenant child scope，证明 provider override 不泄漏到父 scope 或兄弟 scope。
4. 失败证据稳定后再调整 registry ownership；不要先碰前端或删除 legacy。
