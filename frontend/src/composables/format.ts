/**
 * 纯格式化 / 纯判定函数集合。
 *
 * 这里不放任何响应式状态，也不碰 DOM：同样的输入永远得到同样的输出，
 * 所以能直接被单元测试覆盖（见 test/composables/format.spec.ts）。
 * SparseView 只负责把它们绑到模板上。
 */

/** 比例 → 百分比文本；非法值按 0% 处理（后端偶尔给 null）。 */
export function pct(v: number | undefined | null): string {
  if (v == null || !isFinite(v as number)) return '0%'
  return `${Math.round((v as number) * 100)}%`
}

/** 通用数值格式化：≥100 保留 1 位，≥1 保留 2 位，其余保留 4 位；非数值原样输出。 */
export function fmt(v: unknown): string {
  if (v == null) return '—'
  if (typeof v !== 'number') return String(v)
  if (Math.abs(v) >= 100) return v.toFixed(1)
  if (Math.abs(v) >= 1) return v.toFixed(2)
  return v.toFixed(4)
}

/** 毫秒时间戳 → `M/D HH:mm`；0/未定义显示破折号。 */
export function formatTime(ts?: number): string {
  if (!ts) return '—'
  const d = new Date(ts)
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getMonth() + 1}/${d.getDate()} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

/** 基线成熟度文案。 */
export function statusText(status?: string): string {
  const map: Record<string, string> = {
    empty: '空', cold_start: '冷启动', warming: '积累中', ready: '就绪'
  }
  return map[status || ''] || status || '—'
}

/** 篡改类型文案。 */
export function typeText(type?: string): string {
  const map: Record<string, string> = {
    covered: '遮挡',
    defocused: '失焦',
    moved: '移位',
    frozen: '冻结 / 重放',
    normal: '正常',
    unknown: '未定性'
  }
  return map[type || ''] || type || '—'
}

/** 判决等级 → Element Plus 图标组件名。 */
export function levelIcon(level?: string): string {
  if (level === 'critical') return 'CircleCloseFilled'
  if (level === 'warning') return 'WarningFilled'
  if (level === 'ok') return 'CircleCheckFilled'
  return 'InfoFilled'
}

/** 判决码 → 等级（决定徽章/时间线圆点颜色）。 */
export function levelOf(verdict?: string): string {
  if (!verdict) return 'info'
  if (['freeze_replay', 'persistent_covered', 'persistent_defocused', 'persistent_moved',
       'being_rotated'].includes(verdict)) return 'critical'
  if (verdict === 'persistent_change') return 'warning'
  if (verdict === 'normal') return 'ok'
  return 'info'
}

/** 参照物是否匹配成功（既要 ok，也要分数过线）。 */
export function lmOk(m: { ok?: boolean; score?: number; scoreMin?: number } | null | undefined): boolean {
  return !!m?.ok && (m.score ?? 0) >= (m.scoreMin ?? 0.45)
}

/** 该参照物是否被判定为「已移位」（匹配成功且位移 ≥2px）。 */
export function isLmMoved(
  matches: Array<{ id: number; ok?: boolean; score?: number; scoreMin?: number; magnitude?: number }>,
  id: number
): boolean {
  const m = matches.find((x) => x.id === id)
  return !!m && lmOk(m) && (m.magnitude ?? 0) >= 2
}
