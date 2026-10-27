# 摄像头篡改检测系统 · Camera Tampering Detection

面向监控摄像头的篡改检测演示系统，覆盖 **遮挡 / 失焦 / 移位·转动 / 冻结·重放 / 画面替换** 五类攻击。

核心特点是**低频巡检模式**：不实时拉流，每 1 小时（可配到 1 天）拉一次帧、每次连拉 3 帧，
用"与历史基线的偏离 × 微时序动态"判断画面是否被篡改 —— 成本约为实时方案的 **1/1500 ~ 1/18000**。

![低频巡检](docs/screens/sparse.png)

---

## 目录

- [这是什么](#这是什么)
- [能力清单](#能力清单)
- [架构](#架构)
- [目录结构](#目录结构)
- [快速开始](#快速开始)
- [配置服务器连接（必读）](#配置服务器连接必读)
- [页面导航](#页面导航)
- [算法概要](#算法概要)
- [接口一览](#接口一览)
- [自测与验证](#自测与验证)
- [常见问题](#常见问题)
- [已知边界](#已知边界)
- [相关文档](#相关文档)
- [安全须知](#安全须知)

---

## 这是什么

一套三层架构的演示系统：

| 层 | 技术 | 职责 |
|---|---|---|
| 前端 | Vue3 + TypeScript + Element Plus + ECharts | 页面、可视化、本机摄像头采集 |
| 业务后端 | SpringBoot 3 + JPA + H2 | 任务编排、结果落库、文件管理、对外接口 |
| 算法层 | Python + FastAPI + OpenCV | 全部检测计算（经典算法 + YOLO） |

两种工作模式：

| 模式 | 拉帧频率 | 适用场景 |
|---|---|---|
| **低频巡检**（主线） | 1 小时 ~ 1 天一次，每次 K=3 帧 | 成本敏感，无法持续拉流 |
| **实时监控** | 1~10 秒一帧 | 有持续拉流条件，要求即时告警 |

---

## 能力清单

| 模块 | 能力 |
|---|---|
| 媒体上传 | 图片/视频上传、元信息探测（分辨率/时长/帧数）、参考帧、媒体库 |
| 运行检测 | 勾选算法、实时调参、异步任务 + 进度轮询、分数曲线、逐帧结果 |
| 实时监控 | 本机摄像头抓帧检测、连续 N 帧去抖告警、告警帧留档 |
| **低频巡检** | 抽样判定、三层信号可视化、稳定性分解矩阵、参照物标定、忽略区、模板库 |
| 算法对比 | 多算法曲线叠加、告警率/异常分对比、类型分布、两两一致性 |
| 结果分析 | 结论与风险指数、异常事件时间线、标注评估（P/R/F1）、CSV/JSON 导出 |

**算法**：5 个主力经典算法（拉普拉斯方差、直方图熵、帧差、ORB 特征、边缘消失率）
参与 ≥2 票集成；3 个辅助算法（MOG2、连通域、短长期直方图）按场景选用；
YOLO 目标检测用于归因。低频巡检使用**独立实现**（`algo/sparse/`），不复用这套逐帧算法。

---

## 架构

```
浏览器（Vue3 + Element Plus + ECharts）
        │  REST / multipart
        ▼
SpringBoot 3（业务编排 / 任务调度 / 持久化 / 文件管理）
        │  HTTP（JSON + multipart）
        ▼
Python FastAPI（9 个经典算法 + YOLO + 低频巡检）
        │  SSH 隧道（bridge.py，仅 --gpu 模式需要）
        ▼
GPU 服务器（算法层实际运行处）
```

设计要点：

- **算法层独立**：CV 生态在 Python 侧，Java 侧保持轻量、可替换；
- **后端不做算法**：所有判定只在 Python 实现一份，避免双语言逻辑漂移；
- **机位态与会话态分离**：低频巡检的基线按 `cameraId` 落盘，重启不丢；
- **优雅降级**：算法服务未启动、模型缺失、参考帧缺失都有明确提示，不抛栈崩溃。

---

## 目录结构

```
camera-tamper-demo/
├── algo/                        # Python 算法推理层
│   ├── server.py                # FastAPI 入口（检测 / 异步任务 / YOLO / 低频巡检）
│   ├── detectors/               # 9 个算法（5 主力 + 3 辅助 + YOLO）+ 基类
│   ├── sparse/                  # ★ 低频巡检核心
│   │   ├── metrics.py           #   底层指标：单帧 / 微时序 / 鲁棒 z / 参照物匹配
│   │   ├── store.py             #   机位态、基线库、模板库、审计
│   │   └── engine.py            #   判定流程与结果组装
│   ├── batching.py / media.py / tasks.py
│   ├── requirements.txt
│   └── tests/                   # 算法与拼批自测
├── backend/                     # SpringBoot 3
│   └── src/main/java/com/demo/tamper/
│       ├── controller/          # 媒体 / 检测 / 分析 / 实时 / 低频巡检
│       ├── service/             # 编排、网关、实时会话、导出
│       └── ...
├── frontend/                    # Vue3 + TS + Vite
│   └── src/{api,views,components,router,store,utils}
├── deploy/                      # 部署与联调
│   ├── remote.py                # 一条 SSH 连接完成 部署 + 隧道
│   ├── .env.local.example       # 服务器连接配置模板
│   └── test_sparse_e2e.sh       # 低频巡检端到端测试
├── samples/                     # 演示样本与生成脚本
├── docs/                        # 文档与截图
├── start_all.bat / start_all.sh # 一键启动
└── .gitignore
```

---

## 快速开始

### 前置要求

| 依赖 | 版本 | 说明 |
|---|---|---|
| JDK | 17+ | SpringBoot 3 要求 |
| Maven | 3.8+ | 后端构建 |
| Node.js | 18+ | 前端 |
| Python | 3.10+ | 算法层 |
| GPU 服务器 | — | **仅 `--gpu` 模式需要** |

端口占用：算法层 `8000`（本地）或 `18010`（隧道）、后端 `8080`、前端 `5173`。

### 方式 A：纯本地模式（推荐 —— 只演示低频巡检时足够用）

低频巡检是**纯 CPU 算法**，与 GPU、YOLO、实时检测完全无关；这条路径**不需要登录服务器**。

```bash
cd algo
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt      # Windows
# .venv/bin/pip install -r requirements.txt        # Linux / macOS
cd ..
start_all.bat          # Windows，不带 --gpu
./start_all.sh         # Linux / macOS，不带 --gpu
```

启动后 → 打开 http://localhost:5173 → 左侧「**低频巡检**」→ 填机位 id（如 `cam-01`）
→ 点右上「**一键演示（6 次采样）**」，30 秒跑完 正常 → 遮挡 → 失焦 → 重放。

> **演示不需要摄像头，也不需要准备图片**：没有底图时会自动使用内置样图
> （`frontend/public/demo/reference_normal.jpg`）。

**为什么不需要 GPU / YOLO**：

- `requirements.txt` **不含** `ultralytics` / `torch`，安装只有几十 MB；
- 没有 `yolo26n.pt` 时 YOLO 自动关闭，**低频巡检与 9 个经典算法全部可用**；
- 需要 YOLO 归因时，取消 `requirements.txt` 那两行注释、装好 `torch`，
  再把 `yolo26n.pt` 放到 `algo/` 即可。

本地模式下算法层监听 `127.0.0.1:8000`，启动脚本会自动把后端指向它（无需改配置）。

### 方式 B：GPU 服务器模式（需要 YOLO / 完整功能时）

算法层跑在服务器上，本地通过 SSH 隧道访问。

```bash
# 1) 配置服务器连接（见下一节）
cp deploy/.env.local.example deploy/.env.local
#   编辑 deploy/.env.local：CTD_SSH_HOST / CTD_SSH_USER / CTD_SSH_KEY

# 2) 安装算法层依赖（含 paramiko，部署脚本需要）
cd algo
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt      # Windows
# .venv/bin/pip install -r requirements.txt        # Linux / macOS
cd ..

# 3) 一键启动（部署算法层 → 建隧道 → 起后端 → 起前端）
start_all.bat --gpu        # Windows
./start_all.sh --gpu       # Linux / macOS
```

启动后：

| 服务 | 地址 |
|---|---|
| 前端 | http://localhost:5173 |
| 后端健康检查 | http://127.0.0.1:8080/api/algo/health |
| 算法层（经隧道） | http://127.0.0.1:18010/health |

> `--gpu` 会**把 `algo/` 打包上传到服务器并重启远端服务**，因此需要对
> `CTD_REMOTE_ROOT`（默认 `/root/yzn_model_api`）有写权限。
> 只想连已经在跑的服务时，可以只起隧道：`python deploy/remote.py --tunnel`。

### 手动启动（不用一键脚本）

```bash
# ① 算法层
cd algo
.venv\Scripts\python server.py --port 8000        # Windows
# .venv/bin/python server.py --port 8000          # Linux / macOS
# 只跑经典算法可加 --no-yolo

# ② 业务后端
cd backend
mvn -DskipTests spring-boot:run

# ③ 前端
cd frontend
npm install
npm run dev
```

> 两种模式的差别只有一个：后端要连哪个算法层地址。
> 启动脚本会自动设置（`APP_ALGO_BASEURL` 环境变量，透传到 `app.algo.base-url`），
> **不需要手改 `application.yml`**。手动启动时才需要自己设这个变量。

---

## 配置服务器连接（必读）

为避免把服务器地址/账号/密码写进仓库，连接信息通过**环境变量**或**本地未提交的配置文件**提供。

优先级：环境变量 > `deploy/.env.local` > 内置默认值（仅目录与账号名有默认）。

```powershell
# 方式一：环境变量（PowerShell）
$env:CTD_SSH_HOST="192.0.2.10"
$env:CTD_SSH_USER="root"
$env:CTD_SSH_KEY="$HOME\.ssh\id_ed25519"
```

```bash
# 方式一：环境变量（Linux / macOS）
export CTD_SSH_HOST=192.0.2.10
export CTD_SSH_USER=root
export CTD_SSH_KEY=~/.ssh/id_ed25519
```

```bash
# 方式二：本地配置文件（推荐）
cp deploy/.env.local.example deploy/.env.local
# 填写 CTD_SSH_HOST / CTD_SSH_USER / CTD_SSH_KEY（或 CTD_SSH_PASSWORD）
```

支持的变量：

| 变量 | 默认 | 说明 |
|---|---|---|
| `CTD_SSH_HOST` | **必填** | 服务器地址 |
| `CTD_SSH_USER` | `root` | 登录账号 |
| `CTD_SSH_KEY` | `~/.ssh/id_ed25519` | 私钥路径（存在则优先使用） |
| `CTD_SSH_PASSWORD` | 空 | 密码认证（没有可用密钥时才用） |
| `CTD_REMOTE_ROOT` | `/root/yzn_model_api` | 服务器上的部署根目录 |

`deploy/.env.local` 已在 `.gitignore` 中，**不会被提交**。

---

## 页面导航

| 页面 | 路径 | 用途 |
|---|---|---|
| 媒体上传 | `/upload` | 上传图片/视频，管理媒体库 |
| 运行检测 | `/detect` | 对整段视频/图片跑多算法检测 |
| 实时监控 | `/live` | 本机摄像头实时检测（演示用） |
| **低频巡检** | `/sparse` | **主线功能**：按小时抽样、三层可视化、标定 |
| 算法对比 | `/compare` | 多算法曲线叠加与一致性 |
| 结果分析 | `/analysis` | 结论、时间线、指标评估、导出 |

### 首次使用建议顺序

1. 打开 **低频巡检**，填一个机位 id（如 `cam-01`）；
2. 点「**标定参照物**」：在画面上框 2~4 个**不会移动**的结构（大门、楼角、围墙），
   可另把会被频繁挪动的区域标成「忽略区」；
3. 每小时（或手动）提交一次抽样；**同一时段要积累 3 天**才算基线就绪；
4. 想立即看效果：点右上角「**一键演示（6 次采样）**」，
   30 秒跑完 正常 → 遮挡 → 失焦 → 重放 全过程。

---

## 算法概要

### 三层信号模型

| 层 | 时间尺度 | 回答什么 |
|---|---|---|
| **L2 单帧** | 任意一帧 | 这一帧本身好不好（失焦 / 遮挡 / 画质） |
| **L1 微时序** | 一次抽样内 3 帧（几秒） | 这几秒画面动没动、是不是真摄像头 |
| **L0 跨采样点** | 相邻抽样间隔 1 小时 | 跟这个机位的历史比变没变 |

### 核心机制

- **按小时分槽的历史基线**（24 槽 × ≤8 样本）→ 自动吸收昼夜光照变化；
- **稳定性分解**：`dHist`（历史偏离）× `dBurst`（微时序动态）4 格矩阵，
  把「持续篡改」与「有人路过的瞬时事件」分开；
- **冻结/重放**：主判据是**跨采样点逐位相同** + 帧间时间噪声缺失；
- **参照物**：人工标定静止结构，多尺度匹配 + 相似变换拟合判断机位是否被移动/转动；
- **光度对齐**：把"整体变暗/变灰"这类全局仿射变化从偏离计算里剔除。

详细算法、参数与公式见 **[docs/SPARSE.md](docs/SPARSE.md)**，
完整方案见 **[docs/SOLUTION.md](docs/SOLUTION.md)**。

---

## 接口一览

后端统一返回 `{ code, message, data }`。

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/algo/health` | 算法层健康状态 |
| GET | `/api/algo/algorithms` | 算法清单（含参数元数据） |
| POST | `/api/media/upload` | 上传媒体 |
| POST | `/api/detection/run` | 提交检测任务 |
| GET | `/api/detection/{id}` | 查询任务与逐帧结果 |
| POST | `/api/live/sessions` | 开始实时监控会话 |
| POST | `/api/live/sessions/{id}/frame` | 提交一帧 |
| GET | `/api/sparse/meta` | 低频巡检：参数与判决字典 |
| POST | `/api/sparse/sample` | 低频巡检：**提交一次抽样（1~K 帧）** |
| GET | `/api/sparse/state/{cameraId}` | 机位状态（24 槽 + 历史 + 模板） |
| POST | `/api/sparse/landmarks/{cameraId}` | 标定参照物 / 忽略区 |
| POST | `/api/sparse/landmarks/{cameraId}/templates` | 补录参照物模板 |

完整契约见 **[docs/API.md](docs/API.md)**。

---

## 自测与验证

```bash
# 算法自测（59 项断言：遮挡/失焦/移位/冻结/光照/部分遮挡/模板库…）
cd algo
.venv\Scripts\python tests\test_sparse.py     # Windows
# .venv/bin/python tests/test_sparse.py       # Linux / macOS

# 动态拼批（YOLO 批量执行器）
.venv\Scripts\python tests\test_batcher.py

# 低频巡检 HTTP 端到端（在算法层所在机器上跑）
bash deploy/test_sparse_e2e.sh
```

预期输出：`通过 59 / 59 全部通过 ✅`。

---

## 常见问题

**1. 页面显示"算法服务未启动"**
后端连不上算法层。先查隧道：

```bash
curl http://127.0.0.1:18010/health
```

**2. 隧道日志反复刷"认证被拒"**
服务器对重复认证有频率限制。**等 1~2 分钟再重启**，不要连续重试；
优先用 SSH 密钥（`CTD_SSH_KEY`）而不是密码。

**3. `pip install` 卡住或超时**
换国内镜像：

```bash
pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
```

**4. `npm install` 慢**
换镜像：`npm config set registry https://registry.npmmirror.com`

**5. 端口被占用**
`8000 / 8080 / 5173 / 18010` 任一被占都会起不来，
用 `netstat -ano | findstr :8080`（Windows）或 `lsof -i:8080` 排查。

**5.1 后端报 "Port 8080 was already in use"，但 netstat 看不到任何进程（Windows）**
这是 Windows 的**端口保留**（Hyper-V/WSL 预留端口段），绑定会直接报 `WinError 10013`。
两种解法：

```powershell
# 解法一：换个端口，并让前端代理指过去（推荐，不需要管理员）
mvn -DskipTests spring-boot:run -Dspring-boot.run.arguments=--server.port=8090
$env:VITE_API_TARGET="http://127.0.0.1:8090"; npm run dev   # 在 frontend 目录
```

```powershell
# 解法二：临时释放保留端口（需要管理员）
net stop winnat
# 此时启动后端，成功后再执行：
net start winnat
```

查保留段：`netsh interface ipv4 show excludedportrange protocol=tcp`

**6. 一直显示"基线建立中"**
正常。同一时段槽需要 3 个样本（= 3 天）才算就绪，期间只记录、不下结论。

**7. Maven 首次构建很慢**
需要下载依赖，属正常现象；有可用网络后重跑即可。

---

## 已知边界

| 边界 | 说明 |
|---|---|
| **告警延迟下界 = 采样相位** | 1 小时采样平均延迟 30 分钟，"实时告警"不成立 |
| **瞬时事件必然漏检** | 3 秒遮挡落在抽样窗口的概率仅 0.28%~0.42% |
| **冷启动约 3 天** | 同一时段需攒 3 个样本 |
| **缓慢攻击可能被学进基线** | 每天变化都在阈值以下时会随基线漂移 |
| **季节漂移可能锁死报警** | 渐变超阈值后基线冻结，需人工「重设基准」 |
| **假定固定机位** | 手持/云台场景不适用参照物方案 |
| **不做 AIGC 生成画面检测** | 公开研究显示现有检测器在该场景不可靠，不作承诺 |
| **演示级并发** | 未做多租户与鉴权 |

---

## 相关文档

| 文档 | 内容 |
|---|---|
| [docs/SOLUTION.md](docs/SOLUTION.md) | **完整技术方案**（分层、算法、部署、测试、成本、边界） |
| [docs/SPARSE.md](docs/SPARSE.md) | 低频巡检：三层信号、稳定性分解、参照物、模板库 |
| [docs/ALGORITHMS.md](docs/ALGORITHMS.md) | 9 个实时算法的原理、参数与局限 |
| [docs/API.md](docs/API.md) | 接口契约与字段语义 |
| [docs/DEPLOY.md](docs/DEPLOY.md) | 部署与运维细节 |
| [docs/ACCEPTANCE.md](docs/ACCEPTANCE.md) | 需求 ↔ 实现 ↔ 验证对照表 |

> `docs/SOLUTION.docx` 是同一份方案的可汇报版本，可用
> `python deploy/build_solution_docx.py` 从 Markdown 重新生成。

---

## 安全须知

- 仓库里**不包含**任何服务器地址、账号、密码或私钥；
  连接信息请放在环境变量或 `deploy/.env.local`（已忽略）。
- `deploy/remote.py` 会通过 SSH 上传代码并重启远端服务，请先确认目标目录正确。
- 若要在公网暴露本服务，**必须自行加鉴权与 HTTPS** ——
  本项目定位为演示系统，默认无认证。

---

## License

未指定。如需商用请先与作者确认。
