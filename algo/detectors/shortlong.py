"""辅助算法 3：短/长期直方图池差异（Sitara & Mehtre 方法）。

来源：``G:/project/算法/CameraTamperingDetection``（cpu / gpu / video 三份是同一算法）。
特征 = 彩色度二维直方图 + 亮度-色差直方图 + 梯度方向直方图，共 3320 维；
用短池（近期帧）与长池（历史帧）之间的中位距离做分子、长池自比较的中位距离做分母，
``d_norm = ln(d_between / d_long)``，从而**不需要参考帧**、能自适应场景本身的抖动幅度。

相比原实现修正了四处：

* ``d_long`` 为 0（完全静止画面）时用 eps 兜底，避免 ``ln(inf)``；
* 长池自比较排除对角线，否则对角线上的 0 会把中位数整体拉低、阈值失效；
* 去掉每帧 ``print`` 与每帧 matplotlib 绘图（原版在服务里会直接卡死）；
* GPU 版本（cupy）改为 numpy——矩阵规模只有几十×几千，没必要多一个重依赖。

**适用场景**：分钟级以上的持续篡改（长期遮挡、镜头被转向后一直不动、画面长时间异常）。
对 1~2 秒的瞬时事件不敏感（短池里只有两三帧，中位数压不动），
因此在本项目里默认不参与投票，只在明确需要"长时异常"判断时勾选。
"""

from __future__ import annotations

import math
from collections import deque
from typing import Dict, List, Sequence

import cv2
import numpy as np

from .base import (
    COVERED,
    MOVED,
    NORMAL,
    BaseDetector,
    DetectionResult,
    ParamSpec,
    put_label,
    severity,
)


class ShortLongHistogram(BaseDetector):
    name = "shortlong"
    display_name = "短/长期直方图（长时异常，辅助）"
    description = (
        "适用场景：分钟级以上的持续篡改——长期遮挡、镜头被转向后一直不动、画面长时间异常。"
        "用短池/长池的直方图距离比自适应场景抖动，无需参考帧；对 1~2 秒的瞬时事件不敏感"
        "（短池只有两三帧），默认不参与投票，需要判断「是否长期异常」时再勾选。"
    )
    tamper_types: List[str] = [NORMAL, COVERED, MOVED]
    # 只用画面自身的时序统计，不需要外部参考帧
    requires_reference = False
    # 自动标定用的主指标：(detail 字段, 方向, 对应参数 key)
    primary_metric = ("dNorm", "higher", "threshold")
    kind = "legacy"
    param_specs = [
        ParamSpec(
            key="threshold",
            label="d_norm 阈值",
            default=0.3,
            min=0.02,
            max=3.0,
            step=0.01,
            description="ln(短池间距/长池间距) 超过该值判定异常。默认 0.3 来自真实素材正常波动上限（p95 约 0.29）；"
                        "原论文取 1.0，对真实画面偏高、几乎不会触发",
        ),
        ParamSpec(
            key="short_size",
            label="短池长度（帧）",
            default=10,
            min=3,
            max=60,
            step=1,
            type="int",
            description="短池容纳最近多少帧，越小越灵敏但越抖；要抓秒级事件需调到 3~5。短池满后才会往长池搬帧，所以出第一个结果需要 short_size + min_long 帧",
        ),
        ParamSpec(
            key="long_size",
            label="长池长度（帧）",
            default=36,
            min=8,
            max=180,
            step=1,
            type="int",
            description="长池容纳多少历史帧，作为「场景正常波动」的基准",
        ),
        ParamSpec(
            key="min_long",
            label="长池最少样本数",
            default=5,
            min=4,
            max=60,
            step=1,
            type="int",
            description="长池样本少于此数时不做判定（冷启动期）",
        ),
    ]

    def reset(self) -> None:
        self._short = deque(maxlen=int(self.p("short_size")))
        self._long = deque(maxlen=int(self.p("long_size")))
        self._seen = 0
        self._d_long_cache = 0.0

    def current_threshold(self) -> float:
        return float(self.p("threshold"))

    # ------------------------------------------------------------ 特征
    # 直方图对分辨率不敏感，先把长边压到 480 再统计：1080p 输入可省十几毫秒/帧
    FEATURE_WIDTH = 480

    @classmethod
    def _hist(cls, frame: np.ndarray) -> np.ndarray:
        if frame.shape[1] > cls.FEATURE_WIDTH:
            scale = cls.FEATURE_WIDTH / float(frame.shape[1])
            frame = cv2.resize(
                frame,
                (cls.FEATURE_WIDTH, max(1, int(round(frame.shape[0] * scale)))),
                interpolation=cv2.INTER_AREA,
            )

        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        chroma = cv2.calcHist([hsv], [0, 1], None, [50, 60], [0, 180, 0, 256]).flatten()
        chroma /= np.linalg.norm(chroma) + 1e-6

        l1 = np.sum(frame, axis=2, dtype=np.float32)
        rng = (np.amax(frame, axis=2) - np.amin(frame, axis=2)).astype(np.float32)
        stacked = np.stack((l1, rng), axis=-1)
        l1r = cv2.calcHist([stacked], [0, 1], None, [8, 8], [0, 256, 0, 256]).flatten()
        l1r /= np.linalg.norm(l1r) + 1e-6

        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        gx = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
        gy = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
        direction = np.arctan2(gy, gx)
        grad = cv2.calcHist([direction], [0], None, [16], [-np.pi, np.pi]).flatten()
        grad /= np.linalg.norm(grad) + 1e-6

        return np.concatenate((chroma, l1r, grad)).astype(np.float32)

    @staticmethod
    def _pairwise_median(p1: Sequence[np.ndarray], p2: Sequence[np.ndarray],
                         skip_self: bool = False) -> float:
        a = np.asarray(p1, dtype=np.float32)
        b = np.asarray(p2, dtype=np.float32)
        diffs = np.abs(a[:, None, :] - b[None, :, :]).sum(axis=2)
        if skip_self and len(a) == len(b):
            np.fill_diagonal(diffs, np.nan)
            with np.errstate(all="ignore"):
                return float(np.nanmedian(diffs))
        return float(np.median(diffs))

    # ------------------------------------------------------------ 时序
    def _process(self, frame: np.ndarray) -> DetectionResult:
        threshold = float(self.p("threshold"))
        min_long = int(self.p("min_long"))
        self._seen += 1

        self._short.append(self._hist(frame))
        if len(self._short) == self._short.maxlen:
            # 短池满后，把最旧的一帧挪进长池（与原实现一致）
            self._long.append(self._short[0])

        if len(self._long) < min_long:
            return DetectionResult(
                algorithm=self.name,
                skipped=True,
                note=f"长池积累中（{len(self._long)}/{min_long}）",
                tamper_type=NORMAL,
                score=0.0,
                detail={"dNorm": 0.0, "longSamples": len(self._long), "warmup": True},
            )

        d_between = self._pairwise_median(self._short, self._long)
        # 长池自比较较贵（O(n²·3320)），每 5 帧重算一次，其余帧复用
        if self._seen % 5 == 1 or self._d_long_cache <= 0:
            self._d_long_cache = self._pairwise_median(self._long, self._long, skip_self=True)
        d_long = self._d_long_cache

        ratio = (d_between + 1e-6) / (d_long + 1e-6)
        d_norm = math.log(ratio) if ratio > 0 else 0.0

        is_tampered = d_norm > threshold
        score = severity(d_norm, threshold, higher_is_worse=True)

        tamper_type = NORMAL
        if is_tampered:
            # 直方图差异本身不区分遮挡/移位：偏离幅度极大更可能是整幅被挡
            tamper_type = COVERED if d_norm >= threshold * 2 else MOVED

        detail: Dict[str, object] = {
            "dNorm": round(d_norm, 4),
            "dBetween": round(d_between, 5),
            "dLong": round(d_long, 5),
            "threshold": threshold,
            "shortSamples": len(self._short),
            "longSamples": len(self._long),
        }

        result = DetectionResult(
            algorithm=self.name,
            score=score,
            is_tampered=is_tampered,
            tamper_type=tamper_type,
            detail=detail,
            note=f"d_norm={d_norm:.2f}" + ("（超过阈值）" if is_tampered else "（正常）"),
        )

        if self.visual_enabled:
            canvas = frame.copy()
            put_label(
                canvas,
                f"d_norm={d_norm:.2f} thr={threshold:.2f}",
                color=(0, 255, 255) if is_tampered else (0, 255, 0),
            )
            result.visual_image = canvas
        return result
