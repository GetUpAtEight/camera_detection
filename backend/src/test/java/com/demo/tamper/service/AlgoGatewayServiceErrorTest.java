package com.demo.tamper.service;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import com.demo.tamper.common.BusinessException;
import com.demo.tamper.config.AppProperties;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.test.web.client.MockRestServiceServer;
import org.springframework.test.web.client.response.MockRestResponseCreators;
import org.springframework.web.client.RestTemplate;

/**
 * {@code mapHttpError} 的三条契约：
 *
 * <ol>
 *   <li>4xx 把算法层的 {@code detail} 透给前端（可操作的原因）；
 *   <li>5xx 只回 502，**不回显算法层内部细节**（栈、路径、依赖版本）；
 *   <li>body 不是 JSON 时走兜底文案，而不是把原始 body 当消息抛出去。
 * </ol>
 */
class AlgoGatewayServiceErrorTest {

    private MockRestServiceServer server;
    private AlgoGatewayService gateway;

    @BeforeEach
    void setUp() {
        AppProperties properties = new AppProperties();
        properties.getAlgo().setBaseUrl("http://127.0.0.1:8000");
        RestTemplate restTemplate = new RestTemplate();
        server = MockRestServiceServer.bindTo(restTemplate).build();
        gateway = new AlgoGatewayService(restTemplate, new ObjectMapper(), properties);
    }

    private void respond(HttpStatus status, String body) {
        server.expect(org.springframework.test.web.client.ExpectedCount.once(),
                        org.springframework.test.web.client.match.MockRestRequestMatchers.anything())
                .andRespond(MockRestResponseCreators.withStatus(status)
                        .contentType(MediaType.APPLICATION_JSON)
                        .body(body));
    }

    @Test
    @DisplayName("4xx：透出 detail，状态码原样保留")
    void clientErrorPassesThroughDetail() {
        respond(HttpStatus.NOT_FOUND, "{\"detail\": \"机位 cam1 没有历史基线\"}");
        assertThatThrownBy(() -> gateway.sparseState("cam1"))
                .isInstanceOf(BusinessException.class)
                .hasMessage("机位 cam1 没有历史基线")
                .satisfies(e -> assertThat(((BusinessException) e).getStatus()).isEqualTo(404));
    }

    @Test
    @DisplayName("4xx 结构化 detail（FastAPI 422）：转成文本透出，不丢原因")
    void clientErrorStructuredDetail() {
        respond(HttpStatus.UNPROCESSABLE_ENTITY,
                "{\"detail\":[{\"loc\":[\"body\",\"landmarkId\"],\"msg\":\"field required\"}]}");
        assertThatThrownBy(() -> gateway.sparseGetLandmarks("cam1"))
                .isInstanceOf(BusinessException.class)
                .hasMessageContaining("field required")
                .satisfies(e -> assertThat(((BusinessException) e).getStatus()).isEqualTo(422));
    }

    @Test
    @DisplayName("4xx 无 detail：回兜底文案")
    void clientErrorWithoutDetailUsesFallback() {
        respond(HttpStatus.BAD_REQUEST, "{\"message\":\"boom\"}");
        assertThatThrownBy(() -> gateway.sparseState("cam1"))
                .isInstanceOf(BusinessException.class)
                .hasMessage("请求参数有误");
    }

    @Test
    @DisplayName("5xx：只回 502，绝不回显算法层内部细节")
    void serverErrorIsMaskedAsBadGateway() {
        respond(HttpStatus.INTERNAL_SERVER_ERROR,
                "{\"detail\":\"Traceback: /opt/algo/sparse/decide.py line 88 ZeroDivisionError\"}");
        assertThatThrownBy(() -> gateway.sparseState("cam1"))
                .isInstanceOf(BusinessException.class)
                .hasMessage("算法层处理失败（500）")
                .satisfies(e -> {
                    BusinessException be = (BusinessException) e;
                    assertThat(be.getStatus()).isEqualTo(502);
                    assertThat(be.getMessage()).doesNotContain("Traceback", "decide.py",
                            "ZeroDivisionError");
                });
    }

    @Test
    @DisplayName("5xx 非 JSON body：同样只回 502 兜底，不回显原始 body")
    void serverErrorNonJsonBodyIsMasked() {
        respond(HttpStatus.BAD_GATEWAY, "<html><body>proxy error: upstream 10.0.0.7</body></html>");
        assertThatThrownBy(() -> gateway.sparseState("cam1"))
                .isInstanceOf(BusinessException.class)
                .hasMessage("算法层处理失败（502）")
                .satisfies(e -> assertThat(((BusinessException) e).getMessage())
                        .doesNotContain("10.0.0.7"));
    }

    @Test
    @DisplayName("非 JSON body 的 4xx：兜底文案，不把 HTML 抛给前端")
    void clientErrorNonJsonBodyUsesFallback() {
        server.expect(org.springframework.test.web.client.ExpectedCount.once(),
                        org.springframework.test.web.client.match.MockRestRequestMatchers.anything())
                .andRespond(MockRestResponseCreators.withStatus(HttpStatus.BAD_REQUEST)
                        .contentType(MediaType.TEXT_HTML)
                        .body("<html>bad request</html>"));
        assertThatThrownBy(() -> gateway.sparseState("cam1"))
                .isInstanceOf(BusinessException.class)
                .hasMessage("请求参数有误");
    }
}