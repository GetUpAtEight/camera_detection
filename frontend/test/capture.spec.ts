import { beforeEach, describe, expect, it, vi } from 'vitest'
import {
  BURST_GAP_MS,
  FRAME_WIDTH,
  captureOne,
  canvasToBlob,
  dataUrlToBlob,
  drawNoise,
  grabFrame
} from '../src/composables/capture'

/** 造一个最小的 canvas 替身：只需要 getContext 与 toBlob 参与计算。 */
function fakeCanvas(): HTMLCanvasElement {
  return {
    width: 2,
    height: 2,
    getContext: () => ({ putImageData: vi.fn(), getImageData: () => ({ data: new Uint8ClampedArray(16) }) }),
    toBlob: (cb: (b: Blob | null) => void) => cb(new Blob(['x']))
  } as unknown as HTMLCanvasElement
}

beforeEach(() => {
  vi.unstubAllGlobals()
})

describe('抓帧工具', () => {
  it('常量就是页面依赖的尺寸与 burst 间隔', () => {
    expect(FRAME_WIDTH).toBe(960)
    expect(BURST_GAP_MS).toBe(600)
  })

  it('grabFrame 按 960 宽等比缩放（4:3 → 720 高）', () => {
    const drawImage = vi.fn()
    vi.stubGlobal('document', {
      createElement: () => ({
        width: 0,
        height: 0,
        getContext: () => ({ drawImage })
      })
    })
    const canvas = grabFrame({ videoWidth: 1280, videoHeight: 960 } as HTMLVideoElement)
    expect(canvas.width).toBe(960)
    expect(canvas.height).toBe(720)
    expect(drawImage).toHaveBeenCalledWith(expect.anything(), 0, 0, 960, 720)
  })

  it('drawNoise 在 sigma<=0 时不动画面', () => {
    const putImageData = vi.fn()
    drawNoise({ getImageData: vi.fn(), putImageData } as unknown as CanvasRenderingContext2D, 1, 1, 0)
    expect(putImageData).not.toHaveBeenCalled()
  })

  it('canvasToBlob 成功/失败各有明确出口', async () => {
    await expect(canvasToBlob(fakeCanvas())).resolves.toBeInstanceOf(Blob)
    const broken = { toBlob: (cb: (b: Blob | null) => void) => cb(null) } as unknown as HTMLCanvasElement
    await expect(canvasToBlob(broken)).rejects.toThrow('canvas 导出失败')
  })

  it('取底图：优先已选图片，其次已有证据图，都没有返回 null', async () => {
    const file = new Blob(['img'])
    const got = await captureOne({
      source: 'files',
      cameraReady: false,
      videoReady: false,
      pendingFiles: [file],
      keyframe: undefined
    })
    expect(got).toBe(file)

    const fromKeyframe = await captureOne({
      source: 'camera',
      cameraReady: false,
      videoReady: false,
      pendingFiles: [],
      keyframe: 'data:image/jpeg;base64,AAAA'
    })
    expect(fromKeyframe).toBeTruthy()
    expect(fromKeyframe?.type).toBe('image/jpeg')

    const none = await captureOne({
      source: 'camera',
      cameraReady: false,
      videoReady: false,
      pendingFiles: []
    })
    expect(none).toBeNull()
  })

  it('dataUrlToBlob 走 fetch 转 Blob', async () => {
    const blob = await dataUrlToBlob('data:image/jpeg;base64,AAAA')
    expect(blob.type).toBe('image/jpeg')
    expect(blob.size).toBeGreaterThan(0)
  })
})