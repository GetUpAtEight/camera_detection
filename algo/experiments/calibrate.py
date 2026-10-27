"""在真实监控素材上标定各算法的主指标分布，用于定「灵敏 / 均衡 / 保守」三档阈值。

问题背景：8 个算法的默认阈值来自合成样本，换到真实摄像头画面后，主力算法误报不断、
辅助算法一次不报。根因是两类错误的阈值方向相反——主力算法按"理想画面"调得过敏感，
辅助算法按论文/原仓库调得过高。

做法：逐帧跑 8 个算法，按标注把帧分成「正常」与「篡改」两组，看主指标的分位数：

* 正常组的 **95 分位**就是「5% 帧级误报」对应的阈值；
* 再看篡改组有多少比例越过这个阈值（帧级召回），判断这个算法在该素材上有没有用。

用法：
    python algo/experiments/calibrate.py --video <video1.mp4> --baseline baseline.py
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path
from typing import Dict, List, Tuple

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from detectors import ALL_DETECTORS  # noqa: E402
from media import iter_frames  # noqa: E402

# 算法 -> (detail 里的主指标字段, 方向, 对应的参数 key)
METRICS: Dict[str, Tuple[str, str, str]] = {
    "laplacian": ("sharpness", "lower", "threshold"),
    "entropy": ("entropy", "lower", "threshold"),
    "scene_diff": ("sceneDiff", "higher", "threshold"),
    "orb": ("translation", "higher", "motion_pixels"),
    "edge": ("deviation", "higher", "threshold"),
    "mog2_area": ("foregroundRatio", "higher", "ratio"),
    "frame_area": ("largestBlobRatio", "higher", "area_ratio"),
    "shortlong": ("dNorm", "higher", "threshold"),
}

TYPE_ALIAS = {
    "occlusion": "covered",
    "bluriness": "defocused",
    "blur": "defocused",
    "jitter": "moved",
    "covered": "covered",
    "defocused": "defocused",
    "moved": "moved",
}


def clock(text: str) -> int:
    parts = [float(p) for p in text.split(":")]
    seconds = 0.0
    for p in parts:
        seconds = seconds * 60 + p
    return int(seconds * 1000)


def parse_baseline(path: Path, stem: str) -> List[Tuple[int, int, str]]:
    """与后端 GroundTruthService 同口径：按 videoN 分块、type 数组、[start,end] 对。"""
    text = path.read_text(encoding="utf-8", errors="ignore")
    import re

    blocks = re.split(r"^\s*(video\d+)\s*=\s*\{", text, flags=re.M)
    for i in range(1, len(blocks), 2):
        if blocks[i] != stem:
            continue
        body = blocks[i + 1].split("}")[0]
        types = re.findall(r'"type"\s*:\s*\[([^\]]*)\]', body)
        types = re.findall(r'"([a-zA-Z]+)"', types[0]) if types else []
        pairs = re.findall(r'\[\s*"([\d:.]+)"\s*,\s*"([\d:.]+)"\s*\]', body)
        out = []
        for idx, (a, b) in enumerate(pairs):
            start, stop = clock(a), clock(b)
            kind = types[idx] if idx < len(types) else "covered"
            out.append((start, max(stop, start + 500), TYPE_ALIAS.get(kind, kind)))
        return out
    raise SystemExit(f"baseline 里没有 {stem} 块")


def quantiles(values: List[float], points: List[float]) -> Dict[str, float]:
    if not values:
        return {str(p): 0.0 for p in points}
    arr = np.asarray(values, dtype=np.float64)
    return {str(p): round(float(np.percentile(arr, p)), 4) for p in points}


def recommend(neg: List[float], pos: List[float], direction: str, target_fp: float) -> Dict:
    """按目标帧级误报率取阈值，并给出对应的帧级召回。"""
    if not neg:
        return {"threshold": None, "recall": None}
    if direction == "higher":
        threshold = float(np.percentile(neg, 100 - target_fp))
        hits = sum(1 for v in pos if v > threshold)
    else:
        threshold = float(np.percentile(neg, target_fp))
        hits = sum(1 for v in pos if v < threshold)
    recall = hits / len(pos) if pos else 0.0
    return {"threshold": round(threshold, 4), "recall": round(recall, 4)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", required=True)
    ap.add_argument("--baseline", required=True)
    ap.add_argument("--fps", type=float, default=3.0)
    ap.add_argument("--out", default="algo/experiments/calibrate_result.json")
    args = ap.parse_args()

    video = Path(args.video)
    gt = parse_baseline(Path(args.baseline), video.stem)
    print(f"标注 {len(gt)} 段，覆盖 {sum((b - a) / 1000 for a, b, _ in gt):.1f} s")

    cap = cv2.VideoCapture(str(video))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    ok, ref_frame = cap.read()
    cap.release()
    if not ok:
        raise SystemExit("读不到视频首帧")

    step = max(1, int(round(fps / args.fps)))
    detectors = []
    for cls in ALL_DETECTORS:
        det = cls()
        det.visual_enabled = False
        if det.requires_reference:
            det.set_reference(ref_frame)
        detectors.append(det)

    neg: Dict[str, List[float]] = {d.name: [] for d in detectors}
    pos: Dict[str, List[float]] = {d.name: [] for d in detectors}
    skipped: Dict[str, int] = {d.name: 0 for d in detectors}
    frames = 0
    pos_frames = 0

    for item in iter_frames(video, step=step, max_frames=100000):
        frame, ts = item.image, item.timestamp_ms
        frames += 1
        is_pos = any(start <= ts <= end for start, end, _ in gt)
        pos_frames += 1 if is_pos else 0
        # 前 30 帧各算法还在建基线（edge 历史窗口、shortlong 长池），不计入统计
        warming = frames <= 30
        for det in detectors:
            result = det.process(frame)
            if result.skipped or warming:
                skipped[det.name] += 1
                continue
            field = METRICS[det.name][0]
            value = result.detail.get(field)
            if value is None:
                skipped[det.name] += 1
                continue
            (pos if is_pos else neg)[det.name].append(float(value))

    report = {
        "video": video.name,
        "fps": fps,
        "sampledFrames": frames,
        "positiveFrames": pos_frames,
        "negativeFrames": frames - pos_frames,
        "algorithms": {},
    }

    print(f"\n采样 {frames} 帧（3fps 等价），其中标注内 {pos_frames} 帧\n")
    header = f"{'算法':<12}{'主指标':<20}{'正常p50':>10}{'p90':>9}{'p95':>9}{'p99':>9}"
    header += f"{'篡改p50':>10}{'p95':>9}  {'保守(2%)':>9}{'均衡(5%)':>9}{'灵敏(10%)':>10}"
    print(header)
    print("-" * len(header))

    for det in detectors:
        name = det.name
        field, direction, param = METRICS[name]
        n = neg[name]
        p = pos[name]
        nq = quantiles(n, [50, 90, 95, 99])
        pq = quantiles(p, [50, 95])
        rows = {
            "field": field,
            "direction": direction,
            "param": param,
            "negative": nq,
            "positive": pq,
            "skipped": skipped[name],
            "samples": {"neg": len(n), "pos": len(p)},
            "presets": {
                "conservative": recommend(n, p, direction, 2.0),
                "balanced": recommend(n, p, direction, 5.0),
                "sensitive": recommend(n, p, direction, 10.0),
            },
        }
        report["algorithms"][name] = rows
        print(
            f"{name:<12}{field:<20}{nq['50']:>10.2f}{nq['90']:>9.2f}{nq['95']:>9.2f}{nq['99']:>9.2f}"
            f"{pq['50']:>10.2f}{pq['95']:>9.2f}  "
            f"{rows['presets']['conservative']['threshold']:>9.3f}"
            f"{rows['presets']['balanced']['threshold']:>9.3f}"
            f"{rows['presets']['sensitive']['threshold']:>10.3f}"
        )

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n结果已写入 {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
