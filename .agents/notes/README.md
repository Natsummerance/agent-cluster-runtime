# Agent Notes（v0.7 T14.8，dsh .agents/notes 契约移植）

## 布局与生命周期
- 路径编码：`{lifecycle}/{class}/yyyy-mm-dd-topic-title.md`。
- lifecycle 四态（顶层目录）：`proposed/`（评审前提案）、`implemented/`（已落地的决策，
  随代码事实保持最新）、`archived/`（已封存，永久冻结）、`rejected/`（已否决，仅在防止
  重蹈覆辙时保留）。
- class 六类（封闭集合，`scripts/verify_agent_notes.py` 校验）：
  `feature` / `bug-fix` / `simplification` / `architecture` / `process` / `testing`。
- **不设中央 INDEX.md**（校验脚本强制）；交叉引用用相对 Markdown 链接，便于机械校验与移动。

## 何时写
任何非平凡变更（行为/架构/契约/格式/流程/测试策略变化）必须在同一提交内新增或更新
至少一篇 Agent Note。纯机械/局部编辑豁免。

## 归档规则
`implemented/` 决策完结且理由不再指导未来工作时移入 `archived/`；归档树永久冻结，
只允许在 Status 行下插入 `Archived: YYYY-MM-DD`。提案不归档——过时即 `rejected/`。
