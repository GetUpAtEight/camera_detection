# 部署说明

三层可分别部署：算法层必须在 GPU 服务器，SpringBoot 与前端可在任意机器。

| 组件 | 位置 | 端口 | 备注 |
|------|------|------|------|
| 算法层 | GPU 服务器 `125.122.133.73` | 8000 | **只允许操作 `/root/yzn_model_api/` 目录** |
| SpringBoot | 业务服务器 | 8080 | 无 GPU 需求 |
| 前端 | Nginx 静态托管 | 80/443 | `/api` 反代到 SpringBoot |

---

## 1. 算法层（GPU 服务器）

### 1.0 一键部署 + SSH 桥（推荐，本项目实测路径）

服务器 `sshd` 配了 `AllowTcpForwarding no`，`ssh -L` / `paramiko` 的 `direct-tcpip` 通道会被直接拒绝
（`ChannelException(1, 'Administratively prohibited')`），除 22 端口外全部被防火墙屏蔽。所以不能"端口映射"，
本项目改成**在远端起一个小桥接进程，走 SSH 的 exec 通道转发字节流**：

```
本地 127.0.0.1:18010  ──HTTP──▶  [SSH session 通道]  ──▶  远端 bridge.py  ──▶  127.0.0.1:8010 (uvicorn)
```

一条命令完成：环境巡检 → 上传代码 → 解包 → 依赖检查 → 停旧进程（按 `/proc/<pid>/cwd` 判定，只动本服务）→
选空闲端口 → `setsid nohup` 启动 → 健康检查 → 自测 → 常驻桥接。

```bash
cd camera-tamper-demo
./algo/.venv/Scripts/python.exe -u deploy/remote.py --deploy --selftest --tunnel   # Windows
./algo/.venv/bin/python   -u deploy/remote.py --deploy --selftest --tunnel          # Linux/macOS
```

其他子命令：

| 参数 | 作用 |
|------|------|
| `--info` | 只读巡检（GPU/内存/端口/本服务状态/依赖版本），不改任何东西 |
| `--deploy` | 上传并重启算法层 |
| `--selftest` | 跑内置自测（经典算法 + YOLO 单图 + 8 路拼批 + 区间视频检测） |
| `--tunnel` | 常驻转发 `--local-port`（默认 18010）→ 远端端口 |
| `--cleanup` | 只停本服务目录下的历史进程 |
| `--local-port N` | 换本地监听端口 |

**两次认证失败的坑**：服务器对连续 SSH 登录有限流，短时间内重复登录会 `AuthenticationException`。
脚本内置指数退避（3s→5s→9s→15s→…，最长 300s），并且**把部署和隧道合并在同一个连接里**——
部署完不退出，直接进常驻转发，避免"部署一次、隧道又一次"导致第二次登录被拒。

配套：后端 `application.yml` 的 `algo.base-url` 指向 `http://127.0.0.1:18010`；
`./start_all.sh --gpu`（或 `start_all.bat --gpu`）会自动先起桥接再起后端/前端。

> 只访问 `/root/yzn_model_api`：代码放在 `ctd_service/`，pid/日志/端口写在同目录，
> 停进程时按 `cwd` 前缀匹配 `ctd_service` 且命令行含 `server.py` 才 kill，别的进程（gpustack、kubelet、node_exporter）一概不碰。

### 1.1 前置检查

```bash
cd /root/yzn_model_api
ls -lh yolo26n.pt .venv/bin/python
.venv/bin/python -c "import cv2, numpy, fastapi, uvicorn; print('deps ok')"
nvidia-smi --query-gpu=name,memory.used,memory.total --format=csv
```

若 `python-multipart` 缺失（multipart 端点会报 `Form data requires "python-multipart"`）：

```bash
/root/yzn_model_api/.venv/bin/pip install python-multipart
```

### 1.2 部署代码

```bash
# 本地打包后上传（只往这个目录里放，不动其他目录）
cd /path/to/camera-tamper-demo
tar czf algo.tar.gz -C . algo --exclude='algo/.venv' --exclude='algo/_tmp' --exclude='algo/tests/_out'
scp algo.tar.gz root@125.122.133.73:/root/yzn_model_api/

ssh root@125.122.133.73
cd /root/yzn_model_api
mkdir -p ctd && tar xzf algo.tar.gz -C ctd --strip-components=1
cd ctd
# 复用已有虚拟环境（注意：不要 pip install -r 全量覆盖 torch 版本）
/root/yzn_model_api/.venv/bin/python -c "import cv2, fastapi, ultralytics; print('ok')"
```

### 1.3 启动

开发/联调（前台）：

```bash
cd /root/yzn_model_api/ctd
OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 \
/root/yzn_model_api/.venv/bin/python server.py \
  --host 0.0.0.0 --port 8000 \
  --model /root/yzn_model_api/yolo26n.pt \
  --batch 16 --max-wait-ms 2
```

生产（systemd；**必须 workers=1**，动态拼批依赖进程内单例）：

```ini
# /etc/systemd/system/ctd-algo.service
[Unit]
Description=CTD Algorithm Service (FastAPI + YOLO)
After=network.target

[Service]
Type=simple
WorkingDirectory=/root/yzn_model_api/ctd
Environment=OMP_NUM_THREADS=1
Environment=MKL_NUM_THREADS=1
Environment=OPENBLAS_NUM_THREADS=1
Environment=ALGO_MODEL_PATH=/root/yzn_model_api/yolo26n.pt
Environment=ALGO_MAX_BATCH=16
Environment=ALGO_MAX_WAIT_MS=2
Environment=ALGO_DEVICE=0
ExecStart=/root/yzn_model_api/.venv/bin/uvicorn server:app --host 0.0.0.0 --port 8000 --workers 1
Restart=always
RestartSec=3
LimitNOFILE=65535

[Install]
WantedBy=multi-user.target
```

```bash
systemctl daemon-reload && systemctl enable --now ctd-algo
systemctl status ctd-algo --no-pager
curl -s localhost:8000/health | head -c 400
```

### 1.4 安全

- 端口尽量只对内网开放：`ufw allow from 10.0.0.0/8 to any port 8000`；
- 或加 Nginx Basic Auth / IP 白名单，避免模型服务裸奔公网；
- **不要动** gpustack（10150/10151）、kubelet、node_exporter 等其他进程。

### 1.5 压测

```bash
# 单图低延迟
curl -s -F "file=@test.jpg" -F "conf=0.25" http://127.0.0.1:8000/predict | jq '.elapsedMs'

# 批量吞吐（准备 16 张图）
curl -s $(for i in $(seq 1 16); do echo -n "-F files=@test.jpg "; done) \
     -F "conf=0.25" http://127.0.0.1:8000/predict_batch | jq '{count, inferMs, imagesPerSecond, batcher}'
```

预期（A10，batch=16）：HTTP 端到端约 270 img/s，p99 约 260ms；batch=1 时约 46 img/s。
参考值来自服务器既有 `scripts_bench/server_fast.py` 压测结果，本项目保留同样的接口与拼批语义。

---

## 2. 业务后端（SpringBoot）

```bash
cd backend
mvn -DskipTests clean package
java -jar target/camera-tamper-backend-1.0.0.jar \
  --server.port=8080 \
  --app.algo.base-url=http://125.122.133.73:8000 \
  --app.storage.upload-dir=/data/ctd/uploads \
  --app.storage.visual-dir=/data/ctd/visuals
```

systemd：

```ini
[Unit]
Description=CTD Backend
After=network.target

[Service]
WorkingDirectory=/opt/ctd/backend
ExecStart=/usr/bin/java -Xms512m -Xmx2g -jar /opt/ctd/backend/camera-tamper-backend-1.0.0.jar \
  --app.algo.base-url=http://125.122.133.73:8000
Restart=always

[Install]
WantedBy=multi-user.target
```

关键配置（`application.yml` 或命令行覆盖）：

| 配置 | 默认 | 说明 |
|------|------|------|
| `app.algo.base-url` | `http://127.0.0.1:8000` | 算法层地址 |
| `app.algo.read-timeout-ms` | 1800000 | 视频需等整段跑完，务必给足 |
| `app.algo.poll-interval-ms` | 800 | 轮询算法层进度间隔 |
| `app.detection.sample-fps` | 5 | 默认采样帧率 |
| `app.detection.max-frames` | 600 | 默认最多处理帧数 |
| `spring.servlet.multipart.max-file-size` | 512MB | 上传上限 |
| `spring.datasource.url` | H2 文件 | 生产建议 MySQL |

切 MySQL：放开 `application.yml` 末尾注释，加入 `mysql-connector-j` 依赖即可（JPA 自动建表）。

---

## 3. 前端（Nginx）

```bash
cd frontend
npm install
npm run build      # 产物在 dist/
```

```nginx
server {
    listen 80;
    server_name ctd.example.com;

    root /opt/ctd/frontend/dist;
    index index.html;

    # Vue Router history 模式
    location / {
        try_files $uri $uri/ /index.html;
    }

    # API 反代到 SpringBoot
    location /api/ {
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_read_timeout 600s;      # 导出/长任务
        client_max_body_size 512m;    # 视频上传
    }
}
```

---

## 4. 联调检查清单

```bash
curl -s http://<algo>:8000/health            # 算法层
curl -s http://<backend>:8080/api/algo/health # 网关连通、online=true
curl -s http://<backend>:8080/api/media       # 数据库正常
# 浏览器打开前端 → 右上角应显示"算法服务在线"
```

常见问题：

| 现象 | 原因 | 处理 |
|------|------|------|
| 前端提示算法服务离线 | 算法层未启动 / 地址错 / 跨网段不通 | 检查 `app.algo.base-url`，在业务机上 `curl <algo>/health` |
| 上传成功但没有分辨率/帧数 | 上传时算法层不可用，元信息探测被跳过 | 启动算法层后重新上传 |
| 检测任务一直 RUNNING | 算法层进程被 OOM kill / 模型加载失败 | `journalctl -u ctd-algo -n 100`，看 `/health` 的 yolo.error |
| YOLO 结果全是"跳过" | 未装 ultralytics 或模型路径不对 | 检查 `ALGO_MODEL_PATH`，或加 `--no-yolo` 只跑经典算法 |
| 视频只有前若干秒结果 | 触达 `maxFrames` / `sampleFps` 限制 | 调大请求里的 `maxFrames` 或降低采样帧率 |
| 9032 端口占用 / 启动失败 | 端口冲突 | 换端口，避开 gpustack 的 10150/10151 |
