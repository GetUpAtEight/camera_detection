package com.demo.tamper.service;

import com.demo.tamper.common.BusinessException;
import com.demo.tamper.model.entity.GroundTruthEvent;
import com.demo.tamper.model.entity.MediaFile;
import com.demo.tamper.repository.GroundTruthEventRepository;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.nio.charset.StandardCharsets;
import java.time.Instant;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.regex.Matcher;
import java.util.regex.Pattern;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

/**
 * 事件级标注的导入与查询。
 *
 * <p>支持三种来源，都是真实项目里会遇到的格式：</p>
 * <ul>
 *   <li><b>FLAD 的 Survey/baseline.py</b>：<code>video3 = {"occlusion": [["03:16","07:46"]], ...}</code>，
 *       按视频名取对应块；</li>
 *   <li><b>CSV</b>：<code>start,end,type</code> 或 <code>type,start,end</code>，时间支持 <code>mm:ss</code>；</li>
 *   <li><b>JSON</b>：<code>{"events":[{"start":12.0,"end":25.5,"type":"covered"}]}</code>，秒为单位。</li>
 * </ul>
 */
@Service
public class GroundTruthService {

    private static final Logger log = LoggerFactory.getLogger(GroundTruthService.class);

    private static final Pattern VIDEO_BLOCK =
            Pattern.compile("(?m)^\\s*([A-Za-z_][A-Za-z0-9_]*)\\s*=\\s*\\{");
    private static final Pattern TYPE_KEY = Pattern.compile("\"([A-Za-z_]+)\"\\s*:\\s*\\[");
    private static final Pattern SPAN =
            Pattern.compile("\\[\\s*[\"']([0-9:.]+)[\"']\\s*,\\s*[\"']([0-9:.]+)[\"']\\s*\\]");

    /** 标注里的类型别名 → 系统内部类型（与算法层 tamperType 对齐） */
    private static final Map<String, String> TYPE_ALIAS = Map.ofEntries(
            Map.entry("occlusion", "covered"),
            Map.entry("occluded", "covered"),
            Map.entry("covered", "covered"),
            Map.entry("blur", "defocused"),
            Map.entry("bluriness", "defocused"),
            Map.entry("blurred", "defocused"),
            Map.entry("defocus", "defocused"),
            Map.entry("defocused", "defocused"),
            Map.entry("jitter", "moved"),
            Map.entry("moved", "moved"),
            Map.entry("shift", "moved"),
            Map.entry("relocated", "moved"));

    private final GroundTruthEventRepository repository;
    private final ObjectMapper objectMapper;

    public GroundTruthService(GroundTruthEventRepository repository, ObjectMapper objectMapper) {
        this.repository = repository;
        this.objectMapper = objectMapper;
    }

    public record ImportResult(int count, String source, Map<String, Integer> typeCounts,
                               String note, List<GroundTruthEvent> events) {
    }

    // ------------------------------------------------------------------ 查询
    public List<GroundTruthEvent> list(String mediaId) {
        return repository.findByMediaIdOrderByStartMsAsc(mediaId);
    }

    @Transactional
    public void clear(String mediaId) {
        repository.deleteByMediaId(mediaId);
    }

    // ------------------------------------------------------------------ 导入
    @Transactional
    public ImportResult importFile(MediaFile media, String filename, byte[] content) {
        if (content == null || content.length == 0) {
            throw new BusinessException("标注文件为空");
        }
        String text = new String(content, StandardCharsets.UTF_8);
        if (text.startsWith("\uFEFF")) {
            text = text.substring(1);
        }
        String lower = filename == null ? "" : filename.toLowerCase(Locale.ROOT);
        List<RawEvent> raw;
        String source;
        String note;
        if (lower.endsWith(".py")) {
            raw = parseBaseline(text, media.getOriginalName(), media.getDurationMs());
            source = "baseline.py";
            note = "已按视频名 " + stem(media.getOriginalName()) + " 取用标注块";
        } else if (lower.endsWith(".json")) {
            raw = parseJson(text);
            source = "json";
            note = "JSON 事件列表";
        } else {
            raw = parseCsv(text);
            source = "csv";
            note = "CSV 区间";
        }
        if (raw.isEmpty()) {
            throw new BusinessException("没有解析到任何标注区间，请检查文件格式");
        }

        raw.sort(Comparator.comparingLong(RawEvent::startMs));
        repository.deleteByMediaId(media.getId());
        List<GroundTruthEvent> entities = new ArrayList<>();
        for (RawEvent event : raw) {
            GroundTruthEvent entity = new GroundTruthEvent();
            entity.setMediaId(media.getId());
            entity.setStartMs(event.startMs());
            entity.setEndMs(event.endMs());
            entity.setTamperType(event.tamperType());
            // 文件名本身就是 "baseline.py" 时不再重复拼后缀
            String suffix = filename == null || filename.isBlank()
                    || filename.toLowerCase(Locale.ROOT).contains(source) ? "" : ":" + filename;
            entity.setSource(source + suffix);
            entity.setCreatedAt(Instant.now());
            entities.add(entity);
        }
        repository.saveAll(entities);
        log.info("导入标注：媒体 {} 共 {} 段，来源 {}", media.getOriginalName(), entities.size(), source);

        Map<String, Integer> counts = new LinkedHashMap<>();
        for (GroundTruthEvent entity : entities) {
            counts.merge(entity.getTamperType(), 1, Integer::sum);
        }
        return new ImportResult(entities.size(), source, counts, note, entities);
    }

    private record RawEvent(long startMs, long endMs, String tamperType) {
    }

    // ------------------------------------------------------------------ baseline.py
    private List<RawEvent> parseBaseline(String text, String mediaName, Long durationMs) {
        Matcher matcher = VIDEO_BLOCK.matcher(text);
        List<String[]> blocks = new ArrayList<>();
        while (matcher.find()) {
            blocks.add(new String[]{matcher.group(1), String.valueOf(matcher.start())});
        }
        if (blocks.isEmpty()) {
            throw new BusinessException("baseline.py 里没有找到 videoN = {...} 形式的标注块");
        }
        String want = stem(mediaName);
        String block = null;
        for (int i = 0; i < blocks.size(); i++) {
            if (blocks.get(i)[0].equalsIgnoreCase(want)) {
                int begin = Integer.parseInt(blocks.get(i)[1]);
                int end = i + 1 < blocks.size() ? Integer.parseInt(blocks.get(i + 1)[1]) : text.length();
                block = text.substring(begin, end);
                break;
            }
        }
        if (block == null) {
            if (blocks.size() == 1) {
                block = text.substring(Integer.parseInt(blocks.get(0)[1]));
            } else {
                List<String> names = blocks.stream().map(b -> b[0]).toList();
                throw new BusinessException("baseline.py 里没有 " + want + " 的标注块，可选："
                        + String.join(", ", names));
            }
        }

        List<RawEvent> events = new ArrayList<>();
        Matcher keyMatcher = TYPE_KEY.matcher(block);
        List<int[]> keyPositions = new ArrayList<>();
        List<String> keyNames = new ArrayList<>();
        while (keyMatcher.find()) {
            keyPositions.add(new int[]{keyMatcher.start(), keyMatcher.end()});
            keyNames.add(keyMatcher.group(1));
        }
        for (int i = 0; i < keyPositions.size(); i++) {
            int begin = keyPositions.get(i)[1];
            int end = i + 1 < keyPositions.size() ? keyPositions.get(i + 1)[0] : block.length();
            String type = normalizeType(keyNames.get(i));
            Matcher spanMatcher = SPAN.matcher(block.substring(begin, end));
            while (spanMatcher.find()) {
                long start = parseTime(spanMatcher.group(1));
                long stop = parseTime(spanMatcher.group(2));
                if (stop < start) {
                    long tmp = start;
                    start = stop;
                    stop = tmp;
                }
                // 与 tamperlab 口径一致：["02:12","02:13"] 就是 132.0s → 133.0s
                stop = Math.max(stop, start + 500);
                if (durationMs != null && durationMs > 0) {
                    start = Math.min(start, durationMs);
                    stop = Math.min(stop, durationMs);
                }
                if (stop > start) {
                    events.add(new RawEvent(start, stop, type));
                }
            }
        }
        return events;
    }

    // ------------------------------------------------------------------ CSV
    private List<RawEvent> parseCsv(String text) {
        List<RawEvent> events = new ArrayList<>();
        for (String line : text.split("\\r?\\n")) {
            String row = line.trim();
            if (row.isEmpty() || row.startsWith("#")) {
                continue;
            }
            String[] cells = row.split("[,;\\t]");
            if (cells.length < 3) {
                continue;
            }
            String a = cells[0].trim();
            String b = cells[1].trim();
            String c = cells[2].trim();
            try {
                long start;
                long end;
                String type;
                if (isTime(a) && isTime(b)) {
                    start = parseTime(a);
                    end = parseTime(b);
                    type = normalizeType(c);
                } else if (isTime(b) && isTime(c)) {
                    type = normalizeType(a);
                    start = parseTime(b);
                    end = parseTime(c);
                } else {
                    continue;
                }
                if (end > start) {
                    events.add(new RawEvent(start, end, type));
                }
            } catch (RuntimeException e) {
                // 表头或脏行，跳过
            }
        }
        return events;
    }

    // ------------------------------------------------------------------ JSON
    private List<RawEvent> parseJson(String text) {
        List<RawEvent> events = new ArrayList<>();
        try {
            JsonNode root = objectMapper.readTree(text);
            JsonNode array = root.isArray() ? root : root.path("events");
            if (!array.isArray()) {
                throw new BusinessException("JSON 里没有 events 数组");
            }
            for (JsonNode node : array) {
                long start = node.has("startMs")
                        ? node.path("startMs").asLong()
                        : Math.round(node.path("start").asDouble() * 1000);
                long end = node.has("endMs")
                        ? node.path("endMs").asLong()
                        : Math.round(node.path("end").asDouble() * 1000);
                if (end > start) {
                    events.add(new RawEvent(start, end,
                            normalizeType(node.path("type").asText(""))));
                }
            }
        } catch (BusinessException e) {
            throw e;
        } catch (Exception e) {
            throw new BusinessException("JSON 解析失败：" + e.getMessage());
        }
        return events;
    }

    // ------------------------------------------------------------------ 工具
    private static String stem(String name) {
        if (name == null) {
            return "";
        }
        int dot = name.lastIndexOf('.');
        return (dot > 0 ? name.substring(0, dot) : name).trim();
    }

    private static String normalizeType(String raw) {
        String key = raw == null ? "" : raw.trim().toLowerCase(Locale.ROOT);
        return TYPE_ALIAS.getOrDefault(key, key.isEmpty() ? "unknown" : key);
    }

    private static boolean isTime(String value) {
        if (value == null || value.isBlank()) {
            return false;
        }
        if (value.matches("^\\d{1,3}(:\\d{1,2}){1,2}(\\.\\d+)?$")) {
            return true;
        }
        return value.matches("^\\d+(\\.\\d+)?$");
    }

    /** "02:12" / "1:02:03.5" / "12.5"（秒） → 毫秒 */
    static long parseTime(String value) {
        String text = value.trim();
        if (text.contains(":")) {
            String[] parts = text.split(":");
            double seconds = 0;
            for (String part : parts) {
                seconds = seconds * 60 + Double.parseDouble(part);
            }
            return Math.round(seconds * 1000);
        }
        return Math.round(Double.parseDouble(text) * 1000);
    }
}
