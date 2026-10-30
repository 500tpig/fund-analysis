<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'

import type { PerformanceBenchmarkOption } from '@/api/types'
import { chartColors } from '@/theme'
import BenchmarkSelect from '@/components/BenchmarkSelect.vue'
import { formatMonthDay, signedMoney } from '@/shared/formatters'
import {
  type TradingSessionType,
  toTradingMinute,
  computeTradingAxisLayout,
  mapTradingMinuteToX,
  splitTradingSessions,
  getLatestValidPoint,
} from '@/shared/tradingSession'

export interface CurvePoint {
  x: string
  y: number
  nav?: number | null
}

export interface CurveSeries {
  name: string
  color: string
  sourceLabel?: string
  points: CurvePoint[]
}

interface Props {
  series: CurveSeries[]
  xMode: 'date' | 'time'
  sessionType?: TradingSessionType
  valueKind?: 'percent' | 'money'
  benchmarkOptions?: PerformanceBenchmarkOption[]
  benchmarkCode?: string
  refreshing?: boolean
  readoutTarget?: HTMLElement | null
}

const props = withDefaults(defineProps<Props>(), {
  sessionType: 'raw',
  valueKind: 'percent',
  benchmarkOptions: () => [],
  benchmarkCode: '',
  refreshing: false,
  readoutTarget: null,
})

const emit = defineEmits<{
  'update:benchmarkCode': [code: string]
  inspect: []
}>()

const height = ref(290)
const plot = reactive({ left: 68, right: 16, top: 16, bottom: 30 })
const plotHeight = computed(() => height.value - plot.top - plot.bottom)

// SVG 坐标与容器像素 1:1，坐标轴标签用 HTML 渲染，字号不随图表宽度缩放。
const containerRef = ref<HTMLDivElement | null>(null)
const measuredWidth = ref(720)
let resizeObserver: ResizeObserver | null = null

onMounted(() => {
  watch(containerRef, (el) => {
    resizeObserver?.disconnect()
    if (!el) return
    const measure = () => {
      measuredWidth.value = el.clientWidth
      height.value = el.clientHeight
      plot.left = Number(getComputedStyle(el).getPropertyValue('--curve-plot-left'))
    }
    measure()
    resizeObserver = new ResizeObserver(measure)
    resizeObserver.observe(el)
  }, { immediate: true, flush: 'post' })
})

onBeforeUnmount(() => {
  resizeObserver?.disconnect()
  resizeObserver = null
})

const width = computed(() => measuredWidth.value)
const plotWidth = computed(() => width.value - plot.left - plot.right)

// undefined 展示最新值，null 表示当前读点处没有可用数据。
const activeXKey = ref<string | null>()
let selectionInput = 'mouse'

const isCnATradingSession = computed(() => (
  props.xMode === 'time' && props.sessionType === 'cn-a'
))

const cnALayout = computed(() => (
  computeTradingAxisLayout(width.value, plot.left, plot.right, 36)
))

function xValue(value: string): number {
  if (props.xMode === 'date') return new Date(`${value}T00:00:00`).getTime()
  if (value.includes('-') || value.includes('T') || value.includes(' ')) {
    return new Date(value.replace(' ', 'T')).getTime()
  }
  const [hours = 0, minutes = 0, seconds = 0] = value.split(':').map(Number)
  return hours * 3600 + minutes * 60 + seconds
}

const chart = computed(() => {
  const populated = props.series.filter((item) => item.points.length > 0)
  const allPoints = populated.flatMap((item) => item.points)
  if (!allPoints.length) return null

  const ys = allPoints.map((point) => point.y)
  const rawMinY = Math.min(...ys)
  const rawMaxY = Math.max(...ys)
  const padding = Math.max((rawMaxY - rawMinY) * 0.12, 0.2)
  const minY = rawMinY - padding
  const maxY = rawMaxY + padding

  const scaleY = (value: number) => (
    plot.top + ((maxY - value) / Math.max(maxY - minY, 1)) * plotHeight.value
  )

  if (isCnATradingSession.value) {
    const layout = cnALayout.value
    const scaleX = (value: string) => {
      const info = toTradingMinute(value)
      return mapTradingMinuteToX(info, layout)
    }

    const lines = populated.map((item) => {
      const { morning, afternoon } = splitTradingSessions(item.points)
      const morningPolyline = morning.map((p) => `${scaleX(p.x)},${scaleY(p.y)}`).join(' ')
      const afternoonPolyline = afternoon.map((p) => `${scaleX(p.x)},${scaleY(p.y)}`).join(' ')
      const lastPoint = getLatestValidPoint(item.points) || item.points[item.points.length - 1]
      const lastX = scaleX(lastPoint.x)
      const lastY = scaleY(lastPoint.y)

      return {
        ...item,
        polyline: '',
        morningPolyline,
        afternoonPolyline,
        last: lastPoint,
        lastX,
        lastY,
      }
    })

    return {
      minY,
      maxY,
      scaleX,
      scaleY,
      zeroY: minY <= 0 && maxY >= 0 ? scaleY(0) : null,
      lines,
      isCnA: true,
      layout,
    }
  }

  // General continuous date / time mode
  const xs = allPoints.map((point) => xValue(point.x))
  const minX = Math.min(...xs)
  const maxX = Math.max(...xs)
  const scaleX = (value: string) => (
    plot.left + ((xValue(value) - minX) / Math.max(maxX - minX, 1)) * plotWidth.value
  )

  const lines = populated.map((item) => {
    const polyline = item.points.map((point) => `${scaleX(point.x)},${scaleY(point.y)}`).join(' ')
    const lastPoint = getLatestValidPoint(item.points) || item.points[item.points.length - 1]
    const lastX = scaleX(lastPoint.x)
    const lastY = scaleY(lastPoint.y)

    return {
      ...item,
      polyline,
      morningPolyline: '',
      afternoonPolyline: '',
      last: lastPoint,
      lastX,
      lastY,
    }
  })

  return {
    minY,
    maxY,
    scaleX,
    scaleY,
    zeroY: minY <= 0 && maxY >= 0 ? scaleY(0) : null,
    lines,
    isCnA: false,
    xLabels: [
      allPoints.reduce((a, b) => xValue(a.x) < xValue(b.x) ? a : b).x,
      allPoints.reduce((a, b) => xValue(a.x) > xValue(b.x) ? a : b).x,
    ],
  }
})

const uniqueXList = computed(() => {
  const primary = props.series.find((s) => s.points.length > 0)
  const points = primary?.points ?? []
  const valid = isCnATradingSession.value
    ? points.filter((p) => ['morning', 'afternoon'].includes(toTradingMinute(p.x).session))
    : points
  return [...new Set(valid.map((p) => p.x))].sort((a, b) => xValue(a) - xValue(b))
})

// 分时缺口不延用邻点；以实际最小采样间隔的一半作为最近点的吸附范围。
const selectionRadius = computed(() => {
  if (props.xMode !== 'time' || !chart.value) return Infinity
  const positions = uniqueXList.value.map(chart.value.scaleX)
  const steps = positions.slice(1).map((x, i) => x - positions[i]).filter((step) => step > 0)
  return Math.min(...steps) / 2
})

interface HoverItem {
  name: string
  color: string
  value: number
  nav?: number | null
  yPos: number
}

const activeHover = computed<{
  xKey: string
  xPos: number
  items: HoverItem[]
} | null>(() => {
  if (!activeXKey.value || !chart.value || uniqueXList.value.length < 2) return null
  const xKey = activeXKey.value
  if (!uniqueXList.value.includes(xKey)) return null
  const xPos = chart.value.scaleX(xKey)

  const rawItems: Array<HoverItem | null> = chart.value.lines.map((line) => {
    const exactPoint = line.points.length > 1 ? line.points.find((p) => p.x === xKey) : null
    if (exactPoint) {
      return {
        name: line.name,
        color: line.color,
        value: exactPoint.y,
        nav: exactPoint.nav,
        yPos: chart.value!.scaleY(exactPoint.y),
      }
    }

    return null
  })

  const items = rawItems.filter((item): item is HoverItem => item !== null)
  if (!items.length) return null

  return {
    xKey,
    xPos,
    items,
  }
})

const legendItems = computed(() => chart.value?.lines.map((line) => ({
  ...line,
  point: activeXKey.value === undefined
    ? { value: line.last.y, nav: line.last.nav }
    : activeHover.value?.items.find((item) => item.name === line.name),
})) ?? [])

function formatValue(value: number): string {
  if (props.valueKind === 'money') return signedMoney(value)
  return `${value > 0 ? '+' : ''}${value.toFixed(2)}%`
}

function formatAxisValue(value: number): string {
  if (props.valueKind !== 'money' || plot.left !== 48 || Math.abs(value) < 100) return formatValue(value)
  // 手机金额轴缩写，顶部读数仍保留完整金额。
  return value.toLocaleString('en', {
    notation: 'compact', signDisplay: 'exceptZero',
    maximumSignificantDigits: Math.abs(value) < 1000 ? 4 : 3,
  })
}

function formatX(value: string): string {
  if (props.xMode === 'time') {
    if (value.includes('-') || value.includes(' ')) {
      const parts = value.split(/[ T]/)
      return (parts[1] || value).slice(0, 5)
    }
    return value.slice(0, 5)
  }
  return formatMonthDay(value, value.slice(5))
}

function formatTooltipTime(value: string): string {
  if (props.xMode === 'time') {
    if (value.includes('-') || value.includes(' ')) {
      const [datePart = '', timePart = ''] = value.split(/[ T]/)
      const md = datePart.length >= 10 ? datePart.slice(5) : datePart
      return `${md} ${timePart.slice(0, 5)}`
    }
    return value.slice(0, 5)
  }
  return formatX(value)
}

function seriesDisplayLabel(item: { name: string; sourceLabel?: string }, timeStr: string): string {
  if (props.xMode === 'time') {
    const source = item.sourceLabel || (item.name.includes('分时') ? item.name : `${item.name}`)
    const prefix = source.endsWith('分时') ? source : `${source}分时`
    return `${prefix} · ${formatX(timeStr)}`
  }
  return item.name
}

function readPointer(e: PointerEvent): boolean {
  selectionInput = e.pointerType
  if (!chart.value || !containerRef.value) return false
  if (uniqueXList.value.length < 2) {
    activeXKey.value = null
    return false
  }
  const rect = containerRef.value.getBoundingClientRect()
  const relSvgX = (e.clientX - rect.left) * (width.value / rect.width)
  const relY = e.clientY - rect.top
  if (relSvgX < plot.left || relSvgX > width.value - plot.right
    || relY < plot.top || relY > height.value - plot.bottom) {
    activeXKey.value = null
    return false
  }
  emit('inspect')
  if (isCnATradingSession.value
    && relSvgX > cnALayout.value.morningEndX && relSvgX < cnALayout.value.afternoonStartX) {
    activeXKey.value = null
    return true
  }

  let bestKey = uniqueXList.value[0]
  let bestDist = Infinity
  for (const k of uniqueXList.value) {
    const px = chart.value.scaleX(k)
    const dist = Math.abs(px - relSvgX)
    if (dist < bestDist) {
      bestDist = dist
      bestKey = k
    }
  }
  activeXKey.value = bestDist <= selectionRadius.value ? bestKey : null
  return true
}

let touchPointerId: number | null = null
let previousXKey: string | null | undefined
let previousInput = 'mouse'

function handlePointerDown(e: PointerEvent) {
  if (!e.isPrimary || e.button !== 0) return
  if (e.pointerType === 'mouse') {
    readPointer(e)
    return
  }
  if (touchPointerId !== null) return
  previousXKey = activeXKey.value
  previousInput = selectionInput
  if (!readPointer(e)) return
  touchPointerId = e.pointerId
  containerRef.value?.setPointerCapture(e.pointerId)
}

function handlePointerMove(e: PointerEvent) {
  if (e.pointerType === 'mouse' || e.pointerId === touchPointerId) readPointer(e)
}

function handlePointerUp(e: PointerEvent) {
  if (e.pointerId === touchPointerId) touchPointerId = null
}

function handlePointerCancel(e: PointerEvent) {
  if (e.pointerId !== touchPointerId) return
  // 浏览器接管纵向滚动时撤销本次轻触，保留上一次读数。
  activeXKey.value = previousXKey
  selectionInput = previousInput
  touchPointerId = null
}

function handlePointerLeave(e: PointerEvent) {
  if (e.pointerType === 'mouse' && selectionInput === 'mouse') activeXKey.value = undefined
}

function handleKeydown(e: KeyboardEvent) {
  const keys = uniqueXList.value
  if (keys.length < 2 || !['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(e.key)) return
  e.preventDefault()
  emit('inspect')
  selectionInput = 'keyboard'
  const index = activeXKey.value ? keys.indexOf(activeXKey.value) : keys.length - 1
  const next = e.key === 'Home' ? 0 : e.key === 'End' ? keys.length - 1
    : index + (e.key === 'ArrowLeft' ? -1 : 1)
  activeXKey.value = keys[Math.max(0, Math.min(keys.length - 1, next))]
}

const showBenchmarkSelect = computed(() => props.benchmarkOptions.length > 0)

const benchmarkLine = computed(() => {
  if (!chart.value || chart.value.lines.length < 2) return null
  return chart.value.lines[1]
})
</script>

<template>
  <div
    v-if="chart"
    class="curve-chart"
    :class="{ 'is-refreshing': refreshing, 'is-unreadable': uniqueXList.length < 2 }"
  >
    <div class="curve-chart__toolbar">
      <slot name="toolbar-prefix" />
      <Teleport
        :to="readoutTarget || 'body'"
        :disabled="!readoutTarget"
      >
        <div class="curve-chart__legend">
          <time
            class="curve-chart__readout-date"
          >{{ activeXKey !== undefined && !activeHover ? '—' : formatTooltipTime(activeHover?.xKey ?? chart.lines[0].last.x) }}</time>
          <div
            v-for="(item, index) in legendItems"
            :key="item.name"
            class="curve-chart__legend-item"
          >
            <span
              class="curve-chart__swatch"
              :style="{ background: item.color }"
            />
            <BenchmarkSelect
              v-if="showBenchmarkSelect && index > 0"
              :options="benchmarkOptions"
              :model-value="benchmarkCode"
              @update:model-value="emit('update:benchmarkCode', $event)"
            />
            <template v-else>
              <span class="curve-chart__series-label">{{ seriesDisplayLabel(item, activeHover ? activeHover.xKey : item.last.x) }}</span>
              <span class="curve-chart__readout-label">{{ item.sourceLabel || item.name }}</span>
            </template>
            <strong
              v-if="item.point"
              :class="item.point.value >= 0 ? 'value-rise' : 'value-fall'"
            >
              {{ formatValue(item.point.value) }}
            </strong>
            <strong v-else>—</strong>
            <span
              v-if="item.last.nav != null"
              class="curve-chart__readout-nav font-numeric"
            >
              净值 {{ item.point?.nav != null ? item.point.nav.toFixed(4) : '—' }}
            </span>
          </div>
          <div
            v-if="showBenchmarkSelect && !benchmarkLine"
            class="curve-chart__legend-item"
          >
            <span
              class="curve-chart__swatch"
              :style="{ background: chartColors.benchmark }"
            />
            <BenchmarkSelect
              :options="benchmarkOptions"
              :model-value="benchmarkCode"
              @update:model-value="emit('update:benchmarkCode', $event)"
            />
            <strong class="text-[var(--text-muted)]">—</strong>
          </div>
        </div>
      </Teleport>
    </div>

    <div
      ref="containerRef"
      class="curve-chart__plot-wrapper"
      role="slider"
      aria-label="曲线时间点"
      aria-orientation="horizontal"
      :tabindex="uniqueXList.length > 1 ? 0 : -1"
      :aria-disabled="uniqueXList.length < 2"
      :aria-valuemin="0"
      :aria-valuemax="Math.max(0, uniqueXList.length - 1)"
      :aria-valuenow="activeXKey === undefined ? uniqueXList.length - 1 : Math.max(0, uniqueXList.indexOf(activeHover?.xKey ?? ''))"
      :aria-valuetext="activeHover ? `${formatTooltipTime(activeHover.xKey)} ${activeHover.items.map(item => `${item.name} ${formatValue(item.value)}`).join('，')}` : '未选择时间点'"
      @pointerdown="handlePointerDown"
      @pointermove="handlePointerMove"
      @pointerup="handlePointerUp"
      @pointercancel="handlePointerCancel"
      @pointerleave="handlePointerLeave"
      @keydown="handleKeydown"
    >
      <svg
        class="curve-chart__plot"
        :viewBox="`0 0 ${width} ${height}`"
        preserveAspectRatio="none"
        aria-hidden="true"
      >
        <!-- Horizontal grid lines -->
        <line
          v-for="index in 5"
          :key="index"
          :x1="plot.left"
          :x2="width - plot.right"
          :y1="plot.top + ((index - 1) / 4) * plotHeight"
          :y2="plot.top + ((index - 1) / 4) * plotHeight"
          class="curve-chart__grid"
        />

        <!-- Zero line -->
        <line
          v-if="chart.zeroY !== null"
          :x1="plot.left"
          :x2="width - plot.right"
          :y1="chart.zeroY"
          :y2="chart.zeroY"
          class="curve-chart__zero"
        />

        <!-- A-share Trading Session Axis: Lunch break visual zone -->
        <g
          v-if="chart.isCnA && chart.layout"
          class="curve-chart__break-group"
        >
          <rect
            :x="chart.layout.morningEndX"
            :y="plot.top"
            :width="chart.layout.breakGap"
            :height="plotHeight"
            class="curve-chart__break-zone"
          />
          <line
            :x1="chart.layout.morningEndX"
            :x2="chart.layout.morningEndX"
            :y1="plot.top"
            :y2="height - plot.bottom"
            class="curve-chart__break-border"
          />
          <line
            :x1="chart.layout.afternoonStartX"
            :x2="chart.layout.afternoonStartX"
            :y1="plot.top"
            :y2="height - plot.bottom"
            class="curve-chart__break-border"
          />
          <text
            :x="chart.layout.breakCenterX"
            :y="plot.top + plotHeight / 2"
            text-anchor="middle"
            dominant-baseline="central"
            class="curve-chart__break-text"
          >休市</text>
        </g>

        <!-- Polylines: if CnA session, morning and afternoon are drawn as distinct paths -->
        <template v-if="chart.isCnA">
          <g
            v-for="(item, index) in chart.lines"
            :key="item.name"
          >
            <polyline
              v-if="item.morningPolyline"
              :points="item.morningPolyline"
              :stroke="item.color"
              :class="['curve-chart__line', index > 0 ? 'is-benchmark' : '']"
            />
            <polyline
              v-if="item.afternoonPolyline"
              :points="item.afternoonPolyline"
              :stroke="item.color"
              :class="['curve-chart__line', index > 0 ? 'is-benchmark' : '']"
            />
          </g>
        </template>
        <template v-else>
          <polyline
            v-for="(item, index) in chart.lines"
            :key="item.name"
            :points="item.polyline"
            :stroke="item.color"
            :class="['curve-chart__line', index > 0 ? 'is-benchmark' : '']"
          />
        </template>

        <!-- Last point circle -->
        <circle
          v-for="item in chart.lines"
          :key="`${item.name}-last`"
          :cx="item.lastX"
          :cy="item.lastY"
          r="3.5"
          :fill="item.color"
          class="curve-chart__last-point"
        />

        <!-- Hover crosshair and hover circle -->
        <g
          v-if="activeHover"
          class="curve-chart__hover-group"
        >
          <line
            :x1="activeHover.xPos"
            :x2="activeHover.xPos"
            :y1="plot.top"
            :y2="height - plot.bottom"
            class="curve-chart__crosshair"
          />
          <circle
            v-for="pt in activeHover.items"
            :key="`hover-point-${pt.name}`"
            :cx="activeHover.xPos"
            :cy="pt.yPos"
            r="4.5"
            :fill="pt.color"
            class="curve-chart__hover-point"
          />
        </g>
      </svg>

      <!-- Y axis labels (HTML, fixed font size) -->
      <div
        class="curve-chart__y-labels"
        aria-hidden="true"
      >
        <span
          class="curve-chart__axis-label curve-chart__axis-label--y"
          :style="{ top: `${plot.top}px`, width: `${plot.left - 8}px` }"
        >{{ formatAxisValue(chart.maxY) }}</span>
        <span
          v-if="chart.zeroY !== null && Math.abs(chart.zeroY - plot.top) > 16 && Math.abs(chart.zeroY - (height - plot.bottom)) > 16"
          class="curve-chart__axis-label curve-chart__axis-label--y is-zero"
          :style="{ top: `${chart.zeroY}px`, width: `${plot.left - 8}px` }"
        >{{ formatAxisValue(0) }}</span>
        <span
          class="curve-chart__axis-label curve-chart__axis-label--y"
          :style="{ top: `${height - plot.bottom}px`, width: `${plot.left - 8}px` }"
        >{{ formatAxisValue(chart.minY) }}</span>
      </div>

      <!-- X axis labels (HTML, fixed font size) -->
      <div
        class="curve-chart__x-labels"
        aria-hidden="true"
      >
        <template v-if="chart.isCnA && chart.layout">
          <span
            class="curve-chart__axis-label"
            :style="{ left: `${chart.layout.morningStartX}px` }"
          >09:30</span>
          <span
            class="curve-chart__axis-label curve-chart__axis-label--end curve-chart__axis-label--session-edge"
            :style="{ left: `${chart.layout.morningEndX}px` }"
          >11:30</span>
          <span
            class="curve-chart__axis-label curve-chart__axis-label--session-edge"
            :style="{ left: `${chart.layout.afternoonStartX}px` }"
          >13:00</span>
          <span
            class="curve-chart__axis-label curve-chart__axis-label--session-mid"
            :style="{ left: `${chart.layout.breakCenterX}px` }"
          >11:30·13:00</span>
          <span
            class="curve-chart__axis-label curve-chart__axis-label--end"
            :style="{ left: `${chart.layout.afternoonEndX}px` }"
          >15:00</span>
        </template>
        <template v-else-if="chart.xLabels">
          <span
            class="curve-chart__axis-label"
            :style="{ left: `${plot.left}px` }"
          >{{ formatX(chart.xLabels[0]) }}</span>
          <span
            class="curve-chart__axis-label curve-chart__axis-label--end"
            :style="{ left: `${width - plot.right}px` }"
          >{{ formatX(chart.xLabels[1]) }}</span>
        </template>
      </div>

      <!-- Hover Tooltip -->
      <div
        v-if="activeHover"
        class="curve-chart__tooltip"
        :style="{
          left: `${activeHover.xPos}px`,
          transform: activeHover.xPos > width / 2
            ? 'translate(-108%, 0)'
            : 'translate(8%, 0)',
        }"
      >
        <div class="tooltip-header font-numeric">
          {{ formatTooltipTime(activeHover.xKey) }}
        </div>
        <div class="tooltip-body">
          <div
            v-for="item in activeHover.items"
            :key="`tooltip-${item.name}`"
            class="tooltip-row"
          >
            <div class="tooltip-label">
              <span
                class="tooltip-dot"
                :style="{ background: item.color }"
              />
              <span>{{ item.name }}</span>
            </div>
            <div class="flex items-baseline gap-2">
              <span
                v-if="item.nav != null"
                class="font-numeric text-[12px] text-[var(--text-tertiary)]"
              >
                净值 {{ item.nav.toFixed(4) }}
              </span>
              <strong
                class="font-numeric tooltip-value"
                :class="item.value >= 0 ? 'value-rise' : 'value-fall'"
              >
                {{ formatValue(item.value) }}
              </strong>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.curve-chart {
  display: grid;
  gap: 12px;
}

.curve-chart.is-refreshing .curve-chart__plot {
  opacity: 0.45;
  transition: opacity var(--dur-ui) var(--ease-out);
}

.curve-chart__toolbar {
  position: relative;
  z-index: 2;
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 12px;
  min-height: 28px;
}

.curve-chart__toolbar:empty {
  display: none;
}

.curve-chart__legend {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px 18px;
  margin-left: auto;
}

.curve-chart__legend-item {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  color: var(--text-secondary);
  font-size: 12px;
}

.curve-chart__legend-item strong {
  font-family: var(--font-mono);
  font-size: 13px;
}

.curve-chart__swatch {
  width: 16px;
  height: 2px;
  border-radius: 999px;
}

.curve-chart__readout-date,
.curve-chart__readout-nav,
.curve-chart__readout-label,
.curve-chart__axis-label--session-mid {
  display: none;
}

/* 纵向滚动由浏览器接管，并通过 pointercancel 撤销当次读点。 */
.curve-chart__plot-wrapper {
  --curve-plot-left: 68;
  position: relative;
  width: 100%;
  height: 290px;
  cursor: crosshair;
  touch-action: pan-y;
}

.curve-chart__plot {
  width: 100%;
  height: 100%;
  display: block;
  background: transparent;
}

/* 层次：午休底色 < 网格 < 零线 < 基准线 < 主曲线，取值见 main.css 的 --chart-* token。 */
.curve-chart__grid {
  stroke: var(--chart-grid);
  stroke-width: var(--chart-stroke-grid);
  vector-effect: non-scaling-stroke;
}

.curve-chart__zero {
  stroke: var(--chart-zero-line);
  stroke-width: var(--chart-stroke-zero);
  stroke-dasharray: 4 4;
  vector-effect: non-scaling-stroke;
}

.curve-chart__break-zone {
  fill: var(--chart-break-fill);
  pointer-events: none;
}

.curve-chart__break-border {
  stroke: var(--chart-grid);
  stroke-width: var(--chart-stroke-grid);
  stroke-dasharray: 2 3;
  vector-effect: non-scaling-stroke;
  pointer-events: none;
}

.curve-chart__break-text {
  fill: var(--text-secondary);
  font-size: 12px;
  letter-spacing: 0.12em;
  font-family: var(--font-ui);
  user-select: none;
  pointer-events: none;
}

.curve-chart__line {
  fill: none;
  stroke-width: var(--chart-stroke-primary);
  stroke-linecap: round;
  stroke-linejoin: round;
  vector-effect: non-scaling-stroke;
}

.curve-chart__line.is-benchmark {
  stroke-width: var(--chart-stroke-benchmark);
}

.curve-chart__last-point {
  stroke: var(--chart-surface);
  stroke-width: 2;
  vector-effect: non-scaling-stroke;
}

.curve-chart__crosshair {
  stroke: var(--chart-crosshair);
  stroke-width: var(--chart-stroke-zero);
  vector-effect: non-scaling-stroke;
  stroke-dasharray: 4 4;
  pointer-events: none;
}

.curve-chart__hover-point {
  stroke: var(--chart-surface);
  stroke-width: 2;
  pointer-events: none;
}

.curve-chart__y-labels,
.curve-chart__x-labels {
  position: absolute;
  inset: 0;
  pointer-events: none;
}

.curve-chart__axis-label {
  position: absolute;
  color: var(--text-tertiary);
  font-family: var(--font-ui);
  font-size: 12px;
  line-height: 16px;
  font-variant-numeric: tabular-nums lining-nums;
  white-space: nowrap;
}

.curve-chart__axis-label--y {
  left: 0;
  text-align: right;
  transform: translateY(-50%);
}

.curve-chart__axis-label--y.is-zero {
  opacity: 0.75;
}

.curve-chart__x-labels .curve-chart__axis-label {
  bottom: 4px;
}

.curve-chart__axis-label--end {
  transform: translateX(-100%);
}

.curve-chart__tooltip {
  position: absolute;
  top: 16px;
  pointer-events: none;
  z-index: 20;
  min-width: 160px;
  width: max-content;
  white-space: nowrap;
  background: var(--bg-elevated);
  border-radius: var(--radius-panel);
  box-shadow: var(--shadow-overlay);
  padding: 8px 12px;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.tooltip-header {
  font-size: 12px;
  color: var(--text-secondary);
  border-bottom: 1px solid var(--border-default);
  padding-bottom: 4px;
  white-space: nowrap;
}

.tooltip-body {
  display: flex;
  flex-direction: column;
  gap: 5px;
}

.tooltip-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  font-size: 12px;
  white-space: nowrap;
}

.tooltip-label {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  color: var(--text-secondary);
  white-space: nowrap;
  flex-shrink: 0;
}

.tooltip-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  flex-shrink: 0;
}

.tooltip-value {
  font-family: var(--font-mono);
  font-weight: 600;
  white-space: nowrap;
  flex-shrink: 0;
}

@media (max-width: 768px) {
  .curve-chart__plot-wrapper {
    --curve-plot-left: 48;
    height: 240px;
  }

  .curve-chart__grid:not(:nth-child(3)),
  .curve-chart__axis-label--session-edge,
  .curve-chart__series-label,
  .curve-chart.is-unreadable .curve-chart__readout-date,
  .curve-chart.is-unreadable .curve-chart__readout-nav,
  .curve-chart.is-unreadable .curve-chart__legend-item strong,
  .curve-chart__tooltip {
    display: none;
  }

  .curve-chart__axis-label--session-mid {
    display: block;
    transform: translateX(-50%);
  }

  .curve-chart__legend {
    width: 100%;
    margin-left: 0;
    gap: 8px 16px;
  }

  .curve-chart__readout-date {
    display: block;
    width: 100%;
    color: var(--text-secondary);
    font-size: 12px;
    font-variant-numeric: tabular-nums;
  }

  .curve-chart__readout-nav {
    display: inline;
    color: var(--text-tertiary);
    font-size: 12px;
  }

  .curve-chart__readout-label {
    display: inline;
  }

  .curve-chart__legend-item {
    flex-wrap: wrap;
  }
}
</style>
