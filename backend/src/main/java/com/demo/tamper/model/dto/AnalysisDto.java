package com.demo.tamper.model.dto;

import java.util.List;
import java.util.Map;
import lombok.Builder;
import lombok.Getter;
import lombok.Setter;

/**
 * 结果分析页所需的聚合数据：结论 + 逐算法统计 + 事件时间线 + 一致性 + 分数分布 + 标注指标。
 */
@Getter
@Setter
@Builder
public class AnalysisDto {

    private String taskId;
    private String status;
    private MediaDto media;
    private List<String> algorithms;
    private int frames;
    private Long sampleStepMs;

    /** 分段检测：本次任务覆盖的时间窗与逐段汇总（整段检测时为单段或空） */
    private Long windowStartMs;
    private Long windowEndMs;
    private List<SegmentSummary> segments;

    private Conclusion conclusion;
    private List<AlgorithmStat> algorithmStats;
    private List<TamperEvent> events;
    private List<ConsistencyPair> consistency;
    private List<HistogramBin> distribution;
    private Metrics metrics;
    /** 事件级标注与 ±容差匹配结果（真值对照） */
    private EventEvaluation eventEvaluation;

    /** 检测结论 */
    @Getter
    @Setter
    @Builder
    public static class Conclusion {
        /** tampered / normal */
        private String verdict;
        /** covered / defocused / moved / normal */
        private String tamperType;
        private String text;
        /** 0~1 风险等级，用于前端着色 */
        private double riskLevel;
        private int tamperedFrames;
        private int totalFrames;
        private double tamperRate;
        private List<String> triggeredAlgorithms;
        private Long firstAlarmMs;
    }

    /** 单个算法的统计 */
    @Getter
    @Setter
    @Builder
    public static class AlgorithmStat {
        private String algorithm;
        private String displayName;
        private int frames;
        private int tamperedFrames;
        private int skipped;
        private double tamperRate;
        private double avgScore;
        private double maxScore;
        private Double avgCostMs;
        private String dominantTamperType;
        private int events;
        private Long firstAlarmMs;
    }

    /** 篡改事件（连续篡改帧合并） */
    @Getter
    @Setter
    @Builder
    public static class TamperEvent {
        private int startFrame;
        private int endFrame;
        private long startMs;
        private long endMs;
        private long durationMs;
        private int frameCount;
        private String tamperType;
        private double peakScore;
        private double avgScore;
        private List<String> algorithms;
    }

    /** 两两算法的一致性 */
    @Getter
    @Setter
    @Builder
    public static class ConsistencyPair {
        private String algorithmA;
        private String algorithmB;
        private double agreement;
        private int comparedFrames;
    }

    /** 分数分布直方图 */
    @Getter
    @Setter
    @Builder
    public static class HistogramBin {
        private double binStart;
        private double binEnd;
        private int count;
    }

    /** 有 ground truth 时的准确率指标 */
    @Getter
    @Setter
    @Builder
    public static class Metrics {
        private int labelledFrames;
        private int tamperedLabels;
        /** 标注帧中未落在采样帧上的条数（这些帧没有检测结果，不参与 P/R/F1 计算） */
        private int unmatchedLabels;
        private List<AlgorithmMetrics> perAlgorithm;
        /** 多算法投票（≥2 个算法同时告警）后的集成指标 */
        private AlgorithmMetrics ensemble;
    }

    @Getter
    @Setter
    @Builder
    public static class AlgorithmMetrics {
        private String algorithm;
        private int truePositive;
        private int falsePositive;
        private int trueNegative;
        private int falseNegative;
        private double precision;
        private double recall;
        private double f1;
        private double accuracy;
    }

    /** 单个分段的检测汇总 */
    @Getter
    @Setter
    @Builder
    public static class SegmentSummary {
        private int index;
        private String label;
        private long startMs;
        private long endMs;
        private int frameCount;
        private int tamperedFrames;
        private double alarmRate;
        private double peakScore;
        /** 该段主导的篡改类型 */
        private String tamperType;
        /** 标注给出的类型（按标注分段时有值） */
        private String expectedType;
        /** 该段对应的标注是否被判为篡改（无标注时为空） */
        private Boolean gtTampered;
        /** 判定是否与标注一致：有告警 vs 标注有篡改 */
        private Boolean matched;
        /** 篡改类型是否与标注类型一致（标注无篡改时为空） */
        private Boolean typeMatched;
        private int eventCount;
        private Double avgCostMs;
    }

    /** 事件级评价（±容差）：FLAD 标注多为 1~2 秒，逐帧口径会把"晚 1 秒的正确告警"记成漏报+误报 */
    @Getter
    @Setter
    @Builder
    public static class EventEvaluation {
        private long toleranceMs;
        private int gtCount;
        /** 标注来源文件 */
        private String source;
        private List<EventMetrics> perAlgorithm;
        private EventMetrics ensemble;
        /** 集成投票的对照明细（默认展示） */
        private List<EventPair> pairs;
        /** 各算法的对照明细，key 为算法名，另有 ensemble */
        private Map<String, List<EventPair>> pairsByAlgorithm;
    }

    @Getter
    @Setter
    @Builder
    public static class EventMetrics {
        private String algorithm;
        private int truePositive;
        private int falsePositive;
        private int falseNegative;
        private double precision;
        private double recall;
        private double f1;
        private double meanLatencyMs;
        private double maxLatencyMs;
    }

    /** 一条对照记录：标注 ↔ 告警 */
    @Getter
    @Setter
    @Builder
    public static class EventPair {
        /** tp / fn / fp */
        private String kind;
        private String algorithm;
        private Long gtStartMs;
        private Long gtEndMs;
        private String gtType;
        private Long predStartMs;
        private Long predEndMs;
        private String predType;
        /** 告警起点 - 标注起点（负值 = 提前） */
        private Long latencyMs;
        /** 真实重叠时长（0 表示仅靠容差命中） */
        private Long overlapMs;
        /** 误报距离最近标注的时长 */
        private Long nearestGtMs;
        private Double peakScore;
    }
}
