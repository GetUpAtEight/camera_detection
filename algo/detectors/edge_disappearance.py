"""算法 5：边缘消失率法（统一检测，MDPI Sensors 2015 思路）。

原理：遮挡与失焦都会让参考帧中原本存在的边缘在当前帧消失。
以"参考帧边缘在当前帧消失的比例"作为统一指标，并与该指标的时间均值做差，
超过自适应阈值即判定异常——这样能自动适应不同场景本身的边缘规模。

判定：``rate - baseline > threshold`` 且 ``rate > min_rate``。
"""

from __future__ import annotations

from collections import deque
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


class EdgeDisappearance(BaseDetector):
    name = "edge"
    display_name = "边缘消失率（统一检测）"
    description = (
        "Canny 提取参考帧与当前帧边缘，统计参考帧边缘在当前帧消失的比例，"
        "并与时间均值做差后按自适应阈值判定，可同时覆盖遮挡与失焦。"
    )
    tamper_types: List[str] = [NORMAL, COVERED]
    requires_reference = True
    # 自动标定用的主指标：(detail 字段, 方向, 对应参数 key)
    primary_metric = ("deviation", "higher", "threshold")
    param_specs = [
        ParamSpec(
            key="threshold",
            label="自适应偏差阈值",
            default=0.25,
            min=0.05,
            max=2.0,
            step=0.05,
            description="边缘消失率高出时间均值该幅度时判定异常",
        ),
        ParamSpec(
            key="min_rate",
            label="最低消失率",
            default=0.2,
            min=0.02,
            max=1.0,
            step=0.02,
            description="边缘消失率本身低于该值时不做告警（抑制冷启动误报）",
        ),
        ParamSpec(
            key="canny_low",
            label="Canny 低阈值",
            default=50.0,
            min=5.0,
            max=200.0,
            step=5.0,
        ),
        ParamSpec(
            key="canny_high",
            label="Canny 高阈值",
            default=150.0,
            min=10.0,
            max=400.0,
            step=10.0,
        ),
        ParamSpec(
            key="tolerance",
            label="边缘抖动容差",
            default=2,
            min=0,
            max=9,
            step=1,
            type="int",
            unit="px",
            description="当前帧边缘先做膨胀，容忍 1~2 像素的抖动与压缩噪声",
        ),
        ParamSpec(
            key="history",
            label="时间窗口",
            default=60,
            min=5,
            max=300,
            step=5,
            type="int",
            unit="帧",
            description="用最近 N 帧的边缘消失率均值作为基线",
        ),
    ]

    def current_threshold(self) -> float:
        return float(self.p("threshold"))

    def reset(self) -> None:
        self._history = deque(maxlen=int(self.p("history")))
        self._ref_edges = None
        self._ref_edge_count = 0

    def _edges(self, gray: np.ndarray) -> np.ndarray:
        return cv2.Canny(gray, float(self.p("canny_low")), float(self.p("canny_high")))

    def _on_reference(self, frame: np.ndarray) -> None:
        gray = to_gray(frame)
        edges = self._edges(gray)
        self._ref_edges = edges
        self._ref_edge_count = int(np.count_nonzero(edges))
        self._history.clear()

    def _process(self, frame: np.ndarray) -> DetectionResult:
        if self._ref_edges is None or self._ref_edge_count <= 0:
            return DetectionResult(
                algorithm=self.name,
                skipped=True,
                note="参考帧未提取到有效边缘，无法计算消失率",
            )

        gray = to_gray(frame)
        if self._ref_edges.shape != gray.shape:
            return DetectionResult(
                algorithm=self.name,
                skipped=True,
                note="参考帧与当前帧尺寸不一致，已跳过",
            )

        cur_edges = self._edges(gray)
        tolerance = int(self.p("tolerance"))
        if tolerance > 0:
            kernel = np.ones((tolerance + 1, tolerance + 1), np.uint8)
            cur_edges_dilated = cv2.dilate(cur_edges, kernel, iterations=1)
        else:
            cur_edges_dilated = cur_edges

        disappeared = cv2.bitwise_and(self._ref_edges, cv2.bitwise_not(cur_edges_dilated))
        rate = float(np.count_nonzero(disappeared)) / float(self._ref_edge_count)

        threshold = float(self.p("threshold"))
        min_rate = float(self.p("min_rate"))
        # 基线只统计"正常帧"的边缘消失率，避免长时间遮挡把基线抬高后不再告警；
        # 历史不足 3 帧时基线取 0（参考帧自身消失率为 0，不会造成冷启动误报）。
        baseline = float(np.mean(self._history)) if len(self._history) >= 3 else 0.0
        deviation = rate - baseline

        is_tampered = bool(rate > min_rate and deviation > threshold)
        score = severity(deviation, threshold, higher_is_worse=True)
        if not is_tampered:
            score = min(score, 0.49)
            self._history.append(rate)
        else:
            score = max(score, 0.51)

        detail: Dict[str, object] = {
            "edgeDisappearanceRate": round(rate, 4),
            "baseline": round(baseline, 4),
            "deviation": round(deviation, 4),
            "referenceEdgePixels": self._ref_edge_count,
            "currentEdgePixels": int(np.count_nonzero(cur_edges)),
            "edgeDensity": round(float(np.count_nonzero(cur_edges)) / cur_edges.size, 6),
            "adaptiveThreshold": threshold,
            "minRate": min_rate,
            "historySize": len(self._history),
        }

        result = DetectionResult(
            algorithm=self.name,
            score=score,
            is_tampered=is_tampered,
            tamper_type=COVERED if is_tampered else NORMAL,
            detail=detail,
            note=(
                f"边缘消失率 {rate:.2f}，高出基线 {deviation:.2f}"
                if is_tampered
                else f"边缘消失率 {rate:.2f}，处于正常波动范围"
            ),
        )

        if self.visual_enabled:
            # 单图叠加：当前帧边缘画白，相对参考帧"消失"的边缘画红，比三图并排更易读
            canvas = cv2.cvtColor(cur_edges, cv2.COLOR_GRAY2BGR)
            canvas[disappeared > 0] = (0, 0, 255)
            put_label(
                canvas,
                f"ref={self._ref_edge_count}px cur={int(np.count_nonzero(cur_edges))}px "
                f"disappeared={rate * 100:.0f}% (red)",
                color=(0, 255, 255) if is_tampered else (0, 255, 0),
            )
            result.visual_image = canvas
        return result
