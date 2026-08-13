# agent-delivery Actions 模板（第一方）

轻量 GitHub Actions 模板：用 `agent-cluster build` 生成 PR → **人工审批** → 复用 CI 测试管线 →
失败停在失败评论（可选自动修复）。**不自动合并** —— agent 生成的 PR 永远需要人工确认。

## 安装

1. 把 `agent-delivery.yml` 复制到目标仓库 `.github/workflows/agent-delivery.yml`。
2. 修改模板中的 CI 复用路径：

   ```yaml
   uses: Natsummerance/agent-cluster-runtime/.github/workflows/ci.yml@main
   ```

   改为该仓库自己的 `ci.yml`（`uses: ./.github/workflows/ci.yml`），或 fork 后的仓库路径。

3. 目标仓库需要可用 `agent-cluster`（uv 项目 + `uv sync`）与 GitHub Actions 权限。

## 输入（workflow_dispatch）

| 输入 | 必填 | 默认 | 说明 |
| --- | --- | --- | --- |
| `goal` | 是 | - | Agent 目标（任务描述） |
| `model` | 否 | `deterministic` | 模型名；演示/测试用 deterministic，正式请填实际模型 |
| `budget` | 否 | `200000` | 会话 token 预算 |
| `auto_fix_on_failure` | 否 | `false` | CI 失败时自动重跑 agent 修复（默认关闭，人工处理） |

## 人工审批配置

`approve` job 使用 `environment: production`：

1. 仓库 Settings → Environments → New environment → 命名 `production`。
2. 勾选 **Required reviewers** 并添加审批人（至少 1 人）。
3. 可再勾选 **Deployment branches** 限定允许发布的分支。

这样 workflow_dispatch 触发后，build 产出 PR，CI 管线**等待人工审批后才执行**；
失败默认只在 PR 上留评论，不自动合并、不自动写默认分支。

## 安全默认（为什么这样设计）

- Agent 生成的代码变更必须经人工审批后才进入 CI/CD（行业安全默认，综合调研 §4）。
- 模板中不存在任何自动合并/写默认分支的动作。
- `auto_fix_on_failure` 默认关闭，避免失败后无人值守的自动改写循环。