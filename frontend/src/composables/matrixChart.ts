/**
 * 4 格矩阵（dHist × dBurst）ECharts 渲染。
 *
 * 从 SparseView 抽出的原因：这是一段「输入若干数字 → 一大坨 option」的纯映射，
 * 与 ECharts 实例生命周期无关。图例分区/阈值线全部由 `params` 决定，
 * 不依赖任何页面状态，因此 option 构造函数可以单独断言。
 */
import type { SparseHistoryItem, SparseSampleResult } from '@/api'

export interface MatrixThresholds {
  /** 判定为「持续篡改」的 dHist 阈值 */
  dHistAlarm: number
  /** dBurst 分界：超过即视为瞬时事件 */
  dBurstTransient: number
}

export const DEFAULT_MATRIX_THRESHOLDS: MatrixThresholds = {
  dHistAlarm: 0.15,
  dBurstTransient: 0.55
}

export interface MatrixPoint {
  value: [number, number]
  itemStyle: { color: string }
  name: string
}

export interface MatrixCurrentPoint {
  value: [number, number]
  symbolSize: number
  itemStyle: { color: string; borderColor: string; borderWidth: number }
  label: { show: boolean; formatter: string; position: string; fontSize: number }
  name: string
}

/** 历史点：只保留 dHist / dBurst 都有的行。 */
export function buildMatrixPoints(history: SparseHistoryItem[] | undefined): MatrixPoint[] {
  return (history || [])
    .filter((h) => h.dHist != null && h.dBurst != null)
    .map((h) => ({
      value: [h.dHist as number, Math.min(h.dBurst ?? 0, 1)] as [number, number],
      itemStyle: { color: h.alarm ? '#f56c6c' : '#67c23a' },
      name: h.label
    }))
}

/** 本次抽样点（蓝色大点）。 */
export function buildCurrentPoint(result: SparseSampleResult | null | undefined): MatrixCurrentPoint[] {
  const m = result?.matrix
  if (!m) return []
  return [{
    value: [m.dHist ?? 0, Math.min(m.dBurst ?? 0, 1)] as [number, number],
    symbolSize: 16,
    itemStyle: { color: '#409eff', borderColor: '#fff', borderWidth: 2 },
    label: { show: true, formatter: '本次', position: 'right', fontSize: 11 },
    name: '本次'
  }]
}

/** 完整 ECharts option。 */
export function buildMatrixOption(
  history: SparseHistoryItem[] | undefined,
  result: SparseSampleResult | null | undefined,
  thresholds: MatrixThresholds
) {
  const { dHistAlarm, dBurstTransient } = thresholds
  return {
    grid: { left: 48, right: 20, top: 20, bottom: 42 },
    tooltip: {
      trigger: 'item',
      formatter: (p: { name?: string; value: [number, number] }) =>
        `${p.name || ''}<br/>dHist ${Number(p.value[0]).toFixed(3)}<br/>dBurst ${Number(p.value[1]).toFixed(3)}`
    },
    xAxis: {
      name: 'dHist（历史偏离）', min: 0, max: 1, nameLocation: 'middle', nameGap: 26,
      nameTextStyle: { fontSize: 11 }
    },
    yAxis: { name: 'dBurst', min: 0, max: 1, nameTextStyle: { fontSize: 11 } },
    series: [
      {
        type: 'scatter',
        symbolSize: 9,
        data: buildMatrixPoints(history),
        markArea: {
          silent: true,
          label: { show: true, fontSize: 11, color: '#909399' },
          data: [
            [{ xAxis: dHistAlarm, yAxis: 0, name: '持续篡改（判）',
               itemStyle: { color: 'rgba(245,108,108,0.10)' } }, { xAxis: 1, yAxis: dBurstTransient }],
            [{ xAxis: dHistAlarm, yAxis: dBurstTransient, name: '瞬时事件（不判）',
               itemStyle: { color: 'rgba(230,162,60,0.10)' } }, { xAxis: 1, yAxis: 1 }],
            [{ xAxis: 0, yAxis: dBurstTransient, name: '正常活动',
               itemStyle: { color: 'rgba(103,194,58,0.07)' } }, { xAxis: dHistAlarm, yAxis: 1 }],
            [{ xAxis: 0, yAxis: 0, name: '正常',
               itemStyle: { color: 'rgba(103,194,58,0.12)' } }, { xAxis: dHistAlarm, yAxis: dBurstTransient }]
          ]
        },
        markLine: {
          silent: true,
          symbol: 'none',
          lineStyle: { type: 'dashed', color: '#c0c4cc' },
          data: [{ xAxis: dHistAlarm }, { yAxis: dBurstTransient }]
        }
      },
      { type: 'scatter', data: buildCurrentPoint(result), z: 10 }
    ]
  }
}

/** 从参数表里取阈值，缺省回落到默认值。 */
export function thresholdsFromParams(params: Record<string, number>): MatrixThresholds {
  return {
    dHistAlarm: params.dHistAlarm ?? DEFAULT_MATRIX_THRESHOLDS.dHistAlarm,
    dBurstTransient: params.dBurstTransient ?? DEFAULT_MATRIX_THRESHOLDS.dBurstTransient
  }
}
