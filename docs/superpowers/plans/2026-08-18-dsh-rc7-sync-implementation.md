# DeepSeek Harness rc.7 同步后续实施计划

> 基线：`dsh-v0.1.0-rc.7` / `99f6f02fecdb7dff40c3fbc9470f5907c29f74ca`。
> 每项独立 TDD、独立 commit，不 push 未审查提交；不得提前创建后续能力空壳。

## 全局执行规则

1. 每项先读当前 task brief、`AGENTS.md`、`docs/lessons/02-testing.md` 和
   `docs/lessons/07-debugging.md`；3 次失败即停。
2. RED 必须证明缺失的产品契约，而非语法/导入错误；GREEN 后运行聚焦测试、受影响 package
   typecheck/test、`uv run python scripts/verify_agent_notes.py` 和 `git diff --check`。
3. 每个实际上游采用更新 `docs/porting/dsh-provenance.yaml` 的 imports/verification；不得把旧 import
   的真实 source commit 改写为新基线。
4. 不修改前端/legacy，除非下面的对应 P2/P3 task 已开始；删除 legacy 必须另有替代证据提交。

## 1. Task 16.11 — Host activation policy（恢复原任务，不重写）

**依赖：** Task 16.10 已完成。

**允许/禁止：** 允许 `packages/host/src`、Host tests/exports 和同任务 Agent Note；禁止
`agent-runtime` 内容/replay、Organization、frontend、legacy 与 packaging。

**RED：** 保留现有 `packages/host/tests/activation-policy.test.ts` 的 manifest/API/semver、all-grants-
before-any-probe、credential recursive redaction、shadow health、真实 provider owner、deep-readonly epoch
覆盖；增加一例主 activation/health failure 与 shadow disposer failure 同时发生：cleanup 完成后才
reject，primary 与 rollback failure 均以脱敏结构保留，旧 active scope/Host epoch/dependency report
逐字段不变。

**GREEN：** 只修改 `packages/host` 和必要公共 Host 类型；不能实现 typed events/durable store。

**安全/许可/回滚门：** credential/probe/inner aggregate 递归脱敏；不引入上游源码时不新增
provenance import。任一预检/start/health/cleanup 失败都不得改变旧 active 行为或任何 epoch；cleanup
failure 必须保真而不是覆盖 primary failure。

**验收：**

```powershell
pnpm --filter @doai/host exec vitest run tests/activation-policy.test.ts
pnpm typecheck:host
pnpm test:host
uv run python scripts/verify_agent_notes.py
git diff --check
```

**提交边界：** `Task 16.11: enforce host activation policy and dependency epochs`。

## 2. Model-visible/tool-schema snapshot（独立 test-only commit）

**依赖：** Task 16.11；不依赖 Task 16.12。

**允许/禁止：** 允许 `packages/agent-runtime/tests`、稳定 snapshot/fixture 与 testing Agent Note；禁止
Host activation、runtime 行为、frontend/legacy。若 RED 暴露真实 runtime defect，停止并另开修复 task。

**RED：** 在 `packages/agent-runtime/tests` 捕获一次完整 Standard Agent run 的每次
`ModelProvider.generate` 参数，固定 ordered messages 与完整 tool schemas。只归一化临时 cwd、时间、
call id 等动态值；system/user 文本保持原样，assistant/tool payload 只裁成稳定 identity。断言实际
request 等于从唯一日志投影的 model-visible history。

**GREEN：** 优先只加测试/fixture；若发现投影偏差，运行时修复另开 commit，不能藏在 snapshot 更新。

**安全/许可/回滚门：** snapshot 不得记录 secret、绝对临时路径、环境变量或完整敏感 tool payload；
若引用上游 normalizer 代码则新增 provenance，否则记录 differential-test。snapshot 更新必须逐行审查，
无法解释的 surface 变化就回滚 fixture 更新。

**验收：**

```powershell
pnpm --filter @doai/agent-runtime exec vitest run tests/model-visible-snapshot.test.ts
pnpm typecheck:agent
pnpm test:agent
```

**提交边界：** `Task 16.11a: pin the model-visible agent surface`。

## 3. Task 16.12 — Generated typed event/content/replay vocabulary

**依赖：** 1–2 完成。

**允许/禁止：** 允许 canonical schema、generator、generated protocol/Pydantic/TS artifacts、协议测试与
受影响的 Host/Agent/Organization consumers；禁止 store durability、attachments、frontend/legacy 删除。

**RED：** 扩展 canonical schema 与生成 freshness 测试，使 event 成为 owner/ignorable/payload/state
受约束的 discriminated union；content block 与 replay envelope 是生成类型。assembler 测试必须证明
content 与 per-block replay entry 使用同一个 keep/drop mask。durable content 永远权威。

**GREEN：** 只改 schema、generator、generated artifacts、protocol/agent projection 与必要 consumers；
禁止手写第二套事件 union，禁止顺带改 store durability。

**安全/许可/回滚门：** owner/ignorable/state/payload 校验 fail closed，unknown future event 只有显式
ignorable 规则才可跳过；实际采用 replay 结构时记录 upstream commit/path/MIT/deviation。生成失败或
consumer 不兼容则整体回滚 schema+生成物，不保留半套手写类型。

**验收：**

```powershell
uv run pytest tests/test_v1_contracts.py -q
uv run python scripts/generate_protocol.py --check
pnpm typecheck:agent
pnpm test:agent
pnpm typecheck:organization
pnpm test:organization
```

**提交边界：** schema/generator/generated output/consumers 作为一个原子提交。

## 4. Task 16.13 — Durable store，然后 replay 安全降级

**依赖：** Task 16.12。

**允许/禁止：** A 允许 `packages/agent-runtime/src/session-store.ts`、store tests 和必要 store types；
B 才允许 model adapter/projection/replay tests。禁止 attachments、MCP/ACP、frontend/legacy。

**RED A（store）：** append transaction/batch、request digest、mutation result、resume cursor、单写者或
跨进程锁、Windows replace + parent-directory durability、稳定结构化 corruption code；同 key 不同
digest 必须 fail loud。

**GREEN A：** `packages/agent-runtime/src/session-store.ts` 及其 tests；不加入 adapter replay。

**RED/GREEN B（replay）：** max-token transform 以同一 mask 处理 content/replay；legacy、foreign、
malformed 或 misaligned replay state 发出结构化 diagnostic 并退回 provider-neutral durable content，
不能让 session 永久不可继续。不得用 `error.name` 分类 corruption。

**安全/许可/回滚门：** request digest、corruption diagnostic 与 replay degrade 不得泄漏 credential/tool
secret；Windows replace/目录 durability 和崩溃中间态必须可恢复。采用 replay/assembler 设计时记录
`7e95a00c...` 的 MIT provenance。A 不绿不得开始 B；B 失败可独立回滚而不降级 durable store。

**验收：**

```powershell
pnpm --filter @doai/agent-runtime exec vitest run tests/session-store.test.ts
pnpm --filter @doai/agent-runtime exec vitest run tests/agent-loop.test.ts
pnpm typecheck:agent
pnpm test:agent
```

**提交边界：** A durable store 与 B replay alignment/degrade 必须两个独立提交。

## 5. Task 16.14 — Real crash matrix

**依赖：** Task 16.13 A/B。

**允许/禁止：** 允许 `packages/organization-bridge` supervisor/runtime tests、真实 Python fixture 和必要
Agent resume seam；禁止 attachments、settings、frontend、legacy 删除。

杀死真实 Python Organization 子进程，分别覆盖请求前、Agent 中途、durable commit 后；重启从 cursor
恢复且不重复模型、工具、审批或领域事件。测试必须观察真实 process exit/restart，不接受 FakeHost
抛错替代。验收至少运行：

```powershell
pnpm --filter @doai/organization-bridge exec vitest run tests/crash-matrix.e2e.test.ts
pnpm test:organization
pnpm typecheck:organization
pnpm test:agent
```

**安全/许可/回滚门：** kill/restart 日志不得包含 credential；孤儿进程必须回收；fixture 使用仓内实现，
无上游源码采用则不新增 import。任一 crash point 无 exactly-once 证据时保持 blocker 未完成，不用 retry
掩盖重复副作用。

**提交边界：** crash harness/fixtures 与 runtime recovery 可同一 task，但不得混入 rich content。

## 6. P1 — Durable attachment/rich-content 链

**依赖：** Task 16.12–16.14；唯一日志和 artifact owner 已可用。

**允许/禁止：** 允许新的 attachment core、`packages/agent-runtime` content/tool projection、后续独立
MCP/ACP Host adapter packages 与 tests；禁止 settings/UI、product Job、legacy 删除和旁路 artifact store。

**RED：** 分别先证明整批预检前零写入、ordered atomic commit/rollback、typed admission/storage error、
post-policy precedence、Code nested image、MCP exact route/cancel 与 transport verified output。

依次独立提交：

1. attachment core + typed admission/storage errors + 真 atomic ordered batch；全批校验在任何写入前。
2. tool result projection，policy/post-execute replacement/blocking 优先于准备中的 attachment projection。
3. Code nested image result；外层结果只引用已提交 durable attachment。
4. MCP image projection；exact route/capability/cancel/security gate。
5. ACP/其他 transport projection；image-only 不生成空 text，输出只含 verified durable images。

每个提交运行 `pnpm test:agent`/`typecheck:agent` 及新包聚焦 tests；MCP/ACP transport 不得先于 Host
transport owner 出现，也不得写旁路文件或第二事件流。

**验收：** 计划新增 `packages/agent-runtime/tests/{attachments,tool-result-projection,code-mode-images}.test.ts`
和 `packages/host/tests/rich-content-transport.test.ts`；逐层运行：

```powershell
pnpm --filter @doai/agent-runtime exec vitest run tests/attachments.test.ts tests/tool-result-projection.test.ts tests/code-mode-images.test.ts
pnpm --filter @doai/host exec vitest run tests/rich-content-transport.test.ts
pnpm typecheck:agent
pnpm test:agent
pnpm typecheck:host
pnpm test:host
```

**安全/许可/回滚门：** media type/size/count/route/cancel 全部 fail closed；引用只暴露 opaque id，
不记录原始敏感 bytes。每个采用提交登记对应 upstream commit/path/MIT/deviation。batch failure 必须无
可见 partial refs；若物理原子性未证明，回滚本阶段而不是接受 orphan 为契约。

**提交边界：** 上述 1–5 每项一个 commit，后一项可独立回滚且不破坏前一层 durable contract。

## 7. P1 — Durable one-shot product Jobs

**依赖：** Task 16.13–16.14、贯穿 cancellation 和 process-tree ownership。

**允许/禁止：** 允许 Organization durable Job domain、Host process adapter、Codex/Claude adapter 包、
presets/tests；禁止 tool-local Map ledger、settings/UI 和 legacy 删除。

**RED/GREEN：** 先以事件驱动状态机测试 pending/running/completed/failed/killed、重启恢复、kill race、
startup+rollback 双失败、tenant/budget/approval 与父子进程清理；GREEN 后才接 vendor adapter，最后
preset 明确 opt-in。

依次提交 durable Job lifecycle/cancel → Codex/Claude product adapters → preset opt-in/installed smoke。
RED 必须覆盖重启恢复、kill race、startup+rollback 双失败、tenant/budget/approval、父子进程清理。
禁止复制 upstream process-local Job ledger，禁止让 tool 自己成为状态 owner。

**安全/许可/回滚门：** adapter 无 grant/approval/budget 或 tenant mismatch 时 spawn 前失败；日志脱敏，
进程树可回收。移植 composition/provider pattern 时登记 `28fcda27...`/`caf7d48f...` provenance，并
逐 adapter 核对第三方条款；任何 adapter 可独立卸载/回滚，不改变 durable Job truth。

**验收/提交边界：** 每层运行 `pnpm test:organization`、`pnpm test:presets` 及受影响 typecheck；
Job lifecycle、每个 vendor adapter、preset opt-in 分开提交。

计划聚焦命令：

```powershell
pnpm --filter @doai/organization-bridge exec vitest run tests/jobs.e2e.test.ts
pnpm --filter @doai/presets exec vitest run tests/product-job-presets.e2e.test.ts
pnpm typecheck:organization
pnpm test:organization
pnpm typecheck:presets
pnpm test:presets
```

## 8. P2 — Plugin settings namespace 到 keyed UI card

**依赖：** 生成 transport、tenant/RBAC、credential redaction 与 revision/CAS。

**允许/禁止：** 允许 Host settings directory/API、enterprise authorization plugin、生成 transport client
及最后的 keyed UI card；禁止把 namespace registration 直接当 exposure grant，禁止旁路 CAS。

**RED/GREEN：** 先测 owner/dispose/stale/invalidation；再测未授权 namespace 不泄漏存在性、tenant/RBAC、
secret redaction、revision conflict；最后测 keyed card 只消费 served+authorized 交集。

依次提交 Host namespace directory（owner/dispose/stale/invalidation）→ explicit remote exposure + tenant/RBAC
+ secret redaction + CAS → keyed UI card。注册只表示 Host 内 ownership，**不表示远程可写**；未授权
namespace 的 describe/read/write 都 fail loud 且不泄漏存在性。

**安全/许可/回滚门：** remote write 必须显式授权并审计，secret 永不进入 projection/snapshot；采用
registry/keyed-slot pattern 时登记 `4366528a...`/`d8035680...` MIT provenance。任一 transport/auth 门
失败则维持 Host-local directory，不发布 UI mutation surface。

**验收/提交边界：** Host directory、remote authorization/CAS、keyed UI 三个提交；各自运行受影响
package tests/typecheck，UI 提交另跑聚焦 Vitest/build。

计划聚焦命令：

```powershell
pnpm --filter @doai/host exec vitest run tests/settings-directory.test.ts
pnpm test:host
pnpm typecheck:host
pnpm test:enterprise
pnpm typecheck:enterprise
Set-Location frontend
npx vitest run src/v1/settings-plugin-cards.test.tsx
npm run build
```

## 9. P2/P3 — PTY、installed artifacts 与发布门

**依赖：** 明确 persistent PTY 产品需求；sandbox/process-tree policy；P2 packaging profiles；P3 release
pipeline。不得由 rc.7 tag 自动触发。

**允许/禁止：** PTY task 只允许 terminal/sandbox/process supervision 与专用 tests；artifact task 允许
packaging/CI/installer；release gate 允许 scripts/notices/SBOM。禁止在当前 one-shot `process.run` 内偷塞
prompt protocol，禁止宣称 CI 等于 installed artifact。

**RED/GREEN：** PTY 先做 prompt override/readiness/cancel/process-tree/security tests；artifact 先做三平台
无系统 Node/Python 的启动/恢复/迁移/清理 RED；optional gate 用真实 packed fixture 移除依赖后验证
可选能力 fail scoped、主包仍可 load，再实现 compiler/import/release graph gates。

- Persistent PTY 只在明确产品需求后实施；先做 sandbox/process-tree/security design，再评估 node-pty
  beta 的 ABI、预编译产物、许可和三平台供应链。当前 one-shot `process.run` 不移植 Bash prompt patch。
- Windows/macOS/Linux 安装产物必须内置 Node Host + Python Organization，且无系统 runtime 时完成
  启动、恢复、迁移、取消与进程清理。rc.7 不含 Windows Python single-exe，不能作为完成证据。
- 首个 optional workspace package 或 npm publication 前，独立提交 optional dependency module-scope
  import gate、真实 packed artifact 缺依赖测试、release dependency graph、notices freshness 与 SBOM。

**安全/许可/回滚门：** node-pty beta 先审 ABI、prebuild 来源/hash、许可与 sandbox escape surface；
installer/SBOM 必须列真实 payload。任一平台失败保持 release blocker；可回滚 PTY/optional gate 单项，
不得降低三平台门或删除 notices。

**验收/提交边界：** persistent PTY、node-pty dependency/ABI、每个平台 artifact、optional import gate、
packed missing-dependency、release graph、notices/SBOM 分开提交；每项提供聚焦测试和 installed smoke 命令。

计划聚焦命令（文件随对应 task 先 RED 创建）：

```powershell
pnpm --filter @doai/agent-runtime exec vitest run tests/persistent-pty.test.ts
uv run pytest tests/test_v1_installed_artifacts.py -q
uv run pytest tests/test_v1_optional_dependency_artifact.py -q
uv run python scripts/verify_agent_notes.py
git diff --check
```

## 明确不得提前实现

- 不把 DeepSeek `low` reasoning effort 加到当前 provider；等待 generated cross-provider catalog。
- 不以 `error.name` 做 corruption 分类。
- 不把 Code Mode 政名为 PTC Mode。
- 不让 settings “注册即远程暴露”。
- 不在 typed events/durable store 前加入 attachment/replay side channel。
- 不以 Linux/macOS Python executable 或 Windows native CI 代替三平台双 runtime installed-artifact。
