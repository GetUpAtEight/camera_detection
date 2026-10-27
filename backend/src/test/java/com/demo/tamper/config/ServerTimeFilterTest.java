package com.demo.tamper.config;

import static org.assertj.core.api.Assertions.assertThat;

import jakarta.servlet.FilterChain;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.springframework.mock.web.MockHttpServletRequest;
import org.springframework.mock.web.MockHttpServletResponse;

/** #12 后端侧：每个响应都回填 X-Server-Now（毫秒时间戳）。 */
class ServerTimeFilterTest {

    private static final FilterChain NOOP = (req, res) -> { };

    @Test
    @DisplayName("正常响应带 X-Server-Now，值是可解析的当前毫秒时间戳")
    void addsHeader() throws Exception {
        MockHttpServletRequest request = new MockHttpServletRequest("GET", "/api/sparse/meta");
        MockHttpServletResponse response = new MockHttpServletResponse();
        long before = System.currentTimeMillis();

        new ServerTimeFilter().doFilter(request, response, NOOP);

        String value = response.getHeader("X-Server-Now");
        assertThat(value).isNotNull();
        assertThat(Long.parseLong(value)).isBetween(before, System.currentTimeMillis());
    }

    @Test
    @DisplayName("异常路径也回填（finally 里设置）")
    void addsHeaderOnException() {
        MockHttpServletRequest request = new MockHttpServletRequest("GET", "/api/sparse/meta");
        MockHttpServletResponse response = new MockHttpServletResponse();
        FilterChain boom = (req, res) -> {
            throw new IllegalStateException("boom");
        };

        try {
            new ServerTimeFilter().doFilter(request, response, boom);
        } catch (Exception expected) {
            // 异常继续向上抛，但头已经写了
        }
        assertThat(response.getHeader("X-Server-Now")).isNotNull();
    }
}
