/**
 * 判定结果 → 视图模型的纯计算。
 *
 * 从 SparseView 里抽出来的原因：这几段都是「输入 result/state → 输出表格行 /
 * 进度条宽度」，没有任何副作用，抽出来之后可以直接写断言，
 * 而不是只能靠打开页面肉眼看。
 */
import type { SparseSampleResult, SparseLandmark, SparseLandmarkMatch, SparseL1Layer } from '@/api'
import { isLmMoved, pct, fmt, formatTime } from './format'

export interface QuadrantDef {
  key: string
  label: string
  desc: string
  danger: boolean
}

export const QUADRANT_DEFS: QuadrantDef[] = [
  { key: 'Q1', label: '正常活动场景', desc: '有结构变化 + 有独立噪声', danger: false },
  { key: 'Q2', label: '合规静态（不判）', desc: '静止 + 有独立噪声', danger: false },
  { key: 'Q3', label: '冻结 / 重放', desc: '静止 + 无独立噪声', danger: true },
  { key: 'Q4', label: '可疑合成', desc: '有动态 + 无独立噪声', danger: true }
]

export interface L1Indicator {
  key: string
  label: string
  color: string
  desc: string
  /** 指标 saturates 时的满格值 */
  ratio: (r: SparseSampleResult) => number
}

function l1(r: SparseSampleResult, key: keyof SparseL1Layer): number {
  const v = r.layers?.L1?.[key]
  return typeof v === 'number' && isFinite(v) ? v : 0
}

function ratioOf(max: number) {
  return (v: number) => Math.min(1, Math.max(0, v / max))
}

/** L1 微时序 9 指标：进度条宽度 = min(1, 值 / 满格阈值)。 */
export const L1_INDICATORS: L1Indicator[] = [
  { key: 'bitExactDup', label: 'bitExactDup', color: '#f56c6c', desc: 'burst 内逐位完全相同的帧对占比',
    ratio: (r) => Math.min(1, l1(r, 'bitExactDup')) },
  { key: 'intraMad', label: 'intraMad', color: '#409eff', desc: '相邻帧平均绝对差（去噪后）',
    ratio: (r) => ratioOf(0.02)(l1(r, 'intraMad')) },
  { key: 'intraChangeRatio', label: 'intraChangeRatio', color: '#409eff', desc: '去噪后差异>8 的像素占比（结构性变化）',
    ratio: (r) => ratioOf(0.05)(l1(r, 'intraChangeRatio')) },
  { key: 'intraEdgeRate', label: 'intraEdgeRate', color: '#909399', desc: '边缘图变化率',
    ratio: (r) => ratioOf(0.05)(l1(r, 'intraEdgeRate')) },
  { key: 'intraOrbTranslation', label: 'intraOrbTranslation', color: '#e6a23c', desc: 'burst 内 ORB 平移量（px）',
    ratio: (r) => ratioOf(20)(l1(r, 'intraOrbTranslation')) },
  { key: 'intraSharpnessVar', label: 'intraSharpnessVar', color: '#67c23a', desc: 'burst 内清晰度变异系数',
    ratio: (r) => ratioOf(0.5)(l1(r, 'intraSharpnessVar')) },
  { key: 'sigmaPerFrame', label: 'sigmaPerFrame', color: '#f56c6c', desc: '帧间时间噪声 σ（冻结时趋 0）',
    ratio: (r) => ratioOf(5)(l1(r, 'sigmaPerFrame')) },
  { key: 'lumaDrift', label: 'lumaDrift', color: '#b37feb', desc: 'burst 内亮度漂移',
    ratio: (r) => ratioOf(0.2)(l1(r, 'lumaDrift')) },
  { key: 'chromaDrift', label: 'chromaDrift', color: '#b37feb', desc: 'burst 内色度漂移',
    ratio: (r) => ratioOf(0.2)(l1(r, 'chromaDrift')) }
]

export interface CompareRow {
  name: string
  current: string
  baseline: string
  change: string
  worse: boolean
}

function num(v: unknown): number | undefined {
  return typeof v === 'number' && isFinite(v) ? v : undefined
}

/** L0 / L2 与基线对比表的三行。 */
export function buildCompareRows(result: SparseSampleResult | null | undefined): CompareRow[] {
  const L0 = (result?.layers?.L0 || {}) as Record<string, unknown>
  const L2 = (result?.layers?.L2 || {}) as Record<string, unknown>
  const rows = [
    { name: '清晰度 Laplacian', current: L2.sharpness, baseline: L0.baselineSharpness,
      drop: L0.sharpnessDrop },
    { name: '灰度熵 (bit)', current: L2.entropy, baseline: L0.baselineEntropy,
      drop: L0.entropyDrop },
    { name: '边缘密度', current: L2.edgeDensity, baseline: L0.baselineEdge,
      drop: L0.edgeDrop }
  ]
  return rows.map((r) => {
    const drop = num(r.drop)
    return {
      name: r.name,
      current: fmt(r.current),
      baseline: r.baseline == null ? '—' : fmt(r.baseline),
      change: r.drop == null ? '—' : `-${pct(drop ?? 0)}`,
      worse: (drop ?? 0) > 0.3
    }
  })
}

/** 基线来源说明文本。 */
export function baselineSourceText(result: SparseSampleResult | null | undefined): string {
  const src = (result?.layers?.L0 as Record<string, unknown> | undefined)?.scalarSource
  if (!src) return '未建立'
  return src === 'slot' ? '同时段槽基线' : '跨时段全局基线（降到全局）'
}

/** 从机位状态里拆出「会动的参照物」与「忽略区」。 */
export function splitLandmarks(landmarks: SparseLandmark[] | undefined | null) {
  const all = landmarks || []
  return {
    all,
    anchors: all.filter((lm) => (lm.kind || 'landmark') !== 'ignore'),
    ignores: all.filter((lm) => lm.kind === 'ignore')
  }
}

/** 带下标锚点的参照物列表（模板库按 idx 绑定输入框）。 */
export function withIdx(landmarks: SparseLandmark[]): Array<SparseLandmark & { idx: number }> {
  return landmarks.map((lm, i) => ({ ...lm, idx: i }))
}

/** 是否已配置参照物（算法层说配了，或前端看得到参照物列表）。 */
export function isLandmarkConfigured(
  compare: { configured?: boolean } | undefined | null,
  landmarks: SparseLandmark[] | undefined | null
): boolean {
  return !!compare?.configured || (landmarks?.length ?? 0) > 0
}

/** 某参照物当前是否被判为「已移位」。 */
export function landmarkMoved(matches: SparseLandmarkMatch[] | undefined | null, id: number): boolean {
  return isLmMoved(matches || [], id)
}

/** 百分比定位样式（标定框 / 证据图浮层共用）。 */
export function rectStyle(r: { x: number; y: number; w: number; h: number }) {
  return {
    left: `${r.x * 100}%`,
    top: `${r.y * 100}%`,
    width: `${r.w * 100}%`,
    height: `${r.h * 100}%`
  }
}

export interface TimelineRow {
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

/** 把后端 history 洗成时间线行（缺字段补默认值，非法行丢弃）。 */
export function normalizeHistory(raw: unknown): TimelineRow[] {
  if (!Array.isArray(raw)) return []
  return raw
    .filter((h): h is Record<string, unknown> => !!h && typeof h === 'object')
    .map((h) => ({
      tsMs: Number(h.tsMs) || 0,
      verdict: String(h.verdict ?? ''),
      label: String(h.label ?? ''),
      alarm: !!h.alarm,
      risk: Number(h.risk) || 0,
      tamperType: String(h.tamperType ?? ''),
      dHist: num(h.dHist),
      dBurst: num(h.dBurst),
      slot: Number(h.slot) || 0
    }))
}

/** 时段槽画廊条目。 */
export function buildGallery(
  cameraId: string,
  slot: { slot: number; samples: number; latestTs: number },
  thumbUrl: (cameraId: string, slot: number, index: number) => string
) {
  const items: Array<{ key: string; url: string; slot: number; ts: number }> = []
  if (!slot.samples) return items
  for (let i = 0; i < slot.samples; i += 1) {
    items.push({
      key: `${slot.slot}-${i}`,
      url: thumbUrl(cameraId, slot.slot, i),
      slot: slot.slot,
      ts: slot.latestTs
    })
  }
  return items
}

/** 下一次采样的时间文本（advance=0 时不提示）。 */
export function nextTsText(advance: string, simulatedTs: number): string {
  if (advance === '0' || !simulatedTs) return ''
  return formatTime(simulatedTs + Number(advance) * 3600 * 1000)
}
