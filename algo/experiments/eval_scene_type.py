"""比较 scene_diff 的两种「遮挡 vs 移位」判别规则，仅测标注片段。

规则 A（现状）：帧差均值 >= covered_threshold(60) → covered，否则 moved。
规则 B（借鉴 CCTV-Camera-Tampering-Detection 的"最大连通域"思路）：
    变化掩膜里最大连通域占比 >= blob_ratio → covered，否则 moved。

指标：按主系统口径取"段内告警帧的主导类型"，与标注类型比对（20 段）。

用法：
    python algo/experiments/eval_scene_type.py --video ... --baseline ...
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Dict, List, Tuple

import cv2
import numpy as np

ALGO_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ALGO_DIR))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from eval_candidates import parse_baseline  # noqa: E402
from media import iter_frames, read_frame_at  # noqa: E402

from detectors.scene_diff import SceneDifference  # noqa: E402


def largest_blob_ratio(diff_map: np.ndarray, pixel_threshold: int = 25) -> Tuple[float, float]:
    """返回 (最大连通域占比, 变化像素占比)。"""
    mask = (diff_map > pixel_threshold).astype(np.uint8) * 255
    if not mask.any():
        return 0.0, 0.0
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8))
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    largest = max((cv2.contourArea(c) for c in contours), default=0.0)
    total = float(mask.size)
    return largest / total, float(mask.mean() / 255.0)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", required=True)
    ap.add_argument("--baseline", required=True)
    ap.add_argument("--fps", type=float, default=2.0)
    ap.add_argument("--blob-ratio", type=float, default=0.6)
    ap.add_argument("--ref-gap-ms", type=int, default=1000)
    args = ap.parse_args()

    video = Path(args.video)
    gt = parse_baseline(Path(args.baseline), video.stem)
    det = SceneDifference()
    cap = cv2.VideoCapture(str(video))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    cap.release()
    step = max(1, int(round(fps / args.fps)))
    ctx = det  # 复用同一实例，但每段重新 set_reference，与主系统一致

    rows = []
    agree_a = agree_b = 0
    total = 0
    for start, end, kind in gt:
        ref = read_frame_at(str(video), max(0, start - args.ref_gap_ms))
        ctx.reset()
        ctx.set_reference(ref)
        counts_a: Dict[str, int] = {}
        counts_b: Dict[str, int] = {}
        fired = 0
        for item in iter_frames(str(video), step=step, max_frames=5000,
                                start_ms=start, end_ms=end):
            res = ctx.process(item.image)
            if res.skipped or not res.is_tampered:
                continue
            fired += 1
            counts_a[res.tamper_type] = counts_a.get(res.tamper_type, 0) + 1
            blob, _changed = largest_blob_ratio(
                cv2.absdiff(cv2.cvtColor(item.image, cv2.COLOR_BGR2GRAY),
                            cv2.cvtColor(ref, cv2.COLOR_BGR2GRAY)).astype(np.float32))
            guess = "covered" if blob >= args.blob_ratio else "moved"
            counts_b[guess] = counts_b.get(guess, 0) + 1
        type_a = max(counts_a.items(), key=lambda kv: kv[1])[0] if counts_a else "normal"
        type_b = max(counts_b.items(), key=lambda kv: kv[1])[0] if counts_b else "normal"
        total += 1
        agree_a += 1 if type_a == kind else 0
        agree_b += 1 if type_b == kind else 0
        rows.append({"startMs": start, "gt": kind, "fired": fired,
                     "ruleA": type_a, "ruleB": type_b})

    print(f"{'start':>8}  {'gt':<10}{'帧':>4}  {'规则A':<10}{'规则B':<10}")
    for r in rows:
        mark_a = "✓" if r["ruleA"] == r["gt"] else " "
        mark_b = "✓" if r["ruleB"] == r["gt"] else " "
        print(f"{r['startMs']/1000:>8.0f}s {r['gt']:<10}{r['fired']:>4}  "
              f"{r['ruleA']:<9}{mark_a} {r['ruleB']:<9}{mark_b}")
    print(f"\n类型一致：规则A {agree_a}/{total}  规则B(blob>={args.blob_ratio}) {agree_b}/{total}")


if __name__ == "__main__":
    main()
