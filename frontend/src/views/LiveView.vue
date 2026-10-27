<template>
  <div class="live-page">
    <!-- 顶部：控制条 -->
    <div class="ctd-card">
      <h3 class="ctd-card-title">
        <el-icon><VideoCamera /></el-icon> 实时监控
        <span class="sub">用本机摄像头模拟监控点位，按固定间隔抓帧检测，连续判定篡改才告警</span>
      </h3>

      <div class="control-row">
        <div class="field">
          <span class="field-label">摄像头</span>
          <el-select v-model="deviceId" :disabled="running" style="width: 240px" placeholder="默认摄像头">
            <el-option v-for="d in devices" :key="d.deviceId" :value="d.deviceId" :label="d.label" />
          </el-select>
        </div>

        <div class="field">
          <span class="field-label">抓帧间隔</span>
          <el-select v-model="intervalSec" :disabled="running" style="width: 120px">
            <el-option v-for="s in [1, 2, 3, 5, 10]" :key="s" :value="s" :label="`每 ${s} 秒`" />
          </el-select>
        </div>

        <div class="field">
          <span class="field-label">连续确认</span>
          <el-select v-model="requiredStreak" :disabled="running" style="width: 130px">
            <el-option v-for="s in [1, 2, 3, 5]" :key="s" :value="s" :label="`${s} 帧`" />
          </el-select>
        </div>

        <div class="field">
          <span class="field-label">灵敏度</span>
          <el-select v-model="sensitivity" :disabled="learning" style="width: 130px">
            <el-option value="conservative" label="保守（少误报）" />
            <el-option value="balanced" label="均衡" />
            <el-option value="sensitive" label="灵敏（少漏报）" />
          </el-select>
        </div>

        <div class="field">
          <span class="field-label">告警声音</span>
          <el-switch v-model="soundOn" :disabled="running" />
        </div>

        <div class="actions">
          <el-button v-if="!running" type="primary" :loading="starting" @click="start">
            <el-icon><VideoPlay /></el-icon> 开始监控
          </el-button>
          <el-button v-else type="danger" plain @click="stop">
            <el-icon><VideoPause /></el-icon> 停止监控
          </el-button>
          <el-button :disabled="!running || learning" @click="resetReference">
            <el-icon><Aim /></el-icon> 重设基准
          </el-button>
          <el-button :disabled="!running || learning" :loading="learning" @click="relearn">
            <el-icon><Refresh /></el-icon> 重新学习场景
          </el-button>
        </div>
      </div>

      <el-alert
        v-if="errorMessage"
        :title="errorMessage"
        type="error"
        show-icon
        :closable="false"
        style="margin-top: 10px"
      />
      <div v-else class="hint">
        点开始后会先采 <b>{{ LEARN_FRAMES }}</b> 帧画面学习「正常波动幅度」，据此自动校准各算法阈值——
        不同场景的正常抖动差一个数量级，用固定阈值必然要么狂报、要么不报。
        学习期不产生告警；学习完成后当前画面自动成为参考基准。换机位、改光照、换场景时点「重新学习场景」。
        只选 1 个算法时 1 票即告警，选 ≥2 个时需 ≥2 票。
      </div>
    </div>

    <!-- 预览 + 状态 -->
    <div class="live-grid">
      <div class="ctd-card preview-card" :class="{ alarming: alarmActive }">
        <div class="preview-head">
          <span class="dot" :class="{ on: running, learning }" />
          <span v-if="learning">学习场景基线 {{ learnProgress }}/{{ learnTotal }}</span>
          <span v-else>{{ running ? 'LIVE · 摄像头采集中' : '已停止' }}</span>
          <span class="spacer" />
          <span v-if="learning" class="ctd-mono">采集中，暂不告警</span>
          <span v-else-if="last" class="ctd-mono">
            {{ formatTime(last.timestampMs) }} · {{ last.elapsedMs }} ms
          </span>
        </div>

        <div class="video-wrap">
          <video ref="videoEl" autoplay playsinline muted />
          <div v-if="!running" class="video-placeholder">
            <el-icon><VideoCamera /></el-icon>
            <p>摄像头未开启</p>
          </div>
          <div v-if="learning" class="learn-overlay">
            <div class="learn-title">正在学习当前场景的正常波动…</div>
            <el-progress
              :percentage="Math.round((learnProgress / learnTotal) * 100)"
              :stroke-width="6"
              :show-text="false"
              color="var(--ctd-primary)"
            />
            <div class="learn-sub">
              {{ learnProgress }} / {{ learnTotal }} 帧 · 结束后自动校准各算法阈值
            </div>
          </div>
          <div v-else-if="alarmActive" class="alarm-overlay">
            <div class="alarm-title">检测到镜头{{ typeText(last?.tamperType) }}</div>
            <div class="alarm-sub">
              {{ last?.votes }} 个算法告警 · 连续 {{ last?.streak }} 帧
            </div>
          </div>
        </div>

        <div class="preview-foot">
          <span>判定：{{ learning ? '学习期（不产生告警）' : last?.verdict || '等待第一帧' }}</span>
          <span v-if="learnedOnce && !learning" class="score-pill ok" :title="calibTip">阈值已校准</span>
          <span v-if="last" class="score-pill" :class="{ bad: last.frameTampered }">
            集成分数 {{ last.score.toFixed(2) }}
          </span>
        </div>
      </div>

      <div class="side-col">
        <div class="ctd-card stat-card">
          <div class="stat-row">
            <div class="stat">
              <span class="label">运行时长</span>
              <strong>{{ elapsedText }}</strong>
            </div>
            <div class="stat">
              <span class="label">已检测帧</span>
              <strong>{{ session?.frameCount ?? 0 }}</strong>
            </div>
            <div class="stat">
              <span class="label">告警次数</span>
              <strong :class="{ danger: (session?.alarmCount ?? 0) > 0 }">{{ session?.alarmCount ?? 0 }}</strong>
            </div>
          </div>
        </div>

        <div class="ctd-card ref-card">
          <div class="ref-head">
            <span>参考基准</span>
            <el-button size="small" text type="primary" :disabled="!running" @click="resetReference">重设</el-button>
          </div>
          <div class="ref-body">
            <img v-if="session" :src="referenceSrc" alt="参考帧" />
            <div v-else class="ref-empty">开始监控后生成</div>
          </div>
          <p class="ref-tip">帧差 / ORB / 边缘消失率都以这张图为基准</p>
        </div>

        <div class="ctd-card">
          <div class="ref-head"><span>本帧各算法</span></div>
          <div v-if="!last" class="ref-empty" style="height: 80px">暂无数据</div>
          <div v-else class="algo-scores">
            <div v-for="a in last.algorithms" :key="a.algorithm" class="algo-score-row">
              <span class="algo-name" :title="a.note">{{ a.displayName }}</span>
              <div class="bar">
                <div
                  class="bar-inner"
                  :style="{ width: Math.min(100, a.score * 100) + '%' }"
                  :class="{ bad: a.tampered }"
                />
                <span class="bar-mark" />
              </div>
              <span class="algo-val" :class="{ bad: a.tampered }">
                {{ a.skipped ? '跳过' : a.score.toFixed(2) }}
              </span>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- 算法选择 -->
    <el-collapse v-model="openPanels" class="ctd-card algo-collapse">
      <el-collapse-item name="algo">
        <template #title>
          <span class="collapse-title">
            检测算法
            <span class="sub">已选 {{ selected.length }} 个；实时链路用默认参数，阈值需要调整时在这里改</span>
          </span>
        </template>
        <AlgorithmParams
          :algorithms="liveAlgorithms"
          :selected="selected"
          :params="params"
          @update:selected="selected = $event"
          @update:params="params = $event"
        />
      </el-collapse-item>
    </el-collapse>

    <!-- 记录 -->
    <div class="ctd-card">
      <h3 class="ctd-card-title">
        <el-icon><Tickets /></el-icon> 监控记录
        <span class="sub">仅保存在当前页面，最多 200 条；刷新即清空</span>
        <div class="head-actions">
          <el-button size="small" text :disabled="!records.length" @click="exportCsv">导出 CSV</el-button>
          <el-button size="small" text :disabled="!records.length" @click="records = []">清空</el-button>
        </div>
      </h3>

      <el-table :data="records" height="320" size="small" empty-text="开始监控后这里会记录每一帧">
        <el-table-column prop="frameIndex" label="#" width="60" />
        <el-table-column prop="time" label="时间" width="100" />
        <el-table-column label="结果" width="110">
          <template #default="{ row }">
            <el-tag v-if="row.alarm" size="small" type="danger" effect="dark">告警</el-tag>
            <el-tag v-else-if="row.tampered" size="small" type="warning" effect="plain">疑似</el-tag>
            <el-tag v-else size="small" type="success" effect="plain">正常</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="类型" width="100">
          <template #default="{ row }">{{ typeText(row.tamperType) }}</template>
        </el-table-column>
        <el-table-column prop="votes" label="票数" width="70" />
        <el-table-column label="分数" width="90">
          <template #default="{ row }">
            <span :class="{ 'danger-text': row.tampered }">{{ row.score.toFixed(2) }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="verdict" label="判定" min-width="240" show-overflow-tooltip />
        <el-table-column prop="elapsedMs" label="耗时" width="90">
          <template #default="{ row }">{{ row.elapsedMs }} ms</template>
        </el-table-column>
        <el-table-column label="现场" width="70">
          <template #default="{ row }">
            <el-image
              v-if="row.alarmImageUrl"
              :src="row.alarmImageUrl"
              :preview-src-list="[row.alarmImageUrl]"
              fit="cover"
              style="width: 42px; height: 28px; border-radius: 4px"
              preview-teleported
            />
            <span v-else class="muted">—</span>
          </template>
        </el-table-column>
      </el-table>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { liveApi } from '@/api'
import AlgorithmParams from '@/components/AlgorithmParams.vue'
import { useAppStore } from '@/store/app'
import type { LiveFrameResult, LiveRecord, LiveSessionInfo } from '@/types'

const store = useAppStore()

const TYPE_TEXT: Record<string, string> = {
  normal: '正常',
  covered: '遮挡',
  defocused: '失焦/模糊',
  moved: '移位/转向'
}

const videoEl = ref<HTMLVideoElement>()
const canvas = document.createElement('canvas')

const devices = ref<{ deviceId: string; label: string }[]>([])
const deviceId = ref('')
const intervalSec = ref(3)
const requiredStreak = ref(2)
const soundOn = ref(true)

const selected = ref<string[]>([])
const params = ref<Record<string, Record<string, number>>>({})
const openPanels = ref<string[]>([])

const running = ref(false)
const starting = ref(false)
const session = ref<LiveSessionInfo | null>(null)
const last = ref<LiveFrameResult | null>(null)
const records = ref<LiveRecord[]>([])
const alarmActive = ref(false)
const errorMessage = ref('')
const refVersion = ref(0)
const nowTick = ref(Date.now())

/**
 * 学习期：开始监控后先采一段正常画面，按各算法主指标的分位数回填阈值。
 * 绝对阈值没法跨场景通用（户外街景与室内房间的正常波动差一个数量级），
 * 实测默认阈值在真实画面上会让主力算法几乎帧帧告警、辅助算法一次不报。
 */
const LEARN_FRAMES = 20
const LEARN_INTERVAL_MS = 500
const learning = ref(false)
const learnProgress = ref(0)
const learnTotal = ref(LEARN_FRAMES)
const learnedOnce = ref(false)
const calibTip = ref('')

/**
 * 灵敏度：标定出的是「正常波动的上界」，真正当阈值用还要留多少余量。
 * 余量越小越容易报警（漏报少、误报多），越大越保守。
 */
type Sensitivity = 'conservative' | 'balanced' | 'sensitive'
const SENSITIVITY_TEXT: Record<Sensitivity, string> = {
  conservative: '保守',
  balanced: '均衡',
  sensitive: '灵敏'
}
const SENSITIVITY_FACTORS: Record<Sensitivity, { higher: number; lower: number }> = {
  conservative: { higher: 1.6, lower: 0.65 },
  balanced: { higher: 1.25, lower: 0.8 },
  sensitive: { higher: 0.95, lower: 0.9 }
}
const sensitivity = ref<Sensitivity>('balanced')

/** 学习期算出的各算法「正常波动上界」，换灵敏度时直接复用，不必重学 */
interface Baseline {
  base: number
  direction: string
  param: string
  n: number
}
const baseline = ref<Record<string, Baseline>>({})

let stream: MediaStream | null = null
let timer: number | null = null
let ticker: number | null = null
let inflight = false
let stopping = false
let failCount = 0
let startedAt = 0
let alarmTimer: number | null = null
let audioCtx: AudioContext | null = null

/** 实时监控不需要 YOLO（每帧几十毫秒且结果与篡改判定无关），其余算法都可用 */
const liveAlgorithms = computed(() => store.algorithms.filter((a) => a.kind !== 'yolo'))

const referenceSrc = computed(() => {
  const id = session.value?.sessionId
  return id ? `/api/live/sessions/${id}/reference?v=${refVersion.value}` : ''
})

const elapsedText = computed(() => {
  if (!running.value || !startedAt) return '—'
  const s = Math.max(0, Math.floor((nowTick.value - startedAt) / 1000))
  return `${String(Math.floor(s / 60)).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`
})

function typeText(type?: string | null): string {
  return TYPE_TEXT[type || 'normal'] || type || '正常'
}

function formatTime(ts: number): string {
  return new Date(ts).toLocaleTimeString('zh-CN', { hour12: false })
}

// ------------------------------------------------------------------ 初始化

watch(
  () => store.algorithms.length,
  () => {
    if (selected.value.length || !store.classicAlgorithms.length) return
    selected.value = store.classicAlgorithms.map((a) => a.name)
    const next: Record<string, Record<string, number>> = {}
    for (const algo of store.classicAlgorithms) {
      next[algo.name] = {}
      for (const p of algo.params) next[algo.name][p.key] = p.default
    }
    params.value = next
  },
  { immediate: true }
)

onMounted(async () => {
  await listDevices()
  ticker = window.setInterval(() => (nowTick.value = Date.now()), 1000)
})

onBeforeUnmount(() => {
  stop()
  if (ticker) window.clearInterval(ticker)
})

/** 未授权时 label 为空，先请求一次权限再枚举 */
async function listDevices() {
  try {
    const list = await navigator.mediaDevices.enumerateDevices()
    devices.value = list
      .filter((d) => d.kind === 'videoinput')
      .map((d, i) => ({ deviceId: d.deviceId, label: d.label || `摄像头 ${i + 1}` }))
    if (!deviceId.value && devices.value.length) {
      deviceId.value = devices.value[0].deviceId
    }
  } catch {
    devices.value = []
  }
}

// ------------------------------------------------------------------ 采集

function capture(): Promise<Blob> {
  const video = videoEl.value
  if (!video || !video.videoWidth) {
    return Promise.reject(new Error('摄像头画面尚未就绪'))
  }
  // 长边压到 1280：经 SSH 桥上传，原始 4K 帧只会拖慢链路，对检测没有帮助
  const maxWidth = 1280
  const scale = Math.min(1, maxWidth / video.videoWidth)
  canvas.width = Math.max(1, Math.round(video.videoWidth * scale))
  canvas.height = Math.max(1, Math.round(video.videoHeight * scale))
  const ctx = canvas.getContext('2d')
  if (!ctx) return Promise.reject(new Error('无法获取画布上下文'))
  ctx.drawImage(video, 0, 0, canvas.width, canvas.height)
  return new Promise((resolve, reject) => {
    canvas.toBlob(
      (blob) => (blob ? resolve(blob) : reject(new Error('抓帧失败'))),
      'image/jpeg',
      0.85
    )
  })
}

function waitReady(timeoutMs = 4000): Promise<void> {
  const video = videoEl.value
  if (!video) return Promise.reject(new Error('视频元素未就绪'))
  const started = Date.now()
  return new Promise((resolve, reject) => {
    const check = () => {
      if (video.videoWidth > 0 && video.readyState >= 2) {
        resolve()
      } else if (Date.now() - started > timeoutMs) {
        reject(new Error('摄像头画面加载超时'))
      } else {
        window.setTimeout(check, 120)
      }
    }
    check()
  })
}

async function start() {
  if (starting.value || running.value) return
  errorMessage.value = ''
  if (!selected.value.length) {
    errorMessage.value = '请至少选择一个检测算法'
    return
  }
  starting.value = true
  try {
    stream = await navigator.mediaDevices.getUserMedia({
      video: deviceId.value
        ? { deviceId: { exact: deviceId.value }, width: { ideal: 1280 }, height: { ideal: 720 } }
        : { width: { ideal: 1280 }, height: { ideal: 720 } },
      audio: false
    })
    if (videoEl.value) {
      videoEl.value.srcObject = stream
      await videoEl.value.play()
    }
    await waitReady()
    await listDevices()

    const reference = await capture()
    session.value = await liveApi.create(
      reference,
      selected.value,
      params.value,
      requiredStreak.value
    )
    refVersion.value++

    running.value = true
    startedAt = Date.now()
    failCount = 0
    alarmActive.value = false
    records.value = []
    last.value = null
    learnedOnce.value = false
    calibTip.value = ''
    baseline.value = {}

    // 先学习当前场景的正常波动，再进入告警状态
    await learnScene()
    if (!running.value) return
    timer = window.setInterval(tick, intervalSec.value * 1000)
  } catch (e: any) {
    const name = e?.name || ''
    errorMessage.value =
      name === 'NotAllowedError'
        ? '摄像头权限被拒绝：请在浏览器地址栏允许摄像头访问后重试'
        : name === 'NotFoundError'
          ? '未找到摄像头设备'
          : e?.message || '启动失败'
    stop()
  } finally {
    starting.value = false
  }
}

function sleep(ms: number) {
  return new Promise((resolve) => window.setTimeout(resolve, ms))
}

function percentile(sorted: number[], p: number): number {
  if (!sorted.length) return NaN
  const idx = (p / 100) * (sorted.length - 1)
  const lo = Math.floor(idx)
  const hi = Math.ceil(idx)
  if (lo === hi) return sorted[lo]
  return sorted[lo] + (sorted[hi] - sorted[lo]) * (idx - lo)
}

function collectSamples(samples: Record<string, number[]>, result: LiveFrameResult) {
  for (const algo of result.algorithms) {
    if (algo.skipped) continue
    const meta = store.algorithms.find((a) => a.name === algo.algorithm)
    const primary = meta?.primaryMetric
    if (!primary) continue
    const value = algo.detail?.[primary.field]
    if (typeof value !== 'number' || !Number.isFinite(value)) continue
    ;(samples[algo.algorithm] ||= []).push(value)
  }
}

/**
 * 第一步：从学习期样本里算出各算法「正常波动的上界」。
 * 越大越异常 → 取 p95；越小越异常 → 取 p5。
 * 上界与阈值分开存，是为了换灵敏度时不用重新采集（采集要占用摄像头时间）。
 */
function buildBaseline(samples: Record<string, number[]>): number {
  const next: Record<string, Baseline> = {}
  for (const name of selected.value) {
    const meta = store.algorithms.find((a) => a.name === name)
    const primary = meta?.primaryMetric
    const values = samples[name]
    // 样本太少时 p95 没有意义，宁可用原来的默认阈值
    if (!primary || !values || values.length < 6) continue
    const sorted = [...values].sort((a, b) => a - b)
    const base = percentile(sorted, primary.direction === 'higher' ? 95 : 5)
    if (!Number.isFinite(base)) continue
    next[name] = { base, direction: primary.direction, param: primary.param, n: values.length }
  }
  baseline.value = next
  return Object.keys(next).length
}

/** 第二步：按当前灵敏度把上界换算成阈值并写进 params，返回可读说明 */
function applySensitivity(): string[] {
  const next: Record<string, Record<string, number>> = { ...params.value }
  const notes: string[] = []
  const factor = SENSITIVITY_FACTORS[sensitivity.value]
  for (const [name, item] of Object.entries(baseline.value)) {
    const meta = store.algorithms.find((a) => a.name === name)
    const isHigher = item.direction === 'higher'
    const target = isHigher ? item.base * factor.higher : item.base * factor.lower
    const spec = meta?.params.find((p) => p.key === item.param)
    const clamped = Number((spec ? Math.min(spec.max, Math.max(spec.min, target)) : target).toFixed(3))
    next[name] = { ...(next[name] || {}), [item.param]: clamped }
    const before = params.value[name]?.[item.param]
    const tail = before !== undefined && Math.abs(before - clamped) > 1e-6 ? `（原 ${before}）` : ''
    notes.push(`${meta?.displayName || name} ${item.param}=${clamped}${tail}`)
  }
  params.value = next
  return notes
}

/** 开始监控 / 重新学习时调用：采 20 帧正常画面，校准各算法阈值 */
async function learnScene() {
  if (!session.value) return
  learning.value = true
  learnProgress.value = 0
  learnTotal.value = LEARN_FRAMES
  const samples: Record<string, number[]> = {}
  try {
    for (let i = 0; i < LEARN_FRAMES; i++) {
      if (!running.value || !session.value) break
      try {
        const blob = await capture()
        // probe=true：后端只回指标，不推进帧号也不告警
        const result = await liveApi.frame(session.value.sessionId, blob, true)
        collectSamples(samples, result)
      } catch {
        // 学习期单帧失败不影响整体，继续采下一帧
      }
      learnProgress.value = i + 1
      if (i < LEARN_FRAMES - 1) await sleep(LEARN_INTERVAL_MS)
    }
  } finally {
    learning.value = false
  }

  const count = buildBaseline(samples)
  const notes = count ? applySensitivity() : []
  if (notes.length) {
    learnedOnce.value = true
    calibTip.value = `按 ${LEARN_FRAMES} 帧正常画面校准（${SENSITIVITY_TEXT[sensitivity.value]}）：\n${notes.join('\n')}`
    ElMessage.success(`已按当前场景校准 ${notes.length} 个算法的阈值`)
  } else if (!learnedOnce.value) {
    ElMessage.warning('学习期有效帧太少，沿用原阈值')
  }

  // 新阈值同步给后端（会话建完才知道这个场景的正常波动有多大）
  if (running.value && session.value && notes.length) {
    try {
      session.value = await liveApi.updateParams(session.value.sessionId, params.value)
    } catch {
      ElMessage.warning('阈值下发失败，本场景仍按原参数判定')
    }
  }

  // 学习期的画面就是「正常基准」，顺便设为参考帧并清零连续告警计数
  if (running.value && session.value) {
    try {
      const blob = await capture()
      session.value = await liveApi.resetReference(session.value.sessionId, blob)
      refVersion.value++
      alarmActive.value = false
    } catch {
      // 参考帧更新失败沿用原基准，不影响后续检测
    }
  }
}

/** 换机位 / 改光照 / 换场景后重学一次，避免阈值与当前画面不匹配 */
async function relearn() {
  if (!running.value || !session.value || learning.value) return
  await learnScene()
  ElMessage.info('场景基线已更新')
}

/** 换灵敏度：用已学到的基线重算阈值，不用重新采集 */
watch(sensitivity, async (value) => {
  if (!Object.keys(baseline.value).length) return
  const notes = applySensitivity()
  learnedOnce.value = notes.length > 0
  calibTip.value = `按 ${learnTotal.value} 帧正常画面校准（${SENSITIVITY_TEXT[value]}）：\n${notes.join('\n')}`
  if (running.value && session.value && notes.length) {
    try {
      session.value = await liveApi.updateParams(session.value.sessionId, params.value)
    } catch {
      // 下发失败也不打断监控，页面上仍显示本地阈值
    }
  }
  ElMessage.info(`灵敏度改为「${SENSITIVITY_TEXT[value]}」，已重算 ${notes.length} 个算法的阈值`)
})

async function tick() {
  if (inflight || !running.value || !session.value) return
  inflight = true
  try {
    const blob = await capture()
    const result = await liveApi.frame(session.value.sessionId, blob)
    applyResult(result)
    failCount = 0
  } catch (e: any) {
    failCount++
    if (failCount >= 3) {
      errorMessage.value = `连续 ${failCount} 次检测失败：${e?.message || '网络异常'}，已停止监控`
      stop()
    }
  } finally {
    inflight = false
  }
}

function applyResult(result: LiveFrameResult) {
  last.value = result
  if (session.value) {
    session.value.frameCount = result.frameIndex
    if (result.alarm) session.value.alarmCount++
  }

  records.value.unshift({
    frameIndex: result.frameIndex,
    time: formatTime(result.timestampMs),
    alarm: result.alarm,
    tampered: result.frameTampered,
    // 未达判定门槛时票数可能来自单个算法，类型会误导（"判定正常"却写"遮挡"），统一归零
    tamperType: result.frameTampered ? result.tamperType : 'normal',
    votes: result.votes,
    score: result.score,
    verdict: result.verdict,
    elapsedMs: result.elapsedMs,
    alarmImageUrl: result.alarmImageUrl
  })
  if (records.value.length > 200) records.value.pop()

  if (result.alarm) {
    alarmActive.value = true
    beep()
    if (alarmTimer) window.clearTimeout(alarmTimer)
    alarmTimer = window.setTimeout(() => (alarmActive.value = false), 5000)
  }
}

/** 告警蜂鸣：复用同一个 AudioContext，避免每次新建 */
function beep() {
  if (!soundOn.value) return
  try {
    audioCtx = audioCtx || new AudioContext()
    const ctx = audioCtx
    const osc = ctx.createOscillator()
    const gain = ctx.createGain()
    osc.type = 'square'
    osc.frequency.value = 880
    gain.gain.setValueAtTime(0.0001, ctx.currentTime)
    gain.gain.exponentialRampToValueAtTime(0.08, ctx.currentTime + 0.02)
    gain.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + 0.35)
    osc.connect(gain).connect(ctx.destination)
    osc.start()
    osc.stop(ctx.currentTime + 0.36)
  } catch {
    // 浏览器不允许自动播放时静默失败
  }
}

async function resetReference() {
  if (!running.value || !session.value) return
  try {
    const blob = await capture()
    session.value = await liveApi.resetReference(session.value.sessionId, blob)
    refVersion.value++
    alarmActive.value = false
    ElMessage.success('已把当前画面设为新基准，连续告警计数已清零')
  } catch (e: any) {
    ElMessage.error(e?.message || '重设基准失败')
  }
}

async function stop() {
  if (stopping) return
  stopping = true
  running.value = false
  if (timer) {
    window.clearInterval(timer)
    timer = null
  }
  if (alarmTimer) {
    window.clearTimeout(alarmTimer)
    alarmTimer = null
  }
  // 先等在飞的那一帧落地再关会话，否则它会因为会话已删而报 404
  const deadline = Date.now() + 2500
  while (inflight && Date.now() < deadline) {
    await new Promise((resolve) => window.setTimeout(resolve, 80))
  }
  if (session.value) {
    const id = session.value.sessionId
    liveApi.close(id).catch(() => {})
    session.value = null
  }
  stream?.getTracks().forEach((track) => track.stop())
  stream = null
  if (videoEl.value) videoEl.value.srcObject = null
  alarmActive.value = false
  startedAt = 0
  stopping = false
}

// ------------------------------------------------------------------ 导出

function exportCsv() {
  const header = ['序号', '时间', '告警', '疑似篡改', '类型', '票数', '分数', '判定', '耗时ms']
  const lines = records.value.map((r) => [
    r.frameIndex,
    r.time,
    r.alarm ? '告警' : '否',
    r.tampered ? '是' : '否',
    typeText(r.tamperType),
    r.votes,
    r.score.toFixed(3),
    `"${r.verdict.replace(/"/g, '""')}"`,
    r.elapsedMs
  ])
  const csv = [header.join(','), ...lines.map((l) => l.join(','))].join('\n')
  const blob = new Blob(['\uFEFF' + csv], { type: 'text/csv;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = `live-monitor-${new Date().toISOString().slice(0, 19).replace(/[:T]/g, '-')}.csv`
  a.click()
  URL.revokeObjectURL(url)
}
</script>

<style scoped>
.live-page {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.sub {
  margin-left: 8px;
  font-size: 12px;
  font-weight: 400;
  color: var(--ctd-text-secondary);
}

.control-row {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-end;
  gap: 16px;
  margin-top: 12px;
}

.field {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.field-label {
  font-size: 12px;
  color: var(--ctd-text-secondary);
}

.actions {
  display: flex;
  gap: 8px;
  margin-left: auto;
}

.hint {
  margin-top: 12px;
  font-size: 12px;
  line-height: 1.7;
  color: var(--ctd-text-secondary);
}

.live-grid {
  display: grid;
  grid-template-columns: minmax(0, 1.55fr) minmax(280px, 1fr);
  gap: 14px;
  align-items: start;
}

.preview-card {
  transition: box-shadow 0.2s ease, border-color 0.2s ease;
}

.preview-card.alarming {
  border-color: var(--ctd-danger);
  box-shadow: 0 0 0 3px rgba(229, 72, 77, 0.16);
  animation: live-pulse 1.2s ease-in-out infinite;
}

@keyframes live-pulse {
  0%,
  100% {
    box-shadow: 0 0 0 3px rgba(229, 72, 77, 0.14);
  }
  50% {
    box-shadow: 0 0 0 7px rgba(229, 72, 77, 0.05);
  }
}

.preview-head {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  color: var(--ctd-text-secondary);
}

.preview-head .dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #c0c6d2;
}

.preview-head .dot.on {
  background: var(--ctd-success);
  box-shadow: 0 0 0 3px rgba(34, 160, 107, 0.18);
}

.preview-head .dot.learning {
  background: var(--ctd-warning, #e8a33d);
  box-shadow: 0 0 0 3px rgba(232, 163, 61, 0.2);
  animation: live-pulse 1.4s ease-in-out infinite;
}

.preview-head .spacer {
  flex: 1;
}

.video-wrap {
  position: relative;
  margin-top: 10px;
  aspect-ratio: 16 / 9;
  background: #0f1420;
  border-radius: 10px;
  overflow: hidden;
}

.video-wrap video {
  width: 100%;
  height: 100%;
  object-fit: contain;
  display: block;
}

.video-placeholder {
  position: absolute;
  inset: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 8px;
  color: #667085;
  font-size: 13px;
}

.video-placeholder .el-icon {
  font-size: 34px;
}

.learn-overlay {
  position: absolute;
  left: 12px;
  right: 12px;
  bottom: 12px;
  padding: 12px 16px;
  border-radius: 8px;
  background: rgba(18, 24, 38, 0.82);
  color: #fff;
  animation: fade-in 0.18s ease;
}

.learn-title {
  font-size: 13px;
  font-weight: 600;
  margin-bottom: 8px;
}

.learn-sub {
  margin-top: 8px;
  font-size: 12px;
  opacity: 0.8;
  font-variant-numeric: tabular-nums;
}

.alarm-overlay {
  position: absolute;
  left: 12px;
  right: 12px;
  bottom: 12px;
  padding: 10px 14px;
  border-radius: 8px;
  background: rgba(229, 72, 77, 0.92);
  color: #fff;
  animation: fade-in 0.18s ease;
}

@keyframes fade-in {
  from {
    opacity: 0;
    transform: translateY(4px);
  }
  to {
    opacity: 1;
    transform: none;
  }
}

.alarm-title {
  font-size: 15px;
  font-weight: 600;
}

.alarm-sub {
  margin-top: 2px;
  font-size: 12px;
  opacity: 0.9;
}

.preview-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  margin-top: 10px;
  font-size: 13px;
}

.score-pill {
  padding: 2px 10px;
  border-radius: 999px;
  background: var(--ctd-primary-soft);
  color: var(--ctd-primary);
  font-size: 12px;
  font-variant-numeric: tabular-nums;
}

.score-pill.bad {
  background: #fdecec;
  color: var(--ctd-danger);
}

.score-pill.ok {
  background: #eaf7f1;
  color: var(--ctd-success);
  cursor: help;
}

.side-col {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.stat-row {
  display: flex;
  gap: 12px;
}

.stat {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.stat .label {
  font-size: 12px;
  color: var(--ctd-text-secondary);
}

.stat strong {
  font-size: 20px;
  font-variant-numeric: tabular-nums;
}

.stat strong.danger {
  color: var(--ctd-danger);
}

.ref-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-size: 13px;
  font-weight: 600;
}

.ref-body {
  margin-top: 8px;
  aspect-ratio: 16 / 9;
  background: #f2f4f8;
  border-radius: 8px;
  overflow: hidden;
  display: flex;
  align-items: center;
  justify-content: center;
}

.ref-body img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.ref-empty {
  display: flex;
  align-items: center;
  justify-content: center;
  color: #98a1b0;
  font-size: 12px;
}

.ref-tip {
  margin: 8px 0 0;
  font-size: 11px;
  color: #98a1b0;
}

.algo-scores {
  display: flex;
  flex-direction: column;
  gap: 7px;
  margin-top: 10px;
}

.algo-score-row {
  display: grid;
  grid-template-columns: 92px 1fr 40px;
  align-items: center;
  gap: 8px;
  font-size: 12px;
}

.algo-name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--ctd-text-secondary);
}

.bar {
  position: relative;
  height: 6px;
  border-radius: 999px;
  background: #eef1f6;
  overflow: hidden;
}

.bar-inner {
  height: 100%;
  border-radius: 999px;
  background: #9db4e4;
  transition: width 0.25s ease;
}

.bar-inner.bad {
  background: var(--ctd-danger);
}

.bar-mark {
  position: absolute;
  left: 50%;
  top: -2px;
  bottom: -2px;
  width: 1px;
  background: #c7cedb;
}

.algo-val {
  text-align: right;
  font-variant-numeric: tabular-nums;
  color: var(--ctd-text-secondary);
}

.algo-val.bad {
  color: var(--ctd-danger);
  font-weight: 600;
}

.algo-collapse {
  padding: 0 16px;
}

.collapse-title {
  display: flex;
  align-items: baseline;
  font-size: 14px;
  font-weight: 600;
}

.head-actions {
  margin-left: auto;
  display: flex;
  gap: 4px;
}

.danger-text {
  color: var(--ctd-danger);
  font-weight: 600;
}

.muted {
  color: #c0c6d2;
}

@media (max-width: 1100px) {
  .live-grid {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
