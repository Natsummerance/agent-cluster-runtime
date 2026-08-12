# Agent Cluster 工作台 · 设计系统（DESIGN.md）

> 位置：`frontend/DESIGN.md`。本文件是前端视觉与工程基线的唯一事实来源。
> 组件必须消费 token，不得散装硬编码原始视觉常量（色值/字号/圆角/阴影）。

## 1. 产品定位（product-style-map）

- 主类型：**数据看板 / 运营面板** —— 信息优先、层级强、快速扫描。
- 次类型：企业工作台 —— 可信、效率导向、低干扰、连续任务流。
- 主题对象：多 Agent 集群运行时控制台（集群、会话、审批门、token 账本、进化提案）。

## 2. 色盘（color token）

| 语义 | 值（亮色） | 值（暗色） | 用途 |
|------|-----------|-----------|------|
| `--ac-primary` | `#0f8f8f`（信号青绿） | `#2fb6b6` | 主色/链接/信息，唯一强调色 |
| `--ac-primary-weak` | `#e6f6f6` | `#123a3a` | 主色弱化底（标签、脉冲光环） |
| `--surface` | `#ffffff` | `#14181c` | 卡片/容器 |
| `--surface-muted` | `#f4f7f8` | `#0f1416` | 页面底、弱化区块 |
| `--border-weak` | `#e6ebef` | `#2a3138` | 细分隔线 |
| `--text-ink` | `#1f2933` | `#e6edf3` | 正文 |
| `--text-muted` | `#5f6b7a` | `#8b98a5` | 次要说明 |
| 语义状态 | 沿用 AntD 语义色（success/warning/error/info） | 同左 | 状态不单靠颜色，配文字/图标 |

## 3. 排版（typography）

- 正文/标题：系统栈 `-apple-system, "Segoe UI", Roboto, "PingFang SC", "Microsoft YaHei"`。
- 数据/标识：等宽栈 `SFMono-Regular, Consolas, "Cascadia Code", "Liberation Mono"`（会话 ID、token 数字、事件 seq）。
- 层级：页面标题 20 / 区块标题 16 / 正文 14 / 辅助 12；行高 1.5~1.6。
- 数字显示：统计数字使用等宽字形，避免扫描时抖动。

## 4. 间距与布局（spacing / layout）

- 刻度：4/8/12/16/24/32/48。
- 页面边距 24、卡片间距 16、控件间距 8、表单字段间距 16。
- 页面头部统一使用 `PageHeader`（标题 + 描述 + 右侧操作），所有页面同构。
- 内容宽度：主内容区最大 1280，居中；窄表单（设置）最大 640。

## 5. 圆角与阴影（radius / shadow）

- 圆角：控件 8 / 卡片 12 / 弹层 12；标签 6。
- 阴影：卡片 `0 1px 2px rgba(15,20,25,.06) + 0 4px 12px rgba(15,20,25,.04)`；弹层加 24px 投影。

## 6. 动效（motion）

- 状态切换 150ms ease；反馈类 200ms。
- 唯一装饰动效：`LivePulse` 集群脉搏（2s 呼吸光环），表达“集群存活/活跃会话”。
- `prefers-reduced-motion: reduce` 下关闭全部装饰动效。

## 7. 签名元素

- **集群脉搏（LivePulse）**：头部与仪表盘展示的呼吸信号点，随连接状态与活跃会话数变化；
  离线转为红色静态。这是本界面唯一的记忆点，其余布局保持安静与克制。

## 8. 可访问性基线

- 键盘可达、焦点可见（`:focus-visible` 外环）、标签可理解、对比达标（WCAG AA）。
- 状态变化通过 `aria-live` 播报（事件时间线、连接状态）。
- 图标按钮必须带 `aria-label` 或可见文本。
- 触达面积：默认以 AntD 控件高度（≥32px）为准；关键操作不小于 36px（已知限制：小号内联按钮 24px，属可接受权衡，记录为 QA 观察点）。

## 9. 组件分层（工程）

- `src/api`：请求适配与类型契约（Services）。
- `src/store`：服务端数据 + 跨页 UI 状态（zustand）。
- `src/hooks`：复用逻辑（参数读取等）。
- `src/components`：通用基元（展示/输入/反馈）。
- `src/pages`：路由编排与区块组合（不直接散落视觉常量）。
- 每个页面必须覆盖 loading / empty / error / success 四态。