package com.demo.tamper.model.entity;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.GeneratedValue;
import jakarta.persistence.GenerationType;
import jakarta.persistence.Id;
import jakarta.persistence.Index;
import jakarta.persistence.Table;
import lombok.Getter;
import lombok.Setter;

/**
 * 人工标注（ground truth）：某任务某帧是否真的被篡改，用于算 Precision/Recall/F1。
 */
@Entity
@Getter
@Setter
@Table(
        name = "ground_truth_frame",
        indexes = {@Index(name = "idx_gt_task", columnList = "task_id,frame_index")})
public class GroundTruthFrame {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @Column(name = "task_id", nullable = false, length = 36)
    private String taskId;

    @Column(name = "frame_index", nullable = false)
    private Integer frameIndex;

    @Column(nullable = false)
    private Boolean tampered;

    public GroundTruthFrame() {
    }

    public GroundTruthFrame(String taskId, Integer frameIndex, Boolean tampered) {
        this.taskId = taskId;
        this.frameIndex = frameIndex;
        this.tampered = tampered;
    }
}
