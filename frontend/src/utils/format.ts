import dayjs from 'dayjs'

export function formatBytes(bytes?: number | null): string {
  if (!bytes) return '-'
  const units = ['B', 'KB', 'MB', 'GB']
  let value = bytes
  let index = 0
  while (value >= 1024 && index < units.length - 1) {
    value /= 1024
    index++
  }
  return `${value.toFixed(value >= 10 || index === 0 ? 0 : 1)} ${units[index]}`
}

export function formatDuration(ms?: number | null): string {
  if (ms === null || ms === undefined) return '-'
  if (ms < 1000) return `${Math.round(ms)} ms`
  const totalSeconds = ms / 1000
  if (totalSeconds < 60) return `${totalSeconds.toFixed(1)} s`
  const minutes = Math.floor(totalSeconds / 60)
  const seconds = Math.round(totalSeconds % 60)
  return `${minutes} 分 ${seconds} 秒`
}

export function formatClock(ms?: number | null): string {
  if (ms === null || ms === undefined) return '0:00'
  const totalSeconds = Math.floor(ms / 1000)
  const m = Math.floor(totalSeconds / 60)
  const s = totalSeconds % 60
  const mm = String(m).padStart(2, '0')
  const ss = String(s).padStart(2, '0')
  const cs = Math.floor((ms % 1000) / 100)
  return `${mm}:${ss}.${cs}`
}

export function formatTime(value?: string | null): string {
  if (!value) return '-'
  return dayjs(value).format('YYYY-MM-DD HH:mm:ss')
}

export function formatPercent(value?: number | null, digits = 1): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '-'
  return `${(value * 100).toFixed(digits)}%`
}

export const TAMPER_TYPE_TEXT: Record<string, string> = {
  normal: '正常',
  covered: '镜头遮挡',
  defocused: '镜头失焦',
  moved: '镜头移位'
}

export const TAMPER_TYPE_COLOR: Record<string, string> = {
  normal: '#22a06b',
  covered: '#e5484d',
  defocused: '#d97706',
  moved: '#7c5cff'
}

export const STATUS_TEXT: Record<string, string> = {
  PENDING: '排队中',
  RUNNING: '检测中',
  DONE: '已完成',
  FAILED: '失败',
  CANCELLED: '已取消'
}

export const STATUS_TAG: Record<string, 'info' | 'warning' | 'success' | 'danger'> = {
  PENDING: 'info',
  RUNNING: 'warning',
  DONE: 'success',
  FAILED: 'danger',
  CANCELLED: 'info'
}

/** 六个算法固定配色，图表与标签统一使用 */
export const ALGO_COLORS: Record<string, string> = {
  laplacian: '#2f6fed',
  entropy: '#0ea5a4',
  scene_diff: '#f59e0b',
  orb: '#7c5cff',
  edge: '#e5484d',
  yolo: '#8b8f9a'
}

/** 图例、标签页里用的短名，避免长中文名把界面撑开 */
export const ALGO_SHORT: Record<string, string> = {
  laplacian: '拉普拉斯',
  entropy: '直方图熵',
  scene_diff: '帧差',
  orb: 'ORB',
  edge: '边缘消失率',
  yolo: 'YOLO'
}

export function algoShort(name: string, fallback?: string): string {
  return ALGO_SHORT[name] || fallback || name
}

export function algoColor(name: string): string {
  return ALGO_COLORS[name] || '#2f6fed'
}
