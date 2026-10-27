# 01 · 摄像头篡改检测（CTD）算法全景调研

> 任务：t1 · 角色：现有算法与商用产品调研 · 日期：2026-10-22
> 目标场景（全文的判定前提）：**固定机位监控；每 1 小时（可更长）采样一次；每次可拉 1 帧静态图，或连续 K≈3~5 帧（帧间隔 0.5~2 s）；成本敏感，不做实时逐帧检测。**
> 姊妹文档：[02-sparse-adaptation.md](02-sparse-adaptation.md)（本项目 9 个算法的稀疏适配性改造）、[ALGORITHMS.md](../ALGORITHMS.md)（实现细节与实测耗时）、[ALGORITHM_REVIEW.md](../ALGORITHM_REVIEW.md)（FLAD 实测评审）。
>
> **证据等级约定**（本文严格遵守）：
> - `[已读]` 我直接抓取了原文/官方文档并核对了下述说法；
> - `[摘要]` 只读到检索结果的标题+摘要片段，未逐字核对全文；
> - `[未证实]` 检索到线索但无法确认，或业界常见说法但没有可引用来源；
> - `[未检索到]` 明确尽力检索后没有找到。
> 本文所有数字都标了出处；**没有出处的数字一律不写**。

---

## 0. 摘要（TL;DR）

**六条结论：**

| # | 结论 | 依据 |
|---|------|------|
| 1 | **"学术主流"和"本场景"几乎是两个不相交的集合。** 视频取证（video forensics）研究的是"帧级伪造"（删帧/插帧/拼接/换脸），其方法全部依赖**原始帧率的连续帧**（光流一致性、GOP 结构、残差）；而 1 小时采样 + 0.5~2 s burst 恰好破坏了这些前提。能直接用的只有**单帧内在痕迹**与**摄像头健康度（camera health）**这两支。 | §2.2、§2.3、§5 |
| 2 | **真正贴合本场景的学术支流叫 Camera Tampering Detection（CTD），不是 video forgery detection。** 它有独立的问题定义（covered / defocussed / moved 三类）、独立的综述与独立的数据集（UHCTD），而且**被形式化为"时间序列上的残差异常"**——这正是稀疏采样的天然框架。 | [Mantini & Shah 2023](https://arxiv.org/abs/2310.07886) `[已读]` |
| 3 | **单帧就能判的只有"状态已经很坏"的攻击**：全遮挡、严重失焦、黑屏/过曝、明显偏色。任何"相对正常状态的偏离"（中度遮挡、轻度失焦、几像素移位、镜头积灰）**必须**有参考帧或历史基线。这和本项目 [02](02-sparse-adaptation.md) §2 的结论一致，且外部文献同样支持。 | §2.3、§2.4、§5 |
| 4 | **K 帧 burst 在 CTD 文献里几乎没有对应物**——CTD 论文默认处理连续视频流（背景建模需要几十~几百帧）。burst 的独有价值（冻结/重放判定、物理扰动判定）来自**工程实践**（数字水印冻结检测、NVR 健康检查）而非学术主线。这既是缺口，也是本项目的创新空间。 | §2.4.4、§3.3 |
| 5 | **商用产品的实际能力边界比宣传窄，且有一条对本场景致命的机制**：Axis 官方文档明确写"画面被遮挡或转向**超过 10 分钟**，应用会**重新学习场景**并以新场景为基准检测"——即**长期持续篡改会被产品自己"学会"**。海康/大华/博世/威智伦的 tamper 功能同样是"短时突变 + 灵敏度阈值"，不解决"持续数天的遮挡"。 | [AXIS 文档](https://help.axis.com/en-us/axis-image-health-analytics) `[已读]` |
| 6 | **数据集侧最可用的是 UHCTD，最不可用的是 FLAD。** UHCTD 是唯一"监控 + 四类篡改 + 小时级长视频"的公开集（576 次篡改 / 288 小时 / 2 机位）；FLAD 是**会议录制**的内容瑕疵检测集，域差极大（本项目已实测其难度失配，见 [ALGORITHM_REVIEW.md](../ALGORITHM_REVIEW.md)）。AIGC 侧 2026 年才出现监控专用基准 CCDF，且论文自己的结论是"10 个 SOTA 检测器都不可靠"。 | §4 |

**给下游（t3 选型 / t4 新算法）的一句话建议：**
> 主线应是 **"单帧健康度体检（L2） + 跨采样点历史基线（L0，含 PRNU/噪声残差这类可慢积累的指纹） + burst 内的冻结/扰动判定（L1）"**；学术界的 video-forgery 时序模型（3DCNN/Transformer/光流）在本场景**不可直接迁移**，只能借其"时序不一致"的思想，不能借其网络与时序窗口。

---

## 1. 判定口径：怎么给一个算法打"稀疏适配性"分

后文每一节和 §5 的总表都按这 5 个维度标注（与任务书要求一致）：

| 维度 | 取值 | 说明 |
|------|------|------|
| **① 连续帧需求** | 无 / burst(2~5 帧) / 短序列(6~32 帧) / 长序列(>32 帧) / 原始帧率连续流 | "原始帧率"特指必须保持摄像机原生帧率（如 25 fps）才能成立的方法（光流、GOP、帧间残差） |
| **② 最短时序窗口** | 具体帧数或时长 | 论文/文档里能查到就写实数，查不到写"未证实" |
| **③ 参考帧 / 学习期** | 无（no-reference）/ 单参考帧 / 多参考基线 / 需学习期（时长） | 直接决定冷启动成本 |
| **④ 单帧可判** | ✅ / ⚠️（需标定）/ ❌ | "单帧可判"= 不需要任何历史与参考 |
| **⑤ 成本** | CPU 轻（<20 ms/帧）/ CPU 中（20~100 ms）/ GPU 必需 / 训练成本 | 经典算法用本项目实测值（[ALGORITHMS.md](../ALGORITHMS.md) §9，960×540 本地 CPU）；深度模型给出量级并标注是否实测 |

**稀疏适配性分级（全文统一）：**

| 级 | 定义 | 在本场景的含义 |
|----|------|--------------|
| **A** | 1 帧静态图即可判定 | 每次采样拉 1 帧就能跑，零额外成本 |
| **B** | 仅需 K=2~5 帧 burst（帧间隔任意） | 每次多拉 2~4 帧，成本可忽略 |
| **C** | 需要跨采样点的历史基线/长序列（可慢积累） | 冷启动 5~30 天，但**稳态运行时仍只要 1~K 帧** |
| **D** | 需要原始帧率连续帧或超长序列，本场景**不可用** | 无论怎么改都跑不了 |

> 关键区分：**C 不是"不可用"，而是"要攒历史"**。稀疏采样最容易被误解的一点是"没有连续帧所以什么都做不了"——恰恰相反，1 小时一次的采样点序列本身就是一条**时间序列**，CTD 的经典形式化（残差序列 + ARIMA/阈值）就是为这种序列准备的 `[已读]`（[arXiv 2310.07886](https://arxiv.org/abs/2310.07886) §IV-B）。

---

## 2. 学术全景

### 2.0 先正名：CTD ≠ Video Forgery Detection

这两支经常被混为一谈，但问题设定完全不同，**混用会直接选错算法**：

| | **Camera Tampering Detection (CTD)** | **Video Forgery Detection（视频取证）** |
|---|---|---|
| 攻击者目标 | 让摄像头**看不见**（遮挡/失焦/转向/喷漆） | 让视频**说假话**（删掉一段、插入一段、换脸、拼接） |
| 篡改发生在 | **物理世界 / 镜头前**（也可能是视频流替换） | **数字域**（像素、编码、容器） |
| 输入假设 | 单摄像头、固定机位、**允许只有单帧** | 一段**原始帧率**的视频 |
| 典型信号 | 边缘消失率、熵、清晰度、背景差、特征点位移 | 光流一致性、双压缩、PRNU、噪声残差、GOP 异常 |
| 与本场景的关系 | **直接对应**（遮挡/失焦/移位就是本项目三类攻击） | 仅"冻结/重放/拼接"这一小部分相关，且方法多不可迁移 |

CTD 的标准分类是三类 `[已读]`（[Mantini & Shah 2023](https://arxiv.org/abs/2310.07886) §II；[UHCTD devkit README](https://github.com/QuantitativeImagingLaboratory/ctd-devkit)）：

1. **Covered（遮挡）**：喷漆、手挡、灰尘/蛛网/水滴积聚、树叶生长遮挡；
2. **Defocussed（失焦）**：起雾、对焦失败、被故意调焦、日夜切换后自动对焦失败；
3. **Moved（移位）**：大风、被故意转动镜头。

**这个"三类"划分与本项目 `algo/detectors/` 的 `tamper_types` 完全一致**，说明现有工程实现选对了问题域；差距在"稀疏采样下的参考系"，不在问题定义。

该综述的核心方法论贡献值得直接借用：它把 CTD 统一写成 **残差结构** `R = D(T(I_t), F(I_r))`（特征提取 T、参考模型 F、距离 D、判决），并把参考模型分为 **full-reference / reduced-reference / no-reference** 三档 `[已读]`（同上 §III-A）。这正是本场景需要的抽象：**稀疏采样下 F 从"上一帧"变成"历史基线库"，而 T/D 不变**（对应 [02](02-sparse-adaptation.md) §5）。

它还做了一件对本项目极有价值的事：在 UHCTD 上用 **10 类特征**（背景差、背景熵差、时延背景差、直方图峰值差、边缘计数差、梯度幅值差、强边缘梯度差、边缘交集计数、关键点数差、关键点描述子差）逐一做 ARIMA 时序建模，比较"可预测性"与"篡改时的偏离度" `[已读]`（同上 §IV-A/§IV-E）。**结论方向：边缘类与背景类特征在可预测性与偏离度之间取得较好折中**（具体 AUC 数值在原文表格中，本文未逐格抄录，标 `[摘要]`）。这为本项目"边缘消失率 + 背景差"的主力地位提供了外部佐证。

---

### 2.1 方向一：时序一致性 / 光流 / 长短期建模

#### 2.1.1 帧间伪造（删帧 / 插帧 / 复制帧）— 光流一致性

这是"视频取证"里与本场景**最相关也最不可用**的一支。核心假设：删掉若干帧会让运动矢量/光流在拼接点出现**突变**，因为真实运动的加速度是连续的。

| 工作 | 方法要点 | 时序要求 |
|------|---------|---------|
| [Exposing video inter-frame forgery based on velocity field consistency (ICASSP 2014)](https://doi.org/10.1109/icassp.2014.6854085) | 速度场一致性 | 需**原始帧率的连续帧**（至少覆盖拼接点前后数帧）`[摘要]` |
| [A novel video inter-frame forgery model detection scheme based on optical flow consistency (IWDW 2012)](https://dl.acm.org/doi/10.1007/978-3-642-40099-5_22) | 区分插帧 vs 删帧 | 同上 `[摘要]` |
| [Frame Deletion Detection Based on Optical Flow Orientation Variation (IEEE Access 2021)](https://doi.org/10.1109/access.2021.3061586) | 光流方向变化 | 同上 `[摘要]` |
| [Two-Stage Detection and Localization of Inter-Frame Tampering in Surveillance Videos Using Texture and Optical Flow (Mathematics 2024)](https://www.mdpi.com/2227-7390/12/22/3482) | **监控视频专用**，纹理+光流两阶段 | 需连续帧；论文面向完整监控录像取证，非在线抽样 `[摘要]` |

**对本场景的判定：`D`（不可用）。** 原因不是"帧数不够"，而是**帧间隔的物理意义被破坏**：光流一致性检验的本质是"相邻帧之间的位移应当平滑"，而 0.5~2 s 的帧间隔下，位移本身就是任意大的（人可能已经走出画面）。**把 burst 当"稀疏视频"喂给光流法，得到的是无意义的运动矢量。** 这条结论很重要，建议在 t3 选型里明确写出，防止后续有人"顺手"把光流模块接进来。

> 唯一可借用的残余价值：**"帧间不一致"这个思想**可以降级为"burst 内两帧的全局一致性"（即 [02](02-sparse-adaptation.md) §4.6 的 `intraMad` / `intraOrbTranslation`），但这已经和光流无关了。

#### 2.1.2 深度时序模型（3D-CNN / RNN / Transformer）— 时序窗口有多长？

这类模型主要用于 deepfake 视频检测，但"需要多少帧"这个参数是通用的，直接决定它在本场景的死活。检索到的实测窗口（均 `[摘要]`，来自各自论文/报告的参数表）：

| 工作 | 时序窗口 | 备注 |
|------|---------|------|
| [Detecting Deepfake Videos with Temporal Dropout 3DCNN (IJCAI 2021)](https://www.ijcai.org/proceedings/2021/0178.pdf) | 采样连续 **25 帧**，随机取 **20 帧**输入 | 3D-CNN 类典型量级 |
| [Deepfake Detection in Social Media: A Temporal Artifact Analysis Using 3D CNN (arXiv 2605.17573)](https://arxiv.org/html/2605.17573v1) | 系统比较 8~32 帧，**16 帧最优** | 作者明确说"更短序列因时序上下文不足掉 2.7 个点" |
| [Cross-dataset video deepfake detection using Transformer and CNN (MVA 2026)](https://link.springer.com/article/10.1007/s00138-026-01809-w) | 时间窗 **32 / 64 / 96 帧** | Transformer 类通常要更长 |
| [Hybrid CNN-Transformer Architecture for Deepfake Video Detection (UD 报告)](https://csar.ud.ac.ae/wp-content/uploads/projects/deepfake/Hybrid_CNN_Transformer_Architecture_for_Deepfake.pdf) | clip 长度 **8 帧**，frame step 5 | 轻量档 |

**对本场景的判定：`D`。** 两个必要条件同时不满足：
- **帧数**：K=3~5 < 最短可用窗口（8 帧，主流 16~32）；
- **帧率**：这些模型的"帧"是**等间隔、毫秒级间隔**的连续帧，其判别依据是"高频时序纹理"（生成器的逐帧抖动、光流不连续）。0.5~2 s 的间隔下这些高频痕迹**根本不存在**。

> 若一定要用 3D-CNN 类模型，唯一的合法用法是：**把 burst 的 K 帧当作"稀疏序列"重新训练一个专用小模型**（输入维度 K×H×W，K≤5）。这在文献中未见先例（`[未检索到]`），属于 t4 的创新空间，但需要自建数据。

#### 2.1.3 单帧图像篡改定位：MVSS-Net

[MVSS-Net: Multi-View Multi-Scale Supervised Networks for Image Manipulation Detection (ICCV 2021)](https://openaccess.thecvf.com/content/ICCV2021/papers/Chen_Image_Manipulation_Detection_by_Multi-View_Multi-Scale_Supervision_ICCV_2021_paper.pdf)（[PubMed 条目](https://pubmed.ncbi.nlm.nih.gov/35671312/)）`[已读摘要]`：

- **方法**：双"视图"监督 —— **噪声视图**（捕捉局部噪声不一致）+ **边缘视图**（捕捉边界异常），多尺度输出 + 图像级二分类联合训练；目标是同时提高**敏感性**与**特异性**（原文摘要明确说"当前研究强调敏感性而忽视特异性"）。
- **时序要求**：**无**（纯图像方法）。单帧可用 ✅。
- **成本**：GPU 推理（backbone 为 ResNet50 级，双分支）`[未实测]`。
- **对本场景**：`A`（单帧可判）但**不能直接用**。它的训练域是**通用拼接/复制粘贴/修复**（Columbia、CASIA、Coverage、NIST16 之类），不是"监控画面被布挡住""镜头被喷漆"。**它检测的是"这块像素来自另一张图"，不是"这块像素不该存在"**。可作为"画面被替换/拼接"的专用证据，但不能替代遮挡/失焦检测。
- **真正有价值的部分**：它证明了**"噪声视图 + 边缘视图"双流是有效范式**——这与本场景 L2 层"边缘消失率 + 噪声残差 σ"的组合思路同构（见 [02](02-sparse-adaptation.md) §4.2），可以在 t4 借鉴结构。

#### 2.1.4 两处必须澄清的引用（避免下游踩坑）

| 任务书里的名字 | 实际检索结果 | 处置 |
|---|---|---|
| **FETV** | [FETV: A Benchmark for Fine-Grained Evaluation of Open-Domain Text-to-Video Generation (NeurIPS 2023 D&B)](https://github.com/llyx97/FETV) `[已读仓库]`，是**文生视频生成质量评测基准**，与取证/篡改**无关** | 若下游文档把它列为"视频伪造检测方法"，属误引，**应删除** |
| **DVR** | **`[未检索到]`** 以 "DVR" 命名的视频伪造检测算法或数据集。检索到的 DVR 全部指**数字录像机硬件**（如 [Hikvision/Dahua DVR/NVR 取证恢复方法, Information 2025](https://www.mdpi.com/2078-2489/16/11/983)） | 标注"未证实"；若原意是"DVR 设备取证"，见 §2.4.6 的丢帧/重编码部分 |

> 我倾向于认为这两个名字来自上一轮调研的转述误差（429 中断前未核对）。**建议 captain 在最终报告里显式更正**，否则评委会顺着错误引用质疑整份报告的可信度。

---

### 2.2 方向二：单帧 / 图像内在痕迹

这一支是**本场景唯一"零时序依赖"的完整工具箱**，代价是它对"监控特有的物理遮挡"并不直接建模，而是检测"数字处理留下的痕迹"。

#### 2.2.1 JPEG 重压缩 / 双重压缩

| 工作 | 要点 | 对本场景 |
|------|------|---------|
| [Detection of Double-Compression in JPEG Images (IEEE TIFS 2008)](https://dlnext.acm.org/doi/10.1109/TIFS.2008.922456) | 双压缩的 DCT 直方图周期化痕迹，经典基线 | 单帧 ✅（`A`） |
| [Reverse JPEG Compatibility Attack (IEEE TIFS 2019)](https://doi.org/10.1109/tifs.2019.2940904) | 利用解压取整误差方差判定"是否经过 JPEG 流水线"；**原文限定 QF 99/100** `[摘要]` | 高质图像有效；**监控常见低 QF → 适用性受限** |
| [Extending the Reverse JPEG Compatibility Attack to Double Compressed Images (ICASSP 2021)](https://ws.binghamton.edu/fridrich/Research/DoubleCompression-14.pdf) | 扩展到双压缩 | 同上 |
| [Dual JPEG Compatibility: a Reliable and Explainable Tool (arXiv 2408.17106)](https://arxiv.org/abs/2408.17106) | 给出"8×8 块前驱"的可解释判定 | 单帧 ✅，需要**已知压缩流水线参数** |
| [Analysis and Mitigation of the False Alarms of rJCA (IH&MMSec 2023)](https://dl.acm.org/doi/abs/10.1145/3577163.3595092) | **明确指出非受控环境下误报风险** | 重要警告：不要把它当"一票否决"判据 |

**对本场景**：`A`，但**优先级低**。原因：监控链路天然多次转码（相机编码 → NVR 转码 → 平台转码 → 截图），"双重压缩"是**常态而非异常**，判别力被稀释；且低 QF 下 rJCA 类方法前提不成立。**可作"证据链辅助"（这张图是否来自二次编辑），不作在线篡改检测主力。**

#### 2.2.2 重采样 / 缩放痕迹

- [Detection of Upscale-Crop and Partial Manipulation in Surveillance Video Based on Sensor Pattern Noise (Sensors 2013)](https://www.mdpi.com/1424-8220/13/9/12605) `[已读摘要]`：用 MACE-MRH 相关滤波器（对缩放具有不变性）检测**监控视频的放大-裁剪**与局部篡改。这是少数**明确面向监控**的单帧取证工作。
- 对本场景：`A`。"画面被放大后裁剪"（攻击者用一张低分辨率截图冒充实时画面）是真实威胁，且单帧可判；但需要**已知原始分辨率/传感器指纹**作参照 → 实际属于 `C`（需基线）。

#### 2.2.3 噪声不一致 / 传感器指纹（PRNU、Noiseprint）

| 工作 | 要点 | 时序/参考需求 |
|------|------|-------------|
| [Noiseprint: a CNN-based camera model fingerprint (arXiv 1808.08396)](https://arxiv.org/pdf/1808.08396) | 孪生网络抑制场景内容、保留相机模型指纹；用于**伪造定位** | 单帧推理 ✅；需训练 |
| [Combining PRNU and noiseprint for robust device source identification (EURASIP 2020)](https://link.springer.com/content/pdf/10.1186/s13635-020-0101-7.pdf) | 两者互补，提升设备归属鲁棒性 | 需参考图 |
| [Extracting camera-based fingerprints for video forensics (CVPRW 2019)](https://openaccess.thecvf.com/content_CVPRW_2019/papers/Media%20Forensics/Verdoliva_Extracting_camera-based_fingerprints_for_video_forensics_CVPRW_2019_paper.pdf) | **视频**指纹提取（处理压缩/帧间预测带来的困难） | 需多帧估计 |
| [A Video Splicing Forgery Detection and Localization Algorithm Based on Sensor Pattern Noise (Electronics 2023)](https://www.mdpi.com/2079-9292/12/6/1362) | 拼接区域存在**两种 SPN 模式** | 需参考 |
| [An improved surveillance video forgery detection technique using SPN and noise residue correlation (MTAP 2019)](https://link.springer.com/article/10.1007/s11042-019-08236-2) | 监控视频专用 | 需参考 |

**对本场景的判定：`C`（强烈推荐关注）。** 这是我认为**最被低估**的一支，理由是它和稀疏采样**天然互补**：
- PRNU 指纹的估计需要"同一机位的一批干净帧做平均"，**帧数越多越准**——稀疏采样正好提供"跨天、跨光照的大量样本"，可以**离线慢慢攒**，不占用在线预算；
- 一旦指纹建立，**每次采样只要 1 帧**就能算"这帧的噪声残差与指纹的相关性"，从而判定"这帧是否真出自这台传感器"——这直接覆盖了 **视频流替换 / 重放 / 非本机直出** 这一类本项目现有 9 算法**完全没有覆盖**的攻击（[02](02-sparse-adaptation.md) §4.2 的 `sigmaPerFrame` 是它的轻量近似）。
- **风险（必须写明）**：强压缩、缩放、平台转码会**抹掉**指纹 `[摘要]`（[CVPRW 2019](https://openaccess.thecvf.com/content_CVPRW_2019/papers/Media%20Forensics/Verdoliva_Extracting_camera-based_fingerprints_for_video_forensics_CVPRW_2019_paper.pdf) 讨论的正是这个困难）。因此**必须先在本项目真实链路上做可行性验证**（拉一帧 → 解码 → 算残差），否则不要承诺。
- 成本：轻（残差提取是滤波级操作，CPU 可承受）；但"指纹库管理"是新的工程负担。

#### 2.2.4 屏幕翻拍（recapture）

| 工作 | 要点 |
|------|------|
| [An Image Recapture Detection Algorithm Based on ... (IEEE TIFS 2015)](https://www.commsp.ee.ic.ac.uk/~pld/publications/IEEETransactionsINFS_THT_HM_PLD15.pdf) | 经典：利用屏幕像素栅格与相机采样栅格的混叠 |
| [Identification of aliasing-based patterns in re-captured LCD screens (ICIP 2015)](https://doi.org/10.1109/icip.2015.7350872) | 周期性混叠图案 |
| [Doing More With Moiré Pattern Detection in Digital Photos (IEEE TIP 2022)](https://doi.org/10.1109/tip.2022.3232232) | 摩尔纹边缘图提取 |
| [CMA: A Chromaticity Map Adapter for Robust Detection of Screen-Recapture (CVPR 2024)](https://openaccess.thecvf.com/content/CVPR2024/html/Chen_CMA_A_Chromaticity_Map_Adapter_for_Robust_Detection_of_Screen-Recapture_CVPR_2024_paper.html) | 面向社交网络二次压缩后的文档翻拍检测，色度图适配器 |

**对本场景**：`A`（单帧），但**可靠性存疑**。翻拍检测依赖高频混叠/摩尔纹，而监控画面（1080p、强压缩、夜间低照、镜头脏污）本身高频信息就弱；且"攻击者用显示器播放录像骗摄像头"在真实威胁模型里不如"直接切断/替换视频流"常见。**建议列为可选的低优先级证据**，`[未证实]` 其在监控域的实际检出率（未检索到监控场景的翻拍检测评测）。

---

### 2.3 方向三：摄像头特定攻击（逐攻击 × 方法 × 时序要求）

这是**与目标场景重合度最高**的一节。下表把 7 类攻击逐一拆开。注意：**每一类的"最短时序窗口"差别很大**。

#### 2.3.1 遮挡 / 污损（covered / lens soiling）

| 方法族 | 代表工作 | 时序需求 | 单帧 | 稀疏适配 |
|--------|---------|---------|------|---------|
| 熵 / 直方图集中度 | CTD 综述归纳的 reduced-reference 类 `[已读]`（[arXiv 2310.07886](https://arxiv.org/abs/2310.07886) §III-B） | 单帧即可算，**但阈值需参考** | ⚠️ | `A`/`C` |
| 边缘消失率 | 本项目 `edge`（源自 MDPI Sensors 2015 思想，见 [ALGORITHMS.md](../ALGORITHMS.md) §5）；Hikvision 专利族 [EP1936576B1 Camera tampering detection](https://patents.google.com/patent/EP1936576B1/un)（边缘图相似度） | 需参考帧 | ❌ | `C` |
| 背景减除 / MOG2 | [chandan54/Camera-Tampering-Detection-using-Opencv-Python](https://github.com/chandan54/Camera-Tampering-Detection-using-Opencv-Python) | 需 warmup 若干帧 + 在线背景 | ❌ | `D`（与昼夜节律冲突，见 02 §2.1） |
| 4 类图像分类（CNN） | [UHCTD-devkit](https://github.com/QuantitativeImagingLaboratory/ctd-devkit)：AlexNet / ResNet18/50 / DenseNet161，4 类输出 | **单帧**（逐图分类） | ✅ | **`A`（最贴合）** |
| 干净参考 + 滑动统计 | [Clean-Reference Streaming Detection of Lens Occlusion and Photometric Transitions (arXiv 2607.14760, 2026)](https://arxiv.org/abs/2607.14760) | 用"clean-only 滑动参考"比较**采样亮度与局部梯度统计** | ✅ 单帧统计即可 | **`B`/`C`（最贴合工程）** |
| 车载污损检测/去除 | [SoilingNet (arXiv 1905.01492)](https://arxiv.org/abs/1905.01492)、[Let's Get Dirty (IEEE 2021)](https://ieeexplore.ieee.org/document/9423236)、[Let's See Clearly (ICCV 2021)](https://openaccess.thecvf.com/content/ICCV2021/papers/Li_Lets_See_Clearly_Contaminant_Artifact_Removal_for_Moving_Cameras_ICCV_2021_paper.pdf)、[Seeing through Unclear Glass (arXiv 2509.01033)](https://arxiv.org/abs/2509.01033) | 单帧语义分割 | ✅ | `A`（但**域不同**：车载环视，非固定监控） |

**关键洞察（值得写进 t4）：**
1. **UHCTD 的 4 类分类器是"单帧 → 4 类"的直接映射**，与本场景 1 帧模式**完全同构**，是现成的、可复现的 baseline（[README 明确写用 Places365 预训练权重微调](https://github.com/QuantitativeImagingLaboratory/ctd-devkit)）`[已读]`。**这是整份调研里"改造成本最低、收益最确定"的一条。**
2. **"污损"本质是渐进的**（积灰、蛛网、水滴），单帧绝对阈值只能抓"已经糊死"，真正需要的是**跨采样点的趋势**（清晰度/对比度单调下降）——这正是 [02](02-sparse-adaptation.md) §7.3 架构 C 的用武之地，而学术界的污损检测（车载）几乎都假设"当前帧即可判"，**不建模渐进性**。这是明确的缺口。
3. [arXiv 2607.14760](https://arxiv.org/abs/2607.14760) 是本轮检索到**最贴近本场景的一篇**：它明确用"采样亮度+局部梯度"这类**极低成本统计量**、只用**clean-only 参考**、并且**每次篡改只报一次**（避免刷屏），报告的指标是 0.800 F1 / 0.822 balanced accuracy（320 段受控序列），并在 UHCTD 上给出"范围内 covered 召回 0.667 vs 范围外 0.016"的**诚实的能力边界声明** `[已读]`。**建议 t3 把它列为必读 baseline。**

#### 2.3.2 失焦（defocussed）

| 方法 | 时序需求 | 单帧 | 稀疏适配 | 出处 |
|------|---------|------|---------|------|
| 拉普拉斯方差 / 梯度能量 | 无 | ✅（绝对阈值需按机位标定） | `A` | 本项目 `laplacian`（[ALGORITHMS.md](../ALGORITHMS.md) §1）；CTD 综述的 e2/e3 特征 `[已读]` |
| 频域/模糊核拟合 | 无 | ✅ | `A` | CTD 综述 §III-B（频域滤波思路）`[已读]` |
| 商用：Defocus Detection | 设备在线连续分析 | ❌（依赖设备侧） | 只能"借事件" | [Hikvision Sudden Scene Change / Defocus Detection](https://manualslib.com/manual/1145086/Hikvision-Ds-9608ni-I8.html?page=156)；[AXIS Image Health Analytics "Blurred image"](https://help.axis.com/en-us/axis-image-health-analytics) `[已读]` |

**注意一个反直觉点**：失焦是**单帧可判**的，但"判得准"依赖**机位专属的清晰度基线**。本项目已实测：同一组默认阈值在真实素材上"正常 p95 = 385.6，而阈值为 100"这种量级错配（[ALGORITHMS.md](../ALGORITHMS.md) §10.1/§10.3）——**外部文献同样承认这一点**：CTD 综述把"illumination changes"列为三大难点之首 `[已读]`。因此失焦检测在稀疏场景下**准确的定位是 `C`（单帧算指标 + 历史基线定阈值）**，不是纯 `A`。

#### 2.3.3 移位 / 转动（moved / relocation）

| 方法 | 时序需求 | 单帧 | 稀疏适配 | 出处 |
|------|---------|------|---------|------|
| 特征点匹配 + 单应（ORB/SIFT） | 需参考帧（≥1） | ❌ | `C` | 本项目 `orb`（[ALGORITHMS.md](../ALGORITHMS.md) §4）；CTD 综述 k1/k2 关键点特征 `[已读]` |
| 时延背景差 / 块匹配 | 需背景对 | ❌ | `C`/`D` | CTD 综述 b3 特征（Saglam、Huang、Ellwart 等）`[已读]` |
| 商用：Redirected image | 设备侧连续学习 | ❌ | 借事件 | [AXIS Image Health Analytics](https://help.axis.com/en-us/axis-image-health-analytics) `[已读]`；[Avigilon "Enable Tamper"](https://docs.avigilon.com/bundle/camera-configuration-tool-2-12/page/CCT-AnalyticSettings.htm) `[已读]` |
| 标准事件 | — | — | 可直接订阅 | ONVIF `tns1:VideoSource/GlobalSceneChange`（[Imaging Service Spec](https://www.onvif.org/specs/srv/img/ONVIF-Imaging-Service-Spec.pdf)）`[已读]` |

**burst 的独有价值（学术界没有、但本场景需要）**：固定机位下**任何 burst 内可测的位移都不是自然抖动**，只可能是"有人正在动镜头/支架震动"。这把 ORB 从"跨小时比对（判已发生的移位）"扩展成"burst 内比对（判**正在发生的**物理扰动）"。**这是 t4 应当明确写入的创新点之一**，但需要重标定阈值（当前默认 50 px 是按手持素材定的，见 [02](02-sparse-adaptation.md) §2.1）。

#### 2.3.4 视频冻结 / 重放（freeze / loop）

这一类**学术论文极少**，但有成熟的工程方案，且**只有 burst 能判**：

| 方案 | 要点 | 时序需求 | 出处 |
|------|------|---------|------|
| 数字水印 | 在采集端嵌入水印，接收端校验"帧是否新鲜"；对压缩/缓冲/常见图像处理鲁棒 | 需 ≥2 帧比对水印 | [A Robust Method for Frozen Frame Detection in Safety Relevant Video Streams Based on Digital Watermarking (TU Dresden, 2017)](https://st.inf.tu-dresden.de/files/teaching/ss17/ASE/2017_01_24_PaperED_DigitalWatermarking.pdf) `[摘要]` |
| 编码侧 freeze 检测 | 在编解码链路里检测"连续相同帧" | 需帧序列 | [US9826252B2 Method and video system for freeze-frame detection](https://patents.google.com/patent/US9826252B2/en) `[摘要]` |
| 工程实现（可参考代码） | 维护 `lastFrameTime` 与每路相机状态，超时/重复即告警 | ≥2 帧 | [MiBeeNvr `internal/health/freeze.go`](https://github.com/Mi-Bee-Studio/MiBeeNvr/blob/main/internal/health/freeze.go) `[摘要]` |
| 电气网络频率（ENF） | 用市电 50/60 Hz 纹波做"时间戳"，检测假帧注入 | 需较长片段 | [Detecting Malicious False Frame Injection Attacks on Surveillance Systems at the Edge Using ENF Signals (Sensors 2019)](https://pmc.ncbi.nlm.nih.gov/articles/PMC6603661/) `[摘要]` |

**对本场景**：`B`（K≥2 即可）。判据可以极简且**不需要任何阈值学习**：
- `md5(frame_i) == md5(frame_j)` → **强证据**（真实传感器两次采样不可能逐位相同）；
- 弱形式：整帧 MAD ≈ 0 且**噪声残差 σ ≈ 0**（静止场景本身 MAD 也接近 0，必须靠 σ 区分，见 [02](02-sparse-adaptation.md) §4.1）。

> **这是整份调研里"投入产出比最高"的一条**：实现 20 行代码，覆盖一类现有 9 算法**完全无解**的攻击，且不需要训练、不需要 GPU、不需要冷启动。**建议 t3 直接列为 P0。**

#### 2.3.5 剪辑拼接（splicing / 段替换）

| 方法 | 时序需求 | 稀疏适配 | 出处 |
|------|---------|---------|------|
| SPN/PRNU 双模式检测 | 需参考 | `C` | [Electronics 2023](https://www.mdpi.com/2079-9292/12/6/1362)、[MTAP 2019](https://link.springer.com/article/10.1007/s11042-019-08236-2) `[摘要]` |
| 光流/残差不连续 | **原始帧率连续帧** | `D` | §2.1.1 各篇 |
| 单帧图像篡改定位（噪声+边缘双流） | 无 | `A`（但域不匹配） | [MVSS-Net ICCV 2021](https://openaccess.thecvf.com/content/ICCV2021/papers/Chen_Image_Manipulation_Detection_by_Multi-View_Multi-Scale_Supervision_ICCV_2021_paper.pdf) `[已读摘要]` |
| 视频拼接的噪声传递矩阵 | 需片段 | `D` | [An Anti-Forensics Video Forgery Detection Method Based on Noise Transfer Matrix Analysis (Sensors 2024)](https://www.mdpi.com/1424-8220/24/16/5341) `[摘要]` |

**对本场景**：拼接类基本 `D`。唯一的现实通道是"**拼接点恰好落在 burst 窗口内**"→ 概率 ≈ (K×帧间隔 + 事件时长)/3600 s，与 [02](02-sparse-adaptation.md) §4.4 的命中率分析同量级（**<1%**）。**不建议投入。**

#### 2.3.6 丢帧 / 重编码（frame drop / re-encode）

| 方法 | 要点 | 稀疏适配 | 出处 |
|------|------|---------|------|
| 双重压缩检测 | 见 §2.2.1 | `A`（单帧）但判别力弱 | [TIFS 2008](https://dlnext.acm.org/doi/10.1109/TIFS.2008.922456)、[PMC 2022 双重压缩视频](https://pmc.ncbi.nlm.nih.gov/articles/PMC9740893/) `[摘要]` |
| 时间戳 / 帧号连续性 | 相邻帧时间差 > 2× 期望帧间隔，或帧号跳变 > 2 → 判定 gap | **本场景最实用**：直接查容器元数据 | [Automated Forensic Recovery Methodology for Video Evidence from Hikvision and Dahua DVR/NVR Systems (Information 2025)](https://www.mdpi.com/2078-2489/16/11/983) `[已读摘要]` |
| GOP 结构异常 | 关键帧密度/poc 异常 | 需码流 | 同上 `[摘要]` |

**重要区分**：稀疏采样下，**"丢帧"不可能从画面看出来**（我们本来就只看到 1 帧/小时），只能从**容器/码流元数据**看：PTS/DTS 跳变、帧号不连续、GOP 长度异常、码率突变。这与"检测视频流是否被替换"是同一个工程面（拉流层），**不属于算法层，但必须写进架构**（[02](02-sparse-adaptation.md) §5.6 已提出需要 RTSP/ONVIF 拉帧器）。

#### 2.3.7 增益攻击（gain attack）

- **`[未检索到]`** 以 "gain attack" 为名的、面向监控篡改的公开论文。检索到的相邻工作是：
  - [Image Splicing Detection via Camera Response Function Analysis (CVPR 2017)](https://doi.org/10.1109/cvpr.2017.203)：用 **CRF（相机响应函数）一致性**判拼接 `[摘要]`；
  - ONVIF 标准里的 **ImageTooBright / ImageTooDark** 事件（属 tampering detection 的可选项）`[已读]`（[Analytics Service Spec](https://www.onvif.org/specs/srv/analytics/ONVIF-Analytics-Service-Spec.pdf)）；
  - AGC（自动增益）本身是相机的正常行为，厂商文档把它当**误报来源**而非攻击（[Axis 文档明确列 "sudden light changes" 为误报原因](https://help.axis.com/en-us/axis-image-health-analytics)）`[已读]`。
- **判定**：`[未证实]` 该攻击类型在真实威胁模型中是否成立；若成立（攻击者调高增益使画面过曝/噪声爆炸以掩盖细节），可借用的信号是 **burst 内亮度漂移 `lumaDrift` + 噪声 σ 异常**（[02](02-sparse-adaptation.md) §4.2/§4.3），**不需要新算法**。建议在 t3 里降级为"待观察"。

---

### 2.4 方向四：生成式 / AI 换脸换场景

这一支 2024–2026 年爆发，但**与"固定机位监控 + 1 小时采样"的重合度很低**，原因是它的判别信号几乎全在**时序纹理**上。

| 工作 | 规模/要点 | 时序需求 | 稀疏适配 |
|------|----------|---------|---------|
| [CCDF: A Benchmark Dataset for Deepfake Detection in Surveillance Footage (arXiv 2610.07939, 2026)](https://arxiv.org/abs/2610.07939) `[已读]` | **1840 视频（460 真实 / 1380 生成）**，覆盖 **16 类犯罪/事故场景**，生成器为 **Grok Imagine、Google VEO 3.1、OpenAI Sora 2**；论文结论：**10 个 SOTA 检测器都不能可靠区分** | 视频级（各检测器不同） | `D`（视频级）/ `A`（图像级检测器，但不可靠） |
| [GenVidBench: A 6-Million Benchmark for AI-Generated Video Detection (AAAI 2026)](https://arxiv.org/pdf/2501.11340.pdf)（[官网](https://genvidbench.github.io)） | 百万级、带语义标签（物体/动作/地点） | 视频级 | `D` |
| [AIGVDBench (arXiv 2601.11035, 2026)](https://arxiv.org/html/2601.11035v1)（[仓库](https://github.com/LongMa-2025/AIGVDBench)） | **44 万+ 视频 / 31 个生成模型**，评测 33 个检测器 | 视频级 | `D` |
| [DeCoF: Generated Video Detection via Frame Consistency (arXiv 2402.02085)](https://arxiv.org/html/2402.02085v3)（[仓库](https://github.com/LongMa-2025/DeCoF)） | 强调**帧一致性**作为判别依据；首个生成视频检测基准 | **需连续帧**（一致性本身就是时序量） | `D` |
| [DeMamba / GenVideo (arXiv 2405.19707)](https://arxiv.org/html/2405.19707)（[仓库](https://github.com/chenhaoxing/DeMamba)） | 百万级 GenVideo 基准 | 视频级 | `D` |
| [FL-TENB4: Federated-Learning-Enhanced Tiny EfficientNetB4-Lite for Deepfake Detection in CCTV Environments (2025)](https://pmc.ncbi.nlm.nih.gov/articles/PMC11821191/) | 明确面向 CCTV 的轻量 deepfake 检测探索 | 图像/视频帧 | `A`（单帧，但属小样本研究） |
| [FaceForensics++ 等经典换脸数据集] | 通用换脸基准 | 帧/视频 | 域不匹配（高清人脸 → 监控低质小face） |

**对本场景的判定：**
- **视频级 AIGC 检测 `D`（不可用）**：判别信号是"逐帧生成的时序抖动/物理不合理"，需要连续帧；1 小时 1 帧完全看不到。
- **帧级（图像）AIGC 检测 `A`，但误报高**：单帧可以被判定"像生成的"，但监控画面本身的低质、强压缩、噪声会让图像级检测器大量误报 `[未证实具体幅度]`。
- **更现实的威胁不是"AI 生成监控画面"，而是"AI 生成一段假的事件视频去报案"**——这属于取证（事后）场景，不属于在线稀疏监测。**建议 t3/t4 把 AIGC 方向列为"范围外（out of scope）"，但用 CCDF 的结论（SOTA 都不可靠）作为"不要承诺该能力"的依据。**

---

## 3. 商用与开源可用产品

### 3.1 厂商内置 tamper / anti-tamper 能力（逐家核对官方文档）

| 厂商 | 功能名 | 覆盖的攻击 | 机制/限制（原文要点） | 出处 |
|------|--------|-----------|---------------------|------|
| **海康 Hikvision** | Video Tampering Alarm（基础事件） | 镜头被遮挡 | "triggers an alarm when the camera lens is covered"；灵敏度 + 布防时间 | [官方文档](https://enpinfo.hikvision.com/hkwsen/unzip/20230410194813_20373_doc/GUID-4B120ED5-E26C-486E-B39D-86E671E84E35.html) `[已读]` |
| 海康 | **Sudden Scene Change Detection**（智能事件） | 故意转动镜头等外部因素导致的场景突变 | NVR 侧配置，灵敏度/阈值 | [官方文档](https://enpinfo.hikvision.com/hkwsen/unzip/20230410194813_20373_doc/GUID-B92C2086-4B66-4266-A2E5-5F7118ECFEAC.html)、[NVR 手册](https://manualslib.com/manual/1145086/Hikvision-Ds-9608ni-I8.html?page=156) `[已读]` |
| 海康 | **Defocus Detection**（智能事件） | 失焦 | 与上者并列的独立事件 | 同上 `[已读]` |
| **大华 Dahua** | Video Tampering | 镜头被遮挡 / 单色画面 | "performs alarm linkage when the lens is covered or video output is mono-color screen" | [相机手册](https://mans.io/files/viewer/2155702/156) `[摘要]` |
| 大华 | **Scene Change** | 被遮挡、被移动、强光突变 | 官方 Wiki："compare the original picture and the picture of the camera which has been blocked or moved" | [DahuaWiki](https://dahuawiki.com/Scene_Change) `[已读摘要]` |
| **Axis** | **AXIS Image Health Analytics**（ACAP 应用） | **Blocked / Redirected / Blurred / Underexposed** 四类 | ①**学习场景**（learn/relearn）；②每类可设 **validation period**（持续 N 秒才报）；③**画面被遮挡或转向超过 10 分钟会 relearn 场景并以新场景为基准** ← **对"长期持续篡改"是致命缺陷**；④PTZ 每次移动需 30 s 稳定 + 10 s relearn | [官方技术文档](https://help.axis.com/en-us/axis-image-health-analytics) `[已读]` |
| **Bosch** | Tamper detection（FW 6.30+） | scene too dark / too bright、**global change**、**reference image check** | 四种选项并列；reference check 有 sensitivity 与 trigger delay；Camera Trainer 还可用"标记物消失"判遮挡/移位 | [Bosch 知识库](https://knowledge.keenfinity-group.com/video-systems/article/how-to-configure-bosch-camera-tamper-detection)、[手册](https://manualsdump.com/en/manuals/bosch_appliances-ndn-921/212046/88)、[Camera Trainer 白皮书](https://resources-boschsecurity-cdn-avhrfearhpgyhucd.a02.azurefd.net/public/documents/TN_VCA_Camera_Traine_WhitePaper_enUS_70505162123.pdf) `[已读摘要]` |
| **Avigilon（Motorola）** | **Enable Tamper** | "sudden change in the camera field of view, usually caused by someone unexpectedly moving the camera" | 复选框开关 + 报警联动 | [官方文档](https://docs.avigilon.com/bundle/camera-configuration-tool-2-12/page/CCT-AnalyticSettings.htm) `[已读]` |
| **宇视 Uniview** | Tampering（相机设置） | 遮挡等 | Setup > Camera > Tampering | [第三方操作指南](https://xvraid.com/guide/uniview-how-to-guides-2024.html?lang=en)、[官方博客（防篡改设计）](https://global.uniview.com/News/Blog/202508/1054979_169683_0.htm) `[摘要]` |
| **Verkada** | Tamper Detection / Tamper Alerts | **物理**篡改（振动、移动、被拆） | **加速度计**触发；触发后把"最近 1 分钟"视频/缩略图上传云端 | [官方帮助](https://help.verkada.com/en/articles/6203451-tamper-alerts)、[Tamper Detection and Notifications](https://help.verkada.com/en/articles/1580136-tamper-detection-and-notifications) `[已读摘要]` |
| ~~Avnet~~ | — | — | **`[未检索到]`** Avnet 是电子元器件分销商，无自有 tamper 算法；任务书里的 "Avnet" 疑为 **Avigilon** 笔误 | — |

**从这张表能提炼的三条产业事实（对 t3 很重要）：**

1. **厂商能力 = "短时突变 + 灵敏度阈值"，没有一家解决"持续数天的场景改变"。** 海康/大华/博世/威智伦的 tamper 都是事件型（触发即报，然后结束）；Axis 更进一步但**明确会在 10 分钟后 relearn**，等于主动放弃长期篡改检测。**这正好是本项目"历史基线库"路线的差异化价值所在。**
2. **厂商实现的技术栈与学术 CTD 高度同源**：海康的专利族就是**边缘图相似度**（[EP1936576B1](https://patents.google.com/patent/EP1936576B1/un)），与本项目 `edge` 同构；博世的 "reference image check" 就是**参考图 + 灵敏度/延迟**；Axis 是**场景学习 + 持续时长门控**。**说明本项目的 9 算法选型不是"土办法"，而是与产业界同一条技术线。**
3. **没有任何厂商提供"稀疏采样/低频拉帧"模式**——它们全部假设**设备在线、连续分析**。所以本项目不能"直接买一个功能"，只能自己做；但可以**订阅它们的 ONVIF 事件**作为交叉验证信号（见 §3.2）。

### 3.2 标准与平台层：ONVIF 才是"能直接接"的东西

ONVIF 把 tamper 相关能力标准化成了**事件主题**，这意味着：**只要设备支持，本项目不必自己算，直接订阅事件即可**（作为 L0 的第二路证据）。

| 规范 | 内容 | 出处 |
|------|------|------|
| Imaging Service Spec | 事件主题 `tns1:VideoSource/ImageTooBright`、`/ImageTooDark`、`/ImageTooBlurry`、`/GlobalSceneChange`；**原文明确写 GlobalSceneChange 的成因是 tamper（camera movement or coverage）** | [ONVIF-Imaging-Service-Spec.pdf](https://www.onvif.org/specs/srv/img/ONVIF-Imaging-Service-Spec.pdf) `[已读]` |
| Analytics Service Spec | `RuleType="tt:TamperingDetection"` 的可选子项：**GlobalSceneChange / ImageTooDark / ImageTooBright / SignalLoss / ImageTooBlurry** | [ONVIF-Analytics-Service-Spec.pdf](https://www.onvif.org/specs/srv/analytics/ONVIF-Analytics-Service-Spec.pdf) `[已读]` |
| 设备实现示例 | i-PRO 应用笔记列出支持的标准事件（含 GlobalSceneChange） | [i-PRO ONVIF 应用笔记](https://i-pro.com/products_and_solutions/sites/default/files/2024-02/i-PRO_ApplicationNote_ONVIF.pdf.pdf) `[摘要]` |
| Profile M | 元数据/分析事件承载（VMS 集成方式） | [Mirasys 文档（第三方，说明用法）](https://documentation.mirasys.com/integrations/integrations/onvif-profile-m-events-to-trigger-alarms) `[摘要]` |

> **架构建议（给 t3）**：把"ONVIF tamper 事件订阅"做成**可选的第二路信号**，与自研算法**投票/交叉验证**。好处：①零算法成本；②厂商设备侧的连续分析能看到我们 1 小时采样看不到的瞬时事件（互补）；③坏处：**不能依赖**（很多设备不实现、事件语义不一致、且厂商会在 10 分钟后 relearn）。**别把它当主判据。**

VMS 平台（Milestone / Genetec / Nx Witness 等）本身**不提供 tamper 算法**，它们做的是"接收设备事件 + 规则联动"，检索到的 Milestone 文档是通用事件/规则框架（[Events overview](https://doc.milestonesys.com/en-US/bundle/doc1066_2025r1/page/content/standard_features/sf_mc/sf_ui/mc_eventsoverview_rulesandevents.htm)）`[摘要]`。**结论：VMS 层无可复用算法，不必深挖。**

### 3.3 开源项目与权重（"能不能直接跑"是关键）

| 项目 | 内容 | 权重可得性 | 与本场景关系 |
|------|------|-----------|-------------|
| [QuantitativeImagingLaboratory/ctd-devkit](https://github.com/QuantitativeImagingLaboratory/ctd-devkit)（UHCTD 官方 devkit） | 4 类分类（Normal/Covered/Defocussed/Moved）；支持 AlexNet/DenseNet161/ResNet18/ResNet50；训练+预测脚本；预测输出 `output.csv` | **无 CTD 预训练权重**（只给 Places365 主干权重，需自己训练）；数据需向 [qil.uh.edu/datasets](http://qil.uh.edu/datasets) 申请 | ⭐⭐⭐ **最贴合**：单帧 → 4 类，与本场景同构 |
| [chandan54/Camera-Tampering-Detection-using-Opencv-Python](https://github.com/chandan54/Camera-Tampering-Detection-using-Opencv-Python) | MOG2 背景减除 + 前景占比 | 无（纯 CV，无权重） | 本项目已集成为 `mog2_area`；稀疏下建议 drop（[02](02-sparse-adaptation.md) §2.1） |
| [akhila0813/Tampering_Detection](https://github.com/akhila0813/Tampering_Detection) | OpenCV 实时，宣称覆盖 4 类篡改 | 无 | 可作"最小实现"参考 `[摘要]` |
| [Junhua-Liao/VSOD](https://github.com/Junhua-Liao/VSOD) | 视频镜头**遮挡**检测（IEEE TCSVT 2023，基于深度信息异常波动） | 未见权重说明 | 需深度/双目，监控单目**不适用** `[摘要]` |
| [Mi-Bee-Studio/MiBeeNvr `internal/health/freeze.go`](https://github.com/Mi-Bee-Studio/MiBeeNvr/blob/main/internal/health/freeze.go) | NVR 的相机冻结检测（`lastFrameTime` 状态机） | 无（工程代码） | ⭐ **冻结检测的最佳工程参考** |
| [a-nau/tampar (WACV'24)](https://github.com/a-nau/tampar) | **包裹**视觉篡改检测 | 有代码 | ❌ 不是摄像头篡改，**易被误当同类**，注意区分 `[摘要]` |
| [valeoai/WoodScape](https://github.com/valeoai/WoodScape) | 车载鱼眼多任务集，含 **soiling 标注** | 数据集 | 污损检测的**唯一公开标注集**，但域为车载 |

**关键结论：开源生态里没有"开箱即用的监控篡改检测权重"。** UHCTD devkit 给的是**训练代码**，不是模型；其余仓库都是传统 CV 或工程代码。**这意味着 t3 若要走深度路线，训练成本必须自己承担**（好消息是 UHCTD 的 4 类分类任务很轻，ResNet18 + Places365 微调即可 `[已读 README]`）。

### 3.4 SDK 层（拉帧与事件）

| SDK | 用途 | 可得性 |
|-----|------|--------|
| **ONVIF**（`python-onvif-zeep` 等） | 标准拉流（RTSP）+ 事件订阅 | 开放标准，无授权 |
| **海康 ISAPI / HCNetSDK** | 设备配置、抓图、事件（ISAPI 有社区集成实现，如 [JoeyGE0/hikvision_isapi](https://github.com/JoeyGE0/hikvision_isapi)） | 厂商提供，需下载；`[未证实]` 具体许可条款 |
| **大华 NetSDK** | 同上 | 厂商提供 |
| RTSP / HTTP-Fetch | 最通用的"拉一帧"通道 | 开放 |

> 对本场景**最重要的是"拉一帧"这条链路**，而它当前是本项目的明确缺口（[ALGORITHMS.md](../ALGORITHMS.md) §10 列为非目标；[02](02-sparse-adaptation.md) §5.6 已提出需要新增拉帧器）。**算法调研的结论再漂亮，没有稳定的拉帧器也落不了地。**

---

## 4. 公开数据集

### 4.1 主表

| 数据集 | 规模 | 标注形式 | 适用 | 不适用 / 坑 | 出处 |
|--------|------|---------|------|------------|------|
| **UHCTD**（University of Houston CTD） | **576 次篡改 / 288 小时 / 2 个机位（各 6 天）**；训练：每类均匀分布的图像 + 2×24 h 视频；测试：4×24 h 视频；4 类（Normal/Covered/Defocussed/Moved） | 图像级 4 类标签 + 视频（按时间段落入类别） | ⭐⭐⭐ **唯一"监控 + 四类篡改 + 小时级长视频"的公开集**；天然适配"1 帧分类"与"跨小时基线" | ①**合成篡改**（图像处理注入），真实攻击的物理细节可能缺失；②只有 2 个机位 → 跨场景泛化无法评估；③需申请下载 | [devkit README](https://github.com/QuantitativeImagingLaboratory/ctd-devkit) `[已读]`；[AVSS 2019 论文条目](https://www.computer.org/csdl/proceedings-article/avss/2019/08909856/1febN4oV1NC) `[摘要]` |
| UHCTD 的篡改节奏（关键参数） | 每 **10 分钟**注入一次篡改，每次持续 **5~10 分钟**，24 小时循环 | — | **对本场景极重要**：5~10 min / 10 min ⇒ 占空比 50~100%，**1 小时采样几乎必然命中**持续性篡改 | 反过来说：**它无法评估"短于采样间隔的瞬时事件"**（本来也测不了） | [Mantini & Shah 2023 §IV-E](https://arxiv.org/abs/2310.07886) `[已读]` |
| **FLAD**（Video Flaw Detection，会议录制） | Kaggle 数据卡称"共收集 8 场会议录制"；具体片段数/时长**未核实** | 视频级瑕疵标注（三类最常见内容瑕疵） | 可评估"模糊/遮挡/冻结"这类**画面瑕疵**的检测器 | ❌ **域差极大**：会议录制（近景人脸、高码率、无夜视）vs 监控（远景、低码、红外）；本项目已实测其阈值不可迁移（[ALGORITHMS.md](../ALGORITHMS.md) §10.1）。**不要用它做主要评测集** | [ACM NOSSDAV 2022](https://dl.acm.org/doi/10.1145/3534088.3534349)、[论文 PDF](https://junhua-liao.github.io/Junhua-Liao/publications/papers/NOSSDAV_2022.pdf)、[Kaggle 数据卡](https://www.kaggle.com/datasets/seaxiaod/flad-video-flaw-detection) `[摘要]` |
| **TRECVID SED**（Surveillance Event Detection） | 2008–2017；Gatwick 机场 + i-LIDS（UK Home Office） | 事件级（人/车行为）标注 | 历史最悠久的监控评测 | ❌ **不是篡改数据集**，是"事件检测"；且**视频数据仅对当届 TRECVID 参与者开放** | [TRECVID 数据总表](https://trecvid.nist.gov/past.data.table.html)、[SED 数据页](https://nist.gov/itl/iad/mltg/trecvid-surveillance-event-detection-data-page) `[已读]` |
| **CCDF**（CCTV DeepFakes, 2026） | **1840 视频（460 真实 / 1380 生成）**，16 类犯罪/事故场景；生成器：Grok Imagine / VEO 3.1 / Sora 2；三个版本（原始 / 元数据清洗 / 低劣后处理攻击） | 视频级真/假 + 类别 | ⭐ **唯一面向"监控画面 deepfake"的公开基准** | ①仅 1840 条（小规模，作者自称 small-scale）；②生成器均为闭源商用 → 迭代快；③**论文结论：10 个 SOTA 检测器都不可靠** → 现阶段不宜作为产品能力承诺 | [arXiv 2610.07939](https://arxiv.org/abs/2610.07939) `[已读]` |
| **GenVidBench** | 标题即"**6-Million** Benchmark"（600 万级）；带物体/动作/地点三维语义标签 | 视频级真/假 + 语义标签 | AI 生成视频检测的大规模评测 | 面向通用生成视频，**不含监控场景**；视频级任务 | [论文 PDF](https://arxiv.org/pdf/2501.11340.pdf)、[官网](https://genvidbench.github.io)、[AAAI 2026 条目](https://dl.acm.org/doi/10.1609/aaai.v40i18.38587) `[摘要]` |
| **AIGVDBench** | **44 万+ 视频 / 31 个生成模型 / 33 个检测器 / 1500+ 次评测** | 视频级 + 生成模型标签 | 同上，模型覆盖更全 | 同上 | [arXiv 2601.11035](https://arxiv.org/html/2601.11035v1)、[仓库](https://github.com/LongMa-2025/AIGVDBench) `[摘要]` |
| **DeCoF** | 生成视频检测基准（"首个"），强调帧一致性 | 视频级 | 帧一致性方法评测 | 规模**未核实** | [arXiv 2402.02085](https://arxiv.org/html/2402.02085v3)、[仓库](https://github.com/LongMa-2025/DeCoF) `[摘要]` |
| **GenVideo（DeMamba）** | 百万级 | 视频级 | 大规模生成视频检测 | 同上 | [arXiv 2405.19707](https://arxiv.org/html/2405.19707)、[仓库](https://github.com/chenhaoxing/DeMamba) `[摘要]` |
| **WoodScape（soiling 子任务）** | 10K 图像 / 多任务（含 **soiling detection**） | 逐图像/逐区域污损标签 | 污损的**唯一公开标注集** | ❌ 车载鱼眼环视，非固定监控；污损形态（泥水/雨滴）与"监控镜头积灰/蛛网"不同 | [ICCV 2019 论文](https://openaccess.thecvf.com/content_ICCV_2019/html/Yogamani_WoodScape_A_Multi-Task_Multi-Camera_Fisheye_Dataset_for_Autonomous_Driving_ICCV_2019_paper.html)、[仓库](https://github.com/valeoai/WoodScape) `[摘要]` |
| **SoilingNet** | 车载环视污损分类；用 **GAN 合成**扩充数据 | 图像级污损等级 | 方法参考（GAN 增广思路） | 数据非公开/规模未核实 | [arXiv 1905.01492](https://arxiv.org/abs/1905.01492) `[摘要]` |
| **"Outdoor IoT CTD" 数据集（2024/2026）** | 公开：normal / blurred / rotated **静态图像** | 图像级 | ⭐ **与"1 帧静态图"场景完全同构**；作者同时对比了规则法 vs 深度学习法 | 规模未核实（论文称"publicly available datasets"） | [arXiv 2602.05706](https://arxiv.org/abs/2602.05706) `[已读]` |
| **VFDD2.1**（华南理工） | 二手引用：**2189 视频（1496 正常 / 496 帧间伪造 / 197 …）** | 视频级 | 帧间伪造（删/插帧） | `[未证实]`：只从第三方论文的引用段落读到，未找到官方页面；且是**帧间伪造**任务，与本场景不同 | [第三方论文（ResearchGate）](https://www.researchgate.net/publication/394907131_Spatiality-Frequency_Domain_Video_Forgery_Detection_System_Based_on_ResNet-LSTM-CBAM_and_DCT_Hybrid_Network) `[未证实]` |
| **DVAD** | **`[未检索到]`** 与"摄像头篡改/监控异常"对应的 DVAD 数据集。检索到的同名项是 [DVAD: A Dynamic Visual Adaptation Framework for Multi-Class Anomaly Detection (MDPI AI 2025)](https://doi.org/10.3390/ai6110289) —— 一个**多类异常检测框架**，不是数据集 | — | — | **任务书里的 DVAD 需澄清**；若指 Dahua 的车辆异常数据集，`[未检索到]` 可引用来源 | — |
| **TRECVID "S3/SSD"** | **`[未检索到]`** 这两个任务名。TRECVID 官方的监控相关 track 是 **SED（2008–2017）**；SSD 在检索结果中只以无关形式出现 | — | — | **建议在最终报告中更正为 SED** | [TRECVID 数据总表](https://trecvid.nist.gov/past.data.table.html) `[已读]` |

### 4.2 数据集侧的三条判断

1. **能同时满足"监控域 + 篡改标注 + 长时段"的只有 UHCTD。** 它的 288 小时/2 机位规模不大，但**结构完全对**（图像级 4 类 + 24 h 连续视频），而且**篡改持续 5~10 分钟**这个设定，恰好证明"1 小时采样能抓到持续性篡改"。**建议 t3 把 UHCTD 定为唯一主评测集，FLAD 降为"域外泛化"参考。**
2. **"遮挡/污损专用集"实际是空缺。** 车载 soiling（WoodScape/SoilingNet）是最近似的，但**污损形态与监控不同**（车载是泥水飞溅，监控是积灰/蛛网/鸟粪/水渍长期附着），且**没有"渐进污损"的时间序列标注**。**这是 t4 可以自己造数据的地方**（合成：在 UHCTD 正常帧上叠加渐变污渍纹理 + 时间序列）。
3. **AIGC 侧的"监控专用"基准 2026 年才出现（CCDF），且作者自己说检测器不可靠。** 引用它时**必须带上这个否定结论**，否则会误导下游以为"deepfake 检测已可用"。

---

## 5. 对比总表：算法 × 时序要求 × 单帧可用 × 成本 × 稀疏采样适配性

> 这是本文的核心交付物。**分级定义见 §1**（A=单帧即可 / B=K=2~5 burst / C=需跨采样点基线 / D=需原始帧率连续帧，不可用）。
> 成本列：经典算法用本项目实测（960×540 本地 CPU，[ALGORITHMS.md](../ALGORITHMS.md) §9）；深度模型标"GPU"并注明是否实测。

### 5.1 单帧 / 图像内在痕迹类

| # | 算法/方法 | 时序要求 | 最短窗口 | 参考帧/学习期 | 单帧可判 | 成本 | 稀疏适配 | 备注 |
|---|----------|---------|---------|-------------|---------|------|---------|------|
| 1 | 拉普拉斯方差（失焦）`laplacian` | 无 | 1 帧 | 无（绝对）/ 建议历史基线（相对） | ✅ | CPU 17.2 ms `[实测]` | **A**（+C 提准确率） | 本项目最稳的单帧判据 |
| 2 | 直方图熵 + 分块低熵占比 `entropy` | 无 | 1 帧 | 无（绝对）/ 建议相对基线 | ⚠️ 需标定 | CPU 3.8 ms `[实测]` | **A**（+C） | 绝对阈值跨场景失效（本项目已实测） |
| 3 | 边缘消失率 `edge` | 需参考帧 | 2 帧（1 参考 + 1 当前） | **必需**（单参考或多基线） | ❌ | CPU 5.4 ms `[实测]` | **C** | 与海康专利同源；稀疏下基线必须换源 |
| 4 | 帧差 / 场景差异 `scene_diff` | 需参考帧 | 2 帧 | **必需** | ❌ | CPU 6.8 ms `[实测]` | **C** | 需光度归一化 |
| 5 | ORB + 单应（移位）`orb` | 需参考帧 | 2 帧 | **必需** | ❌ | CPU 26.6 ms `[实测]` | **C**（跨小时）+ **B**（burst 内扰动） | burst 内阈值需重标定到 3~8 px |
| 6 | UHCTD 4 类 CNN 分类 | 无 | **1 帧** | 无（图像级分类）；但需训练集 | ✅ | GPU（ResNet18 级，`[未实测]`） | **A** ⭐ | 与本场景同构，改造成本最低 |
| 7 | 参考图检查（Bosch 式） | 需参考图 | 1 帧 + 参考 | **必需**（学习期/reference image） | ❌ | 极低 | **C** | 产业界主流做法 |
| 8 | 亮度/梯度统计 + clean-only 滑动参考（arXiv 2607.14760） | 滑动参考（≥若干历史样本） | 1 帧/次 | **必需**（clean-only） | ⚠️（统计量可单帧算，判据需参考） | 极低（CPU） | **B/C** ⭐ | 2026 年最贴近本场景的工作，0.800 F1 |
| 9 | 中值滤波残差 σ（噪声一致性） | 无（单帧可算） | 1 帧 | 历史 σ 基线（可选） | ✅ | 极低 | **A**（+C） | 判"是否本机直出"的轻量近似 |
| 10 | PRNU 指纹相关 | 需多帧估指纹（可离线） | 在线 1 帧；离线 N 帧 | **必需**（同机位干净帧） | ❌ | 低（滤波级） | **C** ⭐ | 覆盖"视频流替换"，但强压缩会抹掉 |
| 11 | Noiseprint（CNN 指纹） | 无 | 1 帧 | 需训练（模型内含先验） | ✅ | GPU（`[未实测]`） | **A**（域不匹配风险） | 通用取证，非监控专用 |
| 12 | JPEG 双重压缩检测（DCT 直方图） | 无 | 1 帧 | 无 | ✅ | 低 | **A**（判别力弱） | 监控链路天然多压缩 → 常态噪声 |
| 13 | Reverse JPEG Compatibility | 无 | 1 帧 | 需已知压缩参数 | ✅ | 低 | **A**（QF 99/100 限定） | 低 QF 监控流不适用 |
| 14 | 屏幕翻拍 / 摩尔纹 | 无 | 1 帧 | 需训练（CMA 等） | ✅ | GPU（`[未实测]`） | **A**（可靠性存疑） | 监控高频弱 → 未证实 |
| 15 | 单帧图像篡改定位（MVSS-Net） | 无 | 1 帧 | 需训练 | ✅ | GPU | **A**（域不匹配） | 检测"像素来自别处"，非"不该存在" |

### 5.2 时序 / 视频级方法

| # | 算法/方法 | 时序要求 | 最短窗口 | 参考帧/学习期 | 单帧可判 | 成本 | 稀疏适配 | 备注 |
|---|----------|---------|---------|-------------|---------|------|---------|------|
| 16 | 光流一致性（删/插帧） | **原始帧率连续帧** | 覆盖拼接点前后数帧 @25fps | 无 | ❌ | CPU 中-高 | **D** | 帧间隔 0.5~2 s 使前提失效 |
| 17 | 速度场一致性（ICASSP 2014） | 同上 | 同上 | 无 | ❌ | CPU 中 | **D** | 同上 |
| 18 | 3D-CNN（16 帧最优） | 连续帧 | **16 帧**（8~32 区间调参） | 需训练 | ❌ | GPU | **D** | 帧数与帧率双不满足 |
| 19 | Transformer 时序模型 | 连续帧 | **32~96 帧** | 需训练 | ❌ | GPU 高 | **D** | 同上 |
| 20 | 时序 Dropout 3DCNN | 连续帧 | 20 帧（采样自 25 帧） | 需训练 | ❌ | GPU | **D** | 同上 |
| 21 | 帧一致性（DeCoF 类） | 连续帧 | 未证实（≥数帧） | 需训练 | ❌ | GPU | **D** | 一致性本身就是时序量 |
| 22 | MOG2 背景减除 | 在线背景（warmup 5+） | 5 帧起 | **需 warmup + 在线更新** | ❌ | CPU 3.1 ms `[实测]` | **D** | 与昼夜节律冲突，建议 drop |
| 23 | 背景时延差（b3） | 需背景对 | 2 个背景 | 需背景模型 | ❌ | 低 | **C/D** | 稀疏下背景不可得 |
| 24 | 短/长池直方图 `shortlong` | 内部池累积 | ≥15 帧（默认 10+5） | **需长池持久化** | ❌ | CPU 25.8 ms `[实测]` | **C**（按采样点重构后） | 池单位应从"帧"改为"采样点" |
| 25 | 帧间最大连通域 `frame_area` | 依赖上一帧 | 2 帧 | 上一帧（burst 内首帧） | ❌ | CPU 0.9 ms `[实测]` | **B**（作为活动性探针） | 语义应改为"变化活动性" |

### 5.3 burst 专属（学术无对应、工程可用）

| # | 方法 | 时序要求 | 最短窗口 | 参考帧 | 单帧可判 | 成本 | 稀疏适配 | 备注 |
|---|------|---------|---------|--------|---------|------|---------|------|
| 26 | **bit-exact 重复帧检测**（MD5/CRC） | burst | **2 帧**（任意间隔） | 无 | ❌ | 极低（<1 ms） | **B** ⭐⭐⭐ | 冻结/重放，**P0** |
| 27 | 整帧 MAD + 噪声 σ | burst | 2 帧 | 历史 σ（可选） | ❌ | 极低 | **B** | 与 26 互补（静止场景防误报） |
| 28 | burst 内 ORB 平移 | burst | 2~3 帧 | 无（阈值需标定） | ❌ | CPU 26.6 ms/对 | **B** ⭐⭐ | 判"正在被转动/物理扰动" |
| 29 | burst 内亮度/色度漂移 | burst | 2~3 帧 | 无 | ⚠️ | 极低 | **B** | **样本质量门控**，防污染基线 |
| 30 | 曝光/白平衡稳定性 | burst | 2 帧 | 无 | ⚠️ | 极低 | **B** | 同上 |

### 5.4 商用/标准能力（"能不能直接接"）

| # | 能力 | 时序要求 | 单帧可判 | 成本 | 稀疏适配 | 备注 |
|---|------|---------|---------|------|---------|------|
| 31 | 海康 Video Tampering / Sudden Scene Change / Defocus | 设备侧连续分析 | ❌ | 设备内置 | **只能订阅事件**（`C`） | 事件型，无长期基线 |
| 32 | 大华 Video Tampering / Scene Change | 同上 | ❌ | 设备内置 | 只能订阅 | 同上 |
| 33 | Axis Image Health Analytics | 同上 + 学习期 | ❌ | 设备内置 | 只能订阅；**10 分钟后 relearn** | 对长期篡改有硬缺陷 |
| 34 | Bosch Tamper（含 reference image check） | 设备侧 | ❌ | 设备内置 | 只能订阅 | 有参考图机制，最接近我们的思路 |
| 35 | Avigilon Enable Tamper | 设备侧 | ❌ | 设备内置 | 只能订阅 | 仅"视野突变" |
| 36 | Verkada Tamper（加速度计） | 物理事件 | ❌ | 设备内置 | 只能订阅 | **不是画面分析**，是物理传感器 |
| 37 | **ONVIF 标准事件**（GlobalSceneChange / ImageTooBlurry / TooDark / TooBright / SignalLoss） | 设备侧 | ❌ | **零**（订阅） | **C（作第二路证据）** | 无算法成本，但不可依赖 |

---

## 6. 对本项目的直接结论

### 6.1 可用 / 不可用清单

**立即可用（A/B 级，零或极低改动）：**
1. `laplacian` / `entropy` 单帧绝对判据（P0，已在用，补标定）；
2. **UHCTD 4 类 CNN 分类器**（P1，单帧 → 四类，与场景同构，需训练）；
3. **burst 冻结检测（MD5 + σ）**（P0，20 行代码，覆盖现有算法完全无解的攻击）；
4. **burst 内 ORB 平移**（P1，判"正在被动手"，阈值需重标定）；
5. **burst 亮度/色度漂移**（P1，作样本质量门控，防止坏样本污染基线）。

**需要历史基线才可用（C 级，是本项目的主战场）：**
6. `scene_diff` / `edge` / `orb` 的跨小时比对（**必须**换参考源 + 光度归一化，见 [02](02-sparse-adaptation.md) §5）；
7. **中位数背景（按同时段分槽）**（替代 MOG2）；
8. **PRNU/噪声指纹**（离线攒帧，在线 1 帧判"是否本机直出"——**最值得做可行性验证的新方向**）；
9. `shortlong` 按"采样点"重构（长池跨天积累）。

**明确不可用（D 级，不要投入）：**
10. 光流一致性 / 帧间伪造检测（帧率前提被破坏）；
11. 3D-CNN / Transformer 时序模型（帧数 8~32 起，且需连续帧）；
12. 视频级 AIGC 检测（判别信号在时序纹理上）；
13. MOG2 在线背景（与昼夜节律冲突）；
14. 拼接/重编码的画面级检测（只能走元数据链路，非算法）。

### 6.2 三条"必须写进最终报告"的澄清

| 项 | 问题 | 建议处置 |
|---|---|---|
| **FETV** | 被当成取证方法引用，实际是**文生视频评测基准**（[NeurIPS 2023](https://github.com/llyx97/FETV)） | **删除或改为"生成质量评测"** |
| **DVR** | `[未检索到]` 对应算法/数据集；检索到的都是 DVR 硬件 | 标"未证实"，或改为"DVR/NVR 设备取证"（[Information 2025](https://www.mdpi.com/2078-2489/16/11/983)） |
| **TRECVID S3/SSD / DVAD** | `[未检索到]` 该任务名/数据集；TRECVID 对应的是 **SED** | 更正为 SED；DVAD 若指 MDPI 2025 框架则与篡改无关 |

### 6.3 与本项目现有 9 算法的关系（避免重复劳动）

本文是**外部全景**，[02-sparse-adaptation.md](02-sparse-adaptation.md) 是**内部逐算法改造**。两者的交集结论一致、可以互相印证：

| 结论 | 本文（外部证据） | [02](02-sparse-adaptation.md)（内部实测） |
|------|----------------|----------------------------------------|
| 单帧只能判"已经很坏"的状态 | CTD 综述的 no-reference 局限 `[已读]` | §6.1 单帧可判清单 |
| 参考帧必须从"上一帧"升级为"历史基线" | CTD 综述的 full/reduced-reference 框架 + "残差序列"形式化 `[已读]` | §5 四件套（多参考/同时段/中位数/burst 自参照） |
| burst 自参照对持续性篡改灵敏度为 0 | 学术界几乎不做 burst → 无直接证据（`[未检索到]`）；但"持续性篡改在 burst 内是常量"是逻辑必然 | §4.4 量化命中率 |
| 冻结/重放是 burst 独有能力 | 水印/编码侧工程方案 `[摘要]`，非学术主线 | §4.1 列为 ⭐⭐⭐ |
| MOG2 不适用 | 厂商也用"学习期 + 参考图"而非在线背景（Bosch/Axis）`[已读]` | §2.1 建议 drop |

**没有冲突，可以放心合并。**

### 6.4 建议 t3/t4 直接采纳的三个"外部增量"

1. **UHCTD 4 类分类器作为 L2 的新增 baseline**——它是学术界唯一"单帧 → 监控篡改四类"的成熟范式，且训练成本低（ResNet18 + Places365 权重）。**本项目的 `entropy`/`laplacian` 是手工特征，UHCTD 分类器是学习特征，两者互补。**
2. **[arXiv 2607.14760](https://arxiv.org/abs/2607.14760) 的"clean-only 滑动参考 + 亮度/梯度统计 + 每次篡改只报一次"**——这套设计**天生适合低频采样**（它本来就是为"低误报、有界计算、可诊断"设计的），且**只报一次**正好解决 1 小时采样下的告警疲劳。**建议精读全文并对照实现。**
3. **PRNU/噪声指纹的可行性验证**——这是唯一能覆盖"视频流替换/重放"这类**单帧完全无解**攻击的低成本路线，且与稀疏采样"可离线攒帧"的特性天然契合。**建议 t4 先做一个"拉 100 帧 → 估指纹 → 验相关性"的最小实验**，再决定是否投入。

---

## 7. 来源清单（按可点击性分组）

### 7.1 学术：CTD 与综述
- Mantini & Shah, *A Survey of Feature Types and Their Contributions for Camera Tampering Detection*, arXiv 2310.07886 — https://arxiv.org/abs/2310.07886
- UHCTD (AVSS 2019) 论文条目 — https://www.computer.org/csdl/proceedings-article/avss/2019/08909856/1febN4oV1NC
- UHCTD 官方 devkit（数据集说明 + 训练/预测代码）— https://github.com/QuantitativeImagingLaboratory/ctd-devkit
- *Clean-Reference Streaming Detection of Lens Occlusion and Photometric Transitions for Camera Tamper Monitoring*, arXiv 2607.14760 — https://arxiv.org/abs/2607.14760
- *Poster: Camera Tampering Detection for Outdoor IoT Systems*, arXiv 2602.05706 — https://arxiv.org/abs/2602.05706

### 7.2 学术：视频伪造 / 帧间篡改
- *Exposing video inter-frame forgery based on velocity field consistency*, ICASSP 2014 — https://doi.org/10.1109/icassp.2014.6854085
- *A novel video inter-frame forgery model detection scheme based on optical flow consistency*, IWDW 2012 — https://dl.acm.org/doi/10.1007/978-3-642-40099-5_22
- *Frame Deletion Detection Based on Optical Flow Orientation Variation*, IEEE Access 2021 — https://doi.org/10.1109/access.2021.3061586
- *Two-Stage Detection and Localization of Inter-Frame Tampering in Surveillance Videos Using Texture and Optical Flow*, Mathematics 2024 — https://www.mdpi.com/2227-7390/12/22/3482
- *A comprehensive survey on passive techniques for digital video forgery detection*, MTAP 2020 — https://dlnext.acm.org/doi/10.1007/s11042-020-09974-4
- *Systematic analysis of video tampering and detection techniques*, Cogent Engineering 2024 — https://www.tandfonline.com/doi/full/10.1080/23311916.2024.2424466

### 7.3 学术：单帧取证
- *MVSS-Net*, ICCV 2021 — https://openaccess.thecvf.com/content/ICCV2021/papers/Chen_Image_Manipulation_Detection_by_Multi-View_Multi-Scale_Supervision_ICCV_2021_paper.pdf
- *Detection of Double-Compression in JPEG Images*, IEEE TIFS 2008 — https://dlnext.acm.org/doi/10.1109/TIFS.2008.922456
- *Reverse JPEG Compatibility Attack*, IEEE TIFS 2019 — https://doi.org/10.1109/tifs.2019.2940904
- *Extending the Reverse JPEG Compatibility Attack to Double Compressed Images*, ICASSP 2021 — https://ws.binghamton.edu/fridrich/Research/DoubleCompression-14.pdf
- *Dual JPEG Compatibility*, arXiv 2408.17106 — https://arxiv.org/abs/2408.17106
- *Analysis and Mitigation of the False Alarms of the Reverse JPEG Compatibility Attack*, IH&MMSec 2023 — https://dl.acm.org/doi/abs/10.1145/3577163.3595092
- *Noiseprint: a CNN-based camera model fingerprint*, arXiv 1808.08396 — https://arxiv.org/pdf/1808.08396
- *Combining PRNU and noiseprint for robust device source identification*, EURASIP 2020 — https://link.springer.com/content/pdf/10.1186/s13635-020-0101-7.pdf
- *Extracting camera-based fingerprints for video forensics*, CVPRW 2019 — https://openaccess.thecvf.com/content_CVPRW_2019/papers/Media%20Forensics/Verdoliva_Extracting_camera-based_fingerprints_for_video_forensics_CVPRW_2019_paper.pdf
- *A Video Splicing Forgery Detection and Localization Algorithm Based on Sensor Pattern Noise*, Electronics 2023 — https://www.mdpi.com/2079-9292/12/6/1362
- *Detection of Upscale-Crop and Partial Manipulation in Surveillance Video Based on SPN*, Sensors 2013 — https://www.mdpi.com/1424-8220/13/9/12605
- *An Image Recapture Detection Algorithm*, IEEE TIFS 2015 — https://www.commsp.ee.ic.ac.uk/~pld/publications/IEEETransactionsINFS_THT_HM_PLD15.pdf
- *Identification of aliasing-based patterns in re-captured LCD screens*, ICIP 2015 — https://doi.org/10.1109/icip.2015.7350872
- *Doing More With Moiré Pattern Detection in Digital Photos*, IEEE TIP 2022 — https://doi.org/10.1109/tip.2022.3232232
- *CMA: A Chromaticity Map Adapter for Robust Detection of Screen-Recapture*, CVPR 2024 — https://openaccess.thecvf.com/content/CVPR2024/html/Chen_CMA_A_Chromaticity_Map_Adapter_for_Robust_Detection_of_Screen-Recapture_CVPR_2024_paper.html
- *Image Splicing Detection via Camera Response Function Analysis*, CVPR 2017 — https://doi.org/10.1109/cvpr.2017.203

### 7.4 学术：深度时序 / AIGC
- *Detecting Deepfake Videos with Temporal Dropout 3DCNN*, IJCAI 2021 — https://www.ijcai.org/proceedings/2021/0178.pdf
- *Deepfake Detection in Social Media: A Temporal Artifact Analysis Using 3D CNN*, arXiv 2605.17573 — https://arxiv.org/html/2605.17573v1
- *Cross-dataset video deepfake detection using Transformer and CNN*, MVA 2026 — https://link.springer.com/article/10.1007/s00138-026-01809-w
- *Hybrid CNN-Transformer Architecture for Deepfake Video Detection* — https://csar.ud.ac.ae/wp-content/uploads/projects/deepfake/Hybrid_CNN_Transformer_Architecture_for_Deepfake.pdf
- *CCDF: A Benchmark Dataset for Deepfake Detection in Surveillance Footage*, arXiv 2610.07939 — https://arxiv.org/abs/2610.07939
- *GenVidBench: A 6-Million Benchmark for AI-Generated Video Detection*, AAAI 2026 — https://arxiv.org/pdf/2501.11340.pdf / https://genvidbench.github.io
- *AIGVDBench*, arXiv 2601.11035 — https://arxiv.org/html/2601.11035v1 / https://github.com/LongMa-2025/AIGVDBench
- *DeCoF*, arXiv 2402.02085 — https://arxiv.org/html/2402.02085v3 / https://github.com/LongMa-2025/DeCoF
- *DeMamba / GenVideo*, arXiv 2405.19707 — https://arxiv.org/html/2405.19707 / https://github.com/chenhaoxing/DeMamba
- *FL-TENB4: Deepfake Detection in CCTV Environments*, 2025 — https://pmc.ncbi.nlm.nih.gov/articles/PMC11821191/

### 7.5 学术：污损 / 冻结 / 元数据
- *SoilingNet*, arXiv 1905.01492 — https://arxiv.org/abs/1905.01492
- *WoodScape*, ICCV 2019 — https://openaccess.thecvf.com/content_ICCV_2019/html/Yogamani_WoodScape_A_Multi-Task_Multi-Camera_Fisheye_Dataset_for_Autonomous_Driving_ICCV_2019_paper.html / https://github.com/valeoai/WoodScape
- *Let's Get Dirty: GAN Based Data Augmentation for Camera Lens Soiling Detection*, IEEE 2021 — https://ieeexplore.ieee.org/document/9423236
- *Let's See Clearly: Contaminant Artifact Removal for Moving Cameras*, ICCV 2021 — https://openaccess.thecvf.com/content/ICCV2021/papers/Li_Lets_See_Clearly_Contaminant_Artifact_Removal_for_Moving_Cameras_ICCV_2021_paper.pdf
- *Seeing through Unclear Glass: Occlusion Removal with One Shot*, arXiv 2509.01033 — https://arxiv.org/abs/2509.01033
- *A Robust Method for Frozen Frame Detection in Safety Relevant Video Streams Based on Digital Watermarking*, TU Dresden 2017 — https://st.inf.tu-dresden.de/files/teaching/ss17/ASE/2017_01_24_PaperED_DigitalWatermarking.pdf
- US9826252B2 *Method and video system for freeze-frame detection* — https://patents.google.com/patent/US9826252B2/en
- *Detecting Malicious False Frame Injection Attacks on Surveillance Systems Using ENF Signals*, Sensors 2019 — https://pmc.ncbi.nlm.nih.gov/articles/PMC6603661/
- *Automated Forensic Recovery Methodology for Video Evidence from Hikvision and Dahua DVR/NVR Systems*, Information 2025 — https://www.mdpi.com/2078-2489/16/11/983
- *Detection of Double-Compressed Videos Using Descriptors*, 2022 — https://pmc.ncbi.nlm.nih.gov/articles/PMC9740893/

### 7.6 商用与标准
- Hikvision *Configure Video Tampering Alarms* — https://enpinfo.hikvision.com/hkwsen/unzip/20230410194813_20373_doc/GUID-4B120ED5-E26C-486E-B39D-86E671E84E35.html
- Hikvision *Sudden Scene Change Detection* — https://enpinfo.hikvision.com/hkwsen/unzip/20230410194813_20373_doc/GUID-B92C2086-4B66-4266-A2E5-5F7118ECFEAC.html
- Hikvision NVR 手册（含 Sudden Scene Change / Defocus Detection）— https://manualslib.com/manual/1145086/Hikvision-Ds-9608ni-I8.html?page=156
- DahuaWiki *Scene Change* — https://dahuawiki.com/Scene_Change
- Dahua 相机手册 *Setting video tampering* — https://mans.io/files/viewer/2155702/156
- AXIS *Image Health Analytics* 技术文档 — https://help.axis.com/en-us/axis-image-health-analytics
- Bosch *How to configure Bosch camera tamper detection* — https://knowledge.keenfinity-group.com/video-systems/article/how-to-configure-bosch-camera-tamper-detection
- Bosch NDN-921 手册 *Tamper detection* — https://manualsdump.com/en/manuals/bosch_appliances-ndn-921/212046/88
- Bosch *VCA Camera Trainer* 白皮书 — https://resources-boschsecurity-cdn-avhrfearhpgyhucd.a02.azurefd.net/public/documents/TN_VCA_Camera_Traine_WhitePaper_enUS_70505162123.pdf
- Avigilon *Analytics Setting Description*（Enable Tamper）— https://docs.avigilon.com/bundle/camera-configuration-tool-2-12/page/CCT-AnalyticSettings.htm
- Uniview *How Security Camera Design Integrates Advanced Anti-Tampering Features* — https://global.uniview.com/News/Blog/202508/1054979_169683_0.htm
- Uniview 设置指南（第三方）— https://xvraid.com/guide/uniview-how-to-guides-2024.html?lang=en
- Verkada *Tamper Alerts* — https://help.verkada.com/en/articles/6203451-tamper-alerts
- Verkada *Tamper Detection and Notifications* — https://help.verkada.com/en/articles/1580136-tamper-detection-and-notifications
- ONVIF *Imaging Service Specification* — https://www.onvif.org/specs/srv/img/ONVIF-Imaging-Service-Spec.pdf
- ONVIF *Analytics Service Specification* — https://www.onvif.org/specs/srv/analytics/ONVIF-Analytics-Service-Spec.pdf
- i-PRO ONVIF 应用笔记 — https://i-pro.com/products_and_solutions/sites/default/files/2024-02/i-PRO_ApplicationNote_ONVIF.pdf.pdf
- Hikvision 边缘相似度专利 EP1936576B1 *Camera tampering detection* — https://patents.google.com/patent/EP1936576B1/un

### 7.7 数据集与代码
- TRECVID 数据总表 — https://trecvid.nist.gov/past.data.table.html
- TRECVID SED 数据页 — https://nist.gov/itl/iad/mltg/trecvid-surveillance-event-detection-data-page
- FLAD 论文（ACM NOSSDAV 2022）— https://dl.acm.org/doi/10.1145/3534088.3534349 / https://junhua-liao.github.io/Junhua-Liao/publications/papers/NOSSDAV_2022.pdf
- FLAD Kaggle 数据卡 — https://www.kaggle.com/datasets/seaxiaod/flad-video-flaw-detection
- chandan54/Camera-Tampering-Detection-using-Opencv-Python — https://github.com/chandan54/Camera-Tampering-Detection-using-Opencv-Python
- akhila0813/Tampering_Detection — https://github.com/akhila0813/Tampering_Detection
- Junhua-Liao/VSOD — https://github.com/Junhua-Liao/VSOD
- MiBeeNvr freeze 检测 — https://github.com/Mi-Bee-Studio/MiBeeNvr/blob/main/internal/health/freeze.go
- a-nau/tampar（包裹篡改，非摄像头）— https://github.com/a-nau/tampar
- JoeyGE0/hikvision_isapi（ISAPI 社区集成）— https://github.com/JoeyGE0/hikvision_isapi

---

## 8. 方法学说明与"未证实/未检索到"汇总

### 8.1 检索与核对方式

- 检索：`advanced_search`（exa / tavily / keenable 多引擎轮换）+ `web_search`；共约 20 组查询，覆盖 4 个必答方向 + 商用 + 数据集。
- 核对：对 **12 个关键来源**做了 `web_fetch` 全文抓取（CTD 综述、UHCTD devkit README、TRECVID 数据表、Axis 文档、3 篇 2026 arXiv、Noiseprint/JPEG 相关等），其余为标题+摘要级。
- **本文没有使用任何"我记忆中的数字"**。凡是无法回溯到上述链接的数字，一律未写入或标 `[未证实]`。

### 8.2 明确"未检索到 / 未证实"的条目（供 captain 决策）

| 条目 | 状态 | 说明 |
|------|------|------|
| **DVR**（作为篡改检测算法/数据集） | `[未检索到]` | 检索到的 DVR 均为数字录像机硬件；疑为引用误差 |
| **FETV**（作为取证方法） | 已澄清 | 实为文生视频评测基准（NeurIPS 2023），与取证无关 |
| **TRECVID S3 / SSD** | `[未检索到]` | TRECVID 官方监控 track 为 SED（2008–2017） |
| **DVAD**（作为篡改/异常数据集） | `[未检索到]` | 同名项为 MDPI 2025 的多类异常检测框架，非数据集 |
| **"增益攻击"（gain attack）** | `[未检索到]` | 无专门公开论文；相关信号为 CRF 一致性 / ONVIF 过曝欠曝事件 |
| **VFDD2.1 的精确构成** | `[未证实]` | 仅从第三方论文引用段落读到（2189 视频），未找到官方页面 |
| **FLAD 的精确规模与瑕疵类别数** | `[未证实]` | Kaggle 数据卡称"8 场会议录制"，类别数未逐字核对；论文 PDF 抓取超时 |
| **GenVidBench 的 "6-Million" 含义** | `[摘要]` | 论文标题写 6-Million，未核对是视频数还是片段数 |
| **DeCoF 的数据规模** | `[未证实]` | 未核对 |
| **监控场景下屏幕翻拍的检出率** | `[未证实]` | 未检索到监控域的翻拍评测 |
| **PRNU 在强压缩监控流上的可用性** | `[未证实]` | CVPRW 2019 讨论其困难，但无监控实测数字 |
| **UHCTD 的帧率** | `[未证实]` | README 未写；综述 §IV-E 的表述在 HTML 中疑似乱码（"2424 hours"），未采用 |
| **各深度模型的单帧推理耗时** | `[未实测]` | 本文成本列中凡标 GPU 者均未在本机实测，仅给"GPU 必需"的定性判断 |

### 8.3 给下一环的交接提示

1. **t3（选型）可直接用 §5 的四张表**，分级 A/B/C/D 已经是"稀疏适配性"的结论，不需要重新判。
2. **t4（新算法）建议优先读两篇**：[arXiv 2607.14760](https://arxiv.org/abs/2607.14760)（clean-reference 低频监测，最贴近）与 [arXiv 2310.07886](https://arxiv.org/abs/2310.07886)（CTD 的形式化与 10 类特征消融，可指导特征选择）。
3. **若要动深度模型**：[UHCTD devkit](https://github.com/QuantitativeImagingLaboratory/ctd-devkit) 是最短路径（单帧 → 4 类，Places365 权重微调），但**必须先解决数据集申请**。
4. **本文与 [02](02-sparse-adaptation.md) 无冲突**，§6.3 给了逐条对应关系，合并时不必做取舍。
