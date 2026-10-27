package com.demo.tamper.model.dto;

import com.demo.tamper.model.entity.DetectionTask;
import com.demo.tamper.model.entity.TaskStatus;
import java.time.Instant;
import java.util.List;

public record DetectionTaskDto(
        String id,
        String mediaId,
        String mediaName,
        String mediaType,
        List<String> algorithms,
        TaskStatus status,
        double progress,
        String message,
        String errorMessage,
        Integer frameCount,
        Integer tamperedFrameCount,
        Long elapsedMs,
        Long startMs,
        Long endMs,
        Integer segmentCount,
        Instant createdAt,
        Instant startedAt,
        Instant finishedAt) {

    public static DetectionTaskDto from(DetectionTask task, String mediaName, String mediaType) {
        List<String> algorithms = task.getAlgorithms() == null || task.getAlgorithms().isBlank()
                ? List.of()
                : List.of(task.getAlgorithms().split(","));
        return new DetectionTaskDto(
                task.getId(),
                task.getMediaId(),
                mediaName,
                mediaType,
                algorithms,
                task.getStatus(),
                task.getProgress() == null ? 0.0 : task.getProgress(),
                task.getMessage(),
                task.getErrorMessage(),
                task.getFrameCount(),
                task.getTamperedFrameCount(),
                task.getElapsedMs(),
                task.getStartMs(),
                task.getEndMs(),
                task.getSegmentCount(),
                task.getCreatedAt(),
                task.getStartedAt(),
                task.getFinishedAt());
    }
}
