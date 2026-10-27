package com.demo.tamper.model.dto;

import java.util.List;
import lombok.Builder;
import lombok.Getter;
import lombok.Setter;

/**
 * 算法对比页数据：多算法指标、篡改类型分布、整体一致性。
 */
@Getter
@Setter
@Builder
public class CompareDto {

    private String taskId;
    private String mediaName;
    private String mediaType;
    private int frames;
    private double thresholdScore;
    private List<AnalysisDto.AlgorithmStat> stats;
    private List<TypeBreakdown> typeBreakdown;
    private List<AnalysisDto.ConsistencyPair> consistency;
    private double overallAgreement;
    private boolean hasGroundTruth;

    @Getter
    @Setter
    @Builder
    public static class TypeBreakdown {
        private String algorithm;
        private int normal;
        private int covered;
        private int defocused;
        private int moved;
    }
}
