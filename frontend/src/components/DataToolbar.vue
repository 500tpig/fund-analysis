<script setup lang="ts">
defineProps<{
  meta?: string
}>()
</script>

<template>
  <div class="data-toolbar">
    <div class="data-toolbar__primary flex min-w-0 flex-1 flex-wrap items-center gap-3">
      <div
        v-if="$slots.search"
        class="data-toolbar__search w-full max-w-[280px]"
      >
        <slot name="search" />
      </div>
      <slot name="filters" />
    </div>
    <div class="data-toolbar__meta flex shrink-0 items-center gap-3 text-[12px] leading-[18px] text-[var(--text-secondary)]">
      <slot name="meta">
        <span v-if="meta">{{ meta }}</span>
      </slot>
    </div>
  </div>
</template>
