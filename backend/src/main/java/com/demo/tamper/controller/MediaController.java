package com.demo.tamper.controller;

import com.demo.tamper.common.ApiResponse;
import com.demo.tamper.model.dto.MediaDto;
import com.demo.tamper.model.entity.GroundTruthEvent;
import com.demo.tamper.model.entity.MediaFile;
import com.demo.tamper.service.GroundTruthService;
import com.demo.tamper.service.MediaService;
import java.io.IOException;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import org.springframework.core.io.Resource;
import org.springframework.core.io.support.ResourceRegion;
import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpRange;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestPart;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.multipart.MultipartFile;

@RestController
@RequestMapping("/api/media")
public class MediaController {

    private final MediaService mediaService;
    private final GroundTruthService groundTruthService;

    public MediaController(MediaService mediaService, GroundTruthService groundTruthService) {
        this.mediaService = mediaService;
        this.groundTruthService = groundTruthService;
    }

    @PostMapping("/upload")
    public ApiResponse<MediaDto> upload(@RequestPart("file") MultipartFile file,
                                        @RequestPart(value = "reference", required = false) MultipartFile reference) {
        return ApiResponse.ok(mediaService.upload(file, reference));
    }

    @GetMapping
    public ApiResponse<List<MediaDto>> list() {
        return ApiResponse.ok(mediaService.list());
    }

    @GetMapping("/{id}")
    public ApiResponse<MediaDto> get(@PathVariable String id) {
        return ApiResponse.ok(mediaService.get(id));
    }

    /**
     * 原始媒体文件，支持 Range 请求，浏览器才能拖动视频进度条。
     */
    @GetMapping("/{id}/file")
    public ResponseEntity<?> file(@PathVariable String id,
                                  @RequestHeader(value = HttpHeaders.RANGE, required = false) String range)
            throws IOException {
        MediaFile media = mediaService.getEntity(id);
        Resource resource = mediaService.loadFile(id);
        return serve(resource, contentTypeOf(media), range);
    }

    @DeleteMapping("/{id}")
    public ApiResponse<Void> delete(@PathVariable String id) {
        mediaService.delete(id);
        return ApiResponse.ok();
    }

    static ResponseEntity<?> serve(Resource resource, MediaType mediaType, String rangeHeader)
            throws IOException {
        long length = resource.contentLength();
        if (rangeHeader == null || rangeHeader.isBlank()) {
            return ResponseEntity.ok()
                    .contentType(mediaType)
                    .header(HttpHeaders.ACCEPT_RANGES, "bytes")
                    .contentLength(length)
                    .body(resource);
        }
        HttpRange range = HttpRange.parseRanges(rangeHeader).get(0);
        long start = range.getRangeStart(length);
        long end = range.getRangeEnd(length);
        long count = Math.min(end - start + 1, length - start);
        ResourceRegion region = new ResourceRegion(resource, start, Math.max(count, 1));
        return ResponseEntity.status(HttpStatus.PARTIAL_CONTENT)
                .contentType(mediaType)
                .header(HttpHeaders.ACCEPT_RANGES, "bytes")
                .header(HttpHeaders.CONTENT_RANGE, "bytes " + start + "-" + (start + count - 1) + "/" + length)
                .body(region);
    }

    static MediaType contentTypeOf(MediaFile media) {
        String contentType = media.getContentType();
        if (contentType != null && !contentType.isBlank()) {
            try {
                return MediaType.parseMediaType(contentType);
            } catch (RuntimeException ignored) {
                // 落到按扩展名推断
            }
        }
        String name = media.getOriginalName().toLowerCase(Locale.ROOT);
        if (name.endsWith(".mp4")) {
            return MediaType.parseMediaType("video/mp4");
        }
        if (name.endsWith(".webm")) {
            return MediaType.parseMediaType("video/webm");
        }
        if (name.endsWith(".mov")) {
            return MediaType.parseMediaType("video/quicktime");
        }
        if (name.endsWith(".png")) {
            return MediaType.IMAGE_PNG;
        }
        return MediaType.IMAGE_JPEG;
    }

    // ------------------------------------------------------------------ 事件级标注

    /**
     * 导入事件级标注：FLAD 的 baseline.py、CSV（start,end,type）或 JSON（events 数组）。
     *
     * <p>标注挂在媒体上而不是任务上——检测前要先能列出标注区间，"跳到标注"才有的选。</p>
     */
    @PostMapping("/{id}/ground-truth")
    public ApiResponse<Map<String, Object>> importGroundTruth(
            @PathVariable String id,
            @RequestPart("file") MultipartFile file) throws IOException {
        MediaFile media = mediaService.getEntity(id);
        GroundTruthService.ImportResult result =
                groundTruthService.importFile(media, file.getOriginalFilename(), file.getBytes());
        return ApiResponse.ok(Map.of(
                "count", result.count(),
                "source", result.source(),
                "typeCounts", result.typeCounts(),
                "note", result.note()));
    }

    @GetMapping("/{id}/ground-truth")
    public ApiResponse<Map<String, Object>> groundTruth(@PathVariable String id) {
        List<GroundTruthEvent> events = groundTruthService.list(id);
        List<Map<String, Object>> items = events.stream()
                .map(e -> Map.<String, Object>of(
                        "startMs", e.getStartMs(),
                        "endMs", e.getEndMs(),
                        "tamperType", e.getTamperType(),
                        "source", e.getSource() == null ? "" : e.getSource()))
                .toList();
        return ApiResponse.ok(Map.of("count", items.size(), "events", items));
    }

    @DeleteMapping("/{id}/ground-truth")
    public ApiResponse<Void> clearGroundTruth(@PathVariable String id) {
        groundTruthService.clear(id);
        return ApiResponse.ok();
    }
}
