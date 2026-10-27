import { describe, expect, it } from 'vitest'
import {
  REPLAY_JUMP_MS,
  jumpForward,
  nextSampleTs,
  shouldResetAfterSubmit
} from '../src/composables/sampleClock'

describe('采样时间推进', () => {
  it('首次推进会用「当前时间」做基准再累加一档', () => {
    const res = nextSampleTs({ simulatedTs: 0, advance: '1' }, 1_700_000_000_000)
    expect(res.tsMs).toBe(1_700_000_000_000 + 3_600_000)
    expect(res.state.simulatedTs).toBe(res.tsMs)
  })

  it('advance=0 时仍然初始化基准（调用方随后清零）', () => {
    const res = nextSampleTs({ simulatedTs: 0, advance: '0' }, 1_700_000_000_000)
    expect(res.tsMs).toBe(1_700_000_000_000)
    expect(shouldResetAfterSubmit('0')).toBe(true)
    expect(shouldResetAfterSubmit('1')).toBe(false)
  })

  it('已推进过的时间继续按档位累加，且不受基准影响', () => {
    const first = nextSampleTs({ simulatedTs: 0, advance: '24' }, 1_000)
    const second = nextSampleTs(first.state, 999_999_999)
    expect(second.tsMs).toBe(first.tsMs + 24 * 3_600_000)
  })

  it('一键演示的「几小时后重放」是纯加法', () => {
    const jumped = jumpForward({ simulatedTs: 5_000, advance: '1' }, REPLAY_JUMP_MS)
    expect(jumped.simulatedTs).toBe(5_000 + 3 * 3_600_000)
    expect(jumped.advance).toBe('1')
  })
})
