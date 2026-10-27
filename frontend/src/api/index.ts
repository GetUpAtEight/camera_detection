import axios, { type AxiosProgressEvent } from 'axios'
import { ElMessage } from 'element-plus'
import type {
  AlgorithmPayload,
  AnalysisData,
  CompareData,
  FramePage,
  GroundTruthPayload,
  MediaItem,
  LiveFrameResult,
  LiveSessionInfo,
  SegmentSpec,
  TaskItem
} from '@/types'

/**
 * axios 实例：后端统一返回 { code, message, data }，这里直接解包 data。
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
      if (!(response.config as any).silent) ElMessage.error(message)
      return Promise.reject(new Error(message))
    }
    return body
  },
  (error) => {
    const message =
      error?.response?.data?.message ||
      error?.response?.data?.detail ||
      (error?.response?.status === 502 ? '算法服务不可用，请确认 Python 算法层已启动' : error.message) ||
      '网络异常'
    // silent：实时监控这种高频链路自己处理失败重试，不弹全局错误
    if (!(error?.config as any)?.silent) ElMessage.error(message)
    return Promise.reject(Object.assign(new Error(message), { status: error?.response?.status }))
  }
)

export const algoApi = {
  algorithms: () => http.get<any, AlgorithmPayload>('/algo/algorithms'),
  health: () => http.get<any, { online: boolean; baseUrl: string; detail?: any; error?: string }>('/algo/health')
}

export const mediaApi = {
  list: () => http.get<any, MediaItem[]>('/media'),
  get: (id: string) => http.get<any, MediaItem>(`/media/${id}`),
  upload: (file: File, reference?: File | null, onProgress?: (e: AxiosProgressEvent) => void) => {
    const form = new FormData()
    form.append('file', file)
    if (reference) {
      form.append('reference', reference)
    }
    return http.post<any, MediaItem>('/media/upload', form, {
      onUploadProgress: onProgress,
      headers: { 'Content-Type': 'multipart/form-data' }
    })
  },
  remove: (id: string) => http.delete<any, void>(`/media/${id}`),
  fileUrl: (id: string) => `/api/media/${id}/file`,
  groundTruth: (id: string) => http.get<any, GroundTruthPayload>(`/media/${id}/ground-truth`),
  importGroundTruth: (id: string, file: File) => {
    const form = new FormData()
    form.append('file', file)
    return http.post<any, { count: number; source: string; note: string; typeCounts: Record<string, number> }>(
      `/media/${id}/ground-truth`,
      form,
      { headers: { 'Content-Type': 'multipart/form-data' } }
    )
  },
  clearGroundTruth: (id: string) => http.delete<any, void>(`/media/${id}/ground-truth`)
}

export const detectionApi = {
  run: (payload: {
    mediaId: string
    algorithms: string[]
    params: Record<string, Record<string, number>>
    sampleFps?: number
    maxFrames?: number
    /** 区间检测：只跑这一段 */
    startMs?: number
    endMs?: number
    /** 分段扫描：一次跑多段（优先于 startMs/endMs） */
    segments?: SegmentSpec[]
    refGapMs?: number
  }) => http.post<any, TaskItem>('/detection/run', payload),
  list: () => http.get<any, TaskItem[]>('/detection'),
  get: (taskId: string) => http.get<any, TaskItem>(`/detection/${taskId}`),
  frames: (taskId: string, params?: { page?: number; size?: number; algorithm?: string }) =>
    http.get<any, FramePage>(`/detection/${taskId}/frames`, { params }),
  cancel: (taskId: string) => http.post<any, void>(`/detection/${taskId}/cancel`),
  remove: (taskId: string) => http.delete<any, void>(`/detection/${taskId}`),
  analysis: (taskId: string, tolMs?: number) =>
    http.get<any, AnalysisData>(`/detection/${taskId}/analysis`, { params: tolMs ? { tolMs } : undefined }),
  compare: (taskId: string) => http.get<any, CompareData>(`/detection/${taskId}/compare`),
  visualUrl: (taskId: string, algorithm: string, frameIndex: number) =>
    `/api/detection/${taskId}/visual/${algorithm}/${frameIndex}`,
  uploadGroundTruth: (taskId: string, file: File) => {
    const form = new FormData()
    form.append('file', file)
    return http.post<any, { imported: number; skipped: number }>(`/detection/${taskId}/ground-truth`, form)
  },
  exportUrl: (taskId: string, format: 'csv' | 'json') =>
    `/api/detection/${taskId}/export?format=${format}`
}

/**
 * 实时监控：浏览器采集帧 → 后端单帧检测。
 * 参考帧只在建会话时传一次，之后每帧只传当前帧（远程算法层带宽是瓶颈）。
 */
export const liveApi = {
  create: (reference: Blob, algorithms: string[], params: Record<string, any>, requiredStreak: number) => {
    const form = new FormData()
    form.append('reference', reference, 'reference.jpg')
    form.append('algorithms', JSON.stringify(algorithms))
    form.append('params', JSON.stringify(params))
    form.append('requiredStreak', String(requiredStreak))
    return http.post<any, LiveSessionInfo>('/live/sessions', form, { timeout: 60000 })
  },
  /**
   * 单帧检测：失败自动重试一次。
   * 高频链路里偶发的网关抖动（Vite 代理复用失效连接时会返回 502）不该惊动用户，
   * 而且该请求幂等（同一帧重复检测只会多一次计算，不会改变会话状态）。
   */
  frame: async (sessionId: string, frame: Blob, probe = false) => {
    const form = new FormData()
    form.append('frame', frame, 'frame.jpg')
    form.append('probe', String(probe))
    const send = () =>
      http.post<any, LiveFrameResult>(`/live/sessions/${sessionId}/frame`, form, {
        timeout: 60000,
        silent: true
      } as any)
    try {
      return await send()
    } catch (e: any) {
      // 404 = 会话已结束，重试没有意义；只对 5xx / 网络错误重试一次
      if (e?.status && e.status < 500) throw e
      await new Promise((resolve) => setTimeout(resolve, 300))
      return send()
    }
  },
  resetReference: (sessionId: string, reference: Blob) => {
    const form = new FormData()
    form.append('reference', reference, 'reference.jpg')
    return http.post<any, LiveSessionInfo>(`/live/sessions/${sessionId}/reference`, form, { timeout: 60000 })
  },
  /** 学习期结束后回填阈值（会话建完才知道这个场景的正常波动有多大） */
  updateParams: (sessionId: string, params: Record<string, any>) => {
    const form = new FormData()
    form.append('params', JSON.stringify(params))
    return http.patch<any, LiveSessionInfo>(`/live/sessions/${sessionId}/params`, form, { timeout: 15000 })
  },
  info: (sessionId: string) => http.get<any, LiveSessionInfo>(`/live/sessions/${sessionId}`),
  close: (sessionId: string) => http.delete<any, void>(`/live/sessions/${sessionId}`)
}

/**
 * 低频稀疏采样：每 1 小时 / 1 天拉一次帧，每次 1~K 帧。
 *
 * 与实时监控的关键区别：**机位状态在算法层落盘**（按 cameraId），
 * 所以这里没有"会话"概念，每次提交都是一次独立抽样，基线自动累积。
 */
export const sparseApi = {
  meta: () => http.get<any, any>('/sparse/meta'),
  cameras: () => http.get<any, any>('/sparse/cameras'),
  state: (cameraId: string) => http.get<any, any>(`/sparse/state/${cameraId}`),
  reset: (cameraId: string) => http.post<any, any>(`/sparse/state/${cameraId}/reset`),
  remove: (cameraId: string) => http.delete<any, void>(`/sparse/state/${cameraId}`),
  thumbUrl: (cameraId: string, slot: number, index: number) =>
    `/api/sparse/thumb/${cameraId}/${slot}/${index}`,
  /** 参照物 / 忽略区：标定画面 + 归一化矩形列表 */
  setLandmarks: (
    cameraId: string,
    landmarks: {
      x: number
      y: number
      w: number
      h: number
      label?: string
      /** landmark = 静止参照物（判机位移动）；ignore = 忽略区（不参与基线偏离统计） */
      kind?: 'landmark' | 'ignore'
    }[],
    frame?: Blob | null,
    clear = false,
    /** true（默认）= 把这张标定画面同时作为基线样本入库（算法层会先过质量闸） */
    admitFrame = true
  ) => {
    const form = new FormData()
    form.append('landmarks', JSON.stringify(landmarks))
    form.append('clear', String(clear))
    form.append('admitFrame', String(admitFrame))
    if (frame) form.append('file', frame, 'landmark-frame.jpg')
    return http.post<any, any>(`/sparse/landmarks/${cameraId}`, form, { timeout: 60000 })
  },
  getLandmarks: (cameraId: string) => http.get<any, any>(`/sparse/landmarks/${cameraId}`),
  clearLandmarks: (cameraId: string) => http.delete<any, any>(`/sparse/landmarks/${cameraId}`),

  /** 模板库：列出某机位所有参照物的模板与审计记录 */
  listTemplates: (cameraId: string) =>
    http.get<any, any>(`/sparse/landmarks/${cameraId}/templates`),
  /** 给参照物补录一套外观模板（夜间/雨天/误报反馈） */
  addTemplate: (
    cameraId: string,
    landmarkId: number,
    frame: Blob,
    opts: { label?: string; source?: string; actor?: string; verdictAtTime?: string; force?: boolean } = {}
  ) => {
    const form = new FormData()
    form.append('landmarkId', String(landmarkId))
    form.append('file', frame, 'template.jpg')
    form.append('label', opts.label || '')
    form.append('source', opts.source || 'manual')
    form.append('actor', opts.actor || '')
    form.append('verdictAtTime', opts.verdictAtTime || '')
    form.append('force', String(!!opts.force))
    return http.post<any, any>(`/sparse/landmarks/${cameraId}/templates`, form, { timeout: 60000 })
  },
  removeTemplate: (cameraId: string, templateId: string) =>
    http.delete<any, any>(`/sparse/landmarks/${cameraId}/templates/${templateId}`),
  templatePreviewUrl: (cameraId: string, templateId: string) =>
    `/api/sparse/templates/${cameraId}/${templateId}/preview`,
  /** 提交一次抽样：frames 为 1~5 张图 */
  sample: (
    cameraId: string,
    frames: Blob[],
    tsMs: number,
    name: string,
    reset: boolean,
    params: Record<string, number>
  ) => {
    const form = new FormData()
    form.append('cameraId', cameraId)
    frames.forEach((frame, i) => form.append('frames', frame, `frame-${i}.jpg`))
    form.append('tsMs', String(tsMs))
    form.append('name', name)
    form.append('reset', String(reset))
    form.append('params', JSON.stringify(params))
    return http.post<any, any>('/sparse/sample', form, { timeout: 120000 })
  }
}

export default http
