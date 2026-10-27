"""远端算法层：一条 SSH 连接里完成 部署 + 常驻端口转发。

为什么合并成一个脚本：服务器对重复认证有频率限制（连续登录会失败，需等 1~2 分钟）。
本脚本只在启动时登录**一次**，随后所有远端命令（部署、重启、健康检查）与本地端口转发
都复用这条连接，不再产生新的认证。

用法::

    python deploy/remote.py --deploy --tunnel     # 部署并常驻转发（演示用）
    python deploy/remote.py --info                # 只看环境
    python deploy/remote.py --tunnel              # 只转发（服务已在跑）

转发：``127.0.0.1:18010`` → 远端 ``127.0.0.1:8010``。

安全边界：只读写 ``/root/yzn_model_api/ctd_service``，不碰同目录下别人的文件与进程。
"""

from __future__ import annotations

import argparse
import os
import posixpath
import select
import socket
import sys
import tarfile
import tempfile
import threading
import time

import paramiko

# --------------------------------------------------------------------------- 连接配置
# 安全约定：**不要把服务器地址 / 账号 / 密码提交到仓库**（本仓库是公开的）。
# 配置优先级（高 → 低）：
#   1) 环境变量  CTD_SSH_HOST / CTD_SSH_USER / CTD_SSH_PASSWORD / CTD_SSH_KEY / CTD_REMOTE_ROOT
#   2) 本目录的 deploy/.env.local（已被 .gitignore 忽略，适合本机长期使用）
#   3) 内置默认值（仅远程目录与账号名有默认值，**地址与密码必须自己提供**）
#
# 认证优先使用 SSH 密钥（默认 ~/.ssh/id_ed25519，可用 CTD_SSH_KEY 指定）；
# 没有可用密钥时才回退到密码。
def _load_local_env() -> None:
    """加载 deploy/.env.local（若存在）。格式：KEY=VALUE，支持 # 注释。"""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env.local")
    if not os.path.exists(path):
        return
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for raw in fh:
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip())
    except OSError:
        pass


_load_local_env()

HOST = os.environ.get("CTD_SSH_HOST", "").strip()
USER = os.environ.get("CTD_SSH_USER", "root").strip()
PASSWORD = os.environ.get("CTD_SSH_PASSWORD", "").strip()
KEY_PATH = os.environ.get("CTD_SSH_KEY", "").strip() or os.path.expanduser("~/.ssh/id_ed25519")
REMOTE_ROOT = os.environ.get("CTD_REMOTE_ROOT", "/root/yzn_model_api").strip()
SERVICE_DIR = posixpath.join(REMOTE_ROOT, "ctd_service")
LOCAL_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = f"{REMOTE_ROOT}/.venv/bin/python"

BANNER = "=" * 72


# --------------------------------------------------------------------------- 远端脚本
INFO_SCRIPT = rf"""
cd {REMOTE_ROOT} || {{ echo "目录不存在"; exit 1; }}
echo "--- 时间/内核/CPU/内存 ---"
date '+%F %T %Z'; uname -sr; echo "cores=$(nproc)"; free -g | sed -n '1,2p'
echo "--- GPU ---"
(nvidia-smi --query-gpu=index,name,memory.total,memory.used --format=csv,noheader 2>/dev/null \
  || echo "无 nvidia-smi")
echo "--- 本目录（只读）---"
ls -la | sed -n '1,30p'
echo "--- 监听端口（只读）---"
(ss -lntp 2>/dev/null || netstat -lntp 2>/dev/null) | sed -n '1,25p'
echo "--- 本服务状态 ---"
if [ -f {SERVICE_DIR}/ctd.pid ]; then
  PID=$(cat {SERVICE_DIR}/ctd.pid)
  if [ -d "/proc/$PID" ]; then echo "  运行中 pid=$PID 端口=$(cat {SERVICE_DIR}/port.txt 2>/dev/null)"; else echo "  pid 文件残留（进程已退出）"; fi
else
  echo "  未部署"
fi
echo "--- 解释器与依赖 ---"
{PY} -V 2>&1
{PY} - <<'PYEOF'
import importlib
for m in ("fastapi", "uvicorn", "multipart", "cv2", "numpy", "torch", "ultralytics"):
    try:
        mod = importlib.import_module(m)
        print(f"  {{m:12s}} {{getattr(mod, '__version__', '?')}}")
    except Exception as e:
        print(f"  {{m:12s}} MISSING ({{type(e).__name__}})")
PYEOF
echo "--- 低频巡检数据目录 ---"
ls -lh {SERVICE_DIR}/algo/data/sparse 2>/dev/null | tail -5 || echo "  （尚无数据，首次抽样后生成）"
"""

BRIDGE_BODY = r"""
import os, socket, sys, threading

# stdin/stdout 与一个 TCP 连接对接：sshd 只允许 session 通道（AllowTcpForwarding no），
# 所以用 exec 通道跑这个脚本，把本地 HTTP 字节流"搬"到 127.0.0.1:<port>。
port = int(sys.argv[1])
sock = socket.create_connection(("127.0.0.1", port))
sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)


def upstream():
    try:
        while True:
            # 注意用 os.read：buffered read(n) 会一直等到凑满 n 字节，请求会卡在建连阶段
            data = os.read(0, 65536)
            if not data:
                break
            sock.sendall(data)
    except Exception:
        pass
    finally:
        try:
            sock.shutdown(socket.SHUT_WR)
        except Exception:
            pass


threading.Thread(target=upstream, daemon=True).start()
try:
    while True:
        chunk = sock.recv(65536)
        if not chunk:
            break
        sys.stdout.buffer.write(chunk)
        sys.stdout.buffer.flush()
except Exception:
    pass
"""

CLEANUP_SCRIPT = rf"""
SVC={SERVICE_DIR}
for p in $(ls /proc 2>/dev/null | grep -E '^[0-9]+$'); do
  cwd=$(readlink "/proc/$p/cwd" 2>/dev/null || true)
  case "$cwd" in
    "$SVC"*)
      cmd=$(tr '\0' ' ' < "/proc/$p/cmdline" 2>/dev/null || true)
      case "$cmd" in
        *server.py*) echo "  kill $p ($cmd)"; kill "$p" 2>/dev/null ;;
      esac
      ;;
  esac
done
sleep 2
(ss -lntp 2>/dev/null || netstat -lntp) | grep -E ':(80[0-9][0-9])' || echo "  80xx 端口已全部释放"
"""

BRIDGE_SCRIPT = BRIDGE_BODY

DEPLOY_SCRIPT = rf"""
set -u
ROOT={REMOTE_ROOT}
SVC={SERVICE_DIR}
PY={PY}
cd "$ROOT" || exit 1

echo "--- 1) 解包 ---"
mkdir -p "$SVC"
tar -xzf "$SVC/ctd_algo.tar.gz" -C "$SVC"
echo "  文件数 $(find "$SVC/algo" -type f | wc -l)"

echo "--- 1b) 写桥接脚本 ---"
cat > "$SVC/bridge.py" <<'BRIDGEEOF'
{BRIDGE_BODY}
BRIDGEEOF
echo "  $SVC/bridge.py $(wc -l < "$SVC/bridge.py") 行"

echo "--- 2) 依赖 ---"
if ! $PY -c "import multipart" >/dev/null 2>&1; then
  echo "  python-multipart 缺失 → 装到私有目录 pylibs"
  $PY -m pip install -q --target "$SVC/pylibs" python-multipart -i https://pypi.org/simple 2>&1 | tail -2
else
  echo "  python-multipart 已存在（用共享 venv，不改动它）"
fi

echo "--- 3) 停旧版本（按 cwd 判定，只动本服务）---"
if [ -f "$SVC/ctd.pid" ]; then
  OLD=$(cat "$SVC/ctd.pid" 2>/dev/null || true)
  if [ -n "${{OLD:-}}" ] && [ -d "/proc/$OLD" ]; then
    CMD=$(tr '\0' ' ' < "/proc/$OLD/cmdline" 2>/dev/null || true)
    case "$CMD" in
      *ctd_service*) kill "$OLD" 2>/dev/null && echo "  停止旧进程 $OLD"; sleep 2 ;;
      *) echo "  pid 文件指向别的进程，忽略" ;;
    esac
  fi
fi
for p in $(ls /proc 2>/dev/null | grep -E '^[0-9]+$'); do
  cwd=$(readlink "/proc/$p/cwd" 2>/dev/null || true)
  case "$cwd" in
    "$SVC"*)
      cmd=$(tr '\0' ' ' < "/proc/$p/cmdline" 2>/dev/null || true)
      case "$cmd" in
        *server.py*) echo "  停止 $p（$cmd）"; kill "$p" 2>/dev/null ;;
      esac
      ;;
  esac
done
sleep 1

echo "--- 4) 端口 ---"
PORT=$($PY - <<'PYEOF'
import socket
for p in range(8010, 8100):
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", p)); print(p); break
    except OSError:
        continue
    finally:
        s.close()
PYEOF
)
[ -z "${{PORT:-}}" ] && {{ echo "无空闲端口"; exit 3; }}
echo "  使用 $PORT"

echo "--- 5) 启动 ---"
cd "$SVC/algo" || exit 1
export PYTHONPATH="$SVC/pylibs"
export OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1
setsid nohup "$PY" server.py --host 127.0.0.1 --port "$PORT" \
    > "$SVC/service.log" 2>&1 &
echo $! > "$SVC/ctd.pid"
echo "  pid=$(cat "$SVC/ctd.pid")"

echo "--- 6) 就绪检查 ---"
OK=0
for i in $(seq 1 40); do
  if curl -s -m 2 "http://127.0.0.1:$PORT/health" > "$SVC/health.json"; then OK=1; break; fi
  sleep 1
done
if [ "$OK" != "1" ]; then echo "!! 启动失败"; tail -30 "$SVC/service.log"; exit 4; fi
echo "PORT=$PORT" > "$SVC/port.txt"
echo "  $(cat "$SVC/health.json")"
"""

# 演示/自测：低频巡检全链路（健康检查 → 参数字典 → 两次抽样 → 机位状态 → 清理）
SELFTEST_SCRIPT = r"""
SVC=__SVC__
PY=__PY__
PORT=$(cat "$SVC/port.txt" | cut -d= -f2)
echo "port=$PORT"

echo "--- 1) 健康检查 ---"
curl -s -m 10 "http://127.0.0.1:$PORT/health" | $PY -c "import sys,json;d=json.load(sys.stdin);print('  version=%s service=%s cameras=%s cv2=%s' % (d.get('version'), d.get('service'), d.get('cameras'), d.get('cv2')))"

echo "--- 2) 参数 / 判决字典 ---"
curl -s -m 10 "http://127.0.0.1:$PORT/sparse/meta" | $PY -c "import sys,json;d=json.load(sys.stdin);print('  params=%d verdicts=%d' % (len(d.get('params') or []), len(d.get('verdicts') or [])))"

echo "--- 3) 生成测试帧 ---"
$PY - <<'PYEOF'
import cv2, numpy as np, os
out = "/tmp/ctd_selftest"
os.makedirs(out, exist_ok=True)
h, w = 480, 640
img = np.full((h, w, 3), 60, np.uint8)
cv2.rectangle(img, (40, 60), (300, 320), (200, 180, 120), -1)
cv2.rectangle(img, (380, 120), (600, 260), (120, 200, 180), -1)
for i in range(6):
    cv2.line(img, (0, 80 * i), (w, 80 * i + 40), (90, 90, 90), 2)
for k in range(6):
    rng = np.random.default_rng(100 + k)
    n = np.clip(img.astype(np.float32) + rng.normal(0, 2.5, img.shape), 0, 255).astype(np.uint8)
    cv2.imwrite(os.path.join(out, "f%d.jpg" % k), n, [int(cv2.IMWRITE_JPEG_QUALITY), 92])
print("  frames ->", out)
PYEOF

echo "--- 4) 第 1 次抽样（期望 cold_start）---"
curl -s -m 60 -X POST "http://127.0.0.1:$PORT/sparse/sample" \
  -F "cameraId=selftest" -F "name=自检" -F "tsMs=1700000000000" \
  -F "frames=@/tmp/ctd_selftest/f0.jpg" \
  -F "frames=@/tmp/ctd_selftest/f1.jpg" \
  -F "frames=@/tmp/ctd_selftest/f2.jpg" \
  | $PY -c "import sys,json;d=json.load(sys.stdin);print('  verdict=%s label=%s total=%sms' % (d.get('verdict'), d.get('verdictLabel'), (d.get('timingsMs') or {}).get('total')))"

echo "--- 5) 第 2 次抽样（1 小时后，期望 normal）---"
curl -s -m 60 -X POST "http://127.0.0.1:$PORT/sparse/sample" \
  -F "cameraId=selftest" -F "name=自检" -F "tsMs=1700003600000" \
  -F "frames=@/tmp/ctd_selftest/f3.jpg" \
  -F "frames=@/tmp/ctd_selftest/f4.jpg" \
  -F "frames=@/tmp/ctd_selftest/f5.jpg" \
  | $PY -c "import sys,json;d=json.load(sys.stdin);print('  verdict=%s label=%s dHist=%s' % (d.get('verdict'), d.get('verdictLabel'), ((d.get('layers') or {}).get('L0') or {}).get('dHist')))"

echo "--- 6) 机位状态 ---"
curl -s -m 10 "http://127.0.0.1:$PORT/sparse/state/selftest" | $PY -c "import sys,json;d=json.load(sys.stdin);print('  status=%s samples=%s slots=%d' % (d.get('status'), d.get('samples'), len(d.get('slots') or [])))"

echo "--- 7) 清理自检机位 ---"
curl -s -m 10 -X DELETE "http://127.0.0.1:$PORT/sparse/state/selftest" | $PY -c "import sys,json;print('  deleted=%s' % json.load(sys.stdin).get('deleted'))"
""".replace("__SVC__", SERVICE_DIR).replace("__PY__", PY)

RESTART_SCRIPT = rf"""
SVC={SERVICE_DIR}
PY={PY}
cd "$SVC/algo" || exit 5
PORT=$(cat "$SVC/port.txt" 2>/dev/null | cut -d= -f2)
PORT=${{PORT:-8010}}
export PYTHONPATH="$SVC/pylibs"
export OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1
setsid nohup "$PY" server.py --host 127.0.0.1 --port "$PORT" \
    >> "$SVC/service.log" 2>&1 &
echo $! > "$SVC/ctd.pid"
for i in $(seq 1 30); do
  curl -s -m 2 "http://127.0.0.1:$PORT/health" && break
  sleep 1
done
"""


# --------------------------------------------------------------------------- 连接
class Remote:
    def __init__(self) -> None:
        self.client: paramiko.SSHClient | None = None
        self.lock = threading.Lock()
        self.remote_port = 8010

    def connect(self, max_wait: int = 300) -> None:
        if not HOST:
            raise SystemExit(
                "未配置服务器地址。请任选一种方式：\n"
                "  a) 设置环境变量：CTD_SSH_HOST=<服务器IP>（可选 CTD_SSH_USER / CTD_SSH_KEY）\n"
                "  b) 复制 deploy/.env.local.example 为 deploy/.env.local 并填写\n"
            )
        deadline = time.time() + max_wait
        backoff = 3
        while True:
            try:
                client = paramiko.SSHClient()
                client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                kwargs = dict(hostname=HOST, port=22, username=USER, timeout=30,
                              banner_timeout=60, auth_timeout=60,
                              allow_agent=False, look_for_keys=False)
                if KEY_PATH and os.path.exists(KEY_PATH):
                    # 优先密钥认证：比密码更安全，也不会触发服务器的认证频率限制
                    kwargs["key_filename"] = KEY_PATH
                else:
                    kwargs["password"] = PASSWORD
                client.connect(**kwargs)
                client.get_transport().set_keepalive(20)
                self.client = client
                print(f"[ssh] 已连接 {USER}@{HOST}", flush=True)
                return
            except paramiko.AuthenticationException:
                msg = (f"[ssh] 认证被拒（服务器限流/需要间隔）{backoff}s 后重试")
            except Exception as exc:  # noqa: BLE001
                msg = f"[ssh] 连接失败 {type(exc).__name__}: {exc}；{backoff}s 后重试"
            if time.time() > deadline:
                raise SystemExit(msg + " —— 超过最大等待时间，请稍后再试")
            print(msg, flush=True)
            time.sleep(backoff)
            backoff = min(int(backoff * 1.6) + 1, 45)

    def exec(self, script: str, label: str = "", timeout: int = 1800) -> int:
        if label:
            print(f"\n{BANNER}\n{label}\n{BANNER}", flush=True)
        tr = self.ensure()
        chan = tr.open_session(timeout=30)
        chan.settimeout(timeout)
        chan.exec_command("bash -s")
        chan.sendall(script.encode())
        chan.shutdown_write()
        buf = b""
        while True:
            try:
                data = chan.recv(65536)
            except socket.timeout:
                continue
            if not data:
                break
            buf += data
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                print(line.decode("utf-8", "replace"), flush=True)
        if buf.strip():
            print(buf.decode("utf-8", "replace"), flush=True)
        return chan.recv_exit_status()

    def ensure(self) -> paramiko.Transport:
        tr = self.client.get_transport() if self.client else None
        if tr is not None and tr.is_active():
            return tr
        print("[ssh] 连接已断，重新建立（会等待认证）…", flush=True)
        with self.lock:
            tr = self.client.get_transport() if self.client else None
            if tr is not None and tr.is_active():
                return tr
            try:
                self.client.close()
            except Exception:
                pass
            self.connect()
        return self.client.get_transport()

    # ---------------------------------------------------------------- 转发
    def write_bridge(self) -> None:
        """把桥接脚本写到远端（幂等），内容随仓库更新。"""
        tr = self.ensure()
        chan = tr.open_session(timeout=30)
        chan.exec_command(f"cat > {SERVICE_DIR}/bridge.py")
        chan.sendall(BRIDGE_SCRIPT.encode())
        chan.shutdown_write()
        chan.recv_exit_status()
        chan.close()

    def serve_tunnel(self) -> None:
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind((LOCAL_LISTEN, LOCAL_PORT))
        srv.listen(64)
        print(f"[tunnel] 监听 http://{LOCAL_LISTEN}:{LOCAL_PORT} → 远端 127.0.0.1:{self.remote_port}",
              flush=True)
        threading.Thread(target=self.watchdog, daemon=True).start()
        while True:
            try:
                sock, addr = srv.accept()
            except OSError:
                return
            threading.Thread(target=self._handle, args=(sock, addr), daemon=True).start()

    def _handle(self, sock: socket.socket, addr) -> None:
        try:
            tr = self.ensure()
            # 服务端禁用了 TCP 转发，只能用 session 通道跑远端桥接脚本
            chan = tr.open_session(timeout=20)
            chan.exec_command(f"{PY} -u {SERVICE_DIR}/bridge.py {self.remote_port}")
        except Exception as exc:  # noqa: BLE001
            print(f"[bridge] 通道建立失败：{type(exc).__name__}: {exc}", flush=True)
            try:
                sock.close()
            except Exception:
                pass
            return
        try:
            while True:
                r, _, _ = select.select([sock, chan], [], [], 1.0)
                if sock in r:
                    data = sock.recv(65536)
                    if not data:
                        break
                    chan.sendall(data)
                if chan in r:
                    data = chan.recv(65536)
                    if not data:
                        break
                    sock.sendall(data)
        except (OSError, EOFError):
            pass
        finally:
            try:
                chan.close()
            except Exception:
                pass
            try:
                sock.close()
            except Exception:
                pass

    def watchdog(self) -> None:
        """每 20s 探一次远端服务；挂了就在同一条连接里拉起来。"""
        while True:
            time.sleep(20)
            try:
                out = self.quick(f"curl -s -m 4 http://127.0.0.1:{self.remote_port}/health")
                if '"status":"ok"' in out:
                    continue
                print("[watchdog] 远端算法层无响应，尝试重启", flush=True)
                self.exec(RESTART_SCRIPT, "watchdog 重启")
            except Exception as exc:  # noqa: BLE001
                print(f"[watchdog] 检查异常：{type(exc).__name__}: {exc}", flush=True)

    def quick(self, cmd: str, timeout: int = 20) -> str:
        tr = self.ensure()
        chan = tr.open_session(timeout=timeout)
        chan.settimeout(timeout)
        chan.exec_command(cmd)
        out = chan.makefile("rb").read().decode("utf-8", "replace")
        chan.close()
        return out


LOCAL_LISTEN = "127.0.0.1"
LOCAL_PORT = 18010


# --------------------------------------------------------------------------- 打包
def build_tarball() -> str:
    target = os.path.join(tempfile.gettempdir(), "ctd_algo.tar.gz")
    with tarfile.open(target, "w:gz") as tar:
        for name in sorted(os.listdir(os.path.join(LOCAL_ROOT, "algo"))):
            if name in {".venv", "__pycache__", "_tmp", ".pytest_cache"}:
                continue
            tar.add(os.path.join(LOCAL_ROOT, "algo", name),
                    arcname=posixpath.join("algo", name), filter=_skip_junk)
    return target


def _skip_junk(info: tarfile.TarInfo) -> tarfile.TarInfo | None:
    base = posixpath.basename(info.name)
    if base in {"__pycache__", ".venv", "_out"} or base.endswith(".pyc"):
        return None
    return info


def main() -> int:
    parser = argparse.ArgumentParser(description="远端算法层部署 / 隧道")
    parser.add_argument("--info", action="store_true")
    parser.add_argument("--deploy", action="store_true")
    parser.add_argument("--selftest", action="store_true")
    parser.add_argument("--tunnel", action="store_true")
    parser.add_argument("--cleanup", action="store_true", help="停掉本服务目录下的历史进程")
    parser.add_argument("--local-port", type=int, default=18010)
    args = parser.parse_args()

    global LOCAL_PORT
    LOCAL_PORT = args.local_port

    tar_path = build_tarball() if args.deploy else None
    remote = Remote()
    remote.connect()

    if args.info or args.deploy or args.cleanup:
        remote.exec(INFO_SCRIPT, "0) 远端环境（只读）")

    if args.cleanup:
        remote.exec(CLEANUP_SCRIPT, "1) 清理本服务历史进程")

    if args.deploy:
        assert tar_path
        remote.exec(f"mkdir -p {SERVICE_DIR}/samples", "1) 准备目录")
        sftp = remote.client.open_sftp()
        sftp.put(tar_path, posixpath.join(SERVICE_DIR, "ctd_algo.tar.gz"))
        for name in ("reference_normal.jpg", "target_covered.jpg", "demo_video.mp4"):
            local = os.path.join(LOCAL_ROOT, "samples", name)
            if os.path.exists(local):
                sftp.put(local, posixpath.join(SERVICE_DIR, "samples", name))
        sftp.close()
        print("上传完成", flush=True)

        rc = remote.exec(DEPLOY_SCRIPT, "2) 部署")
        if rc != 0:
            return rc

        port = remote.quick(f"cat {SERVICE_DIR}/port.txt")
        if "=" in port:
            remote.remote_port = int(port.strip().split("=")[-1])
        if args.selftest:
            remote.exec(SELFTEST_SCRIPT, "3) 自测")
        os.remove(tar_path) if os.path.exists(tar_path) else None

    if args.tunnel:
        if not args.deploy:
            port = remote.quick(f"cat {SERVICE_DIR}/port.txt").strip()
            if "=" in port:
                remote.remote_port = int(port.split("=")[-1])
                print(f"[bridge] 远端服务端口 {remote.remote_port}", flush=True)
        remote.write_bridge()
        print("\n[bridge] 桥接脚本就绪，进入常驻转发（Ctrl+C 结束）", flush=True)
        try:
            remote.serve_tunnel()
        except KeyboardInterrupt:
            print("\n[tunnel] 已停止", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
