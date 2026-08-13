# 08-token-economy —— 排查与验证的 token 节省法则

> 何时加载：排查耗时问题、面对大日志/大产物、token 预算紧张时**必读**。

## 日志与产物（最大头）
- **大日志先过滤再读**：`Select-String -Pattern "FAILED|Timeout|error|##[error]"` 只取关键行，
  不要整份日志灌进上下文（GitHub job 日志可达 MB 级）。
- **先看摘要再拉日志**：run 列表 → job conclusion 表 → 只拉失败 job；失败 job 的日志也只取尾部。
- **artifacts 先列清单再下载**：`GET /actions/runs/{id}/artifacts` 看名字/大小；400MB 级产物
  （如 win/mac 安装包）通常**不需要下载**——用文件命名与 `latest*.yml` 内容即可推断。
- CI 的 `diagnostics-*` artifact 是首选证据（已截断 120 行），比原生日志省一个数量级。

## 测试与验证
- **全量测试留最后**：本地 pytest 165s / vitest 140s+，每轮等待都是成本；按改动范围选相关文件。
- 版本/机械改动：`test_t12_11` + `test_t12_12` + 4 个前端 mock 文件即可。
- 轮询 CI：用 30s sleep 的长循环，一次等待到底，不频繁请求。

## 后台服务
- serve 冷启动 20-90s：先探活（带认证头）再继续，避免「起了又杀、杀了又起」的反复。
- 后台进程先重定向 stdout/stderr 到文件：需要时再按行读，不占上下文。

## 沟通
- 失败信息先压缩成「现象 + 证据 + 假设」三行再继续，避免把整段报错反复贴入。
- 3 次即停（`07-debugging`）本身就是最大的 token 节省器。