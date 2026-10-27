/**
 * 采样时间推进：页面「采样时间推进」下拉 + 一键演示的核心计时逻辑。
 *
 * 为什么单独抽出来：这段逻辑决定了提交给算法层的 `tsMs`，而
 * 「跨采样点 bit-exact」「时段槽」「基线冷启动」全都依赖这个时间的
 * 单调推进关系。抽成纯函数之后可以用断言把行为钉住，
 * 而不是只能靠点页面观察。
 */
import { serverNow } from '@/api'

/** 时钟来源：显式给定 > 服务端时间 > 本地时钟（仅开发兜底，生产应始终有服务端时间）。 */
export type ClockSource = 'explicit' | 'server' | 'local'

export interface TsState {
  /** 上一次推进到的时间戳（0 = 还没开始推进） */
  simulatedTs: number
  /** 采样时间推进档位：'0' 不推进 / '1' +1 小时 / '24' +24 小时 */
  advance: string
}

export interface NextTsResult {
  /** 下一次要提交的 tsMs */
  tsMs: number
  /** 推进后的状态（调用方写回自己的 ref） */
  state: TsState
  source: ClockSource
}

/** 服务端时间基准偏移：没有服务端时间时返回 null，调用方才退回本地时钟。 */
export function serverClockBase(): number | null {
  const now = serverNow()
  return now > 0 ? now : null
}

/**
 * 推进并返回下一个采样时间戳。
 *
 * 行为契约（与重构前逐条对齐）：
 * - `advance === '0'`：仍然会初始化一次基准时间再返回，调用方随后会把它清零；
 * - 首次推进用「当前时间」做基准，之后累加 step；
 * - 基准时间优先取服务端 `X-Server-Now`，取不到才退回本地 `Date.now()`。
 */
export function nextSampleTs(state: TsState, now?: number): NextTsResult {
  const step = Number(state.advance) * 3600 * 1000
  let simulatedTs = state.simulatedTs
  let source: ClockSource = 'server'
  if (!simulatedTs) {
    const base = now ?? serverClockBase()
    if (base != null) {
      simulatedTs = base
      source = 'server'
    } else {
      simulatedTs = Date.now()
      source = 'local'
    }
  }
  simulatedTs += step
  return { tsMs: simulatedTs, state: { simulatedTs, advance: state.advance }, source }
}

/**
 * 提交成功后是否要把推进状态清零。
 *
 * `advance === '0'` 时页面必须"用当前时间"，所以每次提交后清 0，
 * 让下一次重新取基准时间；推进档位下则保持累加。
 */
export function shouldResetAfterSubmit(advance: string): boolean {
  return advance === '0'
}

/** 一键演示里「几小时后重放」的手动跳跃。 */
export const REPLAY_JUMP_MS = 3 * 3600 * 1000

export function jumpForward(state: TsState, deltaMs: number): TsState {
  return { simulatedTs: state.simulatedTs + deltaMs, advance: state.advance }
}
