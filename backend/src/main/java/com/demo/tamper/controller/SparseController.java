package com.demo.tamper.controller;

import com.demo.tamper.common.ApiResponse;
import com.demo.tamper.service.SparseService;
import java.util.List;
import java.util.Map;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.multipart.MultipartFile;

/**
 * 低频稀疏采样篡改检测。
 *
 * <p>场景：受成本约束不能实时拉流，每 1 小时（甚至 1 天）才拉一次帧；
 * 每次可以只拉 1 张，也可以连拉 3~5 帧作为一个"微序列"。
 *
 * <p>判定分三层：
 *
 * <ul>
 *   <li><b>L0 长周期</b>：与同一机位的历史基线（按小时分槽的中位数背景）比；
 *   <li><b>L1 微时序</b>：单次抽样内部几帧之间的一致性（冻结/重放、正在被转动、样本质量）；
 *   <li><b>L2 单帧</b>：画质退化（遮挡/失焦）与物理一致性。
 * </ul>
 */
@RestController
@RequestMapping("/api/sparse")
public class SparseController {

    private final SparseService sparseService;

    public SparseController(SparseService sparseService) {
        this.sparseService = sparseService;
    }

    /** 参数元数据 + 判决字典（前端据此渲染表单与图例）。 */
    @GetMapping("/meta")
    public ApiResponse<Map<String, Object>> meta() {
        return ApiResponse.ok(sparseService.meta());
    }

    /**
     * 提交一次抽样。
     *
     * @param frames 1~K 帧；K 越大，微时序层能给出的证据越多（但超过 5 帧收益很小）
     */
    @PostMapping(value = "/sample", consumes = MediaType.MULTIPART_FORM_DATA_VALUE)
    public ApiResponse<Map<String, Object>> sample(
            @RequestParam("cameraId") String cameraId,
            @RequestParam("frames") List<MultipartFile> frames,
            @RequestParam(defaultValue = "0") long tsMs,
            @RequestParam(defaultValue = "") String name,
            @RequestParam(defaultValue = "false") boolean reset,
            @RequestParam(defaultValue = "{}") String params) {
        return ApiResponse.ok(sparseService.sample(cameraId, frames, tsMs, name, reset, params));
    }

    @GetMapping("/cameras")
    public ApiResponse<Map<String, Object>> cameras() {
        return ApiResponse.ok(sparseService.cameras());
    }

    @GetMapping("/state/{cameraId}")
    public ApiResponse<Map<String, Object>> state(@PathVariable String cameraId) {
        return ApiResponse.ok(sparseService.state(cameraId));
    }

    /** 重设基准：清空该机位的基线库（换机位/大改光照后使用）。 */
    @PostMapping("/state/{cameraId}/reset")
    public ApiResponse<Map<String, Object>> reset(@PathVariable String cameraId) {
        return ApiResponse.ok(sparseService.reset(cameraId));
    }

    @DeleteMapping("/state/{cameraId}")
    public ApiResponse<Map<String, Object>> delete(@PathVariable String cameraId) {
        return ApiResponse.ok(sparseService.delete(cameraId));
    }

    /** 基线库缩略图。 */
    @GetMapping("/thumb/{cameraId}/{slot}/{index}")
    public ResponseEntity<byte[]> thumb(@PathVariable String cameraId,
                                        @PathVariable int slot,
                                        @PathVariable int index) {
        byte[] bytes = sparseService.thumb(cameraId, slot, index);
        return ResponseEntity.ok()
                .contentType(MediaType.IMAGE_PNG)
                .header("Cache-Control", "no-store")
                .body(bytes);
    }

    /**
     * 标定参照物。
     *
     * @param landmarks  归一化矩形数组 JSON：[{x,y,w,h,label,kind}]，坐标 ∈ [0,1]
     * @param file       可选：标定画面（不传则用该机位基线中位数背景）
     * @param clear      true 表示清空已有参照物
     * @param admitFrame true（默认）表示把这张标定画面同时作为基线样本入库
     *                   （算法层会先过质量闸：熵、清晰度、亮度、与已有基线的一致性）
     */
    @PostMapping(value = "/landmarks/{cameraId}", consumes = MediaType.MULTIPART_FORM_DATA_VALUE)
    public ApiResponse<Map<String, Object>> setLandmarks(
            @PathVariable String cameraId,
            @RequestParam(defaultValue = "[]") String landmarks,
            @RequestParam(required = false) MultipartFile file,
            @RequestParam(defaultValue = "false") boolean clear,
            @RequestParam(defaultValue = "true") boolean admitFrame) {
        return ApiResponse.ok(sparseService.setLandmarks(cameraId, landmarks, file, clear, admitFrame));
    }

    @GetMapping("/landmarks/{cameraId}")
    public ApiResponse<Map<String, Object>> getLandmarks(@PathVariable String cameraId) {
        return ApiResponse.ok(sparseService.getLandmarks(cameraId));
    }

    @DeleteMapping("/landmarks/{cameraId}")
    public ApiResponse<Map<String, Object>> clearLandmarks(@PathVariable String cameraId) {
        return ApiResponse.ok(sparseService.clearLandmarks(cameraId));
    }

    // ---------------------------------------------------------------- 模板库
    /** 列出所有参照物的模板与最近审计记录。 */
    @GetMapping("/landmarks/{cameraId}/templates")
    public ApiResponse<Map<String, Object>> listTemplates(@PathVariable String cameraId) {
        return ApiResponse.ok(sparseService.listTemplates(cameraId));
    }

    /**
     * 给参照物追加一套外观模板（夜间 / 雨天 / 换季 / 误报反馈）。
     *
     * @param landmarkId     参照物 id
     * @param file           模板画面
     * @param label          名称，如"夜间版""雨天版"
     * @param source         manual / import / false_alarm / night / rain
     * @param actor          操作人（写审计）
     * @param verdictAtTime  添加时系统的判定结论（写审计，便于回溯）
     * @param force          质量闸不通过时强制添加（会被审计标记）
     */
    @PostMapping(value = "/landmarks/{cameraId}/templates", consumes = MediaType.MULTIPART_FORM_DATA_VALUE)
    public ApiResponse<Map<String, Object>> addTemplate(
            @PathVariable String cameraId,
            @RequestParam int landmarkId,
            @RequestParam MultipartFile file,
            @RequestParam(defaultValue = "") String label,
            @RequestParam(defaultValue = "manual") String source,
            @RequestParam(defaultValue = "") String actor,
            @RequestParam(defaultValue = "") String verdictAtTime,
            @RequestParam(defaultValue = "false") boolean force) {
        return ApiResponse.ok(sparseService.addTemplate(cameraId, landmarkId, file, label,
                source, actor, verdictAtTime, force));
    }

    @DeleteMapping("/landmarks/{cameraId}/templates/{templateId}")
    public ApiResponse<Map<String, Object>> removeTemplate(@PathVariable String cameraId,
                                                           @PathVariable String templateId,
                                                           @RequestParam(defaultValue = "") String actor) {
        return ApiResponse.ok(sparseService.removeTemplate(cameraId, templateId, actor));
    }

    /** 模板预览（梯度幅值渲染成 PNG）。 */
    @GetMapping("/templates/{cameraId}/{templateId}/preview")
    public ResponseEntity<byte[]> templatePreview(@PathVariable String cameraId,
                                                  @PathVariable String templateId) {
        byte[] bytes = sparseService.templatePreview(cameraId, templateId);
        return ResponseEntity.ok()
                .contentType(MediaType.IMAGE_PNG)
                .header("Cache-Control", "no-store")
                .body(bytes);
    }
}
