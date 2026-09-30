<script setup lang="ts">
import { toastState } from '@/shared/toast'
import {
  CheckCircle2,
  AlertTriangle,
  AlertCircle,
  Info,
} from '@lucide/vue'
</script>

<template>
  <div
    class="pointer-events-none fixed top-5 right-6 z-[9999] flex w-full max-w-sm flex-col gap-2.5"
    role="status"
    aria-live="polite"
    aria-relevant="additions"
  >
    <TransitionGroup
      enter-active-class="transition duration-[280ms] ease-[cubic-bezier(0.05,0.7,0.1,1)]"
      enter-from-class="opacity-0 translate-y-[-12px]"
      enter-to-class="opacity-100 translate-y-0"
      leave-active-class="transition duration-[180ms] ease-[cubic-bezier(0.3,0,1,1)]"
      leave-from-class="opacity-100 translate-y-0"
      leave-to-class="opacity-0 translate-y-[-8px]"
    >
      <div
        v-for="item in toastState.items"
        :key="item.id"
        class="pointer-events-auto flex items-center gap-2.5 rounded-[10px] border border-[var(--border-subtle)] bg-[var(--bg-elevated)] px-4 py-3 text-[13px] font-medium shadow-[var(--shadow-overlay)]"
        :class="
          item.type === 'success'
            ? 'text-[var(--status-info)]'
            : item.type === 'error'
              ? 'text-[var(--status-error)]'
              : item.type === 'warning'
                ? 'text-[var(--status-warning)]'
                : 'text-[var(--text-primary)]'
        "
      >
        <CheckCircle2
          v-if="item.type === 'success'"
          class="h-4 w-4 shrink-0"
          :stroke-width="1.75"
        />
        <AlertCircle
          v-else-if="item.type === 'error'"
          class="h-4 w-4 shrink-0"
          :stroke-width="1.75"
        />
        <AlertTriangle
          v-else-if="item.type === 'warning'"
          class="h-4 w-4 shrink-0"
          :stroke-width="1.75"
        />
        <Info
          v-else
          class="h-4 w-4 shrink-0 text-[var(--brand-accent)]"
          :stroke-width="1.75"
        />
        <span class="leading-normal text-[var(--text-primary)]">{{ item.content }}</span>
      </div>
    </TransitionGroup>
  </div>
</template>
