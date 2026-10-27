"""机位状态与历史基线库的持久化。

为什么必须落盘
--------------
实时监控那套（``server.py`` 的 ``_SESSION_DETECTORS``）把帧间状态放在**内存**里，
并且 ``_SESSION_TTL_S = 30 分钟`` 就回收。而低频采样是 **1 小时才来一次**，
内存态必然在两次抽样之间被清掉 —— 于是每一次抽样都是"预热的第 1 帧"，
基线永远建立不起来。

所以本模块把"机位态"（跨抽样、可存几周）与"会话态"（单次抽样内、秒级）**彻底分开**：

* 会话态：``engine.SparseEngine`` 里的一次调用，用完即弃；
* 机位态：本模块的 :class:`CameraState`，按 ``cameraId`` 落盘到 ``data/sparse/``。

存储布局
--------

::

    algo/data/sparse/
    ├── cameras.json                 机位索引（id / 首次采样时间 / 上次采样时间）
    ├── <cameraId>.json              元数据 + 历史判定 + 哈希表 + 每槽样本的标量指标
    └── <cameraId>.npz               每槽样本的缩略灰度图（用于算中位数背景）

缩略图统一存 ``160×90`` 灰度（14.4 KB/张）。24 槽 × 8 样本 ≈ 2.7 MB/机位，
远小于存原始帧；演示规模（几十个机位）完全够用。
"""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import cv2
import numpy as np

# 基线库形状
SLOT_COUNT = 24                 # 按小时的时段槽
SLOT_CAPACITY = 8               # 每个槽最多保留多少样本（超出丢最旧）
THUMB_SIZE = (160, 90)          # 缩略图尺寸 (w, h)
HISTORY_CAPACITY = 500          # 判定历史条数上限
HASH_CAPACITY = 800             # 逐位哈希表容量
MAX_TEMPLATES_PER_LANDMARK = 8  # 每个参照物最多保存多少套外观模板（夜间/雨天/季节…）
AUDIT_CAPACITY = 300            # 模板/标定 变更审计条数上限

# 基线可用门槛（演示用 3；生产建议 ≥10，见 docs/research/02 §3.2）
MIN_SAMPLES_READY = 3
MIN_SAMPLES_WARMING = 1
MAD_FLOOR = 2.0                 # MAD 下限（灰度级）：防止"过度稳定的基线"把 z 放大到失真


@dataclass
class SlotSample:
    """一个历史样本的标量摘要 + 缩略图。"""

    ts_ms: int
    hash: str
    sharpness: float
    entropy: float
    edge: float
    sigma: float
    luma: float
    ahash: int = 0
    thumb: Optional[np.ndarray] = field(default=None, repr=False, compare=False)

    def meta(self) -> Dict[str, Any]:
        return {
            "tsMs": int(self.ts_ms),
            "hash": self.hash,
            "sharpness": round(float(self.sharpness), 3),
            "entropy": round(float(self.entropy), 3),
            "edge": round(float(self.edge), 5),
            "sigma": round(float(self.sigma), 3),
            "luma": round(float(self.luma), 2),
            "aHash": int(self.ahash),
        }

    @staticmethod
    def from_meta(meta: Dict[str, Any], thumb: Optional[np.ndarray] = None) -> "SlotSample":
        return SlotSample(
            ts_ms=int(meta.get("tsMs", 0)),
            hash=str(meta.get("hash", "")),
            sharpness=float(meta.get("sharpness", 0.0)),
            entropy=float(meta.get("entropy", 0.0)),
            edge=float(meta.get("edge", 0.0)),
            sigma=float(meta.get("sigma", 0.0)),
            luma=float(meta.get("luma", 0.0)),
            ahash=int(meta.get("aHash", 0)),
            thumb=thumb,
        )


def slot_of(ts_ms: int) -> int:
    """时间戳 → 时段槽。用本地时钟的小时数（0~23）。

    1 小时一次采样时，同槽样本天然落在"每天同一小时"，
    能自动吸收昼夜光照变化——这正是绝对阈值跨不了场景的根因（见报告 §5.3）。
    """
    return int((ts_ms // 3_600_000) % SLOT_COUNT)


class CameraState:
    """单个机位的全部持久状态。"""

    def __init__(self, camera_id: str, payload: Optional[Dict[str, Any]] = None):
        self.camera_id = camera_id
        payload = payload or {}
        self.created_ms: int = int(payload.get("createdMs") or int(time.time() * 1000))
        self.updated_ms: int = int(payload.get("updatedMs") or self.created_ms)
        self.name: str = str(payload.get("name") or camera_id)
        # 槽 → 样本列表（按时间升序）
        self.slots: Dict[int, List[SlotSample]] = {}
        for key, items in (payload.get("slots") or {}).items():
            try:
                slot = int(key)
            except (TypeError, ValueError):
                continue
            self.slots[slot] = [SlotSample.from_meta(m) for m in items if isinstance(m, dict)]
        # 判定历史（新→旧）
        self.history: List[Dict[str, Any]] = list(payload.get("history") or [])
        # 逐位哈希 → 首次出现时间（用于跨采样点 bit-exact 判据 D1）
        self.hashes: Dict[str, int] = {
            str(k): int(v) for k, v in (payload.get("hashes") or {}).items()
        }
        # 已见过的近重复哈希（aHash），跨槽检索用
        self.ahashes: List[Dict[str, Any]] = list(payload.get("ahashes") or [])
        # 参照物（人工标定的静止结构：门、楼角、围墙…）
        self.landmarks: List[Dict[str, Any]] = [
            dict(item) for item in (payload.get("landmarks") or []) if isinstance(item, dict)
        ]
        # 参照物模板（梯度幅值图），运行时从 npz 恢复，不写进 JSON
        self.landmark_patches: Dict[int, np.ndarray] = {}
        # 参照物**模板库**：每个参照物可保存多个外观模板（夜间/雨天/换季后由客户补充）。
        # 键为 "lm{landmark_id}t{template_id}"，值是元数据；模板图另存 npz。
        # 匹配时取所有模板中的最高分 —— 这样天气/时段变化不再把参照物判成"丢失"。
        self.templates: Dict[str, Dict[str, Any]] = {
            str(k): dict(v) for k, v in (payload.get("templates") or {}).items()
            if isinstance(v, dict)
        }
        self.template_patches: Dict[str, np.ndarray] = {}
        # 模板变更审计（谁在什么时候、基于什么判定结果添加了模板）
        self.audit: List[Dict[str, Any]] = list(payload.get("audit") or [])
        # 基线参考帧（分析尺度的灰度整帧）：给 ORB 全局配准用（估平移+旋转）。
        # 中位数背景只有 160×90，尺寸太小跑不了 ORB，所以单独存一张。
        self.ref_gray: Optional[np.ndarray] = None

    # ------------------------------------------------------------------ 查询
    def total_samples(self) -> int:
        return sum(len(v) for v in self.slots.values())

    def slot_samples(self, slot: int) -> List[SlotSample]:
        return list(self.slots.get(slot, []))

    def baseline_status(self) -> str:
        """基线成熟度。

        注意"就绪"的口径是**同一时段槽**攒够 MIN_SAMPLES_READY 个样本——
        1 小时一次采样时，这意味着要在同一小时上观测 3 天以上。
        这是刻意的：只有同时段的历史才能吸收昼夜光照变化。
        但"总样本够但还没有哪个槽够"应当报 warming 而不是 cold_start，
        否则用户会误以为一次都没采到。
        """
        if self.total_samples() == 0:
            return "empty"
        ready = sum(1 for items in self.slots.values() if len(items) >= MIN_SAMPLES_READY)
        if ready > 0:
            return "ready" if ready >= 3 else "warming"
        if self.total_samples() >= MIN_SAMPLES_READY:
            return "warming"
        return "cold_start"

    def baseline_thumbs(self, slot: Optional[int] = None) -> List[SlotSample]:
        if slot is not None:
            return self.slot_samples(slot)
        merged: List[SlotSample] = []
        for items in self.slots.values():
            merged.extend(items)
        merged.sort(key=lambda s: s.ts_ms)
        return merged

    def background(self, prefer_slot: Optional[int] = None
                   ) -> Tuple[Optional[np.ndarray], Optional[np.ndarray], Dict[str, Any]]:
        """算"按槽中位数背景"与对应的 MAD。

        优先用同时段槽的样本；样本不足时退回全部样本（跨时段），并在返回里说明，
        这样调用方能区分"同时段基线"与"降级基线"。
        """
        source = "slot"
        samples = self.slot_samples(prefer_slot) if prefer_slot is not None else []
        if len(samples) < MIN_SAMPLES_WARMING:
            samples = self.baseline_thumbs()
            source = "global"
        thumbs = [s.thumb for s in samples if s.thumb is not None]
        meta = {
            "source": source,
            "slot": prefer_slot,
            "samples": len(thumbs),
            "status": self.baseline_status(),
        }
        if not thumbs:
            return None, None, meta
        stack = np.stack([cv2.resize(t, THUMB_SIZE, interpolation=cv2.INTER_AREA) for t in thumbs])
        median_bg = np.median(stack, axis=0).astype(np.float32)
        if len(thumbs) >= 2:
            mad_bg = np.median(np.abs(stack - median_bg[None, ...]), axis=0).astype(np.float32)
        else:
            mad_bg = np.full_like(median_bg, MAD_FLOOR, dtype=np.float32)
        # MAD 必须有下限。若基线样本"过度稳定"（压缩流静止画面、多帧逐像素相同），
        # MAD 会趋近 0，于是 z = |Δ| / (1.4826·MAD) 被放大到几十 —— 正常的传感器噪声
        # 也会被判成篡改。2 个灰度级已经低于常见传感器的噪声水平，
        # 再小就没有分辨力了，不如直接封底。
        mad_bg = np.maximum(mad_bg, MAD_FLOOR)
        return median_bg, mad_bg, meta

    # ------------------------------------------------------------------ 摄入
    def add_sample(self, sample: SlotSample) -> int:
        """把一个样本并入基线库，返回它所属的时段槽。"""
        slot = slot_of(sample.ts_ms)
        items = self.slots.setdefault(slot, [])
        items.append(sample)
        items.sort(key=lambda s: s.ts_ms)
        if len(items) > SLOT_CAPACITY:
            del items[0:len(items) - SLOT_CAPACITY]
        self.updated_ms = max(self.updated_ms, sample.ts_ms)
        return slot

    # ------------------------------------------------------------------ 参照物
    def set_landmarks(self, landmarks: List[Dict[str, Any]],
                      patches: Dict[int, np.ndarray]) -> None:
        """写入人工标定的参照物及其主模板。

        重新标定会**重建**主模板（t0），并保留客户之前手工添加的其它模板
        （夜间/雨天），但会清掉那些所属参照物已经不存在的模板。
        """
        self.landmarks = [dict(item) for item in landmarks]
        self.landmark_patches = {int(k): v for k, v in patches.items() if v is not None}

        # 清掉失效参照物的模板
        valid_ids = set(self.landmark_patches.keys())
        for tid in list(self.templates.keys()):
            lm_id = int(self.templates[tid].get("landmarkId", -1))
            if lm_id not in valid_ids:
                self.templates.pop(tid, None)
                self.template_patches.pop(tid, None)
        # 重建主模板 t0
        for idx, patch in self.landmark_patches.items():
            tid = f"lm{idx}t0"
            self.templates[tid] = {
                "id": tid,
                "landmarkId": int(idx),
                "label": "主模板",
                "source": "calibration",
                "createdAt": int(time.time() * 1000),
            }
            self.template_patches[tid] = patch

    def clear_landmarks(self) -> None:
        self.landmarks = []
        self.landmark_patches = {}
        self.templates = {}
        self.template_patches = {}

    # ------------------------------------------------------------------ 模板库
    def templates_for(self, landmark_id: int) -> List[str]:
        """某参照物下所有模板 id（按添加时间排序）。"""
        ids = [
            tid for tid, meta in self.templates.items()
            if int(meta.get("landmarkId", -1)) == int(landmark_id) and tid in self.template_patches
        ]
        ids.sort(key=lambda t: int(self.templates[t].get("createdAt", 0)))
        return ids

    def add_template(self, landmark_id: int, patch: np.ndarray, label: str,
                     source: str, quality: Optional[Dict[str, Any]] = None,
                     verdict_at_time: str = "") -> str:
        """给某参照物追加一个模板，返回模板 id。超过上限按最旧淘汰。"""
        existing = self.templates_for(landmark_id)
        next_idx = 0
        for tid in existing:
            try:
                next_idx = max(next_idx, int(tid.split("t")[-1]) + 1)
            except ValueError:
                continue
        tid = f"lm{int(landmark_id)}t{next_idx}"
        self.templates[tid] = {
            "id": tid,
            "landmarkId": int(landmark_id),
            "label": (label or "附加模板")[:32],
            "source": source or "manual",
            "createdAt": int(time.time() * 1000),
            "quality": quality or {},
            "verdictAtTime": verdict_at_time,
        }
        self.template_patches[tid] = patch.astype(np.float32)

        # 数量上限：保留主模板 + 最近 MAX_TEMPLATES_PER_LANDMARK 个
        ids = self.templates_for(landmark_id)
        if len(ids) > MAX_TEMPLATES_PER_LANDMARK:
            overflow = len(ids) - MAX_TEMPLATES_PER_LANDMARK
            for tid_old in ids[:overflow]:
                if tid_old.endswith("t0"):
                    continue
                self.templates.pop(tid_old, None)
                self.template_patches.pop(tid_old, None)
        return tid

    def remove_template(self, template_id: str) -> bool:
        if template_id.endswith("t0"):
            return False  # 主模板不允许删除，避免参照物失去参照
        if template_id not in self.templates:
            return False
        self.templates.pop(template_id, None)
        self.template_patches.pop(template_id, None)
        return True

    def template_summary(self) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for tid, meta in self.templates.items():
            out.append({**{k: v for k, v in meta.items()},
                        "hasPatch": tid in self.template_patches})
        out.sort(key=lambda m: (int(m.get("landmarkId", 0)), int(m.get("createdAt", 0))))
        return out

    def push_audit(self, action: str, actor: str = "", detail: Optional[Dict[str, Any]] = None) -> None:
        self.audit.append({
            "ts": int(time.time() * 1000),
            "action": action,
            "actor": actor or "system",
            "detail": detail or {},
        })
        if len(self.audit) > AUDIT_CAPACITY:
            del self.audit[0:len(self.audit) - AUDIT_CAPACITY]

    def has_landmarks(self) -> bool:
        return len(self.anchor_landmarks()) > 0

    def anchor_landmarks(self) -> List[tuple]:
        """参照物（用于判断机位是否移动）。返回 (原始下标, 定义)。"""
        return [(i, lm) for i, lm in enumerate(self.landmarks)
                if str(lm.get("kind") or "landmark") != "ignore"]

    def ignore_regions(self) -> List[Dict[str, Any]]:
        """忽略区（不参与基线偏离统计，比如会被频繁挪动的桌面/货架）。"""
        return [lm for lm in self.landmarks if str(lm.get("kind")) == "ignore"]

    def landmark_summary(self) -> List[Dict[str, Any]]:
        return [
            {**{k: v for k, v in item.items()},
             "kind": str(item.get("kind") or "landmark"),
             "hasPatch": idx in self.landmark_patches}
            for idx, item in enumerate(self.landmarks)
        ]

    def remember(self, digest: str, ts_ms: int, ahash: int = 0) -> None:
        """登记哈希（D1 跨采样点 bit-exact 判据的依据）。"""
        if digest:
            self.hashes[digest] = int(ts_ms)
            if len(self.hashes) > HASH_CAPACITY:
                oldest = sorted(self.hashes.items(), key=lambda kv: kv[1])[:len(self.hashes) - HASH_CAPACITY]
                for key, _ in oldest:
                    self.hashes.pop(key, None)
        if ahash:
            self.ahashes.append({"aHash": int(ahash), "tsMs": int(ts_ms)})
            if len(self.ahashes) > HASH_CAPACITY:
                del self.ahashes[0:len(self.ahashes) - HASH_CAPACITY]

    def push_history(self, entry: Dict[str, Any]) -> None:
        self.history.insert(0, entry)
        if len(self.history) > HISTORY_CAPACITY:
            del self.history[HISTORY_CAPACITY:]

    def to_payload(self) -> Dict[str, Any]:
        return {
            "cameraId": self.camera_id,
            "name": self.name,
            "createdMs": self.created_ms,
            "updatedMs": self.updated_ms,
            "slots": {
                str(slot): [s.meta() for s in items]
                for slot, items in sorted(self.slots.items())
            },
            "history": self.history,
            "hashes": self.hashes,
            "ahashes": self.ahashes,
            "landmarks": self.landmarks,
            "templates": self.templates,
            "audit": self.audit,
        }

    def slot_summary(self) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for slot in range(SLOT_COUNT):
            items = self.slots.get(slot, [])
            out.append({
                "slot": slot,
                "hour": slot,
                "samples": len(items),
                "ready": len(items) >= MIN_SAMPLES_READY,
                "latestTs": items[-1].ts_ms if items else 0,
                "entropy": round(float(np.median([s.entropy for s in items])), 3) if items else 0.0,
                "sharpness": round(float(np.median([s.sharpness for s in items])), 2) if items else 0.0,
            })
        return out


class CameraStore:
    """机位状态的加载 / 保存 / 索引。线程安全（进程内单锁）。"""

    def __init__(self, root: str):
        self.root = root
        os.makedirs(self.root, exist_ok=True)
        self._lock = threading.RLock()
        self._cache: Dict[str, CameraState] = {}
        self._index_path = os.path.join(self.root, "cameras.json")
        self._index: Dict[str, Dict[str, Any]] = self._load_index()

    # ------------------------------------------------------------------ 索引
    def _load_index(self) -> Dict[str, Dict[str, Any]]:
        try:
            with open(self._index_path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save_index(self) -> None:
        with open(self._index_path, "w", encoding="utf-8") as fh:
            json.dump(self._index, fh, ensure_ascii=False, indent=2)

    # ------------------------------------------------------------------ 文件
    def _json_path(self, camera_id: str) -> str:
        return os.path.join(self.root, f"{camera_id}.json")

    def _npz_path(self, camera_id: str) -> str:
        return os.path.join(self.root, f"{camera_id}.npz")

    @staticmethod
    def _safe_id(camera_id: str) -> str:
        cleaned = "".join(ch for ch in str(camera_id) if ch.isalnum() or ch in "-_")
        return cleaned or uuid.uuid4().hex[:12]

    # ------------------------------------------------------------------ 读
    def get(self, camera_id: str) -> CameraState:
        safe = self._safe_id(camera_id)
        with self._lock:
            cached = self._cache.get(safe)
            if cached is not None:
                return cached
            payload: Dict[str, Any] = {}
            try:
                with open(self._json_path(safe), "r", encoding="utf-8") as fh:
                    payload = json.load(fh)
            except (OSError, ValueError):
                payload = {}
            state = CameraState(safe, payload)
            self._restore_thumbs(state, safe)
            self._cache[safe] = state
            return state

    def _restore_thumbs(self, state: CameraState, safe: str) -> None:
        path = self._npz_path(safe)
        if not os.path.exists(path):
            return
        try:
            with np.load(path) as data:
                for slot, items in state.slots.items():
                    for idx, sample in enumerate(items):
                        key = f"s{slot}_{idx}"
                        if key in data:
                            sample.thumb = data[key].astype(np.uint8)
                # 参照物模板：梯度幅值是 float32，单独存
                for idx, _ in enumerate(state.landmarks):
                    key = f"lm{idx}"
                    if key in data:
                        state.landmark_patches[idx] = data[key].astype(np.float32)
                # 模板库（多套外观模板）
                for tid in state.templates.keys():
                    key = "tpl_" + tid
                    if key in data:
                        state.template_patches[tid] = data[key].astype(np.float32)
                if "ref" in data:
                    state.ref_gray = data["ref"].astype(np.uint8)
        except (OSError, ValueError):
            pass

    # ------------------------------------------------------------------ 写
    def save(self, state: CameraState) -> None:
        safe = self._safe_id(state.camera_id)
        with self._lock:
            tmp_json = self._json_path(safe) + ".tmp"
            with open(tmp_json, "w", encoding="utf-8") as fh:
                json.dump(state.to_payload(), fh, ensure_ascii=False)
            os.replace(tmp_json, self._json_path(safe))

            arrays: Dict[str, np.ndarray] = {}
            for slot, items in state.slots.items():
                for idx, sample in enumerate(items):
                    if sample.thumb is not None:
                        arrays[f"s{slot}_{idx}"] = cv2.resize(
                            sample.thumb, THUMB_SIZE, interpolation=cv2.INTER_AREA
                        ).astype(np.uint8)
            # 参照物模板（梯度幅值，float32；体积很小，几十 KB 级）
            for idx, patch in state.landmark_patches.items():
                if patch is not None and patch.size:
                    arrays[f"lm{idx}"] = patch.astype(np.float32)
            # 模板库（多套外观模板）
            for tid, patch in state.template_patches.items():
                if patch is not None and patch.size:
                    arrays["tpl_" + tid] = patch.astype(np.float32)
            # 基线参考帧（整帧灰度，约 230 KB）：ORB 全局配准用
            if state.ref_gray is not None and state.ref_gray.size:
                arrays["ref"] = state.ref_gray.astype(np.uint8)
            if arrays:
                tmp_npz = self._npz_path(safe) + ".tmp.npz"
                np.savez_compressed(tmp_npz, **arrays)
                os.replace(tmp_npz, self._npz_path(safe))

            self._index[safe] = {
                "cameraId": safe,
                "name": state.name,
                "createdMs": state.created_ms,
                "updatedMs": state.updated_ms,
                "samples": state.total_samples(),
                "status": state.baseline_status(),
            }
            self._save_index()
            self._cache[safe] = state

    # ------------------------------------------------------------------ 管理
    def list_cameras(self) -> List[Dict[str, Any]]:
        with self._lock:
            return sorted(self._index.values(), key=lambda item: item.get("updatedMs", 0), reverse=True)

    def delete(self, camera_id: str) -> bool:
        safe = self._safe_id(camera_id)
        with self._lock:
            self._cache.pop(safe, None)
            self._index.pop(safe, None)
            self._save_index()
            removed = False
            for path in (self._json_path(safe), self._npz_path(safe)):
                try:
                    if os.path.exists(path):
                        os.remove(path)
                        removed = True
                except OSError:
                    pass
            return removed

    def reset(self, camera_id: str) -> CameraState:
        """清空基线库但保留机位身份（"重设基准"）。

        注意**参照物要保留**：它是机位配置（"这个画面里哪块建筑是静止的"），
        不是基线样本。重设基准通常是因为光照/季节变了，建筑物并没有变，
        让用户重新框一遍是没有必要的。
        """
        safe = self._safe_id(camera_id)
        with self._lock:
            old = self._cache.get(safe)
            if old is None:
                # 缓存里没有就先从磁盘读一次，别把已标定的参照物丢掉
                try:
                    self.get(safe)
                    old = self._cache.get(safe)
                except Exception:  # noqa: BLE001
                    old = None
            state = CameraState(safe)
            if old is not None:
                state.name = old.name
                state.landmarks = [dict(item) for item in old.landmarks]
                state.landmark_patches = dict(old.landmark_patches)
            self.save(state)
            return state
