package com.demo.tamper.model.entity;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.EnumType;
import jakarta.persistence.Enumerated;
import jakarta.persistence.Id;
import jakarta.persistence.Table;
import java.time.Instant;
import lombok.Getter;
import lombok.Setter;

/**
 * 上传的媒体文件（图片 / 视频）。
 */
@Entity
@Getter
@Setter
@Table(name = "media_task")
public class MediaFile {

    @Id
    @Column(length = 36)
    private String id;

    @Column(nullable = false, length = 300)
    private String originalName;

    @Column(nullable = false, length = 1000)
    private String storedPath;

    /** 可选：图片检测时的参考帧文件路径 */
    @Column(length = 1000)
    private String referencePath;

    @Enumerated(EnumType.STRING)
    @Column(nullable = false, length = 16)
    private MediaKind mediaType;

    private Integer width;
    private Integer height;
    private Double fps;
    private Long durationMs;
    private Integer frameCount;
    private Long fileSize;

    @Column(length = 100)
    private String contentType;

    @Column(nullable = false)
    private Instant createdAt;
}
