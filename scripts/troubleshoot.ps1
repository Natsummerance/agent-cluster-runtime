# troubleshoot.ps1 —— 3 次即停协议的一键诊断（只读，不修改任何状态）
# 用法：powershell -ExecutionPolicy Bypass -File scripts/troubleshoot.ps1
# 输出：环境/进程/端口/缓存/CI 状态摘要 + 可执行结论。CI 状态查询需要 $env:GITHUB_TOKEN。

$ErrorActionPreference = "SilentlyContinue"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
"===== agent-cluster-runtime 诊断 $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') ====="

# 1. 端口 8765
$conn = Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue
if ($conn) {
  $pid8765 = $conn | Select-Object -First 1 -ExpandProperty OwningProcess
  $proc = Get-Process -Id $pid8765 -ErrorAction SilentlyContinue
  "[port] 8765 被占用：PID $pid8765 ($($proc.ProcessName)) —— serve 已就绪或残留"
} else {
  "[port] 8765 空闲 —— 无 serve 运行"
}

# 2. 残留 uv/python 进程
$suspects = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
  Where-Object { $_.CommandLine -match "agent-cluster serve" }
if ($suspects) {
  "[proc] 检测到 serve 相关进程："
  $suspects | ForEach-Object { "    PID $($_.ProcessId): $($_.CommandLine.Substring(0, [Math]::Min(120, $_.CommandLine.Length)))" }
} else {
  "[proc] 无 serve 相关残留进程"
}

# 3. 缓存陈旧线索
$now = Get-Date
foreach ($dir in @("$root\src", "$root\tests")) {
  $pyc = Get-ChildItem $dir -Recurse -Directory -Filter "__pycache__" -ErrorAction SilentlyContinue |
    Where-Object { $_.LastWriteTime -gt $now.AddMinutes(-30) } | Select-Object -First 3
  if ($pyc) { "[cache] $dir 近 30 分钟有 pyc 写入（陈旧字节码风险）:"; $pyc | ForEach-Object { "    $($_.FullName) $($_.LastWriteTime)" } }
}
if (Test-Path "$root\.pytest_cache") {
  $pc = Get-Item "$root\.pytest_cache"
  if ($pc.LastWriteTime -gt $now.AddMinutes(-30)) { "[cache] .pytest_cache 近 30 分钟有写入（场景 B 基线后应清理）" }
}

# 4. venv
if (Test-Path "$root\.venv\Scripts\python.exe") {
  $ver = & "$root\.venv\Scripts\python.exe" -c "import sys; print(sys.version.split()[0])"
  "[venv] OK python $ver"
} else {
  "[venv] 缺失 —— 需 uv sync --frozen"
}

# 5. git
$status = git status --short
if ($status) { "[git] 工作树有未提交改动："; $status | Select-Object -First 5 | ForEach-Object { "    $_" } } else { "[git] 工作树干净" }

# 6. 最近 CI run（需 GITHUB_TOKEN）
if ($env:GITHUB_TOKEN) {
  try {
    $h = @{ Authorization = "Bearer $env:GITHUB_TOKEN"; Accept = "application/vnd.github+json" }
    $runs = Invoke-RestMethod -Headers $h -Uri "https://api.github.com/repos/Natsummerance/agent-cluster-runtime/actions/runs?per_page=5"
    "[ci] 最近 5 个 run："
    $runs.workflow_runs | ForEach-Object { "    $($_.id) $($_.event) $($_.head_sha.Substring(0,7)) $($_.status)/$($_.conclusion)" }
  } catch { "[ci] 查询失败：$($_.Exception.Message)" }
} else {
  "[ci] GITHUB_TOKEN 未设置，跳过 CI 状态查询"
}

"===== 结论 ====="
"1. 若 8765 被占用且是残留 serve：taskkill /PID <pid> /T /F"
"2. 若 pyc/cache 近 30 分钟写入且 QA 偶发失败：清理 __pycache__ / .pytest_cache 后重跑"
"3. 若 CI 红：先看 diagnostics-* artifact 或失败 job 日志尾部，再对照 docs/lessons/ 索引"
"4. 3 次即停：同一问题已试 3 次 → 停止并基于以上证据写根因假设，不要继续换招重试"