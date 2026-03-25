#!/bin/bash
# 快捷重启脚本（会强制清理所有相关进程）

cd "$(dirname "$0")"

echo "停止所有进程..."
pkill -9 -f "uvicorn main:app" 2>/dev/null || true
pkill -9 -f "vite" 2>/dev/null || true
lsof -ti :8000 | xargs kill -9 2>/dev/null || true
lsof -ti :5173 | xargs kill -9 2>/dev/null || true

sleep 2
echo "重新启动..."
./start.sh
