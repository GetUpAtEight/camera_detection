<template>
  <div class="ctd-layout">
    <header class="ctd-header">
      <div class="ctd-brand">
        <div class="ctd-logo">
          <el-icon><VideoCameraFilled /></el-icon>
        </div>
        <div>
          <h1>摄像头篡改检测系统</h1>
          <span>Camera Tampering Detection</span>
        </div>
      </div>

      <el-tooltip v-if="!store.algoOnline" :content="store.algoError || '算法服务未启动'" placement="bottom">
        <el-tag type="danger" effect="light" round size="small">
          <el-icon><WarningFilled /></el-icon> 算法服务离线
        </el-tag>
      </el-tooltip>
      <el-tag v-else type="success" effect="light" round size="small">
        <el-icon><CircleCheckFilled /></el-icon> 算法服务在线 · {{ shortBase }}
      </el-tag>

      <div class="ctd-header-right">
        <span>5 主力算法 + 3 辅助算法 + YOLO 深度学习</span>
        <el-divider direction="vertical" />
        <span class="ctd-mono">v2.1</span>
      </div>
    </header>

    <div class="ctd-body">
      <aside class="ctd-aside">
        <div
          v-for="(item, index) in navs"
          :key="item.name"
          class="ctd-nav-item"
          :class="{ active: route.name === item.name }"
          @click="go(item.name)"
        >
          <el-icon><component :is="item.icon" /></el-icon>
          <span>{{ item.title }}</span>
          <span class="ctd-nav-index">0{{ index + 1 }}</span>
        </div>

        <div class="ctd-aside-footer">
          <div>当前任务</div>
          <div class="ctd-mono task-id">{{ store.currentTaskId ? store.currentTaskId.slice(0, 8) : '未选择' }}</div>
          <el-button
            v-if="store.currentTaskId"
            size="small"
            text
            type="primary"
            style="padding: 0; margin-top: 6px"
            @click="go('analysis')"
          >
            查看分析 →
          </el-button>
        </div>
      </aside>

      <main class="ctd-main">
        <router-view />
      </main>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAppStore } from '@/store/app'

const store = useAppStore()
const route = useRoute()
const router = useRouter()

const navs = [
  { name: 'upload', title: '媒体上传', icon: 'UploadFilled' },
  { name: 'detect', title: '运行检测', icon: 'VideoPlay' },
  { name: 'live', title: '实时监控', icon: 'VideoCamera' },
  { name: 'sparse', title: '低频巡检', icon: 'Timer' },
  { name: 'compare', title: '算法对比', icon: 'DataAnalysis' },
  { name: 'analysis', title: '结果分析', icon: 'TrendCharts' }
]

const shortBase = computed(() => store.algoBaseUrl?.replace(/^https?:\/\//, '') || '')

function go(name: string) {
  router.push({ name })
}

onMounted(async () => {
  store.loadAlgorithms()
  store.loadMedia()
})
</script>

<style scoped>
.task-id {
  margin-top: 4px;
  color: #5b6577;
}

.ctd-nav-item :deep(.el-icon) {
  font-size: 16px;
}
</style>
