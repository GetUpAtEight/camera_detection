# 算法说明

九个算法统一实现 `BaseDetector` 接口（`algo/detectors/base.py`）：5 个主力经典算法（参与投票）+ 3 个辅助算法（不参与投票）+ YOLO 深度学习检测。

```python
class BaseDetector:
    name: str            # 唯一标识
    display_name: str    # 中文名
    requires_reference: bool
    param_specs: list    # 参数元数据（前端动态表单）
    def set_reference(self, frame): ...
    def process(self, frame) -> DetectionResult: ...
```

## 0. 统一约定

### 0.1 异常分（score）

`score ∈ [0,1]`，**0.5 恰好对应各算法自己的判定阈值**，由 `severity()` 统一映射：

| 指标方向 | 映射公式 | 含义 |
|---------|---------|------|
| 越大越异常（帧差、平移量…） | `0.5 + 0.5 × (metric − threshold) / threshold` | 指标 = 2×阈值 时封顶 1.0 |
| 越小越异常（清晰度、熵…） | `0.5 + 0.5 × (threshold − metric) / threshold` | 指标 = 0 时封顶 1.0 |

这样多算法可以同轴对比，前端只画一条 0.5 判定线。**`is_tampered` 始终由原始指标是否越过阈值决定**，而不是 `score > 0.5` 的近似。

### 0.2 confidence

`confidence = |score − 0.5| × 2`，表示"离判定线有多远"，**与是否异常无关**：score=0 的正常帧同样很"确定"。

### 0.3 参考帧

| 场景 | 参考帧来源 |
|------|-----------|
| 视频（整段） | 第一个采样帧 |
| 视频（分段） | **段首前 `refGapMs`（默认 1000ms）处的那一帧** |
| 图片 | 上传时可选附带的参考帧 |
| 缺失 | 依赖参考帧的算法返回 `skipped=true` + 原因，不伪造结论 |

**分段为什么不能直接拿段内首帧当参考帧**：标注区间常常正好从"画面已经糊了"那一刻开始，段内首帧本身已是异常帧，拿它当镜子会让 `参考帧 vs 当前帧` 的对比全部失真（失焦段的相对下降比恒为 0，永远检不出来）。所以在段首**之前**再退 `refGapMs` 取一帧干净画面：

```
refMs = max(0, segment.startMs - refGapMs)     # seek 到该时间点，读一帧
```

`refGapMs` 可通过请求参数调整：标注切得很紧（前一段篡改一直延续到本段开头）时可调小到 300–500ms；画面变化剧烈（手持走动）时可调大到 1500–2000ms。

**采样网格是全局的**：抽帧仍按绝对帧号 `index % step == 0` 取模（只是用 `CAP_PROP_POS_FRAMES` seek 到段起点），时间戳保持绝对毫秒。所以同一段区间，"整段跑"和"分段跑"得到的帧号、时间戳完全一致，两次结果可以直接对比（实测 12 帧全对）。

---

## 1. 拉普拉斯方差（失焦检测）`laplacian`

**原理**：失焦使边缘退化，Laplacian 二阶响应方差显著下降。

```
gray = cvtColor(frame, BGR2GRAY)
fm   = Laplacian(gray, CV_64F).var()
```

**判定**：`fm < threshold`（默认 100）→ 失焦；若提供参考帧，额外计算相对下降比 `1 − fm/refFm`，超过 `relative_ratio`（默认 0.45）也判失焦（适应本身就很模糊的机位）。

| 参数 | 默认 | 说明 |
|------|------|------|
| threshold | 100 | 清晰度阈值，越高越敏感 |
| relative_ratio | 0.45 | 相对参考帧下降比，0 表示不启用 |

**detail**：`sharpness`、`referenceSharpness`、`sharpnessDropRatio`、`absoluteThreshold`

**局限**：夜景/大雾/低照度本身方差低，易误报 → 建议开启相对模式或调低阈值。

---

## 2. 直方图熵（遮挡检测）`entropy`

**原理**：遮挡后画面灰度分布趋于单一，信息熵下降。

```
hist = calcHist(gray, [0], [256], [0,256])
p    = hist / hist.sum()
H    = -Σ p·log2(p)          # p>0
```

**增强**：4×4 分块熵，统计"低熵块占比"。**用占比而不是"任一块低熵"**——大面积白墙/天空本身就有低熵块，按单块判定会持续误报（实测合成场景误报率 100% → 改为占比后归零）。

**判定**：`H < 3.0` **或** 低熵块占比 ≥ `block_ratio`（默认 0.5）→ 遮挡。

| 参数 | 默认 | 说明 |
|------|------|------|
| threshold | 3.0 bit | 全局熵阈值 |
| block_threshold | 2.0 bit | 单块"无纹理"阈值 |
| block_ratio | 0.5 | 低熵块占比阈值 |
| grid | 4 | 分块数 grid×grid |

**detail**：`entropy`、`minBlockEntropy`、`blockEntropies[16]`、`lowEntropyBlocks`、`lowBlockRatio`

---

## 3. 帧差 / 场景差异（遮挡 + 移位）`scene_diff`

**原理**：与参考帧逐像素比较，遮挡或视角变化时平均绝对差骤增。

```
diff = absdiff(gray, ref_gray).mean()
```

可选先做高斯模糊（`blur_kernel`）抑制噪声。

**判定**：`diff > threshold`（默认 30）→ 异常；`diff ≥ covered_threshold`（默认 60）判为遮挡，否则判为移位。

| 参数 | 默认 | 说明 |
|------|------|------|
| threshold | 30 | 帧差阈值 |
| covered_threshold | 60 | 判为遮挡的帧差 |
| blur_kernel | 5 | 高斯核，0 关闭 |

**detail**：`sceneDiff`、`changedPixelRatio`（>25 灰度差的像素占比，对局部大面积遮挡更敏感）

**局限**：无法区分"真正的攻击"与"正常的大范围场景变化"（如开灯、有人走过），需要配合其他算法与多算法投票。

---

## 4. ORB 特征匹配 + 单应性（移位检测）`orb`

**原理**：镜头被转动/挪动时画面依旧清晰，前三个算法可能全部漏报。用特征匹配估计视角变化。

```
kp, des = ORB(nfeatures).detectAndCompute(gray)
knn     = BFMatcher(HAMMING).knnMatch(ref_des, des, k=2)
good    = [m for m,n in knn if m.distance < ratio_test * n.distance]
H, mask = findHomography(src, dst, RANSAC, 3.0)
tx, ty  = H[0,2], H[1,2];  translation = √(tx²+ty²)
rotation= degrees(atan2(H[1,0], H[0,0]));  scale = √|det(H[:2,:2])|
inlier_ratio = mask.sum() / len(good)
```

**判定**：`translation > motion_pixels`（默认 50）**或** `inlier_ratio < min_inlier_ratio`（默认 0.3）→ 移位。

**边界处理**：描述子为空、`good < 4`、单应性求解失败，都返回 `score=0 + note`，不会抛异常；参考帧/当前帧尺寸不一致直接跳过。

| 参数 | 默认 | 说明 |
|------|------|------|
| motion_pixels | 50 px | 平移阈值 |
| n_features | 800 | ORB 特征点数 |
| min_inlier_ratio | 0.3 | 内点率下限 |
| ratio_test | 0.75 | Lowe 比值检验 |

**detail**：`translation`、`tx/ty`、`rotationDeg`、`scale`、`inlierRatio`、`matches/goodMatches`

**可视化**：参考帧与当前帧并排 + RANSAC 内点连线（绿色）。

---

## 5. 边缘消失率（统一检测）`edge`

**原理**（MDPI Sensors 2015）：遮挡与失焦都会让参考帧中原本存在的边缘消失，用"消失率"统一衡量。

```
ref_e = Canny(ref_gray);  cur_e = Canny(gray)
cur_d = dilate(cur_e, tolerance)             # 容忍 1~2px 抖动/压缩噪声
rate  = |ref_e ∧ ¬cur_d| / |ref_e|
```

**自适应阈值**：与"正常帧"的边缘消失率时间均值做差，`deviation > threshold`（默认 0.25）且 `rate > min_rate`（默认 0.2）→ 异常。

三点关键实现细节：

1. 基线只累计**未告警帧**；否则长时间遮挡会把基线抬到 1.0，之后再也不报警；
2. 历史不足 3 帧时基线取 0（参考帧自身的消失率为 0，不会冷启动误报）；
3. 当前帧边缘先膨胀再比较，避免画面轻微抖动导致大面积假"消失"。

| 参数 | 默认 | 说明 |
|------|------|------|
| threshold | 0.25 | 相对基线的偏差阈值 |
| min_rate | 0.2 | 最低绝对消失率，抑制冷启动误报 |
| canny_low / canny_high | 50 / 150 | Canny 双阈值 |
| tolerance | 2 px | 边缘膨胀容差 |
| history | 60 帧 | 基线时间窗口 |

**detail**：`edgeDisappearanceRate`、`baseline`、`deviation`、`referenceEdgePixels`、`edgeDensity`

**可视化**：当前帧边缘（白）+ 相对参考帧消失的边缘（红）叠加。

---

## 6. YOLO 深度学习检测 `yolo`

**定位**：演示"业务后端 → 算法层 → GPU 模型"的完整链路，输出标准目标检测结果。
**不参与篡改判定**：`score` 恒为 0、`is_tampered` 恒为 false，检测明细放在 `detail.detections`。

**并发**：模型只在 `DynamicBatcher` 的单一工作线程内被调用（`algo/batching.py`）：

- HTTP 处理线程 / 视频检测线程只往队列投递 `(image, future)`；
- 工作线程被唤醒后在 `maxWaitMs` 窗口内攒满 `maxBatch` 张再 `model.predict(images)`；
- 队列有界 → 天然背压；推理异常逐条回填 Future，调用方不会挂死；
- 启动时 `warmup` 2 轮 + `torch.cuda.synchronize()`，避免首个真实请求承担 CUDA 初始化。

**优雅降级**：`ultralytics` 未安装或模型文件缺失时，`available=false`，检测返回 `skipped=true` 且 `note` 写明原因（例如 `YOLO 不可用：FileNotFoundError: 模型文件不存在：/root/yzn_model_api/yolo26n.pt`），整条链路不崩。

| 参数 | 默认 | 说明 |
|------|------|------|
| conf | 0.25 | 置信度阈值 |
| imgsz | 640 | 推理尺寸 |
| max_detections | 50 | 最多保留框数 |

**调参建议**：演示时把 `conf` 降到 0.15~0.25、`imgsz` 用 640；追求吞吐时降低 `imgsz` 到 480 并提高 `max_batch`。

---

## 7. 辅助算法（外部实现，不参与投票）

三个算法来自 `G:/project/算法` 下的开源实现，`kind = "legacy"`，默认**不勾选**。
它们与主力算法在信号上同源（前两个都是"像素差 → 判异常"）或时间尺度不匹配（第三个面向分钟级），
进 ≥2 票池只会放大同向误差，因此只输出独立指标与逐帧分数，供对照和特定场景使用。

### 7.1 MOG2 前景面积 `mog2_area`

**来源**：`Camera-Tampering-Detection-using-Opencv-Python`。
**做法**：MOG2 建立背景模型 → 前景像素占比 > `ratio`（默认 33%）判遮挡。
**适用场景**：固定机位、背景基本静止的监控（走廊、围墙、无人值守机房），画面被整块物体挡住。
**不适用**：手持/云台机位；分钟级持续遮挡（背景模型会把遮挡学进去，需要把 `learning_rate` 调小）。

| 参数 | 默认 | 说明 |
|------|------|------|
| ratio | 0.33 | 前景占比阈值 |
| history | 300 | 背景模型历史帧数 |
| var_threshold | 16 | 前景判定方差倍数 |
| learning_rate | -1 | 负数=自动；调小可让持续遮挡更久才被吸收 |
| warmup_frames | 5 | 预热帧数，避免首帧全图前景造成假告警 |

**与原实现的差异**：原代码 `_, contours, _ = cv2.findContours(...)` 是 OpenCV 3 的三返回值签名，
在 OpenCV 4/5 上直接抛 `ValueError`，这里改用两返回值版本；另加预热与形态学开运算。

### 7.2 帧差最大连通域 `frame_area`

**来源**：`CCTV-Camera-Tampering-Detection/tamper_detect.py`。
**做法**：与上一帧做差 → 二值化 → 腐蚀膨胀 → 取最大连通域面积 / 全图 > `area_ratio`（默认 40%）。
**适用场景**：单帧内出现成片遮挡（贴纸、手、喷漆、糊镜头），关注"是否有一整块区域同时变化"。
**不适用**：只在画面突变瞬间敏感，持续遮挡要靠 `scene_diff`；机位平移会把整幅差分撑大 → 误判为遮挡。

| 参数 | 默认 | 说明 |
|------|------|------|
| area_ratio | 0.40 | 最大连通域占比阈值 |
| diff_threshold | 30 | 像素差阈值 |
| dilate_iterations | 3 | 膨胀次数，越多越容易连成大区域 |

### 7.3 短/长期直方图 `shortlong`

**来源**：`CameraTamperingDetection`（cpu / gpu / video 三份是同一算法）。
**做法**：彩色度直方图 + 亮度-色差直方图 + 梯度方向直方图（3320 维），
`d_norm = ln(短池与长池的中位距离 / 长池自比较中位距离)`，> `threshold` 判异常。
**适用场景**：分钟级以上持续篡改（长期遮挡、镜头被转向后一直不动、画面长时间异常）。
不用参考帧、能自适应场景本身的抖动幅度是它的优势。
**不适用**：1~2 秒的瞬时事件——短池里只有两三帧，中位数压不动。

| 参数 | 默认 | 说明 |
|------|------|------|
| threshold | 1.0 | d_norm 阈值（原论文取值，约 2.7 倍距离） |
| short_size | 15 | 短池帧数；抓秒级事件需调到 3~5 |
| long_size | 36 | 长池帧数（正常波动基准） |
| min_long | 8 | 长池冷启动最少样本数 |

**与原实现的差异**：`d_long` 为 0 时用 eps 兜底；长池自比较排除对角线；去掉每帧 `print` 与 matplotlib 绘图；
cupy 换 numpy；特征前先把长边压到 480，单帧从 53ms 降到 26ms。

**实测（FLAD video1，20 段标注 + 25 个负样本窗口）**：`mog2_area` 召回 3/20、`frame_area` 8/20、
`shortlong` 0/20（正负样本峰值分布完全重叠）。所以三者只能作为对照信号，不能替代主力算法。
完整评审见 [ALGORITHM_REVIEW.md](ALGORITHM_REVIEW.md)。

---

## 8. 算法选择建议

| 攻击类型 | 首选 | 辅助 | 说明 |
|---------|------|------|------|
| 遮挡（手/胶带/喷漆） | entropy、scene_diff | edge、laplacian、frame_area、mog2_area | 遮挡同时导致熵下降 + 帧差骤增 + 边缘消失 |
| 失焦（调焦/涂抹） | laplacian | edge | 熵通常不变，帧差变化小 |
| 移位（转动/挪动） | orb | scene_diff | 清晰度与熵都正常，只有视角变化 |
| 综合/不确定 | edge（统一检测） | 全部 | 边缘消失率对遮挡+失焦都有效 |
| 长时异常（分钟级） | edge、scene_diff | shortlong | 只有 shortlong 用长池自适应，能识别"一直不正常" |

**多算法投票**：分析页的"篡改事件"以 ≥2 个主力算法同时告警为准（只选 1 个主力时为 1）。
辅助算法（`kind=legacy`）与 YOLO 不计入投票，见 `AnalysisService.DETECTION_ONLY`。

---

## 9. 性能实测（本地 CPU，960×540）

| 算法 | 单帧耗时（ms） |
|------|---------------|
| entropy | 3.8 |
| scene_diff | 6.8 |
| edge | 5.4 |
| mog2_area | 3.1（640×360 合成视频实测） |
| frame_area | 0.9 |
| shortlong | 25.8（含每 5 帧一次长池自比较） |
| laplacian | 17.2（`CV_64F` 全图 Laplacian） |
| orb | 26.6（800 特征点 + RANSAC） |

采样 10fps 的 5.2s 视频（44 采样帧 × 5 算法）端到端 5.1s，其中算法耗时约 2.6s，其余为解码、可视化编码与落库。
性能优化优先级：`orb.n_features` → `laplacian` 降采样 → 减少 `sampleFps`。

---

## 10. 自动场景标定（实时监控的学习期）

### 10.1 为什么需要

各算法的默认阈值是在**合成样本**上定的，而真实画面的正常波动要大一个数量级。
同一条 2654 帧的公开素材（FLAD video1）实测各算法主指标的**正常帧**分位数：

| 算法 | 主指标 | 正常 p95 | 默认阈值 | 后果 |
|------|--------|---------|---------|------|
| `scene_diff` | sceneDiff | 82.5 | 30 | 几乎帧帧告警 |
| `orb` | translation | 445 px | 50 px | 几乎帧帧告警 |
| `edge` | deviation | 0.91 | 0.25 | 几乎帧帧告警 |
| `mog2_area` | foregroundRatio | 0.13 | 0.33 | 从不触发 |
| `frame_area` | largestBlobRatio | 0.10 | 0.40 | 从不触发 |
| `shortlong` | dNorm | 0.29 | 1.00 | 从不触发 |

也就是说：**同一组默认阈值在真实素材上会同时"太灵敏"和"没反应"**——
主力算法被场景自身的抖动刷爆，辅助算法则永远够不到阈值。这组绝对阈值不可能跨场景通用。

### 10.2 做法

开始监控后先进入**学习期**（默认采 20 帧正常画面，间隔 500ms，期间不判定不告警），
按每个算法声明的 `primary_metric` 取样，算出「正常波动上界」，再换算成阈值回填：

```python
primary_metric = (detail 字段, "higher" | "lower", 对应参数 key)
# 越大越异常 → 阈值 = p95 × 余量；越小越异常 → 阈值 = p5 × 余量
```

余量由前端「灵敏度」三档决定：

| 灵敏度 | 越大越异常 | 越小越异常 | 适用 |
|--------|-----------|-----------|------|
| 保守 | ×1.6 | ×0.65 | 只关心确定发生的大事（少误报） |
| 均衡（默认） | ×1.25 | ×0.80 | 日常 |
| 灵敏 | ×0.95 | ×0.90 | 宁可误报也别漏（少漏报） |

阈值算完后通过 `PATCH /api/live/sessions/{id}/params` 下发给会话，**原地更新**算法层里的
检测器实例（不重建，见 10.4），同时把当前画面设为参考帧、清零连续告警计数。
换机位、改光照、换场景后点「重新学习场景」重跑一遍；只改灵敏度则直接用已学到的基线重算，不必重新采集。

### 10.3 效果（同一段素材，`deploy/live_calib_test.py`）

素材 `samples/demo_video.mp4`（0~49 正常 / 50~89 失焦 / 90~129 遮挡），叠加模拟真实 USB 摄像头
的噪声、自动曝光漂移与 1~2px 微抖（`--jitter 3.0`），8 个算法全选，去抖 2 帧：

| | 正常段告警率 | 失焦段告警率 | 遮挡段告警率 | 准确率 |
|---|---|---|---|---|
| 算法层默认参数 | 12.0% | 17.5% | 97.5% | 69.2% |
| 学习期标定后 | **0.0%** | **92.5%** | **100%** | **97.7%** |

默认参数的问题不是"过于灵敏"或"不够灵敏"，而是**两个方向同时错**：正常段乱报 12%，
失焦段只抓到 17.5%。标定后两个方向一起修好。标定得到的阈值示例（同一素材）：

```
laplacian  sharpness        p-base 385.6  → threshold 308.5
entropy    entropy          p-base 6.53   → threshold 5.23
scene_diff sceneDiff        p-base 34.6   → threshold 43.2   （默认 30，明显偏严）
orb        translation      p-base 7.04   → motion_pixels 8.80（默认 50，明显偏松）
edge       deviation        p-base 0.639  → threshold 0.80
mog2_area  foregroundRatio  p-base 0.034  → ratio 0.042
frame_area largestBlobRatio p-base 0.046  → area_ratio 0.057
shortlong  dNorm            p-base 0.434  → threshold 0.542
```

> 注意：这是**模拟**摄像头抖动，不是用户真实摄像头的复现；不同场景学到的阈值本来就不同，
> 这也正是不能用固定值的原因。素材越干净（如合成视频），标定前后差距越小。

### 10.4 一个前提：辅助算法必须保留帧间状态

`mog2_area` / `frame_area` / `shortlong` 都依赖帧间状态（背景模型、上一帧、长池）。
实时链路是「一帧一次 HTTP 请求」，如果每次请求都新建检测器，它们会**永远停在预热的第 1 帧**，
结果是"一次都不告警"——这是本次修复的另一个真实 bug，与阈值无关。

现在算法层按 `sessionId` 复用同一批检测器实例：

```
POST /detect/image          sessionId=<id>   → 复用该会话的检测器
POST /session/{id}/params   params={...}     → 原地改阈值，保留帧间状态
DELETE /session/{id}                         → 会话结束释放
```

重设参考帧时只重建 `requires_reference=True` 的那批（它们的基线绑在旧场景上），
`mog2_area` 等靠累积的实例保留，避免又从头预热。
