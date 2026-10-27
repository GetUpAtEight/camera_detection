package com.demo.tamper.service;

import com.demo.tamper.common.BusinessException;
import com.demo.tamper.config.AppProperties;
import com.demo.tamper.model.dto.AlgorithmMeta;
import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.nio.file.Path;
import java.util.List;
import java.util.Map;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.core.ParameterizedTypeReference;
import org.springframework.core.io.FileSystemResource;
import org.springframework.http.HttpEntity;
import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpMethod;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.stereotype.Service;
import org.springframework.util.LinkedMultiValueMap;
import org.springframework.util.MultiValueMap;
import org.springframework.web.client.HttpStatusCodeException;
import org.springframework.web.client.ResourceAccessException;
import org.springframework.web.client.RestClientException;
import org.springframework.web.client.RestTemplate;

/**
 * 算法层网关：所有与 Python FastAPI 的交互都收敛在这里，便于统一超时、重试与错误映射。
 */
@Service
public class AlgoGatewayService {

    private static final Logger log = LoggerFactory.getLogger(AlgoGatewayService.class);
    private static final ParameterizedTypeReference<Map<String, Object>> MAP_TYPE =
            new ParameterizedTypeReference<>() {};

    private final RestTemplate restTemplate;
    private final ObjectMapper objectMapper;
    private final String baseUrl;

    public AlgoGatewayService(RestTemplate algoRestTemplate, ObjectMapper objectMapper,
                              AppProperties properties) {
        this.restTemplate = algoRestTemplate;
        this.objectMapper = objectMapper;
        String url = properties.getAlgo().getBaseUrl();
        this.baseUrl = url.endsWith("/") ? url.substring(0, url.length() - 1) : url;
    }

    public String getBaseUrl() {
        return baseUrl;
    }

    // ------------------------------------------------------------------ 元信息
    public Map<String, Object> health() {
        return get("/health");
    }

    public List<AlgorithmMeta> algorithms() {
        JsonNode node = getJson("/algorithms");
        JsonNode list = node.get("algorithms");
        if (list == null || !list.isArray()) {
            throw new BusinessException(502, "算法层 /algorithms 返回格式异常");
        }
        return objectMapper.convertValue(list, new TypeReference<List<AlgorithmMeta>>() {});
    }

    public Map<String, Object> probe(Path file) {
        MultiValueMap<String, Object> parts = new LinkedMultiValueMap<>();
        parts.add("file", toResource(file));
        return postMultipart("/media/probe", parts);
    }

    // ------------------------------------------------------------------ 检测
    public Map<String, Object> detectImage(Path image, Path reference, String algorithmsJson,
                                           String paramsJson, boolean visual) {
        return detectImage(image, reference, algorithmsJson, paramsJson, visual, null, false);
    }

    /**
     * 单图检测。
     *
     * @param sessionId 实时监控会话 id：算法层按它会复用同一批检测器实例，
     *                  否则 MOG2 / 短长期直方图 / 帧差这些依赖帧间状态的算法永远停在预热帧
     * @param reset     丢掉该会话已有的检测器状态（重设参考帧后调用）
     */
    public Map<String, Object> detectImage(Path image, Path reference, String algorithmsJson,
                                           String paramsJson, boolean visual,
                                           String sessionId, boolean reset) {
        MultiValueMap<String, Object> parts = new LinkedMultiValueMap<>();
        parts.add("file", toResource(image));
        if (reference != null) {
            parts.add("reference", toResource(reference));
        }
        parts.add("algorithms", algorithmsJson);
        parts.add("params", paramsJson);
        parts.add("visual", String.valueOf(visual));
        if (sessionId != null && !sessionId.isBlank()) {
            parts.add("sessionId", sessionId);
            parts.add("reset", String.valueOf(reset));
        }
        return postMultipart("/detect/image", parts);
    }

    /** 释放算法层为该会话保存的检测器状态（会话结束时调用，避免长期占用内存）。 */
    public void dropSession(String sessionId) {
        if (sessionId == null || sessionId.isBlank()) {
            return;
        }
        try {
            restTemplate.delete(baseUrl + "/session/{id}", sessionId);
        } catch (RuntimeException e) {
            // 算法层可能已重启或会话本来就没建过状态，清理失败不影响会话结束
            log.debug("清理算法层会话状态失败：{}", e.getMessage());
        }
    }

    /** 运行中改阈值：原地更新算法层里的检测器实例，保留 MOG2 背景模型等帧间状态。 */
    public void updateSessionParams(String sessionId, String paramsJson) {
        if (sessionId == null || sessionId.isBlank()) {
            return;
        }
        try {
            MultiValueMap<String, Object> parts = new LinkedMultiValueMap<>();
            parts.add("params", paramsJson);
            postMultipart("/session/" + sessionId + "/params", parts);
        } catch (RuntimeException e) {
            log.warn("同步新阈值到算法层失败：{}", e.getMessage());
        }
    }

    /** 远端媒体信息（已缓存时才成功），用于避免重复传输大文件。 */
    public Map<String, Object> cacheInfo(String key) {
        try {
            return restTemplate.getForObject(baseUrl + "/media/cache/{key}", Map.class, key);
        } catch (RuntimeException e) {
            throw new BusinessException("远端未缓存：" + e.getMessage());
        }
    }

    /** 把视频存进远端缓存（同一 key 只传一次）。 */
    public Map<String, Object> cacheMedia(Path video, String key) {
        MultiValueMap<String, Object> parts = new LinkedMultiValueMap<>();
        parts.add("file", toResource(video));
        parts.add("key", key);
        return postMultipart("/media/cache", parts);
    }

    public Map<String, Object> submitVideo(Path video, String algorithmsJson, String paramsJson,
                                           double sampleFps, int maxFrames, boolean visual,
                                           Long startMs, Long endMs, String segmentsJson,
                                           Integer refGapMs, String cacheKey) {
        MultiValueMap<String, Object> parts = new LinkedMultiValueMap<>();
        if (video != null) {
            parts.add("file", toResource(video));
        }
        parts.add("cacheKey", cacheKey == null ? "" : cacheKey);
        parts.add("algorithms", algorithmsJson);
        parts.add("params", paramsJson);
        parts.add("sampleFps", String.valueOf(sampleFps));
        parts.add("maxFrames", String.valueOf(maxFrames));
        parts.add("visual", String.valueOf(visual));
        parts.add("startMs", startMs == null ? "-1" : String.valueOf(startMs));
        parts.add("endMs", endMs == null ? "-1" : String.valueOf(endMs));
        parts.add("segments", segmentsJson == null || segmentsJson.isBlank() ? "[]" : segmentsJson);
        parts.add("refGapMs", String.valueOf(refGapMs == null ? 1000 : refGapMs));
        return postMultipart("/detect/video", parts);
    }

    public Map<String, Object> getTask(String algoTaskId, boolean includeFrames, int offset, int limit) {
        String path = "/detect/tasks/" + algoTaskId
                + "?includeFrames=" + includeFrames
                + "&offset=" + offset
                + "&limit=" + limit;
        return get(path);
    }

    public void cancelTask(String algoTaskId) {
        try {
            postMultipart("/detect/tasks/" + algoTaskId + "/cancel", new LinkedMultiValueMap<>());
        } catch (RuntimeException e) {
            log.warn("取消算法层任务失败 {}: {}", algoTaskId, e.getMessage());
        }
    }

    // ------------------------------------------------------------------ 低频稀疏采样
    /** 低频采样参数元数据与判决字典。 */
    public Map<String, Object> sparseMeta() {
        return get("/sparse/meta");
    }

    /** 提交一次稀疏抽样（1~K 帧）。相机状态由算法层按 cameraId 落盘，后端只做透传。 */
    public Map<String, Object> sparseSample(String cameraId, List<Path> frames, long tsMs,
                                            String name, boolean reset, String paramsJson) {
        MultiValueMap<String, Object> parts = new LinkedMultiValueMap<>();
        parts.add("cameraId", cameraId);
        for (Path frame : frames) {
            parts.add("frames", toResource(frame));
        }
        parts.add("tsMs", String.valueOf(tsMs));
        parts.add("name", name == null ? "" : name);
        parts.add("reset", String.valueOf(reset));
        parts.add("params", paramsJson == null || paramsJson.isBlank() ? "{}" : paramsJson);
        return postMultipart("/sparse/sample", parts);
    }

    public Map<String, Object> sparseCameras() {
        return get("/sparse/cameras");
    }

    public Map<String, Object> sparseState(String cameraId) {
        return get("/sparse/state/" + cameraId);
    }

    public Map<String, Object> sparseReset(String cameraId) {
        return postMultipart("/sparse/state/" + cameraId + "/reset", new LinkedMultiValueMap<>());
    }

    public void sparseDelete(String cameraId) {
        restTemplate.delete(baseUrl + "/sparse/state/{id}", cameraId);
    }

    /** 基线库缩略图（PNG 字节流）。 */
    public byte[] sparseThumb(String cameraId, int slot, int index) {
        try {
            ResponseEntity<byte[]> response = restTemplate.exchange(
                    baseUrl + "/sparse/thumb/" + cameraId + "/" + slot + "/" + index,
                    HttpMethod.GET, null, byte[].class);
            return response.getBody() == null ? new byte[0] : response.getBody();
        } catch (HttpStatusCodeException e) {
            throw mapHttpError(e, "/sparse/thumb");
        } catch (RestClientException e) {
            throw new BusinessException(502, "取基线缩略图失败：" + e.getMessage());
        }
    }

    /** 标定参照物：landmarks 为归一化矩形数组的 JSON，frame 为可选标定画面。 */
    public Map<String, Object> sparseSetLandmarks(String cameraId, String landmarksJson,
                                                  Path frame, boolean clear, boolean admitFrame) {
        MultiValueMap<String, Object> parts = new LinkedMultiValueMap<>();
        parts.add("landmarks", landmarksJson == null ? "[]" : landmarksJson);
        parts.add("clear", String.valueOf(clear));
        parts.add("admitFrame", String.valueOf(admitFrame));
        if (frame != null) {
            parts.add("file", toResource(frame));
        }
        return postMultipart("/sparse/landmarks/" + cameraId, parts);
    }

    public Map<String, Object> sparseGetLandmarks(String cameraId) {
        return get("/sparse/landmarks/" + cameraId);
    }

    public Map<String, Object> sparseClearLandmarks(String cameraId) {
        try {
            ResponseEntity<Map<String, Object>> response = restTemplate.exchange(
                    baseUrl + "/sparse/landmarks/" + cameraId, HttpMethod.DELETE, null, MAP_TYPE);
            return response.getBody() == null ? Map.of() : response.getBody();
        } catch (HttpStatusCodeException e) {
            throw mapHttpError(e, "/sparse/landmarks");
        } catch (RestClientException e) {
            throw new BusinessException(502, "清除参照物失败：" + e.getMessage());
        }
    }

    // ------------------------------------------------------------------ 参照物模板库
    public Map<String, Object> sparseListTemplates(String cameraId) {
        return get("/sparse/landmarks/" + cameraId + "/templates");
    }

    /** 追加一套外观模板（夜间/雨天/误报反馈）。 */
    public Map<String, Object> sparseAddTemplate(String cameraId, int landmarkId, Path frame,
                                                 String label, String source, String actor,
                                                 String verdictAtTime, boolean force) {
        MultiValueMap<String, Object> parts = new LinkedMultiValueMap<>();
        parts.add("landmarkId", String.valueOf(landmarkId));
        parts.add("label", label == null ? "" : label);
        parts.add("source", source == null || source.isBlank() ? "manual" : source);
        parts.add("actor", actor == null ? "" : actor);
        parts.add("verdictAtTime", verdictAtTime == null ? "" : verdictAtTime);
        parts.add("force", String.valueOf(force));
        if (frame != null) {
            parts.add("file", toResource(frame));
        }
        return postMultipart("/sparse/landmarks/" + cameraId + "/templates", parts);
    }

    public Map<String, Object> sparseRemoveTemplate(String cameraId, String templateId, String actor) {
        try {
            ResponseEntity<Map<String, Object>> response = restTemplate.exchange(
                    baseUrl + "/sparse/landmarks/" + cameraId + "/templates/" + templateId
                            + "?actor=" + (actor == null ? "" : actor),
                    HttpMethod.DELETE, null, MAP_TYPE);
            return response.getBody() == null ? Map.of() : response.getBody();
        } catch (HttpStatusCodeException e) {
            throw mapHttpError(e, "/sparse/templates");
        } catch (RestClientException e) {
            throw new BusinessException(502, "删除模板失败：" + e.getMessage());
        }
    }

    /** 模板预览图（把梯度幅值渲染成 PNG）。 */
    public byte[] sparseTemplatePreview(String cameraId, String templateId) {
        try {
            ResponseEntity<byte[]> response = restTemplate.exchange(
                    baseUrl + "/sparse/templates/" + cameraId + "/" + templateId + "/preview",
                    HttpMethod.GET, null, byte[].class);
            return response.getBody() == null ? new byte[0] : response.getBody();
        } catch (HttpStatusCodeException e) {
            throw mapHttpError(e, "/sparse/templates");
        } catch (RestClientException e) {
            throw new BusinessException(502, "取模板预览失败：" + e.getMessage());
        }
    }

    // ------------------------------------------------------------------ 底层
    private FileSystemResource toResource(Path file) {
        return new FileSystemResource(file);
    }

    private JsonNode getJson(String path) {
        try {
            String body = restTemplate.getForObject(baseUrl + path, String.class);
            if (body == null) {
                throw new BusinessException(502, "算法层返回空响应");
            }
            return objectMapper.readTree(body);
        } catch (HttpStatusCodeException e) {
            throw mapHttpError(e, path);
        } catch (ResourceAccessException e) {
            throw new BusinessException(502, "无法连接算法服务（" + baseUrl + "）：" + e.getMessage());
        } catch (BusinessException e) {
            throw e;
        } catch (Exception e) {
            throw new BusinessException(502, "调用算法层失败：" + e.getMessage());
        }
    }

    private Map<String, Object> get(String path) {
        try {
            ResponseEntity<Map<String, Object>> response =
                    restTemplate.exchange(baseUrl + path, HttpMethod.GET, null, MAP_TYPE);
            return response.getBody() == null ? Map.of() : response.getBody();
        } catch (HttpStatusCodeException e) {
            throw mapHttpError(e, path);
        } catch (ResourceAccessException e) {
            throw new BusinessException(502, "无法连接算法服务（" + baseUrl + "）：算法层未启动？");
        } catch (RestClientException e) {
            throw new BusinessException(502, "调用算法层失败：" + e.getMessage());
        }
    }

    private Map<String, Object> postMultipart(String path, MultiValueMap<String, Object> parts) {
        HttpHeaders headers = new HttpHeaders();
        headers.setContentType(MediaType.MULTIPART_FORM_DATA);
        HttpEntity<MultiValueMap<String, Object>> entity = new HttpEntity<>(parts, headers);
        try {
            ResponseEntity<Map<String, Object>> response =
                    restTemplate.exchange(baseUrl + path, HttpMethod.POST, entity, MAP_TYPE);
            return response.getBody() == null ? Map.of() : response.getBody();
        } catch (HttpStatusCodeException e) {
            throw mapHttpError(e, path);
        } catch (ResourceAccessException e) {
            throw new BusinessException(502, "无法连接算法服务（" + baseUrl + "）：" + e.getMessage());
        } catch (RestClientException e) {
            throw new BusinessException(502, "调用算法层失败：" + e.getMessage());
        }
    }

    private BusinessException mapHttpError(HttpStatusCodeException e, String path) {
        String body = e.getResponseBodyAsString();
        String detail = body;
        try {
            JsonNode node = objectMapper.readTree(body);
            if (node.hasNonNull("detail")) {
                detail = node.get("detail").asText();
            }
        } catch (Exception ignored) {
            // 保持原始响应体
        }
        log.warn("算法层返回 {} {} -> {}", e.getStatusCode().value(), path, detail);
        int status = e.getStatusCode().value();
        // 算法层的 4xx 是用户输入问题，原样返回；5xx 统一包装成 502
        return new BusinessException(status >= 500 ? 502 : status, "算法层错误：" + detail);
    }
}
