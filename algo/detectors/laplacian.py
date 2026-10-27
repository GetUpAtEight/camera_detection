"""算法 1：拉普拉斯方差法（失焦 / 模糊检测）。

原理：图像失焦后边缘退化，Laplacian 二阶响应的方差显著下降。
判定：``fm < threshold`` → 失焦。若提供了参考帧，额外看相对下降比例。
"""

from __future__ import annotations

from typing import Dict, List

import cv2
import numpy as np

from .base import (
    DEFOCUSED,
    NORMAL,
    BaseDetector,
    DetectionResult,
    ParamSpec,
    heatmap,
    put_label,
    severity,
    to_gray,
)


class LaplacianVariance(BaseDetector):
    name = "laplacian"
    display_name = "拉普拉斯方差（失焦检测）"
    description = (
        "对灰度图求 Laplacian 后计算方差，方差越小画面越模糊。"
        "适用于人为调焦、涂抹镜头等失焦攻击。"
    )
    tamper_types: List[str] = [NORMAL, DEFOCUSED]
    requires_reference = False
    # 自动标定用的主指标：(detail 字段, 方向, 对应参数 key)
    primary_metric = ("sharpness", "lower", "threshold")
    param_specs = [
        ParamSpec(
            key="threshold",
            label="清晰度阈值",
            default=100.0,
            min=5.0,
            max=800.0,
            step=5.0,
            description="Laplacian 方差低于该值判定为失焦",
        ),
        ParamSpec(
            key="relative_ratio",
            label="相对参考帧下降比",
            default=0.45,
            min=0.05,
            max=1.0,
            step=0.05,
            description="清晰度低于参考帧的该比例时也判定为失焦（0 表示不启用）",
        ),
    ]

    def current_threshold(self) -> float:
        return float(self.p("threshold"))

    def _process(self, frame: np.ndarray) -> DetectionResult:
        gray = to_gray(frame)
        sharpness = float(cv2.Laplacian(gray, cv2.CV_64F).var())
        threshold = float(self.p("threshold"))

        absolute_tampered = sharpness < threshold
        score = severity(sharpness, threshold, higher_is_worse=False)
        tamper_type = DEFOCUSED if absolute_tampered else NORMAL
        reasons = []
        if absolute_tampered:
            reasons.append(f"清晰度 {sharpness:.1f} < 阈值 {threshold:.0f}")

        detail: Dict[str, object] = {
            "sharpness": round(sharpness, 2),
            "absoluteThreshold": threshold,
        }

        if self.has_reference():
            ref_sharpness = float(cv2.Laplacian(to_gray(self._reference), cv2.CV_64F).var())
            ratio = float(self.p("relative_ratio"))
            drop_ratio = 1.0 - (sharpness / ref_sharpness) if ref_sharpness > 1e-6 else 0.0
            detail["referenceSharpness"] = round(ref_sharpness, 2)
            detail["sharpnessDropRatio"] = round(drop_ratio, 4)
            if ratio > 0 and drop_ratio > ratio:
                score = max(score, severity(drop_ratio, ratio, higher_is_worse=True))
                if not absolute_tampered:
                    tamper_type = DEFOCUSED
                    reasons.append(
                        f"相对参考帧清晰度下降 {drop_ratio * 100:.1f}% > {ratio * 100:.0f}%"
                    )

        is_tampered = tamper_type != NORMAL
        result = DetectionResult(
            algorithm=self.name,
            score=score,
            is_tampered=is_tampered,
            tamper_type=tamper_type,
            detail=detail,
            note="；".join(reasons),
        )

        if self.visual_enabled:
            blur = cv2.Laplacian(gray, cv2.CV_64F)
            canvas = heatmap(np.abs(blur).astype(np.float32))
            canvas = cv2.addWeighted(frame, 0.35, canvas, 0.65, 0)
            put_label(
                canvas,
                f"sharpness={sharpness:.1f} thr={threshold:.0f}",
                color=(0, 255, 255) if is_tampered else (0, 255, 0),
            )
            result.visual_image = canvas
        return result
