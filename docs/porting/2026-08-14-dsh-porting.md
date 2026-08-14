# dsh 契约移植基线（Task 14.1）

> 本文件是 v0.7.0 全部移植工作的唯一权威清单。任何后续任务（14.2–14.17）只按本清单搬运，
> 新增/变更搬运项必须先改本文件再实现。
> 调研依据：`T:\Programming\Project\codex\agent\deepseek-harness-report.md`（2026-08-14）与 `_dsh_research/` 七切片。

## 1. 源基线（固定，不随上游漂移）

| 项 | 值 |
|---|---|
| 仓库 | `deepseek-ai/deepseek-harness`（本地克隆 `T:\Programming\Project\codex\agent\deepseek-harness`） |
| commit | `47f943859b`（`Merge pull request #2519`） |
| release | `0.1.0-rc.5`（package.json 实测） |
| 许可证 | MIT（`LICENSE`）；本仓库移植的契约/格式/语义均保留出处标注，不复制源码文件 |

## 2. 搬运清单（契约级：格式/词汇/语义照搬，Python/React 等价实现）

### A. 事件溯源会话（→ 14.2/14.3）
- 事件命名 `domain/verb`（`turn/start`、`step/start`、`tool/call`、`tool/result`…）。
- surface 事件仅三类：`user/message` / `assistant/message` / `tool/result`；其余为 durable 事实。
- **model-visible ⟺ logged** 不变量：模型请求必须能从事件日志 `derive_messages()` 重建，
  JSON 串比对不一致即抛（对照 dsh `agent-loop/src/invariant.ts`）。
- **确定性请求派生**：system + tools + 消息投影顺序字节级稳定（对照 dsh `request/header` 折叠）。
- JSONL 头行（对照 `session-persistence-jsonl/src/format.ts`）：
  `{"type":"session","version","id","createdAt","cwd","parentSession","seedLength"}`
  → 本项目 `SESSION_FORMAT_VERSION=1`。
- 版本机制：单调整数、写者决定 bump、方向感知拒绝、per-event `ignorable` 标记。
- 事件词汇为**生成集**：`KNOWN_SESSION_EVENT_TYPES` 由生成器产出 + freshness 校验。

### B. 能力接缝（→ 14.4/14.5）
- 三角：Service Definition（ABC）+ Provider 注册表 + Consumer；换实现只改配置。
- effect 式注册：卸载按注册逆序回滚；同名 provider 重复加载 fail-loud。
- 注册表式 seam 与单例式 seam 并存；跨边界 id 用 Branded 风格（Python `NewType`）。

### C. 配置分层（→ 14.7）
- profile / bundle / patch 三层；patch 按 id **整块替换 + disabled**（无深合并）。
- `--dump-config` 离线渲染（不 eval 配置代码）；启动 `assertEntriesActivated` fail-loud。
- 内置 profile：`serve` / `chat` / `headless`（headless 不监听端口，跑完退出）。

### D. LLM 缓存（→ 14.6）
- usage 回填：`prompt_cache_hit_tokens` / `prompt_cache_miss_tokens`（DeepSeek/OpenAI）、
  `cache_read_input_tokens` / `cache_creation_input_tokens`（Anthropic）。
- DeepSeek 专属请求头：`x-deepseek-harness-session-id`（服务端会话 id）、
  `x-deepseek-harness-compact: 1`（压缩态）。**不搬运 `x-deepseek-harness-user-id`**（隐私缺陷）。
- 头锚定压缩：保头 + 定价尾部、中间剪枝、不拆 tool-call/result 对（对照 `compaction-basic/src/region.ts`）；
  压缩 LLM 调用重建真实前缀复用 KV 缓存。

### E. 数据与治理策略（→ 14.5）
- spill：`maxInlineBytes: 50000`，溢出写 0700 私有目录 + head/tail 预览。
- credentials：配置面只存**环境变量名引用**，每操作解析一次，空值=不存在，env+file 分层。
- guard：合作式工具超时 `TOOL_TIMEOUT` 结构化错误；连续重复调用提醒（只增强不授权）。
- plan/goal/jobs/schedule（→ 14.17）：`plan/mode` 折叠状态；`goal/change` 快照+CAS+轮次上限 256；
  jobs 注册表 first-wins settlement + owner 作用域授权；schedule at/after/every（5 分钟下限）。

### F. 工程化（→ 14.8）
- Agent Notes：路径 `{lifecycle}/{class}/yyyy-mm-dd-topic-title.md`，四态
  proposed/implemented/archived/rejected，封闭分类集合，无中央 INDEX，相对链接交叉引用。
- postmortem：三条件（隐蔽/系统性/重新发现代价高）+ Executive summary 开头。
- 生成器 + freshness 校验器配对（`gen-*` + `verify-*`），目录：config-catalog / tool-catalog / module-graph。
- pre-push 最小测试选择（按 change-scope 报告最小测试集，不盲目全量）。

### G. 前端源码级适配（→ 对应史诗任务）
- `ui-conversation` → SessionDetail 会话时间线；`ui-trajectory` → Audit 轨迹视图；
  `ui-permission-presets` → RBAC 设置；`ui-plan`/`ui-goal`/`ui-jobs` → PPM 面板。
- 保持 antd 主题与现有路由结构；数据全部接真实后端（禁止伪造）。

## 3. 不搬运清单（含理由）

| 项 | 理由 |
|---|---|
| Cordis 本体（`vendor/`） | TS + effect 追踪/HMR，Python 无法直接复用；以 `seam.py` 轻量等价实现（14.4） |
| Typert 类型图 | TS 类型层代码生成器，Python 无对应物，收益不匹配成本 |
| landlock-run（C11） | Linux-only 内核 UAPI；本项目 Windows 优先，沙箱沿用 Docker/worktree |
| 遥测匿名头 `x-deepseek-harness-user-id` | 报告 §6.2 明示隐私缺陷（不受遥测开关控制、跨会话可关联） |
| 逐文件 100% 行覆盖门禁 | 当前 658 测试基线规模下成本过高；以关键模块覆盖 + 不变量测试替代 |
| 全量带 key 真实 API 测试 | 成本；仅保留命中率门槛一项（14.6，自跳过） |
| zstd 压缩 | 暂缓，仅纯 JSONL（`SESSION_FORMAT_VERSION=1` 预留压缩位） |
| 双语 sidecar 文档体系 | 现有中文单语文档体系不迁移 |
| 匿名身份 UUID | 与隐私策略冲突；身份走 14.9 认证体系 |

## 4. MIT 出处

移植的契约、格式与语义源自 [deepseek-ai/deepseek-harness](https://github.com/deepseek-ai/deepseek-harness)
（MIT License，commit `47f943859b`，release `0.1.0-rc.5`）。本项目为 Python/React 等价实现，
未复制其源码文件；协议文本见 https://github.com/deepseek-ai/deepseek-harness/blob/47f943859b/LICENSE 。
