package com.demo.tamper.service;

import com.demo.tamper.common.BusinessException;
import com.demo.tamper.config.AppProperties;
import com.demo.tamper.model.dto.DetectionRunRequest;
import com.demo.tamper.model.dto.DetectionTaskDto;
import com.demo.tamper.model.dto.FrameResultDto;
import com.demo.tamper.model.dto.SegmentSpec;
import com.demo.tamper.model.entity.DetectionTask;
import com.demo.tamper.model.entity.FrameResult;
import com.demo.tamper.model.entity.MediaFile;
import com.demo.tamper.model.entity.MediaKind;
import com.demo.tamper.model.entity.TaskStatus;
import com.demo.tamper.repository.DetectionTaskRepository;
import com.demo.tamper.repository.FrameResultRepository;
import com.demo.tamper.repository.MediaFileRepository;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Instant;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.Base64;
import java.util.HashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Optional;
import java.util.Set;
import java.util.UUID;
import java.util.concurrent.Executor;
import java.util.stream.Collectors;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Qualifier;
import org.springframework.data.domain.Page;
import org.springframework.data.domain.PageRequest;
import org.springframework.data.domain.Sort;
import org.springframework.stereotype.Service;

/**
 * 检测任务编排：创建任务 → 异步调用算法层 → 轮询进度 → 落库逐帧结果。
 */
@Service
public class DetectionService {

    private static final Logger log = LoggerFactory.getLogger(DetectionService.class);
    private static final int FRAME_PAGE_SIZE = 100;
    /** 一次分段扫描最多多少段（FLAD 单个视频 20 段左右，留足余量） */
    private static final int MAX_SEGMENTS = 500;
    /** 已确认存在于远端缓存的媒体 id（避免同一段录像反复经网络传输） */
    private final Set<String> remoteCached = java.util.concurrent.ConcurrentHashMap.newKeySet();

    private final DetectionTaskRepository taskRepository;
    private final FrameResultRepository frameRepository;
    private final MediaFileRepository mediaRepository;
    private final MediaService mediaService;
    private final AlgoGatewayService gateway;
    private final AppProperties properties;
    private final ObjectMapper objectMapper;
    private final Executor executor;

    public DetectionService(DetectionTaskRepository taskRepository,
                           FrameResultRepository frameRepository,
                           MediaFileRepository mediaRepository,
                           MediaService mediaService,
                           AlgoGatewayService gateway,
                           AppProperties properties,
                           ObjectMapper objectMapper,
                           @Qualifier("detectionExecutor") Executor executor) {
        this.taskRepository = taskRepository;
        this.frameRepository = frameRepository;
        this.mediaRepository = mediaRepository;
        this.mediaService = mediaService;
        this.gateway = gateway;
        this.properties = properties;
        this.objectMapper = objectMapper;
        this.executor = executor;
    }

    // ------------------------------------------------------------------ 提交
    public DetectionTaskDto submit(DetectionRunRequest request) {
        MediaFile media = mediaService.getEntity(request.mediaId());
        List<String> algorithms = request.algorithms().stream().distinct().toList();
        validateAlgorithms(algorithms);
        List<SegmentSpec> segments = normalizeSegments(request, media);
        long[] window = overallWindow(request, segments);

        DetectionTask task = new DetectionTask();
        task.setId(UUID.randomUUID().toString());
        task.setMediaId(media.getId());
        task.setAlgorithms(String.join(",", algorithms));
        task.setParamsJson(writeJson(request.params() == null ? Map.of() : request.params()));
        task.setStatus(TaskStatus.PENDING);
        task.setProgress(0.0);
        task.setStartMs(window[0] >= 0 ? window[0] : null);
        task.setEndMs(window[1] >= 0 ? window[1] : null);
        task.setSegmentCount(segments.size());
        task.setMessage(segments.isEmpty() ? "已提交，排队执行" : "已提交，" + segments.size() + " 段排队执行");
        task.setCreatedAt(Instant.now());
        taskRepository.save(task);

        DetectionRunRequest normalized = new DetectionRunRequest(media.getId(), algorithms,
                request.params(), request.sampleFps(), request.maxFrames(),
                task.getStartMs(), task.getEndMs(), segments, request.refGapMs());
        executor.execute(() -> run(task.getId(), normalized));
        return DetectionTaskDto.from(task, media.getOriginalName(), media.getMediaType().name());
    }

    /**
     * 整理分段：裁到媒体时长内、丢掉空段、按时间排序，并限制段数（防止一次提交上千段把队列压垮）。
     */
    private List<SegmentSpec> normalizeSegments(DetectionRunRequest request, MediaFile media) {
        if (!request.hasSegments()) {
            return List.of();
        }
        long duration = media.getDurationMs() == null ? 0 : media.getDurationMs();
        List<SegmentSpec> cleaned = new ArrayList<>();
        for (SegmentSpec spec : request.segments()) {
            if (spec == null) {
                continue;
            }
            long start = spec.startMs() == null ? 0 : Math.max(0, spec.startMs());
            long end = spec.endMs() == null ? duration : spec.endMs();
            if (duration > 0) {
                start = Math.min(start, duration);
                end = Math.min(end, duration);
            }
            if (end <= start) {
                continue;
            }
            cleaned.add(new SegmentSpec(
                    spec.label() == null || spec.label().isBlank()
                            ? String.format(Locale.CHINA, "%.1fs - %.1fs", start / 1000.0, end / 1000.0)
                            : spec.label(),
                    spec.type(), start, end));
        }
        cleaned.sort(Comparator.comparingLong(SegmentSpec::startMs));
        if (cleaned.size() > MAX_SEGMENTS) {
            throw new BusinessException("一次最多扫描 " + MAX_SEGMENTS + " 段，请先筛选标注区间");
        }
        return cleaned;
    }

    /** 返回 {start, end}，整段检测时为 {-1, -1}。 */
    private long[] overallWindow(DetectionRunRequest request, List<SegmentSpec> segments) {
        if (!segments.isEmpty()) {
            return new long[]{segments.get(0).startMs(), segments.get(segments.size() - 1).endMs()};
        }
        if (request.hasWindow()) {
            return new long[]{request.startMs(), request.endMs()};
        }
        return new long[]{-1, -1};
    }

    /**
     * 提交前先对照算法层清单做一次校验，避免"任务建了、跑起来才发现算法名拼错"。
     * 算法层不可用时不阻塞提交（执行阶段会给出更明确的连接错误）。
     */
    private void validateAlgorithms(List<String> algorithms) {
        if (algorithms.isEmpty()) {
            throw new BusinessException("至少选择一个算法");
        }
        Set<String> known;
        try {
            known = gateway.algorithms().stream()
                    .map(com.demo.tamper.model.dto.AlgorithmMeta::name)
                    .collect(Collectors.toSet());
        } catch (RuntimeException e) {
            log.warn("无法获取算法清单，跳过提交前校验：{}", e.getMessage());
            return;
        }
        List<String> unknown = algorithms.stream().filter(name -> !known.contains(name)).toList();
        if (!unknown.isEmpty()) {
            throw new BusinessException("未知算法：" + String.join(", ", unknown));
        }
    }

    // ------------------------------------------------------------------ 执行
    private void run(String taskId, DetectionRunRequest request) {
        DetectionTask task = taskRepository.findById(taskId).orElse(null);
        if (task == null) {
            log.warn("任务 {} 已不存在，跳过执行", taskId);
            return;
        }
        MediaFile media = mediaRepository.findById(task.getMediaId()).orElse(null);
        if (media == null) {
            fail(task, "媒体文件不存在，可能已被删除");
            return;
        }

        task.setStatus(TaskStatus.RUNNING);
        task.setStartedAt(Instant.now());
        task.setMessage("正在调用算法层");
        task.setProgress(0.05);
        taskRepository.save(task);

        long startedAt = System.currentTimeMillis();
        try {
            Path file = Path.of(media.getStoredPath());
            if (!Files.exists(file)) {
                throw new BusinessException("媒体文件已丢失：" + media.getStoredPath());
            }
            String algorithmsJson = writeJson(request.algorithms());
            String paramsJson = writeJson(request.params() == null ? Map.of() : request.params());

            if (media.getMediaType() == MediaKind.IMAGE) {
                runImage(task, media, file, algorithmsJson, paramsJson);
            } else {
                runVideo(task, media, file, algorithmsJson, paramsJson, request);
            }

            task.setStatus(TaskStatus.DONE);
            task.setProgress(1.0);
            task.setMessage("检测完成");
            task.setFinishedAt(Instant.now());
            task.setElapsedMs(System.currentTimeMillis() - startedAt);
            taskRepository.save(task);
            log.info("检测任务完成 {}，媒体 {}，耗时 {} ms", taskId, media.getOriginalName(),
                    task.getElapsedMs());
        } catch (BusinessException e) {
            fail(task, e.getMessage());
        } catch (Exception e) {
            log.error("检测任务失败 {}", taskId, e);
            fail(task, e.getClass().getSimpleName() + ": " + e.getMessage());
        }
    }

    private void runImage(DetectionTask task, MediaFile media, Path file,
                          String algorithmsJson, String paramsJson) {
        Path reference = mediaService.getReferencePath(media);
        Map<String, Object> response =
                gateway.detectImage(file, reference, algorithmsJson, paramsJson, true);

        @SuppressWarnings("unchecked")
        List<Map<String, Object>> results =
                (List<Map<String, Object>>) response.getOrDefault("results", List.of());

        List<FrameResult> entities = new ArrayList<>();
        boolean tampered = false;
        for (Map<String, Object> result : results) {
            FrameResult entity = toEntity(task.getId(), 0, 0L, result);
            if (Boolean.TRUE.equals(entity.getTampered())) {
                tampered = true;
            }
            entities.add(entity);
        }
        frameRepository.saveAll(entities);

        task.setFrameCount(1);
        task.setTamperedFrameCount(tampered ? 1 : 0);
        task.setElapsedMs(asLong(response.get("elapsedMs")));
        task.setTimingsJson(writeJson(response.getOrDefault("timings", Map.of())));
        task.setProgress(0.99);
        task.setMessage("已保存 " + entities.size() + " 条算法结果");
        taskRepository.save(task);
    }

    private void runVideo(DetectionTask task, MediaFile media, Path file,
                          String algorithmsJson, String paramsJson, DetectionRunRequest request) {
        double sampleFps = request.sampleFps() == null || request.sampleFps() <= 0
                ? properties.getDetection().getSampleFps()
                : request.sampleFps();
        int maxFrames = request.maxFrames() == null || request.maxFrames() <= 0
                ? properties.getDetection().getMaxFrames()
                : request.maxFrames();
        String segmentsJson = request.hasSegments() ? writeJson(request.segments()) : "[]";

        // 356MB 的录像经 SSH 桥传一次就要两分钟，所以先落到远端缓存，后续任务只传 key
        boolean cached = ensureRemoteCached(media, file);
        Map<String, Object> submit =
                gateway.submitVideo(cached ? null : file, algorithmsJson, paramsJson,
                        sampleFps, maxFrames, true,
                        request.hasSegments() ? null : request.startMs(),
                        request.hasSegments() ? null : request.endMs(),
                        segmentsJson, request.refGapMs(),
                        cached ? media.getId() : "");
        String algoTaskId = String.valueOf(submit.get("taskId"));
        task.setAlgoTaskId(algoTaskId);
        task.setMessage(request.hasSegments()
                ? "算法层已接收，" + request.segments().size() + " 段开始检测"
                : request.hasWindow() ? "算法层已接收，区间检测中" : "算法层已接收，开始逐帧检测");
        task.setProgress(0.1);
        taskRepository.save(task);

        pollUntilFinished(task, algoTaskId);
        if (isCancelled(task.getId())) {
            return;
        }
        persistFrames(task, algoTaskId);

        Map<String, Object> snapshot = gateway.getTask(algoTaskId, false, 0, 0);
        task.setElapsedMs(asLong(snapshot.get("elapsedMs")));
        task.setTimingsJson(writeJson(snapshot.getOrDefault("timings", Map.of())));
        Object totalFrames = snapshot.get("totalFrames");
        if (totalFrames instanceof Number number && number.intValue() > 0) {
            task.setFrameCount(number.intValue());
        }
        // 逐段汇总（算法层返回，含每段帧数/告警帧/峰值/主导类型）
        Object segments = snapshot.get("segments");
        if (segments instanceof List<?> list && !list.isEmpty()) {
            task.setSegmentsJson(writeJson(segments));
            task.setSegmentCount(list.size());
        }
        taskRepository.save(task);
    }

    /** 确保媒体已存在于远端缓存；失败则退回直传文件。 */
    private boolean ensureRemoteCached(MediaFile media, Path file) {
        String key = media.getId();
        if (remoteCached.contains(key)) {
            return true;
        }
        try {
            gateway.cacheInfo(key);
            remoteCached.add(key);
            log.info("远端已有媒体缓存 {}，跳过传输", key);
            return true;
        } catch (RuntimeException ignored) {
            // 未缓存，继续上传
        }
        try {
            long started = System.currentTimeMillis();
            gateway.cacheMedia(file, key);
            remoteCached.add(key);
            log.info("媒体 {} 已缓存到远端，耗时 {} ms", media.getOriginalName(),
                    System.currentTimeMillis() - started);
            return true;
        } catch (RuntimeException e) {
            log.warn("远端缓存失败，改为直传文件：{}", e.getMessage());
            return false;
        }
    }

    private void pollUntilFinished(DetectionTask task, String algoTaskId) {
        long pollInterval = Math.max(200, properties.getAlgo().getPollIntervalMs());
        long deadline = System.currentTimeMillis()
                + properties.getAlgo().getMaxPollMinutes() * 60_000L;

        while (true) {
            if (isCancelled(task.getId())) {
                gateway.cancelTask(algoTaskId);
                return;
            }
            Map<String, Object> snapshot = gateway.getTask(algoTaskId, false, 0, 0);
            String status = String.valueOf(snapshot.get("status"));
            double progress = snapshot.get("progress") instanceof Number n ? n.doubleValue() : 0.0;
            task.setProgress(Math.max(0.1, Math.min(0.95, 0.1 + progress * 0.8)));
            task.setMessage(snapshot.get("message") == null ? "检测中" : String.valueOf(snapshot.get("message")));
            taskRepository.save(task);

            switch (status) {
                case "done" -> {
                    return;
                }
                case "failed" -> throw new BusinessException(
                        "算法层执行失败：" + Optional.ofNullable(snapshot.get("error")).orElse("未知错误"));
                case "cancelled" -> {
                    task.setStatus(TaskStatus.CANCELLED);
                    task.setMessage("任务已取消");
                    task.setFinishedAt(Instant.now());
                    taskRepository.save(task);
                    return;
                }
                default -> {
                    // pending / running 继续轮询
                }
            }

            if (System.currentTimeMillis() > deadline) {
                gateway.cancelTask(algoTaskId);
                throw new BusinessException("检测超时（超过 " + properties.getAlgo().getMaxPollMinutes() + " 分钟）");
            }

            try {
                Thread.sleep(pollInterval);
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
                throw new BusinessException("检测线程被中断");
            }
        }
    }

    private void persistFrames(DetectionTask task, String algoTaskId) {
        int offset = 0;
        int totalFrames = 0;
        int tamperedFrames = 0;

        while (true) {
            Map<String, Object> page = gateway.getTask(algoTaskId, true, offset, FRAME_PAGE_SIZE);
            @SuppressWarnings("unchecked")
            List<Map<String, Object>> frames =
                    (List<Map<String, Object>>) page.getOrDefault("frames", List.of());
            if (frames.isEmpty()) {
                break;
            }

            List<FrameResult> entities = new ArrayList<>();
            for (Map<String, Object> frame : frames) {
                int frameIndex = frame.get("frameIndex") instanceof Number n ? n.intValue() : totalFrames;
                int segmentIndex = frame.get("segmentIndex") instanceof Number n ? n.intValue() : 0;
                long timestampMs = frame.get("timestampMs") instanceof Number n ? n.longValue() : 0L;
                @SuppressWarnings("unchecked")
                List<Map<String, Object>> results =
                        (List<Map<String, Object>>) frame.getOrDefault("results", List.of());
                boolean frameTampered = false;
                for (Map<String, Object> result : results) {
                    FrameResult entity = toEntity(task.getId(), frameIndex, timestampMs, result);
                    entity.setSegmentIndex(segmentIndex);
                    if (Boolean.TRUE.equals(entity.getTampered())) {
                        frameTampered = true;
                    }
                    entities.add(entity);
                }
                if (frameTampered) {
                    tamperedFrames++;
                }
                totalFrames++;
            }
            frameRepository.saveAll(entities);

            offset += frames.size();
            if (frames.size() < FRAME_PAGE_SIZE) {
                break;
            }
        }

        task.setFrameCount(totalFrames);
        task.setTamperedFrameCount(tamperedFrames);
        task.setMessage("已保存 " + totalFrames + " 帧检测结果");
        taskRepository.save(task);
    }

    private FrameResult toEntity(String taskId, int frameIndex, long timestampMs,
                                 Map<String, Object> result) {
        FrameResult entity = new FrameResult();
        entity.setTaskId(taskId);
        entity.setAlgorithm(String.valueOf(result.get("algorithm")));
        entity.setFrameIndex(frameIndex);
        entity.setTimestampMs(timestampMs);
        entity.setScore(asDouble(result.get("score")));
        entity.setTampered(Boolean.TRUE.equals(result.get("isTampered")));
        entity.setTamperType(result.get("tamperType") == null ? "normal" : String.valueOf(result.get("tamperType")));
        entity.setConfidence(asDouble(result.get("confidence")));
        entity.setDetailJson(writeJson(result.getOrDefault("detail", Map.of())));
        entity.setNote(truncate(result.get("note") == null ? null : String.valueOf(result.get("note")), 500));
        entity.setSkipped(Boolean.TRUE.equals(result.get("skipped")));
        Object visual = result.get("visual");
        if (visual instanceof String dataUrl && dataUrl.startsWith("data:image")) {
            entity.setVisualFile(saveVisual(taskId, entity.getAlgorithm(), frameIndex, dataUrl));
        }
        return entity;
    }

    private String saveVisual(String taskId, String algorithm, int frameIndex, String dataUrl) {
        try {
            int comma = dataUrl.indexOf(',');
            if (comma < 0) {
                return null;
            }
            byte[] bytes = Base64.getDecoder().decode(dataUrl.substring(comma + 1));
            Path dir = mediaService.getVisualDir().resolve(taskId);
            Files.createDirectories(dir);
            String filename = algorithm + "_" + frameIndex + ".jpg";
            Files.write(dir.resolve(filename), bytes);
            return filename;
        } catch (IOException | IllegalArgumentException e) {
            log.warn("保存可视化图失败 {}/{}: {}", taskId, algorithm, e.getMessage());
            return null;
        }
    }

    // ------------------------------------------------------------------ 查询
    public List<DetectionTaskDto> list() {
        Map<String, MediaFile> mediaMap = mediaRepository.findAll().stream()
                .collect(Collectors.toMap(MediaFile::getId, m -> m, (a, b) -> a));
        return taskRepository.findAllByOrderByCreatedAtDesc().stream()
                .map(task -> {
                    MediaFile media = mediaMap.get(task.getMediaId());
                    return DetectionTaskDto.from(task, media == null ? "已删除的媒体" : media.getOriginalName(),
                            media == null ? "UNKNOWN" : media.getMediaType().name());
                })
                .collect(Collectors.toList());
    }

    public DetectionTask getEntity(String taskId) {
        return taskRepository.findById(taskId)
                .orElseThrow(() -> BusinessException.notFound("任务不存在：" + taskId));
    }

    public DetectionTaskDto get(String taskId) {
        DetectionTask task = getEntity(taskId);
        MediaFile media = mediaRepository.findById(task.getMediaId()).orElse(null);
        return DetectionTaskDto.from(task, media == null ? "已删除的媒体" : media.getOriginalName(),
                media == null ? "UNKNOWN" : media.getMediaType().name());
    }

    /** 分页获取逐帧结果，避免一次性传输过长 JSON。 */
    public Map<String, Object> frames(String taskId, String algorithm, int page, int size) {
        getEntity(taskId);
        int safeSize = Math.max(1, Math.min(2000, size));
        PageRequest pageable = PageRequest.of(Math.max(0, page), safeSize,
                Sort.by(Sort.Direction.ASC, "frameIndex", "algorithm"));

        Page<FrameResult> resultPage = (algorithm == null || algorithm.isBlank())
                ? frameRepository.findByTaskIdOrderByFrameIndexAscAlgorithmAsc(taskId, pageable)
                : frameRepository.findByTaskIdAndAlgorithmOrderByFrameIndexAsc(taskId, algorithm, pageable);

        List<FrameResultDto> items = resultPage.getContent().stream()
                .map(this::toDto)
                .collect(Collectors.toList());

        Map<String, Object> payload = new HashMap<>();
        payload.put("content", items);
        payload.put("page", resultPage.getNumber());
        payload.put("size", resultPage.getSize());
        payload.put("totalElements", resultPage.getTotalElements());
        payload.put("totalPages", resultPage.getTotalPages());
        payload.put("algorithms", frameRepository.findAlgorithmsByTaskId(taskId));
        return payload;
    }

    public FrameResultDto toDto(FrameResult entity) {
        JsonNode detail = null;
        if (entity.getDetailJson() != null && !entity.getDetailJson().isBlank()) {
            try {
                detail = objectMapper.readTree(entity.getDetailJson());
            } catch (IOException e) {
                detail = objectMapper.createObjectNode();
            }
        }
        String visualUrl = entity.getVisualFile() == null
                ? null
                : "/api/detection/" + entity.getTaskId() + "/visual/" + entity.getAlgorithm()
                        + "/" + entity.getFrameIndex();
        return FrameResultDto.from(entity, detail, visualUrl);
    }

    public Set<String> algorithmsOf(String taskId) {
        return new HashSet<>(frameRepository.findAlgorithmsByTaskId(taskId));
    }

    public List<FrameResult> allResults(String taskId) {
        return frameRepository.findByTaskIdOrderByFrameIndexAscAlgorithmAsc(taskId);
    }

    // ------------------------------------------------------------------ 取消/删除
    public void cancel(String taskId) {
        DetectionTask task = getEntity(taskId);
        if (task.getStatus().isFinished()) {
            throw new BusinessException("任务已结束，无法取消");
        }
        task.setStatus(TaskStatus.CANCELLED);
        task.setMessage("用户已取消");
        task.setFinishedAt(Instant.now());
        taskRepository.save(task);
        if (task.getAlgoTaskId() != null) {
            gateway.cancelTask(task.getAlgoTaskId());
        }
    }

    public void delete(String taskId) {
        DetectionTask task = getEntity(taskId);
        if (!task.getStatus().isFinished()) {
            task.setStatus(TaskStatus.CANCELLED);
            taskRepository.save(task);
            if (task.getAlgoTaskId() != null) {
                gateway.cancelTask(task.getAlgoTaskId());
            }
        }
        frameRepository.deleteByTaskId(taskId);
        mediaService.deleteVisualDir(taskId);
        taskRepository.deleteById(taskId);
    }

    // ------------------------------------------------------------------ 工具
    private void fail(DetectionTask task, String message) {
        task.setStatus(TaskStatus.FAILED);
        task.setErrorMessage(truncate(message, 2000));
        task.setMessage("检测失败");
        task.setFinishedAt(Instant.now());
        taskRepository.save(task);
        log.warn("检测任务 {} 失败: {}", task.getId(), message);
    }

    private boolean isCancelled(String taskId) {
        return taskRepository.findById(taskId)
                .map(t -> t.getStatus() == TaskStatus.CANCELLED)
                .orElse(true);
    }

    private String writeJson(Object value) {
        try {
            return objectMapper.writeValueAsString(value);
        } catch (Exception e) {
            return "{}";
        }
    }

    private static Double asDouble(Object value) {
        return value instanceof Number number ? number.doubleValue() : null;
    }

    private static Long asLong(Object value) {
        return value instanceof Number number ? number.longValue() : null;
    }

    private static String truncate(String value, int max) {
        if (value == null) {
            return null;
        }
        return value.length() <= max ? value : value.substring(0, max);
    }
}
