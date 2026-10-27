package com.demo.tamper.controller;

import com.demo.tamper.common.ApiResponse;
import com.demo.tamper.service.AlgoGatewayService;
import java.util.LinkedHashMap;
import java.util.Map;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

/**
 * 算法层状态透传：前端据此判断"低频巡检算法服务是否在线"。
 *
 * <p>本系统只保留低频巡检，算法层没有"算法清单"概念，因此只保留健康检查。
 */
@RestController
@RequestMapping("/api/algo")
public class AlgoController {

    private static final Logger log = LoggerFactory.getLogger(AlgoController.class);

    private final AlgoGatewayService gateway;

    public AlgoController(AlgoGatewayService gateway) {
        this.gateway = gateway;
    }

    @GetMapping("/health")
    public ApiResponse<Map<String, Object>> health() {
        Map<String, Object> payload = new LinkedHashMap<>();
        payload.put("baseUrl", gateway.getBaseUrl());
        try {
            payload.put("online", true);
            payload.put("detail", gateway.health());
        } catch (RuntimeException e) {
            log.warn("算法层不可用：{}", e.getMessage());
            payload.put("online", false);
            payload.put("error", e.getMessage());
        }
        return ApiResponse.ok(payload);
    }
}
