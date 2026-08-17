# DoAI v1 CLI 与一次性迁移

- 日期：2026-08-17
- 类：feature
- 状态：implemented

## 问题

旧运行时的数据必须能在不保留兼容执行链的前提下迁入 v1 唯一事件流；同时 v1 需要一个
固定且可诊断的命令入口，禁止未知参数和未配置能力静默落回旧实现。

## 决策

- CLI 命令面固定为 `run/web/plugin/config/session/doctor/migrate`；尚未接入活动 profile 的命令
  立即以结构化错误退出。
- `migrate --dry-run|--apply --from <path> --to <path>` 将旧 `session.json` 转换为规范 JSONL
  `SessionEvent`，写入前完成全量转换校验，写入采用临时文件原子重命名。
- apply 会备份每个源文件；重复运行跳过已有目标。源/目标嵌套、重复 session id 和互斥参数
  均 fail loud；写入中途失败删除本轮全部输出。

## 验证

- CLI 类型检查通过。
- 单测覆盖命令族、未知参数、dry-run、apply、幂等、重叠路径、重复 id 和写入中途回滚。
