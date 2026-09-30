<script setup lang="ts">
import { computed, ref, watch } from 'vue'

import { fetchPortfolioRisk } from '@/api/client'
import type {
  PortfolioAnalysis,
  PortfolioBreakdown,
  PortfolioRiskResponse,
} from '@/api/types'
import MarketValue from '@/components/MarketValue.vue'
import Button from '@/components/ui/Button.vue'
import Drawer from '@/components/ui/Drawer.vue'
import { formatPercent } from '@/shared/formatters'

const props = defineProps<{
  analysis: PortfolioAnalysis
  hasFundHoldings: boolean
}>()

const drawerOpen = ref(false)
const risk = ref<PortfolioRiskResponse | null>(null)
const riskLoading = ref(false)
const riskError = ref<string | null>(null)
let riskRequestId = 0

const distributionPanels = computed(() => ([
  { key: 'asset_class', title: '资产类别', breakdown: props.analysis.distribution.asset_class },
  { key: 'region', title: '估值市场', breakdown: props.analysis.distribution.region },
  { key: 'currency', title: '币种', breakdown: props.analysis.distribution.currency },
  { key: 'industry', title: '行业', breakdown: props.analysis.distribution.industry },
]))

const topHoldings = computed(() => props.analysis.concentration.holdings.slice(0, 5))
const topContributions = computed(() => (
  [...props.analysis.contribution.items]
    .sort((left, right) => {
      const delta = Math.abs(right.estimated_profit ?? 0) - Math.abs(left.estimated_profit ?? 0)
      if (delta !== 0) return delta
      return left.code.localeCompare(right.code)
    })
    .slice(0, 5)
))
const topPairs = computed(() => (
  (risk.value?.pairs ?? [])
    .filter((item) => item.correlation != null)
    .slice(0, 6)
))

async function loadRisk(): Promise<void> {
  if (!props.hasFundHoldings) {
    risk.value = null
    riskError.value = null
    riskLoading.value = false
    return
  }
  const requestId = ++riskRequestId
  riskLoading.value = true
  riskError.value = null
  try {
    const payload = await fetchPortfolioRisk('1y')
    if (requestId !== riskRequestId) return
    risk.value = payload
  } catch (caught) {
    if (requestId !== riskRequestId) return
    risk.value = null
    riskError.value = caught instanceof Error ? caught.message : '加载净值风险指标失败'
  } finally {
    if (requestId === riskRequestId) riskLoading.value = false
  }
}

watch(
  () => [props.hasFundHoldings, props.analysis.concentration.holdings.map((item) => item.code).join(',')] as const,
  () => {
    risk.value = null
    riskError.value = null
    if (drawerOpen.value) void loadRisk()
  },
)

watch(drawerOpen, (open) => {
  if (open && risk.value == null && !riskLoading.value) void loadRisk()
})

function openDrawer(): void {
  drawerOpen.value = true
}

function closeDrawer(): void {
  drawerOpen.value = false
}

function barWidth(weight: number | null | undefined): string {
  if (weight == null || weight <= 0) return '0%'
  return `${Math.min(weight, 100)}%`
}

function breakdownCaption(breakdown: PortfolioBreakdown): string {
  if (!breakdown.available) return ''
  return breakdown.note || ''
}

function formatCorr(value: number | null): string {
  if (value == null) return '—'
  return value.toFixed(3)
}
</script>

<template>
  <div
    class="portfolio-analysis"
    :class="drawerOpen ? 'is-open' : ''"
  >
    <button
      type="button"
      class="portfolio-analysis__summary"
      :aria-expanded="drawerOpen"
      aria-haspopup="dialog"
      @click="openDrawer"
    >
      <span class="portfolio-analysis__title">组合分析</span>
      <span class="portfolio-analysis__meta">
        最大单只 {{ formatPercent(analysis.concentration.max_weight_pct, { signed: false }) }}
        · 前五 {{ formatPercent(analysis.concentration.top5_weight_pct, { signed: false }) }}
      </span>
    </button>

    <Drawer
      v-model:show="drawerOpen"
      title="组合分析"
      width="clamp(620px, 42vw, 760px)"
      @close="closeDrawer"
    >
      <template #header>
        <div class="min-w-0 pr-4">
          <h2 class="truncate text-[20px] font-medium leading-7">
            组合分析
          </h2>
          <p class="mt-1 text-[12px] leading-[18px] text-(--text-secondary)">
            最大单只 {{ formatPercent(analysis.concentration.max_weight_pct, { signed: false }) }}
            · 前五 {{ formatPercent(analysis.concentration.top5_weight_pct, { signed: false }) }}
          </p>
        </div>
      </template>

      <div class="portfolio-analysis__body">
        <div class="portfolio-analysis__dist">
          <article
            v-for="panel in distributionPanels"
            :key="panel.key"
            class="portfolio-analysis__card"
          >
            <header class="portfolio-analysis__card-head">
              <h3>{{ panel.title }}</h3>
              <p v-if="breakdownCaption(panel.breakdown)">
                {{ breakdownCaption(panel.breakdown) }}
              </p>
            </header>
            <ul
              v-if="panel.breakdown.available && panel.breakdown.items.length"
              class="portfolio-analysis__bars"
            >
              <li
                v-for="item in panel.breakdown.items"
                :key="item.key"
              >
                <div class="portfolio-analysis__bar-row">
                  <span class="portfolio-analysis__bar-label">{{ item.label }}</span>
                  <span class="font-numeric">{{ formatPercent(item.weight_pct, { signed: false }) }}</span>
                </div>
                <div
                  class="weight-bar__track portfolio-analysis__track"
                  :aria-label="`${item.label} ${formatPercent(item.weight_pct, { signed: false })}`"
                >
                  <div
                    class="weight-bar__fill"
                    :style="{ width: barWidth(item.weight_pct) }"
                  />
                </div>
              </li>
            </ul>
            <p
              v-else
              class="portfolio-analysis__empty"
            >
              {{ panel.breakdown.available ? '暂无持仓金额' : (panel.breakdown.reason || '暂无可靠口径') }}
            </p>
          </article>
        </div>

        <div class="portfolio-analysis__split">
          <article class="portfolio-analysis__card">
            <header class="portfolio-analysis__card-head">
              <h3>集中度</h3>
              <p>按当前持仓金额</p>
            </header>
            <div class="portfolio-analysis__stats">
              <div>
                <span>最大单只</span>
                <strong class="font-numeric">{{ formatPercent(analysis.concentration.max_weight_pct, { signed: false }) }}</strong>
              </div>
              <div>
                <span>前三</span>
                <strong class="font-numeric">{{ formatPercent(analysis.concentration.top3_weight_pct, { signed: false }) }}</strong>
              </div>
              <div>
                <span>前五</span>
                <strong class="font-numeric">{{ formatPercent(analysis.concentration.top5_weight_pct, { signed: false }) }}</strong>
              </div>
            </div>
            <ul
              v-if="topHoldings.length"
              class="portfolio-analysis__list"
            >
              <li
                v-for="item in topHoldings"
                :key="item.code"
              >
                <span
                  class="portfolio-analysis__list-name"
                  :title="item.name"
                >{{ item.name }}</span>
                <span class="font-numeric">{{ formatPercent(item.weight_pct, { signed: false }) }}</span>
              </li>
            </ul>
          </article>

          <article class="portfolio-analysis__card">
            <header class="portfolio-analysis__card-head">
              <h3>今日可核对贡献</h3>
              <p>{{ analysis.contribution.note }}</p>
            </header>
            <ul
              v-if="topContributions.length"
              class="portfolio-analysis__list"
            >
              <li
                v-for="item in topContributions"
                :key="item.code"
              >
                <span
                  class="portfolio-analysis__list-name"
                  :title="item.name"
                >{{ item.name }}</span>
                <MarketValue
                  :value="item.estimated_profit"
                  kind="signed-money"
                  :state="item.estimated_profit_state"
                />
              </li>
            </ul>
            <p
              v-else
              class="portfolio-analysis__empty"
            >
              暂无可核对收益，不计算贡献
            </p>
          </article>
        </div>

        <p class="portfolio-analysis__note">
          底层持仓重合度：{{ analysis.overlap.reason }}
        </p>

        <article class="portfolio-analysis__card">
          <header class="portfolio-analysis__card-head">
            <h3>净值波动与相关</h3>
            <p>{{ risk?.basis.note || '近 1 年官方净值，样本不足 20 个交易日不计算' }}</p>
          </header>
          <div
            v-if="riskLoading"
            class="portfolio-analysis__empty"
          >
            正在读取官方净值历史
          </div>
          <div
            v-else-if="riskError"
            class="portfolio-analysis__error"
          >
            <span>{{ riskError }}</span>
            <Button
              size="sm"
              variant="ghost"
              @click="loadRisk"
            >
              重试
            </Button>
          </div>
          <template v-else-if="risk">
            <div class="portfolio-analysis__risk">
              <div class="portfolio-analysis__table-wrap">
                <table class="portfolio-analysis__table">
                  <thead>
                    <tr>
                      <th>
                        基金
                      </th>
                      <th class="text-right">
                        波动率
                      </th>
                      <th class="text-right">
                        最大回撤
                      </th>
                      <th class="text-right">
                        样本
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr
                      v-for="item in risk.funds"
                      :key="item.code"
                    >
                      <td>
                        <span
                          class="portfolio-analysis__list-name"
                          :title="item.name"
                        >{{ item.name }}</span>
                      </td>
                      <td class="text-right font-numeric">
                        {{ item.volatility_pct == null ? '样本不足' : formatPercent(item.volatility_pct, { signed: false }) }}
                      </td>
                      <td class="text-right font-numeric">
                        {{ item.max_drawdown_pct == null ? '样本不足' : formatPercent(item.max_drawdown_pct, { signed: false }) }}
                      </td>
                      <td class="text-right font-numeric">
                        {{ item.sample_count }}
                      </td>
                    </tr>
                  </tbody>
                </table>
              </div>
              <ul
                v-if="topPairs.length"
                class="portfolio-analysis__list portfolio-analysis__pairs"
              >
                <li
                  v-for="item in topPairs"
                  :key="`${item.code_a}-${item.code_b}`"
                >
                  <span
                    class="portfolio-analysis__list-name"
                    :title="`${item.name_a} / ${item.name_b}`"
                  >{{ item.name_a }} / {{ item.name_b }}</span>
                  <span class="font-numeric">{{ formatCorr(item.correlation) }}</span>
                </li>
              </ul>
              <p
                v-else
                class="portfolio-analysis__empty"
              >
                重叠样本不足，不计算相关系数
              </p>
            </div>
            <p
              v-if="risk.failures.length"
              class="portfolio-analysis__note"
            >
              {{ risk.failures.length }} 只基金净值历史读取失败
            </p>
          </template>
          <p
            v-else
            class="portfolio-analysis__empty"
          >
            暂无基金持仓
          </p>
        </article>
      </div>
    </Drawer>
  </div>
</template>
