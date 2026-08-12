#requires -Version 5.1
<#
.SYNOPSIS
    AgentClusterWorkbench 桌面壳：自定义图标 + Windows 代码签名 + 构建 + 验证 一站式脚本（Windows）。

.DESCRIPTION
    默认使用自签名代码签名证书（仅本机测试）。正式发布请购买 CA 代码签名证书（DigiCert/GlobalSign 等）：
      * 方式 A（PFX 文件）：设置环境变量后运行
            $env:CERT_FILE = 'C:\path\to\cert.pfx'
            $env:CERT_PASSWORD = 'password'
            .\desktop\sign.ps1
        脚本会把 certificateFile / certificatePassword 临时注入 electron-builder.yml 构建，构建后还原。
      * 方式 B（证书存储）：把脚本顶部 $SubjectName 改成正式证书主题（或改 electron-builder.yml 的
        win.certificateSubjectName 为正式证书主题）。

.NOTES
    - 构建前会临时清除 GITHUB_TOKEN（避免 electron-builder 推断 publish provider 崩溃），构建后恢复。
    - electron-builder 25.1.8 的 CLI 点号传参（--config.win.certificateSubjectName=...）会被 yargs 丢弃，
      因此本脚本直接临时改写 electron-builder.yml 的 win 段，构建后还原。
    - 自签名证书首次装入“受信任根证书存储”时 Windows 会弹“安全警告”，需点“是(Y)”（仅本机测试需要；
      正式 CA 证书不需要此步）。
    - 原生签名需要 winCodeSign 缓存（%LOCALAPPDATA%\electron-builder\Cache\winCodeSign\winCodeSign-2.6.0，
      内含 windows-6/signtool.exe 与 rcedit-x64.exe）。缺失时 electron-builder 会重新下载解压；
      非管理员 / 未启用 Developer Mode 会因 darwin 符号链接解压失败。脚本会尝试从已有解压目录复制补全缓存。
    - 若原生路径仍失败，可用历史备选：npm run build:win -- --config.win.signAndEditExecutable=false，
      再用 desktop\build\tools\rcedit-x64.exe --set-icon 与 signtool 手工签名（见仓库交接文档）。
#>
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$desktop   = $PSScriptRoot
$distDir   = Join-Path $desktop 'dist'
$ymlPath   = Join-Path $desktop 'electron-builder.yml'
$SubjectName = 'CN=Natsummerance, O=AgentClusterWorkbench'

function Write-Step($msg) { Write-Host "[sign] $msg" -ForegroundColor Cyan }

# 1) 证书：优先 CERT_FILE/CERT_PASSWORD（正式证书），否则创建/复用自签名证书
$useCertFile = [bool]$env:CERT_FILE
$thumbprint = $null
if ($useCertFile) {
    if (-not (Test-Path $env:CERT_FILE)) { throw "CERT_FILE 不存在: $env:CERT_FILE" }
    Write-Step "使用证书文件: $env:CERT_FILE"
} else {
    $cert = Get-ChildItem Cert:\CurrentUser\My -CodeSigningCert -ErrorAction SilentlyContinue |
        Where-Object { $_.Subject -like '*CN=Natsummerance*' } | Select-Object -First 1
    if ($null -eq $cert) {
        $cert = New-SelfSignedCertificate -Type CodeSigningCert -Subject $SubjectName `
            -CertStoreLocation Cert:\CurrentUser\My -KeyUsage DigitalSignature `
            -KeyExportPolicy Exportable -NotAfter (Get-Date).AddYears(3) -NotBefore (Get-Date).AddDays(-1)
        Write-Step "已创建自签名证书: $($cert.Thumbprint)"
    } else {
        Write-Step "复用自签名证书: $($cert.Thumbprint)"
    }
    $thumbprint = $cert.Thumbprint
    # 自签名证书装入当前用户受信任根（首次会弹“安全警告”，点“是”）
    $inRoot = Get-ChildItem Cert:\CurrentUser\Root -ErrorAction SilentlyContinue |
        Where-Object { $_.Thumbprint -eq $thumbprint }
    if (-not $inRoot) {
        $cerTmp = Join-Path $env:TEMP "agentcluster-selfsigned-$thumbprint.cer"
        Export-Certificate -Cert $cert -FilePath $cerTmp -Force | Out-Null
        Import-Certificate -FilePath $cerTmp -CertStoreLocation Cert:\CurrentUser\Root | Out-Null
        Remove-Item $cerTmp -Force
        Write-Step '自签名证书已装入受信任根（本机测试用）'
    }
}
# 2) 确保 winCodeSign 缓存（原生资源编辑+签名需要）
$wcRoot = Join-Path $env:LOCALAPPDATA 'electron-builder\Cache\winCodeSign'
$wcCanon = Join-Path $wcRoot 'winCodeSign-2.6.0'
if (-not (Test-Path (Join-Path $wcCanon 'windows-6\signtool.exe'))) {
    $src = Get-ChildItem $wcRoot -Directory -ErrorAction SilentlyContinue |
        Where-Object { Test-Path (Join-Path $_.FullName 'windows-6\signtool.exe') } | Select-Object -First 1
    if ($src) {
        Copy-Item -LiteralPath $src.FullName -Destination $wcCanon -Recurse -Force
        Write-Step "已补全 winCodeSign 缓存（取自 $($src.Name)）"
    } else {
        Write-Warning 'winCodeSign 缓存缺失；构建将触发重新下载解压，非管理员环境可能因符号链接失败。'
    }
}

# 3) 临时把证书配置注入 electron-builder.yml（win 段），构建后还原
$ymlOriginal = [System.IO.File]::ReadAllText($ymlPath)
$anchor = "win:`n  target:"
if (-not $ymlOriginal.Contains($anchor)) { throw "electron-builder.yml 未找到 win: 段（$anchor）" }
$certLines = if ($useCertFile) {
    "  certificateFile: `"$($env:CERT_FILE -replace '\\','/')`"`n  certificatePassword: `"$env:CERT_PASSWORD`""
} else {
    "  certificateSubjectName: `"$SubjectName`""
}
$ymlPatched = $ymlOriginal.Replace($anchor, "win:`n$certLines`n  target:")
[System.IO.File]::WriteAllText($ymlPath, $ymlPatched, [System.Text.UTF8Encoding]::new($false))
Write-Step '已临时注入签名配置到 electron-builder.yml（构建后还原）'

try {
    # 4) 构建（临时清除 GITHUB_TOKEN，构建后恢复）
    $bakToken = $env:GITHUB_TOKEN
    Remove-Item Env:GITHUB_TOKEN -ErrorAction SilentlyContinue
    Write-Step '开始 electron-builder 构建（--win nsis）...'
    Push-Location $desktop
    try {
        npm run build:win
        if ($LASTEXITCODE -ne 0) { throw "npm run build:win 失败，退出码 $LASTEXITCODE" }
    } finally {
        Pop-Location
        if ($null -ne $bakToken) { $env:GITHUB_TOKEN = $bakToken }
    }
    Write-Step '构建完成'
}
finally {
    [System.IO.File]::WriteAllText($ymlPath, $ymlOriginal, [System.Text.UTF8Encoding]::new($false))
    Write-Step 'electron-builder.yml 已还原'
}

# 5) 验证签名
$targets = @()
$targets += Get-ChildItem (Join-Path $distDir '*.exe') -ErrorAction SilentlyContinue
$appExe = Join-Path $distDir 'win-unpacked\AgentClusterWorkbench.exe'
if (Test-Path $appExe) { $targets += Get-Item $appExe }
$failed = $false
foreach ($t in $targets) {
    $sig = Get-AuthenticodeSignature -LiteralPath $t.FullName
    $signer = if ($sig.SignerCertificate) { $sig.SignerCertificate.Subject } else { '(none)' }
    $ts = if ($sig.TimeStamperCertificate) { $sig.TimeStamperCertificate.Subject } else { '(none)' }
    Write-Step ("签名检查 {0}: Status={1} Signer={2} TimeStamper={3}" -f $t.Name, $sig.Status, $signer, $ts)
    if ($sig.Status -ne 'Valid') { $failed = $true }
}
if ($failed) {
    Write-Warning '存在签名状态非 Valid 的文件（自签名需先装入受信任根；正式证书请检查有效期/私钥）'
}
Write-Step ("完成。安装包: {0}" -f (Join-Path $distDir 'AgentClusterWorkbench Setup 0.5.0.exe'))