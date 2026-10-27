"""算法 2：直方图熵法（遮挡检测）。

原理：镜头被手、胶带、塑料袋、喷雾遮挡后，画面灰度分布趋于单一，信息熵下降。
增强：额外做 4×4 分块熵，任一块熵过低即判为"部分遮挡"。
"""

from __future__ import annotations

from typing import Dict, List

import cv2
import numpy as np

from .base import (
    COVERED,
    NORMAL,
    BaseDetector,
    DetectionResult,
    ParamSpec,
    put_label,
    severity,
    to_gray,
)


def _entropy(gray: np.ndarray) -> float:
    hist = cv2.calcHist([gray], [0], None, [256], [0, 256]).ravel()
    total = float(hist.sum())
    if total <= 0:
        return 0.0
    p = hist / total
    p = p[p > 0]
    return float(max(0.0, -np.sum(p * np.log2(p))))


class HistogramEntropy(BaseDetector):
    name = "entropy"
    display_name = "直方图熵（遮挡检测）"
    description = (
        "统计灰度直方图的香农熵，遮挡时熵显著下降；同时做 4×4 分块熵，"
        "可识别只遮住部分画面的攻击。"
    )
    tamper_types: List[str] = [NORMAL, COVERED]
    requires_reference = False
    # 自动标定用的主指标：(detail 字段, 方向, 对应参数 key)
    primary_metric = ("entropy", "lower", "threshold")
    param_specs = [
        ParamSpec(
            key="threshold",
            label="全局熵阈值",
            default=3.0,
            min=0.5,
            max=8.0,
            step=0.1,
            unit="bit",
            description="整幅图灰度熵低于该值判定为遮挡",
        ),
        ParamSpec(
            key="block_threshold",
            label="分块熵阈值",
            default=2.0,
            min=0.2,
            max=8.0,
            step=0.1,
            unit="bit",
            description="图像块熵低于该值视为「无纹理块」",
        ),
        ParamSpec(
            key="block_ratio",
            label="低熵块占比阈值",
            default=0.5,
            min=0.1,
            max=1.0,
            step=0.05,
            description="低熵块占比超过该值判定为局部/整体遮挡（避免大面积白墙误报）",
        ),
        ParamSpec(
            key="grid",
            label="分块数",
            default=4,
            min=2,
            max=8,
            step=1,
            type="int",
            description="把画面切成 grid×grid 块分别统计熵",
        ),
    ]

    def current_threshold(self) -> float:
        return float(self.p("threshold"))

    def _process(self, frame: np.ndarray) -> DetectionResult:
        gray = to_gray(frame)
        threshold = float(self.p("threshold"))
        block_threshold = float(self.p("block_threshold"))
        block_ratio = float(self.p("block_ratio"))
        grid = int(self.p("grid"))

        entropy = _entropy(gray)
        h, w = gray.shape[:2]
        block_entropies: List[float] = []
        low_blocks = 0
        for gy in range(grid):
            for gx in range(grid):
                y0, y1 = int(h * gy / grid), int(h * (gy + 1) / grid)
                x0, x1 = int(w * gx / grid), int(w * (gx + 1) / grid)
                block = gray[y0:max(y1, y0 + 1), x0:max(x1, x0 + 1)]
                block_entropy = _entropy(block)
                block_entropies.append(round(block_entropy, 3))
                if block_entropy < block_threshold:
                    low_blocks += 1

        min_block = min(block_entropies) if block_entropies else 0.0
        low_ratio = low_blocks / float(len(block_entropies) or 1)

        global_tampered = entropy < threshold
        block_tampered = low_ratio >= block_ratio

        score = max(
            severity(entropy, threshold, higher_is_worse=False),
            severity(low_ratio, block_ratio, higher_is_worse=True) if block_tampered else 0.0,
        )
        if not global_tampered and not block_tampered:
            score = min(score, 0.49)
        is_tampered = global_tampered or block_tampered
        reasons = []
        if global_tampered:
            reasons.append(f"全局熵 {entropy:.2f} < {threshold:.1f}")
        if block_tampered:
            reasons.append(f"{low_ratio * 100:.0f}% 的图像块熵低于 {block_threshold:.1f}")

        detail: Dict[str, object] = {
            "entropy": round(entropy, 4),
            "minBlockEntropy": round(min_block, 4),
            "blockEntropies": block_entropies,
            "lowEntropyBlocks": low_blocks,
            "lowBlockRatio": round(low_ratio, 4),
            "grid": grid,
            "globalThreshold": threshold,
            "blockThreshold": block_threshold,
            "blockRatioThreshold": block_ratio,
        }

        result = DetectionResult(
            algorithm=self.name,
            score=score,
            is_tampered=is_tampered,
            tamper_type=COVERED if is_tampered else NORMAL,
            detail=detail,
            note="；".join(reasons),
        )

        if self.visual_enabled:
            canvas = frame.copy()
            for gy in range(grid):
                for gx in range(grid):
                    idx = gy * grid + gx
                    value = block_entropies[idx]
                    y0, y1 = int(h * gy / grid), int(h * (gy + 1) / grid)
                    x0, x1 = int(w * gx / grid), int(w * (gx + 1) / grid)
                    low = value < block_threshold
                    color = (0, 0, 255) if low else (0, 255, 0)
                    cv2.rectangle(canvas, (x0, y0), (x1 - 1, y1 - 1), color, 1)
                    put_label(
                        canvas,
                        f"{value:.1f}",
                        origin=(x0 + 4, y0 + 16),
                        color=color,
                    )
            put_label(
                canvas,
                f"H={entropy:.2f} thr={threshold:.1f} low_blocks={low_ratio * 100:.0f}% "
                f"(thr {block_ratio * 100:.0f}%)",
                origin=(8, h - 12),
                color=(0, 255, 255) if is_tampered else (0, 255, 0),
            )
            result.visual_image = canvas
        return result
