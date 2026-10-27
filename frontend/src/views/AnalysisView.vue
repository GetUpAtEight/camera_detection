<template>
  <div>
    <div class="ctd-page-head">
      <h2>结果分析</h2>
      <p>检测结论、异常事件时间线、统计报表与标注指标，可导出 CSV / JSON。</p>
      <div class="head-actions">
        <el-button :icon="VideoPlay" @click="router.push({ name: 'detect' })">重新检测</el-button>
        <el-button :icon="Download" :disabled="!analysis" @click="exportFile('csv')">导出 CSV</el-button>
        <el-button :icon="Download" :disabled="!analysis" @click="exportFile('json')">导出 JSON</el-button>
      </div>
    </div>

    <div class="ctd-card">
      <div class="picker-row">
        <div class="picker-main">
          <label class="field-label">分析任务</label>
          <TaskPicker v-model="taskId" @update:model-value="load" />
        </div>
        <div v-if="analysis?.media" class="picker-meta">
          <span>{{ analysis.media.originalName }}</span>
          <el-tag size="small" effect="plain">{{ analysis.media.mediaType === 'VIDEO' ? '视频' : '图片' }}</el-tag>
          <span class="ctd-mono">{{ analysis.media.width }}×{{ analysis.media.height }}</span>
          <span v-if="analysis.media.frameCount" class="ctd-mono">{{ analysis.media.frameCount }} 帧</span>
          <el-tag size="small" :type="analysis.status === 'DONE' ? 'success' : 'warning'" effect="plain">
            {{ STATUS_TEXT[analysis.status] }}
          </el-tag>
        </div>
      </div>
    </div>

    <template v-if="analysis">
      <div class="conclusion" :class="analysis.conclusion.verdict">
        <div class="conclusion-main">
          <div class="verdict-icon">
            <el-icon v-if="analysis.conclusion.verdict === 'tampered'"><WarningFilled /></el-icon>
            <el-icon v-else><CircleCheckFilled /></el-icon>
          </div>
          <div class="verdict-body">
            <div class="verdict-title">
              {{ analysis.conclusion.verdict === 'tampered' ? '检测到摄像头篡改行为' : '未发现摄像头篡改行为' }}
              <el-tag size="small" effect="dark" :color="TAMPER_TYPE_COLOR[analysis.conclusion.tamperType]" style="border: none; margin-left: 8px">
                {{ TAMPER_TYPE_TEXT[analysis.conclusion.tamperType] }}
              </el-tag>
            </div>
            <p class="verdict-text">{{ analysis.conclusion.text }}</p>
            <div class="verdict-chips">
              <span>采样帧 <b>{{ analysis.conclusion.totalFrames }}</b></span>
              <span>告警帧 <b>{{ analysis.conclusion.tamperedFrames }}</b></span>
              <span>告警占比 <b>{{ formatPercent(analysis.conclusion.tamperRate) }}</b></span>
              <span v-if="analysis.conclusion.firstAlarmMs != null">
                首次告警 <b class="ctd-mono">{{ formatClock(analysis.conclusion.firstAlarmMs) }}</b>
              </span>
            </div>
            <div v-if="analysis.conclusion.triggeredAlgorithms.length" class="verdict-algos">
              触发算法：
              <el-tag v-for="name in analysis.conclusion.triggeredAlgorithms" :key="name" size="small" effect="plain">
                {{ name }}
              </el-tag>
            </div>
          </div>
        </div>
        <div class="risk">
          <el-progress
            type="dashboard"
            :percentage="Math.round(analysis.conclusion.riskLevel * 100)"
            :width="118"
            :stroke-width="10"
            :color="riskColor"
          >
            <template #default="{ percentage }">
              <div class="risk-value">{{ percentage }}</div>
              <div class="risk-label">风险指数</div>
            </template>
          </el-progress>
        </div>
      </div>

      <div class="stats-row">
        <StatCard label="采样帧数" :value="analysis.frames" :hint="analysis.sampleStepMs ? `采样间隔约 ${analysis.sampleStepMs} ms` : '单帧图片'" />
        <StatCard label="篡改事件" :value="analysis.events.length" tone="warning" hint="连续告警帧合并为一段" />
        <StatCard label="告警帧" :value="analysis.conclusion.tamperedFrames" tone="danger" :hint="`占比 ${formatPercent(analysis.conclusion.tamperRate)}`" />
        <StatCard label="峰值异常分" :value="peak.toFixed(3)" :tone="peak >= 0.5 ? 'danger' : 'success'" hint="0.5 为判定阈值" />
        <StatCard label="参与算法" :value="analysis.algorithmStats.length" hint="含 YOLO 时仅作链路演示" />
      </div>

      <div class="ctd-card">
        <h3 class="ctd-card-title">
          <el-icon><Clock /></el-icon> 异常事件时间线
          <span class="sub">多算法共识判定：≥2 个算法同时告警才算一次篡改</span>
        </h3>
        <EventTimeline
          :events="analysis.events"
          :total-frames="analysis.frames"
          :total-ms="totalMs"
          :label-map="labelMap"
        />
      </div>

      <div class="analysis-row">
        <div class="ctd-card">
          <h3 class="ctd-card-title"><el-icon><Histogram /></el-icon> 异常分分布</h3>
          <BarChart :categories="binLabels" :series="binSeries" height="260px" y-name="次数" />
          <div class="ctd-muted dist-hint">分布越靠右，说明越多帧接近判定线；正常场景应集中在 0~0.3。</div>
        </div>
        <div class="ctd-card">
          <h3 class="ctd-card-title"><el-icon><DataAnalysis /></el-icon> 逐算法统计</h3>
          <el-table :data="analysis.algorithmStats" size="small" stripe max-height="300">
            <el-table-column label="算法" min-width="150">
              <template #default="{ row }">
                <span class="algo-dot" :style="{ background: algoColor(row.algorithm) }"></span>
                {{ row.displayName }}
              </template>
            </el-table-column>
            <el-table-column prop="tamperedFrames" label="告警帧" width="80" />
            <el-table-column label="告警率" width="86">
              <template #default="{ row }">{{ formatPercent(row.tamperRate) }}</template>
            </el-table-column>
            <el-table-column prop="events" label="事件" width="66" />
            <el-table-column label="峰值" width="76">
              <template #default="{ row }">
                <span class="ctd-mono">{{ row.maxScore?.toFixed(2) }}</span>
              </template>
            </el-table-column>
            <el-table-column label="单帧耗时" width="100">
              <template #default="{ row }">
                <span class="ctd-mono">{{ row.avgCostMs != null ? row.avgCostMs.toFixed(1) + 'ms' : '-' }}</span>
              </template>
            </el-table-column>
          </el-table>
        </div>
      </div>

      <div v-if="segments.length > 1" class="ctd-card">
        <h3 class="ctd-card-title">
          <el-icon><Scissor /></el-icon> 分段结果
          <span class="sub">共 {{ segments.length }} 段，每段独立参考帧；「检出」「类型」两列需要该媒体已导入标注</span>
        </h3>
        <el-table :data="segments" size="small" stripe max-height="300">
          <el-table-column label="#" width="52" prop="index" />
          <el-table-column label="分段" min-width="180" show-overflow-tooltip prop="label" />
          <el-table-column label="区间" width="168">
            <template #default="{ row }">
              <span class="ctd-mono">{{ formatClock(row.startMs) }} → {{ formatClock(row.endMs) }}</span>
            </template>
          </el-table-column>
          <el-table-column prop="frameCount" label="帧数" width="70" />
          <el-table-column label="告警" width="96">
            <template #default="{ row }">
              <span class="ctd-mono">{{ row.tamperedFrames }} / {{ row.frameCount }}</span>
            </template>
          </el-table-column>
          <el-table-column label="告警率" width="82">
            <template #default="{ row }">{{ formatPercent(row.alarmRate) }}</template>
          </el-table-column>
          <el-table-column label="峰值" width="72">
            <template #default="{ row }"><span class="ctd-mono">{{ row.peakScore?.toFixed(2) }}</span></template>
          </el-table-column>
          <el-table-column label="类型" width="96">
            <template #default="{ row }">
              <span :style="{ color: TAMPER_TYPE_COLOR[row.tamperType] }">{{ TAMPER_TYPE_TEXT[row.tamperType] }}</span>
            </template>
          </el-table-column>
          <el-table-column label="标注" width="96">
            <template #default="{ row }">
              <span :style="{ color: TAMPER_TYPE_COLOR[row.expectedType] || '#98a1b0' }">
                {{ row.expectedType ? TAMPER_TYPE_TEXT[row.expectedType] || row.expectedType : '-' }}
              </span>
            </template>
          </el-table-column>
          <el-table-column label="检出" width="84">
            <template #default="{ row }">
              <el-tag v-if="row.matched === null || row.matched === undefined" size="small" type="info" effect="plain">无标注</el-tag>
              <el-tag v-else-if="row.matched" size="small" type="success" effect="plain">命中</el-tag>
              <el-tag v-else size="small" type="danger" effect="plain">漏判</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="类型" width="84">
            <template #default="{ row }">
              <el-tag v-if="row.typeMatched === null || row.typeMatched === undefined" size="small" type="info" effect="plain">-</el-tag>
              <el-tag v-else-if="row.typeMatched" size="small" type="success" effect="plain">一致</el-tag>
              <el-tag v-else size="small" type="warning" effect="plain">不符</el-tag>
            </template>
          </el-table-column>
        </el-table>
      </div>

      <div v-if="evaluation" class="ctd-card">
        <h3 class="ctd-card-title">
          <el-icon><Aim /></el-icon> 真值对照（事件级 ±容差）
          <span class="sub">
            {{ evaluation.gtCount }} 段标注（{{ evaluation.source || '未知来源' }}）· 命中 {{ evaluation.ensemble.truePositive }}
            · 漏报 {{ evaluation.ensemble.falseNegative }} · 误报 {{ evaluation.ensemble.falsePositive }}
          </span>
          <div style="margin-left: auto; display: flex; align-items: center; gap: 8px">
            <span class="ctd-muted">容差</span>
            <el-radio-group v-model="tolMs" size="small" @change="reload">
              <el-radio-button v-for="t in tolOptions" :key="t" :value="t">{{ t / 1000 }}s</el-radio-button>
            </el-radio-group>
          </div>
        </h3>

        <div class="eval-kpis">
          <StatCard label="命中事件" :value="evaluation.ensemble.truePositive" tone="danger" :hint="`共 ${evaluation.gtCount} 段标注`" />
          <StatCard label="漏报" :value="evaluation.ensemble.falseNegative" tone="warning" hint="标注有、算法没报" />
          <StatCard label="误报" :value="evaluation.ensemble.falsePositive" tone="warning" hint="算法报了、标注没有" />
          <StatCard
            label="平均响应延迟"
            :value="`${(evaluation.ensemble.meanLatencyMs / 1000).toFixed(1)}s`"
            hint="告警起点 − 标注起点"
          />
        </div>

        <el-table :data="eventMetricRows" size="small" stripe>
          <el-table-column label="算法" min-width="150">
            <template #default="{ row }">
              <el-tag v-if="row.highlight" size="small" type="success" effect="dark" style="margin-right: 6px">集成</el-tag>
              <span v-else class="algo-dot" :style="{ background: algoColor(row.algorithm) }"></span>
              {{ row.label }}
            </template>
          </el-table-column>
          <el-table-column prop="truePositive" label="命中" width="72" />
          <el-table-column prop="falseNegative" label="漏报" width="72" />
          <el-table-column prop="falsePositive" label="误报" width="72" />
          <el-table-column label="Precision" width="96">
            <template #default="{ row }">{{ formatPercent(row.precision) }}</template>
          </el-table-column>
          <el-table-column label="Recall" width="96">
            <template #default="{ row }">{{ formatPercent(row.recall) }}</template>
          </el-table-column>
          <el-table-column label="F1" width="92">
            <template #default="{ row }">
              <b :style="{ color: row.f1 >= 0.8 ? 'var(--ctd-success)' : row.f1 >= 0.6 ? 'var(--ctd-warning)' : 'var(--ctd-danger)' }">
                {{ row.f1.toFixed(3) }}
              </b>
            </template>
          </el-table-column>
          <el-table-column label="延迟均值" width="96">
            <template #default="{ row }">
              <span class="ctd-mono">{{ (row.meanLatencyMs / 1000).toFixed(1) }}s</span>
            </template>
          </el-table-column>
        </el-table>

        <div class="pair-tools">
          <span class="ctd-muted">逐条对照</span>
          <el-select v-model="pairAlgorithm" size="small" style="width: 150px">
            <el-option value="ensemble" label="集成投票" />
            <el-option v-for="algo in analysis!.algorithms" :key="algo" :value="algo" :label="labelMap[algo] || algo" />
          </el-select>
          <el-radio-group v-model="pairFilter" size="small">
            <el-radio-button value="all">全部</el-radio-button>
            <el-radio-button value="tp">命中</el-radio-button>
            <el-radio-button value="fn">漏报</el-radio-button>
            <el-radio-button value="fp">误报</el-radio-button>
          </el-radio-group>
          <span class="ctd-muted">{{ pairRows.length }} 条</span>
        </div>

        <el-table :data="pairRows" size="small" max-height="360" stripe>
          <el-table-column label="结果" width="86">
            <template #default="{ row }">
              <el-tag size="small" :type="row.kind === 'tp' ? 'success' : row.kind === 'fn' ? 'warning' : 'danger'" effect="plain">
                {{ row.kind === 'tp' ? '命中' : row.kind === 'fn' ? '漏报' : '误报' }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="标注区间" width="176">
            <template #default="{ row }">
              <span v-if="row.gtStartMs != null" class="ctd-mono">{{ formatClock(row.gtStartMs) }} → {{ formatClock(row.gtEndMs) }}</span>
              <span v-else class="ctd-muted">—</span>
            </template>
          </el-table-column>
          <el-table-column label="标注类型" width="96">
            <template #default="{ row }">
              <span v-if="row.gtType" :style="{ color: TAMPER_TYPE_COLOR[row.gtType] }">
                {{ TAMPER_TYPE_TEXT[row.gtType] || row.gtType }}
              </span>
              <span v-else class="ctd-muted">—</span>
            </template>
          </el-table-column>
          <el-table-column label="告警区间" width="176">
            <template #default="{ row }">
              <span v-if="row.predStartMs != null" class="ctd-mono">{{ formatClock(row.predStartMs) }} → {{ formatClock(row.predEndMs) }}</span>
              <span v-else class="ctd-muted">—</span>
            </template>
          </el-table-column>
          <el-table-column label="告警类型" width="96">
            <template #default="{ row }">
              <span v-if="row.predType" :style="{ color: TAMPER_TYPE_COLOR[row.predType] }">
                {{ TAMPER_TYPE_TEXT[row.predType] || row.predType }}
              </span>
              <span v-else class="ctd-muted">—</span>
            </template>
          </el-table-column>
          <el-table-column label="延迟" width="88">
            <template #default="{ row }">
              <span class="ctd-mono">{{ row.latencyMs == null ? '—' : signedSeconds(row.latencyMs) }}</span>
            </template>
          </el-table-column>
          <el-table-column label="重叠" width="82">
            <template #default="{ row }">
              <span class="ctd-mono">{{ row.overlapMs == null ? '—' : (row.overlapMs / 1000).toFixed(1) + 's' }}</span>
            </template>
          </el-table-column>
          <el-table-column label="距最近标注" min-width="110">
            <template #default="{ row }">
              <span class="ctd-mono">{{ row.nearestGtMs == null ? '—' : (row.nearestGtMs / 1000).toFixed(1) + 's' }}</span>
            </template>
          </el-table-column>
        </el-table>
        <div class="ctd-muted eval-note">
          为什么给容差：标注多为 1~2 秒的瞬时事件，而算法要积累几帧才越阈值，天然带 1~3 秒延迟；
          按帧严格比对会把「晚 1 秒的正确告警」同时记成漏报和误报。逐帧口径见下方准确率评估。
        </div>
      </div>

      <div class="ctd-card">
        <h3 class="ctd-card-title">
          <el-icon><Aim /></el-icon> 准确率评估（可选）
          <span class="sub">上传标注 CSV（两列：frame_index,tampered），自动计算 TP/FP/TN/FN 与 P/R/F1</span>
          <div style="margin-left: auto">
            <el-upload :auto-upload="false" :show-file-list="false" accept=".csv,text/csv" :on-change="onGroundTruth">
              <el-button size="small" :icon="Upload">上传标注 CSV</el-button>
            </el-upload>
          </div>
        </h3>

        <template v-if="analysis.metrics">
          <div class="metrics-head">
            <el-tag type="success" effect="light">
              已标注 {{ analysis.metrics.labelledFrames }} 帧，其中篡改 {{ analysis.metrics.tamperedLabels }} 帧
            </el-tag>
            <span v-if="(analysis.metrics.unmatchedLabels || 0) > 0" class="metrics-note">
              另有 {{ analysis.metrics.unmatchedLabels }} 条标注不在采样帧上，已忽略
            </span>
          </div>
          <el-table :data="metricsRows" size="small" stripe>
            <el-table-column prop="algorithm" label="算法" min-width="180">
              <template #default="{ row }">
                <el-tag v-if="row.highlight" size="small" type="success" effect="dark" style="margin-right: 6px">集成</el-tag>
                {{ row.algorithm }}
              </template>
            </el-table-column>
            <el-table-column prop="truePositive" label="TP" width="70" />
            <el-table-column prop="falsePositive" label="FP" width="70" />
            <el-table-column prop="trueNegative" label="TN" width="70" />
            <el-table-column prop="falseNegative" label="FN" width="70" />
            <el-table-column label="Precision" width="100">
              <template #default="{ row }">{{ formatPercent(row.precision) }}</template>
            </el-table-column>
            <el-table-column label="Recall" width="100">
              <template #default="{ row }">{{ formatPercent(row.recall) }}</template>
            </el-table-column>
            <el-table-column label="F1" width="100">
              <template #default="{ row }">
                <b :style="{ color: row.f1 >= 0.8 ? 'var(--ctd-success)' : row.f1 >= 0.6 ? 'var(--ctd-warning)' : 'var(--ctd-danger)' }">
                  {{ row.f1.toFixed(3) }}
                </b>
              </template>
            </el-table-column>
            <el-table-column label="Accuracy" width="110">
              <template #default="{ row }">{{ formatPercent(row.accuracy) }}</template>
            </el-table-column>
          </el-table>
        </template>
        <div v-else class="ctd-muted gt-hint">
          尚未导入标注。示例 CSV：
          <pre class="ctd-mono sample">frame_index,tampered
0,0
5,0
10,1
15,1</pre>
          <span>视频场景下 frame_index 为采样后的原始帧号，可从导出的 CSV 中获取。</span>
        </div>
      </div>
    </template>

    <el-empty v-else-if="!loading" description="请选择一个已完成的检测任务" :image-size="90" />
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { Aim, CircleCheckFilled, Clock, DataAnalysis, Download, Histogram, Scissor, Upload, VideoPlay, WarningFilled } from '@element-plus/icons-vue'
import type { AnalysisData, TaskItem } from '@/types'
import { detectionApi } from '@/api'
import { useAppStore } from '@/store/app'
import { STATUS_TEXT, TAMPER_TYPE_COLOR, TAMPER_TYPE_TEXT, algoColor, formatClock, formatPercent } from '@/utils/format'
import TaskPicker from '@/components/TaskPicker.vue'
import StatCard from '@/components/StatCard.vue'
import EventTimeline from '@/components/EventTimeline.vue'
import BarChart from '@/components/BarChart.vue'

const route = useRoute()
const router = useRouter()
const store = useAppStore()

const taskId = ref<string>((route.query.taskId as string) || store.currentTaskId || '')
const analysis = ref<AnalysisData | null>(null)
const taskInfo = ref<TaskItem | null>(null)
const loading = ref(false)

/** 事件级匹配容差（毫秒），切换后重新拉取分析结果 */
const tolMs = ref(3000)
const tolOptions = [0, 1000, 2000, 3000, 5000, 8000]
const pairFilter = ref<'all' | 'tp' | 'fn' | 'fp'>('all')
const pairAlgorithm = ref('ensemble')

const evaluation = computed(() => analysis.value?.eventEvaluation || null)
const segments = computed(() => analysis.value?.segments || [])

const eventMetricRows = computed(() => {
  const ev = evaluation.value
  if (!ev) return []
  return [
    ...ev.perAlgorithm.map((m) => ({
      ...m,
      label: labelMap.value[m.algorithm] || m.algorithm,
      highlight: false
    })),
    { ...ev.ensemble, label: '集成判定（≥2 算法投票）', highlight: true }
  ]
})

const pairRows = computed(() => {
  const ev = evaluation.value
  if (!ev) return []
  const byAlgorithm = ev.pairsByAlgorithm || {}
  const list = byAlgorithm[pairAlgorithm.value] || (pairAlgorithm.value === 'ensemble' ? ev.pairs : [])
  return list.filter((p) => pairFilter.value === 'all' || p.kind === pairFilter.value)
})

function signedSeconds(ms?: number | null) {
  if (ms === null || ms === undefined) return '—'
  const value = ms / 1000
  return `${value > 0 ? '+' : ''}${value.toFixed(1)}s`
}

async function reload() {
  if (taskId.value) await load(taskId.value)
}

const labelMap = computed<Record<string, string>>(() => {
  const map: Record<string, string> = {}
  for (const algo of store.algorithms) map[algo.name] = algo.displayName
  for (const stat of analysis.value?.algorithmStats || []) map[stat.algorithm] = stat.displayName
  return map
})

const peak = computed(() =>
  (analysis.value?.algorithmStats || []).reduce((max, s) => Math.max(max, s.maxScore || 0), 0)
)

const totalMs = computed(() => {
  const media = analysis.value?.media
  if (!media) return 0
  if (media.durationMs) return media.durationMs
  return 0
})

const binLabels = computed(() =>
  (analysis.value?.distribution || []).map((bin) => `${bin.binStart.toFixed(1)}~${bin.binEnd.toFixed(1)}`)
)

const binSeries = computed(() => [
  {
    name: '次数（所有算法累计）',
    color: '#2f6fed',
    data: (analysis.value?.distribution || []).map((bin) => bin.count)
  }
])

const metricsRows = computed(() => {
  const metrics = analysis.value?.metrics
  if (!metrics) return []
  return [
    ...metrics.perAlgorithm.map((m) => ({
      ...m,
      algorithm: labelMap.value[m.algorithm] || m.algorithm,
      highlight: false
    })),
    { ...metrics.ensemble, algorithm: '集成判定（≥2 算法投票）', highlight: true }
  ]
})

function riskColor(percentage: number) {
  if (percentage >= 60) return '#e5484d'
  if (percentage >= 30) return '#d97706'
  return '#22a06b'
}

async function load(id: string) {
  if (!id) {
    analysis.value = null
    return
  }
  loading.value = true
  try {
    const [data, task] = await Promise.all([detectionApi.analysis(id, tolMs.value), detectionApi.get(id)])
    analysis.value = data
    taskInfo.value = task
    store.setTask(id)
  } catch {
    analysis.value = null
  } finally {
    loading.value = false
  }
}

function exportFile(format: 'csv' | 'json') {
  if (!taskId.value) return
  window.open(detectionApi.exportUrl(taskId.value, format), '_blank')
}

async function onGroundTruth(uploadFile: any) {
  const file = uploadFile.raw as File
  if (!file || !taskId.value) return
  try {
    const result = await detectionApi.uploadGroundTruth(taskId.value, file)
    ElMessage.success(`已导入 ${result.imported} 条标注${result.skipped ? `，跳过 ${result.skipped} 行` : ''}`)
    await load(taskId.value)
  } catch (e: any) {
    ElMessage.error(e?.message || '标注导入失败')
  }
}

onMounted(async () => {
  await store.loadAlgorithms()
  if (taskId.value) await load(taskId.value)
})
</script>

<style scoped>
.eval-kpis {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
  gap: 12px;
  margin-bottom: 14px;
}

.pair-tools {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
  margin: 16px 0 8px;
  padding-top: 12px;
  border-top: 1px dashed var(--ctd-border);
  font-size: 12px;
}

.eval-note {
  margin-top: 10px;
  line-height: 1.7;
  font-size: 12px;
}

.head-actions {
  margin-left: auto;
  display: flex;
  gap: 8px;
}

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
  flex-wrap: wrap;
}

.field-label {
  display: block;
  font-size: 12px;
  color: var(--ctd-text-secondary);
  margin-bottom: 6px;
}

.conclusion {
  display: flex;
  align-items: center;
  gap: 20px;
  border-radius: 14px;
  padding: 20px 24px;
  margin-top: 16px;
  border: 1px solid transparent;
}

.conclusion.tampered {
  background: linear-gradient(120deg, #fff4f4, #fffaf5);
  border-color: #ffd9d9;
}

.conclusion.normal {
  background: linear-gradient(120deg, #f3fbf7, #f7fbff);
  border-color: #d8f0e4;
}

.conclusion-main {
  display: flex;
  gap: 16px;
  flex: 1;
  min-width: 0;
}

.verdict-icon {
  width: 46px;
  height: 46px;
  border-radius: 12px;
  display: grid;
  place-items: center;
  font-size: 24px;
  color: #fff;
  flex: 0 0 46px;
}

.conclusion.tampered .verdict-icon {
  background: linear-gradient(135deg, #e5484d, #f97316);
}

.conclusion.normal .verdict-icon {
  background: linear-gradient(135deg, #22a06b, #0ea5a4);
}

.verdict-title {
  font-size: 18px;
  font-weight: 650;
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}

.verdict-text {
  margin: 8px 0;
  font-size: 13px;
  line-height: 1.7;
  color: #475467;
}

.verdict-chips {
  display: flex;
  gap: 18px;
  flex-wrap: wrap;
  font-size: 12px;
  color: var(--ctd-text-secondary);
}

.verdict-chips b {
  color: var(--ctd-text);
  font-weight: 600;
}

.verdict-algos {
  margin-top: 8px;
  font-size: 12px;
  color: var(--ctd-text-secondary);
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}

.risk-value {
  font-size: 26px;
  font-weight: 700;
  line-height: 1.1;
}

.risk-label {
  font-size: 11px;
  color: var(--ctd-text-secondary);
}

.stats-row {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
  gap: 12px;
  margin: 16px 0;
}

.analysis-row {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 16px;
}

.analysis-row .ctd-card + .ctd-card {
  margin-top: 0;
}

.dist-hint {
  margin-top: 8px;
}

.algo-dot {
  display: inline-block;
  width: 8px;
  height: 8px;
  border-radius: 50%;
  margin-right: 6px;
  vertical-align: middle;
}

.metrics-head {
  margin-bottom: 10px;
  display: flex;
  align-items: center;
  gap: 10px;
}

.metrics-note {
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.gt-hint {
  line-height: 1.8;
}

.gt-hint .sample {
  background: #f7f9fc;
  border: 1px solid var(--ctd-border);
  border-radius: 8px;
  padding: 10px 12px;
  margin: 8px 0;
  font-size: 11.5px;
  color: #475467;
}

@media (max-width: 1100px) {
  .analysis-row {
    grid-template-columns: 1fr;
  }

  .analysis-row .ctd-card + .ctd-card {
    margin-top: 16px;
  }

  .conclusion {
    flex-direction: column;
    align-items: flex-start;
  }
}
</style>
