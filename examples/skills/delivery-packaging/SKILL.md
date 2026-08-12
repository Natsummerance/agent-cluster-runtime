---
name: delivery-packaging
description: 交付包组装技能：产物清单、token 计量表、README/用户手册/API 文档、DELIVERY.md 勾连索引。
version: 0.1.0
license: MIT
allowed-tools:
  - read_file
  - write_file
  - mkdir
  - list_dir
  - grep
  - glob
  - count_tokens
  - git_status
  - git_diff
  - git_add
  - git_commit
---
# 交付包组装执行指引

1. 盘点交付物：代码、测试报告、部署产物、README、用户手册、API 文档逐一确认存在。
2. token 计量：用 count_tokens 统计每个产物的 token 大小；汇总各阶段/角色消耗，
   形成 DELIVERY.md 的 token 计量表（产物大小 / 产生消耗 / 预算剩余）。
3. 勾连索引：需求 → PRD → 设计 → 代码 → 测试 → 部署 → 手册 的路径索引。
4. 质量核对：测试报告显示通过、构建/冒烟产物存在、缺失项在 DELIVERY.md 标注。
5. 收尾：更新 README/用户手册到最终版本，git add + commit，输出交付说明。
