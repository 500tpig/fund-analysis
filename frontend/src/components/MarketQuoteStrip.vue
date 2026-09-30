<script setup lang="ts">
import type { MarketQuoteItem, MarketQuotesResponse, ValueState } from '@/api/types'
import { formatPercent, signedState, valueStateClass } from '@/shared/formatters'

interface Props {
  quotes: MarketQuotesResponse | null | undefined
}

defineProps<Props>()

function price(value: number | null, fallback = '—'): string {
  if (value == null) return fallback
  return value.toLocaleString('zh-CN', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })
}

function quoteState(item: MarketQuoteItem): ValueState {
  return signedState(item.change_pct)
}

function sessionClass(item: MarketQuoteItem): string {
  if (item.status === 'failed' || item.status === 'stale') return 'is-warning'
  if (item.session_status === 'trading') return 'is-live'
  return 'is-muted'
}
</script>

<template>
  <section
    v-if="quotes?.items?.length"
    class="market-quote-compact-strip"
    aria-label="市场行情"
  >
    <div class="market-quote-compact-scroll">
      <div class="market-quote-compact-list">
        <div
          v-for="item in quotes.items"
          :key="item.symbol"
          class="market-quote-item"
        >
          <span
            class="market-quote-item__dot"
            :class="sessionClass(item)"
            :title="item.session_label"
          />
          <span class="market-quote-item__name">{{ item.name }}</span>
          <span class="market-quote-item__price font-numeric desktop-only">{{ price(item.last_price) }}</span>
          <span
            class="market-quote-item__pct font-numeric"
            :class="valueStateClass(quoteState(item))"
          >{{ formatPercent(item.change_pct) }}</span>
        </div>
      </div>
    </div>
  </section>
</template>

<style scoped>
.market-quote-compact-strip {
  min-width: 0;
  max-width: 100%;
  box-sizing: border-box;
  overflow: hidden;
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-sm);
  background: var(--bg-soft);
  padding: 8px 14px;
}

.market-quote-compact-scroll {
  min-width: 0;
  max-width: 100%;
  overflow-x: auto;
  scrollbar-width: none;
  -webkit-overflow-scrolling: touch;
}

.market-quote-compact-scroll::-webkit-scrollbar {
  display: none;
}

.market-quote-compact-list {
  display: flex;
  align-items: center;
  gap: 20px;
  min-width: max-content;
}

.market-quote-item {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  white-space: nowrap;
}

.market-quote-item__dot {
  display: inline-block;
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: currentColor;
  flex-shrink: 0;
}

.market-quote-item__dot.is-live {
  color: var(--status-live);
}

.market-quote-item__dot.is-warning {
  color: var(--status-warning);
}

.market-quote-item__dot.is-muted {
  color: var(--text-tertiary);
}

.market-quote-item__name {
  color: var(--text-secondary);
  font-weight: 500;
}

.market-quote-item__price {
  color: var(--text-primary);
  font-size: 13px;
}

.market-quote-item__pct {
  font-size: 13px;
  font-weight: 500;
}

@media (max-width: 768px) {
  .market-quote-compact-strip {
    padding: 7px 12px;
    border-radius: 6px;
  }

  .market-quote-compact-list {
    gap: 14px;
  }

  .market-quote-item {
    font-size: 12px;
    gap: 5px;
  }

  .desktop-only {
    display: none;
  }

  .market-quote-item__pct {
    font-size: 12px;
  }
}
</style>
