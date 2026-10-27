package com.demo.tamper.model.dto;

import com.demo.tamper.model.entity.MediaFile;
import com.demo.tamper.model.entity.MediaKind;
import java.time.Instant;

public record MediaDto(
        String id,
        String originalName,
        MediaKind mediaType,
        Integer width,
        Integer height,
        Double fps,
        Long durationMs,
        Integer frameCount,
        Long fileSize,
        String contentType,
        boolean hasReference,
        Instant createdAt) {

    public static MediaDto from(MediaFile media) {
        return new MediaDto(
                media.getId(),
                media.getOriginalName(),
                media.getMediaType(),
                media.getWidth(),
                media.getHeight(),
                media.getFps(),
                media.getDurationMs(),
                media.getFrameCount(),
                media.getFileSize(),
                media.getContentType(),
                media.getReferencePath() != null,
                media.getCreatedAt());
    }
}
