#!/usr/bin/env bash
# ============================================================================
#  Camera Tampering Detection - one-click start (Linux / macOS)
#  1) algo     FastAPI     http://127.0.0.1:8000     （本地模式）
#     bridge   SSH 隧道    http://127.0.0.1:18010    （--gpu 模式，算法层在服务器）
#  2) backend  SpringBoot  http://127.0.0.1:8080
#  3) frontend Vite dev    http://127.0.0.1:5173
#  Usage: ./start_all.sh [--gpu]   |   Ctrl+C stops all three
#         --gpu  登录 GPU 服务器、部署算法层并常驻隧道（要求后端 base-url 指向 18010）
#
#  PyPI 源（首次创建 .venv 时生效，三个变量都遵循 pip 官方语义）：
#    PIP_INDEX_URL     包索引地址，默认国内镜像 https://mirrors.aliyun.com/pypi/simple
#    PIP_TRUSTED_HOST  额外受信主机，默认 mirrors.aliyun.com（免 HTTPS 证书校验问题）
#    PIP_INSTALL_ARGS  追加给 pip install 的额外参数
#  回退官方源（任选其一）：
#    PIP_INDEX_URL=https://pypi.org/simple ./start_all.sh
#    CTD_PIP_OFFICIAL=1 ./start_all.sh
# ============================================================================
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PIP_INDEX_URL_DEFAULT="https://mirrors.aliyun.com/pypi/simple"
PIP_TRUSTED_HOST_DEFAULT="mirrors.aliyun.com"
ALGO_DIR="$ROOT/algo"
BACK_DIR="$ROOT/backend"
FRONT_DIR="$ROOT/frontend"
GPU_MODE=0
for arg in "$@"; do
  case "$arg" in
    --gpu|--remote) GPU_MODE=1 ;;
  esac
done

# ------------------------------------------------------------- pip 镜像配置
# 默认走国内镜像；已显式导出 PIP_INDEX_URL 时不覆盖（尊重 pip 原生变量）。
export PIP_INDEX_URL="${PIP_INDEX_URL:-$PIP_INDEX_URL_DEFAULT}"
export PIP_TRUSTED_HOST="${PIP_TRUSTED_HOST:-$PIP_TRUSTED_HOST_DEFAULT}"
# 一键回退官方源
if [ "${CTD_PIP_OFFICIAL:-0}" = "1" ]; then
  export PIP_INDEX_URL="https://pypi.org/simple"
  unset PIP_TRUSTED_HOST
fi

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
    echo "      pip index: $PIP_INDEX_URL"
    "$ALGO_DIR/.venv/bin/python" -m pip install -q ${PIP_INSTALL_ARGS:-} -r "$ALGO_DIR/requirements.txt"
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
    echo "      pip index: $PIP_INDEX_URL"
    "$ALGO_DIR/.venv/bin/python" -m pip install -q ${PIP_INSTALL_ARGS:-} -r "$ALGO_DIR/requirements.txt"
  fi
  ( cd "$ALGO_DIR" && exec .venv/bin/python server.py --host 127.0.0.1 --port 8000 ) &
  PIDS+=($!)
  sleep 3
fi

# ------------------------------------------------------------------- backend
echo "[2/3] backend ..."
# 后端连哪个算法层：GPU 模式走隧道 18010，本地模式直连 8000。
# 用环境变量覆盖（Spring Boot 松散绑定 APP_ALGO_BASEURL → app.algo.base-url），
# 两种模式都不用手改 application.yml。
if [ "$GPU_MODE" = "1" ]; then
  export APP_ALGO_BASEURL="http://127.0.0.1:18010"
else
  export APP_ALGO_BASEURL="http://127.0.0.1:8000"
fi
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
