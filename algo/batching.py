"""动态拼批（dynamic batching）执行器。

对应需求文档 3.4.3 的"方案 C：单进程 + 动态拼批 + 推理独占线程"：

* **只有一个线程**真正调用 ``model.predict``，不存在多线程共享 YOLO 实例的竞态；
* 请求方（HTTP 处理线程 / 视频检测线程）把图像丢进队列并拿到 Future，不阻塞模型；
* 工作线程被唤醒后，在 ``max_wait_ms`` 的时间窗内尽量攒满 ``max_batch`` 张再推理一次，
  单张推理的固定开销被摊薄，吞吐成倍提升；
* 队列有界，天然形成背压，不会无限堆积。

在 GPU 服务器上如果需要极限吞吐，可以把 ``predict_fn`` 换成
``scripts_bench/server_fast.py`` 的双进程 + 共享内存版本，本类接口保持不变。
"""

from __future__ import annotations

import queue
import threading
import time
from concurrent.futures import Future
from typing import Any, Callable, List, Optional, Sequence


class DynamicBatcher:
    def __init__(
        self,
        predict_fn: Callable[[Sequence[Any]], List[Any]],
        max_batch: int = 16,
        max_wait_ms: float = 2.0,
        queue_size: int = 512,
        name: str = "batcher",
    ) -> None:
        self._predict = predict_fn
        self._max_batch = max(1, int(max_batch))
        self._max_wait = max(0.0, float(max_wait_ms)) / 1000.0
        self._name = name
        self._queue: "queue.Queue[Optional[tuple]]" = queue.Queue(maxsize=queue_size)
        self._thread = threading.Thread(target=self._loop, name=f"{name}-worker", daemon=True)
        self._started = False
        self._closed = False
        self._lock = threading.Lock()
        self.stats = {"batches": 0, "images": 0, "maxObservedBatch": 0}

    # ---------------------------------------------------------------- lifecycle
    def start(self) -> "DynamicBatcher":
        with self._lock:
            if not self._started:
                self._thread.start()
                self._started = True
        return self

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
        try:
            self._queue.put_nowait(None)
        except queue.Full:
            pass

    # ------------------------------------------------------------------- submit
    def submit(self, image: Any) -> Future:
        if not self._started:
            self.start()
        if self._closed:
            raise RuntimeError("batcher 已关闭")
        future: Future = Future()
        self._queue.put((image, future), timeout=30)
        return future

    def submit_many(self, images: Sequence[Any], timeout: float = 120.0) -> List[Any]:
        """一次性提交多张图并等待全部结果（顺序与输入一致）。"""
        futures = [self.submit(image) for image in images]
        return [f.result(timeout=timeout) for f in futures]

    # ------------------------------------------------------------------ worker
    def _loop(self) -> None:
        while True:
            item = self._queue.get()
            if item is None:
                break
            batch = [item]
            deadline = time.perf_counter() + self._max_wait
            while len(batch) < self._max_batch:
                remaining = deadline - time.perf_counter()
                if remaining <= 0:
                    break
                try:
                    nxt = self._queue.get(timeout=remaining)
                except queue.Empty:
                    break
                if nxt is None:
                    self._closed = True
                    break
                batch.append(nxt)

            images = [b[0] for b in batch]
            try:
                outputs = self._predict(images)
                if not isinstance(outputs, (list, tuple)) or len(outputs) != len(images):
                    raise RuntimeError("predict 返回值与输入数量不匹配")
                errors = [None] * len(images)
            except Exception as exc:  # 推理异常必须回填给每个 Future，否则调用方挂死
                outputs = [None] * len(images)
                errors = [exc] * len(images)

            with self._lock:
                self.stats["batches"] += 1
                self.stats["images"] += len(images)
                self.stats["maxObservedBatch"] = max(self.stats["maxObservedBatch"], len(images))

            for (_, future), output, error in zip(batch, outputs, errors):
                if future.done():
                    continue
                if error is not None:
                    future.set_exception(error)
                else:
                    future.set_result(output)

            if self._closed and self._queue.empty():
                break

    # -------------------------------------------------------------------- stats
    def snapshot(self) -> dict:
        with self._lock:
            stats = dict(self.stats)
        batches = stats["batches"] or 1
        return {
            **stats,
            "avgBatch": round(stats["images"] / batches, 2),
            "maxBatch": self._max_batch,
            "maxWaitMs": round(self._max_wait * 1000, 2),
            "queueSize": self._queue.qsize(),
        }
