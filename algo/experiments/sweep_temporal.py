"""短/长期直方图法（shortlong）的参数扫描。

动机：原实现（短池 15 / 长池 36 / 聚合=中位数 / 阈值 1.0）在 FLAD 的 1~2 秒级事件上
d_norm 峰值只有 0.47，**从未越过阈值**。先判定这是"阈值没调好"还是"池设计本身对短事件不敏感"，
再决定要不要把它改进后集成。

做法：每个时间窗只解码一次，直方图只算一次，然后并行喂给多组配置，
统计每个窗口的 max(d_norm)，最后对每组配置扫阈值取最优 F1。

用法：
    python algo/experiments/sweep_temporal.py --video ... --baseline ... --negatives 25
"""

from __future__ import annotations

import argparse
import json
import math
from collections import deque
from pathlib import Path
from typing import Dict, List, Sequence, Tuple

import cv2
import numpy as np

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from eval_candidates import parse_baseline, sample_windows  # noqa: E402
from media import iter_frames  # noqa: E402

AGGREGATORS = {
    "median": np.median,
    "p75": lambda values: float(np.percentile(values, 75)),
    "max": np.max,
}


class Config:
    """一组短/长期池配置。"""

    def __init__(self, short: int, long: int, agg: str):
        self.short = deque(maxlen=short)
        self.long = deque(maxlen=long)
        self.short_size = short
        self.long_size = long
        self.agg_name = agg
        self.agg = AGGREGATORS[agg]
        self.name = f"s{short}_l{long}_{agg}"

    def update(self, hist: np.ndarray) -> float:
        self.short.append(hist)
        if len(self.short) == self.short.maxlen:
            self.long.append(self.short[0])
        if len(self.long) < 5:
            return 0.0
        a = np.asarray(self.short)
        b = np.asarray(self.long)
        d_between = self.agg(np.abs(a[:, None, :] - b[None, :, :]).sum(axis=2).ravel())
        c = b
        diffs = np.abs(c[:, None, :] - c[None, :, :]).sum(axis=2)
        if len(c) > 1:
            np.fill_diagonal(diffs, np.nan)
        d_long = self.agg(diffs[~np.isnan(diffs)])
        return math.log((float(d_between) + 1e-6) / (float(d_long) + 1e-6))


def histogram(frame: np.ndarray) -> np.ndarray:
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    chroma = cv2.normalize(cv2.calcHist([hsv], [0, 1], None, [50, 60], [0, 180, 0, 256]),
                           None).flatten()
    l1 = np.sum(frame, axis=2).astype(np.float32)
    rng = (np.amax(frame, axis=2) - np.amin(frame, axis=2)).astype(np.float32)
    l1r = cv2.normalize(cv2.calcHist([np.stack((l1, rng), axis=-1)], [0, 1], None,
                                     [8, 8], [0, 256, 0, 256]), None).flatten()
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    gx = cv2.Sobel(gray, cv2.CV_64F, 1, 0, ksize=3)
    gy = cv2.Sobel(gray, cv2.CV_64F, 0, 1, ksize=3)
    grad = cv2.normalize(cv2.calcHist([np.arctan2(gy, gx).astype(np.float32)], [0], None,
                                      [16], [-np.pi, np.pi]), None).flatten()
    return np.concatenate((chroma, l1r, grad)).astype(np.float32)


def run_window(configs: Sequence[Config], path: Path, step: int,
               start_ms: int, end_ms: int, warmup_ms: int) -> List[float]:
    peak = [0.0] * len(configs)
    warm_from = max(0, start_ms - warmup_ms)
    for item in iter_frames(path, step=step, max_frames=200000,
                            start_ms=warm_from, end_ms=end_ms):
        hist = histogram(item.image)
        if item.timestamp_ms < start_ms:
            for cfg in configs:
                cfg.update(hist)
            continue
        for idx, cfg in enumerate(configs):
            value = cfg.update(hist)
            if value > peak[idx]:
                peak[idx] = value
    return peak


def best_threshold(pos: List[float], neg: List[float]) -> Tuple[float, float, float, float]:
    """扫阈值取最优 F1（pos = 正样本峰值，neg = 负样本峰值）。

    只测标注片段（``neg`` 为空）时没有误报可算，退化为"取正样本 25 分位作为工作点"，
    报告该工作点下的召回——用于判断"这个配置能不能把多数标注段点着"，
    误报率放到整库评测里再谈。
    """
    if not neg:
        if not pos:
            return 0.0, 0.0, 0.0, 0.0
        working = float(np.percentile(pos, 25))
        recall = sum(1 for v in pos if v > working) / len(pos)
        return working, 0.0, recall, recall
    candidates = sorted(set(pos + neg))
    best = (0.0, 0.0, 0.0, 0.0)  # threshold, P, R, F1
    for t in candidates:
        tp = sum(1 for v in pos if v > t)
        fp = sum(1 for v in neg if v > t)
        if tp == 0:
            continue
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / len(pos)
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        if f1 > best[3]:
            best = (t, precision, recall, f1)
    return best


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", required=True)
    ap.add_argument("--baseline", required=True)
    ap.add_argument("--fps", type=float, default=3.0)
    ap.add_argument("--warmup-ms", type=int, default=12000)
    ap.add_argument("--negatives", type=int, default=0,
                    help="负样本窗口数；0 = 只测标注片段（默认）")
    args = ap.parse_args()

    video = Path(args.video)
    gt = parse_baseline(Path(args.baseline), video.stem)
    cap = cv2.VideoCapture(str(video))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    duration = cap.get(cv2.CAP_PROP_FRAME_COUNT) / fps * 1000
    cap.release()
    step = max(1, int(round(fps / args.fps)))
    negatives = sample_windows(gt, int(duration), args.negatives, 10_000)

    grid = [(s, l, a) for s in (3, 5, 15) for l in (30, 60) for a in ("median", "p75")]
    configs = [Config(s, l, a) for s, l, a in grid]
    names = [c.name for c in configs]

    pos_peaks: List[List[float]] = []
    neg_peaks: List[List[float]] = []
    for start, end, _kind in gt:
        for cfg in configs:
            cfg.__init__(cfg.short_size, cfg.long_size, cfg.agg_name)
        pos_peaks.append(run_window(configs, video, step, start, end, args.warmup_ms))
    for start, end in negatives:
        for cfg in configs:
            cfg.__init__(cfg.short_size, cfg.long_size, cfg.agg_name)
        neg_peaks.append(run_window(configs, video, step, start, end, args.warmup_ms))

    rows = []
    for idx, name in enumerate(names):
        pos = [p[idx] for p in pos_peaks]
        neg = [n[idx] for n in neg_peaks]
        t, precision, recall, f1 = best_threshold(pos, neg)
        rows.append({"config": name, "bestThreshold": round(t, 3),
                     "precision": round(precision, 3), "recall": round(recall, 3),
                     "f1": round(f1, 3),
                     "posMaxMedian": round(float(np.median(pos)), 3),
                     "negMaxMedian": round(float(np.median(neg)), 3)})
    rows.sort(key=lambda r: -r["f1"])
    print(f"{'config':<20}{'thr':>6}{'P':>7}{'R':>7}{'F1':>7}{'pos中位':>10}{'neg中位':>10}")
    for r in rows:
        print(f"{r['config']:<20}{r['bestThreshold']:>6.2f}{r['precision']:>7.2f}"
              f"{r['recall']:>7.2f}{r['f1']:>7.2f}{r['posMaxMedian']:>10.2f}{r['negMaxMedian']:>10.2f}")

    out = Path(__file__).with_name("sweep_temporal_result.json")
    out.write_text(json.dumps({"rows": rows, "posPeaks": pos_peaks, "negPeaks": neg_peaks},
                              indent=2), encoding="utf-8")
    print("\n结果写入", out)


if __name__ == "__main__":
    main()
