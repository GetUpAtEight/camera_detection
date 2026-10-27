"""验证「学习期自动标定」的效果：同一段素材，标定前 vs 标定后。

用户实测反馈是「5 个经典算法帧帧报警、3 个辅助算法一次不报」，根因是默认阈值来自
合成样本，与真实监控画面的正常波动差 3~4 倍。本脚本用 samples/demo_video.mp4
（0~49 正常 / 50~89 失焦 / 90~129 遮挡）分别跑两遍：

* baseline：用算法层默认参数；
* calibrated：建会话后先发 20 帧「探针帧」（probe=true，不判定不计帧），
  按各算法 primaryMetric 的 p95/p5 回填阈值，PATCH 到会话，再跑同一段。

输出每段的告警率、整体准确率，以及标定后的阈值，用于文档里的前后对比数字。

用法：
    .venv/Scripts/python.exe deploy/live_calib_test.py
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import cv2
import numpy as np
import requests

BASE = "http://127.0.0.1:8080"

ALL_ALGOS = [
    "laplacian",
    "entropy",
    "scene_diff",
    "orb",
    "edge",
    "mog2_area",
    "frame_area",
    "shortlong",
]
# 与 AnalysisService.isVotingAlgorithm 保持一致：只有主力算法参与集成投票
VOTING = {"laplacian", "entropy", "scene_diff", "orb", "edge"}

SENSITIVITY = {
    "conservative": {"higher": 1.6, "lower": 0.65},
    "balanced": {"higher": 1.25, "lower": 0.8},
    "sensitive": {"higher": 0.95, "lower": 0.9},
}


def encode(frame) -> bytes:
    ok, buf = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 88])
    if not ok:
        raise SystemExit("JPEG 编码失败")
    return buf.tobytes()


def jitter_frames(frames, scale: float, seed: int = 7):
    """给画面加上"真实摄像头"的噪声：传感器噪声 + 自动曝光/自动白平衡漂移 + 轻微抖动。

    合成素材本身太干净（正常段几乎是静止的），默认阈值在上面表现很好，
    复现不出用户实测时的"帧帧报警"。真实 USB/网络摄像头每帧都有这几个扰动，
    这正是默认阈值失效的原因。
    """
    if scale <= 0:
        return frames
    rng = np.random.default_rng(seed)
    out = []
    for i, frame in enumerate(frames):
        img = frame.astype(np.float32)
        # 曝光漂移：整体亮度按低频正弦变化 ±6%
        img *= 1.0 + 0.06 * scale * np.sin(i * 0.35)
        # 自动白平衡：各通道略微独立漂移
        for c in range(3):
            img[:, :, c] *= 1.0 + 0.03 * scale * np.sin(i * 0.21 + c)
        # 传感器噪声
        img += rng.normal(0.0, 3.0 * scale, img.shape)
        # 手持/云台微抖：亚像素级平移
        dx = 1.2 * scale * np.sin(i * 0.6)
        dy = 1.2 * scale * np.cos(i * 0.45)
        img = cv2.warpAffine(img, np.float32([[1, 0, dx], [0, 1, dy]]), (frame.shape[1], frame.shape[0]),
                             flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
        out.append(np.clip(img, 0, 255).astype(np.uint8))
    return out


def read_frames(path: Path):
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise SystemExit(f"打不开视频：{path}")
    out = []
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        out.append(frame)
    cap.release()
    return out


def percentile(values, p: float) -> float:
    if not values:
        return float("nan")
    s = sorted(values)
    idx = (p / 100) * (len(s) - 1)
    lo, hi = int(idx), min(len(s) - 1, int(idx) + 1)
    if lo == hi:
        return s[lo]
    return s[lo] + (s[hi] - s[lo]) * (idx - lo)


def fetch_meta() -> dict:
    r = requests.get(f"{BASE}/api/algo/algorithms", timeout=30)
    data = r.json()["data"]
    rows = data["algorithms"] if isinstance(data, dict) else data
    return {row["name"]: row for row in rows}


def create_session(reference: bytes, algos, params, streak: int) -> str:
    r = requests.post(
        f"{BASE}/api/live/sessions",
        files={"reference": ("ref.jpg", reference, "image/jpeg")},
        data={
            "algorithms": json.dumps(algos),
            "params": json.dumps(params),
            "requiredStreak": str(streak),
        },
        timeout=60,
    )
    r.raise_for_status()
    return r.json()["data"]["sessionId"]


def send_frame(sid: str, frame: bytes, probe: bool = False) -> dict:
    r = requests.post(
        f"{BASE}/api/live/sessions/{sid}/frame",
        files={"frame": ("f.jpg", frame, "image/jpeg")},
        data={"probe": "true" if probe else "false"},
        timeout=120,
    )
    r.raise_for_status()
    return r.json()["data"]


def patch_params(sid: str, params) -> None:
    r = requests.patch(
        f"{BASE}/api/live/sessions/{sid}/params",
        data={"params": json.dumps(params)},
        timeout=30,
    )
    r.raise_for_status()


def delete_session(sid: str) -> None:
    requests.delete(f"{BASE}/api/live/sessions/{sid}", params={"purge": "true"}, timeout=30)


def run_pass(frames, ref, algos, params, streak: int, label: str):
    """跑一遍完整流，返回每帧结果。"""
    sid = create_session(ref, algos, params, streak)
    rows = []
    try:
        for i, frame in enumerate(frames):
            res = send_frame(sid, encode(frame))
            rows.append(
                {
                    "index": i,
                    "alarm": res["alarm"],
                    "tampered": res["frameTampered"],
                    "votes": res["votes"],
                    "type": res["tamperType"],
                    "algorithms": {
                        a["algorithm"]: a["tampered"] for a in res["algorithms"] if not a.get("skipped")
                    },
                }
            )
            if (i + 1) % 20 == 0:
                print(f"  [{label}] {i + 1}/{len(frames)} 帧", flush=True)
    finally:
        delete_session(sid)
    return rows


def learn(frames, ref, algos, params, streak: int, meta: dict, learn_n: int, sensitivity: str):
    """模拟前端学习期：探针帧采样 → 按分位数回填阈值。"""
    sid = create_session(ref, algos, params, streak)
    samples: dict[str, list[float]] = {}
    used = 0
    try:
        for i in range(learn_n):
            res = send_frame(sid, encode(frames[i]), probe=True)
            used += 1
            for a in res["algorithms"]:
                if a.get("skipped"):
                    continue
                pm = meta.get(a["algorithm"], {}).get("primaryMetric")
                if not pm:
                    continue
                value = (a.get("detail") or {}).get(pm["field"])
                if isinstance(value, (int, float)):
                    samples.setdefault(a["algorithm"], []).append(float(value))
    finally:
        delete_session(sid)

    factor = SENSITIVITY[sensitivity]
    calibrated = {k: dict(v) for k, v in params.items()}
    applied = {}
    for name, values in samples.items():
        pm = meta.get(name, {}).get("primaryMetric")
        if not pm or len(values) < 6:
            continue
        base = percentile(values, 95 if pm["direction"] == "higher" else 5)
        target = base * (factor["higher"] if pm["direction"] == "higher" else factor["lower"])
        spec = next((p for p in meta[name].get("params", []) if p["key"] == pm["param"]), None)
        if spec:
            target = min(spec["max"], max(spec["min"], target))
        calibrated.setdefault(name, {})[pm["param"]] = round(target, 3)
        applied[name] = {
            "field": pm["field"],
            "base": round(base, 3),
            "before": params.get(name, {}).get(pm["param"]),
            "after": round(target, 3),
        }
    return calibrated, applied, used


def summarize(rows, label: str) -> dict:
    normal = [r for r in rows if r["index"] < 50]
    blur = [r for r in rows if 50 <= r["index"] < 90]
    covered = [r for r in rows if r["index"] >= 90]
    tampered = blur + covered

    def rate(sub, key="alarm"):
        return (sum(1 for r in sub if r[key]) / len(sub)) if sub else 0.0

    fp = sum(1 for r in normal if r["alarm"])
    fn = sum(1 for r in tampered if not r["alarm"])
    per_algo = {}
    for name in ALL_ALGOS:
        if not any(name in r["algorithms"] for r in rows):
            continue
        per_algo[name] = {
            "normal": sum(1 for r in normal if r["algorithms"].get(name)) / max(1, len(normal)),
            "blur": sum(1 for r in blur if r["algorithms"].get(name)) / max(1, len(blur)),
            "covered": sum(1 for r in covered if r["algorithms"].get(name)) / max(1, len(covered)),
        }
    return {
        "label": label,
        "frames": len(rows),
        "normalAlarmRate": round(rate(normal), 4),
        "blurAlarmRate": round(rate(blur), 4),
        "coveredAlarmRate": round(rate(covered), 4),
        "accuracy": round((len(normal) - fp + len(tampered) - fn) / len(rows), 4),
        "fp": fp,
        "fn": fn,
        "perAlgoFireRate": {k: {kk: round(vv, 4) for kk, vv in v.items()} for k, v in per_algo.items()},
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", default="samples/demo_video.mp4")
    ap.add_argument("--streak", type=int, default=2)
    ap.add_argument("--learn-frames", type=int, default=20)
    ap.add_argument("--sensitivity", default="balanced", choices=list(SENSITIVITY))
    ap.add_argument("--algorithms", default=",".join(ALL_ALGOS))
    ap.add_argument("--jitter", type=float, default=0.0,
                    help="模拟真实摄像头的噪声/曝光漂移/微抖强度，0 表示不加")
    ap.add_argument("--out", default="deploy/live_calib_result.json")
    args = ap.parse_args()

    frames = jitter_frames(read_frames(Path(args.video)), args.jitter)
    if len(frames) < 60:
        raise SystemExit(f"视频太短（{len(frames)} 帧），至少需要 60 帧")
    algos = [a.strip() for a in args.algorithms.split(",") if a.strip()]
    meta = fetch_meta()
    missing = [a for a in algos if a not in meta]
    if missing:
        raise SystemExit(f"算法层不认识这些算法：{missing}")
    print(f"素材 {args.video}：{len(frames)} 帧；算法 {len(algos)} 个；去抖 {args.streak} 帧；模拟抖动 {args.jitter}")

    ref = encode(frames[0])
    base_params: dict = {}

    print("\n[1/3] 标定前（算法层默认参数）…")
    before_rows = run_pass(frames, ref, algos, base_params, args.streak, "before")
    before = summarize(before_rows, "默认参数")

    print("\n[2/3] 学习期：采样正常画面并回填阈值…")
    calibrated, applied, used = learn(
        frames, ref, algos, base_params, args.streak, meta, args.learn_frames, args.sensitivity
    )
    print(f"  采样 {used} 帧，回填 {len(applied)} 个算法：")
    for name, item in applied.items():
        print(f"    {name:11s} {item['field']:17s} p-base={item['base']:<9} "
              f"{item['before']} -> {item['after']}")

    print("\n[3/3] 标定后…")
    after_rows = run_pass(frames, ref, algos, calibrated, args.streak, "after")
    after = summarize(after_rows, "标定后")

    report = {
        "video": args.video,
        "streak": args.streak,
        "learnFrames": used,
        "sensitivity": args.sensitivity,
        "jitter": args.jitter,
        "algorithms": algos,
        "before": before,
        "after": after,
        "calibration": applied,
        "params": calibrated,
    }
    Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n" + "=" * 62)
    print(f"{'':14s}{'正常段告警率':>12s}{'失焦段告警率':>13s}{'遮挡段告警率':>13s}{'准确率':>9s}")
    for item in (before, after):
        print(f"{item['label']:14s}{item['normalAlarmRate']:>12.2%}"
              f"{item['blurAlarmRate']:>13.2%}{item['coveredAlarmRate']:>13.2%}{item['accuracy']:>9.2%}")
    print("=" * 62)
    print("\n各算法触发率（标定后）：正常 / 失焦 / 遮挡")
    for name, r in after["perAlgoFireRate"].items():
        tag = "" if name in VOTING else "  (辅助，不投票)"
        print(f"  {name:11s} {r['normal']:>6.1%} / {r['blur']:>6.1%} / {r['covered']:>6.1%}{tag}")
    print(f"\n明细已写入 {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
