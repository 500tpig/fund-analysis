<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { ChevronDown } from '@lucide/vue'

import type { PerformanceBenchmarkOption } from '@/api/types'

interface Props {
  options: PerformanceBenchmarkOption[]
  modelValue: string
}

const props = defineProps<Props>()
const emit = defineEmits<{
  'update:modelValue': [code: string]
}>()

const open = ref(false)
const rootRef = ref<HTMLElement | null>(null)
const triggerRef = ref<HTMLButtonElement | null>(null)
const listRef = ref<HTMLElement | null>(null)
const activeIndex = ref(0)

const selected = computed(() => (
  props.options.find((item) => item.code === props.modelValue) ?? props.options[0]
))

function closeMenu(): void {
  open.value = false
}

function toggleMenu(): void {
  open.value = !open.value
}

function selectCode(code: string): void {
  if (code !== props.modelValue) emit('update:modelValue', code)
  closeMenu()
  triggerRef.value?.focus()
}

function moveActive(delta: number): void {
  const count = props.options.length
  if (!count) return
  activeIndex.value = (activeIndex.value + delta + count) % count
  const option = listRef.value?.querySelectorAll<HTMLElement>('[role="option"]')[activeIndex.value]
  option?.focus()
}

function onTriggerKeydown(event: KeyboardEvent): void {
  if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
    event.preventDefault()
    event.stopPropagation()
    if (!open.value) open.value = true
    else moveActive(event.key === 'ArrowDown' ? 1 : -1)
  }
}

function onRootKeydown(event: KeyboardEvent): void {
  if (event.key === 'Escape' && open.value) {
    event.preventDefault()
    event.stopPropagation()
    closeMenu()
    triggerRef.value?.focus()
    return
  }
  if (!open.value) return
  if (event.key === 'ArrowDown') {
    event.preventDefault()
    moveActive(1)
  } else if (event.key === 'ArrowUp') {
    event.preventDefault()
    moveActive(-1)
  } else if (event.key === 'Home') {
    event.preventDefault()
    activeIndex.value = 0
    listRef.value?.querySelectorAll<HTMLElement>('[role="option"]')[0]?.focus()
  } else if (event.key === 'End') {
    event.preventDefault()
    activeIndex.value = props.options.length - 1
    const options = listRef.value?.querySelectorAll<HTMLElement>('[role="option"]')
    options?.[activeIndex.value]?.focus()
  } else if (event.key === 'Enter' || event.key === ' ') {
    const current = props.options[activeIndex.value]
    if (current) {
      event.preventDefault()
      selectCode(current.code)
    }
  }
}

function onDocumentPointerDown(event: PointerEvent): void {
  const target = event.target
  if (!(target instanceof Node)) return
  if (rootRef.value?.contains(target)) return
  closeMenu()
}

watch(open, (value) => {
  if (!value) return
  activeIndex.value = Math.max(
    0,
    props.options.findIndex((item) => item.code === props.modelValue),
  )
  void nextTick(() => {
    const option = listRef.value?.querySelectorAll<HTMLElement>('[role="option"]')[activeIndex.value]
    option?.focus()
  })
})

onMounted(() => {
  document.addEventListener('pointerdown', onDocumentPointerDown)
})

onBeforeUnmount(() => {
  document.removeEventListener('pointerdown', onDocumentPointerDown)
})
</script>

<template>
  <div
    ref="rootRef"
    class="benchmark-select"
    @keydown="onRootKeydown"
  >
    <button
      ref="triggerRef"
      type="button"
      class="benchmark-select__trigger"
      aria-haspopup="listbox"
      :aria-expanded="open ? 'true' : 'false'"
      :aria-controls="open ? 'benchmark-select-menu' : undefined"
      :aria-label="`选择对比指标，当前${selected?.name ?? ''}`"
      @click="toggleMenu"
      @keydown="onTriggerKeydown"
    >
      <span>{{ selected?.name ?? '对比指标' }}</span>
      <ChevronDown
        class="benchmark-select__chevron"
        :class="open ? 'is-open' : ''"
        :stroke-width="2"
      />
    </button>

    <ul
      v-if="open"
      id="benchmark-select-menu"
      ref="listRef"
      class="benchmark-select__menu"
      role="listbox"
      :aria-activedescendant="options[activeIndex] ? `benchmark-${options[activeIndex].code}` : undefined"
    >
      <li
        v-for="(item, index) in options"
        :id="`benchmark-${item.code}`"
        :key="item.code"
        role="option"
        tabindex="-1"
        :aria-selected="item.code === modelValue ? 'true' : 'false'"
        :class="[
          'benchmark-select__option',
          item.code === modelValue ? 'is-selected' : '',
          index === activeIndex ? 'is-active' : '',
        ]"
        @click="selectCode(item.code)"
        @pointerenter="activeIndex = index"
      >
        {{ item.name }}
      </li>
    </ul>
  </div>
</template>

<style scoped>
.benchmark-select {
  position: relative;
  display: inline-flex;
}

.benchmark-select__trigger {
  display: inline-flex;
  align-items: center;
  gap: 2px;
  margin: 0;
  padding: 2px 4px 2px 2px;
  border: 0;
  border-radius: var(--radius-control);
  background: transparent;
  color: inherit;
  font: inherit;
  line-height: 1.2;
  cursor: pointer;
}

.benchmark-select__trigger:hover {
  color: var(--text-primary);
  background: var(--bg-surface-hover);
}

.benchmark-select__chevron {
  width: 13px;
  height: 13px;
  color: var(--text-muted);
  transition: transform var(--dur-micro) var(--ease-out);
}

.benchmark-select__chevron.is-open {
  transform: rotate(180deg);
}

.benchmark-select__menu {
  position: absolute;
  top: calc(100% + 8px);
  right: 0;
  z-index: 30;
  min-width: 132px;
  margin: 0;
  padding: 6px;
  list-style: none;
  border-radius: var(--radius-panel);
  background: var(--bg-elevated);
  box-shadow: var(--shadow-overlay);
}

.benchmark-select__option {
  display: flex;
  align-items: center;
  min-height: 36px;
  padding: 0 12px;
  border-radius: var(--radius-control);
  color: var(--text-secondary);
  font-size: 13px;
  cursor: pointer;
}

.benchmark-select__option.is-active {
  background: var(--bg-elevated-hover);
  color: var(--text-primary);
}

.benchmark-select__option.is-selected {
  color: var(--text-primary);
  font-weight: 500;
}

@media (max-width: 768px) {
  .benchmark-select__trigger {
    min-width: 44px;
    min-height: 44px;
  }

  .benchmark-select__option {
    min-height: 44px;
  }
}

@media (prefers-reduced-motion: reduce) {
  .benchmark-select__chevron {
    transition: none;
  }
}
</style>
