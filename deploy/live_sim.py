"""实时监控链路的离线联调脚本（不需要摄像头）。

用一段合成视频模拟"摄像头"：第 0 帧当参考基准，之后按 stride 抽帧逐帧提交，
验证会话创建 → 单帧检测 → 连续告警去抖 → 告警帧留存 → 重设基准 → 关闭会话。

用法：
    python deploy/live_sim.py --video algo/tests/_out/synthetic.mp4 --stride 2
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import cv2
import requests

BASE = "http://127.0.0.1:8080"


def encode(frame) -> bytes:
    ok, buf = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 88])
    if not ok:
        raise SystemExit("JPEG 编码失败")
    return buf.tobytes()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", default="algo/tests/_out/synthetic.mp4")
    ap.add_argument("--stride", type=int, default=2)
    ap.add_argument("--streak", type=int, default=2)
    ap.add_argument("--algorithms", default="laplacian,entropy,scene_diff,orb,edge")
    args = ap.parse_args()

    video = Path(args.video)
    if not video.exists():
        raise SystemExit(f"视频不存在：{video}")

    cap = cv2.VideoCapture(str(video))
    frames = []
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        frames.append(frame)
    cap.release()
    if not frames:
        raise SystemExit("视频没有帧")

    session = requests.Session()
    algs = args.algorithms.split(",")
    print(f"视频 {video.name}：{len(frames)} 帧，每 {args.stride} 帧提交一次，去抖 {args.streak} 帧")

    resp = session.post(
        f"{BASE}/api/live/sessions",
        files={"reference": ("reference.jpg", encode(frames[0]), "image/jpeg")},
        data={
            "algorithms": json.dumps(algs),
            "params": "{}",
            "requiredStreak": str(args.streak),
        },
        timeout=60,
    )
    body = resp.json()
    if body.get("code") != 0:
        raise SystemExit(f"创建会话失败：{body}")
    info = body["data"]
    sid = info["sessionId"]
    print(f"会话 {sid[:8]} 已创建，参考帧 {info['referenceUrl']}")

    ref = session.get(f"{BASE}{info['referenceUrl']}", timeout=30)
    print(f"参考帧可访问：{ref.status_code}，{len(ref.content)} 字节")

    alarm_frames = []
    tampered_frames = []
    print(f"\n{'#':>3} {'alarm':>6} {'tamper':>7} {'votes':>6} {'type':>10}  判定")
    try:
        for idx in range(0, len(frames), args.stride):
            payload = session.post(
                f"{BASE}/api/live/sessions/{sid}/frame",
                files={"frame": ("frame.jpg", encode(frames[idx]), "image/jpeg")},
                timeout=60,
            )
            data = payload.json()
            if data.get("code") != 0:
                raise SystemExit(f"第 {idx} 帧失败：{data}")
            row = data["data"]
            if row["frameTampered"]:
                tampered_frames.append(row["frameIndex"])
            if row["alarm"]:
                alarm_frames.append(row["frameIndex"])
            print(
                f"{row['frameIndex']:>3} {str(row['alarm']):>6} {str(row['frameTampered']):>7} "
                f"{row['votes']:>6} {row['tamperType']:>10}  {row['verdict']}"
            )
        last = row
    finally:
        # 检查告警帧留存
        if alarm_frames:
            probe = session.get(
                f"{BASE}/api/live/sessions/{sid}/alarms/{alarm_frames[0]}", timeout=30
            )
            print(f"\n告警帧留存：{probe.status_code}，{len(probe.content)} 字节")
        # 重设基准（用最后一帧），验证连续计数清零
        reset = session.post(
            f"{BASE}/api/live/sessions/{sid}/reference",
            files={"reference": ("reference.jpg", encode(frames[-1]), "image/jpeg")},
            timeout=60,
        )
        print(f"重设基准：code={reset.json().get('code')}，告警计数={reset.json()['data']['alarmCount']}")
        closed = session.delete(f"{BASE}/api/live/sessions/{sid}", timeout=30)
        after = session.get(f"{BASE}/api/live/sessions/{sid}", timeout=30)
        print(f"关闭会话：code={closed.json().get('code')}，关闭后查询 code={after.json().get('code')}")

    print(
        f"\n合计：篡改帧 {len(tampered_frames)} 个，告警 {len(alarm_frames)} 次"
        f"（告警帧 {alarm_frames[:5]}{'...' if len(alarm_frames) > 5 else ''}）"
    )
    return 0 if alarm_frames else 1


if __name__ == "__main__":
    sys.exit(main())
