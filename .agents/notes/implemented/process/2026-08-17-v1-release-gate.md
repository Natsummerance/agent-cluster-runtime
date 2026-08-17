# DoAI v1 独立 CI 与删除门

- 日期：2026-08-17
- 类：process
- 状态：implemented

## 问题

双平面代码存在于独立 pnpm workspace，原有 Python/前端 CI 不会自动覆盖 Host、bridge、preset、
enterprise plugin 和 CLI。若没有显式 deletion gate，alpha 基础设施容易被误判为可删除旧主链。

## 决策

- 新增 `v1-core` CI job，冻结 Node/pnpm 版本，校验协议生成 freshness、Agent Notes、六个包的
  typecheck 与测试。
- `docs/v1/release-readiness.md` 作为能力对照和删除门；未接生产 transport、Workbench、安装包
  和 OS 级沙箱前保持 `1.0.0-alpha.0`，不删除 legacy runtime。

## 验证

- 本机六个 v1 包共 47 个测试通过且 typecheck 全绿。
- 协议/Organization Plane 16 个、旧 Python 893 个、前端 164 个测试通过，前端生产构建通过。
