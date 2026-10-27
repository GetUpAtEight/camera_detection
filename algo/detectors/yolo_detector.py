"""YOLO 深度学习检测器（第 6 个可选算法）。

定位：演示"后端 → Python 算法层 → GPU 深度学习模型"的完整链路。
输出标准目标检测结果（边界框 / 类别 / 置信度），不参与摄像头篡改的最终判定，
因此 ``score`` 恒为 0、``is_tampered`` 恒为 False，仅把检测明细放进 ``detail``。

并发：模型只在 :class:`DynamicBatcher` 的单一工作线程中被调用，
调用方（HTTP 线程 / 视频检测线程）只是往队列投递图像，避免多线程共享 YOLO 实例的竞态。
"""

from __future__ import annotations

import os
import threading
import time
from typing import Any, Dict, List, Optional, Sequence

import cv2
import numpy as np

from batching import DynamicBatcher
from .base import (
    NORMAL,
    DetectionResult,
    ParamSpec,
    encode_data_url,
    put_label,
)


class YoloDetector:
    name = "yolo"
    display_name = "YOLO 深度学习检测"
    description = (
        "复用服务器上的 yolo26n.pt，输出目标检测边界框/类别/置信度，"
        "用于演示算法层对 GPU 深度学习模型的完整调用链路。"
    )
    kind = "yolo"
    tamper_types = [NORMAL]
    requires_reference = False
    # 前端可调参数（与经典算法共用同一套参数表单协议）
    param_specs = [
        ParamSpec(
            key="conf",
            label="置信度阈值",
            default=0.25,
            min=0.01,
            max=0.95,
            step=0.01,
            description="低于该置信度的检测框被丢弃",
        ),
        ParamSpec(
            key="imgsz",
            label="推理尺寸",
            default=640,
            min=320,
            max=1280,
            step=32,
            type="int",
            unit="px",
        ),
        ParamSpec(
            key="max_detections",
            label="最多保留框数",
            default=50,
            min=1,
            max=300,
            step=1,
            type="int",
        ),
    ]

    def __init__(
        self,
        model_path: str,
        device: str = "auto",
        max_batch: int = 16,
        max_wait_ms: float = 2.0,
        enabled: bool = True,
    ) -> None:
        self.model_path = model_path
        self.device = self._resolve_device(device)
        self.max_batch = max_batch
        self.max_wait_ms = max_wait_ms
        self.enabled = enabled
        self.params: Dict[str, Any] = {spec.key: spec.default for spec in self.param_specs}
        self._model = None
        self._load_error: Optional[str] = None
        self._load_attempt_at: float = 0.0
        self._loading = False
        self._loaded = False
        self._names: Dict[int, str] = {}
        self._lock = threading.Lock()
        self._batcher = DynamicBatcher(
            self._predict_batch, max_batch=max_batch, max_wait_ms=max_wait_ms, name="yolo"
        )
        self.visual_enabled = True

    # ------------------------------------------------------------------ 初始化
    @staticmethod
    def _resolve_device(device: str) -> str:
        if device and device.lower() not in ("auto", ""):
            return device
        try:
            import torch  # type: ignore

            return "0" if torch.cuda.is_available() else "cpu"
        except Exception:
            return "cpu"

    @property
    def available(self) -> bool:
        return bool(self.enabled and self._loaded and self._model is not None)

    def status(self) -> Dict[str, Any]:
        return {
            "enabled": self.enabled,
            "available": self.available,
            "loading": self._loading,
            "modelPath": self.model_path,
            "modelExists": os.path.exists(self.model_path),
            "device": self.device,
            "error": self._load_error,
            "classes": len(self._names),
            "batcher": self._batcher.snapshot() if self._loaded else None,
        }

    def load_async(self) -> None:
        """后台加载模型，避免首次请求冷启动抖动，也不阻塞服务启动。"""
        if not self.enabled or self._loaded or self._loading:
            return
        threading.Thread(target=self.load, name="yolo-load", daemon=True).start()

    def load(self) -> bool:
        with self._lock:
            if self._loaded:
                return True
            if self._loading:
                return False
            self._loading = True
            self._load_error = None
            self._load_attempt_at = time.time()
        try:
            if not os.path.exists(self.model_path):
                raise FileNotFoundError(f"模型文件不存在：{self.model_path}")
            from ultralytics import YOLO  # type: ignore

            model = YOLO(self.model_path)
            try:
                model.to(self.device)
            except Exception:
                # 某些版本 device 通过 predict 传参即可，这里失败不致命
                pass
            self._model = model
            names = getattr(model, "names", None) or {}
            self._names = {int(k): str(v) for k, v in dict(names).items()}
            self._batcher.start()
            self._warmup()
            self._loaded = True
            return True
        except Exception as exc:
            self._load_error = f"{type(exc).__name__}: {exc}"
            self._model = None
            self._loaded = False
            return False
        finally:
            self._loading = False

    def _warmup(self, rounds: int = 2) -> None:
        """预热推理图，避免首个真实请求承担 CUDA 初始化开销。"""
        try:
            dummy = [np.zeros((640, 640, 3), dtype=np.uint8) for _ in range(min(2, self.max_batch))]
            for _ in range(rounds):
                self._predict_batch(dummy)
            try:
                import torch  # type: ignore

                if torch.cuda.is_available():
                    torch.cuda.synchronize()
            except Exception:
                pass
        except Exception:
            pass

    # ------------------------------------------------------------------ 参数
    def update_params(self, params: Optional[Dict[str, Any]]) -> None:
        for spec in self.param_specs:
            if not params or spec.key not in params:
                continue
            try:
                value = float(params[spec.key])
            except (TypeError, ValueError):
                continue
            value = min(float(spec.max), max(float(spec.min), value))
            self.params[spec.key] = int(round(value)) if spec.type == "int" else round(value, 4)

    def reset(self) -> None:
        """YOLO 无时序状态。"""

    def set_reference(self, frame: np.ndarray) -> None:
        """YOLO 不使用参考帧。"""

    def has_reference(self) -> bool:
        return False

    # ------------------------------------------------------------------ 推理
    def _predict_batch(self, images: Sequence[np.ndarray]) -> List[Dict[str, Any]]:
        if self._model is None:
            raise RuntimeError(self._load_error or "YOLO 模型未加载")
        started = time.perf_counter()
        results = self._model.predict(
            list(images),
            imgsz=int(self.params.get("imgsz", 640)),
            conf=float(self.params.get("conf", 0.25)),
            verbose=False,
            device=self.device,
        )
        elapsed = (time.perf_counter() - started) * 1000.0
        payloads: List[Dict[str, Any]] = []
        for result in results:
            payloads.append(self._to_payload(result, elapsed / max(len(results), 1)))
        return payloads

    def _to_payload(self, result: Any, elapsed_ms: float) -> Dict[str, Any]:
        detections: List[Dict[str, Any]] = []
        boxes = getattr(result, "boxes", None)
        max_det = int(self.params.get("max_detections", 50))
        if boxes is not None and len(boxes) > 0:
            xyxy = boxes.xyxy.cpu().numpy()
            confs = boxes.conf.cpu().numpy()
            classes = boxes.cls.cpu().numpy().astype(int)
            order = np.argsort(-confs)[:max_det]
            for idx in order:
                cls_id = int(classes[idx])
                detections.append(
                    {
                        "label": self._names.get(cls_id, str(cls_id)),
                        "classId": cls_id,
                        "confidence": round(float(confs[idx]), 4),
                        "bbox": [round(float(v), 1) for v in xyxy[idx].tolist()],
                    }
                )
        return {
            "detections": detections,
            "count": len(detections),
            "inferenceMs": round(float(elapsed_ms), 2),
            "model": os.path.basename(self.model_path),
            "device": self.device,
        }

    def analyze_many(self, frames: Sequence[np.ndarray]) -> List[DetectionResult]:
        """批量分析多帧（视频场景），返回与输入等长的结果列表。"""
        if not self.enabled:
            return [self._unavailable("YOLO 已在配置中关闭") for _ in frames]
        if not self._loaded:
            # 注意：必须先取加载错误快照，再触发 load_async()。
            # load() 开头会把 _load_error 置空，先读后写才不会把"已失败"
            # 误判成"正在加载"（否则每次请求都会看到错误的提示文案）。
            error = self._load_error
            retryable = error is None or (time.time() - self._load_attempt_at) > 15.0
            if retryable:
                self.load_async()
            if error:
                reason = f"YOLO 不可用：{error}" + ("（正在后台重试加载）" if retryable else "")
            else:
                reason = "YOLO 模型正在加载，请稍后重试"
            return [self._unavailable(reason) for _ in frames]

        try:
            payloads = self._batcher.submit_many(frames)
        except Exception as exc:
            return [self._unavailable(f"YOLO 推理失败：{exc}") for _ in frames]
        return [self._to_result(frame, payload) for frame, payload in zip(frames, payloads)]

    def analyze_one(self, frame: np.ndarray) -> DetectionResult:
        return self.analyze_many([frame])[0]

    def _to_result(self, frame: np.ndarray, payload: Dict[str, Any]) -> DetectionResult:
        detections = payload.get("detections", [])
        note = f"检测到 {len(detections)} 个目标，单帧推理 {payload.get('inferenceMs', 0)} ms"
        result = DetectionResult(
            algorithm=self.name,
            score=0.0,
            is_tampered=False,
            tamper_type=NORMAL,
            confidence=round(max((d["confidence"] for d in detections), default=0.0), 4),
            detail={
                "detections": detections,
                "count": len(detections),
                "inferenceMs": payload.get("inferenceMs"),
                "model": payload.get("model"),
                "device": payload.get("device"),
                "note": "深度学习目标检测链路演示，不参与篡改判定",
            },
            note=note,
        )
        if self.visual_enabled and detections:
            result.visual_image = self._render(frame, detections)
        return result

    def _render(self, frame: np.ndarray, detections: List[Dict[str, Any]]) -> np.ndarray:
        canvas = frame.copy()
        for det in detections:
            x1, y1, x2, y2 = [int(v) for v in det["bbox"]]
            cv2.rectangle(canvas, (x1, y1), (x2, y2), (0, 200, 255), 2)
            label = f"{det['label']} {det['confidence']:.2f}"
            (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)
            cv2.rectangle(canvas, (x1, max(y1 - th - 6, 0)), (x1 + tw + 6, y1), (0, 200, 255), -1)
            cv2.putText(
                canvas,
                label,
                (x1 + 3, max(y1 - 5, th)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.5,
                (0, 0, 0),
                1,
                cv2.LINE_AA,
            )
        put_label(canvas, f"YOLO detections={len(detections)}", color=(0, 255, 255))
        return canvas

    def _unavailable(self, reason: str) -> DetectionResult:
        return DetectionResult(
            algorithm=self.name,
            score=0.0,
            is_tampered=False,
            tamper_type=NORMAL,
            skipped=True,
            note=reason,
            detail={"error": self._load_error or reason},
        )

    def meta(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "displayName": self.display_name,
            "description": self.description,
            "tamperTypes": self.tamper_types,
            "requiresReference": False,
            "kind": self.kind,
            "available": self.available,
            "thresholdScore": 0.5,
            "params": [spec.to_dict() for spec in self.param_specs],
            "status": self.status(),
        }

    def encode_visual(self, result: DetectionResult, max_width: int = 480) -> str:
        if result.visual_image is None:
            return ""
        return encode_data_url(result.visual_image, max_width=max_width)
