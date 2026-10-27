"""对 G:/project/算法 下的候选算法做离线评估（不接入服务，只在本地视频上量指标）。

评估对象：
1. ``mog2_area``   —— Camera-Tampering-Detection-using-Opencv-Python：MOG2 前景面积占比
2. ``frame_area``  —— CCTV-Camera-Tampering-Detection：帧差 + 最大连通域面积 > 40%
3. ``shortlong``   —— CameraTamperingDetection（cpu/gpu/video 三份同算法）：
                      彩色度直方图 + L1R 直方图 + 梯度方向直方图，短/长期池差异 d_norm = ln(d_between/d_long)

评估口径与主系统一致：
* 采样网格按**全局帧号** `index % step == 0`，3 fps；
* 每段先做 ``warmup_ms`` 预热（喂帧但不计结果），解决时序算法冷启动；
* 正样本 = FLAD 标注的 20 段（任一段内出现告警即算检出）；
* 负样本 = 标注之外随机抽 40 个 10s 窗口（出现告警即算误报）。

用法：
    python algo/experiments/eval_candidates.py --video "C:/Users/yzn/Downloads/archive/Videos/video1.mp4" \
        --baseline "C:/Users/yzn/Downloads/archive/Survey/baseline.py"
"""

from __future__ import annotations

import argparse
import json
import math
import random
import re
import statistics
import sys
import time
from collections import deque
from pathlib import Path
from typing import Dict, List, Sequence, Tuple

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from media import iter_frames  # noqa: E402

# ----------------------------------------------------------------- 标注解析


TYPE_ALIAS = {
    "occlusion": "covered",
    "covered": "covered",
    "bluriness": "defocused",
    "blur": "defocused",
    "defocused": "defocused",
    "jitter": "moved",
    "moved": "moved",
}


def parse_baseline(path: Path, stem: str) -> List[Tuple[int, int, str]]:
    """解析 FLAD 的 baseline.py：``videoN = {"occlusion": [["mm:ss","mm:ss"], ...], ...}``。

    类型名字段（而不是 ``"type": [...]``）——这一点与后端的 ``GroundTruthService`` 保持一致。
    """
    text = path.read_text(encoding="utf-8", errors="ignore")
    blocks = re.split(r"^\s*(video\d+)\s*=\s*\{", text, flags=re.M)
    for i in range(1, len(blocks), 2):
        if blocks[i] != stem:
            continue
        body = blocks[i + 1].split("\n}")[0]
        out: List[Tuple[int, int, str]] = []
        for key, chunk in re.findall(r'"([a-zA-Z]+)"\s*:\s*\[(.*?)\]\s*(?=,\s*"|\Z)', body, flags=re.S):
            tamper = TYPE_ALIAS.get(key.lower())
            if tamper is None:
                continue
            for a, b in re.findall(r'\[\s*"([\d:.]+)"\s*,\s*"([\d:.]+)"\s*\]', chunk):
                start = _clock(a)
                out.append((start, max(_clock(b), start + 500), tamper))
        out.sort(key=lambda item: item[0])
        return out
    raise SystemExit(f"baseline 里没有 {stem} 块")


def _clock(value: str) -> int:
    parts = [float(p) for p in value.split(":")]
    seconds = 0.0
    for p in parts:
        seconds = seconds * 60 + p
    return int(seconds * 1000)


# ----------------------------------------------------------------- 候选算法


class Mog2Foreground:
    """MOG2 前景面积占比（原仓库 camera_tampering_detection.py 的思路，修掉了 cv2.5 的 API 差异）。"""

    name = "mog2_area"

    def __init__(self, ratio: float = 0.33):
        self.bg = cv2.createBackgroundSubtractorMOG2(history=300, varThreshold=16, detectShadows=False)
        self.kernel = np.ones((5, 5), np.uint8)
        self.ratio = ratio

    def update(self, frame: np.ndarray) -> Tuple[bool, float, Dict]:
        mask = self.bg.apply(frame)
        mask = cv2.erode(mask, self.kernel, iterations=2)
        mask = cv2.dilate(mask, self.kernel, iterations=2)
        foreground = float(np.count_nonzero(mask)) / mask.size
        return foreground > self.ratio, foreground, {"foregroundRatio": foreground}


class FrameAreaDiff:
    """帧差 + 最大连通域面积 / 全图（原仓库 tamper_detect.py 的思路）。"""

    name = "frame_area"

    def __init__(self, diff_thresh: int = 30, area_ratio: float = 0.4):
        self.prev = None
        self.diff_thresh = diff_thresh
        self.area_ratio = area_ratio
        self.k4 = np.ones((4, 4), np.uint8)
        self.k9 = np.ones((9, 9), np.uint8)

    def update(self, frame: np.ndarray) -> Tuple[bool, float, Dict]:
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        if self.prev is None:
            self.prev = gray
            return False, 0.0, {"changedRatio": 0.0, "largestBlobRatio": 0.0}
        diff = cv2.absdiff(gray, self.prev)
        _, mask = cv2.threshold(diff, self.diff_thresh, 255, cv2.THRESH_BINARY)
        mask = cv2.erode(mask, self.k4, iterations=1)
        mask = cv2.dilate(mask, self.k9, iterations=3)
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        largest = max((cv2.contourArea(c) for c in contours), default=0.0)
        changed = float(np.count_nonzero(mask)) / mask.size
        blob = largest / float(mask.size)
        self.prev = gray
        return blob > self.area_ratio, blob, {"changedRatio": changed, "largestBlobRatio": blob}


class ShortLongHistogram:
    """短/长期直方图池差异（Sitara & Mehtre 的 camera tampering 方法）。

    原仓库的 gpu 版本用 cupy，这里统一用 numpy——池子只有 15×36 对、特征 256 维，
    单帧几千次减法，numpy 完全够；cupy 只是把一个 0.2ms 的操作变成 0.1ms，却多一个重依赖。

    修掉原实现的几个坑：
    * ``d_long`` 可能为 0（静止画面）→ 用 eps 兜住，避免 ln(inf)；
    * 长期池自比较**排除对角线**，否则 36 个 0 会把中位数整体拉低；
    * 不再每帧 ``print``、不再每帧画 matplotlib。
    """

    name = "shortlong"

    def __init__(self, short_size: int = 15, long_size: int = 36, threshold: float = 1.0):
        self.short = deque(maxlen=short_size)
        self.long = deque(maxlen=long_size)
        self.threshold = threshold
        self.bins = (50, 60)

    # ---------------------------------------------------------- 特征
    def _hist(self, frame: np.ndarray) -> np.ndarray:
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        chroma = cv2.calcHist([hsv], [0, 1], None, list(self.bins), [0, 180, 0, 256])
        chroma = cv2.normalize(chroma, chroma).flatten()

        l1 = np.sum(frame, axis=2).astype(np.float32)
        rng = (np.amax(frame, axis=2) - np.amin(frame, axis=2)).astype(np.float32)
        stacked = np.stack((l1, rng), axis=-1)
        l1r = cv2.calcHist([stacked], [0, 1], None, [8, 8], [0, 256, 0, 256])
        l1r = cv2.normalize(l1r, l1r).flatten()

        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        gx = cv2.Sobel(gray, cv2.CV_64F, 1, 0, ksize=3)
        gy = cv2.Sobel(gray, cv2.CV_64F, 0, 1, ksize=3)
        direction = np.arctan2(gy, gx).astype(np.float32)
        grad = cv2.calcHist([direction], [0], None, [16], [-np.pi, np.pi])
        grad = cv2.normalize(grad, grad).flatten()

        return np.concatenate((chroma, l1r, grad))

    @staticmethod
    def _pairwise_median(p1: Sequence[np.ndarray], p2: Sequence[np.ndarray],
                         skip_self: bool = False) -> float:
        a = np.asarray(p1)
        b = np.asarray(p2)
        diffs = np.abs(a[:, None, :] - b[None, :, :]).sum(axis=2)
        if skip_self and len(a) == len(b):
            np.fill_diagonal(diffs, np.nan)
            return float(np.nanmedian(diffs))
        return float(np.median(diffs))

    # ---------------------------------------------------------- 时序
    def update(self, frame: np.ndarray) -> Tuple[bool, float, Dict]:
        self.short.append(self._hist(frame))
        if len(self.short) == self.short.maxlen:
            # 原实现在短池满后每帧把最旧的一帧挪进长池
            self.long.append(self.short[0])

        if len(self.long) < 8:
            return False, 0.0, {"dNorm": 0.0, "dBetween": 0.0, "dLong": 0.0,
                                "warm": len(self.long), "warmup": True}

        d_between = self._pairwise_median(self.short, self.long)
        d_long = self._pairwise_median(self.long, self.long, skip_self=True)
        d_norm = math.log((d_between + 1e-6) / (d_long + 1e-6))
        return d_norm > self.threshold, d_norm, {
            "dNorm": d_norm, "dBetween": d_between, "dLong": d_long,
            "warm": len(self.long), "warmup": False,
        }


# ----------------------------------------------------------------- 评估


def sample_windows(gt, duration_ms: int, count: int, length_ms: int, seed: int = 7):
    """在标注之外随机取负样本窗口（避开所有标注区间 ±2s）。"""
    rng = random.Random(seed)
    out = []
    guard = 2000
    tries = 0
    while len(out) < count and tries < count * 50:
        tries += 1
        start = rng.randrange(5_000, max(6_000, duration_ms - length_ms - 5_000))
        end = start + length_ms
        if any(not (end + guard < a or start - guard > b) for a, b, _ in gt):
            continue
        out.append((start, end))
    return out


def run_window(detector, path: Path, fps: float, step: int, start_ms: int, end_ms: int,
               warmup_ms: int) -> Dict:
    """跑一个时间窗：先预热，再逐帧判定，返回告警帧列表与分数。

    ``detector`` 由调用方每次新建，保证各段之间状态隔离（与主系统"每段独立检测器"一致）。
    """
    warm_from = max(0, start_ms - warmup_ms)
    scores = []
    alarms = 0
    frames = 0
    max_score = 0.0
    for item in iter_frames(path, step=step, max_frames=100000,
                            start_ms=warm_from, end_ms=end_ms):
        if item.timestamp_ms < start_ms:
            detector.update(item.image)          # 预热：只喂不判定
            continue
        fired, value, detail = detector.update(item.image)
        frames += 1
        scores.append(value)
        max_score = max(max_score, value)
        if fired:
            alarms += 1
    return {"frames": frames, "alarms": alarms, "maxScore": max_score,
            "detected": alarms > 0, "scores": scores}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", required=True)
    ap.add_argument("--baseline", required=True)
    ap.add_argument("--fps", type=float, default=3.0)
    ap.add_argument("--warmup-ms", type=int, default=20000)
    ap.add_argument("--negatives", type=int, default=30)
    args = ap.parse_args()

    video = Path(args.video)
    stem = video.stem
    gt = parse_baseline(Path(args.baseline), stem)

    cap = cv2.VideoCapture(str(video))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    duration = cap.get(cv2.CAP_PROP_FRAME_COUNT) / fps * 1000
    cap.release()
    step = max(1, int(round(fps / args.fps)))
    print(f"视频 {video.name} {duration/1000:.1f}s fps={fps:.1f} step={step} 标注 {len(gt)} 段")

    negatives = sample_windows(gt, int(duration), args.negatives, 10_000)
    candidates = {
        "mog2_area": Mog2Foreground,
        "frame_area": FrameAreaDiff,
        "shortlong": ShortLongHistogram,
    }

    report = {}
    for name, cls in candidates.items():
        t0 = time.time()
        tp = 0
        per_segment = []
        for start, end, kind in gt:
            det = cls()
            res = run_window(det, video, fps, step, start, end, args.warmup_ms)
            tp += 1 if res["detected"] else 0
            per_segment.append({"startMs": start, "endMs": end, "type": kind,
                                "frames": res["frames"], "alarms": res["alarms"],
                                "detected": res["detected"], "max": round(res["maxScore"], 3)})
        fp = 0
        for start, end in negatives:
            det = cls()
            res = run_window(det, video, fps, step, start, end, args.warmup_ms)
            fp += 1 if res["detected"] else 0

        recall = tp / len(gt)
        precision = tp / (tp + fp) if tp + fp else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        span = time.time() - t0
        report[name] = {
            "truePositive": tp, "falseNegative": len(gt) - tp,
            "falsePositive": fp, "negatives": len(negatives),
            "precision": round(precision, 3), "recall": round(recall, 3), "f1": round(f1, 3),
            "seconds": round(span, 1), "perSegment": per_segment,
        }
        print(f"\n[{name}] TP={tp}/{len(gt)} FP={fp}/{len(negatives)} "
              f"P={precision:.2f} R={recall:.2f} F1={f1:.2f} 用时 {span:.0f}s")
        missed = [(s["startMs"], s["type"]) for s in per_segment if not s["detected"]]
        if missed:
            print("   漏检:", ", ".join(f"{m/1000:.1f}s/{k}" for m, k in missed))

    out = Path(__file__).with_name("eval_candidates_result.json")
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print("\n结果写入", out)


if __name__ == "__main__":
    main()
