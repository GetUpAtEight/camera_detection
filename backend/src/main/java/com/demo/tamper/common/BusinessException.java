package com.demo.tamper.common;

/**
 * 业务异常，由 {@link GlobalExceptionHandler} 统一转换为 HTTP 4xx。
 */
public class BusinessException extends RuntimeException {

    private final int status;

    public BusinessException(String message) {
        this(400, message);
    }

    public BusinessException(int status, String message) {
        super(message);
        this.status = status;
    }

    public int getStatus() {
        return status;
    }

    public static BusinessException notFound(String message) {
        return new BusinessException(404, message);
    }
}
