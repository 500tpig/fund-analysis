<script setup lang="ts">
import { nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { X as CloseIcon } from '@lucide/vue'

import { useDrawerHistory, type DrawerHandle } from '@/shared/drawerHistory'

interface Props {
  show: boolean
  title?: string
  width?: string | number
}

const props = withDefaults(defineProps<Props>(), {
  show: false,
  title: '',
  width: 'clamp(620px, 42vw, 760px)',
})

const emit = defineEmits<{
  'update:show': [val: boolean]
  close: []
}>()

const panelRef = ref<HTMLElement | null>(null)
const closeRef = ref<HTMLButtonElement | null>(null)
const lastFocus = ref<HTMLElement | null>(null)
let previousBodyOverflow: string | null = null

// 返回栈协调见 shared/drawerHistory.ts：所有抽屉共用一条记录，返回手势关闭最上层抽屉。
const drawerHistory = useDrawerHistory()
let historyHandle: DrawerHandle | null = null

function close() {
  emit('update:show', false)
  emit('close')
}

function focusables(): HTMLElement[] {
  if (!panelRef.value) return []
  return [...panelRef.value.querySelectorAll<HTMLElement>(
    'a[href], button:not([disabled]), textarea, input, select, [tabindex]:not([tabindex="-1"])',
  )].filter((node) => !node.hasAttribute('disabled') && node.getAttribute('aria-disabled') !== 'true')
}

function onPanelKeydown(event: KeyboardEvent) {
  if (event.key === 'Escape') {
    event.stopPropagation()
    close()
    return
  }
  if (event.key !== 'Tab') return
  const nodes = focusables()
  if (!nodes.length) return
  const first = nodes[0]
  const last = nodes[nodes.length - 1]
  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault()
    last.focus()
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault()
    first.focus()
  }
}

function unlockBody(): void {
  if (previousBodyOverflow === null) return
  document.body.style.overflow = previousBodyOverflow
  previousBodyOverflow = null
}

watch(
  () => props.show,
  (val) => {
    if (val) {
      if (previousBodyOverflow === null) previousBodyOverflow = document.body.style.overflow
      document.body.style.overflow = 'hidden'
      lastFocus.value = document.activeElement instanceof HTMLElement ? document.activeElement : null
      void nextTick(() => closeRef.value?.focus())
      if (!historyHandle) historyHandle = drawerHistory.register(close)
    } else {
      unlockBody()
      lastFocus.value?.focus()
      releaseHistory()
    }
  },
  { immediate: true },
)

function releaseHistory(): void {
  if (!historyHandle) return
  const handle = historyHandle
  historyHandle = null
  drawerHistory.unregister(handle)
}

// 卸载可能是路由跳转引起的：unregister 只在记录仍可消费时回退，不会撤销跳转。
onBeforeUnmount(() => {
  unlockBody()
  releaseHistory()
})
</script>

<template>
  <Teleport to="body">
    <Transition
      enter-active-class="drawer-fade-enter-active"
      enter-from-class="opacity-0"
      enter-to-class="opacity-100"
      leave-active-class="drawer-fade-leave-active"
      leave-from-class="opacity-100"
      leave-to-class="opacity-0"
    >
      <div
        v-if="show"
        class="fixed inset-0 z-[70] bg-black/50"
        @click="close"
      />
    </Transition>

    <Transition
      enter-active-class="drawer-panel-enter-active"
      enter-from-class="drawer-panel-enter-from"
      enter-to-class="drawer-panel-enter-to"
      leave-active-class="drawer-panel-leave-active"
      leave-from-class="drawer-panel-leave-from"
      leave-to-class="drawer-panel-leave-to"
    >
      <aside
        v-if="show"
        ref="panelRef"
        role="dialog"
        aria-modal="true"
        :aria-label="title"
        :style="{ width: typeof width === 'number' ? `${width}px` : width }"
        class="drawer-panel fixed top-0 right-0 z-[80] flex h-full max-h-full max-w-full flex-col overflow-hidden border-l border-[var(--border-subtle)]"
        @keydown="onPanelKeydown"
      >
        <div class="drawer-panel__header flex shrink-0 items-start justify-between gap-4 border-b border-[var(--border-subtle)] px-6 py-4">
          <div class="min-w-0 flex-1">
            <slot name="header">
              <h3 class="truncate text-[16px] font-medium leading-6 text-[var(--text-primary)]">
                {{ title }}
              </h3>
            </slot>
          </div>
          <button
            ref="closeRef"
            type="button"
            aria-label="关闭"
            class="relative inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-[8px] text-[var(--text-secondary)] hover:bg-[var(--bg-surface-hover)] hover:text-[var(--text-primary)] after:absolute after:inset-[-8px] after:content-['']"
            @click="close"
          >
            <CloseIcon
              class="h-4 w-4"
              :stroke-width="1.75"
            />
          </button>
        </div>

        <div class="drawer-panel__body flex-1 overflow-y-auto overscroll-contain px-6 py-5">
          <slot />
        </div>

        <div
          v-if="$slots.footer"
          class="drawer-panel__footer flex shrink-0 items-center justify-end gap-2.5 border-t border-[var(--border-subtle)] px-6 py-4"
        >
          <slot name="footer" />
        </div>
      </aside>
    </Transition>
  </Teleport>
</template>
