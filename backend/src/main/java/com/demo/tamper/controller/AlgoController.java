package com.demo.tamper.controller;

import com.demo.tamper.common.ApiResponse;
import com.demo.tamper.model.dto.AlgorithmMeta;
import com.demo.tamper.service.AlgoGatewayService;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

/**
 * 算法层状态透传：前端据此渲染算法卡片、动态参数表单，并感知算法服务是否在线。
 */
@RestController
@RequestMapping("/api/algo")
public class AlgoController {

    private static final Logger log = LoggerFactory.getLogger(AlgoController.class);

    private final AlgoGatewayService gateway;

    public AlgoController(AlgoGatewayService gateway) {
        this.gateway = gateway;
    }

    @GetMapping("/algorithms")
    public ApiResponse<Map<String, Object>> algorithms() {
        Map<String, Object> payload = new LinkedHashMap<>();
        payload.put("baseUrl", gateway.getBaseUrl());
        payload.put("thresholdScore", 0.5);
        try {
            List<AlgorithmMeta> algorithms = gateway.algorithms();
            payload.put("online", true);
            payload.put("algorithms", algorithms);
        } catch (RuntimeException e) {
            log.warn("算法层不可用: {}", e.getMessage());
            payload.put("online", false);
            payload.put("error", e.getMessage());
            payload.put("algorithms", List.of());
        }
        return ApiResponse.ok(payload);
    }

    @GetMapping("/health")
    public ApiResponse<Map<String, Object>> health() {
        Map<String, Object> payload = new LinkedHashMap<>();
        payload.put("baseUrl", gateway.getBaseUrl());
        try {
            payload.put("online", true);
            payload.put("detail", gateway.health());
        } catch (RuntimeException e) {
            payload.put("online", false);
            payload.put("error", e.getMessage());
        }
        return ApiResponse.ok(payload);
    }
}
