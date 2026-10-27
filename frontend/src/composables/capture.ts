/**
 * 采样来源抓帧：摄像头 / 视频文件 / 上传图片 → `Blob[]`。
 *
 * 全部是与 DOM 打交道的命令式代码（getUserMedia、seek、canvas 合成），
 * 和判定结果、ECharts、模板库都没有关系，所以整块搬出 SparseView。
 * 本文件不改任何响应式状态：调用方拿 ref 传进来，函数只读它们。
 */
/** 统一抓帧宽度：算法层按比例缩放到 960 宽，前端先缩到同尺寸可以省带宽。 */
export const FRAME_WIDTH = 960

/** 摄像头连拍帧间隔：模拟 0.5~2s 的 burst。 */
export const BURST_GAP_MS = 600

export function drawNoise(
  ctx: CanvasRenderingContext2D,
  w: number,
  h: number,
  sigma: number
): void {
  if (sigma <= 0) return
  const img = ctx.getImageData(0, 0, w, h)
  const d = img.data
  for (let i = 0; i < d.length; i += 4) {
    const n = (Math.random() - 0.5) * 2 * sigma
    d[i] = Math.max(0, Math.min(255, d[i] + n))
    d[i + 1] = Math.max(0, Math.min(255, d[i + 1] + n))
    d[i + 2] = Math.max(0, Math.min(255, d[i + 2] + n))
  }
  ctx.putImageData(img, 0, 0)
}

export function canvasToBlob(canvas: HTMLCanvasElement, quality = 0.92): Promise<Blob> {
  return new Promise((resolve, reject) => {
    canvas.toBlob((b) => (b ? resolve(b) : reject(new Error('canvas 导出失败'))), 'image/jpeg', quality)
  })
}

/** 从 video 元素抓一帧画到 canvas（统一到 960 宽）。 */
export function grabFrame(video: HTMLVideoElement): HTMLCanvasElement {
  const vw = video.videoWidth || 1280
  const vh = video.videoHeight || 720
  const w = FRAME_WIDTH
  const h = Math.round((vh / vw) * w)
  const canvas = document.createElement('canvas')
  canvas.width = w
  canvas.height = h
  const ctx = canvas.getContext('2d')!
  ctx.drawImage(video, 0, 0, w, h)
  return canvas
}

/** 把位图/文件画成 960 宽的 canvas。 */
export async function bitmapToCanvas(src: Blob): Promise<HTMLCanvasElement> {
  const bmp = await createImageBitmap(src)
  const canvas = document.createElement('canvas')
  canvas.width = FRAME_WIDTH
  canvas.height = Math.round((bmp.height / bmp.width) * FRAME_WIDTH)
  canvas.getContext('2d')!.drawImage(bmp, 0, 0, canvas.width, canvas.height)
  return canvas
}

/** 摄像头连拍 K 帧。 */
export async function captureFromCamera(
  video: HTMLVideoElement | undefined,
  ready: boolean,
  count: number
): Promise<Blob[]> {
  if (!video || !ready) throw new Error('摄像头未就绪')
  const blobs: Blob[] = []
  for (let i = 0; i < count; i += 1) {
    blobs.push(await canvasToBlob(grabFrame(video)))
    if (i < count - 1) await new Promise((r) => setTimeout(r, BURST_GAP_MS))
  }
  return blobs
}

/** 从视频文件的不同时间点抽 K 帧。 */
export async function captureFromVideo(
  video: HTMLVideoElement | undefined,
  ready: boolean,
  count: number
): Promise<Blob[]> {
  if (!video || !ready) throw new Error('请先选择一个视频文件')
  if (!video.duration || isNaN(video.duration)) {
    await new Promise<void>((resolve) => {
      video.addEventListener('loadedmetadata', () => resolve(), { once: true })
    })
  }
  const duration = video.duration || 1
  const blobs: Blob[] = []
  for (let i = 0; i < count; i += 1) {
    const t = Math.min(duration * 0.95, duration * (0.1 + 0.7 * (i / Math.max(count - 1, 1))))
    video.currentTime = t
    await new Promise<void>((resolve) =>
      video.addEventListener('seeked', () => resolve(), { once: true })
    )
    blobs.push(await canvasToBlob(grabFrame(video)))
  }
  return blobs
}

export type SynthKind = 'normal' | 'covered' | 'defocused'

/** 由一张底图合成 K 帧（每帧加独立噪声，模拟真实传感器）。 */
export async function synthFrames(
  base: HTMLCanvasElement,
  kind: SynthKind,
  count: number
): Promise<Blob[]> {
  const blobs: Blob[] = []
  for (let i = 0; i < count; i += 1) {
    const canvas = document.createElement('canvas')
    canvas.width = base.width
    canvas.height = base.height
    const ctx = canvas.getContext('2d')!
    if (kind === 'defocused') ctx.filter = 'blur(7px)'
    ctx.drawImage(base, 0, 0)
    ctx.filter = 'none'
    if (kind === 'covered') {
      ctx.fillStyle = 'rgb(38,40,42)'
      ctx.fillRect(0, 0, canvas.width, canvas.height)
    }
    // 真实摄像头每帧都有独立噪声，这里必须模拟，否则会被判成"冻结重放"
    drawNoise(ctx, canvas.width, canvas.height, kind === 'covered' ? 2.0 : 2.5)
    blobs.push(await canvasToBlob(canvas))
  }
  return blobs
}

export interface CaptureInputs {
  source: string
  cameraReady: boolean
  videoReady: boolean
  videoEl?: HTMLVideoElement
  videoFileEl?: HTMLVideoElement
  pendingFiles: Blob[]
  /** 当前判定结果里的关键帧（dataURL），最后的兜底来源 */
  keyframe?: string
}

/**
 * 取一张底图给「标定参照物」对话框用。
 *
 * 优先级：摄像头 > 视频 > 已选图片 > 已有证据图。
 */
export async function captureOne(inputs: CaptureInputs): Promise<Blob | null> {
  if (inputs.source === 'camera' && inputs.cameraReady) {
    return (await captureFromCamera(inputs.videoEl, inputs.cameraReady, 1))[0]
  }
  if (inputs.source === 'video' && inputs.videoReady) {
    return (await captureFromVideo(inputs.videoFileEl, inputs.videoReady, 1))[0]
  }
  if (inputs.pendingFiles.length) {
    return inputs.pendingFiles[0]
  }
  if (inputs.keyframe) {
    const resp = await fetch(inputs.keyframe)
    return resp.blob()
  }
  return null
}

/** 一键演示用的底图：摄像头/视频/图片，都没有时退回内置样图。 */
export async function demoBaseCanvas(inputs: {
  source: string
  cameraReady: boolean
  videoReady: boolean
  videoEl?: HTMLVideoElement
  videoFileEl?: HTMLVideoElement
  pendingFiles: Blob[]
}): Promise<HTMLCanvasElement | null> {
  if (inputs.source === 'camera' && inputs.cameraReady && inputs.videoEl) {
    return grabFrame(inputs.videoEl)
  }
  if (inputs.source === 'video' && inputs.videoReady && inputs.videoFileEl) {
    const video = inputs.videoFileEl
    video.currentTime = Math.min(video.duration * 0.2, 2)
    await new Promise<void>((r) => video.addEventListener('seeked', () => r(), { once: true }))
    return grabFrame(video)
  }
  if (inputs.pendingFiles.length) {
    return bitmapToCanvas(inputs.pendingFiles[0])
  }
  // 兜底：没有摄像头/视频/上传图片时，用内置样图。
  // 这样"只想看低频巡检效果"的人零准备就能点一键演示。
  const res = await fetch('/demo/reference_normal.jpg')
  if (!res.ok) throw new Error('样图加载失败')
  return bitmapToCanvas(await res.blob())
}

/** 把 dataURL 转 Blob（结果里的 keyframe 就是 dataURL）。 */
export function dataUrlToBlob(dataUrl: string): Promise<Blob> {
  return fetch(dataUrl).then((r) => r.blob())
}
