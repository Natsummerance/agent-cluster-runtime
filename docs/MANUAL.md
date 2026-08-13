# 用户手册：agent-cluster-runtime

> 多 Agent 组织型全栈开发集群运行时 —— 安装、配置、运行与扩展指南。
> 适用版本：0.6.2 ｜ 环境：Windows 优先（其余平台兼容不验收），Python 3.11+

---

## 目录

1. 能力速览
2. 环境要求与安装
3. 快速开始（确定性后端，零配置）
4. 工具模式（真实工作区执行）
5. CLI 命令参考
6. 接入真实 LLM（DeepSeek / Codex 当前模型 / OpenAI）
7. 三协议模型接入（v0.4）
8. 连续开发 REPL：chat（v0.4）
9. 插件与 hooks（v0.4）
10. doctor 环境预检（v0.4）
11. Docker 沙箱（v0.4）
12. git worktree 隔离（v0.4）
13. 有界子代理（v0.4）
14. 工具层扩展：apply_patch / http_fetch / MCP resources / AGENTS.md 项目记忆（v0.4）
15. 流程文件（YAML）编写指南
16. 会议与审批门操作
17. 技能系统
18. 岗位系统与扩展
19. 进化提案
20. 绩效度量与信号
21. 退出码与错误处理
22. 常见问题（FAQ）
23. 目录结构
24. 桌面工作台：serve + React 前端 + Electron（v0.5）
25. 一键演示 demo（v0.5）
26. 记忆库与进化集成（v0.5）`r`n27. 项目组合层与看板：serve v0.6 扩展

---

## 1. 能力速览

| 能力 | 命令 | 说明 |
|---|---|---|
| 运行流程 | `agent-cluster run --flow <yaml> [--sandbox docker] [--worktrees]` | 编译并运行 YAML 流程，含审批交互；可选容器沙箱与按角色 worktree 隔离 |
| 工具模式 | `agent-cluster run --flow <yaml> --workspace <dir>` | 岗位 Agent 在真实工作区读文件/改代码/跑测试/走 git |
| 工具清单 | `agent-cluster tools list` | 列出内置工具与权限分层（read/workspace_write/dangerous） |
| MCP 清单 | `agent-cluster mcp list --server NAME=CMD` | 连接 MCP stdio 服务器并列出其工具 |
| 连续开发 REPL | `agent-cluster chat --workspace <dir>` | v0.4 连续多轮开发：关键词选岗 + 工具模式 + token 计量 + 斜杠命令 |
| 环境预检 | `agent-cluster doctor` | v0.4 Python/git/Docker 硬依赖 + 模型/工作区/插件/MCP 检查 |
| 插件清单 | `agent-cluster plugins list` | v0.4 列出发现的插件、技能与 hooks（双清单 + marketplace） |
| 技能列表 | `agent-cluster skills list --root <dir>` | 列出 SKILL.md 技能目录 |
| 岗位列表 | `agent-cluster roles list` | 列出 12 个岗位与审批范围 |
| 进化演示 | `agent-cluster proposals demo` | 六步进化闭环演示 |
| 提案提交 | `agent-cluster proposals submit --title ... --rollback-plan ...` | 提交进化提案 |
| 度量演示 | `agent-cluster metrics demo` | 度量采集与阈值信号演示 |

`python -m agent_cluster` 与 `agent-cluster` 等价；`main()` 返回 int 退出码
（0 成功，1 失败）。

## 2. 环境要求与安装

前置：Python 3.11+ 与 [uv](https://docs.astral.sh/uv/)（推荐）或任意虚拟环境工具。

```bash
# 1) 克隆或进入项目目录
git clone <本仓库地址>
cd agent-cluster-runtime

# 2) 安装依赖（首次）
uv sync

# 3) 查看 CLI 帮助（中文）
uv run agent-cluster --help
```

若使用原生 pip：

```bash
python -m venv .venv
source .venv/bin/activate        # Windows PowerShell：.venv\Scripts\Activate.ps1
pip install -e .
```

## 3. 快速开始（确定性后端，零配置）

默认 `deterministic` 后端无需任何 API key，可完整跑通 MVP 闭环，用于验证安装与理解流程：

```bash
# 无人值守运行（--yes 自动接受全部审批；bypass-immune 高风险门自动拒绝）
uv run agent-cluster run --flow examples/flows/fullstack-sprint.yaml --project examples --yes

# 交互式运行：遇审批门打印请求并等待人工输入结论
uv run agent-cluster run --flow examples/flows/fullstack-sprint.yaml --project examples
```

运行结束后打印摘要：事件总数、会议数、任务数（状态分布）、审批记录、产出物清单。

## 4. 工具模式（真实工作区执行）

> v0.2 新增：岗位 Agent 不再只输出文本摘要，而是在**真实工作区**里读文件、改代码、
> 跑测试、走 git，最终产出可运行的代码库。默认确定性演示路径（不传 `--workspace`）
> 完全不变。

### 4.1 概念

- 传入 `--workspace <目录>` 即启用工具模式：岗位节点进入 ReAct 循环（模型 → 工具调用 →
  工具结果 → 继续），通过受限工作区真实执行。
- 工作区为**空目录** = 新项目场景（生成可运行项目）；工作区为**已有 git 仓库** =
  既有仓库功能开发场景（在仓库内改代码、跑测试、提交）。
- 不传 `--workspace` 保持 v0.1 的确定性演示模式（向后兼容）。

### 4.2 工具与权限分层

内置 23 个工具，按风险分四层；`tools list` 可查看实时清单：

| 权限 | 行为 | 工具 |
|---|---|---|
| `read` | 自动执行，无需审批 | `list_dir` / `read_file` / `grep` / `glob` / `git_status` / `git_diff` / `count_tokens` |
| `workspace_write` | 自动执行 + 审计留痕 | `write_file` / `edit_file`（多 hunk）/ `apply_patch`（codex 风格补丁块）/ `mkdir` / `git_add` / `git_commit` / `git_revert` / `git_init` / `run_tests` |
| `dangerous` | 走审批门（HITL interrupt） | `run_shell`（非白名单）/ `run_python` / `delete_file` / `git_push` / `run_service` / `http_fetch`（出网抓取） |
| `human_interaction` | 向人工提问，等待自由文本回答 | `ask_user`（PM 需求澄清） |

- 路径统一 `resolve` 后校验必须落在工作区内（`../`、绝对路径、符号链接外逃一律拒绝）。
- `run_tests` 为白名单命令执行器（默认 `uv run pytest -q`，可配置测试/构建命令前缀）。
- 危险工具在 `--yes` 无人值守下**自动拒绝**（bypass-immune），人工 `ask` 模式挂起等待
  accept/reject；恢复后同一危险调用复用上次审批决策（节点重跑幂等）。

### 4.3 模型双轨工具协议

- 原生 function calling：DeepSeek / OpenAI 客户端走 `tools` 参数并解析 `tool_calls`。
- 文本 JSON action 回退：不支持原生工具的模型可从回复中解析 fenced JSON 动作
  （`{"name": ..., "args": {...}}`）。
- 无 API key 时可用确定性工具脚本（`--tool-script`）跑通工具全链路。

### 4.4 两场景示例

```bash
# 场景 A：空工作区生成可运行新项目（确定性脚本演示，零 key）
# 注意：--max-rounds 需大于脚本步数（脚本 7 步 + 1 轮收尾文本）
uv run agent-cluster run --flow examples/flows/build-new-project.yaml \
  --workspace .agent-cluster-demo/ws-new \
  --tool-script examples/tool-scripts/build-new-project.json \
  --max-rounds 10 --yes

# 场景 A（真实 LLM）：岗位 Agent 自主决策写文件/跑测试/提交
uv run agent-cluster run --flow examples/flows/build-new-project.yaml   --workspace .agent-cluster-demo/ws-new --model codex --yes

# 场景 B：既有 git 仓库功能开发（在仓库内修复/开发，QA 真实测试通过才 Done）
uv run agent-cluster run --flow examples/flows/build-new-project.yaml   --workspace C:\path\to\existing-repo --model deepseek-v4-flash --yes
```

分岗位质量门槛：开发岗产出真实文件即进入评审；QA/评审/排查岗必须 `run_tests` 真实
通过才算 Done，失败自动回流返工。工具模式运行结束若存在**验收未通过的岗位任务**
（review / blocked），CLI 退出码为 1；会议生成的 todo 行动项属于积压清单，不视为失败。

### 4.5 MCP 外部工具（stdio）

```bash
# 注册外部 MCP stdio 服务器（可重复 --mcp），工具名 mcp_<name>_<tool>
uv run agent-cluster run --flow examples/flows/build-new-project.yaml   --workspace .agent-cluster-demo/ws-new   --mcp "fs=npx -y @modelcontextprotocol/server-filesystem C:\tmp" --yes

# 单独查看某 MCP 服务器暴露的工具
uv run agent-cluster mcp list --server "fs=npx -y @modelcontextprotocol/server-filesystem C:\tmp"
```

- 外部工具一律注册为 `dangerous` 权限（外部工具不可信，执行前必须人工审批）。
- 连接失败即 fail-fast 报错退出（退出码 1）。

## 5. CLI 命令参考

### 5.1 `build` —— 会话式产品构建（v0.3）

```
usage: agent-cluster build [--goal GOAL] [--workspace DIR] [--flow YAML]
                           [--model MODEL] [--resume] [--budget N]
                           [--max-rework N] [--deterministic] [--yes]
                           [--qa-script JSON] [--tool-script JSON]
                           [--role-tool-script JSON] [--skills-root DIR]
                           [--sandbox none|docker] [--plugin-dir DIR]
                           [--mcp NAME=COMMAND] [--max-rounds N]
```

输入一个需求，向导全程交互产出完整交付包（PRD/代码/测试/部署/手册/`DELIVERY.md`）。

| 参数 | 说明 |
|---|---|
| `--goal GOAL` | 产品需求目标（`--resume` 时可省略） |
| `--workspace DIR` | 工作区；缺省 `build-out/<目标>` |
| `--sandbox MODE` | 执行沙箱：`none`（本机，缺省）/ `docker`（容器内执行；无 Docker 时给指引并退出非零码） |
| `--flow YAML` | 生命周期流程（缺省 `examples/flows/build-product.yaml`） |
| `--model MODEL` | `codex`（缺省，解析当前对话模型）/ `deterministic`（演示）/ `deepseek-*` |
| `--resume` | 断点续跑：从 `.agent-cluster/` 检查点继续 |
| `--budget N` | 全局 token 预算（缺省 500000），阶段预算按 10/15/50/15/5/5 比例 |
| `--max-rework N` | 单门返工上限（缺省 3，超过升级人工） |
| `--deterministic` | 确定性演示模式（无需 API key，token 为估算值） |
| `--yes` | 无人值守：门自动接受、澄清用缺省答案留痕、升级自动结束（退出码 3） |
| `--qa-script JSON` | 脚本化澄清问答（字符串数组），供演示/测试 |
| `--role-tool-script JSON` | 按岗位工具脚本（`{role: [tool_call]}`） |
| `--mcp NAME=COMMAND` | MCP stdio 服务器（可重复），外部工具一律危险权限 |
| `--plugin-dir DIR` | 插件搜索目录（可重复；缺省包含 ~/.codex/plugins/cache），插件技能与 hooks 自动接入 |

**交互命令**（向导等待输入时）：`/status`（token 消耗/剩余/阶段）、`/budget`（预算明细）、
`/skip`（跳过当前澄清/接受当前门）、`/abort`（保存检查点退出，退出码 2）。

**退出码**：`0` 成功；`1` 存在验收未通过任务；`2` 用户中止（检查点已保存，可 `--resume`）；
`3` 升级结束（预算超限/返工上限，保存现状）。

```
# 演示（确定性，零 key）
uv run agent-cluster build --goal "做一个待办事项网站" --deterministic --yes \
  --workspace build-out/demo --budget 500000

# 真实 LLM（deepseek-v4-flash / Codex 配置）
uv run agent-cluster build --goal "做一个带用户系统的待办事项 Web 应用" \
  --workspace build-out/todo --model codex

# 断点续跑
uv run agent-cluster build --workspace build-out/todo --resume
```

### 5.2 `run` —— 编译并运行流程



```
usage: agent-cluster run [-h] --flow FLOW [--project PROJECT] [--yes]
                         [--thread THREAD] [--model MODEL]
                         [--workspace WORKSPACE] [--sandbox none|docker]
                         [--worktrees] [--mcp NAME=COMMAND]
                         [--max-rounds MAX_ROUNDS] [--tool-script TOOL_SCRIPT]
                         [--skills-root SKILLS_ROOT]
```

| 参数 | 说明 |
|---|---|
| `--flow FLOW` | 必填。流程 YAML 文件路径 |
| `--project PROJECT` | 项目目录；缺省用流程名生成项目名 |
| `--yes` | 无人值守：自动接受全部审批（bypass-immune 门自动拒绝） |
| `--thread THREAD` | 线程 id；缺省用流程 YAML 的 `thread_id` |
| `--model MODEL` | 岗位模型后端：`deterministic`（缺省）/ `deepseek-*` / `codex`；缺省可经环境变量 `DEEPSEEK_MODEL` |
| `--workspace DIR` | 启用工具模式：工作区目录（空目录=新项目，已有 git 目录=既有仓库功能开发） |
| `--sandbox MODE` | 执行沙箱：`none`（本机，缺省）/ `docker`（容器内执行 shell/python/tests/service；需 `--workspace`，无 Docker 时给指引并退出非零码） |
| `--worktrees` | git worktree 隔离：每开发角色独立 worktree 提交，节点完成后合并回主工作区（需 `--workspace`） |
| `--mcp NAME=COMMAND` | MCP stdio 服务器（可重复）；工具注册为 `mcp_<name>_<tool>`，危险权限 |
| `--max-rounds N` | 工具模式 ReAct 最大轮数（缺省 6） |
| `--tool-script JSON` | 确定性工具脚本（`[{name, args}, ...]`），无 API key 跑通工具全链路 |
| `--skills-root DIR` | 技能根目录（把岗位技能上下文注入工具模式 system prompt） |

### 5.3 `chat` —— 连续多轮开发 REPL（v0.4）

```
usage: agent-cluster chat [--workspace DIR] [--model MODEL] [--sandbox none|docker]
                          [--budget N] [--max-rounds N] [--deterministic] [--yes]
                          [--skills-root DIR] [--plugin-dir DIR] [--mcp NAME=COMMAND]
```

| 参数 | 说明 |
|---|---|
| `--workspace DIR` | 工作区（缺省当前目录）；空目录=新项目，已有 git 目录=既有仓库开发 |
| `--model MODEL` | `codex`（缺省）/ `deterministic`（演示）/ `deepseek-*` |
| `--sandbox MODE` | 执行沙箱：`none` / `docker` |
| `--budget N` | 全局 token 预算（缺省 500000） |
| `--max-rounds N` | 单轮 ReAct 最大轮数（缺省 6） |
| `--deterministic` | 确定性演示模式（无需 API key） |
| `--yes` | 无人值守：危险工具自动拒绝、澄清用缺省答案 |
| `--skills-root DIR` | 技能根目录（挂载岗位技能上下文） |
| `--plugin-dir DIR` | 插件搜索目录（可重复；缺省包含 ~/.codex/plugins/cache） |
| `--mcp NAME=COMMAND` | MCP stdio 服务器（可重复），外部工具一律危险权限 |

每轮指令按关键词自动选择岗位（详见 §8），进入工具模式 ReAct 循环；跨轮保留模型消息历史
（至多 12 条）。斜杠命令：`/status`（token 消耗/剩余）、`/budget`（预算明细）、
`/skills`（已挂载技能）、`/plugins`（已发现插件与 hooks）、`/exit`（退出，0）。
插件 hooks（session_start / session_end / user_prompt_submit / stop）随会话自动执行。
退出码：0 正常 / 1 运行失败 / 2 中断（Ctrl+C）。

### 5.4 `doctor` —— 环境预检（v0.4）

```
agent-cluster doctor [--model MODEL] [--workspace DIR] [--plugin-dir DIR]
                     [--mcp NAME=COMMAND] [--skip-docker-check]
```

检查项：python（≥3.11，硬性）、git（硬性）、docker（v0.4 硬依赖，缺失给安装指引，
`--skip-docker-check` 跳过）、model / workspace / plugin_dirs / mcp（信息性）。
全部硬性项通过返回 0，否则打印阻塞项并返回 1（详见 §10）。

### 5.5 `plugins list` —— 插件清单（v0.4）

```
agent-cluster plugins list [--plugin-dir DIR]
```

输出发现的插件（name@version / 技能数 / hooks 事件）与插件技能；搜索目录 =
显式 `--plugin-dir` + 缺省目录（`~/.codex/plugins/cache` 与 `AGENT_CLUSTER_PLUGIN_DIRS`）。

### 5.6 `skills list` —— 技能列表

```
agent-cluster skills list --root <技能根目录>
```

输出技能 `name@version` 与描述；要求目录下技能按 `name/SKILL.md` 组织。

### 5.7 `roles list` —— 岗位列表

```
agent-cluster roles list
```

输出 12 个岗位的 id / 名称 / 类别 / 审批范围。

### 5.8 `proposals` —— 进化提案

```
agent-cluster proposals demo
agent-cluster proposals submit --title <标题> --rollback-plan <回滚方案> \
    [--author-role pm] [--category skill]
```

- `--rollback-plan` 必填且不可为空白（防止不可回滚的进化）。
- `--category` 取值：`skill` / `knowledge` / `process` / `organization`。

### 5.9 `tools list` —— 工具清单

```
agent-cluster tools list
```

输出内置工具的名称、权限分层（read / workspace_write / dangerous）与描述。

### 5.10 `mcp list` —— MCP 服务器工具清单

```
agent-cluster mcp list --server "NAME=COMMAND"
```

连接 MCP stdio 服务器并列出其暴露的工具；连接失败退出码 1。

### 5.11 `metrics demo` —— 度量演示

```
agent-cluster metrics demo
```

打印采集的度量点、快照指标数与触发的进化信号。

## 6. 接入真实 LLM

API key 只从环境变量或 Codex 配置读取，**绝不写入仓库、日志或检查点**。

### 6.1 DeepSeek（推荐，与当前 Codex 对话同源）

```bash
# 环境变量：DEEPSEEK_API_KEY（必填）
# 默认端点 https://api.deepseek.com；若本机 Codex 配置了 [model_providers.custom]
# 会自动解析 base_url 与 env_key
uv run agent-cluster run --flow examples/flows/fullstack-sprint.yaml --project examples \
  --model deepseek-v4-flash --yes
```

行为说明：

- 走 `chat/completions`，只返回 `message.content`；推理模型的思维链（`reasoning_content`）
  **不作为任务输出**。
- `max_tokens` 同时计入推理与回答；若推理吃满预算导致 content 为空，自动扩容重试一次
  （至多 8192），仍为空才报错。
- 启动即 fail-fast：缺 key / 模型名无效时立即报错退出 1。

### 6.2 复用当前 Codex 对话模型

```bash
uv run agent-cluster run --flow examples/flows/fullstack-sprint.yaml --project examples \
  --model codex --yes
```

读取 `~/.codex/config.toml`（或 `CODEX_HOME`）的顶层 `model` / `model_provider` 与
`[model_providers.<provider>]` 节；供应商为 DeepSeek 时接入 `DeepSeekClient`，OpenAI 系
接入 `OpenAIClient`。

### 6.3 环境变量兜底

```bash
# PowerShell
$env:DEEPSEEK_MODEL = "deepseek-v4-flash"
# bash
export DEEPSEEK_MODEL="deepseek-v4-flash"
uv run agent-cluster run --flow examples/flows/fullstack-sprint.yaml --yes
```

### 6.4 OpenAI

```bash
# 需要 openai 包：uv add openai；环境变量 OPENAI_API_KEY
uv run agent-cluster run --flow examples/flows/fullstack-sprint.yaml \
  --model gpt-4o-mini --yes
```

### 6.5 模型选择优先级

岗位 `Role.model` 偏好 > 运行时默认（`--model` / `DEEPSEEK_MODEL`）> `deterministic`。

## 7. 三协议模型接入（v0.4）

`ModelConfig.wire_api` 决定线上协议，`ChatModelFactory` 自动路由：

| wire_api | 后端 | 端点 | 环境变量 |
|---|---|---|---|
| `chat`（缺省） | DeepSeekClient / OpenAIClient | `/v1/chat/completions` | `DEEPSEEK_API_KEY` / `OPENAI_API_KEY` |
| `responses` | OpenAIResponsesClient | `/v1/responses` | `OPENAI_API_KEY` |
| `anthropic` | AnthropicClient | `/v1/messages` | `ANTHROPIC_API_KEY` |

- 三协议均支持原生工具调用（function calling / tool_use）与 usage 计量；推理类
  reasoning 输出不作为任务结果。
- `--model codex` 时以 Codex `config.toml` 供应商的 `wire_api` 为权威（协议优先）；
  显式 `ModelConfig.wire_api` 次之，缺省 `chat`。
- 全部走 stdlib `urllib` 直连，零新增依赖；缺 key 启动即 fail-fast 报错退出 1。

## 8. 连续开发 REPL：chat（v0.4）

`agent-cluster chat` 是连续多轮开发入口：每轮用户指令 → 关键词启发式选岗 → 工具模式
ReAct 循环（真实工作区执行）→ 下一轮。

| 指令关键词 | 岗位 |
|---|---|
| 测试 / 检查 / 验收 / 回归 / 质检 | qa |
| 部署 / 发布 / 上线 / docker / 镜像 / 运维 / 监控 | devops |
| 文档 / 手册 / README / 说明 | docs |
| 架构 / 设计评审 / 方案 / 技术选型 / 数据库设计 | architect |
| 前端 / 页面 / UI / 界面 / 组件 / 样式 / 交互 | frontend |
| 算法 / 模型 / 训练 / 推理 / 特征 / 数据 | algorithm |
| 接口 / API / 后端 / 数据库 / 服务 / 登录 / 业务 | backend |
| 需求 / PRD / 竞品 / 产品 / 用户 | pm |

未命中关键词回落到 backend。跨轮上下文：模型消息历史在轮次间保留（至多 12 条）；
token 计量经 TokenLedger 记账（`/status` / `/budget` 查看）；插件 hooks
（session_start / session_end / user_prompt_submit / stop）自动执行。

```bash
# 确定性演示（零 key）
uv run agent-cluster chat --workspace .agent-cluster-demo/ws-chat --deterministic
```

## 9. 插件与 hooks（v0.4）

- **双清单合并**：`.codex-plugin/plugin.json` 与 `.claude-plugin/plugin.json`
  （name/version/description/keywords/skills/hooks/interface）合并为
  `PluginManifest`，对应 codex-rs `PluginManifestPaths`。
- **11 事件 hooks**：`PreToolUse` / `PermissionRequest` / `PostToolUse` /
  `PreCompact` / `PostCompact` / `SessionStart` / `SessionEnd` /
  `UserPromptSubmit` / `SubagentStart` / `SubagentStop` / `Stop`
  （PascalCase 与 snake_case 均接受）。
- 每个事件可配 `MatcherGroup`（多个 handler）；handler `type` 支持
  `command` / `mcp_tool` / `prompt` / `agent`——本平台执行 `command`
  （含 `commandWindows` 平台回退、timeout、async），其余类型记录为「不支持」。
- hook 输入：子进程 stdin 写 JSON payload（session_id / cwd / hook_event_name /
  model / permission_mode / source 等），并注入 `AGENT_CLUSTER_*` 环境变量。
- 插件技能以 `plugin:<插件名>:<技能名>` 命名空间加载进 SkillRegistry。
- 搜索目录：显式 `--plugin-dir`（可重复）+ 缺省 `~/.codex/plugins/cache` +
  `AGENT_CLUSTER_PLUGIN_DIRS`；`plugins list` 查看发现结果。

```bash
uv run agent-cluster plugins list
uv run agent-cluster chat --workspace ws --plugin-dir .codex-plugins
```

## 10. doctor 环境预检（v0.4）

`agent-cluster doctor` 运行环境预检：

| 检查项 | 级别 | 说明 |
|---|---|---|
| python | 硬性 | 运行时版本 ≥ 3.11 |
| git | 硬性 | git 可用（工具模式依赖） |
| docker | 硬性（v0.4） | Docker 可用；缺失给安装指引，`--skip-docker-check` 跳过（沙箱功能将不可用） |
| model | 信息性 | 模型配置可构造客户端（缺 key 不阻断，可回落 deterministic） |
| workspace | 信息性 | 工作区目录可写（传参时检查） |
| plugin_dirs | 信息性 | 插件目录存在（传参时检查） |
| mcp | 信息性 | MCP 服务器参数可解析（传参时检查） |

全部硬性项通过 → 退出码 0；存在阻塞项 → 打印指引并退出码 1。

## 11. Docker 沙箱（v0.4）

`--sandbox docker`（run / build / chat）把工具执行放入容器：

- 前置：Docker 可用（`docker version`）；需要 `--workspace`（沙箱挂载工作区到
  `/workspace`）。
- 执行位置：`run_shell` / `run_tests` / `run_python` / `run_service` 均在容器内执行。
- 镜像：默认 `python:3.11-slim`，可用环境变量 `AGENT_CLUSTER_SANDBOX_IMAGE` 覆盖；
  镜像内需自备测试工具（如 uv/pytest），否则命令以明确错误返回。
- 权限语义不变：沙箱只改变执行位置，白名单 / 审批仍由 ToolSession 负责
  （`--yes` 危险工具自动拒绝）。
- 无 Docker 时：CLI 打印安装指引（「请安装 Docker Desktop 并启动，或用
  `--sandbox none` 在本机执行」）并以退出码 1 结束，不执行流程。

## 12. git worktree 隔离（v0.4）

`--worktrees`（run，需同时 `--workspace`）启用按角色 worktree 隔离：

- 工作区不是 git 仓库时自动 `git init` + 初始提交（worktree add 需要 HEAD）；
  git 身份未配置时提交失败并给出明确错误。
- 每个开发角色在独立 worktree 分支（前缀 `acs-`）提交，节点完成后 `merge_back`
  （`--no-ff`）合并回主工作区并清理 worktree，避免并行写冲突。
- 越界校验：worktree 会话的 ToolSession 以 worktree 路径为根，`../` 逃逸天然拒绝。
- 合并冲突时不删除 worktree（保留现场），返回失败信息由上层决定处理。

## 13. 有界子代理（v0.4）

工具模式（run / build / chat）自动注册 `run_subagent` 工具（危险权限，需人工审批，
`--yes` 自动拒绝）：

- 独立 ReAct 循环：子任务 prompt + 工具 schema → 逐轮推理 → 执行工具 → 回传结果。
- 双截断：token 预算（缺省 20,000）与 `max_rounds`（缺省 6），超限即返回
  （truncated=true），不无限循环。
- 权限收敛：子代理只使用只读 + 工作区写工具，不内置危险工具。
- 计量不旁路：子代理消耗的 token 经 usage_hook 记入现有 TokenLedger / 运行时报表。

## 14. 工具层扩展：apply_patch / http_fetch / MCP resources / AGENTS.md 项目记忆（v0.4）

- `apply_patch`：codex 风格补丁块工具（`*** Begin Patch` / `*** Update File: <rel>` /
  `@@` 多 hunk / `-` 删除 / `+` 新增），workspace_write 权限（自动执行 + 审计），
  适合大范围多文件原子修改，格式错误即报错不落盘。
- `http_fetch`：HTTP(S) URL 抓取（只读网络工具），dangerous 权限（出网需人工审批，
  `--yes` 自动拒绝）；默认 15s 超时、单次最多 200,000 字节。
- MCP resources：服务器支持时自动注册 `mcp_<server>_read_resource` 工具（dangerous），
  经 `resources/list` 发现、`resources/read` 按 URI 读取结构化数据（配置/schema/文档）；
  服务器不支持 resources 时跳过注册。
- AGENTS.md 项目记忆：工具模式（run / build / chat）自动读取工作区根目录
  `AGENTS.md`（`load_agents_md`），注入岗位 ReAct 的 system 上下文（上限 20,000
  字符，超长截断附注）；无文件则跳过，跨会话保持项目约定。

## 15. 流程文件（YAML）编写指南

### 15.1 顶层结构

```yaml
name: fullstack-sprint          # 流程名（也是缺省项目名）
description: 流程说明（可含完整 MVP 链描述）
max_iterations: 40              # 编译期校验必须 ≥ 节点总数（防死循环）
thread_id: "proj:demo:iter:1"   # 缺省线程 id
nodes: [...]                    # 节点列表
edges: [...]                    # 边列表
```

### 15.2 节点类型

| 类型 | 字段 | 说明 |
|---|---|---|
| `start` / `end` | 无 | 起止节点 |
| `agent` | `role: <岗位id>` | 岗位执行节点（角色执行层产出任务/消息/账本） |
| `meeting` | `meeting: <会议类型>`, `participants: [岗位id...]` | 会议节点（纪要/决策/行动项） |
| `gate` | `gate: <门类型>` | 审批门节点（interrupt 挂起等待人工） |
| `parallel` | `children: [子节点id...]` | 并行执行子节点 |

会议类型：`kickoff` / `requirement_review` / `design_review` / `daily_standup` /
`code_review` / `retro` / `release_review`。

门类型：`requirement_confirmation` / `design_review` / `iteration_acceptance` /
`release` / `evolution_apply` / `dangerous_tool`。

### 15.3 边与条件路由

```yaml
edges:
  - {from: design_gate, to: develop_parallel, on_accept: develop_parallel, on_reject: design, on_edit: design}
  - {from: release_gate, to: end, on_accept: end, on_reject: release}
```

- 普通边：`{from, to}`。
- 门节点的边可带 `on_accept` / `on_reject` / `on_edit` 条件路由，用于「返工回路」：
  - `accept` → 继续；
  - `reject` → 回流（如设计门拒绝回设计节点）；
  - `edit` → 带修改意见进入目标节点。

### 15.4 完整示例（内置 `examples/flows/fullstack-sprint.yaml`）

```yaml
name: fullstack-sprint
description: 全栈冲刺 MVP 闭环
max_iterations: 40
thread_id: "proj:demo:iter:1"
nodes:
  - {id: start, type: start}
  - {id: requirement_review, type: meeting, meeting: requirement_review, participants: [pm, architect, frontend, backend, qa]}
  - {id: requirement_gate, type: gate, gate: requirement_confirmation}
  - {id: design, type: agent, role: architect}
  - {id: design_review, type: meeting, meeting: design_review, participants: [architect, pmo, frontend, backend, qa, devops]}
  - {id: design_gate, type: gate, gate: design_review}
  - {id: develop_parallel, type: parallel, children: [develop_frontend, develop_backend]}
  - {id: develop_frontend, type: agent, role: frontend}
  - {id: develop_backend, type: agent, role: backend}
  - {id: code_review, type: meeting, meeting: code_review, participants: [frontend, backend, reviewer]}
  - {id: test, type: agent, role: qa}
  - {id: iteration_gate, type: gate, gate: iteration_acceptance}
  - {id: release, type: agent, role: devops}
  - {id: release_gate, type: gate, gate: release}
  - {id: end, type: end}
edges:
  - {from: start, to: requirement_review}
  - {from: requirement_review, to: requirement_gate}
  - {from: requirement_gate, to: design, on_accept: design, on_reject: requirement_review, on_edit: design}
  - {from: design, to: design_review}
  - {from: design_review, to: design_gate}
  - {from: design_gate, to: develop_parallel, on_accept: develop_parallel, on_reject: design, on_edit: design}
  - {from: develop_parallel, to: code_review}
  - {from: code_review, to: test}
  - {from: test, to: iteration_gate}
  - {from: iteration_gate, to: release, on_accept: release, on_reject: test, on_edit: code_review}
  - {from: release, to: release_gate}
  - {from: release_gate, to: end, on_accept: end, on_reject: release}
```

## 16. 会议与审批门操作

### 16.1 交互式审批

流程运行到门节点会打印待审批请求（类别 / 风险 / bypass-immune / 说明）并挂起：

```
请选择审批结论 [accept|reject|response <内容>|edit <内容>]：
```

| 输入 | 含义 |
|---|---|
| `accept` | 通过，沿 `on_accept` 继续 |
| `reject` | 拒绝，沿 `on_reject` 回流返工 |
| `response <内容>` | 附人工意见通过（进入 `on_accept` 目标） |
| `edit <内容>` | 带修改意见进入 `on_edit` 目标 |

### 16.2 无人值守（`--yes`）

- 自动对全部门给出 `accept`；
- `bypass-immune`（不可绕过）的高风险门自动转为 `reject`，防止无人值守时越权放行。

### 16.3 挂起与恢复

挂起时运行写入检查点（`MemorySaver`）；恢复后从断点继续，事件流与审批记录不丢失。

## 17. 技能系统

### 17.1 目录约定

```text
skills_root/
  requirement-analysis/
    SKILL.md                 # 必填：frontmatter 含 name/version/description
    assets/                  # 附资产（模板、示例）
    references/              # 参考资料
    scripts/                 # 可执行辅助脚本
```

### 17.2 查看技能

```bash
uv run agent-cluster skills list --root examples/skills
```

内置技能：`requirement-analysis@1.0.0`、`backend-api-design@2.1.0`、
`frontend-design@1.0.0`、`qa-testing@1.0.0`。

### 17.3 新增技能

1. 在技能根目录下新建 `技能名/SKILL.md`，frontmatter 声明 `name` / `version` /
   `description`；
2. 正文写执行步骤（可含检查清单与产出物模板）；
3. 资源放 `assets` / `references` / `scripts` 子目录；
4. 在岗位的 `skills` 字段（`roles.py`）按 `name@version` 挂载。

## 18. 岗位系统与扩展

### 18.1 查看岗位

```bash
uv run agent-cluster roles list
```

### 18.2 Role 字段

`Role` 模型字段（`agent_cluster/models.py`）：`id` / `name` / `kind` / `goal` /
`backstory` / `skills`（name@version 列表）/ `tools` / `model`（岗位偏好模型，None 走
运行时默认）/ `approval_scope`（可审批的门类别）。

### 18.3 新增/调整岗位

编辑 `agent_cluster/roles.py` 的 `build_role_catalog()`：

- 岗位 id 唯一；`kind` 取八类之一（pm/pmo/arch/frontend/backend/algorithm/qa/devops）；
- `approval_scope` 控制该岗位可审批的门；
- 会议默认参与者由 `RoleRegistry` 按会议类型返回。

## 19. 进化提案

```bash
# 六步闭环演示
uv run agent-cluster proposals demo

# 提交真实提案（自动评审）
uv run agent-cluster proposals submit \
  --title "改进测试技能包" \
  --rollback-plan "回滚到上一版本技能定义" \
  --author-role pm \
  --category skill
```

安全约束：

- `--rollback-plan` 必填（空白即拒绝）；
- 进化生效（`evolution_apply`）属于治理层审批范围；
- 禁止 Agent 自我扩权（提案不可扩大自己的审批权限）。

## 20. 绩效度量与信号

```bash
uv run agent-cluster metrics demo
```

指标：评审通过率 / 返工率 / 行动项关闭率 / 循环迭代次数 / 门等待时长等；
`MetricRules` 按阈值触发进化信号（severity 分级），供进化闭环消费。

## 21. 退出码与错误处理

| 退出码 | 含义 |
|---|---|
| 0 | 成功 |
| 1 | 失败（流程校验失败 / 运行异常 / 模型配置无效 / 提案参数缺失 / MCP 连接失败 / 工具模式存在未完成任务 / doctor 存在阻塞项 / chat 运行失败 / `--sandbox docker` 无 Docker 等） |
| 2 | build 用户中止（检查点已保存，可 `--resume`）/ chat 中断（Ctrl+C） |
| 3 | build 升级结束（预算超限 / 返工上限，保存现状） |

工具模式（`--workspace`）下，运行结束若任务板仍有验收未通过的岗位任务
（review / blocked，如 QA 测试未通过、开发任务未完成），视为验收未通过，退出码 1；
会议生成的 todo 行动项（积压清单）不触发退出码 1。

常见错误信息示例：

- `模型配置无效（...）：DeepSeekClient 需要环境变量 DEEPSEEK_API_KEY` —— 缺 key；
- `DeepSeek API 回复 content 为空...` —— 推理模型吃满 token 预算且扩容重试后仍为空；
- `流程挂起但未从检查点找到待审批请求` —— 检查点状态异常（正常运行不会出现）；
- 流程 YAML 校验失败 —— 检查节点 id 引用、`max_iterations ≥ 节点总数`、边引用有效性。

## 22. 常见问题（FAQ）

**Q1：不想用任何 API key，能否运行？**
可以。缺省 `deterministic` 后端零依赖，`uv run agent-cluster run --flow ... --yes` 即可
跑通全流程。

**Q2：`--model deepseek-v4-flash` 报缺 key？**
设置环境变量 `DEEPSEEK_API_KEY`（PowerShell：`$env:DEEPSEEK_API_KEY="sk-..."`），或用
`--model deterministic` / `--model codex`（自动读取 Codex 配置）。

**Q3：为什么 `--model codex` 报「无法解析 Codex 配置」？**
`~/.codex/config.toml` 缺失，或未配置 `model_provider` 与 `[model_providers.<id>]`
节（需含 `base_url` 与 `env_key`）。可改用显式 `--model deepseek-v4-flash`。

**Q4：DeepSeek 回复 content 为空？**
`deepseek-*` 为推理模型时 `max_tokens` 同时计入思维链与回答；运行时已自动扩容重试一次
（至多 8192）。若仍为空，请检查模型名是否可用，或改用非推理模型。

**Q5：会泄露模型思维链吗？**
不会。只返回 `message.content`；`reasoning_content`（思维链）不作为任务输出，content
为空时直接报错。

**Q6：Windows 上克隆/操作报长路径错误？**
在对应仓库内启用 `git config core.longpaths true`（勿改全局配置）。

**Q7：运行输出出现 langgraph「未注册类型」告警？**
正常运行已通过 `JsonPlusSerializer` 白名单抑制；若出现请升级 `langgraph-checkpoint`。

**Q8：如何换回确定性后端？**
不加 `--model` 且不设置 `DEEPSEEK_MODEL` 即回退 `deterministic`。

**Q9：API key 会被写入仓库吗？**
不会。key 只从环境变量或 Codex 配置读取；提交前可用
`git grep -nE "sk-[A-Za-z0-9]{16,}"` 自检。

**Q10：工具模式会不会让 Agent 乱删文件/执行任意命令？**
不会。危险工具（`run_shell` / `run_python` / `delete_file` / `git_push`）一律走审批门：
交互模式挂起等人确认，`--yes` 无人值守自动拒绝；文件写入路径必须落在工作区内，
`../` / 绝对路径 / 符号链接外逃都会被拦截。

**Q11：没有 API key 如何体验工具模式全链路？**
用 `--tool-script` 指定确定性工具脚本 JSON（`examples/tool-scripts/build-new-project.json`），
配合 `--workspace` 即可无 key 跑通「写文件 → 跑测试 → git 提交」真实执行。

**Q12：没有 Docker 能不能用 v0.4？**
可以。本机执行是缺省路径；`doctor` 会提示 Docker 缺失（`--skip-docker-check` 跳过），
`--sandbox docker` 在无 Docker 时给出安装指引并退出非零码，改用缺省 `--sandbox none`
即可本机执行。

**Q13：chat 和 build / run 的区别？**
`chat` 是连续多轮开发 REPL（边聊边改，跨轮上下文 + token 计量）；`build` 是输入一个
需求一次产出完整交付包；`run` 是编译并运行 YAML 流程（最底层执行模型）。

**Q14：插件 hooks 如何配置？**
在插件目录下提供 `.codex-plugin/plugin.json` 或 `.claude-plugin/plugin.json`
（含 hooks 段），`chat` / `build` 传 `--plugin-dir` 即自动发现；11 个事件
（PreToolUse / PermissionRequest / PostToolUse / PreCompact / PostCompact /
SessionStart / SessionEnd / UserPromptSubmit / SubagentStart / SubagentStop / Stop）
支持 PascalCase 与 snake_case 两种写法，`command` 类型 hook 由平台执行（stdin 传
JSON payload，另注入 `AGENT_CLUSTER_*` 环境变量）。


## 23. 目录结构

```text
agent-cluster-runtime/
├── src/agent_cluster/
│   ├── models.py        # pydantic v2 数据模型与枚举
│   ├── workflow.py      # YAML 流程 DSL → StateGraph 编译与执行
│   ├── runtime.py       # AgentRuntime / ChatModelClient（chat/responses/anthropic 三协议）+ 工具模式 ReAct handler
│   ├── tools.py         # v0.2 工具模型/注册表/会话执行器（23 内置工具，含 apply_patch/http_fetch + 权限分层 + 路径越界拦截）
│   ├── mcp_client.py    # v0.2 轻量 MCP stdio 客户端（JSON-RPC 2.0）
│   ├── providers.py     # Codex config.toml 解析与 DeepSeek 默认值
│   ├── session.py       # v0.3 会话构建（build）与 TokenLedger 计量
│   ├── tokens.py        # v0.3 token 估算与计量
│   ├── roles.py         # 12 岗位目录与注册表
│   ├── meetings.py      # 7 类会议模板
│   ├── gates.py         # 审批门（interrupt HITL）
│   ├── skills.py        # SKILL.md 技能加载与目录
│   ├── ledger.py        # 任务账本与任务板
│   ├── evolution.py     # 六步进化闭环
│   ├── metrics.py       # 度量采集与阈值规则
│   ├── doctor.py        # v0.4 环境预检（Python/git/Docker 硬依赖）
│   ├── plugins.py       # v0.4 插件层（双清单 + 11 事件 hooks + marketplace）
│   ├── repl.py          # v0.4 chat REPL（连续多轮开发）
│   ├── sandbox.py       # v0.4 Docker 沙箱执行器
│   ├── worktree.py      # v0.4 git worktree 隔离
│   ├── subagent.py      # v0.4 有界子代理
│   ├── server.py           # v0.5 serve 后端（REST+SSE+全局索引）
│   ├── memory.py           # v0.5 SQLite 四级记忆库
│   ├── evolution_integration.py # v0.5 进化集成（记忆→提案/复盘/SOP）
│   ├── budget.py/pricing.py      # v0.5 自适应预算 + 价格表
│   ├── trace.py/judge.py/eval.py # v0.5 可观测 + LLM 评审 + 回归集
│   ├── changes.py          # v0.5 变更版本化/回滚
│   ├── subagent.py      # v0.4 有界子代理
│   └── cli.py           # 命令行入口（run/build/chat/doctor/tools/mcp/plugins/skills/roles/proposals/metrics）
├── examples/
│   ├── flows/fullstack-sprint.yaml
│   ├── flows/build-new-project.yaml   # v0.2 新项目全流程（含前后端并行/返工边）
│   ├── flows/build-product.yaml       # v0.3 会话式全生命周期流程
│   ├── tool-scripts/build-new-project.json  # 确定性工具脚本（无 key 演示）
│   └── skills/          # 4 个示例技能包
├── tests/               # 539+ 项自动化测试
├── docs/PRODUCT.md      # 产品介绍
├── docs/MANUAL.md       # 本手册
└── README.md
```

更多设计背景见 [`docs/../agent-clusters/智能体集群设计方案.md`](../agent-clusters/智能体集群设计方案.md)
与 [`docs/superpowers/plans/implementation-plan.md`](superpowers/plans/implementation-plan.md)。


---

## 24. 桌面工作台：serve + React 前端 + Electron（v0.5）

### 24.1 启动后端

```powershell
agent-cluster serve --port 8765                # 默认仅本机
agent-cluster serve --port 8765 --auth-token xxxx   # 可选认证（X-Auth-Token）
agent-cluster serve --mcp fs='npx -y @modelcontextprotocol/server-filesystem C:\tmp'  # 可选 MCP
```

- REST：`GET /api/v1/status`、`GET|POST /api/v1/projects`、`GET|POST /api/v1/projects/{pid}/sessions`、
  `GET /api/v1/sessions/{sid}`、`POST /api/v1/sessions/{sid}/approve|reject|edit|response`、
  `POST /api/v1/sessions/{sid}/interrupt`、`POST /api/v1/sessions/{sid}/rollback`、
  `GET /api/v1/projects/{pid}/workspace/tree|file`、`GET /api/v1/projects/{pid}/memory`、
  `GET /api/v1/metrics`、`GET /api/v1/plugins|skills|mcp`、
  `GET /api/v1/evolution/proposals`、`POST /api/v1/evolution/generate|retro`、
  `POST /api/v1/evolution/proposals/{id}/apply|rollback`、`POST /api/v1/sessions/{sid}/audit/export`。
- SSE：`GET /api/v1/sessions/{sid}/events?since=N`（node.entered / gate.waiting /
  approval.pending / tool.call / change.applied / task.done / phase.completed /
  budget.warning / metrics.updated，事件可重放）。

### 24.2 React 工作台（frontend/）

```powershell
cd frontend
npm install
npm run dev        # Vite dev server，默认 http://127.0.0.1:5173
```

页面：仪表盘（token/成本/健康）、项目看板、会话详情（审批弹窗、实时打断、变更历史回滚、
任务板、token/阶段、SSE 时间线）、产物浏览（文件树+预览）、记忆库、进化提案、集成
（插件/技能/MCP）、审计导出、设置（serverUrl/认证/模型/预算）。

### 24.3 Electron 桌面壳（desktop/）

```powershell
cd desktop
npm install
npm start          # 启动 Electron：自动拉起 serve + 加载前端
npm run build:win  # electron-builder NSIS 安装包
```

- 托盘/系统通知（会话等待审批时提醒）、全局快捷键 `Ctrl+Alt+K` 唤起/隐藏、开机自启
  （`AGENT_CLUSTER_AUTOSTART=1`）、退出时清理后端子进程。
- 后端二进制优先 `resources/agent-cluster-backend.exe`，缺失回退 `uv run agent-cluster serve`。

## 25. 一键演示 demo（v0.5）

```powershell
agent-cluster demo                                  # 确定性演示（无需 API key）
agent-cluster demo --workspace D:\demo-ws
agent-cluster demo --port 8765                      # 完成后把工作区注册进已运行的 serve
```

- 在缺省 `./.agent-cluster-demo/` 跑示例需求全生命周期，产出 PRD/架构/代码/测试/部署/
  手册/`DELIVERY.md`（含 token 计量表）并 git 提交；结束后打印面板接入指引。

## 26. 记忆库与进化集成（v0.5）`r`n27. 项目组合层与看板：serve v0.6 扩展

```powershell
# 记忆：工作区 .agent-cluster/memory.db（SQLite 四级晋升，内容存 Markdown 文件）
agent-cluster evolution capture --workspace DIR --notes "建议：需求评审前必须完成验收标准初稿。"
agent-cluster evolution generate --workspace DIR   # 记忆失败模式 → 进化提案
agent-cluster evolution list --workspace DIR
agent-cluster evolution apply --workspace DIR --proposal <id> [--human-required]
agent-cluster evolution rollback --workspace DIR --proposal <id> --reason "..."
agent-cluster evolution retro --workspace DIR --goal "..."   # 自动复盘报告 docs/retro-<ts>.md
```

- 提案持久化到 `<root>/.agent-cluster/evolution/proposals.json`；process/organization 类
  提案生效后自动追加 `.agent-cluster/SOP.md` 变更记录；`--human-required` 在非 ask 模式
  下组织流程变更自动驳回（bypass-immune）。

## 27. 项目组合层与看板：serve v0.6 扩展

v0.6 在 v0.5 桌面工作台之上新增**项目组合层**与无人值守验收能力，全部经 `serve` REST/WS 暴露。

### 27.1 项目容器与预算池

```powershell
# 创建项目（workspace 目录须真实存在；含 v0.5 session.json 时自动迁移为首个会话）
curl -X POST http://127.0.0.1:8765/api/v1/projects -H "X-Auth-Token: ci" `
  -H "Content-Type: application/json" -d '{\"name\":\"待办应用\",\"workspace\":\"D:/ws/todo\"}'
# 预算池：硬上限 + 预警 + 解锁（自服务 200 / 审批模式 202 → approve|deny）
curl -X PATCH http://127.0.0.1:8765/api/v1/projects/<pid> -H "X-Auth-Token: ci" `
  -H "Content-Type: application/json" -d '{\"budget_pool\":{\"hard_limit_tokens\":500000}}'
curl -X POST http://127.0.0.1:8765/api/v1/projects/<pid>/budget/unlock `
  -H "X-Auth-Token: ci" -H "Content-Type: application/json" -d '{\"additional_tokens\":100000,\"reason\":\"扩容\"}'
```

- 预算语义：聚合用量 > 硬上限 → 新会话 409 `budget_pool_exhausted`；用量 ≥
  上限 × `warn_ratio` 触发 `budget.warning` 事件（滞回防抖），`budget.exhausted`
  事件在超限瞬间落审计。

### 27.2 fork 血缘派生

```powershell
# 终态（completed）会话派生；返回 fork_depth 血缘，子会话 dormant 登记，账本不双计
curl -X POST http://127.0.0.1:8765/api/v1/sessions/<sid>/fork -H "X-Auth-Token: ci" `
  -H "Content-Type: application/json" -d '{\"goal\":\"衍生需求\",\"worktree\":false}'
```

### 27.3 实时 stdin 注入与自动评审

```powershell
# 挂起中注入（202 accepted）→ 落 transcript/变更历史/PRD 追加/stdin.applied 事件
curl -X POST http://127.0.0.1:8765/api/v1/sessions/<sid>/stdin -H "X-Auth-Token: ci" `
  -H "Content-Type: application/json" -d '{\"text\":\"改用邮箱验证码登录\"}'
# 审批继续（挂起门）
curl -X POST http://127.0.0.1:8765/api/v1/sessions/<sid>/approve -H "X-Auth-Token: ci"
```

- 门策略：项目 `gate_policy.auto_review` 开（默认）时自动白名单门（design_review/
  code_review/iteration_acceptance）由 reviewer 自动放行；deterministic 模式
  （`deterministic:true`）直接 `deterministic-accept`，无需真实 LLM 即可无人值守
  跑通全流程。

### 27.4 WebSocket 实时面板

```text
ws://127.0.0.1:8765/api/v1/ws?token=ci&session_id=<sid>
{type:"subscribe", id:"s1", payload:{session_ids:["<sid>"]}}  →  snapshot
{type:"ping", id:"p1"}                                        →  pong
{type:"cancel", id:"c1", payload:{session_id:"<sid>"}}        →  ack
```

### 27.5 看板与任务面板

- `GET /api/v1/projects/{pid}/dashboard`：cost/progress/health 三轴（状态枚举
  ok|warn|critical）。
- `GET /api/v1/projects/{pid}/tasks?status=completed&assignee=alice&q=关键词`：
  注册表投影过滤；`PATCH /api/v1/projects/{pid}/tasks/{sid} {"assignee":"alice"}` 指派。

### 27.6 工程化与发布

- 前端真实后端 e2e：`cd frontend && npm run e2e:real`（Playwright 自管 `uv run
  agent-cluster serve --port 8765 --auth-token ci`，deterministic 全链路）。
- Docker 自动安装：`scripts/install-docker.ps1` / `install-docker.sh`；
  `agent-cluster doctor --fix-docker` 一键联动。
- 桌面打包矩阵：`cd desktop && npm run build:win`（NSIS x64+arm64）、`build:mac`、
  `build:linux`；electron-updater 双通道（stable/latest）+ lastKnownGood 回退 +
  minimumVersion 钉扎。
- CI：`.github/workflows/ci.yml` 五段流水线（backend-test/frontend-test/e2e-real/
  package/release）；`.github/workflow-templates/agent-delivery.yml` 交付模板
  （build → 人工批准 → ci → 报告）。
