<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { AlertTriangle, Copy } from '@lucide/vue'

import {
  fetchFundIntradayCurve,
  fetchFundOfficialProfit,
  fetchFundPerformance,
} from '@/api/client'
import {
  PERFORMANCE_BENCHMARK_OPTIONS,
  PERFORMANCE_BENCHMARKS,
  type FundPerformanceResponse,
  type HoldingRow,
  type IntradayCurveResponse,
  type OfficialDailyProfitResponse,
  type SelectedBenchmarkCode,
} from '@/api/types'
import DataStatus from '@/components/DataStatus.vue'
import EmptyState from '@/components/EmptyState.vue'
import FundCurveChart from '@/components/FundCurveChart.vue'
import Button from '@/components/ui/Button.vue'
import Drawer from '@/components/ui/Drawer.vue'
import { sourceDisplayName } from '@/shared/dataStatus'
import {
  formatDateTime,
  formatMoney,
  formatMonthDay,
  formatNav,
  formatPercent,
  signedMoney,
  valueStateClass,
} from '@/shared/formatters'
import { kindLabel, measurementReasons, rowEstimateView, rowTodayDisplay } from '@/shared/todayProfit'
import { chartColors } from '@/theme'

interface Props {
  show: boolean
  fund: HoldingRow | null
}

const props = withDefaults(defineProps<Props>(), {
  show: false,
  fund: null,
})

const emit = defineEmits<{
  'update:show': [value: boolean]
  close: []
  'copy-code': [code: string]
}>()

const selectedFund = computed(() => props.fund)
const activeCurveTab = ref<'performance' | 'intraday'>('intraday')
const performanceRange = ref<FundPerformanceResponse['range']>('3m')
const selectedBenchmark = ref<SelectedBenchmarkCode>('')
const performance = ref<FundPerformanceResponse | null>(null)
const intradayCurve = ref<IntradayCurveResponse | null>(null)
const performanceLoading = ref(false)
const performanceRefreshing = ref(false)
const intradayLoading = ref(false)
const performanceError = ref<string | null>(null)
const intradayError = ref<string | null>(null)
const performanceCache = new Map<string, FundPerformanceResponse>()
const officialProfit = ref<OfficialDailyProfitResponse | null>(null)
const officialProfitLoading = ref(false)
const officialProfitError = ref<string | null>(null)

const performanceRanges: Array<{ value: FundPerformanceResponse['range']; label: string }> = [
  { value: '1m', label: '近1月' },
  { value: '3m', label: '近3月' },
  { value: '6m', label: '近6月' },
  { value: '1y', label: '近1年' },
  { value: '3y', label: '近3年' },
]

const performanceSeries = computed(() => {
  if (!performance.value) return []
  const series = [
    {
      name: '本基金',
      color: chartColors.fund,
      points: performance.value.points.map((point) => ({ x: point.date, y: point.return_pct })),
    },
  ]
  if (performance.value.benchmark) {
    series.push({
      name: performance.value.benchmark.name,
      color: chartColors.benchmark,
      points: performance.value.benchmark.points.map((point) => ({
        x: point.date,
        y: point.return_pct,
      })),
    })
  }
  return series
})

const intradaySeries = computed(() => {
  if (!intradayCurve.value?.points.length) return []
  return [
    {
      name: '分时估算',
      sourceLabel: sourceDisplayName(intradayCurve.value.source, true),
      color: chartColors.fund,
      points: intradayCurve.value.points.map((point) => ({
        x: point.datetime || point.time,
        y: point.estimated_return_pct,
        nav: point.estimated_nav,
      })),
    },
  ]
})

const intradayDateLabel = computed(() => {
  if (!intradayCurve.value) return ''
  const dates = Array.from(
    new Set(intradayCurve.value.points.map((p) => p.date).filter((d): d is string => Boolean(d)))
  ).sort()
  if (dates.length > 1) return `${dates[0]} ~ ${dates[dates.length - 1]}`
  if (dates.length === 1) return dates[0]
  return intradayCurve.value.date
})

const officialProfitSeries = computed(() => {
  const items = officialProfit.value?.items ?? []
  if (!items.length) return []
  return [
    {
      name: '已记录日期合计',
      color: chartColors.fund,
      points: items.map((item) => ({
        x: item.nav_date,
        y: item.cumulative_profit,
      })),
    },
  ]
})

const officialProfitDaysDesc = computed(() => (
  [...(officialProfit.value?.items ?? [])].reverse()
))

const todayDisplay = computed(() => (
  selectedFund.value ? rowTodayDisplay(selectedFund.value) : null
))

const estimateView = computed(() => (
  selectedFund.value ? rowEstimateView(selectedFund.value) : null
))

function copyCode(code: string): void {
  emit('copy-code', code)
}

async function loadPerformance(): Promise<void> {
  const code = selectedFund.value?.code
  if (!code) return
  const range = performanceRange.value
  const benchmark = selectedBenchmark.value
  const key = `${code}:${range}:${benchmark}`
  const cached = performanceCache.get(key)
  if (cached) {
    performance.value = cached
    performanceError.value = null
    return
  }
  const hasChart = performance.value !== null
  if (hasChart) performanceRefreshing.value = true
  else performanceLoading.value = true
  performanceError.value = null
  try {
    const payload = await fetchFundPerformance(code, range, benchmark)
    performanceCache.set(key, payload)
    if (
      selectedFund.value?.code === code
      && performanceRange.value === range
      && selectedBenchmark.value === benchmark
    ) {
      performance.value = payload
    }
  } catch (caught) {
    if (
      selectedFund.value?.code === code
      && performanceRange.value === range
      && selectedBenchmark.value === benchmark
    ) {
      performanceError.value = caught instanceof Error ? caught.message : '加载历史业绩失败'
    }
  } finally {
    if (
      selectedFund.value?.code === code
      && performanceRange.value === range
      && selectedBenchmark.value === benchmark
    ) {
      performanceLoading.value = false
      performanceRefreshing.value = false
    }
  }
}

async function loadIntradayCurve(): Promise<void> {
  const code = selectedFund.value?.code
  if (!code) return
  intradayLoading.value = true
  intradayError.value = null
  try {
    const payload = await fetchFundIntradayCurve(code)
    if (selectedFund.value?.code !== code) return
    intradayCurve.value = payload
  } catch (caught) {
    if (selectedFund.value?.code !== code) return
    intradayError.value = caught instanceof Error ? caught.message : '加载盘中走势失败'
  } finally {
    if (selectedFund.value?.code === code) intradayLoading.value = false
  }
}

async function loadOfficialProfit(): Promise<void> {
  const row = selectedFund.value
  const code = row?.code
  if (!code || !row.is_fund_asset) {
    officialProfit.value = null
    officialProfitError.value = null
    officialProfitLoading.value = false
    return
  }
  officialProfitLoading.value = true
  officialProfitError.value = null
  try {
    const payload = await fetchFundOfficialProfit(code)
    if (selectedFund.value?.code === code) officialProfit.value = payload
  } catch (caught) {
    if (selectedFund.value?.code === code) {
      officialProfit.value = null
      officialProfitError.value = caught instanceof Error ? caught.message : '加载官方日收益失败'
    }
  } finally {
    if (selectedFund.value?.code === code) officialProfitLoading.value = false
  }
}

function formatDisclosureRule(rule?: string | null): string {
  if (!rule) return '规则待核验'
  if (rule === 'cn_same_day_night') return '境内当日夜间披露（19:00 - 24:00）'
  if (rule === 'qdii_next_day_night') return 'QDII 次日夜间披露（T+1 19:00 - 24:00）'
  if (rule === 'cash_daily') return '按日收益结算'
  if (rule === 'unknown_rule') return '规则待核验'
  return rule
}

function defaultBenchmarkForRow(row: HoldingRow): SelectedBenchmarkCode {
  const mapped = row.benchmark_code?.trim() || ''
  if (PERFORMANCE_BENCHMARKS.some((item) => item.code === mapped)) {
    return mapped as SelectedBenchmarkCode
  }
  return ''
}

function resetDetail(): void {
  performance.value = null
  intradayCurve.value = null
  officialProfit.value = null
  performanceError.value = null
  intradayError.value = null
  officialProfitError.value = null
  performanceLoading.value = false
  performanceRefreshing.value = false
  intradayLoading.value = false
  officialProfitLoading.value = false
}

function openDetail(row: HoldingRow): void {
  selectedBenchmark.value = defaultBenchmarkForRow(row)
  activeCurveTab.value = 'intraday'
  resetDetail()
  if (row.is_fund_asset) {
    void loadIntradayCurve()
    void loadOfficialProfit()
  }
}

watch(
  () => [props.show, props.fund?.code] as const,
  ([show, code], previous) => {
    if (!show || !props.fund) {
      if (!show) resetDetail()
      return
    }
    const previousShow = previous?.[0]
    const previousCode = previous?.[1]
    if (!previousShow || code !== previousCode) openDetail(props.fund)
  },
  { immediate: true },
)

watch(
  () => props.fund,
  (fund, previous) => {
    if (!props.show || !fund?.is_fund_asset || !previous) return
    if (previous.code !== fund.code || previous === fund) return
    void loadOfficialProfit()
  },
)

function selectPerformanceRange(range: FundPerformanceResponse['range']): void {
  if (performanceRange.value === range) return
  performanceRange.value = range
  void loadPerformance()
}

function selectBenchmark(code: string): void {
  const next = code as SelectedBenchmarkCode
  if (selectedBenchmark.value === next) return
  selectedBenchmark.value = next
  void loadPerformance()
}

function selectCurveTab(value: string): void {
  activeCurveTab.value = value === 'performance' ? 'performance' : 'intraday'
  if (activeCurveTab.value === 'performance' && performance.value === null) {
    void loadPerformance()
  } else if (activeCurveTab.value === 'intraday' && intradayCurve.value === null) {
    void loadIntradayCurve()
  }
}
</script>

<template>
  <Drawer
    :show="show"
    :title="selectedFund ? selectedFund.name : '基金明细'"
    width="clamp(620px, 42vw, 760px)"
    @update:show="emit('update:show', $event)"
    @close="emit('close')"
  >
    <template
      v-if="selectedFund"
      #header
    >
      <div class="min-w-0 pr-4">
        <div class="flex items-start gap-3">
          <h2
            class="min-w-0 truncate text-[20px] font-medium leading-7"
            :title="selectedFund.name"
          >
            {{ selectedFund.name }}
          </h2>
          <Button
            v-if="!selectedFund.is_cash_asset"
            variant="ghost"
            size="icon"
            aria-label="复制基金代码"
            @click="copyCode(selectedFund.code)"
          >
            <Copy
              class="h-3.5 w-3.5"
              :stroke-width="1.75"
            />
          </Button>
        </div>
        <DataStatus
          class="mt-1"
          :row="selectedFund"
        />
      </div>
    </template>

    <div
      v-if="selectedFund"
      class="flex flex-col gap-6"
    >
      <div class="metric-band">
        <div class="metric-band__item">
          <div class="metric-band__label">
            持仓金额
          </div>
          <div class="metric-band__value">
            <span class="currency-symbol">¥</span>{{ formatMoney(selectedFund.amount) }}
          </div>
          <div class="metric-band__sub">
            <span v-if="selectedFund.amount_nav_date">金额基准日 {{ formatMonthDay(selectedFund.amount_nav_date) }}</span>
            <span v-else>金额基准日未记录</span>
          </div>
        </div>
        <div class="metric-band__item">
          <div class="metric-band__label">
            持有收益
          </div>
          <div
            class="metric-band__value"
            :class="valueStateClass(selectedFund.holding_profit_state, 'muted')"
          >
            {{ signedMoney(selectedFund.holding_profit) }}
          </div>
          <div
            class="metric-band__sub"
            :class="valueStateClass(selectedFund.holding_profit_rate_state, 'muted')"
          >
            <span>{{ formatPercent(selectedFund.holding_profit_rate) }}</span>
            <span v-if="selectedFund.holding_profit_conflict"> · 口径冲突</span>
          </div>
        </div>
        <div class="metric-band__item">
          <div class="metric-band__label">
            今日预估 / 官方测算
          </div>
          <div
            class="metric-band__value"
            :class="valueStateClass(todayDisplay && todayDisplay.amount != null && todayDisplay.amount !== 0 ? (todayDisplay.amount > 0 ? 'rise' : 'fall') : 'muted', 'strong')"
          >
            {{ signedMoney(todayDisplay?.amount) }}
          </div>
          <div class="metric-band__sub">
            <span>{{ todayDisplay?.label }}</span>
          </div>
        </div>
        <div class="metric-band__item">
          <div class="metric-band__label">
            {{ estimateView?.overseas ? '海外最近估值' : '净值估算' }}
          </div>
          <div class="metric-band__value">
            {{ formatNav(estimateView?.nav) }}
          </div>
          <div class="metric-band__sub">
            <span>官方 {{ formatNav(selectedFund.latest_nav) }}</span>
          </div>
        </div>
      </div>

      <div
        v-if="selectedFund.holding_profit_conflict"
        class="alert-banner alert-banner--warn"
      >
        <AlertTriangle
          class="h-4 w-4 shrink-0"
          :stroke-width="1.75"
        />
        <span>持有收益按记录成本测算，持有收益率来自导入值，两者口径不一致，未改成本。</span>
      </div>
      <div
        v-if="selectedFund.estimated_warning"
        class="alert-banner alert-banner--warn"
      >
        <AlertTriangle
          class="h-4 w-4 shrink-0"
          :stroke-width="1.75"
        />
        <span>{{ selectedFund.estimated_warning }}</span>
      </div>

      <section
        v-if="selectedFund.is_fund_asset"
        class="flex flex-col gap-4"
      >
        <div class="flex items-center justify-between gap-3">
          <div class="segmented-control">
            <button
              type="button"
              :class="['segmented-item', activeCurveTab === 'intraday' ? 'segmented-item--active' : '']"
              @click="selectCurveTab('intraday')"
            >
              盘中分时
            </button>
            <button
              type="button"
              :class="['segmented-item', activeCurveTab === 'performance' ? 'segmented-item--active' : '']"
              @click="selectCurveTab('performance')"
            >
              历史业绩
            </button>
          </div>
        </div>

        <div
          v-if="activeCurveTab === 'intraday'"
          class="space-y-3"
        >
          <div
            v-if="intradayLoading"
            class="flex h-56 items-center justify-center gap-2 text-[13px] text-(--text-tertiary)"
          >
            <span class="h-3.5 w-3.5 animate-spin rounded-full border-2 border-(--brand-accent) border-t-transparent" />
            拉取盘中分时
          </div>
          <div
            v-else-if="intradayError"
            class="alert-banner alert-banner--error"
          >
            {{ intradayError }}
          </div>
          <FundCurveChart
            v-else-if="intradaySeries.length"
            :series="intradaySeries"
            x-mode="time"
            session-type="raw"
          />
          <EmptyState
            v-else
            title="今日暂无分钟走势数据"
          />
          <p
            v-if="intradayCurve"
            class="flex justify-between gap-4 text-[12px] text-(--text-secondary)"
          >
            <span>{{ intradayDateLabel }} 分时走势</span>
            <span>分时曲线来源：{{ sourceDisplayName(intradayCurve.source, true) }}</span>
          </p>
        </div>

        <div
          v-else
          class="space-y-3"
        >
          <div
            v-if="performanceLoading"
            class="flex h-56 items-center justify-center gap-2 text-[13px] text-(--text-tertiary)"
          >
            <span class="h-3.5 w-3.5 animate-spin rounded-full border-2 border-(--brand-accent) border-t-transparent" />
            加载历史业绩
          </div>
          <div
            v-else-if="performanceError && !performanceSeries.length"
            class="alert-banner alert-banner--error"
          >
            {{ performanceError }}
          </div>
          <FundCurveChart
            v-else-if="performanceSeries.length"
            :series="performanceSeries"
            x-mode="date"
            :benchmark-options="PERFORMANCE_BENCHMARK_OPTIONS"
            :benchmark-code="selectedBenchmark"
            :refreshing="performanceRefreshing"
            @update:benchmark-code="selectBenchmark"
          >
            <template #toolbar-prefix>
              <div class="text-filters">
                <button
                  v-for="item in performanceRanges"
                  :key="item.value"
                  type="button"
                  class="text-filter"
                  :class="performanceRange === item.value ? 'is-active' : ''"
                  @click="selectPerformanceRange(item.value)"
                >
                  {{ item.label }}
                </button>
              </div>
            </template>
          </FundCurveChart>
          <EmptyState
            v-else
            title="暂无历史业绩数据"
          />
          <p
            v-if="performance"
            class="flex justify-between gap-4 text-[12px] text-(--text-secondary)"
          >
            <span>{{ formatMonthDay(performance.start_date) }} 至 {{ formatMonthDay(performance.end_date) }}</span>
            <span>{{ sourceDisplayName(performance.source) }}</span>
          </p>
        </div>
      </section>

      <section class="compare-sheet">
        <div class="compare-sheet__col">
          <h3>官方数据</h3>
          <div class="compare-sheet__row">
            <span class="compare-sheet__label">净值</span>
            <span class="compare-sheet__value">{{ formatNav(selectedFund.latest_nav) }}</span>
          </div>
          <div class="compare-sheet__row">
            <span class="compare-sheet__label">日涨跌</span>
            <span
              class="compare-sheet__value"
              :class="valueStateClass(selectedFund.daily_return_state)"
            >{{ formatPercent(selectedFund.daily_return_pct) }}</span>
          </div>
          <div class="compare-sheet__row">
            <span class="compare-sheet__label">官方情景</span>
            <span
              class="compare-sheet__value"
              :class="valueStateClass(selectedFund.official_scenario?.amount == null || selectedFund.official_scenario.amount === 0 ? 'muted' : selectedFund.official_scenario.amount > 0 ? 'rise' : 'fall')"
            >{{ signedMoney(selectedFund.official_scenario?.amount ?? null) }}</span>
          </div>
          <div class="compare-sheet__row">
            <span class="compare-sheet__label">导入收益</span>
            <span class="compare-sheet__value">{{ selectedFund.imported_daily_profit ? signedMoney(selectedFund.imported_daily_profit.amount) : '—' }}</span>
          </div>
          <div class="compare-sheet__row">
            <span class="compare-sheet__label">净值日期</span>
            <span class="compare-sheet__value">{{ formatMonthDay(selectedFund.nav_date, '未记录') }}</span>
          </div>
          <div
            v-if="selectedFund.is_fund_asset"
            class="compare-sheet__row"
          >
            <span class="compare-sheet__label">下次披露</span>
            <span class="compare-sheet__value">{{ selectedFund.next_disclosure_date ? `${formatMonthDay(selectedFund.next_disclosure_date)} 晚间` : '—' }}</span>
          </div>
          <div class="compare-sheet__row">
            <span class="compare-sheet__label">同步时间</span>
            <span class="compare-sheet__value">{{ formatDateTime(selectedFund.fetched_at, '未记录') }}</span>
          </div>
        </div>
        <div class="compare-sheet__col">
          <h3>{{ estimateView?.overseas ? '海外最近估值' : '盘中估算' }}</h3>
          <div class="compare-sheet__row">
            <span class="compare-sheet__label">估值</span>
            <span class="compare-sheet__value">{{ formatNav(estimateView?.nav) }}</span>
          </div>
          <div class="compare-sheet__row">
            <span class="compare-sheet__label">估算涨跌</span>
            <span
              class="compare-sheet__value"
              :class="valueStateClass(estimateView?.state)"
            >{{ formatPercent(estimateView?.pct) }}</span>
          </div>
          <div
            v-if="estimateView?.overseas"
            class="compare-sheet__row"
          >
            <span class="compare-sheet__label">涨跌基准</span>
            <span class="compare-sheet__value">{{ estimateView.basisDate ? `${formatMonthDay(estimateView.basisDate)} 净值 ${formatNav(estimateView.basisNav)}` : '未知' }}{{ estimateView.conflict ? ' · 冲突' : '' }}</span>
          </div>
          <div
            v-else
            class="compare-sheet__row"
          >
            <span class="compare-sheet__label">预估情景</span>
            <span
              class="compare-sheet__value"
              :class="valueStateClass(selectedFund.estimate_scenario?.amount == null || selectedFund.estimate_scenario.amount === 0 ? 'muted' : selectedFund.estimate_scenario.amount > 0 ? 'rise' : 'fall')"
            >{{ signedMoney(selectedFund.estimate_scenario?.amount ?? null) }}</span>
          </div>
          <div class="compare-sheet__row">
            <span class="compare-sheet__label">来源时间</span>
            <span class="compare-sheet__value">{{ formatDateTime(estimateView?.at, '未记录') }}</span>
          </div>
          <div class="compare-sheet__row">
            <span class="compare-sheet__label">数据来源</span>
            <span class="compare-sheet__value">{{ sourceDisplayName(estimateView?.source, true) }}</span>
          </div>
        </div>
      </section>
      <p
        v-if="selectedFund.imported_daily_profit"
        class="text-[12px] text-(--text-secondary)"
      >
        {{ selectedFund.imported_daily_profit.label }}：{{ signedMoney(selectedFund.imported_daily_profit.amount) }}，不计入今日收益。
      </p>
      <p class="text-[12px] text-(--text-secondary)">
        可核对收益：{{ signedMoney(selectedFund.today_profit.amount) }} · {{ kindLabel(selectedFund.today_profit.kind, selectedFund.today_profit.included) }}
      </p>
      <p
        v-if="measurementReasons(selectedFund.today_profit).length"
        class="text-[12px] text-(--text-secondary)"
      >
        {{ kindLabel(selectedFund.today_profit.kind, selectedFund.today_profit.included) }}：{{ measurementReasons(selectedFund.today_profit).join('；') }}
      </p>

      <section
        v-if="selectedFund.is_fund_asset"
        class="profit-section"
      >
        <div class="profit-section__header">
          <h3 class="profit-section__title">
            已记录官方测算
          </h3>
        </div>

        <template v-if="!officialProfitLoading && officialProfit && officialProfit.record_count > 0 && officialProfit.latest">
          <p class="profit-section__empty">
            {{ officialProfit.basis_note }}。已记录日期合计，不补齐未记录日期。
          </p>
          <div class="profit-section__metrics">
            <div class="profit-section__metric">
              <div class="profit-section__label">
                最近记录日 · {{ officialProfit.latest.nav_date }}
              </div>
              <div
                class="profit-section__value"
                :class="valueStateClass(officialProfit.latest.profit_amount > 0 ? 'rise' : officialProfit.latest.profit_amount < 0 ? 'fall' : 'muted')"
              >
                {{ signedMoney(officialProfit.latest.profit_amount) }}
              </div>
            </div>
            <div class="profit-section__metric">
              <div class="profit-section__label">
                已记录日期合计
              </div>
              <div
                class="profit-section__value"
                :class="valueStateClass(officialProfit.cumulative_profit > 0 ? 'rise' : officialProfit.cumulative_profit < 0 ? 'fall' : 'muted')"
              >
                {{ signedMoney(officialProfit.cumulative_profit) }}
              </div>
            </div>
          </div>
          <FundCurveChart
            v-if="officialProfitSeries.length"
            :series="officialProfitSeries"
            x-mode="date"
            value-kind="money"
          />
          <p
            v-if="officialProfit.observed_dates.length"
            class="profit-section__empty"
          >
            观测日期 {{ officialProfit.observed_dates.join('、') }}
            <template v-if="officialProfit.completeness === 'gapped_recorded_dates'">
              ；日期有缺口，不是连续完整收益区间
            </template>
          </p>
          <div class="profit-section__list">
            <div
              v-for="item in officialProfitDaysDesc"
              :key="item.nav_date"
              class="profit-section__row"
            >
              <span class="profit-section__date">{{ item.nav_date }}</span>
              <span
                class="profit-section__amount"
                :class="valueStateClass(item.profit_amount > 0 ? 'rise' : item.profit_amount < 0 ? 'fall' : 'muted')"
              >{{ signedMoney(item.profit_amount) }}</span>
            </div>
          </div>
        </template>
        <p
          v-else-if="!officialProfitLoading"
          class="profit-section__empty"
        >
          {{ officialProfitError || '暂无官方日收益记录' }}
        </p>
      </section>

      <section
        v-if="selectedFund.is_fund_asset"
        class="accuracy-section"
      >
        <div class="accuracy-section__header">
          <h3 class="accuracy-section__title">
            历史估值误差
          </h3>
          <span
            v-if="selectedFund.accuracy?.sample_count"
            class="accuracy-section__meta"
          >
            {{ sourceDisplayName(selectedFund.accuracy.source, true) }} · 近 {{ selectedFund.accuracy.window }} 个对账样本
          </span>
        </div>

        <template v-if="selectedFund.accuracy && selectedFund.accuracy.mae_pct_points != null && selectedFund.accuracy.bias_pct_points != null">
          <div class="accuracy-section__grid">
            <div class="accuracy-section__item">
              <span class="accuracy-section__label">平均绝对误差</span>
              <span class="accuracy-section__value">
                {{ selectedFund.accuracy.mae_pct_points.toFixed(2) }} 百分点
              </span>
            </div>
            <div class="accuracy-section__item">
              <span class="accuracy-section__label">平均偏差</span>
              <span
                class="accuracy-section__value"
                :class="selectedFund.accuracy.bias_pct_points > 0 ? 'value-rise' : selectedFund.accuracy.bias_pct_points < 0 ? 'value-fall' : ''"
              >
                {{ selectedFund.accuracy.bias_pct_points >= 0 ? '+' : '' }}{{ selectedFund.accuracy.bias_pct_points.toFixed(2) }} 百分点
              </span>
            </div>
            <div class="accuracy-section__item">
              <span class="accuracy-section__label">最大绝对误差</span>
              <span class="accuracy-section__value">
                {{ selectedFund.accuracy.max_abs_error_pct_points != null ? selectedFund.accuracy.max_abs_error_pct_points.toFixed(2) + ' 百分点' : '—' }}
              </span>
            </div>
            <div class="accuracy-section__item">
              <span class="accuracy-section__label">有效样本数</span>
              <span class="accuracy-section__value">
                {{ selectedFund.accuracy.sample_count }} 条
              </span>
            </div>
          </div>
        </template>
        <template v-else-if="selectedFund.accuracy && selectedFund.accuracy.sample_count > 0">
          <p class="accuracy-section__empty">
            对账样本积累中（已记录 {{ selectedFund.accuracy.sample_count }}/3 条）
          </p>
        </template>
        <template v-else-if="selectedFund.accuracy && (selectedFund.accuracy.pending_count ?? 0) > 0">
          <p class="accuracy-section__empty">
            待对账（已记录估值样本，等待匹配官方净值日）
          </p>
        </template>
        <template v-else>
          <p class="accuracy-section__empty">
            暂无对账数据
          </p>
        </template>
      </section>

      <section
        v-if="selectedFund.is_fund_asset"
        class="fund-profile-section"
      >
        <div class="fund-profile-section__header">
          <h3 class="fund-profile-section__title">
            基础档案与披露规则
          </h3>
          <span
            v-if="selectedFund.verified_at"
            class="fund-profile-section__meta"
          >
            已核验 · {{ selectedFund.verified_at }}
          </span>
        </div>

        <div class="fund-profile-section__grid">
          <div class="fund-profile-section__item">
            <span class="fund-profile-section__label">基金类型</span>
            <span class="fund-profile-section__value">
              {{ selectedFund.fund_type_label || selectedFund.asset_class || '未分类' }}
            </span>
          </div>
          <div class="fund-profile-section__item">
            <span class="fund-profile-section__label">估值市场</span>
            <span class="fund-profile-section__value">
              {{ selectedFund.valuation_market && selectedFund.market_calendar ? `${selectedFund.valuation_market}（${selectedFund.market_calendar}）` : '未核验' }}
            </span>
          </div>
          <div class="fund-profile-section__item">
            <span class="fund-profile-section__label">净值披露规则</span>
            <span class="fund-profile-section__value">
              {{ formatDisclosureRule(selectedFund.nav_disclosure_rule) }}
            </span>
          </div>
          <div class="fund-profile-section__item">
            <span class="fund-profile-section__label">对比基准</span>
            <span
              class="fund-profile-section__value truncate"
              :title="selectedFund.benchmark_name || '暂无跟踪基准'"
            >
              {{ selectedFund.benchmark_name || '暂无跟踪基准' }}
            </span>
          </div>
        </div>
        <p
          v-if="selectedFund.source_url"
          class="mt-2 text-[12px] text-(--text-tertiary)"
        >
          档案来源：
          <a
            :href="selectedFund.source_url"
            target="_blank"
            rel="noopener noreferrer"
            class="underline hover:text-(--brand-accent)"
          >
            {{ selectedFund.source_url }}
          </a>
        </p>
      </section>
    </div>
  </Drawer>
</template>
