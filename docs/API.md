# 接口契约

后端统一返回：

```json
{ "code": 0, "message": "ok", "data": { } }
```

`code != 0` 时 `message` 为可读原因。算法层（Python）直接返回业务 JSON，由后端透传。

本文档只覆盖低频巡检。所有路径以 `/api` 为前缀。

---

## 0. 所有接口的公共约定

### 0.1 鉴权（`APP_AUTH_TOKEN`）

后端读配置项 `app.auth.token`，环境变量 `APP_AUTH_TOKEN` 可覆盖（Spring Boot 松散绑定）。

| 后端配置 | 行为 |
|---|---|
| **未配置 / 空串** | **fail-open：一律放行**，启动时日志打印一条告警。适合本地演示 |
| 已配置 | 请求必须携带令牌，否则 `401` |

携带方式（任选其一）：

| 方式 | 示例 | 说明 |
|---|---|---|
| 请求头 Authorization | `Authorization: Bearer <token>` | **主用**，HTTP 标准写法 |
| 自定义头 | `X-API-Token: <token>` | 备用别名，等价，方便脚本/网关透传 |

```bash
curl -H "Authorization: Bearer $APP_AUTH_TOKEN" \
     http://127.0.0.1:8080/api/sparse/cameras
```

鉴权失败响应（`401`，仍是统一包裹结构）：

```json
{ "code": 401, "message": "未授权：缺少或错误的访问令牌", "data": null }
```

> 这是**部署边界**，不是多租户边界：一个令牌等于全部权限。令牌怎么生成、怎么轮换见
> [DEPLOY.md §5 鉴权配置](DEPLOY.md)。

### 0.2 租户（请求头 `X-Tenant-Id`）

所有接口都接受请求头 `X-Tenant-Id`，缺省为 `default`。

后端把 `cameraId` 拼成 `{tenant}:{cameraId}` 再调算法层，因此：

```
X-Tenant-Id: siteA   +  cameraId=cam-01
        ↓ 后端
算法层收到的 cameraId = "siteA:cam-01"
```

由此产生三条使用侧约定：

1. **响应里的 `cameraId` 会带着租户前缀回来**，前端／脚本要比较机位时请按原样比较，
   或自己剥掉前缀，不要假设它等于你传进去的那串。
2. 落盘目录同样按 `{tenant}:{cameraId}` 分文件——换一个租户 id 看到的是另一套基线，
   不会串机位。
3. 租户 id 允许的字符集是**字母数字与 `-_`、`:`**；含其它字符的请求**直接返回 `400`
   拒绝，不做静默清洗**。这是刻意的：静默清洗会把 `tenant/a` 洗成另一个合法机位 id，
   等于悄悄串租／越权。清洗不参与，所以 `X-Tenant-Id` 的取值就是它落盘的取值。

   ```json
   { "code": 400, "message": "非法的 X-Tenant-Id：只允许字母数字与 -_:", "data": null }
   ```

   即便如此，租户 id 仍**不是安全边界**——真正的边界是 0.1 的访问令牌。

```bash
curl -H "X-Tenant-Id: siteA" http://127.0.0.1:8080/api/sparse/cameras
```

### 0.3 服务端时间（响应头 `X-Server-Now`）

后端在每个响应上带一个响应头 `X-Server-Now`，值是**服务端当前时间的毫秒时间戳**：

```
X-Server-Now: 1700000000000
```

用途：客户端没传 `tsMs`（或传 0）时，前端可以用它作为采样时间兜底，
避免依赖本机时钟（本机时钟被改／漂移会污染「按小时分槽」的基线）。
抽样接口在服务端补上时间时，响应体里的 `tsMs` 与它一致。

---

## 1. 健康检查

### GET `/api/algo/health`

```json
{
  "code": 0,
  "data": {
    "baseUrl": "http://127.0.0.1:8000",
    "online": true,
    "detail": {
      "status": "ok",
      "version": "3.0.0",
      "service": "sparse-inspection",
      "uptimeSeconds": 128,
      "cv2": "5.0.0",
      "numpy": "2.4.6",
      "cameras": 2,
      "dataDir": "/…/algo/data/sparse"
    }
  }
}
```

`online=false` 时 `data.error` 给出原因，不抛 5xx（页面据此显示「算法服务离线」）。

---

## 2. 参数元数据

### GET `/api/sparse/meta`

```json
{
  "params": [
    {
      "key": "dHistAlarm", "label": "历史偏离告警阈值",
      "default": 0.15, "min": 0.05, "max": 0.9, "step": 0.01,
      "unit": "", "description": "dHist 超过它才认为画面显著偏离历史基线"
    }
  ],
  "verdicts": [
    { "key": "normal", "label": "正常", "alarm": false, "tamperType": "normal",
      "description": "…" }
  ],
  "defaults": { "dHistAlarm": 0.15, "outlierAlarm": 0.1 }
}
```

---

## 3. 提交抽样

### POST `/api/sparse/sample`（multipart）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `cameraId` | string | ✅ | 机位 id（基线按它落盘） |
| `frames` | file[] | ✅ | 1~12 帧；推荐 3 帧（间隔约 1 秒） |
| `tsMs` | long | | 采样时间戳；0 或不传表示用服务器当前时间（取其值见响应头 `X-Server-Now`，见 §0.3） |
| `name` | string | | 机位显示名 |
| `reset` | bool | | true 表示先清空该机位基线 |
| `params` | string | | 判定阈值覆盖（JSON 字符串） |

响应（节选）：

```json
{
  "cameraId": "cam-01",
  "tsMs": 1700000000000,
  "slot": 22,
  "frames": 3,
  "verdict": "persistent_covered",
  "verdictLabel": "持续遮挡",
  "level": "critical",
  "alarm": true,
  "tamperType": "covered",
  "risk": 0.87,
  "confidence": 0.85,
  "reasons": ["全局对比度相对基线下降 98.9%（阈值 55%）：画面趋于单一颜色 ⇒ 遮挡"],
  "matrix": { "dHist": 0.9992, "dBurst": 0.04, "cell": "persistent", "baselineReady": true },
  "layers": {
    "L2": { "sharpness": 73.6, "entropy": 2.2, "edgeDensity": 0.0063, "luma": 82.6 },
    "L1": { "quadrant": "Q2", "sigmaPerFrame": 0.97, "intraMad": 0.0008 },
    "L0": {
      "dHist": 0.9992,
      "pixelOutlierRatio": 1.0,
      "entropyDrop": 0.65,
      "contrastDrop": 0.989,
      "photometric": { "ok": true, "gain": 1.0, "residualRatio": 0.03, "applied": false },
      "landmarks": { "configured": true, "consensus": { "moved": false } }
    }
  },
  "baseline": { "status": "ready", "samples": 4, "admitted": false },
  "timingsMs": { "L2": 17.3, "L1": 30.6, "L0": 0.6, "total": 49.2 },
  "keyframe": "data:image/jpeg;base64,…",
  "baselineCompare": "data:image/jpeg;base64,…",
  "diffHeatmap": "data:image/jpeg;base64,…"
}
```

**判决取值**：`normal`、`cold_start`、`information_insufficient`、`transient_event`、
`object_change`、`freeze_replay`、`persistent_covered`、`persistent_defocused`、
`persistent_moved`、`persistent_change`、`being_rotated`、`no_signal`。

`alarm=true` 的只有：`freeze_replay`、`persistent_*`、`being_rotated`。

---

## 4. 机位

### GET `/api/sparse/cameras`

```json
{ "cameras": [ { "cameraId": "cam-01", "name": "北门", "status": "ready",
                 "samples": 12, "updatedMs": 1700003600000 } ] }
```

`status`：`empty` / `cold_start` / `warming` / `ready`。

### GET `/api/sparse/state/{cameraId}`

```json
{
  "cameraId": "cam-01",
  "name": "北门",
  "createdMs": 1699000000000,
  "updatedMs": 1700003600000,
  "status": "ready",
  "samples": 12,
  "slots": [ { "slot": 9, "hour": 9, "samples": 3, "ready": true, "latestTs": 1700003600000 } ],
  "history": [ { "tsMs": 1700003600000, "verdict": "normal", "label": "正常",
                 "alarm": false, "dHist": 0.03, "dBurst": 0.1, "quadrant": "Q1", "slot": 9 } ],
  "landmarks": [ { "id": 0, "x": 0.08, "y": 0.15, "w": 0.2, "h": 0.3,
                   "kind": "landmark", "label": "左楼角", "hasPatch": true } ],
  "templates": [ { "id": "lm0t0", "landmarkId": 0, "label": "主模板",
                   "source": "calibration", "createdAt": 1699000000000 } ],
  "audit": [ { "ts": 1700000000000, "action": "add_template", "actor": "ops", "detail": {} } ],
  "baseline": [ { "slot": 9, "tsMs": 1700003600000, "entropy": 6.3, "sharpness": 880.1 } ]
}
```

### POST `/api/sparse/state/{cameraId}/reset`

清空基线库与判定历史，**保留参照物、模板库、审计**。

```json
{ "cameraId": "cam-01", "status": "empty", "samples": 0 }
```

### DELETE `/api/sparse/state/{cameraId}`

删除整个机位（基线 + 参照物 + 模板 + 审计）。

```json
{ "deleted": true }
```

### GET `/api/sparse/thumb/{cameraId}/{slot}/{index}`

返回 `image/png`，基线样本缩略图（160×90）。

---

## 5. 参照物与忽略区

### POST `/api/sparse/landmarks/{cameraId}`（multipart）

| 字段 | 类型 | 说明 |
|---|---|---|
| `landmarks` | string(JSON) | 数组，坐标归一化到 `[0,1]` |
| `file` | file | 当前画面（可选；不传则用基线中位数背景截取模板） |
| `clear` | bool | true 表示先清空再写入 |
| `admitFrame` | bool | 默认 true：这张画面同时作为基线样本入库（先过质量闸） |

`landmarks` 元素：

```json
{ "x": 0.08, "y": 0.15, "w": 0.20, "h": 0.25, "label": "左楼角", "kind": "landmark" }
```

`kind`：`landmark`（参照物，参与移位判定）或 `ignore`（忽略区，不参与基线偏离统计）。

响应：

```json
{ "cameraId": "cam-01", "landmarks": [ … ], "frameAdmitted": true,
  "frameAdmitReason": "标定画面已作为基线样本加入时段槽 9:00" }
```

### GET `/api/sparse/landmarks/{cameraId}`

```json
{ "cameraId": "cam-01", "landmarks": [ { "id": 0, "label": "左楼角", "hasPatch": true } ] }
```

### DELETE `/api/sparse/landmarks/{cameraId}`

清除全部参照物与忽略区（含模板库）。

---

## 6. 模板库

### GET `/api/sparse/landmarks/{cameraId}/templates`

```json
{
  "templates": [
    { "id": "lm0t0", "landmarkId": 0, "label": "主模板", "source": "calibration",
      "createdAt": 1699000000000, "quality": {}, "hasPatch": true },
    { "id": "lm0t1", "landmarkId": 0, "label": "夜间版", "source": "false_alarm",
      "createdAt": 1700000000000, "verdictAtTime": "persistent_moved",
      "quality": { "texture": 123.7, "entropy": 5.96, "sharpness": 4059.5, "contrast": 59.1 } }
  ],
  "audit": [ { "ts": 1700000000000, "action": "add_template", "actor": "ops" } ],
  "limits": { "maxPerLandmark": 8 }
}
```

### POST `/api/sparse/landmarks/{cameraId}/templates`（multipart）

| 字段 | 类型 | 说明 |
|---|---|---|
| `landmarkId` | int | 参照物 id |
| `file` | file | 模板画面（必填） |
| `label` | string | 名称，如「夜间版」「雨天版」 |
| `source` | string | `manual` / `import` / `false_alarm` |
| `actor` | string | 操作人（写审计） |
| `verdictAtTime` | string | 添加时系统的判定结论（写审计） |
| `force` | bool | 质量闸不通过时强制添加（审计会标记） |

成功：

```json
{ "added": true, "templateId": "lm0t1", "landmarkId": 0,
  "quality": { … }, "forced": false, "templateCount": 2 }
```

重复（与已有模板相似度 ≥0.98）：

```json
{ "added": false, "duplicate": true, "templateId": "lm0t0", "similarity": 0.997 }
```

质量闸拒绝 → `400`，`message` 形如：
`模板质量不合格：区域对比度过低（6.1，疑似被遮挡）。如果确认这是正常画面，可强制添加`

### DELETE `/api/sparse/landmarks/{cameraId}/templates/{templateId}`

主模板（`…t0`）不可删除，返回 400。

### GET `/api/sparse/templates/{cameraId}/{templateId}/preview`

返回 `image/png`（梯度幅值渲染），用于前端展示模板外观。

---

## 7. 错误码

| HTTP | 场景 |
|---|---|
| 400 | 参数错误（缺 cameraId、帧数超限、解码失败、质量闸拒绝、主模板不可删）；`X-Tenant-Id` 含非法字符（见 §0.2） |
| 401 | 已配置 `APP_AUTH_TOKEN` 但请求没带令牌或令牌不对（未配置时不会出现，见 §0.1） |
| 404 | 缩略图/模板不存在 |
| 413 | 单帧超过 `ALGO_MAX_UPLOAD_MB` 或后端 multipart 上限 |
| 502 | 后端连不上算法层（算法层未启动） |
| 500 | 内部错误（**只返回通用提示**，详情在服务日志） |
