<template>
  <el-dialog v-model="dialogVisible" title="标定参照物（静止结构）" width="920px" destroy-on-close>
    <div class="lm-tip">
      在画面上框选<strong>不会自己移动</strong>的人造结构：大门、楼角、围墙、灯杆基座。
      不要选树叶、旗帜、水面、广告布（会被风吹动），也不要选车辆/行人（会移动）。
      建议标 2~4 个，分布在不同位置。
      判据是"多个参照物是否<strong>一致整体位移</strong>"——只有 1 个动了，说明是那个位置上的东西变了，不是机位变了。
    </div>
    <div class="lm-toolbar">
      <el-radio-group v-model="mode" size="small">
        <el-radio-button value="landmark">参照物（判机位移动）</el-radio-button>
        <el-radio-button value="ignore">忽略区（不判变化）</el-radio-button>
      </el-radio-group>
      <el-button size="small" type="primary" plain @click="onCapture">取当前画面</el-button>
      <el-button size="small" :disabled="!rects.length" @click="undo">撤销上一个</el-button>
      <el-button size="small" type="danger" plain :disabled="!rects.length" @click="clearRects">
        清空列表
      </el-button>
    </div>
    <div class="hint" style="margin-bottom: 8px">
      当前模式：<b>{{ mode === 'landmark' ? '参照物' : '忽略区' }}</b> —
      {{
        mode === 'landmark'
          ? '选不会自己动的结构（大门/楼角/围墙），用来判断机位有没有被转动'
          : '选会被频繁挪动的区域（桌面/货架/堆放点），这些像素不参与基线偏离统计'
      }}
    </div>

    <div class="lm-stage" v-if="imageUrl">
      <img :src="imageUrl" class="lm-img" @load="onImageLoad" alt="calibration" />
      <div class="lm-layer" @mousedown="onDown" @mousemove="onMove" @mouseup="onUp" @mouseleave="onUp">
        <div
          v-for="(r, i) in rects"
          :key="'r' + i"
          class="lm-box"
          :class="{ ignore: r.kind === 'ignore' }"
          :style="rectStyle(r)"
        >
          <span class="lm-box-label" :class="{ ignore: r.kind === 'ignore' }">{{ r.label }}</span>
        </div>
        <div
          v-if="drawing"
          class="lm-box drawing"
          :class="{ ignore: mode === 'ignore' }"
          :style="rectStyle(draft)"
        ></div>
      </div>
    </div>
    <el-empty v-else description="点「取当前画面」取一张底图" :image-size="70" />

    <div class="lm-list" v-if="rects.length">
      <div v-for="(r, i) in rects" :key="'li' + i" class="lm-item">
        <el-tag size="small" :type="r.kind === 'ignore' ? 'info' : 'success'">
          {{ r.kind === 'ignore' ? '忽略区' : '参照物' }}
        </el-tag>
        <span class="hint">
          {{ r.label }} · x {{ pct(r.x) }} · y {{ pct(r.y) }} · w {{ pct(r.w) }} · h {{ pct(r.h) }}
        </span>
        <el-button size="small" text type="danger" @click="removeAt(i)">删除</el-button>
      </div>
    </div>

    <div class="lm-admit">
      <el-checkbox v-model="admitFrame">把这张画面同时加入历史基线</el-checkbox>
      <span class="hint">
        标定画面通常就是"正常画面"，直接入库能少等一次抽样；但若被遮挡、失焦或与已有基线差异过大，会自动跳过
      </span>
    </div>

    <div class="lm-tpl" v-if="anchors.length">
      <div class="lm-tpl-title">
        参照物模板库
        <span class="hint">
          同一个参照物可保存多套外观模板（夜间/雨天/换季）。匹配时取最高分 ——
          这样天气变化不会再被误判成"参照物丢失"。上限 8 套/参照物。
        </span>
      </div>
      <div v-for="a in anchors" :key="'tpl' + a.idx" class="lm-tpl-row">
        <span class="lm-tpl-name">{{ a.label }}</span>
        <el-tag size="small" effect="plain">{{ templateCount(a.idx) }} 套</el-tag>
        <el-input
          :model-value="tplLabels[a.idx]"
          @update:model-value="(v: string) => onTplLabelChange(a.idx, v)"
          size="small"
          placeholder="模板名称，如 夜间版"
          style="width: 150px"
        />
        <el-button size="small" :disabled="!canAddTemplate" @click="onAddTemplate(a.idx)">
          用当前画面补录
        </el-button>
      </div>
    </div>

    <template #footer>
      <el-button @click="dialogVisible = false">取消</el-button>
      <el-button size="default" @click="onClearAll" :disabled="!hasExisting">清除已有标定</el-button>
      <el-button
        type="primary"
        :loading="saving"
        :disabled="!rects.length || !canAddTemplate"
        @click="onSave"
      >
        保存标定（{{ rects.length }} 个）
      </el-button>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
/**
 * 参照物标定对话框（纯展示 + 事件上抛）。
 *
 * 状态机在 useLandmarkCalibration 里，这里只负责绑定、布局与把用户动作抛给父组件。
 * 样式与拆分前逐条一致（.lm-tip / .lm-toolbar / .lm-stage / .lm-box …），类名一个都没改。
 */
import { computed } from 'vue'
import { pct } from '@/composables/format'
import { rectStyle } from '@/composables/sparseView'
import type { LmRect } from '@/composables/useLandmarkCalibration'

const props = defineProps<{
  dialogVisible: boolean
  imageUrl: string
  imageBlob: Blob | null
  mode: 'landmark' | 'ignore'
  rects: LmRect[]
  drawing: boolean
  draft: { x: number; y: number; w: number; h: number }
  saving: boolean
  admitFrame: boolean
  anchors: Array<{ idx: number; label?: string }>
  tplLabels: Record<number, string>
  templateCount: (idx: number) => number
  hasExisting: boolean
}>()

const emit = defineEmits<{
  (e: 'update:dialogVisible', v: boolean): void
  (e: 'update:mode', v: 'landmark' | 'ignore'): void
  (e: 'update:admitFrame', v: boolean): void
  (e: 'update:tplLabel', idx: number, v: string): void
  (e: 'capture'): void
  (e: 'down', ev: MouseEvent): void
  (e: 'move', ev: MouseEvent): void
  (e: 'up'): void
  (e: 'undo'): void
  (e: 'clear-rects'): void
  (e: 'remove-rect', i: number): void
  (e: 'save'): void
  (e: 'clear-all'): void
  (e: 'add-template', idx: number): void
  (e: 'image-load'): void
}>()

const dialogVisible = computed({
  get: () => props.dialogVisible,
  set: (v: boolean) => emit('update:dialogVisible', v)
})
const mode = computed({
  get: () => props.mode,
  set: (v: 'landmark' | 'ignore') => emit('update:mode', v)
})
const admitFrame = computed({
  get: () => props.admitFrame,
  set: (v: boolean) => emit('update:admitFrame', v)
})

/** 保存按钮的可用条件就是「有底图」——拆分前判的就是 lmBlob。 */
const canAddTemplate = computed(() => !!props.imageBlob)

function onCapture() {
  emit('capture')
}
function onDown(ev: MouseEvent) {
  emit('down', ev)
}
function onMove(ev: MouseEvent) {
  emit('move', ev)
}
function onUp() {
  emit('up')
}
function undo() {
  emit('undo')
}
function clearRects() {
  emit('clear-rects')
}
function removeAt(i: number) {
  emit('remove-rect', i)
}
function onSave() {
  emit('save')
}
function onClearAll() {
  emit('clear-all')
}
function onAddTemplate(idx: number) {
  emit('add-template', idx)
}
function onTplLabelChange(idx: number, v: string) {
  emit('update:tplLabel', idx, v)
}
function onImageLoad() {
  emit('image-load')
}
</script>

<style scoped>
.lm-tip {
  font-size: 12px;
  color: #606266;
  background: #f7f8fa;
  border-radius: 8px;
  padding: 8px 12px;
  line-height: 1.7;
}
.lm-toolbar {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 10px 0;
}
.lm-stage {
  position: relative;
  display: inline-block;
  max-width: 100%;
  line-height: 0;
}
.lm-img {
  display: block;
  max-width: 100%;
  border-radius: 8px;
  border: 1px solid #ebeef5;
}
.lm-layer {
  position: absolute;
  inset: 0;
  cursor: crosshair;
}
.lm-box {
  position: absolute;
  border: 2px solid #67c23a;
  background: rgba(103, 194, 58, 0.12);
  border-radius: 4px;
}
.lm-box.drawing {
  border-color: #409eff;
  background: rgba(64, 158, 255, 0.12);
}
.lm-box.ignore {
  border-color: #909399;
  border-style: dashed;
  background: rgba(144, 147, 153, 0.12);
}
.lm-box-label.ignore {
  background: #909399;
}
.lm-box-label {
  position: absolute;
  top: -18px;
  left: 0;
  font-size: 11px;
  line-height: 16px;
  color: #fff;
  background: #67c23a;
  border-radius: 3px;
  padding: 0 4px;
  white-space: nowrap;
}
.lm-list {
  margin-top: 10px;
  max-height: 140px;
  overflow-y: auto;
}
.lm-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 3px 0;
  font-size: 12px;
}
.lm-admit {
  margin-top: 12px;
  padding-top: 10px;
  border-top: 1px dashed #ebeef5;
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}
.lm-tpl {
  margin-top: 12px;
  padding-top: 10px;
  border-top: 1px dashed #ebeef5;
}
.lm-tpl-title {
  font-size: 12px;
  font-weight: 600;
  color: #303133;
  margin-bottom: 8px;
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.lm-tpl-row {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 6px;
}
.lm-tpl-name {
  min-width: 90px;
  font-size: 12px;
  color: #606266;
  font-weight: 600;
}
.hint {
  font-size: 12px;
  color: #909399;
}
</style>
