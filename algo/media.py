"""媒体读写工具：图片解码、视频探测、按采样率抽帧。"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, Iterator, Optional, Tuple

import cv2
import numpy as np


class MediaError(RuntimeError):
    """无法解码 / 打开媒体时抛出，服务层会转成 400。"""


@dataclass
class Frame:
    index: int
    timestamp_ms: int
    image: np.ndarray


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


def open_video(path: str) -> cv2.VideoCapture:
    capture = cv2.VideoCapture(path)
    if not capture.isOpened():
        capture.release()
        raise MediaError(f"视频打开失败：{path}")
    return capture


def probe_video(path: str) -> Dict[str, object]:
    capture = open_video(path)
    try:
        width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        fps = float(capture.get(cv2.CAP_PROP_FPS) or 0.0)
        frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        if fps <= 0 or not math.isfinite(fps):
            fps = 25.0
    finally:
        capture.release()

    if width <= 0 or height <= 0:
        raise MediaError("无法读取视频分辨率，文件可能已损坏")
    return {
        "mediaType": "video",
        "width": width,
        "height": height,
        "fps": round(fps, 3),
        "frameCount": frame_count,
        "durationMs": int(frame_count / fps * 1000) if frame_count > 0 else 0,
    }


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


def read_frame_at(path: str, timestamp_ms: int) -> np.ndarray:
    """读取指定时刻的一帧，用作参考帧（段首之前的那面"镜子"）。"""
    capture = open_video(path)
    try:
        fps = float(capture.get(cv2.CAP_PROP_FPS) or 0.0) or 25.0
        index = frame_index_at(fps, max(0, timestamp_ms))
        capture.set(cv2.CAP_PROP_POS_FRAMES, index)
        # 有些容器 seek 会偏，最多顺读 8 帧找回
        for _ in range(8):
            ok, frame = capture.read()
            if ok and frame is not None:
                return frame
        raise MediaError("无法读取参考帧")
    finally:
        capture.release()


def iter_frames(path: str, step: int, max_frames: int,
                start_ms: Optional[int] = None,
                end_ms: Optional[int] = None) -> Iterator[Frame]:
    """顺序解码视频并每 ``step`` 帧产出一帧。

    ``start_ms`` / ``end_ms`` 给定时只解码该时间窗（区间检测）：
    * 用 ``CAP_PROP_POS_FRAMES`` 直接跳到起始帧，长视频不必从头解码；
    * 采样网格仍按**全局帧号**取模，所以同一帧在"整段"与"区间"两种跑法下都会出现，
      两次结果可以直接对比；
    * ``timestamp_ms`` 保持**绝对时间**（从视频开头算），这样区间检测的结果能和
      人工标注的时间轴直接对齐。
    """
    capture = open_video(path)
    try:
        fps = float(capture.get(cv2.CAP_PROP_FPS) or 0.0) or 25.0
        total = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        index = 0
        if start_ms is not None and start_ms > 0:
            index = frame_index_at(fps, start_ms)
            if total > 0:
                index = min(index, max(0, total - 1))
            capture.set(cv2.CAP_PROP_POS_FRAMES, index)
            index = int(capture.get(cv2.CAP_PROP_POS_FRAMES) or index)
        # 允许 1.5 帧的 seek 误差
        tolerance = 1.5 * 1000.0 / fps
        emitted = 0
        while True:
            ok, frame = capture.read()
            if not ok or frame is None:
                break
            timestamp_ms = int(index / fps * 1000)
            if end_ms is not None and end_ms >= 0 and timestamp_ms > end_ms + 1e-6:
                break
            if start_ms is not None and start_ms > 0 and timestamp_ms < start_ms - tolerance:
                index += 1
                continue
            if index % step == 0:
                yield Frame(index=index, timestamp_ms=timestamp_ms, image=frame)
                emitted += 1
                if emitted >= max_frames:
                    break
            index += 1
    finally:
        capture.release()


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
