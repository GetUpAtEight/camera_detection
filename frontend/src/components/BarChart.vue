<template>
  <div ref="el" class="bar-chart" :style="{ height }"></div>
</template>

<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import * as echarts from 'echarts'

interface BarSeries {
  name: string
  color?: string
  data: number[]
}

const props = withDefaults(
  defineProps<{
    categories: string[]
    series: BarSeries[]
    height?: string
    stack?: boolean
    horizontal?: boolean
    yMax?: number
    percent?: boolean
    yName?: string
  }>(),
  { height: '280px', stack: false, horizontal: false, percent: false, yName: '' }
)

const el = ref<HTMLElement>()
let chart: echarts.ECharts | null = null
let observer: ResizeObserver | null = null

function render() {
  if (!chart) return
  const valueAxis = {
    type: 'value' as const,
    max: props.yMax,
    name: props.yName,
    nameTextStyle: { fontSize: 10, color: '#8a94a6' },
    axisLabel: {
      fontSize: 10,
      color: '#8a94a6',
      formatter: (value: number) => (props.percent ? `${Math.round(value * 100)}%` : String(value))
    },
    splitLine: { lineStyle: { color: '#eef1f6' } }
  }

  chart.setOption(
    {
      animationDuration: 300,
      grid: { left: 48, right: 16, top: props.series.length > 1 ? 34 : 16, bottom: props.horizontal ? 24 : 32 },
      legend: {
        show: props.series.length > 1,
        top: 0,
        right: 0,
        icon: 'roundRect',
        itemWidth: 9,
        itemHeight: 8,
        textStyle: { fontSize: 11, color: '#5b6577' }
      },
      tooltip: {
        trigger: 'axis',
        confine: true,
        axisPointer: { type: 'shadow' },
        backgroundColor: 'rgba(255,255,255,0.96)',
        borderColor: '#e5e8ef',
        textStyle: { color: '#1c2434', fontSize: 12 },
        valueFormatter: (value: any) =>
          props.percent ? `${(Number(value) * 100).toFixed(1)}%` : String(value)
      },
      xAxis: props.horizontal
        ? valueAxis
        : {
            type: 'category',
            data: props.categories,
            axisTick: { show: false },
            axisLine: { lineStyle: { color: '#e5e8ef' } },
            axisLabel: { fontSize: 10, color: '#5b6577', interval: 0, overflow: 'truncate', width: 84 }
          },
      yAxis: props.horizontal
        ? {
            type: 'category',
            data: props.categories,
            axisTick: { show: false },
            axisLine: { lineStyle: { color: '#e5e8ef' } },
            axisLabel: { fontSize: 10, color: '#5b6577', width: 110, overflow: 'truncate' }
          }
        : valueAxis,
      series: props.series.map((s) => ({
        name: s.name,
        type: 'bar',
        stack: props.stack ? 'total' : undefined,
        data: s.data,
        barMaxWidth: 26,
        itemStyle: {
          color: s.color,
          borderRadius: props.stack ? 0 : [4, 4, 0, 0]
        },
        emphasis: { focus: 'series' }
      }))
    },
    { notMerge: true }
  )
}

onMounted(() => {
  if (!el.value) return
  chart = echarts.init(el.value)
  render()
  observer = new ResizeObserver(() => chart?.resize())
  observer.observe(el.value)
})

onBeforeUnmount(() => {
  observer?.disconnect()
  chart?.dispose()
  chart = null
})

watch(() => [props.categories, props.series, props.percent, props.stack, props.horizontal], render, {
  deep: true
})
</script>

<style scoped>
.bar-chart {
  width: 100%;
}
</style>
