#requires -Version 5.1
<#
.SYNOPSIS
    DoAI Workbench 桌面壳：自定义图标 + 代码签名 + 构建 + 验证 一站式脚本（Windows PowerShell）。

.DESCRIPTION
    默认构建 Windows NSIS（自签名测试证书）。支持环境变量切换目标平台/架构：
      $env:TARGET = 'win' | 'mac' | 'linux'   （默认 win）
      $env:ARCH   = 'x64' | 'arm64'            （默认 x64；win 可同时构建 x64+arm64 两架构）
    正式发布请购买 CA 代码签名证书（DigiCert/GlobalSign 等）：
      * Windows：设置 CERT_FILE/CERT_PASSWORD（PFX）或改 $SubjectName（证书存储）
      * macOS：APPLE_ID/APPLE_APP_PASSWORD/APPLE_TEAM_ID 齐备时自动启用公证（NOTARIZE=1）；
        无证书时跳过签名并追加 "-unsigned" 产物名（UPDATER_SUFFIX=-unsigned），latest-mac.yml 由
        scripts/patch-update-metadata.js 标注 unsigned: true（release 阶段执行）
      * Linux：deb 无强签名基建，不做签名

.NOTES
    - 构建前会临时清除 GITHUB_TOKEN（避免 electron-builder 推断 publish provider），构建后恢复。
    - electron-builder 25.1.8 的 CLI 点号传参（--config.win.certificateSubjectName=...）会被 yargs 丢弃，
      因此本脚本直接临时改写 electron-builder.yml 的 win 段，构建后还原。
    - 自签名证书首次装入"受信任根证书存储"时 Windows 会弹"安全警告"，需点"是(Y)"（仅本机测试需要；
      正式 CA 证书不需要此步）。
    - 原生签名需要 winCodeSign 缓存（%LOCALAPPDATA%\electron-builder\Cache\winCodeSign\winCodeSign-2.6.0，
      内含 windows-6/signtool.exe 与 rcedit-x64.exe）。缺失时 electron-builder 会重新下载解压；
      非管理员 / 未启用 Developer Mode 会因 darwin 符号链接解压失败。脚本会尝试从已有解压目录复制补全缓存。
    - macOS 分支为条件预留：本机为 Windows 时打印指引，不做实际构建（CI macos runner 上才真正执行）。
#>
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$desktop   = $PSScriptRoot
$distDir   = Join-Path $desktop 'dist'
$ymlPath   = Join-Path $desktop 'electron-builder.yml'
$SubjectName = 'CN=Natsummerance, O=DoAI Workbench'
$Target    = if ($env:TARGET) { $env:TARGET.ToLowerInvariant() } else { 'win' }
$Arch      = if ($env:ARCH)   { $env:ARCH.ToLowerInvariant() } else { 'x64' }

function Write-Step($msg) { Write-Host "[sign] $msg" -ForegroundColor Cyan }

if ($Target -notin @('win', 'mac', 'linux')) { throw "TARGET 必须是 win/mac/linux，当前: $Target" }
if ($Arch -notin @('x64', 'arm64')) { throw "ARCH 必须是 x64/arm64，当前: $Arch" }
if ($Target -eq 'linux' -and $Arch -ne 'x64') { Write-Warning 'Linux 首版仅 x64，忽略 ARCH' }

# 1) Windows 证书准备（仅 win 需要）
if ($Target -eq 'win') {
    $useCertFile = [bool]$env:CERT_FILE
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
    # 确保 winCodeSign 缓存（原生资源编辑+签名需要）
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
}

# 2) macOS 公证/无证书分支（条件预留；本机 Windows 仅打印指引）
if ($Target -eq 'mac') {
    $hasAppleEnv = [bool]($env:APPLE_ID -and $env:APPLE_APP_PASSWORD -and $env:APPLE_TEAM_ID)
    if ($hasAppleEnv) {
        $env:NOTARIZE = '1'
        $env:UPDATER_SUFFIX = ''
        Write-Step 'macOS：检测到 Apple 公证凭据，启用 NOTARIZE=1（dmg 与 zip 同时公证）'
    } else {
        Remove-Item Env:NOTARIZE -ErrorAction SilentlyContinue
        $env:UPDATER_SUFFIX = '-unsigned'
        Write-Step 'macOS：无证书，跳过签名/公证，产物名追加 -unsigned（latest-mac.yml 将由 patch-update-metadata.js 标注 unsigned: true）'
    }
    if ($env:OS -like 'Windows*') {
        Write-Warning '当前是 Windows 主机：macOS 构建请交给 CI macos runner（本脚本预留分支，此处仅设置环境变量后继续）。'
    }
}

# 3) Windows：临时把证书配置注入 electron-builder.yml（win 段），构建后还原
$ymlOriginal = [System.IO.File]::ReadAllText($ymlPath)
if ($Target -eq 'win') {
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
}

try {
    # 4) 构建（临时清除 GITHUB_TOKEN，构建后恢复）
    $bakToken = $env:GITHUB_TOKEN
    Remove-Item Env:GITHUB_TOKEN -ErrorAction SilentlyContinue
    $scriptName = switch ($Target) {
        'win'   { if ($Arch -eq 'arm64') { 'npx electron-builder --win nsis --arm64' } else { 'npm run build:win' } }
        'mac'   { if ($Arch -eq 'arm64') { 'npx electron-builder --mac --arm64' } else { 'npm run build:mac' } }
        'linux' { 'npm run build:linux' }
    }
    Write-Step "开始 electron-builder 构建（$Target/$Arch）：$scriptName"
    Push-Location $desktop
    try {
        if ($scriptName.StartsWith('npx ')) {
            $parts = $scriptName -split ' '
            & $parts[0] $parts[1..($parts.Count - 1)]
        } else {
            npm run ($scriptName -replace '^npm run ', '')
        }
        if ($LASTEXITCODE -ne 0) { throw "$scriptName 失败，退出码 $LASTEXITCODE" }
    } finally {
        Pop-Location
        if ($null -ne $bakToken) { $env:GITHUB_TOKEN = $bakToken }
    }
    Write-Step '构建完成'
}
finally {
    if ($ymlPatched) {
        [System.IO.File]::WriteAllText($ymlPath, $ymlOriginal, [System.Text.UTF8Encoding]::new($false))
        Write-Step 'electron-builder.yml 已还原'
    }
}

# 5) 验证（仅 Windows 做签名检查；mac/linux 由 CI 产物确认）
if ($Target -eq 'win') {
    $targets = @()
    $targets += Get-ChildItem (Join-Path $distDir '*.exe') -ErrorAction SilentlyContinue
    $appExe = Join-Path $distDir 'win-unpacked\DoAI Workbench.exe'
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
}

$version = (Get-Content (Join-Path $desktop 'package.json') -Raw | ConvertFrom-Json).version
Write-Step "完成（$Target/$Arch, v$version）。产物目录: $distDir"