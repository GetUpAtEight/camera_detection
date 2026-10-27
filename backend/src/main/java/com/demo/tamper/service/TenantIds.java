package com.demo.tamper.service;

import com.demo.tamper.common.BusinessException;
import jakarta.servlet.http.HttpServletRequest;
import java.util.regex.Pattern;

/**
 * 租户解析与机位作用域拼接。
 *
 * <p>放在 service 层而不是 {@code config}，是因为它被业务服务直接依赖：
 * 测试可以只构造一个 request 就能验证拼接规则，不必启动整个 MVC。
 */
public final class TenantIds {

    /** 未传 {@code X-Tenant-Id} 时使用的租户。 */
    public static final String DEFAULT_TENANT = "default";

    /** 请求头。 */
    public static final String HEADER = "X-Tenant-Id";

    /** 租户与机位之间的分隔符，**只由后端拼接**。 */
    public static final char SEPARATOR = ':';

    /**
     * 租户段允许的字符：字母数字与 {@code -_}，长度 1~64。
     *
     * <p>不含 {@code :}——它是本层的分隔符，租户自带会让 {@code tenant:cameraId}
     * 的切分出现歧义（"第一个 : 之前是租户"这条解析规则就失效了）。
     */
    private static final Pattern TENANT = Pattern.compile("[A-Za-z0-9_-]{1,64}");

    /** 由 {@code TenantInterceptor} 写入 request attribute。 */
    public static final String ATTRIBUTE = TenantIds.class.getName() + ".tenant";

    private TenantIds() {
    }

    /** 只做格式校验，不做清洗：非法字符一律不放行。 */
    public static boolean isValid(String tenant) {
        return tenant != null && TENANT.matcher(tenant).matches();
    }

    /** 取当前请求的租户；没设置过就是缺省租户。 */
    public static String of(HttpServletRequest request) {
        if (request == null) {
            return DEFAULT_TENANT;
        }
        Object value = request.getAttribute(ATTRIBUTE);
        return value instanceof String s && !s.isEmpty() ? s : DEFAULT_TENANT;
    }

    /** 拼出算法层使用的作用域 id：{@code {tenant}:{cameraId}}。 */
    public static String scoped(String tenant, String cameraId) {
        String t = tenant == null || tenant.isBlank() ? DEFAULT_TENANT : tenant;
        return t + SEPARATOR + cameraId;
    }

    /** 校验请求头里的租户并写入 attribute；非法直接 400。 */
    public static void bind(HttpServletRequest request, String rawTenant) {
        if (rawTenant == null || rawTenant.isBlank()) {
            request.setAttribute(ATTRIBUTE, DEFAULT_TENANT);
            return;
        }
        String tenant = rawTenant.trim();
        if (!isValid(tenant)) {
            throw new BusinessException(400, "X-Tenant-Id 非法：只允许字母、数字与 - _，长度 1~64");
        }
        request.setAttribute(ATTRIBUTE, tenant);
    }
}
