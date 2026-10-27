package com.demo.tamper.config;

import lombok.Getter;
import lombok.Setter;
import org.springframework.boot.context.properties.ConfigurationProperties;

/**
 * app.* 配置绑定。
 *
 * <p>本系统只保留低频巡检，因此只有算法层地址这一组配置。
 */
@Getter
@Setter
@ConfigurationProperties(prefix = "app")
public class AppProperties {

    private Algo algo = new Algo();

    @Getter
    @Setter
    public static class Algo {
        /** 算法层地址，例如 http://127.0.0.1:8000（本地）或隧道 http://127.0.0.1:18010 */
        private String baseUrl = "http://127.0.0.1:8000";

        /** 建立连接超时（毫秒） */
        private long connectTimeoutMs = 5000;

        /**
         * 读取超时（毫秒）。
         *
         * <p>一次抽样要跑三层信号 + 参照物匹配，CPU 机器上约 0.2~2 秒；
         * 给足余量，避免慢机器上正常请求被误判成超时。
         */
        private long readTimeoutMs = 120000;
    }
}
