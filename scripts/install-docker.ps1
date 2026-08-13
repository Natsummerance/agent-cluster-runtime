#requires -Version 5.1
<#
.SYNOPSIS
    Windows 自动安装 Docker Desktop（供 agent-cluster doctor --fix-docker 调用）。

.DESCRIPTION
    状态机：检测（docker --version CLI → docker info daemon）→ 已可用退出 0；
    winget 可用则 winget install Docker.DockerDesktop --silent，否则官方直链下载
    Docker Desktop Installer → 静默安装 → 轮询等待 daemon（上限 300s）→ docker info 验活；
    超时打印人工步骤清单（启动 Docker Desktop、WSL2 内核）并以非零码退出。
#>
[CmdletBinding()]
param(
    [int]$WaitSeconds = 300
)

$ErrorActionPreference = 'Stop'

function Write-Step($msg) { Write-Host "[install-docker] $msg" -ForegroundColor Cyan }

# 1) 检测
Write-Step '检测 Docker CLI...'
$dockerCli = Get-Command docker -ErrorAction SilentlyContinue
if ($null -eq $dockerCli) {
    Write-Step 'Docker CLI 未安装，进入安装流程'
} else {
    Write-Step 'Docker CLI 已安装，检测 daemon...'
    docker info *> $null
    if ($LASTEXITCODE -eq 0) {
        Write-Step 'Docker 已可用（CLI + daemon），无需安装'
        exit 0
    }
    Write-Step 'Docker CLI 存在但 daemon 未就绪，尝试启动/安装'
}

# 2) 安装
$installer = Join-Path $env:TEMP 'DockerDesktopInstaller.exe'
$useWinget = $true
$winget = Get-Command winget -ErrorAction SilentlyContinue
if ($null -ne $winget) {
    Write-Step '使用 winget 安装 Docker.DockerDesktop（--silent）'
    & winget install --id Docker.DockerDesktop --silent --accept-package-agreements --accept-source-agreements --disable-interactivity
    if ($LASTEXITCODE -ne 0) {
        Write-Warning "winget 安装退出码 $LASTEXITCODE，回退官方直链下载"
        $useWinget = $false
    }
} else {
    $useWinget = $false
}
if (-not $useWinget) {
    if (-not (Test-Path $installer)) {
        Write-Step '下载 Docker Desktop Installer（官方直链）...'
        $url = 'https://desktop.docker.com/win/main/amd64/Docker%20Desktop%20Installer.exe'
        Invoke-WebRequest -Uri $url -OutFile $installer -UseBasicParsing
        if (-not (Test-Path $installer)) { throw 'Docker Desktop Installer 下载失败' }
    }
    Write-Step "静默安装：$installer"
    & $installer install --quiet --accept-license
    if ($LASTEXITCODE -ne 0) {
        Write-Warning "安装器退出码 $LASTEXITCODE（常见 1 = 需要重启或已被占用，继续等待 daemon）"
    }
}

# 3) 轮询等待 daemon（上限 $WaitSeconds 秒）
$deadline = (Get-Date).AddSeconds($WaitSeconds)
$ok = $false
while ((Get-Date) -lt $deadline) {
    $cliNow = Get-Command docker -ErrorAction SilentlyContinue
    if ($null -ne $cliNow) {
        docker info *> $null
        if ($LASTEXITCODE -eq 0) { $ok = $true; break }
    }
    Start-Sleep -Seconds 5
}
if ($ok) {
    Write-Step 'Docker daemon 就绪（docker info 通过）'
    exit 0
}

# 4) 超时：人工步骤清单
Write-Warning @"
Docker 未在 $WaitSeconds 秒内就绪，请人工完成：
  1) 启动 Docker Desktop（开始菜单搜索 Docker Desktop）。
  2) 若提示 WSL2 内核更新：下载安装 https://aka.ms/wsl2kernel 后重启。
  3) 确认 Windows 功能「适用于 Linux 的 Windows 子系统」与「虚拟机平台」已启用。
  4) 重启后再次运行：agent-cluster doctor --fix-docker
"@
exit 1