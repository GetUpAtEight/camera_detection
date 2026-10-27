"""算法层自测：用合成画面验证 5 个算法在正常/失焦/遮挡/移位四种场景下的判定。

直接运行（不需要启动服务）：

    .venv/Scripts/python.exe tests/smoke_test.py

会生成一段合成视频到 ``tests/_out/synthetic.mp4`` 并打印每个场景的判定表。
"""

from __future__ import annotations

import os
import sys
from typing import Dict, List

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from detectors import CLASSIC_DETECTORS  # noqa: E402
import media as media_utils  # noqa: E402

W, H = 640, 360


def synthetic_scene(seed: int = 7) -> np.ndarray:
    rng = np.random.default_rng(seed)
    canvas = rng.integers(20, 235, (H, W, 3), dtype=np.uint8)
    canvas = cv2.GaussianBlur(canvas, (9, 9), 0)
    for _ in range(60):
        x, y = int(rng.integers(0, W - 40)), int(rng.integers(0, H - 40))
        w, h = int(rng.integers(24, 140)), int(rng.integers(24, 100))
        color = tuple(int(v) for v in rng.integers(0, 255, 3))
        cv2.rectangle(canvas, (x, y), (x + w, y + h), color, -1)
        cv2.putText(canvas, "CAM-07", (x, y + 20), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 1)
    return canvas


def make_blur(frame: np.ndarray) -> np.ndarray:
    return cv2.GaussianBlur(frame, (21, 21), 0)


def make_cover(frame: np.ndarray) -> np.ndarray:
    return np.full_like(frame, 35)


def make_shift(frame: np.ndarray, dx: int = 160, dy: int = 40) -> np.ndarray:
    matrix = np.float32([[1, 0, dx], [0, 1, dy]])
    return cv2.warpAffine(frame, matrix, (frame.shape[1], frame.shape[0]))


def run_case(name: str, reference: np.ndarray, current: np.ndarray) -> Dict[str, Dict[str, object]]:
    out: Dict[str, Dict[str, object]] = {}
    for cls in CLASSIC_DETECTORS:
        detector = cls()
        detector.visual_enabled = False
        if detector.requires_reference:
            detector.set_reference(reference)
        result = detector.process(current)
        out[detector.name] = {
            "score": round(result.score, 3),
            "tampered": result.is_tampered,
            "type": result.tamper_type,
            "note": result.note,
        }
    return out


def write_video(path: str) -> int:
    base = synthetic_scene()
    writer = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*"mp4v"), 25.0, (W, H))
    frames = 0
    for i in range(40):  # 正常
        frame = base.copy()
        cv2.circle(frame, (60 + i * 3, 300), 8, (255, 255, 255), -1)
        writer.write(frame)
        frames += 1
    for _ in range(25):  # 失焦
        writer.write(make_blur(base))
        frames += 1
    for _ in range(25):  # 遮挡
        writer.write(make_cover(base))
        frames += 1
    writer.release()
    return frames


def main() -> int:
    out_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_out")
    os.makedirs(out_dir, exist_ok=True)
    video_path = os.path.join(out_dir, "synthetic.mp4")
    total = write_video(video_path)
    info = media_utils.probe_video(video_path)
    print(f"合成视频: {video_path} 写入 {total} 帧, 探测结果 {info}")

    base = synthetic_scene()
    cases = [
        ("正常", base.copy()),
        ("失焦", make_blur(base)),
        ("遮挡", make_cover(base)),
        ("移位", make_shift(base)),
    ]

    header = f"{'场景':<6}" + "".join(f"{cls().display_name[:8]:<12}" for cls in CLASSIC_DETECTORS)
    print("\n" + header)
    failures: List[str] = []
    expectations = {
        "正常": {"laplacian": False, "entropy": False, "scene_diff": False, "orb": False, "edge": False},
        "失焦": {"laplacian": True},
        "遮挡": {"entropy": True, "scene_diff": True, "edge": True},
        "移位": {"orb": True, "scene_diff": True},
    }

    for label, frame in cases:
        row = run_case(label, base, frame)
        line = f"{label:<6}"
        for name, result in row.items():
            flag = "✓" if result["tampered"] else "·"
            line += f"{str(result['score']):>6}{flag}    "
        print(line)
        for algorithm, expected in expectations.get(label, {}).items():
            actual = row[algorithm]["tampered"]
            if actual != expected:
                failures.append(f"{label}/{algorithm}: 期望 {expected} 实际 {actual} ({row[algorithm]['note']})")

    # 视频抽帧链路
    sampled = list(media_utils.iter_frames(video_path, step=5, max_frames=50))
    print(f"\n抽帧结果: {len(sampled)} 帧, 首帧 ts={sampled[0].timestamp_ms}ms, "
          f"末帧 ts={sampled[-1].timestamp_ms}ms")

    if failures:
        print("\n[FAIL] 判定不符合预期：")
        for item in failures:
            print("  -", item)
        return 1
    print("\n[OK] 全部场景判定符合预期")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
