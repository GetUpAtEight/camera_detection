<template>
  <div>
    <div class="ctd-page-head">
      <h2>算法对比</h2>
      <p>把多个算法对同一输入的检测结果放在同一坐标系下对比：曲线叠加、指标并排、类型分布与一致性。</p>
    </div>

    <div class="ctd-card">
      <div class="picker-row">
        <div class="picker-main">
          <label class="field-label">对比任务</label>
          <TaskPicker v-model="taskId" @update:model-value="load" />
        </div>
        <div v-if="compare" class="picker-meta">
          <span>{{ compare.mediaName }}</span>
          <el-tag size="small" effect="plain">{{ compare.mediaType === 'VIDEO' ? '视频' : '图片' }}</el-tag>
          <span class="ctd-mono">{{ compare.frames }} 帧 · {{ compare.stats.length }} 个算法</span>
        </div>
        <el-button :icon="VideoPlay" @click="router.push({ name: 'detect' })">去运行新检测</el-button>
      </div>
    </div>

    <template v-if="compare">
      <div class="stats-row">
        <StatCard label="采样帧数" :value="compare.frames" />
        <StatCard label="参与算法" :value="compare.stats.length" hint="含 YOLO 时该行不参与篡改判定" />
        <StatCard
          label="算法整体一致性"
          :value="formatPercent(compare.overallAgreement)"
          :tone="compare.overallAgreement >= 0.8 ? 'success' : 'warning'"
          hint="两两算法判定一致率的均值"
        />
        <StatCard
          label="标注数据"
          :value="compare.hasGroundTruth ? '已导入' : '未导入'"
          :tone="compare.hasGroundTruth ? 'success' : 'default'"
          hint="导入后可在分析页看到 P/R/F1"
        />
      </div>

      <div class="ctd-card">
        <h3 class="ctd-card-title">
          <el-icon><DataLine /></el-icon> 多算法分数曲线叠加
          <span class="sub">同一坐标系，红线为 0.5 判定阈值</span>
        </h3>
        <ScoreChart
          :series="chartSeries"
          :times="chartTimes"
          :frame-indexes="frameIndexList"
          :x-mode="isVideo ? 'time' : 'frame'"
          :threshold="compare.thresholdScore"
          height="340px"
        />
      </div>

      <div class="charts-row">
        <div class="ctd-card">
          <h3 class="ctd-card-title"><el-icon><Histogram /></el-icon> 告警率与异常分对比</h3>
          <BarChart
            :categories="algorithmLabels"
            :series="metricSeries"
            percent
            :y-max="1"
            height="280px"
          />
        </div>
        <div class="ctd-card">
          <h3 class="ctd-card-title"><el-icon><PieChart /></el-icon> 篡改类型分布</h3>
          <BarChart
            :categories="algorithmLabels"
            :series="typeSeries"
            stack
            height="280px"
          />
        </div>
      </div>

      <div class="ctd-card">
        <h3 class="ctd-card-title">
          <el-icon><Grid /></el-icon> 关键指标并排
          <span class="sub">平均耗时来自算法层上报的单帧耗时</span>
        </h3>
        <el-table :data="compare.stats" size="small" stripe>
          <el-table-column label="算法" min-width="190">
            <template #default="{ row }">
              <span class="algo-dot" :style="{ background: algoColor(row.algorithm) }"></span>
              {{ row.displayName }}
            </template>
          </el-table-column>
          <el-table-column prop="frames" label="帧数" width="80" />
          <el-table-column prop="tamperedFrames" label="告警帧" width="90" />
          <el-table-column label="告警率" width="100">
            <template #default="{ row }">{{ formatPercent(row.tamperRate) }}</template>
          </el-table-column>
          <el-table-column prop="events" label="事件数" width="90" />
          <el-table-column label="平均分" width="90">
            <template #default="{ row }">
              <span class="ctd-mono">{{ row.avgScore?.toFixed(3) }}</span>
            </template>
          </el-table-column>
          <el-table-column label="峰值" width="90">
            <template #default="{ row }">
              <span class="ctd-mono" :style="{ color: row.maxScore >= 0.5 ? 'var(--ctd-danger)' : 'inherit' }">
                {{ row.maxScore?.toFixed(3) }}
              </span>
            </template>
          </el-table-column>
          <el-table-column label="平均单帧耗时" width="130">
            <template #default="{ row }">
              <span class="ctd-mono">{{ row.avgCostMs != null ? row.avgCostMs.toFixed(2) + ' ms' : '-' }}</span>
            </template>
          </el-table-column>
          <el-table-column label="主导类型" width="110">
            <template #default="{ row }">
              <el-tag size="small" effect="plain" :style="{ color: TAMPER_TYPE_COLOR[row.dominantTamperType], borderColor: 'transparent', background: TAMPER_TYPE_COLOR[row.dominantTamperType] + '14' }">
                {{ TAMPER_TYPE_TEXT[row.dominantTamperType] }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="skipped" label="跳过帧" width="90" />
        </el-table>
      </div>

      <div class="ctd-card">
        <h3 class="ctd-card-title">
          <el-icon><Connection /></el-icon> 算法一致性（两两对比）
          <span class="sub">同一帧两个算法判定是否一致</span>
        </h3>
        <div v-if="compare.consistency.length" class="consistency-list">
          <div v-for="pair in compare.consistency" :key="pair.algorithmA + pair.algorithmB" class="consistency-item">
            <div class="pair-name">
              <span class="algo-dot" :style="{ background: algoColor(pair.algorithmA) }"></span>
              {{ shortOf(pair.algorithmA) }}
              <span class="vs">vs</span>
              <span class="algo-dot" :style="{ background: algoColor(pair.algorithmB) }"></span>
              {{ shortOf(pair.algorithmB) }}
            </div>
            <el-progress
              :percentage="Math.round(pair.agreement * 100)"
              :stroke-width="10"
              :color="pair.agreement >= 0.8 ? '#22a06b' : pair.agreement >= 0.6 ? '#d97706' : '#e5484d'"
            />
            <span class="ctd-mono frames">{{ pair.comparedFrames }} 帧</span>
          </div>
        </div>
        <el-empty v-else description="至少选择 2 个经典算法才能计算一致性" :image-size="70" />
      </div>
    </template>

    <el-empty v-else-if="!loading" description="请选择一个已完成的检测任务" :image-size="90" />
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Connection, DataLine, Grid, Histogram, PieChart, VideoPlay } from '@element-plus/icons-vue'
import type { CompareData, FrameResultItem, TaskItem } from '@/types'
import { detectionApi } from '@/api'
import { useAppStore } from '@/store/app'
import { ALGO_COLORS, TAMPER_TYPE_COLOR, TAMPER_TYPE_TEXT, algoColor, algoShort, formatPercent } from '@/utils/format'
import TaskPicker from '@/components/TaskPicker.vue'
import ScoreChart from '@/components/ScoreChart.vue'
import BarChart from '@/components/BarChart.vue'
import StatCard from '@/components/StatCard.vue'

const route = useRoute()
const router = useRouter()
const store = useAppStore()

const taskId = ref<string>((route.query.taskId as string) || store.currentTaskId || '')
const compare = ref<CompareData | null>(null)
const frames = ref<FrameResultItem[]>([])
const taskInfo = ref<TaskItem | null>(null)
const loading = ref(false)

const labelMap = computed<Record<string, string>>(() => {
  const map: Record<string, string> = {}
  for (const algo of store.algorithms) map[algo.name] = algo.displayName
  for (const stat of compare.value?.stats || []) map[stat.algorithm] = stat.displayName
  return map
})

const isVideo = computed(() => taskInfo.value?.mediaType === 'VIDEO')

const frameIndexList = computed(() =>
  Array.from(new Set(frames.value.map((f) => f.frameIndex))).sort((a, b) => a - b)
)

const chartTimes = computed(() => {
  const timeMap = new Map<number, number>()
  for (const item of frames.value) timeMap.set(item.frameIndex, item.timestampMs)
  return frameIndexList.value.map((fi) => timeMap.get(fi) || 0)
})

const chartSeries = computed(() =>
  (compare.value?.stats || []).map((stat) => {
    const scoreMap = new Map<number, number>()
    for (const item of frames.value) {
      if (item.algorithm === stat.algorithm) scoreMap.set(item.frameIndex, item.score)
    }
    return {
      name: stat.algorithm,
      label: algoShort(stat.algorithm, stat.displayName),
      color: ALGO_COLORS[stat.algorithm] || '#2f6fed',
      data: frameIndexList.value.map((fi) => (scoreMap.has(fi) ? scoreMap.get(fi)! : null))
    }
  })
)

const algorithmLabels = computed(() =>
  (compare.value?.stats || []).map((s) => algoShort(s.algorithm, s.displayName))
)

const metricSeries = computed(() => {
  const stats = (compare.value?.stats || []).filter((s) => s.algorithm !== 'yolo')
  return [
    { name: '告警率', color: '#2f6fed', data: stats.map((s) => Number(s.tamperRate.toFixed(4))) },
    { name: '平均异常分', color: '#0ea5a4', data: stats.map((s) => Number(s.avgScore.toFixed(4))) },
    { name: '峰值异常分', color: '#e5484d', data: stats.map((s) => Number(s.maxScore.toFixed(4))) }
  ]
})

const typeSeries = computed(() => {
  const list = compare.value?.typeBreakdown || []
  return [
    { name: '正常', color: '#cbd5e1', data: list.map((t) => t.normal) },
    { name: '遮挡', color: TAMPER_TYPE_COLOR.covered, data: list.map((t) => t.covered) },
    { name: '失焦', color: TAMPER_TYPE_COLOR.defocused, data: list.map((t) => t.defocused) },
    { name: '移位', color: TAMPER_TYPE_COLOR.moved, data: list.map((t) => t.moved) }
  ]
})

function labelOf(name: string) {
  return labelMap.value[name] || name
}

function shortOf(name: string) {
  return algoShort(name, labelMap.value[name])
}

async function load(id: string) {
  if (!id) {
    compare.value = null
    return
  }
  loading.value = true
  try {
    const [compareData, task, frameData] = await Promise.all([
      detectionApi.compare(id),
      detectionApi.get(id),
      detectionApi.frames(id, { page: 0, size: 2000 })
    ])
    compare.value = compareData
    taskInfo.value = task
    frames.value = frameData.content
    store.setTask(id)
  } catch {
    compare.value = null
  } finally {
    loading.value = false
  }
}

onMounted(async () => {
  await store.loadAlgorithms()
  if (taskId.value) await load(taskId.value)
})
</script>

<style scoped>
.picker-row {
  display: flex;
  align-items: flex-end;
  gap: 16px;
  flex-wrap: wrap;
}

.picker-main {
  flex: 0 1 360px;
  min-width: 280px;
}

.picker-meta {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 12px;
  color: #475467;
  margin-bottom: 4px;
}

.field-label {
  display: block;
  font-size: 12px;
  color: var(--ctd-text-secondary);
  margin-bottom: 6px;
}

.stats-row {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(170px, 1fr));
  gap: 12px;
  margin: 16px 0;
}

.charts-row {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 16px;
}

.charts-row .ctd-card + .ctd-card {
  margin-top: 0;
}

.algo-dot {
  display: inline-block;
  width: 8px;
  height: 8px;
  border-radius: 50%;
  margin-right: 6px;
  vertical-align: middle;
}

.consistency-list {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(360px, 1fr));
  gap: 10px 32px;
}

.consistency-item {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 108px 48px;
  align-items: center;
  gap: 10px;
  font-size: 12px;
}

.pair-name {
  display: flex;
  align-items: center;
  gap: 3px;
  color: #475467;
  min-width: 0;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.pair-name .vs {
  color: #98a1b0;
  margin: 0 2px;
}

.consistency-item .frames {
  color: #98a1b0;
  text-align: right;
}

@media (max-width: 1000px) {
  .charts-row {
    grid-template-columns: 1fr;
  }

  .charts-row .ctd-card + .ctd-card {
    margin-top: 16px;
  }
}
</style>
