#!/usr/bin/env bash
# install-docker.sh —— macOS 自动安装 Docker（供 agent-cluster doctor --fix-docker 调用）
set -u
WAIT_SECONDS="${WAIT_SECONDS:-300}"
log() { printf '[install-docker] %s\n' "$*"; }
if command -v docker >/dev/null 2>&1; then
  log 'Docker CLI 已安装，检测 daemon...'
  if docker info >/dev/null 2>&1; then
    log 'Docker 已可用（CLI + daemon），无需安装'
    exit 0
  fi
  log 'Docker CLI 存在但 daemon 未就绪，尝试启动/安装'
else
  log 'Docker CLI 未安装，进入安装流程'
fi# 2) 安装
if command -v brew >/dev/null 2>&1; then
  log '使用 Homebrew 安装 Docker（brew install --cask docker）...'
  if ! brew install --cask docker; then
    log 'brew cask docker 失败，尝试 colima + docker CLI 路径...'
    brew install colima docker || log 'colima 安装失败，继续尝试官方 dmg'
    colima start || log 'colima start 失败，继续尝试官方 dmg'
  fi
else
  log '未检测到 Homebrew，使用官方 dmg 安装...'
fi  ARCH="$(uname -m)"
  if [ "$ARCH" = "arm64" ]; then
    DMG_URL="https://desktop.docker.com/mac/main/arm64/Docker.dmg"
  else
    DMG_URL="https://desktop.docker.com/mac/main/amd64/Docker.dmg"
  fi
  DMG_PATH="${TMPDIR:-/tmp}/Docker.dmg"
  log "下载 $DMG_URL ..."
  curl -fL "$DMG_URL" -o "$DMG_PATH" || { log '下载失败'; exit 1; }
  log '挂载并复制 Docker.app 到 /Applications ...'
  hdiutil attach "$DMG_PATH" -nobrowse -quiet
  cp -R "/Volumes/Docker/Docker.app" /Applications/ || true
  hdiutil detach "/Volumes/Docker" -quiet || true
fi# 3) 轮询等待 daemon（上限 $WAIT_SECONDS 秒）
deadline=$(( $(date +%s) + WAIT_SECONDS ))
while [ "$(date +%s)" -lt "$deadline" ]; do
  if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
    log 'Docker daemon 就绪（docker info 通过）'
    exit 0
  fi
  sleep 5
done

# 4) 超时：人工步骤清单
cat <<'EOF'
Docker 未在 300 秒内就绪，请人工完成：
  1) 打开"启动台"启动 Docker Desktop（首次需同意服务条款）。
  2) 若提示缺少虚拟化支持：确认 macOS 系统设置 → 通用 → 软件更新已安装最新版本。
  3) 若使用 colima：执行 colima start 并确认 docker context 指向 colima。
  4) 就绪后再次运行：agent-cluster doctor --fix-docker
EOF
exit 1