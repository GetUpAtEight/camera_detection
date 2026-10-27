package com.demo.tamper.config;

import org.springframework.boot.web.servlet.FilterRegistrationBean;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.core.Ordered;
import org.springframework.http.client.SimpleClientHttpRequestFactory;
import org.springframework.web.client.RestTemplate;

@Configuration
public class AppConfig {

    /**
     * 调用算法层的 HTTP 客户端。
     *
     * <p>关闭请求体缓冲，上传大视频时采用流式写出，避免把整个文件读进 JVM 堆内存。
     */
    @Bean
    public RestTemplate algoRestTemplate(AppProperties properties) {
        SimpleClientHttpRequestFactory factory = new SimpleClientHttpRequestFactory();
        factory.setConnectTimeout((int) properties.getAlgo().getConnectTimeoutMs());
        factory.setReadTimeout((int) properties.getAlgo().getReadTimeoutMs());
        factory.setBufferRequestBody(false);
        return new RestTemplate(factory);
    }

    /**
     * {@code X-Server-Now} 回填。
     *
     * <p>注册成最外层过滤器（最高优先级），这样错误分派、CORS 预检、拦截器
     * 直接写出的 401/400 响应也都能带上这个头。
     */
    @Bean
    public FilterRegistrationBean<ServerTimeFilter> serverTimeFilter() {
        FilterRegistrationBean<ServerTimeFilter> registration =
                new FilterRegistrationBean<>(new ServerTimeFilter());
        registration.addUrlPatterns("/*");
        registration.setOrder(Ordered.HIGHEST_PRECEDENCE);
        return registration;
    }
}
