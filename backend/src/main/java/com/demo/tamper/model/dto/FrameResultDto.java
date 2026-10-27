package com.demo.tamper.model.dto;

import com.demo.tamper.model.entity.FrameResult;
import com.fasterxml.jackson.databind.JsonNode;

public record FrameResultDto(
        String algorithm,
        Integer frameIndex,
        Integer segmentIndex,
        Long timestampMs,
        Double score,
        boolean tampered,
        String tamperType,
        Double confidence,
        JsonNode detail,
        String note,
        String visualUrl,
        boolean skipped) {

    public static FrameResultDto from(FrameResult entity, JsonNode detail, String visualUrl) {
        return new FrameResultDto(
                entity.getAlgorithm(),
                entity.getFrameIndex(),
                entity.getSegmentIndex(),
                entity.getTimestampMs(),
                entity.getScore(),
                Boolean.TRUE.equals(entity.getTampered()),
                entity.getTamperType(),
                entity.getConfidence(),
                detail,
                entity.getNote(),
                visualUrl,
                Boolean.TRUE.equals(entity.getSkipped()));
    }
}
