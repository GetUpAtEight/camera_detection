package com.demo.tamper.config;

import com.fasterxml.jackson.databind.ObjectMapper;
import org.springframework.context.annotation.Configuration;
import org.springframework.web.servlet.config.annotation.CorsRegistry;
import org.springframework.web.servlet.config.annotation.InterceptorRegistry;
import org.springframework.web.servlet.config.annotation.WebMvcConfigurer;

/**
 * 跨域 + 拦截器链。
 *
 * <p>正常情况下前端经 Vite 代理同源访问 {@code /api}，并不需要 CORS。
 * 这里只为「浏览器直连后端调试」放行本机开发端口，**不放开到任意来源**。
 *
 * <p>拦截器顺序有意义：先 {@link AuthInterceptor}（认证），再
 * {@link TenantInterceptor}（解析身份）。未认证的请求不该看到租户参数的校验结果。
 * 放开 {@code /api/algo/health} 是因为前端用它判断"算法服务是否在线"，
 * 这个探活要能在没有令牌时也给出结果，否则页面只会显示"后端连不上"。
 */
@Configuration
public class WebConfig implements WebMvcConfigurer {

    private final AppProperties properties;
    private final ObjectMapper objectMapper;

    public WebConfig(AppProperties properties, ObjectMapper objectMapper) {
        this.properties = properties;
        this.objectMapper = objectMapper;
    }

    @Override
    public void addCorsMappings(CorsRegistry registry) {
        registry.addMapping("/api/**")
                .allowedOriginPatterns(
                        "http://localhost:*",
                        "http://127.0.0.1:*")
                .allowedMethods("GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS")
                .allowedHeaders("*")
                .exposedHeaders("Content-Disposition", "X-Server-Now")
                .maxAge(3600);
    }

    @Override
    public void addInterceptors(InterceptorRegistry registry) {
        registry.addInterceptor(new AuthInterceptor(properties.getAuth().getToken(), objectMapper))
                .order(0)
                .addPathPatterns("/api/**")
                .excludePathPatterns("/api/algo/health");
        registry.addInterceptor(new TenantInterceptor(objectMapper))
                .order(1)
                .addPathPatterns("/api/**");
    }
}
