package com.demo.tamper.model.entity;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.EnumType;
import jakarta.persistence.Enumerated;
import jakarta.persistence.Id;
import jakarta.persistence.Lob;
import jakarta.persistence.Table;
import java.time.Instant;
import lombok.Getter;
import lombok.Setter;

/**
 * 一次检测任务（一个媒体 + 一组算法 + 一组参数）。
 */
@Entity
@Getter
@Setter
@Table(name = "detection_task")
public class DetectionTask {

    @Id
    @Column(length = 36)
    private String id;

    @Column(nullable = false, length = 36)
    private String mediaId;

    /** 逗号分隔的算法标识，便于列表页直接展示 */
    @Column(nullable = false, length = 500)
    private String algorithms;

    @Lob
    @Column(name = "params_json")
    private String paramsJson;

    @Enumerated(EnumType.STRING)
    @Column(nullable = false, length = 16)
    private TaskStatus status = TaskStatus.PENDING;

    private Double progress = 0.0;

    @Column(length = 300)
    private String message;

    @Column(length = 2000)
    private String errorMessage;

    /** 算法层异步任务 id（视频场景） */
    @Column(length = 64)
    private String algoTaskId;

    private Integer frameCount;
    private Integer tamperedFrameCount;
    private Long elapsedMs;

    /** 各算法平均单帧耗时（毫秒），JSON: {"laplacian": 3.2, ...} */
    @Lob
    @Column(name = "timings_json")
    private String timingsJson;

    /** 分段检测：本次任务覆盖的时间窗（绝对毫秒），整段检测时为空 */
    private Long startMs;
    private Long endMs;

    /** 分段数（0 表示整段检测） */
    private Integer segmentCount;

    /** 逐段汇总，JSON 数组：label / startMs / endMs / frameCount / tamperedFrames / peakScore */
    @Lob
    @Column(name = "segments_json")
    private String segmentsJson;

    @Column(nullable = false)
    private Instant createdAt;

    private Instant startedAt;
    private Instant finishedAt;
}
