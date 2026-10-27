<template>
  <div>
    <div class="ctd-page-head">
      <h2>运行检测</h2>
      <p>选择媒体与算法，提交后由 SpringBoot 异步编排、Python 算法层逐帧推理，前端实时查看进度与曲线。</p>
    </div>

    <div class="ctd-card">
      <div class="select-row">
        <div class="select-main">
          <label class="field-label">检测媒体</label>
          <el-select
            v-model="mediaId"
            filterable
            placeholder="选择已上传的图片或视频"
            style="width: 100%"
            @change="onMediaChange"
          >
            <el-option v-for="item in store.mediaList" :key="item.id" :value="item.id" :label="item.originalName">
              <div class="media-option">
                <el-tag size="small" :type="item.mediaType === 'VIDEO' ? 'warning' : 'success'" effect="plain">
                  {{ item.mediaType === 'VIDEO' ? '视频' : '图片' }}
                </el-tag>
                <span class="name">{{ item.originalName }}</span>
                <span class="ctd-mono meta">{{ item.width }}×{{ item.height }}</span>
              </div>
            </el-option>
          </el-select>

          <div v-if="currentMedia" class="media-meta">
            <el-tag size="small" effect="plain">{{ currentMedia.mediaType === 'VIDEO' ? '视频' : '图片' }}</el-tag>
            <span class="ctd-mono">{{ currentMedia.width }}×{{ currentMedia.height }}</span>
            <span v-if="currentMedia.mediaType === 'VIDEO'" class="ctd-mono">
              {{ formatDuration(currentMedia.durationMs) }} · {{ currentMedia.frameCount || '-' }} 帧 · {{ currentMedia.fps?.toFixed(1) || '-' }} fps
            </span>
            <span class="ctd-mono">{{ formatBytes(currentMedia.fileSize) }}</span>
            <el-tag v-if="currentMedia.mediaType === 'IMAGE' && currentMedia.hasReference" size="small" type="info" effect="plain">
              已带参考帧
            </el-tag>
            <el-tag v-if="currentMedia.mediaType === 'IMAGE' && !currentMedia.hasReference" size="small" type="warning" effect="plain">
              无参考帧 · 3 个算法将跳过
            </el-tag>
          </div>
        </div>

        <div class="select-task">
          <label class="field-label">历史任务</label>
          <TaskPicker v-model="historyTaskId" @update:model-value="loadTask" />
        </div>

        <el-button :icon="Plus" @click="router.push({ name: 'upload' })">上传新文件</el-button>
      </div>
    </div>

    <SegmentPicker
      v-if="isVideo && currentMedia"
      :media="currentMedia"
      :gt-events="gtEvents"
      :sample-fps="sampleFps"
      :algorithm-count="selected.length"
      @update:plan="segPlan = $event"
      @pick-gt="importGt"
      @clear-gt="clearGt"
    />

    <div class="ctd-card">
      <h3 class="ctd-card-title">
        <el-icon><SetUp /></el-icon> 检测算法
        <span class="sub">勾选后展开参数，参数改动会下发到算法层生效</span>
        <div style="margin-left: auto; display: flex; gap: 8px">
          <el-button size="small" text @click="selectAll">全选主力</el-button>
          <el-button size="small" text @click="selectAux">全选辅助</el-button>
          <el-button size="small" text @click="selected = []">清空</el-button>
        </div>
      </h3>

      <AlgorithmParams
        :algorithms="store.algorithms"
        :selected="selected"
        :params="params"
        @update:selected="selected = $event"
        @update:params="params = $event"
      />

      <div v-if="isVideo" class="settings-row">
        <div class="setting">
          <span class="field-label">采样帧率</span>
          <el-slider v-model="sampleFps" :min="1" :max="25" :step="1" show-input :show-input-controls="false" />
          <span class="ctd-muted">默认 5 fps，越高越精细但越慢</span>
        </div>
        <div class="setting">
          <span class="field-label">最多处理帧数</span>
          <el-slider v-model="maxFrames" :min="50" :max="3000" :step="50" show-input :show-input-controls="false" />
          <span class="ctd-muted">超出部分不再采样，避免长视频等待过久</span>
        </div>
      </div>

      <div v-else class="ctd-muted image-hint">
        图片检测为单帧检测。帧差 / ORB / 边缘消失率依赖参考帧，如需完整对比请在上传时附带一张正常画面作为参考帧。
      </div>

      <div class="run-bar">
        <el-button
          type="primary"
          size="large"
          :icon="VideoPlay"
          :loading="running"
          :disabled="!mediaId || !selected.length"
          @click="runDetection"
        >
          {{ running ? '检测中…' : '开始检测' }}
        </el-button>
        <el-button v-if="running" size="large" @click="cancel">取消任务</el-button>
        <div v-if="task" class="run-status">
          <el-tag :type="STATUS_TAG[task.status]" effect="light">{{ STATUS_TEXT[task.status] }}</el-tag>
          <span class="ctd-muted">{{ task.message }}</span>
        </div>
      </div>

      <el-progress
        v-if="task && (task.status === 'RUNNING' || task.status === 'PENDING')"
        :percentage="Math.round((task.progress || 0) * 100)"
        :stroke-width="10"
        striped
        striped-flow
        style="margin-top: 14px"
      />
      <el-alert
        v-if="task?.status === 'FAILED'"
        type="error"
        show-icon
        :closable="false"
        style="margin-top: 14px"
        title="检测失败"
        :description="task.errorMessage || '请检查算法服务日志'"
      />
    </div>

    <template v-if="task && task.status === 'DONE'">
      <div class="stats-row">
        <StatCard label="采样帧数" :value="analysis?.frames ?? uniqueFrameCount" hint="按采样帧率抽取的帧数" />
        <StatCard
          label="告警帧"
          :value="analysis?.conclusion.tamperedFrames ?? 0"
          tone="danger"
          :hint="`占比 ${formatPercent(analysis?.conclusion.tamperRate)}`"
        />
        <StatCard label="篡改事件" :value="analysis?.events.length ?? 0" tone="warning" hint="连续告警帧合并" />
        <StatCard
          label="峰值异常分"
          :value="peakScore.toFixed(2)"
          :tone="peakScore >= 0.5 ? 'danger' : 'success'"
          hint="0.5 为判定阈值"
        />
        <StatCard label="总耗时" :value="formatDuration(task.elapsedMs)" hint="含算法层推理与落库" />
      </div>

      <div v-if="showSegments" class="ctd-card">
        <h3 class="ctd-card-title">
          <el-icon><Scissor /></el-icon> 分段检测结果
          <span class="sub">每段独立参考帧，按段给出告警情况并与标注对照</span>
          <div style="margin-left: auto">
            <el-tag size="small" effect="plain">共 {{ analysis!.segments!.length }} 段</el-tag>
          </div>
        </h3>
        <el-table :data="analysis!.segments" size="small" stripe>
          <el-table-column label="#" width="52" prop="index" />
          <el-table-column label="分段" min-width="200" show-overflow-tooltip prop="label" />
          <el-table-column label="区间" width="170">
            <template #default="{ row }">
              <span class="ctd-mono">{{ formatClock(row.startMs) }} → {{ formatClock(row.endMs) }}</span>
            </template>
          </el-table-column>
          <el-table-column label="帧数" width="72" prop="frameCount" />
          <el-table-column label="告警帧" width="80">
            <template #default="{ row }">
              <span :style="{ color: row.tamperedFrames ? 'var(--ctd-danger)' : 'inherit' }">{{ row.tamperedFrames }}</span>
            </template>
          </el-table-column>
          <el-table-column label="告警率" width="82">
            <template #default="{ row }">{{ formatPercent(row.alarmRate) }}</template>
          </el-table-column>
          <el-table-column label="峰值" width="72">
            <template #default="{ row }">
              <span class="ctd-mono">{{ row.peakScore?.toFixed(2) }}</span>
            </template>
          </el-table-column>
          <el-table-column label="实测类型" width="100">
            <template #default="{ row }">
              <span :style="{ color: TAMPER_TYPE_COLOR[row.tamperType] }">{{ TAMPER_TYPE_TEXT[row.tamperType] }}</span>
            </template>
          </el-table-column>
          <el-table-column label="标注类型" width="100">
            <template #default="{ row }">
              <span :style="{ color: TAMPER_TYPE_COLOR[row.expectedType] || '#98a1b0' }">
                {{ row.expectedType ? TAMPER_TYPE_TEXT[row.expectedType] || row.expectedType : '-' }}
              </span>
            </template>
          </el-table-column>
          <el-table-column label="检出" width="86">
            <template #default="{ row }">
              <el-tag v-if="row.matched === null || row.matched === undefined" size="small" type="info" effect="plain">无标注</el-tag>
              <el-tag v-else-if="row.matched" size="small" type="success" effect="plain">命中</el-tag>
              <el-tag v-else size="small" type="danger" effect="plain">漏判</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="类型" width="86">
            <template #default="{ row }">
              <el-tag v-if="row.typeMatched === null || row.typeMatched === undefined" size="small" type="info" effect="plain">-</el-tag>
              <el-tag v-else-if="row.typeMatched" size="small" type="success" effect="plain">一致</el-tag>
              <el-tag v-else size="small" type="warning" effect="plain">不符</el-tag>
            </template>
          </el-table-column>
        </el-table>
      </div>

      <div class="ctd-card">
        <h3 class="ctd-card-title">
          <el-icon><TrendCharts /></el-icon> 检测分数曲线
          <span class="sub">每条曲线对应一个算法，红线为 0.5 判定阈值</span>
          <div style="margin-left: auto; display: flex; gap: 8px">
            <el-button size="small" :icon="DataAnalysis" @click="router.push({ name: 'compare', query: { taskId: task!.id } })">
              算法对比
            </el-button>
            <el-button size="small" type="primary" :icon="TrendCharts" @click="router.push({ name: 'analysis', query: { taskId: task!.id } })">
              结果分析
            </el-button>
          </div>
        </h3>
        <ScoreChart
          :series="chartSeries"
          :times="chartTimes"
          :frame-indexes="frameIndexList"
          :x-mode="isVideo ? 'time' : 'frame'"
          height="320px"
        />
      </div>

      <div class="ctd-card">
        <h3 class="ctd-card-title">
          <el-icon><PictureRounded /></el-icon> 中间过程可视化
          <span class="sub">边缘图、ORB 匹配、帧差热力图、分块熵、YOLO 检测框</span>
        </h3>
        <VisualViewer :task-id="task.id" :results="frames" :label-map="labelMap" />
      </div>

      <div class="ctd-card">
        <h3 class="ctd-card-title">
          <el-icon><List /></el-icon> 逐帧结果
          <span class="sub">共 {{ frames.length }} 条记录</span>
          <div style="margin-left: auto; display: flex; gap: 10px; align-items: center">
            <el-select v-model="filterAlgorithm" size="small" style="width: 180px" clearable placeholder="全部算法">
              <el-option v-for="algo in algorithmsInResult" :key="algo" :value="algo" :label="labelMap[algo] || algo" />
            </el-select>
            <el-switch v-model="onlyTampered" size="small" active-text="只看告警帧" />
          </div>
        </h3>

        <el-table :data="pagedFrames" size="small" stripe max-height="460">
          <el-table-column prop="frameIndex" label="帧号" width="80" />
          <el-table-column label="时间" width="110">
            <template #default="{ row }">
              <span class="ctd-mono">{{ formatClock(row.timestampMs) }}</span>
            </template>
          </el-table-column>
          <el-table-column label="算法" width="180">
            <template #default="{ row }">
              <span class="algo-dot" :style="{ background: algoColor(row.algorithm) }"></span>
              {{ labelMap[row.algorithm] || row.algorithm }}
            </template>
          </el-table-column>
          <el-table-column label="异常分" width="90">
            <template #default="{ row }">
              <span class="ctd-mono" :style="{ color: row.score >= 0.5 ? 'var(--ctd-danger)' : 'inherit' }">
                {{ row.score?.toFixed(3) }}
              </span>
            </template>
          </el-table-column>
          <el-table-column label="判定" width="90">
            <template #default="{ row }">
              <el-tag size="small" :type="row.tampered ? 'danger' : 'success'" effect="plain">
                {{ row.tampered ? '异常' : '正常' }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="类型" width="100">
            <template #default="{ row }">
              <span :style="{ color: TAMPER_TYPE_COLOR[row.tamperType] }">{{ TAMPER_TYPE_TEXT[row.tamperType] }}</span>
            </template>
          </el-table-column>
          <el-table-column label="说明" min-width="260" show-overflow-tooltip>
            <template #default="{ row }">
              <span :class="{ 'ctd-muted': row.skipped }">{{ row.note || '-' }}</span>
            </template>
          </el-table-column>
        </el-table>

        <el-pagination
          v-model:current-page="page"
          :page-size="pageSize"
          :total="filteredFrames.length"
          layout="total, prev, pager, next"
          style="margin-top: 12px; justify-content: flex-end"
        />
      </div>
    </template>

    <el-empty v-else-if="!task" description="选择一个媒体并点击「开始检测」，或从历史任务中加载结果" :image-size="90" />
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import {
  DataAnalysis,
  List,
  PictureRounded,
  Plus,
  Scissor,
  SetUp,
  TrendCharts,
  VideoPlay
} from '@element-plus/icons-vue'
import type {
  AnalysisData,
  FrameResultItem,
  GroundTruthEventItem,
  SegmentPlan,
  TaskItem
} from '@/types'
import { detectionApi, mediaApi } from '@/api'
import { useAppStore } from '@/store/app'
import { ALGO_COLORS, STATUS_TAG, STATUS_TEXT, TAMPER_TYPE_COLOR, TAMPER_TYPE_TEXT, algoColor, algoShort, formatBytes, formatClock, formatDuration, formatPercent } from '@/utils/format'
import AlgorithmParams from '@/components/AlgorithmParams.vue'
import ScoreChart from '@/components/ScoreChart.vue'
import VisualViewer from '@/components/VisualViewer.vue'
import StatCard from '@/components/StatCard.vue'
import TaskPicker from '@/components/TaskPicker.vue'
import SegmentPicker from '@/components/SegmentPicker.vue'

const store = useAppStore()
const router = useRouter()
const route = useRoute()

const mediaId = ref(store.currentMediaId)
const historyTaskId = ref('')
const selected = ref<string[]>([])
const params = ref<Record<string, Record<string, number>>>({})
const sampleFps = ref(5)
const maxFrames = ref(600)
const gtEvents = ref<GroundTruthEventItem[]>([])
const segPlan = ref<SegmentPlan>({ mode: 'full', startMs: 0, endMs: 0, segments: [] })

const task = ref<TaskItem | null>(null)
const frames = ref<FrameResultItem[]>([])
const analysis = ref<AnalysisData | null>(null)
const running = ref(false)
const filterAlgorithm = ref('')
const onlyTampered = ref(false)
const page = ref(1)
const pageSize = 50

let timer: number | undefined

const currentMedia = computed(() => store.mediaList.find((m) => m.id === mediaId.value) || null)
const isVideo = computed(() => currentMedia.value?.mediaType === 'VIDEO')

const labelMap = computed<Record<string, string>>(() => {
  const map: Record<string, string> = {}
  for (const algo of store.algorithms) map[algo.name] = algo.displayName
  return map
})

const algorithmsInResult = computed(() => Array.from(new Set(frames.value.map((f) => f.algorithm))))
const uniqueFrameCount = computed(() => new Set(frames.value.map((f) => f.frameIndex)).size)

const frameIndexList = computed(() =>
  Array.from(new Set(frames.value.map((f) => f.frameIndex))).sort((a, b) => a - b)
)

const chartTimes = computed(() => {
  const timeMap = new Map<number, number>()
  for (const item of frames.value) timeMap.set(item.frameIndex, item.timestampMs)
  return frameIndexList.value.map((fi) => timeMap.get(fi) || 0)
})

const chartSeries = computed(() => {
  const indexes = frameIndexList.value
  return algorithmsInResult.value.map((algorithm) => {
    const scoreMap = new Map<number, number>()
    for (const item of frames.value) {
      if (item.algorithm === algorithm) scoreMap.set(item.frameIndex, item.score)
    }
    return {
      name: algorithm,
      label: algoShort(algorithm, labelMap.value[algorithm]),
      color: ALGO_COLORS[algorithm] || '#2f6fed',
      data: indexes.map((fi) => (scoreMap.has(fi) ? scoreMap.get(fi)! : null))
    }
  })
})

const peakScore = computed(() => frames.value.reduce((max, item) => Math.max(max, item.score || 0), 0))

/** 只有真正分段（多段或手动区间）时才展示分段结果表 */
const showSegments = computed(() => {
  const list = analysis.value?.segments || []
  if (list.length > 1) return true
  return list.length === 1 && !!(task.value?.segmentCount || task.value?.startMs)
})

const filteredFrames = computed(() => {
  return frames.value.filter((item) => {
    if (onlyTampered.value && !item.tampered) return false
    if (filterAlgorithm.value && item.algorithm !== filterAlgorithm.value) return false
    return true
  })
})

const pagedFrames = computed(() => {
  const start = (page.value - 1) * pageSize
  return filteredFrames.value.slice(start, start + pageSize)
})

watch([filterAlgorithm, onlyTampered], () => {
  page.value = 1
})

function defaultsFor(algoName: string): Record<string, number> {
  const meta = store.algorithms.find((a) => a.name === algoName)
  const result: Record<string, number> = {}
  meta?.params.forEach((p) => (result[p.key] = p.default))
  return result
}

function initParams() {
  if (!store.algorithms.length) return
  const next: Record<string, Record<string, number>> = { ...params.value }
  for (const algo of store.algorithms) {
    if (!next[algo.name]) next[algo.name] = defaultsFor(algo.name)
  }
  params.value = next
  if (!selected.value.length) selectAll()
}

function selectAll() {
  selected.value = store.classicAlgorithms.map((a) => a.name)
}

/** 辅助算法按场景手动追加，不覆盖已选的主力算法 */
function selectAux() {
  const names = store.auxAlgorithms.map((a) => a.name)
  selected.value = Array.from(new Set([...selected.value, ...names]))
  initParams()
}

function onMediaChange(id: string) {
  store.setMedia(id)
  frames.value = []
  analysis.value = null
  task.value = null
  historyTaskId.value = ''
  segPlan.value = { mode: 'full', startMs: 0, endMs: 0, segments: [] }
  loadGroundTruth(id)
}

async function loadGroundTruth(id: string) {
  gtEvents.value = []
  if (!id) return
  const media = store.mediaList.find((m) => m.id === id)
  if (media && media.mediaType !== 'VIDEO') return
  try {
    const data = await mediaApi.groundTruth(id)
    gtEvents.value = data.events || []
  } catch {
    gtEvents.value = []
  }
}

async function importGt(file: File) {
  if (!mediaId.value) {
    ElMessage.warning('请先选择媒体')
    return
  }
  try {
    const result = await mediaApi.importGroundTruth(mediaId.value, file)
    ElMessage.success(`已导入 ${result.count} 段标注（${result.source}）`)
    await loadGroundTruth(mediaId.value)
  } catch {
    /* 错误已由拦截器提示 */
  }
}

async function clearGt() {
  if (!mediaId.value) return
  await mediaApi.clearGroundTruth(mediaId.value)
  gtEvents.value = []
  ElMessage.info('已清除标注')
}

async function runDetection() {
  if (!mediaId.value) {
    ElMessage.warning('请先选择媒体')
    return
  }
  if (!selected.value.length) {
    ElMessage.warning('请至少选择一个算法')
    return
  }
  running.value = true
  frames.value = []
  analysis.value = null
  try {
    const plan = segPlan.value
    const payload: Parameters<typeof detectionApi.run>[0] = {
      mediaId: mediaId.value,
      algorithms: selected.value,
      params: params.value,
      sampleFps: isVideo.value ? sampleFps.value : undefined,
      maxFrames: isVideo.value ? maxFrames.value : undefined
    }
    if (isVideo.value) {
      if (plan.mode === 'range' && plan.endMs > plan.startMs) {
        payload.startMs = plan.startMs
        payload.endMs = plan.endMs
      } else if (plan.mode === 'segments' && plan.segments.length) {
        payload.segments = plan.segments
      }
    }
    const created = await detectionApi.run(payload)
    task.value = created
    historyTaskId.value = created.id
    store.setTask(created.id)
    startPolling(created.id)
  } catch (e: any) {
    running.value = false
    ElMessage.error(e?.message || '提交失败')
  }
}

function startPolling(taskId: string) {
  stopPolling()
  timer = window.setInterval(async () => {
    try {
      const latest = await detectionApi.get(taskId)
      task.value = latest
      if (latest.status === 'DONE') {
        stopPolling()
        running.value = false
        ElMessage.success('检测完成')
        await loadResults(taskId)
      } else if (latest.status === 'FAILED' || latest.status === 'CANCELLED') {
        stopPolling()
        running.value = false
      }
    } catch {
      stopPolling()
      running.value = false
    }
  }, 1200)
}

function stopPolling() {
  if (timer) {
    window.clearInterval(timer)
    timer = undefined
  }
}

async function cancel() {
  if (!task.value) return
  await detectionApi.cancel(task.value.id)
  ElMessage.info('已发送取消请求')
}

async function loadResults(taskId: string) {
  const all: FrameResultItem[] = []
  let currentPage = 0
  let totalPages = 1
  do {
    const pageData = await detectionApi.frames(taskId, { page: currentPage, size: 2000 })
    all.push(...pageData.content)
    totalPages = pageData.totalPages
    currentPage++
  } while (currentPage < totalPages)

  frames.value = all
  try {
    analysis.value = await detectionApi.analysis(taskId)
  } catch {
    analysis.value = null
  }
  page.value = 1
}

async function loadTask(taskId: string) {
  if (!taskId) return
  stopPolling()
  frames.value = []
  analysis.value = null
  const latest = await detectionApi.get(taskId)
  task.value = latest
  store.setTask(latest.id)
  mediaId.value = latest.mediaId
  store.setMedia(latest.mediaId)
  historyTaskId.value = latest.id
  if (latest.status === 'DONE') {
    await loadResults(latest.id)
  } else if (latest.status === 'RUNNING' || latest.status === 'PENDING') {
    running.value = true
    startPolling(latest.id)
  }
}

onMounted(async () => {
  await Promise.all([store.loadAlgorithms(), store.loadMedia()])
  initParams()

  const queryMedia = (route.query.mediaId as string) || ''
  const queryTask = (route.query.taskId as string) || ''
  if (queryMedia) {
    mediaId.value = queryMedia
    store.setMedia(queryMedia)
  }
  if (mediaId.value) await loadGroundTruth(mediaId.value)
  const taskToLoad = queryTask || (queryMedia ? '' : store.currentTaskId)
  if (taskToLoad) {
    historyTaskId.value = taskToLoad
    await loadTask(taskToLoad)
  }
})

watch(
  () => store.algorithms,
  () => initParams(),
  { deep: false }
)

onBeforeUnmount(stopPolling)
</script>

<style scoped>
.select-row {
  display: flex;
  align-items: flex-end;
  gap: 16px;
  flex-wrap: wrap;
}

.select-main {
  flex: 1 1 380px;
  min-width: 300px;
}

.select-task {
  flex: 0 1 320px;
  min-width: 260px;
}

.field-label {
  display: block;
  font-size: 12px;
  color: var(--ctd-text-secondary);
  margin-bottom: 6px;
}

.media-meta {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
  margin-top: 10px;
  font-size: 12px;
  color: #475467;
}

.media-option {
  display: flex;
  align-items: center;
  gap: 8px;
}

.media-option .name {
  max-width: 260px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.media-option .meta {
  margin-left: auto;
  color: #98a1b0;
}

.settings-row {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 24px;
  margin-top: 16px;
  padding-top: 16px;
  border-top: 1px dashed var(--ctd-border);
}

.setting {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.image-hint {
  margin-top: 12px;
  line-height: 1.7;
}

.run-bar {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-top: 18px;
  flex-wrap: wrap;
}

.run-status {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
}

.stats-row {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
  gap: 12px;
  margin: 16px 0;
}

.algo-dot {
  display: inline-block;
  width: 8px;
  height: 8px;
  border-radius: 50%;
  margin-right: 6px;
  vertical-align: middle;
}

@media (max-width: 1000px) {
  .settings-row {
    grid-template-columns: 1fr;
  }
}
</style>
