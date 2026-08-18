# prepare-backend.ps1 - 为 Electron 打包准备后端运行时 (Windows PowerShell)
# 用法: .\scripts\prepare-backend.ps1
# 
# 此脚本在项目根目录执行，生成 .venv-pack 目录供 electron-builder 使用

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Resolve-Path (Join-Path $ScriptDir "..\..")

Write-Host "📦 准备后端运行时..." -ForegroundColor Cyan
Write-Host "项目根目录: $ProjectRoot" -ForegroundColor Gray

Set-Location $ProjectRoot

# 检查 uv 是否安装
try {
    $uvVersion = & uv --version 2>&1
    Write-Host "✅ uv 已安装: $uvVersion" -ForegroundColor Green
} catch {
    Write-Host "❌ 错误: uv 未安装" -ForegroundColor Red
    Write-Host "请安装 uv: https://docs.astral.sh/uv/getting-started/installation/" -ForegroundColor Yellow
    exit 1
}

# 清理旧的 .venv-pack
if (Test-Path ".venv-pack") {
    Write-Host "🗑️  清理旧的 .venv-pack..." -ForegroundColor Yellow
    Remove-Item -Recurse -Force .venv-pack
}

# 生成精简 venv（无 dev 依赖）
Write-Host "🔧 生成精简虚拟环境 (.venv-pack)..." -ForegroundColor Cyan
$env:UV_PROJECT_ENVIRONMENT = ".venv-pack"
& uv sync --no-dev --frozen

Write-Host ""
Write-Host "✅ 后端运行时准备完成!" -ForegroundColor Green
$size = (Get-ChildItem .venv-pack -Recurse | Measure-Object -Property Length -Sum).Sum / 1MB
Write-Host "📁 .venv-pack 目录大小: $([math]::Round($size, 2)) MB" -ForegroundColor Gray
Write-Host ""
Write-Host "下一步: 运行 electron-builder 打包桌面应用" -ForegroundColor Cyan
Write-Host "  cd desktop && npm run build:win   # Windows" -ForegroundColor Gray
Write-Host "  cd desktop && npm run build:mac   # macOS" -ForegroundColor Gray
Write-Host "  cd desktop && npm run build:linux # Linux" -ForegroundColor Gray
