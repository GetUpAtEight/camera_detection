import { describe, expect, it } from 'vitest'
import {
  L1_INDICATORS,
  QUADRANT_DEFS,
  baselineSourceText,
  buildCompareRows,
  buildGallery,
  isLandmarkConfigured,
  landmarkMoved,
  nextTsText,
  normalizeHistory,
  rectStyle,
  splitLandmarks,
  withIdx
} from '../src/composables/sparseView'
import { fmt, formatTime, isLmMoved, levelIcon, levelOf, lmOk, pct, statusText, typeText } from '../src/composables/format'
import type { SparseLandmark, SparseSampleResult } from '../src/api'

function result(overrides: Partial<SparseSampleResult> = {}): SparseSampleResult {
  return { verdict: 'normal', ...overrides }
}

describe('格式化工具', () => {
  it('pct 把非法值折叠成 0%', () => {
    expect(pct(0.153)).toBe('15%')
    expect(pct(null)).toBe('0%')
    expect(pct(Number.NaN)).toBe('0%')
  })

  it('fmt 按量级切换精度', () => {
    expect(fmt(null)).toBe('—')
    expect(fmt('abc')).toBe('abc')
    expect(fmt(123.456)).toBe('123.5')
    expect(fmt(1.2345)).toBe('1.23')
    expect(fmt(0.12345)).toBe('0.1235')
  })

  it('formatTime / statusText / typeText / levelIcon / levelOf', () => {
    expect(formatTime(0)).toBe('—')
    expect(formatTime(new Date(2024, 0, 2, 3, 4).getTime())).toBe('1/2 03:04')
    expect(statusText('cold_start')).toBe('冷启动')
    expect(statusText('weird')).toBe('weird')
    expect(statusText(undefined)).toBe('—')
    expect(typeText('frozen')).toBe('冻结 / 重放')
    expect(levelIcon('critical')).toBe('CircleCloseFilled')
    expect(levelIcon(undefined)).toBe('InfoFilled')
    expect(levelOf('freeze_replay')).toBe('critical')
    expect(levelOf('persistent_change')).toBe('warning')
    expect(levelOf('normal')).toBe('ok')
    expect(levelOf(undefined)).toBe('info')
  })

  it('参照物匹配判定尊重 scoreMin 与 2px 门限', () => {
    expect(lmOk({ ok: true, score: 0.6 })).toBe(true)
    expect(lmOk({ ok: true, score: 0.3 })).toBe(false)
    expect(lmOk({ ok: true, score: 0.3, scoreMin: 0.2 })).toBe(true)
    expect(lmOk(null)).toBe(false)
    expect(isLmMoved([{ id: 0, ok: true, score: 0.9, magnitude: 5 }], 0)).toBe(true)
    expect(isLmMoved([{ id: 0, ok: true, score: 0.9, magnitude: 1 }], 0)).toBe(false)
    expect(isLmMoved([{ id: 0, ok: false, score: 0.9, magnitude: 9 }], 0)).toBe(false)
  })
})

describe('视图模型计算', () => {
  it('L1 指标比例被夹在 0~1', () => {
    const r = result({ layers: { L1: { intraMad: 1, sigmaPerFrame: 2.5, bitExactDup: 0.4 } } })
    const mad = L1_INDICATORS.find((i) => i.key === 'intraMad')!
    const sigma = L1_INDICATORS.find((i) => i.key === 'sigmaPerFrame')!
    const dup = L1_INDICATORS.find((i) => i.key === 'bitExactDup')!
    expect(mad.ratio(r)).toBe(1)
    expect(sigma.ratio(r)).toBe(0.5)
    expect(dup.ratio(r)).toBe(0.4)
    expect(mad.ratio(result())).toBe(0)
  })

  it('对比表在缺基线时用破折号而不是 NaN', () => {
    const rows = buildCompareRows(result({ layers: { L2: { sharpness: 1.5 } } }))
    expect(rows).toHaveLength(3)
    expect(rows[0]).toMatchObject({ name: '清晰度 Laplacian', current: '1.50', baseline: '—', change: '—', worse: false })
  })

  it('对比表把 drop 渲染成变化百分比，>30% 标红', () => {
    const rows = buildCompareRows(
      result({ layers: { L2: { sharpness: 1 }, L0: { baselineSharpness: 2, sharpnessDrop: 0.5 } } })
    )
    expect(rows[0].change).toBe('-50%')
    expect(rows[0].worse).toBe(true)
  })

  it('基线来源文案', () => {
    expect(baselineSourceText(null)).toBe('未建立')
    expect(baselineSourceText(result({ layers: { L0: { scalarSource: 'slot' } } }))).toBe('同时段槽基线')
    expect(baselineSourceText(result({ layers: { L0: { scalarSource: 'global' } } }))).toBe(
      '跨时段全局基线（降到全局）'
    )
  })

  it('参照物拆分与下标附加', () => {
    const lms: SparseLandmark[] = [
      { x: 0.1, y: 0.1, w: 0.2, h: 0.2 },
      { x: 0.5, y: 0.5, w: 0.1, h: 0.1, kind: 'ignore', label: '桌面' }
    ]
    const split = splitLandmarks(lms)
    expect(split.anchors).toHaveLength(1)
    expect(split.ignores).toHaveLength(1)
    expect(withIdx(lms)[1].idx).toBe(1)
    expect(isLandmarkConfigured(undefined, lms)).toBe(true)
    expect(isLandmarkConfigured({ configured: true }, [])).toBe(true)
    expect(isLandmarkConfigured({}, [])).toBe(false)
    expect(landmarkMoved([{ id: 1, ok: true, score: 0.9, magnitude: 3 }], 1)).toBe(true)
    expect(landmarkMoved([], 1)).toBe(false)
  })

  it('百分比定位样式', () => {
    expect(rectStyle({ x: 0.1, y: 0.2, w: 0.3, h: 0.4 })).toEqual({
      left: '10%',
      top: '20%',
      width: '30%',
      height: '40%'
    })
  })

  it('历史记录清洗：丢非法行、补默认值', () => {
    expect(normalizeHistory(null)).toEqual([])
    const rows = normalizeHistory([
      { tsMs: 1, verdict: 'normal', label: '正常', alarm: false, risk: 0.1, tamperType: 'normal', slot: 3 },
      null,
      'oops'
    ])
    expect(rows).toHaveLength(1)
    expect(rows[0]).toMatchObject({ verdict: 'normal', slot: 3, dHist: undefined })
  })

  it('时段槽画廊按样本数生成缩略图地址', () => {
    const items = buildGallery('cam', { slot: 7, samples: 2, latestTs: 42 }, (c, s, i) => `/thumb/${c}/${s}/${i}`)
    expect(items).toEqual([
      { key: '7-0', url: '/thumb/cam/7/0', slot: 7, ts: 42 },
      { key: '7-1', url: '/thumb/cam/7/1', slot: 7, ts: 42 }
    ])
    expect(buildGallery('cam', { slot: 7, samples: 0, latestTs: 42 }, () => 'x')).toEqual([])
  })

  it('下次采样提示', () => {
    expect(nextTsText('0', 1000)).toBe('')
    expect(nextTsText('1', 0)).toBe('')
    expect(nextTsText('1', new Date(2024, 0, 2, 3, 4).getTime())).toBe('1/2 04:04')
  })

  it('4 格象限定义保持四个且只有一个 danger 组合', () => {
    expect(QUADRANT_DEFS.map((q) => q.key)).toEqual(['Q1', 'Q2', 'Q3', 'Q4'])
    expect(QUADRANT_DEFS.filter((q) => q.danger).map((q) => q.key)).toEqual(['Q3', 'Q4'])
  })
})
