package com.demo.tamper.service;

import com.demo.tamper.common.BusinessException;
import com.demo.tamper.model.dto.AnalysisDto;
import com.demo.tamper.model.dto.AlgorithmMeta;
import com.demo.tamper.model.dto.CompareDto;
import com.demo.tamper.model.dto.MediaDto;
import com.demo.tamper.model.entity.DetectionTask;
import com.demo.tamper.model.entity.FrameResult;
import com.demo.tamper.model.entity.GroundTruthEvent;
import com.demo.tamper.model.entity.GroundTruthFrame;
import com.demo.tamper.model.entity.MediaFile;
import com.demo.tamper.repository.GroundTruthEventRepository;
import com.demo.tamper.repository.GroundTruthRepository;
import com.demo.tamper.repository.MediaFileRepository;
import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Optional;
import java.util.Set;
import java.util.TreeMap;
import java.util.stream.Collectors;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

/**
 * 结果分析：结论、事件聚合、算法一致性、分数分布、标注指标、导出。
 */
@Service
public class AnalysisService {

    private static final Logger log = LoggerFactory.getLogger(AnalysisService.class);

    /** 算法层不可用时的显示名兜底 */
    private static final Map<String, String> FALLBACK_NAMES = Map.of(
            "laplacian", "拉普拉斯方差（失焦）",
            "entropy", "直方图熵（遮挡）",
            "scene_diff", "帧差（遮挡/移位）",
            "orb", "ORB 特征匹配（移位）",
            "edge", "边缘消失率（统一）",
            "yolo", "YOLO 深度学习检测",
            "mog2_area", "MOG2 前景面积（遮挡·辅助）",
            "frame_area", "帧差最大连通域（遮挡·辅助）",
            "shortlong", "短/长期直方图（长时异常·辅助）");

    /**
     * 不参与「≥2 算法同帧投票」的算法。
     * <p>只有 yolo：它输出的是目标检测结果，`is_tampered` 恒为 false，语义上与篡改判定不可比。
     * <p>辅助算法（mog2_area / frame_area / shortlong）**默认不勾选**，所以默认不会进投票池；
     * 一旦用户显式勾选，就按"勾了就算"处理——否则会出现"算法报警了、但界面不告警"的困惑。
     */
    private static final Set<String> DETECTION_ONLY = Set.of("yolo");

    /** 该算法是否参与「≥2 算法同帧投票」。实时监控与会话判定复用同一口径。 */
    public static boolean isVotingAlgorithm(String algorithm) {
        return algorithm != null && !DETECTION_ONLY.contains(algorithm);
    }

    /** 事件级匹配的默认容差：3 秒（FLAD 标注中位数 1~2 秒，算法天然带 1~3 秒延迟） */
    public static final long DEFAULT_TOLERANCE_MS = 3000L;

    private final DetectionService detectionService;
    private final GroundTruthRepository groundTruthRepository;
    private final GroundTruthEventRepository groundTruthEventRepository;
    private final MediaFileRepository mediaRepository;
    private final AlgoGatewayService gateway;
    private final ObjectMapper objectMapper;

    private volatile Map<String, String> cachedNames = Map.of();
    private volatile long cachedNamesAt = 0L;

    public AnalysisService(DetectionService detectionService,
                           GroundTruthRepository groundTruthRepository,
                           GroundTruthEventRepository groundTruthEventRepository,
                           MediaFileRepository mediaRepository,
                           AlgoGatewayService gateway,
                           ObjectMapper objectMapper) {
        this.detectionService = detectionService;
        this.groundTruthRepository = groundTruthRepository;
        this.groundTruthEventRepository = groundTruthEventRepository;
        this.mediaRepository = mediaRepository;
        this.gateway = gateway;
        this.objectMapper = objectMapper;
    }

    // ------------------------------------------------------------------ 分析
    public AnalysisDto analyze(String taskId) {
        return analyze(taskId, DEFAULT_TOLERANCE_MS);
    }

    public AnalysisDto analyze(String taskId, long toleranceMs) {
        DetectionTask task = detectionService.getEntity(taskId);
        List<FrameResult> rows = detectionService.allResults(taskId);
        Map<String, List<FrameResult>> byAlgorithm = groupByAlgorithm(rows);
        int frames = countFrames(rows);

        Map<String, String> names = displayNames();
        Map<String, Double> costs = parseTimings(task.getTimingsJson());
        List<String> algorithms = new ArrayList<>(byAlgorithm.keySet());

        List<AnalysisDto.AlgorithmStat> stats = new ArrayList<>();
        Map<String, List<AnalysisDto.TamperEvent>> perAlgorithmEvents = new LinkedHashMap<>();
        for (String algorithm : algorithms) {
            List<FrameResult> series = byAlgorithm.get(algorithm);
            List<AnalysisDto.TamperEvent> events = buildEvents(series, List.of(algorithm));
            perAlgorithmEvents.put(algorithm, events);
            stats.add(buildStat(algorithm, series, names, costs.get(algorithm), events.size()));
        }

        List<AnalysisDto.TamperEvent> consensusEvents = buildConsensusEvents(byAlgorithm, frames);
        AnalysisDto.Conclusion conclusion = buildConclusion(byAlgorithm, consensusEvents, frames, names);

        List<AnalysisDto.ConsistencyPair> consistency = buildConsistency(byAlgorithm);
        List<AnalysisDto.HistogramBin> distribution = buildDistribution(byAlgorithm);
        AnalysisDto.Metrics metrics = buildMetrics(taskId, byAlgorithm);

        // 分段汇总 + 事件级（±容差）真值对照
        List<GroundTruthEvent> gtEvents =
                groundTruthEventRepository.findByMediaIdOrderByStartMsAsc(task.getMediaId());
        List<AnalysisDto.SegmentSummary> segments =
                buildSegments(task, rows, byAlgorithm, gtEvents, consensusEvents, costs);
        AnalysisDto.EventEvaluation eventEvaluation =
                buildEventEvaluation(byAlgorithm, gtEvents, toleranceMs, task, frames);

        MediaFile media = mediaRepository.findById(task.getMediaId()).orElse(null);
        return AnalysisDto.builder()
                .taskId(taskId)
                .status(task.getStatus().name())
                .media(media == null ? null : MediaDto.from(media))
                .algorithms(algorithms)
                .frames(frames)
                .sampleStepMs(sampleStepMs(rows))
                .windowStartMs(task.getStartMs())
                .windowEndMs(task.getEndMs())
                .segments(segments)
                .eventEvaluation(eventEvaluation)
                .conclusion(conclusion)
                .algorithmStats(stats)
                .events(consensusEvents)
                .consistency(consistency)
                .distribution(distribution)
                .metrics(metrics)
                .build();
    }

    public CompareDto compare(String taskId) {
        AnalysisDto analysis = analyze(taskId);
        Map<String, CompareDto.TypeBreakdown> breakdown = new LinkedHashMap<>();
        List<FrameResult> rows = detectionService.allResults(taskId);
        for (Map.Entry<String, List<FrameResult>> entry : groupByAlgorithm(rows).entrySet()) {
            int normal = 0;
            int covered = 0;
            int defocused = 0;
            int moved = 0;
            for (FrameResult row : entry.getValue()) {
                String type = row.getTamperType() == null ? "normal" : row.getTamperType();
                switch (type) {
                    case "covered" -> covered++;
                    case "defocused" -> defocused++;
                    case "moved" -> moved++;
                    default -> normal++;
                }
            }
            breakdown.put(entry.getKey(), CompareDto.TypeBreakdown.builder()
                    .algorithm(entry.getKey())
                    .normal(normal)
                    .covered(covered)
                    .defocused(defocused)
                    .moved(moved)
                    .build());
        }

        double overall = analysis.getConsistency().isEmpty()
                ? 1.0
                : analysis.getConsistency().stream()
                        .mapToDouble(AnalysisDto.ConsistencyPair::getAgreement)
                        .average()
                        .orElse(1.0);

        return CompareDto.builder()
                .taskId(taskId)
                .mediaName(analysis.getMedia() == null ? "" : analysis.getMedia().originalName())
                .mediaType(analysis.getMedia() == null ? "" : analysis.getMedia().mediaType().name())
                .frames(analysis.getFrames())
                .thresholdScore(0.5)
                .stats(analysis.getAlgorithmStats())
                .typeBreakdown(new ArrayList<>(breakdown.values()))
                .consistency(analysis.getConsistency())
                .overallAgreement(round(overall))
                .hasGroundTruth(analysis.getMetrics() != null)
                .build();
    }

    // ------------------------------------------------------------------ 统计
    private AnalysisDto.AlgorithmStat buildStat(String algorithm, List<FrameResult> series,
                                                Map<String, String> names, Double costMs, int events) {
        int frames = series.size();
        long skipped = series.stream().filter(r -> Boolean.TRUE.equals(r.getSkipped())).count();
        List<FrameResult> valid = series.stream()
                .filter(r -> !Boolean.TRUE.equals(r.getSkipped()))
                .toList();
        int tamperedFrames = (int) series.stream().filter(r -> Boolean.TRUE.equals(r.getTampered())).count();
        double avgScore = valid.stream()
                .filter(r -> r.getScore() != null)
                .mapToDouble(FrameResult::getScore)
                .average()
                .orElse(0.0);
        double maxScore = series.stream()
                .filter(r -> r.getScore() != null)
                .mapToDouble(FrameResult::getScore)
                .max()
                .orElse(0.0);

        Map<String, Long> typeCounts = series.stream()
                .filter(r -> Boolean.TRUE.equals(r.getTampered()))
                .collect(Collectors.groupingBy(
                        r -> r.getTamperType() == null ? "normal" : r.getTamperType(),
                        Collectors.counting()));
        String dominant = typeCounts.entrySet().stream()
                .max(Map.Entry.comparingByValue())
                .map(Map.Entry::getKey)
                .orElse("normal");
        Long firstAlarm = series.stream()
                .filter(r -> Boolean.TRUE.equals(r.getTampered()))
                .map(FrameResult::getTimestampMs)
                .filter(java.util.Objects::nonNull)
                .min(Long::compareTo)
                .orElse(null);

        return AnalysisDto.AlgorithmStat.builder()
                .algorithm(algorithm)
                .displayName(names.getOrDefault(algorithm, algorithm))
                .frames(frames)
                .tamperedFrames(tamperedFrames)
                .skipped((int) skipped)
                .tamperRate(round(frames == 0 ? 0 : (double) tamperedFrames / frames))
                .avgScore(round(avgScore))
                .maxScore(round(maxScore))
                .avgCostMs(costMs == null ? null : round(costMs))
                .dominantTamperType(dominant)
                .events(events)
                .firstAlarmMs(firstAlarm)
                .build();
    }

    private List<AnalysisDto.TamperEvent> buildEvents(List<FrameResult> series, List<String> algorithms) {
        List<FrameResult> ordered = series.stream()
                .sorted(Comparator.comparing(FrameResult::getFrameIndex))
                .toList();
        List<AnalysisDto.TamperEvent> events = new ArrayList<>();
        List<FrameResult> current = new ArrayList<>();
        String runningType = null;
        for (FrameResult row : ordered) {
            boolean tampered = Boolean.TRUE.equals(row.getTampered());
            if (tampered) {
                String type = row.getTamperType() == null ? "normal" : row.getTamperType();
                // 篡改类型发生切换（如"失焦"→"遮挡"）时切成两段事件，避免时间线被合并成一段
                if (!current.isEmpty() && runningType != null && !runningType.equals(type)
                        && current.size() >= 2) {
                    events.add(toEvent(current, algorithms));
                    current = new ArrayList<>();
                }
                runningType = type;
                current.add(row);
            } else if (!current.isEmpty()) {
                events.add(toEvent(current, algorithms));
                current = new ArrayList<>();
                runningType = null;
            }
        }
        if (!current.isEmpty()) {
            events.add(toEvent(current, algorithms));
        }
        return events;
    }

    /** 取一组帧中占多数的篡改类型。 */
    private static String dominantType(List<FrameResult> frames) {
        return frames.stream()
                .collect(Collectors.groupingBy(
                        f -> f.getTamperType() == null ? "normal" : f.getTamperType(),
                        Collectors.counting()))
                .entrySet().stream()
                .max(Map.Entry.comparingByValue())
                .map(Map.Entry::getKey)
                .orElse("normal");
    }

    /** 篡改类型的中文描述。 */
    private static String typeText(String tamperType) {
        return switch (tamperType == null ? "normal" : tamperType) {
            case "covered" -> "镜头遮挡";
            case "defocused" -> "镜头失焦/模糊";
            case "moved" -> "镜头移位/转向";
            default -> "画面异常";
        };
    }

    private AnalysisDto.TamperEvent toEvent(List<FrameResult> frames, List<String> algorithms) {
        FrameResult first = frames.get(0);
        FrameResult last = frames.get(frames.size() - 1);
        long startMs = Optional.ofNullable(first.getTimestampMs()).orElse(0L);
        long endMs = Optional.ofNullable(last.getTimestampMs()).orElse(startMs);
        double peak = frames.stream()
                .filter(f -> f.getScore() != null)
                .mapToDouble(FrameResult::getScore)
                .max()
                .orElse(0.0);
        double avg = frames.stream()
                .filter(f -> f.getScore() != null)
                .mapToDouble(FrameResult::getScore)
                .average()
                .orElse(0.0);
        String type = dominantType(frames);

        return AnalysisDto.TamperEvent.builder()
                .startFrame(first.getFrameIndex())
                .endFrame(last.getFrameIndex())
                .startMs(startMs)
                .endMs(endMs)
                .durationMs(Math.max(0, endMs - startMs))
                .frameCount(frames.size())
                .tamperType(type)
                .peakScore(round(peak))
                .avgScore(round(avg))
                .algorithms(algorithms)
                .build();
    }

    /**
     * 跨算法共识事件：至少 2 个算法（只选 1 个算法时为 1 个）在同一帧告警，才认为该帧被篡改。
     * 这样可以显著压低单算法的偶发误报。
     */
    private List<AnalysisDto.TamperEvent> buildConsensusEvents(Map<String, List<FrameResult>> byAlgorithm,
                                                               int totalFrames) {
        List<String> classic = byAlgorithm.keySet().stream()
                .filter(a -> !DETECTION_ONLY.contains(a))
                .toList();
        if (classic.isEmpty()) {
            return List.of();
        }
        int needed = classic.size() >= 2 ? 2 : 1;

        TreeMap<Integer, List<FrameResult>> perFrame = new TreeMap<>();
        TreeMap<Integer, Long> timestamps = new TreeMap<>();
        for (String algorithm : classic) {
            for (FrameResult row : byAlgorithm.get(algorithm)) {
                perFrame.computeIfAbsent(row.getFrameIndex(), k -> new ArrayList<>()).add(row);
                if (row.getTimestampMs() != null) {
                    timestamps.merge(row.getFrameIndex(), row.getTimestampMs(), Math::min);
                }
            }
        }

        List<AnalysisDto.TamperEvent> events = new ArrayList<>();
        List<FrameResult> current = new ArrayList<>();
        List<Integer> currentFrames = new ArrayList<>();
        String runningType = null;

        for (Map.Entry<Integer, List<FrameResult>> entry : perFrame.entrySet()) {
            List<FrameResult> alarmed = entry.getValue().stream()
                    .filter(r -> Boolean.TRUE.equals(r.getTampered()))
                    .toList();
            boolean alarm = alarmed.size() >= needed;
            if (alarm) {
                String frameType = dominantType(alarmed);
                // 主导篡改类型切换（失焦 → 遮挡）时切成两段事件
                if (!currentFrames.isEmpty() && runningType != null
                        && !runningType.equals(frameType) && currentFrames.size() >= 2) {
                    events.add(toConsensusEvent(current, currentFrames, timestamps));
                    current = new ArrayList<>();
                    currentFrames = new ArrayList<>();
                }
                runningType = frameType;
                current.addAll(alarmed);
                currentFrames.add(entry.getKey());
            } else if (!currentFrames.isEmpty()) {
                events.add(toConsensusEvent(current, currentFrames, timestamps));
                current = new ArrayList<>();
                currentFrames = new ArrayList<>();
                runningType = null;
            }
        }
        if (!currentFrames.isEmpty()) {
            events.add(toConsensusEvent(current, currentFrames, timestamps));
        }
        return events;
    }

    private AnalysisDto.TamperEvent toConsensusEvent(List<FrameResult> frames, List<Integer> frameIndexes,
                                                     Map<Integer, Long> timestamps) {
        Set<String> algorithms = frames.stream()
                .map(FrameResult::getAlgorithm)
                .collect(Collectors.toCollection(LinkedHashSet::new));
        long startMs = Optional.ofNullable(timestamps.get(frameIndexes.get(0))).orElse(0L);
        long endMs = Optional.ofNullable(timestamps.get(frameIndexes.get(frameIndexes.size() - 1)))
                .orElse(startMs);
        double peak = frames.stream()
                .filter(f -> f.getScore() != null)
                .mapToDouble(FrameResult::getScore)
                .max()
                .orElse(0.0);
        double avg = frames.stream()
                .filter(f -> f.getScore() != null)
                .mapToDouble(FrameResult::getScore)
                .average()
                .orElse(0.0);
        String type = dominantType(frames);

        return AnalysisDto.TamperEvent.builder()
                .startFrame(frameIndexes.get(0))
                .endFrame(frameIndexes.get(frameIndexes.size() - 1))
                .startMs(startMs)
                .endMs(endMs)
                .durationMs(Math.max(0, endMs - startMs))
                .frameCount(frameIndexes.size())
                .tamperType(type)
                .peakScore(round(peak))
                .avgScore(round(avg))
                .algorithms(new ArrayList<>(algorithms))
                .build();
    }

    private AnalysisDto.Conclusion buildConclusion(Map<String, List<FrameResult>> byAlgorithm,
                                                   List<AnalysisDto.TamperEvent> events,
                                                   int totalFrames, Map<String, String> names) {
        if (events.isEmpty()) {
            return AnalysisDto.Conclusion.builder()
                    .verdict("normal")
                    .tamperType("normal")
                    .text(totalFrames == 0
                            ? "暂无检测结果。"
                            : "未检测到明显的摄像头篡改行为：所有采样帧的异常分均未越过判定阈值。")
                    .riskLevel(0.0)
                    .tamperedFrames(0)
                    .totalFrames(totalFrames)
                    .tamperRate(0.0)
                    .triggeredAlgorithms(List.of())
                    .build();
        }

        int tamperedFrames = events.stream().mapToInt(AnalysisDto.TamperEvent::getFrameCount).sum();
        double peak = events.stream().mapToDouble(AnalysisDto.TamperEvent::getPeakScore).max().orElse(0.0);
        Map<String, Long> typeFrames = new HashMap<>();
        for (AnalysisDto.TamperEvent event : events) {
            typeFrames.merge(event.getTamperType(), (long) event.getFrameCount(), Long::sum);
        }
        String dominant = typeFrames.entrySet().stream()
                .max(Map.Entry.comparingByValue())
                .map(Map.Entry::getKey)
                .orElse("normal");
        Set<String> triggered = events.stream()
                .flatMap(e -> e.getAlgorithms().stream())
                .collect(Collectors.toCollection(LinkedHashSet::new));
        List<String> triggeredNames = triggered.stream()
                .map(a -> names.getOrDefault(a, a))
                .collect(Collectors.toList());
        Long firstAlarm = events.stream().map(AnalysisDto.TamperEvent::getStartMs).min(Long::compareTo)
                .orElse(null);

        double rate = totalFrames == 0 ? 0 : Math.min(1.0, (double) tamperedFrames / totalFrames);
        // 单帧（图片）任务没有时间轴，不必输出 "0.00s – 0.00s" 这种无意义区间
        boolean singleFrame = totalFrames <= 1;
        String segments = events.stream()
                .map(e -> singleFrame
                        ? typeText(e.getTamperType())
                        : String.format(Locale.CHINA, "%s（%.2fs – %.2fs）",
                                typeText(e.getTamperType()),
                                e.getStartMs() / 1000.0,
                                e.getEndMs() / 1000.0))
                .collect(Collectors.joining("、"));
        String text = String.format(Locale.CHINA,
                "检测到 %d 段篡改事件：%s，共涉及 %d 帧（占采样帧 %.1f%%），峰值异常分 %.2f，主要由「%s」触发。",
                events.size(), segments, tamperedFrames, rate * 100, peak,
                String.join("、", triggeredNames));

        return AnalysisDto.Conclusion.builder()
                .verdict("tampered")
                .tamperType(dominant)
                .text(text)
                .riskLevel(round(Math.min(1.0, 0.5 * rate + 0.5 * peak)))
                .tamperedFrames(tamperedFrames)
                .totalFrames(totalFrames)
                .tamperRate(round(rate))
                .triggeredAlgorithms(triggeredNames)
                .firstAlarmMs(firstAlarm)
                .build();
    }

    private List<AnalysisDto.ConsistencyPair> buildConsistency(Map<String, List<FrameResult>> byAlgorithm) {
        List<String> algorithms = byAlgorithm.keySet().stream()
                .filter(a -> !DETECTION_ONLY.contains(a))
                .toList();
        List<AnalysisDto.ConsistencyPair> pairs = new ArrayList<>();
        for (int i = 0; i < algorithms.size(); i++) {
            for (int j = i + 1; j < algorithms.size(); j++) {
                Map<Integer, Boolean> a = tamperMap(byAlgorithm.get(algorithms.get(i)));
                Map<Integer, Boolean> b = tamperMap(byAlgorithm.get(algorithms.get(j)));
                int compared = 0;
                int same = 0;
                for (Map.Entry<Integer, Boolean> entry : a.entrySet()) {
                    Boolean other = b.get(entry.getKey());
                    if (other == null) {
                        continue;
                    }
                    compared++;
                    if (other.equals(entry.getValue())) {
                        same++;
                    }
                }
                if (compared > 0) {
                    pairs.add(AnalysisDto.ConsistencyPair.builder()
                            .algorithmA(algorithms.get(i))
                            .algorithmB(algorithms.get(j))
                            .agreement(round((double) same / compared))
                            .comparedFrames(compared)
                            .build());
                }
            }
        }
        return pairs;
    }

    private List<AnalysisDto.HistogramBin> buildDistribution(Map<String, List<FrameResult>> byAlgorithm) {
        int bins = 10;
        int[] counts = new int[bins];
        for (Map.Entry<String, List<FrameResult>> entry : byAlgorithm.entrySet()) {
            if (DETECTION_ONLY.contains(entry.getKey())) {
                continue;
            }
            for (FrameResult row : entry.getValue()) {
                if (Boolean.TRUE.equals(row.getSkipped()) || row.getScore() == null) {
                    continue;
                }
                int bin = (int) Math.floor(row.getScore() * bins);
                counts[Math.max(0, Math.min(bins - 1, bin))]++;
            }
        }
        List<AnalysisDto.HistogramBin> result = new ArrayList<>();
        for (int i = 0; i < bins; i++) {
            result.add(AnalysisDto.HistogramBin.builder()
                    .binStart(round(i / (double) bins))
                    .binEnd(round((i + 1) / (double) bins))
                    .count(counts[i])
                    .build());
        }
        return result;
    }

    private AnalysisDto.Metrics buildMetrics(String taskId, Map<String, List<FrameResult>> byAlgorithm) {
        List<GroundTruthFrame> gt = groundTruthRepository.findByTaskIdOrderByFrameIndexAsc(taskId);
        if (gt.isEmpty()) {
            return null;
        }
        Map<Integer, Boolean> allLabels = gt.stream()
                .collect(Collectors.toMap(GroundTruthFrame::getFrameIndex,
                        g -> Boolean.TRUE.equals(g.getTampered()), (a, b) -> a));

        // 只评估"真的有检测结果"的帧：标注可能覆盖整段视频（25fps 每帧），
        // 而检测只跑了采样帧；不裁剪会让不同算法的 FN 分母不一致（集成投票尤其明显）。
        Set<Integer> detectedFrames = byAlgorithm.values().stream()
                .flatMap(List::stream)
                .map(FrameResult::getFrameIndex)
                .collect(Collectors.toSet());
        Map<Integer, Boolean> labels = allLabels.entrySet().stream()
                .filter(e -> detectedFrames.contains(e.getKey()))
                .collect(Collectors.toMap(Map.Entry::getKey, Map.Entry::getValue));
        int unmatchedLabels = allLabels.size() - labels.size();
        if (labels.isEmpty()) {
            log.warn("标注文件与采样帧没有交集，跳过指标计算（标注 {} 帧）", allLabels.size());
            return null;
        }
        int positives = (int) labels.values().stream().filter(Boolean::booleanValue).count();

        List<AnalysisDto.AlgorithmMetrics> perAlgorithm = new ArrayList<>();
        Map<Integer, Integer> alarmVotes = new HashMap<>();
        for (Map.Entry<String, List<FrameResult>> entry : byAlgorithm.entrySet()) {
            if (DETECTION_ONLY.contains(entry.getKey())) {
                continue;
            }
            perAlgorithm.add(computeMetrics(entry.getKey(), tamperMap(entry.getValue()), labels));
            for (Map.Entry<Integer, Boolean> pair : tamperMap(entry.getValue()).entrySet()) {
                if (Boolean.TRUE.equals(pair.getValue()) && labels.containsKey(pair.getKey())) {
                    alarmVotes.merge(pair.getKey(), 1, Integer::sum);
                }
            }
        }

        int needed = perAlgorithm.size() >= 2 ? 2 : 1;
        Map<Integer, Boolean> ensembleFlags = new HashMap<>();
        for (Integer frame : labels.keySet()) {
            ensembleFlags.put(frame, alarmVotes.getOrDefault(frame, 0) >= needed);
        }
        AnalysisDto.AlgorithmMetrics ensemble = computeMetrics("ensemble (≥" + needed + " 算法投票)",
                ensembleFlags, labels);

        return AnalysisDto.Metrics.builder()
                .labelledFrames(labels.size())
                .tamperedLabels(positives)
                .unmatchedLabels(unmatchedLabels)
                .perAlgorithm(perAlgorithm)
                .ensemble(ensemble)
                .build();
    }

    private AnalysisDto.AlgorithmMetrics computeMetrics(String name, Map<Integer, Boolean> predictions,
                                                        Map<Integer, Boolean> labels) {
        int tp = 0;
        int fp = 0;
        int tn = 0;
        int fn = 0;
        for (Map.Entry<Integer, Boolean> entry : labels.entrySet()) {
            Boolean prediction = predictions.get(entry.getKey());
            if (prediction == null) {
                continue;
            }
            boolean actual = Boolean.TRUE.equals(entry.getValue());
            if (actual && prediction) {
                tp++;
            } else if (!actual && prediction) {
                fp++;
            } else if (actual) {
                fn++;
            } else {
                tn++;
            }
        }
        double precision = tp + fp == 0 ? 0 : (double) tp / (tp + fp);
        double recall = tp + fn == 0 ? 0 : (double) tp / (tp + fn);
        double f1 = precision + recall == 0 ? 0 : 2 * precision * recall / (precision + recall);
        int total = tp + fp + tn + fn;
        double accuracy = total == 0 ? 0 : (double) (tp + tn) / total;

        return AnalysisDto.AlgorithmMetrics.builder()
                .algorithm(name)
                .truePositive(tp)
                .falsePositive(fp)
                .trueNegative(tn)
                .falseNegative(fn)
                .precision(round(precision))
                .recall(round(recall))
                .f1(round(f1))
                .accuracy(round(accuracy))
                .build();
    }

    // ------------------------------------------------------------------ 标注
    @Transactional
    public Map<String, Object> importGroundTruth(String taskId, String csv) {
        DetectionTask task = detectionService.getEntity(taskId);
        if (csv == null || csv.isBlank()) {
            throw new BusinessException("标注内容为空");
        }
        List<GroundTruthFrame> parsed = new ArrayList<>();
        String[] lines = csv.split("\\r?\\n");
        int skipped = 0;
        for (String raw : lines) {
            String line = raw.trim();
            if (line.isEmpty() || line.startsWith("#")) {
                continue;
            }
            String[] parts = line.split("[,;\\t]");
            if (parts.length < 2) {
                skipped++;
                continue;
            }
            try {
                int frameIndex = (int) Double.parseDouble(parts[0].trim());
                boolean tampered = parseLabel(parts[1].trim());
                parsed.add(new GroundTruthFrame(taskId, frameIndex, tampered));
            } catch (NumberFormatException e) {
                skipped++; // 表头等非数字行
            }
        }
        if (parsed.isEmpty()) {
            throw new BusinessException("没有解析到有效标注，CSV 需为 frame_index,tampered 两列");
        }
        groundTruthRepository.deleteByTaskId(task.getId());
        groundTruthRepository.saveAll(parsed);
        log.info("任务 {} 导入标注 {} 条（跳过 {} 行）", taskId, parsed.size(), skipped);
        return Map.of("imported", parsed.size(), "skipped", skipped);
    }

    private static boolean parseLabel(String raw) {
        String value = raw.toLowerCase(Locale.ROOT);
        return switch (value) {
            case "1", "true", "yes", "tampered", "tamper", "abnormal", "anomaly" -> true;
            case "0", "false", "no", "normal", "ok" -> false;
            default -> {
                double numeric = Double.parseDouble(value);
                yield numeric > 0;
            }
        };
    }

    // ------------------------------------------------------------------ 导出
    public String exportCsv(String taskId) {
        DetectionTask task = detectionService.getEntity(taskId);
        List<FrameResult> rows = detectionService.allResults(taskId);
        StringBuilder sb = new StringBuilder("\uFEFF");
        sb.append("frame_index,timestamp_ms,algorithm,score,tampered,tamper_type,confidence,skipped,note,detail\n");
        for (FrameResult row : rows) {
            sb.append(row.getFrameIndex()).append(',')
                    .append(row.getTimestampMs() == null ? 0 : row.getTimestampMs()).append(',')
                    .append(row.getAlgorithm()).append(',')
                    .append(row.getScore() == null ? 0 : row.getScore()).append(',')
                    .append(Boolean.TRUE.equals(row.getTampered())).append(',')
                    .append(row.getTamperType()).append(',')
                    .append(row.getConfidence() == null ? 0 : row.getConfidence()).append(',')
                    .append(Boolean.TRUE.equals(row.getSkipped())).append(',')
                    .append(csvEscape(row.getNote())).append(',')
                    .append(csvEscape(row.getDetailJson()))
                    .append('\n');
        }
        return sb.toString();
    }

    public byte[] exportJson(String taskId) {
        Map<String, Object> payload = new LinkedHashMap<>();
        payload.put("analysis", analyze(taskId));
        payload.put("frames", exportFrameGroups(taskId));
        try {
            return objectMapper.writerWithDefaultPrettyPrinter().writeValueAsBytes(payload);
        } catch (Exception e) {
            throw new BusinessException("导出 JSON 失败：" + e.getMessage());
        }
    }

    private List<Map<String, Object>> exportFrameGroups(String taskId) {
        Map<Integer, List<FrameResult>> perFrame = new TreeMap<>();
        Map<Integer, Long> timestamps = new HashMap<>();
        for (FrameResult row : detectionService.allResults(taskId)) {
            perFrame.computeIfAbsent(row.getFrameIndex(), k -> new ArrayList<>()).add(row);
            if (row.getTimestampMs() != null) {
                timestamps.put(row.getFrameIndex(), row.getTimestampMs());
            }
        }
        List<Map<String, Object>> frames = new ArrayList<>();
        for (Map.Entry<Integer, List<FrameResult>> entry : perFrame.entrySet()) {
            Map<String, Object> frame = new LinkedHashMap<>();
            frame.put("frameIndex", entry.getKey());
            frame.put("timestampMs", timestamps.getOrDefault(entry.getKey(), 0L));
            frame.put("results", entry.getValue().stream().map(r -> {
                Map<String, Object> item = new LinkedHashMap<>();
                item.put("algorithm", r.getAlgorithm());
                item.put("score", r.getScore());
                item.put("tampered", Boolean.TRUE.equals(r.getTampered()));
                item.put("tamperType", r.getTamperType());
                item.put("confidence", r.getConfidence());
                item.put("skipped", Boolean.TRUE.equals(r.getSkipped()));
                item.put("note", r.getNote());
                item.put("detail", readDetail(r.getDetailJson()));
                return item;
            }).toList());
            frames.add(frame);
        }
        return frames;
    }

    public String downloadName(String taskId, String suffix) {
        DetectionTask task = detectionService.getEntity(taskId);
        MediaFile media = mediaRepository.findById(task.getMediaId()).orElse(null);
        String base = media == null ? "detection" : media.getOriginalName().replaceAll("[\\\\/:*?\"<>|]", "_");
        return base + "_" + taskId.substring(0, 8) + suffix;
    }

    // ------------------------------------------------------------------ 工具
    private Map<String, List<FrameResult>> groupByAlgorithm(List<FrameResult> rows) {
        Map<String, List<FrameResult>> map = new LinkedHashMap<>();
        rows.stream()
                .sorted(Comparator.comparing(FrameResult::getFrameIndex))
                .forEach(row -> map.computeIfAbsent(row.getAlgorithm(), k -> new ArrayList<>()).add(row));
        return map;
    }

    private static int countFrames(List<FrameResult> rows) {
        return (int) rows.stream().map(FrameResult::getFrameIndex).distinct().count();
    }

    private static Long sampleStepMs(List<FrameResult> rows) {
        List<Long> values = rows.stream()
                .map(FrameResult::getTimestampMs)
                .filter(java.util.Objects::nonNull)
                .distinct()
                .sorted()
                .toList();
        if (values.size() < 2) {
            return null;
        }
        return values.get(1) - values.get(0);
    }

    private static Map<Integer, Boolean> tamperMap(List<FrameResult> series) {
        Map<Integer, Boolean> map = new HashMap<>();
        for (FrameResult row : series) {
            if (Boolean.TRUE.equals(row.getSkipped())) {
                continue;
            }
            map.put(row.getFrameIndex(), Boolean.TRUE.equals(row.getTampered()));
        }
        return map;
    }

    private Map<String, String> displayNames() {
        long now = System.currentTimeMillis();
        if (!cachedNames.isEmpty() && now - cachedNamesAt < 60_000L) {
            return cachedNames;
        }
        Map<String, String> names = new HashMap<>(FALLBACK_NAMES);
        try {
            for (AlgorithmMeta meta : gateway.algorithms()) {
                names.put(meta.name(), meta.displayName());
            }
            cachedNamesAt = now;
        } catch (RuntimeException e) {
            log.debug("读取算法显示名失败，使用内置兜底：{}", e.getMessage());
        }
        cachedNames = names;
        return names;
    }

    private Map<String, Double> parseTimings(String json) {
        if (json == null || json.isBlank()) {
            return Map.of();
        }
        try {
            return objectMapper.readValue(json, new TypeReference<Map<String, Double>>() {});
        } catch (Exception e) {
            return Map.of();
        }
    }

    private Object readDetail(String json) {
        if (json == null || json.isBlank()) {
            return Map.of();
        }
        try {
            return objectMapper.readValue(json, Object.class);
        } catch (Exception e) {
            return Map.of();
        }
    }

    private static String csvEscape(String value) {
        if (value == null) {
            return "";
        }
        String cleaned = value.replace("\r", " ").replace("\n", " ");
        if (cleaned.contains(",") || cleaned.contains("\"")) {
            return "\"" + cleaned.replace("\"", "\"\"") + "\"";
        }
        return cleaned;
    }

    private static double round(double value) {
        return Math.round(value * 10000.0) / 10000.0;
    }

    // ------------------------------------------------------------------ 分段
    /** 没有分段信息时，整段检测也当成一个"段"返回，前端表格不用分情况处理。 */
    private List<AnalysisDto.SegmentSummary> buildSegments(
            DetectionTask task,
            List<FrameResult> rows,
            Map<String, List<FrameResult>> byAlgorithm,
            List<GroundTruthEvent> gtEvents,
            List<AnalysisDto.TamperEvent> consensusEvents,
            Map<String, Double> costs) {

        Map<Integer, Integer> segmentOfFrame = new HashMap<>();
        for (FrameResult row : rows) {
            segmentOfFrame.putIfAbsent(row.getFrameIndex(),
                    row.getSegmentIndex() == null ? 0 : row.getSegmentIndex());
        }

        List<Map<String, Object>> plans = parseSegmentPlans(task.getSegmentsJson());
        if (plans.isEmpty()) {
            long start = task.getStartMs() == null ? 0L : task.getStartMs();
            long end = task.getEndMs() == null
                    ? rows.stream().map(FrameResult::getTimestampMs)
                            .filter(java.util.Objects::nonNull).max(Long::compareTo).orElse(0L)
                    : task.getEndMs();
            Map<String, Object> plan = new LinkedHashMap<>();
            plan.put("label", task.getStartMs() == null ? "整段" : "手动区间");
            plan.put("startMs", start);
            plan.put("endMs", end);
            plan.put("index", 0);
            plans = List.of(plan);
        }

        // 帧级投票：≥2 个算法同时告警才算该帧篡改（单算法时为 1）
        List<String> classic = byAlgorithm.keySet().stream()
                .filter(a -> !DETECTION_ONLY.contains(a))
                .toList();
        int needed = classic.size() >= 2 ? 2 : 1;
        Map<Integer, List<FrameResult>> perFrame = new HashMap<>();
        for (String algorithm : classic) {
            for (FrameResult row : byAlgorithm.get(algorithm)) {
                perFrame.computeIfAbsent(row.getFrameIndex(), k -> new ArrayList<>()).add(row);
            }
        }
        Map<Integer, Boolean> frameAlarm = new HashMap<>();
        perFrame.forEach((index, list) -> frameAlarm.put(index,
                list.stream().filter(r -> Boolean.TRUE.equals(r.getTampered())).count() >= needed));

        List<AnalysisDto.SegmentSummary> summaries = new ArrayList<>();
        for (int i = 0; i < plans.size(); i++) {
            Map<String, Object> plan = plans.get(i);
            long start = asLongValue(plan.get("startMs"), 0L);
            long end = asLongValue(plan.get("endMs"), start);
            Object rawType = plan.get("expectedType") != null ? plan.get("expectedType") : plan.get("type");
            String expected = rawType == null ? null : String.valueOf(rawType);
            String label = plan.get("label") == null
                    ? String.format(Locale.CHINA, "%.1fs - %.1fs", start / 1000.0, end / 1000.0)
                    : String.valueOf(plan.get("label"));

            List<FrameResult> inSegment = rows.stream()
                    .filter(r -> r.getTimestampMs() != null
                            && r.getTimestampMs() >= start && r.getTimestampMs() <= end)
                    .toList();
            Set<Integer> frameIds = inSegment.stream().map(FrameResult::getFrameIndex)
                    .collect(Collectors.toCollection(LinkedHashSet::new));
            int tamperedFrames = (int) frameIds.stream()
                    .filter(idx -> Boolean.TRUE.equals(frameAlarm.get(idx)))
                    .count();
            double peak = inSegment.stream().filter(r -> r.getScore() != null)
                    .mapToDouble(FrameResult::getScore).max().orElse(0.0);
            List<FrameResult> alarmed = inSegment.stream()
                    .filter(r -> Boolean.TRUE.equals(r.getTampered())).toList();
            String type = alarmed.isEmpty() ? "normal" : dominantType(alarmed);
            int eventCount = (int) consensusEvents.stream()
                    .filter(e -> e.getStartMs() <= end && e.getEndMs() >= start)
                    .count();

            Boolean gtTampered = null;
            if (!gtEvents.isEmpty()) {
                gtTampered = gtEvents.stream()
                        .anyMatch(g -> g.getStartMs() <= end && g.getEndMs() >= start);
            }
            double cost = inSegment.stream()
                    .mapToDouble(r -> {
                        Double c = r.getAlgorithm() == null ? null : costs.get(r.getAlgorithm());
                        return c == null ? 0.0 : c;
                    })
                    .average().orElse(0.0);

            summaries.add(AnalysisDto.SegmentSummary.builder()
                    .index(segmentOfFrame.getOrDefault(frameIds.stream().findFirst().orElse(-1), i))
                    .label(label)
                    .startMs(start)
                    .endMs(end)
                    .frameCount(frameIds.size())
                    .tamperedFrames(tamperedFrames)
                    .alarmRate(round(frameIds.isEmpty() ? 0 : (double) tamperedFrames / frameIds.size()))
                    .peakScore(round(peak))
                    .tamperType(type)
                    .expectedType(expected)
                    .gtTampered(gtTampered)
                    .matched(gtTampered == null ? null : gtTampered == (tamperedFrames > 0))
                    .typeMatched(Boolean.TRUE.equals(gtTampered) && expected != null
                            ? expected.equalsIgnoreCase(type) : null)
                    .eventCount(eventCount)
                    .avgCostMs(round(cost))
                    .build());
        }
        return summaries;
    }

    private List<Map<String, Object>> parseSegmentPlans(String json) {
        if (json == null || json.isBlank()) {
            return List.of();
        }
        try {
            List<Map<String, Object>> parsed =
                    objectMapper.readValue(json, new TypeReference<List<Map<String, Object>>>() { });
            return parsed == null ? List.of() : parsed;
        } catch (Exception e) {
            log.warn("分段信息解析失败：{}", e.getMessage());
            return List.of();
        }
    }

    // ------------------------------------------------------------------ 事件级真值对照
    /**
     * ±容差事件级匹配。
     *
     * <p>FLAD 的标注中位数只有 1~2 秒，而算法要积累几帧才越阈值，天然带 1~3 秒延迟；
     * 按帧严格比对会把"晚 1 秒的正确告警"同时记成漏报和误报。这里按事件配对，
     * 逐帧口径仍保留在 {@link AnalysisDto.Metrics} 里，两个数字并排看才有信息量。</p>
     */
    private AnalysisDto.EventEvaluation buildEventEvaluation(
            Map<String, List<FrameResult>> byAlgorithm,
            List<GroundTruthEvent> gtEvents,
            long toleranceMs,
            DetectionTask task,
            int totalFrames) {

        if (gtEvents.isEmpty()) {
            return null;
        }
        // 只保留"确实扫描过"的时间范围：分段扫描时未选中的区间不该算成漏报
        List<long[]> ranges = new ArrayList<>();
        List<Map<String, Object>> plans = parseSegmentPlans(task.getSegmentsJson());
        if (plans.isEmpty()) {
            ranges.add(new long[]{
                    task.getStartMs() == null ? 0L : task.getStartMs(),
                    task.getEndMs() == null ? Long.MAX_VALUE : task.getEndMs()});
        } else {
            for (Map<String, Object> plan : plans) {
                long start = asLongValue(plan.get("startMs"), 0L);
                long end = asLongValue(plan.get("endMs"), start);
                if (end > start) {
                    ranges.add(new long[]{start, end});
                }
            }
        }

        List<GroundTruthEvent> clipped = new ArrayList<>();
        Set<String> seen = new LinkedHashSet<>();
        for (GroundTruthEvent event : gtEvents) {
            for (long[] range : ranges) {
                long start = Math.max(event.getStartMs(), range[0]);
                long end = Math.min(event.getEndMs(), range[1]);
                if (end <= start || !seen.add(start + ":" + end + ":" + event.getTamperType())) {
                    continue;
                }
                GroundTruthEvent copy = new GroundTruthEvent();
                copy.setMediaId(event.getMediaId());
                copy.setStartMs(start);
                copy.setEndMs(end);
                copy.setTamperType(event.getTamperType());
                copy.setSource(event.getSource());
                clipped.add(copy);
            }
        }
        clipped.sort(Comparator.comparingLong(GroundTruthEvent::getStartMs));

        Map<String, List<AnalysisDto.EventPair>> pairsByAlgorithm = new LinkedHashMap<>();
        List<AnalysisDto.EventMetrics> perAlgorithm = new ArrayList<>();
        for (Map.Entry<String, List<FrameResult>> entry : byAlgorithm.entrySet()) {
            List<AnalysisDto.TamperEvent> preds = buildEvents(entry.getValue(), List.of(entry.getKey()));
            List<AnalysisDto.EventPair> pairs = matchEvents(clipped, preds, toleranceMs, entry.getKey());
            pairsByAlgorithm.put(entry.getKey(), pairs);
            perAlgorithm.add(toEventMetrics(entry.getKey(), pairs));
        }

        List<AnalysisDto.TamperEvent> ensemblePreds = buildConsensusEvents(byAlgorithm, totalFrames);
        List<AnalysisDto.EventPair> ensemblePairs = matchEvents(clipped, ensemblePreds, toleranceMs, "ensemble");
        AnalysisDto.EventMetrics ensemble = toEventMetrics("ensemble", ensemblePairs);
        pairsByAlgorithm.put("ensemble", ensemblePairs);

        String source = clipped.stream()
                .map(GroundTruthEvent::getSource)
                .filter(s -> s != null && !s.isBlank())
                .findFirst().orElse("");

        return AnalysisDto.EventEvaluation.builder()
                .toleranceMs(toleranceMs)
                .gtCount(clipped.size())
                .source(source)
                .perAlgorithm(perAlgorithm)
                .ensemble(ensemble)
                .pairs(ensemblePairs)
                .pairsByAlgorithm(pairsByAlgorithm)
                .build();
    }

    private List<AnalysisDto.EventPair> matchEvents(List<GroundTruthEvent> gts,
                                                    List<AnalysisDto.TamperEvent> preds,
                                                    long toleranceMs,
                                                    String algorithm) {
        List<AnalysisDto.TamperEvent> ordered = preds.stream()
                .sorted(Comparator.comparingLong(AnalysisDto.TamperEvent::getStartMs))
                .toList();
        boolean[] used = new boolean[ordered.size()];
        List<AnalysisDto.EventPair> pairs = new ArrayList<>();

        for (GroundTruthEvent gt : gts) {
            int bestIndex = -1;
            long bestOverlap = -1;
            long bestDistance = Long.MAX_VALUE;
            for (int i = 0; i < ordered.size(); i++) {
                if (used[i]) {
                    continue;
                }
                AnalysisDto.TamperEvent pred = ordered.get(i);
                if (pred.getStartMs() > gt.getEndMs() + toleranceMs
                        || pred.getEndMs() < gt.getStartMs() - toleranceMs) {
                    continue;
                }
                long overlap = Math.min(gt.getEndMs(), pred.getEndMs())
                        - Math.max(gt.getStartMs(), pred.getStartMs());
                long distance = Math.abs(pred.getStartMs() - gt.getStartMs());
                if (overlap > bestOverlap || (overlap == bestOverlap && distance < bestDistance)) {
                    bestIndex = i;
                    bestOverlap = overlap;
                    bestDistance = distance;
                }
            }
            if (bestIndex >= 0) {
                AnalysisDto.TamperEvent pred = ordered.get(bestIndex);
                used[bestIndex] = true;
                long overlap = Math.max(0, Math.min(gt.getEndMs(), pred.getEndMs())
                        - Math.max(gt.getStartMs(), pred.getStartMs()));
                pairs.add(AnalysisDto.EventPair.builder()
                        .kind("tp")
                        .algorithm(algorithm)
                        .gtStartMs(gt.getStartMs())
                        .gtEndMs(gt.getEndMs())
                        .gtType(gt.getTamperType())
                        .predStartMs(pred.getStartMs())
                        .predEndMs(pred.getEndMs())
                        .predType(pred.getTamperType())
                        .latencyMs(pred.getStartMs() - gt.getStartMs())
                        .overlapMs(overlap)
                        .peakScore(pred.getPeakScore())
                        .build());
            } else {
                pairs.add(AnalysisDto.EventPair.builder()
                        .kind("fn")
                        .algorithm(algorithm)
                        .gtStartMs(gt.getStartMs())
                        .gtEndMs(gt.getEndMs())
                        .gtType(gt.getTamperType())
                        .build());
            }
        }

        for (int i = 0; i < ordered.size(); i++) {
            if (used[i]) {
                continue;
            }
            AnalysisDto.TamperEvent pred = ordered.get(i);
            long nearest = nearestGtDistance(pred, gts);
            pairs.add(AnalysisDto.EventPair.builder()
                    .kind("fp")
                    .algorithm(algorithm)
                    .predStartMs(pred.getStartMs())
                    .predEndMs(pred.getEndMs())
                    .predType(pred.getTamperType())
                    .nearestGtMs(nearest)
                    .peakScore(pred.getPeakScore())
                    .build());
        }

        pairs.sort(Comparator.comparingLong(p -> p.getGtStartMs() == null
                ? p.getPredStartMs() : p.getGtStartMs()));
        return pairs;
    }

    private static long nearestGtDistance(AnalysisDto.TamperEvent pred, List<GroundTruthEvent> gts) {
        long best = -1;
        for (GroundTruthEvent gt : gts) {
            long distance;
            if (pred.getEndMs() < gt.getStartMs()) {
                distance = gt.getStartMs() - pred.getEndMs();
            } else if (pred.getStartMs() > gt.getEndMs()) {
                distance = pred.getStartMs() - gt.getEndMs();
            } else {
                return 0;
            }
            if (best < 0 || distance < best) {
                best = distance;
            }
        }
        return best;
    }

    private AnalysisDto.EventMetrics toEventMetrics(String algorithm, List<AnalysisDto.EventPair> pairs) {
        int tp = (int) pairs.stream().filter(p -> "tp".equals(p.getKind())).count();
        int fn = (int) pairs.stream().filter(p -> "fn".equals(p.getKind())).count();
        int fp = (int) pairs.stream().filter(p -> "fp".equals(p.getKind())).count();
        double precision = tp + fp == 0 ? 0 : (double) tp / (tp + fp);
        double recall = tp + fn == 0 ? 0 : (double) tp / (tp + fn);
        double f1 = precision + recall == 0 ? 0 : 2 * precision * recall / (precision + recall);
        List<Long> latencies = pairs.stream()
                .filter(p -> "tp".equals(p.getKind()) && p.getLatencyMs() != null)
                .map(AnalysisDto.EventPair::getLatencyMs)
                .toList();
        double mean = latencies.stream().mapToLong(Long::longValue).average().orElse(0);
        long max = latencies.stream().mapToLong(Long::longValue).max().orElse(0);
        return AnalysisDto.EventMetrics.builder()
                .algorithm(algorithm)
                .truePositive(tp)
                .falsePositive(fp)
                .falseNegative(fn)
                .precision(round(precision))
                .recall(round(recall))
                .f1(round(f1))
                .meanLatencyMs(round(mean))
                .maxLatencyMs(max)
                .build();
    }

    private static long asLongValue(Object value, long fallback) {
        if (value instanceof Number number) {
            return number.longValue();
        }
        if (value instanceof String text) {
            try {
                return Long.parseLong(text.trim());
            } catch (NumberFormatException ignored) {
                return fallback;
            }
        }
        return fallback;
    }
}
