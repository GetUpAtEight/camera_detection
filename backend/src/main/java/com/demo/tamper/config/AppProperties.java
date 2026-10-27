package com.demo.tamper.config;

import lombok.Getter;
import lombok.Setter;
import org.springframework.boot.context.properties.ConfigurationProperties;

/**
 * app.* 配置绑定。
 */
@Getter
@Setter
@ConfigurationProperties(prefix = "app")
public class AppProperties {

    private Algo algo = new Algo();
    private Storage storage = new Storage();
    private Detection detection = new Detection();

    @Getter
    @Setter
    public static class Algo {
        /** 算法层地址，例如 http://127.0.0.1:8000 */
        private String baseUrl = "http://127.0.0.1:8000";
        private long connectTimeoutMs = 5000;
        private long readTimeoutMs = 1800000;
        /** 轮询算法层视频任务状态的间隔 */
        private long pollIntervalMs = 800;
        /** 单个检测任务最长等待时间 */
        private long maxPollMinutes = 60;
    }

    @Getter
    @Setter
    public static class Storage {
        private String uploadDir = "./data/uploads";
        private String visualDir = "./data/visuals";
        private long maxFileSizeMb = 512;
    }

    @Getter
    @Setter
    public static class Detection {
        private double sampleFps = 5;
        private int maxFrames = 600;
        private int corePoolSize = 4;
        private int maxPoolSize = 8;
        private int queueCapacity = 50;
    }
}
