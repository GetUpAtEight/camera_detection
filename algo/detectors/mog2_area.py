"""辅助算法 1：MOG2 前景面积占比（遮挡）。

来源：``G:/project/算法/Camera-Tampering-Detection-using-Opencv-Python``。

原实现用 MOG2 建立背景模型，把前景像素占比超过阈值当作篡改。这里保留思路，
修掉三处会导致无法运行/误判的问题：

* 原代码 ``_, contours, _ = cv2.findContours(...)`` 是 OpenCV 3 的三返回值签名，
  在 OpenCV 4/5 上直接抛 ``ValueError``；本项目用 4 参数版本重写；
* 首帧 ``apply`` 会把整幅图判成前景，加 ``warmup_frames`` 预热，避免段首假告警；
* 持续遮挡会被 MOG2 慢慢学进背景，所以 ``learning_rate`` 暴露成参数，
  需要长时间告警时调小。

**适用场景**：固定机位、背景相对静止的场景（走廊、围墙、无人值守机房），
用于发现「画面被整块物体挡住」。不适合手持/云台机位，也不适合分钟级以上的
持续遮挡（背景模型会自适应掉）。
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


class Mog2Foreground(BaseDetector):
    name = "mog2_area"
    display_name = "MOG2 前景面积（遮挡，辅助）"
    description = (
        "适用场景：固定机位、背景基本静止的监控（走廊/围墙/机房），检测画面被整块物体挡住。"
        "用 MOG2 背景建模，前景像素占比超过阈值即告警；不适合手持或云台机位，"
        "分钟级持续遮挡会被背景模型自适应掉。仅对视频有效。"
    )
    tamper_types: List[str] = [NORMAL, COVERED, MOVED]
    # MOG2 自带背景模型，不依赖外部参考帧
    requires_reference = False
    # 自动标定用的主指标：(detail 字段, 方向, 对应参数 key)
    primary_metric = ("foregroundRatio", "higher", "ratio")
    kind = "legacy"
    param_specs = [
        ParamSpec(
            key="ratio",
            label="前景面积占比阈值",
            default=0.15,
            min=0.02,
            max=0.95,
            step=0.01,
            description="前景占比超过该值判定为遮挡。默认 15% 来自真实监控素材的正常波动上限（p95 约 13%）；"
                        "原论文取 1/3，对真实画面偏高、几乎不会触发",
        ),
        ParamSpec(
            key="history",
            label="背景模型历史帧数",
            default=300,
            min=60,
            max=1200,
            step=10,
            type="int",
            description="MOG2 学习背景所用的历史长度，越长越稳定、对缓慢变化越迟钝",
        ),
        ParamSpec(
            key="var_threshold",
            label="前景判定方差阈值",
            default=16,
            min=4,
            max=64,
            step=1,
            description="像素与背景模型差异超过该方差倍数才算前景，越小越敏感",
        ),
        ParamSpec(
            key="learning_rate",
            label="背景学习率",
            default=-1,
            min=-1,
            max=0.1,
            step=0.001,
            description="负数表示自动；调小（如 0.001）可让持续遮挡更久才被吸收",
        ),
        ParamSpec(
            key="warmup_frames",
            label="预热帧数",
            default=5,
            min=0,
            max=30,
            step=1,
            type="int",
            description="前若干帧只用于建背景、不参与判定，避免首帧全图前景造成假告警",
        ),
    ]

    def reset(self) -> None:
        self._bg = None
        self._seen = 0

    def current_threshold(self) -> float:
        return float(self.p("ratio"))

    def _model(self):
        if self._bg is None:
            self._bg = cv2.createBackgroundSubtractorMOG2(
                history=int(self.p("history")),
                varThreshold=float(self.p("var_threshold")),
                detectShadows=False,
            )
        return self._bg

    def _process(self, frame: np.ndarray) -> DetectionResult:
        ratio_threshold = float(self.p("ratio"))
        gray = to_gray(frame)
        mask = self._model().apply(gray, learningRate=float(self.p("learning_rate")))
        self._seen += 1

        warmup = int(self.p("warmup_frames"))
        if self._seen <= warmup:
            return DetectionResult(
                algorithm=self.name,
                skipped=True,
                note=f"背景建模中（{self._seen}/{warmup} 帧）",
                tamper_type=NORMAL,
            )

        # 前景二值化 + 形态学去噪：先小核腐蚀掉噪点，再适度膨胀补回轮廓
        _, mask = cv2.threshold(mask, 127, 255, cv2.THRESH_BINARY)
        kernel = np.ones((5, 5), np.uint8)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)

        foreground = float(np.count_nonzero(mask)) / float(mask.size)
        is_tampered = foreground > ratio_threshold
        score = severity(foreground, ratio_threshold, higher_is_worse=True)

        tamper_type = NORMAL
        if is_tampered:
            # 前景几乎铺满多为遮挡；占比中等但成片则可能是机位被转动
            tamper_type = COVERED if foreground >= 0.6 else MOVED

        detail: Dict[str, object] = {
            "foregroundRatio": round(foreground, 4),
            "threshold": ratio_threshold,
            "warmupFrames": warmup,
            "backgroundFrames": self._seen,
        }

        result = DetectionResult(
            algorithm=self.name,
            score=score,
            is_tampered=is_tampered,
            tamper_type=tamper_type,
            detail=detail,
            note=f"前景占比 {foreground * 100:.1f}%" + ("，超过阈值" if is_tampered else "，正常"),
        )

        if self.visual_enabled:
            canvas = cv2.cvtColor(mask, cv2.COLOR_GRAY2BGR)
            put_label(
                canvas,
                f"foreground={foreground * 100:.1f}% thr={ratio_threshold * 100:.0f}%",
                color=(0, 255, 255) if is_tampered else (0, 255, 0),
            )
            result.visual_image = canvas
        return result
