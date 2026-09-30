<script setup lang="ts">
import { computed, ref } from 'vue'
import { Loader2 } from '@lucide/vue'

interface Props {
  variant?: 'primary' | 'secondary' | 'ghost' | 'outline' | 'danger'
  size?: 'sm' | 'md' | 'lg' | 'icon' | 'icon-sm'
  disabled?: boolean
  loading?: boolean
  type?: 'button' | 'submit' | 'reset'
}

const props = withDefaults(defineProps<Props>(), {
  variant: 'secondary',
  size: 'md',
  disabled: false,
  loading: false,
  type: 'button',
})

const emit = defineEmits<{
  click: [e: MouseEvent]
}>()

const shaking = ref(false)

const variantClasses = computed(() => {
  switch (props.variant) {
    case 'primary':
      return 'bg-[var(--action-primary-bg)] text-[var(--action-primary-text)] hover:bg-[#F4F1E8]'
    case 'ghost':
      return 'bg-transparent text-[var(--text-tertiary)] hover:text-[var(--text-primary)] hover:bg-[var(--bg-surface-hover)]'
    case 'outline':
      return 'bg-transparent text-[var(--text-secondary)] shadow-[inset_0_0_0_1px_var(--border-subtle)] hover:text-[var(--text-primary)] hover:bg-[var(--bg-surface-hover)]'
    case 'danger':
      return 'bg-[rgba(216,121,121,0.12)] text-[var(--status-error)] hover:bg-[rgba(216,121,121,0.2)]'
    case 'secondary':
    default:
      return 'bg-[var(--action-secondary-bg)] text-[var(--text-primary)] shadow-[inset_0_0_0_1px_var(--border-subtle)] hover:bg-[var(--bg-surface-hover)]'
  }
})

const sizeClasses = computed(() => {
  switch (props.size) {
    case 'sm':
      return 'h-9 px-3 text-[13px] rounded-[8px] gap-1.5'
    case 'lg':
      return 'h-10 px-4 text-[14px] rounded-[8px] gap-2'
    case 'icon':
      return 'relative h-9 w-9 p-0 rounded-[8px] justify-center after:absolute after:inset-[-6px] after:content-[\'\']'
    case 'icon-sm':
      return 'relative h-8 w-8 p-0 rounded-[8px] justify-center text-[12px] after:absolute after:inset-[-8px] after:content-[\'\']'
    case 'md':
    default:
      return 'h-10 px-3.5 text-[13px] rounded-[8px] gap-1.5'
  }
})

function handleClick(e: MouseEvent) {
  if (props.disabled || props.loading) {
    e.preventDefault()
    shaking.value = false
    requestAnimationFrame(() => {
      shaking.value = true
    })
    window.setTimeout(() => {
      shaking.value = false
    }, 360)
    return
  }
  emit('click', e)
}
</script>

<template>
  <button
    :type="type"
    :disabled="disabled || loading || undefined"
    :aria-disabled="disabled || loading ? 'true' : undefined"
    :class="[
      'ui-press inline-flex items-center justify-center font-medium select-none cursor-pointer',
      variantClasses,
      sizeClasses,
      disabled || loading ? 'opacity-40' : '',
      shaking ? 'is-shaking' : '',
    ]"
    @click="handleClick"
    @animationend="shaking = false"
  >
    <Loader2
      v-if="loading"
      class="h-3.5 w-3.5 animate-spin"
      :stroke-width="1.75"
    />
    <slot
      v-else
      name="icon"
    />
    <slot />
  </button>
</template>
