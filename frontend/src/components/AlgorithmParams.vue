<template>
  <div class="algo-sections">
    <section v-for="group in groups" :key="group.key" class="algo-section">
      <div v-if="group.title" class="group-head">
        <span class="group-title">{{ group.title }}</span>
        <span v-if="group.hint" class="group-hint">{{ group.hint }}</span>
      </div>

      <div class="algo-grid">
        <div
          v-for="algo in group.items"
          :key="algo.name"
          class="algo-card"
          :class="{
            active: isSelected(algo.name),
            muted: !algo.available && algo.kind === 'yolo',
            legacy: algo.kind === 'legacy'
          }"
        >
          <div class="algo-head">
            <el-checkbox :model-value="isSelected(algo.name)" @change="toggle(algo)" />
            <div class="algo-title">
              <span class="name">{{ algo.displayName }}</span>
              <div class="tags">
                <el-tag
                  v-for="t in algo.tamperTypes.filter((x) => x !== 'normal')"
                  :key="t"
                  size="small"
                  effect="plain"
                  :style="{ color: TAMPER_TYPE_COLOR[t], borderColor: TAMPER_TYPE_COLOR[t] + '55' }"
                >
                  {{ TAMPER_TYPE_TEXT[t] }}
                </el-tag>
                <el-tag v-if="algo.requiresReference" size="small" type="info" effect="plain">需参考帧</el-tag>
                <el-tag v-if="algo.kind === 'legacy'" size="small" type="info" effect="dark">辅助</el-tag>
                <el-tag v-if="algo.kind === 'yolo'" size="small" type="warning" effect="plain">GPU 模型</el-tag>
                <el-tag v-if="!algo.available && algo.kind === 'yolo'" size="small" type="danger" effect="plain">
                  模型未就绪
                </el-tag>
              </div>
            </div>
          </div>

          <p class="algo-desc">{{ algo.description }}</p>

          <div v-if="isSelected(algo.name)" class="param-toggle">
            <el-button size="small" text type="primary" @click="toggleExpand(algo.name)">
              <el-icon><component :is="expanded.has(algo.name) ? 'ArrowUp' : 'Setting'" /></el-icon>
              {{ expanded.has(algo.name) ? '收起参数' : `调整参数（${algo.params.length}）` }}
            </el-button>
          </div>

          <div v-if="isSelected(algo.name) && expanded.has(algo.name)" class="algo-params">
            <div v-for="param in algo.params" :key="param.key" class="param-row">
              <div class="param-label">
                <span>{{ param.label }}<em v-if="param.unit">（{{ param.unit }}）</em></span>
              </div>
              <el-slider
                :model-value="params[algo.name]?.[param.key] ?? param.default"
                :min="param.min"
                :max="param.max"
                :step="param.step || 1"
                show-input
                :show-input-controls="false"
                size="small"
                @update:model-value="(value: number) => updateParam(algo.name, param.key, value)"
              />
              <div v-if="param.description" class="param-tip">{{ param.description }}</div>
            </div>
            <div v-if="!algo.params.length" class="param-tip">该算法没有可调参数</div>
          </div>
        </div>
      </div>
    </section>
  </div>
</template>

<script setup lang="ts">
import { computed, reactive } from 'vue'
import type { AlgorithmMeta } from '@/types'
import { TAMPER_TYPE_COLOR, TAMPER_TYPE_TEXT } from '@/utils/format'

const props = defineProps<{
  algorithms: AlgorithmMeta[]
  selected: string[]
  params: Record<string, Record<string, number>>
}>()

const emit = defineEmits<{
  (e: 'update:selected', value: string[]): void
  (e: 'update:params', value: Record<string, Record<string, number>>): void
}>()

/** 默认折叠参数，避免勾选多个算法后页面被参数表单淹没 */
const expanded = reactive(new Set<string>())

/**
 * 分三组展示：主力（参与投票）→ 辅助（不投票，按场景选用）→ 深度学习。
 * 辅助算法来自 G:/project/算法 的外部实现，卡片描述首句即「适用场景」。
 */
const groups = computed(() => {
  const classic = props.algorithms.filter((a) => a.kind !== 'legacy' && a.kind !== 'yolo')
  const aux = props.algorithms.filter((a) => a.kind === 'legacy')
  const gpu = props.algorithms.filter((a) => a.kind === 'yolo')
  return [
    {
      key: 'classic',
      title: '主力算法',
      hint: '默认全选，≥2 个算法同帧告警才算篡改',
      items: classic
    },
    {
      key: 'aux',
      title: '辅助算法',
      hint: '按场景勾选，只出独立指标，不参与投票（避免同源信号放大误差）',
      items: aux
    },
    { key: 'gpu', title: '深度学习', hint: '', items: gpu }
  ].filter((g) => g.items.length > 0)
})

function isSelected(name: string): boolean {
  return props.selected.includes(name)
}

function toggleExpand(name: string) {
  if (expanded.has(name)) {
    expanded.delete(name)
  } else {
    expanded.add(name)
  }
}

function toggle(algo: AlgorithmMeta) {
  const next = isSelected(algo.name)
    ? props.selected.filter((n) => n !== algo.name)
    : [...props.selected, algo.name]
  emit('update:selected', next)
  if (!isSelected(algo.name) && !props.params[algo.name]) {
    const defaults: Record<string, number> = {}
    for (const param of algo.params) defaults[param.key] = param.default
    emit('update:params', { ...props.params, [algo.name]: defaults })
  }
}

function updateParam(algorithm: string, key: string, value: number) {
  const next = { ...props.params, [algorithm]: { ...(props.params[algorithm] || {}), [key]: value } }
  emit('update:params', next)
}
</script>

<style scoped>
.algo-sections {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.group-head {
  display: flex;
  align-items: baseline;
  gap: 8px;
  margin-bottom: 8px;
  padding-left: 2px;
}

.group-title {
  font-size: 13px;
  font-weight: 600;
  color: #344054;
}

.group-title::before {
  content: '';
  display: inline-block;
  width: 3px;
  height: 12px;
  margin-right: 6px;
  border-radius: 2px;
  background: var(--ctd-primary, #2f6fed);
  vertical-align: -1px;
}

.group-hint {
  font-size: 11px;
  color: #98a1b0;
}

.algo-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
  gap: 12px;
}

.algo-card {
  border: 1px solid var(--ctd-border);
  border-radius: 10px;
  padding: 12px 14px;
  transition: all 0.18s ease;
  background: #fff;
}

.algo-card.active {
  border-color: rgba(47, 111, 237, 0.45);
  box-shadow: 0 0 0 3px rgba(47, 111, 237, 0.08);
}

.algo-card.legacy {
  background: #fcfcfd;
}

.algo-card.muted {
  opacity: 0.72;
}

.algo-head {
  display: flex;
  gap: 8px;
  align-items: flex-start;
}

.algo-title {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.algo-title .name {
  font-size: 14px;
  font-weight: 600;
}

.tags {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}

.algo-desc {
  margin: 8px 0 0;
  font-size: 12px;
  line-height: 1.65;
  color: var(--ctd-text-secondary);
}

.algo-params {
  margin-top: 6px;
  padding-top: 10px;
  border-top: 1px dashed var(--ctd-border);
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.param-toggle {
  margin-top: 4px;
}

.param-toggle :deep(.el-button) {
  padding: 2px 0;
  font-size: 12px;
}

.param-row {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.param-label {
  display: flex;
  justify-content: space-between;
  font-size: 12px;
  color: #475467;
}

.param-label em {
  font-style: normal;
  color: #98a1b0;
}

.param-tip {
  font-size: 11px;
  color: #98a1b0;
  line-height: 1.5;
}

:deep(.el-slider) {
  --el-slider-height: 4px;
}

:deep(.el-slider__input) {
  width: 76px;
}
</style>
