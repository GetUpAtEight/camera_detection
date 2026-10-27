"""Camera Tampering Detection —— Python 算法推理层（FastAPI）。

启动（开发）::

    python server.py --host 0.0.0.0 --port 8000 --batch 16 --max-wait-ms 2

生产（GPU 服务器，单 worker！动态拼批依赖进程内单例，多 worker 会各自建一套）::

    OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 \
    .venv/bin/uvicorn server:app --host 0.0.0.0 --port 8000 --workers 1

对外接口（SpringBoot 只依赖这些）：

    GET  /health                      健康检查 + YOLO/GPU 状态
    GET  /algorithms                  算法清单（含可调参数元数据）
    POST /media/probe                 媒体元信息（分辨率/时长/帧数）
    POST /detect/image                单图检测（同步，含参考帧可选参数）
    POST /session/{sid}/params        运行中更新该会话的阈值（原地改，保留帧间状态）
    DELETE /session/{sid}             释放该会话的检测器状态
    POST /detect/video                视频检测（异步，返回 taskId）
    GET  /detect/tasks/{task_id}      查询异步任务与逐帧结果
    POST /detect/tasks/{task_id}/cancel
    POST /predict                     YOLO 单图低延迟推理
    POST /predict_batch               YOLO 批量高吞吐推理（基准/压测用）
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import tempfile
import threading
import time
import uuid
from collections import defaultdict
from typing import Any, Dict, List, Optional, Sequence, Tuple

# 必须在 import cv2 / numpy 之前设置，避免 CPU 过度订阅（见需求文档 3.4.4）
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")

import cv2  # noqa: E402
import numpy as np  # noqa: E402
from fastapi import FastAPI, File, Form, HTTPException, UploadFile  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.responses import JSONResponse  # noqa: E402

import media as media_utils  # noqa: E402
from detectors import ALL_DETECTORS, CLASSIC_DETECTORS, YoloDetector  # noqa: E402
from detectors.base import DetectionResult, encode_data_url  # noqa: E402
from sparse import SAMPLE_VERDICTS, CameraStore, SparseEngine  # noqa: E402
from sparse.engine import DEFAULT_PARAMS as SPARSE_DEFAULT_PARAMS  # noqa: E402
from tasks import CANCELLED, DONE, FAILED, PENDING, RUNNING, AlgoTask, TaskStore  # noqa: E402

# --------------------------------------------------------------------------- 配置
BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def _default_model_path() -> str:
    candidates = [
        os.environ.get("ALGO_MODEL_PATH", ""),
        "/root/yzn_model_api/yolo26n.pt",
        os.path.join(BASE_DIR, "yolo26n.pt"),
        os.path.join(BASE_DIR, "..", "yolo26n.pt"),
    ]
    for path in candidates:
        if path and os.path.exists(path):
            return os.path.abspath(path)
    # 都不存在时保留 Linux 部署路径原样返回（Windows 上 abspath 会错误地加上盘符）
    return candidates[1]


MODEL_PATH = _default_model_path()
DEVICE = os.environ.get("ALGO_DEVICE", "auto")
MAX_BATCH = int(os.environ.get("ALGO_MAX_BATCH", "16"))
MAX_WAIT_MS = float(os.environ.get("ALGO_MAX_WAIT_MS", "2"))
ENABLE_YOLO = os.environ.get("ALGO_ENABLE_YOLO", "1") not in ("0", "false", "False")
CV_THREADS = int(os.environ.get("ALGO_CV_THREADS", "1"))
WORK_DIR = os.environ.get("ALGO_WORK_DIR", os.path.join(BASE_DIR, "_tmp"))
MAX_UPLOAD_MB = int(os.environ.get("ALGO_MAX_UPLOAD_MB", "512"))

# 远端媒体缓存：同一段录像只经网络传一次，后续检测按 key 复用
CACHE_DIR = os.environ.get("ALGO_CACHE_DIR") or os.path.join(BASE_DIR, "cache")
os.makedirs(CACHE_DIR, exist_ok=True)
CACHE_SUFFIXES = (".mp4", ".avi", ".mov", ".mkv", ".webm", ".flv", ".m4v")


def _cache_path(key: str, suffix: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]", "", key or "")[:72] or uuid.uuid4().hex
    return os.path.join(CACHE_DIR, safe + suffix)


def _find_cached(key: str) -> Optional[str]:
    safe = re.sub(r"[^A-Za-z0-9_.-]", "", key or "")[:72]
    if not safe:
        return None
    for suffix in CACHE_SUFFIXES:
        candidate = os.path.join(CACHE_DIR, safe + suffix)
        if os.path.exists(candidate) and os.path.getsize(candidate) > 0:
            return candidate
    return None
TASK_TTL = int(os.environ.get("ALGO_TASK_TTL", "3600"))

cv2.setNumThreads(max(1, CV_THREADS))
os.makedirs(WORK_DIR, exist_ok=True)

yolo = YoloDetector(
    model_path=MODEL_PATH,
    device=DEVICE,
    max_batch=MAX_BATCH,
    max_wait_ms=MAX_WAIT_MS,
    enabled=ENABLE_YOLO,
)
store = TaskStore(ttl_seconds=TASK_TTL)

app = FastAPI(
    title="Camera Tampering Detection - 算法推理层",
    version="2.1.0",
    description="5 个主力经典算法 + 3 个辅助算法 + YOLO 深度学习检测",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

STARTED_AT = time.time()
TASK_LOCK = threading.Lock()
TASK_THREADS: Dict[str, threading.Thread] = {}


@app.on_event("startup")
def _on_startup() -> None:
    yolo.load_async()


# --------------------------------------------------------------------------- 工具
class VisualBudget:
    """限制返回的可视化图数量，避免视频结果 JSON 过大。"""

    def __init__(self, total: int = 12, per_algorithm: int = 2, always_first_frame: bool = True):
        self.total = total
        self.per_algorithm = per_algorithm
        self.always_first_frame = always_first_frame
        self._used_total = 0
        self._used: Dict[str, int] = defaultdict(int)

    def take(self, algorithm: str, frame_index: int, tampered: bool) -> bool:
        if self._used_total >= self.total:
            return False
        if not tampered and not (self.always_first_frame and frame_index == 0):
            return False
        if self._used[algorithm] >= self.per_algorithm:
            return False
        self._used[algorithm] += 1
        self._used_total += 1
        return True


def _parse_json(raw: Optional[str], default: Any) -> Any:
    if not raw:
        return default
    try:
        return json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=f"JSON 参数解析失败：{exc}")


def _parse_algorithms(raw: Optional[str]) -> List[str]:
    names = _parse_json(raw, None)
    if names is None:
        names = ["laplacian", "entropy", "scene_diff", "orb", "edge"]
    if isinstance(names, str):
        names = [names]
    if not isinstance(names, list):
        raise HTTPException(status_code=400, detail="algorithms 必须是字符串数组")

    known = {cls.name for cls in ALL_DETECTORS} | {YoloDetector.name}
    unknown = [n for n in names if n not in known]
    if unknown:
        raise HTTPException(status_code=400, detail=f"未知算法：{', '.join(unknown)}")
    if not names:
        raise HTTPException(status_code=400, detail="至少选择一个算法")
    # 去重并保持入参顺序
    seen = set()
    ordered = [n for n in names if not (n in seen or seen.add(n))]
    return ordered


def build_detectors(
    algorithm_names: Sequence[str], params: Dict[str, Any], visual: bool
) -> Tuple[List[Any], bool]:
    detectors: List[Any] = []
    # 主力 + 辅助算法共用同一套构建流程；是否参与投票由后端决定，
    # 算法层只负责「被点名就跑」。
    for cls in ALL_DETECTORS:
        if cls.name not in algorithm_names:
            continue
        detector = cls(params.get(cls.name) or {})
        detector.visual_enabled = visual
        detectors.append(detector)
    return detectors, YoloDetector.name in algorithm_names


def run_yolo(
    frames: Sequence[np.ndarray], params: Dict[str, Any], visual: bool
) -> List[DetectionResult]:
    yolo.update_params(params.get(YoloDetector.name) or {})
    yolo.visual_enabled = visual
    return yolo.analyze_many(list(frames))


def _read_upload(file: UploadFile, max_mb: int = MAX_UPLOAD_MB) -> bytes:
    data = file.file.read()
    if not data:
        raise HTTPException(status_code=400, detail="上传内容为空")
    if len(data) > max_mb * 1024 * 1024:
        raise HTTPException(status_code=413, detail=f"文件超过 {max_mb} MB 限制")
    return data


def _save_upload(file: UploadFile) -> str:
    suffix = os.path.splitext(file.filename or "video.mp4")[1] or ".mp4"
    path = os.path.join(WORK_DIR, f"{uuid.uuid4().hex}{suffix}")
    file.file.seek(0)
    size = 0
    with open(path, "wb") as out:
        while True:
            chunk = file.file.read(1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            if size > MAX_UPLOAD_MB * 1024 * 1024:
                out.close()
                os.remove(path)
                raise HTTPException(status_code=413, detail=f"文件超过 {MAX_UPLOAD_MB} MB 限制")
            out.write(chunk)
    if size == 0:
        os.remove(path)
        raise HTTPException(status_code=400, detail="上传内容为空")
    return path


def _encode_results(
    results: List[DetectionResult], budget: VisualBudget, frame_index: int, max_width: int
) -> List[Dict[str, Any]]:
    payload: List[Dict[str, Any]] = []
    for result in results:
        if result.visual_image is not None and budget.take(
            result.algorithm, frame_index, result.is_tampered
        ):
            result.visual = encode_data_url(result.visual_image, max_width=max_width)
        result.visual_image = None
        payload.append(result.to_dict())
    return payload


# --------------------------------------------------------------------------- 基础接口
@app.get("/health")
def health() -> Dict[str, Any]:
    return {
        "status": "ok",
        "version": app.version,
        "uptimeSeconds": int(time.time() - STARTED_AT),
        "cv2": cv2.__version__,
        "numpy": np.__version__,
        "cvThreads": cv2.getNumThreads(),
        "yolo": yolo.status(),
        "tasks": store.stats(),
    }


@app.get("/algorithms")
def algorithms() -> Dict[str, Any]:
    metas = [cls().meta() for cls in ALL_DETECTORS]
    metas.append(yolo.meta())
    return {
        "algorithms": metas,
        "thresholdScore": 0.5,
        "scoreHint": "score 为 0~1 归一化异常分，0.5 恰好等于各算法自身判定阈值",
    }


@app.post("/media/probe")
def probe(file: UploadFile = File(...)) -> Dict[str, Any]:
    filename = (file.filename or "").lower()
    is_video_ext = filename.endswith((".mp4", ".avi", ".mov", ".mkv", ".flv", ".webm", ".m4v"))
    if is_video_ext:
        path = _save_upload(file)
        try:
            info = media_utils.probe_video(path)
        except media_utils.MediaError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        finally:
            try:
                os.remove(path)
            except OSError:
                pass
        return info

    data = _read_upload(file)
    try:
        return media_utils.probe_image(data)
    except media_utils.MediaError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# --------------------------------------------------------------------------- 单图检测

# 会话态检测器缓存。
# MOG2 背景模型、短/长期直方图、帧差这几类算法都依赖帧间状态；而实时监控是「一帧一次
# HTTP 请求」，如果每次请求都 build_detectors，这些算法永远停在预热的第一帧，永远不会
# 告警（实测 130 帧里它们一次结果都没返回）。所以这里按 sessionId 复用同一批检测器实例。
_SESSION_DETECTORS: Dict[str, Dict[str, Any]] = {}
_SESSION_TTL_S = 30 * 60.0
_SESSION_MAX = 32
_session_lock = threading.Lock()


def _session_signature(names: List[str], params_obj: Dict[str, Any], visual: bool) -> str:
    return json.dumps([names, params_obj, visual], sort_keys=True, ensure_ascii=False)


def _session_detectors(session_id: str, names: List[str], params_obj: Dict[str, Any],
                       visual: bool, reset: bool):
    """取（或重建）某个实时会话的检测器实例。

    参数签名变化时自动重建——但"学习期校准后改阈值"这种情况会走
    ``POST /session/{id}/params`` 原地更新，避免把 MOG2 背景模型之类的累积状态清掉。
    """
    signature = _session_signature(names, params_obj, visual)
    now = time.time()
    with _session_lock:
        if len(_SESSION_DETECTORS) >= _SESSION_MAX:
            stale = [k for k, v in _SESSION_DETECTORS.items() if now - v["at"] > _SESSION_TTL_S]
            for key in stale:
                _SESSION_DETECTORS.pop(key, None)
            if len(_SESSION_DETECTORS) >= _SESSION_MAX:
                # 还是满就丢掉最久没用的一个，保证不会无限增长
                oldest = min(_SESSION_DETECTORS.items(), key=lambda kv: kv[1]["at"])[0]
                _SESSION_DETECTORS.pop(oldest, None)
        entry = _SESSION_DETECTORS.get(session_id)
        if reset and entry is not None and entry["signature"] == signature:
            # 重设基准时只重建「依赖参考帧」的算法：它们的基线是相对旧场景算的，已经失效；
            # 而 MOG2 背景模型 / 帧差 / 短长期直方图靠帧间累积，重建等于让它们重新预热，
            # 白白损失好几帧的检测能力。
            fresh, _ = build_detectors(names, params_obj, visual)
            keep = {d.name: d for d in entry["detectors"] if not d.requires_reference}
            entry["detectors"] = [keep.get(d.name, d) for d in fresh]
            entry["at"] = now
            return entry["detectors"], entry["needYolo"]
        if reset or entry is None or entry["signature"] != signature:
            detectors, need_yolo = build_detectors(names, params_obj, visual)
            entry = {
                "signature": signature,
                "detectors": detectors,
                "needYolo": need_yolo,
                "names": names,
                "params": params_obj,
                "visual": visual,
                "at": now,
            }
            _SESSION_DETECTORS[session_id] = entry
        else:
            entry["at"] = now
        return entry["detectors"], entry["needYolo"]


def _update_session_params(session_id: str, params_obj: Dict[str, Any]) -> int:
    """把新阈值直接写进已有检测器实例，保留帧间状态。"""
    with _session_lock:
        entry = _SESSION_DETECTORS.get(session_id)
        if entry is None:
            return 0
        updated = 0
        for detector in entry["detectors"]:
            sub = params_obj.get(detector.name)
            if isinstance(sub, dict):
                detector.update_params(sub)
                updated += 1
        entry["params"] = params_obj
        entry["signature"] = _session_signature(entry["names"], params_obj, entry["visual"])
        entry["at"] = time.time()
        return updated


def _drop_session(session_id: str) -> bool:
    with _session_lock:
        return _SESSION_DETECTORS.pop(session_id, None) is not None


@app.delete("/session/{session_id}")
def delete_session(session_id: str) -> Dict[str, Any]:
    """释放实例。参数中的状态（MOG2 背景模型等）只对当前会话有效，会话结束就该丢掉。"""
    return {"dropped": _drop_session(session_id)}


@app.post("/session/{session_id}/params")
def update_session_params(session_id: str, params: str = Form("{}")) -> Dict[str, Any]:
    """运行中改阈值：原地更新，不清空帧间状态（学习期校准完立刻生效）。"""
    params_obj = _parse_json(params, {}) or {}
    return {"updated": _update_session_params(session_id, params_obj)}


@app.post("/detect/image")
def detect_image(
    file: UploadFile = File(...),
    reference: Optional[UploadFile] = File(None),
    algorithms: str = Form("[]"),
    params: str = Form("{}"),
    visual: bool = Form(True),
    sessionId: str = Form(""),
    reset: bool = Form(False),
) -> Dict[str, Any]:
    algorithm_names = _parse_algorithms(algorithms)
    params_obj = _parse_json(params, {}) or {}
    started = time.perf_counter()

    image = media_utils.decode_image(_read_upload(file))
    reference_image = None
    if reference is not None and reference.filename:
        reference_image = media_utils.decode_image(_read_upload(reference))

    if sessionId:
        detectors, need_yolo = _session_detectors(sessionId, algorithm_names, params_obj, visual, reset)
    else:
        detectors, need_yolo = build_detectors(algorithm_names, params_obj, visual)
    for detector in detectors:
        if reference_image is not None and detector.requires_reference:
            detector.set_reference(reference_image)

    timings: Dict[str, float] = {}
    results: List[DetectionResult] = []
    for detector in detectors:
        t0 = time.perf_counter()
        results.append(detector.process(image))
        timings[detector.name] = (time.perf_counter() - t0) * 1000.0
    if need_yolo:
        t0 = time.perf_counter()
        yolo_results = run_yolo([image], params_obj, visual)
        timings[YoloDetector.name] = (time.perf_counter() - t0) * 1000.0
        results.extend(yolo_results)

    index = {name: i for i, name in enumerate(algorithm_names)}
    results.sort(key=lambda r: index.get(r.algorithm, 99))

    budget = VisualBudget(total=12, per_algorithm=1, always_first_frame=True)
    payload = _encode_results(results, budget, 0, max_width=720)
    h, w = image.shape[:2]
    return {
        "mediaType": "image",
        "width": w,
        "height": h,
        "hasReference": reference_image is not None,
        "frameCount": 1,
        "elapsedMs": int((time.perf_counter() - started) * 1000),
        "timings": {name: round(cost, 3) for name, cost in timings.items()},
        "results": payload,
    }


# --------------------------------------------------------------------------- 视频检测
def _segment_plans(segments: Optional[List[Dict[str, Any]]],
                   info: Dict[str, Any]) -> List[Dict[str, Any]]:
    """把请求里的分段整理成待执行计划（绝对毫秒时间窗）。

    单段区间检测与多段扫描走同一套逻辑：没有 segments 时就是"整段"。
    """
    duration = int(info.get("durationMs") or 0)
    plans: List[Dict[str, Any]] = []
    for raw in segments or []:
        start = raw.get("startMs")
        end = raw.get("endMs")
        start = int(start) if start is not None else 0
        end = int(end) if end is not None else duration
        start = max(0, start)
        if duration > 0:
            end = min(end, duration) if end > 0 else duration
        if end > 0 and end <= start:
            continue
        plans.append({
            "startMs": start,
            "endMs": end,
            "label": raw.get("label") or raw.get("type") or "",
            "type": raw.get("type"),
        })
    return plans


def _run_video_job(
    task: AlgoTask,
    path: str,
    algorithm_names: List[str],
    params_obj: Dict[str, Any],
    sample_fps: float,
    max_frames: int,
    visual: bool,
    segments: Optional[List[Dict[str, Any]]] = None,
    ref_gap_ms: int = 1000,
    keep_file: bool = False,
) -> None:
    """分段执行：每段独立建检测器、独立取参考帧，结果带 segmentIndex。"""
    started = time.perf_counter()
    ordered: List[Dict[str, Any]] = []
    costs: Dict[str, float] = defaultdict(float)
    try:
        store.update(task, status=RUNNING, message="读取视频信息", progress=0.02)
        info = media_utils.probe_video(path)
        fps = float(info["fps"])
        step = media_utils.sample_step(fps, sample_fps)
        plans = _segment_plans(segments, info) or [{"startMs": None, "endMs": None,
                                                   "label": "", "type": None}]
        expected_per = [
            media_utils.expected_sampled_frames(int(info["frameCount"]), step, max_frames, fps,
                                                plan["startMs"], plan["endMs"])
            for plan in plans
        ]
        expected_total = max(1, sum(expected_per))
        store.update(task, media=info, total_frames=expected_total,
                     start_ms=plans[0]["startMs"], end_ms=plans[-1]["endMs"],
                     message=f"共 {len(plans)} 段，预计 {expected_total} 帧")

        multi = len(plans) > 1
        budget_total = 12 if len(plans) <= 3 else 4
        budget = VisualBudget(total=budget_total, per_algorithm=2 if len(plans) <= 3 else 1,
                              always_first_frame=True)
        batch_size = max(1, min(MAX_BATCH, 32))
        frames_done = 0
        tampered_done = 0
        segment_summaries: List[Dict[str, Any]] = []

        for seg_index, plan in enumerate(plans):
            if task.cancelled:
                store.update(task, status=CANCELLED, message="任务已取消")
                return

            seg_start = plan["startMs"]
            seg_end = plan["endMs"]
            detectors, need_yolo = build_detectors(algorithm_names, params_obj, visual)
            if need_yolo:
                yolo.update_params(params_obj.get(YoloDetector.name) or {})
                yolo.visual_enabled = visual

            # 段前参考帧：固定机位的"镜子"取段首前 ref_gap 处，比段内首帧更干净
            ref_frame = None
            if seg_start and seg_start > 0:
                try:
                    ref_frame = media_utils.read_frame_at(path, max(0, seg_start - ref_gap_ms))
                    task.ref_mode = f"pre-roll {ref_gap_ms}ms"
                except media_utils.MediaError:
                    ref_frame = None
            if ref_frame is not None:
                for detector in detectors:
                    if detector.requires_reference:
                        detector.set_reference(ref_frame)

            seg_frames = 0
            seg_tampered = 0
            seg_peak = 0.0
            seg_types: Dict[str, int] = {}
            seg_costs: Dict[str, float] = defaultdict(float)
            yolo_buffer: List[Tuple[int, int, np.ndarray, int]] = []

            def flush_yolo() -> None:
                if not yolo_buffer:
                    return
                images = [item[2] for item in yolo_buffer]
                t0 = time.perf_counter()
                batch_results = yolo.analyze_many(images)
                seg_costs[YoloDetector.name] += (time.perf_counter() - t0) * 1000.0
                costs[YoloDetector.name] += (time.perf_counter() - t0) * 1000.0
                for item, res in zip(yolo_buffer, batch_results):
                    record = by_index.get(item[0])
                    if record is None:
                        continue
                    encoded = _encode_results([res], budget, item[0], max_width=560)
                    with task.lock:
                        record["results"].extend(encoded)
                yolo_buffer.clear()

            by_index: Dict[int, Dict[str, Any]] = {}

            for sample in media_utils.iter_frames(path, step, max_frames,
                                                  start_ms=seg_start, end_ms=seg_end):
                if task.cancelled:
                    store.update(task, status=CANCELLED, message="任务已取消")
                    return
                frame = sample.image
                if seg_frames == 0 and ref_frame is None:
                    for detector in detectors:
                        if detector.requires_reference:
                            detector.set_reference(frame)

                frame_results: List[DetectionResult] = []
                for detector in detectors:
                    t0 = time.perf_counter()
                    frame_results.append(detector.process(frame))
                    elapsed = (time.perf_counter() - t0) * 1000.0
                    seg_costs[detector.name] += elapsed
                    costs[detector.name] += elapsed

                encoded = _encode_results(frame_results, budget, sample.index, max_width=560)
                record = {
                    "frameIndex": sample.index,
                    "timestampMs": sample.timestamp_ms,
                    "segmentIndex": seg_index,
                    "results": encoded,
                }
                by_index[sample.index] = record
                ordered.append(record)

                if any(r.is_tampered for r in frame_results):
                    seg_tampered += 1
                    tampered_done += 1
                for result in frame_results:
                    if result.is_tampered and result.tamper_type:
                        seg_types[result.tamper_type] = seg_types.get(result.tamper_type, 0) + 1
                    seg_peak = max(seg_peak, float(result.score or 0.0))

                seg_frames += 1
                frames_done += 1
                if need_yolo:
                    yolo_buffer.append((sample.index, sample.timestamp_ms, frame, seg_index))
                    if len(yolo_buffer) >= batch_size:
                        flush_yolo()

                progress = min(0.99, frames_done / expected_total)
                if frames_done % 3 == 0 or frames_done == expected_total:
                    with task.lock:
                        task.frames = ordered
                    store.update(task, progress=progress,
                                 message=f"已处理 {frames_done}/{expected_total} 帧")

            flush_yolo()

            dominant = max(seg_types.items(), key=lambda kv: kv[1])[0] if seg_types else "normal"
            segment_summaries.append({
                "index": seg_index,
                "label": plan["label"] or f"{seg_start/1000:.1f}s - {seg_end/1000:.1f}s",
                "startMs": seg_start,
                "endMs": seg_end,
                # 标注给出的类型（按标注分段扫描时回传，供分析页做"标注 vs 实测"对照）
                "type": plan.get("type"),
                "frameCount": seg_frames,
                "tamperedFrames": seg_tampered,
                "alarmRate": round(seg_tampered / seg_frames, 4) if seg_frames else 0.0,
                "peakScore": round(seg_peak, 4),
                "tamperType": dominant,
                "expectedFrames": expected_per[seg_index],
                "timings": {name: round(total / max(seg_frames, 1), 3)
                            for name, total in seg_costs.items()},
            })
            with task.lock:
                task.frames = ordered
                task.segments = segment_summaries
            store.update(task, progress=min(0.99, frames_done / expected_total),
                         message=f"第 {seg_index + 1}/{len(plans)} 段完成（{seg_frames} 帧）")

        elapsed = int((time.perf_counter() - started) * 1000)
        frames_denom = max(frames_done, 1)
        store.update(
            task,
            status=DONE,
            progress=1.0,
            message=(f"检测完成：{len(plans)} 段 / {frames_done} 帧（采样间隔 {step} 帧）"
                     if multi else f"检测完成，共 {frames_done} 帧（采样间隔 {step} 帧）"),
            elapsed_ms=elapsed,
            total_frames=frames_done,
            tampered_frames=tampered_done,
            segments=segment_summaries,
            timings={name: total / frames_denom for name, total in costs.items()},
        )
    except Exception as exc:  # 任何异常都不能让任务停留在 running
        store.update(task, status=FAILED, error=f"{type(exc).__name__}: {exc}", message="检测失败")
    finally:
        with TASK_LOCK:
            TASK_THREADS.pop(task.id, None)
        if not keep_file:
            try:
                os.remove(path)
            except OSError:
                pass


@app.post("/media/cache")
def cache_media(file: Optional[UploadFile] = File(None), key: str = Form("")) -> Dict[str, Any]:
    """把视频存进远端缓存（同一 key 只传一次），返回媒体信息。"""
    if not key:
        key = uuid.uuid4().hex
    existing = _find_cached(key)
    if existing:
        return {"key": key, "cached": True, "sizeBytes": os.path.getsize(existing),
                "media": media_utils.probe_video(existing)}
    if file is None:
        raise HTTPException(status_code=400, detail="首次上传必须带 file")
    suffix = os.path.splitext(file.filename or "video.mp4")[1].lower() or ".mp4"
    if suffix not in CACHE_SUFFIXES:
        suffix = ".mp4"
    tmp = _save_upload(file)
    dest = _cache_path(key, suffix)
    shutil.move(tmp, dest)
    return {"key": key, "cached": True, "sizeBytes": os.path.getsize(dest),
            "media": media_utils.probe_video(dest)}


@app.get("/media/cache/{key}")
def cache_info(key: str) -> Dict[str, Any]:
    path = _find_cached(key)
    if not path:
        raise HTTPException(status_code=404, detail="远端未缓存该媒体")
    return {"key": key, "cached": True, "sizeBytes": os.path.getsize(path),
            "media": media_utils.probe_video(path)}


@app.post("/detect/video")
def detect_video(
    file: Optional[UploadFile] = File(None),
    algorithms: str = Form("[]"),
    params: str = Form("{}"),
    sampleFps: float = Form(5.0),
    maxFrames: int = Form(600),
    visual: bool = Form(True),
    startMs: int = Form(-1),
    endMs: int = Form(-1),
    segments: str = Form("[]"),
    refGapMs: int = Form(1000),
    cacheKey: str = Form(""),
) -> Dict[str, Any]:
    """视频检测（异步）。

    * 不传区间：整段检测；
    * ``startMs`` / ``endMs``：只跑这一段；
    * ``segments``：``[{"startMs":..,"endMs":..,"label":"..","type":"covered"}]`` 批量分段。
    """
    algorithm_names = _parse_algorithms(algorithms)
    params_obj = _parse_json(params, {}) or {}
    sample_fps = float(sampleFps) if sampleFps and sampleFps > 0 else 5.0
    max_frames = int(max(1, min(20000, maxFrames or 600)))

    seg_list = _parse_json(segments, []) or []
    if not seg_list and startMs >= 0:
        seg_list = [{"startMs": int(startMs),
                     "endMs": int(endMs) if endMs >= 0 else None,
                     "label": "手动区间"}]
    if not isinstance(seg_list, list):
        raise HTTPException(status_code=400, detail="segments 必须是数组")

    keep_file = False
    if cacheKey:
        path = _find_cached(cacheKey)
        if not path:
            raise HTTPException(status_code=404, detail="远端未缓存该媒体，请先调用 /media/cache")
        keep_file = True
        media_name = os.path.basename(path)
    else:
        if file is None:
            raise HTTPException(status_code=400, detail="缺少 file 且未指定 cacheKey")
        path = _save_upload(file)
        media_name = file.filename or os.path.basename(path)
    try:
        info = media_utils.probe_video(path)
    except media_utils.MediaError as exc:
        if not keep_file:
            try:
                os.remove(path)
            except OSError:
                pass
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    task = store.create(media_name, "video", algorithm_names)
    task.media = info
    thread = threading.Thread(
        target=_run_video_job,
        args=(task, path, algorithm_names, params_obj, sample_fps, max_frames, visual,
              seg_list, int(refGapMs or 1000), keep_file),
        name=f"video-job-{task.id[:8]}",
        daemon=True,
    )
    with TASK_LOCK:
        TASK_THREADS[task.id] = thread
    thread.start()
    return {
        "taskId": task.id,
        "status": PENDING,
        "media": info,
        "sampleFps": sample_fps,
        "maxFrames": max_frames,
        "segmentCount": len(seg_list),
    }


@app.get("/detect/tasks/{task_id}")
def get_task(task_id: str, includeFrames: bool = True, offset: int = 0, limit: int = 0):
    task = store.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在或已过期")
    with task.lock:
        return task.snapshot(include_frames=includeFrames, offset=max(0, offset), limit=max(0, limit))


@app.post("/detect/tasks/{task_id}/cancel")
def cancel_task(task_id: str) -> Dict[str, Any]:
    task = store.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在或已过期")
    task.cancelled = True
    store.update(task, message="正在取消…")
    return {"taskId": task_id, "cancelled": True}


@app.delete("/detect/tasks/{task_id}")
def delete_task(task_id: str) -> Dict[str, Any]:
    task = store.get(task_id)
    if task is not None:
        task.cancelled = True
    return {"deleted": store.delete(task_id)}


# --------------------------------------------------------------------------- YOLO 专用
@app.post("/predict")
def predict(file: UploadFile = File(...), conf: float = Form(0.25), imgsz: int = Form(640)):
    image = media_utils.decode_image(_read_upload(file))
    started = time.perf_counter()
    result = run_yolo([image], {"yolo": {"conf": conf, "imgsz": imgsz}}, True)[0]
    payload = result.to_dict()
    payload["elapsedMs"] = round((time.perf_counter() - started) * 1000, 2)
    return payload


@app.post("/predict_batch")
def predict_batch(files: List[UploadFile] = File(...), conf: float = Form(0.25),
                  imgsz: int = Form(640)):
    if not files:
        raise HTTPException(status_code=400, detail="未收到图片")
    started = time.perf_counter()
    images = [media_utils.decode_image(_read_upload(f)) for f in files]
    decode_ms = (time.perf_counter() - started) * 1000
    infer_start = time.perf_counter()
    results = run_yolo(images, {"yolo": {"conf": conf, "imgsz": imgsz}}, False)
    infer_ms = (time.perf_counter() - infer_start) * 1000
    total_ms = (time.perf_counter() - started) * 1000
    return {
        "count": len(results),
        "decodeMs": round(decode_ms, 2),
        "inferMs": round(infer_ms, 2),
        "totalMs": round(total_ms, 2),
        "imagesPerSecond": round(len(results) / max(total_ms / 1000.0, 1e-6), 1),
        "batcher": yolo._batcher.snapshot(),  # noqa: SLF001 - 压测信息
        "results": [
            {
                "count": r.detail.get("count", 0),
                "detections": r.detail.get("detections", []),
                "skipped": r.skipped,
                "note": r.note,
            }
            for r in results
        ],
    }


# --------------------------------------------------------------------------- 低频稀疏采样
# 与上面的实时链路完全独立：实时链路是「一帧一次 HTTP」，状态在内存里、30 分钟过期；
# 低频采样是「一小时一次、每次 K 帧」，机位状态必须落盘，否则每次都从"预热第 1 帧"开始。
_SPARSE_DIR = os.path.join(BASE_DIR, "data", "sparse")
_sparse_store = CameraStore(_SPARSE_DIR)


def _sparse_engine(params_obj: Optional[Dict[str, Any]] = None) -> SparseEngine:
    engine = SparseEngine(_sparse_store)
    if params_obj:
        engine.params.update({k: v for k, v in params_obj.items() if k in SPARSE_DEFAULT_PARAMS})
    return engine


@app.get("/sparse/meta")
def sparse_meta() -> Dict[str, Any]:
    """低频采样的参数元数据 + 判决字典（前端据此渲染表单与图例）。"""
    return {
        "params": [
            {"key": "dHistAlarm", "label": "历史偏离告警阈值", "default": SPARSE_DEFAULT_PARAMS["dHistAlarm"],
             "min": 0.05, "max": 0.9, "step": 0.01, "unit": "",
             "description": "dHist 超过它才认为画面显著偏离历史基线"},
            {"key": "outlierAlarm", "label": "逐像素离群占比", "default": SPARSE_DEFAULT_PARAMS["outlierAlarm"],
             "min": 0.01, "max": 0.9, "step": 0.01, "unit": "",
             "description": "逐像素鲁棒 z>3 的像素占比阈值"},
            {"key": "relEntropyDrop", "label": "相对熵降（遮挡）", "default": SPARSE_DEFAULT_PARAMS["relEntropyDrop"],
             "min": 0.05, "max": 0.9, "step": 0.01, "unit": "",
             "description": "熵相对同时段基线下降多少判为遮挡"},
            {"key": "relSharpnessDrop", "label": "相对清晰度降（失焦）",
             "default": SPARSE_DEFAULT_PARAMS["relSharpnessDrop"],
             "min": 0.05, "max": 0.9, "step": 0.01, "unit": "",
             "description": "清晰度相对同时段基线下降多少判为失焦"},
            {"key": "dBurstTransient", "label": "瞬时事件动态阈值",
             "default": SPARSE_DEFAULT_PARAMS["dBurstTransient"],
             "min": 0.05, "max": 1.0, "step": 0.01, "unit": "",
             "description": "burst 内动态超过它判为瞬时事件（不告警）"},
            {"key": "rotateOrb", "label": "转动判定平移量", "default": SPARSE_DEFAULT_PARAMS["rotateOrb"],
             "min": 0.5, "max": 40.0, "step": 0.5, "unit": "px",
             "description": "burst 内一致平移超过它且 inlier 足够 ⇒ 正在被转动"},
            {"key": "freezeGapMs", "label": "跨采样点重复最短间隔",
             "default": SPARSE_DEFAULT_PARAMS["freezeGapMs"], "min": 60000, "max": 86400000,
             "step": 60000, "unit": "ms",
             "description": "与多久之前的历史样本逐位相同才判冻结（防止同一次抽样内部重复上报）"},
            {"key": "noiseFloor", "label": "噪声绝对下限", "default": SPARSE_DEFAULT_PARAMS["noiseFloor"],
             "min": 0.01, "max": 8.0, "step": 0.01, "unit": "灰度级",
             "description": "噪声残差 σ 低于它认为画面没有真实传感器噪声"},
        ],
        "verdicts": [
            {"key": key, **value} for key, value in SAMPLE_VERDICTS.items()
        ],
        "defaults": {k: v for k, v in SPARSE_DEFAULT_PARAMS.items() if k != "admitBaseline"},
    }


@app.post("/sparse/sample")
def sparse_sample(
    cameraId: str = Form(...),
    frames: List[UploadFile] = File(...),
    tsMs: int = Form(0),
    name: str = Form(""),
    reset: bool = Form(False),
    params: str = Form("{}"),
) -> Dict[str, Any]:
    """提交一次稀疏抽样（1~K 帧），返回三层判定结果。

    这是低频采样链路的唯一入口：一次请求 = 一个采样点。
    """
    camera_id = (cameraId or "").strip()
    if not camera_id:
        raise HTTPException(status_code=400, detail="cameraId 不能为空")
    if not frames:
        raise HTTPException(status_code=400, detail="至少需要 1 帧画面")
    if len(frames) > 12:
        raise HTTPException(status_code=400, detail="单次抽样最多 12 帧（burst 过长没有意义且浪费带宽）")

    images = []
    for upload in frames:
        data = _read_upload(upload)
        try:
            images.append(media_utils.decode_image(data))
        except media_utils.MediaError as exc:
            raise HTTPException(status_code=400, detail=f"第 {len(images) + 1} 帧解码失败：{exc}") from exc

    params_obj = _parse_json(params, {}) or {}
    engine = _sparse_engine(params_obj)
    ts = int(tsMs) if tsMs and tsMs > 0 else int(time.time() * 1000)
    return engine.ingest(camera_id, images, ts_ms=ts, name=name or None, reset=bool(reset))


@app.get("/sparse/cameras")
def sparse_cameras() -> Dict[str, Any]:
    return {"cameras": _sparse_store.list_cameras()}


@app.get("/sparse/state/{camera_id}")
def sparse_state(camera_id: str) -> Dict[str, Any]:
    state = _sparse_store.get(camera_id)
    return {
        "cameraId": state.camera_id,
        "name": state.name,
        "createdMs": state.created_ms,
        "updatedMs": state.updated_ms,
        "status": state.baseline_status(),
        "samples": state.total_samples(),
        "slots": state.slot_summary(),
        "history": state.history[:60],
        "landmarks": state.landmark_summary(),
        "templates": state.template_summary(),
        "audit": state.audit[-30:],
        "baseline": [
            {"slot": s["slot"], "tsMs": item.ts_ms, "entropy": round(item.entropy, 3),
             "sharpness": round(item.sharpness, 2)}
            for s in state.slot_summary() for item in state.slot_samples(s["slot"])
        ],
    }


@app.post("/sparse/landmarks/{camera_id}")
def sparse_set_landmarks(
    camera_id: str,
    landmarks: str = Form("[]"),
    file: Optional[UploadFile] = File(None),
    clear: bool = Form(False),
    admitFrame: bool = Form(True),
) -> Dict[str, Any]:
    """标定参照物（静止结构：门 / 楼角 / 围墙 / 灯杆基座）。

    前端在画面上框选若干矩形，坐标是**归一化**的 (x, y, w, h ∈ [0,1])，
    这样与分辨率无关。模板从 ``file``（当前画面）截取；不传则退回基线中位数背景。

    ``admitFrame=True``（默认）时，标定用的这张画面会**同时作为基线样本入库**，
    但要先过质量闸（熵 / 清晰度 / 亮度 / 与已有基线的一致性），
    避免用户在被遮挡或失焦的画面上标定时，把异常画面学成基线。
    """
    items = _parse_json(landmarks, []) or []
    if not isinstance(items, list):
        raise HTTPException(status_code=400, detail="landmarks 必须是数组")
    frame = None
    if file is not None and file.filename:
        try:
            frame = media_utils.decode_image(_read_upload(file))
        except media_utils.MediaError as exc:
            raise HTTPException(status_code=400, detail=f"标定画面解码失败：{exc}") from exc
    engine = _sparse_engine({})
    try:
        return engine.calibrate_landmarks(camera_id, items, frame=frame, clear=bool(clear),
                                          admit_frame=bool(admitFrame))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/sparse/landmarks/{camera_id}")
def sparse_get_landmarks(camera_id: str) -> Dict[str, Any]:
    state = _sparse_store.get(camera_id)
    return {"cameraId": state.camera_id, "landmarks": state.landmark_summary()}


@app.delete("/sparse/landmarks/{camera_id}")
def sparse_clear_landmarks(camera_id: str) -> Dict[str, Any]:
    state = _sparse_store.get(camera_id)
    state.clear_landmarks()
    _sparse_store.save(state)
    return {"cameraId": state.camera_id, "landmarks": []}


# ---------------------------------------------------------------- 参照物模板库
@app.get("/sparse/landmarks/{camera_id}/templates")
def sparse_list_templates(camera_id: str) -> Dict[str, Any]:
    """列出该机位所有参照物的模板（含审计记录）。"""
    return _sparse_engine({}).list_landmark_templates(camera_id)


@app.post("/sparse/landmarks/{camera_id}/templates")
def sparse_add_template(
    camera_id: str,
    landmarkId: int = Form(...),
    file: Optional[UploadFile] = File(None),
    label: str = Form(""),
    source: str = Form("manual"),
    actor: str = Form(""),
    verdictAtTime: str = Form(""),
    force: bool = Form(False),
) -> Dict[str, Any]:
    """给参照物追加一套外观模板。

    典型用法：夜里/雨天判错了 → 操作员把当前画面确认成"正常" → 加为模板。
    算法层会做质量闸与去重，并把这次变更写进审计。
    """
    if file is None or not file.filename:
        raise HTTPException(status_code=400, detail="必须提供模板画面")
    try:
        frame = media_utils.decode_image(_read_upload(file))
    except media_utils.MediaError as exc:
        raise HTTPException(status_code=400, detail=f"模板画面解码失败：{exc}") from exc
    try:
        return _sparse_engine({}).add_landmark_template(
            camera_id, int(landmarkId), frame, label=label, source=source,
            actor=actor, verdict_at_time=verdictAtTime, force=bool(force),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.delete("/sparse/landmarks/{camera_id}/templates/{template_id}")
def sparse_remove_template(camera_id: str, template_id: str,
                           actor: str = "") -> Dict[str, Any]:
    try:
        return _sparse_engine({}).remove_landmark_template(camera_id, template_id, actor=actor)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/sparse/templates/{camera_id}/{template_id}/preview")
def sparse_template_preview(camera_id: str, template_id: str) -> Any:
    """把模板（梯度幅值数组）渲染成 PNG 预览，供前端展示模板外观。"""
    from fastapi.responses import Response

    state = _sparse_store.get(camera_id)
    patch = state.template_patches.get(template_id)
    if patch is None:
        raise HTTPException(status_code=404, detail="模板不存在")
    img = cv2.normalize(patch, None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8)
    ok, buf = cv2.imencode(".png", img)
    if not ok:
        raise HTTPException(status_code=500, detail="渲染失败")
    return Response(content=buf.tobytes(), media_type="image/png")


@app.get("/sparse/thumb/{camera_id}/{slot}/{index}")
def sparse_thumb(camera_id: str, slot: int, index: int):
    """基线库某槽某样本的缩略图（前端基线画廊用）。"""
    from fastapi.responses import Response

    state = _sparse_store.get(camera_id)
    items = state.slot_samples(slot)
    if index < 0 or index >= len(items) or items[index].thumb is None:
        raise HTTPException(status_code=404, detail="缩略图不存在")
    ok, buf = cv2.imencode(".png", items[index].thumb)
    if not ok:
        raise HTTPException(status_code=500, detail="缩略图编码失败")
    return Response(content=buf.tobytes(), media_type="image/png")


@app.post("/sparse/state/{camera_id}/reset")
def sparse_reset(camera_id: str) -> Dict[str, Any]:
    """清空基线库但保留机位（"重设基准"）。"""
    state = _sparse_store.reset(camera_id)
    return {"cameraId": state.camera_id, "status": state.baseline_status(), "samples": 0}


@app.delete("/sparse/state/{camera_id}")
def sparse_delete(camera_id: str) -> Dict[str, Any]:
    return {"deleted": _sparse_store.delete(camera_id)}


@app.get("/sparse/verdicts")
def sparse_verdicts() -> Dict[str, Any]:
    return {"verdicts": [{"key": key, **value} for key, value in SAMPLE_VERDICTS.items()]}


# --------------------------------------------------------------------------- 异常兜底
@app.exception_handler(Exception)
async def _unhandled(request, exc):  # pragma: no cover - 兜底
    return JSONResponse(
        status_code=500,
        content={"detail": f"算法层内部错误：{type(exc).__name__}: {exc}"},
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="CTD 算法推理层")
    parser.add_argument("--host", default=os.environ.get("ALGO_HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("ALGO_PORT", "8000")))
    parser.add_argument("--model", default=MODEL_PATH)
    parser.add_argument("--device", default=DEVICE)
    parser.add_argument("--batch", type=int, default=MAX_BATCH)
    parser.add_argument("--max-wait-ms", type=float, default=MAX_WAIT_MS)
    parser.add_argument("--no-yolo", action="store_true", help="只启用 5 个经典算法")
    parser.add_argument("--reload", action="store_true")
    args = parser.parse_args()

    yolo.model_path = os.path.abspath(args.model)
    yolo.device = YoloDetector._resolve_device(args.device)  # noqa: SLF001
    yolo.max_batch = args.batch
    yolo.max_wait_ms = args.max_wait_ms
    yolo.enabled = not args.no_yolo

    import uvicorn

    uvicorn.run("server:app" if args.reload else app, host=args.host, port=args.port,
                reload=args.reload, workers=1, log_level="info")


if __name__ == "__main__":
    main()
