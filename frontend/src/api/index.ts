import axios from 'axios'
import { ElMessage } from 'element-plus'

/**
 * axios 实例：后端统一返回 `{ code, message, data }`，这里直接解包 `data`。
 *
 * 本系统只保留低频巡检，因此 API 面收敛到 `algo` 健康检查 + `sparse` 一组接口。
 */
const http = axios.create({
  baseURL: '/api',
  timeout: 0
})

http.interceptors.response.use(
  (response) => {
    const body = response.data
    if (body && typeof body === 'object' && 'code' in body) {
      if (body.code === 0) {
        return body.data
      }
      const message = body.message || '请求失败'
      ElMessage.error(message)
      return Promise.reject(new Error(message))
    }
    return body
  },
  (error) => {
    const message =
      error?.response?.data?.message ||
      error?.response?.data?.detail ||
      (error?.response?.status === 502
        ? '算法服务不可用，请确认 Python 算法层已启动'
        : error.message) ||
      '网络异常'
    ElMessage.error(message)
    return Promise.reject(Object.assign(new Error(message), { status: error?.response?.status }))
  }
)

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

export interface SparseLayers {
  L2?: Record<string, number | string | null>
  L1?: Record<string, number | string | null>
  L0?: Record<string, unknown>
  [key: string]: unknown
}

/** 一次抽样的判定结果。字段较多，这里只声明前端真正读取的部分。 */
export interface SparseSampleResult {
  cameraId?: string
  verdict: string
  verdictLabel?: string
  alarm?: boolean
  tamperType?: string
  risk?: number
  confidence?: number
  reasons?: string[]
  matrix?: { dHist?: number; dBurst?: number; cell?: string }
  layers?: SparseLayers
  baseline?: { status?: string; samples?: number; admitted?: boolean; reason?: string }
  timingsMs?: Record<string, number>
  keyframe?: string
  baselineCompare?: string
  diffHeatmap?: string
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

export interface SparseState {
  cameraId: string
  name?: string
  createdMs?: number
  updatedMs?: number
  status?: string
  samples?: number
  slots?: Array<{ slot: number; hour: number; samples: number; ready: boolean; latestTs: number }>
  history?: Array<Record<string, unknown>>
  landmarks?: SparseLandmark[]
  templates?: SparseTemplateItem[]
  audit?: SparseAuditItem[]
  baseline?: Array<Record<string, unknown>>
}

// ------------------------------------------------------------------ 接口
export const algoApi = {
  health: () => http.get<unknown, AlgoHealth>('/algo/health')
}

export const sparseApi = {
  /** 参数元数据 + 判决字典 */
  meta: () => http.get<unknown, SparseMeta>('/sparse/meta'),

  /** 提交一次抽样：frames 为 1~K 张图 */
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
    return http.post<unknown, SparseSampleResult>('/sparse/sample', form, { timeout: 120000 })
  },

  cameras: () => http.get<unknown, { cameras: SparseCameraItem[] }>('/sparse/cameras'),

  state: (cameraId: string) => http.get<unknown, SparseState>(`/sparse/state/${cameraId}`),

  /** 重设基准：清空基线，保留参照物与模板库 */
  reset: (cameraId: string) => http.post<unknown, unknown>(`/sparse/state/${cameraId}/reset`),

  /** 删除机位（含基线、参照物、模板、审计） */
  remove: (cameraId: string) => http.delete<unknown, unknown>(`/sparse/state/${cameraId}`),

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
    return http.post<unknown, Record<string, unknown>>(`/sparse/landmarks/${cameraId}`, form, {
      timeout: 60000
    })
  },

  getLandmarks: (cameraId: string) =>
    http.get<unknown, { cameraId: string; landmarks: SparseLandmark[] }>(
      `/sparse/landmarks/${cameraId}`
    ),

  clearLandmarks: (cameraId: string) =>
    http.delete<unknown, unknown>(`/sparse/landmarks/${cameraId}`),

  /** 模板库：列出某机位所有参照物的模板与审计记录 */
  listTemplates: (cameraId: string) =>
    http.get<
      unknown,
      { templates: SparseTemplateItem[]; audit: SparseAuditItem[]; limits?: Record<string, number> }
    >(`/sparse/landmarks/${cameraId}/templates`),

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
    return http.post<unknown, Record<string, unknown>>(
      `/sparse/landmarks/${cameraId}/templates`,
      form,
      { timeout: 60000 }
    )
  },

  removeTemplate: (cameraId: string, templateId: string) =>
    http.delete<unknown, unknown>(`/sparse/landmarks/${cameraId}/templates/${templateId}`),

  /** 模板预览图地址（梯度幅值渲染成 PNG） */
  templatePreviewUrl: (cameraId: string, templateId: string) =>
    `/api/sparse/templates/${cameraId}/${templateId}/preview`,

  /** 基线样本缩略图地址 */
  thumbUrl: (cameraId: string, slot: number, index: number) =>
    `/api/sparse/thumb/${cameraId}/${slot}/${index}`
}

export default http
