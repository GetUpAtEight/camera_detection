<template>
  <div ref="chartEl" class="score-chart" :style="{ height }"></div>
</template>

<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import * as echarts from 'echarts'
import { formatClock } from '@/utils/format'

interface Series {
  name: string
  label: string
  color?: string
  data: (number | null)[]
}

const props = withDefaults(
  defineProps<{
    series: Series[]
    times?: number[]
    frameIndexes?: number[]
    threshold?: number
    height?: string
    xMode?: 'time' | 'frame'
    yName?: string
  }>(),
  {
    times: () => [],
    frameIndexes: () => [],
    threshold: 0.5,
    height: '300px',
    xMode: 'time',
    yName: '异常分'
  }
)

const chartEl = ref<HTMLElement>()
let chart: echarts.ECharts | null = null
let observer: ResizeObserver | null = null

function categories(): string[] {
  if (props.xMode === 'frame') {
    return props.frameIndexes.map((f) => String(f))
  }
  return props.times.map((t) => formatClock(t))
}

function render() {
  if (!chart) return
  const cats = categories()
  const first = props.series[0]

  chart.setOption(
    {
      animationDuration: 320,
      grid: { left: 46, right: 16, top: 30, bottom: cats.length > 80 ? 52 : 30 },
      legend: {
        top: 0,
        right: 0,
        type: 'scroll',
        icon: 'roundRect',
        itemWidth: 10,
        itemHeight: 8,
        textStyle: { fontSize: 11, color: '#5b6577' }
      },
      tooltip: {
        trigger: 'axis',
        confine: true,
        backgroundColor: 'rgba(255,255,255,0.96)',
        borderColor: '#e5e8ef',
        textStyle: { color: '#1c2434', fontSize: 12 },
        axisPointer: { type: 'line', lineStyle: { color: '#c7d0e0', type: 'dashed' } }
      },
      xAxis: {
        type: 'category',
        data: cats,
        boundaryGap: false,
        axisLine: { lineStyle: { color: '#e5e8ef' } },
        axisTick: { show: false },
        axisLabel: { fontSize: 10, color: '#8a94a6', hideOverlap: true }
      },
      yAxis: {
        type: 'value',
        min: 0,
        max: 1,
        name: props.yName,
        nameTextStyle: { fontSize: 10, color: '#8a94a6' },
        splitLine: { lineStyle: { color: '#eef1f6' } },
        axisLabel: { fontSize: 10, color: '#8a94a6' }
      },
      dataZoom:
        cats.length > 80
          ? [
              { type: 'inside', throttle: 60 },
              { type: 'slider', height: 16, bottom: 6, borderColor: 'transparent', fillerColor: 'rgba(47,111,237,0.12)', handleSize: 14 }
            ]
          : undefined,
      series: props.series.map((s, index) => ({
        name: s.label,
        type: 'line',
        data: s.data,
        showSymbol: false,
        smooth: 0.2,
        connectNulls: false,
        sampling: 'lttb',
        lineStyle: { width: 1.6, color: s.color },
        itemStyle: { color: s.color },
        emphasis: { focus: 'series' },
        markLine:
          first && index === 0
            ? {
                silent: true,
                symbol: 'none',
                lineStyle: { color: '#e5484d', type: 'dashed', width: 1 },
                label: {
                  formatter: `判定线 ${props.threshold}`,
                  position: 'insideEndTop',
                  fontSize: 10,
                  color: '#e5484d'
                },
                data: [{ yAxis: props.threshold }]
              }
            : undefined
      }))
    },
    { notMerge: true }
  )
}

onMounted(() => {
  if (!chartEl.value) return
  chart = echarts.init(chartEl.value)
  render()
  observer = new ResizeObserver(() => chart?.resize())
  observer.observe(chartEl.value)
})

onBeforeUnmount(() => {
  observer?.disconnect()
  chart?.dispose()
  chart = null
})

watch(() => [props.series, props.times, props.frameIndexes, props.threshold, props.xMode], render, {
  deep: true
})
</script>

<style scoped>
.score-chart {
  width: 100%;
}
</style>
