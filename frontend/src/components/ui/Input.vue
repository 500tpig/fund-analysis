<script setup lang="ts">
defineOptions({ inheritAttrs: false })

interface Props {
  modelValue?: string | number | null
  type?: string
  placeholder?: string
  disabled?: boolean
  readonly?: boolean
  size?: 'sm' | 'md'
  class?: string
  id?: string
  autocomplete?: string
  ariaLabel?: string
}

withDefaults(defineProps<Props>(), {
  modelValue: '',
  type: 'text',
  placeholder: '',
  disabled: false,
  readonly: false,
  size: 'md',
  class: '',
  id: undefined,
  autocomplete: undefined,
  ariaLabel: undefined,
})

const emit = defineEmits<{
  'update:modelValue': [value: string]
  change: [e: Event]
  input: [e: Event]
}>()

function onInput(e: Event) {
  const target = e.target as HTMLInputElement
  emit('update:modelValue', target.value)
  emit('input', e)
}
</script>

<template>
  <div class="relative flex w-full items-center">
    <div
      v-if="$slots.prefix"
      class="pointer-events-none absolute left-3 flex items-center text-[var(--text-tertiary)]"
    >
      <slot name="prefix" />
    </div>
    <input
      :id="id"
      :type="type"
      :value="modelValue ?? ''"
      :placeholder="placeholder"
      :disabled="disabled"
      :readonly="readonly"
      :autocomplete="autocomplete"
      :aria-label="ariaLabel"
      v-bind="$attrs"
      :class="[
        'w-full rounded-(--radius-control) border border-[var(--border-default)] bg-[var(--bg-surface)] text-[var(--text-primary)] placeholder:text-[var(--text-tertiary)]',
        'hover:border-[var(--border-strong)]',
        size === 'sm' ? 'h-9 text-[13px] px-3' : 'h-9 text-[14px] px-3.5',
        $slots.prefix ? 'pl-9' : '',
        $slots.suffix ? 'pr-9' : '',
        disabled ? 'opacity-40 cursor-not-allowed' : '',
        $props.class,
      ]"
      @input="onInput"
      @change="$emit('change', $event)"
    >
    <div
      v-if="$slots.suffix"
      class="absolute right-3 flex items-center text-[var(--text-tertiary)]"
    >
      <slot name="suffix" />
    </div>
  </div>
</template>
