package com.demo.tamper.service;

import com.demo.tamper.common.BusinessException;
import com.demo.tamper.config.AppProperties;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.net.URI;
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
import org.springframework.web.util.UriComponentsBuilder;

/**
 * 算法层（Python / FastAPI）的唯一出口。
 *
 * <p>后端<b>不做任何判定计算</b>，只负责把请求翻译成 multipart、把算法层的错误统一
 * 映射成 {@link BusinessException}——避免同一份判定逻辑在两个语言里各写一遍而逐渐漂移。
 *
 * <p>本服务只覆盖低频巡检（{@code /sparse/*}）接口。
 *
 * <p><b>URL 编码</b>：cameraId / templateId / actor 全部来自外部输入，一旦混入
 * {@code / ? & = # % 空格 中文}，裸字符串拼接会把它们当成路径或查询分隔符——
 * 轻则 404，重则打到别的算法端点（路径穿越）。因此这里统一用
 * {@link UriComponentsBuilder} 的 URI 模板：先把<b>字面量</b>路径与查询写好，再
 * {@code encode()}，最后 {@code buildAndExpand(...)} 注入变量。
 * Spring 在该顺序下逐个变量做严格的 segment/query 编码（{@code /} → {@code %2F}、
 * 空格 → {@code %20}、中文按 UTF-8 百分号编码），且不会二次编码模板里已编好的字符。
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
        return get(uri("/health"));
    }

    // ------------------------------------------------------------------ 低频巡检
    public Map<String, Object> sparseMeta() {
        return get(uri("/sparse/meta"));
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
        return postMultipart(uri("/sparse/sample"), parts);
    }

    public Map<String, Object> sparseCameras() {
        return get(uri("/sparse/cameras"));
    }

    public Map<String, Object> sparseState(String cameraId) {
        return get(uri("/sparse/state/{cameraId}", cameraId));
    }

    public Map<String, Object> sparseReset(String cameraId) {
        return postMultipart(uri("/sparse/state/{cameraId}/reset", cameraId),
                new LinkedMultiValueMap<>());
    }

    public Map<String, Object> sparseDelete(String cameraId) {
        return delete(uri("/sparse/state/{cameraId}", cameraId));
    }

    public byte[] sparseThumb(String cameraId, int slot, int index) {
        return getBytes(uri("/sparse/thumb/{cameraId}/{slot}/{index}",
                cameraId, String.valueOf(slot), String.valueOf(index)));
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
        return postMultipart(uri("/sparse/landmarks/{cameraId}", cameraId), parts);
    }

    public Map<String, Object> sparseGetLandmarks(String cameraId) {
        return get(uri("/sparse/landmarks/{cameraId}", cameraId));
    }

    public Map<String, Object> sparseClearLandmarks(String cameraId) {
        return delete(uri("/sparse/landmarks/{cameraId}", cameraId));
    }

    // ------------------------------------------------------------------ 模板库
    public Map<String, Object> sparseListTemplates(String cameraId) {
        return get(uri("/sparse/landmarks/{cameraId}/templates", cameraId));
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
        return postMultipart(uri("/sparse/landmarks/{cameraId}/templates", cameraId), parts);
    }

    public Map<String, Object> sparseRemoveTemplate(String cameraId, String templateId, String actor) {
        // actor 走查询参数变量：{@code &} / {@code =} / 中文由 UriComponentsBuilder 编码，
        // 否则 "a&force=1" 这样的 actor 会变成额外的查询参数。
        URI target = UriComponentsBuilder.fromHttpUrl(baseUrl)
                .path("/sparse/landmarks/{cameraId}/templates/{templateId}")
                .queryParam("actor", "{actor}")
                .encode()
                .buildAndExpand(cameraId, templateId, actor == null ? "" : actor)
                .toUri();
        return delete(target);
    }

    public byte[] sparseTemplatePreview(String cameraId, String templateId) {
        return getBytes(uri("/sparse/templates/{cameraId}/{templateId}/preview",
                cameraId, templateId));
    }

    // ------------------------------------------------------------------ 内部
    private FileSystemResource toResource(Path file) {
        return new FileSystemResource(file);
    }

    /**
     * 把「算法层路径模板 + 变量」编成绝对的、已编码的 URI。
     *
     * <p>模板里的 {@code {name}} 必须在调用时全部给定，否则
     * {@code buildAndExpand} 会保留占位符并把请求原样打出去。
     */
    private URI uri(String pathTemplate, Object... vars) {
        return UriComponentsBuilder.fromHttpUrl(baseUrl)
                .path(pathTemplate)
                .encode()
                .buildAndExpand(vars)
                .toUri();
    }

    private Map<String, Object> get(URI target) {
        try {
            ResponseEntity<Map<String, Object>> response =
                    restTemplate.exchange(target, HttpMethod.GET, null, MAP_TYPE);
            return response.getBody() == null ? Map.of() : response.getBody();
        } catch (HttpStatusCodeException e) {
            throw mapHttpError(e, target);
        } catch (ResourceAccessException e) {
            throw new BusinessException(502, "无法连接算法服务（" + baseUrl + "）：算法层未启动？");
        } catch (RestClientException e) {
            throw new BusinessException(502, "调用算法服务失败：" + e.getMessage());
        }
    }

    private byte[] getBytes(URI target) {
        try {
            ResponseEntity<byte[]> response =
                    restTemplate.exchange(target, HttpMethod.GET, null, byte[].class);
            return response.getBody() == null ? new byte[0] : response.getBody();
        } catch (HttpStatusCodeException e) {
            throw mapHttpError(e, target);
        } catch (ResourceAccessException e) {
            throw new BusinessException(502, "无法连接算法服务（" + baseUrl + "）：算法层未启动？");
        } catch (RestClientException e) {
            throw new BusinessException(502, "调用算法服务失败：" + e.getMessage());
        }
    }

    private Map<String, Object> delete(URI target) {
        try {
            ResponseEntity<Map<String, Object>> response =
                    restTemplate.exchange(target, HttpMethod.DELETE, null, MAP_TYPE);
            return response.getBody() == null ? Map.of() : response.getBody();
        } catch (HttpStatusCodeException e) {
            throw mapHttpError(e, target);
        } catch (ResourceAccessException e) {
            throw new BusinessException(502, "无法连接算法服务（" + baseUrl + "）：算法层未启动？");
        } catch (RestClientException e) {
            throw new BusinessException(502, "调用算法服务失败：" + e.getMessage());
        }
    }

    private Map<String, Object> postMultipart(URI target, MultiValueMap<String, Object> parts) {
        HttpHeaders headers = new HttpHeaders();
        headers.setContentType(MediaType.MULTIPART_FORM_DATA);
        HttpEntity<MultiValueMap<String, Object>> entity = new HttpEntity<>(parts, headers);
        try {
            ResponseEntity<Map<String, Object>> response =
                    restTemplate.exchange(target, HttpMethod.POST, entity, MAP_TYPE);
            return response.getBody() == null ? Map.of() : response.getBody();
        } catch (HttpStatusCodeException e) {
            throw mapHttpError(e, target);
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
     *
     * <p>注意 {@code detail} 也可能是结构化对象（FastAPI 的 422 校验错误
     * {@code [{"loc":...,"msg":...}]}），此时 {@code asText()} 会得到空串，
     * 这里按"拿不到可读原因"处理，走兜底文案而不是回一个空消息。
     */
    private BusinessException mapHttpError(HttpStatusCodeException e, URI target) {
        String body = e.getResponseBodyAsString();
        String detail = null;
        try {
            JsonNode node = objectMapper.readTree(body);
            if (node != null && node.hasNonNull("detail")) {
                JsonNode detailNode = node.get("detail");
                if (detailNode.isTextual()) {
                    detail = detailNode.asText();
                } else {
                    detail = detailNode.toString();
                }
            }
        } catch (Exception ignore) {
            // body 不是 JSON：忽略，走下面的兜底
        }
        if (detail != null && detail.isBlank()) {
            detail = null;
        }
        int status = e.getStatusCode().value();
        if (status >= 400 && status < 500) {
            return new BusinessException(status, detail == null ? "请求参数有误" : detail);
        }
        log.warn("算法层返回 {}（{}）：{}", status, target, body);
        return new BusinessException(502, "算法层处理失败（" + status + "）");
    }
}
