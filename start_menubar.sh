#!/usr/bin/env bash
# 启动桌面浮动浮标 app（可拖拽圆形图标，类似豆包）
set -e
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_DIR"

source venv/bin/activate

echo "启动 Copilot 浮动浮标..."
echo "（圆形可拖拽图标会出现在桌面上）"
echo ""

exec python3 -m copilot.floating_native
