package com.demo.tamper.config;

import lombok.Getter;
import lombok.Setter;
import org.springframework.boot.context.properties.ConfigurationProperties;

/**
 * app.* 配置绑定。
 *
 * <p>本系统只保留低频巡检，因此只有算法层地址与访问令牌两组配置。
 */
@Getter
@Setter
@ConfigurationProperties(prefix = "app")
public class AppProperties {

    private Algo algo = new Algo();

    private Auth auth = new Auth();

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

    @Getter
    @Setter
    public static class Auth {
        /**
         * 访问令牌。环境变量 {@code APP_AUTH_TOKEN} 可覆盖本值。
         *
         * <p><b>留空 = 不鉴权（fail-open）</b>：启动后第一次请求会打一条 WARN 日志。
         * 这是为了本地演示能一键起，但部署前必须配置。
         */
        private String token = "";
    }
}
