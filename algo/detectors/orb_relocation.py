"""算法 4：ORB 特征匹配 + 单应性估计（镜头移位 / 转向检测）。

原理：镜头被外力转动或挪动时画面内容仍然清晰，其清晰度与熵可能都正常，
前三个算法容易漏报。这里用 ORB 特征点匹配 + RANSAC 求解单应矩阵，
从矩阵中分解出平移量(tx, ty) 与旋转角，并统计内点率。

判定：平移量 > motion_pixels 且内点率仍较高（说明确实找到了同一场景）。
"""

from __future__ import annotations

from typing import Dict, List

import cv2
import numpy as np

from .base import (
    MOVED,
    NORMAL,
    BaseDetector,
    DetectionResult,
    ParamSpec,
    put_label,
    severity,
    to_gray,
)


class ORBRelocation(BaseDetector):
    name = "orb"
    display_name = "ORB 特征匹配（移位检测）"
    description = (
        "用 ORB 提取特征点并做汉明距离匹配，RANSAC 求解单应矩阵，"
        "分解出平移量与旋转角；内点率用于判断匹配是否可信。"
    )
    tamper_types: List[str] = [NORMAL, MOVED]
    requires_reference = True
    # 自动标定用的主指标：(detail 字段, 方向, 对应参数 key)
    primary_metric = ("translation", "higher", "motion_pixels")
    param_specs = [
        ParamSpec(
            key="motion_pixels",
            label="平移阈值",
            default=50.0,
            min=2.0,
            max=1200.0,
            step=2.0,
            unit="px",
            description="平移量超过该像素数判定为移位",
        ),
        ParamSpec(
            key="n_features",
            label="特征点数量",
            default=800,
            min=100,
            max=5000,
            step=100,
            type="int",
            description="ORB 最多提取的特征点数",
        ),
        ParamSpec(
            key="min_inlier_ratio",
            label="最低内点率",
            default=0.3,
            min=0.05,
            max=0.95,
            step=0.05,
            description="内点率低于该值时认为匹配不可信 / 场景已改变",
        ),
        ParamSpec(
            key="ratio_test",
            label="Lowe 比值",
            default=0.75,
            min=0.5,
            max=0.95,
            step=0.01,
            description="KNN 匹配的比值检验阈值，越小筛选越严格",
        ),
    ]

    def current_threshold(self) -> float:
        return float(self.p("motion_pixels"))

    def reset(self) -> None:
        self._ref_gray = None
        self._ref_kp = None
        self._ref_des = None

    def _make_orb(self):
        return cv2.ORB_create(nfeatures=int(self.p("n_features")))

    def _on_reference(self, frame: np.ndarray) -> None:
        gray = to_gray(frame)
        orb = self._make_orb()
        kp, des = orb.detectAndCompute(gray, None)
        self._ref_gray = gray
        self._ref_kp = kp
        self._ref_des = des

    def _process(self, frame: np.ndarray) -> DetectionResult:
        motion_pixels = float(self.p("motion_pixels"))
        min_inlier_ratio = float(self.p("min_inlier_ratio"))
        ratio_test = float(self.p("ratio_test"))

        gray = to_gray(frame)
        if self._ref_des is None or self._ref_gray is None:
            return DetectionResult(
                algorithm=self.name,
                skipped=True,
                note="参考帧特征点为空，无法做匹配（画面可能本身无纹理）",
            )
        if self._ref_gray.shape != gray.shape:
            return DetectionResult(
                algorithm=self.name,
                skipped=True,
                note="参考帧与当前帧尺寸不一致，已跳过",
            )

        orb = self._make_orb()
        kp, des = orb.detectAndCompute(gray, None)
        if des is None or len(kp) < 8:
            return DetectionResult(
                algorithm=self.name,
                score=0.0,
                is_tampered=False,
                tamper_type=NORMAL,
                detail={
                    "matches": 0,
                    "goodMatches": 0,
                    "keypoints": len(kp) if kp is not None else 0,
                },
                note="当前帧可提取的特征点过少，无法估计变换（可能已完全遮挡）",
            )

        matcher = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=False)
        knn = matcher.knnMatch(self._ref_des, des, k=2)
        good = []
        for pair in knn:
            if len(pair) < 2:
                continue
            m, n = pair
            if m.distance < ratio_test * n.distance:
                good.append(m)

        detail: Dict[str, object] = {
            "keypoints": len(kp),
            "referenceKeypoints": len(self._ref_kp) if self._ref_kp else 0,
            "matches": len(knn),
            "goodMatches": len(good),
            "motionThreshold": motion_pixels,
            "minInlierRatio": min_inlier_ratio,
        }

        if len(good) < 4:
            return DetectionResult(
                algorithm=self.name,
                score=0.0,
                is_tampered=False,
                tamper_type=NORMAL,
                detail=detail,
                note=f"有效匹配点仅 {len(good)} 个（<4），无法求解单应矩阵",
            )

        src = np.float32([self._ref_kp[m.queryIdx].pt for m in good]).reshape(-1, 1, 2)
        dst = np.float32([kp[m.trainIdx].pt for m in good]).reshape(-1, 1, 2)
        homography, mask = cv2.findHomography(src, dst, cv2.RANSAC, 3.0, maxIters=2000, confidence=0.995)

        if homography is None:
            return DetectionResult(
                algorithm=self.name,
                score=0.0,
                is_tampered=False,
                tamper_type=NORMAL,
                detail=detail,
                note="单应矩阵求解失败（匹配点退化）",
            )

        inliers = int(mask.sum()) if mask is not None else 0
        inlier_ratio = inliers / float(len(good))
        tx = float(homography[0, 2])
        ty = float(homography[1, 2])
        translation = float((tx ** 2 + ty ** 2) ** 0.5)
        rotation = float(np.degrees(np.arctan2(homography[1, 0], homography[0, 0])))
        scale = float(np.sqrt(abs(np.linalg.det(homography[:2, :2]))))

        score = max(
            severity(translation, motion_pixels, higher_is_worse=True),
            severity(inlier_ratio, min_inlier_ratio, higher_is_worse=False),
        )
        is_tampered = translation > motion_pixels or inlier_ratio < min_inlier_ratio

        detail.update(
            {
                "translation": round(translation, 2),
                "tx": round(tx, 2),
                "ty": round(ty, 2),
                "rotationDeg": round(rotation, 2),
                "scale": round(scale, 4),
                "inlierRatio": round(inlier_ratio, 4),
                "inliers": inliers,
            }
        )

        reasons = []
        if translation > motion_pixels:
            reasons.append(f"平移 {translation:.1f}px > {motion_pixels:.0f}px")
        if inlier_ratio < min_inlier_ratio:
            reasons.append(f"内点率 {inlier_ratio:.2f} < {min_inlier_ratio:.2f}")

        result = DetectionResult(
            algorithm=self.name,
            score=score,
            is_tampered=is_tampered,
            tamper_type=MOVED if is_tampered else NORMAL,
            detail=detail,
            note="；".join(reasons) if reasons else "匹配稳定，视角未发生明显变化",
        )

        if self.visual_enabled:
            matches_mask = mask.ravel().tolist() if mask is not None else None
            canvas = cv2.drawMatches(
                self._ref_gray,
                self._ref_kp,
                gray,
                kp,
                good,
                None,
                matchColor=(0, 255, 0),
                singlePointColor=(255, 0, 0),
                matchesMask=matches_mask,
                flags=cv2.DrawMatchesFlags_NOT_DRAW_SINGLE_POINTS,
            )
            # 匹配图是左右拼接的双倍宽度，缩到 1000px 内以保证落库体积可控
            if canvas.shape[1] > 1000:
                scale = 1000.0 / canvas.shape[1]
                canvas = cv2.resize(canvas, (1000, int(canvas.shape[0] * scale)))
            put_label(
                canvas,
                f"t=({tx:.0f},{ty:.0f}) |t|={translation:.0f}px inlier={inlier_ratio:.2f}",
                color=(0, 255, 255) if is_tampered else (0, 255, 0),
            )
            result.visual_image = canvas
        return result
