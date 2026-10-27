package com.demo.tamper.model.entity;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.GeneratedValue;
import jakarta.persistence.GenerationType;
import jakarta.persistence.Id;
import jakarta.persistence.Index;
import jakarta.persistence.Table;
import java.time.Instant;
import lombok.Getter;
import lombok.Setter;

/**
 * 事件级标注（人工标注的篡改区间）。
 *
 * <p>为什么不用 {@link GroundTruthFrame}：FLAD 的标注就是"某段 1~2 秒的区间"，
 * 按帧存会把 78 个区间摊成几万行，而且丢失了"这是一个事件"的语义。
 * 事件级标注配合 ±容差匹配，才是这类数据该用的评价口径。</p>
 */
@Entity
@Getter
@Setter
@Table(
        name = "ground_truth_event",
        indexes = {@Index(name = "idx_gte_media", columnList = "media_id,start_ms")})
public class GroundTruthEvent {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @Column(name = "media_id", nullable = false, length = 36)
    private String mediaId;

    @Column(name = "start_ms", nullable = false)
    private Long startMs;

    @Column(name = "end_ms", nullable = false)
    private Long endMs;

    /** covered / defocused / moved / unknown */
    @Column(length = 32)
    private String tamperType;

    /** 标注来源：baseline.py / csv / json */
    @Column(length = 200)
    private String source;

    @Column(nullable = false)
    private Instant createdAt = Instant.now();
}
