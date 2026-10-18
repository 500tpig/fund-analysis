<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'

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
}

const props = withDefaults(defineProps<Props>(), {
  sessionType: 'raw',
  valueKind: 'percent',
  benchmarkOptions: () => [],
  benchmarkCode: '',
  refreshing: false,
})

const emit = defineEmits<{
  'update:benchmarkCode': [code: string]
}>()

const height = 290
const plot = { left: 68, right: 16, top: 16, bottom: 30 }
const plotHeight = height - plot.top - plot.bottom

// SVG 坐标与容器像素 1:1，坐标轴标签用 HTML 渲染，字号不随图表宽度缩放。
const containerRef = ref<HTMLDivElement | null>(null)
const measuredWidth = ref(720)
let resizeObserver: ResizeObserver | null = null

onMounted(() => {
  const el = containerRef.value
  if (!el) return
  measuredWidth.value = Math.max(Math.round(el.clientWidth), 280)
  resizeObserver = new ResizeObserver((entries) => {
    const entry = entries[0]
    if (!entry) return
    const next = Math.max(Math.round(entry.contentRect.width), 280)
    if (next !== measuredWidth.value) measuredWidth.value = next
  })
  resizeObserver.observe(el)
})

onBeforeUnmount(() => {
  resizeObserver?.disconnect()
  resizeObserver = null
})

const width = computed(() => measuredWidth.value)
const plotWidth = computed(() => width.value - plot.left - plot.right)

const activeXKey = ref<string | null>(null)

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
    plot.top + ((maxY - value) / Math.max(maxY - minY, 1)) * plotHeight
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
  if (!props.series.length) return []
  const primary = props.series.find((s) => s.points.length > 0)
  if (primary && primary.points.length > 0) {
    return primary.points.map((p) => p.x)
  }
  const map = new Map<string, number>()
  for (const s of props.series) {
    for (const p of s.points) {
      if (!map.has(p.x)) {
        map.set(p.x, xValue(p.x))
      }
    }
  }
  return Array.from(map.entries())
    .sort((a, b) => a[1] - b[1])
    .map((item) => item[0])
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
  if (!activeXKey.value || !chart.value) return null
  const xKey = activeXKey.value
  const xPos = chart.value.scaleX(xKey)
  const targetXVal = xValue(xKey)

  const rawItems: Array<HoverItem | null> = chart.value.lines.map((line) => {
    const exactPoint = line.points.find((p) => p.x === xKey)
    if (exactPoint) {
      return {
        name: line.name,
        color: line.color,
        value: exactPoint.y,
        nav: exactPoint.nav,
        yPos: chart.value!.scaleY(exactPoint.y),
      }
    }

    const prevPoints = line.points.filter((p) => xValue(p.x) <= targetXVal)
    if (prevPoints.length > 0) {
      const nearest = prevPoints[prevPoints.length - 1]
      return {
        name: line.name,
        color: line.color,
        value: nearest.y,
        nav: nearest.nav,
        yPos: chart.value!.scaleY(nearest.y),
      }
    }

    if (line.points.length > 0) {
      const first = line.points[0]
      return {
        name: line.name,
        color: line.color,
        value: first.y,
        nav: first.nav,
        yPos: chart.value!.scaleY(first.y),
      }
    }

    return null
  })

  const items = rawItems.filter((item): item is HoverItem => item !== null)

  return {
    xKey,
    xPos,
    items,
  }
})

function formatValue(value: number): string {
  if (props.valueKind === 'money') return signedMoney(value)
  return `${value > 0 ? '+' : ''}${value.toFixed(2)}%`
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

// 触摸滚动交给浏览器（touch-action: pan-y），十字线只在鼠标指针下出现。
function handlePointerMove(e: PointerEvent) {
  if (e.pointerType !== 'mouse') return
  if (!chart.value || !uniqueXList.value.length || !containerRef.value) return
  const rect = containerRef.value.getBoundingClientRect()
  const relSvgX = ((e.clientX - rect.left) / rect.width) * width.value
  const clampedX = Math.max(plot.left, Math.min(width.value - plot.right, relSvgX))

  let bestKey = uniqueXList.value[0]
  let bestDist = Infinity
  for (const k of uniqueXList.value) {
    const px = chart.value.scaleX(k)
    const dist = Math.abs(px - clampedX)
    if (dist < bestDist) {
      bestDist = dist
      bestKey = k
    }
  }
  activeXKey.value = bestKey
}

function handlePointerLeave() {
  activeXKey.value = null
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
    :class="refreshing ? 'is-refreshing' : ''"
  >
    <div class="curve-chart__toolbar">
      <slot name="toolbar-prefix" />
      <div class="curve-chart__legend">
        <div
          v-for="(item, index) in chart.lines"
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
          <span v-else>{{ seriesDisplayLabel(item, activeHover ? activeHover.xKey : item.last.x) }}</span>
          <strong
            v-if="activeHover"
            :class="((activeHover.items.find(i => i.name === item.name)?.value ?? 0) >= 0) ? 'value-rise' : 'value-fall'"
          >
            {{ formatValue(activeHover.items.find(i => i.name === item.name)?.value ?? item.last.y) }}
          </strong>
          <strong
            v-else
            :class="item.last.y >= 0 ? 'value-rise' : 'value-fall'"
          >
            {{ formatValue(item.last.y) }}
          </strong>
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
    </div>

    <div
      ref="containerRef"
      class="curve-chart__plot-wrapper"
      :style="{ height: `${height}px` }"
      @pointermove="handlePointerMove"
      @pointerleave="handlePointerLeave"
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
            v-for="item in chart.lines"
            :key="item.name"
          >
            <polyline
              v-if="item.morningPolyline"
              :points="item.morningPolyline"
              :stroke="item.color"
              class="curve-chart__line"
            />
            <polyline
              v-if="item.afternoonPolyline"
              :points="item.afternoonPolyline"
              :stroke="item.color"
              class="curve-chart__line"
            />
          </g>
        </template>
        <template v-else>
          <polyline
            v-for="item in chart.lines"
            :key="item.name"
            :points="item.polyline"
            :stroke="item.color"
            class="curve-chart__line"
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
        >{{ formatValue(chart.maxY) }}</span>
        <span
          v-if="chart.zeroY !== null && Math.abs(chart.zeroY - plot.top) > 16 && Math.abs(chart.zeroY - (height - plot.bottom)) > 16"
          class="curve-chart__axis-label curve-chart__axis-label--y is-zero"
          :style="{ top: `${chart.zeroY}px`, width: `${plot.left - 8}px` }"
        >{{ formatValue(0) }}</span>
        <span
          class="curve-chart__axis-label curve-chart__axis-label--y"
          :style="{ top: `${height - plot.bottom}px`, width: `${plot.left - 8}px` }"
        >{{ formatValue(chart.minY) }}</span>
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
            class="curve-chart__axis-label curve-chart__axis-label--end"
            :style="{ left: `${chart.layout.morningEndX}px` }"
          >11:30</span>
          <span
            class="curve-chart__axis-label"
            :style="{ left: `${chart.layout.afternoonStartX}px` }"
          >13:00</span>
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
                class="font-numeric text-[11px] text-[var(--text-tertiary)]"
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

/* touch-action: pan-y 让纵向滚动透传给页面；十字线只在鼠标指针下响应。 */
.curve-chart__plot-wrapper {
  position: relative;
  width: 100%;
  cursor: crosshair;
  touch-action: pan-y;
}

.curve-chart__plot {
  width: 100%;
  height: 100%;
  display: block;
  background: transparent;
}

.curve-chart__grid {
  stroke: var(--chart-grid);
  stroke-width: 1;
}

.curve-chart__zero {
  stroke: var(--chart-zero-line);
  stroke-width: 1.25;
  stroke-dasharray: 4 4;
}

.curve-chart__break-zone {
  fill: rgba(255, 255, 255, 0.022);
  pointer-events: none;
}

.curve-chart__break-border {
  stroke: var(--border-subtle);
  stroke-width: 1;
  stroke-dasharray: 2 3;
  opacity: 0.85;
  pointer-events: none;
}

.curve-chart__break-text {
  fill: var(--text-disabled);
  font-size: 10px;
  letter-spacing: 0.12em;
  font-family: var(--font-ui);
  user-select: none;
  pointer-events: none;
}

.curve-chart__line {
  fill: none;
  stroke-width: 1.75;
  stroke-linecap: round;
  stroke-linejoin: round;
  vector-effect: non-scaling-stroke;
}

.curve-chart__last-point {
  stroke: var(--bg-sunken);
  stroke-width: 2;
  vector-effect: non-scaling-stroke;
}

.curve-chart__crosshair {
  stroke: rgba(241, 239, 233, 0.35);
  stroke-width: 1.25;
  stroke-dasharray: 4 4;
  pointer-events: none;
}

.curve-chart__hover-point {
  stroke: var(--bg-base);
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
  font-size: 11px;
  line-height: 14px;
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
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-container);
  box-shadow: var(--shadow-overlay);
  padding: 8px 12px;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.tooltip-header {
  font-size: 11px;
  color: var(--text-secondary);
  border-bottom: 1px solid var(--border-subtle);
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
</style>
