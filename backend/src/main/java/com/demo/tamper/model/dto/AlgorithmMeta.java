package com.demo.tamper.model.dto;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import com.fasterxml.jackson.annotation.JsonProperty;
import java.util.List;

/**
 * 算法层 /algorithms 返回的元数据，前端据此渲染算法卡片与动态参数表单。
 */
@JsonIgnoreProperties(ignoreUnknown = true)
public record AlgorithmMeta(
        String name,
        String displayName,
        String description,
        List<String> tamperTypes,
        boolean requiresReference,
        String kind,
        boolean available,
        Double thresholdScore,
        List<ParamMeta> params,
        PrimaryMetric primaryMetric) {

    /**
     * 自动标定用的主指标：从 detail 取哪个字段、方向（higher/lower）、写回哪个参数。
     * 实时监控的「学习场景基线」按它算分位数并回填阈值。
     */
    @JsonIgnoreProperties(ignoreUnknown = true)
    public record PrimaryMetric(String field, String direction, String param) {
    }

    @JsonIgnoreProperties(ignoreUnknown = true)
    public record ParamMeta(
            String key,
            String label,
            @JsonProperty("default") Double defaultValue,
            Double min,
            Double max,
            Double step,
            String unit,
            String type,
            String description) {
    }
}
