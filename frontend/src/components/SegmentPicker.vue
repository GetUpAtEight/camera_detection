<template>
  <div class="ctd-card">
    <h3 class="ctd-card-title">
      <el-icon><Scissor /></el-icon> 检测区间
      <span class="sub">整段 / 拖动区间 / 按标注分段扫描——长视频不必从头跑到尾</span>
      <div class="head-right">
        <el-tag v-if="gtEvents.length" size="small" type="success" effect="plain">
          {{ gtEvents.length }} 段标注
        </el-tag>
        <el-upload :show-file-list="false" :auto-upload="false" accept=".py,.csv,.json,.txt" @change="onPickGt">
          <el-button size="small" :icon="Upload">导入标注</el-button>
        </el-upload>
        <el-button v-if="gtEvents.length" size="small" text @click="$emit('clear-gt')">清除</el-button>
      </div>
    </h3>

    <div class="mode-row">
      <el-radio-group v-model="mode" size="small">
        <el-radio-button value="full">整段</el-radio-button>
        <el-radio-button value="range">区间</el-radio-button>
        <el-radio-button value="segments" :disabled="!gtEvents.length">按标注分段</el-radio-button>
      </el-radio-group>
      <span class="ctd-muted hint">{{ modeHint }}</span>
    </div>

    <!-- 时间轴：标注色块 + 当前选择 -->
    <div class="lane">
      <div class="lane-base"></div>
      <div
        v-for="(gt, index) in gtEvents"
        :key="'gt-' + index"
        class="lane-gt"
        :style="laneStyle(gt.startMs, gt.endMs, TAMPER_TYPE_COLOR[gt.tamperType] || '#98a1b0', 0.35)"
        :title="`${typeText(gt.tamperType)} ${formatClock(gt.startMs)} → ${formatClock(gt.endMs)}`"
      ></div>
      <div v-if="mode === 'range'" class="lane-sel" :style="laneStyle(startMs, endMs, 'var(--ctd-primary)', 0.28)"></div>
      <div
        v-for="seg in activeSegments"
        :key="'sel-' + seg.startMs + '-' + seg.endMs"
        class="lane-sel"
        :style="laneStyle(seg.startMs, seg.endMs, 'var(--ctd-primary)', 0.32)"
      ></div>
      <div class="lane-axis">
        <span>0:00</span>
        <span>{{ formatClock(durationMs / 2) }}</span>
        <span>{{ formatClock(durationMs) }}</span>
      </div>
    </div>

    <div v-if="mode === 'range'" class="range-row">
      <div class="range-slider">
        <el-slider v-model="rangeSec" range :min="0" :max="durationSec" :step="1" :marks="marks" />
      </div>
      <div class="range-inputs">
        <el-input-number v-model="startSec" :min="0" :max="durationSec" :step="1" controls-position="right" size="small" />
        <span class="ctd-muted">→</span>
        <el-input-number v-model="endSec" :min="1" :max="durationSec" :step="1" controls-position="right" size="small" />
        <el-select v-model="jumpIndex" placeholder="跳到标注" size="small" clearable style="width: 190px" @change="applyJump">
          <el-option
            v-for="(gt, index) in gtEvents"
            :key="'jump-' + index"
            :value="index"
            :label="`${formatClock(gt.startMs)} ${typeText(gt.tamperType)}`"
          />
        </el-select>
      </div>
    </div>

    <div v-if="mode === 'segments'" class="seg-block">
      <div class="seg-tools">
        <el-select v-model="typeFilter" size="small" clearable placeholder="全部类型" style="width: 140px">
          <el-option v-for="t in types" :key="t" :value="t" :label="typeText(t)" />
        </el-select>
        <el-button size="small" text @click="selectAllRows">全选</el-button>
        <el-button size="small" text @click="clearRows">清空</el-button>
        <span class="ctd-muted">已选 {{ picked.length }} / {{ filtered.length }} 段</span>
      </div>
      <el-table
        ref="tableRef"
        :data="filtered"
        size="small"
        height="240"
        row-key="key"
        @selection-change="onSelectionChange"
      >
        <el-table-column type="selection" width="42" :reserve-selection="true" />
        <el-table-column label="类型" width="96">
          <template #default="{ row }">
            <el-tag
              size="small"
              effect="light"
              :style="{ color: TAMPER_TYPE_COLOR[row.tamperType], background: TAMPER_TYPE_COLOR[row.tamperType] + '14', borderColor: 'transparent' }"
            >
              {{ typeText(row.tamperType) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="区间" width="180">
          <template #default="{ row }">
            <span class="ctd-mono">{{ formatClock(row.startMs) }} → {{ formatClock(row.endMs) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="时长" width="90">
          <template #default="{ row }">
            <span class="ctd-mono">{{ ((row.endMs - row.startMs) / 1000).toFixed(1) }}s</span>
          </template>
        </el-table-column>
        <el-table-column label="预估帧数" width="100">
          <template #default="{ row }">
            <span class="ctd-mono">{{ estimateFrames(row.startMs, row.endMs) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="标注来源" min-width="140" show-overflow-tooltip>
          <template #default="{ row }">
            <span class="ctd-muted">{{ row.source || '-' }}</span>
          </template>
        </el-table-column>
      </el-table>
    </div>

    <div class="estimate">
      <span>
        本次预计 <b>{{ totalFrames }}</b> 帧
        <template v-if="mode === 'segments'">（{{ picked.length }} 段）</template>
        · 采样 {{ sampleFps }} fps
        · 约 {{ estimateSeconds }} 秒
      </span>
      <span class="ctd-muted">
        每段自动取「段首前 {{ refGapMs / 1000 }}s」一帧作参考，避免用段内首帧（已经糊了）当镜子
      </span>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { Scissor, Upload } from '@element-plus/icons-vue'
import type { GroundTruthEventItem, MediaItem, SegmentPlan, SegmentSpec } from '@/types'
import { TAMPER_TYPE_COLOR, TAMPER_TYPE_TEXT, formatClock } from '@/utils/format'

const props = defineProps<{
  media: MediaItem | null
  gtEvents: GroundTruthEventItem[]
  sampleFps: number
  algorithmCount: number
  refGapMs?: number
}>()

const emit = defineEmits<{
  (e: 'update:plan', plan: SegmentPlan): void
  (e: 'pick-gt', file: File): void
  (e: 'clear-gt'): void
}>()

const refGapMs = computed(() => props.refGapMs ?? 1000)
const mode = ref<'full' | 'range' | 'segments'>('full')
const durationMs = computed(() => props.media?.durationMs || 0)
const durationSec = computed(() => Math.max(1, Math.floor(durationMs.value / 1000)))

const rangeSec = ref<[number, number]>([0, durationSec.value])
const startSec = computed({
  get: () => rangeSec.value[0],
  set: (v: number) => (rangeSec.value = [v, Math.max(v + 1, rangeSec.value[1])])
})
const endSec = computed({
  get: () => rangeSec.value[1],
  set: (v: number) => (rangeSec.value = [Math.min(v - 1, rangeSec.value[0]), v])
})
const startMs = computed(() => startSec.value * 1000)
const endMs = computed(() => Math.min(endSec.value, durationSec.value) * 1000)

const jumpIndex = ref<number | null>(null)
const typeFilter = ref('')
const picked = ref<Row[]>([])
const tableRef = ref<any>(null)

interface Row extends GroundTruthEventItem {
  key: string
}

const rows = computed<Row[]>(() =>
  props.gtEvents.map((gt, index) => ({
    ...gt,
    key: `${gt.startMs}-${gt.endMs}-${gt.tamperType}-${index}`
  }))
)
const types = computed(() => Array.from(new Set(rows.value.map((r) => r.tamperType))))
const filtered = computed(() =>
  typeFilter.value ? rows.value.filter((r) => r.tamperType === typeFilter.value) : rows.value
)

const labelOf = (gt: GroundTruthEventItem) =>
  `${formatClock(gt.startMs)}-${formatClock(gt.endMs)} ${typeText(gt.tamperType)}`

const activeSegments = computed<SegmentSpec[]>(() => {
  if (mode.value !== 'segments') return []
  return picked.value.map((row) => ({
    label: labelOf(row),
    type: row.tamperType,
    startMs: row.startMs,
    endMs: row.endMs
  }))
})

const modeHint = computed(() => {
  if (mode.value === 'full') return '从视频开头跑到结尾（受「最多处理帧数」限制）'
  if (mode.value === 'range') return '只跑选中的一段，段前自动留参考帧'
  return '逐段检测，每段独立参考帧，适合按人工标注批量核验'
})

function typeText(type: string | null | undefined): string {
  if (!type) return '未标注'
  return TAMPER_TYPE_TEXT[type] || type
}

function laneStyle(start: number, end: number, color: string, alpha: number) {
  const total = Math.max(durationMs.value, 1)
  const left = Math.min(99, Math.max(0, (start / total) * 100))
  const width = Math.max(0.6, ((end - start) / total) * 100)
  return {
    left: `${left}%`,
    width: `${Math.min(100 - left, width)}%`,
    background: color,
    opacity: alpha
  }
}

const marks = computed(() => {
  if (!props.gtEvents.length || durationSec.value > 3600) return {}
  const result: Record<number, string> = {}
  for (const gt of props.gtEvents) {
    const sec = Math.round(gt.startMs / 1000)
    if (sec > 0 && sec < durationSec.value) result[sec] = ''
  }
  return result
})

function estimateFrames(start: number, end: number): number {
  const seconds = Math.max(0, (end - start) / 1000)
  return Math.max(1, Math.ceil(seconds * props.sampleFps))
}

const totalFrames = computed(() => {
  if (mode.value === 'full') return Math.ceil((durationMs.value / 1000) * props.sampleFps)
  if (mode.value === 'range') return estimateFrames(startMs.value, endMs.value)
  return picked.value.reduce((sum, row) => sum + estimateFrames(row.startMs, row.endMs), 0)
})

/** 粗估：单帧单算法约 6ms（ORB 最慢，实测 8~12ms），另加传输与落库开销 */
const estimateSeconds = computed(() =>
  Math.max(1, Math.round((totalFrames.value * Math.max(props.algorithmCount, 1) * 6) / 1000))
)

function applyJump(index: number | null) {
  if (index === null || index === undefined) return
  const gt = props.gtEvents[index]
  if (!gt) return
  const pad = Math.ceil(refGapMs.value / 1000)
  const from = Math.max(0, Math.floor(gt.startMs / 1000) - pad)
  const to = Math.min(durationSec.value, Math.ceil(gt.endMs / 1000) + pad)
  rangeSec.value = [from, Math.max(from + 1, to)]
}

function onSelectionChange(selection: Row[]) {
  picked.value = selection
}

function selectAllRows() {
  if (!tableRef.value) return
  tableRef.value.clearSelection()
  filtered.value.forEach((row) => tableRef.value.toggleRowSelection(row, true))
}

function clearRows() {
  tableRef.value?.clearSelection()
  picked.value = []
}

function onPickGt(uploadFile: any) {
  const file = uploadFile?.raw as File | undefined
  if (!file) return
  emit('pick-gt', file)
}

// 媒体切换：重置区间与分段选择
watch(
  () => props.media?.id,
  () => {
    rangeSec.value = [0, durationSec.value]
    jumpIndex.value = null
    picked.value = []
    tableRef.value?.clearSelection()
    mode.value = 'full'
  }
)

watch(
  () => props.gtEvents,
  (list) => {
    if (!list.length) {
      if (mode.value === 'segments') mode.value = 'full'
      return
    }
    // 导入标注后默认全选，点一下就能按标注批量扫描
    if (mode.value === 'segments') {
      setTimeout(selectAllRows, 0)
    }
  },
  { deep: false }
)

watch(mode, (value) => {
  if (value === 'segments') {
    if (!props.gtEvents.length) {
      ElMessage.warning('还没有标注区间，请先导入 baseline.py 或 CSV')
      mode.value = 'full'
      return
    }
    setTimeout(selectAllRows, 0)
  }
})

watch(
  [mode, startMs, endMs, picked, () => props.refGapMs],
  () => {
    emit('update:plan', {
      mode: mode.value,
      startMs: mode.value === 'range' ? startMs.value : 0,
      endMs: mode.value === 'range' ? endMs.value : durationMs.value,
      segments:
        mode.value === 'segments'
          ? activeSegments.value
          : []
    })
  },
  { deep: true, immediate: true }
)
</script>

<style scoped>
.head-right {
  margin-left: auto;
  display: flex;
  align-items: center;
  gap: 8px;
}

.mode-row {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}

.hint {
  font-size: 12px;
}

.lane {
  position: relative;
  height: 44px;
  margin: 18px 0 6px;
  padding-top: 12px;
}

.lane-base {
  position: absolute;
  left: 0;
  right: 0;
  top: 12px;
  height: 16px;
  border-radius: 4px;
  background: #eef1f7;
}

.lane-gt,
.lane-sel {
  position: absolute;
  top: 12px;
  height: 16px;
  border-radius: 3px;
}

.lane-sel {
  top: 9px;
  height: 22px;
  border: 1.5px solid var(--ctd-primary);
  box-sizing: border-box;
}

.lane-axis {
  position: absolute;
  left: 0;
  right: 0;
  bottom: 0;
  display: flex;
  justify-content: space-between;
  font-size: 11px;
  color: #98a1b0;
  font-variant-numeric: tabular-nums;
}

.range-row {
  display: flex;
  align-items: center;
  gap: 20px;
  flex-wrap: wrap;
  margin-top: 10px;
}

.range-slider {
  flex: 1 1 360px;
  min-width: 260px;
  padding: 0 8px;
}

.range-inputs {
  display: flex;
  align-items: center;
  gap: 8px;
}

.range-inputs :deep(.el-input-number) {
  width: 110px;
}

.seg-block {
  margin-top: 10px;
}

.seg-tools {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 8px;
}

.estimate {
  display: flex;
  justify-content: space-between;
  gap: 16px;
  flex-wrap: wrap;
  margin-top: 12px;
  padding-top: 10px;
  border-top: 1px dashed var(--ctd-border);
  font-size: 12px;
  color: var(--ctd-text-secondary);
}

.estimate b {
  color: var(--ctd-primary);
  font-size: 14px;
}
</style>
