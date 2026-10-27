package com.demo.tamper.config;

import com.demo.tamper.common.ApiResponse;
import com.demo.tamper.common.BusinessException;
import com.demo.tamper.service.TenantIds;
import com.fasterxml.jackson.databind.ObjectMapper;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.web.servlet.HandlerInterceptor;

/**
 * 解析 {@code X-Tenant-Id} 请求头。
 *
 * <p>缺省为 {@code default}（前端不传也能用）。**非法字符直接 400，绝不清洗**：
 * 静默把 {@code tenant/a} 洗成 {@code tenanta}，等于把请求悄悄落到另一个合法租户，
 * 属于静默串租/越权。算法层的 {@code _safe_id} 是落盘兜底，与这里的入口校验职责不同。
 *
 * <p>排在鉴权之后（先认证再解析身份）：未通过认证的请求不该知道自己传的租户参数是否合法。
 */
public class TenantInterceptor implements HandlerInterceptor {

    private final ObjectMapper objectMapper;

    public TenantInterceptor(ObjectMapper objectMapper) {
        this.objectMapper = objectMapper;
    }

    @Override
    public boolean preHandle(HttpServletRequest request, HttpServletResponse response,
                             Object handler) throws Exception {
        String raw = request.getHeader(TenantIds.HEADER);
        try {
            TenantIds.bind(request, raw);
            return true;
        } catch (BusinessException e) {
            response.setStatus(HttpStatus.BAD_REQUEST.value());
            response.setContentType(MediaType.APPLICATION_JSON_VALUE);
            response.setCharacterEncoding(StandardCharsets.UTF_8.name());
            objectMapper.writeValue(response.getWriter(), ApiResponse.fail(400, e.getMessage()));
            return false;
        }
    }
}
