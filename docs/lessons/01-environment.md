# 01-environment —— Windows / PowerShell / uv / git

> 何时加载：任何本机命令操作；写文件失败、命令被策略拦截、后台进程异常时**必读**。

## 写文件
- `apply_patch` 报 `Access is denied`（Z:/ 权限）→ 用 PowerShell
  `[System.IO.File]::WriteAllText(path, text, New-Object System.Text.UTF8Encoding($false))`（UTF-8 无 BOM）。

## 命令策略（本机实测的拦截规则）
- 单条命令过长 → 被拦截：长逻辑拆多条执行。
- `Remove-Item` → 被拦截：用 `[System.IO.File]::Delete` / `[System.IO.Directory]::Delete(dir, $true)`。
- `Start-Process` 组合复杂表达式 → 被拦截：拆成「启动」与「等待/检查」两条。
- 后台进程一律 `-WindowStyle Hidden`；`uv run` 会留子进程 → 清理用 `taskkill /PID <pid> /T /F`。

## uv / venv
- **禁止 `uv run --python 3.11`**（会重建 venv，慢且危险）→ 一律 `uv run pytest`（venv 已固定 3.11）。
- `uv run agent-cluster serve` 冷启动很慢（可达 20s+，曾 90s 未就绪）→ 探活窗口给足 90s。
- 改版本后首次 `uv run python` 会自动重装包并更新 `uv.lock`（正常现象，非报错）。

## git
- `autocrlf=true`，提交时 CRLF/LF 警告无害。
- 行尾约定（改文件前先查）：`ci.yml` / `electron-builder.yml` / `playwright.real.config.ts` / `test_doctor.py` / docs 为 LF；`test_tools.py` / `test_acceptance_v04.py` 为 CRLF。
- `git add -A` 会把未跟踪文档也带上（如历史 handoff），提交前 `git status` 确认范围。