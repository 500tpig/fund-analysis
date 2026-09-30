<script setup lang="ts">
import { computed } from 'vue'

import type { DataFreshnessResponse, HoldingRow, HoldingsSummary } from '@/api/types'
import {
  resolveIntradayStatus,
  resolveOfficialStatus,
  resolveSummaryStatus,
  type StatusDescriptor,
  type StatusTone,
} from '@/shared/dataStatus'

const props = defineProps<{
  row?: HoldingRow | null
  summary?: HoldingsSummary | null
  rows?: HoldingRow[]
  freshness?: DataFreshnessResponse | null
  compact?: boolean
}>()

function toneClass(tone: StatusTone | undefined): string {
  if (tone === 'info') return 'is-info'
  if (tone === 'warning') return 'is-warning'
  if (tone === 'error') return 'is-error'
  if (tone === 'muted') return 'is-muted'
  return ''
}

const rowParts = computed<StatusDescriptor[]>(() => {
  if (!props.row) return []
  const intraday = resolveIntradayStatus(props.row)
  if (props.row.is_cash_asset) return [intraday]
  return [intraday, resolveOfficialStatus(props.row)]
})

const compactTitle = computed(() => {
  const labels = rowParts.value.map((part) => part.label)
  const details = rowParts.value.map((part) => part.detail).filter(Boolean)
  return [...labels, ...details].join(' · ')
})
</script>

<template>
  <div
    v-if="summary"
    class="data-status"
  >
    <div
      v-for="item in resolveSummaryStatus(summary, rows ?? [], freshness)"
      :key="item.key"
      class="data-status__item"
      :class="toneClass(item.tone)"
      :title="item.detail"
    >
      <span class="data-status__label">{{ item.label }}</span>
      <span class="data-status__value">{{ item.value }}</span>
    </div>
  </div>

  <div
    v-else-if="row && compact"
    class="status-stack"
    :title="compactTitle"
  >
    <span
      v-for="part in rowParts"
      :key="part.key"
      class="status-inline"
      :class="toneClass(part.tone)"
    >{{ part.compactLabel || part.label }}</span>
  </div>

  <p
    v-else-if="row"
    class="flex flex-wrap items-center gap-x-2 gap-y-1 text-[13px] text-[var(--text-secondary)]"
  >
    <span
      v-for="part in rowParts"
      :key="part.key"
      class="status-inline"
      :class="toneClass(part.tone)"
      :title="part.detail"
    >{{ part.label }}</span>
  </p>
</template>
