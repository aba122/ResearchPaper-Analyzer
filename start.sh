#!/bin/bash
set -e

ROOT="$(cd "$(dirname "$0")" && pwd)"

# Load .env if present
if [ -f "$ROOT/.env" ]; then
  export $(grep -v '^#' "$ROOT/.env" | xargs)
fi

# Check required env vars
if [ -z "$MINERU_TOKEN" ]; then
  echo "⚠️  MINERU_TOKEN 未设置，MinerU 解析功能将不可用"
fi

# ────────────────────────────────────────────────────────────────────────────
# 清理旧进程，避免端口冲突
# ────────────────────────────────────────────────────────────────────────────
echo "清理旧进程..."
pkill -f "uvicorn main:app" 2>/dev/null || true
pkill -f "vite" 2>/dev/null || true
sleep 1

# ────────────────────────────────────────────────────────────────────────────
# 确保使用正确的 Python（miniconda with OpenSSL 3.0）
# ────────────────────────────────────────────────────────────────────────────
PYTHON_BIN="/Users/wulinxie/miniconda3/bin/python3"

if [ ! -f "$PYTHON_BIN" ]; then
  echo "❌ 未找到 miniconda Python: $PYTHON_BIN"
  echo "请安装 miniconda 或修改 start.sh 中的 PYTHON_BIN 路径"
  exit 1
fi

# 检查 venv 是否用正确的 Python 创建（必须是 OpenSSL 3.0+）
cd "$ROOT/backend"
NEEDS_REBUILD=0
if [ ! -f "venv/bin/python" ]; then
  NEEDS_REBUILD=1
else
  # 检查 OpenSSL 版本
  SSL_VERSION=$(venv/bin/python -c "import ssl; print(ssl.OPENSSL_VERSION)" 2>/dev/null || echo "")
  if ! echo "$SSL_VERSION" | grep -q "OpenSSL 3"; then
    echo "⚠️  检测到旧的 SSL 版本: $SSL_VERSION"
    NEEDS_REBUILD=1
  fi
fi

if [ $NEEDS_REBUILD -eq 1 ]; then
  echo "重建 venv（使用 miniconda Python + OpenSSL 3.0）..."
  rm -rf venv
  $PYTHON_BIN -m venv venv
  venv/bin/pip install -q -r requirements.txt
  echo "✓ venv 已重建，OpenSSL 版本：$(venv/bin/python -c 'import ssl; print(ssl.OPENSSL_VERSION)')"
fi

# ────────────────────────────────────────────────────────────────────────────
# 启动后端（保留代理环境变量，Claude CLI SSE 认证需要它；MinerU 通过 session.proxies={} 单独绕过代理）
# ────────────────────────────────────────────────────────────────────────────
source venv/bin/activate
uvicorn main:app --reload --port 8000 &
BACKEND_PID=$!

# ────────────────────────────────────────────────────────────────────────────
# 启动前端
# ────────────────────────────────────────────────────────────────────────────
cd "$ROOT/frontend"
npm run dev &
FRONTEND_PID=$!

echo "✓ Backend PID: $BACKEND_PID (http://localhost:8000)"
echo "✓ Frontend PID: $FRONTEND_PID (http://localhost:5173)"
echo "Press Ctrl+C to stop both."

trap "kill $BACKEND_PID $FRONTEND_PID 2>/dev/null; exit 0" INT TERM
wait
