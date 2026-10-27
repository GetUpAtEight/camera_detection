<template>
  <div class="stat-card" :class="tone">
    <div class="label">{{ label }}</div>
    <div class="value">
      {{ value }}<span v-if="unit" class="unit">{{ unit }}</span>
    </div>
    <div v-if="hint" class="hint">{{ hint }}</div>
  </div>
</template>

<script setup lang="ts">
withDefaults(
  defineProps<{
    label: string
    value: string | number
    unit?: string
    hint?: string
    tone?: 'default' | 'danger' | 'warning' | 'success'
  }>(),
  { tone: 'default', unit: '', hint: '' }
)
</script>

<style scoped>
.stat-card {
  border: 1px solid var(--ctd-border);
  border-radius: 12px;
  padding: 14px 16px;
  background: #fff;
  min-width: 0;
}

.stat-card .label {
  font-size: 12px;
  color: var(--ctd-text-secondary);
}

.stat-card .value {
  font-size: 24px;
  font-weight: 650;
  margin-top: 6px;
  font-variant-numeric: tabular-nums;
  line-height: 1.1;
}

.stat-card .unit {
  font-size: 12px;
  font-weight: 400;
  color: var(--ctd-text-secondary);
  margin-left: 4px;
}

.stat-card .hint {
  font-size: 11px;
  color: #98a1b0;
  margin-top: 6px;
}

.stat-card.danger .value {
  color: var(--ctd-danger);
}

.stat-card.warning .value {
  color: var(--ctd-warning);
}

.stat-card.success .value {
  color: var(--ctd-success);
}
</style>
