#!/bin/bash
# prepare-backend.sh - 为 Electron 打包准备后端运行时
# 用法: bash scripts/prepare-backend.sh
# 
# 此脚本在项目根目录执行，生成 .venv-pack 目录供 electron-builder 使用

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

echo "📦 准备后端运行时..."
echo "项目根目录: $PROJECT_ROOT"

cd "$PROJECT_ROOT"

# 检查 uv 是否安装
if ! command -v uv &> /dev/null; then
    echo "❌ 错误: uv 未安装"
    echo "请安装 uv: https://docs.astral.sh/uv/getting-started/installation/"
    exit 1
fi

echo "✅ uv 已安装: $(uv --version)"

# 清理旧的 .venv-pack
if [ -d ".venv-pack" ]; then
    echo "🗑️  清理旧的 .venv-pack..."
    rm -rf .venv-pack
fi

# 生成精简 venv（无 dev 依赖）
echo "🔧 生成精简虚拟环境 (.venv-pack)..."
UV_PROJECT_ENVIRONMENT=.venv-pack uv sync --no-dev --frozen

echo "✅ 后端运行时准备完成!"
echo "📁 .venv-pack 目录大小: $(du -sh .venv-pack | cut -f1)"
echo ""
echo "下一步: 运行 electron-builder 打包桌面应用"
echo "  cd desktop && npm run build:win   # Windows"
echo "  cd desktop && npm run build:mac   # macOS"
echo "  cd desktop && npm run build:linux # Linux"
