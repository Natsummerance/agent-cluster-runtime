# 用户手册：agent-cluster-runtime

> 多 Agent 组织型全栈开发集群运行时 —— 安装、配置、运行与扩展指南。
> 适用版本：0.2.0 ｜ 环境：Windows / macOS / Linux（Python 3.11+）

---

## 目录

1. 能力速览
2. 环境要求与安装
3. 快速开始（确定性后端，零配置）
4. 工具模式（真实工作区执行）
5. CLI 命令参考
6. 接入真实 LLM（DeepSeek / Codex 当前模型 / OpenAI）
7. 流程文件（YAML）编写指南
8. 会议与审批门操作
9. 技能系统
10. 岗位系统与扩展
11. 进化提案
12. 绩效度量与信号
13. 退出码与错误处理
14. 常见问题（FAQ）
15. 目录结构

---

## 1. 能力速览

| 能力 | 命令 | 说明 |
|---|---|---|
| 运行流程 | `agent-cluster run --flow <yaml>` | 编译并运行 YAML 流程，含审批交互 |
| 工具模式 | `agent-cluster run --flow <yaml> --workspace <dir>` | 岗位 Agent 在真实工作区读文件/改代码/跑测试/走 git |
| 工具清单 | `agent-cluster tools list` | 列出内置工具与权限分层（read/workspace_write/dangerous） |
| MCP 清单 | `agent-cluster mcp list --server NAME=CMD` | 连接 MCP stdio 服务器并列出其工具 |
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

内置 18 个工具，按风险分三层；`tools list` 可查看实时清单：

| 权限 | 行为 | 工具 |
|---|---|---|
| `read` | 自动执行，无需审批 | `list_dir` / `read_file` / `grep` / `glob` / `git_status` / `git_diff` |
| `workspace_write` | 自动执行 + 审计留痕 | `write_file` / `edit_file`（多 hunk）/ `mkdir` / `git_add` / `git_commit` / `git_revert` / `git_init` / `run_tests` |
| `dangerous` | 走审批门（HITL interrupt） | `run_shell`（非白名单）/ `run_python` / `delete_file` / `git_push` |

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
                           [--mcp NAME=COMMAND] [--max-rounds N]
```

输入一个需求，向导全程交互产出完整交付包（PRD/代码/测试/部署/手册/`DELIVERY.md`）。

| 参数 | 说明 |
|---|---|
| `--goal GOAL` | 产品需求目标（`--resume` 时可省略） |
| `--workspace DIR` | 工作区；缺省 `build-out/<目标>` |
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
                         [--workspace WORKSPACE] [--mcp NAME=COMMAND]
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
| `--mcp NAME=COMMAND` | MCP stdio 服务器（可重复）；工具注册为 `mcp_<name>_<tool>`，危险权限 |
| `--max-rounds N` | 工具模式 ReAct 最大轮数（缺省 6） |
| `--tool-script JSON` | 确定性工具脚本（`[{name, args}, ...]`），无 API key 跑通工具全链路 |
| `--skills-root DIR` | 技能根目录（把岗位技能上下文注入工具模式 system prompt） |

### 5.2 `skills list` —— 技能列表

```
agent-cluster skills list --root <技能根目录>
```

输出技能 `name@version` 与描述；要求目录下技能按 `name/SKILL.md` 组织。

### 5.3 `roles list` —— 岗位列表

```
agent-cluster roles list
```

输出 12 个岗位的 id / 名称 / 类别 / 审批范围。

### 5.4 `proposals` —— 进化提案

```
agent-cluster proposals demo
agent-cluster proposals submit --title <标题> --rollback-plan <回滚方案> \
    [--author-role pm] [--category skill]
```

- `--rollback-plan` 必填且不可为空白（防止不可回滚的进化）。
- `--category` 取值：`skill` / `knowledge` / `process` / `organization`。

### 5.5 `tools list` —— 工具清单

```
agent-cluster tools list
```

输出内置工具的名称、权限分层（read / workspace_write / dangerous）与描述。

### 5.6 `mcp list` —— MCP 服务器工具清单

```
agent-cluster mcp list --server "NAME=COMMAND"
```

连接 MCP stdio 服务器并列出其暴露的工具；连接失败退出码 1。

### 5.7 `metrics demo` —— 度量演示

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

## 7. 流程文件（YAML）编写指南

### 7.1 顶层结构

```yaml
name: fullstack-sprint          # 流程名（也是缺省项目名）
description: 流程说明（可含完整 MVP 链描述）
max_iterations: 40              # 编译期校验必须 ≥ 节点总数（防死循环）
thread_id: "proj:demo:iter:1"   # 缺省线程 id
nodes: [...]                    # 节点列表
edges: [...]                    # 边列表
```

### 7.2 节点类型

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

### 7.3 边与条件路由

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

### 7.4 完整示例（内置 `examples/flows/fullstack-sprint.yaml`）

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

## 8. 会议与审批门操作

### 8.1 交互式审批

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

### 8.2 无人值守（`--yes`）

- 自动对全部门给出 `accept`；
- `bypass-immune`（不可绕过）的高风险门自动转为 `reject`，防止无人值守时越权放行。

### 8.3 挂起与恢复

挂起时运行写入检查点（`MemorySaver`）；恢复后从断点继续，事件流与审批记录不丢失。

## 9. 技能系统

### 9.1 目录约定

```text
skills_root/
  requirement-analysis/
    SKILL.md                 # 必填：frontmatter 含 name/version/description
    assets/                  # 附资产（模板、示例）
    references/              # 参考资料
    scripts/                 # 可执行辅助脚本
```

### 9.2 查看技能

```bash
uv run agent-cluster skills list --root examples/skills
```

内置技能：`requirement-analysis@1.0.0`、`backend-api-design@2.1.0`、
`frontend-design@1.0.0`、`qa-testing@1.0.0`。

### 9.3 新增技能

1. 在技能根目录下新建 `技能名/SKILL.md`，frontmatter 声明 `name` / `version` /
   `description`；
2. 正文写执行步骤（可含检查清单与产出物模板）；
3. 资源放 `assets` / `references` / `scripts` 子目录；
4. 在岗位的 `skills` 字段（`roles.py`）按 `name@version` 挂载。

## 10. 岗位系统与扩展

### 10.1 查看岗位

```bash
uv run agent-cluster roles list
```

### 10.2 Role 字段

`Role` 模型字段（`agent_cluster/models.py`）：`id` / `name` / `kind` / `goal` /
`backstory` / `skills`（name@version 列表）/ `tools` / `model`（岗位偏好模型，None 走
运行时默认）/ `approval_scope`（可审批的门类别）。

### 10.3 新增/调整岗位

编辑 `agent_cluster/roles.py` 的 `build_role_catalog()`：

- 岗位 id 唯一；`kind` 取八类之一（pm/pmo/arch/frontend/backend/algorithm/qa/devops）；
- `approval_scope` 控制该岗位可审批的门；
- 会议默认参与者由 `RoleRegistry` 按会议类型返回。

## 11. 进化提案

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

## 12. 绩效度量与信号

```bash
uv run agent-cluster metrics demo
```

指标：评审通过率 / 返工率 / 行动项关闭率 / 循环迭代次数 / 门等待时长等；
`MetricRules` 按阈值触发进化信号（severity 分级），供进化闭环消费。

## 13. 退出码与错误处理

| 退出码 | 含义 |
|---|---|
| 0 | 成功 |
| 1 | 失败（流程校验失败 / 运行异常 / 模型配置无效 / 提案参数缺失 / MCP 连接失败 / 工具模式存在未完成任务等） |

工具模式（`--workspace`）下，运行结束若任务板仍有验收未通过的岗位任务
（review / blocked，如 QA 测试未通过、开发任务未完成），视为验收未通过，退出码 1；
会议生成的 todo 行动项（积压清单）不触发退出码 1。

常见错误信息示例：

- `模型配置无效（...）：DeepSeekClient 需要环境变量 DEEPSEEK_API_KEY` —— 缺 key；
- `DeepSeek API 回复 content 为空...` —— 推理模型吃满 token 预算且扩容重试后仍为空；
- `流程挂起但未从检查点找到待审批请求` —— 检查点状态异常（正常运行不会出现）；
- 流程 YAML 校验失败 —— 检查节点 id 引用、`max_iterations ≥ 节点总数`、边引用有效性。

## 14. 常见问题（FAQ）

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

## 15. 目录结构

```text
agent-cluster-runtime/
├── src/agent_cluster/
│   ├── models.py        # pydantic v2 数据模型与枚举
│   ├── workflow.py      # YAML 流程 DSL → StateGraph 编译与执行
│   ├── runtime.py       # AgentRuntime / ChatModelClient（deterministic/deepseek/openai）+ 工具模式 ReAct handler
│   ├── tools.py         # v0.2 工具模型/注册表/会话执行器（18 内置工具 + 权限分层 + 路径越界拦截）
│   ├── mcp_client.py    # v0.2 轻量 MCP stdio 客户端（JSON-RPC 2.0）
│   ├── providers.py     # Codex config.toml 解析与 DeepSeek 默认值
│   ├── roles.py         # 12 岗位目录与注册表
│   ├── meetings.py      # 7 类会议模板
│   ├── gates.py         # 审批门（interrupt HITL）
│   ├── skills.py        # SKILL.md 技能加载与目录
│   ├── ledger.py        # 任务账本与任务板
│   ├── evolution.py     # 六步进化闭环
│   ├── metrics.py       # 度量采集与阈值规则
│   └── cli.py           # 命令行入口（run/workspace/mcp/tools/skills/roles/proposals/metrics）
├── examples/
│   ├── flows/fullstack-sprint.yaml
│   ├── flows/build-new-project.yaml   # v0.2 新项目全流程（含前后端并行/返工边）
│   ├── tool-scripts/build-new-project.json  # 确定性工具脚本（无 key 演示）
│   └── skills/          # 4 个示例技能包
├── tests/               # 288 项自动化测试
├── docs/PRODUCT.md      # 产品介绍
├── docs/MANUAL.md       # 本手册
└── README.md
```

更多设计背景见 [`docs/../agent-clusters/智能体集群设计方案.md`](../agent-clusters/智能体集群设计方案.md)
与 [`docs/superpowers/plans/implementation-plan.md`](superpowers/plans/implementation-plan.md)。
