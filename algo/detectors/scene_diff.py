"""算法 3：帧差 / 场景差异法（遮挡 + 移位）。

原理：与参考帧逐像素比较，遮挡或视角移动时平均绝对差骤增。
"""

from __future__ import annotations

from typing import Dict, List

import cv2
import numpy as np

from .base import (
    COVERED,
    MOVED,
    NORMAL,
    BaseDetector,
    DetectionResult,
    ParamSpec,
    heatmap,
    put_label,
    severity,
    to_gray,
)


class SceneDifference(BaseDetector):
    name = "scene_diff"
    display_name = "帧差 / 场景差异（遮挡+移位）"
    description = (
        "计算当前帧与参考帧的平均绝对差。差值越大说明画面与「正常」状态偏离越多，"
        "可同时覆盖遮挡和镜头移位两类攻击。"
    )
    tamper_types: List[str] = [NORMAL, COVERED, MOVED]
    requires_reference = True
    # 自动标定用的主指标：(detail 字段, 方向, 对应参数 key)
    primary_metric = ("sceneDiff", "higher", "threshold")
    param_specs = [
        ParamSpec(
            key="threshold",
            label="帧差阈值",
            default=30.0,
            min=1.0,
            max=255.0,
            step=1.0,
            description="平均绝对差超过该值判定为异常",
        ),
        ParamSpec(
            key="covered_threshold",
            label="判定为遮挡的帧差",
            default=60.0,
            min=5.0,
            max=200.0,
            step=1.0,
            description="帧差超过该值时倾向判定为遮挡（否则倾向判定为移位）",
        ),
        ParamSpec(
            key="blur_kernel",
            label="高斯模糊核",
            default=5,
            min=0,
            max=31,
            step=2,
            type="int",
            description="比较前先做高斯模糊以抑制噪声（0 表示不模糊）",
        ),
    ]

    def _on_reference(self, frame: np.ndarray) -> None:
        self._ref_small = self._prepare(frame)

    def current_threshold(self) -> float:
        return float(self.p("threshold"))

    def _prepare(self, frame: np.ndarray) -> np.ndarray:
        gray = to_gray(frame)
        kernel = int(self.p("blur_kernel"))
        if kernel >= 3:
            kernel = kernel if kernel % 2 == 1 else kernel + 1
            gray = cv2.GaussianBlur(gray, (kernel, kernel), 0)
        return gray

    def _process(self, frame: np.ndarray) -> DetectionResult:
        threshold = float(self.p("threshold"))
        covered_threshold = float(self.p("covered_threshold"))
        gray = self._prepare(frame)
        ref_gray = getattr(self, "_ref_small", None)
        if ref_gray is None or ref_gray.shape != gray.shape:
            return DetectionResult(
                algorithm=self.name,
                skipped=True,
                note="参考帧缺失或与当前帧尺寸不一致，已跳过",
            )

        diff_map = cv2.absdiff(gray, ref_gray)
        diff = float(diff_map.mean())
        is_tampered = diff > threshold
        score = severity(diff, threshold, higher_is_worse=True)
        tamper_type = NORMAL
        if is_tampered:
            tamper_type = COVERED if diff >= covered_threshold else MOVED

        # 变化像素占比，比整体均值更能反映"局部大面积遮挡"
        changed_ratio = float((diff_map > 25).mean())

        detail: Dict[str, object] = {
            "sceneDiff": round(diff, 3),
            "changedPixelRatio": round(changed_ratio, 4),
            "threshold": threshold,
            "coveredThreshold": covered_threshold,
        }

        result = DetectionResult(
            algorithm=self.name,
            score=score,
            is_tampered=is_tampered,
            tamper_type=tamper_type,
            detail=detail,
            note=f"帧差 {diff:.1f}" + (" 超过阈值" if is_tampered else " 正常"),
        )

        if self.visual_enabled:
            canvas = heatmap(diff_map.astype(np.float32))
            put_label(
                canvas,
                f"diff={diff:.1f} thr={threshold:.0f}",
                color=(0, 255, 255) if is_tampered else (0, 255, 0),
            )
            result.visual_image = canvas
        return result
