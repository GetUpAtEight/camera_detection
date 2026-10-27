"""低频巡检算法层（FastAPI，单服务）。

本服务只做一件事：**低速稀疏采样的摄像头篡改判定**。
一次抽样 = 一次 HTTP 请求（1~K 帧），判定结果与机位基线状态由 ``algo/sparse`` 负责，
机位状态落盘到 ``algo/data/sparse``，重启不丢。

为什么单 worker：机位状态由进程内的 ``CameraStore`` 持锁管理，
多 worker 会各自持有一份状态并互相覆盖。生产用::

    uvicorn server:app --host 0.0.0.0 --port 8000 --workers 1

开发::

    python server.py --host 127.0.0.1 --port 8000

对外接口（SpringBoot 只依赖这些）：

    GET    /health                                            健康检查
    GET    /sparse/meta                                       参数元数据 + 判决字典
    POST   /sparse/sample                                     提交一次抽样（1~12 帧）
    GET    /sparse/cameras                                    机位列表
    GET    /sparse/state/{camera_id}                          机位状态（24 槽 + 历史 + 模板 + 审计）
    POST   /sparse/state/{camera_id}/reset                    重设基准（清空基线，保留参照物）
    DELETE /sparse/state/{camera_id}                          删除机位
    GET    /sparse/thumb/{camera_id}/{slot}/{index}           基线样本缩略图
    POST   /sparse/landmarks/{camera_id}                      标定参照物 / 忽略区
    GET    /sparse/landmarks/{camera_id}                      查询参照物
    DELETE /sparse/landmarks/{camera_id}                      清除参照物
    GET    /sparse/landmarks/{camera_id}/templates            模板库 + 审计
    POST   /sparse/landmarks/{camera_id}/templates            补录一套外观模板
    DELETE /sparse/landmarks/{camera_id}/templates/{tid}      删除模板
    GET    /sparse/templates/{camera_id}/{tid}/preview        模板预览图
    GET    /sparse/verdicts                                   判决字典
"""

from __future__ import annotations

import argparse
import json
import os
import time
from typing import Any, Dict, List, Optional

# 必须在 import cv2 / numpy 之前设置，避免 CPU 过度订阅
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")

import cv2  # noqa: E402
import numpy as np  # noqa: E402
from fastapi import FastAPI, File, Form, HTTPException, UploadFile  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.responses import JSONResponse, Response  # noqa: E402

import media as media_utils  # noqa: E402
from sparse import SAMPLE_VERDICTS, CameraStore, SparseEngine  # noqa: E402
from sparse.engine import DEFAULT_PARAMS as SPARSE_DEFAULT_PARAMS  # noqa: E402

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
VERSION = "3.0.0"

CV_THREADS = int(os.environ.get("ALGO_CV_THREADS", "1"))
MAX_UPLOAD_MB = int(os.environ.get("ALGO_MAX_UPLOAD_MB", "32"))
MAX_FRAMES_PER_SAMPLE = 12
SPARSE_DIR = os.environ.get("ALGO_SPARSE_DIR") or os.path.join(BASE_DIR, "data", "sparse")

cv2.setNumThreads(max(1, CV_THREADS))

app = FastAPI(
    title="Camera Tampering Detection - 低频巡检算法层",
    version=VERSION,
    description="低速稀疏采样的摄像头篡改判定（遮挡 / 失焦 / 移位·转动 / 冻结·重放 / 画面替换）",
)

# CORS：只放行本机开发端口。前端默认经 SpringBoot 代理同源访问，
# 这里主要是为了让 http://localhost:5173 能直连调试。
_CORS_ORIGINS = [
    origin.strip()
    for origin in os.environ.get(
        "ALGO_CORS_ORIGINS",
        "http://localhost:5173,http://127.0.0.1:5173",
    ).split(",")
    if origin.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_CORS_ORIGINS,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

STARTED_AT = time.time()

_sparse_store = CameraStore(SPARSE_DIR)


# --------------------------------------------------------------------------- 工具
def _parse_json(raw: Optional[str], default: Any) -> Any:
    if not raw:
        return default
    try:
        return json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=f"JSON 参数解析失败：{exc}") from exc


def _read_upload(file: UploadFile, max_mb: int = MAX_UPLOAD_MB) -> bytes:
    data = file.file.read()
    if not data:
        raise HTTPException(status_code=400, detail="上传内容为空")
    if len(data) > max_mb * 1024 * 1024:
        raise HTTPException(status_code=413, detail=f"文件超过 {max_mb} MB 限制")
    return data


def _sparse_engine(params_obj: Optional[Dict[str, Any]] = None) -> SparseEngine:
    engine = SparseEngine(_sparse_store)
    if params_obj:
        engine.params.update({k: v for k, v in params_obj.items() if k in SPARSE_DEFAULT_PARAMS})
    return engine


# --------------------------------------------------------------------------- 基础接口
@app.get("/health")
def health() -> Dict[str, Any]:
    return {
        "status": "ok",
        "version": VERSION,
        "service": "sparse-inspection",
        "uptimeSeconds": int(time.time() - STARTED_AT),
        "cv2": cv2.__version__,
        "numpy": np.__version__,
        "cvThreads": cv2.getNumThreads(),
        # 本服务不含 YOLO；保留字段是为了让上游健康检查的解析逻辑不变
        "yolo": {"enabled": False, "available": False},
        "dataDir": SPARSE_DIR,
        "cameras": len(_sparse_store.list_cameras()),
    }


# --------------------------------------------------------------------------- 低频巡检
@app.get("/sparse/meta")
def sparse_meta() -> Dict[str, Any]:
    """参数元数据 + 判决字典（前端据此渲染表单与图例）。"""
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
            {"key": "relContrastDrop", "label": "相对对比度降（遮挡）",
             "default": SPARSE_DEFAULT_PARAMS["relContrastDrop"],
             "min": 0.1, "max": 0.95, "step": 0.01, "unit": "",
             "description": "全局对比度相对同时段基线下降多少判为遮挡（区分遮挡与失焦的关键）"},
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
        "verdicts": [{"key": key, **value} for key, value in SAMPLE_VERDICTS.items()],
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

    这是低频巡检的唯一入口：一次请求 = 一个采样点。
    """
    camera_id = (cameraId or "").strip()
    if not camera_id:
        raise HTTPException(status_code=400, detail="cameraId 不能为空")
    if not frames:
        raise HTTPException(status_code=400, detail="至少需要 1 帧画面")
    if len(frames) > MAX_FRAMES_PER_SAMPLE:
        raise HTTPException(
            status_code=400,
            detail=f"单次抽样最多 {MAX_FRAMES_PER_SAMPLE} 帧（burst 过长没有意义且浪费带宽）",
        )

    images = []
    for upload in frames:
        data = _read_upload(upload)
        try:
            images.append(media_utils.decode_image(data))
        except media_utils.MediaError as exc:
            raise HTTPException(
                status_code=400, detail=f"第 {len(images) + 1} 帧解码失败：{exc}"
            ) from exc

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


@app.post("/sparse/state/{camera_id}/reset")
def sparse_reset(camera_id: str) -> Dict[str, Any]:
    """清空基线库但保留机位（"重设基准"，参照物与模板库保留）。"""
    state = _sparse_store.reset(camera_id)
    return {"cameraId": state.camera_id, "status": state.baseline_status(), "samples": 0}


@app.delete("/sparse/state/{camera_id}")
def sparse_delete(camera_id: str) -> Dict[str, Any]:
    return {"deleted": _sparse_store.delete(camera_id)}


@app.get("/sparse/thumb/{camera_id}/{slot}/{index}")
def sparse_thumb(camera_id: str, slot: int, index: int) -> Response:
    """基线库某槽某样本的缩略图（前端基线画廊用）。"""
    state = _sparse_store.get(camera_id)
    items = state.slot_samples(slot)
    if index < 0 or index >= len(items) or items[index].thumb is None:
        raise HTTPException(status_code=404, detail="缩略图不存在")
    ok, buf = cv2.imencode(".png", items[index].thumb)
    if not ok:
        raise HTTPException(status_code=500, detail="缩略图编码失败")
    return Response(content=buf.tobytes(), media_type="image/png")


@app.get("/sparse/verdicts")
def sparse_verdicts() -> Dict[str, Any]:
    return {"verdicts": [{"key": key, **value} for key, value in SAMPLE_VERDICTS.items()]}


# --------------------------------------------------------------------------- 参照物
@app.post("/sparse/landmarks/{camera_id}")
def sparse_set_landmarks(
    camera_id: str,
    landmarks: str = Form("[]"),
    file: Optional[UploadFile] = File(None),
    clear: bool = Form(False),
    admitFrame: bool = Form(True),
) -> Dict[str, Any]:
    """标定参照物（静止结构：门 / 楼角 / 围墙 / 灯杆基座）或忽略区。

    前端在画面上框选若干矩形，坐标是**归一化**的 (x, y, w, h ∈ [0,1])，与分辨率无关。
    模板从 ``file``（当前画面）截取；不传则退回基线中位数背景。

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


# --------------------------------------------------------------------------- 模板库
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
def sparse_template_preview(camera_id: str, template_id: str) -> Response:
    """把模板（梯度幅值数组）渲染成 PNG 预览，供前端展示模板外观。"""
    state = _sparse_store.get(camera_id)
    patch = state.template_patches.get(template_id)
    if patch is None:
        raise HTTPException(status_code=404, detail="模板不存在")
    img = cv2.normalize(patch, None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8)
    ok, buf = cv2.imencode(".png", img)
    if not ok:
        raise HTTPException(status_code=500, detail="渲染失败")
    return Response(content=buf.tobytes(), media_type="image/png")


# --------------------------------------------------------------------------- 异常兜底
@app.exception_handler(Exception)
async def _unhandled(request, exc):  # pragma: no cover - 兜底
    # 不把内部异常原文返回给调用方（可能含路径/依赖细节），完整栈打到服务日志
    import traceback

    traceback.print_exc()
    return JSONResponse(
        status_code=500,
        content={"detail": f"算法层内部错误（{type(exc).__name__}），详情见服务日志"},
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="CTD 低频巡检算法层")
    parser.add_argument("--host", default=os.environ.get("ALGO_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("ALGO_PORT", "8000")))
    parser.add_argument("--data-dir", default=SPARSE_DIR,
                        help="机位基线落盘目录（默认 algo/data/sparse）")
    parser.add_argument("--reload", action="store_true")
    args = parser.parse_args()

    global _sparse_store
    if os.path.abspath(args.data_dir) != os.path.abspath(SPARSE_DIR):
        _sparse_store = CameraStore(args.data_dir)

    import uvicorn

    uvicorn.run("server:app" if args.reload else app, host=args.host, port=args.port,
                reload=args.reload, workers=1, log_level="info")


if __name__ == "__main__":
    main()
