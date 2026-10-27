"""低频稀疏采样算法的自测（无需 FastAPI，纯 numpy/OpenCV）。

跑法::

    cd algo && .venv/Scripts/python tests/test_sparse.py

覆盖的场景（全部是合成画面，确定性可复现）：

=====================================  ============================================
场景                                    期望结论
=====================================  ============================================
正常画面连续多次采样                    normal（且样本并入基线）
画面被整块遮挡（持续）                  persistent_covered
画面失焦（高斯模糊，持续）              persistent_defocused
同一帧重复提交（跨采样点）              freeze_replay
静止画面 + 人为抹掉噪声（Q3）           freeze_replay
burst 内一致平移（正在被转动）          being_rotated
历史偏离高但 burst 内高动态             transient_event（不告警）
曝光在 burst 内剧烈变化                 information_insufficient（不并入基线）
=====================================  ============================================
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile

import cv2
import numpy as np

# 允许 `python tests/test_sparse.py` 直接跑
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sparse import CameraStore, SparseEngine  # noqa: E402
from sparse import metrics  # noqa: E402
from sparse.store import slot_of  # noqa: E402

H, W = 240, 320
PASS: list[str] = []
FAIL: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        PASS.append(name)
        print(f"  [PASS] {name}")
    else:
        FAIL.append(name)
        print(f"  [FAIL] {name} {detail}")


def scene(seed: int = 0, brightness: int = 0) -> np.ndarray:
    """合成一个"有纹理、有边缘、有噪声"的固定机位场景。"""
    rng = np.random.default_rng(1234)  # 场景内容固定
    base = np.zeros((H, W, 3), np.uint8)
    base[:, :] = (60, 70, 80)
    # 几何结构：矩形、圆、斜线
    cv2.rectangle(base, (30, 40), (150, 150), (200, 200, 210), -1)
    cv2.circle(base, (240, 90), 45, (120, 180, 220), -1)
    cv2.line(base, (20, 200), (300, 160), (250, 250, 250), 4)
    cv2.putText(base, "CAM-01", (200, 220), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2)
    # 纹理
    noise_tex = rng.integers(-18, 18, size=(H, W, 1), dtype=np.int16)
    base = np.clip(base.astype(np.int16) + noise_tex, 0, 255).astype(np.uint8)
    if brightness:
        base = np.clip(base.astype(np.int16) + brightness, 0, 255).astype(np.uint8)
    return base


def add_sensor_noise(frame: np.ndarray, sigma: float = 3.0, seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    noise = rng.normal(0, sigma, frame.shape)
    return np.clip(frame.astype(np.float32) + noise, 0, 255).astype(np.uint8)


def burst(frame: np.ndarray, k: int = 3, base_seed: int = 0) -> list:
    """模拟一次抽样内的 K 帧：同一场景 + 每帧独立的传感器噪声。"""
    return [add_sensor_noise(frame, 3.0, base_seed + i) for i in range(k)]


def day_ms(day: int, hour: int) -> int:
    """构造"第 day 天第 hour 点"的时间戳（用于跨天累积基线）。"""
    base = 1_700_000_000_000  # 固定基准，保证可复现
    return base + day * 86_400_000 + hour * 3_600_000


def main() -> int:
    tmp = tempfile.mkdtemp(prefix="sparse_test_")
    print(f"临时数据目录：{tmp}\n")
    store = CameraStore(tmp)
    engine = SparseEngine(store)

    try:
        # ---------------------------------------------------------------- 1. 基线累积
        print("[1] 正常画面累积基线（同一时段槽 3 次采样）")
        # 注意：每次采样的传感器噪声种子要**不同**，否则基线各样本逐像素相同，
        # MAD 退化为 0，z 会被放大到失真（真实相机每次采样噪声都不同）。
        for i in range(4):
            result = engine.ingest("cam-normal", burst(scene(), base_seed=i * 10),
                                   ts_ms=day_ms(0, 9) + i * 1000)
        check("正常样本不告警", result["verdict"] == "normal", result["verdict"])
        check("正常样本并入基线", result["baseline"]["admitted"] is True)
        check("基线可积累", result["baseline"]["samples"] >= 3, str(result["baseline"]))
        print(f"    verdict={result['verdict']} samples={result['baseline']['samples']} "
              f"dHist={result['matrix'].get('dHist')}")

        # ---------------------------------------------------------------- 2. 持续遮挡
        print("\n[2] 持续遮挡（大面积纯色覆盖）")
        covered = scene().copy()
        cv2.rectangle(covered, (0, 0), (W, H), (35, 38, 40), -1)  # 几乎全遮
        covered = add_sensor_noise(covered, 3.0, 99)
        r = engine.ingest("cam-normal", burst(covered, base_seed=500), ts_ms=day_ms(0, 9) + 10_000)
        check("遮挡被判为 persistent_covered", r["verdict"] == "persistent_covered", r["verdict"])
        check("遮挡会告警", r["alarm"] is True)
        print(f"    verdict={r['verdict']} type={r['tamperType']} risk={r['risk']} "
              f"entropyDrop={r['layers']['L0'].get('entropyDrop')}")

        # ---------------------------------------------------------------- 3. 持续失焦
        print("\n[3] 持续失焦（高斯模糊）")
        blurred = cv2.GaussianBlur(scene(), (0, 0), 7.0)
        blurred = add_sensor_noise(blurred, 3.0, 101)
        r = engine.ingest("cam-normal", burst(blurred, base_seed=600), ts_ms=day_ms(0, 9) + 20_000)
        check("失焦被判为 persistent_defocused", r["verdict"] == "persistent_defocused", r["verdict"])
        print(f"    verdict={r['verdict']} sharpnessDrop={r['layers']['L0'].get('sharpnessDrop')}")

        # ---------------------------------------------------------------- 4. 跨采样点 bit-exact
        print("\n[4] 冻结 / 重放：同一帧在 1 小时后重复提交")
        fixed = add_sensor_noise(scene(), 3.0, 777)
        engine.ingest("cam-freeze", burst(fixed, k=3, base_seed=0), ts_ms=day_ms(0, 9))
        r = engine.ingest("cam-freeze", burst(fixed, k=3, base_seed=0), ts_ms=day_ms(0, 10))
        check("跨采样点重复被判为冻结", r["verdict"] == "freeze_replay", r["verdict"])
        print(f"    verdict={r['verdict']} reasons={r['reasons'][0][:60]}...")

        # ---------------------------------------------------------------- 5. Q3 无噪声静止
        print("\n[5] 冻结 / 重放：静止且无传感器噪声（象限 Q3）")
        # 先有一次正常抽样（真实噪声），这样机位已经有历史样本；
        # 首次抽样就凭 Q3 定罪会误伤"压缩流静止画面"，所以引擎要求先有历史。
        r_first = engine.ingest("cam-q3", burst(scene(), base_seed=800), ts_ms=day_ms(0, 8))
        check("首次无噪声样本不定罪", r_first["verdict"] != "freeze_replay", r_first["verdict"])
        clean = scene()  # 场景纹理是固定的，但这里**故意不加**传感器噪声
        # 连 3 帧完全相同 ⇒ temporalSigma=0 且 sigmaSpread=0
        r = engine.ingest("cam-q3", [clean.copy(), clean.copy(), clean.copy()], ts_ms=day_ms(0, 9))
        l1 = r["layers"]["L1"]
        check("被识别为无噪声静止", l1.get("quadrant") == "Q3", str(l1.get("quadrant")))
        check("已有历史时判为冻结", r["verdict"] == "freeze_replay", r["verdict"])
        print(f"    quadrant={l1.get('quadrant')} sigma={l1.get('sigmaPerFrame')} "
              f"spread={l1.get('sigmaSpread')} verdict={r['verdict']}")

        # ---------------------------------------------------------------- 6. 合规静态
        print("\n[6] 合规静态场景（静止但有独立噪声，象限 Q2）不应误报")
        static = scene()
        r = engine.ingest("cam-q2", burst(static, k=3, base_seed=900), ts_ms=day_ms(0, 9))
        l1 = r["layers"]["L1"]
        check("合规静态落在 Q2", l1.get("quadrant") == "Q2", str(l1.get("quadrant")))
        check("合规静态不判冻结", r["verdict"] != "freeze_replay", r["verdict"])
        print(f"    quadrant={l1.get('quadrant')} sigma={l1.get('sigmaPerFrame')} verdict={r['verdict']}")

        # ---------------------------------------------------------------- 7. 正在被转动
        print("\n[7] 正在被转动：burst 内一致平移")
        base = scene()
        shifted = []
        for i in range(4):
            matrix = np.float32([[1, 0, i * 6], [0, 1, i * 3]])
            moved = cv2.warpAffine(base, matrix, (W, H), borderMode=cv2.BORDER_REPLICATE)
            shifted.append(add_sensor_noise(moved, 2.0, 300 + i))
        r = engine.ingest("cam-rot", shifted, ts_ms=day_ms(0, 9))
        check("一致平移被判为正在被转动", r["verdict"] == "being_rotated", r["verdict"])
        print(f"    verdict={r['verdict']} orb={r['layers']['L1'].get('intraOrbTranslation')}px "
              f"inlier={r['layers']['L1'].get('intraOrbInlier')}")

        # ---------------------------------------------------------------- 8. 瞬时事件
        print("\n[8] 瞬时事件：历史偏离高 + burst 内高动态 ⇒ 不告警")
        # 先建立基线（每次采样用不同噪声种子）
        for i in range(3):
            engine.ingest("cam-transient", burst(scene(), base_seed=i * 10),
                          ts_ms=day_ms(0, 9) + i * 1000)
        # burst 里丢了几个强对照的大色块且位置在动（模拟有人+物体经过）
        frames = []
        for i in range(4):
            f = scene().copy()
            cv2.rectangle(f, (20 + i * 40, 40), (120 + i * 40, 180), (0, 0, 255), -1)
            frames.append(add_sensor_noise(f, 3.0, 400 + i))
        r = engine.ingest("cam-transient", frames, ts_ms=day_ms(0, 9) + 30_000)
        check("瞬时事件不告警", r["alarm"] is False, r["verdict"])
        check("瞬时事件被识别", r["verdict"] == "transient_event", r["verdict"])
        print(f"    verdict={r['verdict']} dHist={r['matrix'].get('dHist')} "
              f"dBurst={r['matrix'].get('dBurst')} cell={r['matrix'].get('cell')}")

        # ---------------------------------------------------------------- 9. 曝光不稳定
        print("\n[9] burst 内曝光剧烈变化 ⇒ 样本不可用")
        unstable = []
        for i, delta in enumerate([-60, -20, 20, 60]):
            unstable.append(add_sensor_noise(scene(brightness=delta), 3.0, 500 + i))
        r = engine.ingest("cam-expo", unstable, ts_ms=day_ms(0, 9))
        check("曝光不稳判为样本不可用", r["verdict"] == "information_insufficient", r["verdict"])
        check("不可用样本不并入基线", r["baseline"]["admitted"] is False)
        print(f"    verdict={r['verdict']} lumaDrift={r['layers']['L1'].get('lumaDrift')}")

        # ---------------------------------------------------------------- 10. 持久化
        print("\n[10] 机位状态持久化：重新加载后基线仍在")
        store.save(store.get("cam-normal"))
        fresh = CameraStore(tmp)
        reloaded = fresh.get("cam-normal")
        check("重新加载后样本数不丢", reloaded.total_samples() >= 3, str(reloaded.total_samples()))
        check("重新加载后缩略图可恢复",
              any(s.thumb is not None for s in reloaded.baseline_thumbs()))

        # ---------------------------------------------------------------- 11. 时段槽
        print("\n[11] 时段槽与背景中位数")
        check("slot_of 按小时分槽", slot_of(day_ms(0, 9)) != slot_of(day_ms(0, 14)))
        median_bg, mad_bg, meta = reloaded.background(slot_of(day_ms(0, 9)))
        check("中位数背景可算出", median_bg is not None)
        check("MAD 与中位数同形状", mad_bg is not None and mad_bg.shape == median_bg.shape)
        print(f"    baseline source={meta.get('source')} samples={meta.get('samples')} "
              f"shape={None if median_bg is None else median_bg.shape}")

        # ---------------------------------------------------------------- 12. 已发生的移位
        print("\n[12] 已发生的移位（镜头被转动后停住）")
        # 关键点：镜头转过去停住之后，burst 内完全没有运动，边缘/熵/清晰度也几乎不变，
        # 只能靠"与基线背景存在一致全局平移"判定。
        moved_scene = cv2.warpAffine(scene(), np.float32([[1, 0, 26], [0, 1, 12]]), (W, H),
                                     borderMode=cv2.BORDER_REPLICATE)
        r = engine.ingest("cam-normal", burst(moved_scene, base_seed=1200),
                          ts_ms=day_ms(0, 9) + 40_000)
        check("移位被判为 persistent_moved", r["verdict"] == "persistent_moved", r["verdict"])
        shift = (r["layers"].get("L0") or {}).get("bgShift") or {}
        print(f"    verdict={r['verdict']} bgShift={shift.get('magnitude')}px "
              f"response={shift.get('response')}")

        # ---------------------------------------------------------------- 13. 静止场景不误报移位
        print("\n[13] 同一场景的新噪声帧不应被判移位")
        r = engine.ingest("cam-normal", burst(scene(), base_seed=1500),
                          ts_ms=day_ms(0, 9) + 50_000)
        check("静止场景不判移位", r["verdict"] != "persistent_moved", r["verdict"])
        print(f"    verdict={r['verdict']}")

        # ---------------------------------------------------------------- 14. 参照物标定
        print("\n[14] 参照物标定：静止结构一致位移 ⇒ 移位")
        # 在场景里框三个"建筑物"：矩形块、圆、斜线附近（归一化坐标）
        engine.ingest("cam-lm", burst(scene(), base_seed=2000), ts_ms=day_ms(0, 9))
        engine.ingest("cam-lm", burst(scene(), base_seed=2010), ts_ms=day_ms(0, 9) + 1000)
        calib = engine.calibrate_landmarks("cam-lm", [
            {"x": 0.08, "y": 0.15, "w": 0.20, "h": 0.25, "label": "左楼角"},
            {"x": 0.62, "y": 0.20, "w": 0.22, "h": 0.22, "label": "右侧门"},
            {"x": 0.30, "y": 0.60, "w": 0.35, "h": 0.12, "label": "围墙线"},
        ], frame=scene())
        check("参照物标定成功", calib["count"] == 3, str(calib.get("count")))

        moved_scene = cv2.warpAffine(scene(), np.float32([[1, 0, 18], [0, 1, 9]]), (W, H),
                                     borderMode=cv2.BORDER_REPLICATE)
        r = engine.ingest("cam-lm", burst(moved_scene, base_seed=2020),
                          ts_ms=day_ms(0, 9) + 20_000)
        lm = (r["layers"].get("L0") or {}).get("landmarks") or {}
        cons = lm.get("consensus") or {}
        check("参照物检出一致位移", cons.get("moved") is True, str(cons))
        check("参照物判定为持续移位", r["verdict"] == "persistent_moved", r["verdict"])
        print(f"    verdict={r['verdict']} mag={cons.get('magnitude')}px "
              f"spread={cons.get('spread')}px count={cons.get('count')}")

        # ---------------------------------------------------------------- 15. 局部变化不误判移位
        print("\n[15] 画面局部变化（树影/贴告示）不应判成移位")
        local_change = scene().copy()
        # 在参照物之外的位置贴一块东西（模拟有人贴告示/树影变化）
        cv2.rectangle(local_change, (5, 190), (95, 235), (30, 200, 30), -1)
        r = engine.ingest("cam-lm", burst(local_change, base_seed=2030),
                          ts_ms=day_ms(0, 9) + 30_000)
        cons2 = ((r["layers"].get("L0") or {}).get("landmarks") or {}).get("consensus") or {}
        check("局部变化不判为移位", r["verdict"] != "persistent_moved", r["verdict"])
        print(f"    verdict={r['verdict']} consensusMoved={cons2.get('moved')} "
              f"mag={cons2.get('magnitude')}px")

        # ---------------------------------------------------------------- 16. 参照物持久化
        print("\n[16] 参照物持久化")
        fresh2 = CameraStore(tmp)
        st2 = fresh2.get("cam-lm")
        check("重新加载后参照物仍在", len(st2.landmarks) == 3, str(len(st2.landmarks)))
        check("重新加载后模板可恢复", len(st2.landmark_patches) == 3,
              str(len(st2.landmark_patches)))

        # ---------------------------------------------------------------- 17. 大幅转动
        print("\n[17] 大幅转动（15°）：参照物移出小窗也必须能判出来")
        # 1 小时采样下，转动几乎必然发生在两次抽样之间。
        # 真的转了 15° 时建筑会大幅移出原来的位置，靠 ±5% 的小搜索窗必然失配；
        # 多尺度全幅粗搜要能把它们找回来。
        base_scene = scene()
        center = (W / 2.0, H / 2.0)
        rot = cv2.getRotationMatrix2D(center, 15.0, 1.0)
        rotated = cv2.warpAffine(base_scene, rot, (W, H), borderMode=cv2.BORDER_REPLICATE)
        r = engine.ingest("cam-lm", burst(rotated, base_seed=2100),
                          ts_ms=day_ms(0, 9) + 40_000)
        lmi = (r["layers"].get("L0") or {}).get("landmarks") or {}
        aff = lmi.get("affine") or {}
        check("大幅转动判定为移位/转动", r["verdict"] == "persistent_moved", r["verdict"])
        print(f"    verdict={r['verdict']} consensus={lmi.get('consensus', {}).get('available')} "
              f"affineRot={aff.get('rotationDeg')}° inlier={aff.get('inlierRatio')}")

        # ---------------------------------------------------------------- 18. 换视野
        print("\n[18] 画面被换成别处（参照物全失配、但不遮挡也不失焦）⇒ 仍判移位")
        other = np.full((H, W, 3), 40, np.uint8)
        for i in range(14):
            cv2.rectangle(other, (8 + i * 22, 20 + (i % 5) * 30),
                          (8 + i * 22 + 60, 20 + (i % 5) * 30 + 70),
                          (60 + i * 12, 120, 200 - i * 8), -1)
        for i in range(9):
            cv2.circle(other, (40 + i * 30, 200), 18, (220, 180, 90), -1)
        r = engine.ingest("cam-lm", burst(other, base_seed=2200),
                          ts_ms=day_ms(0, 9) + 50_000)
        lmi2 = (r["layers"].get("L0") or {}).get("landmarks") or {}
        cons2 = lmi2.get("consensus") or {}
        check("换视野判为移位/转动", r["verdict"] == "persistent_moved", r["verdict"])
        # 参照物可能"假匹配"到别处，但必须是 lost（可信参照物不足）状态
        check("换视野时参照系失效", bool(cons2.get("lost")) or cons2.get("available") is False,
              str(cons2))
        print(f"    verdict={r['verdict']} consensusLost={cons2.get('lost')} "
              f"ncc={lmi2.get('refNcc')}")

        # ---------------------------------------------------------------- 19. 局部物体变动
        print("\n[19] 局部物体变动（桌上多了个箱子）：不遮挡、不失焦 ⇒ 只记录不告警")
        # 先建立一个干净的机位
        engine.ingest("cam-obj", burst(scene(), base_seed=3000), ts_ms=day_ms(0, 9))
        engine.ingest("cam-obj", burst(scene(), base_seed=3010), ts_ms=day_ms(0, 9) + 1000)
        engine.ingest("cam-obj", burst(scene(), base_seed=3020), ts_ms=day_ms(0, 9) + 2000)
        obj_scene = scene().copy()
        cv2.rectangle(obj_scene, (150, 120), (280, 220), (30, 200, 180), -1)  # 约占 17%
        r = engine.ingest("cam-obj", burst(obj_scene, base_seed=3030),
                          ts_ms=day_ms(0, 9) + 30_000)
        ch = (r["layers"].get("L0") or {}).get("changed") or {}
        check("局部变化判为场景内物体变动", r["verdict"] == "object_change", r["verdict"])
        check("物体变动不告警", r["alarm"] is False, str(r["alarm"]))
        print(f"    verdict={r['verdict']} largest={ch.get('largestRatio')} "
              f"ratio={ch.get('ratio')} alarm={r['alarm']}")

        # ---------------------------------------------------------------- 20. 忽略区
        print("\n[20] 同一块变化标成「忽略区」后应完全不计入基线偏离")
        engine.ingest("cam-ign", burst(scene(), base_seed=3100), ts_ms=day_ms(0, 9))
        engine.ingest("cam-ign", burst(scene(), base_seed=3110), ts_ms=day_ms(0, 9) + 1000)
        engine.ingest("cam-ign", burst(scene(), base_seed=3120), ts_ms=day_ms(0, 9) + 2000)
        cal = engine.calibrate_landmarks("cam-ign", [
            {"x": 0.08, "y": 0.15, "w": 0.20, "h": 0.25, "label": "左楼角"},
            {"x": 0.45, "y": 0.45, "w": 0.45, "h": 0.48, "label": "桌面", "kind": "ignore"},
        ], frame=scene())
        check("忽略区标定成功", cal["count"] == 2, str(cal.get("count")))
        r = engine.ingest("cam-ign", burst(obj_scene, base_seed=3130),
                          ts_ms=day_ms(0, 9) + 30_000)
        ch = (r["layers"].get("L0") or {}).get("changed") or {}
        check("忽略区生效（不计入基线偏离）", r["verdict"] == "normal", r["verdict"])
        check("忽略区占比已记录", (ch.get("ignoredRatio") or 0) > 0.1, str(ch.get("ignoredRatio")))
        print(f"    verdict={r['verdict']} ignoredRatio={ch.get('ignoredRatio')} "
              f"dHist={r['layers']['L0'].get('dHist')}")

        # ---------------------------------------------------------------- 21. 标定画面入库
        print("\n[21] 标定画面自动入基线（带质量闸）")
        # 新机位、零基线：标定时把这张正常画面直接作为第 1 个基线样本
        cal = engine.calibrate_landmarks("cam-cal", [
            {"x": 0.08, "y": 0.15, "w": 0.20, "h": 0.25, "label": "左楼角"},
        ], frame=scene(), admit_frame=True)
        check("正常标定画面自动入库", cal.get("frameAdmitted") is True, str(cal.get("frameAdmitted")))
        check("入库后基线有 1 个样本", cal.get("baselineSamples") == 1, str(cal.get("baselineSamples")))
        print(f"    reason={cal.get('frameAdmitReason')}")

        # 被遮挡的画面：质量闸必须拦住，否则会把"被挡住的样子"学成基线
        blocked = np.zeros((H, W, 3), np.uint8)
        blocked[:, :] = (35, 38, 40)
        cal2 = engine.calibrate_landmarks("cam-cal", [
            {"x": 0.08, "y": 0.15, "w": 0.20, "h": 0.25, "label": "左楼角"},
        ], frame=blocked, admit_frame=True)
        check("遮挡画面被质量闸拦住", cal2.get("frameAdmitted") is False,
              str(cal2.get("frameAdmitted")))
        check("拦截后样本数不增加", cal2.get("baselineSamples") == 1, str(cal2.get("baselineSamples")))
        print(f"    reason={cal2.get('frameAdmitReason')}")

        # ---------------------------------------------------------------- 22. 纸张遮挡
        print("\n[22] 纸遮挡（低对比度 + 仍有噪声）：必须判遮挡，不能判移位")
        # 复现现场问题：一张纸盖住镜头时，熵只掉 20% 左右（纸面有噪声/JPEG 纹理），
        # 旧逻辑因为熵没到 30% 阈值，转而用"清晰度下降"判成失焦，
        # 又因为 NCC 掉到 0.4 被判成"视野换了" → 误报"摄像头移位"。
        for i in range(3):
            engine.ingest("cam-paper", burst(scene(), base_seed=4000 + i * 10),
                          ts_ms=day_ms(0, 9) + i * 1000)
        engine.calibrate_landmarks("cam-paper", [
            {"x": 0.08, "y": 0.15, "w": 0.20, "h": 0.25, "label": "左楼角"},
            {"x": 0.62, "y": 0.20, "w": 0.22, "h": 0.22, "label": "右侧门"},
        ], frame=scene())
        rng = np.random.default_rng(4100)
        paper = np.clip(np.full((H, W, 3), 202.0) + rng.normal(0, 3.0, (H, W, 3)),
                        0, 255).astype(np.uint8)
        r = engine.ingest("cam-paper", burst(paper, base_seed=4200),
                          ts_ms=day_ms(0, 9) + 30_000)
        L0 = r["layers"]["L0"]
        check("纸遮挡判为遮挡", r["verdict"] == "persistent_covered", r["verdict"])
        check("纸遮挡不判移位", r["verdict"] != "persistent_moved", r["verdict"])
        print(f"    verdict={r['verdict']} contrastDrop={L0.get('contrastDrop')} "
              f"entropyDrop={L0.get('entropyDrop')} "
              f"ncc={(L0.get('landmarks') or {}).get('refNcc')}")

        # ---------------------------------------------------------------- 23. 阴雨/光照变化
        print("\n[23] 阴雨/光照变化（整体变暗变灰，结构不变）：不应告警")
        for i in range(3):
            engine.ingest("cam-weather", burst(scene(), base_seed=4300 + i * 10),
                          ts_ms=day_ms(0, 9) + i * 1000)
        engine.calibrate_landmarks("cam-weather", [
            {"x": 0.08, "y": 0.15, "w": 0.20, "h": 0.25, "label": "左楼角"},
            {"x": 0.62, "y": 0.20, "w": 0.22, "h": 0.22, "label": "右侧门"},
        ], frame=scene())
        base_f = scene().astype(np.float32)
        # 对比度降到 0.65、整体压暗到 0.85：纯全局仿射变化，结构完全一致
        gloomy = np.clip((base_f - 128.0) * 0.65 + 128.0 * 0.85, 0, 255).astype(np.uint8)
        r = engine.ingest("cam-weather", burst(gloomy, base_seed=4400),
                          ts_ms=day_ms(0, 9) + 30_000)
        L0 = r["layers"]["L0"]
        ph = L0.get("photometric") or {}
        check("光照变化不告警", r["verdict"] == "normal", r["verdict"])
        check("光照变化不判移位", r["verdict"] != "persistent_moved", r["verdict"])
        check("光度对齐已生效", ph.get("applied") is True, str(ph))
        print(f"    verdict={r['verdict']} dHist={L0.get('dHist')} "
              f"gain={ph.get('gain')} residualRatio={ph.get('residualRatio')}")

        # ---------------------------------------------------------------- 24. 子块投票
        print("\n[24] 参照物中间被挡 1/3：整块失配，但子块投票应认定参照物仍在")
        for i in range(3):
            engine.ingest("cam-sub", burst(scene(), base_seed=4500 + i * 10),
                          ts_ms=day_ms(0, 9) + i * 1000)
        box = {"x": 0.08, "y": 0.15, "w": 0.20, "h": 0.25, "label": "左楼角"}
        engine.calibrate_landmarks("cam-sub", [box], frame=scene())

        # 挡住该参照物**中间三分之一**（模拟车停在门中间）
        bx0 = int(box["x"] * W)
        bw = int(box["w"] * W)
        by0 = int(box["y"] * H)
        bh = int(box["h"] * H)
        partial = scene().copy()
        cv2.rectangle(partial, (bx0 + bw // 3, by0), (bx0 + bw * 2 // 3, by0 + bh),
                      (45, 45, 45), -1)
        r = engine.ingest("cam-sub", burst(partial, base_seed=4600),
                          ts_ms=day_ms(0, 9) + 30_000)
        lmi = (r["layers"].get("L0") or {}).get("landmarks") or {}
        m0 = (lmi.get("matches") or [{}])[0]
        check("部分遮挡时参照物仍可用", bool(m0.get("ok")) is True, str(m0.get("ok")))
        check("部分遮挡不判移位", r["verdict"] != "persistent_moved", r["verdict"])
        print(f"    ok={m0.get('ok')} score={m0.get('score')} "
              f"subBlocks={m0.get('fromSubBlocks')} blocks={m0.get('blocks')} "
              f"verdict={r['verdict']}")

        # 整个画幅被盖住（镜头被完全挡死）：必须判为丢失。
        # 这里专门验证"匹配位置无纹理"的防呆：否则 TM_CCOEFF_NORMED
        # 在纯色窗口会返回虚高分，把"全遮挡"判成"匹配得非常好"。
        rng_f = np.random.default_rng(4701)
        full = np.clip(np.full((H, W, 3), 190.0) + rng_f.normal(0, 2.5, (H, W, 3)),
                       0, 255).astype(np.uint8)
        r2 = engine.ingest("cam-sub", burst(full, base_seed=4700),
                           ts_ms=day_ms(0, 9) + 40_000)
        lmi2 = (r2["layers"].get("L0") or {}).get("landmarks") or {}
        m1 = (lmi2.get("matches") or [{}])[0]
        check("镜头被完全遮挡时参照物丢失", bool(m1.get("ok")) is False, str(m1.get("score")))
        print(f"    全遮挡: ok={m1.get('ok')} score={m1.get('score')} "
              f"flatMatch={m1.get('flatMatch')} verdict={r2['verdict']}")

        # 直接单测子块投票：中间一列被"有纹理的遮挡物"盖住（更像真车）
        g_gray = metrics.prepare_gray(scene())
        g_grad = metrics.gradient_magnitude(g_gray)
        g_fh, g_fw = g_grad.shape
        g_patch = metrics.crop_norm(g_grad, box)
        gpw, gph = g_patch.shape[1], g_patch.shape[0]
        gx0, gy0 = int(box["x"] * g_fw), int(box["y"] * g_fh)
        occluded = g_grad.copy()
        rng2 = np.random.default_rng(4711)
        c0 = gx0 + gpw // 3
        c1 = gx0 + 2 * gpw // 3
        occluded[gy0:gy0 + gph, c0:c1] = rng2.uniform(0, 60, (gph, c1 - c0))
        sub = metrics.match_landmark_subblocks(occluded, g_patch, box)
        check("子块投票能抢救部分遮挡", bool(sub.get("ok")) is True, str(sub.get("ok")))
        check("子块投票识别出部分遮挡",
              bool(sub.get("partial")) is True and float(sub.get("blockRatio") or 0) <= 0.8,
              f"ratio={sub.get('blockRatio')}")
        print(f"    subBlocks ok={sub.get('ok')} blocks={sub.get('blocks')}/"
              f"{sub.get('blockTotal')} ratio={sub.get('blockRatio')} "
              f"spread={sub.get('spread')}")

        # ---------------------------------------------------------------- 25. 模板库
        print("\n[25] 模板库：补录『阴影版』模板后，变暗场景的参照物应能重新匹配")
        for i in range(3):
            engine.ingest("cam-tpl", burst(scene(), base_seed=4800 + i * 10),
                          ts_ms=day_ms(0, 9) + i * 1000)
        engine.calibrate_landmarks("cam-tpl", [box], frame=scene())

        # 模拟傍晚：整幅加上斜向阴影条纹（结构变了，不是单纯变暗）
        shadowed = scene().copy()
        for k in range(0, W + H, 40):
            cv2.line(shadowed, (k, 0), (0, k), (20, 20, 20), 9)
        r = engine.ingest("cam-tpl", burst(shadowed, base_seed=4900),
                          ts_ms=day_ms(0, 9) + 30_000)
        before = ((r["layers"].get("L0") or {}).get("landmarks") or {}).get("matches") or [{}]
        score_before = float(before[0].get("score") or 0.0)

        tpl = engine.add_landmark_template("cam-tpl", 0, shadowed, label="傍晚阴影版",
                                           source="night", actor="tester",
                                           verdict_at_time=r["verdict"])
        check("模板添加成功", tpl.get("added") is True, str(tpl))

        r2 = engine.ingest("cam-tpl", burst(shadowed, base_seed=4910),
                           ts_ms=day_ms(0, 9) + 40_000)
        after = ((r2["layers"].get("L0") or {}).get("landmarks") or {}).get("matches") or [{}]
        score_after = float(after[0].get("score") or 0.0)
        check("补录模板后匹配分数提升", score_after > score_before,
              f"{score_before} -> {score_after}")
        check("命中的是补录模板", str(after[0].get("templateId", "")).endswith("t1"),
              str(after[0].get("templateId")))
        print(f"    before={score_before} after={score_after} "
              f"template={after[0].get('templateId')} count={after[0].get('templateCount')}")

        # ---------------------------------------------------------------- 26. 模板质量闸
        print("\n[26] 模板质量闸：在被遮挡的画面上加模板必须被拒绝")
        blocked2 = np.zeros((H, W, 3), np.uint8)
        blocked2[:, :] = (35, 38, 40)
        try:
            engine.add_landmark_template("cam-tpl", 0, blocked2, label="坏模板")
            ok_reject = False
        except ValueError as exc:
            ok_reject = "质量不合格" in str(exc)
        check("低质量模板被拒绝", ok_reject is True, "未被拒绝")

        # ---------------------------------------------------------------- 27. 去重与审计
        print("\n[27] 模板去重与审计")
        dup = engine.add_landmark_template("cam-tpl", 0, shadowed, label="重复模板")
        check("相同画面不重复添加", dup.get("added") is False and dup.get("duplicate") is True,
              str(dup))
        lst = engine.list_landmark_templates("cam-tpl")
        check("模板列表包含 2 个模板", len(lst.get("templates") or []) == 2,
              str(len(lst.get("templates") or [])))
        check("审计记录了添加动作",
              any(a.get("action") == "add_template" for a in lst.get("audit") or []),
              str([a.get("action") for a in (lst.get("audit") or [])][-3:]))
        print(f"    templates={len(lst.get('templates') or [])} "
              f"audit={[a.get('action') for a in (lst.get('audit') or [])]}")

    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n" + "=" * 60)
    print(f"通过 {len(PASS)} / {len(PASS) + len(FAIL)}")
    if FAIL:
        print("失败项：")
        for name in FAIL:
            print(f"  - {name}")
        return 1
    print("全部通过 ✅")
    return 0


if __name__ == "__main__":
    sys.exit(main())
