<template>
  <div class="task-picker">
    <el-select
      :model-value="modelValue"
      filterable
      clearable
      placeholder="选择一个检测任务"
      style="width: 100%"
      @update:model-value="onChange"
    >
      <el-option v-for="task in tasks" :key="task.id" :value="task.id" :label="`${task.mediaName} · ${formatTime(task.createdAt)}`">
        <div class="option">
          <span class="name">{{ task.mediaName }}</span>
          <el-tag size="small" :type="STATUS_TAG[task.status]" effect="plain">{{ STATUS_TEXT[task.status] }}</el-tag>
          <span class="time ctd-mono">{{ formatTime(task.createdAt) }}</span>
        </div>
      </el-option>
    </el-select>
    <el-button :icon="Refresh" circle text @click="load" />
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { Refresh } from '@element-plus/icons-vue'
import type { TaskItem } from '@/types'
import { detectionApi } from '@/api'
import { STATUS_TAG, STATUS_TEXT, formatTime } from '@/utils/format'

const props = defineProps<{ modelValue: string }>()
const emit = defineEmits<{ (e: 'update:modelValue', value: string): void; (e: 'loaded', tasks: TaskItem[]): void }>()

const tasks = ref<TaskItem[]>([])

async function load() {
  try {
    tasks.value = await detectionApi.list()
    emit('loaded', tasks.value)
  } catch {
    tasks.value = []
  }
}

function onChange(value: string) {
  emit('update:modelValue', value)
}

onMounted(load)

defineExpose({ load, tasks })
</script>

<style scoped>
.task-picker {
  display: flex;
  align-items: center;
  gap: 6px;
  width: 100%;
}

.option {
  display: flex;
  align-items: center;
  gap: 8px;
}

.option .name {
  max-width: 220px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.option .time {
  margin-left: auto;
  color: #98a1b0;
  font-size: 11px;
}
</style>
