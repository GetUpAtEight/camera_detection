package com.demo.tamper.model.dto;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotEmpty;
import java.util.List;
import java.util.Map;

/**
 * 提交检测任务的请求体。
 *
 * <pre>
 * // 整段检测
 * { "mediaId": "xxx", "algorithms": ["laplacian", "entropy"], "sampleFps": 5 }
 *
 * // 区间检测（只跑这一段）
 * { "mediaId": "xxx", "algorithms": ["entropy"], "startMs": 12000, "endMs": 24000 }
 *
 * // 分段扫描（按标注分段，一次跑多段）
 * { "mediaId": "xxx", "algorithms": ["entropy"],
 *   "segments": [ {"label": "遮挡 0:22-0:23", "type": "covered", "startMs": 22000, "endMs": 23000} ] }
 * </pre>
 *
 * <p>{@code segments} 非空时优先于 {@code startMs/endMs}；{@code refGapMs} 是段前参考帧的
 * 前置余量（默认 1000ms），用于给每个分段一面干净的"镜子"。</p>
 */
public record DetectionRunRequest(
        @NotBlank(message = "mediaId 不能为空") String mediaId,
        @NotEmpty(message = "至少选择一个算法") List<String> algorithms,
        Map<String, Map<String, Object>> params,
        Double sampleFps,
        Integer maxFrames,
        Long startMs,
        Long endMs,
        List<SegmentSpec> segments,
        Integer refGapMs) {

    /** 兼容旧调用：不带区间。 */
    public DetectionRunRequest(String mediaId, List<String> algorithms,
                               Map<String, Map<String, Object>> params,
                               Double sampleFps, Integer maxFrames) {
        this(mediaId, algorithms, params, sampleFps, maxFrames, null, null, List.of(), null);
    }

    public boolean hasSegments() {
        return segments != null && !segments.isEmpty();
    }

    public boolean hasWindow() {
        return startMs != null && endMs != null && endMs > startMs;
    }
}
