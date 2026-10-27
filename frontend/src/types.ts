export type MediaKind = 'IMAGE' | 'VIDEO'
export type TaskStatus = 'PENDING' | 'RUNNING' | 'DONE' | 'FAILED' | 'CANCELLED'
export type TamperType = 'normal' | 'covered' | 'defocused' | 'moved'

export interface MediaItem {
  id: string
  originalName: string
  mediaType: MediaKind
  width?: number
  height?: number
  fps?: number
  durationMs?: number
  frameCount?: number
  fileSize?: number
  contentType?: string
  hasReference?: boolean
  createdAt: string
}

export interface ParamMeta {
  key: string
  label: string
  default: number
  min: number
  max: number
  step: number
  unit?: string
  type?: 'number' | 'int'
  description?: string
}

export interface AlgorithmMeta {
  name: string
  displayName: string
  description: string
  tamperTypes: TamperType[]
  requiresReference: boolean
  kind: string
  available: boolean
  thresholdScore: number
  params: ParamMeta[]
  /** 自动标定用的主指标：detail 字段 / 方向 / 回填的参数 key */
  primaryMetric?: { field: string; direction: 'higher' | 'lower'; param: string } | null
}

export interface AlgorithmPayload {
  online: boolean
  baseUrl: string
  error?: string
  thresholdScore: number
  algorithms: AlgorithmMeta[]
}

export interface FrameResultItem {
  algorithm: string
  frameIndex: number
  /** 分段检测时该帧属于第几段 */
  segmentIndex?: number
  timestampMs: number
  score: number
  tampered: boolean
  tamperType: TamperType
  confidence: number
  detail: Record<string, any>
  note?: string
  skipped?: boolean
  visualUrl?: string | null
}

export interface FramePage {
  content: FrameResultItem[]
  page: number
  size: number
  totalElements: number
  totalPages: number
  algorithms: string[]
}

export interface TaskItem {
  id: string
  mediaId: string
  mediaName: string
  mediaType: MediaKind | 'UNKNOWN'
  algorithms: string[]
  status: TaskStatus
  progress: number
  message?: string
  errorMessage?: string
  frameCount?: number
  tamperedFrameCount?: number
  elapsedMs?: number
  /** 分段检测覆盖的时间窗与段数 */
  startMs?: number | null
  endMs?: number | null
  segmentCount?: number | null
  createdAt: string
  startedAt?: string
  finishedAt?: string
}

export interface AlgorithmStat {
  algorithm: string
  displayName: string
  frames: number
  tamperedFrames: number
  skipped: number
  tamperRate: number
  avgScore: number
  maxScore: number
  avgCostMs?: number | null
  dominantTamperType: TamperType
  events: number
  firstAlarmMs?: number | null
}

export interface TamperEvent {
  startFrame: number
  endFrame: number
  startMs: number
  endMs: number
  durationMs: number
  frameCount: number
  tamperType: TamperType
  peakScore: number
  avgScore: number
  algorithms: string[]
}

export interface Conclusion {
  verdict: 'tampered' | 'normal'
  tamperType: TamperType
  text: string
  riskLevel: number
  tamperedFrames: number
  totalFrames: number
  tamperRate: number
  triggeredAlgorithms: string[]
  firstAlarmMs?: number | null
}

export interface ConsistencyPair {
  algorithmA: string
  algorithmB: string
  agreement: number
  comparedFrames: number
}

export interface HistogramBin {
  binStart: number
  binEnd: number
  count: number
}

export interface AlgorithmMetrics {
  algorithm: string
  truePositive: number
  falsePositive: number
  trueNegative: number
  falseNegative: number
  precision: number
  recall: number
  f1: number
  accuracy: number
}

export interface Metrics {
  labelledFrames: number
  tamperedLabels: number
  /** 标注中未落在采样帧上的条数（没有检测结果，已忽略） */
  unmatchedLabels?: number
  perAlgorithm: AlgorithmMetrics[]
  ensemble: AlgorithmMetrics
}

export interface SegmentSummary {
  index: number
  label: string
  startMs: number
  endMs: number
  frameCount: number
  tamperedFrames: number
  alarmRate: number
  peakScore: number
  tamperType: TamperType
  expectedType?: string | null
  gtTampered?: boolean | null
  matched?: boolean | null
  typeMatched?: boolean | null
  eventCount: number
  avgCostMs?: number | null
}

export interface EventMetrics {
  algorithm: string
  truePositive: number
  falsePositive: number
  falseNegative: number
  precision: number
  recall: number
  f1: number
  meanLatencyMs: number
  maxLatencyMs: number
}

export interface EventPair {
  kind: 'tp' | 'fn' | 'fp'
  algorithm: string
  gtStartMs?: number | null
  gtEndMs?: number | null
  gtType?: string | null
  predStartMs?: number | null
  predEndMs?: number | null
  predType?: string | null
  latencyMs?: number | null
  overlapMs?: number | null
  nearestGtMs?: number | null
  peakScore?: number | null
}

export interface EventEvaluation {
  toleranceMs: number
  gtCount: number
  source?: string
  perAlgorithm: EventMetrics[]
  ensemble: EventMetrics
  pairs: EventPair[]
  pairsByAlgorithm?: Record<string, EventPair[]>
}

export interface GroundTruthEventItem {
  startMs: number
  endMs: number
  tamperType: string
  source?: string
}

export interface GroundTruthPayload {
  count: number
  events: GroundTruthEventItem[]
}

export interface SegmentSpec {
  label?: string
  type?: string
  startMs: number
  endMs: number
}

export interface SegmentPlan {
  mode: 'full' | 'range' | 'segments'
  startMs: number
  endMs: number
  segments: SegmentSpec[]
}

export interface AnalysisData {
  taskId: string
  status: TaskStatus
  media?: MediaItem
  algorithms: string[]
  frames: number
  sampleStepMs?: number
  windowStartMs?: number | null
  windowEndMs?: number | null
  segments?: SegmentSummary[]
  eventEvaluation?: EventEvaluation | null
  conclusion: Conclusion
  algorithmStats: AlgorithmStat[]
  events: TamperEvent[]
  consistency: ConsistencyPair[]
  distribution: HistogramBin[]
  metrics?: Metrics
}

export interface TypeBreakdown {
  algorithm: string
  normal: number
  covered: number
  defocused: number
  moved: number
}

export interface CompareData {
  taskId: string
  mediaName: string
  mediaType: string
  frames: number
  thresholdScore: number
  stats: AlgorithmStat[]
  typeBreakdown: TypeBreakdown[]
  consistency: ConsistencyPair[]
  overallAgreement: number
  hasGroundTruth: boolean
}

// ------------------------------------------------------------------ 实时监控

export interface LiveSessionInfo {
  sessionId: string
  algorithms: string[]
  requiredStreak: number
  frameCount: number
  alarmCount: number
  createdAt: number
  lastFrameAt: number
  referenceUrl: string
  alarmImages: number
}

export interface LiveAlgoResult {
  algorithm: string
  displayName: string
  score: number
  tampered: boolean
  tamperType: string
  note: string
  skipped: boolean
  /** 中间量，自动标定从这里取主指标 */
  detail?: Record<string, number | string | boolean>
}

export interface LiveFrameResult {
  sessionId: string
  frameIndex: number
  timestampMs: number
  alarm: boolean
  frameTampered: boolean
  streak: number
  requiredStreak: number
  votes: number
  tamperType: string
  score: number
  verdict: string
  algorithms: LiveAlgoResult[]
  elapsedMs: number
  alarmImageUrl?: string | null
}

/** 前端本地保存的一条监控记录（不入库，便于导出） */
export interface LiveRecord {
  frameIndex: number
  time: string
  alarm: boolean
  tampered: boolean
  tamperType: string
  votes: number
  score: number
  verdict: string
  elapsedMs: number
  alarmImageUrl?: string | null
}
