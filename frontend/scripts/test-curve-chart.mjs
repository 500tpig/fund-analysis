import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { createContext, runInContext } from 'node:vm'
import { computed, nextTick, reactive, ref, watch } from 'vue'
import ts from 'typescript'

import * as tradingSession from '../src/shared/tradingSession.ts'
import { formatMonthDay, signedMoney } from '../src/shared/formatters.ts'

// 执行组件真实的 setup 与事件处理，不另写一份手势逻辑。
const source = readFileSync(new URL('../src/components/FundCurveChart.vue', import.meta.url), 'utf8')
const setup = source.match(/<script setup lang="ts">([\s\S]*?)<\/script>/)[1]
const ast = ts.createSourceFile('chart.ts', setup, ts.ScriptTarget.Latest, true, ts.ScriptKind.TS)
const script = ts.transpile(ast.statements.filter((node) => !ts.isImportDeclaration(node))
  .map((node) => node.getFullText(ast)).join(''), {
  module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ESNext,
})

function mount(points, options = {}) {
  const props = reactive({ series: [{ name: '基金', color: 'gold', points }], xMode: 'time', ...options })
  let mounted
  let resized
  const emitted = []
  const context = createContext({
    ...tradingSession, computed, reactive, ref, watch, formatMonthDay, signedMoney,
    chartColors: {}, exports: {},
    defineProps: () => props,
    withDefaults: (value, defaults) => {
      for (const [key, fallback] of Object.entries(defaults)) {
        if (value[key] === undefined) value[key] = typeof fallback === 'function' ? fallback() : fallback
      }
      return value
    },
    defineEmits: () => (event) => emitted.push(event),
    onMounted: (callback) => { mounted = callback },
    onBeforeUnmount: () => {},
    getComputedStyle: (el) => ({ getPropertyValue: () => el.clientHeight === 240 ? '48' : '68' }),
    ResizeObserver: class {
      constructor(callback) { resized = callback }
      observe() {}
      disconnect() {}
    },
  })
  runInContext(script, context)
  const state = runInContext(`({ chart, containerRef, height, plot, uniqueXList, activeHover,
    activeXKey, legendItems, formatTooltipTime, formatValue, formatAxisValue, handlePointerDown, handlePointerMove,
    handlePointerUp, handlePointerCancel, handlePointerLeave, handleKeydown })`, context)
  const element = {
    clientWidth: 358, clientHeight: 240,
    getBoundingClientRect() { return { left: 0, top: 0, width: this.clientWidth } },
    setPointerCapture: () => {},
  }
  state.containerRef.value = element
  mounted()
  return {
    ...state, props, element, emitted, resize: () => resized(),
    event(xKey, extra = {}) {
      return {
        pointerType: 'touch', pointerId: 1, isPrimary: true, button: 0,
        clientX: state.chart.value.scaleX(xKey), clientY: 100, ...extra,
      }
    },
  }
}

const points = [
  { x: '09:30', y: -0.2, nav: 1.2345 },
  { x: '09:31', y: 0 },
  { x: '09:32', y: 0.3 },
  { x: '09:40', y: 0.5 },
]
const chart = mount(points)
assert.equal(chart.height.value, 240)
assert.equal(chart.plot.left, 48)
chart.element.clientHeight = 290
chart.resize()
assert.equal(chart.height.value, 290)
assert.equal(chart.plot.left, 68)

// 空数据移除绘图区后重新出现，必须重新绑定尺寸观察。
chart.containerRef.value = null
await nextTick()
chart.element.clientHeight = 240
chart.containerRef.value = chart.element
await nextTick()
assert.equal(chart.height.value, 240)
assert.equal(chart.plot.left, 48)

// 轻触即读，横向跟手，松手及触摸离开后保留；鼠标离开恢复最新值。
chart.handlePointerDown(chart.event('09:30'))
assert.deepEqual(chart.emitted, ['inspect'])
assert.equal(chart.activeHover.value.xKey, '09:30')
assert.equal(chart.activeHover.value.items[0].nav, 1.2345)
chart.handlePointerMove(chart.event('09:32'))
chart.handlePointerUp(chart.event('09:32'))
chart.handlePointerLeave(chart.event('09:32'))
chart.handlePointerLeave(chart.event('09:32', { pointerType: 'mouse' }))
assert.equal(chart.activeHover.value.xKey, '09:32')
chart.handlePointerMove(chart.event('09:40'))
assert.equal(chart.activeHover.value.xKey, '09:32', '松手后的触摸移动不得改读数')

// 纵向滚动的 pointercancel 撤销本次按下；第二指不接管选点。
chart.handlePointerDown(chart.event('09:30'))
chart.handlePointerMove(chart.event('09:31', { pointerId: 2, isPrimary: false }))
assert.equal(chart.activeHover.value.xKey, '09:30')
chart.handlePointerCancel(chart.event('09:30'))
assert.equal(chart.activeHover.value.xKey, '09:32')
chart.handlePointerMove(chart.event('09:31', { pointerType: 'mouse' }))
assert.equal(chart.activeHover.value.xKey, '09:31')
chart.handlePointerLeave(chart.event('09:31', { pointerType: 'mouse' }))
assert.equal(chart.activeHover.value, null)
assert.equal(chart.legendItems.value[0].point.value, 0.5)

// 轴区、单点、分时缺口与午休不产生读数，也不回填其他序列的前值。
chart.handlePointerDown(chart.event('09:30', { clientX: 10 }))
assert.equal(chart.activeHover.value, null)
chart.handlePointerDown(chart.event('09:36'))
assert.equal(chart.activeHover.value, null)
assert.equal(chart.legendItems.value[0].point, undefined)
chart.handlePointerUp(chart.event('09:36'))
const single = mount(points.slice(0, 1))
single.handlePointerDown(single.event('09:30'))
assert.equal(single.activeHover.value, null)
const comparison = mount(points, { series: [
  { name: '基金', points },
  { name: '基准', points: [points[0], points[3]] },
] })
comparison.handlePointerDown(comparison.event('09:32'))
assert.equal(comparison.activeHover.value.items.length, 1)
assert.equal(comparison.legendItems.value[1].point, undefined)
const cnA = mount([
  { x: '09:30', y: 0 }, { x: '11:30', y: 1 },
  { x: '13:00', y: 2 }, { x: '15:00', y: 3 },
], { sessionType: 'cn-a' })
cnA.handlePointerDown(cnA.event('12:00', { clientX: cnA.chart.value.layout.breakCenterX }))
assert.equal(cnA.activeHover.value, null)

// 跨日原始时间轴与金额格式保持，键盘可逐点读取；数据移除后不保留旧点。
const overnight = mount([
  { x: '2026-10-28 23:59:00', y: -1 },
  { x: '2026-10-29 00:00:00', y: 2 },
])
overnight.handlePointerDown(overnight.event('2026-10-29 00:00:00'))
assert.equal(overnight.formatTooltipTime(overnight.activeHover.value.xKey), '10-29 00:00')
const money = mount([
  { x: '2026-10-28', y: -12.34 }, { x: '2026-10-29', y: 20 },
], { xMode: 'date', valueKind: 'money' })
assert.equal(money.formatAxisValue(128.2), '+128.2')
assert.equal(money.formatAxisValue(1234.56), '+1.23K')
assert.equal(money.formatAxisValue(-12345678), '-12.3M')
assert.equal(money.formatValue(1234.56), '+1,234.56')
money.element.clientHeight = 290
money.resize()
assert.equal(money.formatAxisValue(1234.56), '+1,234.56')
money.handleKeydown({ key: 'Home', preventDefault() {} })
assert.equal(money.formatValue(money.activeHover.value.items[0].value), '-12.34')
money.handleKeydown({ key: 'ArrowRight', preventDefault() {} })
assert.equal(money.activeHover.value.xKey, '2026-10-29')
money.props.series = []
assert.equal(money.activeHover.value, null)

// 详情只让当前可读曲线占用手机吸顶区；单点、空数据与桌面保留原位图例。
const panel = readFileSync(new URL('../src/components/FundDetailPanel.vue', import.meta.url), 'utf8')
const targetSetup = panel.slice(panel.indexOf('const mainReadoutTarget'), panel.indexOf('const officialProfitDaysDesc'))
const readoutContext = createContext({
  computed,
  compact: ref(true), activeReadout: ref('main'), activeCurveTab: ref('intraday'),
  readoutHost: ref({ name: 'readout-host' }),
  intradaySeries: ref([{ points }]), performanceSeries: ref([]),
  officialProfitSeries: ref([{ points: points.slice(0, 1) }]),
})
runInContext(ts.transpile(targetSetup), readoutContext)
const targets = runInContext('({ mainReadoutTarget, officialReadoutTarget })', readoutContext)
assert.equal(targets.mainReadoutTarget.value, readoutContext.readoutHost.value)
assert.equal(targets.officialReadoutTarget.value, null)
readoutContext.activeReadout.value = 'official'
assert.equal(targets.mainReadoutTarget.value, null)
assert.equal(targets.officialReadoutTarget.value, null)
readoutContext.officialProfitSeries.value[0].points = points
assert.equal(targets.officialReadoutTarget.value, readoutContext.readoutHost.value)
readoutContext.activeReadout.value = 'main'
readoutContext.activeCurveTab.value = 'performance'
assert.equal(targets.mainReadoutTarget.value, null)
readoutContext.performanceSeries.value = [{ points }]
assert.equal(targets.mainReadoutTarget.value, readoutContext.readoutHost.value)
readoutContext.compact.value = false
assert.equal(targets.mainReadoutTarget.value, null)
assert.equal(targets.officialReadoutTarget.value, null)

console.log('Curve chart: sizing, input, retention, gaps, formatting and mobile readout ownership passed')
