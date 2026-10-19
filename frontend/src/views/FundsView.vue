<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import {
  RefreshCw,
  Copy,
  Check,
  AlertCircle,
  Search,
  X,
  Images,
} from '@lucide/vue'

import {
  fetchFunds,
  refreshFunds,
} from '@/api/client'
import {
  type FundsRefreshResponse,
  type FundsResponse,
  type HoldingRow,
} from '@/api/types'
import DataToolbar from '@/components/DataToolbar.vue'
import EmptyState from '@/components/EmptyState.vue'
import FundDetailDrawer from '@/components/FundDetailDrawer.vue'
import FundDetailPanel from '@/components/FundDetailPanel.vue'
import FundIdentityHeader from '@/components/FundIdentityHeader.vue'
import HeroMetrics, { type HeroMetric } from '@/components/HeroMetrics.vue'
import MarketQuoteStrip from '@/components/MarketQuoteStrip.vue'
import PortfolioAnalysisPanel from '@/components/PortfolioAnalysisPanel.vue'
import MarketValue from '@/components/MarketValue.vue'
import SortableTh from '@/components/SortableTh.vue'
import WorkbenchPageHeader from '@/components/WorkbenchPageHeader.vue'
import Button from '@/components/ui/Button.vue'
import Input from '@/components/ui/Input.vue'
import {
  inventoryLabel,
  matchesIntradayFilter,
  resolveIntradayStatus,
  resolveOfficialStatus,
  sourceDisplayName,
  type IntradayFilter,
} from '@/shared/dataStatus'
import {
  formatClock,
  formatCompactMoney,
  formatMoney,
  formatDateTime,
  formatMonthDay,
  formatPercent,
  signedMoney,
  signedCompactMoney,
  signedState,
  valueStateClass,
} from '@/shared/formatters'
import {
  CHECK_TICK_MS,
  MAX_AUTO_FAILURES,
  describeSourceUpdates,
  diffSourceUpdates,
  isFailedRun,
  pollingInterval,
  shouldAutoRefresh,
  shouldInitialBackfill,
} from '@/shared/refreshPolicy'
import { type RowSessionProfit, rowEstimateView, rowSessionProfitView, todayHeroCopy } from '@/shared/todayProfit'
import { useMessage } from '@/shared/toast'
import { isEditableTarget, useHotkeys } from '@/shared/hotkeys'
import { compareNullableNumbers } from '@/shared/sorting'

type SortField =
  | 'name'
  | 'amount'
  | 'estimated_return_pct'
  | 'today_profit'
  | 'holding_profit'

type SortOrder = 'asc' | 'desc' | null

const funds = ref<FundsResponse | null>(null)
const loading = ref(true)
const refreshing = ref(false)
const error = ref<string | null>(null)
type StatusPart = { text: string; tone: 'neutral' | 'warning' }

// 页首状态按片段保留语义，失败片段单独着色；纯文本由 parts 拼回。
const refreshNoteParts = ref<StatusPart[] | null>(null)
const refreshNote = computed(() => refreshNoteParts.value?.map((part) => part.text).join(' · ') ?? null)
const autoFailures = ref(0)
let refreshInFlight = false
let syncInFlight = false
let pageActive = false
let initialBackfillPending = false
let lastAttemptAt: number | null = null
let loadedAt = 0
let checkTimer: ReturnType<typeof setInterval> | null = null

const searchQuery = ref('')
const statusFilter = ref<IntradayFilter>('all')
const mobileSearchOpen = ref(false)

const selectedFund = ref<HoldingRow | null>(null)
const drawerOpen = ref(false)

// ≥1440px 使用列表 + 常驻详情分栏，抽屉只在窄屏使用；两处共用详情内容。
const wideSplit = ref(false)
let wideSplitMedia: MediaQueryList | null = null

function onWideSplitChange(matches: boolean): void {
  wideSplit.value = matches
  if (matches) drawerOpen.value = false
}

function onWideSplitMediaEvent(event: MediaQueryListEvent): void {
  onWideSplitChange(event.matches)
}

const copiedCode = ref<string | null>(null)
const tablePressing = ref(false)

const router = useRouter()
// 区分「组合本身为空」与「筛选后无匹配」，前者给出录入入口。
const hasFundRows = computed(() => (funds.value?.rows.length ?? 0) > 0)

const sortField = ref<SortField | null>(null)
const sortOrder = ref<SortOrder>(null)
const message = useMessage()

const filterCounts = computed(() => {
  const rows = funds.value?.rows ?? []
  const fundRows = rows.filter((r) => !r.is_cash_asset)
  const cashRows = rows.filter((r) => r.is_cash_asset)
  const delayed = fundRows.filter((r) => matchesIntradayFilter(r, 'delayed')).length
  const none = fundRows.filter((r) => matchesIntradayFilter(r, 'none')).length
  const available = fundRows.filter((r) => matchesIntradayFilter(r, 'available')).length
  return {
    all: cashRows.length > 0 ? rows.length : fundRows.length,
    available,
    delayed,
    none,
  }
})

const isLatestOfficial = computed(() => funds.value?.summary.today.display_session?.mode === 'latest_official')

watch(isLatestOfficial, (latest) => {
  if (latest && statusFilter.value !== 'all') {
    statusFilter.value = 'all'
  }
})

const filterOptions = computed<Array<{ key: IntradayFilter; label: string; count: number }>>(() => {
  if (isLatestOfficial.value) {
    return [{ key: 'all', label: '全部', count: filterCounts.value.all }]
  }
  return [
    { key: 'all', label: '全部', count: filterCounts.value.all },
    { key: 'available', label: '盘中可用', count: filterCounts.value.available },
    { key: 'delayed', label: '数据延迟', count: filterCounts.value.delayed },
    { key: 'none', label: '无盘中估值', count: filterCounts.value.none },
  ]
})

const mobileFilterOptions = computed<Array<{ key: IntradayFilter; label: string; count: number }>>(() => {
  if (isLatestOfficial.value) {
    return [{ key: 'all', label: '全部', count: filterCounts.value.all }]
  }
  const list: Array<{ key: IntradayFilter; label: string; count: number }> = [
    { key: 'all', label: '全部', count: filterCounts.value.all },
  ]
  if (filterCounts.value.delayed > 0) {
    list.push({ key: 'delayed', label: '延迟', count: filterCounts.value.delayed })
  }
  if (filterCounts.value.none > 0) {
    list.push({ key: 'none', label: '无估值', count: filterCounts.value.none })
  }
  return list
})

const rowSessionProfitMap = computed(() => {
  const ds = funds.value?.summary.today.display_session
  const map = new Map<number | string, RowSessionProfit>()
  for (const row of funds.value?.rows ?? []) {
    map.set(row.id, rowSessionProfitView(row, ds))
  }
  return map
})

async function copyCode(code: string, event?: Event) {
  if (event) event.stopPropagation()
  if (!code || code === 'CASH') return
  try {
    await navigator.clipboard.writeText(code)
    copiedCode.value = code
    message.success(`已复制基金代码 ${code}`)
    setTimeout(() => {
      if (copiedCode.value === code) copiedCode.value = null
    }, 1500)
  } catch {
    message.warning(`基金代码：${code}`)
  }
}

function toggleSort(field: SortField) {
  if (sortField.value === field) {
    if (sortOrder.value === 'desc') {
      sortOrder.value = 'asc'
    } else if (sortOrder.value === 'asc') {
      sortField.value = null
      sortOrder.value = null
    } else {
      sortOrder.value = 'desc'
    }
  } else {
    sortField.value = field
    sortOrder.value = field === 'name' ? 'asc' : 'desc'
  }
}

const displayRows = computed(() => {
  const query = searchQuery.value.trim().toLowerCase()
  const rows = (funds.value?.rows ?? []).filter((row) => {
    if (!matchesIntradayFilter(row, statusFilter.value)) return false
    if (!query) return true
    return (
      row.name.toLowerCase().includes(query)
      || row.code.toLowerCase().includes(query)
      || (row.theme ?? '').toLowerCase().includes(query)
    )
  })

  if (!sortField.value || !sortOrder.value) return rows

  const field = sortField.value
  const orderFactor = sortOrder.value === 'asc' ? 1 : -1
  const profitMap = rowSessionProfitMap.value
  return [...rows].sort((a, b) => {
    if (field === 'name') {
      return orderFactor * a.name.localeCompare(b.name, 'zh-CN')
    }

    let valA: number | null = null
    let valB: number | null = null
    if (field === 'amount') {
      valA = a.amount
      valB = b.amount
    } else if (field === 'estimated_return_pct') {
      const spA = profitMap.get(a.id)
      const spB = profitMap.get(b.id)
      valA = spA?.returnPct ?? rowEstimateView(a).pct
      valB = spB?.returnPct ?? rowEstimateView(b).pct
    } else if (field === 'today_profit') {
      valA = profitMap.get(a.id)?.amount ?? null
      valB = profitMap.get(b.id)?.amount ?? null
    } else if (field === 'holding_profit') {
      valA = a.holding_profit
      valB = b.holding_profit
    }

    return compareNullableNumbers(valA, valB, orderFactor)
  })
})

function pnlState(value: number | null | undefined): HeroMetric['state'] {
  if (value == null) return 'plain'
  if (value > 0) return 'rise'
  if (value < 0) return 'fall'
  return 'neutral'
}

function badgeClass(badge: string): string {
  if (badge === '延迟') return 'is-warning'
  if (badge === '估') return 'is-accent'
  return ''
}

function rowStatusTitle(row: HoldingRow): string {
  if (row.is_cash_asset) return '现金资产'
  const parts: string[] = []
  if (row.name) parts.push(row.name)
  if (row.code) parts.push(`(${row.code})`)
  if (row.nav_date) parts.push(`最新官方净值日：${row.nav_date}`)
  if (row.estimated_at) parts.push(`估值时间：${formatDateTime(row.estimated_at)}`)
  if (row.estimated_source) parts.push(`来源：${sourceDisplayName(row.estimated_source)}`)
  const intraday = resolveIntradayStatus(row)
  if (intraday.detail) parts.push(intraday.detail)
  const official = resolveOfficialStatus(row)
  if (official.detail) parts.push(official.detail)
  return parts.join(' · ')
}

const heroItems = computed<HeroMetric[]>(() => {
  const today = funds.value?.summary.today
  const hero = todayHeroCopy(today)
  const holdingProfit = funds.value?.summary.total_holding_profit ?? null
  const totalAmount = funds.value?.summary.total_amount ?? null

  return [
    {
      key: 'today',
      label: hero.label,
      value: hero.amount == null ? '暂无' : signedCompactMoney(hero.amount),
      subValue: hero.ratePct != null ? formatPercent(hero.ratePct) : undefined,
      subValueState: pnlState(hero.ratePct),
      hint: hero.hint,
      state: pnlState(hero.amount),
      featured: true,
      infoDetails: hero.infoDetails,
    },
    {
      key: 'total',
      label: '总资产',
      prefix: '¥',
      value: formatCompactMoney(totalAmount),
    },
    {
      key: 'holding-profit',
      label: '持有收益',
      value: holdingProfit == null ? '暂无' : signedCompactMoney(holdingProfit),
      state: pnlState(holdingProfit),
    },
  ]
})

function applyMonitor(monitor: FundsResponse): void {
  funds.value = monitor
  loadedAt = Date.now()
  const selected = selectedFund.value
  if (!selected) return
  const next = monitor.rows.find((row) => row.code === selected.code)
  if (next) selectedFund.value = next
}

async function loadFunds() {
  loading.value = true
  error.value = null
  try {
    applyMonitor(await fetchFunds())
  } catch (caught) {
    error.value = caught instanceof Error ? caught.message : '加载基金监控失败'
  } finally {
    loading.value = false
  }
}

/** 只读本地库，更新交易时段与披露状态判断；不请求上游来源。 */
async function syncLocalState(): Promise<void> {
  if (syncInFlight || refreshInFlight || !funds.value) return
  syncInFlight = true
  try {
    const monitor = await fetchFunds()
    if (!refreshInFlight) applyMonitor(monitor)
  } catch {
    // 保留当前数据；下次检查或手动刷新会再次尝试。
  } finally {
    syncInFlight = false
  }
}

function nowClock(): string {
  return formatClock(new Date().toISOString())
}

async function runRefresh(options: { auto?: boolean } = {}): Promise<void> {
  if (refreshInFlight) return
  refreshInFlight = true
  refreshing.value = true
  lastAttemptAt = Date.now()
  // 已有数据时不插入或移除页首提示，避免已读过的数据发生闪烁或高度变化。
  if (!funds.value) error.value = null
  const previous = funds.value
  try {
    const result: FundsRefreshResponse = await refreshFunds()
    const updates = diffSourceUpdates(previous, result.monitor)
    applyMonitor(result.monitor)
    autoFailures.value = isFailedRun(result) ? autoFailures.value + 1 : 0
    const noteParts: StatusPart[] = [
      { text: `${nowClock()} ${result.coalesced ? '已合并进行中的刷新' : '已刷新'}`, tone: 'neutral' },
      { text: describeSourceUpdates(updates), tone: 'neutral' },
    ]
    if (result.has_failure) noteParts.push({ text: '部分来源失败', tone: 'warning' })
    refreshNoteParts.value = noteParts
    if (!options.auto) {
      if (result.has_failure) message.warning(result.message)
      else message.success(result.message)
    } else if (autoFailures.value === 1) {
      message.warning(result.message)
    }
  } catch (caught) {
    autoFailures.value += 1
    const msg = caught instanceof Error ? caught.message : '刷新行情失败'
    refreshNoteParts.value = [{ text: `${nowClock()} 刷新失败 · 保留此前数据`, tone: 'warning' }]
    if (!funds.value) error.value = msg
    if (!options.auto || autoFailures.value === 1) message.error(msg)
  } finally {
    refreshing.value = false
    refreshInFlight = false
  }
}

function doRefresh(): void {
  autoFailures.value = 0
  void runRefresh()
}

async function maybeAutoRefresh(): Promise<void> {
  if (!pageActive || refreshInFlight) return
  if (document.visibilityState !== 'visible') return
  const now = Date.now()
  if (!shouldAutoRefresh({
    monitor: funds.value,
    now,
    lastAttemptAt,
    failures: autoFailures.value,
  })) {
    // 非轮询时段定期重读本地状态，以便及时反映盘中/盘后切换与官方披露推进。
    if (pollingInterval(funds.value) == null && now - loadedAt >= 5 * 60_000) {
      await syncLocalState()
    }
    return
  }
  await runRefresh({ auto: true })
}

async function maybeInitialBackfill(): Promise<void> {
  if (!pageActive || !initialBackfillPending || refreshInFlight) return
  if (document.visibilityState !== 'visible' || !funds.value) return
  initialBackfillPending = false
  const now = Date.now()
  const input = {
    monitor: funds.value,
    now,
    lastAttemptAt,
    failures: autoFailures.value,
  }
  if (shouldAutoRefresh(input) || shouldInitialBackfill(input)) {
    await runRefresh({ auto: true })
  }
}

function startChecks(): void {
  stopChecks()
  checkTimer = setInterval(() => void maybeAutoRefresh(), CHECK_TICK_MS)
}

function stopChecks(): void {
  if (checkTimer) clearInterval(checkTimer)
  checkTimer = null
}

function onVisibilityChange(): void {
  if (!pageActive) return
  if (document.visibilityState !== 'visible') {
    stopChecks()
    return
  }
  autoFailures.value = 0
  startChecks()
  // 恢复可见前，其他标签页可能已刷新；先同步服务端时间再判断冷却。
  void syncLocalState().then(() => (
    initialBackfillPending ? maybeInitialBackfill() : maybeAutoRefresh()
  ))
}

onMounted(() => {
  pageActive = true
  initialBackfillPending = true
  document.addEventListener('visibilitychange', onVisibilityChange)
  wideSplitMedia = window.matchMedia('(min-width: 1440px)')
  onWideSplitChange(wideSplitMedia.matches)
  wideSplitMedia.addEventListener('change', onWideSplitMediaEvent)
  void loadFunds().then(() => {
    if (!pageActive) return
    if (document.visibilityState === 'visible') startChecks()
    return maybeInitialBackfill()
  })
})

onBeforeUnmount(() => {
  pageActive = false
  initialBackfillPending = false
  stopChecks()
  wideSplitMedia?.removeEventListener('change', onWideSplitMediaEvent)
  wideSplitMedia = null
  document.removeEventListener('visibilitychange', onVisibilityChange)
})

const autoPaused = computed(() => autoFailures.value >= MAX_AUTO_FAILURES)

const csiAllShareQuote = computed(() => (
  funds.value?.market_quotes?.items?.find((item) => item.symbol === 'CSI_ALL_SHARE')
))

// 读取 funds.market_quotes 中 CSI_ALL_SHARE 的 session_status / session_label
const headerPhaseStatus = computed(() => {
  if (refreshing.value) return '刷新中'
  const quote = csiAllShareQuote.value
  const ds = funds.value?.summary.today.display_session
  const dateStr = (quote?.trade_date || ds?.date) ? formatMonthDay(quote?.trade_date || ds?.date) : ''

  if (quote) {
    if (quote.session_status === 'trading') {
      const timeStr = quote.quoted_at ? formatClock(quote.quoted_at) : nowClock()
      return `盘中 ${timeStr}`
    }
    if (quote.session_status === 'break') {
      return '午间休市'
    }
    return dateStr ? `${dateStr} 收盘` : (quote.session_label || '收盘')
  }

  if (ds?.mode === 'today') {
    return dateStr ? `${dateStr} 盘中` : '盘中'
  }
  return dateStr ? `${dateStr} 收盘` : '收盘'
})

// 手机端紧凑状态展示：在暂停或失败时展示简短提示
const mobileHeaderStatus = computed(() => {
  if (refreshing.value) return '刷新中'
  if (autoPaused.value) return '刷新已暂停'
  if (refreshNote.value && (refreshNote.value.includes('失败') || refreshNote.value.includes('部分来源失败'))) {
    return '刷新失败'
  }
  return headerPhaseStatus.value
})

const headerStatusParts = computed<StatusPart[]>(() => {
  const parts: StatusPart[] = [{ text: headerPhaseStatus.value, tone: 'neutral' }]
  if (refreshing.value) {
    parts.push({ text: '正在刷新行情', tone: 'neutral' })
  } else if (refreshNoteParts.value) {
    parts.push(...refreshNoteParts.value)
  }
  if (autoPaused.value) {
    parts.push({ text: '连续失败，自动刷新已暂停', tone: 'warning' })
  }
  return parts.filter((part) => part.text)
})

const headerStatusLine = computed(() => headerStatusParts.value.map((part) => part.text).join(' · '))

const hasActiveMobileMeta = computed(() => Boolean(sortField.value || hasActiveFilterOrSort.value))

useHotkeys((event) => {
  if (event.isComposing || isEditableTarget(event.target)) return
  if (event.metaKey || event.ctrlKey || event.altKey) return
  if (event.key === 'r' || event.key === 'R') {
    event.preventDefault()
    void doRefresh()
  }
  if (event.key === '/') {
    event.preventDefault()
    mobileSearchOpen.value = true
    nextTick(() => {
      document.getElementById('funds-search')?.focus()
    })
  }
})

function openFundDrawer(row: HoldingRow): void {
  if (wideSplit.value) {
    selectedFund.value = row
    return
  }
  selectedFund.value = row
  drawerOpen.value = true
}

function closeFundDrawer(): void {
  drawerOpen.value = false
  selectedFund.value = null
}

function closeFundDetail(): void {
  if (wideSplit.value) {
    selectedFund.value = null
    return
  }
  closeFundDrawer()
}

function onRowKeydown(event: KeyboardEvent, row: HoldingRow) {
  if (event.key === 'Enter' || event.key === ' ') {
    event.preventDefault()
    openFundDrawer(row)
  }
}

const sortLabel = computed(() => {
  if (!sortField.value) return ''
  const labels: Record<SortField, string> = {
    name: '名称',
    amount: '金额',
    estimated_return_pct: '当日涨跌',
    today_profit: '当日收益',
    holding_profit: '持有收益',
  }
  return `${labels[sortField.value]} · ${sortOrder.value === 'asc' ? '升序' : '降序'}`
})

const hasActiveFilterOrSort = computed(() => (
  Boolean(searchQuery.value.trim())
  || statusFilter.value !== 'all'
  || sortField.value !== null
))

function resetFiltersAndSort() {
  searchQuery.value = ''
  statusFilter.value = 'all'
  sortField.value = null
  sortOrder.value = null
  mobileSearchOpen.value = false
}

function openMobileSearch() {
  mobileSearchOpen.value = true
  nextTick(() => {
    document.getElementById('funds-search')?.focus()
  })
}

function closeMobileSearch() {
  mobileSearchOpen.value = false
  searchQuery.value = ''
}
</script>

<template>
  <div class="workbench-container flex flex-col gap-4 min-w-0 max-w-full overflow-x-hidden">
    <div
      v-if="error"
      class="alert-banner alert-banner--error shrink-0"
      role="alert"
    >
      <AlertCircle
        class="h-4 w-4 shrink-0"
        :stroke-width="1.75"
      />
      <span class="flex-1">{{ error }}</span>
      <button
        type="button"
        class="cursor-pointer text-[12px] opacity-75 hover:opacity-100"
        @click="error = null"
      >
        关闭
      </button>
    </div>

    <div
      class="sr-only"
      aria-live="polite"
    >
      {{ headerStatusLine }}
    </div>

    <WorkbenchPageHeader title="基金估值">
      <template #meta>
        <p class="valuation-desktop-only page-header__status text-[13px] text-(--text-secondary)">
          <span
            v-for="(part, index) in headerStatusParts"
            :key="index"
            class="page-header__status-part"
            :class="part.tone === 'warning' ? 'is-warning' : ''"
          >{{ part.text }}</span>
        </p>
      </template>

      <template #actions>
        <div class="valuation-desktop-only items-center gap-3 shrink-0">
          <Button
            variant="secondary"
            :loading="refreshing"
            @click="doRefresh"
          >
            <template #icon>
              <RefreshCw
                class="h-3.5 w-3.5"
                :stroke-width="1.75"
              />
            </template>
            刷新行情
          </Button>
        </div>

        <div class="valuation-mobile-only mobile-header-tools items-center gap-2 shrink-0">
          <span class="text-[12px] text-(--text-secondary)">
            {{ mobileHeaderStatus }}
          </span>
          <button
            type="button"
            class="mobile-refresh-icon-btn min-w-[44px] min-h-[44px] flex items-center justify-center rounded-[8px] text-(--text-secondary) hover:text-(--text-primary) active:bg-(--bg-surface-hover)"
            :disabled="refreshing"
            aria-label="刷新行情"
            @click="doRefresh"
          >
            <RefreshCw
              class="h-4 w-4"
              :class="refreshing ? 'animate-spin' : ''"
              :stroke-width="1.75"
            />
          </button>
        </div>
      </template>
    </WorkbenchPageHeader>

    <HeroMetrics :items="heroItems" />

    <MarketQuoteStrip :quotes="funds?.market_quotes" />

    <div class="funds-split">
      <section
        class="funds-blotter blotter flex min-h-0 flex-1 flex-col"
        :class="tablePressing ? 'is-pressing' : ''"
        @mousedown="tablePressing = true"
        @mouseup="tablePressing = false"
        @mouseleave="tablePressing = false"
      >
        <DataToolbar
          :class="{
            'is-mobile-search-open': mobileSearchOpen,
            'has-mobile-meta': hasActiveMobileMeta,
          }"
        >
          <template #search>
            <div
              class="w-full valuation-search-container"
              :class="mobileSearchOpen ? 'is-open' : ''"
            >
              <Input
                id="funds-search"
                v-model="searchQuery"
                placeholder="搜索基金代码/名称"
                size="sm"
                autocomplete="off"
                aria-label="搜索基金"
              >
                <template #prefix>
                  <Search
                    class="h-3.5 w-3.5 text-(--text-tertiary)"
                    :stroke-width="1.75"
                  />
                </template>
                <template
                  v-if="mobileSearchOpen"
                  #suffix
                >
                  <button
                    type="button"
                    class="min-w-[44px] min-h-[44px] flex items-center justify-center text-(--text-tertiary) hover:text-(--text-primary)"
                    aria-label="关闭搜索"
                    @click="closeMobileSearch"
                  >
                    <X
                      class="h-3.5 w-3.5"
                      :stroke-width="1.75"
                    />
                  </button>
                </template>
              </Input>
            </div>
          </template>

          <template #filters>
            <div
              class="text-filters text-filters--desktop valuation-desktop-only"
              role="tablist"
              aria-label="按盘中状态筛选"
            >
              <button
                v-for="item in filterOptions"
                :key="item.key"
                type="button"
                role="tab"
                class="text-filter"
                :class="statusFilter === item.key ? 'is-active' : ''"
                :aria-selected="statusFilter === item.key"
                @click="statusFilter = item.key"
              >
                {{ item.label }} {{ item.count }}
              </button>
            </div>

            <div
              v-if="!mobileSearchOpen"
              class="toolbar-mobile-row valuation-mobile-only flex items-center justify-between w-full gap-2"
            >
              <div
                class="flex items-center gap-1.5 overflow-x-auto no-scrollbar"
                role="tablist"
                aria-label="按盘中状态筛选"
              >
                <button
                  v-for="item in mobileFilterOptions"
                  :key="`m-${item.key}`"
                  type="button"
                  role="tab"
                  class="mobile-filter-tab px-2.5 py-1 rounded-[6px] text-[12px] font-medium transition-colors"
                  :class="statusFilter === item.key ? 'bg-(--bg-surface-hover) text-(--text-primary)' : 'text-(--text-secondary) hover:text-(--text-primary)'"
                  :aria-selected="statusFilter === item.key"
                  @click="statusFilter = item.key"
                >
                  {{ item.label }} {{ item.count }}
                </button>
              </div>
              <button
                type="button"
                class="mobile-search-trigger min-w-[44px] min-h-[44px] flex items-center justify-center rounded-[6px] text-(--text-secondary) hover:text-(--text-primary)"
                aria-label="展开搜索"
                @click="openMobileSearch"
              >
                <Search
                  class="h-4 w-4"
                  :stroke-width="1.75"
                />
              </button>
            </div>
          </template>

          <template #meta>
            <span class="valuation-desktop-only">{{ inventoryLabel(funds?.rows ?? [], displayRows) }}</span>
            <div
              v-if="sortField"
              class="valuation-mobile-only inline-flex items-center gap-1 rounded-[6px] bg-(--bg-surface) px-2 py-0.5 text-[12px] text-(--text-secondary)"
            >
              <span>{{ sortLabel }}</span>
            </div>
            <button
              v-if="hasActiveFilterOrSort"
              type="button"
              class="cursor-pointer text-[12px] text-(--text-secondary) transition-colors hover:text-(--text-primary) min-h-[36px] flex items-center"
              @click="resetFiltersAndSort"
            >
              重置
            </button>
          </template>
        </DataToolbar>

        <!-- 手机端列表 -->
        <div class="fund-mobile-list">
          <div class="fund-mobile-list__head">
            <button
              type="button"
              @click="toggleSort('name')"
            >
              <SortableTh
                label="基金"
                :active="sortField === 'name'"
                :order="sortOrder"
              />
            </button>
            <button
              type="button"
              @click="toggleSort('amount')"
            >
              <SortableTh
                label="金额"
                align="right"
                :active="sortField === 'amount'"
                :order="sortOrder"
              />
            </button>
            <button
              type="button"
              @click="toggleSort('today_profit')"
            >
              <SortableTh
                label="当日收益"
                align="right"
                :active="sortField === 'today_profit'"
                :order="sortOrder"
              />
            </button>
          </div>

          <div
            v-if="loading"
            class="fund-mobile-list__loading"
            aria-label="正在加载基金"
          >
            <div
              v-for="index in 6"
              :key="`mobile-sk-${index}`"
              class="fund-mobile-row"
            >
              <span class="skeleton w-32" />
              <span class="skeleton w-14" />
              <span class="skeleton w-16" />
            </div>
          </div>

          <EmptyState
            v-else-if="displayRows.length === 0"
            :title="hasFundRows ? '暂无匹配的基金' : '还没有持仓'"
            :description="hasFundRows ? '调整筛选或搜索条件后重试' : '从持仓截图识别录入，或在持仓页手动新增'"
          >
            <Button
              v-if="!hasFundRows"
              variant="primary"
              @click="router.push({ name: 'import' })"
            >
              <template #icon>
                <Images
                  class="h-4 w-4"
                  :stroke-width="1.75"
                />
              </template>
              去录入
            </Button>
          </EmptyState>

          <button
            v-for="row in displayRows"
            v-else
            :key="`mobile-${row.id}`"
            type="button"
            class="fund-mobile-row"
            :class="selectedFund?.id === row.id ? 'is-active' : ''"
            @click="openFundDrawer(row)"
          >
            <span class="fund-mobile-row__identity">
              <span
                class="fund-mobile-row__name"
                :title="row.name"
              >{{ row.name }}</span>
              <span class="fund-mobile-row__subline">
                <span>{{ row.is_cash_asset ? '现金' : row.code }}</span>
                <template
                  v-for="badge in rowSessionProfitMap.get(row.id)?.badges"
                  :key="badge"
                >
                  <span
                    class="inline-badge"
                    :class="badgeClass(badge)"
                  >{{ badge }}</span>
                </template>
              </span>
            </span>

            <span class="fund-mobile-row__amount">
              {{ row.amount != null ? formatMoney(row.amount) : '—' }}
            </span>

            <span class="fund-mobile-row__profit">
              <span
                class="fund-mobile-row__profit-amount"
                :class="valueStateClass(signedState(rowSessionProfitMap.get(row.id)?.amount))"
              >
                {{ row.is_cash_asset ? '—' : signedMoney(rowSessionProfitMap.get(row.id)?.amount) }}
              </span>
              <span
                v-if="!row.is_cash_asset && rowSessionProfitMap.get(row.id)?.returnPct != null"
                class="fund-mobile-row__profit-pct"
                :class="valueStateClass(signedState(rowSessionProfitMap.get(row.id)?.returnPct))"
              >
                {{ formatPercent(rowSessionProfitMap.get(row.id)?.returnPct) }}
              </span>
              <span
                v-else-if="!row.is_cash_asset"
                class="fund-mobile-row__profit-pct text-(--text-tertiary)"
              >
                —
              </span>
            </span>
          </button>
        </div>

        <!-- 桌面端表格 -->
        <div class="funds-table-scroll blotter-scroll">
          <table class="data-dense-table funds-table">
            <thead>
              <tr>
                <th
                  class="w-[32%] min-w-[200px] cursor-pointer text-left"
                  @click="toggleSort('name')"
                >
                  <SortableTh
                    label="基金"
                    :active="sortField === 'name'"
                    :order="sortOrder"
                  />
                </th>
                <th
                  class="w-[17%] min-w-[110px] cursor-pointer text-right"
                  @click="toggleSort('amount')"
                >
                  <SortableTh
                    label="持仓金额"
                    align="right"
                    :active="sortField === 'amount'"
                    :order="sortOrder"
                  />
                </th>
                <th
                  class="w-[17%] min-w-[100px] cursor-pointer text-right"
                  @click="toggleSort('estimated_return_pct')"
                >
                  <SortableTh
                    label="当日涨跌"
                    align="right"
                    :active="sortField === 'estimated_return_pct'"
                    :order="sortOrder"
                  />
                </th>
                <th
                  class="w-[17%] min-w-[110px] cursor-pointer text-right"
                  @click="toggleSort('today_profit')"
                >
                  <SortableTh
                    label="当日收益"
                    align="right"
                    :active="sortField === 'today_profit'"
                    :order="sortOrder"
                  />
                </th>
                <th
                  class="w-[17%] min-w-[110px] cursor-pointer text-right"
                  @click="toggleSort('holding_profit')"
                >
                  <SortableTh
                    label="持有收益"
                    align="right"
                    :active="sortField === 'holding_profit'"
                    :order="sortOrder"
                  />
                </th>
              </tr>
            </thead>
            <tbody>
              <template v-if="loading">
                <tr
                  v-for="index in 10"
                  :key="`sk-${index}`"
                >
                  <td><span class="skeleton w-40" /></td>
                  <td class="text-right">
                    <span class="skeleton w-16" />
                  </td>
                  <td class="text-right">
                    <span class="skeleton w-12" />
                  </td>
                  <td class="text-right">
                    <span class="skeleton w-16" />
                  </td>
                  <td class="text-right">
                    <span class="skeleton w-16" />
                  </td>
                </tr>
              </template>
              <tr v-else-if="displayRows.length === 0">
                <td colspan="5">
                  <EmptyState
                    :title="hasFundRows ? '暂无匹配的基金' : '还没有持仓'"
                    :description="hasFundRows ? '调整筛选或搜索条件后重试' : '从持仓截图识别录入，或在持仓页手动新增'"
                  >
                    <Button
                      v-if="!hasFundRows"
                      variant="primary"
                      @click="router.push({ name: 'import' })"
                    >
                      <template #icon>
                        <Images
                          class="h-4 w-4"
                          :stroke-width="1.75"
                        />
                      </template>
                      去录入
                    </Button>
                  </EmptyState>
                </td>
              </tr>
              <tr
                v-for="row in displayRows"
                :key="row.id"
                :class="['group cursor-pointer', selectedFund?.id === row.id ? 'is-active' : '']"
                tabindex="0"
                :title="rowStatusTitle(row)"
                @click="openFundDrawer(row)"
                @keydown="onRowKeydown($event, row)"
              >
                <td>
                  <span
                    class="fund-name"
                    :title="row.name"
                  >{{ row.name }}</span>
                  <div class="fund-code flex items-center gap-1.5">
                    <span>{{ row.is_cash_asset ? '现金' : row.code }}</span>
                    <button
                      v-if="!row.is_cash_asset"
                      type="button"
                      class="row-action rounded-[6px] p-0.5 text-(--text-tertiary) hover:text-(--text-primary)"
                      aria-label="复制基金代码"
                      @click="copyCode(row.code, $event)"
                    >
                      <Check
                        v-if="copiedCode === row.code"
                        class="h-3 w-3"
                        :stroke-width="1.75"
                      />
                      <Copy
                        v-else
                        class="h-3 w-3"
                        :stroke-width="1.75"
                      />
                    </button>
                    <template
                      v-for="badge in rowSessionProfitMap.get(row.id)?.badges"
                      :key="badge"
                    >
                      <span
                        class="inline-badge"
                        :class="badgeClass(badge)"
                      >{{ badge }}</span>
                    </template>
                  </div>
                </td>
                <td class="text-right">
                  <MarketValue :value="row.amount" />
                </td>
                <td class="text-right">
                  <MarketValue
                    :value="rowSessionProfitMap.get(row.id)?.returnPct ?? rowEstimateView(row).pct"
                    kind="percent"
                    :state="signedState(rowSessionProfitMap.get(row.id)?.returnPct ?? rowEstimateView(row).pct)"
                    :tone-level="rowEstimateView(row).overseas ? 'muted' : 'strong'"
                  />
                </td>
                <td class="text-right">
                  <MarketValue
                    :value="rowSessionProfitMap.get(row.id)?.amount"
                    kind="signed-money"
                    :state="signedState(rowSessionProfitMap.get(row.id)?.amount)"
                  />
                </td>
                <td class="text-right">
                  <MarketValue
                    :value="row.holding_profit"
                    kind="signed-money"
                    :state="row.holding_profit_state"
                    tone-level="strong"
                  />
                  <div
                    v-if="row.holding_profit_rate != null"
                    class="text-[12px] font-numeric"
                    :class="valueStateClass(row.holding_profit_state, 'muted')"
                  >
                    {{ formatPercent(row.holding_profit_rate) }}
                  </div>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>

      <aside
        v-if="wideSplit"
        class="funds-detail-pane"
        aria-label="基金详情"
      >
        <template v-if="selectedFund">
          <div class="funds-detail-pane__header">
            <FundIdentityHeader
              :fund="selectedFund"
              @copy-code="copyCode"
            />
            <button
              type="button"
              class="funds-detail-pane__close"
              aria-label="关闭详情"
              @click="closeFundDetail"
            >
              <X
                class="h-4 w-4"
                :stroke-width="1.75"
              />
            </button>
          </div>
          <div class="funds-detail-pane__body">
            <FundDetailPanel :fund="selectedFund" />
          </div>
        </template>
        <EmptyState
          v-else
          title="选择基金查看详情"
          description="点击左侧列表任意基金，在此查看估值、走势与档案"
        />
      </aside>
    </div>

    <!-- 组合分析 -->
    <PortfolioAnalysisPanel
      v-if="funds?.portfolio_analysis"
      :analysis="funds.portfolio_analysis"
      :has-fund-holdings="(funds.summary.fund_holding_count ?? 0) > 0"
    />

    <FundDetailDrawer
      v-model:show="drawerOpen"
      :fund="selectedFund"
      @close="closeFundDrawer"
      @copy-code="copyCode"
    />
  </div>
</template>

<style scoped>
@media (max-width: 768px) {
  .valuation-desktop-only {
    display: none !important;
  }

  .valuation-mobile-only {
    display: flex;
  }

  .text-filters--desktop {
    display: none !important;
  }

  /* 移动端搜索收起时，外层容器不占高度 */
  :deep(.data-toolbar:not(.is-mobile-search-open) .data-toolbar__search) {
    display: none !important;
  }

  .valuation-search-container:not(.is-open) {
    display: none !important;
  }

  /* 移动端 meta 为空时不留空行 */
  :deep(.data-toolbar:not(.has-mobile-meta) .data-toolbar__meta) {
    display: none !important;
  }

  /* 移动端筛选 Tab 尺寸 ≥ 44×44 */
  .mobile-filter-tab {
    min-height: 44px;
    min-width: 44px;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    box-sizing: border-box;
    /* 44px 触摸目标保留，外形收敛为胶囊，避免读起来像方形磁贴 */
    padding-left: 14px;
    padding-right: 14px;
    border-radius: 999px;
  }
}

@media (min-width: 769px) {
  .valuation-desktop-only {
    display: flex;
  }

  .valuation-desktop-only.page-header__status {
    display: block;
  }

  .page-header__status-part + .page-header__status-part::before {
    color: var(--text-tertiary);
    content: ' · ';
  }

  .page-header__status-part.is-warning {
    color: var(--status-warning);
  }

  span.valuation-desktop-only {
    display: inline;
  }

  .valuation-mobile-only,
  .toolbar-mobile-row {
    display: none !important;
  }

  .valuation-search-container {
    display: block !important;
  }
}

/* 769px~1279px 桌面紧凑布局：确保 1024×768 下首屏显示 ≥ 6 行 */
@media (min-width: 769px) and (max-width: 1279px) {
  :deep(.page-header) {
    display: flex !important;
    flex-direction: row !important;
    align-items: center !important;
    justify-content: space-between !important;
    gap: 16px !important;
    padding-bottom: 0 !important;
  }

  :deep(.page-header__heading) {
    display: flex !important;
    align-items: baseline !important;
    gap: 12px !important;
    flex-shrink: 0 !important;
  }

  :deep(.page-title) {
    font-size: 24px !important;
    line-height: 30px !important;
  }

  :deep(.page-header__tools) {
    display: flex !important;
    align-items: center !important;
    justify-content: flex-end !important;
    gap: 12px !important;
    flex: 1 !important;
    min-width: 0 !important;
  }

  :deep(.page-header__status) {
    display: block !important;
    white-space: nowrap !important;
    overflow: hidden !important;
    text-overflow: ellipsis !important;
  }

  :deep(.metric-strip) {
    grid-template-columns: 1.4fr 1fr 1fr !important;
    gap: 0 !important;
    padding: 0 !important;
  }

  :deep(.metric-strip__item--featured) {
    grid-column: auto !important;
    padding: 0 16px 0 0 !important;
  }

  :deep(.metric-strip__item) {
    padding: 0 16px !important;
    border-left: 1px solid var(--border-subtle) !important;
  }

  :deep(.metric-strip__item:first-child) {
    padding-left: 0 !important;
    border-left: none !important;
  }

  :deep(.metric-strip__item--featured .metric-strip__value) {
    font-size: 26px !important;
    line-height: 30px !important;
  }

  :deep(.metric-strip__item:not(.metric-strip__item--featured) .metric-strip__value) {
    font-size: 18px !important;
    line-height: 24px !important;
  }
}

/* ---------- ≥1440px 列表 + 常驻详情分栏 ---------- */

/* 承接原 .funds-blotter 的 flex-1 + min-h-0，桌面端保持表格内部滚动。 */
.funds-split {
  display: flex;
  flex: 1 1 0%;
  min-height: 0;
  min-width: 0;
}

@media (min-width: 1440px) {
  .funds-split {
    gap: 20px;
  }

  .funds-detail-pane {
    display: flex;
    flex-direction: column;
    width: clamp(380px, 28vw, 500px);
    flex-shrink: 0;
    min-height: 0;
    border-left: 1px solid var(--border-subtle);
  }
}

.funds-detail-pane__header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  flex-shrink: 0;
  padding: 4px 20px 12px;
  border-bottom: 1px solid var(--border-subtle);
}

.funds-detail-pane__close {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 32px;
  height: 32px;
  flex-shrink: 0;
  border-radius: var(--radius-control);
  color: var(--text-tertiary);
  cursor: pointer;
  transition: color var(--dur-ui) var(--ease-out), background-color var(--dur-ui) var(--ease-out);
}

.funds-detail-pane__close:hover {
  color: var(--text-primary);
  background: var(--bg-surface-hover);
}

.funds-detail-pane__body {
  flex: 1 1 0%;
  min-height: 0;
  overflow-y: auto;
  overscroll-behavior: contain;
  padding: 16px 20px 24px;
}
</style>
