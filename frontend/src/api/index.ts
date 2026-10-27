import axios from 'axios'
import type { AxiosError, AxiosInstance, AxiosResponse, InternalAxiosRequestConfig } from 'axios'
import { ElMessage } from 'element-plus'

/**
 * axios 实例：后端统一返回 `{ code, message, data }`，这里直接解包 `data`。
 *
 * 本系统只保留低频巡检，因此 API 面收敛到 `algo` 健康检查 + `sparse` 一组接口。
 *
 * 「服务端时间」：后端每个响应都回填响应头 `X-Server-Now`（服务端毫秒时间戳）。
 * 前端提交抽样时如果没显式给 tsMs，就用最近一次响应的 `X-Server-Now` 兜底，
 * **而不是**浏览器的 `Date.now()` —— 这样即使检测机本身的系统时钟被篡改，
 * 采样时间仍然以服务端时钟为准，避免用被拨动的时间去绕过「跨采样点」
 * 「时段槽」「基线冷启动」这些依赖真实时间间隔的判据。
 */
export const DEFAULT_TIMEOUT_MS = 15000

const http = axios.create({
  baseURL: '/api',
  // 默认 15s：本系统全部是「本机算法层 / 内网后端」的低频巡检接口，
  // 正常 P99 在百毫秒级；15s 是机器挂起与用户可感知卡顿之间的折中。
  // 重活（提交抽样、标定、模板补录）在各自接口里单独放长，见 REQUEST_TIMEOUTS。
  timeout: DEFAULT_TIMEOUT_MS
})

// ------------------------------------------------------------------ 超时
export class ApiTimeoutError extends Error {
  readonly timeoutMs: number
  readonly url?: string

  constructor(timeoutMs: number, url?: string) {
    super(`请求超时（${Math.round(timeoutMs / 1000)} 秒未响应）`)
    this.name = 'ApiTimeoutError'
    this.timeoutMs = timeoutMs
    this.url = url
  }
}

export class ApiError extends Error {
  readonly status?: number
  readonly code?: number

  constructor(message: string, status?: number, code?: number) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
  }
}

/**
 * 把 axios 的原始错误转成一句人能读懂的话。
 *
 * 超时（ECONNABORTED / ETIMEDOUT）必须单独判：axios 自带的
 * `timeout of 15000ms exceeded` 对使用者毫无信息量，而且没有提示去查什么。
 */
export function toApiMessage(error: unknown, fallback = '网络异常'): string {
  const err = error as AxiosError | undefined
  const ax = err?.isAxiosError ? err : undefined
  const code = ax?.code
  const status = ax?.response?.status
  const data = ax?.response?.data as Record<string, unknown> | undefined
  const serverMsg = (data?.message as string) || (data?.detail as string) || ''

  if (code === 'ECONNABORTED' || code === 'ETIMEDOUT') {
    const timeoutMs = (ax?.config as InternalAxiosRequestConfig | undefined)?.timeout
    if (typeof timeoutMs === 'number' && timeoutMs > 0) {
      return new ApiTimeoutError(timeoutMs, ax?.config?.url).message
    }
    return '请求超时：服务无响应'
  }
  if (serverMsg) return serverMsg
  if (status === 502) return '算法服务不可用，请确认 Python 算法层已启动'
  if (err instanceof Error && err.message) return err.message
  if (err && typeof err === 'object' && 'message' in err) return String((err as Error).message)
  return fallback
}

// ------------------------------------------------------------------ 服务端时间
let lastServerNowMs = 0
let lastServerNowAt = 0
let lastServerNowReceivedAt = 0

/** 最近一次响应头里的服务端时间（毫秒）；从未收到过则返回 0。 */
export function getLastServerNow(): number {
  return lastServerNowMs
}

/**
 * 最近一次响应的服务端时间外推到「此刻」（按单调时钟 elapsed 补偿）。
 *
 * 只用服务端时间做基准，绝不用本地墙钟：本地墙钟正是可能被篡改的东西。
 * 用 `performance.now()` 的差值外推，避免把两个时钟混在一起。
 */
export function serverNow(): number {
  if (!lastServerNowMs) return 0
  const elapsed = lastServerNowAt && typeof performance !== 'undefined'
    ? performance.now() - lastServerNowAt
    : 0
  const drift = lastServerNowReceivedAt ? Math.max(0, elapsed) : 0
  return Math.round(lastServerNowMs + drift)
}

/** 仅供测试与调试：重置服务端时间缓存。 */
export function __resetServerClock(): void {
  lastServerNowMs = 0
  lastServerNowAt = 0
  lastServerNowReceivedAt = 0
}

function rememberServerNow(response: AxiosResponse): void {
  const raw = response?.headers?.['x-server-now']
  const value = Array.isArray(raw) ? raw[0] : raw
  const ms = Number(value)
  if (!Number.isFinite(ms) || ms <= 0) return
  lastServerNowMs = ms
  lastServerNowAt = typeof performance !== 'undefined' ? performance.now() : 0
  lastServerNowReceivedAt = Date.now()
}

http.interceptors.response.use(
  (response) => {
    // 先记服务端时间：即便这次响应体是业务错误，服务端时钟信息依然有效。
    rememberServerNow(response)
    const body = response.data
    if (body && typeof body === 'object' && 'code' in body) {
      const envelope = body as { code: number; message?: string; data?: unknown }
      if (envelope.code === 0) {
        return envelope.data
      }
      const message = envelope.message || '请求失败'
      ElMessage.error(message)
      return Promise.reject(new ApiError(message, response.status, envelope.code))
    }
    return body
  },
  (error: unknown) => {
    const ax = error as AxiosError | undefined
    if (ax?.response) rememberServerNow(ax.response)
    const message = toApiMessage(error)
    ElMessage.error(message)
    return Promise.reject(
      Object.assign(
        new ApiError(message, ax?.response?.status),
        ax?.code ? { code: ax.code } : {}
      )
    )
  }
)

// ------------------------------------------------------------------ 超时取值
/**
 * 各接口的超时表（毫秒）。
 *
 * - 元数据/状态/健康检查这类读接口走默认 15s。
 * - 一次抽样最多 5 帧 JPEG，算法层要做基线重建 + 多尺度比较，实测百毫秒~数秒，
 *   但冷启动（首次载入 numpy/cv2 与全天基线）可能到几十秒，故给 120s。
 * - 标定/模板补录要落盘并算梯度模板，给 60s。
 */
export const REQUEST_TIMEOUTS = {
  default: DEFAULT_TIMEOUT_MS,
  sample: 120000,
  calibration: 60000
} as const

// ------------------------------------------------------------------ 类型
export interface AlgoHealth {
  online: boolean
  baseUrl: string
  detail?: {
    status?: string
    version?: string
    service?: string
    uptimeSeconds?: number
    cv2?: string
    numpy?: string
    cameras?: number
    dataDir?: string
  }
  error?: string
}

export interface SparseParamMeta {
  key: string
  label: string
  default: number
  min: number
  max: number
  step: number
  unit?: string
  description?: string
}

export interface SparseVerdictMeta {
  key: string
  label: string
  alarm?: boolean
  tamperType?: string
  description?: string
}

export interface SparseMeta {
  params: SparseParamMeta[]
  verdicts: SparseVerdictMeta[]
  defaults: Record<string, number>
}

export interface SparseCameraItem {
  cameraId: string
  name?: string
  /** 基线成熟度：empty / cold_start / warming / ready */
  status: string
  samples: number
  updatedMs?: number
}

/** L0/L1/L2 各层指标。字段名随算法层演进，前端只强类型化真正读取的部分。 */
export interface SparseL1Layer {
  bitExactDup?: number
  intraMad?: number
  intraChangeRatio?: number
  intraEdgeRate?: number
  intraOrbTranslation?: number
  intraSharpnessVar?: number
  sigmaPerFrame?: number
  lumaDrift?: number
  chromaDrift?: number
  quadrant?: string
  motionHigh?: boolean
  [key: string]: number | string | boolean | null | undefined
}

export interface SparseL0Layer {
  pixelOutlierRatio?: number
  changed?: { ignoredRatio?: number }
  bgShift?: { magnitude?: number; response?: number }
  scalarSource?: string
  baselineSharpness?: number
  baselineEntropy?: number
  baselineEdge?: number
  sharpnessDrop?: number
  entropyDrop?: number
  edgeDrop?: number
  landmarks?: SparseLandmarkCompare
  [key: string]: unknown
}

export interface SparseL2Layer {
  sharpness?: number
  entropy?: number
  edgeDensity?: number
  [key: string]: number | string | null | undefined
}

export interface SparseLayers {
  L2?: SparseL2Layer
  L1?: SparseL1Layer
  L0?: SparseL0Layer
  [key: string]: unknown
}

/** 参照物匹配结果（算法层 L0.landmarks.matches 单项）。 */
export interface SparseLandmarkMatch {
  id: number
  ok?: boolean
  score?: number
  scoreMin?: number
  magnitude?: number
  [key: string]: unknown
}

export interface SparseLandmarkCompare {
  configured?: boolean
  matches?: SparseLandmarkMatch[]
  consensus?: { magnitude?: number; spread?: number; moved?: boolean } | null
  [key: string]: unknown
}

export interface SparseSampleMatrix {
  dHist?: number
  dBurst?: number
  cell?: string
  baselineReady?: boolean
  freezeEvidence?: { d1?: boolean; d3?: boolean; gapMs?: number }
  [key: string]: unknown
}

/** 一次抽样的判定结果。字段较多，这里只声明前端真正读取的部分。 */
export interface SparseSampleResult {
  cameraId?: string
  tsMs?: number
  slot?: number
  verdict: string
  verdictLabel?: string
  level?: string
  alarm?: boolean
  tamperType?: string
  risk?: number
  confidence?: number
  reasons?: string[]
  matrix?: SparseSampleMatrix
  layers?: SparseLayers
  baseline?: { status?: string; samples?: number; admitted?: boolean; reason?: string }
  timingsMs?: Record<string, number>
  keyframe?: string
  baselineCompare?: string
  diffHeatmap?: string
  history?: Array<Record<string, unknown>>
  slots?: Array<Record<string, unknown>>
  [key: string]: unknown
}

export interface SparseLandmark {
  id?: number
  x: number
  y: number
  w: number
  h: number
  kind?: 'landmark' | 'ignore'
  label?: string
  hasPatch?: boolean
}

export interface SparseTemplateItem {
  id: string
  landmarkId: number
  label?: string
  source?: string
  createdAt?: number
  verdictAtTime?: string
  quality?: Record<string, number>
  hasPatch?: boolean
}

export interface SparseAuditItem {
  ts: number
  action: string
  actor?: string
  detail?: Record<string, unknown>
}

export interface SparseSlot {
  slot: number
  hour: number
  samples: number
  ready: boolean
  latestTs: number
}

export interface SparseHistoryItem {
  tsMs: number
  verdict: string
  label: string
  alarm: boolean
  risk: number
  tamperType: string
  dHist?: number
  dBurst?: number
  slot: number
}

export interface SparseState {
  cameraId: string
  name?: string
  createdMs?: number
  updatedMs?: number
  status?: string
  samples?: number
  slots?: SparseSlot[]
  history?: SparseHistoryItem[]
  landmarks?: SparseLandmark[]
  templates?: SparseTemplateItem[]
  audit?: SparseAuditItem[]
  baseline?: Array<Record<string, unknown>>
}

/** 保存标定后的回执：标定画面是否进了基线，以及原因。 */
export interface SparseLandmarkSaveResult {
  cameraId?: string
  landmarks?: SparseLandmark[]
  frameAdmitted?: boolean
  frameAdmitReason?: string
  [key: string]: unknown
}

export interface SparseTemplateAddResult {
  added?: boolean
  note?: string
  template?: SparseTemplateItem
  [key: string]: unknown
}

export interface SparseTemplateListResult {
  templates: SparseTemplateItem[]
  audit: SparseAuditItem[]
  limits?: Record<string, number>
}

// ------------------------------------------------------------------ 接口
export const algoApi = {
  health: () => http.get<unknown, AlgoHealth>('/algo/health')
}

export const sparseApi = {
  /** 参数元数据 + 判决字典 */
  meta: () => http.get<unknown, SparseMeta>('/sparse/meta'),

  /**
   * 提交一次抽样：frames 为 1~K 张图。
   *
   * `tsMs` 显式传入时原样提交（一键演示要靠它模拟「几小时后重放」）；
   * 传 0 / 不传时由服务端按自己的时钟定采样时间。
   */
  sample: (
    cameraId: string,
    frames: Blob[],
    tsMs: number = 0,
    name: string = '',
    reset: boolean = false,
    params: Record<string, number> = {}
  ) => {
    const form = new FormData()
    form.append('cameraId', cameraId)
    form.append('tsMs', String(tsMs ?? 0))
    form.append('name', name ?? '')
    form.append('reset', String(!!reset))
    form.append('params', JSON.stringify(params ?? {}))
    frames.forEach((frame, i) => form.append('frames', frame, `frame-${i}.jpg`))
    return http.post<unknown, SparseSampleResult>('/sparse/sample', form, {
      timeout: REQUEST_TIMEOUTS.sample
    })
  },

  cameras: () => http.get<unknown, { cameras: SparseCameraItem[] }>('/sparse/cameras'),

  state: (cameraId: string) => http.get<unknown, SparseState>(`/sparse/state/${cameraId}`),

  /** 重设基准：清空基线，保留参照物与模板库 */
  reset: (cameraId: string) =>
    http.post<unknown, unknown>(`/sparse/state/${cameraId}/reset`, undefined, {
      timeout: REQUEST_TIMEOUTS.calibration
    }),

  /** 删除机位（含基线、参照物、模板、审计） */
  remove: (cameraId: string) =>
    http.delete<unknown, unknown>(`/sparse/state/${cameraId}`, {
      timeout: REQUEST_TIMEOUTS.calibration
    }),

  /** 标定参照物 / 忽略区；frame 为可选画面（同时可入库为基线样本） */
  setLandmarks: (
    cameraId: string,
    landmarks: SparseLandmark[],
    frame?: Blob | null,
    clear: boolean = false,
    admitFrame: boolean = true
  ) => {
    const form = new FormData()
    form.append('landmarks', JSON.stringify(landmarks))
    form.append('clear', String(!!clear))
    form.append('admitFrame', String(admitFrame !== false))
    if (frame) form.append('file', frame, 'landmark-frame.jpg')
    return http.post<unknown, SparseLandmarkSaveResult>(`/sparse/landmarks/${cameraId}`, form, {
      timeout: REQUEST_TIMEOUTS.calibration
    })
  },

  getLandmarks: (cameraId: string) =>
    http.get<unknown, { cameraId: string; landmarks: SparseLandmark[] }>(
      `/sparse/landmarks/${cameraId}`
    ),

  clearLandmarks: (cameraId: string) =>
    http.delete<unknown, unknown>(`/sparse/landmarks/${cameraId}`, {
      timeout: REQUEST_TIMEOUTS.calibration
    }),

  /** 模板库：列出某机位所有参照物的模板与审计记录 */
  listTemplates: (cameraId: string) =>
    http.get<unknown, SparseTemplateListResult>(`/sparse/landmarks/${cameraId}/templates`),

  /** 给参照物补录一套外观模板（夜间/雨天/误报反馈） */
  addTemplate: (
    cameraId: string,
    landmarkId: number,
    frame: Blob,
    opts: {
      label?: string
      source?: string
      actor?: string
      verdictAtTime?: string
      force?: boolean
    } = {}
  ) => {
    const form = new FormData()
    form.append('landmarkId', String(landmarkId))
    form.append('file', frame, 'template.jpg')
    form.append('label', opts.label || '')
    form.append('source', opts.source || 'manual')
    form.append('actor', opts.actor || '')
    form.append('verdictAtTime', opts.verdictAtTime || '')
    form.append('force', String(!!opts.force))
    return http.post<unknown, SparseTemplateAddResult>(
      `/sparse/landmarks/${cameraId}/templates`,
      form,
      { timeout: REQUEST_TIMEOUTS.calibration }
    )
  },

  removeTemplate: (cameraId: string, templateId: string) =>
    http.delete<unknown, unknown>(`/sparse/landmarks/${cameraId}/templates/${templateId}`, {
      timeout: REQUEST_TIMEOUTS.calibration
    }),

  /** 模板预览图地址（梯度幅值渲染成 PNG） */
  templatePreviewUrl: (cameraId: string, templateId: string) =>
    `/api/sparse/templates/${cameraId}/${templateId}/preview`,

  /** 基线样本缩略图地址 */
  thumbUrl: (cameraId: string, slot: number, index: number) =>
    `/api/sparse/thumb/${cameraId}/${slot}/${index}`
}

export type { AxiosInstance }
export default http
