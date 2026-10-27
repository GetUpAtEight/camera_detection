package com.demo.tamper.config;

import com.demo.tamper.common.ApiResponse;
import com.fasterxml.jackson.databind.ObjectMapper;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpMethod;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.util.StringUtils;
import org.springframework.web.servlet.HandlerInterceptor;

/**
 * {@code /api/**} 的令牌校验。
 *
 * <p>令牌来源有两个头，二者等价：
 *
 * <ul>
 *   <li>{@code Authorization: Bearer <token>}（为准）
 *   <li>{@code X-API-Token: <token>}（备用别名，给无法设置 Authorization 的调用方保留）
 * </ul>
 *
 * <p><b>fail-open 是有意的</b>：没配令牌时放行并打一条 WARN。演示/内网环境下，
 * "起不来"比"没鉴权"更糟；但部署前必须配好，日志里已经把这句写死。
 *
 * <p>这里用 {@link MessageDigest#isEqual} 而不是 {@code String.equals}：后者在首个
 * 不同字符处提前返回，比较耗时会随匹配前缀长度变化，可被用来逐字节猜令牌。
 */
public class AuthInterceptor implements HandlerInterceptor {

    private static final Logger log = LoggerFactory.getLogger(AuthInterceptor.class);

    public static final String BEARER = "Bearer ";

    public static final String HEADER_API_TOKEN = "X-API-Token";

    private static final String CHALLENGE = "Bearer realm=\"camera-tamper\"";

    private final String token;
    private final ObjectMapper objectMapper;

    /** 已打过 fail-open 告警，避免每条请求刷一行日志。 */
    private volatile boolean warned;

    public AuthInterceptor(String token, ObjectMapper objectMapper) {
        this.token = token == null ? "" : token.trim();
        this.objectMapper = objectMapper;
    }

    @Override
    public boolean preHandle(HttpServletRequest request, HttpServletResponse response,
                             Object handler) throws Exception {
        if (!StringUtils.hasText(token)) {
            if (!warned) {
                warned = true;
                log.warn("未配置 app.auth.token（环境变量 APP_AUTH_TOKEN），/api/** 当前对所有调用方开放。"
                        + "部署前必须配置 token，否则任何能访问该端口的人都能调用算法接口。");
            }
            return true;
        }

        // CORS 预检不带 Authorization 头（浏览器规范如此），拦它等于把跨域调试打死。
        if (HttpMethod.OPTIONS.matches(request.getMethod())) {
            return true;
        }

        String presented = extractToken(request);
        if (presented != null && matches(presented)) {
            return true;
        }

        log.warn("鉴权失败 {} {}：{}", request.getMethod(), request.getRequestURI(),
                presented == null ? "未携带令牌" : "令牌不匹配");
        response.setHeader(HttpHeaders.WWW_AUTHENTICATE, CHALLENGE);
        writeUnauthorized(response);
        return false;
    }

    /** 按 Bearer 优先、X-API-Token 兜底的顺序取令牌；都没有返回 null。 */
    static String extractToken(HttpServletRequest request) {
        String authorization = request.getHeader(HttpHeaders.AUTHORIZATION);
        if (authorization != null) {
            String value = authorization.trim();
            if (value.regionMatches(true, 0, BEARER, 0, BEARER.length())) {
                String bearer = value.substring(BEARER.length()).trim();
                if (!bearer.isEmpty()) {
                    return bearer;
                }
            }
        }
        String apiToken = request.getHeader(HEADER_API_TOKEN);
        if (StringUtils.hasText(apiToken)) {
            return apiToken.trim();
        }
        return null;
    }

    private boolean matches(String presented) {
        return MessageDigest.isEqual(
                presented.getBytes(StandardCharsets.UTF_8),
                token.getBytes(StandardCharsets.UTF_8));
    }

    private void writeUnauthorized(HttpServletResponse response) throws IOException {
        response.setStatus(HttpStatus.UNAUTHORIZED.value());
        response.setContentType(MediaType.APPLICATION_JSON_VALUE);
        response.setCharacterEncoding(StandardCharsets.UTF_8.name());
        // 沿用项目统一响应体：{code, message, data}
        ApiResponse<Void> body = ApiResponse.fail(HttpStatus.UNAUTHORIZED.value(),
                "缺少或错误的访问令牌：请携带 Authorization: Bearer <token> 或 X-API-Token");
        objectMapper.writeValue(response.getWriter(), body);
    }
}