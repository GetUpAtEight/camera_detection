package com.demo.tamper.model.entity;

public enum TaskStatus {
    PENDING,
    RUNNING,
    DONE,
    FAILED,
    CANCELLED;

    public boolean isFinished() {
        return this == DONE || this == FAILED || this == CANCELLED;
    }
}
