<template>
  <div class="timeline">
    <div class="track">
      <div class="track-base"></div>
      <div
        v-for="(event, index) in events"
        :key="index"
        class="segment"
        :style="segmentStyle(event)"
        :title="tooltip(event)"
      ></div>
      <div class="threshold-tick"></div>
    </div>
    <div class="axis">
      <span>0:00</span>
      <span>{{ formatClock(totalMs / 2) }}</span>
      <span>{{ formatClock(totalMs) }}</span>
    </div>
    <div v-if="events.length" class="event-list">
      <div v-for="(event, index) in events" :key="'row-' + index" class="event-row">
        <span class="index">{{ String(index + 1).padStart(2, '0') }}</span>
        <el-tag size="small" effect="light" :style="{ color: TAMPER_TYPE_COLOR[event.tamperType], background: TAMPER_TYPE_COLOR[event.tamperType] + '14', borderColor: 'transparent' }">
          {{ TAMPER_TYPE_TEXT[event.tamperType] }}
        </el-tag>
        <span class="range ctd-mono">{{ formatClock(event.startMs) }} → {{ formatClock(event.endMs) }}</span>
        <span class="meta">{{ event.frameCount }} 帧 · 峰值 {{ event.peakScore.toFixed(2) }}</span>
        <span class="algos">
          <el-tag v-for="algo in event.algorithms" :key="algo" size="small" effect="plain" type="info">
            {{ labelOf(algo) }}
          </el-tag>
        </span>
      </div>
    </div>
    <el-empty v-else description="未检测到篡改事件" :image-size="70" />
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import type { TamperEvent } from '@/types'
import { TAMPER_TYPE_COLOR, TAMPER_TYPE_TEXT, formatClock } from '@/utils/format'

const props = defineProps<{
  events: TamperEvent[]
  totalFrames: number
  totalMs: number
  labelMap?: Record<string, string>
}>()

const safeTotal = computed(() => Math.max(props.totalFrames, 1))
const useTime = computed(() => props.totalMs > 0)

function segmentStyle(event: TamperEvent) {
  // 事件里的帧号是原始帧号，而 totalFrames 是"采样帧数"，直接用帧号比例会把片段挤到最右边；
  // 因此有真实时长时统一按时间轴定位。
  const left = useTime.value
    ? (event.startMs / props.totalMs) * 100
    : (event.startFrame / safeTotal.value) * 100
  const width = useTime.value
    ? Math.max(1.2, ((event.endMs - event.startMs) / props.totalMs) * 100)
    : Math.max(1.2, ((event.endFrame - event.startFrame + 1) / safeTotal.value) * 100)
  const clampedLeft = Math.min(98, Math.max(0, left))
  return {
    left: `${clampedLeft}%`,
    width: `${Math.min(100 - clampedLeft, width)}%`,
    background: TAMPER_TYPE_COLOR[event.tamperType] || '#e5484d'
  }
}

function tooltip(event: TamperEvent): string {
  return `${TAMPER_TYPE_TEXT[event.tamperType]} · ${event.frameCount} 帧 · 峰值 ${event.peakScore}`
}

function labelOf(name: string): string {
  return props.labelMap?.[name] || name
}
</script>

<style scoped>
.timeline {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.track {
  position: relative;
  height: 26px;
  border-radius: 8px;
  overflow: hidden;
  background: #f4f6fa;
}

.track-base {
  position: absolute;
  inset: 0;
  background: repeating-linear-gradient(90deg, #eef1f6 0 1px, transparent 1px 5%);
}

.segment {
  position: absolute;
  top: 4px;
  bottom: 4px;
  border-radius: 4px;
  opacity: 0.88;
  box-shadow: 0 2px 6px rgba(229, 72, 77, 0.22);
}

.axis {
  display: flex;
  justify-content: space-between;
  font-size: 11px;
  color: #98a1b0;
  font-family: 'JetBrains Mono', Consolas, monospace;
}

.event-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin-top: 4px;
}

.event-row {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 12px;
  padding: 6px 8px;
  border-radius: 8px;
  background: #fafbfd;
}

.event-row .index {
  font-family: 'JetBrains Mono', Consolas, monospace;
  color: #98a1b0;
}

.event-row .range {
  color: #475467;
}

.event-row .meta {
  color: var(--ctd-text-secondary);
}

.event-row .algos {
  margin-left: auto;
  display: flex;
  gap: 4px;
  flex-wrap: wrap;
}
</style>
