# 算法层接口契约（Python FastAPI）

默认地址：`http://127.0.0.1:8000`；GPU 服务器上为 `http://125.122.133.73:8000`（建议内网 + token）。

所有接口返回 JSON，错误统一为 `{"detail": "..."}`，HTTP 4xx 表示输入问题，5xx 表示算法层内部错误
（SpringBoot 会把 5xx 包装成 502 提示"算法服务不可用"）。

---

## 1. 元信息

### GET /health

```json
{
  "status": "ok",
  "version": "2.1.0",
  "uptimeSeconds": 249,
  "cv2": "4.10.0",
  "numpy": "1.26.4",
  "cvThreads": 1,
  "yolo": {
    "enabled": true,
    "available": false,
    "loading": false,
    "modelPath": "/root/yzn_model_api/yolo26n.pt",
    "modelExists": true,
    "device": "0",
    "error": null,
    "classes": 80,
    "batcher": { "batches": 12, "images": 96, "avgBatch": 8.0, "maxBatch": 16, "maxWaitMs": 2.0 }
  },
  "tasks": { "total": 1, "byStatus": { "running": 1 } }
}
```

### GET /algorithms

返回算法清单，前端据此渲染算法卡片和动态参数表单。

`kind` 有三类，前端按此分组：

| kind | 含义 | 是否参与 ≥2 票投票 |
|------|------|------------------|
| `opencv` | 主力经典算法（5 个） | ✅ |
| `legacy` | 辅助算法（`mog2_area` / `frame_area` / `shortlong`），来自 `G:/project/算法` | ❌ 只出独立指标 |
| `yolo` | 深度学习检测 | ❌ |

`description` 首句即「适用场景：…」，前端直接显示在卡片上。辅助算法默认不勾选，
需要按场景手动选择（例如分钟级持续篡改才用 `shortlong`）。

```json
{
  "algorithms": [
    {
      "name": "laplacian",
      "displayName": "拉普拉斯方差（失焦检测）",
      "description": "...",
      "tamperTypes": ["normal", "defocused"],
      "requiresReference": false,
      "kind": "opencv",
      "available": true,
      "thresholdScore": 0.5,
      "params": [
        {
          "key": "threshold",
          "label": "清晰度阈值",
          "default": 100.0,
          "min": 5.0,
          "max": 800.0,
          "step": 5.0,
          "unit": "",
          "type": "number",
          "description": "Laplacian 方差低于该值判定为失焦"
        }
      ]
    }
  ],
  "thresholdScore": 0.5,
  "scoreHint": "score 为 0~1 归一化异常分，0.5 恰好等于各算法自身判定阈值"
}
```

---

## 2. 媒体探测

### POST /media/probe

`multipart/form-data`：`file`

```json
{ "mediaType": "video", "width": 1920, "height": 1080, "fps": 25.0, "frameCount": 130, "durationMs": 5200 }
```

图片返回 `mediaType=image`、`frameCount=1`、`fps=0`。

---

## 3. 单图检测

### POST /detect/image

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| file | file | ✅ | 待检测图片 |
| reference | file | ❌ | 参考帧（正常画面），缺省时参考类算法返回 `skipped=true` |
| algorithms | string(JSON) | ✅ | `["laplacian","entropy","scene_diff","orb","edge","yolo"]` |
| params | string(JSON) | ❌ | `{"laplacian":{"threshold":120},"yolo":{"conf":0.3}}` |
| visual | bool | ❌ | 是否生成中间过程图，默认 true |

```json
{
  "mediaType": "image",
  "width": 960,
  "height": 540,
  "hasReference": true,
  "frameCount": 1,
  "elapsedMs": 96,
  "timings": { "laplacian": 49.7, "entropy": 4.4, "scene_diff": 7.4, "orb": 18.7, "edge": 10.3 },
  "results": [
    {
      "algorithm": "laplacian",
      "score": 0.9945,
      "isTampered": true,
      "tamperType": "defocused",
      "confidence": 0.989,
      "detail": { "sharpness": 1.1, "absoluteThreshold": 100.0, "referenceSharpness": 812.4, "sharpnessDropRatio": 0.9986, "threshold": 100.0 },
      "skipped": false,
      "note": "清晰度 1.1 < 阈值 100",
      "visual": "data:image/jpeg;base64,..."
    }
  ]
}
```

---

## 4. 视频检测（异步）

### POST /detect/video

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| file | file | ❌ | 视频文件（已存在于远端缓存时可省略，改传 `cacheKey`） |
| cacheKey | string | ❌ | 远端媒体缓存 key，配合 `/media/cache` 使用 |
| algorithms | string(JSON) | ✅ | 同单图 |
| params | string(JSON) | ❌ | 同单图 |
| sampleFps | float | ❌ | 目标采样帧率，默认 5 |
| maxFrames | int | ❌ | 最多处理帧数，默认 600（上限 5000） |
| visual | bool | ❌ | 是否生成可视化（默认 true，单任务最多 12 张） |
| startMs / endMs | int | ❌ | 只处理这一段（绝对毫秒）。与 `segments` 互斥，`segments` 优先 |
| segments | string(JSON) | ❌ | 分段扫描，一次跑多段：`[{"label":"a","type":"covered","startMs":544000,"endMs":568000}]` |
| refGapMs | int | ❌ | 取参考帧时相对段首再往前的间隔，默认 1000 |

```json
{ "taskId": "b1c2...", "status": "pending", "media": {...}, "sampleFps": 10.0, "maxFrames": 300, "segmentCount": 3 }
```

**分段语义**：`segments` 里每一段独立建检测器、独立取参考帧（段首前 `refGapMs` 处），互不影响；采样网格仍按**全局帧号**取模，所以"整段跑"和"分段跑"重叠区域的帧号、时间戳完全一致。返回的每帧带 `segmentIndex`，任务汇总里带 `segmentSummaries`（每段的帧数、告警帧数、峰值分数、篡改类型）。

### 媒体缓存（避免大文件反复过网）

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/media/cache` | `multipart`：`file` + `key`，把视频落到远端 `cache/<key>.mp4`，已存在则直接跳过 |
| GET | `/media/cache/{key}` | 命中返回 `{key,size,path}`，未命中 404 `{"detail":"远端未缓存该媒体"}` |

356MB 的 FLAD 录像经 SSH 桥传一次约 2 分钟，缓存后同一媒体再提交只传 `cacheKey`，任务秒级进入扫描阶段。

### GET /detect/tasks/{taskId}

查询参数：`includeFrames=true|false`、`offset`、`limit`（分页取逐帧结果）。

```json
{
  "taskId": "b1c2...",
  "status": "done",
  "progress": 1.0,
  "message": "检测完成，共 44 帧（采样间隔 3 帧）",
  "elapsedMs": 5120,
  "tamperedFrames": 27,
  "totalFrames": 44,
  "timings": { "laplacian": 17.19, "entropy": 3.83, "orb": 26.59 },
  "frames": [
    {
      "frameIndex": 51,
      "timestampMs": 2040,
      "results": [ { "algorithm": "laplacian", "score": 0.98, "isTampered": true, "...": "..." } ]
    }
  ]
}
```

`status` 取值：`pending | running | done | failed | cancelled`。

### POST /detect/tasks/{taskId}/cancel

`{ "taskId": "...", "cancelled": true }`

### DELETE /detect/tasks/{taskId}

删除内存任务（TTL 默认 1 小时自动回收）。

---

## 5. YOLO 专用端点

### POST /predict

`multipart`：`file`、`conf`、`imgsz` —— 单图低延迟推理（batch=1）。

### POST /predict_batch

`multipart`：`files`（多个）、`conf`、`imgsz` —— 高吞吐批量推理，返回实测吞吐，可用于压测：

```json
{
  "count": 16,
  "decodeMs": 12.4,
  "inferMs": 58.1,
  "totalMs": 70.5,
  "imagesPerSecond": 227.0,
  "batcher": { "batches": 1, "images": 16, "avgBatch": 16.0, "maxObservedBatch": 16 },
  "results": [ { "count": 3, "detections": [ { "label": "person", "confidence": 0.91, "bbox": [12,40,120,300] } ] } ]
}
```

---

## 6. 结果数据结构（跨层契约）

| 字段 | 类型 | 说明 |
|------|------|------|
| algorithm | string | 算法标识 |
| score | number | 0~1 归一化异常分，**0.5 = 判定阈值** |
| isTampered | bool | 是否判定为篡改（由原始指标决定） |
| tamperType | string | `normal` / `covered` / `defocused` / `moved` |
| confidence | number | 判定置信度（离判定线的相对距离，与是否异常无关） |
| detail | object | 中间量，各算法不同（sharpness / entropy / translation / edgeDisappearanceRate ...） |
| skipped | bool | 是否因缺参考帧等原因跳过 |
| note | string | 命中或跳过原因（中文，可直接展示） |
| visual | string | 中间过程图 data URL（JPEG base64），可能为空 |

---

## 7. 错误码

| HTTP | 场景 | 示例 |
|------|------|------|
| 400 | 参数/文件问题 | 未知算法、图片解码失败、JSON 解析失败 |
| 404 | 任务不存在 | 任务过期被回收 |
| 413 | 文件过大 | 超过 `ALGO_MAX_UPLOAD_MB`（默认 512） |
| 500 | 算法层内部错误 | 未捕获异常（兜底 handler 返回 detail） |

---

# 业务后端接口（SpringBoot）

统一响应体：`{ "code": 0, "message": "ok", "data": ... }`，`code != 0` 表示业务失败，HTTP 状态同步设置。

## 媒体

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/media/upload` | `file` + 可选 `reference`，返回 MediaDto |
| GET | `/api/media` | 列表（按上传时间倒序） |
| GET | `/api/media/{id}` | 元信息 |
| GET | `/api/media/{id}/file` | 原始文件，支持 `Range`（206 分段响应） |
| DELETE | `/api/media/{id}` | 删除媒体及其任务/结果/可视化 |

## 检测

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/detection/run` | 见下方请求体 |
| GET | `/api/detection` | 任务列表 |
| GET | `/api/detection/{taskId}` | 任务状态（含 progress / message / elapsedMs） |
| GET | `/api/detection/{taskId}/frames` | `page`、`size`(≤2000)、`algorithm` 可选 |
| GET | `/api/detection/{taskId}/visual/{algo}/{frame}` | JPEG 可视化图 |
| POST | `/api/detection/{taskId}/cancel` | 取消运行中的任务 |
| DELETE | `/api/detection/{taskId}` | 删除任务及其结果 |

`POST /api/detection/run` 请求体：

```json
{
  "mediaId": "uuid",
  "algorithms": ["laplacian", "entropy", "scene_diff", "orb", "edge"],
  "params": { "laplacian": { "threshold": 120 }, "orb": { "motion_pixels": 40 } },
  "sampleFps": 10,
  "maxFrames": 300,

  "startMs": 15000,
  "endMs": 20000,
  "segments": [
    { "label": "第 1 段", "type": "moved",   "startMs": 15000,  "endMs": 20000 },
    { "label": "第 2 段", "type": "covered", "startMs": 544000, "endMs": 568000 }
  ],
  "refGapMs": 1000
}
```

> `params` 里只需给出想覆盖的参数，未给出的使用算法层默认值。
> `segments` 优先于 `startMs/endMs`；段会被裁到视频时长内、按起点排序、丢掉空段（上限 500 段）。
> 提交前后端会确保媒体已落进远端缓存（第一次需上传，之后只传 key），所以大文件只在首跑慢一次。

## 真值标注（事件级）

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/media/{id}/ground-truth` | `file`（multipart），自动识别 baseline.py / CSV / JSON 三种格式 |
| GET | `/api/media/{id}/ground-truth` | 返回 `{count, events[]}` |
| DELETE | `/api/media/{id}/ground-truth` | 清空该媒体的标注 |

支持三种来源：

| 格式 | 样例 | 说明 |
|------|------|------|
| `baseline.py` | `video1 = { "type": ["covered"], "change": [["02:03","02:05"]] }` | 直接上传 FLAD 的 baseline.py，按文件名里的 `videoN` 取对应块 |
| CSV | `start,end,type` / `type,start,end` | 时间支持 `mm:ss` 或秒 |
| JSON | `{"events":[{"start":15000,"end":20000,"type":"moved"}]}` | `start/end` 与 `startMs/endMs` 都认 |

类型统一归一：`occlusion→covered`、`bluriness/blur→defocused`、`jitter→moved`。

## 分析

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/detection/{taskId}/analysis` | 结论 / 事件 / 逐算法统计 / 一致性 / 分数分布 / 标注指标 / **分段结果 / 事件级对照** |
| GET | `/api/detection/{taskId}/analysis?tolMs=3000` | 事件级匹配容差（默认 3000ms） |
| GET | `/api/detection/{taskId}/compare` | 多算法指标 + 类型分布 + 整体一致性 |
| POST | `/api/detection/{taskId}/ground-truth` | 逐帧真值 CSV（`frame_index,tampered`），仅在没导入事件级标注时用于帧级指标 |
| GET | `/api/detection/{taskId}/export` | `format=csv|json`，带 Content-Disposition |

AnalysisDto 结构（节选）：

```json
{
  "taskId": "...",
  "status": "DONE",
  "media": { "originalName": "demo_video.mp4", "width": 960, "height": 540, "frameCount": 130 },
  "frames": 44,
  "sampleStepMs": 120,
  "conclusion": {
    "verdict": "tampered",
    "tamperType": "covered",
    "text": "检测到 1 段镜头遮挡事件，涉及 27 帧（占采样帧 61.4%），峰值异常分 1.00 ...",
    "riskLevel": 0.81,
    "tamperedFrames": 27,
    "totalFrames": 44,
    "tamperRate": 0.614,
    "triggeredAlgorithms": ["边缘消失率（统一检测）", "..."],
    "firstAlarmMs": 2000
  },
  "algorithmStats": [
    { "algorithm": "entropy", "displayName": "直方图熵（遮挡检测）", "frames": 44, "tamperedFrames": 14,
      "skipped": 0, "tamperRate": 0.318, "avgScore": 0.444, "maxScore": 1.0,
      "avgCostMs": 3.83, "dominantTamperType": "covered", "events": 1, "firstAlarmMs": 3600 }
  ],
  "events": [
    { "startFrame": 90, "endFrame": 129, "startMs": 2000, "endMs": 5120, "durationMs": 3120,
      "frameCount": 27, "tamperType": "covered", "peakScore": 1.0, "avgScore": 0.93,
      "algorithms": ["edge", "laplacian", "entropy", "scene_diff"] }
  ],
  "consistency": [ { "algorithmA": "edge", "algorithmB": "entropy", "agreement": 0.615, "comparedFrames": 44 } ],
  "distribution": [ { "binStart": 0.0, "binEnd": 0.1, "count": 85 } ],
  "metrics": {
    "labelledFrames": 44,
    "tamperedLabels": 14,
    "perAlgorithm": [ { "algorithm": "entropy", "truePositive": 14, "falsePositive": 0,
                        "trueNegative": 30, "falseNegative": 0,
                        "precision": 1.0, "recall": 1.0, "f1": 1.0, "accuracy": 1.0 } ],
    "ensemble": { "algorithm": "ensemble (≥2 算法投票)", "...": "..." }
  },

  "windowStartMs": 15000,
  "windowEndMs": 675000,
  "segments": [
    { "index": 0, "label": "moved-15-20", "startMs": 15000, "endMs": 20000,
      "frameCount": 31, "tamperedFrames": 22, "alarmRate": 0.71, "peakScore": 1.0,
      "tamperType": "moved", "expectedType": "moved", "gtTampered": true, "matched": true,
      "eventCount": 1, "avgCostMs": 14.2 }
  ],
  "eventEvaluation": {
    "toleranceMs": 3000,
    "gtCount": 3,
    "source": "baseline.py",
    "perAlgorithm": [ { "algorithm": "edge", "gtCount": 3, "truePositive": 3, "falseNegative": 0,
                        "falsePositive": 2, "precision": 0.6, "recall": 1.0, "f1": 0.75,
                        "avgDelayMs": 1240 } ],
    "ensemble": { "gtCount": 3, "truePositive": 3, "falseNegative": 0, "falsePositive": 1,
                  "precision": 0.75, "recall": 1.0, "f1": 0.857, "avgDelayMs": 980 },
    "pairs": [ { "algorithm": "edge", "kind": "TP", "alarmMs": 16240, "expectedMs": 15000,
                 "delayMs": 1240, "overlapMs": 2000, "nearestGtMs": 0 } ],
    "pairsByAlgorithm": { "edge": [ "..." ] }
  }
}
```

**事件级对照的判定口径**：算法天然有 1–3 秒延迟（需要累积帧、参考帧要往前取），逐帧严格比对会把"晚 1 秒的正确告警"记成一次漏报加一次误报。所以按**事件**配对：告警区间与标注区间重叠即命中，容差 `tolMs` 允许起点偏移；`FN` 只统计**实际扫描过的段内**的标注（没选的区间不算漏报）。`avgDelayMs` 是命中事件的告警相对标注起点的平均延迟。

## 实时监控

浏览器 `getUserMedia` 采集，后端做"参考帧 + 算法调用 + 连续帧去抖"。会话只在内存，**不入库**。

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/live/sessions` | 开始监控：首帧作为参考基准 |
| POST | `/api/live/sessions/{id}/frame` | 提交一帧，同步返回结果 |
| POST | `/api/live/sessions/{id}/reference` | 重设参考基准，并把连续告警计数清零 |
| PATCH | `/api/live/sessions/{id}/params` | 运行中更新阈值（学习期标定后回填，<br>原地改算法层的检测器，不清空帧间状态） |
| GET | `/api/live/sessions/{id}` | 会话信息（帧数/告警数/参考帧地址） |
| GET | `/api/live/sessions/{id}/reference` | 参考帧图片 |
| GET | `/api/live/sessions/{id}/alarms/{frameIndex}` | 告警帧现场图（每会话最多留 30 张） |
| DELETE | `/api/live/sessions/{id}` | 结束会话；`?purge=true` 连告警图一起删 |

### POST /api/live/sessions

`multipart/form-data`：

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| reference | file | ✅ | 参考帧（开始监控那一刻的画面） |
| algorithms | string(JSON) | ❌ | 默认 `["laplacian","entropy","scene_diff","orb","edge"]` |
| params | string(JSON) | ❌ | 同检测接口 |
| requiredStreak | int | ❌ | 连续多少帧判篡改才告警，1–5，默认 2 |

```json
{
  "sessionId": "9d5ea7f4-...",
  "algorithms": ["laplacian", "entropy", "scene_diff", "orb", "edge"],
  "requiredStreak": 2,
  "frameCount": 0,
  "alarmCount": 0,
  "createdAt": 1792546173578,
  "lastFrameAt": 1792546173578,
  "referenceUrl": "/api/live/sessions/9d5ea7f4-.../reference",
  "alarmImages": 0
}
```

### POST /api/live/sessions/{id}/frame

`multipart/form-data`：

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| frame | file | ✅ | 当前帧（JPEG/PNG） |
| probe | bool | ❌ | 默认 `false`。`true` = 学习期采样帧：只回各算法指标，**不推进帧号、不计连续告警、不落告警图** |

响应：

```json
{
  "sessionId": "9d5ea7f4-...",
  "frameIndex": 4,
  "timestampMs": 1792546179000,
  "alarm": true,
  "frameTampered": true,
  "streak": 4,
  "requiredStreak": 2,
  "votes": 2,
  "tamperType": "defocused",
  "score": 0.84,
  "verdict": "检测到镜头失焦/模糊（2/5 个算法告警，连续 4 帧）",
  "algorithms": [
    { "algorithm": "laplacian", "displayName": "拉普拉斯方差（失焦检测）",
      "score": 0.88, "tampered": true, "tamperType": "defocused",
      "note": "清晰度 42.1 低于阈值", "skipped": false,
      "detail": { "sharpness": 42.1, "threshold": 100.0 } }
  ],
  "elapsedMs": 139,
  "alarmImageUrl": "/api/live/sessions/9d5ea7f4-.../alarms/4"
}
```

字段语义：

- `frameTampered`：本帧是否判篡改 = **≥2 个主力算法同帧告警**（只选 1 个主力时为 1）。辅助算法与 YOLO 不计票。
- `alarm`：`frameTampered` 连续达到 `requiredStreak` 次才为 `true`，前端据此闪红/蜂鸣。
- `streak`：连续判篡改的帧数；`verdict` 会写"再确认 N 帧后告警"。
- `alarmImageUrl`：仅 `alarm=true` 且未超过 30 张时有值，会话结束后仍可访问。
- `detail`：该算法本帧的原始指标，前端学习期靠它取 `primaryMetric.field` 做标定。

### PATCH /api/live/sessions/{id}/params

`multipart/form-data`：`params`（string(JSON)，同检测接口）。返回会话信息。
前端学习期算完阈值后调用；后端会同步到算法层 `POST /session/{id}/params` 原地更新检测器参数，
并把会话的连续告警计数清零（新旧口径的结果不能拼成同一个 streak）。

**上报频率建议**：1~3 秒一帧。链路是"浏览器 → 后端 → SSH 桥 → GPU"，单帧端到端约 130~170 ms（含上传一张
1280 宽 JPEG），比采样间隔小一个数量级，不会积压。前端用 in-flight 标志保证不并发堆积。

## 低频巡检（稀疏采样）

面向「1 小时 / 1 天拉一次帧」的场景。与实时监控的关键区别：**机位状态在算法层按 `cameraId` 落盘**，
不随会话回收，所以没有"会话"概念；每次提交都是一次独立抽样，基线自动累积。
算法细节见 [SPARSE.md](SPARSE.md)。

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/sparse/meta` | 参数元数据 + 判决字典（前端渲染表单与图例） |
| POST | `/api/sparse/sample` | 提交一次抽样（1~12 帧，推荐 3~5 帧） |
| GET | `/api/sparse/cameras` | 机位列表（含样本数与基线状态） |
| GET | `/api/sparse/state/{cameraId}` | 机位状态：24 时段槽明细 + 判定历史 |
| POST | `/api/sparse/state/{cameraId}/reset` | 清空基线（重设基准） |
| DELETE | `/api/sparse/state/{cameraId}` | 删除机位及其全部基线数据 |
| GET | `/api/sparse/thumb/{cameraId}/{slot}/{index}` | 基线样本缩略图（PNG） |

### POST /api/sparse/sample

`multipart/form-data`：

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| cameraId | string | ✅ | 机位 id；基线按它落盘 |
| frames | file[] | ✅ | 1~12 帧。K 越大微时序证据越强，但 1h 周期下 K>5 收益很小 |
| tsMs | long | ❌ | 采样时间戳（毫秒）。`0` = 用服务器当前时间 |
| name | string | ❌ | 机位显示名 |
| reset | bool | ❌ | `true` = 先清空该机位基线（演示/换机位时用） |
| params | string(JSON) | ❌ | 覆盖判定阈值，键见 SPARSE.md §7 |

响应（节选，完整字段见 SPARSE.md §5）：

```json
{
  "cameraId": "cam-demo-01",
  "tsMs": 1761212345678,
  "slot": 15,
  "verdict": "persistent_covered",
  "verdictLabel": "持续遮挡",
  "level": "critical",
  "alarm": true,
  "tamperType": "covered",
  "risk": 0.87,
  "confidence": 0.85,
  "matrix": { "dHist": 0.9992, "dBurst": 0.04, "cell": "persistent" },
  "layers": {
    "L2": { "sharpness": 73.6, "entropy": 2.2, "edgeDensity": 0.0063 },
    "L1": { "quadrant": "Q2", "sigmaPerFrame": 0.97, "intraMad": 0.0008 },
    "L0": { "dHist": 0.9992, "pixelOutlierRatio": 1.0, "entropyDrop": 0.65 }
  },
  "reasons": ["熵相对基线下降 65.4%（阈值 30%）：灰度分布向少数色阶塌陷"],
  "baseline": { "status": "warming", "samples": 3, "admitted": true },
  "timingsMs": { "L2": 17.3, "L1": 30.6, "L0": 0.6, "total": 49.2 },
  "keyframe": "data:image/jpeg;base64,...",
  "baselineCompare": "data:image/jpeg;base64,...",
  "diffHeatmap": "data:image/jpeg;base64,..."
}
```

字段语义：

- `verdict`：11 种判决之一，见 SPARSE.md §3；`alarm=true` 的只有 6 种。
- `matrix`：稳定性分解结果。`cell=persistent` 表示"历史偏离高 + 微时序静止"。
- `layers.L1.quadrant`：运动×噪声 2×2 象限。**注意**：冻结也可能由跨采样点的
  D1（逐位相同）单独触发，此时象限可能仍是 Q2 —— 两者不矛盾，
  `matrix.freezeEvidence.d1/d3` 标明到底是哪条判据命中的。
- `baseline.admitted`：本次样本是否并入基线。只有正常样本才准入，
  异常样本一律不进（否则一次遮挡会把基线污染成"被遮挡的样子"）。
- `keyframe` / `baselineCompare` / `diffHeatmap`：三张证据图，直接可放进 `<img src>`。

## 算法状态

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/algo/algorithms` | 透传算法层清单；`online=false` 时前端使用内置兜底元数据 |
| GET | `/api/algo/health` | 算法层健康状态与 YOLO/GPU 信息 |
