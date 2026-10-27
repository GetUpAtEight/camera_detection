# 候选算法评审：`G:\project\算法`（3 仓库 / 4 个脚本）

评审日期：2026-10-20
评审对象：`CCTV-Camera-Tampering-Detection`、`Camera-Tampering-Detection-using-Opencv-Python`、`CameraTamperingDetection`
评测数据：FLAD `video1.mp4`（884.6s / 1280×720 / 30fps），标注 `Survey/baseline.py` 的 20 段
（moved 4 / covered 12 / defocused 4），采样 3fps（`step=10`），每段前 12~20s 预热。

## 1. 结论速览

**四个脚本里只有 3 个不同算法（`cpu` / `gpu` / `video` 三份是同一套直方图法），都不能当主力，已按「辅助算法」接入。**

评审时（2026-10-20）结论是三者都不值得集成。后续按使用方要求**以辅助算法形式加入**
（`kind=legacy`、默认不勾选、不参与 ≥2 票投票），只输出独立指标与逐帧分数，
详见 §6 与 [ALGORITHMS.md §7](ALGORITHMS.md)。下面的实测数据仍然成立，它决定了这三个算法只能当对照信号：
它们要么召回不足（`mog2_area` 3/20、`frame_area` 8/20），要么与现有信号完全不可分（`shortlong`）。

| 候选 | 方法 | 检出（20 段） | 误报（25 窗口） | P | R | F1 | 结论 |
|------|------|--------------|----------------|---|---|---|------|
| `mog2_area` | MOG2 前景面积占比 > 33% | 3 | 1 | 0.75 | 0.15 | 0.25 | ❌ 召回太低 |
| `frame_area` | 帧差 + 最大连通域 > 40% 全图 | 8 | 0 | 1.00 | 0.40 | 0.57 | ❌ 与 `scene_diff` 重复且更差 |
| `shortlong` | 短/长期直方图差异 `ln(d_between/d_long)` | 0 | 0 | — | 0.00 | 0.00 | ❌ 正负样本完全不可分 |
| （参照）现有 ensemble | 5 算法 ≥2 票 | 12 | 2 | 0.86 | 0.60 | 0.71 | — |
| （参照）`scene_diff` 单算法 | 参考帧平均绝对差 | 13 | 6 | 0.68 | 0.65 | 0.67 | — |

另测了"把 `frame_area` 的最大连通域占比用作 `scene_diff` 的遮挡/移位判别规则"：
类型一致率 **4/20**，比现有规则（5/20）**更差**，因此连这个局部借鉴也不采纳。

## 2. 逐个分析

### 2.1 `mog2_area` — MOG2 前景面积（Camera-Tampering-Detection-using-Opencv-Python）

```python
fgbg = cv2.createBackgroundSubtractorMOG2()
mask = fgbg.apply(frame)  # 腐蚀膨胀后统计前景
if Σ(bounding_rect 面积) >= W*H/3: TAMPERING DETECTED
```

* **思路**：遮挡/移位会让"整幅画面"都变成前景，面积占比自然超过 1/3。
* **实测**：TP 3/20、FP 1/25、R 0.15。原因是 FLAD 的篡改多为**局部**遮挡（手挡镜头一角、焦距被动过），
  前景面积到不了 1/3；而阈值调低又会被正常走动的人群/车辆打穿。
* **代码问题**：`_, contours, _ = cv2.findContours(...)` 是 OpenCV 3 的签名，
  在本项目的 `cv2 5.0.0` 上直接抛 `ValueError`；`if(frame is None)` 是死判断；`imshow` 阻塞式循环，无法作为服务调用。
* **结论**：不集成。

### 2.2 `frame_area` — 帧差 + 最大连通域（CCTV-Camera-Tampering-Detection）

```python
diff = absdiff(gray, prev_gray); mask = diff > 30
if max(contourArea) > 0.4 * H * W: tampering
```

* **思路**：与"上一帧"比，而不是与参考帧比；单个连通块足够大才算篡改，用来压制运动的零碎噪点。
* **实测**：TP 8/20、FP 0/25、P 1.00、R 0.40。**它一个误报都没有**，但召回只有 `scene_diff`（R 0.65）的六成。
* **为什么重复**：两者都是"像素差 → 判异常"，只差在①比对对象是参考帧还是上一帧；②用均值还是最大连通块。
  同源信号进同一个投票池，只会让 ≥2 票更容易被同一类误判凑齐，不会带来新信息。
* **结论**：不集成为独立算法。**顺带验证**了它的"最大连通域"能否改善类型判定——见 §3，结果是否。

### 2.3 `shortlong` — 短/长期直方图差异（CameraTamperingDetection，三份同算法）

这是三个仓库里**理论上最讲究**的一个，出自 Sitara & Mehtre 的经典方法：

```
特征 = 彩色度直方图(HSV 50×60) ⊕ L1R 直方图(8×8) ⊕ 梯度方向直方图(16 维)
短池 = 最近 15 帧；长池 = 更早的 36 帧
d_norm = ln( median(短×长 两两 L1 距离) / median(长×长 两两 L1 距离) )
d_norm > 1 → 篡改
```

**优点**：不需要参考帧（自比较）、分母 `d_long` 把"这个机位本来就有的波动"归一化掉了，
理论上比绝对阈值更抗场景变化——这恰好命中我们"`entropy` 的绝对阈值在真实素材上 TP=0"的痛点。

**实测**：20 段全部漏检，d_norm 峰值中位数只有 0.10~0.17，最强的一段（37s/moved）也只有 0.47，够不到阈值 1.0。
进一步做了参数扫描（短池 3/5/15 × 长池 30/60 × 聚合 median/p75），每组都取"正样本 25 分位"作工作点：

| 配置 | 工作点阈值 | 该点召回 | 正样本峰值中位 |
|------|-----------|---------|--------------|
| s5_l30_median | 0.05 | 0.75 | 0.16 |
| s15_l60_p75 | 0.04 | 0.75 | 0.10 |

看起来"压到 0.05 就能召回 75%"，但这是**用阈值换召回**：抽 6 个非标注窗口测同一配置，d_norm 峰值为
`0.082 / 0.225 / 0.123 / 0.155 / 0.038 / 0.176`，其中 5/6 都高于 0.05。
**正样本（中位 0.16）与负样本（中位 0.15）分布完全重叠，任何阈值都无法分开。**

更狠的验证：换用**最敏感的聚合方式** `max`（取 540 对里差异最大的那一对），
正样本峰值中位 **0.016**、负样本中位 **0.068**——**方向都反了**（正常窗口反而更"异常"）：

| `max` 聚合阈值 | 召回 | 误报（6 个非标注窗口） |
|---------------|------|---------------------|
| 0.3 | 1/20 | 0/6 |
| 0.5 / 0.8 / 1.0 | 0/20 | 0/6 |

即"要么几乎不报，要么乱报"，不存在可用工作点。

**根因**（这是方法本身与数据的错配，不是调参能救的）：

1. 该方法在论文里针对的是**持续性篡改**（几十秒到几分钟），用"短池 vs 长池的中位数"描述"最近是否长期偏离历史"。
   FLAD 的标注是 **1~2 秒**的瞬时事件，落在 5 秒短池里只有 2~3 帧，中位数根本压不动；
2. 我们实测了 20 秒级的 `covered` 段（544–568s，73 帧），d_norm 峰值仍只有 0.16——事件结束后长池迅速把异常"学"进去，自适应反而掩盖了长期遮挡；
3. 直方图对**模糊/失焦**本身不敏感（糊掉的画面直方图形状几乎不变），这类事件天生抓不到。

**代码问题**：
* `cupy` 依赖：只把 540×256 的减法从 0.2ms 变 0.1ms，却要多装一个 CUDA 运行时，收益不成比例；
* 每帧 `print(d_norm)` + 每帧 `matplotlib` 重绘（约 30ms/帧），是 demo 写法；
* `d_long == 0` 时 `log(inf)` 无兜底（静止画面必踩）；
* 长池自比较包含对角线上的 36 个 0，会把中位数整体拉低。

**结论**：不集成。若将来要覆盖"摄像机被长时间遮挡/转向"这类**分钟级**场景，值得按以下形式重做后再评估：
短池 5~10s + 长池 3~5min + 用 `p75`/`max` 替代中位数 + 排除自比较对角线 + 阈值按负样本分布定（而不是写死 1.0）。

## 3. 局部借鉴尝试：最大连通域作为类型判别（已否决）

`scene_diff` 目前用"帧差均值 ≥ 60 → 遮挡，否则移位"判类型，实测 20 段里只对 5 段。
`frame_area` 提示了另一种判据：遮挡是"一整块连通区域变化"，移位是"边缘被撕成多块"。于是实测对比：

| 规则 | 判据 | 类型一致率 |
|------|------|-----------|
| A（现状） | 帧差均值 ≥ 60 → covered | 5/20 |
| B（借鉴） | 最大连通域占比 ≥ 0.6 → covered | 4/20 |

FLAD 的 `covered` 多是"手/布遮住镜头一部分"，变化区域并不连通，所以 B 反而更差。
**结论：不改 `scene_diff`。**

## 4. 三条硬约束（也是不集成的理由）

1. **契约不匹配**：三者都是 `while True + imshow` 的演示脚本，没有 `score∈[0,1]`/`is_tampered`/`tamper_type`/`detail` 这套跨层契约，
   也没有逐帧结果与取消机制；改造量约等于重写，而收益（见上表）为负。
2. **信息重复**：`mog2_area`、`frame_area` 与现有 `scene_diff` 属同一信号族（像素级差异 / 前景面积），
   进同一个 ≥2 票池只会放大同向误差。
3. **可信度优先**：给客户演示的系统里加一个在真实素材上 0/20 的算法，比不加更糟——曲线会多一条"永远不动"的线。

## 5. 复现方式

```bash
cd camera-tamper-demo/algo

# 三候选算法在同一口径下评测（正样本=20 段标注，负样本=25 个非标注窗口）
./.venv/Scripts/python.exe -u experiments/eval_candidates.py \
  --video "C:/Users/yzn/Downloads/archive/Videos/video1.mp4" \
  --baseline "C:/Users/yzn/Downloads/archive/Survey/baseline.py" --negatives 25

# shortlong 参数扫描（默认只测标注片段，--negatives N 可加负样本）
./.venv/Scripts/python.exe -u experiments/sweep_temporal.py \
  --video "C:/Users/yzn/Downloads/archive/Videos/video1.mp4" \
  --baseline "C:/Users/yzn/Downloads/archive/Survey/baseline.py"

# 类型判别规则 A/B 对比
./.venv/Scripts/python.exe -u experiments/eval_scene_type.py \
  --video "C:/Users/yzn/Downloads/archive/Videos/video1.mp4" \
  --baseline "C:/Users/yzn/Downloads/archive/Survey/baseline.py"
```

结果落盘：`algo/experiments/eval_candidates_result.json`、`sweep_temporal_result.json`。

## 6. 集成结果：作为辅助算法接入

评审结论是"不能当主力"，不是"不能用"。按使用方要求，三者已作为**辅助算法**集成进系统，
定位是"特定场景下的对照信号"，而不是替代现有集成：

| 算法 | 代码 | 适用场景 | 默认状态 |
|------|------|---------|---------|
| `mog2_area` | `algo/detectors/mog2_area.py` | 固定机位、背景静止，画面被整块物体挡住 | 不勾选 |
| `frame_area` | `algo/detectors/frame_area.py` | 单帧内出现成片遮挡，关注"整块区域同时变化" | 不勾选 |
| `shortlong` | `algo/detectors/shortlong.py` | 分钟级以上持续篡改（长期遮挡、镜头长期偏转） | 不勾选 |

隔离方式（三层，缺一层都可能污染现有结论）：

1. **算法层**：`AUX_DETECTORS` 与 `CLASSIC_DETECTORS` 分开导出，`kind="legacy"`；
   `build_detectors()` 两个列表都遍历（被点名就跑），`/algorithms` 一并返回。
2. **后端**：`AnalysisService.DETECTION_ONLY` 加入三个名字，`computeMetrics` 与
   `buildConsensusEvents` 都跳过它们——它们不会贡献投票，只出现在逐算法指标里。
3. **前端**：`store.classicAlgorithms` 排除 `legacy`，"全选主力"不会带上它们；
   算法卡片按「主力 / 辅助 / 深度学习」三组展示，辅助卡片带「辅助」标签，
   描述首句即"适用场景：…"。需要时点"全选辅助"或单个勾选。

**代码修正**（原实现直接跑会报错或不可用）：

- `mog2_area`：`cv2.findContours` 三返回值签名在 OpenCV 4/5 上抛 `ValueError` → 改两返回值；
  另加 `warmup_frames` 预热，避免首帧"全图前景"造成假告警。
- `shortlong`：`d_long=0` 时 `ln(inf)` → eps 兜底；长池自比较未排除对角线 → 已排除；
  去掉每帧 `print` 与 matplotlib 绘图；cupy 换 numpy；特征前压到 480 宽，
  单帧 53ms → 26ms。

**实测（合成视频 90 帧，三算法同时跑）**：`mog2_area` 告警 15 帧 / 预热跳过 5 帧 / 3.1ms，
`frame_area` 告警 1 帧（只在突变瞬间）/ 0.9ms，`shortlong` 告警 47 帧 / 25.8ms。
`shortlong` 在合成视频上误报偏高、在 FLAD 上全漏检，正说明它**必须按场景标定阈值**，
不能开箱即用——这也是把它放在"辅助"而不是"主力"的直接原因。

复现评测仍用 `algo/experiments/` 下的三个脚本，与主链路口径一致（同采样网格、同参考帧策略、同事件级容差）。
