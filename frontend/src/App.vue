<template>
  <div class="ctd-layout">
    <header class="ctd-header">
      <div class="ctd-brand">
        <div class="ctd-logo">
          <el-icon><Timer /></el-icon>
        </div>
        <div>
          <h1>摄像头篡改检测 · 低频巡检</h1>
          <span>Camera Tampering Detection · Low-frequency Inspection</span>
        </div>
      </div>

      <el-tooltip
        v-if="!store.algoOnline"
        :content="store.algoError || '算法服务未启动'"
        placement="bottom"
      >
        <el-tag type="danger" effect="light" round size="small">
          <el-icon><WarningFilled /></el-icon> 算法服务离线
        </el-tag>
      </el-tooltip>
      <el-tag v-else type="success" effect="light" round size="small">
        <el-icon><CircleCheckFilled /></el-icon> 算法服务在线 · {{ shortBase }}
      </el-tag>

      <div class="ctd-header-right">
        <span>1 小时 / 1 天一次拉帧 · 遮挡 / 失焦 / 移位·转动 / 冻结·重放</span>
        <el-divider direction="vertical" />
        <span class="ctd-mono">v3.0</span>
      </div>
    </header>

    <div class="ctd-body">
      <aside class="ctd-aside">
        <div class="ctd-nav-item active">
          <el-icon><Timer /></el-icon>
          <span>低频巡检</span>
          <span class="ctd-nav-index">01</span>
        </div>

        <div class="ctd-aside-footer">
          <div>算法层</div>
          <div class="ctd-mono task-id">{{ shortBase || '未连接' }}</div>
          <div class="aside-tip">
            机位基线与参照物由算法层按机位落盘，重启不丢。
          </div>
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
import { useAppStore } from '@/store/app'

const store = useAppStore()

const shortBase = computed(() => store.algoBaseUrl?.replace(/^https?:\/\//, '') || '')

onMounted(() => {
  store.loadHealth()
})
</script>

<style scoped>
.task-id {
  margin-top: 4px;
  color: #5b6577;
}

.aside-tip {
  margin-top: 10px;
  font-size: 11px;
  line-height: 1.5;
  color: #99a1b0;
}

.ctd-nav-item :deep(.el-icon) {
  font-size: 16px;
}
</style>
