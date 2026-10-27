"""一次稀疏抽样的完整判定流程（L2 → L1 → L0 → 判决）。

判决优先级（对应报告 §5.2 的 P-1..P6）：

=====  ========================  ==================================================
优先级  结论                       触发条件
=====  ========================  ==================================================
P-1    ``information_insufficient`` 曝光/白平衡在 burst 内不稳 → 样本不可用，不下结论
P0     ``freeze_replay``         跨采样点 bit-exact，或 静止+无噪声（Q3）
P1     ``persistent_covered``    历史偏离高 + 微时序静止 + 熵/边缘显著下降
P2     ``persistent_defocused``  历史偏离高 + 微时序静止 + 清晰度显著下降
P3     ``persistent_moved``      历史偏离高 + 微时序静止 + ORB 单应/边缘结构改变
P4     ``persistent_change``     历史偏离高 + 静止，但类型证据不足（只报"画面变了"）
P5     ``being_rotated``         微时序内一致平移 + 高 inlier（正在被转动，立即告警）
P6     ``transient_event``       历史偏离高 + 微时序高动态（瞬时事件，不告警）
—      ``normal``                无上述任何一条
=====  ========================  ==================================================

关键设计：**判定"持续性"靠的是"历史偏离高 且 微时序静止"**。
篡改在几秒的 burst 内是空间常量，所以 burst 自参照对持续篡改的灵敏度结构性地为 0；
真正提供"这不是瞬时事件"这条证据的，是 burst 内的**低动态**。
"""

from __future__ import annotations

import base64
import time
from typing import Any, Dict, List, Optional, Sequence

import cv2
import numpy as np

from . import metrics
from .store import (
    MIN_SAMPLES_READY,
    SLOT_CAPACITY,
    THUMB_SIZE,
    CameraState,
    CameraStore,
    SlotSample,
    slot_of,
)

# --------------------------------------------------------------------------- 判决常量
V_NORMAL = "normal"
V_FREEZE = "freeze_replay"
V_COVERED = "persistent_covered"
V_DEFOCUSED = "persistent_defocused"
V_MOVED = "persistent_moved"
V_CHANGE = "persistent_change"
V_OBJECT = "object_change"
V_ROTATED = "being_rotated"
V_TRANSIENT = "transient_event"
V_UNUSABLE = "information_insufficient"
V_COLD = "cold_start"
V_EMPTY = "no_signal"

SAMPLE_VERDICTS: Dict[str, Dict[str, Any]] = {
    V_NORMAL: {"label": "正常", "level": "ok", "alarm": False, "tamperType": "normal"},
    V_FREEZE: {"label": "冻结 / 重放", "level": "critical", "alarm": True, "tamperType": "frozen"},
    V_COVERED: {"label": "持续遮挡", "level": "critical", "alarm": True, "tamperType": "covered"},
    V_DEFOCUSED: {"label": "持续失焦", "level": "critical", "alarm": True, "tamperType": "defocused"},
    V_MOVED: {"label": "摄像头被移位/转动", "level": "critical", "alarm": True, "tamperType": "moved"},
    V_CHANGE: {"label": "画面持续异常", "level": "warning", "alarm": True, "tamperType": "unknown"},
    V_OBJECT: {"label": "场景内物体变动（不告警）", "level": "info", "alarm": False, "tamperType": "object"},
    V_ROTATED: {"label": "正在转动（采样瞬间捕获）", "level": "critical", "alarm": True, "tamperType": "moved"},
    V_TRANSIENT: {"label": "瞬时事件（不告警）", "level": "info", "alarm": False, "tamperType": "normal"},
    V_UNUSABLE: {"label": "样本不可用（不下结论）", "level": "info", "alarm": False, "tamperType": "normal"},
    V_COLD: {"label": "基线建立中（低置信）", "level": "info", "alarm": False, "tamperType": "normal"},
    V_EMPTY: {"label": "无有效画面", "level": "info", "alarm": False, "tamperType": "normal"},
}

# 默认判定阈值（全部可在请求里覆盖）
DEFAULT_PARAMS: Dict[str, Any] = {
    # --- L2 单帧绝对阈值（只在没有基线时兜底，生产不建议依赖）
    "sharpnessFloor": 100.0,        # Laplacian 方差低于它算失焦
    "entropyFloor": 3.0,            # 全局熵低于它算遮挡
    # --- L0 历史偏离
    "dHistAlarm": 0.15,             # dHist 超过它算"画面显著偏离历史基线"
    "outlierAlarm": 0.10,           # 逐像素 z>3 的占比超过它算异常（对局部大面积变化更敏感）
    "relEntropyDrop": 0.30,         # 相对基线熵下降比
    "relSharpnessDrop": 0.45,       # 相对基线清晰度下降比
    "relEdgeDrop": 0.40,            # 相对基线边缘密度下降比
    "relContrastDrop": 0.55,        # 相对基线全局对比度(灰度标准差)下降比 ⇒ 遮挡（区分遮挡/失焦的关键）
    "bgShiftPx": 2.0,               # 与基线背景的相位相关位移超过它 ⇒ 移位
    "bgShiftResponse": 0.08,        # 相位相关响应的下限（太低说明对不齐，不能当归移）
    # --- 参照物（人工标定的静止结构：门/楼角/围墙）
    # 一旦标定了参照物，就不再使用全图相位相关 —— 后者会被树影、旗帜、行人车辆带偏，
    # 这正是"对镜头移动过于敏感"的根因。
    "lmScoreMin": 0.45,             # 模板匹配分数低于它 ⇒ 该参照物不可用（被挡/失焦/光线剧变）
    "lmMarginMin": 0.05,            # 峰值唯一性：最高峰要比次高峰高这么多，否则视为假匹配
    # 子块投票（抗部分遮挡）：整块匹配失败时，把参照物切成 3×3 小块分别匹配，
    # 只要有超过一半的子块对得上、且偏移一致，就认定参照物仍在原处。
    # 解决"一辆车停在参照物中间"这类部分遮挡导致的"参照物丢失"误判。
    "lmSubScoreMin": 0.50,          # 单个子块的最低匹配分数
    # 模板库：一个参照物可保存多套外观模板（夜间/雨天/季节变化由客户补录），
    # 匹配时取最高分；分数够高就早停，避免模板数线性放大算力。
    "lmTemplateEarlyStop": 0.85,    # 某个模板匹配到这个分数就不再试其它模板
    "lmTemplateMinTexture": 4.0,    # 新模板的最低梯度纹理量（太低说明选错了区域）
    "lmTemplateMinEntropy": 2.5,    # 新模板区域的最低熵（太低疑似被遮挡）
    "lmTemplateMinSharpness": 20.0, # 新模板区域的最低清晰度（太低疑似模糊）
    "lmTemplateMinContrast": 8.0,   # 新模板区域的最低对比度（太低疑似纯色）
    "lmMovePx": 6.0,                # 参照物一致位移超过它 ⇒ 镜头被移位（分析尺度，长边 640）
    "lmConsistencyPx": 3.0,         # 各参照物拟合残差上限
    "lmMoveDeg": 0.8,               # 拟合出的旋转角超过它 ⇒ 镜头被转动
    # --- 局部物体变动
    # 真实场景里桌上的杯子/椅子上的包/门口的纸箱每天都会变。这类变化是**局部团块**，
    # 而遮挡/失焦/机位移动是**大面积或全局**变化。用最大连通块占比把两者分开。
    "objectChangeRatio": 0.25,      # 最大变化连通块占画面比例 ≤ 它 ⇒ 视为场景内物体变动
    "objectChangeAlarm": False,     # 物体变动是否告警（默认只记录）
    "lmSearchPadRatio": 0.05,       # 搜索窗口外扩比例（相对画幅长边，仅精修用）
    # --- L1 微时序
    "dBurstTransient": 0.55,        # 微时序动态超过它 ⇒ 瞬时事件
    "rotateOrb": 3.0,               # 一致平移像素
    "rotateInlier": 0.6,            # 平移 inlier 比
    "lumaDriftUnstable": 0.12,      # burst 内亮度漂移过大 ⇒ 样本不可用
    "chromaDriftUnstable": 0.15,    # burst 内色度漂移过大 ⇒ 样本不可用
    # --- 冻结
    "freezeGapMs": 5 * 60 * 1000,   # 跨采样点重复的最短时间间隔（避免同一瞬间重复上报）
    "noiseFloor": 0.30,             # 帧间时间噪声 σ 的绝对下限（灰度级）
    "staticNoiseSpread": 0.002,     # σ 离散度下限
    # --- 判据内部阈值（原散落在 _decide / _classify_persistent 里的字面量）
    "dBurstFullScale": 0.02,        # intraMad 归一化到 0~1 的满量程（再大也只算 1.0）
    "freezeRisk": 0.95,             # 冻结判决的风险分
    "freezeConfidence": 0.90,       # 冻结判决的置信度
    "rotateRisk": 0.90,             # 正在转动的风险分
    "rotateConfidence": 0.85,       # 正在转动的置信度
    "transientRiskRatio": 0.5,      # 瞬时事件的风险 = dHist × 它
    "transientConfidence": 0.60,    # 瞬时事件的置信度
    "affineInlierGlobal": 0.5,      # 与基线参考帧全局配准的最小内点率（判"刚体移动/已移位"用）
    "affineMoveTransPx": 15.0,      # 全局配准平移超过它 ⇒ 机位动了
    "affineMoveRotDegFactor": 2.0,  # 全局配准旋转阈值 = lmMoveDeg × 它
    "affineInlierLocal": 0.4,       # 未标参照物时全局配准的最低内点率（补充证据）
    "affineLocalTransPx": 10.0,     # 未标参照物时全局配准的平移阈值
    "affineLocalRotDeg": 1.5,       # 未标参照物时全局配准的旋转阈值
    "movedAffineRisk": 0.92,        # 刚体移动（全局配准判定机位动了）的风险分
    "movedAffineConfidence": 0.88,  # 同上，置信度
    "objectChangeRiskCap": 0.35,    # 物体变动的风险上限（占比再大也不超过它）
    "objectChangeConfidence": 0.60, # 物体变动的置信度
    "coldStartRisk": 0.55,          # 基线未建立但单帧越线时的风险分
    "coldStartConfidence": 0.25,    # 同上，低置信
    "coldIdleConfidence": 0.15,     # 基线建立中的置信度
    "normalConfidence": 0.80,       # 正常判决的置信度
    # --- 类型投票：证据触发阈值
    "lowBlockRatioCovered": 0.5,    # 低熵块占比超过它 ⇒ 遮挡证据
    "entropyDropExtraFactor": 1.5,  # 熵降超过 relEntropyDrop × 它 ⇒ 再加一票遮挡
    "entropyDropBlurFactor": 0.8,   # 熵降低于 relEntropyDrop × 它 ⇒ 才允许判失焦
    "defocusContrastPreserved": 0.35,  # 对比度降幅低于它 ⇒ 对比度仍保留（判失焦前提）
    "defocusRefNccMin": 0.5,        # 整帧相关性 ≥ 它 ⇒ 内容对得上（判失焦前提）
    "coveredEdgeDensity": 0.0035,   # 边缘密度低于它 ⇒ 画面结构基本消失
    "coveredEdgeContrast": 0.30,    # 边缘消失时，对比度还要再降这么多才判遮挡
    "structureContrastKept": 0.45,  # 对比度降幅低于它 ⇒ 认为画面结构仍在
    "refNccLost": 0.45,             # 整帧相关性低于它 ⇒ 内容已不是原视野
    # --- 类型投票：各类证据的票重
    "coveredVoteContrast": 0.7,     # 全局对比度崩塌 → 遮挡
    "coveredVoteLowBlock": 0.5,     # 低熵块占比 → 遮挡
    "coveredVoteEntropy": 0.6,      # 相对熵降 → 遮挡
    "coveredVoteEntropyExtra": 0.2, # 熵降翻倍时追加给遮挡
    "coveredVoteEdgeCollapse": 0.3, # 参照物失配但结构塌陷 → 遮挡
    "defocusedVote": 0.9,           # 确认失焦
    "defocusedVoteWeak": 0.15,      # 清晰度降了但证据不足的弱票
    "movedVoteCoveredRef": 0.7,     # 参照物失配但结构塌陷 → 遮挡
    "movedVoteLandmark": 0.95,      # 参照物一致位移 → 移位
    "movedVoteDefocusedRef": 0.6,   # 参照物失配但明显变软 → 失焦
    "movedVoteAffineRef": 1.0,      # 参照物失配但全局配准成功 → 移位
    "movedVoteStructureKept": 1.0,  # 结构仍在但整帧对不上 → 移位
    "movedVoteBgShift": 0.85,       # 全图相位相关一致平移 → 移位
    "movedVoteEdgeChange": 0.5,     # 边缘结构改变（次证据）→ 移位
    "movedVoteAffineLocal": 0.8,    # 未标参照物时全局配准 → 移位
    "movedVoteBeingRotated": 0.4,   # burst 内正在转动追加给移位
    # --- 类型投票：得分 → 风险/置信度
    "voteConfidenceBase": 0.45,     # 置信度 = base + scale × 得分
    "voteConfidenceScale": 0.5,
    "voteConfidenceCap": 0.95,      # 置信度上限
    "voteRiskBase": 0.55,           # 风险 = base + scale × 得分
    "voteRiskScale": 0.4,
    "voteRiskCap": 0.99,            # 风险上限
    "voteEmptyRisk": 0.6,           # 一条证据都没有时的风险（只报"画面持续异常"）
    "voteEmptyConfidence": 0.5,     # 同上，置信度
    # --- 基线准入
    "admitBaseline": True,          # 只有正常样本才进基线库
}


def _encode(image: Optional[np.ndarray], max_width: int = 520) -> str:
    if image is None or image.size == 0:
        return ""
    img = image
    h, w = img.shape[:2]
    if w > max_width:
        scale = max_width / float(w)
        img = cv2.resize(img, (max_width, max(int(h * scale), 1)), interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", img, [int(cv2.IMWRITE_JPEG_QUALITY), 78])
    if not ok:
        return ""
    return "data:image/jpeg;base64," + base64.b64encode(buf.tobytes()).decode("ascii")


def _median(values: Sequence[float]) -> float:
    vals = [float(v) for v in values if v is not None and np.isfinite(v)]
    return float(np.median(vals)) if vals else 0.0


def _ratio_drop(current: float, baseline: float) -> float:
    """相对下降比（baseline 为 0 时返回 0，避免除零）。"""
    if baseline is None or baseline <= 1e-9:
        return 0.0
    return float(max(0.0, (baseline - current) / baseline))


# --------------------------------------------------------------------------- 判决纯函数
# 这些函数只由入参决定返回值，不读写实例状态，便于单独调参与单测。
# 约定：返回 None 表示"本分支不成立"；返回元组表示"本分支命中，直接作为 _decide 的判决"。
def _check_unusable(l1: Dict[str, Any], l0: Dict[str, Any], cfg: Dict[str, Any]) -> Optional[tuple]:
    """P-1 样本可用性：burst 内曝光/白平衡未收敛时不下结论。"""
    if not l1.get("available"):
        return None
    if l1.get("lumaDrift", 0.0) > cfg["lumaDriftUnstable"]:
        return (V_UNUSABLE, 0.0, 0.0,
                [f"burst 内亮度漂移 {l1['lumaDrift']:.3f} 超过 {cfg['lumaDriftUnstable']}，"
                 "判定为曝光/自动增益未收敛，本次不下结论（样本不并入基线）"],
                {"cell": "unusable", "dHist": l0.get("dHist"), "dBurst": l1.get("intraMad")})
    if l1.get("chromaDrift", 0.0) > cfg["chromaDriftUnstable"]:
        return (V_UNUSABLE, 0.0, 0.0,
                [f"burst 内色度漂移 {l1['chromaDrift']:.3f} 超过 {cfg['chromaDriftUnstable']}，"
                 "白平衡未收敛，本次不下结论"],
                {"cell": "unusable", "dHist": l0.get("dHist"), "dBurst": l1.get("intraMad")})
    return None


def _check_being_rotated(l1: Dict[str, Any], l0: Dict[str, Any],
                         cfg: Dict[str, Any]) -> Optional[tuple]:
    """P5 正在被转动：burst 内一致平移 + 足够 inlier（先于持续性判定）。"""
    if not l1.get("beingRotated"):
        return None
    return (V_ROTATED, cfg["rotateRisk"], cfg["rotateConfidence"],
            [f"burst 内一致平移 {l1.get('intraOrbTranslation')} px、inlier {l1.get('intraOrbInlier')}"
             f"，超过阈值 {cfg['rotateOrb']} px / {cfg['rotateInlier']}：镜头正在被转动"],
            {"cell": "being_rotated", "dHist": l0.get("dHist"),
             "dBurst": l1.get("intraMad")})


def _stability_cell(l0: Dict[str, Any], l1: Dict[str, Any],
                    cfg: Dict[str, Any]) -> tuple:
    """稳定性分解：历史偏离(高/低) × 微时序动态(高/低) → 4 格矩阵。"""
    d_hist = float(l0.get("dHist") or 0.0)
    outlier_ratio = float(l0.get("pixelOutlierRatio") or 0.0)
    baseline_ready = bool(l0.get("available")) and (l0.get("dHist") is not None)
    dBurst = float(l1.get("intraMad") or 0.0)
    # 把 intraMad 归一到 0~1：经验上 0.02 已经很剧烈
    d_burst_norm = min(dBurst / cfg["dBurstFullScale"], 1.0)

    # 两个口径必须并用：
    #   dHist 是"全图平均偏离"，对全局变化敏感，但局部大面积变化会被稀释；
    #   pixelOutlierRatio 是"有多少像素显著偏离"，对局部遮挡/异物更敏感。
    # 只用 dHist 会把"画面里多了一辆车"这种局部变化算成正常。
    high_hist = baseline_ready and (
        d_hist > cfg["dHistAlarm"] or outlier_ratio > cfg["outlierAlarm"]
    )
    high_burst = d_burst_norm > cfg["dBurstTransient"]

    if high_hist and not high_burst:
        cell = "persistent"
    elif high_hist and high_burst:
        cell = "transient"
    elif (not high_hist) and high_burst:
        cell = "activity"
    else:
        cell = "normal"

    matrix = {"dHist": round(d_hist, 4), "dBurst": round(d_burst_norm, 4),
              "cell": cell, "baselineReady": baseline_ready}
    return cell, matrix, d_hist, outlier_ratio, d_burst_norm, baseline_ready, high_burst


def _rigid_motion_evidence(l0: Dict[str, Any], cfg: Dict[str, Any],
                           matrix: Dict[str, Any]) -> Optional[tuple]:
    """与基线参考帧的全局配准成功且位移/旋转足够大 ⇒ 机位本身动了（刚体运动）。"""
    contour_drop = float(l0.get("contrastDrop") or 0.0)
    structure_alive = contour_drop < cfg["relContrastDrop"]
    affine = ((l0.get("landmarks") or {}).get("affine") or {})
    if not (structure_alive and affine.get("ok")
            and float(affine.get("inlierRatio") or 0.0) >= cfg["affineInlierGlobal"]):
        return None
    rot = abs(float(affine.get("rotationDeg") or 0.0))
    trans = float(affine.get("translation") or 0.0)
    if not (rot >= cfg["lmMoveDeg"] * cfg["affineMoveRotDegFactor"]
            or trans >= cfg["affineMoveTransPx"]):
        return None
    reason = (
        f"与基线参考帧的 ORB 全局配准成功：{trans:.1f}px 平移 / {rot:.1f}° 旋转"
        f"（内点率 {affine.get('inlierRatio')}，{affine.get('matches')} 对匹配点）——"
        "整幅画面是同一个刚体变换，说明是机位本身动了，不是画面内容变化"
    )
    return reason, (V_MOVED, cfg["movedAffineRisk"], cfg["movedAffineConfidence"],
                    [reason], matrix)


def _object_change_evidence(l2: Dict[str, Any], l0: Dict[str, Any], cfg: Dict[str, Any],
                            matrix: Dict[str, Any]) -> Optional[tuple]:
    """变化集中在画面一小块、且不遮挡/不失焦/参照物未动 ⇒ 场景内物体变动。"""
    changed = l0.get("changed") or {}
    largest = float(changed.get("largestRatio") or 0.0)
    covered_ev = (float(l0.get("entropyDrop") or 0.0) >= cfg["relEntropyDrop"]) \
        or (l2["entropy"] < cfg["entropyFloor"])
    blur_ev = float(l0.get("sharpnessDrop") or 0.0) >= cfg["relSharpnessDrop"]
    lm_consensus = ((l0.get("landmarks") or {}).get("consensus") or {})
    if not (largest > 0.0 and largest <= cfg["objectChangeRatio"]
            and not covered_ev and not blur_ev
            and not lm_consensus.get("moved")):
        return None
    if cfg["objectChangeAlarm"]:
        reason = (
            f"变化集中在画面一块区域（最大连通块占 {largest:.0%} ≤ "
            f"{cfg['objectChangeRatio']:.0%}），不遮挡、不失焦、机位未动："
            "判为场景内物体变动（已按配置告警）"
        )
    else:
        reason = (
            f"变化集中在画面一块区域（最大连通块占 {largest:.0%} ≤ "
            f"{cfg['objectChangeRatio']:.0%}），不遮挡、不失焦、机位未动 ⇒ "
            "判为场景内物体变动（如桌面物品被挪动），只记录不告警。"
            "如需连这块也不管，可把它标成「忽略区」"
        )
    return reason, (V_OBJECT, min(cfg["objectChangeRiskCap"], largest),
                    cfg["objectChangeConfidence"], [reason],
                    {**matrix, "cell": "object_change", "changed": changed})


class SparseEngine:
    """低频采样判定的唯一入口。"""

    def __init__(self, store: CameraStore, params: Optional[Dict[str, Any]] = None):
        self.store = store
        self.params = dict(DEFAULT_PARAMS)
        if params:
            self.params.update({k: v for k, v in params.items() if k in DEFAULT_PARAMS})

    # ------------------------------------------------------------------ 主流程
    def ingest(self, camera_id: str, frames: Sequence[np.ndarray], ts_ms: Optional[int] = None,
               name: Optional[str] = None, reset: bool = False) -> Dict[str, Any]:
        started = time.perf_counter()
        ts = int(ts_ms) if ts_ms is not None else int(time.time() * 1000)

        valid = [f for f in frames if isinstance(f, np.ndarray) and f.size > 0]
        if not valid:
            return self._render_result(
                camera_id, ts, V_EMPTY, risk=0.0, confidence=0.0,
                layers={}, matrix={}, reasons=["未收到任何有效画面"],
                timings={"total": 0.0}, baseline={}, frames=0,
            )

        if reset:
            self.store.reset(camera_id)
        state = self.store.get(camera_id)
        if name:
            state.name = name

        # ---------------------------------------------------------- L2 单帧层
        t0 = time.perf_counter()
        grays = [metrics.prepare_gray(f) for f in valid]
        l2_per_frame = []
        for gray in grays:
            low_entropy, low_ratio, _ = metrics.block_entropy_stats(gray)
            l2_per_frame.append({
                "sharpness": metrics.laplacian_var(gray),
                "entropy": metrics.global_entropy(gray),
                "minBlockEntropy": low_entropy,
                "lowBlockRatio": low_ratio,
                "edgeDensity": metrics.edge_density(gray),
                "noiseSigma": metrics.noise_sigma(gray),
                "luma": metrics.luma_mean(gray),
            })
        l2 = {
            "sharpness": _median([m["sharpness"] for m in l2_per_frame]),
            "entropy": _median([m["entropy"] for m in l2_per_frame]),
            "minBlockEntropy": _median([m["minBlockEntropy"] for m in l2_per_frame]),
            "lowBlockRatio": _median([m["lowBlockRatio"] for m in l2_per_frame]),
            "edgeDensity": _median([m["edgeDensity"] for m in l2_per_frame]),
            "noiseSigma": _median([m["noiseSigma"] for m in l2_per_frame]),
            "luma": _median([m["luma"] for m in l2_per_frame]),
        }
        l2_ms = (time.perf_counter() - t0) * 1000.0

        # ---------------------------------------------------------- L1 微时序层
        t0 = time.perf_counter()
        l1 = metrics.analyze_burst(valid, {
            "staticNoiseSpread": self.params["staticNoiseSpread"],
            "noiseFloor": self.params["noiseFloor"],
            "rotateOrb": self.params["rotateOrb"],
            "rotateInlier": self.params["rotateInlier"],
        })
        l1_ms = (time.perf_counter() - t0) * 1000.0

        # ---------------------------------------------------------- L0 跨采样点层
        t0 = time.perf_counter()
        slot = slot_of(ts)
        median_bg, mad_bg, baseline_meta = state.background(slot)
        rep_gray = grays[len(grays) // 2]
        thumb_gray = cv2.resize(rep_gray, THUMB_SIZE, interpolation=cv2.INTER_AREA)
        # 忽略区：用户圈出的"会频繁变化、不应参与篡改判定"的区域（桌面、货架、门口堆放点）。
        # 这些像素不参与 dHist / 离群占比统计 —— 否则桌上挪个杯子就会误报。
        ignore_mask = metrics.region_mask(thumb_gray.shape, state.ignore_regions())
        # 光度对齐：把"整体变暗/变灰/开灯"这类**全局仿射变化**从偏离计算里剔除。
        # 注意对比度仍在**原始帧**上算（见下），否则遮挡造成的对比度塌陷会被一起"归一化"掉。
        if median_bg is not None:
            aligned_gray, photometric = metrics.photometric_align(median_bg, thumb_gray)
        else:
            aligned_gray, photometric = thumb_gray, {"ok": False}
        # 只有"残差很小"才认定这是纯光度变化、可以采用对齐结果。
        # 否则（画面里真有多出来的东西）拟合会被那些结构带偏，
        # 反而把没变的区域也系统性偏移掉 —— 那是帮倒忙。
        resid = float(photometric.get("residualRatio", 1.0) or 1.0)
        apply_align = bool(photometric.get("ok")) and resid <= 0.25
        photo_for_z = aligned_gray if apply_align else thumb_gray
        photometric["applied"] = apply_align
        l0 = metrics.robust_z_profile(photo_for_z, median_bg, mad_bg, ignore=ignore_mask)
        l0["photometric"] = photometric
        l0.update(self._baseline_scalars(state, slot, l2))
        # 对比度（灰度标准差）相对变化。
        # 这是区分"遮挡"和"失焦"的关键量：
        #   * 遮挡（纸/手/布盖住）→ 画面近乎纯色 → 对比度**崩塌**
        #   * 失焦 → 低频结构还在 → 对比度**基本保留**，只是边缘变软
        # 只用熵不够：纸面有噪声和 JPEG 纹理时，熵可能只掉 20%，判不出遮挡。
        l0["contrastCur"] = round(float(thumb_gray.std()), 2)
        if median_bg is not None:
            base_contrast = float(np.clip(median_bg, 0, 255).std())
            l0["contrastBase"] = round(base_contrast, 2)
            if base_contrast > 1e-6:
                l0["contrastDrop"] = round(max(0.0, 1.0 - l0["contrastCur"] / base_contrast), 4)
        # 与基线背景的全局位移：已发生的"移位"在 burst 内是常量、且不改变边缘/熵/清晰度，
        # 只能靠"当前帧与历史基线之间存在一致平移"来判定。
        if median_bg is not None:
            shift = metrics.phase_shift(median_bg, thumb_gray)
            l0["bgShift"] = shift
            l0["bgShiftMagnitude"] = shift.get("magnitude", 0.0)
            l0["bgShiftResponse"] = shift.get("response", 0.0)
        # 参照物匹配：用人工标定的静止结构（门/楼角/围墙）估全局位移。
        # 比全图相位相关稳得多 —— 树影、旗帜、行人车辆不会带偏它。
        l0["landmarks"] = self._match_landmarks(state, rep_gray)
        l0_ms = (time.perf_counter() - t0) * 1000.0

        # ---------------------------------------------------------- 判决
        verdict, risk, confidence, reasons, matrix = self._decide(
            state, l1, l2, l0, baseline_meta, ts,
        )
        info = SAMPLE_VERDICTS.get(verdict, SAMPLE_VERDICTS[V_NORMAL])

        # ---------------------------------------------------------- 可视化
        heat = metrics.diff_heatmap(thumb_gray, median_bg) if l0.get("available") else None
        comparison = metrics.side_by_side(
            [cv2.cvtColor(median_bg.astype(np.uint8), cv2.COLOR_GRAY2BGR) if median_bg is not None else None,
             cv2.cvtColor(thumb_gray, cv2.COLOR_GRAY2BGR),
             heat],
            ["median baseline", "current", "diff"],
        ) if median_bg is not None else None

        # ---------------------------------------------------------- 基线准入
        admitted = False
        if verdict in (V_NORMAL, V_COLD, V_TRANSIENT) and self.params["admitBaseline"]:
            digest = l1["hashes"][0] if l1.get("hashes") else metrics.frame_hash(grays[0])
            state.add_sample(SlotSample(
                ts_ms=ts, hash=digest, sharpness=l2["sharpness"], entropy=l2["entropy"],
                edge=l2["edgeDensity"], sigma=l2["noiseSigma"], luma=l2["luma"],
                ahash=metrics.a_hash(thumb_gray), thumb=thumb_gray.copy(),
            ))
            admitted = True
            # 基线参考帧：只在还没有时写入（或"重设基准"后被清空时重建）。
            # 不能每个正常样本都刷——那样缓慢的镜头漂移会被一步步学进参考帧。
            if state.ref_gray is None:
                state.ref_gray = rep_gray.copy()
        for digest in (l1.get("hashes") or []):
            state.remember(digest, ts, metrics.a_hash(thumb_gray))

        baseline_after = {
            "status": state.baseline_status(),
            "samples": state.total_samples(),
            "slots": len([1 for items in state.slots.values() if items]),
            "slotSamples": len(state.slot_samples(slot)),
            "slot": slot,
            "slotCapacity": SLOT_CAPACITY,
            "minReady": MIN_SAMPLES_READY,
            "admitted": admitted,
            "source": baseline_meta.get("source"),
        }

        result = self._render_result(
            camera_id, ts, verdict, risk=risk, confidence=confidence,
            layers={
                "L2": {k: round(float(v), 4) for k, v in l2.items()},
                "L1": {k: v for k, v in l1.items()
                       if k not in ("hashes", "translations", "cfg", "sigmaSeries", "sharpnessSeries")},
                "L0": l0,
            },
            matrix=matrix,
            reasons=reasons,
            timings={"L2": round(l2_ms, 2), "L1": round(l1_ms, 2), "L0": round(l0_ms, 2),
                     "total": round((time.perf_counter() - started) * 1000.0, 2)},
            baseline=baseline_after,
            frames=len(valid),
            extra={
                "keyframe": _encode(valid[len(valid) // 2]),
                "baselineCompare": _encode(comparison),
                "diffHeatmap": _encode(heat),
                "sharpnessSeries": l1.get("sharpnessSeries", []),
                "sigmaSeries": l1.get("sigmaSeries", []),
                "translations": l1.get("translations", []),
                "l2PerFrame": [
                    {k: round(float(v), 4) for k, v in m.items()} for m in l2_per_frame
                ],
            },
        )

        state.push_history({
            "tsMs": ts,
            "verdict": verdict,
            "label": info["label"],
            "alarm": bool(info["alarm"]),
            "risk": round(risk, 4),
            "tamperType": info["tamperType"],
            "dHist": l0.get("dHist"),
            "dBurst": l1.get("intraMad"),
            "quadrant": l1.get("quadrant"),
            "slot": slot,
        })
        result["history"] = state.history[:60]
        result["slots"] = state.slot_summary()
        self.store.save(state)
        return result

    # ------------------------------------------------------------------ 判决核心
    def _decide(self, state: CameraState, l1: Dict[str, Any], l2: Dict[str, Any],
                l0: Dict[str, Any], baseline_meta: Dict[str, Any], ts: int
                ) -> tuple:
        cfg = self.params
        reasons: List[str] = []

        # ---------- P-1 样本可用性（必须最先判，坏样本不能拿去比基线）
        unusable = _check_unusable(l1, l0, cfg)
        if unusable is not None:
            return unusable

        # ---------- P0 冻结 / 重放
        freeze, freeze_reason, freeze_evidence = self._detect_freeze(state, l1, l2, ts)
        if freeze:
            reasons.append(freeze_reason)
            return (V_FREEZE, cfg["freezeRisk"], cfg["freezeConfidence"], reasons,
                    {"cell": "freeze", "dHist": l0.get("dHist"),
                     "dBurst": l1.get("intraMad"), "freezeEvidence": freeze_evidence})

        # ---------- P5 正在被转动（先于持续性判定：这是"正在发生"的强证据）
        rotated = _check_being_rotated(l1, l0, cfg)
        if rotated is not None:
            return rotated

        # ---------- 稳定性分解 4 格矩阵
        cell, matrix, d_hist, outlier_ratio, d_burst_norm, baseline_ready, high_burst = \
            _stability_cell(l0, l1, cfg)

        # ---------- 瞬时事件：不告警
        if cell == "transient":
            reasons.append(
                f"历史偏离高（dHist={d_hist:.3f}）但 burst 内动态也高（dBurst={d_burst_norm:.3f}）"
                "：判为瞬时事件（有人经过 / 光照突变），不告警"
            )
            return (V_TRANSIENT, d_hist * cfg["transientRiskRatio"],
                    cfg["transientConfidence"], reasons, matrix)

        # ---------- P1~P4 持续性篡改
        if cell == "persistent":
            # 先看"全局配准"这条强证据：能跟基线参考帧配出大角度旋转/大平移，
            # 说明画面是**刚体运动**，那就是机位真的动了。
            # 这条必须排在类型投票之前——旋转插值会让清晰度下降，
            # 否则"真被转走"会被误判成"失焦"。
            #
            # 但必须排除"画面已经被遮住"的情况：纯色画面上 ORB 可能凑出少量
            # 伪匹配点，硬解出一个大变换。遮挡时结构已经塌陷，不能拿配准说事。
            rigid = _rigid_motion_evidence(l0, cfg, matrix)
            if rigid is not None:
                reasons.append(rigid[0])
                return rigid[1]

            # 局部物体变动：变化集中在画面一小块（最大连通块占比小），
            # 且不遮挡、不失焦、参照物也没动 —— 这是"桌上东西被挪走了"，不是篡改。
            obj = _object_change_evidence(l2, l0, cfg, matrix)
            if obj is not None:
                reasons.append(obj[0])
                return obj[1]

            verdict, risk, confidence, type_reasons = self._classify_persistent(l1, l2, l0)
            reasons.extend(type_reasons)
            return (verdict, risk, confidence, reasons, matrix)

        # ---------- 基线未建立：只能给"低置信"结论
        if not baseline_ready:
            abs_covered = l2["entropy"] < cfg["entropyFloor"]
            abs_blur = l2["sharpness"] < cfg["sharpnessFloor"]
            if abs_covered or abs_blur:
                kind = "遮挡" if abs_covered else "失焦"
                reasons.append(
                    f"基线尚未建立（{baseline_meta.get('status')}，样本 {baseline_meta.get('samples')}），"
                    f"但单帧绝对指标已越过阈值（{kind}）——低置信提示，需积累基线后复核"
                )
                return (V_COLD, cfg["coldStartRisk"], cfg["coldStartConfidence"], reasons,
                        {**matrix, "cell": "cold_start_alert"})
            reasons.append(
                f"基线建立中（{baseline_meta.get('status')}，{baseline_meta.get('samples')} 个样本，"
                f"{len([1 for i in state.slots.values() if i])} 个时段槽）：本次只记录，不下结论"
            )
            return (V_COLD, 0.0, cfg["coldIdleConfidence"], reasons, {**matrix, "cell": "cold_start"})

        motion_desc = "微时序静止" if not high_burst else "微时序有动态但未超出瞬时阈值"
        reasons.append(
            f"历史偏离低（dHist={d_hist:.3f} ≤ {cfg['dHistAlarm']}，"
            f"离群占比 {outlier_ratio:.3f} ≤ {cfg['outlierAlarm']}）、"
            f"{motion_desc}（dBurst={d_burst_norm:.3f}）：与基线一致"
        )
        return (V_NORMAL, 0.0, cfg["normalConfidence"], reasons, matrix)

    # ------------------------------------------------------------------ 冻结判据
    def _detect_freeze(self, state: CameraState, l1: Dict[str, Any], l2: Dict[str, Any],
                       ts: int) -> tuple:
        """冻结/重放判据。

        返回 ``(是否冻结, 原因文本, 证据字典)``。证据字典会透传到前端，
        因为"冻结"可能由两种完全不同的机制触发，界面必须讲清楚是哪一种：

        * ``d1`` 跨采样点 bit-exact —— 强证据，与 burst 内部的动态无关；
        * ``d3`` 静止 + 无独立时间噪声 —— 依赖 burst 内部证据。
        """
        cfg = self.params
        evidence = {"d1": False, "d3": False, "gapMs": 0}
        if not l1.get("available"):
            return False, "", evidence

        # D1：跨采样点 bit-exact —— 与历史某次抽样逐位相同
        # 注意：这条必须在"跨采样点"上做，不能只看 burst 内。H.264 静止场景会走
        # skip macroblock、3DNR 会让连续帧高度相似，burst 内重复是合法链路的常态。
        gap = cfg["freezeGapMs"]
        for digest in (l1.get("hashes") or []):
            prev_ts = state.hashes.get(digest)
            if prev_ts is not None and abs(ts - prev_ts) >= gap:
                evidence["d1"] = True
                evidence["gapMs"] = int(abs(ts - prev_ts))
                return True, (
                    f"当前画面与 {int(abs(ts - prev_ts) / 1000)} 秒前的历史样本**逐位完全相同**"
                    f"（bit-exact，D1）：独立采样不可能产生完全相同的像素，判为冻结/重放"
                ), evidence

        # D3/D4 + 运动×噪声象限 Q3：静止 且 无独立时间噪声
        # 注意 noiseJudgeable：K=1 时没有帧间差分，无法估计时间噪声，本条判据不成立，
        # 冻结只能靠跨采样点 D1（这正是"K=1 也能用"的来源）。
        #
        # 还要注意一个陷阱：**压缩视频的静止画面**（H.264 skip macroblock、3DNR）
        # 解码出来也可能几乎逐位相同，首次抽样就凭 Q3 定罪会误报。
        # 所以 Q3 只在"已经有历史样本"时才定罪；首次抽样只记录、等下一次用 D1 确认。
        if l1.get("noiseJudgeable") and l1.get("quadrant") == "Q3" and l1.get("noiseAbsent"):
            if state.total_samples() < 1:
                return False, (
                    f"burst 内静止且帧间时间噪声 σ={l1.get('sigmaPerFrame')} 异常低（疑似无独立噪声）；"
                    "但这是该机位的首次抽样，压缩流静止画面也会如此，暂不定罪——"
                    "已记录本次画面哈希，下一次抽样若仍逐位相同将由跨采样点判据(D1)确认"
                ), evidence
            evidence["d3"] = True
            return True, (
                f"burst 内静止（去噪后 intraMad={l1.get('intraMad')}）且**帧间时间噪声** "
                f"σ={l1.get('sigmaPerFrame')} 低于下限 {cfg['noiseFloor']}"
                f"（空间噪声 σ={l1.get('sigmaSpatial')}，离散度 {l1.get('sigmaSpread')}）"
                f"，且该机位已有 {state.total_samples()} 个历史样本："
                f"真实传感器每帧都有独立噪声，帧差不可能恒为 0，判为冻结/重放（象限 Q3）"
            ), evidence
        return False, "", evidence

    # ------------------------------------------------------------------ 类型投票
    def _classify_persistent(self, l1: Dict[str, Any], l2: Dict[str, Any], l0: Dict[str, Any]
                             ) -> tuple:
        cfg = self.params
        reasons: List[str] = []

        entropy_drop = float(l0.get("entropyDrop") or 0.0)
        sharp_drop = float(l0.get("sharpnessDrop") or 0.0)
        edge_drop = float(l0.get("edgeDrop") or 0.0)
        low_block = float(l2.get("lowBlockRatio") or 0.0)
        contrast_drop = float(l0.get("contrastDrop") or 0.0)

        votes: Dict[str, float] = {"covered": 0.0, "defocused": 0.0, "moved": 0.0}

        # ---------------- 遮挡：证据是"灰度分布塌陷 + 对比度崩塌"
        # 全遮挡画面清晰度也会归零，所以"清晰度下降"根本不能用来判遮挡；
        # 真正有区分力的是熵 / 低熵块 / **全局对比度**。
        # 加对比度这个维度很关键：纸面、手掌、布料盖住镜头时，虽然有噪声和 JPEG 纹理
        # 让熵只掉 20%，但灰度标准差会掉到原来的几分之一 —— 信号最强。
        if contrast_drop >= cfg["relContrastDrop"]:
            votes["covered"] += cfg["coveredVoteContrast"]
            reasons.append(
                f"全局对比度（灰度标准差）相对基线下降 {contrast_drop:.1%}"
                f"（阈值 {cfg['relContrastDrop']:.0%}）：画面趋于单一颜色 ⇒ 遮挡"
            )
        if low_block >= cfg["lowBlockRatioCovered"]:
            votes["covered"] += cfg["coveredVoteLowBlock"]
            reasons.append(f"低熵块占比 {low_block:.2f} ≥ {cfg['lowBlockRatioCovered']}："
                           "画面大面积为无纹理区域")
        if entropy_drop >= cfg["relEntropyDrop"]:
            votes["covered"] += cfg["coveredVoteEntropy"]
            reasons.append(f"熵相对基线下降 {entropy_drop:.1%}（阈值 {cfg['relEntropyDrop']:.0%}）："
                           "灰度分布向少数色阶塌陷")
        if entropy_drop >= cfg["entropyDropExtraFactor"] * cfg["relEntropyDrop"]:
            votes["covered"] += cfg["coveredVoteEntropyExtra"]

        # ---------------- 失焦：证据是"直方图/对比度基本不变，只是变软"
        # 这是与遮挡的关键分界：模糊不改变灰度分布，只削弱高频。
        # 所以要求"对比度没有被破坏"，否则就是遮挡而不是失焦。
        #
        # 还要求"内容对得上"（与基线参考帧 NCC ≥ 0.5）：
        # 如果画面根本换成别的东西了，它的清晰度本来就可能更低，
        # 这时候拿"清晰度下降"解释成失焦是错的。
        contrast_preserved = contrast_drop < cfg["defocusContrastPreserved"]
        ref_ncc = (l0.get("landmarks") or {}).get("refNcc")
        content_matches = (ref_ncc is None) or (float(ref_ncc) >= cfg["defocusRefNccMin"])
        if sharp_drop >= cfg["relSharpnessDrop"]:
            if entropy_drop < cfg["entropyDropBlurFactor"] * cfg["relEntropyDrop"] \
                    and contrast_preserved and content_matches:
                votes["defocused"] += cfg["defocusedVote"]
                reasons.append(
                    f"清晰度相对基线下降 {sharp_drop:.1%}（阈值 {cfg['relSharpnessDrop']:.0%}），"
                    f"而熵只降 {entropy_drop:.1%}、对比度只降 {contrast_drop:.1%}、"
                    f"整帧相关性 {ref_ncc}：灰度分布与对比度都在、只是边缘变软 ⇒ 失焦"
                )
            else:
                votes["defocused"] += cfg["defocusedVoteWeak"]
                hint = "" if content_matches else f"（整帧相关性仅 {ref_ncc}，内容已变）"
                reasons.append(
                    f"清晰度下降 {sharp_drop:.1%}，但熵降 {entropy_drop:.1%}、"
                    f"对比度降 {contrast_drop:.1%}{hint}，不足以判定失焦"
                )

        # ---------------- 移位：与基线之间存在一致的全局平移
        # 这是"已发生的移位"唯一可靠的判据：镜头转过去停住之后，burst 内没有运动、
        # 边缘密度/熵/清晰度都几乎不变，只有"和基线对不上、而且是整体平移错位"能说明问题。
        #
        # 但"全图相位相关"在真实监控画面里非常容易过敏：树影、旗帜、行人车辆、雨雪
        # 都会把相关峰推偏。所以**一旦人工标定了参照物，就只信参照物**——
        # 建筑物不会自己走，参照物一致位移才是镜头真的被转了。
        landmarks = l0.get("landmarks") or {}
        consensus = landmarks.get("consensus") or {}
        landmark_configured = bool(landmarks.get("configured"))

        if landmark_configured:
            if consensus.get("moved"):
                votes["moved"] += cfg["movedVoteLandmark"]
                reasons.append(
                    f"已标定 {consensus.get('count')} 个参照物，它们能被同一个刚体变换解释："
                    f"{consensus.get('translation')}px 平移 / {consensus.get('rotationDeg')}° 旋转"
                    f"（拟合残差仅 {consensus.get('residual')}px）——"
                    "建筑物这类静止结构不会自己移动 ⇒ 摄像头被移位/转动"
                )
            elif consensus.get("available") and not consensus.get("lost"):
                reasons.append(
                    f"已标定 {consensus.get('count')} 个参照物，最大一致位移仅 "
                    f"{consensus.get('magnitude')}px（阈值 {cfg['lmMovePx']}px）⇒ 不是移位"
                )
            else:
                # 参照物全部失配。有四种可能，必须按"证据强度"分开：
                #   a) 镜头被整体挡住（纸/手/布）→ 遮挡
                #   b) 严重失焦 → 失焦
                #   c) 视野真的变了（转动/移位/换源）→ 移位
                #   d) 都不是（天气/光照变化导致模板不像）→ 不下移位结论
                #
                # 关键区分：**遮挡会把画面的"结构"一起抹掉，而换视野不会**。
                #   * 纸遮挡：边缘塌陷 + 对比度崩塌 → 画质问题
                #   * 换视野：新场景同样有边缘和对比度 → 视野问题
                # 只看 NCC 会把这两种混为一谈（纸遮挡时 NCC 也会掉到 0.4）。
                contrast_drop_here = float(l0.get("contrastDrop") or 0.0)
                edge_density = float(l2.get("edgeDensity") or 0.0)
                covered_ev = (
                    (entropy_drop >= cfg["relEntropyDrop"])
                    or (low_block >= cfg["lowBlockRatioCovered"])
                    or (contrast_drop_here >= cfg["relContrastDrop"])
                    # 边缘几乎消失 + 对比度也掉了 ⇒ 更像"被盖住"而不是"变软"
                    or (edge_density < cfg["coveredEdgeDensity"]
                        and contrast_drop_here >= cfg["coveredEdgeContrast"])
                )
                blur_ev = sharp_drop >= cfg["relSharpnessDrop"]
                # 画面还剩不剩结构？对比度没崩、边缘不是接近 0
                structure_kept = (contrast_drop_here < cfg["structureContrastKept"]) \
                    and (edge_density >= cfg["coveredEdgeDensity"])
                affine = ((l0.get("landmarks") or {}).get("affine") or {})
                ncc = float(((l0.get("landmarks") or {}).get("refNcc")) or 0.0)
                rot = abs(float(affine.get("rotationDeg") or 0.0))
                trans = float(affine.get("translation") or 0.0)
                affine_moved = bool(
                    affine.get("ok")
                    and (rot >= cfg["lmMoveDeg"] * cfg["affineMoveRotDegFactor"]
                         or trans >= cfg["affineMoveTransPx"])
                )

                if covered_ev:
                    votes["covered"] += cfg["movedVoteCoveredRef"]
                    reasons.append(
                        f"参照物全部失配，但画面结构已塌陷（对比度降 {contrast_drop_here:.1%}、"
                        f"熵降 {entropy_drop:.1%}、边缘密度 {edge_density:.4f}）⇒ 按遮挡处理，不判移位"
                    )
                elif blur_ev and ncc >= cfg["defocusRefNccMin"] and not affine_moved:
                    votes["defocused"] += cfg["movedVoteDefocusedRef"]
                    reasons.append(
                        f"参照物全部失配，但画面明显变软（清晰度降 {sharp_drop:.1%}）、"
                        f"对比度只降 {contrast_drop_here:.1%}、整帧相关性仍有 {ncc:.2f}"
                        "（内容对得上）⇒ 按失焦处理，不判移位"
                    )
                elif affine_moved:
                    votes["moved"] += cfg["movedVoteAffineRef"]
                    reasons.append(
                        f"参照物全部失配，但与基线参考帧的全局配准成功：{trans:.1f}px 平移 / "
                        f"{rot:.1f}° 旋转（内点率 {affine.get('inlierRatio')}）⇒ 摄像头被转动/移位"
                    )
                elif ncc < cfg["refNccLost"] and structure_kept:
                    votes["moved"] += cfg["movedVoteStructureKept"]
                    reasons.append(
                        f"参照物全部失配，画面结构仍在（边缘密度 {edge_density:.4f}、"
                        f"对比度降 {contrast_drop_here:.1%}），但与基线参考帧整体相关性只有 {ncc:.2f}"
                        "⇒ 画面内容已不是原机位视野，判定为被转动/移位/换源"
                    )
                else:
                    # 参照物失配，但既没有遮挡/失焦的强证据，也没有移位的强证据。
                    # 典型场景：天气/光照变化让模板不像了。此时不能硬判移位。
                    reasons.append(
                        f"参照物失配，但证据不足以下移位结论"
                        f"（整帧相关性 {ncc:.2f}、结构保留={structure_kept}、"
                        f"对比度降 {contrast_drop_here:.1%}）：可能是光照/天气变化导致模板失配"
                    )
        else:
            shift = l0.get("bgShift") or {}
            shift_mag = float(shift.get("magnitude") or 0.0)
            shift_resp = float(shift.get("response") or 0.0)
            # 前提：画面本身还有结构。边缘已经消失（遮挡成大片纯色）时，
            # 相位相关算出的"位移"只是噪声在对齐噪声，必须排除。
            has_structure = edge_drop < cfg["relEdgeDrop"]
            if has_structure and shift_mag >= cfg["bgShiftPx"] and shift_resp >= cfg["bgShiftResponse"]:
                votes["moved"] += cfg["movedVoteBgShift"]
                reasons.append(
                    f"当前画面与基线背景存在一致全局平移 {shift_mag:.1f}px"
                    f"（相位相关响应 {shift_resp:.2f}，阈值 {cfg['bgShiftPx']}px / {cfg['bgShiftResponse']}）"
                    "，且画面既不模糊也未变平 ⇒ 镜头可能已被移位"
                    "（提示：标定参照物可显著降低此判据的过敏）"
                )
            # 边缘结构改变、但既不是"变平"也不是"变软"，作为移位的次证据
            if edge_drop >= cfg["relEdgeDrop"] and entropy_drop < cfg["relEntropyDrop"] \
                    and sharp_drop < cfg["relSharpnessDrop"]:
                votes["moved"] += cfg["movedVoteEdgeChange"]
                reasons.append(f"边缘结构相对基线改变 {edge_drop:.1%}，但画面既不模糊也未变平 ⇒ 视角/机位变化")

            # 全局配准（平移 + 旋转）：没标参照物时的补充证据。
            # 相位相关估不了旋转，而摄像机被拧一下主要是旋转 —— 这条能补上。
            affine = ((l0.get("landmarks") or {}).get("affine") or {})
            if affine.get("ok") and affine.get("inlierRatio", 0) >= cfg["affineInlierLocal"]:
                rot = abs(float(affine.get("rotationDeg") or 0.0))
                trans = float(affine.get("translation") or 0.0)
                if trans >= cfg["affineLocalTransPx"] or rot >= cfg["affineLocalRotDeg"]:
                    votes["moved"] += cfg["movedVoteAffineLocal"]
                    reasons.append(
                        f"与基线参考帧的 ORB 全局配准：{trans:.1f}px 平移 / {rot:.1f}° 旋转"
                        f"（内点率 {affine.get('inlierRatio')}）⇒ 摄像机视野已改变"
                    )

        if l1.get("beingRotated"):
            votes["moved"] += cfg["movedVoteBeingRotated"]

        best = max(votes.items(), key=lambda kv: kv[1])
        total = sum(votes.values())
        if total <= 0:
            return (V_CHANGE, cfg["voteEmptyRisk"], cfg["voteEmptyConfidence"],
                    ["画面显著偏离历史基线，但类型证据不足，只报『画面持续异常』"])
        kind, score = best
        confidence = min(cfg["voteConfidenceCap"],
                         cfg["voteConfidenceBase"] + cfg["voteConfidenceScale"] * score)
        risk = min(cfg["voteRiskCap"], cfg["voteRiskBase"] + cfg["voteRiskScale"] * score)
        mapping = {"covered": V_COVERED, "defocused": V_DEFOCUSED, "moved": V_MOVED}
        return (mapping[kind], risk, confidence, reasons)

    # ------------------------------------------------------------------ 基线标量
    @staticmethod
    def _baseline_scalars(state: CameraState, slot: int, l2: Dict[str, Any]) -> Dict[str, Any]:
        """用"同时段（不足则全部）历史样本"的标量中位数算相对下降比。"""
        samples = state.slot_samples(slot)
        source = "slot"
        if len(samples) < 1:
            samples = state.baseline_thumbs()
            source = "global"
        if not samples:
            return {"available": False, "note": "历史样本为空，无法计算相对下降比"}
        ent_base = _median([s.entropy for s in samples])
        sharp_base = _median([s.sharpness for s in samples])
        edge_base = _median([s.edge for s in samples])
        return {
            "scalarSource": source,
            "scalarSamples": len(samples),
            "baselineEntropy": round(ent_base, 3),
            "baselineSharpness": round(sharp_base, 2),
            "baselineEdge": round(edge_base, 5),
            "entropyDrop": round(_ratio_drop(l2["entropy"], ent_base), 4),
            "sharpnessDrop": round(_ratio_drop(l2["sharpness"], sharp_base), 4),
            "edgeDrop": round(_ratio_drop(l2["edgeDensity"], edge_base), 4),
        }

    # ------------------------------------------------------------------ 参照物
    def calibrate_landmarks(self, camera_id: str, landmarks: List[Dict[str, Any]],
                            frame: Optional[np.ndarray] = None,
                            clear: bool = False,
                            admit_frame: bool = True) -> Dict[str, Any]:
        """标定参照物：从给定画面（或基线中位数背景）截取模板并落盘。

        参照物应当选**不会自己移动的人造结构**：大门、楼角、围墙、灯杆基座。
        不要选树叶、旗帜、水面、广告牌（会被风吹动），也不要选车/人（会移动）。

        模板存的是**梯度幅值**而不是原始灰度：对昼夜光照变化、镜头积灰、
        轻微失焦都不敏感，而对"整体平移"极其敏感。

        ``admit_frame=True`` 时，标定用的这张画面**同时作为基线样本入库**——
        用户既然在这张画面上标定，通常就认为它是"正常画面"，白白浪费掉很可惜，
        尤其新机位冷启动时能少等一次抽样。

        但入库**必须先过质量闸**，否则用户在被遮挡/失焦时才想起来标定，
        就会把异常画面学成基线，之后永远不告警。
        """
        state = self.store.get(camera_id)
        if clear and not landmarks:
            state.clear_landmarks()
            self.store.save(state)
            return {"cameraId": state.camera_id, "landmarks": [], "count": 0}

        if frame is not None:
            gray = metrics.prepare_gray(frame)
            source = "frame"
        else:
            median_bg, _, _ = state.background(None)
            gray = median_bg.astype(np.uint8) if median_bg is not None else None
            source = "baseline"

        if gray is None:
            raise ValueError("该机位还没有基线，也没有提供画面，无法标定参照物")
        if not landmarks:
            raise ValueError("landmarks 不能为空")

        grad = metrics.gradient_magnitude(gray)
        clean: List[Dict[str, Any]] = []
        patches: Dict[int, np.ndarray] = {}
        for item in landmarks[:12]:  # 最多 12 个（参照物 + 忽略区合计）
            kind = "ignore" if str(item.get("kind")) == "ignore" else "landmark"
            box = {
                "x": float(item.get("x", 0)),
                "y": float(item.get("y", 0)),
                "w": float(item.get("w", 0)),
                "h": float(item.get("h", 0)),
            }
            if box["w"] <= 0.005 or box["h"] <= 0.005:
                continue
            idx = len(clean)
            if kind == "ignore":
                # 忽略区不需要模板：它只用于把这块像素从基线偏离统计里剔除
                clean.append({
                    "id": idx,
                    "kind": "ignore",
                    "label": str(item.get("label") or f"忽略区{idx + 1}")[:32],
                    **{k: round(v, 5) for k, v in box.items()},
                })
                continue
            patch = metrics.crop_norm(grad, box)
            if patch is None or patch.shape[0] < 12 or patch.shape[1] < 12:
                continue
            clean.append({
                "id": idx,
                "kind": "landmark",
                "label": str(item.get("label") or f"参照物{idx + 1}")[:32],
                **{k: round(v, 5) for k, v in box.items()},
            })
            patches[idx] = patch.astype(np.float32)

        if not clean:
            raise ValueError("所有区域都太小（分析尺度下不足 12×12 像素），请画大一点")
        if not patches:
            raise ValueError("至少要标 1 个参照物（忽略区不能单独成立）")
        state.set_landmarks(clean, patches)

        # ---------------------------------------------------------- 标定画面入库
        admit = {"requested": bool(admit_frame), "admitted": False, "reason": ""}
        if admit_frame and frame is not None:
            admit = self._admit_calibration_frame(state, gray)
        elif admit_frame and frame is None:
            admit = {"requested": True, "admitted": False,
                     "reason": "标定用的是基线中位数背景，没有真实画面可入库"}

        self.store.save(state)
        return {
            "cameraId": state.camera_id,
            "source": source,
            "count": len(clean),
            "frameAdmitted": bool(admit.get("admitted")),
            "frameAdmitReason": admit.get("reason", ""),
            "baselineStatus": state.baseline_status(),
            "baselineSamples": state.total_samples(),
            "landmarks": state.landmark_summary(),
            "frameSize": {"width": int(gray.shape[1]), "height": int(gray.shape[0])},
        }

    def _admit_calibration_frame(self, state: CameraState, gray: np.ndarray) -> Dict[str, Any]:
        """把标定用的画面作为基线样本入库，但先过质量闸。

        顺序是刻意的：**先质量、再一致性**。

        1. 质量闸（绝对阈值）：熵过低=疑似遮挡、清晰度过低=疑似失焦、亮度极端=曝光异常；
        2. 一致性闸（相对基线）：如果这个机位已有基线，新画面偏离过大就不入库——
           否则用户在被换过的画面上标定，会把"新画面"混进旧基线，导致两边都判不准。
           这种情况应当走「重设基准」，而不是悄悄入库。
        """
        cfg = self.params
        now_ms = int(time.time() * 1000)

        entropy = float(metrics.global_entropy(gray))
        sharpness = float(metrics.laplacian_var(gray))
        luma = float(metrics.luma_mean(gray))
        edge = float(metrics.edge_density(gray))

        def _skip(reason: str) -> Dict[str, Any]:
            return {"requested": True, "admitted": False, "reason": reason}

        if entropy < cfg["entropyFloor"]:
            return _skip(f"画面熵 {entropy:.2f} bit 低于 {cfg['entropyFloor']}，疑似被遮挡，未加入基线")
        if sharpness < cfg["sharpnessFloor"]:
            return _skip(f"清晰度 {sharpness:.1f} 低于 {cfg['sharpnessFloor']}，疑似失焦，未加入基线")
        if luma < 8.0 or luma > 247.0:
            return _skip(f"画面平均亮度 {luma:.0f} 属极端值，未加入基线")

        slot = slot_of(now_ms)
        thumb = cv2.resize(gray, THUMB_SIZE, interpolation=cv2.INTER_AREA)
        median_bg, mad_bg, _meta = state.background(slot)
        if median_bg is not None:
            prof = metrics.robust_z_profile(thumb, median_bg, mad_bg)
            d_hist = float(prof.get("dHist") or 0.0)
            if d_hist > cfg["dHistAlarm"]:
                return _skip(
                    f"与已有基线偏离 dHist={d_hist:.3f} > {cfg['dHistAlarm']}，未加入基线"
                    "（如果机位/视角确实变了，请先「重设基准」再标定）"
                )

        digest = metrics.frame_hash(gray)
        state.add_sample(SlotSample(
            ts_ms=now_ms, hash=digest, sharpness=sharpness, entropy=entropy,
            edge=edge, sigma=float(metrics.noise_sigma(gray)), luma=luma,
            ahash=metrics.a_hash(thumb), thumb=thumb.copy(),
        ))
        state.remember(digest, now_ms, metrics.a_hash(thumb))
        if state.ref_gray is None:
            state.ref_gray = gray.copy()
        return {
            "requested": True,
            "admitted": True,
            "reason": f"标定画面已作为基线样本加入时段槽 {slot}:00（当前共 {state.total_samples()} 个样本）",
        }

    def _match_landmarks(self, state: CameraState, gray: np.ndarray) -> Dict[str, Any]:
        """把每个参照物模板在**全幅**搜索（多尺度），再做一致性投票。

        同时算一次「当前帧 vs 基线参考帧」的 ORB 全局配准：参照物可能因为转动幅度过大
        而彻底失配，这时需要的是一条独立的、能回答"还是不是同一个视野"的判据。
        """
        cfg = self.params
        grad = metrics.gradient_magnitude(gray)
        matches: List[Dict[str, Any]] = []
        for idx, lm in state.anchor_landmarks():
            # 模板库：一个参照物可以有多个外观模板（主模板 + 夜间/雨天/季节补录）。
            # 逐个匹配，取最高分；分数足够高就早停，控制算力。
            tids = state.templates_for(idx) or (
                [""] if idx in state.landmark_patches else []
            )
            best: Optional[Dict[str, Any]] = None
            best_tid = ""
            for tid in tids:
                patch = (state.template_patches.get(tid) if tid
                         else state.landmark_patches.get(idx))
                if patch is None:
                    continue
                found = metrics.match_landmark(grad, patch, lm, cfg["lmSearchPadRatio"])
                # 整块匹配不理想时（典型场景：一辆车停在参照物中间、招牌挡住半边），
                # 用子块投票抢救：只要超过一半的子块还能对上、且偏移一致，
                # 参照物其实**还在原处**，不应该被判成"参照物丢失"。
                whole_ok = (found.get("ok")
                            and found.get("score", 0.0) >= cfg["lmScoreMin"]
                            and found.get("margin", 1.0) >= cfg["lmMarginMin"])
                if not whole_ok:
                    sub = metrics.match_landmark_subblocks(
                        grad, patch, lm,
                        hint_dx=float(found.get("dx") or 0.0),
                        hint_dy=float(found.get("dy") or 0.0),
                        sub_score_min=cfg["lmSubScoreMin"],
                    )
                    if sub.get("ok"):
                        # 子块共识内部已做一致性校验，这里给它通过峰值唯一性闸的标记
                        sub["margin"] = 1.0
                        sub["wholeScore"] = found.get("score", 0.0)
                        found = sub
                if best is None or float(found.get("score") or 0.0) > float(best.get("score") or 0.0):
                    best = found
                    best_tid = tid
                if float(found.get("score") or 0.0) >= cfg["lmTemplateEarlyStop"]:
                    break
            if best is None:
                continue
            found = best
            found.update({
                "id": idx,
                "label": lm.get("label", ""),
                "box": {k: lm.get(k) for k in ("x", "y", "w", "h")},
                "scoreMin": cfg["lmScoreMin"],
                "templateId": best_tid,
                "templateCount": len([t for t in tids if t]),
            })
            matches.append(found)
        consensus = metrics.landmark_consensus(
            matches, cfg["lmMovePx"], cfg["lmConsistencyPx"],
            margin_min=cfg["lmMarginMin"], required=min(2, max(len(matches), 1)),
            move_deg=cfg["lmMoveDeg"],
        )

        # 全局配准（平移 + 旋转 + 缩放）：参照物失配时的独立证据
        affine = metrics.orb_affine(state.ref_gray, gray) if state.ref_gray is not None else {"available": False}
        # 与参考帧的整体相关性：区分"同视野变软"与"根本不是这个画面"
        ncc = metrics.ncc_same_size(state.ref_gray, gray) if state.ref_gray is not None else 0.0
        return {
            "configured": bool(state.anchor_landmarks()),
            "ignoreRegions": len(state.ignore_regions()),
            "count": len(matches),
            "matches": matches,
            "consensus": consensus,
            "affine": affine,
            "refNcc": round(float(ncc), 4),
        }

    # ------------------------------------------------------------------ 模板库
    def add_landmark_template(self, camera_id: str, landmark_id: int, frame: np.ndarray,
                              label: str = "", source: str = "manual",
                              actor: str = "", verdict_at_time: str = "",
                              force: bool = False) -> Dict[str, Any]:
        """给某个参照物追加一套外观模板（夜间 / 雨天 / 换季后由客户补录）。

        **这是"人机共驾"的关键接口**，但有风险：如果客户在**被遮挡/被篡改**的画面上
        点了"加为模板"，等于把攻击学成正常。所以这里设了三道闸：

        1. 质量闸：纹理量 / 熵 / 清晰度 / 对比度，任何一项过低就拒绝（可 force 强推，
           但会被记进审计）；
        2. 去重闸：与已有模板几乎相同就不重复添加，避免占满 8 个配额；
        3. 审计：记录时间、操作人、来源（导入/误报反馈）、当时的判定结论。
        """
        cfg = self.params
        state = self.store.get(camera_id)
        anchors = dict(state.anchor_landmarks())
        lm = anchors.get(int(landmark_id))
        if lm is None:
            raise ValueError(f"参照物 {landmark_id} 不存在")
        if frame is None:
            raise ValueError("必须提供画面")

        gray = metrics.prepare_gray(frame)
        grad = metrics.gradient_magnitude(gray)
        patch = metrics.crop_norm(grad, lm)
        if patch is None or patch.shape[0] < 12 or patch.shape[1] < 12:
            raise ValueError("参照物区域太小（分析尺度不足 12×12），无法生成模板")
        crop_gray = metrics.crop_norm(gray, lm)
        quality = {
            "texture": round(float(patch.mean()), 2),
            "entropy": round(float(metrics.global_entropy(crop_gray)), 2),
            "sharpness": round(float(metrics.laplacian_var(crop_gray)), 1),
            "contrast": round(float(crop_gray.std()), 1),
        }
        reason = ""
        if quality["texture"] < cfg["lmTemplateMinTexture"]:
            reason = f"区域纹理太少（{quality['texture']} < {cfg['lmTemplateMinTexture']}）"
        elif quality["entropy"] < cfg["lmTemplateMinEntropy"]:
            reason = f"区域信息量过低（熵 {quality['entropy']}，疑似被遮挡）"
        elif quality["sharpness"] < cfg["lmTemplateMinSharpness"]:
            reason = f"区域过于模糊（{quality['sharpness']}，疑似失焦）"
        elif quality["contrast"] < cfg["lmTemplateMinContrast"]:
            reason = f"区域对比度过低（{quality['contrast']}，疑似被遮挡）"
        if reason and not force:
            state.push_audit("add_template_rejected", actor, {
                "landmarkId": int(landmark_id), "reason": reason,
                "quality": quality, "verdictAtTime": verdict_at_time,
            })
            self.store.save(state)
            raise ValueError(f"模板质量不合格：{reason}。如果确认这是正常画面，可强制添加")

        # 去重：与已有模板几乎相同就不再添加
        for tid in state.templates_for(int(landmark_id)):
            old = state.template_patches.get(tid)
            if old is None or old.shape != patch.shape:
                continue
            try:
                sim = float(cv2.matchTemplate(old.astype(np.float32),
                                              patch.astype(np.float32),
                                              cv2.TM_CCOEFF_NORMED)[0][0])
            except cv2.error:
                continue
            if sim >= 0.98:
                return {"cameraId": state.camera_id, "added": False, "duplicate": True,
                        "templateId": tid, "similarity": round(sim, 4),
                        "note": "与已有模板几乎相同，未重复添加"}

        tid = state.add_template(int(landmark_id), patch, label or "附加模板", source,
                                 quality=quality, verdict_at_time=verdict_at_time)
        state.push_audit("add_template", actor, {
            "landmarkId": int(landmark_id), "templateId": tid, "label": label,
            "source": source, "quality": quality, "forced": bool(force and reason),
            "rejectReason": reason, "verdictAtTime": verdict_at_time,
        })
        self.store.save(state)
        return {
            "cameraId": state.camera_id,
            "added": True,
            "templateId": tid,
            "landmarkId": int(landmark_id),
            "quality": quality,
            "forced": bool(force and reason),
            "templateCount": len(state.templates_for(int(landmark_id))),
        }

    def list_landmark_templates(self, camera_id: str) -> Dict[str, Any]:
        state = self.store.get(camera_id)
        return {
            "cameraId": state.camera_id,
            "templates": state.template_summary(),
            "audit": state.audit[-50:],
            "limits": {"maxPerLandmark": 8},
        }

    def remove_landmark_template(self, camera_id: str, template_id: str,
                                 actor: str = "") -> Dict[str, Any]:
        state = self.store.get(camera_id)
        if not state.remove_template(template_id):
            raise ValueError("模板不存在，或主模板不允许删除")
        state.push_audit("remove_template", actor, {"templateId": template_id})
        self.store.save(state)
        return {"cameraId": state.camera_id, "removed": True, "templateId": template_id}

    # ------------------------------------------------------------------ 输出
    def _render_result(self, camera_id: str, ts: int, verdict: str, risk: float, confidence: float,
                       layers: Dict[str, Any], matrix: Dict[str, Any], reasons: List[str],
                       timings: Dict[str, float], baseline: Dict[str, Any], frames: int,
                       extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        info = SAMPLE_VERDICTS.get(verdict, SAMPLE_VERDICTS[V_NORMAL])
        alarm = bool(info["alarm"])
        # "场景内物体变动"默认不告警，但允许用参数打开
        if verdict == V_OBJECT and self.params.get("objectChangeAlarm"):
            alarm = True
        payload: Dict[str, Any] = {
            "cameraId": camera_id,
            "tsMs": ts,
            "slot": slot_of(ts),
            "frames": frames,
            "verdict": verdict,
            "verdictLabel": info["label"],
            "level": info["level"],
            "alarm": alarm,
            "tamperType": info["tamperType"],
            "risk": round(float(risk), 4),
            "confidence": round(float(confidence), 4),
            "layers": layers,
            "matrix": matrix,
            "reasons": reasons,
            "baseline": baseline,
            "timingsMs": timings,
        }
        if extra:
            payload.update(extra)
        return payload
