import { describe, expect, it } from 'vitest'
import {
  DEFAULT_MATRIX_THRESHOLDS,
  buildCurrentPoint,
  buildMatrixOption,
  buildMatrixPoints,
  thresholdsFromParams
} from '../src/composables/matrixChart'
import type { SparseHistoryItem, SparseSampleResult } from '../src/api'

const history: SparseHistoryItem[] = [
  { tsMs: 1, verdict: 'normal', label: '正常样本', alarm: false, risk: 0, tamperType: 'normal', slot: 1, dHist: 0.05, dBurst: 0.3 },
  { tsMs: 2, verdict: 'persistent_covered', label: '遮挡', alarm: true, risk: 0.9, tamperType: 'covered', slot: 2, dHist: 0.7, dBurst: 1.4 },
  { tsMs: 3, verdict: 'normal', label: '缺字段', alarm: false, risk: 0, tamperType: 'normal', slot: 3 }
]

describe('4 格矩阵 option', () => {
  it('历史点只保留 dHist/dBurst 齐全的行，并按告警着色', () => {
    const points = buildMatrixPoints(history)
    expect(points).toHaveLength(2)
    expect(points[0].itemStyle.color).toBe('#67c23a')
    expect(points[1].itemStyle.color).toBe('#f56c6c')
  })

  it('dBurst 超过 1 时被截断（纵轴上限是 1）', () => {
    expect(buildMatrixPoints(history)[1].value).toEqual([0.7, 1])
  })

  it('本次抽样的点：没有 matrix 就不画', () => {
    expect(buildCurrentPoint(null)).toEqual([])
    expect(buildCurrentPoint({ verdict: 'normal' })).toEqual([])
    const cur = buildCurrentPoint({ verdict: 'normal', matrix: { dHist: 0.2, dBurst: 2 } } as SparseSampleResult)
    expect(cur[0].value).toEqual([0.2, 1])
    expect(cur[0].name).toBe('本次')
  })

  it('阈值默认值与参数覆盖', () => {
    expect(thresholdsFromParams({})).toEqual(DEFAULT_MATRIX_THRESHOLDS)
    expect(thresholdsFromParams({ dHistAlarm: 0.3, dBurstTransient: 0.4 })).toEqual({
      dHistAlarm: 0.3,
      dBurstTransient: 0.4
    })
  })

  it('option 里的分区/阈值线跟着参数走', () => {
    const option = buildMatrixOption(history, null, { dHistAlarm: 0.25, dBurstTransient: 0.6 })
    const scatter = option.series[0] as {
      markArea: { data: Array<Array<{ xAxis?: number; yAxis?: number }>> }
      markLine: { data: Array<Record<string, number>> }
      data: unknown[]
    }
    expect(scatter.data).toHaveLength(2)
    expect(scatter.markLine.data).toEqual([{ xAxis: 0.25 }, { yAxis: 0.6 }])
    expect(scatter.markArea.data[0][0].xAxis).toBe(0.25)
    expect(scatter.markArea.data[1][0].yAxis).toBe(0.6)
  })
})
