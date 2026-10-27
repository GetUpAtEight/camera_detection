"""验证动态拼批执行器的行为：并发提交应被合并成更大的 batch。

    .venv/Scripts/python.exe tests/test_batcher.py
"""

from __future__ import annotations

import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from batching import DynamicBatcher  # noqa: E402


def main() -> int:
    batches: list[int] = []
    errors: list[str] = []

    def fake_predict(images):
        batches.append(len(images))
        time.sleep(0.02)  # 模拟一次推理的固定开销
        return [{"count": int(img.split("-")[-1])} for img in images]

    batcher = DynamicBatcher(fake_predict, max_batch=16, max_wait_ms=3).start()

    # 16 个线程各自提交 1 张，模拟 16 路并发请求
    results: dict[int, int] = {}

    def submit(idx: int) -> None:
        try:
            results[idx] = batcher.submit(f"img-{idx}").result(timeout=10)["count"]
        except Exception as exc:  # pragma: no cover
            errors.append(str(exc))

    threads = [threading.Thread(target=submit, args=(i,)) for i in range(16)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # 单次批量提交 20 张（超过 max_batch，应被切成 2 批）
    many = batcher.submit_many([f"img-{i}" for i in range(20)])

    stats = batcher.snapshot()
    print("每批实际大小:", batches)
    print("拼批统计:", stats)
    print("结果数:", len(results), "错误:", errors)

    ok = (
        not errors
        and len(results) == 16
        and all(results[i] == i for i in range(16))
        and len(many) == 20
        and any(b > 1 for b in batches)
        and max(batches) <= 16
    )
    if not ok:
        print("[FAIL] 拼批行为不符合预期")
        return 1

    # 推理异常必须回填到 Future，不能挂死
    def boom(images):
        raise RuntimeError("模拟推理失败")

    bad = DynamicBatcher(boom, max_batch=4).start()
    try:
        bad.submit("img-0").result(timeout=5)
        print("[FAIL] 异常没有被回填")
        return 1
    except RuntimeError as exc:
        print(f"异常回填正常: {exc}")

    print("[OK] 动态拼批与异常回填均正常")
    batcher.close()
    bad.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
