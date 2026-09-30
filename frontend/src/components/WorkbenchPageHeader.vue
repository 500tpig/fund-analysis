<script setup lang="ts">
defineProps<{
  title: string
  description?: string
}>()
</script>

<template>
  <header class="page-header shrink-0 md:flex-row md:items-start md:justify-between">
    <div class="page-header__heading min-w-0">
      <h1 class="page-title">
        {{ title }}
      </h1>
      <p
        v-if="description"
        class="page-description"
      >
        {{ description }}
      </p>
    </div>

    <div
      v-if="$slots.meta || $slots.actions"
      class="page-header__tools flex shrink-0 flex-wrap items-center justify-start gap-3 md:justify-end"
    >
      <slot name="meta" />
      <slot name="actions" />
    </div>
  </header>
</template>
