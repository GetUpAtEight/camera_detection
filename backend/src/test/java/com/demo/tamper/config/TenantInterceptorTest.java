package com.demo.tamper.config;

import static org.assertj.core.api.Assertions.assertThat;

import com.demo.tamper.service.TenantIds;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.springframework.mock.web.MockHttpServletRequest;
import org.springframework.mock.web.MockHttpServletResponse;

/** #1b 入口校验：非法租户直接 400 + ApiResponse，不清洗。 */
class TenantInterceptorTest {

    private final ObjectMapper objectMapper = new ObjectMapper();

    @Test
    @DisplayName("未传：缺省 default，放行")
    void defaultsToDefault() throws Exception {
        MockHttpServletRequest request =
                new MockHttpServletRequest("GET", "/api/sparse/state/cam1");
        MockHttpServletResponse response = new MockHttpServletResponse();
        assertThat(new TenantInterceptor(objectMapper).preHandle(request, response, new Object()))
                .isTrue();
        assertThat(TenantIds.of(request)).isEqualTo("default");
    }

    @Test
    @DisplayName("非法字符：400 + ApiResponse{code:400}，且不写入任何租户")
    void rejectsIllegalTenant() throws Exception {
        MockHttpServletRequest request =
                new MockHttpServletRequest("GET", "/api/sparse/state/cam1");
        request.addHeader(TenantIds.HEADER, "tenant/a");
        MockHttpServletResponse response = new MockHttpServletResponse();

        assertThat(new TenantInterceptor(objectMapper).preHandle(request, response, new Object()))
                .isFalse();
        assertThat(response.getStatus()).isEqualTo(400);
        assertThat(response.getContentAsString()).contains("\"code\":400");
        assertThat(request.getAttribute(TenantIds.ATTRIBUTE)).isNull();
    }

    @Test
    @DisplayName("租户含 ':' 拒绝")
    void rejectsSeparator() throws Exception {
        MockHttpServletRequest request =
                new MockHttpServletRequest("GET", "/api/sparse/state/cam1");
        request.addHeader(TenantIds.HEADER, "a:b");
        MockHttpServletResponse response = new MockHttpServletResponse();
        assertThat(new TenantInterceptor(objectMapper).preHandle(request, response, new Object()))
                .isFalse();
        assertThat(response.getStatus()).isEqualTo(400);
    }

    @Test
    @DisplayName("合法租户写入 attribute")
    void acceptsLegalTenant() throws Exception {
        MockHttpServletRequest request =
                new MockHttpServletRequest("GET", "/api/sparse/state/cam1");
        request.addHeader(TenantIds.HEADER, "tenant_01-A");
        MockHttpServletResponse response = new MockHttpServletResponse();
        assertThat(new TenantInterceptor(objectMapper).preHandle(request, response, new Object()))
                .isTrue();
        assertThat(TenantIds.of(request)).isEqualTo("tenant_01-A");
    }
}
