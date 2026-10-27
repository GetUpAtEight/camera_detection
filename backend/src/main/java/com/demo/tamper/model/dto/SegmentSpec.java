package com.demo.tamper.model.dto;

/**
 * 一个检测分段（绝对毫秒时间窗）。
 *
 * <p>{@code type} 只在"按标注分段扫描"时给出来源类型（covered / defocused / moved），
 * 便于前端在表格里直接和标注对照；手动拖出来的区间可以不传。</p>
 */
public record SegmentSpec(String label, String type, Long startMs, Long endMs) {
}
