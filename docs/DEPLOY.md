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

> 两个启动脚本**默认已经走阿里云镜像**（`PIP_INDEX_URL`），首次建 `.venv` 时会打印
> `pip index: …`。手动装依赖时同理加 `-i`，或先 `export PIP_INDEX_URL=…`：
>
> | 需求 | 做法 |
> |---|---|
> | 换镜像 | `PIP_INDEX_URL=<源> ./start_all.sh` |
> | 回退官方源 | `PIP_INDEX_URL=https://pypi.org/simple ./start_all.sh` 或 `CTD_PIP_OFFICIAL=1 ./start_all.sh` |
> | 内网私有源 | 同上，源地址换成内网地址；脚本不会覆盖你已设置的 `PIP_INDEX_URL` |

### 1.3 启动

```bash
./start_all.sh        # Linux / macOS
start_all.bat         # Windows
```

脚本会：起算法层（8000）→ 起后端（8080）→ 起前端（5173），并自动把后端指向 `127.0.0.1:8000`。

> **日志在哪**
> - `start_all.bat`：三个服务各开一个独立控制台窗口（`CTD-Algo-8000` /
>   `CTD-Backend-8080` / `CTD-Frontend-5173`），日志就是窗口输出；关窗口即停该服务。
> - `start_all.sh`：三个子进程的输出直接打到当前终端，Ctrl+C 统一收尾。
> - 服务器模式另外还有远端 `<CTD_REMOTE_ROOT>/ctd_service/service.log`（见 §2.5）。

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
| `app.auth.token` | 空（= 不鉴权） | 访问令牌（可被 `APP_AUTH_TOKEN` 覆盖）。空值时 **fail-open：放行并告警**，见 §5 |

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

## 5. 鉴权配置

### 5.1 这是一道「默认开着门」的闸

后端读 `app.auth.token`（环境变量 `APP_AUTH_TOKEN`）：

| 配置状态 | 后端行为 | 启动日志 |
|---|---|---|
| 未设置 / 空串 | **fail-open：`/api/**` 一律放行** | 打印一条告警，提示当前无鉴权 |
| 已设置 | 请求必须带正确令牌，否则 `401` | 正常启动 |

> **fail-open 是刻意的取舍**：本地演示、单机验证不需要先造一个令牌。
> 但这个默认一旦带进内网/公网，就等于把「删除机位、清空基线、改参照物」
> 这些不可逆操作完全敞开。**上线前必须配 `APP_AUTH_TOKEN`。**

### 5.2 配置示例

Linux / macOS：

```bash
export APP_AUTH_TOKEN="$(python3 -c 'import secrets;print(secrets.token_urlsafe(32))')"
./start_all.sh
```

Windows：

```bat
set APP_AUTH_TOKEN=<32 字节以上的随机串>
start_all.bat
```

写进 `application.yml` 亦可（环境变量优先）：

```yaml
app:
  auth:
    token: ${APP_AUTH_TOKEN:}
```

**不要**把令牌提交进仓库：放在环境变量、systemd `Environment=`、
容器 secret 或 `deploy/.env.local`（已在 `.gitignore`）里。

### 5.3 调用方式

```bash
# 主用：标准 Bearer
curl -H "Authorization: Bearer $APP_AUTH_TOKEN" http://127.0.0.1:8080/api/sparse/cameras
# 备用别名（等价的另一种写法，方便脚本/网关透传）
curl -H "X-API-Token: $APP_AUTH_TOKEN"         http://127.0.0.1:8080/api/sparse/cameras
```

失败返回 `401`，响应体仍是统一结构 `{ "code": 401, "message": "...", "data": null }`。
字段级约定见 [API.md §0.1](API.md)。

### 5.4 上线前自查

- [ ] `APP_AUTH_TOKEN` 已经设置，且**不是**仓库里的示例值；
- [ ] 启动日志里出现的是「已启用鉴权」，不是那条 fail-open 告警；
- [ ] 不带令牌 `curl` 一次 `/api/sparse/cameras`，确认拿到 `401`；
- [ ] 前端配置了同样的令牌（否则页面会整体 401）；
- [ ] 令牌有轮换计划——它没有过期时间，换一次要同步改前端。


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

- 鉴权由 `APP_AUTH_TOKEN` 控制：**不配置就是放行（fail-open）+ 启动告警**，
  配置方法与失败行为见 **§5 鉴权配置**；
- 多租户用请求头 `X-Tenant-Id` 做逻辑隔离（`{tenant}:{cameraId}`），
  它**不是安全边界**——能绕过令牌的人也能随便换租户 id；
- 后端已移除数据库与控制台（无 H2 console）；
- CORS 只放行 `localhost` / `127.0.0.1`；
- 服务器地址与密码只放在 `deploy/.env.local`，不进仓库；
- 即便配了令牌，公网部署仍建议在前面加一层反向代理做 TLS 终止、限流与访问日志。

---

## 6. 扩展性边界与后续演进

> 对应 CODE_REVIEW 问题 #11（`store.py` 单机单目录的扩展性）。
> **本节只描述边界，不改代码**：当前容量对「演示 / 单点验证 / 小规模内网」是够用的，
> 下面写清楚它在哪一步撞墙，以及撞墙时按什么顺序演进。

### 6.1 现状：单机 + 单目录

`algo/data/sparse/` 下是「一个索引 + 每机位一对文件」：

```
cameras.json          机位索引（全量，一个文件）
<cameraId>.json       元数据 + 判定历史 + 模板元数据 + 审计
<cameraId>.npz        缩略图 + 参照物模板 + 模板库 + 基线参考帧
```

存储层是一把进程内锁 + 整文件读写，没有数据库、没有分片、没有外置对象存储。

### 6.2 三个瓶颈点（按撞墙先后）

| # | 瓶颈 | 触发条件（量级估计） | 症状 |
|---|---|---|---|
| B1 | `list_cameras` 全量读索引 | 机位上千 | 机位列表页变慢；索引越大，每次列机的读放大越明显 |
| B2 | `save` 每次全量重写该机位的 NPZ | 基线样本攒满、模板变多之后 | 单次抽样里写盘耗时占比上升；NPZ 越大，每次抽样重写越贵 |
| B3 | 单机单 worker + 进程内锁 | 并发抽样数上去 | 抽样串行化，CPU 用不满；锁竞争拉长尾延迟 |

> 量级说明：单机位数据约 3 MB（见 §1.5），系统是**低频**采样（1 小时一次），
> 所以「一千个机位」意味着约 3 GB 数据、每小时一千次抽样——
> 写入 QPS 很低，真正的痛点是 B1 的读放大和 B3 的串行化，而不是磁盘吞吐。

### 6.3 演进路径（按投入从小到大）

**第 0 步 · 不改代码就能做的**

- 把 `ALGO_SPARSE_DIR` 指向更大/更快的盘（SSD），NPZ 写入不再和其它 IO 抢带宽；
- 机位列表按需拉取，页面不要周期性全量刷新；
- 定期备份 + 删除废弃机位，把索引规模控制在真实在用机位数。

**第 1 步 · 分片目录（改动最小，收益立竿见影）**

把 `<cameraId>.json` / `<cameraId>.npz` 按 `cameraId` 的哈希前缀分到 256 个子目录：

```
algo/data/sparse/c7/<cameraId>.json
algo/data/sparse/c7/<cameraId>.npz
```

解决的问题：单目录文件数不再随机位数线性膨胀（ext4/NTFS 都受益），
`ls` / 备份 / rsync 不再退化成全目录扫描。
**不解决** B1（索引还是全量）和 B2（还是整文件重写）。

**第 2 步 · SQLite 接管索引与元数据（推荐的第一处真改动）**

- `cameras.json` → SQLite 一张 `cameras` 表：`list_cameras` 从「读全量文件」
  变成「一条 `SELECT`」，还能加分页/过滤/排序，B1 直接消失；
- 判定历史、审计这类**只追加**的数据 → `history` / `audit` 表，
  天然支持按时间范围查询，不必把整段历史塞进一个 JSON；
- 用 WAL 模式，读写不互相阻塞，顺带缓解 B3 的锁粒度。

仍留在文件系统里的：NPZ 大数组（缩略图、模板、参考帧）。
SQLite 存 BLOB 也能跑，但需要配套的备份策略（`VACUUM INTO` / 在线备份）。

**第 3 步 · 对象存储承接 NPZ（B2 的解法）**

- NPZ 里是**不可变的图像块**，很适合放对象存储（S3 / MinIO / OSS）；
- 按 `{cameraId}/{slot}/{sampleId}` 分键，写入从「重写整个 NPZ」
  变成「新增一个对象」，B2 的全量重写问题消失；
- 本地留一层 LRU 缓存，命中最近访问的机位，避免每次抽样都走网络；
- 代价：多一个依赖、多一套凭据管理，演示环境**不要**上这一层。

**第 4 步 · 多 worker / 多实例（B3 的解法）**

当前「单 worker + 进程内锁」是有意为之：`CameraStore` 在进程内持锁。
一旦要横向扩展，必须先把状态搬到共享存储（第 2/3 步完成后才可能）：

1. 同一机位的写入需要**跨进程互斥**——用存储层的原子操作（SQLite 事务、
   或对象存储的条件写），而不是 `threading.Lock`；
2. 抽样是天然可并行的：按 `cameraId` 哈希分片到多个 worker，
   同机位永远落到同一个 worker，就能在**不改算法**的前提下拿到近线性扩展；
3. 到这一步再谈负载均衡与健康检查，前面的朴素方案都不用提前做。

### 6.4 什么时候该动手

| 观察到的现象 | 该做的第几步 |
|---|---|
| 机位列表页 > 1s，或 `cameras.json` > 几 MB | 第 2 步（SQLite） |
| 单次抽样里写盘耗时明显超过判定耗时 | 第 1 步治目录膨胀，或第 3 步（对象存储） |
| 抽样排队、CPU 打不满、尾延迟随并发上升 | 第 4 步（分片多 worker） |
| 只是备份 / rsync 变慢 | 第 1 步（分片目录） |

**判断原则**：先量，再动。上面的触发条件都是**量级估计**而非实测；
真实阈值取决于磁盘、机位活跃度与单机位样本数。上线前用真实数据压一遍
`POST /api/sparse/sample`，拿响应里的 `timingsMs` 与写盘耗时占比说话。

