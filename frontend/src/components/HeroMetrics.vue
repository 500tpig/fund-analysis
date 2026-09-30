<script setup lang="ts">
import { nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import { Info, X } from '@lucide/vue'

export interface HeroMetric {
  key: string
  label: string
  value: string
  subValue?: string
  subValueState?: 'rise' | 'fall' | 'neutral' | 'plain'
  prefix?: string
  suffix?: string
  hint?: string
  delta?: string
  state?: 'rise' | 'fall' | 'neutral' | 'plain'
  deltaToneLevel?: 'strong' | 'muted'
  featured?: boolean
  mobileHidden?: boolean
  infoDetails?: string[]
}

const props = defineProps<{
  items: HeroMetric[]
}>()

const activeInfoKey = ref<string | null>(null)

function toggleInfo(key: string) {
  if (activeInfoKey.value === key) {
    closeInfo(key)
  } else {
    activeInfoKey.value = key
  }
}

function closeInfo(key?: string) {
  const returnKey = key || activeInfoKey.value
  activeInfoKey.value = null
  if (returnKey) {
    nextTick(() => {
      document.getElementById(`hero-info-btn-${returnKey}`)?.focus()
    })
  }
}

function handleGlobalClick(event: MouseEvent) {
  const target = event.target as HTMLElement | null
  if (!target?.closest('.metric-info-container')) {
    activeInfoKey.value = null
  }
}

function handleKeydown(event: KeyboardEvent) {
  if (event.key === 'Escape' && activeInfoKey.value) {
    event.stopPropagation()
    closeInfo()
  }
}

onMounted(() => {
  document.addEventListener('click', handleGlobalClick)
  document.addEventListener('keydown', handleKeydown)
})

onBeforeUnmount(() => {
  document.removeEventListener('click', handleGlobalClick)
  document.removeEventListener('keydown', handleKeydown)
})

function valueClass(state: HeroMetric['state'] | undefined, level: 'strong' | 'muted' = 'strong'): string {
  if (state === 'rise') return level === 'muted' ? 'value-rise-muted' : 'value-rise'
  if (state === 'fall') return level === 'muted' ? 'value-fall-muted' : 'value-fall'
  if (state === 'neutral') return 'value-muted'
  return ''
}

function columns(): string {
  const count = Math.max(props.items.length, 1)
  const featured = props.items.some((item) => item.featured)
  if (featured) {
    return `minmax(220px, 1.4fr) repeat(${count - 1}, minmax(130px, 1fr))`
  }
  return `repeat(${count}, minmax(120px, 1fr))`
}
</script>

<template>
  <section
    class="metric-strip shrink-0"
    :style="{ gridTemplateColumns: columns() }"
  >
    <div
      v-for="item in items"
      :key="item.key"
      :class="['metric-strip__item', item.featured ? 'metric-strip__item--featured' : '']"
      :data-mobile-hidden="item.mobileHidden || undefined"
    >
      <div class="metric-strip__label">
        <span class="truncate">{{ item.label }}</span>
        <div
          v-if="item.infoDetails?.length"
          class="metric-info-container relative inline-flex items-center"
        >
          <button
            :id="`hero-info-btn-${item.key}`"
            type="button"
            class="metric-strip__info-btn"
            :aria-expanded="activeInfoKey === item.key"
            :aria-controls="`hero-info-popover-${item.key}`"
            aria-label="收益说明"
            @click.stop="toggleInfo(item.key)"
          >
            <Info
              class="h-3.5 w-3.5"
              :stroke-width="1.75"
            />
          </button>
          <div
            v-if="activeInfoKey === item.key"
            :id="`hero-info-popover-${item.key}`"
            class="metric-info-popover"
            role="region"
            :aria-labelledby="`hero-info-btn-${item.key}`"
          >
            <div class="metric-info-popover__content">
              <p
                v-for="(line, idx) in item.infoDetails"
                :key="idx"
              >
                {{ line }}
              </p>
            </div>
            <button
              type="button"
              class="metric-info-popover__close"
              aria-label="关闭说明"
              @click.stop="closeInfo(item.key)"
            >
              <X
                class="h-3.5 w-3.5"
                :stroke-width="1.75"
              />
            </button>
          </div>
        </div>
      </div>
      <div
        class="metric-strip__value"
        :class="valueClass(item.state)"
      >
        <span
          v-if="item.prefix"
          class="currency-symbol"
        >{{ item.prefix }}</span>
        <span>{{ item.value }}</span>
        <span
          v-if="item.subValue"
          class="metric-strip__subvalue"
          :class="valueClass(item.subValueState ?? item.state)"
        >{{ item.subValue }}</span>
        <span
          v-if="item.suffix"
          class="text-[13px] font-normal text-[var(--text-secondary)]"
        >{{ item.suffix }}</span>
      </div>
      <div
        v-if="item.delta"
        class="metric-strip__delta"
        :class="valueClass(item.state, item.deltaToneLevel ?? 'strong')"
      >
        {{ item.delta }}
      </div>
      <div
        v-else-if="item.hint"
        class="metric-strip__hint"
      >
        {{ item.hint }}
      </div>
    </div>
  </section>
</template>

<style scoped>
.metric-strip__label {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.metric-strip__info-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 24px;
  height: 24px;
  margin: -4px -4px -4px 0;
  border-radius: 4px;
  color: var(--text-tertiary);
  cursor: pointer;
  transition: color 0.15s ease;
}

.metric-strip__info-btn:hover,
.metric-strip__info-btn:focus-visible {
  color: var(--text-primary);
}

.metric-info-popover {
  position: absolute;
  top: calc(100% + 6px);
  left: 0;
  z-index: 50;
  min-width: 260px;
  max-width: min(360px, 92vw);
  padding: 12px 14px;
  border: 1px solid var(--border-default);
  border-radius: var(--radius-sm);
  background: var(--bg-elevated);
  box-shadow: var(--shadow-overlay);
  display: flex;
  align-items: flex-start;
  gap: 8px;
}

.metric-info-popover__content {
  flex: 1;
  font-size: 12px;
  line-height: 18px;
  color: var(--text-secondary);
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.metric-info-popover__close {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  color: var(--text-tertiary);
  cursor: pointer;
  padding: 4px;
  min-width: 28px;
  min-height: 28px;
  border-radius: 4px;
  transition: color 0.15s ease;
}

.metric-info-popover__close:hover,
.metric-info-popover__close:focus-visible {
  color: var(--text-primary);
}

.metric-strip__subvalue {
  font-size: 0.58em;
  font-weight: 500;
  margin-left: 8px;
  letter-spacing: -0.01em;
  vertical-align: baseline;
}

@media (max-width: 768px) {
  .metric-strip__info-btn {
    min-width: 44px;
    min-height: 44px;
    margin: -10px -10px -10px 0;
  }

  .metric-info-popover__close {
    min-width: 44px;
    min-height: 44px;
    margin: -8px -8px -8px 0;
  }

  .metric-info-popover {
    left: auto;
    right: 0;
    max-width: calc(100vw - 32px);
  }
}
</style>
