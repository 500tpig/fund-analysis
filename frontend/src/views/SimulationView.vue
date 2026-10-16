<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { AlertCircle, RefreshCw } from '@lucide/vue'

import {
  createSimulationAccount,
  fetchSimulationAccount,
  fetchSimulationAccounts,
  fetchSimulationOrder,
  fetchSimulationOrders,
  fetchSimulationPositions,
} from '@/api/client'
import type {
  SimulationAccount,
  SimulationAccountsResponse,
  SimulationOrder,
  SimulationOrderDetail,
  SimulationPosition,
} from '@/api/types'
import EmptyState from '@/components/EmptyState.vue'
import HeroMetrics, { type HeroMetric } from '@/components/HeroMetrics.vue'
import StatusBadge from '@/components/StatusBadge.vue'
import WorkbenchPageHeader from '@/components/WorkbenchPageHeader.vue'
import Button from '@/components/ui/Button.vue'
import Drawer from '@/components/ui/Drawer.vue'
import Tabs from '@/components/ui/Tabs.vue'
import {
  ACCOUNT_STATUS,
  ORDER_FILTERS,
  ORDER_STATUS,
  decimalSign,
  formatBeijing,
  formatDay,
  formatDecimal,
  formatRatio,
  formatSignedDecimal,
  reasonLabel,
} from '@/shared/simulation'

const CREATE_KEY_STORAGE = 'fundpilot.simulation.create-key'

const accounts = ref<SimulationAccountsResponse | null>(null)
const account = ref<SimulationAccount | null>(null)
const positions = ref<SimulationPosition[]>([])
const orders = ref<SimulationOrder[]>([])
const nextCursor = ref<string | null>(null)
const orderFilter = ref('all')
const loading = ref(true)
const loadingMore = ref(false)
const creating = ref(false)
const error = ref<string | null>(null)

const drawerOpen = ref(false)
const detail = ref<SimulationOrderDetail | null>(null)
const detailLoading = ref(false)
const detailError = ref<string | null>(null)

const currentAccountId = computed(() => {
  const items = accounts.value?.items ?? []
  return (items.find((item) => item.status !== 'archived') ?? items[0])?.id ?? null
})
const hasActiveAccount = computed(() => (accounts.value?.items ?? []).some((item) => item.status !== 'archived'))
const statusMeta = computed(() => (account.value ? ACCOUNT_STATUS[account.value.status] : null))
const filterStatuses = computed(() => ORDER_FILTERS.find((item) => item.key === orderFilter.value)?.statuses ?? [])

function messageOf(caught: unknown, fallback: string): string {
  return caught instanceof Error ? caught.message : fallback
}

async function loadOrders(reset: boolean) {
  const id = currentAccountId.value
  if (!id) return
  const page = await fetchSimulationOrders(id, {
    cursor: reset ? null : nextCursor.value,
    status: filterStatuses.value,
  })
  orders.value = reset ? page.items : [...orders.value, ...page.items]
  nextCursor.value = page.next_cursor
}

async function loadAll() {
  loading.value = true
  error.value = null
  try {
    accounts.value = await fetchSimulationAccounts()
    const id = currentAccountId.value
    if (!id) {
      account.value = null
      positions.value = []
      orders.value = []
      return
    }
    const [loadedAccount, loadedPositions] = await Promise.all([
      fetchSimulationAccount(id),
      fetchSimulationPositions(id),
      loadOrders(true),
    ])
    account.value = loadedAccount
    positions.value = loadedPositions.items
  } catch (caught) {
    error.value = messageOf(caught, '加载模拟账户失败')
  } finally {
    loading.value = false
  }
}

async function changeFilter(key: string) {
  orderFilter.value = key
  try {
    await loadOrders(true)
  } catch (caught) {
    error.value = messageOf(caught, '加载订单失败')
  }
}

async function loadMore() {
  loadingMore.value = true
  try {
    await loadOrders(false)
  } catch (caught) {
    error.value = messageOf(caught, '加载订单失败')
  } finally {
    loadingMore.value = false
  }
}

async function createAccount() {
  creating.value = true
  error.value = null
  // 同一次建立动作的重试复用幂等键，避免网络重试重复入金。
  let key = sessionStorage.getItem(CREATE_KEY_STORAGE)
  if (!key) {
    key = `web-${crypto.randomUUID()}`
    sessionStorage.setItem(CREATE_KEY_STORAGE, key)
  }
  try {
    await createSimulationAccount(key)
    sessionStorage.removeItem(CREATE_KEY_STORAGE)
    await loadAll()
  } catch (caught) {
    error.value = messageOf(caught, '建立模拟账户失败')
  } finally {
    creating.value = false
  }
}

async function openOrder(order: SimulationOrder) {
  drawerOpen.value = true
  detail.value = null
  detailError.value = null
  detailLoading.value = true
  try {
    detail.value = await fetchSimulationOrder(order.id)
  } catch (caught) {
    detailError.value = messageOf(caught, '加载订单详情失败')
  } finally {
    detailLoading.value = false
  }
}

const heroItems = computed<HeroMetric[]>(() => {
  const current = account.value
  if (!current) return []
  const range = current.nav_date_range
  const dateHint = range.min
    ? range.min === range.max
      ? `净值日 ${formatDay(range.min)}`
      : `净值日 ${formatDay(range.min)} 至 ${formatDay(range.max)}`
    : '暂无持仓净值'
  return [
    {
      key: 'total',
      label: '总资产',
      prefix: '¥',
      value: formatDecimal(current.balances.total),
      hint: current.quality === 'complete' ? dateHint : `数据不完整 · ${dateHint}`,
      featured: true,
    },
    {
      key: 'profit',
      label: '费用后变化',
      value: formatSignedDecimal(current.profit),
      delta: current.profit_rate !== null ? formatRatio(current.profit_rate, { signed: true }) : undefined,
      deltaToneLevel: 'muted',
      state: decimalSign(current.profit),
      hint: `起始 ¥${formatDecimal(current.initial_cash)}`,
    },
    {
      key: 'drawdown',
      label: '距高点回撤',
      value: formatRatio(current.drawdown),
      hint: `高点 ¥${formatDecimal(current.peak)}`,
      state: 'plain',
    },
  ]
})

const balanceItems = computed(() => {
  const balances = account.value?.balances
  if (!balances) return []
  return [
    { key: 'available', label: '可用现金', value: balances.available },
    { key: 'frozen', label: '冻结（待确认申购）', value: balances.frozen },
    { key: 'receivable', label: '赎回应收', value: balances.receivable },
    { key: 'dividend', label: '分红应收', value: balances.dividend_receivable },
  ]
})

function orderQuantity(order: SimulationOrder): string {
  if (order.side === 'buy') return `¥${formatDecimal(order.accepted_amount ?? order.requested_amount)}`
  return `${formatDecimal(order.confirmed_shares ?? order.requested_shares)} 份`
}

function orderReason(order: SimulationOrder): string {
  if (order.blocked_reason) return reasonLabel(order.blocked_reason)
  if (order.status === 'rejected' || order.status === 'failed') return reasonLabel(order.reason_code)
  return ''
}

const detailRows = computed(() => {
  const item = detail.value
  if (!item) return []
  const money = (value: string | null) => (value === null ? '—' : `¥${formatDecimal(value)}`)
  const rows = [
    { label: item.side === 'buy' ? '申请金额' : '申请份额', value: item.side === 'buy' ? money(item.requested_amount) : `${formatDecimal(item.requested_shares)} 份` },
    { label: '适用净值', value: item.nav_value ? formatDecimal(item.nav_value) : '—' },
    { label: '确认份额', value: item.confirmed_shares ? `${formatDecimal(item.confirmed_shares)} 份` : '—' },
    { label: '费用', value: money(item.fee) },
  ]
  if (item.side === 'buy') rows.push({ label: '净申购额', value: money(item.net_amount) })
  else {
    rows.push({ label: '赎回毛额', value: money(item.gross_amount) })
    rows.push({ label: '到账金额', value: money(item.net_amount) })
  }
  return rows
})

const detailDates = computed(() => {
  const item = detail.value
  if (!item) return []
  const rows = [
    { label: '申请', value: formatBeijing(item.submitted_at), note: '' },
    { label: '净值归属', value: formatDay(item.nav_date), note: item.blocked_reason ? reasonLabel(item.blocked_reason) : '' },
    {
      label: '确认',
      value: formatDay(item.confirmation_due_date),
      note: item.confirmed_at ? `处理于 ${formatBeijing(item.confirmed_at)}` : '',
    },
  ]
  if (item.side === 'sell') {
    rows.push({
      label: '到账',
      value: formatDay(item.payment_due_date),
      note: item.paid_at ? `处理于 ${formatBeijing(item.paid_at)}` : '',
    })
  }
  return rows
})

onMounted(() => {
  void loadAll()
})
</script>

<template>
  <div class="workbench-container flex flex-col gap-5">
    <WorkbenchPageHeader
      title="模拟账户"
      description="独立虚拟账户，与真实持仓分开记账；订单由验收命令或后续定投策略产生。"
    >
      <template #meta>
        <div class="flex flex-wrap items-center gap-2">
          <StatusBadge
            v-if="accounts?.environment === 'fixture'"
            tone="warning"
            label="隔离样例"
            title="使用 fixture 规则与人造净值，不代表支付宝实际规则"
          />
          <StatusBadge
            v-if="statusMeta && account"
            :tone="statusMeta.tone"
            :label="statusMeta.label"
            :title="reasonLabel(account.pause_reason) || undefined"
          />
        </div>
      </template>
      <template #actions>
        <Button
          variant="ghost"
          size="sm"
          :loading="loading"
          aria-label="重新读取模拟账户"
          @click="loadAll"
        >
          <template #icon>
            <RefreshCw
              class="h-3.5 w-3.5"
              :stroke-width="1.75"
            />
          </template>
          刷新
        </Button>
      </template>
    </WorkbenchPageHeader>

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
    </div>

    <div
      v-if="loading && !account"
      class="sim-skeleton"
      aria-label="正在加载模拟账户"
    >
      <span class="skeleton w-40" />
      <span class="skeleton w-64" />
    </div>

    <EmptyState
      v-else-if="!currentAccountId"
      title="还没有模拟账户"
      description="建立后获得 200,000.00 元虚拟现金，不复制、不修改真实持仓。"
    >
      <Button
        variant="primary"
        :loading="creating"
        @click="createAccount"
      >
        建立模拟账户
      </Button>
    </EmptyState>

    <template v-else-if="account">
      <p
        v-if="!hasActiveAccount"
        class="sim-note"
      >
        当前查看的是已归档实验，仅供追溯。
      </p>

      <HeroMetrics :items="heroItems" />

      <dl class="sim-balances">
        <div
          v-for="item in balanceItems"
          :key="item.key"
          class="sim-balances__item"
        >
          <dt>{{ item.label }}</dt>
          <dd class="font-numeric tabular-nums">
            ¥{{ formatDecimal(item.value) }}
          </dd>
        </div>
      </dl>

      <p
        v-if="account.missing.length"
        class="sim-note sim-note--warn"
      >
        数据不完整：
        <template
          v-for="(item, index) in account.missing"
          :key="`${item.code}-${item.reason}`"
        >
          {{ index ? '；' : '' }}{{ item.code }} {{ reasonLabel(item.reason) }}<template v-if="item.expected_nav_date">
            （应有 {{ formatDay(item.expected_nav_date) }}，最新 {{ formatDay(item.nav_date) }}）
          </template>
        </template>
      </p>

      <section
        class="sim-section"
        aria-labelledby="sim-positions-title"
      >
        <h2
          id="sim-positions-title"
          class="sim-section__title"
        >
          持仓
          <span>{{ positions.length }} 只</span>
        </h2>
        <EmptyState
          v-if="!positions.length"
          title="暂无已确认份额"
        />
        <ul
          v-else
          class="sim-list"
        >
          <li
            v-for="item in positions"
            :key="item.code"
            class="sim-row"
          >
            <span class="sim-row__identity">
              <span
                class="sim-row__name"
                :title="item.name"
              >{{ item.name }}</span>
              <span class="sim-row__meta">{{ item.code }} · {{ item.lot_count }} 个批次</span>
            </span>
            <span class="sim-row__value">
              <span class="font-numeric">{{ formatDecimal(item.shares) }} 份</span>
              <span>{{ decimalSign(item.frozen_shares) !== 'neutral' ? `冻结 ${formatDecimal(item.frozen_shares)}` : `成本 ¥${formatDecimal(item.net_cost)}` }}</span>
            </span>
            <span class="sim-row__value">
              <span class="font-numeric">¥{{ formatDecimal(item.value) }}</span>
              <span :class="item.issues.length ? 'sim-warn' : ''">
                {{ item.nav_date ? `净值 ${formatDay(item.nav_date)}` : '无净值' }}{{ item.issues.length ? ` · ${reasonLabel(item.issues[0].reason)}` : '' }}
              </span>
            </span>
          </li>
        </ul>
      </section>

      <section
        class="sim-section"
        aria-labelledby="sim-orders-title"
      >
        <div class="sim-section__head">
          <h2
            id="sim-orders-title"
            class="sim-section__title"
          >
            订单
            <span v-if="account.pending_order_count">{{ account.pending_order_count }} 笔处理中</span>
          </h2>
          <Tabs
            :model-value="orderFilter"
            :options="ORDER_FILTERS.map(({ key, label }) => ({ key, label }))"
            size="sm"
            @change="changeFilter"
          />
        </div>
        <EmptyState
          v-if="!orders.length"
          title="暂无订单"
        />
        <ul
          v-else
          class="sim-list"
        >
          <li
            v-for="order in orders"
            :key="order.id"
          >
            <button
              type="button"
              class="sim-row sim-row--button"
              :aria-label="`${order.side === 'buy' ? '申购' : '赎回'} ${order.name ?? order.code}，${ORDER_STATUS[order.status].label}，查看详情`"
              @click="openOrder(order)"
            >
              <span class="sim-row__identity">
                <span
                  class="sim-row__name"
                  :title="order.name ?? order.code"
                >
                  <span :class="['sim-side', order.side === 'buy' ? 'sim-side--buy' : 'sim-side--sell']">{{ order.side === 'buy' ? '买' : '卖' }}</span>
                  {{ order.name ?? order.code }}
                </span>
                <span class="sim-row__meta">申请 {{ formatBeijing(order.submitted_at) }}</span>
              </span>
              <span class="sim-row__value">
                <span class="font-numeric">{{ orderQuantity(order) }}</span>
                <span>{{ order.nav_date ? `净值日 ${formatDay(order.nav_date)}` : '—' }}</span>
              </span>
              <span class="sim-row__value">
                <StatusBadge
                  :tone="order.blocked_reason ? 'warning' : ORDER_STATUS[order.status].tone"
                  :label="ORDER_STATUS[order.status].label"
                />
                <span class="sim-row__reason">{{ orderReason(order) || ' ' }}</span>
              </span>
            </button>
          </li>
        </ul>
        <div
          v-if="nextCursor"
          class="sim-more"
        >
          <Button
            variant="ghost"
            size="sm"
            :loading="loadingMore"
            @click="loadMore"
          >
            加载更多
          </Button>
        </div>
      </section>
    </template>

    <Drawer
      v-model:show="drawerOpen"
      :title="detail ? `${detail.side === 'buy' ? '申购' : '赎回'} · ${detail.name ?? detail.code}` : '订单详情'"
    >
      <div
        v-if="detailLoading"
        class="sim-skeleton"
      >
        <span class="skeleton w-40" />
        <span class="skeleton w-56" />
      </div>
      <p
        v-else-if="detailError"
        class="sim-note sim-note--warn"
      >
        {{ detailError }}
      </p>
      <div
        v-else-if="detail"
        class="sim-detail"
      >
        <div class="sim-detail__status">
          <StatusBadge
            :tone="ORDER_STATUS[detail.status].tone"
            :label="ORDER_STATUS[detail.status].label"
          />
          <span v-if="orderReason(detail)">{{ orderReason(detail) }}</span>
          <span v-if="detail.reason_detail">{{ detail.reason_detail }}</span>
        </div>

        <h3 class="sim-detail__heading">
          日期
        </h3>
        <dl class="sim-detail__grid">
          <template
            v-for="row in detailDates"
            :key="row.label"
          >
            <dt>{{ row.label }}</dt>
            <dd>
              <span class="font-numeric">{{ row.value }}</span>
              <span
                v-if="row.note"
                class="sim-detail__note"
              >{{ row.note }}</span>
            </dd>
          </template>
        </dl>

        <h3 class="sim-detail__heading">
          金额与份额
        </h3>
        <dl class="sim-detail__grid">
          <template
            v-for="row in detailRows"
            :key="row.label"
          >
            <dt>{{ row.label }}</dt>
            <dd class="font-numeric">
              {{ row.value }}
            </dd>
          </template>
        </dl>

        <template v-if="detail.allocations.length">
          <h3 class="sim-detail__heading">
            卖出批次（先进先出）
          </h3>
          <ul class="sim-detail__list">
            <li
              v-for="lot in detail.allocations"
              :key="lot.lot_id"
            >
              确认日 {{ formatDay(lot.lot_confirmed_date) }} · <span class="font-numeric">{{ formatDecimal(lot.shares) }} 份</span>
            </li>
          </ul>
        </template>

        <h3 class="sim-detail__heading">
          状态记录
        </h3>
        <ol class="sim-detail__list">
          <li
            v-for="(event, index) in detail.events"
            :key="index"
          >
            <span class="font-numeric">{{ formatBeijing(event.occurred_at) }}</span>
            {{ event.to_status === 'accepted' ? '已受理' : ORDER_STATUS[event.to_status as keyof typeof ORDER_STATUS]?.label ?? event.to_status }}
            <span
              v-if="event.reason"
              class="sim-detail__note"
            >{{ reasonLabel(event.reason) }}</span>
          </li>
        </ol>

        <template v-if="detail.rule">
          <h3 class="sim-detail__heading">
            规则
          </h3>
          <p class="sim-detail__rule">
            {{ detail.rule.id }} · {{ detail.rule.environment === 'fixture' ? '隔离样例' : '生产' }}
            <template v-if="detail.rule.evidence?.evidence_ref">
              <br>{{ detail.rule.evidence.evidence_ref }}
            </template>
          </p>
        </template>
      </div>
    </Drawer>
  </div>
</template>

<style scoped>
.sim-skeleton {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 16px 0;
}

.sim-note {
  color: var(--text-secondary);
  font-size: 12px;
  line-height: 18px;
}

.sim-note--warn,
.sim-warn {
  color: var(--status-warning);
}

.sim-balances {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 12px;
  margin: 0;
}

.sim-balances__item {
  min-width: 0;
  padding: 10px 12px;
  border: 1px solid var(--border-subtle);
  border-radius: 8px;
}

.sim-balances__item dt {
  color: var(--text-secondary);
  font-size: 12px;
  line-height: 18px;
}

.sim-balances__item dd {
  margin: 4px 0 0;
  overflow: hidden;
  color: var(--text-primary);
  font-size: 15px;
  font-weight: 550;
  line-height: 22px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.sim-section {
  display: flex;
  flex-direction: column;
  border-top: 1px solid var(--border-subtle);
}

.sim-section__head {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 8px 12px;
}

.sim-section__title {
  display: flex;
  align-items: baseline;
  gap: 8px;
  padding: 12px 0;
  color: var(--text-primary);
  font-size: 15px;
  font-weight: 560;
  line-height: 22px;
}

.sim-section__title span {
  color: var(--text-tertiary);
  font-size: 12px;
  font-weight: 400;
}

.sim-list {
  margin: 0;
  padding: 0;
  list-style: none;
}

.sim-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(92px, auto) minmax(96px, auto);
  column-gap: 16px;
  align-items: center;
  width: 100%;
  min-height: 64px;
  padding: 10px 0;
  border-bottom: 1px solid var(--border-subtle);
  text-align: left;
}

.sim-row--button {
  cursor: pointer;
}

.sim-row--button:hover,
.sim-row--button:focus-visible {
  background: var(--bg-surface-hover);
}

.sim-row--button:focus-visible {
  outline: 2px solid var(--brand-accent);
  outline-offset: -2px;
}

.sim-row__identity,
.sim-row__value {
  display: flex;
  min-width: 0;
  flex-direction: column;
  gap: 4px;
}

.sim-row__value {
  align-items: flex-end;
  text-align: right;
}

.sim-row__name {
  overflow: hidden;
  color: var(--text-primary);
  font-size: 14px;
  font-weight: 540;
  line-height: 20px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.sim-row__meta,
.sim-row__value > span:last-child {
  overflow: hidden;
  color: var(--text-tertiary);
  font-size: 12px;
  line-height: 16px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.sim-row__value > .font-numeric {
  color: var(--text-primary);
  font-size: 14px;
  font-weight: 550;
  line-height: 20px;
  white-space: nowrap;
}

.sim-row__reason {
  max-width: 10em;
}

.sim-side {
  display: inline-flex;
  margin-right: 4px;
  padding: 0 4px;
  border-radius: 4px;
  font-size: 12px;
  line-height: 18px;
}

.sim-side--buy {
  background: var(--brand-accent-muted);
  color: var(--text-primary);
}

.sim-side--sell {
  background: var(--bg-surface);
  color: var(--text-secondary);
}

.sim-more {
  display: flex;
  justify-content: center;
  padding: 12px 0;
}

.sim-detail {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.sim-detail__status {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  color: var(--text-secondary);
  font-size: 13px;
  line-height: 20px;
}

.sim-detail__heading {
  margin-top: 12px;
  color: var(--text-secondary);
  font-size: 12px;
  font-weight: 500;
  line-height: 18px;
}

.sim-detail__grid {
  display: grid;
  grid-template-columns: 6.5em minmax(0, 1fr);
  gap: 6px 12px;
  margin: 0;
  font-size: 13px;
  line-height: 20px;
}

.sim-detail__grid dt {
  color: var(--text-tertiary);
}

.sim-detail__grid dd {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 8px;
  margin: 0;
  color: var(--text-primary);
}

.sim-detail__note {
  color: var(--text-tertiary);
  font-size: 12px;
}

.sim-detail__list {
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin: 0;
  padding: 0;
  color: var(--text-primary);
  font-size: 13px;
  line-height: 20px;
  list-style: none;
}

.sim-detail__rule {
  overflow-wrap: anywhere;
  color: var(--text-secondary);
  font-size: 12px;
  line-height: 18px;
}

@media (max-width: 768px) {
  .sim-balances {
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 8px;
  }

  .sim-row {
    grid-template-columns: minmax(0, 1fr) minmax(76px, auto) minmax(72px, auto);
    column-gap: 10px;
  }

  .sim-row__reason {
    max-width: 7em;
  }
}
</style>
