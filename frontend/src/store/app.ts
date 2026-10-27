import { defineStore } from 'pinia'
import { ref } from 'vue'
import { algoApi } from '@/api'

/**
 * 全局状态：只保留「算法层是否在线」这一项。
 *
 * 重构为只做低频巡检后，媒体库 / 当前任务等状态已无必要；
 * 机位状态全部由算法层按 cameraId 落盘，前端不缓存业务数据。
 */
export const useAppStore = defineStore('app', () => {
  const algoOnline = ref(false)
  const algoBaseUrl = ref('')
  const algoError = ref('')

  async function loadHealth() {
    try {
      const res = await algoApi.health()
      algoOnline.value = !!res?.online
      algoBaseUrl.value = res?.baseUrl || ''
      algoError.value = res?.error || ''
    } catch (e: unknown) {
      algoOnline.value = false
      algoError.value = e instanceof Error ? e.message : '算法服务不可用'
    }
  }

  return { algoOnline, algoBaseUrl, algoError, loadHealth }
})
