---
name: prd-writing
description: 产品需求文档（PRD）编写技能：需求背景、目标用户、功能清单、验收标准、范围边界与交付物 token 预估。
version: 0.1.0
license: MIT
allowed-tools:
  - read_file
  - write_file
  - mkdir
  - ask_user
  - count_tokens
---
# PRD 编写执行指引

1. 背景与目标：一句话说明要解决什么问题、给谁用、成功标准。
2. 目标用户与场景：用户画像 + 典型使用路径（必要时经 ask_user 澄清）。
3. 功能清单：按优先级（P0/P1/P2）列出，每条含验收标准（Given/When/Then）。
4. 范围边界：明确不做的事、外部依赖、非功能需求（性能/安全/可访问性）。
5. 交付物清单与 token 预估：用 count_tokens 估算 PRD 与后续产物的 token 大小，
   按 v0.3 token 制规划给每个阶段/任务标注预估消耗。
6. 落盘：写入 docs/PRD.md，保持单一事实来源；需求变更走 edit 并更新版本号。
