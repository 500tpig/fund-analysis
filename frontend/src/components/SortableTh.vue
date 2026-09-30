<script setup lang="ts">
import { ArrowDown, ArrowUp } from '@lucide/vue'

withDefaults(
  defineProps<{
    label: string
    active?: boolean
    order?: 'asc' | 'desc' | null
    align?: 'left' | 'right'
  }>(),
  {
    active: false,
    order: null,
    align: 'left',
  },
)
</script>

<template>
  <div
    :class="[
      'inline-flex items-center gap-1.5',
      align === 'right' ? 'w-full justify-end' : '',
    ]"
  >
    <span>{{ label }}</span>
    <ArrowUp
      v-if="active && order === 'asc'"
      class="sort-icon is-active h-3 w-3"
      :stroke-width="1.75"
    />
    <ArrowDown
      v-else
      :class="['sort-icon h-3 w-3', active ? 'is-active' : '']"
      :stroke-width="1.75"
    />
  </div>
</template>
