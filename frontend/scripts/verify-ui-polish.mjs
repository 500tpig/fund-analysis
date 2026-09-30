import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import {
  parseBeijingTime,
  toTradingMinute,
  computeTradingAxisLayout,
  mapTradingMinuteToX,
  splitTradingSessions,
  getLatestValidPoint,
} from '../src/shared/tradingSession.ts'
import {
  formatCompactCurrency,
  formatCompactMoney,
  signedCompactMoney,
} from '../src/shared/formatters.ts'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const rootDir = path.resolve(__dirname, '..')

console.log('=== Step 1: Verifying Trading Session Logic ===')
assert.deepEqual(parseBeijingTime('13:48:00'), [13, 48, 0])

// 1. A-share 240-minute trading layout with 36px lunch break gap
const layout = computeTradingAxisLayout(720, 62, 18, 36)
assert.equal(layout.breakGap, 36)
assert.equal(layout.morningStartX, 62)
assert.equal(layout.morningEndX, 364)
assert.equal(layout.afternoonStartX, 400)
assert.equal(layout.afternoonEndX, 702)
assert.equal(layout.afternoonStartX - layout.morningEndX, 36, 'Break gap must be exactly 36px')

// Morning & Afternoon mappings
const m0930 = toTradingMinute('09:30')
const m1130 = toTradingMinute('11:30')
const m1300 = toTradingMinute('13:00')
const m1500 = toTradingMinute('15:00')
const m1348 = toTradingMinute('13:48')

assert.equal(mapTradingMinuteToX(m0930, layout), 62)
assert.equal(mapTradingMinuteToX(m1130, layout), 364)
assert.equal(mapTradingMinuteToX(m1300, layout), 400)
assert.equal(mapTradingMinuteToX(m1500, layout), 702)
assert.ok(mapTradingMinuteToX(m1348, layout) > 400 && mapTradingMinuteToX(m1348, layout) < 702)

// Lunch break filtering
const points = [
  { x: '09:30', y: 0.1 },
  { x: '11:30', y: -0.3 },
  { x: '11:45', y: -0.32 }, // break
  { x: '12:30', y: -0.33 }, // break
  { x: '13:00', y: -0.25 },
  { x: '13:48', y: -0.18, nav: 1.2345 },
]
const { morning, afternoon } = splitTradingSessions(points)
assert.equal(morning.length, 2)
assert.equal(afternoon.length, 2)
assert.equal(afternoon[1].x, '13:48')
assert.equal(afternoon[1].y, -0.18)

// Latest valid point
const latest = getLatestValidPoint(points)
assert.equal(latest?.x, '13:48')
assert.equal(latest?.y, -0.18)
assert.equal(latest?.nav, 1.2345)

console.log('✓ Trading session logic verified.')

assert.equal(formatCompactMoney(99_999_999), '99,999,999.00')
assert.equal(formatCompactMoney(123_456_789), '1.23亿')
assert.equal(formatCompactCurrency(123_456_789), '¥1.23亿')
assert.equal(signedCompactMoney(-1_234_567_890), '-12.35亿')

console.log('=== Step 2: Verifying main.css Color Tokens & Classes ===')
const mainCss = fs.readFileSync(path.join(rootDir, 'src/assets/main.css'), 'utf-8')

const requiredTokens = [
  '--bg-canvas: #0A0C0B;',
  '--bg-sidebar: #080A09;',
  '--bg-surface: #101310;',
  '--bg-surface-hover: #161A17;',
  '--border-subtle: #272C28;',
  '--border-strong: #353A36;',
  '--text-primary: #F1EFE9;',
  '--text-secondary: #AAA79F;',
  '--text-tertiary: #85827A;',
  '--text-disabled: #62655F;',
  '--brand-accent: #C9AE70;',
  '--status-warning: #D6924D;',
  '--status-live: #79A7BD;',
  '--market-up-strong: #E0787B;',
  '--market-up-muted: #C96669;',
  '--market-down-strong: #54C397;',
  '--market-down-muted: #429E7C;',
  '--chart-line-primary: #CDB16F;',
  '--chart-grid: rgba(126, 133, 126, 0.18);',
  '--chart-zero-line: rgba(188, 192, 184, 0.58);',
]

for (const token of requiredTokens) {
  assert.ok(mainCss.includes(token), `Missing token in main.css: ${token}`)
}

assert.ok(mainCss.includes('.currency-symbol'), 'Missing .currency-symbol class')
assert.ok(mainCss.includes('.value-rise-muted'), 'Missing .value-rise-muted class')
assert.ok(mainCss.includes('.value-fall-muted'), 'Missing .value-fall-muted class')
assert.ok(mainCss.includes('outline: 2px solid var(--brand-accent);'), 'Missing 2px focus outline')
assert.ok(mainCss.includes('background: var(--bg-sidebar);'), 'Missing rail background var(--bg-sidebar)')

console.log('✓ main.css tokens and classes verified.')

console.log('=== Step 3: Verifying theme/index.ts Token Single Source ===')
const themeTs = fs.readFileSync(path.join(rootDir, 'src/theme/index.ts'), 'utf-8')
assert.ok(themeTs.includes('var(--chart-line-primary)'), 'theme/index.ts must reference CSS variables')
assert.ok(themeTs.includes('var(--market-up-strong)'), 'theme/index.ts must reference CSS variables')
assert.ok(themeTs.includes('var(--market-down-strong)'), 'theme/index.ts must reference CSS variables')
console.log('✓ theme/index.ts verified.')

console.log('=== Step 4: Verifying Views and Components ===')
const fundsView = fs.readFileSync(path.join(rootDir, 'src/views/FundsView.vue'), 'utf-8')
const fundDetailDrawer = fs.readFileSync(path.join(rootDir, 'src/components/FundDetailDrawer.vue'), 'utf-8')
const holdingsView = fs.readFileSync(path.join(rootDir, 'src/views/HoldingsView.vue'), 'utf-8')
const chartVue = fs.readFileSync(path.join(rootDir, 'src/components/FundCurveChart.vue'), 'utf-8')
const heroMetricsVue = fs.readFileSync(path.join(rootDir, 'src/components/HeroMetrics.vue'), 'utf-8')
const dataStatusVue = fs.readFileSync(path.join(rootDir, 'src/shared/dataStatus.ts'), 'utf-8')

// FundsView
assert.ok(fundsView.includes("prefix: '¥'"), 'FundsView must use prefix: ¥ for total amount')
assert.ok(fundsView.includes("deltaToneLevel: 'muted'"), 'FundsView must use deltaToneLevel: muted')
assert.ok(fundsView.includes("tone-level=\"muted\""), 'FundsView holding profit must use tone-level=muted')
assert.ok(fundDetailDrawer.includes('分时走势'), 'FundDetailDrawer must show intraday curve source caption')
assert.ok(fundDetailDrawer.includes('持有收益'), 'FundDetailDrawer must show 持有收益 metric')
assert.ok(fundDetailDrawer.includes('今日预估'), 'FundDetailDrawer must show 今日预估 metric')
assert.ok(fundDetailDrawer.includes("activeCurveTab = ref<'performance' | 'intraday'>('intraday')"), 'FundDetailDrawer default tab must be intraday')
assert.ok(fundsView.includes('hasActiveFilterOrSort'), 'FundsView must support hasActiveFilterOrSort')
assert.ok(fundsView.includes('resetFiltersAndSort'), 'FundsView must support resetFiltersAndSort')
assert.ok(fundsView.includes("label: '有估值数据'"), 'FundsView must not describe coverage as accuracy')
assert.ok(fundDetailDrawer.includes('session-type="raw"'), 'Unknown fund types must keep the raw intraday axis')
assert.ok(fundDetailDrawer.includes('分时曲线来源：'), 'FundDetailDrawer must label the intraday curve source')

// HoldingsView
assert.ok(holdingsView.includes("prefix: '¥'"), 'HoldingsView must use prefix: ¥ for total amount')
assert.ok(holdingsView.includes("deltaToneLevel: 'muted'"), 'HoldingsView must use deltaToneLevel: muted')
assert.ok(holdingsView.includes("tone-level=\"muted\""), 'HoldingsView holding profit must use tone-level=muted')
assert.ok(holdingsView.includes('hasActiveFilterOrSort'), 'HoldingsView must support hasActiveFilterOrSort')

// FundCurveChart
assert.ok(chartVue.includes('computeTradingAxisLayout'), 'Chart must use computeTradingAxisLayout')
assert.ok(chartVue.includes('curve-chart__break-zone'), 'Chart must render lunch break zone')
assert.ok(chartVue.includes('curve-chart__break-border'), 'Chart must render lunch break borders')
assert.ok(chartVue.includes('morningPolyline'), 'Chart must split morning and afternoon polylines')
assert.ok(chartVue.includes('seriesDisplayLabel'), 'Chart must use seriesDisplayLabel for dynamic legend')
assert.ok(chartVue.includes('item.nav'), 'Chart tooltip must support nav')
assert.ok(chartVue.includes("sessionType: 'raw'"), 'Chart must default unknown sessions to the raw axis')

// HeroMetrics
assert.ok(heroMetricsVue.includes('metric-strip__item--featured'), 'HeroMetrics must mark its featured item')
assert.ok(mainCss.includes('grid-column: 1 / -1;'), 'Featured mobile metric must span the full row')

// dataStatus.ts
assert.ok(dataStatusVue.includes('最新官方净值日'), 'dataStatus.ts must use 最新官方净值日')
assert.ok(dataStatusVue.includes('各基金实际净值日以表格行内数据为准'), 'dataStatus.ts must have tooltip hint')

console.log('✓ All Views, Components, and Formatters verified.')
console.log('=== ALL VERIFICATIONS PASSED ===')
