package com.demo.tamper.controller;

import com.demo.tamper.common.ApiResponse;
import com.demo.tamper.model.dto.AnalysisDto;
import com.demo.tamper.model.dto.CompareDto;
import com.demo.tamper.service.AnalysisService;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.util.Map;
import org.springframework.http.ContentDisposition;
import org.springframework.http.HttpHeaders;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.multipart.MultipartFile;

@RestController
@RequestMapping("/api/detection")
public class AnalysisController {

    private final AnalysisService analysisService;

    public AnalysisController(AnalysisService analysisService) {
        this.analysisService = analysisService;
    }

    /** 结果分析：结论、事件时间线、统计、一致性、标注指标。 */
    @GetMapping("/{taskId}/analysis")
    public ApiResponse<AnalysisDto> analysis(
            @PathVariable String taskId,
            @RequestParam(value = "tolMs", required = false) Long tolMs) {
        long tolerance = tolMs == null || tolMs < 0 ? AnalysisService.DEFAULT_TOLERANCE_MS : tolMs;
        return ApiResponse.ok(analysisService.analyze(taskId, tolerance));
    }

    /** 多算法对比指标。 */
    @GetMapping("/{taskId}/compare")
    public ApiResponse<CompareDto> compare(@PathVariable String taskId) {
        return ApiResponse.ok(analysisService.compare(taskId));
    }

    /** 上传 ground-truth CSV（frame_index,tampered），返回解析条数。 */
    @PostMapping("/{taskId}/ground-truth")
    public ApiResponse<Map<String, Object>> uploadGroundTruth(@PathVariable String taskId,
                                                              @RequestParam("file") MultipartFile file)
            throws IOException {
        String content = new String(file.getBytes(), StandardCharsets.UTF_8);
        return ApiResponse.ok(analysisService.importGroundTruth(taskId, content));
    }

    /** 导出逐帧结果，format=csv|json。 */
    @GetMapping("/{taskId}/export")
    public ResponseEntity<byte[]> export(@PathVariable String taskId,
                                         @RequestParam(defaultValue = "csv") String format) {
        boolean json = "json".equalsIgnoreCase(format);
        byte[] body = json
                ? analysisService.exportJson(taskId)
                : analysisService.exportCsv(taskId).getBytes(StandardCharsets.UTF_8);
        String filename = analysisService.downloadName(taskId, json ? ".json" : ".csv");
        HttpHeaders headers = new HttpHeaders();
        headers.setContentDisposition(ContentDisposition.attachment()
                .filename(filename, StandardCharsets.UTF_8)
                .build());
        headers.setContentType(json ? MediaType.APPLICATION_JSON : MediaType.parseMediaType("text/csv"));
        return new ResponseEntity<>(body, headers, org.springframework.http.HttpStatus.OK);
    }
}
