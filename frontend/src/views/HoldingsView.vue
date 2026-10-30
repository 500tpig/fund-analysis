<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import {
  Plus,
  Copy,
  Check,
  Search,
  AlertCircle,
  AlertTriangle,
  Images,
  Ellipsis,
} from '@lucide/vue'

import { deleteHolding, fetchHoldings } from '@/api/client'
import type { HoldingRow, HoldingsResponse } from '@/api/types'
import DataToolbar from '@/components/DataToolbar.vue'
import EmptyState from '@/components/EmptyState.vue'
import HoldingEditorDrawer from '@/components/HoldingEditorDrawer.vue'
import HeroMetrics, { type HeroMetric } from '@/components/HeroMetrics.vue'
import MarketValue from '@/components/MarketValue.vue'
import SortableTh from '@/components/SortableTh.vue'
import WorkbenchPageHeader from '@/components/WorkbenchPageHeader.vue'
import Button from '@/components/ui/Button.vue'
import Input from '@/components/ui/Input.vue'
import { inventoryLabel } from '@/shared/dataStatus'
import {
  formatCompactCurrency,
  formatCompactMoney,
  formatMonthDay,
  formatPercent,
  signedCompactMoney,
  valueStateClass,
} from '@/shared/formatters'
import { useMessage } from '@/shared/toast'
import { isEditableTarget, useHotkeys } from '@/shared/hotkeys'
import { compareNullableNumbers } from '@/shared/sorting'

type SortField = 'name' | 'amount' | 'weight_pct' | 'holding_profit'
type SortOrder = 'asc' | 'desc' | null

const holdings = ref<HoldingsResponse | null>(null)
const loading = ref(true)
const error = ref<string | null>(null)
const searchQuery = ref('')
const sortField = ref<SortField>('amount')
const sortOrder = ref<SortOrder>('desc')

const drawerOpen = ref(false)
const editingHolding = ref<HoldingRow | null>(null)

const deleteTarget = ref<HoldingRow | null>(null)
const deleting = ref(false)
const copiedCode = ref<string | null>(null)
const actionMenu = ref<{ id: number; top: number; right: number } | null>(null)

const message = useMessage()
const router = useRouter()

const actionMenuRow = computed(() => (
  (holdings.value?.rows ?? []).find((row) => row.id === actionMenu.value?.id) ?? null
))

async function copyCode(code: string, event?: Event) {
  event?.stopPropagation()
  if (!code || code === 'CASH') return
  try {
    await navigator.clipboard.writeText(code)
    copiedCode.value = code
    message.success(`已复制基金代码 ${code}`)
    setTimeout(() => {
      if (copiedCode.value === code) copiedCode.value = null
    }, 1500)
  } catch {
    message.warning(`代码：${code}`)
  }
}

async function loadHoldings() {
  loading.value = true
  error.value = null
  try {
    holdings.value = await fetchHoldings()
  } catch (caught) {
    error.value = caught instanceof Error ? caught.message : '加载持仓失败'
  } finally {
    loading.value = false
  }
}

function openCreate() {
  closeActionMenu()
  editingHolding.value = null
  drawerOpen.value = true
}

function openEdit(row: HoldingRow) {
  closeActionMenu()
  editingHolding.value = row
  drawerOpen.value = true
}

function promptDelete(row: HoldingRow) {
  deleteTarget.value = row
}

async function executeDelete() {
  if (!deleteTarget.value) return
  deleting.value = true
  try {
    await deleteHolding(deleteTarget.value.id)
    message.success(`已删除持仓 ${deleteTarget.value.name}`)
    deleteTarget.value = null
    await loadHoldings()
  } catch (caught) {
    message.error(caught instanceof Error ? caught.message : '删除失败')
  } finally {
    deleting.value = false
  }
}

const summary = computed(() => holdings.value?.summary)

// 区分「组合本身为空」与「筛选后无匹配」，前者给出录入入口。
const hasAnyHolding = computed(() => (holdings.value?.rows.length ?? 0) > 0)

const holdingProfitRate = computed(() => {
  const profit = summary.value?.total_holding_profit ?? 0
  const total = summary.value?.total_amount ?? 0
  const cost = total - profit
  if (!cost || cost <= 0) return null
  return (profit / cost) * 100
})

const showThemeColumn = computed(() => (
  Boolean(holdings.value?.rows.some((row) => Boolean(row.theme?.trim())))
))

const maxWeightPct = computed(() => {
  const list = holdings.value?.rows ?? []
  if (!list.length) return 0
  const max = Math.max(...list.map((row) => row.weight_pct ?? 0))
  return Number.isFinite(max) ? max : 0
})

function toggleSort(field: SortField) {
  if (sortField.value === field) {
    sortOrder.value = sortOrder.value === 'desc' ? 'asc' : 'desc'
    return
  }
  sortField.value = field
  sortOrder.value = field === 'name' ? 'asc' : 'desc'
}


const sortLabel = computed(() => {
  const labels: Record<SortField, string> = {
    name: '名称',
    amount: '持仓金额',
    weight_pct: '组合占比',
    holding_profit: '累计收益',
  }
  return `${labels[sortField.value]} · ${sortOrder.value === 'asc' ? '升序' : '降序'}`
})

const hasActiveFilterOrSort = computed(() => (
  Boolean(searchQuery.value.trim())
  || sortField.value !== 'amount'
  || sortOrder.value !== 'desc'
))

function resetFiltersAndSort() {
  searchQuery.value = ''
  sortField.value = 'amount'
  sortOrder.value = 'desc'
}

const filteredRows = computed(() => {
  const list = holdings.value?.rows ?? []
  const q = searchQuery.value.trim().toLowerCase()
  const rows = q
    ? list.filter((row) => (
      row.code.toLowerCase().includes(q)
      || row.name.toLowerCase().includes(q)
      || (row.theme && row.theme.toLowerCase().includes(q))
    ))
    : list

  if (!sortField.value || !sortOrder.value) return rows
  const field = sortField.value
  const factor = sortOrder.value === 'asc' ? 1 : -1
  return [...rows].sort((a, b) => {
    if (field === 'name') return factor * a.name.localeCompare(b.name, 'zh-CN')
    const valA = a[field]
    const valB = b[field]
    return compareNullableNumbers(valA, valB, factor)
  })
})

const heroItems = computed<HeroMetric[]>(() => {
  const profit = summary.value?.total_holding_profit ?? 0
  return [
    {
      key: 'total',
      label: '总资产',
      prefix: '¥',
      value: formatCompactMoney(summary.value?.total_amount),
      featured: true,
    },
    {
      key: 'fund',
      label: '基金资产',
      value: formatCompactCurrency(summary.value?.fund_amount),
      hint: `${summary.value?.fund_holding_count ?? 0} 只基金`,
      state: 'plain',
      mobileHidden: true,
    },
    {
      key: 'cash',
      label: '现金资产',
      value: formatCompactCurrency(summary.value?.cash_amount),
      hint: `${summary.value?.cash_holding_count ?? 0} 笔`,
      state: 'plain',
      mobileHidden: true,
    },
    {
      key: 'max',
      label: '最大单项占比',
      value: formatPercent(maxWeightPct.value || null, { signed: false }),
      hint: '相对组合总资产',
      state: 'plain',
      mobileHidden: true,
    },
    {
      key: 'holding',
      label: '累计持有收益',
      value: signedCompactMoney(profit),
      delta: holdingProfitRate.value !== null ? formatPercent(holdingProfitRate.value) : undefined,
      deltaToneLevel: 'muted',
      state: profit > 0 ? 'rise' : profit < 0 ? 'fall' : 'neutral',
    },
  ]
})

function barWidth(weight: number | null | undefined): string {
  const value = Math.max(0, Math.min(100, weight ?? 0))
  return `${value}%`
}

function closeActionMenu() {
  actionMenu.value = null
}

function onRowKeydown(event: KeyboardEvent, row: HoldingRow) {
  // 行内复制/菜单按钮自身会处理按键，避免重复打开编辑。
  if (event.target !== event.currentTarget) return
  if (event.key === 'Enter' || event.key === ' ') {
    event.preventDefault()
    openEdit(row)
  }
}

function toggleActionMenu(row: HoldingRow, event: MouseEvent) {
  event.stopPropagation()
  const trigger = event.currentTarget as HTMLElement
  const rect = trigger.getBoundingClientRect()
  if (actionMenu.value?.id === row.id) {
    closeActionMenu()
    return
  }
  actionMenu.value = {
    id: row.id,
    top: rect.bottom + 4,
    right: window.innerWidth - rect.right,
  }
}

function editFromMenu(row: HoldingRow) {
  closeActionMenu()
  openEdit(row)
}

function deleteFromMenu(row: HoldingRow) {
  closeActionMenu()
  promptDelete(row)
}

function onDocumentPointerDown(event: PointerEvent) {
  const target = event.target
  if (!(target instanceof Element)) return
  if (target.closest('.row-menu, .row-menu__panel')) return
  closeActionMenu()
}

function onDocumentKeydown(event: KeyboardEvent) {
  if (event.key === 'Escape') closeActionMenu()
}

onMounted(() => {
  void loadHoldings()
  document.addEventListener('pointerdown', onDocumentPointerDown)
  document.addEventListener('keydown', onDocumentKeydown)
  window.addEventListener('resize', closeActionMenu)
  window.addEventListener('scroll', closeActionMenu, true)
})

onBeforeUnmount(() => {
  document.removeEventListener('pointerdown', onDocumentPointerDown)
  document.removeEventListener('keydown', onDocumentKeydown)
  window.removeEventListener('resize', closeActionMenu)
  window.removeEventListener('scroll', closeActionMenu, true)
})

useHotkeys((event) => {
  if (event.isComposing) return
  if (event.metaKey || event.ctrlKey || event.altKey) return
  if (event.key === '/' && !isEditableTarget(event.target)) {
    event.preventDefault()
    document.getElementById('holdings-search')?.focus()
    return
  }
  if ((event.key === 'n' || event.key === 'N') && !isEditableTarget(event.target)) {
    event.preventDefault()
    openCreate()
  }
})
</script>

<template>
  <div class="workbench-container flex flex-col gap-5">
    <WorkbenchPageHeader
      title="持仓管理"
      description="新增、修改或删除基金持仓。"
    >
      <template #actions>
        <Button
          variant="primary"
          @click="openCreate"
        >
          <template #icon>
            <Plus
              class="h-3.5 w-3.5"
              :stroke-width="1.75"
            />
          </template>
          新增持仓
        </Button>
      </template>
    </WorkbenchPageHeader>

    <div
      v-if="error"
      class="alert-banner alert-banner--error shrink-0"
    >
      <AlertCircle
        class="h-4 w-4 shrink-0"
        :stroke-width="1.75"
      />
      <span class="flex-1">{{ error }}</span>
      <button
        type="button"
        class="min-h-[44px] min-w-[44px] cursor-pointer text-[12px] opacity-75 hover:opacity-100"
        @click="error = null"
      >
        关闭
      </button>
    </div>

    <HeroMetrics :items="heroItems" />

    <section class="blotter flex min-h-0 flex-1 flex-col">
      <DataToolbar>
        <template #search>
          <Input
            id="holdings-search"
            v-model="searchQuery"
            :placeholder="showThemeColumn ? '搜索代码、名称或板块' : '搜索代码或名称'"
            size="sm"
            autocomplete="off"
            aria-label="搜索持仓"
          >
            <template #prefix>
              <Search
                class="h-3.5 w-3.5"
                :stroke-width="1.75"
              />
            </template>
          </Input>
        </template>
        <template #meta>
          <span>{{ inventoryLabel(holdings?.rows ?? [], filteredRows) }}</span>
          <div
            v-if="sortField && sortField !== 'amount'"
            class="hidden max-md:inline-flex items-center gap-1 rounded-(--radius-control) bg-[var(--bg-surface)] px-2 py-0.5 text-[12px] text-[var(--text-secondary)]"
          >
            <span>{{ sortLabel }}</span>
          </div>
          <button
            v-if="hasActiveFilterOrSort"
            type="button"
            class="min-h-[44px] min-w-[44px] cursor-pointer text-[12px] text-[var(--text-secondary)] transition-colors duration-(--dur-micro) ease-(--ease-out) hover:text-[var(--text-primary)]"
            @click="resetFiltersAndSort"
          >
            重置
          </button>
        </template>
      </DataToolbar>

      <div class="holding-mobile-list">
        <div class="holding-mobile-list__head">
          <button
            type="button"
            @click="toggleSort('name')"
          >
            <SortableTh
              label="持仓"
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
            @click="toggleSort('holding_profit')"
          >
            <SortableTh
              label="累计收益"
              align="right"
              :active="sortField === 'holding_profit'"
              :order="sortOrder"
            />
          </button>
        </div>

        <div
          v-if="loading"
          class="holding-mobile-list__loading"
          aria-label="正在加载持仓"
        >
          <div
            v-for="index in 6"
            :key="`holding-mobile-sk-${index}`"
            class="holding-mobile-row"
          >
            <span class="skeleton w-32" />
            <span class="skeleton w-14" />
            <span class="skeleton w-16" />
          </div>
        </div>

        <EmptyState
          v-else-if="filteredRows.length === 0"
          :title="hasAnyHolding ? '暂无匹配的持仓记录' : '还没有持仓记录'"
          :description="hasAnyHolding ? '调整搜索或排序后重试' : '从持仓截图识别录入，或手动新增一条持仓'"
        >
          <Button
            v-if="!hasAnyHolding"
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

        <div
          v-for="row in filteredRows"
          v-else
          :key="`holding-mobile-${row.id}`"
          class="holding-mobile-row ui-press ui-press--row"
          @click="openEdit(row)"
        >
          <span class="holding-mobile-row__identity">
            <span
              class="holding-mobile-row__name"
              :title="row.name"
            >{{ row.name }}</span>
            <span class="holding-mobile-row__meta">
              {{ row.is_cash_asset ? '现金' : row.code }}
              <template v-if="row.weight_pct != null"> · {{ formatPercent(row.weight_pct, { signed: false }) }}</template>
            </span>
          </span>
          <span class="holding-mobile-row__value">
            <MarketValue :value="row.amount" />
            <span>{{ row.amount_nav_date ? `基准日 ${formatMonthDay(row.amount_nav_date)}` : '金额基准日未记录' }}</span>
          </span>
          <span class="holding-mobile-row__value">
            <span
              v-if="row.is_cash_asset"
              class="value-muted font-numeric"
            >—</span>
            <MarketValue
              v-else
              :value="row.holding_profit"
              kind="signed-money"
              :state="row.holding_profit_state"
              tone-level="muted"
            />
            <span v-if="row.is_cash_asset">—</span>
            <span
              v-else-if="row.holding_profit_rate != null"
              class="font-numeric"
              :class="valueStateClass(row.holding_profit_rate_state, 'muted')"
            >{{ formatPercent(row.holding_profit_rate) }}</span>
            <span v-else>—</span>
          </span>
          <div
            class="holding-mobile-row__menu row-menu"
            :class="actionMenu?.id === row.id ? 'is-open' : ''"
          >
            <button
              type="button"
              class="row-menu__trigger"
              aria-haspopup="menu"
              :aria-expanded="actionMenu?.id === row.id"
              :aria-label="`${row.name} 的持仓操作`"
              @click="toggleActionMenu(row, $event)"
            >
              <Ellipsis
                class="h-4 w-4"
                :stroke-width="1.75"
              />
            </button>
          </div>
        </div>
      </div>

      <div class="holdings-table-scroll blotter-scroll">
        <table class="data-dense-table">
          <thead>
            <tr>
              <th
                class="w-[28%] min-w-[200px] cursor-pointer text-left"
                @click="toggleSort('name')"
              >
                <SortableTh
                  label="基金"
                  :active="sortField === 'name'"
                  :order="sortOrder"
                />
              </th>
              <th
                class="w-[14%] min-w-[110px] cursor-pointer text-right"
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
                class="w-[16%] min-w-[140px] cursor-pointer text-right"
                @click="toggleSort('weight_pct')"
              >
                <SortableTh
                  label="组合占比"
                  align="right"
                  :active="sortField === 'weight_pct'"
                  :order="sortOrder"
                />
              </th>
              <th class="col-priority-medium w-[13%] min-w-[110px] text-right">
                持有成本
              </th>
              <th
                class="w-[14%] min-w-[110px] cursor-pointer text-right"
                @click="toggleSort('holding_profit')"
              >
                <SortableTh
                  label="累计收益"
                  align="right"
                  :active="sortField === 'holding_profit'"
                  :order="sortOrder"
                />
              </th>
              <th
                v-if="showThemeColumn"
                class="col-priority-low w-[10%] min-w-[88px] text-left"
              >
                板块
              </th>
              <th class="w-[70px] min-w-[70px] text-right">
                操作
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
                  <span class="skeleton w-24" />
                </td>
                <td class="col-priority-medium text-right">
                  <span class="skeleton w-16" />
                </td>
                <td class="text-right">
                  <span class="skeleton w-16" />
                </td>
                <td
                  v-if="showThemeColumn"
                  class="col-priority-low"
                >
                  <span class="skeleton w-12" />
                </td>
                <td />
              </tr>
            </template>
            <tr v-else-if="filteredRows.length === 0">
              <td :colspan="showThemeColumn ? 7 : 6">
                <EmptyState
                  :title="hasAnyHolding ? '暂无匹配的持仓记录' : '还没有持仓记录'"
                  :description="hasAnyHolding ? '调整搜索或排序后重试' : '从持仓截图识别录入，或手动新增一条持仓'"
                >
                  <Button
                    v-if="!hasAnyHolding"
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
              v-for="row in filteredRows"
              :key="row.id"
              class="group cursor-pointer"
              tabindex="0"
              @click="openEdit(row)"
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
                    class="row-action rounded-(--radius-control) p-0.5 text-[var(--text-tertiary)] hover:text-[var(--text-primary)]"
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
                </div>
              </td>
              <td class="text-right">
                <MarketValue :value="row.amount" />
                <div class="fund-code">
                  {{ row.amount_nav_date ? `基准日 ${formatMonthDay(row.amount_nav_date)}` : '金额基准日未记录' }}
                </div>
              </td>
              <td class="text-right">
                <div class="weight-bar">
                  <div
                    class="weight-bar__track"
                    :aria-label="`组合占比 ${formatPercent(row.weight_pct, { signed: false })}`"
                  >
                    <div
                      class="weight-bar__fill"
                      :style="{ width: barWidth(row.weight_pct) }"
                    />
                  </div>
                  <span class="weight-bar__value font-numeric text-[var(--text-secondary)]">
                    {{ formatPercent(row.weight_pct, { signed: false }) }}
                  </span>
                </div>
              </td>
              <td class="col-priority-medium text-right text-[var(--text-secondary)]">
                <span
                  v-if="row.is_cash_asset"
                  class="text-[12px] text-[var(--text-tertiary)]"
                >现金</span>
                <MarketValue
                  v-else
                  :value="row.cost"
                />
              </td>
              <td class="text-right">
                <span
                  v-if="row.is_cash_asset"
                  class="text-[12px] text-[var(--text-tertiary)]"
                >—</span>
                <template v-else>
                  <MarketValue
                    :value="row.holding_profit"
                    kind="signed-money"
                    :state="row.holding_profit_state"
                    tone-level="muted"
                  />
                  <div
                    v-if="row.holding_profit_rate != null"
                    class="text-[12px] font-numeric"
                    :class="valueStateClass(row.holding_profit_rate_state, 'muted')"
                  >
                    {{ formatPercent(row.holding_profit_rate) }}
                  </div>
                </template>
              </td>
              <td
                v-if="showThemeColumn"
                class="col-priority-low text-[13px] text-[var(--text-secondary)]"
              >
                {{ row.theme || '—' }}
              </td>
              <td class="text-right">
                <div
                  class="row-menu"
                  :class="actionMenu?.id === row.id ? 'is-open' : ''"
                >
                  <button
                    type="button"
                    class="row-menu__trigger"
                    aria-haspopup="menu"
                    :aria-expanded="actionMenu?.id === row.id"
                    :aria-label="`${row.name} 的持仓操作`"
                    @click="toggleActionMenu(row, $event)"
                  >
                    <Ellipsis
                      class="h-4 w-4"
                      :stroke-width="1.75"
                    />
                  </button>
                </div>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>

    <Teleport to="body">
      <div
        v-if="actionMenu && actionMenuRow"
        class="row-menu__panel"
        role="menu"
        :style="{ top: `${actionMenu.top}px`, right: `${actionMenu.right}px` }"
      >
        <button
          type="button"
          class="row-menu__item"
          role="menuitem"
          @click="editFromMenu(actionMenuRow)"
        >
          编辑持仓
        </button>
        <button
          type="button"
          class="row-menu__item is-danger"
          role="menuitem"
          @click="deleteFromMenu(actionMenuRow)"
        >
          删除持仓
        </button>
      </div>
    </Teleport>

    <HoldingEditorDrawer
      v-model:show="drawerOpen"
      :holding="editingHolding"
      @saved="loadHoldings"
    />

    <Teleport to="body">
      <div
        v-if="deleteTarget"
        class="fixed inset-0 z-50 flex items-center justify-center bg-[var(--scrim-bg)] p-4"
        role="presentation"
        @keydown.esc="deleteTarget = null"
        @click.self="deleteTarget = null"
      >
        <div
          role="alertdialog"
          aria-modal="true"
          aria-labelledby="delete-holding-title"
          class="w-full max-w-sm space-y-4 rounded-(--radius-panel) bg-[var(--bg-elevated)] p-5 shadow-[var(--shadow-overlay)]"
        >
          <div class="flex items-center gap-3 text-[var(--status-error)]">
            <AlertTriangle class="h-5 w-5" />
            <h4
              id="delete-holding-title"
              class="font-medium text-[var(--text-primary)]"
            >
              确认移除持仓？
            </h4>
          </div>
          <p class="text-[13px] leading-relaxed text-[var(--text-secondary)]">
            确定要从组合中删除 <strong class="text-[var(--text-primary)]">{{ deleteTarget.name }}</strong>（{{ deleteTarget.code }}）吗？该操作不可撤销。
          </p>
          <div class="flex items-center justify-end gap-2 pt-2">
            <Button
              variant="ghost"
              size="sm"
              class="min-h-[44px]"
              @click="deleteTarget = null"
            >
              取消
            </Button>
            <Button
              variant="danger"
              size="sm"
              class="min-h-[44px]"
              :loading="deleting"
              @click="executeDelete"
            >
              确认删除
            </Button>
          </div>
        </div>
      </div>
    </Teleport>
  </div>
</template>

<style scoped>
.ui-press:not(.ui-press--row),
:deep(input) {
  min-width: 44px;
  min-height: 44px;
}

.data-dense-table th {
  height: 44px;
}

@media (min-width: 769px) {
  /* 扩大命中区，保留桌面表格的行高。 */
  .row-action,
  :deep(.row-menu__trigger) {
    position: relative;
  }

  :deep(.row-menu__trigger::after),
  .row-action::after {
    position: absolute;
    inset: -6px;
    content: '';
  }

  .row-action::after {
    inset: -20px -15px -10px;
  }
}
</style>
