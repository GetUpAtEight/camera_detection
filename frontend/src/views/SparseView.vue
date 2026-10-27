<template>
  <div class="sparse-view">
    <!-- ============================ 顶部：机位与采样控制 ============================ -->
    <el-card shadow="never" class="block">
      <template #header>
        <div class="block-head">
          <div>
            <span class="block-title">低频巡检 · 稀疏采样篡改检测</span>
            <el-tag size="small" type="info" effect="plain" class="ml8">1 小时 / 1 天一次拉帧</el-tag>
          </div>
          <div class="head-actions">
            <el-button size="small" :loading="loading" @click="loadAll">刷新</el-button>
            <el-button size="small" type="primary" plain :loading="demoRunning" @click="runDemo">
              一键演示（6 次采样）
            </el-button>
          </div>
        </div>
      </template>

      <el-row :gutter="16">
        <el-col :span="8">
          <div class="field-label">机位</div>
          <div class="row-gap">
            <el-select
              v-model="cameraId"
              filterable
              allow-create
              default-first-option
              placeholder="输入或选择机位 id"
              style="width: 100%"
              @change="onCameraChange"
            >
              <el-option v-for="c in cameras" :key="c.cameraId" :label="c.cameraId" :value="c.cameraId">
                <span>{{ c.cameraId }}</span>
                <span class="opt-meta">{{ c.samples }} 样本 · {{ statusText(c.status) }}</span>
              </el-option>
            </el-select>
          </div>
          <div class="row-gap">
            <el-input v-model="cameraName" placeholder="机位名称（可选）" size="small" />
          </div>
          <div class="row-gap">
            <el-button size="small" :disabled="!cameraId" @click="resetBaseline">重设基准</el-button>
            <el-button size="small" type="primary" plain :disabled="!cameraId" @click="openLandmarkDialog">
              标定参照物
            </el-button>
            <el-button size="small" type="danger" plain :disabled="!cameraId" @click="removeCamera">
              删除机位
            </el-button>
          </div>
        </el-col>

        <el-col :span="10">
          <div class="field-label">采样来源</div>
          <el-radio-group v-model="source" size="small" class="row-gap">
            <el-radio-button value="camera">本机摄像头</el-radio-button>
            <el-radio-button value="video">视频文件抽帧</el-radio-button>
            <el-radio-button value="files">上传图片</el-radio-button>
          </el-radio-group>

          <div v-if="source === 'camera'" class="row-gap">
            <video ref="videoEl" class="preview" autoplay muted playsinline v-show="cameraReady"></video>
            <el-button v-if="!cameraReady" size="small" type="primary" plain @click="startCamera">
              打开摄像头
            </el-button>
            <el-button v-else size="small" @click="stopCamera">关闭</el-button>
          </div>

          <div v-if="source === 'video'" class="row-gap">
            <input type="file" accept="video/*" @change="onVideoFile" />
            <video
              ref="videoFileEl"
              class="preview"
              muted
              playsinline
              v-show="videoReady"
              style="display: none"
            ></video>
            <div v-if="videoReady" class="hint">已加载视频，将从不同时间点抽帧</div>
          </div>

          <div v-if="source === 'files'" class="row-gap">
            <input type="file" accept="image/*" multiple @change="onImageFiles" />
            <div class="hint">选 1~5 张，按顺序当作同一个 burst 的连续帧</div>
          </div>
        </el-col>

        <el-col :span="6">
          <div class="field-label">本次抽样</div>
          <div class="row-gap">
            <span class="mini-label">帧数 K</span>
            <el-radio-group v-model="k" size="small">
              <el-radio-button :value="1">1</el-radio-button>
              <el-radio-button :value="3">3</el-radio-button>
              <el-radio-button :value="5">5</el-radio-button>
            </el-radio-group>
          </div>
          <div class="row-gap">
            <span class="mini-label">采样时间推进</span>
            <el-select v-model="advance" size="small" style="width: 100%">
              <el-option label="不推进（用当前时间）" value="0" />
              <el-option label="每次 +1 小时" value="1" />
              <el-option label="每次 +24 小时（同槽）" value="24" />
            </el-select>
          </div>
          <div class="row-gap">
            <el-button
              type="primary"
              size="small"
              :loading="loading"
              :disabled="!cameraId"
              @click="sampleOnce"
            >
              提交一次抽样
            </el-button>
          </div>
          <div class="row-gap hint" v-if="nextTsText">下次采样：{{ nextTsText }}</div>
        </el-col>
      </el-row>

      <el-collapse class="mt12">
        <el-collapse-item title="判定阈值（可调，立即生效于下一次抽样）" name="p">
          <el-row :gutter="12">
            <el-col :span="12" v-for="p in paramSpecs" :key="p.key">
              <div class="param-row">
                <span class="param-label">{{ p.label }}</span>
                <el-input-number
                  v-model="params[p.key]"
                  :min="p.min"
                  :max="p.max"
                  :step="p.step"
                  size="small"
                  controls-position="right"
                  style="width: 130px"
                />
                <span class="param-desc">{{ p.description }}</span>
              </div>
            </el-col>
          </el-row>
        </el-collapse-item>
      </el-collapse>
    </el-card>

    <!-- ============================ 判定结果 ============================ -->
    <el-card v-if="result" shadow="never" class="block">
      <div class="verdict-wrap">
        <div class="verdict-badge" :class="'lvl-' + result.level">
          <el-icon><component :is="levelIcon(result.level)" /></el-icon>
        </div>
        <div class="verdict-main">
          <div class="verdict-title">
            {{ result.verdictLabel }}
            <el-tag v-if="result.alarm" type="danger" size="small" effect="dark" class="ml8">告警</el-tag>
            <el-tag v-else type="success" size="small" effect="plain" class="ml8">不告警</el-tag>
          </div>
          <div class="verdict-sub">
            <span>类型：{{ typeText(result.tamperType) }}</span>
            <el-divider direction="vertical" />
            <span>风险 {{ pct(result.risk) }}</span>
            <el-divider direction="vertical" />
            <span>置信度 {{ pct(result.confidence) }}</span>
            <el-divider direction="vertical" />
            <span>耗时 {{ result.timingsMs?.total ?? '-' }} ms</span>
            <el-divider direction="vertical" />
            <span>采样时间 {{ formatTime(result.tsMs) }}</span>
            <el-divider direction="vertical" />
            <span>时段槽 {{ String(result.slot ?? '').padStart(2, '0') }}:00</span>
          </div>
        </div>
        <div class="verdict-metrics">
          <div class="meter">
            <div class="meter-label">历史偏离 dHist</div>
            <el-progress :percentage="Math.round((result.matrix?.dHist ?? 0) * 100)" :stroke-width="8" />
          </div>
          <div class="meter">
            <div class="meter-label">微时序动态 dBurst</div>
            <el-progress
              :percentage="Math.round((result.matrix?.dBurst ?? 0) * 100)"
              :stroke-width="8"
              color="#e6a23c"
            />
          </div>
        </div>
      </div>

      <div class="reasons">
        <div class="reasons-title">判定依据（原因链）</div>
        <ul>
          <li v-for="(r, i) in result.reasons" :key="i">{{ r }}</li>
        </ul>
      </div>

      <div class="feedback-bar" v-if="anchorRects.length">
        <el-button size="small" type="primary" plain :loading="tplAdding" @click="addTemplateFromResult">
          这不是篡改 · 加为参照物模板
        </el-button>
        <span class="hint">
          用于夜间/雨天/换季等环境变化造成的误报：把当前画面补进模板库，
          之后同类画面不会再被当成"参照物丢失"。本次操作会记入审计。
        </span>
      </div>
    </el-card>

    <!-- ============================ 三层信号 ============================ -->
    <el-row :gutter="16" v-if="result">
      <el-col :span="12">
        <el-card shadow="never" class="block">
          <template #header>
            <span class="block-title">稳定性分解 4 格矩阵（核心）</span>
            <el-tooltip
              content="横轴：与历史基线的偏离程度；纵轴：burst 内动态。持续篡改 = 高偏离 + 低动态；瞬时事件 = 高偏离 + 高动态（不告警）"
              placement="top"
            >
              <el-icon class="ml8"><QuestionFilled /></el-icon>
            </el-tooltip>
          </template>
          <div ref="matrixEl" class="chart" style="height: 300px"></div>
        </el-card>
      </el-col>
      <el-col :span="12">
        <el-card shadow="never" class="block">
          <template #header>
            <span class="block-title">运动 × 噪声 2×2 象限（冻结/重放判据）</span>
          </template>
          <div class="quad-grid">
            <div
              v-for="q in quadrants"
              :key="q.key"
              class="quad-cell"
              :class="{ active: result?.layers?.L1?.quadrant === q.key, danger: q.danger }"
            >
              <div class="quad-key">{{ q.key }}</div>
              <div class="quad-label">{{ q.label }}</div>
              <div class="quad-desc">{{ q.desc }}</div>
            </div>
          </div>
          <div class="quad-meta">
            静止？<b>{{ result?.layers?.L1?.motionHigh ? '否（有结构变化）' : '是' }}</b>
            <el-divider direction="vertical" />
            独立噪声 σ=<b>{{ result?.layers?.L1?.sigmaPerFrame }}</b>
            <el-divider direction="vertical" />
            bit-exact 重复帧对 <b>{{ result?.layers?.L1?.bitExactDup }}</b>
          </div>
          <div class="quad-evidence">
            <span class="ev-title">冻结判据来源</span>
            <el-tag
              size="small"
              :type="result?.matrix?.freezeEvidence?.d1 ? 'danger' : 'info'"
              :effect="result?.matrix?.freezeEvidence?.d1 ? 'dark' : 'plain'"
            >
              D1 跨采样点 bit-exact {{ result?.matrix?.freezeEvidence?.d1 ? '命中' : '未命中' }}
            </el-tag>
            <el-tag
              size="small"
              :type="result?.matrix?.freezeEvidence?.d3 ? 'danger' : 'info'"
              :effect="result?.matrix?.freezeEvidence?.d3 ? 'dark' : 'plain'"
            >
              D3 静止+无噪声 {{ result?.matrix?.freezeEvidence?.d3 ? '命中' : '未命中' }}
            </el-tag>
          </div>
          <div class="hint mt8">
            说明：象限只反映 burst 内部证据。持续篡改在 burst 内是常量，所以"冻结"也可能由
            跨采样点的逐位重复（D1）单独触发——此时象限可能仍是 Q2，两者并不矛盾。
          </div>

          <div class="lm-status" v-if="lmConfigured">
            <div class="lm-status-title">
              参照物状态
              <span class="hint" v-if="lmConsensus">
                一致位移 {{ fmt(lmConsensus.magnitude) }}px · 偏差 {{ fmt(lmConsensus.spread) }}px
                <b :class="lmConsensus.moved ? 'worse' : ''">
                  {{ lmConsensus.moved ? '（判定：被移位）' : '（判定：未移位）' }}
                </b>
              </span>
            </div>
            <div v-for="m in lmMatches" :key="'lm' + m.id" class="lm-status-row">
              <span class="lm-dot" :class="lmOk(m) ? 'ok' : 'bad'"></span>
              <span class="lm-name">{{ m.label || ('参照物' + (m.id + 1)) }}</span>
              <span class="lm-score">匹配 {{ fmt(m.score) }}</span>
              <span class="lm-offset">位移 {{ fmt(m.magnitude) }}px</span>
            </div>
            <div class="lm-status-row" v-if="ignoreBoxes.length">
              <span class="lm-dot" style="background: #909399"></span>
              <span class="lm-name">忽略区</span>
              <span class="lm-offset">
                {{ ignoreBoxes.length }} 块，占画面
                {{ pct(result?.layers?.L0?.changed?.ignoredRatio ?? 0) }}，
                这些像素不参与基线偏离统计
              </span>
            </div>
          </div>
          <div class="hint mt8" v-else-if="activeLandmarks.length === 0">
            未标定参照物：移位判定目前用全图相位相关，容易被树影/旗帜/行人带偏。
            点左侧「标定参照物」框选不会移动的建筑物（大门、楼角、围墙）可显著降低过敏。
          </div>
        </el-card>
      </el-col>
    </el-row>

    <el-row :gutter="16" v-if="result">
      <el-col :span="12">
        <el-card shadow="never" class="block">
          <template #header><span class="block-title">L1 微时序 9 指标</span></template>
          <div class="ind-list">
            <div v-for="ind in l1Indicators" :key="ind.key" class="ind-row">
              <span class="ind-name">{{ ind.label }}</span>
              <el-tooltip :content="ind.desc" placement="top">
                <span class="ind-value">{{ fmt(result.layers?.L1?.[ind.key]) }}</span>
              </el-tooltip>
              <div class="ind-bar">
                <div class="ind-fill" :style="{ width: ind.ratio(result) * 100 + '%', background: ind.color }"></div>
              </div>
            </div>
          </div>
        </el-card>
      </el-col>
      <el-col :span="12">
        <el-card shadow="never" class="block">
          <template #header><span class="block-title">L0 / L2 与基线对比</span></template>
          <el-table :data="compareRows" size="small" :show-header="true">
            <el-table-column prop="name" label="指标" width="150" />
            <el-table-column prop="current" label="当前" width="100" />
            <el-table-column prop="baseline" label="基线" width="100" />
            <el-table-column label="变化">
              <template #default="{ row }">
                <span :class="row.worse ? 'worse' : ''">{{ row.change }}</span>
              </template>
            </el-table-column>
          </el-table>
          <div class="l0-extra">
            <div>
              逐像素离群占比（z&gt;3）：<b>{{ pct(result?.layers?.L0?.pixelOutlierRatio ?? 0) }}</b>
              <span class="hint">（阈值 {{ pct(params.outlierAlarm ?? 0.1) }}）</span>
            </div>
            <div>
              与基线全局位移：
              <b>{{ result?.layers?.L0?.bgShift?.magnitude ?? 0 }} px</b>
              <span class="hint">（响应 {{ result?.layers?.L0?.bgShift?.response ?? 0 }}）</span>
            </div>
            <div>
              基线来源：<b>{{ baselineSourceText }}</b>
            </div>
          </div>
        </el-card>
      </el-col>
    </el-row>

    <!-- ============================ 图像证据 ============================ -->
    <el-card v-if="result" shadow="never" class="block">
      <template #header><span class="block-title">图像证据</span></template>
      <el-row :gutter="12">
        <el-col :span="8" v-if="result.keyframe">
          <div class="img-cap">当前关键帧</div>
          <div class="evidence-wrap">
            <img :src="result.keyframe" class="evidence" alt="keyframe" />
            <div
              v-for="(lm, i) in ignoreBoxes"
              :key="'ig' + i"
              class="lm-overlay-box ignore"
              :style="{
                left: (lm.x * 100) + '%',
                top: (lm.y * 100) + '%',
                width: (lm.w * 100) + '%',
                height: (lm.h * 100) + '%'
              }"
            >
              <span class="lm-overlay-label ignore">{{ lm.label }}</span>
            </div>
            <div
              v-for="(lm, i) in anchorBoxes"
              :key="'ol' + i"
              class="lm-overlay-box"
              :class="{ moved: lmMoved(lm.id) }"
              :style="{
                left: (lm.x * 100) + '%',
                top: (lm.y * 100) + '%',
                width: (lm.w * 100) + '%',
                height: (lm.h * 100) + '%'
              }"
            >
              <span class="lm-overlay-label">{{ lm.label }}</span>
            </div>
          </div>
        </el-col>
        <el-col :span="16" v-if="result.baselineCompare">
          <div class="img-cap">基线中位数 / 当前 / 差异热力图</div>
          <img :src="result.baselineCompare" class="evidence" alt="compare" />
        </el-col>
      </el-row>
      <div v-if="!result.baselineCompare" class="hint mt8">
        基线尚未建立，暂无对比图（继续采样几次就会出现）
      </div>
    </el-card>

    <!-- ============================ 基线库与时间线 ============================ -->
    <el-row :gutter="16">
      <el-col :span="12">
        <el-card shadow="never" class="block">
          <template #header>
            <span class="block-title">历史基线库（24 个时段槽）</span>
            <el-tag size="small" effect="plain" class="ml8">
              状态 {{ statusText(stateData?.status) }} · {{ stateData?.samples ?? 0 }} 样本
            </el-tag>
          </template>
          <div class="slot-grid">
            <el-tooltip
              v-for="s in stateData?.slots || []"
              :key="s.slot"
              :content="`槽 ${s.hour}:00 · ${s.samples} 个样本${s.ready ? '（可判定）' : ''}`"
              placement="top"
            >
              <div
                class="slot-cell"
                :class="{ filled: s.samples > 0, ready: s.ready }"
                :style="{ opacity: s.samples ? 0.35 + Math.min(s.samples / 8, 1) * 0.65 : 0.18 }"
                @click="openSlot(s)"
              >
                <span>{{ s.hour }}</span>
                <em v-if="s.samples">{{ s.samples }}</em>
              </div>
            </el-tooltip>
          </div>
          <div class="slot-gallery" v-if="gallery.length">
            <div v-for="g in gallery" :key="g.key" class="gallery-item">
              <img :src="g.url" alt="baseline" />
              <span>槽 {{ g.slot }} · {{ formatTime(g.ts) }}</span>
            </div>
          </div>
          <div v-else class="hint mt8">点击上方槽位查看该时段的历史样本</div>
        </el-card>
      </el-col>
      <el-col :span="12">
        <el-card shadow="never" class="block">
          <template #header><span class="block-title">判定时间线</span></template>
          <el-empty v-if="!history.length" description="还没有采样记录" :image-size="60" />
          <div v-else class="timeline">
            <div v-for="(h, i) in history" :key="i" class="tl-item">
              <span class="tl-dot" :class="'lvl-' + levelOf(h.verdict)"></span>
              <span class="tl-time">{{ formatTime(h.tsMs) }}</span>
              <span class="tl-label">{{ h.label }}</span>
              <span class="tl-meta">dHist {{ fmt(h.dHist) }} · 槽 {{ h.slot }}</span>
              <el-tag v-if="h.alarm" size="small" type="danger" effect="plain">告警</el-tag>
            </div>
          </div>
        </el-card>
      </el-col>
    </el-row>

    <!-- ============================ 参照物标定对话框 ============================ -->
    <LandmarkCalibrationDialog
      v-model:dialog-visible="lm.dialogVisible.value"
      :image-url="lm.imageUrl.value"
      :image-blob="lm.imageBlob.value"
      :mode="lm.mode.value"
      :rects="lm.rects.value"
      :drawing="lm.drawing.value"
      :draft="lm.draft.value"
      :saving="lm.saving.value"
      :admit-frame="lm.admitFrame.value"
      :anchors="anchorRects"
      :tpl-labels="tplLabel"
      :template-count="countFor"
      :has-existing="activeLandmarks.length > 0"
      @update:dialog-visible="lm.dialogVisible.value = $event"
      @update:mode="lm.mode.value = $event"
      @update:admit-frame="lm.admitFrame.value = $event"
      @update:tpl-label="(idx: number, v: string) => (tplLabel[idx] = v)"
      @capture="captureForLandmark"
      @down="lm.onDown"
      @move="lm.onMove"
      @up="lm.onUp"
      @undo="lm.undo"
      @clear-rects="lm.clearRects"
      @remove-rect="lm.removeAt"
      @save="saveLandmarks"
      @clear-all="clearLandmarkConfig"
      @add-template="addTemplateFor"
      @image-load="onLmImageLoad"
    />
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import * as echarts from 'echarts'
import { ElMessage, ElMessageBox } from 'element-plus'
import { sparseApi } from '@/api'
import type {
  SparseCameraItem,
  SparseLandmark,
  SparseLandmarkMatch,
  SparseMeta,
  SparseParamMeta,
  SparseSampleResult,
  SparseSlot,
  SparseState,
  SparseTemplateItem
} from '@/api'
import LandmarkCalibrationDialog from '@/components/sparse/LandmarkCalibrationDialog.vue'
import { fmt, formatTime, levelIcon, levelOf, lmOk, pct, statusText, typeText } from '@/composables/format'
import {
  L1_INDICATORS,
  QUADRANT_DEFS,
  baselineSourceText as computeBaselineSourceText,
  buildCompareRows,
  buildGallery,
  isLandmarkConfigured,
  landmarkMoved,
  normalizeHistory,
  nextTsText as computeNextTsText,
  rectStyle,
  withIdx
} from '@/composables/sparseView'
import type { TimelineRow } from '@/composables/sparseView'
import {
  captureOne,
  captureFromCamera,
  captureFromVideo,
  dataUrlToBlob,
  demoBaseCanvas,
  synthFrames
} from '@/composables/capture'
import { buildMatrixOption, thresholdsFromParams } from '@/composables/matrixChart'
import {
  REPLAY_JUMP_MS,
  jumpForward,
  nextSampleTs,
  shouldResetAfterSubmit
} from '@/composables/sampleClock'
import { useLandmarkCalibration } from '@/composables/useLandmarkCalibration'

/* ------------------------------------------------------------------ 状态 */
const cameras = ref<SparseCameraItem[]>([])
const cameraId = ref('')
const cameraName = ref('')
const stateData = ref<SparseState | null>(null)
const result = ref<SparseSampleResult | null>(null)
const history = ref<TimelineRow[]>([])
const meta = ref<SparseMeta | null>(null)
const params = reactive<Record<string, number>>({})
const loading = ref(false)
const demoRunning = ref(false)

const source = ref<'camera' | 'video' | 'files'>('camera')
const k = ref(3)
const advance = ref<'0' | '1' | '24'>('1')
const simulatedTs = ref<number>(0)
const pendingFiles = ref<Blob[]>([])
const cameraReady = ref(false)
const videoReady = ref(false)

const videoEl = ref<HTMLVideoElement>()
const videoFileEl = ref<HTMLVideoElement>()
const matrixEl = ref<HTMLElement>()
const gallery = ref<{ key: string; url: string; slot: number; ts: number }[]>([])

let stream: MediaStream | null = null
let matrixChart: echarts.ECharts | null = null

/* ------------------------------------------------------------------ 常量 */
const quadrants = QUADRANT_DEFS
const l1Indicators = L1_INDICATORS

/* ------------------------------------------------------------------ 模板库 */
const templateList = ref<SparseTemplateItem[]>([])
const tplLabel = reactive<Record<number, string>>({})
const tplAdding = ref(false)

/* ------------------------------------------------------------------ 派生 */
const activeLandmarks = computed<SparseLandmark[]>(() => stateData.value?.landmarks || [])
const anchorsAll = computed(() => withIdx(activeLandmarks.value))
const anchorRects = computed(() =>
  anchorsAll.value.filter((lm) => (lm.kind || 'landmark') !== 'ignore')
)
const anchorBoxes = computed(() =>
  activeLandmarks.value.filter((lm: SparseLandmark) => (lm.kind || 'landmark') !== 'ignore')
)
const ignoreBoxes = computed(() =>
  activeLandmarks.value.filter((lm: SparseLandmark) => lm.kind === 'ignore')
)
const lmInfo = computed(() => result.value?.layers?.L0?.landmarks || {})
const lmConfigured = computed<boolean>(() =>
  isLandmarkConfigured(lmInfo.value, activeLandmarks.value)
)
const lmMatches = computed<SparseLandmarkMatch[]>(() => lmInfo.value.matches || [])
const lmConsensus = computed(() => lmInfo.value.consensus || null)

const paramSpecs = computed<SparseParamMeta[]>(() => meta.value?.params || [])
const compareRows = computed(() => buildCompareRows(result.value))
const baselineSourceText = computed(() => computeBaselineSourceText(result.value))
const nextTsText = computed(() => computeNextTsText(advance.value, simulatedTs.value))

function countFor(idx: number): number {
  return templateList.value.filter((t) => Number(t.landmarkId) === idx).length
}

function lmMoved(id?: number): boolean {
  if (id == null) return false
  return landmarkMoved(lmMatches.value, id)
}

/* ------------------------------------------------------------------ 标定对话框 */
const lm = useLandmarkCalibration({
  loadImage: () => captureForLandmarkBlob(),
  onSave: (rects, frame, admit) => saveLandmarksTo(rects, frame, admit),
  onClear: () => clearLandmarkConfig()
})

function onLmImageLoad() {
  /* 图片按容器宽度自适应，overlay 用百分比定位，无需额外计算 */
}

/* ------------------------------------------------------------------ 数据加载 */
async function loadAll() {
  loading.value = true
  try {
    const [m, c] = await Promise.all([sparseApi.meta(), sparseApi.cameras()])
    meta.value = m
    cameras.value = c?.cameras || []
    if (!Object.keys(params).length) {
      Object.assign(params, m?.defaults || {})
    }
    if (!cameraId.value && cameras.value.length) {
      cameraId.value = cameras.value[0].cameraId
    }
    if (cameraId.value) await loadState()
  } catch (e) {
    ElMessage.error(e instanceof Error ? e.message : '加载失败')
  } finally {
    loading.value = false
  }
}

async function loadState() {
  if (!cameraId.value) return
  try {
    stateData.value = await sparseApi.state(cameraId.value)
    history.value = normalizeHistory(stateData.value?.history)
  } catch {
    stateData.value = null
  }
  await loadTemplates()
}

function onCameraChange() {
  result.value = null
  gallery.value = []
  loadState()
}

async function resetBaseline() {
  try {
    await ElMessageBox.confirm(
      `清空机位 ${cameraId.value} 的历史基线？之后的抽样需要重新积累。`,
      '重设基准',
      { type: 'warning' }
    )
    await sparseApi.reset(cameraId.value)
    result.value = null
    ElMessage.success('基线已清空')
    await loadAll()
  } catch (e) {
    if (e !== 'cancel') ElMessage.error(e instanceof Error ? e.message : '操作失败')
  }
}

async function removeCamera() {
  try {
    await ElMessageBox.confirm(`删除机位 ${cameraId.value} 的全部基线数据？`, '删除机位', { type: 'warning' })
    await sparseApi.remove(cameraId.value)
    ElMessage.success('已删除')
    cameraId.value = ''
    result.value = null
    await loadAll()
  } catch (e) {
    if (e !== 'cancel') ElMessage.error(e instanceof Error ? e.message : '操作失败')
  }
}

async function openSlot(slot: SparseSlot) {
  gallery.value = []
  if (!slot.samples || !cameraId.value) return
  // 该槽的样本最多 8 个，逐个拉缩略图（失败就跳过，不打断页面）
  const slotSamples = (stateData.value?.slots || []).find((s) => s.slot === slot.slot)
  const count = slotSamples?.samples || 0
  gallery.value = buildGallery(
    cameraId.value,
    { slot: slot.slot, samples: count, latestTs: slot.latestTs },
    sparseApi.thumbUrl
  )
}

/* ------------------------------------------------------------------ 模板库 */
async function loadTemplates() {
  if (!cameraId.value) return
  try {
    const res = await sparseApi.listTemplates(cameraId.value)
    templateList.value = res?.templates || []
  } catch {
    templateList.value = []
  }
}

async function addTemplateFor(
  idx: number,
  blob?: Blob | null,
  label?: string,
  source = 'manual'
) {
  const frame = blob || lm.imageBlob.value
  if (!frame) {
    ElMessage.warning('没有可用画面')
    return
  }
  tplAdding.value = true
  try {
    const res = await sparseApi.addTemplate(cameraId.value, idx, frame, {
      label: label || tplLabel[idx] || '',
      source,
      verdictAtTime: result.value?.verdict || ''
    })
    if (res?.added === false) {
      ElMessage.info(res?.note || '与已有模板几乎相同，未添加')
    } else {
      ElMessage.success(`已补录模板：${label || tplLabel[idx] || '附加模板'}`)
    }
    await loadTemplates()
  } catch (e) {
    const msg = e instanceof Error ? e.message : '补录失败'
    if (String(msg).includes('质量不合格')) {
      try {
        await ElMessageBox.confirm(
          `${msg}

确认要在这种画面上强制添加吗？该操作会被记入审计。`,
          '模板质量告警',
          { type: 'warning', confirmButtonText: '强制添加', cancelButtonText: '取消' }
        )
        await sparseApi.addTemplate(cameraId.value, idx, frame, {
          label: label || tplLabel[idx] || '强制模板',
          source,
          verdictAtTime: result.value?.verdict || '',
          force: true
        })
        ElMessage.success('已强制补录（审计已记录）')
        await loadTemplates()
      } catch {
        /* 用户取消 */
      }
    } else {
      ElMessage.error(msg)
    }
  } finally {
    tplAdding.value = false
  }
}

function suggestLabel(): string {
  const h = new Date().getHours()
  if (h >= 18 || h < 6) return '夜间版'
  return '环境变化版'
}

async function addTemplateFromResult() {
  if (!result.value?.keyframe) {
    ElMessage.warning('没有当前画面')
    return
  }
  try {
    const { value: label } = await ElMessageBox.prompt(
      '给这套模板起个名字（例如：夜间版 / 雨天版）',
      '补录参照物模板',
      {
        inputValue: suggestLabel(),
        confirmButtonText: '确定',
        cancelButtonText: '取消'
      }
    )
    const blob = await dataUrlToBlob(result.value.keyframe)
    for (const a of anchorRects.value) {
      await addTemplateFor(a.idx, blob, label || '误报反馈', 'false_alarm')
    }
    ElMessage.success('模板已补录，下一次抽样起生效')
  } catch {
    /* 用户取消 */
  }
}

/* ------------------------------------------------------------------ 参照物标定 */
function openLandmarkDialog() {
  lm.open(activeLandmarks.value)
}

async function captureForLandmarkBlob(): Promise<Blob | null> {
  return captureOne({
    source: source.value,
    cameraReady: cameraReady.value,
    videoReady: videoReady.value,
    videoEl: videoEl.value,
    videoFileEl: videoFileEl.value,
    pendingFiles: pendingFiles.value,
    keyframe: result.value?.keyframe
  })
}

async function captureForLandmark() {
  try {
    const ok = await lm.capture()
    if (!ok) {
      ElMessage.warning('请先打开摄像头 / 选视频 / 选图片，再取画面')
    }
  } catch (e) {
    ElMessage.error(e instanceof Error ? e.message : '取画面失败')
  }
}

async function saveLandmarksTo(
  rects: Array<{ x: number; y: number; w: number; h: number; label: string; kind: 'landmark' | 'ignore' }>,
  frame: Blob | null,
  admitFrame: boolean
) {
  if (!cameraId.value) return
  try {
    const res = await sparseApi.setLandmarks(cameraId.value, rects, frame, false, admitFrame)
    const anchorCount = rects.filter((r) => r.kind !== 'ignore').length
    ElMessage.success(`已标定 ${anchorCount} 个参照物`)
    if (res?.frameAdmitted) {
      ElMessage.success(res.frameAdmitReason || '标定画面已加入基线')
    } else if (res?.frameAdmitReason) {
      ElMessage.warning(res.frameAdmitReason)
    }
    lm.close()
    await loadState()
  } catch (e) {
    ElMessage.error(e instanceof Error ? e.message : '保存标定失败')
  }
}

async function saveLandmarks() {
  await lm.save()
}

async function clearLandmarkConfig() {
  try {
    await sparseApi.clearLandmarks(cameraId.value)
    ElMessage.success('已清除参照物')
    lm.rects.value = []
    lm.close()
    await loadState()
  } catch (e) {
    ElMessage.error(e instanceof Error ? e.message : '清除失败')
  }
}

/* ------------------------------------------------------------------ 采样来源 */
async function startCamera() {
  try {
    stream = await navigator.mediaDevices.getUserMedia({ video: { width: 1280, height: 720 } })
    if (videoEl.value) {
      videoEl.value.srcObject = stream
      await videoEl.value.play()
    }
    cameraReady.value = true
  } catch (e) {
    ElMessage.error('打开摄像头失败：' + (e instanceof Error ? e.message : e))
  }
}

function stopCamera() {
  stream?.getTracks().forEach((t) => t.stop())
  stream = null
  cameraReady.value = false
}

function onVideoFile(e: Event) {
  const file = (e.target as HTMLInputElement).files?.[0]
  if (!file) return
  videoReady.value = true
  pendingFiles.value = []
  const url = URL.createObjectURL(file)
  nextTick(() => {
    if (videoFileEl.value) {
      videoFileEl.value.src = url
      videoFileEl.value.load()
    }
  })
}

function onImageFiles(e: Event) {
  const files = Array.from((e.target as HTMLInputElement).files || [])
  pendingFiles.value = files.slice(0, 5)
  if (files.length > 5) ElMessage.warning('最多取前 5 张')
}

/* ------------------------------------------------------------------ 提交 */
function nextTs(): number {
  const next = nextSampleTs({ simulatedTs: simulatedTs.value, advance: advance.value })
  simulatedTs.value = next.state.simulatedTs
  return next.tsMs
}

async function submit(frames: Blob[], label?: string, reset = false) {
  if (!cameraId.value) {
    ElMessage.warning('请先填写机位 id')
    return
  }
  if (!frames.length) {
    ElMessage.warning('没有可提交的帧')
    return
  }
  loading.value = true
  try {
    const res = await sparseApi.sample(
      cameraId.value,
      frames,
      nextTs(),
      cameraName.value || cameraId.value,
      reset,
      { ...params }
    )
    result.value = res
    if (shouldResetAfterSubmit(advance.value)) simulatedTs.value = 0
    await loadState()
    if (res.alarm) {
      ElMessage.warning(`检测到${res.verdictLabel}`)
    } else if (label) {
      ElMessage.success(`${label} · ${res.verdictLabel}`)
    }
    await nextTick()
    renderMatrix()
  } catch (e) {
    ElMessage.error(e instanceof Error ? e.message : '提交失败')
  } finally {
    loading.value = false
  }
}

async function sampleOnce() {
  try {
    let frames: Blob[] = []
    if (source.value === 'camera') {
      frames = await captureFromCamera(videoEl.value, cameraReady.value, k.value)
    } else if (source.value === 'video') {
      frames = await captureFromVideo(videoFileEl.value, videoReady.value, k.value)
    } else {
      frames = pendingFiles.value.slice(0, k.value)
    }
    await submit(frames)
  } catch (e) {
    ElMessage.error(e instanceof Error ? e.message : '采样失败')
  }
}

/* ------------------------------------------------------------------ 一键演示 */
async function runDemo() {
  if (!cameraId.value) {
    ElMessage.warning('请先填写机位 id')
    return
  }
  demoRunning.value = true
  const savedAdvance = advance.value
  advance.value = '1'
  simulatedTs.value = Date.now()
  try {
    // 取一张底图
    let baseCanvas: HTMLCanvasElement | null = null
    try {
      baseCanvas = await demoBaseCanvas({
        source: source.value,
        cameraReady: cameraReady.value,
        videoReady: videoReady.value,
        videoEl: videoEl.value,
        videoFileEl: videoFileEl.value,
        pendingFiles: pendingFiles.value
      })
    } catch {
      ElMessage.warning('请先打开摄像头 / 选视频 / 选图片，演示需要一张底图')
      return
    }

    // 1~2：正常两次（建立基线）
    // 第一次带 reset=true：演示必须自包含，不能沿用这个机位之前的基线
    // （否则换了场景/换了底图，历史基线会立刻把正常帧判成"画面持续异常"）。
    if (!baseCanvas) throw new Error('演示底图缺失')
    await submit(await synthFrames(baseCanvas, 'normal', k.value), '正常样本', true)
    await submit(await synthFrames(baseCanvas, 'normal', k.value), '正常样本')
    // 3：遮挡
    await submit(await synthFrames(baseCanvas, 'covered', k.value), '遮挡')
    // 4：失焦
    await submit(await synthFrames(baseCanvas, 'defocused', k.value), '失焦')
    // 5：重放（复用第一次那批原始字节，制造跨采样点 bit-exact）
    const firstNormal = await synthFrames(baseCanvas, 'normal', k.value)
    await submit(firstNormal, '正常样本')
    // 模拟"几小时后把这段录像重放一次"
    simulatedTs.value = jumpForward(
      { simulatedTs: simulatedTs.value, advance: advance.value },
      REPLAY_JUMP_MS
    ).simulatedTs
    await submit(firstNormal, '重放旧画面')
    ElMessage.success('演示完成，请看时间线与 4 格矩阵')
  } catch (e) {
    ElMessage.error(e instanceof Error ? e.message : '演示失败')
  } finally {
    advance.value = savedAdvance
    demoRunning.value = false
  }
}

/* ------------------------------------------------------------------ 矩阵图 */
function renderMatrix() {
  if (!matrixEl.value) return
  if (!matrixChart) {
    matrixChart = echarts.init(matrixEl.value)
  }
  matrixChart.setOption(
    buildMatrixOption(history.value, result.value, thresholdsFromParams(params)),
    true
  )
}

function onResize() {
  matrixChart?.resize()
}

/* ------------------------------------------------------------------ 生命周期 */
watch([result, history], () => nextTick(renderMatrix), { deep: false })

onMounted(async () => {
  await loadAll()
  if (source.value === 'camera') await startCamera().catch(() => undefined)
  nextTick(renderMatrix)
  window.addEventListener('resize', onResize)
})

onBeforeUnmount(() => {
  stopCamera()
  window.removeEventListener('resize', onResize)
  matrixChart?.dispose()
  matrixChart = null
  lm.revokeUrl()
})
</script>

<style scoped>
.sparse-view {
  display: flex;
  flex-direction: column;
  gap: 14px;
}
.block-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.block-title {
  font-weight: 600;
  font-size: 15px;
}
.head-actions {
  display: flex;
  gap: 8px;
}
.ml8 {
  margin-left: 8px;
}
.mt8 {
  margin-top: 8px;
}
.mt12 {
  margin-top: 12px;
}
.row-gap {
  margin-top: 10px;
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.field-label {
  font-size: 12px;
  color: #909399;
  margin-bottom: 4px;
}
.mini-label {
  font-size: 12px;
  color: #606266;
}
.hint {
  font-size: 12px;
  color: #909399;
}
.opt-meta {
  float: right;
  color: #909399;
  font-size: 12px;
}
.preview {
  width: 100%;
  max-width: 420px;
  border-radius: 8px;
  background: #000;
}

/* 判定卡片 */
.verdict-wrap {
  display: flex;
  align-items: center;
  gap: 18px;
}
.verdict-badge {
  width: 56px;
  height: 56px;
  border-radius: 16px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 30px;
  color: #fff;
  flex: none;
}
.lvl-critical { background: #f56c6c; }
.lvl-warning { background: #e6a23c; }
.lvl-ok { background: #67c23a; }
.lvl-info { background: #909399; }
.verdict-main {
  flex: 1;
  min-width: 0;
}
.verdict-title {
  font-size: 22px;
  font-weight: 700;
  display: flex;
  align-items: center;
}
.verdict-sub {
  margin-top: 6px;
  font-size: 12px;
  color: #606266;
}
.verdict-metrics {
  width: 260px;
  flex: none;
}
.meter {
  margin-bottom: 8px;
}
.meter-label {
  font-size: 12px;
  color: #606266;
  margin-bottom: 2px;
}
.reasons {
  margin-top: 14px;
  background: #f7f8fa;
  border-radius: 8px;
  padding: 10px 14px;
}
.reasons-title {
  font-size: 12px;
  color: #909399;
  margin-bottom: 4px;
}
.reasons ul {
  margin: 0;
  padding-left: 18px;
}
.reasons li {
  font-size: 13px;
  line-height: 1.7;
  color: #303133;
}

.chart {
  width: 100%;
}

/* 2×2 象限 */
.quad-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 8px;
}
.quad-cell {
  border: 1px solid #ebeef5;
  border-radius: 8px;
  padding: 8px 10px;
  opacity: 0.55;
  transition: all 0.2s;
}
.quad-cell.active {
  opacity: 1;
  border-color: #409eff;
  box-shadow: 0 0 0 2px rgba(64, 158, 255, 0.15);
}
.quad-cell.active.danger {
  border-color: #f56c6c;
  box-shadow: 0 0 0 2px rgba(245, 108, 108, 0.18);
  background: rgba(245, 108, 108, 0.06);
}
.quad-key {
  font-family: ui-monospace, monospace;
  font-size: 11px;
  color: #909399;
}
.quad-label {
  font-weight: 600;
  font-size: 13px;
}
.quad-desc {
  font-size: 11px;
  color: #909399;
}
.quad-meta {
  margin-top: 10px;
  font-size: 12px;
  color: #606266;
}
.quad-evidence {
  margin-top: 8px;
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}
.ev-title {
  font-size: 12px;
  color: #909399;
  margin-right: 2px;
}

/* 指标条 */
.ind-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.ind-row {
  display: grid;
  grid-template-columns: 150px 70px 1fr;
  align-items: center;
  gap: 8px;
}
.ind-name {
  font-size: 12px;
  color: #606266;
  font-family: ui-monospace, monospace;
}
.ind-value {
  font-size: 12px;
  text-align: right;
  font-family: ui-monospace, monospace;
  color: #303133;
}
.ind-bar {
  height: 8px;
  background: #f0f2f5;
  border-radius: 4px;
  overflow: hidden;
}
.ind-fill {
  height: 100%;
  border-radius: 4px;
  transition: width 0.25s;
}

/* 证据图 */
.img-cap {
  font-size: 12px;
  color: #909399;
  margin-bottom: 4px;
}
.evidence {
  width: 100%;
  border-radius: 8px;
  border: 1px solid #ebeef5;
}
.worse {
  color: #f56c6c;
  font-weight: 600;
}
.l0-extra {
  margin-top: 10px;
  font-size: 12px;
  color: #606266;
  line-height: 1.9;
}

/* 时段槽 */
.slot-grid {
  display: grid;
  grid-template-columns: repeat(12, 1fr);
  gap: 4px;
}
.slot-cell {
  position: relative;
  height: 26px;
  border-radius: 4px;
  background: #409eff;
  color: #fff;
  font-size: 10px;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
}
.slot-cell.ready {
  outline: 2px solid #67c23a;
  outline-offset: -2px;
}
.slot-cell em {
  position: absolute;
  top: -2px;
  right: 1px;
  font-size: 9px;
  font-style: normal;
  opacity: 0.9;
}
.slot-gallery {
  display: flex;
  gap: 8px;
  overflow-x: auto;
  margin-top: 10px;
}
.gallery-item {
  flex: none;
  width: 110px;
  font-size: 10px;
  color: #909399;
}
.gallery-item img {
  width: 110px;
  border-radius: 6px;
  border: 1px solid #ebeef5;
}

/* 时间线 */
.timeline {
  max-height: 300px;
  overflow-y: auto;
}
.tl-item {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  padding: 5px 0;
  border-bottom: 1px dashed #f0f2f5;
}
.tl-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  flex: none;
}
.tl-dot.lvl-critical { background: #f56c6c; }
.tl-dot.lvl-warning { background: #e6a23c; }
.tl-dot.lvl-ok { background: #67c23a; }
.tl-dot.lvl-info { background: #909399; }
.tl-time {
  width: 84px;
  color: #909399;
  font-family: ui-monospace, monospace;
}
.tl-label {
  min-width: 120px;
  font-weight: 600;
}
.tl-meta {
  flex: 1;
  color: #909399;
}
.param-row {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
}
.param-label {
  width: 130px;
  font-size: 12px;
  color: #606266;
}
.param-desc {
  font-size: 11px;
  color: #b0b3b8;
  flex: 1;
}

/* 参照物标定 */
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
.lm-overlay-box.ignore {
  border-color: rgba(144, 147, 153, 0.95);
  border-style: dashed;
}
.lm-overlay-label.ignore {
  background: rgba(144, 147, 153, 0.85);
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
.evidence-wrap {
  position: relative;
  line-height: 0;
}
.lm-overlay-box {
  position: absolute;
  border: 2px solid rgba(103, 194, 58, 0.9);
  border-radius: 3px;
  pointer-events: none;
}
.lm-overlay-box.moved {
  border-color: #f56c6c;
}
.lm-overlay-label {
  position: absolute;
  top: -16px;
  left: 0;
  font-size: 10px;
  line-height: 14px;
  color: #fff;
  background: rgba(103, 194, 58, 0.85);
  border-radius: 3px;
  padding: 0 3px;
  white-space: nowrap;
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
.feedback-bar {
  margin-top: 12px;
  padding-top: 10px;
  border-top: 1px dashed #ebeef5;
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}
.lm-status {
  margin-top: 12px;
  border-top: 1px dashed #ebeef5;
  padding-top: 10px;
}
.lm-status-title {
  font-size: 12px;
  font-weight: 600;
  color: #303133;
  margin-bottom: 6px;
}
.lm-status-row {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  color: #606266;
  padding: 2px 0;
}
.lm-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  flex: none;
}
.lm-dot.ok { background: #67c23a; }
.lm-dot.bad { background: #f56c6c; }
.lm-name { min-width: 72px; font-weight: 600; }
.lm-score { min-width: 88px; }
.lm-offset { flex: 1; }
</style>
