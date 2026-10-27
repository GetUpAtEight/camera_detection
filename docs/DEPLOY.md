# 部署与运维

本系统只有一条链路：**前端 → SpringBoot（透传）→ Python 算法层（低频巡检）**。
算法层是纯 CPU 服务，不依赖 GPU、不依赖深度学习模型。

---

## 1. 本地部署（默认，推荐）

### 1.1 依赖

| 组件 | 版本 | 说明 |
|---|---|---|
| JDK | 17+ | SpringBoot 3 |
| Maven | 3.8+ | 后端构建 |
| Node.js | 18+ | 前端 |
| Python | 3.10+ | 算法层 |

### 1.2 安装

```bash
cd algo
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt      # Windows
# .venv/bin/pip install -r requirements.txt        # Linux / macOS

cd ../frontend && npm install
```

> PyPI 直连不稳定时用镜像：`-i https://mirrors.aliyun.com/pypi/simple`

### 1.3 启动

```bash
./start_all.sh        # Linux / macOS
start_all.bat         # Windows
```

脚本会：起算法层（8000）→ 起后端（8080）→ 起前端（5173），并自动把后端指向 `127.0.0.1:8000`。

### 1.4 手动启动

```bash
# ① 算法层
cd algo && .venv/bin/python server.py --host 127.0.0.1 --port 8000

# ② 后端（环境变量指定算法层地址）
cd backend
APP_ALGO_BASEURL=http://127.0.0.1:8000 \
  mvn -DskipTests spring-boot:run -Dspring-boot.run.arguments=--server.port=8080

# ③ 前端（如需改后端端口）
cd frontend
VITE_API_TARGET=http://127.0.0.1:8080 npm run dev
```

### 1.5 数据位置

机位基线、参照物、模板库、审计全部落在算法层的
`algo/data/sparse/`（可用 `--data-dir` 指定别的目录）：

```
algo/data/sparse/
├── cameras.json          机位索引
├── <cameraId>.json       元数据 + 判定历史 + 哈希 + 模板元数据 + 审计
└── <cameraId>.npz        缩略图 + 参照物模板 + 模板库 + 基线参考帧
```

单机位约 3 MB。备份/迁移直接拷这个目录即可。

---

## 2. 服务器部署（可选）

适合多人共享或算力集中。算法层跑在远端，本地通过 **SSH 隧道**访问。

### 2.1 为什么需要隧道

远端算法层只监听 `127.0.0.1`（安全），且 sshd 可能关闭了 TCP 转发，
所以 `ssh -L` 不一定可用。本系统用 **SSH exec 通道**跑 `bridge.py`：

```
本地隧道进程 → SSH exec 通道 → 远端 python bridge.py 8010
                                  ├ 连接 127.0.0.1:8010
                                  └ 与 stdin/stdout 对接
```

一个 HTTP 请求对应一个 exec 通道，SSH 连接只建立一次，不反复认证。

### 2.2 配置连接

```bash
cp deploy/.env.local.example deploy/.env.local
```

```ini
CTD_SSH_HOST=<服务器 IP>
CTD_SSH_USER=root
CTD_SSH_KEY=~/.ssh/id_ed25519
# CTD_SSH_PASSWORD=<只有没有密钥时才用>
```

`deploy/.env.local` 已在 `.gitignore` 中，不会提交。

### 2.3 部署与启动

```bash
python deploy/remote.py --deploy --selftest --tunnel
```

| 参数 | 作用 |
|---|---|
| `--deploy` | 打包 `algo/` 上传、（按 cwd 判定后）停旧进程、挑空闲端口（8010~8100）启动、健康检查 |
| `--selftest` | 跑内置自测：健康检查 → 参数字典 → 两次抽样 → 机位状态 → 清理 |
| `--tunnel` | 常驻隧道，把远端口映射到本地 `18010` |
| `--info` | 只读查看远端环境（不改变任何东西） |

启动后本地 `http://127.0.0.1:18010/health` 应返回 `service=sparse-inspection`。

### 2.4 一键启动

```bash
start_all.bat --gpu        # Windows
./start_all.sh --gpu       # Linux / macOS
```

脚本会在后台跑 `remote.py --deploy --selftest --tunnel`，并等待 `18010` 就绪，
然后起后端（`APP_ALGO_BASEURL=http://127.0.0.1:18010`）与前端。

### 2.5 远端运行时文件

```
<CTD_REMOTE_ROOT>/ctd_service/
├── algo/                上传的算法层代码
├── data/sparse/         机位基线（与本地同结构）
├── bridge.py            隧道桥接脚本
├── ctd.pid              进程号
├── port.txt             实际端口
└── service.log          服务日志
```

---

## 3. 配置项

### 3.1 后端（`application.yml`）

| 配置 | 默认 | 说明 |
|---|---|---|
| `server.port` | 8080 | 后端端口 |
| `spring.servlet.multipart.max-file-size` | 16MB | 单帧上限 |
| `spring.servlet.multipart.max-request-size` | 32MB | 一次抽样总上限 |
| `app.algo.base-url` | `http://127.0.0.1:8000` | 算法层地址（可被 `APP_ALGO_BASEURL` 覆盖） |
| `app.algo.connect-timeout-ms` | 5000 | 连接超时 |
| `app.algo.read-timeout-ms` | 120000 | 读超时（一次抽样要跑完整判定） |

### 3.2 算法层（环境变量）

| 变量 | 默认 | 说明 |
|---|---|---|
| `ALGO_HOST` / `ALGO_PORT` | `127.0.0.1` / `8000` | 监听地址（`--host/--port` 亦可） |
| `ALGO_SPARSE_DIR` | `algo/data/sparse` | 机位数据目录（`--data-dir` 亦可） |
| `ALGO_CV_THREADS` | 1 | OpenCV 线程数（多机位并发时建议 1） |
| `ALGO_MAX_UPLOAD_MB` | 32 | 单帧上限 |
| `ALGO_CORS_ORIGINS` | `http://localhost:5173,http://127.0.0.1:5173` | 允许的浏览器来源 |

### 3.3 前端

| 变量 | 默认 | 说明 |
|---|---|---|
| `VITE_API_TARGET` | `http://127.0.0.1:8080` | Vite 代理的后端地址 |

---

## 4. 运维

### 4.1 健康检查

```bash
curl -s http://127.0.0.1:8000/health     # 本地算法层
curl -s http://127.0.0.1:18010/health    # 隧道（服务器模式）
curl -s http://127.0.0.1:8080/api/algo/health
```

### 4.2 常见故障

| 现象 | 原因 | 处理 |
|---|---|---|
| 页面「算法服务离线」 | 算法层没起 / 地址不对 / 隧道断 | 先 `curl` 算法层 `/health` |
| 隧道日志刷「认证被拒」 | 服务器认证频率限制 | **等 1~2 分钟**再试，改用密钥认证 |
| 启动报 `Port 8080 already in use` | 端口被占用 | 换端口 + `VITE_API_TARGET` 指过去 |
| Windows 绑 8080 报 `WinError 10013` | 系统端口保留（Hyper-V/WSL） | 换端口；或管理员 `net stop winnat` → 起服务 → `net start winnat` |
| 一直「基线建立中」 | 同槽样本不足 3 | 正常，需 3 天 |
| 正常画面被判「持续遮挡」 | 换了机位没重设基准 | 点「重设基准」（保留参照物） |

### 4.3 数据维护

- 备份：拷贝 `algo/data/sparse/`（或远端同名目录）；
- 删除机位：`DELETE /api/sparse/state/{cameraId}`，会一并删除基线、参照物、模板与审计；
- 重设基准：`POST /api/sparse/state/{cameraId}/reset`，只清基线，**保留参照物与模板库**。

### 4.4 安全

- 本系统**默认无鉴权**，定位为演示/内网系统；暴露到公网前必须自行加认证与 HTTPS；
- 后端已移除数据库与控制台（无 H2 console）；
- CORS 只放行 `localhost` / `127.0.0.1`；
- 服务器地址与密码只放在 `deploy/.env.local`，不进仓库。
