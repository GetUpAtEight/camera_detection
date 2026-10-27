package com.demo.tamper.model.dto;

import java.util.List;

/**
 * 实时监控（本机摄像头模拟监控点位）的接口数据契约。
 *
 * <p>会话只存在内存里：实时检测是"演示 + 现场调试"能力，不做历史归档，
 * 需要留存时前端把记录导出为 CSV/JSON。参考帧与告警帧落盘在 {@code data/live/<sessionId>/}。
 */
public final class LiveDto {

    private LiveDto() {
    }

    /** 会话信息。 */
    public record SessionInfo(
            String sessionId,
            List<String> algorithms,
            int requiredStreak,
            int frameCount,
            int alarmCount,
            long createdAt,
            long lastFrameAt,
            String referenceUrl,
            int alarmImages) {
    }

    /** 单算法结果（只保留前端要展示的字段）。 */
    public record AlgoResult(
            String algorithm,
            String displayName,
            double score,
            boolean tampered,
            String tamperType,
            String note,
            boolean skipped,
            /** 中间量（sharpness / entropy / sceneDiff …），自动标定从这里取主指标 */
            java.util.Map<String, Object> detail) {
    }

    /** 一帧的检测结果。 */
    public record FrameResult(
            String sessionId,
            int frameIndex,
            long timestampMs,
            /** 去抖后的最终告警（前端据此闪红/蜂鸣） */
            boolean alarm,
            /** 本帧是否判定为篡改（≥2 个主力算法同帧告警） */
            boolean frameTampered,
            /** 连续判定为篡改的帧数 */
            int streak,
            int requiredStreak,
            /** 本帧告警的算法数 */
            int votes,
            String tamperType,
            double score,
            String verdict,
            List<AlgoResult> algorithms,
            long elapsedMs,
            String alarmImageUrl) {
    }
}
