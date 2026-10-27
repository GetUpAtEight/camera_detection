package com.demo.tamper.service;

import com.demo.tamper.common.BusinessException;
import com.demo.tamper.model.dto.LiveDto;
import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.io.IOException;
import java.io.InputStream;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.ConcurrentHashMap;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;
import org.springframework.web.multipart.MultipartFile;

/**
 * 实时监控服务：本机摄像头按固定间隔抓帧，交给算法层做单帧检测。
 *
 * <p>设计取舍：
 * <ul>
 *   <li><b>参考帧只传一次</b>：会话创建时上传首帧作为基准，后续每帧只传当前帧，
 *       避免两倍上行流量（远程算法层经 SSH 桥，带宽是瓶颈）；</li>
 *   <li><b>连续帧去抖</b>：单帧的偶发误报不做告警，连续 {@code requiredStreak} 帧判篡改才告警，
 *       对应监控系统里的"防抖延时"；</li>
 *   <li><b>会话只在内存</b>：实时能力用于演示与现场调参，不写数据库；需要留存时前端导出。</li>
 * </ul>
 */
@Service
public class LiveService {

    private static final Logger log = LoggerFactory.getLogger(LiveService.class);

    /** 会话闲置多久后回收 */
    private static final long IDLE_TIMEOUT_MS = 30 * 60 * 1000L;
    /** 单个会话最多保留多少张告警帧，避免长时间监控把磁盘写满 */
    private static final int MAX_ALARM_IMAGES = 30;

    private static final Map<String, String> TYPE_TEXT = Map.of(
            "defocused", "失焦/模糊",
            "covered", "遮挡",
            "moved", "移位/转向",
            "normal", "正常");

    /** 类型判定优先级：失焦的特征最"硬"（清晰度下降几乎不受其他因素影响），遮挡次之 */
    private static final List<String> TYPE_PRIORITY = List.of("defocused", "covered", "moved");

    private final AlgoGatewayService gateway;
    private final AnalysisService analysisService;
    private final ObjectMapper objectMapper;
    private final Path baseDir;
    private final Map<String, Session> sessions = new ConcurrentHashMap<>();

    /** 算法中文名缓存（算法层离线时退化为英文名，不影响检测） */
    private volatile Map<String, String> displayNames = Map.of();
    private volatile long displayNamesAt = 0L;

    public LiveService(AlgoGatewayService gateway, AnalysisService analysisService,
                       ObjectMapper objectMapper,
                       com.demo.tamper.config.AppProperties properties) {
        this.gateway = gateway;
        this.analysisService = analysisService;
        this.objectMapper = objectMapper;
        this.baseDir = Path.of(properties.getStorage().getUploadDir()).toAbsolutePath()
                .getParent().resolve("live");
        cleanupOldDirs();
    }

    // ------------------------------------------------------------------ 会话

    /** 会话内部状态。 */
    private static final class Session {
        final String id;
        final String algorithmsJson;
        /** 学习期结束会按场景回填阈值，所以不是 final */
        String paramsJson;
        final List<String> algorithms;
        final int requiredStreak;
        final Path dir;
        final long createdAt = System.currentTimeMillis();

        int frameCount;
        int alarmCount;
        int streak;
        int alarmImages;
        long lastFrameAt = createdAt;
        /** 下一帧要丢掉算法层保存的检测器状态（重设参考帧后，旧背景模型/直方图池已失效） */
        boolean resetDetectors;

        Session(String id, String algorithmsJson, String paramsJson, List<String> algorithms,
                int requiredStreak, Path dir) {
            this.id = id;
            this.algorithmsJson = algorithmsJson;
            this.paramsJson = paramsJson;
            this.algorithms = algorithms;
            this.requiredStreak = requiredStreak;
            this.dir = dir;
        }
    }

    public LiveDto.SessionInfo create(MultipartFile reference, String algorithmsJson,
                                      String paramsJson, Integer requiredStreak) {
        cleanupExpired();
        if (reference == null || reference.isEmpty()) {
            throw new BusinessException(400, "缺少参考帧：请先让摄像头对准正常画面再开始监控");
        }
        List<String> algorithms = parseAlgorithms(algorithmsJson);
        int streak = requiredStreak == null ? 2 : Math.min(5, Math.max(1, requiredStreak));

        String id = UUID.randomUUID().toString();
        Path dir = baseDir.resolve(id);
        try {
            Files.createDirectories(dir.resolve("alarms"));
            writeFile(reference, dir.resolve("reference.jpg"));
        } catch (IOException e) {
            throw new BusinessException(500, "保存参考帧失败：" + e.getMessage());
        }

        Session session = new Session(id, algorithmsJson, paramsJson, algorithms, streak, dir);
        sessions.put(session.id, session);
        log.info("实时会话创建 {}，算法 {}，去抖 {} 帧", session.id, algorithms, streak);
        return toInfo(session);
    }

    public LiveDto.FrameResult detect(String sessionId, MultipartFile frame) {
        return detect(sessionId, frame, false);
    }

    /**
     * 单帧检测。
     *
     * @param probe true = 学习期采样帧：只回各算法指标（供前端标定阈值），
     *              不推进帧号、不累计连续告警、不落盘告警图，也不影响会话统计
     */
    public LiveDto.FrameResult detect(String sessionId, MultipartFile frame, boolean probe) {
        Session session = require(sessionId);
        if (frame == null || frame.isEmpty()) {
            throw new BusinessException(400, "帧内容为空");
        }

        synchronized (session) {
            long started = System.currentTimeMillis();
            Path framePath = session.dir.resolve("frame.jpg");
            Path refPath = session.dir.resolve("reference.jpg");
            try {
                writeFile(frame, framePath);
            } catch (IOException e) {
                throw new BusinessException(500, "保存帧失败：" + e.getMessage());
            }

            Map<String, Object> raw;
            try {
                // visual=false：实时链路不需要中间过程图，data URL 会把每帧响应撑到几百 KB
                // sessionId：让算法层复用同一批检测器，MOG2/帧差/短长期直方图才有帧间状态
                boolean reset = session.resetDetectors;
                raw = gateway.detectImage(framePath, refPath, session.algorithmsJson,
                        session.paramsJson, false, session.id, reset);
                session.resetDetectors = false;
            } catch (RuntimeException e) {
                throw new BusinessException(502, "算法层不可用：" + e.getMessage());
            }

            List<LiveDto.AlgoResult> results = parseResults(raw);
            session.lastFrameAt = System.currentTimeMillis();

            if (probe) {
                double probeScore = results.stream()
                        .filter(r -> !r.skipped())
                        .mapToDouble(LiveDto.AlgoResult::score)
                        .max().orElse(0.0);
                return new LiveDto.FrameResult(
                        session.id,
                        session.frameCount,
                        session.lastFrameAt,
                        false,
                        false,
                        0,
                        session.requiredStreak,
                        0,
                        "normal",
                        round(probeScore),
                        "学习期采样（不判定）",
                        results,
                        System.currentTimeMillis() - started,
                        null);
            }

            session.frameCount++;

            // ---- 集成判定：与离线分析同一口径（≥2 个主力算法同帧告警） ----
            List<LiveDto.AlgoResult> voting = results.stream()
                    .filter(r -> !r.skipped() && analysisService.isVotingAlgorithm(r.algorithm()))
                    .toList();
            List<LiveDto.AlgoResult> voted = voting.stream()
                    .filter(LiveDto.AlgoResult::tampered)
                    .toList();
            int needed = voting.size() >= 2 ? 2 : 1;
            boolean frameTampered = voted.size() >= needed;

            session.streak = frameTampered ? session.streak + 1 : 0;
            boolean alarm = frameTampered && session.streak >= session.requiredStreak;
            if (alarm) {
                session.alarmCount++;
            }

            String tamperType = pickType(voted);
            double score = voted.isEmpty()
                    ? results.stream().mapToDouble(LiveDto.AlgoResult::score).max().orElse(0.0)
                    : voted.stream().mapToDouble(LiveDto.AlgoResult::score).average().orElse(0.0);

            String alarmUrl = null;
            if (alarm && session.alarmImages < MAX_ALARM_IMAGES) {
                alarmUrl = saveAlarmImage(session, framePath);
            }

            String verdict = buildVerdict(frameTampered, alarm, session, tamperType, voted.size(),
                    voting.size());

            return new LiveDto.FrameResult(
                    session.id,
                    session.frameCount,
                    System.currentTimeMillis(),
                    alarm,
                    frameTampered,
                    session.streak,
                    session.requiredStreak,
                    voted.size(),
                    tamperType,
                    round(score),
                    verdict,
                    results,
                    System.currentTimeMillis() - started,
                    alarmUrl);
        }
    }

    /** 重设参考帧：画面恢复正常后，把它作为新的基准。 */
    public LiveDto.SessionInfo resetReference(String sessionId, MultipartFile reference) {        Session session = require(sessionId);
        if (reference == null || reference.isEmpty()) {
            throw new BusinessException(400, "缺少参考帧");
        }
        synchronized (session) {
            try {
                writeFile(reference, session.dir.resolve("reference.jpg"));
            } catch (IOException e) {
                throw new BusinessException(500, "保存参考帧失败：" + e.getMessage());
            }
            session.streak = 0;
            session.resetDetectors = true;
            session.lastFrameAt = System.currentTimeMillis();
        }
        log.info("实时会话 {} 重设参考帧（连续告警计数清零）", session.id);
        return toInfo(session);
    }

    /**
     * 更新检测参数：前端学习完当前场景的正常波动后，把算出来的阈值回填到会话。
     *
     * <p>会话建完之后才可能知道"这个场景的正常抖动有多大"，所以参数必须能在运行中改；
     * 改参数意味着判定口径变了，连续计数一并清零，避免新旧口径的结果拼成一个 streak。
     */
    public LiveDto.SessionInfo updateParams(String sessionId, String paramsJson) {
        Session session = require(sessionId);
        if (paramsJson == null || paramsJson.isBlank()) {
            throw new BusinessException(400, "params 不能为空");
        }
        synchronized (session) {
            session.paramsJson = paramsJson;
            session.streak = 0;
            session.lastFrameAt = System.currentTimeMillis();
        }
        // 原地改算法层的阈值：不能重建检测器，否则 MOG2 背景模型/短长期直方图池都要从头攒
        gateway.updateSessionParams(session.id, paramsJson);
        log.info("实时会话 {} 更新检测参数（连续告警计数清零）", session.id);
        return toInfo(session);
    }

    public LiveDto.SessionInfo info(String sessionId) {
        return toInfo(require(sessionId));
    }

    public void close(String sessionId) {
        close(sessionId, false);
    }

    /**
     * 结束会话。
     *
     * @param purge true = 连告警帧一起删（闲置回收用）；false = 只删参考帧与当前帧，
     *              保留告警帧让页面上的历史记录还能看到现场图
     */
    public void close(String sessionId, boolean purge) {
        Session session = sessions.remove(sessionId);
        if (session == null) {
            return;
        }
        // 算法层为这个会话保存着检测器实例（MOG2 背景模型等），会话结束要一起释放
        gateway.dropSession(session.id);
        if (purge) {
            deleteRecursively(session.dir);
        } else {
            try {
                Files.deleteIfExists(session.dir.resolve("frame.jpg"));
                Files.deleteIfExists(session.dir.resolve("reference.jpg"));
            } catch (IOException e) {
                log.warn("清理实时会话临时帧失败：{}", e.getMessage());
            }
        }
        log.info("实时会话结束 {}，共 {} 帧 / {} 次告警", sessionId, session.frameCount, session.alarmCount);
    }

    private void deleteRecursively(Path dir) {
        try (var paths = Files.walk(dir)) {
            paths.sorted(Comparator.reverseOrder()).forEach(p -> {
                try {
                    Files.deleteIfExists(p);
                } catch (IOException ignored) {
                    // 删不掉不影响会话已结束
                }
            });
        } catch (IOException e) {
            log.warn("清理实时会话目录失败：{}", e.getMessage());
        }
    }

    /** 启动时清掉一天前的残留目录（保留告警帧意味着目录会累积，需要有兜底） */
    private void cleanupOldDirs() {
        if (!Files.exists(baseDir)) {
            return;
        }
        long cutoff = System.currentTimeMillis() - 24 * 60 * 60 * 1000L;
        try (var dirs = Files.list(baseDir)) {
            dirs.filter(Files::isDirectory).forEach(dir -> {
                try {
                    if (Files.getLastModifiedTime(dir).toMillis() < cutoff) {
                        deleteRecursively(dir);
                    }
                } catch (IOException ignored) {
                    // 单个目录读不到时间戳时跳过
                }
            });
        } catch (IOException e) {
            log.warn("清理历史实时会话目录失败：{}", e.getMessage());
        }
    }

    /**
     * 参考帧 / 告警帧按目录直接读取，不要求会话还在内存里。
     * 页面停止监控后仍会渲染历史记录里的现场图，若这里依赖会话存在就会 404。
     */
    public Path referenceImage(String sessionId) {
        return baseDir.resolve(safeId(sessionId)).resolve("reference.jpg");
    }

    public Path alarmImage(String sessionId, int frameIndex) {
        return baseDir.resolve(safeId(sessionId)).resolve("alarms").resolve(frameIndex + ".jpg");
    }

    /** 只接受标准 UUID，杜绝 "../" 之类的路径穿越 */
    private static String safeId(String sessionId) {
        if (sessionId == null || !sessionId.matches("[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")) {
            throw BusinessException.notFound("会话 id 不合法");
        }
        return sessionId;
    }

    // ------------------------------------------------------------------ 内部

    private Session require(String sessionId) {
        Session session = sessions.get(sessionId);
        if (session == null) {
            throw BusinessException.notFound("实时会话不存在或已结束，请重新开始监控");
        }
        return session;
    }

    private void cleanupExpired() {
        long now = System.currentTimeMillis();
        List<String> expired = sessions.entrySet().stream()
                .filter(e -> now - e.getValue().lastFrameAt > IDLE_TIMEOUT_MS)
                .map(Map.Entry::getKey)
                .toList();
        for (String id : expired) {
            log.info("回收闲置实时会话 {}", id);
            close(id, true);
        }
    }

    private LiveDto.SessionInfo toInfo(Session session) {
        return new LiveDto.SessionInfo(
                session.id,
                session.algorithms,
                session.requiredStreak,
                session.frameCount,
                session.alarmCount,
                session.createdAt,
                session.lastFrameAt,
                "/api/live/sessions/" + session.id + "/reference",
                session.alarmImages);
    }

    private String saveAlarmImage(Session session, Path framePath) {
        try {
            Path target = session.dir.resolve("alarms").resolve((session.frameCount) + ".jpg");
            Files.copy(framePath, target, StandardCopyOption.REPLACE_EXISTING);
            session.alarmImages++;
            return "/api/live/sessions/" + session.id + "/alarms/" + session.frameCount;
        } catch (IOException e) {
            log.warn("保存告警帧失败：{}", e.getMessage());
            return null;
        }
    }

    private void writeFile(MultipartFile file, Path target) throws IOException {
        Files.createDirectories(target.getParent());
        try (InputStream in = file.getInputStream()) {
            Files.copy(in, target, StandardCopyOption.REPLACE_EXISTING);
        }
    }

    private List<String> parseAlgorithms(String algorithmsJson) {
        try {
            List<String> names = objectMapper.readValue(algorithmsJson, new TypeReference<List<String>>() {
            });
            if (names == null || names.isEmpty()) {
                throw new BusinessException(400, "至少选择一个算法");
            }
            return names.stream().distinct().toList();
        } catch (BusinessException e) {
            throw e;
        } catch (IOException e) {
            throw new BusinessException(400, "algorithms 必须是字符串数组");
        }
    }

    @SuppressWarnings("unchecked")
    private List<LiveDto.AlgoResult> parseResults(Map<String, Object> raw) {
        Object value = raw.get("results");
        if (!(value instanceof List<?> list)) {
            throw new BusinessException(502, "算法层返回格式异常：缺少 results");
        }
        Map<String, String> names = displayNames();
        List<LiveDto.AlgoResult> out = new ArrayList<>(list.size());
        for (Object item : list) {
            if (!(item instanceof Map<?, ?> map)) {
                continue;
            }
            Map<String, Object> row = (Map<String, Object>) map;
            String algorithm = String.valueOf(row.getOrDefault("algorithm", ""));
            Map<String, Object> detail = row.get("detail") instanceof Map<?, ?> d
                    ? (Map<String, Object>) d
                    : Map.of();
            out.add(new LiveDto.AlgoResult(
                    algorithm,
                    names.getOrDefault(algorithm, algorithm),
                    asDouble(row.get("score")),
                    Boolean.TRUE.equals(row.get("isTampered")),
                    String.valueOf(row.getOrDefault("tamperType", "normal")),
                    String.valueOf(row.getOrDefault("note", "")),
                    Boolean.TRUE.equals(row.get("skipped")),
                    detail));
        }
        return out;
    }

    private Map<String, String> displayNames() {
        long now = System.currentTimeMillis();
        if (now - displayNamesAt < 60_000L) {
            return displayNames;
        }
        try {
            Map<String, String> map = new HashMap<>();
            for (var meta : gateway.algorithms()) {
                map.put(meta.name(), meta.displayName());
            }
            displayNames = map;
            displayNamesAt = now;
        } catch (RuntimeException e) {
            // 算法层暂时取不到元数据时沿用旧缓存（首次为空则退化为英文名）
            displayNamesAt = now;
        }
        return displayNames;
    }

    private String pickType(List<LiveDto.AlgoResult> voted) {
        if (voted.isEmpty()) {
            return "normal";
        }
        Map<String, Integer> counts = new LinkedHashMap<>();
        for (LiveDto.AlgoResult r : voted) {
            counts.merge(r.tamperType(), 1, Integer::sum);
        }
        return counts.entrySet().stream()
                .max(Comparator
                        .comparingInt((Map.Entry<String, Integer> e) -> e.getValue())
                        .thenComparingInt(e -> {
                            int idx = TYPE_PRIORITY.indexOf(e.getKey());
                            return idx < 0 ? TYPE_PRIORITY.size() : TYPE_PRIORITY.size() - idx;
                        }))
                .map(Map.Entry::getKey)
                .orElse("normal");
    }

    private String buildVerdict(boolean frameTampered, boolean alarm, Session session,
                                String tamperType, int votes, int total) {
        String typeText = TYPE_TEXT.getOrDefault(tamperType, tamperType);
        if (alarm) {
            return "检测到镜头" + typeText + "（" + votes + "/" + total + " 个算法告警，连续 "
                    + session.streak + " 帧）";
        }
        if (frameTampered) {
            int remain = Math.max(0, session.requiredStreak - session.streak);
            return "疑似镜头" + typeText + "，再确认 " + remain + " 帧后告警";
        }
        return "画面正常";
    }

    private static double asDouble(Object value) {
        if (value instanceof Number number) {
            return number.doubleValue();
        }
        try {
            return Double.parseDouble(String.valueOf(value));
        } catch (NumberFormatException e) {
            return 0.0;
        }
    }

    private static double round(double value) {
        return Math.round(value * 1000.0) / 1000.0;
    }

    /** 供控制器统计用：当前活跃会话数。 */
    public int activeSessions() {
        return sessions.size();
    }
}
