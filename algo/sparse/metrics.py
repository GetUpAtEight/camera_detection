"""底层视觉指标（L2 单帧 / L1 微时序 / L0 跨采样点）。

设计约束（来自 ``docs/research/02-sparse-adaptation.md``）：

* 所有指标都在**统一分析尺寸**上计算（长边 ``ANALYZE_LONG_SIDE``），
  这样不同分辨率、不同 JPEG 质量的画面之间才可比；
* 指标必须**确定性**且**便宜**（单帧 CPU 毫秒级），否则 1 小时一次也扛不住车队规模；
* 任何指标都要能解释，``detail`` 里给原始值而不是只给 0~1 的分数。

术语：

* ``L2`` 单帧指标 —— 清晰度、熵、边缘密度、噪声 σ；
* ``L1`` 微时序指标 —— burst 内 9 个指标（bitExactDup / intraMad / ...）；
* ``L0`` 跨采样指标 —— 与历史基线（按槽中位数背景）的逐像素鲁棒 z。
"""

from __future__ import annotations

import hashlib
from typing import Any, Dict, List, Optional, Sequence, Tuple

import cv2
import numpy as np

# 统一分析尺寸：长边缩到 640，短边按比例。1 小时一次的场景下这点算力可以忽略。
ANALYZE_LONG_SIDE = 640

# 噪声残差估计用的窗口。3×3 中值滤波能保留边缘、滤掉独立噪声，
# 残差的标准差就是"传感器噪声水平"的稳健估计。
_NOISE_KERNEL = 3

# ORB 平移估计：特征点少一点，够估几像素的平移就行，不要让它抢 CPU。
_ORB = cv2.ORB_create(nfeatures=400, fastThreshold=12)


# --------------------------------------------------------------------------- 预处理
def to_gray(frame: np.ndarray) -> np.ndarray:
    if frame.ndim == 2:
        return frame
    return cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)


def prepare_gray(frame: np.ndarray, long_side: int = ANALYZE_LONG_SIDE) -> np.ndarray:
    """转灰度 + 等比缩放到统一分析尺寸。"""
    gray = to_gray(frame)
    h, w = gray.shape[:2]
    if max(h, w) <= long_side or max(h, w) == 0:
        return gray
    scale = long_side / float(max(h, w))
    return cv2.resize(gray, (max(int(round(w * scale)), 1), max(int(round(h * scale)), 1)),
                      interpolation=cv2.INTER_AREA)


def prepare_bgr(frame: np.ndarray, long_side: int = ANALYZE_LONG_SIDE) -> np.ndarray:
    bgr = frame if frame.ndim == 3 else cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)
    h, w = bgr.shape[:2]
    if max(h, w) <= long_side or max(h, w) == 0:
        return bgr
    scale = long_side / float(max(h, w))
    return cv2.resize(bgr, (max(int(round(w * scale)), 1), max(int(round(h * scale)), 1)),
                      interpolation=cv2.INTER_AREA)


# --------------------------------------------------------------------------- L2 指标
def laplacian_var(gray: np.ndarray) -> float:
    """清晰度：Laplacian 二阶响应的方差。失焦时显著下降。"""
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


def global_entropy(gray: np.ndarray) -> float:
    """全局灰度熵（bit）。大面积遮挡时趋于单一，熵下降。"""
    hist = cv2.calcHist([gray], [0], None, [256], [0, 256]).ravel()
    total = float(hist.sum())
    if total <= 0:
        return 0.0
    p = hist / total
    p = p[p > 0]
    return float(-(p * np.log2(p)).sum())


def block_entropy_stats(gray: np.ndarray, grid: int = 4, block_threshold: float = 2.0
                        ) -> Tuple[float, float, List[float]]:
    """分块熵：返回 (最低块熵, 低熵块占比, 全部块熵)。

    用"低熵块占比"而不是"任一块低熵"——白墙/天空本身就有低熵块，
    按单块判定会持续误报（见 ``docs/ALGORITHMS.md`` §2）。
    """
    h, w = gray.shape[:2]
    if h < grid or w < grid:
        e = global_entropy(gray)
        return e, (1.0 if e < block_threshold else 0.0), [e]
    bh, bw = h // grid, w // grid
    entropies: List[float] = []
    for gy in range(grid):
        for gx in range(grid):
            block = gray[gy * bh:(gy + 1) * bh, gx * bw:(gx + 1) * bw]
            entropies.append(global_entropy(block))
    low = sum(1 for e in entropies if e < block_threshold)
    return float(min(entropies)), float(low / float(len(entropies) or 1)), entropies


def edge_density(gray: np.ndarray) -> float:
    """边缘像素占比（Canny）。遮挡/失焦都会让边缘消失。"""
    edges = cv2.Canny(gray, 60, 160)
    return float((edges > 0).mean())


def noise_sigma(gray: np.ndarray, kernel: int = _NOISE_KERNEL) -> float:
    """传感器噪声水平：中值滤波残差的稳健标准差（MAD × 1.4826）。

    这是冻结/重放判据的物理基础：真实传感器每帧都有独立噪声，
    而被冻结/重放的画面噪声是"复制"的。
    """
    residual = gray.astype(np.float32) - cv2.medianBlur(gray, kernel).astype(np.float32)
    mad = float(np.median(np.abs(residual)))
    return 1.4826 * mad


def luma_mean(gray: np.ndarray) -> float:
    return float(gray.mean())


def chroma_mean(bgr: np.ndarray) -> float:
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    return float(hsv[:, :, 1].mean())


def frame_hash(frame: np.ndarray) -> str:
    """逐位哈希（解码后像素）。同一份文件重复解码得到的哈希一致。"""
    arr = np.ascontiguousarray(frame)
    return hashlib.sha1(arr.tobytes()).hexdigest()


def a_hash(gray: np.ndarray, size: int = 8) -> int:
    """均值哈希（aHash）：跨采样点近重复检索用，容忍 JPEG 重编码。"""
    small = cv2.resize(gray, (size, size), interpolation=cv2.INTER_AREA)
    return int(np.packbits(small > small.mean()).tobytes().hex(), 16)


def hamming(a: int, b: int) -> int:
    return int(bin(a ^ b).count("1"))


# --------------------------------------------------------------------------- 参照物标定
def gradient_magnitude(gray: np.ndarray) -> np.ndarray:
    """Sobel 梯度幅值。

    参照物匹配**必须用梯度而不是原始灰度**：
    * 对光照/昼夜变化不敏感（灰度整体升降不改变梯度结构）；
    * 对镜头积灰、轻微失焦的容忍度更好；
    * 建筑物/门/围墙这类人造结构边缘强，梯度图上特征最稳。
    """
    gx = cv2.Sobel(gray.astype(np.float32), cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(gray.astype(np.float32), cv2.CV_32F, 0, 1, ksize=3)
    return cv2.magnitude(gx, gy)


def crop_norm(frame: np.ndarray, box: Dict[str, float]) -> Optional[np.ndarray]:
    """按归一化坐标 (x, y, w, h ∈ [0,1]) 裁剪。"""
    if frame is None or frame.size == 0:
        return None
    h, w = frame.shape[:2]
    x0 = int(round(float(box.get("x", 0)) * w))
    y0 = int(round(float(box.get("y", 0)) * h))
    bw = int(round(float(box.get("w", 0)) * w))
    bh = int(round(float(box.get("h", 0)) * h))
    x0 = max(0, min(x0, w - 1))
    y0 = max(0, min(y0, h - 1))
    bw = max(1, min(bw, w - x0))
    bh = max(1, min(bh, h - y0))
    return frame[y0:y0 + bh, x0:x0 + bw].copy()


def match_landmark(frame_grad: np.ndarray, patch: np.ndarray, box: Dict[str, float],
                   pad_ratio: float = 0.05, coarse_div: int = 4,
                   refine_px: int = 8) -> Dict[str, Any]:
    """多尺度匹配参照物模板：先在 1/coarse_div 分辨率上**全幅粗搜**，再全分辨率精修。

    为什么不能只在小窗口里搜：
    1 小时采样下，镜头被转动几乎必然发生在两次抽样之间。真的转了 10°~20° 时，
    建筑会直接移出 ±5% 画幅的小搜索窗 —— 只在小窗里搜的结果是"匹配失败"，
    于是系统只能说"本次不判移位"，把最关键的一类攻击漏掉。

    粗搜全幅（1/4 尺度）能把任意平移都找回来，代价只有几十毫秒；
    精修再用全分辨率在候选点附近 ±refine_px 内细化，保证亚像素精度。
    """
    if frame_grad is None or patch is None or patch.size == 0:
        return {"ok": False, "score": 0.0, "dx": 0.0, "dy": 0.0}
    fh, fw = frame_grad.shape[:2]
    ph, pw = patch.shape[:2]
    if ph < 12 or pw < 12:
        return {"ok": False, "score": 0.0, "dx": 0.0, "dy": 0.0, "note": "参照物过小"}

    x0 = int(round(float(box.get("x", 0)) * fw))
    y0 = int(round(float(box.get("y", 0)) * fh))
    pad = max(8, int(round(pad_ratio * max(fw, fh))))

    def _local_search(cx: int, cy: int) -> Optional[Tuple[float, int, int, float]]:
        """在 (cx, cy) 附近 ±refine_px 内做全分辨率精修。

        返回 (score, x, y, margin)，其中 margin = 最高峰 − 次高峰。
        全幅搜索会带来假匹配：重复纹理（栏杆、地砖、窗格）或"换了个场景"时，
        模板在别处也能凑出很高的相似度。**峰值唯一性**就是区分真匹配与假匹配的判据：
        真参照物的匹配应该是一个尖峰，假匹配往往有一堆差不多高的次峰。
        """
        sx0 = max(0, cx - refine_px)
        sy0 = max(0, cy - refine_px)
        sx1 = min(fw, cx + pw + refine_px)
        sy1 = min(fh, cy + ph + refine_px)
        window = frame_grad[sy0:sy1, sx0:sx1]
        if window.shape[0] < ph or window.shape[1] < pw:
            return None
        try:
            res = cv2.matchTemplate(window, patch, cv2.TM_CCOEFF_NORMED)
        except cv2.error:
            return None
        if res.size == 0:
            return None
        _, max_val, _, max_loc = cv2.minMaxLoc(res)
        margin = float(max_val) - _second_peak(res, max_loc, ph, pw)
        return float(max_val), sx0 + max_loc[0], sy0 + max_loc[1], margin

    # ---------- 第一步：粗搜（全幅，低分辨率）----------
    coarse_hit: Optional[Tuple[float, int, int, float]] = None
    div = max(2, int(coarse_div))
    if fw // div >= 16 and fh // div >= 16:
        small_frame = cv2.resize(frame_grad, (fw // div, fh // div), interpolation=cv2.INTER_AREA)
        spw, sph = max(pw // div, 4), max(ph // div, 4)
        if spw < small_frame.shape[1] and sph < small_frame.shape[0]:
            small_patch = cv2.resize(patch, (spw, sph), interpolation=cv2.INTER_AREA)
            try:
                res = cv2.matchTemplate(small_frame, small_patch, cv2.TM_CCOEFF_NORMED)
                _, _, _, max_loc = cv2.minMaxLoc(res)
                coarse_hit = _local_search(max_loc[0] * div, max_loc[1] * div)
            except cv2.error:
                coarse_hit = None

    # ---------- 第二步：期望位置附近精修 ----------
    local_hit = _local_search(x0, y0)

    # ---------- 择优（分数高者优先；同分时优先唯一性更好、位移更小的）----------
    candidates = [c for c in (coarse_hit, local_hit) if c is not None]
    if not candidates:
        return {"ok": False, "score": 0.0, "dx": 0.0, "dy": 0.0, "note": "搜索窗口越界"}
    max_val, bx, by, margin = max(
        candidates, key=lambda c: (round(c[0], 3), c[3], -abs(c[1] - x0) - abs(c[2] - y0))
    )
    dx = float(bx - x0)
    dy = float(by - y0)

    # 关键防呆：TM_CCOEFF_NORMED 在**搜索窗口几乎无纹理**时会给出虚高的分数
    # （分母趋 0，OpenCV 直接返回接近 1 的相关性）。于是"参照物被整块盖住"
    # 反而会被判成"匹配得非常好"。必须检查匹配位置是否真的有梯度能量。
    mx0 = int(np.clip(bx, 0, max(fw - pw, 0)))
    my0 = int(np.clip(by, 0, max(fh - ph, 0)))
    matched = frame_grad[my0:my0 + ph, mx0:mx0 + pw]
    ref_energy = float(patch.mean()) or 1e-6
    energy_ratio = float(matched.mean() / ref_energy) if matched.size else 0.0
    if energy_ratio < 0.30:
        return {
            "ok": False,
            "score": round(float(max_val), 4),
            "dx": round(dx, 2),
            "dy": round(dy, 2),
            "centerX": round(x0 + pw / 2.0, 2),
            "centerY": round(y0 + ph / 2.0, 2),
            "magnitude": round(float(np.hypot(dx, dy)), 2),
            "energyRatio": round(energy_ratio, 3),
            "flatMatch": True,
            "note": "匹配位置几乎没有纹理（疑似被遮挡），分数不可信",
        }

    return {
        "ok": True,
        "score": round(float(max_val), 4),
        "margin": round(float(margin), 4),
        "dx": round(dx, 2),
        "dy": round(dy, 2),
        "centerX": round(x0 + pw / 2.0, 2),
        "centerY": round(y0 + ph / 2.0, 2),
        "magnitude": round(float(np.hypot(dx, dy)), 2),
        "energyRatio": round(energy_ratio, 3),
        "searchedPadPx": pad,
        "coarseSearched": coarse_hit is not None,
    }


def match_landmark_subblocks(frame_grad: np.ndarray, patch: np.ndarray,
                             box: Dict[str, float], hint_dx: float = 0.0,
                             hint_dy: float = 0.0, grid: int = 3,
                             sub_score_min: float = 0.5, pad: int = 10
                             ) -> Dict[str, Any]:
    """整块模板匹配失败后，用**子块投票**抢救：把一个参照物切成 3×3 小块，各块独立匹配。

    解决什么问题：
    一辆车停在参照物中间、一块招牌挡住半边 —— 整块 NCC 会掉到阈值以下，
    被判成"参照物丢失"。但只要有超过一半的子块还能对上，参照物其实**还在原处**。

    做法：
      1. 用整块匹配给出的位置作为锚点（``hint_dx/dy``），在每个子块的预期位置附近 ±pad 搜索；
      2. 只统计分数 ≥ ``sub_score_min`` 的子块；
      3. 子块数量 ≥ ``min_blocks``（默认 4/9）且偏移一致 ⇒ 认定参照物仍存在；
      4. 返回 ``blockRatio``（对上几块）与 ``partial``（是否部分遮挡）。

    这样既提高了抗遮挡能力，又能把"部分遮挡"作为一个可解释的信号报给上层。
    """
    if frame_grad is None or patch is None or patch.size == 0:
        return {"ok": False, "reason": "无模板"}
    fh, fw = frame_grad.shape[:2]
    ph, pw = patch.shape[:2]
    g = max(2, int(grid))
    sh, sw = ph // g, pw // g
    # 子块至少要 10×10 才有统计意义，否则退回"整块失败"的结论
    if sh < 10 or sw < 10:
        return {"ok": False, "reason": f"子块过小（{sw}×{sh}）"}

    x0 = int(round(float(box.get("x", 0)) * fw))
    y0 = int(round(float(box.get("y", 0)) * fh))

    blocks: List[Dict[str, Any]] = []
    for j in range(g):
        for i in range(g):
            sub = patch[j * sh:(j + 1) * sh, i * sw:(i + 1) * sw]
            if sub.size == 0:
                continue
            # 该子块在画面中的预期位置（叠加整块匹配给出的锚点位移）
            bx = x0 + i * sw + int(round(hint_dx))
            by = y0 + j * sh + int(round(hint_dy))
            sx0 = max(0, bx - pad)
            sy0 = max(0, by - pad)
            sx1 = min(fw, bx + sw + pad)
            sy1 = min(fh, by + sh + pad)
            window = frame_grad[sy0:sy1, sx0:sx1]
            if window.shape[0] < sh or window.shape[1] < sw:
                continue
            try:
                res = cv2.matchTemplate(window, sub, cv2.TM_CCOEFF_NORMED)
                _, max_val, _, max_loc = cv2.minMaxLoc(res)
            except cv2.error:
                continue
            if float(max_val) < sub_score_min:
                continue
            dx = float(sx0 + max_loc[0] - (x0 + i * sw))
            dy = float(sy0 + max_loc[1] - (y0 + j * sh))
            blocks.append({"dx": dx, "dy": dy, "score": float(max_val), "i": i, "j": j})

    min_blocks = max(2, (g * g) // 2)  # 默认 3×3 ⇒ 至少 4 块
    if len(blocks) < min_blocks:
        return {"ok": False, "blocks": len(blocks), "required": min_blocks,
                "reason": f"可信子块不足（{len(blocks)}/{min_blocks}）"}

    dxs = np.array([b["dx"] for b in blocks], dtype=np.float32)
    dys = np.array([b["dy"] for b in blocks], dtype=np.float32)
    med_dx = float(np.median(dxs))
    med_dy = float(np.median(dys))
    spread = float(max(np.max(np.abs(dxs - med_dx)), np.max(np.abs(dys - med_dy))))
    mean_score = float(np.mean([b["score"] for b in blocks]))
    total = g * g
    return {
        "ok": True,
        "score": round(mean_score, 4),
        "dx": round(med_dx, 2),
        "dy": round(med_dy, 2),
        "centerX": round(x0 + pw / 2.0, 2),
        "centerY": round(y0 + ph / 2.0, 2),
        "magnitude": round(float(np.hypot(med_dx, med_dy)), 2),
        "blocks": len(blocks),
        "blockTotal": total,
        "blockRatio": round(len(blocks) / float(total), 3),
        "spread": round(spread, 2),
        "partial": len(blocks) < total,
        "fromSubBlocks": True,
    }


def _second_peak(res: np.ndarray, max_loc: Tuple[int, int], ph: int, pw: int) -> float:
    """压制最高峰邻域后求次高峰（用于峰值唯一性判据）。"""
    mask = res.copy()
    h, w = mask.shape[:2]
    r = max(2, min(ph, pw) // 2)
    y0 = max(0, max_loc[1] - r)
    y1 = min(h, max_loc[1] + r + 1)
    x0 = max(0, max_loc[0] - r)
    x1 = min(w, max_loc[0] + r + 1)
    if (y1 - y0) * (x1 - x0) >= mask.size:
        return -1.0
    mask[y0:y1, x0:x1] = -2.0
    return float(mask.max())


def landmark_consensus(matches: Sequence[Dict[str, Any]], move_px: float,
                       consistency_px: float, margin_min: float = 0.05,
                       required: Optional[int] = None,
                       move_deg: float = 0.8) -> Dict[str, Any]:
    """用多个参照物的对应点**拟合一个相似变换**（平移 + 旋转 + 缩放），据此判断机位是否变了。

    为什么不是简单的"位移中位数"：
    摄像机被拧一下主要是**旋转**，而绕光心旋转时，画面不同位置的参照物会朝**不同方向**
    移动（圆周运动）——"位移一致"这个模型对旋转根本不成立，会把真实的旋转判成"不一致"。

    所以这里直接拟合相似变换：只要各参照物的对应关系能被**同一个**旋转+平移解释，
    就说明整个画面是刚体运动，也就是机位真的动了；拟合残差大则说明参照物互相矛盾
    （多半是假匹配或局部物体变化），此时退回"参照系失效"处理。

    可信条件（缺一不可）：

    1. ``score >= scoreMin``：相似度够高；
    2. ``margin >= margin_min``：峰值唯一（不是重复纹理骗出来的假匹配）；
    3. 拟合残差 ``<= consistency_px``。

    可信参照物不足 ``required`` 个 → ``lost=True``。这**不等于"没移位"**，
    而是"参照系已失效"，需要上层用遮挡/失焦证据与全局配准来区分"被挡住"和"视野被换掉"。
    """
    trusted = [
        m for m in matches
        if m.get("ok")
        and m.get("score", 0) >= m.get("scoreMin", 0)
        and m.get("margin", 1.0) >= margin_min
    ]
    need = required if required is not None else min(2, max(len(matches), 1))
    if not trusted:
        return {"available": False, "trusted": 0, "lost": True, "moved": False,
                "required": need, "rotationDeg": 0.0, "translation": 0.0,
                "note": "没有可信参照物（相似度或峰值唯一性不足）"}
    if len(trusted) < need:
        return {"available": True, "trusted": len(trusted), "required": need, "lost": True,
                "moved": False, "rotationDeg": 0.0, "translation": 0.0,
                "count": len(trusted),
                "magnitude": round(float(np.median([m["magnitude"] for m in trusted])), 2),
                "note": f"可信参照物不足（{len(trusted)}/{need}）"}

    src = np.float32([[m["centerX"], m["centerY"]] for m in trusted])
    dst = np.float32([[m["centerX"] + m["dx"], m["centerY"] + m["dy"]] for m in trusted])
    method = 0 if len(trusted) == 2 else cv2.RANSAC
    try:
        matrix, _inliers = cv2.estimateAffinePartial2D(
            src, dst, method=method, ransacReprojThreshold=3.0)
    except cv2.error:
        matrix = None
    if matrix is None:
        return {"available": True, "trusted": len(trusted), "required": need, "lost": True,
                "moved": False, "rotationDeg": 0.0, "translation": 0.0, "count": len(trusted),
                "note": "参照物对应关系互相矛盾（拟合失败）"}

    dx, dy = float(matrix[0, 2]), float(matrix[1, 2])
    translation = float(np.hypot(dx, dy))
    a_coef, b_coef = float(matrix[0, 0]), float(matrix[1, 0])
    rotation = float(np.degrees(np.arctan2(b_coef, a_coef)))
    scale = float(np.hypot(a_coef, b_coef))

    # 拟合残差：各参照物实际位移与模型预测位移的偏差
    predicted = (matrix[:, :2] @ src.T).T + matrix[:, 2][None, :]
    residual = float(np.median(np.linalg.norm(predicted - dst, axis=1)))
    consistent = residual <= consistency_px
    moved = bool(consistent and (translation >= move_px or abs(rotation) >= move_deg))

    return {
        "available": True,
        "trusted": len(trusted),
        "required": need,
        "lost": False,
        "count": len(trusted),
        "dx": round(dx, 2),
        "dy": round(dy, 2),
        "translation": round(translation, 2),
        "magnitude": round(translation, 2),
        "rotationDeg": round(rotation, 2),
        "scale": round(scale, 4),
        "residual": round(residual, 2),
        "spread": round(residual, 2),
        "consistent": bool(consistent),
        "moved": moved,
    }


def phase_shift(ref_gray: np.ndarray, cur_gray: np.ndarray) -> Dict[str, Any]:
    """用相位相关估计"当前帧相对基线背景"的全局位移。

    为什么需要它：已发生的**移位**（镜头被转动后停住）在 burst 内是常量，
    ORB 在 burst 内部看不到任何位移；而平移后边缘密度、熵、清晰度几乎不变，
    所以"边缘消失/熵降/清晰度降"这三类判据都抓不到它。
    真正有区分力的是：当前帧与历史基线之间存在一个**一致的全局平移**。

    相位相关的好处：对纯平移敏感、对亮度线性变化不敏感、直接给亚像素位移。
    """
    if ref_gray is None or cur_gray is None:
        return {"available": False}
    a = ref_gray.astype(np.float32)
    b = cur_gray.astype(np.float32)
    if a.shape[:2] != b.shape[:2]:
        b = cv2.resize(b, (a.shape[1], a.shape[0]))
    h, w = a.shape[:2]
    if h < 16 or w < 16:
        return {"available": False}
    win = cv2.createHanningWindow((w, h), cv2.CV_32F)
    try:
        (dx, dy), response = cv2.phaseCorrelate(a, b, win)
    except cv2.error:
        return {"available": False}
    return {
        "available": True,
        "dx": round(float(dx), 3),
        "dy": round(float(dy), 3),
        "magnitude": round(float(np.hypot(dx, dy)), 3),
        "response": round(float(response), 4),
    }


# --------------------------------------------------------------------------- L1 微时序
def orb_translation(gray_a: np.ndarray, gray_b: np.ndarray) -> Dict[str, Any]:
    """估计两帧之间的平移量（像素）与"整体一致平移"的置信度。

    用于区分两种"高动态"：
    * 场景里有人车走动 → 特征点运动方向杂乱，inlier 比例低；
    * 摄像头正在被转动 → 几乎全部特征点朝同一方向平移，inlier 比例高。
    """
    kp_a, des_a = _ORB.detectAndCompute(gray_a, None)
    kp_b, des_b = _ORB.detectAndCompute(gray_b, None)
    if des_a is None or des_b is None or len(kp_a) < 8 or len(kp_b) < 8:
        return {"dx": 0.0, "dy": 0.0, "magnitude": 0.0, "inlierRatio": 0.0, "matches": 0, "ok": False}
    matcher = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=False)
    knn = matcher.knnMatch(des_a, des_b, k=2)
    good = [m for m, n in knn if m.distance < 0.75 * n.distance]
    if len(good) < 4:
        return {"dx": 0.0, "dy": 0.0, "magnitude": 0.0, "inlierRatio": 0.0,
                "matches": len(good), "ok": False}
    src = np.float32([kp_a[m.queryIdx].pt for m in good]).reshape(-1, 1, 2)
    dst = np.float32([kp_b[m.trainIdx].pt for m in good]).reshape(-1, 1, 2)
    matrix, inliers = cv2.estimateAffinePartial2D(src, dst, method=cv2.RANSAC,
                                                  ransacReprojThreshold=3.0)
    if matrix is None or inliers is None:
        return {"dx": 0.0, "dy": 0.0, "magnitude": 0.0,
                "inlierRatio": 0.0, "matches": len(good), "ok": False}
    dx, dy = float(matrix[0, 2]), float(matrix[1, 2])
    inlier_ratio = float(inliers.sum()) / float(len(good))
    return {
        "dx": round(dx, 3),
        "dy": round(dy, 3),
        "magnitude": round(float(np.hypot(dx, dy)), 3),
        "inlierRatio": round(inlier_ratio, 4),
        "matches": len(good),
        "ok": True,
    }


def ncc_same_size(ref_gray: np.ndarray, cur_gray: np.ndarray) -> float:
    """两张同尺寸灰度图的归一化互相关（NCC），返回 -1~1。

    用途：区分"同一个视野只是变软了"（失焦）和"根本不是这个画面了"（视野被换掉）。

    * 失焦：结构还在，只是高频被削弱 ⇒ NCC 仍然很高（0.8 以上）；
    * 换视野：结构完全不同 ⇒ NCC 很低（0.4 以下）；
    * 遮挡：NCC 也低，但熵会大幅下降，由遮挡判据处理。
    """
    if ref_gray is None or cur_gray is None or ref_gray.size == 0 or cur_gray.size == 0:
        return 0.0
    a = prepare_gray(ref_gray).astype(np.float32)
    b = prepare_gray(cur_gray).astype(np.float32)
    if a.shape[:2] != b.shape[:2]:
        b = cv2.resize(b, (a.shape[1], a.shape[0]))
    a = a - a.mean()
    b = b - b.mean()
    denom = float(np.sqrt((a * a).sum() * (b * b).sum()))
    if denom <= 1e-6:
        return 0.0
    return float((a * b).sum() / denom)


def orb_affine(ref_gray: np.ndarray, cur_gray: np.ndarray) -> Dict[str, Any]:
    """ORB + RANSAC 估计「当前帧 → 参考帧」的相似变换（平移 + 旋转 + 缩放）。

    与 :func:`orb_translation` 的区别：那个是 burst **内部**相邻帧的比较（判断"正在被转动"），
    这个是当前帧与**基线参考帧**的比较（判断"已经被转走了"）。

    为什么需要它：
    * 相位相关只能估平移，估不了旋转 —— 而摄像机被拧一下，主要是**旋转**；
    * 参照物在搜索范围外失配时，需要一路独立的全局配准来回答"画面还是不是同一个视野"。
    """
    if ref_gray is None or cur_gray is None:
        return {"available": False}
    a = prepare_gray(ref_gray)
    b = prepare_gray(cur_gray)
    if a.shape[:2] != b.shape[:2]:
        b = cv2.resize(b, (a.shape[1], a.shape[0]))
    kp_a, des_a = _ORB.detectAndCompute(a, None)
    kp_b, des_b = _ORB.detectAndCompute(b, None)
    if des_a is None or des_b is None or len(kp_a) < 8 or len(kp_b) < 8:
        return {"available": False, "reason": "特征点不足"}
    matcher = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=False)
    try:
        knn = matcher.knnMatch(des_b, des_a, k=2)
    except cv2.error:
        return {"available": False}
    good = [m for m, n in knn if m.distance < 0.75 * n.distance]
    if len(good) < 6:
        return {"available": True, "ok": False, "matches": len(good),
                "reason": "匹配点不足", "inlierRatio": 0.0}
    src = np.float32([kp_b[m.queryIdx].pt for m in good]).reshape(-1, 1, 2)
    dst = np.float32([kp_a[m.trainIdx].pt for m in good]).reshape(-1, 1, 2)
    matrix, inliers = cv2.estimateAffinePartial2D(src, dst, method=cv2.RANSAC,
                                                  ransacReprojThreshold=3.0)
    if matrix is None or inliers is None:
        return {"available": True, "ok": False, "matches": len(good),
                "inlierRatio": 0.0, "reason": "单应求解失败"}
    inlier_ratio = float(inliers.sum()) / float(len(good))
    dx, dy = float(matrix[0, 2]), float(matrix[1, 2])
    # 相似变换矩阵 [[a, -b, tx], [b, a, ty]] ⇒ 旋转角 atan2(b, a)，尺度 hypot(a, b)
    a_coef, b_coef = float(matrix[0, 0]), float(matrix[1, 0])
    rotation_deg = float(np.degrees(np.arctan2(b_coef, a_coef)))
    scale = float(np.hypot(a_coef, b_coef))
    return {
        "available": True,
        "ok": True,
        "dx": round(dx, 2),
        "dy": round(dy, 2),
        "translation": round(float(np.hypot(dx, dy)), 2),
        "rotationDeg": round(rotation_deg, 2),
        "scale": round(scale, 4),
        "inlierRatio": round(inlier_ratio, 4),
        "matches": len(good),
    }


def analyze_burst(frames: Sequence[np.ndarray], params: Optional[Dict[str, Any]] = None
                  ) -> Dict[str, Any]:
    """L1 微时序：单次抽样内部 K 帧的 9 个指标 + 运动×噪声象限。

    9 个指标（与 ``docs/research/03-novel-ideas.md`` §2 对齐）：

    ``bitExactDup``            burst 内逐位完全相同的帧对占比
    ``intraMad``               burst 内相邻帧平均绝对差（**去噪后**，归一化 0~1）
    ``intraEdgeRate``          burst 内边缘图变化率（去噪后）
    ``intraOrbTranslation``    burst 内 ORB 平移量中位数（像素）
    ``intraSharpnessVar``      burst 内清晰度变异系数
    ``sigmaPerFrame``          每帧**空间**噪声残差 σ（中位数，含场景纹理，仅作参考）
    ``sigmaSpread``            帧间**时间**噪声的离散度
    ``lumaDrift``              burst 内亮度漂移（0~1）
    ``chromaDrift``            burst 内色度漂移（0~1）

    两个容易踩的坑，这里都做了处理：

    1. **运动必须用去噪后的帧差**。传感器噪声本身就有 σ≈3 灰度级，直接帧差算出的
       ``intraMad``≈0.013，会被误判成"画面在动"——于是任何静态场景都进不了 Q2。
       这里先做 5×5 中值滤波，把独立噪声抹掉，剩下的才是真实结构变化。
    2. **噪声必须用帧间时间残差，不能用空间残差**。空间残差（中值滤波残差）里
       混着场景本身的纹理；而"同帧重复"与"真实摄像头"的本质区别在时间维度：
       真实传感器每帧独立噪声 ⇒ 相邻帧差有非零方差；冻结重放 ⇒ 帧差恒等于 0。
       这就是 ``temporalSigma``。
    """
    cfg = {
        "motionMad": 0.0015,        # 去噪后 intraMad 超过它算"有结构在动"（辅助）
        "motionChangeRatio": 0.004,  # 去噪后 |差|>8 的像素占比，超过它算"有结构变化"（主判据）
        "motionOrb": 2.0,           # 或 ORB 平移超过 2 px
        "staticNoiseSpread": 0.002,  # 时间噪声离散度低于 0.2% ⇒ 非独立采样（D4）
        "noiseFloor": 0.30,         # 时间噪声 σ 绝对下限（灰度级），低于它认为没有独立噪声
        "rotateOrb": 3.0,           # 一致平移超过 3 px 且 inlier 高 ⇒ 正在被转动
        "rotateInlier": 0.6,
    }
    if params:
        cfg.update({k: v for k, v in params.items() if k in cfg})

    if not frames:
        return {"available": False, "note": "微时序层需要至少 1 帧", "frames": 0}

    grays = [prepare_gray(f) for f in frames]
    # 尺寸不一致时（极少数：分辨率被中途改过）统一到第一帧尺寸
    h, w = grays[0].shape[:2]
    grays = [g if g.shape[:2] == (h, w) else cv2.resize(g, (w, h)) for g in grays]
    bgrs = [prepare_bgr(f) for f in frames]
    bgrs = [b if b.shape[:2] == (h, w) else cv2.resize(b, (w, h)) for b in bgrs]
    # 去噪版：中值滤波抹掉独立传感器噪声，只保留结构（用于运动/边缘/ORB）
    denoised = [cv2.medianBlur(g, 5) for g in grays]

    n = len(grays)
    hashes = [frame_hash(g) for g in grays]

    # --- bit-exact 重复帧对
    dup_pairs = 0
    total_pairs = 0
    for i in range(n):
        for j in range(i + 1, n):
            total_pairs += 1
            if hashes[i] == hashes[j]:
                dup_pairs += 1
    bit_exact_dup = float(dup_pairs) / float(total_pairs or 1)

    # --- 帧间绝对差 / 结构变化占比 / 边缘变化率（去噪后）
    intra_mads: List[float] = []
    intra_change_ratios: List[float] = []
    intra_edge_rates: List[float] = []
    for i in range(1, n):
        raw_diff = cv2.absdiff(denoised[i], denoised[i - 1])
        intra_mads.append(float(raw_diff.mean()) / 255.0)
        # 结构性变化：去噪后仍有大幅差异的像素占比。用"占比"而不是"均值"，
        # 是因为中值滤波后残留噪声会把均值抬起来，而占比对少量强变化更敏感。
        intra_change_ratios.append(float((raw_diff > 8).mean()))
        e0 = cv2.Canny(denoised[i - 1], 60, 160)
        e1 = cv2.Canny(denoised[i], 60, 160)
        intra_edge_rates.append(float(cv2.absdiff(e0, e1).mean()) / 255.0)

    # --- ORB 平移（去噪后，逐对取中位数）
    translations: List[Dict[str, Any]] = []
    for i in range(1, n):
        translations.append(orb_translation(denoised[i - 1], denoised[i]))
    magnitudes = [t["magnitude"] for t in translations if t["ok"]]
    inliers = [t["inlierRatio"] for t in translations if t["ok"]]
    orb_mag = float(np.median(magnitudes)) if magnitudes else 0.0
    orb_inlier = float(np.median(inliers)) if inliers else 0.0

    # --- 时间噪声：相邻帧差的 σ / √2 = 单帧独立噪声的估计
    #     同一帧重复 ⇒ diff 恒为 0 ⇒ temporalSigma = 0（冻结的物理证据）
    temporal_sigmas: List[float] = []
    for i in range(1, n):
        d = grays[i].astype(np.float32) - grays[i - 1].astype(np.float32)
        temporal_sigmas.append(float(np.std(d)) / float(np.sqrt(2.0)))
    temporal_sigma = float(np.median(temporal_sigmas)) if temporal_sigmas else 0.0
    temporal_mean = float(np.mean(temporal_sigmas)) if temporal_sigmas else 0.0
    temporal_spread = float(np.std(temporal_sigmas)) / temporal_mean if temporal_mean > 1e-6 else 0.0

    # --- 空间噪声残差（含场景纹理，仅作参考/展示）
    spatial_sigmas = [noise_sigma(g) for g in grays]
    sigma_spatial = float(np.median(spatial_sigmas)) if spatial_sigmas else 0.0

    # --- 清晰度序列
    sharps = [laplacian_var(g) for g in grays]
    sharp_mean = float(np.mean(sharps)) if sharps else 0.0
    sharp_var_cv = float(np.std(sharps)) / sharp_mean if sharp_mean > 1e-6 else 0.0

    # --- 亮度/色度漂移
    lumas = [luma_mean(g) for g in grays]
    chromas = [chroma_mean(b) for b in bgrs]
    luma_drift = abs(lumas[-1] - lumas[0]) / 255.0 if n > 1 else 0.0
    chroma_drift = abs(chromas[-1] - chromas[0]) / 255.0 if n > 1 else 0.0

    intra_mad = float(np.mean(intra_mads)) if intra_mads else 0.0
    intra_change_ratio = float(np.mean(intra_change_ratios)) if intra_change_ratios else 0.0
    intra_edge_rate = float(np.mean(intra_edge_rates)) if intra_edge_rates else 0.0

    motion_high = (
        (intra_change_ratio > cfg["motionChangeRatio"])
        or (intra_mad > cfg["motionMad"] * 4)
        or (orb_mag > cfg["motionOrb"])
    )
    noise_absent = temporal_sigma < cfg["noiseFloor"]
    # 只有拿到至少一对帧才能谈"噪声"；K=1 时本条判据不成立（冻结只能靠跨采样点 D1）
    noise_judgeable = n >= 2

    # 运动 × 噪声 2×2 象限
    if not noise_judgeable:
        quadrant = "Q?"
        quadrant_label = "单帧模式（噪声不可判，仅能靠跨采样点 bit-exact）"
    elif motion_high and not noise_absent:
        quadrant = "Q1"      # 有动态 + 噪声正常 ⇒ 正常活动场景
        quadrant_label = "正常活动场景"
    elif not motion_high and not noise_absent:
        quadrant = "Q2"      # 静止 + 噪声正常 ⇒ 合规静态场景（不判）
        quadrant_label = "合规静态（有独立噪声）"
    elif not motion_high and noise_absent:
        quadrant = "Q3"      # 静止 + 无噪声 ⇒ 冻结重放
        quadrant_label = "冻结/重放（无独立噪声）"
    else:
        quadrant = "Q4"      # 有动态 + 无噪声 ⇒ 可疑（合成/贴片）
        quadrant_label = "可疑合成（有动态但无独立噪声）"

    # 正在被转动：一致平移 + 高 inlier + 幅度够大
    being_rotated = bool(orb_mag >= cfg["rotateOrb"] and orb_inlier >= cfg["rotateInlier"])

    return {
        "available": True,
        "frames": n,
        "bitExactDup": round(bit_exact_dup, 4),
        "intraMad": round(intra_mad, 6),
        "intraChangeRatio": round(intra_change_ratio, 5),
        "intraEdgeRate": round(intra_edge_rate, 6),
        "intraOrbTranslation": round(orb_mag, 3),
        "intraOrbInlier": round(orb_inlier, 4),
        "intraSharpnessVar": round(sharp_var_cv, 4),
        "sigmaPerFrame": round(temporal_sigma, 4),
        "sigmaSpread": round(temporal_spread, 5),
        "sigmaSpatial": round(sigma_spatial, 4),
        "lumaDrift": round(luma_drift, 4),
        "chromaDrift": round(chroma_drift, 4),
        "motion": round(max(
            min(intra_change_ratio / max(cfg["motionChangeRatio"], 1e-9), 1.0),
            min(intra_mad / max(cfg["motionMad"] * 4, 1e-9), 1.0),
        ), 4),
        "noise": round(min(temporal_sigma / max(cfg["noiseFloor"], 1e-9), 1.0), 4),
        "quadrant": quadrant,
        "quadrantLabel": quadrant_label,
        "noiseAbsent": bool(noise_absent),
        "noiseJudgeable": bool(noise_judgeable),
        "motionHigh": bool(motion_high),
        "noiseLow": bool(noise_absent),
        "beingRotated": being_rotated,
        "hashes": hashes,
        "sharpnessSeries": [round(v, 2) for v in sharps],
        "sigmaSeries": [round(v, 4) for v in temporal_sigmas],
        "translations": translations,
        "framesMeta": {
            "width": int(w),
            "height": int(h),
        },
        "cfg": cfg,
    }


# --------------------------------------------------------------------------- L0 基线
def region_mask(shape: Tuple[int, int], regions: Sequence[Dict[str, float]]) -> Optional[np.ndarray]:
    """把归一化矩形区域转成布尔掩码（True = 该像素被忽略/不参与统计）。"""
    if not regions:
        return None
    h, w = shape[:2]
    mask = np.zeros((h, w), dtype=bool)
    for box in regions:
        x0 = int(round(float(box.get("x", 0)) * w))
        y0 = int(round(float(box.get("y", 0)) * h))
        x1 = int(round((float(box.get("x", 0)) + float(box.get("w", 0))) * w))
        y1 = int(round((float(box.get("y", 0)) + float(box.get("h", 0))) * h))
        x0, x1 = max(0, min(x0, w)), max(0, min(x1, w))
        y0, y1 = max(0, min(y0, h)), max(0, min(y1, h))
        if x1 > x0 and y1 > y0:
            mask[y0:y1, x0:x1] = True
    return mask if mask.any() else None


def changed_region_stats(changed: np.ndarray, ignore: Optional[np.ndarray] = None) -> Dict[str, Any]:
    """分析"变化像素"的空间分布：整体占比 / 最大连通块占比 / 连通块数量。

    为什么需要它：真实监控里桌上的杯子、椅子上的包、门口的纸箱每天都会被移动，
    这些变化是**局部团块**；而遮挡、失焦、机位移动造成的是**大面积/全局**变化。
    只看"变化了多少像素"分不开这两类，必须看**空间形态**。
    """
    mask = changed.astype(bool)
    ignored = 0.0
    if ignore is not None and ignore.shape[:2] == mask.shape[:2]:
        mask = mask & ~ignore
        ignored = float(ignore.mean())
    total = float(mask.size) or 1.0
    ratio = float(mask.sum()) / total
    if ratio <= 0:
        return {"ratio": 0.0, "largestRatio": 0.0, "components": 0,
                "bbox": None, "ignoredRatio": round(ignored, 4)}
    num, _labels, stats, _centroids = cv2.connectedComponentsWithStats(
        mask.astype(np.uint8), connectivity=8)
    areas = stats[1:, cv2.CC_STAT_AREA] if num > 1 else np.array([0])
    largest_idx = int(np.argmax(areas)) + 1 if num > 1 else 0
    largest_ratio = float(areas.max()) / total if num > 1 else 0.0
    bbox = None
    if largest_idx > 0:
        x, y, bw, bh, _ = stats[largest_idx]
        bbox = {
            "x": round(x / mask.shape[1], 4), "y": round(y / mask.shape[0], 4),
            "w": round(bw / mask.shape[1], 4), "h": round(bh / mask.shape[0], 4),
        }
    return {
        "ratio": round(ratio, 4),
        "largestRatio": round(largest_ratio, 4),
        "components": int(num - 1),
        "bbox": bbox,
        "ignoredRatio": round(ignored, 4),
    }


def photometric_align(base: np.ndarray, cur: np.ndarray) -> Tuple[np.ndarray, Dict[str, Any]]:
    """把当前帧的**全局光度**对齐到基线：``cur ≈ gain × base + offset``。

    为什么需要：阴天/黄昏/开灯会让整幅画面整体变暗或对比度下降，
    这是**全局仿射变化**，不是篡改。但它会让每个像素都偏离基线，
    于是 dHist / 离群占比一起升高，导致"天气变了"被误判成"画面异常"。

    做法：最小二乘拟合 (gain, offset)，把当前帧反变换回基线的光度尺度，
    再做逐像素比对。这样：
      * 整体变暗/变亮/变灰 → 对齐后残差极小 → dHist 低 → 判正常；
      * 局部篡改（车停进来、被遮住、糊了）→ 拟合由主体结构主导，
        对齐后残差仍然大 → 照样检出。

    ``residualRatio = std(残差) / std(当前)``，越小说明这次变化越"纯光度"。
    """
    if base is None or cur is None or base.size == 0 or cur.size == 0:
        return cur, {"ok": False}
    b = base.astype(np.float32)
    c = cur.astype(np.float32)
    if b.shape[:2] != c.shape[:2]:
        c = cv2.resize(c, (b.shape[1], b.shape[0]))
    bf = b.ravel()
    cf = c.ravel()
    mb, mc = float(bf.mean()), float(cf.mean())
    denom = float(((bf - mb) ** 2).sum())
    if denom < 1e-6:
        return cur, {"ok": False, "reason": "基线无对比度"}
    gain = float(((bf - mb) * (cf - mc)).sum() / denom)
    offset = mc - gain * mb
    # 增益过低说明当前画面几乎不含基线的结构（被遮住/彻底换了画面），
    # 此时不要"对齐"，让后续判据按原样处理。
    if gain < 0.15:
        return cur, {"ok": False, "gain": round(gain, 4), "offset": round(offset, 2),
                     "reason": "增益过低，不做光度对齐"}
    adj = np.clip((c - offset) / gain, 0.0, 255.0)
    residual = c - (gain * b + offset)
    cur_std = float(c.std())
    resid_ratio = float(residual.std() / cur_std) if cur_std > 1e-6 else 1.0
    return adj.astype(np.uint8), {
        "ok": True,
        "gain": round(gain, 4),
        "offset": round(offset, 2),
        "residualRatio": round(resid_ratio, 4),
    }


def robust_z_profile(gray: np.ndarray, median_bg: np.ndarray, mad_bg: np.ndarray,
                     clip: float = 5.0, ignore: Optional[np.ndarray] = None) -> Dict[str, Any]:
    """与历史基线（按槽中位数背景）的逐像素鲁棒 z。

    ``z = |cur - median| / (1.4826 * MAD + eps)``

    返回 ``dHist``（平均截断 z 归一化到 0~1）与 ``pixelOutlierRatio``（z>3 的像素占比）。
    用 MAD 而不是标准差，是因为"篡改只占画面一小块"时标准差会被正常区域稀释。

    ``ignore`` 是"忽略区"掩码（用户圈出的会变化的桌面/货架等），
    这些像素**完全不参与** dHist 与离群占比的计算——这是抑制"桌面东西被挪动"误报的主要手段。
    """
    if median_bg is None or mad_bg is None:
        return {"available": False, "note": "基线尚未建立"}
    if median_bg.shape[:2] != gray.shape[:2]:
        gray = cv2.resize(gray, (median_bg.shape[1], median_bg.shape[0]))
    scale = 1.4826 * mad_bg + 1e-3
    z = np.abs(gray.astype(np.float32) - median_bg.astype(np.float32)) / scale
    z = np.clip(z, 0.0, clip)

    valid = None
    if ignore is not None and ignore.shape[:2] == z.shape[:2] and ignore.any():
        valid = ~ignore
    z_eval = z[valid] if valid is not None else z
    changed_raw = (z > 3.0)
    if valid is not None:
        changed_eval = changed_raw & valid
    else:
        changed_eval = changed_raw

    return {
        "available": True,
        "dHist": round(float(z_eval.mean()) / clip, 4) if z_eval.size else 0.0,
        "pixelOutlierRatio": round(float(changed_eval.mean()), 4),
        "dHistP95": round(float(np.percentile(z_eval, 95)) / clip, 4) if z_eval.size else 0.0,
        "meanZ": round(float(z_eval.mean()), 4) if z_eval.size else 0.0,
        "maxZ": round(float(z_eval.max()), 3) if z_eval.size else 0.0,
        "analyzedRatio": round(float(z_eval.size) / float(z.size), 4),
        "changed": changed_region_stats(changed_raw, ignore),
    }


def diff_heatmap(current: np.ndarray, median_bg: np.ndarray, max_width: int = 520) -> Optional[np.ndarray]:
    """生成"当前帧 vs 基线中位数"的差异热力图（可视化用）。"""
    if median_bg is None:
        return None
    if median_bg.shape[:2] != current.shape[:2]:
        current = cv2.resize(current, (median_bg.shape[1], median_bg.shape[0]))
    # 基线是 float32 的中位数（多帧平均会出小数），absdiff 要求两边同类型，
    # 这里统一转成 uint8 再算差异，避免 OpenCV 的 "different types" 报错。
    cur_u8 = np.clip(current, 0, 255).astype(np.uint8)
    bg_u8 = np.clip(median_bg, 0, 255).astype(np.uint8)
    diff = cv2.absdiff(cur_u8, bg_u8)
    norm = cv2.normalize(diff, None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8)
    colored = cv2.applyColorMap(norm, cv2.COLORMAP_JET)
    h, w = colored.shape[:2]
    if w > max_width:
        scale = max_width / float(w)
        colored = cv2.resize(colored, (max_width, max(int(h * scale), 1)))
    return colored


def side_by_side(images: Sequence[np.ndarray], labels: Optional[Sequence[str]] = None,
                 max_width: int = 900) -> Optional[np.ndarray]:
    """把若干张图横向拼在一起（基线与当前帧并排展示）。"""
    prepared: List[np.ndarray] = []
    for image in images:
        if image is None:
            continue
        img = image if image.ndim == 3 else cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
        prepared.append(img)
    if not prepared:
        return None
    h = max(img.shape[0] for img in prepared)
    padded = []
    for idx, img in enumerate(prepared):
        if img.shape[0] != h:
            scale = h / float(img.shape[0])
            img = cv2.resize(img, (max(int(img.shape[1] * scale), 1), h))
        if labels and idx < len(labels):
            cv2.putText(img, labels[idx], (6, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 3, cv2.LINE_AA)
            cv2.putText(img, labels[idx], (6, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 1, cv2.LINE_AA)
        padded.append(img)
    merged = np.hstack(padded)
    if merged.shape[1] > max_width:
        scale = max_width / float(merged.shape[1])
        merged = cv2.resize(merged, (max_width, max(int(merged.shape[0] * scale), 1)))
    return merged
