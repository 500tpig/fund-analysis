<script setup lang="ts">
import { computed } from 'vue'

interface Props {
  variant?: 'info' | 'warning' | 'error' | 'muted' | 'neutral' | 'rise' | 'fall' | 'primary' | 'outline'
  size?: 'sm' | 'md'
  dot?: boolean
}

const props = withDefaults(defineProps<Props>(), {
  variant: 'neutral',
  size: 'sm',
  dot: false,
})

const variantClasses = computed(() => {
  switch (props.variant) {
    case 'info':
      return 'status-badge status-badge--info'
    case 'warning':
      return 'status-badge status-badge--warning'
    case 'error':
      return 'status-badge status-badge--error'
    case 'muted':
      return 'status-badge status-badge--muted'
    case 'rise':
    case 'fall':
    case 'primary':
    case 'outline':
    case 'neutral':
    default:
      return 'status-badge status-badge--muted'
  }
})
</script>

<template>
  <span :class="[variantClasses, size === 'md' ? 'h-6 px-2.5 text-[13px]' : '']">
    <slot />
  </span>
</template>
