# 用户手册：agent-cluster-runtime

> 多 Agent 组织型全栈开发集群运行时 —— 安装、配置、运行与扩展指南。
> 适用版本：0.1.0 ｜ 环境：Windows / macOS / Linux（Python 3.11+）

---

## 目录

1. 能力速览
2. 环境要求与安装
3. 快速开始（确定性后端，零配置）
4. CLI 命令参考
5. 接入真实 LLM（DeepSeek / Codex 当前模型 / OpenAI）
6. 流程文件（YAML）编写指南
7. 会议与审批门操作
8. 技能系统
9. 岗位系统与扩展
10. 进化提案
11. 绩效度量与信号
12. 退出码与错误处理
13. 常见问题（FAQ）
14. 目录结构

---

## 1. 能力速览

| 能力 | 命令 | 说明 |
|---|---|---|
| 运行流程 | `agent-cluster run --flow <yaml>` | 编译并运行 YAML 流程，含审批交互 |
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

## 4. CLI 命令参考

### 4.1 `run` —— 编译并运行流程

```
usage: agent-cluster run [-h] --flow FLOW [--project PROJECT] [--yes]
                         [--thread THREAD] [--model MODEL]
```

| 参数 | 说明 |
|---|---|
| `--flow FLOW` | 必填。流程 YAML 文件路径 |
| `--project PROJECT` | 项目目录；缺省用流程名生成项目名 |
| `--yes` | 无人值守：自动接受全部审批（bypass-immune 门自动拒绝） |
| `--thread THREAD` | 线程 id；缺省用流程 YAML 的 `thread_id` |
| `--model MODEL` | 岗位模型后端：`deterministic`（缺省）/ `deepseek-*` / `codex`；缺省可经环境变量 `DEEPSEEK_MODEL` |

### 4.2 `skills list` —— 技能列表

```
agent-cluster skills list --root <技能根目录>
```

输出技能 `name@version` 与描述；要求目录下技能按 `name/SKILL.md` 组织。

### 4.3 `roles list` —— 岗位列表

```
agent-cluster roles list
```

输出 12 个岗位的 id / 名称 / 类别 / 审批范围。

### 4.4 `proposals` —— 进化提案

```
agent-cluster proposals demo
agent-cluster proposals submit --title <标题> --rollback-plan <回滚方案> \
    [--author-role pm] [--category skill]
```

- `--rollback-plan` 必填且不可为空白（防止不可回滚的进化）。
- `--category` 取值：`skill` / `knowledge` / `process` / `organization`。

### 4.5 `metrics demo` —— 度量演示

```
agent-cluster metrics demo
```

打印采集的度量点、快照指标数与触发的进化信号。

## 5. 接入真实 LLM

API key 只从环境变量或 Codex 配置读取，**绝不写入仓库、日志或检查点**。

### 5.1 DeepSeek（推荐，与当前 Codex 对话同源）

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

### 5.2 复用当前 Codex 对话模型

```bash
uv run agent-cluster run --flow examples/flows/fullstack-sprint.yaml --project examples \
  --model codex --yes
```

读取 `~/.codex/config.toml`（或 `CODEX_HOME`）的顶层 `model` / `model_provider` 与
`[model_providers.<provider>]` 节；供应商为 DeepSeek 时接入 `DeepSeekClient`，OpenAI 系
接入 `OpenAIClient`。

### 5.3 环境变量兜底

```bash
# PowerShell
$env:DEEPSEEK_MODEL = "deepseek-v4-flash"
# bash
export DEEPSEEK_MODEL="deepseek-v4-flash"
uv run agent-cluster run --flow examples/flows/fullstack-sprint.yaml --yes
```

### 5.4 OpenAI

```bash
# 需要 openai 包：uv add openai；环境变量 OPENAI_API_KEY
uv run agent-cluster run --flow examples/flows/fullstack-sprint.yaml \
  --model gpt-4o-mini --yes
```

### 5.5 模型选择优先级

岗位 `Role.model` 偏好 > 运行时默认（`--model` / `DEEPSEEK_MODEL`）> `deterministic`。

## 6. 流程文件（YAML）编写指南

### 6.1 顶层结构

```yaml
name: fullstack-sprint          # 流程名（也是缺省项目名）
description: 流程说明（可含完整 MVP 链描述）
max_iterations: 40              # 编译期校验必须 ≥ 节点总数（防死循环）
thread_id: "proj:demo:iter:1"   # 缺省线程 id
nodes: [...]                    # 节点列表
edges: [...]                    # 边列表
```

### 6.2 节点类型

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

### 6.3 边与条件路由

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

### 6.4 完整示例（内置 `examples/flows/fullstack-sprint.yaml`）

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

## 7. 会议与审批门操作

### 7.1 交互式审批

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

### 7.2 无人值守（`--yes`）

- 自动对全部门给出 `accept`；
- `bypass-immune`（不可绕过）的高风险门自动转为 `reject`，防止无人值守时越权放行。

### 7.3 挂起与恢复

挂起时运行写入检查点（`MemorySaver`）；恢复后从断点继续，事件流与审批记录不丢失。

## 8. 技能系统

### 8.1 目录约定

```text
skills_root/
  requirement-analysis/
    SKILL.md                 # 必填：frontmatter 含 name/version/description
    assets/                  # 附资产（模板、示例）
    references/              # 参考资料
    scripts/                 # 可执行辅助脚本
```

### 8.2 查看技能

```bash
uv run agent-cluster skills list --root examples/skills
```

内置技能：`requirement-analysis@1.0.0`、`backend-api-design@2.1.0`、
`frontend-design@1.0.0`、`qa-testing@1.0.0`。

### 8.3 新增技能

1. 在技能根目录下新建 `技能名/SKILL.md`，frontmatter 声明 `name` / `version` /
   `description`；
2. 正文写执行步骤（可含检查清单与产出物模板）；
3. 资源放 `assets` / `references` / `scripts` 子目录；
4. 在岗位的 `skills` 字段（`roles.py`）按 `name@version` 挂载。

## 9. 岗位系统与扩展

### 9.1 查看岗位

```bash
uv run agent-cluster roles list
```

### 9.2 Role 字段

`Role` 模型字段（`agent_cluster/models.py`）：`id` / `name` / `kind` / `goal` /
`backstory` / `skills`（name@version 列表）/ `tools` / `model`（岗位偏好模型，None 走
运行时默认）/ `approval_scope`（可审批的门类别）。

### 9.3 新增/调整岗位

编辑 `agent_cluster/roles.py` 的 `build_role_catalog()`：

- 岗位 id 唯一；`kind` 取八类之一（pm/pmo/arch/frontend/backend/algorithm/qa/devops）；
- `approval_scope` 控制该岗位可审批的门；
- 会议默认参与者由 `RoleRegistry` 按会议类型返回。

## 10. 进化提案

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

## 11. 绩效度量与信号

```bash
uv run agent-cluster metrics demo
```

指标：评审通过率 / 返工率 / 行动项关闭率 / 循环迭代次数 / 门等待时长等；
`MetricRules` 按阈值触发进化信号（severity 分级），供进化闭环消费。

## 12. 退出码与错误处理

| 退出码 | 含义 |
|---|---|
| 0 | 成功 |
| 1 | 失败（流程校验失败 / 运行异常 / 模型配置无效 / 提案参数缺失等） |

常见错误信息示例：

- `模型配置无效（...）：DeepSeekClient 需要环境变量 DEEPSEEK_API_KEY` —— 缺 key；
- `DeepSeek API 回复 content 为空...` —— 推理模型吃满 token 预算且扩容重试后仍为空；
- `流程挂起但未从检查点找到待审批请求` —— 检查点状态异常（正常运行不会出现）；
- 流程 YAML 校验失败 —— 检查节点 id 引用、`max_iterations ≥ 节点总数`、边引用有效性。

## 13. 常见问题（FAQ）

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

## 14. 目录结构

```text
agent-cluster-runtime/
├── src/agent_cluster/
│   ├── models.py        # pydantic v2 数据模型与枚举
│   ├── workflow.py      # YAML 流程 DSL → StateGraph 编译与执行
│   ├── runtime.py       # AgentRuntime / ChatModelClient（deterministic/deepseek/openai）
│   ├── providers.py     # Codex config.toml 解析与 DeepSeek 默认值
│   ├── roles.py         # 12 岗位目录与注册表
│   ├── meetings.py      # 7 类会议模板
│   ├── gates.py         # 审批门（interrupt HITL）
│   ├── skills.py        # SKILL.md 技能加载与目录
│   ├── ledger.py        # 任务账本与任务板
│   ├── evolution.py     # 六步进化闭环
│   ├── metrics.py       # 度量采集与阈值规则
│   └── cli.py           # 命令行入口
├── examples/
│   ├── flows/fullstack-sprint.yaml
│   └── skills/          # 4 个示例技能包
├── tests/               # 248 项自动化测试
├── docs/PRODUCT.md      # 产品介绍
├── docs/MANUAL.md       # 本手册
└── README.md
```

更多设计背景见 [`docs/../agent-clusters/智能体集群设计方案.md`](../agent-clusters/智能体集群设计方案.md)
与 [`docs/superpowers/plans/implementation-plan.md`](superpowers/plans/implementation-plan.md)。
