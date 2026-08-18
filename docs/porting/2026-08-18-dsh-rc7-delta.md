# DeepSeek Harness rc.5 → rc.7 差分采用矩阵

## 固定证据

| 项 | 核验值 |
|---|---|
| 范围 | `47f943859bef60e4160492346772ded9b24f765a..99f6f02fecdb7dff40c3fbc9470f5907c29f74ca` |
| 规模 | 111 commits、539 files、8,183 insertions、1,625 deletions |
| release | `dsh-v0.1.0-rc.7` |
| HEAD | `99f6f02fecdb7dff40c3fbc9470f5907c29f74ca` |
| Cordis | `@deepseek-ai/cordis@4.0.1`；`vendor/cordis` 无范围内源码变化 |
| license | MIT，LICENSE 内容未变化 |
| HEAD license SHA-256 | `EBB4F09972AEE8608BE255DEBAF78451A68E95C290F55C240DEC2ECFA16EA6BE` |

审查使用本地只读上游的 `git rev-parse`、`git tag --points-at`、`git rev-list --count`、
`git diff --shortstat`、`git diff --name-status` 和逐提交 `git show`。提交先按 Core/Host/LLM/
tools/MCP/ACP/subagent/packaging/test 分类，再核对源码与同提交测试；纯版本 bump、merge、翻译和
UI polish 不作为移植理由。上游测试文件是设计证据，本次文档任务没有把上游仓当作验收目标运行。

两个容易误判的事实：

- `notifications/tools/list_changed` 的串行事务重同步已存在于旧基线；rc.7 的 MCP 新能力是
  **durable image result projection**，不是该通知协议。
- rc.7 没有 Windows Python single-exe。Python SDK single-exe 仍只覆盖 Linux/macOS；新增 native
  resolver 只处理 Linux node-pty addon。Windows 增量是 native CI 与 node-pty beta 覆盖，不能替代
  DoAI 的 Windows 双 runtime installed-artifact 门。

## Adopt now

| 上游证据 | 能力/测试证据 | DoAI 决策与边界 |
|---|---|---|
| `caf7d48f88fb9768c7c8a0a4b7f3ed2bc581b3bd`; `packages/subagent/tool-subagent/src/index.ts` | `tool-subagent.spec.ts` 的 `reports startup rollback failure after cancellation as a failed task` 证明 cleanup `AggregateError` 不能被归类为 clean kill | 恢复 Task 16.11 时加入主 activation/health failure + rollback failure 同时保真回归；等待 rollback，递归脱敏，active scope/Host epoch/dependency epoch 全不推进。不是重写 Task 16.11，也不增加新运行时任务。 |
| `8692a1b76bd0672e27d3d5588bcb849dcb14dd32`; `scripts/smoke-python-runtime.py` 与 `scripts/snapshots/python-sdk-single-exe/minimal/model-visible.json` | 固定每一次模型请求的 ordered messages 与完整 tool schemas，只归一化动态运行时值 | Task 16.11 后独立 test-only commit，在 `packages/agent-runtime` 固定真实 `ModelProvider.generate` 调用面，补强 `model-visible ⟺ logged`。不得混入 Host activation commit。 |

## Adopt later（按依赖排序）

### P0 follow-up：typed content/replay 与 durable store

`7e95a00c8a5eed37fc8d16487b6a1a9b772b075c` 在
`packages/llm/llm/src/{types,assembler}.ts` 与 `packages/llm/llm-pi-ai/src/replay.ts` 引入响应级 +
逐 block 对齐的 replay envelope。`assembler.spec.ts` 证明 max-token 丢弃 tool call 时以同一 mask
丢弃 replay entry；`convert.spec.ts` 证明旧版、外来、畸形、provider/model/block 不匹配元数据会带
诊断降级为 provider-neutral history，而不是卡死 session。

DoAI 必须先完成 Task 16.12 generated typed event/content/replay vocabulary，再由 Task 16.13 提供
durable batch、cursor 与结构化 corruption code。持久 content 是权威，adapter replay 只恢复 fidelity；
不得先塞一个开放 `unknown` payload 旁路 schema。

### P1：durable rich content 链

采用顺序不能打乱：

Code Mode 的 typed tool-return 基础在 rc.5 旧基线已经存在；rc.7 在这里新增的是 image-bearing
nested result projection，不得把 typed return 本身误记为本次新能力。

1. `219d2a1fb965ba0d67c0abc73d4152401eb52722`：
   `packages/attachment/attachment/src/index.ts` 的 ordered batch admission；`tests/index.spec.ts` 证明
   完整批次 count/aggregate/media/成员校验在写入前完成，并保持输入顺序。
2. `57fc6bc539ee960531db4b3fb49db184db9fb5a1`、
   `de1720605115be81a966833e7e232c4deddcc1c5`：
   `packages/attachment/attachment/src/error.ts` 的 admission/storage typed failure codes，分类测试仍在
   `packages/attachment/attachment/tests/index.spec.ts`。
3. `e00146be738bcff67cb67d7839cd3a2ad767ad30`：
   `packages/core/tools/src/code-mode.ts` 的 Code nested image tool result 向外层投影；
   `packages/core/tools/tests/code-mode.spec.ts` 证明 post-execute removal/replacement 仍优先。
4. `49426cae02e9f0a638c06c58fd1001586bc5fa5b`：
   `packages/mcp/mcp-client/src/tools.ts` 的 durable image projection；`tests/mcp-client.spec.ts` 覆盖
   exact route、整批拒绝、取消、storage/policy 区分与 post-execute precedence。
5. `4f87c1fe6d6911809aaaaf0c30e4ceeeef5c13ea`、
   `adf4878b4a3b6b5890b6487acfbb683a62f2e201`：`packages/acp/acp/src/{content,index}.ts` 的 ACP image
   prompt/reply；`packages/acp/acp/tests/{content,turns}.spec.ts` 覆盖 admission 与无关 Agent work 隔离。

DoAI 在 Task 16.13 后依次提交 attachment core/typed errors/真正 atomic batch、tool projection、Code
nested image、MCP/ACP transport。所有引用必须进入唯一事件日志并由唯一 artifact owner 管理；如果
store 能提供原子 batch，DoAI 应强于上游“失败不返回 partial refs、但可能留下 orphan write”的边界。

### P1：durable product subagent Jobs

`28fcda2751c73882c3b0e4471f2d4d2813e7e0de` 通过
`apps/cli/config/agent-presets/*/agent.cordis.yml` 与 example compositions 让 Codex/Claude Code product
providers 作为 one-shot background jobs；
`packages/subagent/subagent-{codex,claude-code}/tests/loader-composition.e2e.ts` 和
`packages/subagent/tool-subagent/tests/tool-subagent.spec.ts` 固定 opt-in/job 行为。

DoAI 仅在 Task 16.13/16.14 之后采用：先 durable intent/result 和 Job lifecycle/cancel，再 Codex/
Claude adapters，最后 preset opt-in。Job 必须可恢复，并经过 tenant、budget、approval 与 process-tree
ownership；拒绝复制 upstream process-local ledger。

### P2：plugin settings

- `4366528a382694971397a7aebf51bc0d63d80f7e` 在
  `packages/host/apiproxy/src/api-proxy.ts` 与
  `packages/client/ui-plugin-config/src/client/{section-store,slot-contract}.ts` 让 settings API 由已注册
  namespace 驱动；`packages/host/apiproxy/tests/api-proxy-config.spec.ts` 验证任意 registered namespace，
  `d8035680b9d642e619311b23b4a3a14bd7955d85` 又在 `ui-plugin-config/tests` 增加 keyed section directory
  的 disposal、stale-read、invalidation 测试。DoAI P2 可采用 registry ownership/keyed cards，但
  remote exposure 必须另有显式 allow、tenant/RBAC、secret redaction 与 revision CAS；拒绝
  “注册即远程可写”。

### P3/首个 optional package：发布依赖门

- `7b973e27c807b4e4ece13329e74a5390d091d45e` 的 TypeScript Program gate 拒绝 optional dependency
  的 module-scope value load；测试区分会被 emit 的 bare/value/star export 与会擦除的 type imports。
  `47399764c5e245f1066a68f87bd5a65206d75d7f` 的 release graph 测试固定 install edge、peer cycle、
  dev exclusion 和 pre-pack cycle failure。DoAI 当前所有 workspace package 都是 private 且没有
  optional dependency，因此在 P3/首个 optional package 出现时再落门，并增加真实 packed artifact
  缺依赖测试、notices 与 SBOM。

### P2/P3：node-pty 与 Python installed artifact

`078dd2b6dffd67b41de0b93e48a680048e3b5892` 在
`packages/subprocess/subprocess-local/package.json` 将 node-pty 升至 beta 并更新补丁；
`a785eb80f7a82b4b5e5f585204441db01981c029` 在
`scripts/build-exe-for-python-sdk-native-pty.ts` 和 `scripts/build-exe-for-python-sdk.ts` 增加 Linux
addon resolver，`scripts/build-exe-for-python-sdk-native-pty.spec.ts` 覆盖 build/prebuild fallback。
构建目标仍只有 Linux/macOS，Windows 变化只是 native CI/node-pty 覆盖。

DoAI 当前没有三平台 Node Host + Python Organization installed-artifact 证据。P2 packaging 先做
node-pty beta security/ABI/许可与预编译供应链评估；P3 必须在 Windows/macOS/Linux 无系统 runtime
环境验证启动、恢复、迁移、取消和进程清理。结论明确为 **Windows Python single-exe 未实现**。

## Conscious divergence / reject now

| 上游 | DoAI 偏差 |
|---|---|
| `226600147e4a14e61bacfe3804d51b2125292df4` DeepSeek `low` reasoning effort | 不直接移植；等待跨 provider generated capability catalog。 |
| `a8dc6f9776d20d2e846e8373628ffd1a03808c84` persistent Bash 受控 prompt 修复 | 当前 `process.run` 是 one-shot；只有明确 persistent PTY 产品需求和安全边界后才采用。 |
| `c91cfc3ec2e8424bcb73c23a958490b4583051b7e` 以 `error.name` 跨 package copy 分类 corruption | 不采用可伪造 name；Task 16.13 使用稳定结构化 code/discriminant。 |
| `fd24df156dde2e93edd6d62719f0913287f56c13` 等英文 UI 将 Code preset 改名 PTC Mode | 保留 DoAI 产品名 `Code Mode`。 |
| `6e77499326e225d53d856a793ce0cb8e27879fde` browser-only dependency gate | 被 `f2830fec6ddaf898cf09036043b840e3b9b129ea` 回退且 rc.7 HEAD 不含该文件，不把中间提交当发布能力。 |

## 许可、安全与测试门

- `docs/porting/dsh-provenance.yaml` 固定 current/previous/range/license SHA，且
  `policy.automatic_tracking: false` 不得删除。
- 每个实际移植提交必须补 source file、commit、license、action、deviation、verification；原 rc.5
  imports 不得伪改 source commit。
- credentials、probe cause、health/rollback aggregate、settings、attachment metadata 与 replay
  diagnostics 都必须做递归脱敏；禁止 secret 进入日志、snapshot、epoch report 或 Agent Note。
- 每阶段严格先红后绿并执行受影响 package tests/typecheck、Agent Note 校验与 `git diff --check`。
- 三平台（Windows/macOS/Linux）Node Host + Python Organization installed-artifact、恢复、迁移与进程
  清理仍是 release 门；rc.7 的 Linux/macOS Python executable 和 Windows CI 不能替代该证据。

下一步唯一执行顺序见
`docs/superpowers/plans/2026-08-18-dsh-rc7-sync-implementation.md`。
