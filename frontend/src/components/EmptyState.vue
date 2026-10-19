<script setup lang="ts">
defineProps<{
  title: string
  description?: string
}>()
</script>

<template>
  <div class="flex flex-col items-center justify-center gap-2 py-10 text-center">
    <p class="text-[14px] text-[var(--text-secondary)]">
      {{ title }}
    </p>
    <p
      v-if="description"
      class="max-w-sm text-[12px] leading-[18px] text-[var(--text-tertiary)]"
    >
      {{ description }}
    </p>
    <slot />
  </div>
</template>
