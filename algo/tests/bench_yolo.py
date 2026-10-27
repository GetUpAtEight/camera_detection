"""YOLO 通道压测脚本（在 GPU 服务器上跑，用来验收"吞吐/延迟"两条通道）。

用法：
    # 低延迟通道（batch=1）：/predict
    python tests/bench_yolo.py --mode latency --concurrency 8 --iters 200

    # 高吞吐通道（动态拼批）：/predict_batch
    python tests/bench_yolo.py --mode batch --concurrency 16 --iters 200 --batch 16

    # 自带图片（默认用合成图，不需要素材）
    python tests/bench_yolo.py --image /root/yzn_model_api/sample.jpg

只依赖标准库，服务器上不需要额外装 requests。
"""

from __future__ import annotations

import argparse
import io
import json
import statistics
import threading
import time
import urllib.request
import uuid
from typing import List, Tuple

import cv2
import numpy as np


def make_image(width: int = 1280, height: int = 720, seed: int = 0) -> bytes:
    """生成一张有纹理的合成图（纯噪声会被模型当背景，不适合测吞吐）。"""
    rng = np.random.default_rng(seed)
    canvas = np.full((height, width, 3), 40, dtype=np.uint8)
    for _ in range(18):
        x1, y1 = int(rng.integers(0, width - 80)), int(rng.integers(0, height - 80))
        w, h = int(rng.integers(60, 320)), int(rng.integers(60, 260))
        color = tuple(int(v) for v in rng.integers(40, 255, 3))
        cv2.rectangle(canvas, (x1, y1), (x1 + w, y1 + h), color, -1)
    canvas = cv2.GaussianBlur(canvas, (5, 5), 0)
    ok, buf = cv2.imencode(".jpg", canvas, [int(cv2.IMWRITE_JPEG_QUALITY), 92])
    if not ok:
        raise RuntimeError("无法编码测试图片")
    return buf.tobytes()


def build_multipart(field: str, files: List[bytes], extra: dict) -> Tuple[bytes, str]:
    boundary = f"----ctd{uuid.uuid4().hex}"
    out = io.BytesIO()
    for idx, payload in enumerate(files):
        out.write(f"--{boundary}\r\n".encode())
        out.write(
            f'Content-Disposition: form-data; name="{field}"; filename="img{idx}.jpg"\r\n'.encode()
        )
        out.write(b"Content-Type: image/jpeg\r\n\r\n")
        out.write(payload)
        out.write(b"\r\n")
    for key, value in extra.items():
        out.write(f"--{boundary}\r\n".encode())
        out.write(f'Content-Disposition: form-data; name="{key}"\r\n\r\n'.encode())
        out.write(f"{value}\r\n".encode())
    out.write(f"--{boundary}--\r\n".encode())
    return out.getvalue(), f"multipart/form-data; boundary={boundary}"


def post(url: str, body: bytes, content_type: str, timeout: float) -> dict:
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", content_type)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def percentile(values: List[float], ratio: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, int(round(ratio * (len(ordered) - 1)))))
    return ordered[idx]


def main() -> int:
    parser = argparse.ArgumentParser(description="YOLO 通道压测")
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--mode", choices=["latency", "batch"], default="batch")
    parser.add_argument("--concurrency", type=int, default=16)
    parser.add_argument("--iters", type=int, default=200, help="每个线程的请求数")
    parser.add_argument("--batch", type=int, default=16, help="batch 模式每次请求携带的图片数")
    parser.add_argument("--image", default="", help="用真实图片替代合成图")
    parser.add_argument("--timeout", type=float, default=120.0)
    args = parser.parse_args()

    if args.image:
        with open(args.image, "rb") as fp:
            raw = fp.read()
        payloads = [raw] * args.batch
    else:
        payloads = [make_image(seed=i) for i in range(args.batch)]

    endpoint = "/predict" if args.mode == "latency" else "/predict_batch"
    field = "file" if args.mode == "latency" else "files"
    url = args.url.rstrip("/") + endpoint

    # 预热，避免第一轮承担 CUDA / 图优化开销
    try:
        post(url, *build_multipart(field, payloads[:1], {"conf": 0.25}), timeout=args.timeout)
    except Exception as exc:  # noqa: BLE001
        print(f"[预热失败] {exc}")
        print("检查算法层是否已启动、模型文件是否存在（/health 里的 yolo.error）")
        return 1

    latencies: List[float] = []
    errors: List[str] = []
    lock = threading.Lock()
    images_per_sec_samples: List[float] = []
    batcher_snapshot: dict = {}

    def worker(tid: int) -> None:
        nonlocal batcher_snapshot
        local: List[float] = []
        for _ in range(args.iters):
            body, ctype = build_multipart(field, payloads, {"conf": 0.25})
            started = time.perf_counter()
            try:
                data = post(url, body, ctype, args.timeout)
                elapsed = (time.perf_counter() - started) * 1000.0
                local.append(elapsed)
                if args.mode == "batch":
                    snap = data.get("batcher")
                    if snap:
                        batcher_snapshot = snap
                    ips = data.get("imagesPerSecond")
                    if ips:
                        with lock:
                            images_per_sec_samples.append(float(ips))
                elif data.get("skipped"):
                    with lock:
                        errors.append(str(data.get("note")))
                    return
            except Exception as exc:  # noqa: BLE001
                with lock:
                    errors.append(f"{type(exc).__name__}: {exc}")
                return
        with lock:
            latencies.extend(local)

    images_per_request = 1 if args.mode == "latency" else args.batch
    threads = [threading.Thread(target=worker, args=(i,), daemon=True) for i in range(args.concurrency)]
    started = time.perf_counter()
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    wall = time.perf_counter() - started

    if errors and not latencies:
        print("[FAIL] 全部请求失败：")
        for message in dict.fromkeys(errors):
            print("  -", message)
        return 1

    total_images = len(latencies) * images_per_request
    p50 = percentile(latencies, 0.50)
    p90 = percentile(latencies, 0.90)
    p99 = percentile(latencies, 0.99)

    print(f"endpoint        : {url}")
    print(f"mode            : {args.mode}（每次 {images_per_request} 张，并发 {args.concurrency}）")
    print(f"requests        : {len(latencies)}（失败 {len(errors)}）")
    print(f"wall            : {wall:.2f}s")
    print(f"throughput      : {total_images / wall:.1f} img/s")
    print(f"latency p50/p90/p99 : {p50:.1f} / {p90:.1f} / {p99:.1f} ms")
    if latencies:
        print(f"latency mean    : {statistics.fmean(latencies):.1f} ms")
    if images_per_sec_samples:
        print(f"单请求 imagesPerSecond 均值 : {statistics.fmean(images_per_sec_samples):.1f}")
    if batcher_snapshot:
        print(f"batcher         : {json.dumps(batcher_snapshot, ensure_ascii=False)}")
    if errors:
        print(f"[WARN] 部分请求失败：{dict.fromkeys(errors)}")

    # 与需求 3.4.4 的基准对比（A10，batch=16：约 275 img/s，p99 约 263ms）
    if args.mode == "batch":
        target_ips, target_p99 = 275.0, 263.0
        ips = total_images / wall
        print()
        print(f"目标吞吐 {target_ips:.0f} img/s → 实测 {ips:.1f} img/s"
              f"（{'达标' if ips >= target_ips * 0.9 else '未达标'}）")
        print(f"目标 p99 {target_p99:.0f} ms → 实测 {p99:.1f} ms"
              f"（{'达标' if p99 <= target_p99 * 1.1 else '未达标'}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
