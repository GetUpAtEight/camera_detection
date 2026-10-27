"""媒体读写工具：图片解码、视频抽帧网格计算。"""

from __future__ import annotations

import math
from typing import Dict, Optional, Tuple

import cv2
import numpy as np


class MediaError(RuntimeError):
    """无法解码 / 打开媒体时抛出，服务层会转成 400。"""


def decode_image(data: bytes) -> np.ndarray:
    if not data:
        raise MediaError("上传内容为空")
    buffer = np.frombuffer(data, dtype=np.uint8)
    image = cv2.imdecode(buffer, cv2.IMREAD_COLOR)
    if image is None:
        raise MediaError("图片解码失败，可能不是有效的图片文件")
    return image


def probe_image(data: bytes) -> Dict[str, object]:
    image = decode_image(data)
    h, w = image.shape[:2]
    return {"mediaType": "image", "width": w, "height": h, "frameCount": 1,
            "fps": 0.0, "durationMs": 0}


def sample_step(fps: float, sample_fps: float) -> int:
    """按目标采样帧率计算抽帧间隔（至少 1 帧）。

    用向上取整，保证实际采样率不超过请求的 sample_fps（25fps 取 10fps → 每 3 帧取 1 帧）。
    """
    if sample_fps is None or sample_fps <= 0 or fps <= 0:
        return 1
    return max(1, int(math.ceil(fps / float(sample_fps) - 1e-9)))


def frame_index_at(fps: float, timestamp_ms: int) -> int:
    """毫秒时间戳 → 帧号（四舍五入，seek 用）。"""
    if fps <= 0:
        return 0
    return max(0, int(round(timestamp_ms * fps / 1000.0)))


def expected_sampled_frames(frame_count: int, step: int, max_frames: int,
                            fps: float = 0.0,
                            start_ms: Optional[int] = None,
                            end_ms: Optional[int] = None) -> int:
    """预估采样帧数（进度条分母）。按采样网格精确计数，避免分母虚高导致进度卡住。"""
    total = frame_count
    if total > 0 and fps > 0:
        begin = max(0, min(frame_index_at(fps, start_ms or 0), total - 1))
        if end_ms and end_ms > 0:
            finish = max(begin, min(frame_index_at(fps, end_ms), total - 1))
        else:
            finish = total - 1
    elif fps > 0 and (start_ms or end_ms):
        # 容器没给总帧数：按时间估
        begin = frame_index_at(fps, start_ms or 0)
        finish = frame_index_at(fps, end_ms) if end_ms and end_ms > 0 else begin + max_frames * step
    elif total > 0:
        begin, finish = 0, total - 1
    else:
        return max_frames

    first = ((begin + step - 1) // step) * step
    if first > finish:
        return 1
    return max(1, min(max_frames, (finish - first) // step + 1))


def resize_for_speed(frame: np.ndarray, max_width: int) -> Tuple[np.ndarray, float]:
    """限制处理分辨率，长视频演示时能显著提升速度（返回缩放比）。"""
    if max_width <= 0 or frame.shape[1] <= max_width:
        return frame, 1.0
    scale = max_width / float(frame.shape[1])
    resized = cv2.resize(frame, (max_width, max(1, int(frame.shape[0] * scale))))
    return resized, scale
