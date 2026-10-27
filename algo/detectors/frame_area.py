"""辅助算法 2：帧差 + 最大连通域面积（遮挡）。

来源：``G:/project/算法/CCTV-Camera-Tampering-Detection/tamper_detect.py``。

原实现把当前帧与上一帧做差、二值化、腐蚀膨胀后取**最大连通域**，
若该连通域面积占全图超过阈值就判篡改。这里保留"最大连通域"这个与
``scene_diff``（整幅平均差）互补的视角：它看的是"是否有一整块区域同时变化"。

**适用场景**：单帧内出现成片遮挡（贴纸、手、喷漆、糊镜头）。
对局部小幅改动和缓慢漂移不敏感；因为与上一帧比较，机位平移会产生大面积
差分，容易把"镜头转动"误判成遮挡，所以只能当辅助信号，不参与投票。
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
    put_label,
    severity,
    to_gray,
)


class FrameAreaBlob(BaseDetector):
    name = "frame_area"
    display_name = "帧差最大连通域（遮挡，辅助）"
    description = (
        "适用场景：单帧内出现成片遮挡（贴纸、手、喷漆、糊镜头），看的是「是否有一整块区域同时变化」。"
        "与上一帧比较，只在画面突变的瞬间敏感，持续遮挡要靠 scene_diff 判断；"
        "机位平移也会把整幅差分撑大，容易误判成遮挡，因此只作辅助信号，默认不参与 ≥2 票集成。"
    )
    tamper_types: List[str] = [NORMAL, COVERED, MOVED]
    # 与原实现一致：与上一采样帧比较，不需要外部参考帧
    requires_reference = False
    # 自动标定用的主指标：(detail 字段, 方向, 对应参数 key)
    primary_metric = ("largestBlobRatio", "higher", "area_ratio")
    kind = "legacy"
    param_specs = [
        ParamSpec(
            key="area_ratio",
            label="最大连通域占比阈值",
            default=0.12,
            min=0.02,
            max=0.95,
            step=0.01,
            description="最大连通域面积占全图比例超过该值判定为遮挡。默认 12% 来自真实素材正常波动上限（p95 约 10%）；"
                        "原实现取 40%，对真实画面偏高",
        ),
        ParamSpec(
            key="diff_threshold",
            label="像素差阈值",
            default=30,
            min=5,
            max=90,
            step=1,
            description="与上一帧的灰度差超过该值才算变化像素",
        ),
        ParamSpec(
            key="dilate_iterations",
            label="膨胀次数",
            default=3,
            min=0,
            max=6,
            step=1,
            type="int",
            description="把邻近的变化像素连成整块，次数越多越容易连成大区域",
        ),
    ]

    def reset(self) -> None:
        self._prev = None

    def current_threshold(self) -> float:
        return float(self.p("area_ratio"))

    def _process(self, frame: np.ndarray) -> DetectionResult:
        area_threshold = float(self.p("area_ratio"))
        gray = to_gray(frame)

        if self._prev is None or self._prev.shape != gray.shape:
            self._prev = gray
            return DetectionResult(
                algorithm=self.name,
                skipped=True,
                note="首帧仅用于建立比较基准",
                tamper_type=NORMAL,
            )

        diff = cv2.absdiff(gray, self._prev)
        self._prev = gray
        _, mask = cv2.threshold(diff, float(self.p("diff_threshold")), 255, cv2.THRESH_BINARY)
        mask = cv2.erode(mask, np.ones((4, 4), np.uint8), iterations=1)
        iterations = int(self.p("dilate_iterations"))
        if iterations > 0:
            mask = cv2.dilate(mask, np.ones((9, 9), np.uint8), iterations=iterations)

        # OpenCV 4/5 是两返回值（原仓库的三返回值写法在这里会直接报错）
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        largest = max((cv2.contourArea(c) for c in contours), default=0.0)
        blob_ratio = largest / float(mask.size)
        changed_ratio = float(np.count_nonzero(mask)) / float(mask.size)

        is_tampered = blob_ratio > area_threshold
        score = severity(blob_ratio, area_threshold, higher_is_worse=True)

        tamper_type = NORMAL
        if is_tampered:
            # 差异集中在单块 → 遮挡；变化分散在全图 → 更可能是机位被转动
            tamper_type = COVERED if changed_ratio <= 0.75 else MOVED

        detail: Dict[str, object] = {
            "largestBlobRatio": round(blob_ratio, 4),
            "changedPixelRatio": round(changed_ratio, 4),
            "threshold": area_threshold,
            "diffThreshold": float(self.p("diff_threshold")),
        }

        result = DetectionResult(
            algorithm=self.name,
            score=score,
            is_tampered=is_tampered,
            tamper_type=tamper_type,
            detail=detail,
            note=f"最大连通域 {blob_ratio * 100:.1f}%" + ("，超过阈值" if is_tampered else "，正常"),
        )

        if self.visual_enabled:
            canvas = cv2.cvtColor(mask, cv2.COLOR_GRAY2BGR)
            if contours:
                cv2.drawContours(canvas, contours, -1, (0, 255, 255), 1)
            put_label(
                canvas,
                f"blob={blob_ratio * 100:.1f}% thr={area_threshold * 100:.0f}%",
                color=(0, 255, 255) if is_tampered else (0, 255, 0),
            )
            result.visual_image = canvas
        return result
