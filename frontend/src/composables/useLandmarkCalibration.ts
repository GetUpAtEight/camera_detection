/**
 * 参照物标定对话框的状态机。
 *
 * 抽出原因：这个对话框有 10 个互相牵连的 ref（底图、模式、已画框、
 * 正在拖拽的草稿、保存中、是否入库…），塞在 2000 行的页面里根本看不出
 * 谁改谁。这里把它们收成一个 composable，只暴露页面真正需要的东西。
 *
 * 调用方（页面）只需要提供两件事：
 *  - `loadImage`：取一张底图（摄像头/视频/图片/已有证据图）；
 *  - 保存/清除时的接口调用（由页面注入，便于复用与测试）。
 */
import { computed, ref } from 'vue'
import type { SparseLandmark } from '@/api'

export type LmKind = 'landmark' | 'ignore'

export interface LmRect {
  x: number
  y: number
  w: number
  h: number
  label: string
  kind: LmKind
}

/** 拖拽最小边长（归一化坐标）：小于它视为误触，不建框。 */
export const MIN_RECT_SIZE = 0.01

/** 归一化坐标（0~1），超出图片外的部分截断。 */
export function clampNormalized(ev: MouseEvent, rect: DOMRect): { x: number; y: number } {
  const x = Math.min(1, Math.max(0, (ev.clientX - rect.left) / rect.width))
  const y = Math.min(1, Math.max(0, (ev.clientY - rect.top) / rect.height))
  return { x, y }
}

/** 由起点与当前点算出归一化矩形（支持四个方向拖）。 */
export function rectFromDrag(
  start: { x: number; y: number },
  cur: { x: number; y: number }
): { x: number; y: number; w: number; h: number } {
  return {
    x: Math.min(start.x, cur.x),
    y: Math.min(start.y, cur.y),
    w: Math.abs(cur.x - start.x),
    h: Math.abs(cur.y - start.y)
  }
}

/** 新框的默认标签：按同类已画个数递增。 */
export function nextRectLabel(kind: LmKind, rects: LmRect[]): string {
  const count = rects.filter((r) => r.kind === kind).length + 1
  return `${kind === 'ignore' ? '忽略区' : '参照物'}${count}`
}

/** 已标定的参照物 → 对话框里的可编辑初始值。 */
export function toRects(landmarks: SparseLandmark[] | undefined): LmRect[] {
  return (landmarks || []).map((lm, i) => ({
    x: lm.x,
    y: lm.y,
    w: lm.w,
    h: lm.h,
    label: lm.label || `${lm.kind === 'ignore' ? '忽略区' : '参照物'}${i + 1}`,
    kind: (lm.kind === 'ignore' ? 'ignore' : 'landmark') as LmKind
  }))
}

export interface UseLandmarkCalibrationOptions {
  /** 取一张底图；拿不到返回 null。 */
  loadImage: () => Promise<Blob | null>
  /** 保存标定（页面注入，内部负责调接口 + 提示）。 */
  onSave?: (
    rects: LmRect[],
    frame: Blob | null,
    admitFrame: boolean
  ) => Promise<void>
  /** 清除已有标定。 */
  onClear?: () => Promise<void>
}

export function useLandmarkCalibration(options: UseLandmarkCalibrationOptions) {
  const dialogVisible = ref(false)
  const imageUrl = ref('')
  const imageBlob = ref<Blob | null>(null)
  const mode = ref<LmKind>('landmark')
  const rects = ref<LmRect[]>([])
  const drawing = ref(false)
  const draft = ref({ x: 0, y: 0, w: 0, h: 0 })
  const start = ref({ x: 0, y: 0 })
  const saving = ref(false)
  const admitFrame = ref(true)
  const imgEl = ref<HTMLImageElement>()

  const anchorCount = computed(() => rects.value.filter((r) => r.kind !== 'ignore').length)

  function revokeUrl() {
    if (imageUrl.value) URL.revokeObjectURL(imageUrl.value)
    imageUrl.value = ''
  }

  /** 打开对话框：用当前已标定的参照物做初始值，并要求重新取一张底图。 */
  function open(landmarks: SparseLandmark[] | undefined) {
    rects.value = toRects(landmarks)
    mode.value = 'landmark'
    imageBlob.value = null
    revokeUrl()
    dialogVisible.value = true
  }

  async function capture() {
    const blob = await options.loadImage()
    if (!blob) return false
    imageBlob.value = blob
    revokeUrl()
    imageUrl.value = URL.createObjectURL(blob)
    return true
  }

  function relPos(ev: MouseEvent) {
    const img = imgEl.value
    if (!img) return { x: 0, y: 0 }
    return clampNormalized(ev, img.getBoundingClientRect())
  }

  function onDown(ev: MouseEvent) {
    if (!imageUrl.value) return
    const p = relPos(ev)
    start.value = p
    draft.value = { x: p.x, y: p.y, w: 0, h: 0 }
    drawing.value = true
  }

  function onMove(ev: MouseEvent) {
    if (!drawing.value) return
    draft.value = rectFromDrag(start.value, relPos(ev))
  }

  function onUp() {
    if (!drawing.value) return
    drawing.value = false
    const d = draft.value
    if (d.w < MIN_RECT_SIZE || d.h < MIN_RECT_SIZE) return
    const kind = mode.value
    rects.value.push({
      x: d.x, y: d.y, w: d.w, h: d.h,
      kind,
      label: nextRectLabel(kind, rects.value)
    })
    draft.value = { x: 0, y: 0, w: 0, h: 0 }
  }

  function undo() {
    rects.value.pop()
  }

  function clearRects() {
    rects.value = []
  }

  function removeAt(i: number) {
    rects.value.splice(i, 1)
  }

  async function save() {
    if (!options.onSave) return
    saving.value = true
    try {
      await options.onSave(rects.value, imageBlob.value, admitFrame.value)
    } finally {
      saving.value = false
    }
  }

  async function clearAll() {
    if (!options.onClear) return
    await options.onClear()
  }

  function close() {
    dialogVisible.value = false
  }

  return {
    dialogVisible,
    imageUrl,
    imageBlob,
    mode,
    rects,
    drawing,
    draft,
    saving,
    admitFrame,
    imgEl,
    anchorCount,
    open,
    capture,
    onDown,
    onMove,
    onUp,
    undo,
    clearRects,
    removeAt,
    save,
    clearAll,
    close,
    revokeUrl
  }
}
