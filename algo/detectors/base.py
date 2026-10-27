"""算法基类与跨层数据契约。

所有检测算法的统一约定：

* ``name``        : 唯一标识（英文，前端与 SpringBoot 用它做 key）
* ``display_name``: 中文显示名
* ``score``       : 0~1 的归一化异常分，**0.5 恰好对应各算法自身的判定阈值**，
                    因此多算法可以放在同一张曲线图上直接对比，前端只需画一条 0.5 判定线。
* ``is_tampered`` : 布尔判定结果（等价于原始指标越过阈值，而非 score > 0.5 的近似）
* ``tamper_type`` : normal / covered / defocused / moved
* ``detail``      : 中间量（sharpness、entropy、translation ...），供前端展示与排查
"""

from __future__ import annotations

import base64
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import cv2
import numpy as np

NORMAL = "normal"
COVERED = "covered"
DEFOCUSED = "defocused"
MOVED = "moved"


def clamp01(value: float) -> float:
    """把数值夹到 [0, 1]，同时兜住 NaN / inf。"""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return 0.0
    if not np.isfinite(v):
        return 0.0
    return float(min(1.0, max(0.0, v)))


def severity(metric: float, threshold: float, higher_is_worse: bool = True) -> float:
    """把原始指标映射成 0~1 的异常分，且保证 0.5 落在判定阈值上。

    ``metric == threshold`` 时返回 0.5；指标恶化到 2 倍阈值（或下降到 0）时封顶 1.0。
    """
    if threshold is None or threshold <= 0 or not np.isfinite(metric):
        return 0.0
    if higher_is_worse:
        return clamp01(0.5 + 0.5 * (metric - threshold) / threshold)
    return clamp01(0.5 + 0.5 * (threshold - metric) / threshold)


def to_gray(frame: np.ndarray) -> np.ndarray:
    if frame.ndim == 2:
        return frame
    return cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)


def encode_data_url(image: np.ndarray, max_width: int = 480, quality: int = 78) -> str:
    """把可视化图编码成前端可直接放进 <img src> 的 data URL。"""
    if image is None or image.size == 0:
        return ""
    h, w = image.shape[:2]
    if w > max_width:
        scale = max_width / float(w)
        image = cv2.resize(image, (max_width, max(int(h * scale), 1)), interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", image, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
    if not ok:
        return ""
    return "data:image/jpeg;base64," + base64.b64encode(buf.tobytes()).decode("ascii")


def put_label(image: np.ndarray, text: str, origin=(8, 22), color=(0, 255, 255)) -> np.ndarray:
    """在图上写一行带描边的文字。"""
    cv2.putText(image, text, origin, cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 0, 0), 3, cv2.LINE_AA)
    cv2.putText(image, text, origin, cv2.FONT_HERSHEY_SIMPLEX, 0.55, color, 1, cv2.LINE_AA)
    return image


def heatmap(gray_or_diff: np.ndarray) -> np.ndarray:
    """把单通道差异图映射成伪彩色热力图。"""
    norm = cv2.normalize(gray_or_diff, None, 0, 255, cv2.NORM_MINMAX)
    return cv2.applyColorMap(norm.astype(np.uint8), cv2.COLORMAP_JET)


@dataclass
class ParamSpec:
    """参数元数据，前端据此动态生成表单。"""

    key: str
    label: str
    default: float
    min: float
    max: float
    step: float = 1.0
    unit: str = ""
    type: str = "number"  # number | int
    description: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "default": self.default,
            "min": self.min,
            "max": self.max,
            "step": self.step,
            "unit": self.unit,
            "type": self.type,
            "description": self.description,
        }


@dataclass
class DetectionResult:
    """单帧单算法的检测结果。"""

    algorithm: str
    score: float = 0.0
    is_tampered: bool = False
    tamper_type: str = NORMAL
    confidence: float = 0.0
    detail: Dict[str, Any] = field(default_factory=dict)
    skipped: bool = False
    note: str = ""
    visual: Optional[str] = None
    # 中间可视化图：由算法内部填充，序列化前由服务层编码为 data URL 并置空
    visual_image: Optional[np.ndarray] = field(default=None, repr=False, compare=False)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "algorithm": self.algorithm,
            "score": round(float(self.score), 4),
            "isTampered": bool(self.is_tampered),
            "tamperType": self.tamper_type,
            "confidence": round(float(self.confidence), 4),
            "detail": _jsonable(self.detail),
            "skipped": bool(self.skipped),
            "note": self.note,
            "visual": self.visual or "",
        }


def _jsonable(value: Any) -> Any:
    """把 numpy 标量/数组安全地转成原生 JSON 类型。"""
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items() if not k.startswith("_")}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, (np.floating, np.integer)):
        return value.item()
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


class BaseDetector(ABC):
    """所有经典算法的基类。"""

    name: str = ""
    display_name: str = ""
    description: str = ""
    tamper_types: List[str] = [NORMAL]
    requires_reference: bool = True
    param_specs: List[ParamSpec] = []
    kind: str = "opencv"
    # 自动标定用的主指标：(detail 里的字段名, "higher" | "lower", 对应参数 key)
    # 前端"学习场景基线"按这个字段取样本、算分位数、写回参数
    primary_metric: Optional[Tuple[str, str, str]] = None

    def __init__(self, params: Optional[Dict[str, Any]] = None):
        self.params: Dict[str, Any] = {spec.key: spec.default for spec in self.param_specs}
        if params:
            self.update_params(params)
        self._reference: Optional[np.ndarray] = None
        # 是否生成中间过程可视化图（视频场景下服务层会按帧数预算关闭，避免结果 JSON 过大）
        self.visual_enabled: bool = True
        self.reset()

    # ------------------------------------------------------------------ 参数
    def update_params(self, params: Dict[str, Any]) -> None:
        """按 param_specs 做类型转换与范围裁剪，非法值直接忽略。"""
        for spec in self.param_specs:
            if spec.key not in params:
                continue
            raw = params.get(spec.key)
            try:
                value = float(raw)
            except (TypeError, ValueError):
                continue
            if not np.isfinite(value):
                continue
            value = min(float(spec.max), max(float(spec.min), value))
            self.params[spec.key] = int(round(value)) if spec.type == "int" else round(value, 4)

    def p(self, key: str) -> Any:
        return self.params.get(key)

    def meta(self, available: bool = True) -> Dict[str, Any]:
        primary = None
        if self.primary_metric:
            field, direction, param = self.primary_metric
            primary = {"field": field, "direction": direction, "param": param}
        return {
            "name": self.name,
            "displayName": self.display_name,
            "description": self.description,
            "tamperTypes": self.tamper_types,
            "requiresReference": self.requires_reference,
            "kind": self.kind,
            "available": available,
            "thresholdScore": 0.5,
            "primaryMetric": primary,
            "params": [spec.to_dict() for spec in self.param_specs],
        }

    # -------------------------------------------------------------- 参考帧
    def set_reference(self, frame: np.ndarray) -> None:
        if frame is None:
            return
        self._reference = frame.copy()
        self._on_reference(frame)

    def _on_reference(self, frame: np.ndarray) -> None:
        """子类可覆写以预计算参考帧特征（如 ORB 描述子、边缘图）。"""

    def has_reference(self) -> bool:
        return self._reference is not None

    def reset(self) -> None:
        """重置算法内部的时序状态（每个视频任务开始前调用）。"""

    # -------------------------------------------------------------- 执行
    def process(self, frame: np.ndarray) -> DetectionResult:
        if frame is None or frame.size == 0:
            return DetectionResult(
                algorithm=self.name, skipped=True, note="空帧，已跳过", tamper_type=NORMAL
            )
        if self.requires_reference and not self.has_reference():
            return DetectionResult(
                algorithm=self.name,
                skipped=True,
                note="该算法依赖参考帧，当前输入未提供参考帧",
                tamper_type=NORMAL,
            )
        try:
            result = self._process(frame)
        except Exception as exc:  # 单个算法异常不应拖垮整条链路
            return DetectionResult(
                algorithm=self.name,
                skipped=True,
                note=f"算法执行异常：{exc}",
                tamper_type=NORMAL,
            )
        result.algorithm = self.name
        result.score = clamp01(result.score)
        result.confidence = clamp01(result.confidence) if result.confidence else clamp01(
            abs(result.score - 0.5) * 2
        )
        result.detail = result.detail or {}
        result.detail.setdefault("threshold", self.current_threshold())
        return result

    def current_threshold(self) -> float:
        """用于展示的主阈值（子类覆写）。"""
        return 0.5

    @abstractmethod
    def _process(self, frame: np.ndarray) -> DetectionResult:
        raise NotImplementedError
