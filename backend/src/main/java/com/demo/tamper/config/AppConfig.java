package com.demo.tamper.config;

import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
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
}
