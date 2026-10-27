"""生成演示用样本：正常参考图 + 失焦图 + 遮挡图 + 一段含篡改的合成视频。

    .venv/Scripts/python.exe ../samples/generate_samples.py
"""

from __future__ import annotations

import os

import cv2
import numpy as np

W, H = 960, 540
OUT = os.path.dirname(os.path.abspath(__file__))


def scene(seed: int = 3) -> np.ndarray:
    rng = np.random.default_rng(seed)
    canvas = rng.integers(30, 220, (H, W, 3), dtype=np.uint8)
    canvas = cv2.GaussianBlur(canvas, (15, 15), 0)
    # 地面与墙面分割，模拟固定机位监控画面
    cv2.rectangle(canvas, (0, int(H * 0.62)), (W, H), (58, 62, 70), -1)
    for _ in range(35):
        x, y = int(rng.integers(0, W - 80)), int(rng.integers(0, int(H * 0.62) - 60))
        w, h = int(rng.integers(40, 200)), int(rng.integers(30, 140))
        color = tuple(int(v) for v in rng.integers(60, 255, 3))
        cv2.rectangle(canvas, (x, y), (x + w, y + h), color, -1)
        cv2.rectangle(canvas, (x, y), (x + w, y + h), (20, 22, 28), 2)
    for i in range(6):
        cv2.putText(canvas, f"CAM-07  2026-10-20 10:{i:02d}", (24, 40 + i * 22),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (235, 235, 235), 1, cv2.LINE_AA)
    return canvas


def main() -> None:
    base = scene()
    cv2.imwrite(os.path.join(OUT, "reference_normal.jpg"), base)
    cv2.imwrite(os.path.join(OUT, "target_defocused.jpg"), cv2.GaussianBlur(base, (31, 31), 0))
    # 轻微失焦：清晰度介于正常与重度失焦之间，用来演示"阈值改动能改变结论"
    cv2.imwrite(os.path.join(OUT, "target_mild_blur.jpg"), cv2.GaussianBlur(base, (5, 5), 0))
    covered = base.copy()
    covered[:] = (34, 36, 40)
    cv2.circle(covered, (W // 2, H // 2), 180, (10, 10, 12), -1)
    cv2.imwrite(os.path.join(OUT, "target_covered.jpg"), covered)

    shift_matrix = np.float32([[1, 0, 210], [0, 1, 60]])
    shifted = cv2.warpAffine(base, shift_matrix, (W, H))
    cv2.imwrite(os.path.join(OUT, "target_shifted.jpg"), shifted)

    video_path = os.path.join(OUT, "demo_video.mp4")
    writer = cv2.VideoWriter(video_path, cv2.VideoWriter_fourcc(*"mp4v"), 25.0, (W, H))
    frames = 0
    for i in range(50):  # 正常
        frame = base.copy()
        cx = 120 + i * 12
        cv2.circle(frame, (cx, int(H * 0.7)), 26, (240, 240, 240), -1)
        cv2.putText(frame, "person", (cx - 26, int(H * 0.7) - 34), cv2.FONT_HERSHEY_SIMPLEX,
                    0.5, (240, 240, 240), 1, cv2.LINE_AA)
        writer.write(frame)
        frames += 1
    for _ in range(40):  # 失焦（模拟调焦）
        writer.write(cv2.GaussianBlur(base, (27, 27), 0))
        frames += 1
    for _ in range(40):  # 遮挡（模拟喷漆/贴胶带）
        writer.write(covered)
        frames += 1
    writer.release()

    # 标注文件：第 50 帧起为失焦段，第 90 帧起为遮挡段，两段都算篡改
    gt_path = os.path.join(OUT, "ground_truth_demo.csv")
    with open(gt_path, "w", encoding="utf-8", newline="") as fp:
        fp.write("frame_index,tampered\n")
        for i in range(frames):
            fp.write(f"{i},{1 if i >= 50 else 0}\n")

    print(f"已生成样本到 {OUT}")
    print(f"- reference_normal.jpg / target_defocused.jpg / target_mild_blur.jpg / target_covered.jpg / target_shifted.jpg")
    print(f"- demo_video.mp4（{frames} 帧，25fps，约 {frames / 25:.1f}s：正常 2s → 失焦 1.6s → 遮挡 1.6s）")
    print(f"- ground_truth_demo.csv（frame_index,tampered；第 50~129 帧标为篡改）")


if __name__ == "__main__":
    main()
