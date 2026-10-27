package com.demo.tamper.service;

import static org.assertj.core.api.Assertions.assertThat;

import com.demo.tamper.config.AppProperties;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.net.URI;
import java.util.function.Consumer;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.springframework.http.HttpMethod;
import org.springframework.http.MediaType;
import org.springframework.http.client.ClientHttpRequest;
import org.springframework.test.web.client.ExpectedCount;
import org.springframework.test.web.client.MockRestServiceServer;
import org.springframework.test.web.client.RequestMatcher;
import org.springframework.test.web.client.response.MockRestResponseCreators;
import org.springframework.web.client.RestTemplate;

/**
 * #2 回归：cameraId / templateId / actor 含有路径或查询分隔符时必须被编码。
 *
 * <p>断言的是**实际发出的 URI**，而不是"方法没抛异常"——裸拼接时代
 * {@code cameraId="a/b"} 会变成两段路径，服务端 404，但客户端代码看起来一切正常。
 */
class AlgoGatewayServiceUriTest {

    private static final String BASE = "http://127.0.0.1:8000";

    private MockRestServiceServer server;
    private AlgoGatewayService gateway;

    @BeforeEach
    void setUp() {
        AppProperties properties = new AppProperties();
        properties.getAlgo().setBaseUrl(BASE);
        RestTemplate restTemplate = new RestTemplate();
        server = MockRestServiceServer.bindTo(restTemplate).build();
        gateway = new AlgoGatewayService(restTemplate, new ObjectMapper(), properties);
    }

    @AfterEach
    void tearDown() {
        server.verify();
    }

    /**
     * 登记一次期望请求：校验方法与已编码路径，并用实际 URI 回调断言。
     *
     * @param pathValidator 接收实际 URI（{@link URI} 的 raw 形式保留 %XX）
     */
    private void expect(HttpMethod method, String expectedRawPath, Consumer<URI> pathValidator) {
        RequestMatcher matcher = (ClientHttpRequest request) -> {
            URI uri = request.getURI();
            assertThat(request.getMethod()).isEqualTo(method);
            assertThat(uri.getRawPath()).isEqualTo(expectedRawPath);
            pathValidator.accept(uri);
        };
        server.expect(ExpectedCount.once(), matcher)
                .andRespond(MockRestResponseCreators.withSuccess("{}", MediaType.APPLICATION_JSON));
    }

    @Test
    @DisplayName("cameraId 含 / 空格 中文：编码成单段路径")
    void stateEncodesCameraId() {
        expect(HttpMethod.GET, "/sparse/state/cam%2F1%20%E4%B8%AD%20%E6%96%87",
                uri -> assertThat(uri.getRawQuery()).isNull());
        gateway.sparseState("cam/1 中 文");
    }

    @Test
    @DisplayName("删除模板：templateId 编码，actor 作为单个查询参数编码（& = 空格 中文）")
    void removeTemplateEncodesTemplateIdAndActor() {
        expect(HttpMethod.DELETE, "/sparse/landmarks/cam1/templates/tpl%26x%3D1", uri -> {
            assertThat(uri.getRawQuery())
                    .isEqualTo("actor=%E5%BC%A0%E4%B8%89%26force%3D1%20%E5%A4%87%E6%B3%A8");
            // 关键回归点：actor 里的 & 若没编码，raw query 会被切成两个参数
            assertThat(uri.getRawQuery().split("&")).hasSize(1);
            assertThat(uri.getRawQuery()).contains("%26", "%3D");
        });
        gateway.sparseRemoveTemplate("cam1", "tpl&x=1", "张三&force=1 备注");
    }

    @Test
    @DisplayName("actor 为空时仍保留 actor= 参数，保持既有调用契约")
    void removeTemplateKeepsEmptyActor() {
        expect(HttpMethod.DELETE, "/sparse/landmarks/cam1/templates/tpl1",
                uri -> assertThat(uri.getRawQuery()).isEqualTo("actor="));
        gateway.sparseRemoveTemplate("cam1", "tpl1", null);
    }

    @Test
    @DisplayName("模板预览：两个变量都编码，路径段数量不变")
    void templatePreviewEncodesBothIds() {
        expect(HttpMethod.GET, "/sparse/templates/a%2Fb/c%20d/preview", uri -> { });
        gateway.sparseTemplatePreview("a/b", "c d");
    }

    @Test
    @DisplayName("缩略图：cameraId 编码，数字段原样")
    void thumbEncodesCameraId() {
        expect(HttpMethod.GET, "/sparse/thumb/cam%201/2/3", uri -> { });
        gateway.sparseThumb("cam 1", 2, 3);
    }

    @Test
    @DisplayName("reset 端点编码")
    void resetEncodes() {
        expect(HttpMethod.POST, "/sparse/state/cam%201/reset", uri -> { });
        gateway.sparseReset("cam 1");
    }

    @Test
    @DisplayName("delete state 端点编码")
    void deleteStateEncodes() {
        expect(HttpMethod.DELETE, "/sparse/state/cam%201", uri -> { });
        gateway.sparseDelete("cam 1");
    }

    @Test
    @DisplayName("get landmarks 端点编码")
    void getLandmarksEncodes() {
        expect(HttpMethod.GET, "/sparse/landmarks/cam%201", uri -> { });
        gateway.sparseGetLandmarks("cam 1");
    }

    @Test
    @DisplayName("clear landmarks 端点编码")
    void clearLandmarksEncodes() {
        expect(HttpMethod.DELETE, "/sparse/landmarks/cam%201", uri -> { });
        gateway.sparseClearLandmarks("cam 1");
    }

    @Test
    @DisplayName("list templates 端点编码")
    void listTemplatesEncodes() {
        expect(HttpMethod.GET, "/sparse/landmarks/cam%201/templates", uri -> { });
        gateway.sparseListTemplates("cam 1");
    }

    @Test
    @DisplayName("无变量端点不受影响")
    void plainEndpointsUnchanged() {
        expect(HttpMethod.GET, "/sparse/meta", uri -> { });
        gateway.sparseMeta();
    }
}