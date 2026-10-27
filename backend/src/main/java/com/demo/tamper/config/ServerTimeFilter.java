package com.demo.tamper.config;

import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import java.io.IOException;
import org.springframework.web.filter.OncePerRequestFilter;

/**
 * 给每个响应回填 {@code X-Server-Now}（服务端当前毫秒时间戳）。
 *
 * <p>前端可能跑在时钟不准的机器上（演示机、虚拟机时钟漂移）。缺 {@code tsMs} 时
 * 用它兜底，比用浏览器本地时间更接近算法层看到的"现在"。
 *
 * <p>时间戳在**链尾**（{@code doFilter} 返回后）才取：这样拿到的时刻离响应真正
 * 发出的时刻最近，也覆盖到异常/错误响应的回填路径。若响应已经提交（例如被
 * 容器提前 flush），加头会静默失败——不抛异常，宁可少一个头也不要打断响应。
 */
public class ServerTimeFilter extends OncePerRequestFilter {

    public static final String HEADER = "X-Server-Now";

    @Override
    protected void doFilterInternal(HttpServletRequest request, HttpServletResponse response,
                                    FilterChain chain) throws ServletException, IOException {
        try {
            chain.doFilter(request, response);
        } finally {
            if (!response.isCommitted()) {
                response.setHeader(HEADER, String.valueOf(System.currentTimeMillis()));
            }
        }
    }
}
