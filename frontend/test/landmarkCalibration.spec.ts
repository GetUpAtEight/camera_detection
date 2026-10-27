import { describe, expect, it } from 'vitest'
import {
  MIN_RECT_SIZE,
  nextRectLabel,
  rectFromDrag,
  toRects
} from '../src/composables/useLandmarkCalibration'
import { clampNormalized } from '../src/composables/useLandmarkCalibration'

describe('标定对话框的纯几何', () => {
  it('拖拽矩形支持四个方向，输出归一化坐标', () => {
    const rect = rectFromDrag({ x: 0.2, y: 0.3 }, { x: 0.5, y: 0.1 })
    expect(rect.x).toBeCloseTo(0.2)
    expect(rect.y).toBeCloseTo(0.1)
    expect(rect.w).toBeCloseTo(0.3)
    expect(rect.h).toBeCloseTo(0.2)
  })

  it('鼠标坐标被夹在图片范围内', () => {
    const rect = { left: 100, top: 100, width: 200, height: 100 } as DOMRect
    const inside = clampNormalized({ clientX: 200, clientY: 150 } as MouseEvent, rect)
    expect(inside).toEqual({ x: 0.5, y: 0.5 })
    const outside = clampNormalized({ clientX: 0, clientY: 999 } as MouseEvent, rect)
    expect(outside).toEqual({ x: 0, y: 1 })
  })

  it('小于最小边长的拖拽不算框', () => {
    expect(MIN_RECT_SIZE).toBe(0.01)
    const tiny = rectFromDrag({ x: 0.5, y: 0.5 }, { x: 0.505, y: 0.505 })
    expect(tiny.w < MIN_RECT_SIZE || tiny.h < MIN_RECT_SIZE).toBe(true)
  })

  it('新框标签按同类计数递增', () => {
    expect(nextRectLabel('landmark', [])).toBe('参照物1')
    expect(
      nextRectLabel('landmark', [
        { x: 0, y: 0, w: 1, h: 1, label: '参照物1', kind: 'landmark' },
        { x: 0, y: 0, w: 1, h: 1, label: '忽略区1', kind: 'ignore' }
      ])
    ).toBe('参照物2')
    expect(nextRectLabel('ignore', [])).toBe('忽略区1')
  })

  it('已有参照物转成可编辑初始值，缺 label 时按序号补', () => {
    const rects = toRects([
      { x: 0.1, y: 0.1, w: 0.2, h: 0.2 },
      { x: 0.3, y: 0.3, w: 0.1, h: 0.1, kind: 'ignore', label: '桌面' }
    ])
    expect(rects[0]).toMatchObject({ label: '参照物1', kind: 'landmark' })
    expect(rects[1]).toMatchObject({ label: '桌面', kind: 'ignore' })
    expect(toRects(undefined)).toEqual([])
  })
})