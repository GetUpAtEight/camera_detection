#!/usr/bin/env bash
# ============================================================================
#  Camera Tampering Detection - one-click start (Linux / macOS)
#  1) algo     FastAPI     http://127.0.0.1:8000     （本地模式）
#     bridge   SSH 隧道    http://127.0.0.1:18010    （--gpu 模式，算法层在服务器）
#  2) backend  SpringBoot  http://127.0.0.1:8080
#  3) frontend Vite dev    http://127.0.0.1:5173
#  Usage: ./start_all.sh [--no-yolo] [--gpu]   |   Ctrl+C stops all three
#         --gpu  登录 GPU 服务器、部署算法层并常驻隧道（要求后端 base-url 指向 18010）
# ============================================================================
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ALGO_DIR="$ROOT/algo"
BACK_DIR="$ROOT/backend"
FRONT_DIR="$ROOT/frontend"
GPU_MODE=0
YOLO_FLAG=""
for arg in "$@"; do
  case "$arg" in
    --gpu|--remote) GPU_MODE=1 ;;
    *) YOLO_FLAG="$arg" ;;
  esac
done

PIDS=()
cleanup() {
  echo ""
  echo "[stop] shutting down services ..."
  for pid in "${PIDS[@]:-}"; do
    kill "$pid" 2>/dev/null || true
  done
  wait 2>/dev/null || true
  echo "[stop] done"
}
trap cleanup EXIT INT TERM

# ---------------------------------------------------------------- algo layer
if [ "$GPU_MODE" = "1" ]; then
  echo "[1/3] gpu bridge (deploy + tunnel) ..."
  if [ ! -x "$ALGO_DIR/.venv/bin/python" ]; then
    echo "      creating .venv ..."
    python3 -m venv "$ALGO_DIR/.venv"
    "$ALGO_DIR/.venv/bin/python" -m pip install -q -i https://pypi.org/simple -r "$ALGO_DIR/requirements.txt"
  fi
  # 一条 SSH 连接完成：上传代码 → 重启算法层 → 自测 → 常驻转发 18010 → 远端 8010
  ( cd "$ROOT" && exec "$ALGO_DIR/.venv/bin/python" -u deploy/remote.py --deploy --selftest --tunnel ) &
  PIDS+=($!)
  echo "      waiting for tunnel on 127.0.0.1:18010 ..."
  for i in $(seq 1 120); do
    if curl -fsS -m 2 http://127.0.0.1:18010/health >/dev/null 2>&1; then
      echo "      tunnel ready"
      break
    fi
    sleep 2
  done
else
  echo "[1/3] algo layer ..."
  if [ ! -x "$ALGO_DIR/.venv/bin/python" ]; then
    echo "      creating .venv ..."
    python3 -m venv "$ALGO_DIR/.venv"
    "$ALGO_DIR/.venv/bin/python" -m pip install -q -i https://pypi.org/simple -r "$ALGO_DIR/requirements.txt"
  fi
  ( cd "$ALGO_DIR" && exec .venv/bin/python server.py --host 127.0.0.1 --port 8000 $YOLO_FLAG ) &
  PIDS+=($!)
  sleep 3
fi

# ------------------------------------------------------------------- backend
echo "[2/3] backend ..."
( cd "$BACK_DIR" && exec mvn -DskipTests spring-boot:run -Dspring-boot.run.arguments=--server.port=8080 ) &
PIDS+=($!)
sleep 3

# ------------------------------------------------------------------ frontend
echo "[3/3] frontend ..."
if [ ! -d "$FRONT_DIR/node_modules" ]; then
  ( cd "$FRONT_DIR" && npm install )
fi
( cd "$FRONT_DIR" && exec npm run dev ) &
PIDS+=($!)

if [ "$GPU_MODE" = "1" ]; then
  ALGO_URL="http://127.0.0.1:18010（经 SSH 桥接到 GPU 服务器）"
else
  ALGO_URL="http://127.0.0.1:8000/health"
fi

cat <<EOF

All three services are starting (first run may take 1-2 minutes).

  algo     : $ALGO_URL
  backend  : http://127.0.0.1:8080/api/algo/health
  frontend : http://localhost:5173

Press Ctrl+C to stop all services.
EOF

wait
