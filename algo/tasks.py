"""算法层异步任务存储（内存实现，演示够用）。

SpringBoot 提交视频检测后拿到 taskId，然后轮询 ``GET /detect/tasks/{id}``。
任务完成后结果在内存中保留 ``TASK_TTL_SECONDS`` 秒，之后自动回收。
"""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

PENDING = "pending"
RUNNING = "running"
DONE = "done"
FAILED = "failed"
CANCELLED = "cancelled"


@dataclass
class AlgoTask:
    id: str
    media_name: str
    media_type: str
    algorithms: List[str]
    status: str = PENDING
    progress: float = 0.0
    message: str = "排队中"
    error: Optional[str] = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    elapsed_ms: int = 0
    media: Dict[str, Any] = field(default_factory=dict)
    frames: List[Dict[str, Any]] = field(default_factory=list)
    total_frames: int = 0
    tampered_frames: int = 0
    cancelled: bool = False
    timings: Dict[str, float] = field(default_factory=dict)
    # 分段检测：本次任务处理的时间窗（绝对毫秒）与逐段汇总
    start_ms: Optional[int] = None
    end_ms: Optional[int] = None
    segments: List[Dict[str, Any]] = field(default_factory=list)
    ref_mode: str = "first-frame"
    # 结果在后台线程增量写入，读取时用它加锁，避免序列化到一半
    lock: Any = field(default_factory=threading.Lock, repr=False, compare=False)

    def snapshot(self, include_frames: bool = False, offset: int = 0, limit: int = 0) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "taskId": self.id,
            "mediaName": self.media_name,
            "mediaType": self.media_type,
            "algorithms": self.algorithms,
            "status": self.status,
            "progress": round(self.progress, 4),
            "message": self.message,
            "error": self.error,
            "elapsedMs": self.elapsed_ms,
            "media": self.media,
            "totalFrames": self.total_frames,
            "tamperedFrames": self.tampered_frames,
            "frameCount": len(self.frames),
            "timings": {k: round(v, 3) for k, v in self.timings.items()},
            "startMs": self.start_ms,
            "endMs": self.end_ms,
            "refMode": self.ref_mode,
            "segments": self.segments,
        }
        if include_frames:
            frames = self.frames
            if limit > 0:
                frames = frames[offset: offset + limit]
            payload["frames"] = frames
            payload["offset"] = offset
            payload["limit"] = limit
        return payload


class TaskStore:
    def __init__(self, ttl_seconds: int = 3600) -> None:
        self._tasks: Dict[str, AlgoTask] = {}
        self._lock = threading.Lock()
        self._ttl = ttl_seconds

    def create(self, media_name: str, media_type: str, algorithms: List[str]) -> AlgoTask:
        task = AlgoTask(
            id=uuid.uuid4().hex,
            media_name=media_name,
            media_type=media_type,
            algorithms=algorithms,
        )
        with self._lock:
            self._tasks[task.id] = task
            self._evict_locked()
        return task

    def get(self, task_id: str) -> Optional[AlgoTask]:
        with self._lock:
            return self._tasks.get(task_id)

    def update(self, task: AlgoTask, **fields: Any) -> None:
        with self._lock:
            for key, value in fields.items():
                setattr(task, key, value)
            task.updated_at = time.time()

    def delete(self, task_id: str) -> bool:
        with self._lock:
            return self._tasks.pop(task_id, None) is not None

    def _evict_locked(self) -> None:
        now = time.time()
        expired = [
            tid
            for tid, task in self._tasks.items()
            if task.status in (DONE, FAILED, CANCELLED) and now - task.updated_at > self._ttl
        ]
        for tid in expired:
            self._tasks.pop(tid, None)

    def stats(self) -> Dict[str, Any]:
        with self._lock:
            by_status: Dict[str, int] = {}
            for task in self._tasks.values():
                by_status[task.status] = by_status.get(task.status, 0) + 1
        return {"total": len(self._tasks), "byStatus": by_status}
