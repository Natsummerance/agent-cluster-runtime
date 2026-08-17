# DoAI v1 深度审查与续作交接

- 日期：2026-08-17
- 类：process
- 状态：implemented

## 问题

初版 release readiness 主要记录产品入口和发布缺口，但没有揭示 scope 串扰、不可逆工具注册、
权限未执行、事件存储 durability、FakeHost 崩溃证据、企业插件占位和迁移丢字段等基础问题，
容易让后续实现者错误地从 M5 开始或提前删除 legacy。

## 决策

- 新增单文档可开工的 v1 continuation handoff，固定审查基线、双轴 findings、完成度矩阵、
  不得删除清单、P0–P3 顺序、公共接口、测试矩阵、风险和首个 TDD 任务。
- release readiness 改为四阶段阻断门，并明确区分 regression、integration smoke、real E2E 和
  installed-artifact evidence。
- P0 基础契约完成前不继续产品迁移；替代证据完整前不删除任何 legacy 主链。

## 验证

- handoff 中的证据路径均由 `origin/main...707d4b6` 静态审查确认。
- `scripts/verify_agent_notes.py` 与文档 diff 检查必须通过。
