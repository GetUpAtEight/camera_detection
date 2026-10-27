package com.demo.tamper.model.entity;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.GeneratedValue;
import jakarta.persistence.GenerationType;
import jakarta.persistence.Id;
import jakarta.persistence.Index;
import jakarta.persistence.Lob;
import jakarta.persistence.Table;
import lombok.Getter;
import lombok.Setter;

/**
 * 逐帧逐算法的检测结果。
 */
@Entity
@Getter
@Setter
@Table(
        name = "detection_result",
        indexes = {
            @Index(name = "idx_result_task", columnList = "task_id,algorithm,frame_index"),
            @Index(name = "idx_result_task_frame", columnList = "task_id,frame_index")
        })
public class FrameResult {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @Column(name = "task_id", nullable = false, length = 36)
    private String taskId;

    @Column(nullable = false, length = 32)
    private String algorithm;

    @Column(name = "frame_index", nullable = false)
    private Integer frameIndex;

    /** 分段检测时该帧属于第几段（从 0 起）；整段检测为 0 */
    private Integer segmentIndex;

    private Long timestampMs;

    private Double score;

    private Boolean tampered;

    @Column(length = 32)
    private String tamperType;

    private Double confidence;

    @Lob
    @Column(name = "detail_json")
    private String detailJson;

    /** 中间过程可视化文件名（相对 visual 目录） */
    @Column(length = 300)
    private String visualFile;

    /** 该算法在这一帧的说明（命中原因、跳过原因等） */
    @Column(length = 500)
    private String note;

    /** 该算法本帧是否被跳过（缺参考帧、匹配点不足等） */
    private Boolean skipped;
}
