package com.demo.tamper;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.boot.context.properties.ConfigurationPropertiesScan;

/**
 * 低频巡检后端入口。
 *
 * <p>只做请求编排与透传：判定逻辑全在 Python 算法层，后端不持有业务状态
 * （原先引出的 H2/JPA 随之移除，避免空密码数据库控制台暴露）。
 */
@ConfigurationPropertiesScan
@SpringBootApplication
public class TamperApplication {

    public static void main(String[] args) {
        SpringApplication.run(TamperApplication.class, args);
    }
}
