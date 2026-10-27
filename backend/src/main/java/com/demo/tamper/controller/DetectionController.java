package com.demo.tamper.controller;

import com.demo.tamper.common.ApiResponse;
import com.demo.tamper.common.BusinessException;
import com.demo.tamper.model.dto.DetectionRunRequest;
import com.demo.tamper.model.dto.DetectionTaskDto;
import com.demo.tamper.model.entity.FrameResult;
import com.demo.tamper.repository.FrameResultRepository;
import com.demo.tamper.service.DetectionService;
import com.demo.tamper.service.MediaService;
import jakarta.validation.Valid;
import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;
import java.util.Map;
import org.springframework.core.io.FileSystemResource;
import org.springframework.core.io.Resource;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/api/detection")
public class DetectionController {

    private final DetectionService detectionService;
    private final MediaService mediaService;
    private final FrameResultRepository frameRepository;

    public DetectionController(DetectionService detectionService,
                               MediaService mediaService,
                               FrameResultRepository frameRepository) {
        this.detectionService = detectionService;
        this.mediaService = mediaService;
        this.frameRepository = frameRepository;
    }

    /** 提交检测任务，立即返回 taskId，前端轮询进度。 */
    @PostMapping("/run")
    public ApiResponse<DetectionTaskDto> run(@Valid @RequestBody DetectionRunRequest request) {
        return ApiResponse.ok(detectionService.submit(request));
    }

    @GetMapping
    public ApiResponse<List<DetectionTaskDto>> list() {
        return ApiResponse.ok(detectionService.list());
    }

    @GetMapping("/{taskId}")
    public ApiResponse<DetectionTaskDto> get(@PathVariable String taskId) {
        return ApiResponse.ok(detectionService.get(taskId));
    }

    /** 分页获取逐帧结果（避免一次性传输过大 JSON）。 */
    @GetMapping("/{taskId}/frames")
    public ApiResponse<Map<String, Object>> frames(@PathVariable String taskId,
                                                   @RequestParam(defaultValue = "0") int page,
                                                   @RequestParam(defaultValue = "500") int size,
                                                   @RequestParam(required = false) String algorithm) {
        return ApiResponse.ok(detectionService.frames(taskId, algorithm, page, size));
    }

    /** 中间过程可视化图（边缘图 / 匹配图 / 热力图 / 检测框）。 */
    @GetMapping("/{taskId}/visual/{algorithm}/{frameIndex}")
    public ResponseEntity<?> visual(@PathVariable String taskId,
                                    @PathVariable String algorithm,
                                    @PathVariable int frameIndex) throws IOException {
        List<FrameResult> rows = frameRepository.findOneResult(taskId, algorithm, frameIndex);
        String visualFile = rows.stream()
                .map(FrameResult::getVisualFile)
                .filter(name -> name != null && !name.isBlank())
                .findFirst()
                .orElseThrow(() -> BusinessException.notFound("该帧没有可视化图"));
        Path path = mediaService.getVisualDir().resolve(taskId).resolve(visualFile);
        if (!Files.exists(path)) {
            throw BusinessException.notFound("可视化图文件不存在");
        }
        Resource resource = new FileSystemResource(path);
        return ResponseEntity.ok()
                .contentType(MediaType.IMAGE_JPEG)
                .header("Cache-Control", "public, max-age=86400")
                .body(resource);
    }

    @PostMapping("/{taskId}/cancel")
    public ApiResponse<Void> cancel(@PathVariable String taskId) {
        detectionService.cancel(taskId);
        return ApiResponse.ok();
    }

    @DeleteMapping("/{taskId}")
    public ApiResponse<Void> delete(@PathVariable String taskId) {
        detectionService.delete(taskId);
        return ApiResponse.ok();
    }
}
