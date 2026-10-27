package com.demo.tamper.config;

import static org.assertj.core.api.Assertions.assertThat;

import com.demo.tamper.service.TenantIds;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.springframework.http.HttpHeaders;
import org.springframework.mock.web.MockHttpServletRequest;
import org.springframework.mock.web.MockHttpServletResponse;

/**
 * #1a 鉴权拦截器：401 结构、两种令牌头、预检放行、fail-open。
 *
 * <p>直接实例化拦截器而不是启动 Spring 上下文：这里验证的是判定规则本身，
 * 用真实 request/response 断言状态码与响应体，比 MockMvc 更贴近"拦截器到底写了什么"。
 */
class AuthInterceptorTest {

    static final String TOKEN = "s3cr3t-token";

    private final ObjectMapper objectMapper = new ObjectMapper();

    private MockHttpServletRequest request;
    private MockHttpServletResponse response;

    @BeforeEach
    void setUp() {
        request = new MockHttpServletRequest("GET", "/api/sparse/state/cam1");
        response = new MockHttpServletResponse();
    }

    private boolean invoke(AuthInterceptor interceptor) throws Exception {
        return interceptor.preHandle(request, response, new Object());
    }

    @Test
    @DisplayName("无令牌：401 + ApiResponse{code:401,message,data:null}")
    void rejectsWithoutToken() throws Exception {
        assertThat(invoke(new AuthInterceptor(TOKEN, objectMapper))).isFalse();
        assertThat(response.getStatus()).isEqualTo(401);
        assertThat(response.getHeader(HttpHeaders.WWW_AUTHENTICATE))
                .isEqualTo("Bearer realm=\"camera-tamper\"");
        String body = response.getContentAsString();
        assertThat(body).contains("\"code\":401");
        assertThat(body).contains("\"data\":null");
        // 不泄露正确令牌
        assertThat(body).doesNotContain(TOKEN);
    }

    @Test
    @DisplayName("令牌错误：401")
    void rejectsWrongToken() throws Exception {
        request.addHeader(HttpHeaders.AUTHORIZATION, "Bearer nope");
        assertThat(invoke(new AuthInterceptor(TOKEN, objectMapper))).isFalse();
        assertThat(response.getStatus()).isEqualTo(401);
    }

    @Test
    @DisplayName("Authorization: Bearer <token> 通过")
    void acceptsBearer() throws Exception {
        request.addHeader(HttpHeaders.AUTHORIZATION, "Bearer " + TOKEN);
        assertThat(invoke(new AuthInterceptor(TOKEN, objectMapper))).isTrue();
        assertThat(response.getStatus()).isEqualTo(200);
    }

    @Test
    @DisplayName("X-API-Token 别名同样通过")
    void acceptsApiTokenHeader() throws Exception {
        request.addHeader(AuthInterceptor.HEADER_API_TOKEN, TOKEN);
        assertThat(invoke(new AuthInterceptor(TOKEN, objectMapper))).isTrue();
    }

    @Test
    @DisplayName("Bearer 方案名大小写不敏感")
    void bearerSchemeIsCaseInsensitive() throws Exception {
        request.addHeader(HttpHeaders.AUTHORIZATION, "bearer " + TOKEN);
        assertThat(invoke(new AuthInterceptor(TOKEN, objectMapper))).isTrue();
    }

    @Test
    @DisplayName("两种头都带时以 Authorization 为准；Bearer 错误则 401")
    void bearerTakesPrecedence() throws Exception {
        request.addHeader(HttpHeaders.AUTHORIZATION, "Bearer wrong");
        request.addHeader(AuthInterceptor.HEADER_API_TOKEN, TOKEN);
        assertThat(invoke(new AuthInterceptor(TOKEN, objectMapper))).isFalse();
    }

    @Test
    @DisplayName("OPTIONS 预检放行（浏览器不带头）")
    void allowsCorsPreflight() throws Exception {
        request = new MockHttpServletRequest("OPTIONS", "/api/sparse/state/cam1");
        assertThat(invoke(new AuthInterceptor(TOKEN, objectMapper))).isTrue();
    }

    @Test
    @DisplayName("token 未配置：fail-open 放行，且不写 401")
    void failsOpenWhenTokenMissing() throws Exception {
        assertThat(invoke(new AuthInterceptor("", objectMapper))).isTrue();
        assertThat(response.getStatus()).isEqualTo(200);
    }

    @Test
    @DisplayName("token 未配置：带错误凭据也放行，不把自己锁死")
    void failsOpenEvenWithWrongCredentials() throws Exception {
        request.addHeader(HttpHeaders.AUTHORIZATION, "Bearer whatever");
        assertThat(invoke(new AuthInterceptor(null, objectMapper))).isTrue();
    }

    @Test
    @DisplayName("令牌两侧空白被忽略（环境变量粘贴常见）")
    void trimsConfiguredToken() throws Exception {
        request.addHeader(HttpHeaders.AUTHORIZATION, "Bearer " + TOKEN);
        assertThat(invoke(new AuthInterceptor("  " + TOKEN + "  ", objectMapper))).isTrue();
    }
}
