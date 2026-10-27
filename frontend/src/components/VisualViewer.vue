<template>
  <div class="visual-viewer">
    <div v-if="!algorithms.length" class="ctd-empty">暂无中间过程可视化图</div>
    <template v-else>
      <div class="toolbar">
        <el-radio-group v-model="selectedAlgorithm" size="small">
          <el-radio-button v-for="algo in algorithms" :key="algo.name" :value="algo.name">
            {{ algo.label }}
          </el-radio-button>
        </el-radio-group>
        <el-select v-model="selectedFrame" size="small" style="width: 190px" placeholder="选择帧">
          <el-option
            v-for="item in frames"
            :key="item.frameIndex"
            :label="`帧 ${item.frameIndex} · ${formatClock(item.timestampMs)}`"
            :value="item.frameIndex"
          />
        </el-select>
      </div>

      <div class="canvas-wrap">
        <el-image
          v-if="current"
          :src="src"
          :preview-src-list="[src]"
          fit="contain"
          class="visual-img"
          preview-teleported
        />
        <div v-else class="ctd-empty">该算法没有可视化帧</div>
      </div>

      <div v-if="current" class="meta-row">
        <el-tag size="small" :type="current.tampered ? 'danger' : 'success'" effect="light">
          {{ current.tampered ? TAMPER_TYPE_TEXT[current.tamperType] : '正常' }}
        </el-tag>
        <span>异常分 <b class="ctd-mono">{{ current.score.toFixed(2) }}</b></span>
        <span class="ctd-muted">{{ current.note }}</span>
      </div>

      <el-collapse v-if="detailEntries.length" class="detail-collapse">
        <el-collapse-item title="中间量（detail）" name="detail">
          <div class="detail-grid">
            <div v-for="entry in detailEntries" :key="entry.key" class="detail-item">
              <span class="k">{{ entry.key }}</span>
              <span class="v">{{ entry.value }}</span>
            </div>
          </div>
        </el-collapse-item>
      </el-collapse>
    </template>
  </div>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import type { FrameResultItem } from '@/types'
import { detectionApi } from '@/api'
import { TAMPER_TYPE_TEXT, algoShort, formatClock } from '@/utils/format'

const props = defineProps<{
  taskId: string
  results: FrameResultItem[]
  labelMap?: Record<string, string>
}>()

const selectedAlgorithm = ref('')
const selectedFrame = ref<number | null>(null)

const algorithms = computed(() => {
  const names = new Set<string>()
  for (const item of props.results) {
    if (item.visualUrl) names.add(item.algorithm)
  }
  return Array.from(names).map((name) => ({ name, label: algoShort(name, props.labelMap?.[name]) }))
})

const frames = computed(() =>
  props.results
    .filter((item) => item.algorithm === selectedAlgorithm.value && item.visualUrl)
    .sort((a, b) => a.frameIndex - b.frameIndex)
)

const current = computed(
  () => frames.value.find((item) => item.frameIndex === selectedFrame.value) || frames.value[0] || null
)

const src = computed(() =>
  current.value ? detectionApi.visualUrl(props.taskId, current.value.algorithm, current.value.frameIndex) : ''
)

const detailEntries = computed(() => {
  const detail = current.value?.detail || {}
  return Object.entries(detail)
    .filter(([key, value]) => !key.startsWith('_') && !(Array.isArray(value) && value.length > 8))
    .map(([key, value]) => ({ key, value: typeof value === 'object' ? JSON.stringify(value) : String(value) }))
})

watch(
  algorithms,
  (list) => {
    if (!list.some((a) => a.name === selectedAlgorithm.value)) {
      selectedAlgorithm.value = list[0]?.name || ''
    }
  },
  { immediate: true }
)

watch(selectedAlgorithm, () => {
  selectedFrame.value = frames.value[0]?.frameIndex ?? null
})

watch(frames, (list) => {
  if (list.length && selectedFrame.value === null) {
    selectedFrame.value = list[0].frameIndex
  }
})

watch(
  () => props.results,
  () => {
    if (frames.value.length && !frames.value.some((f) => f.frameIndex === selectedFrame.value)) {
      selectedFrame.value = frames.value[0].frameIndex
    }
  }
)
</script>

<style scoped>
.visual-viewer {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.toolbar {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}

.canvas-wrap {
  background: #0d1117;
  border-radius: 10px;
  min-height: 220px;
  display: flex;
  align-items: center;
  justify-content: center;
  overflow: hidden;
}

.visual-img {
  width: 100%;
  max-height: 420px;
}

.meta-row {
  display: flex;
  align-items: center;
  gap: 12px;
  font-size: 12px;
  color: #475467;
  flex-wrap: wrap;
}

.detail-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
  gap: 6px 16px;
}

.detail-item {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  font-size: 12px;
  padding: 4px 0;
  border-bottom: 1px dashed #f0f2f7;
}

.detail-item .k {
  color: var(--ctd-text-secondary);
}

.detail-item .v {
  font-family: 'JetBrains Mono', Consolas, monospace;
  color: var(--ctd-text);
  word-break: break-all;
  text-align: right;
}
</style>
