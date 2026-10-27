package com.demo.tamper.service;

import com.demo.tamper.common.BusinessException;
import com.demo.tamper.config.AppProperties;
import com.demo.tamper.model.dto.MediaDto;
import com.demo.tamper.model.entity.DetectionTask;
import com.demo.tamper.model.entity.MediaFile;
import com.demo.tamper.model.entity.MediaKind;
import com.demo.tamper.repository.DetectionTaskRepository;
import com.demo.tamper.repository.FrameResultRepository;
import com.demo.tamper.repository.GroundTruthRepository;
import com.demo.tamper.repository.MediaFileRepository;
import jakarta.annotation.PostConstruct;
import java.io.IOException;
import java.io.InputStream;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import java.time.Instant;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.UUID;
import java.util.stream.Collectors;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.core.io.FileSystemResource;
import org.springframework.core.io.Resource;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.web.multipart.MultipartFile;

@Service
public class MediaService {

    private static final Logger log = LoggerFactory.getLogger(MediaService.class);
    private static final List<String> VIDEO_EXT =
            List.of(".mp4", ".avi", ".mov", ".mkv", ".flv", ".webm", ".m4v", ".mpeg", ".mpg");
    private static final List<String> IMAGE_EXT =
            List.of(".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tif", ".tiff");

    private final MediaFileRepository mediaRepository;
    private final DetectionTaskRepository taskRepository;
    private final FrameResultRepository frameRepository;
    private final GroundTruthRepository groundTruthRepository;
    private final AlgoGatewayService gateway;
    private final AppProperties properties;

    private Path uploadDir;
    private Path visualDir;

    public MediaService(MediaFileRepository mediaRepository,
                        DetectionTaskRepository taskRepository,
                        FrameResultRepository frameRepository,
                        GroundTruthRepository groundTruthRepository,
                        AlgoGatewayService gateway,
                        AppProperties properties) {
        this.mediaRepository = mediaRepository;
        this.taskRepository = taskRepository;
        this.frameRepository = frameRepository;
        this.groundTruthRepository = groundTruthRepository;
        this.gateway = gateway;
        this.properties = properties;
    }

    @PostConstruct
    void init() {
        try {
            uploadDir = Path.of(properties.getStorage().getUploadDir()).toAbsolutePath().normalize();
            visualDir = Path.of(properties.getStorage().getVisualDir()).toAbsolutePath().normalize();
            Files.createDirectories(uploadDir);
            Files.createDirectories(visualDir);
            log.info("媒体存储目录: {}", uploadDir);
        } catch (IOException e) {
            throw new IllegalStateException("无法创建存储目录", e);
        }
    }

    public Path getUploadDir() {
        return uploadDir;
    }

    public Path getVisualDir() {
        return visualDir;
    }

    // ------------------------------------------------------------------ 上传
    public MediaDto upload(MultipartFile file, MultipartFile reference) {
        if (file == null || file.isEmpty()) {
            throw new BusinessException("请选择要上传的文件");
        }
        long maxBytes = properties.getStorage().getMaxFileSizeMb() * 1024 * 1024;
        if (file.getSize() > maxBytes) {
            throw new BusinessException(413, "文件超过 " + properties.getStorage().getMaxFileSizeMb() + " MB 限制");
        }

        String originalName = file.getOriginalFilename() == null ? "unnamed" : file.getOriginalFilename();
        String ext = extensionOf(originalName);
        MediaKind kind = detectKind(ext, file.getContentType());
        if (kind == null) {
            throw new BusinessException("不支持的文件类型：" + ext + "（仅支持 jpg/png/bmp/webp 与 mp4/avi/mov/mkv/webm）");
        }

        String id = UUID.randomUUID().toString();
        Path target = uploadDir.resolve(id + ext);
        try (InputStream in = file.getInputStream()) {
            Files.copy(in, target, StandardCopyOption.REPLACE_EXISTING);
        } catch (IOException e) {
            throw new BusinessException("保存文件失败：" + e.getMessage());
        }

        MediaFile entity = new MediaFile();
        entity.setId(id);
        entity.setOriginalName(originalName);
        entity.setStoredPath(target.toString());
        entity.setMediaType(kind);
        entity.setFileSize(file.getSize());
        entity.setContentType(file.getContentType());
        entity.setCreatedAt(Instant.now());

        if (reference != null && !reference.isEmpty()) {
            if (kind != MediaKind.IMAGE) {
                throw new BusinessException("只有图片检测才需要单独上传参考帧");
            }
            String refExt = extensionOf(reference.getOriginalFilename());
            Path refTarget = uploadDir.resolve(id + "_ref" + (refExt.isEmpty() ? ".jpg" : refExt));
            try (InputStream in = reference.getInputStream()) {
                Files.copy(in, refTarget, StandardCopyOption.REPLACE_EXISTING);
            } catch (IOException e) {
                throw new BusinessException("保存参考帧失败：" + e.getMessage());
            }
            entity.setReferencePath(refTarget.toString());
        }

        probeAndFill(entity, target);
        mediaRepository.save(entity);
        return MediaDto.from(entity);
    }

    private void probeAndFill(MediaFile entity, Path path) {
        try {
            Map<String, Object> info = gateway.probe(path);
            entity.setWidth(asInt(info.get("width")));
            entity.setHeight(asInt(info.get("height")));
            entity.setFrameCount(asInt(info.get("frameCount")));
            entity.setDurationMs(asLong(info.get("durationMs")));
            Object fps = info.get("fps");
            if (fps instanceof Number number) {
                entity.setFps(number.doubleValue());
            }
        } catch (BusinessException e) {
            if (e.getStatus() >= 400 && e.getStatus() < 500) {
                // 算法层判定媒体文件本身无法解析：这是用户输入问题，必须拒绝，不能落库成"坏媒体"
                try {
                    Files.deleteIfExists(path);
                    if (entity.getReferencePath() != null) {
                        Files.deleteIfExists(Path.of(entity.getReferencePath()));
                    }
                } catch (IOException ignored) {
                    log.warn("清理无法解析的临时文件失败: {}", path);
                }
                throw new BusinessException(400, "文件无法解析为图片或视频，请确认格式是否损坏：" + e.getMessage());
            }
            // 算法层不可用（502）时仍然允许上传成功，元信息留空，检测时会给出明确错误
            log.warn("读取媒体元信息失败（算法层可能未启动）: {}", e.getMessage());
        } catch (RuntimeException e) {
            log.warn("读取媒体元信息失败: {}", e.getMessage());
        }
    }

    // ------------------------------------------------------------------ 查询
    public List<MediaDto> list() {
        return mediaRepository.findAllByOrderByCreatedAtDesc().stream()
                .map(MediaDto::from)
                .collect(Collectors.toList());
    }

    public MediaFile getEntity(String id) {
        return mediaRepository.findById(id)
                .orElseThrow(() -> BusinessException.notFound("媒体不存在：" + id));
    }

    public MediaDto get(String id) {
        return MediaDto.from(getEntity(id));
    }

    public Resource loadFile(String id) {
        MediaFile media = getEntity(id);
        Path path = Path.of(media.getStoredPath());
        if (!Files.exists(path)) {
            throw BusinessException.notFound("媒体文件已丢失：" + id);
        }
        return new FileSystemResource(path);
    }

    public Path getReferencePath(MediaFile media) {
        return media.getReferencePath() == null ? null : Path.of(media.getReferencePath());
    }

    // ------------------------------------------------------------------ 删除
    @Transactional
    public void delete(String id) {
        MediaFile media = getEntity(id);
        List<DetectionTask> tasks = taskRepository.findByMediaIdOrderByCreatedAtDesc(id);
        for (DetectionTask task : tasks) {
            frameRepository.deleteByTaskId(task.getId());
            groundTruthRepository.deleteByTaskId(task.getId());
            taskRepository.deleteById(task.getId());
            deleteVisualDir(task.getId());
        }
        try {
            Files.deleteIfExists(Path.of(media.getStoredPath()));
            if (media.getReferencePath() != null) {
                Files.deleteIfExists(Path.of(media.getReferencePath()));
            }
        } catch (IOException e) {
            log.warn("删除媒体文件失败 {}: {}", id, e.getMessage());
        }
        mediaRepository.delete(media);
    }

    public void deleteVisualDir(String taskId) {
        Path dir = visualDir.resolve(taskId);
        if (!Files.exists(dir)) {
            return;
        }
        try (var paths = Files.walk(dir)) {
            paths.sorted((a, b) -> b.getNameCount() - a.getNameCount()).forEach(p -> {
                try {
                    Files.deleteIfExists(p);
                } catch (IOException ignored) {
                    // 清理失败不影响主流程
                }
            });
        } catch (IOException e) {
            log.warn("清理可视化目录失败 {}: {}", taskId, e.getMessage());
        }
    }

    // ------------------------------------------------------------------ 工具
    private static String extensionOf(String name) {
        if (name == null) {
            return "";
        }
        int idx = name.lastIndexOf('.');
        return idx < 0 ? "" : name.substring(idx).toLowerCase(Locale.ROOT);
    }

    private static MediaKind detectKind(String ext, String contentType) {
        if (VIDEO_EXT.contains(ext) || (contentType != null && contentType.startsWith("video/"))) {
            return MediaKind.VIDEO;
        }
        if (IMAGE_EXT.contains(ext) || (contentType != null && contentType.startsWith("image/"))) {
            return MediaKind.IMAGE;
        }
        return null;
    }

    private static Integer asInt(Object value) {
        return value instanceof Number number ? number.intValue() : null;
    }

    private static Long asLong(Object value) {
        return value instanceof Number number ? number.longValue() : null;
    }
}
