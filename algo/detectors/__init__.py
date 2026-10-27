"""算法层导出。"""

from .base import (
    COVERED,
    DEFOCUSED,
    MOVED,
    NORMAL,
    BaseDetector,
    DetectionResult,
    ParamSpec,
)
from .edge_disappearance import EdgeDisappearance
from .entropy import HistogramEntropy
from .frame_area import FrameAreaBlob
from .laplacian import LaplacianVariance
from .mog2_area import Mog2Foreground
from .orb_relocation import ORBRelocation
from .scene_diff import SceneDifference
from .shortlong import ShortLongHistogram
from .yolo_detector import YoloDetector

# 主力算法：参与 ≥2 票集成，前端默认全选
CLASSIC_DETECTORS = [
    LaplacianVariance,
    HistogramEntropy,
    SceneDifference,
    ORBRelocation,
    EdgeDisappearance,
]

# 辅助算法（kind="legacy"）：来自 G:/project/算法 的外部实现，按场景选用。
# 它们与主力算法信号同源（frame_area≈scene_diff）或时效不匹配（shortlong 面向分钟级），
# 因此**不参与投票**，只作为独立指标展示，避免放大同向误差。
AUX_DETECTORS = [
    Mog2Foreground,
    FrameAreaBlob,
    ShortLongHistogram,
]

ALL_DETECTORS = CLASSIC_DETECTORS + AUX_DETECTORS

__all__ = [
    "ALL_DETECTORS",
    "AUX_DETECTORS",
    "CLASSIC_DETECTORS",
    "COVERED",
    "DEFOCUSED",
    "MOVED",
    "NORMAL",
    "BaseDetector",
    "DetectionResult",
    "EdgeDisappearance",
    "FrameAreaBlob",
    "HistogramEntropy",
    "LaplacianVariance",
    "Mog2Foreground",
    "ORBRelocation",
    "ParamSpec",
    "SceneDifference",
    "ShortLongHistogram",
    "YoloDetector",
]
