import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { algoApi, mediaApi } from '@/api'
import type { AlgorithmMeta, MediaItem } from '@/types'

/**
 * 算法层离线时的兜底元数据，保证演示环境即使算法服务没起来，
 * 前端界面（算法选择、参数表单）仍然可用。
 */
const FALLBACK_ALGORITHMS: AlgorithmMeta[] = [
  {
    name: 'laplacian',
    displayName: '拉普拉斯方差（失焦检测）',
    description: 'Laplacian 方差衡量清晰度，模糊时方差下降，用于检测调焦、涂抹镜头。',
    tamperTypes: ['normal', 'defocused'],
    requiresReference: false,
    kind: 'opencv',
    available: true,
    thresholdScore: 0.5,
    params: [
      { key: 'threshold', label: '清晰度阈值', default: 100, min: 5, max: 800, step: 5, type: 'number', description: '方差低于该值判定为失焦' },
      { key: 'relative_ratio', label: '相对参考帧下降比', default: 0.45, min: 0.05, max: 1, step: 0.05, type: 'number' }
    ]
  },
  {
    name: 'entropy',
    displayName: '直方图熵（遮挡检测）',
    description: '灰度直方图香农熵下降代表画面信息量减少，配合分块熵可识别局部遮挡。',
    tamperTypes: ['normal', 'covered'],
    requiresReference: false,
    kind: 'opencv',
    available: true,
    thresholdScore: 0.5,
    params: [
      { key: 'threshold', label: '全局熵阈值', default: 3, min: 0.5, max: 8, step: 0.1, unit: 'bit', type: 'number' },
      { key: 'block_threshold', label: '分块熵阈值', default: 2, min: 0.2, max: 8, step: 0.1, unit: 'bit', type: 'number' },
      { key: 'block_ratio', label: '低熵块占比阈值', default: 0.5, min: 0.1, max: 1, step: 0.05, type: 'number' },
      { key: 'grid', label: '分块数', default: 4, min: 2, max: 8, step: 1, type: 'int' }
    ]
  },
  {
    name: 'scene_diff',
    displayName: '帧差 / 场景差异（遮挡+移位）',
    description: '与参考帧逐像素比较，平均绝对差骤增说明画面被遮挡或视角改变。',
    tamperTypes: ['normal', 'covered', 'moved'],
    requiresReference: true,
    kind: 'opencv',
    available: true,
    thresholdScore: 0.5,
    params: [
      { key: 'threshold', label: '帧差阈值', default: 30, min: 1, max: 120, step: 1, type: 'number' },
      { key: 'covered_threshold', label: '判定为遮挡的帧差', default: 60, min: 5, max: 200, step: 1, type: 'number' },
      { key: 'blur_kernel', label: '高斯模糊核', default: 5, min: 0, max: 31, step: 2, type: 'int' }
    ]
  },
  {
    name: 'orb',
    displayName: 'ORB 特征匹配（移位检测）',
    description: 'ORB 特征点匹配 + RANSAC 单应性估计，分解平移量与旋转角，专治清晰度正常的镜头移位。',
    tamperTypes: ['normal', 'moved'],
    requiresReference: true,
    kind: 'opencv',
    available: true,
    thresholdScore: 0.5,
    params: [
      { key: 'motion_pixels', label: '平移阈值', default: 50, min: 2, max: 400, step: 2, unit: 'px', type: 'number' },
      { key: 'n_features', label: '特征点数量', default: 800, min: 100, max: 5000, step: 100, type: 'int' },
      { key: 'min_inlier_ratio', label: '最低内点率', default: 0.3, min: 0.05, max: 0.95, step: 0.05, type: 'number' },
      { key: 'ratio_test', label: 'Lowe 比值', default: 0.75, min: 0.5, max: 0.95, step: 0.01, type: 'number' }
    ]
  },
  {
    name: 'edge',
    displayName: '边缘消失率（统一检测）',
    description: '参考帧边缘在当前帧消失的比例，与时间均值做差后按自适应阈值判定，遮挡与失焦都能覆盖。',
    tamperTypes: ['normal', 'covered'],
    requiresReference: true,
    kind: 'opencv',
    available: true,
    thresholdScore: 0.5,
    params: [
      { key: 'threshold', label: '自适应偏差阈值', default: 0.25, min: 0.05, max: 1, step: 0.05, type: 'number' },
      { key: 'min_rate', label: '最低消失率', default: 0.2, min: 0.02, max: 1, step: 0.02, type: 'number' },
      { key: 'canny_low', label: 'Canny 低阈值', default: 50, min: 5, max: 200, step: 5, type: 'number' },
      { key: 'canny_high', label: 'Canny 高阈值', default: 150, min: 10, max: 400, step: 10, type: 'number' },
      { key: 'tolerance', label: '边缘抖动容差', default: 2, min: 0, max: 9, step: 1, unit: 'px', type: 'int' },
      { key: 'history', label: '时间窗口', default: 60, min: 5, max: 300, step: 5, unit: '帧', type: 'int' }
    ]
  },
  {
    name: 'yolo',
    displayName: 'YOLO 深度学习检测',
    description: '复用 GPU 服务器上的 yolo26n.pt，输出目标检测框，演示深度学习调用链路。',
    tamperTypes: ['normal'],
    requiresReference: false,
    kind: 'yolo',
    available: false,
    thresholdScore: 0.5,
    params: [
      { key: 'conf', label: '置信度阈值', default: 0.25, min: 0.01, max: 0.95, step: 0.01, type: 'number' },
      { key: 'imgsz', label: '推理尺寸', default: 640, min: 320, max: 1280, step: 32, unit: 'px', type: 'int' },
      { key: 'max_detections', label: '最多保留框数', default: 50, min: 1, max: 300, step: 1, type: 'int' }
    ]
  }
]

export const useAppStore = defineStore('app', () => {
  const mediaList = ref<MediaItem[]>([])
  const algorithms = ref<AlgorithmMeta[]>([])
  const algoOnline = ref(false)
  const algoError = ref('')
  const algoBaseUrl = ref('')
  const currentMediaId = ref<string>(localStorage.getItem('ctd.mediaId') || '')
  const currentTaskId = ref<string>(localStorage.getItem('ctd.taskId') || '')

  const currentMedia = computed(() => mediaList.value.find((m) => m.id === currentMediaId.value) || null)
  /** 主力算法：参与 ≥2 票集成，可一键全选 */
  const classicAlgorithms = computed(() =>
    algorithms.value.filter((a) => a.kind !== 'yolo' && a.kind !== 'legacy')
  )
  /** 辅助算法：按场景选用，不参与投票 */
  const auxAlgorithms = computed(() => algorithms.value.filter((a) => a.kind === 'legacy'))

  function setMedia(id: string) {
    currentMediaId.value = id
    localStorage.setItem('ctd.mediaId', id)
  }

  function setTask(id: string) {
    currentTaskId.value = id
    if (id) {
      localStorage.setItem('ctd.taskId', id)
    } else {
      localStorage.removeItem('ctd.taskId')
    }
  }

  async function loadMedia() {
    mediaList.value = await mediaApi.list()
    if (currentMediaId.value && !mediaList.value.some((m) => m.id === currentMediaId.value)) {
      currentMediaId.value = mediaList.value[0]?.id || ''
    }
    return mediaList.value
  }

  async function loadAlgorithms() {
    try {
      const payload = await algoApi.algorithms()
      algoOnline.value = payload.online
      algoError.value = payload.error || ''
      algoBaseUrl.value = payload.baseUrl
      algorithms.value = payload.online && payload.algorithms.length ? payload.algorithms : FALLBACK_ALGORITHMS
    } catch (e: any) {
      algoOnline.value = false
      algoError.value = e?.message || '算法服务不可用'
      algorithms.value = FALLBACK_ALGORITHMS
    }
    return algorithms.value
  }

  return {
    mediaList,
    algorithms,
    classicAlgorithms,
    auxAlgorithms,
    algoOnline,
    algoError,
    algoBaseUrl,
    currentMediaId,
    currentTaskId,
    currentMedia,
    setMedia,
    setTask,
    loadMedia,
    loadAlgorithms
  }
})
