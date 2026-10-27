package com.demo.tamper.controller;

import com.demo.tamper.common.ApiResponse;
import com.demo.tamper.common.BusinessException;
import com.demo.tamper.model.dto.LiveDto;
import com.demo.tamper.service.LiveService;
import java.nio.file.Files;
import java.nio.file.Path;
import org.springframework.core.io.FileSystemResource;
import org.springframework.core.io.Resource;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PatchMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.multipart.MultipartFile;

/**
 * 实时监控：本机摄像头按间隔抓帧 → 单帧检测 → 连续告警。
 *
 * <p>浏览器负责采集（getUserMedia），后端负责"参考帧 + 算法调用 + 去抖"，
 * 这样摄像头不需要装在算法层那台服务器上。
 */
@RestController
@RequestMapping("/api/live")
public class LiveController {

    private final LiveService liveService;

    public LiveController(LiveService liveService) {
        this.liveService = liveService;
    }

    /** 开始监控：首帧作为参考基准。 */
    @PostMapping("/sessions")
    public ApiResponse<LiveDto.SessionInfo> create(
            @RequestParam("reference") MultipartFile reference,
            @RequestParam(defaultValue = "[\"laplacian\",\"entropy\",\"scene_diff\",\"orb\",\"edge\"]")
            String algorithms,
            @RequestParam(defaultValue = "{}") String params,
            @RequestParam(required = false) Integer requiredStreak) {
        return ApiResponse.ok(liveService.create(reference, algorithms, params, requiredStreak));
    }

    /** 提交一帧，同步返回检测结果。probe=true 时只采样不判定（学习期用）。 */
    @PostMapping("/sessions/{sessionId}/frame")
    public ApiResponse<LiveDto.FrameResult> frame(@PathVariable String sessionId,
                                                  @RequestParam("frame") MultipartFile frame,
                                                  @RequestParam(defaultValue = "false") boolean probe) {
        return ApiResponse.ok(liveService.detect(sessionId, frame, probe));
    }

    /** 画面恢复正常后，把当前帧设为新基准（同时清零连续告警计数）。 */
    @PostMapping("/sessions/{sessionId}/reference")
    public ApiResponse<LiveDto.SessionInfo> resetReference(@PathVariable String sessionId,
                                                           @RequestParam("reference") MultipartFile reference) {
        return ApiResponse.ok(liveService.resetReference(sessionId, reference));
    }

    /** 学习期结束后回填阈值：会话运行中更新检测参数。 */
    @PatchMapping("/sessions/{sessionId}/params")
    public ApiResponse<LiveDto.SessionInfo> updateParams(@PathVariable String sessionId,
                                                         @RequestParam("params") String params) {
        return ApiResponse.ok(liveService.updateParams(sessionId, params));
    }

    @GetMapping("/sessions/{sessionId}")
    public ApiResponse<LiveDto.SessionInfo> info(@PathVariable String sessionId) {
        return ApiResponse.ok(liveService.info(sessionId));
    }

    /** 结束会话。默认保留告警帧（页面历史记录还要看图），传 purge=true 连图一起删。 */
    @DeleteMapping("/sessions/{sessionId}")
    public ApiResponse<Void> close(@PathVariable String sessionId,
                                   @RequestParam(defaultValue = "false") boolean purge) {
        liveService.close(sessionId, purge);
        return ApiResponse.ok();
    }

    /** 参考帧缩略图。 */
    @GetMapping("/sessions/{sessionId}/reference")
    public ResponseEntity<Resource> reference(@PathVariable String sessionId) {
        return image(liveService.referenceImage(sessionId));
    }

    /** 告警帧（保留现场，最多 30 张）。 */
    @GetMapping("/sessions/{sessionId}/alarms/{frameIndex}")
    public ResponseEntity<Resource> alarm(@PathVariable String sessionId,
                                          @PathVariable int frameIndex) {
        return image(liveService.alarmImage(sessionId, frameIndex));
    }

    private ResponseEntity<Resource> image(Path path) {
        if (!Files.exists(path)) {
            throw BusinessException.notFound("图片不存在");
        }
        return ResponseEntity.ok()
                .contentType(MediaType.IMAGE_JPEG)
                .header("Cache-Control", "no-store")
                .body(new FileSystemResource(path));
    }
}
