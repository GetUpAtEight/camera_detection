package com.demo.tamper.service;

import com.demo.tamper.common.BusinessException;
import com.demo.tamper.config.AppProperties;
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
 * 算法层（Python / FastAPI）的唯一出口。
 *
 * <p>后端<b>不做任何判定计算</b>，只负责把请求翻译成 multipart、把算法层的错误统一
 * 映射成 {@link BusinessException}——避免同一份判定逻辑在两个语言里各写一遍而逐渐漂移。
 *
 * <p>本服务只覆盖低频巡检（{@code /sparse/*}）接口。
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
        this.baseUrl = properties.getAlgo().getBaseUrl();
    }

    // ------------------------------------------------------------------ 健康
    public String getBaseUrl() {
        return baseUrl;
    }

    public Map<String, Object> health() {
        return get("/health");
    }

    // ------------------------------------------------------------------ 低频巡检
    public Map<String, Object> sparseMeta() {
        return get("/sparse/meta");
    }

    /** 一次抽样 = 一次请求，frames 为已经落盘的临时文件。 */
    public Map<String, Object> sparseSample(String cameraId, List<Path> frames, long tsMs,
                                            String name, boolean reset, String params) {
        MultiValueMap<String, Object> parts = new LinkedMultiValueMap<>();
        parts.add("cameraId", cameraId);
        parts.add("tsMs", String.valueOf(tsMs));
        parts.add("name", name == null ? "" : name);
        parts.add("reset", String.valueOf(reset));
        parts.add("params", params == null || params.isBlank() ? "{}" : params);
        for (Path frame : frames) {
            parts.add("frames", toResource(frame));
        }
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

    public Map<String, Object> sparseDelete(String cameraId) {
        return delete("/sparse/state/" + cameraId);
    }

    public byte[] sparseThumb(String cameraId, int slot, int index) {
        return getBytes("/sparse/thumb/" + cameraId + "/" + slot + "/" + index);
    }

    // ------------------------------------------------------------------ 参照物
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
        return delete("/sparse/landmarks/" + cameraId);
    }

    // ------------------------------------------------------------------ 模板库
    public Map<String, Object> sparseListTemplates(String cameraId) {
        return get("/sparse/landmarks/" + cameraId + "/templates");
    }

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
        return delete("/sparse/landmarks/" + cameraId + "/templates/" + templateId
                + "?actor=" + (actor == null ? "" : actor));
    }

    public byte[] sparseTemplatePreview(String cameraId, String templateId) {
        return getBytes("/sparse/templates/" + cameraId + "/" + templateId + "/preview");
    }

    // ------------------------------------------------------------------ 内部
    private FileSystemResource toResource(Path file) {
        return new FileSystemResource(file);
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
            throw new BusinessException(502, "调用算法服务失败：" + e.getMessage());
        }
    }

    private byte[] getBytes(String path) {
        try {
            ResponseEntity<byte[]> response =
                    restTemplate.exchange(baseUrl + path, HttpMethod.GET, null, byte[].class);
            return response.getBody() == null ? new byte[0] : response.getBody();
        } catch (HttpStatusCodeException e) {
            throw mapHttpError(e, path);
        } catch (ResourceAccessException e) {
            throw new BusinessException(502, "无法连接算法服务（" + baseUrl + "）：算法层未启动？");
        } catch (RestClientException e) {
            throw new BusinessException(502, "调用算法服务失败：" + e.getMessage());
        }
    }

    private Map<String, Object> delete(String path) {
        try {
            ResponseEntity<Map<String, Object>> response =
                    restTemplate.exchange(baseUrl + path, HttpMethod.DELETE, null, MAP_TYPE);
            return response.getBody() == null ? Map.of() : response.getBody();
        } catch (HttpStatusCodeException e) {
            throw mapHttpError(e, path);
        } catch (ResourceAccessException e) {
            throw new BusinessException(502, "无法连接算法服务（" + baseUrl + "）：算法层未启动？");
        } catch (RestClientException e) {
            throw new BusinessException(502, "调用算法服务失败：" + e.getMessage());
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
            throw new BusinessException(502, "无法连接算法服务（" + baseUrl + "）：算法层未启动？");
        } catch (RestClientException e) {
            throw new BusinessException(502, "调用算法服务失败：" + e.getMessage());
        }
    }

    /**
     * 把算法层的 HTTP 错误映射成业务异常。
     *
     * <p>算法层用 {@code {"detail": "..."}} 表达可读原因，这里透出它；
     * 4xx 直接把 detail 给前端，5xx 只回状态码、不泄露内部细节。
     */
    private BusinessException mapHttpError(HttpStatusCodeException e, String path) {
        String body = e.getResponseBodyAsString();
        String detail = null;
        try {
            JsonNode node = objectMapper.readTree(body);
            if (node != null && node.hasNonNull("detail")) {
                detail = node.get("detail").asText();
            }
        } catch (Exception ignore) {
            // body 不是 JSON：忽略，走下面的兜底
        }
        int status = e.getStatusCode().value();
        if (status >= 400 && status < 500) {
            return new BusinessException(status, detail == null ? "请求参数有误" : detail);
        }
        log.warn("算法层返回 {}（{}）：{}", status, path, body);
        return new BusinessException(502, "算法层处理失败（" + status + "）");
    }
}
