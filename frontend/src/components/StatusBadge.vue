<script setup lang="ts">
import type { StatusTone } from '@/shared/dataStatus'

withDefaults(
  defineProps<{
    tone?: StatusTone
    label: string
    title?: string
  }>(),
  {
    tone: 'muted',
    title: undefined,
  },
)
</script>

<template>
  <span
    class="status-badge"
    :class="`status-badge--${tone === 'neutral' ? 'muted' : tone}`"
    :title="title"
  >
    {{ label }}
  </span>
</template>
