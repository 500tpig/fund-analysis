<script setup lang="ts">
interface Props {
  hoverable?: boolean
  dense?: boolean
  class?: string
}

withDefaults(defineProps<Props>(), {
  hoverable: false,
  dense: false,
  class: '',
})
</script>

<template>
  <div
    :class="[
      'relative border-t border-[var(--border-subtle)]',
      hoverable ? 'hover:bg-[var(--bg-surface-hover)]' : '',
      $props.class,
    ]"
  >
    <slot />
  </div>
</template>
