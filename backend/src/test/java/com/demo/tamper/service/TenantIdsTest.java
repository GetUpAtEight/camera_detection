package com.demo.tamper.service;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import com.demo.tamper.common.BusinessException;
import java.util.Map;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.springframework.mock.web.MockHttpServletRequest;

/**
 * #1b 租户规则：缺省 default、非法 400、拼成 {@code tenant:cameraId}。
 *
 * <p>刻意不启动 Spring 上下文：租户规则是纯函数，直接构造 MockHttpServletRequest
 * 就能覆盖，失败信息也更直白。
 */
class TenantIdsTest {

    @Test
    @DisplayName("未传请求头：缺省 default")
    void defaultsToDefaultTenant() {
        MockHttpServletRequest request = new MockHttpServletRequest();
        TenantIds.bind(request, null);
        assertThat(TenantIds.of(request)).isEqualTo("default");
        assertThat(TenantIds.scoped("default", "cam1")).isEqualTo("default:cam1");
    }

    @Test
    @DisplayName("空白请求头按未传处理")
    void blankHeaderFallsBackToDefault() {
        MockHttpServletRequest request = new MockHttpServletRequest();
        TenantIds.bind(request, "   ");
        assertThat(TenantIds.of(request)).isEqualTo("default");
    }

    @Test
    @DisplayName("合法租户：字母数字与 - _")
    void acceptsLegalTenant() {
        MockHttpServletRequest request = new MockHttpServletRequest();
        TenantIds.bind(request, "tenant_01-A");
        assertThat(TenantIds.of(request)).isEqualTo("tenant_01-A");
        assertThat(TenantIds.scoped(TenantIds.of(request), "cam1")).isEqualTo("tenant_01-A:cam1");
    }

    @Test
    @DisplayName("非法字符直接 400，绝不清洗（tenant/a 不能被洗成 tenanta）")
    void rejectsIllegalCharacters() {
        MockHttpServletRequest request = new MockHttpServletRequest();
        assertThatThrownBy(() -> TenantIds.bind(request, "tenant/a"))
                .isInstanceOf(BusinessException.class)
                .satisfies(e -> assertThat(((BusinessException) e).getStatus()).isEqualTo(400));
        // 关键：request 上不留任何被清洗后的租户
        assertThat(request.getAttribute(TenantIds.ATTRIBUTE)).isNull();
    }

    @Test
    @DisplayName("租户自带分隔符 ':' 同样拒绝：分隔符只能由后端引入")
    void rejectsSeparatorInTenant() {
        MockHttpServletRequest request = new MockHttpServletRequest();
        assertThatThrownBy(() -> TenantIds.bind(request, "a:b"))
                .isInstanceOf(BusinessException.class);
    }

    @Test
    @DisplayName("超长租户拒绝")
    void rejectsOverlongTenant() {
        MockHttpServletRequest request = new MockHttpServletRequest();
        assertThatThrownBy(() -> TenantIds.bind(request, "a".repeat(65)))
                .isInstanceOf(BusinessException.class);
    }

    @Test
    @DisplayName("解析规则确定：第一个 ':' 之前是租户段")
    void scopeIsSplitOnFirstColon() {
        String scoped = TenantIds.scoped("t1", "cam:2");
        assertThat(scoped).isEqualTo("t1:cam:2");
        assertThat(scoped.substring(0, scoped.indexOf(TenantIds.SEPARATOR))).isEqualTo("t1");
        assertThat(scoped.substring(scoped.indexOf(TenantIds.SEPARATOR) + 1)).isEqualTo("cam:2");
    }

    @Test
    @DisplayName("请求 attribute 与业务读取一致（拦截器写入 → 服务读取）")
    void attributeRoundTrips() {
        MockHttpServletRequest request = new MockHttpServletRequest();
        TenantIds.bind(request, "shop42");
        assertThat(TenantIds.of(request)).isEqualTo("shop42");
        assertThat(Map.of("scoped", TenantIds.scoped(TenantIds.of(request), "gate"))
                .get("scoped")).isEqualTo("shop42:gate");
    }
}
