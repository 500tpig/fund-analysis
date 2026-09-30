/**
 * 估值页自动刷新策略（纯函数，便于脚本回归）。
 *
 * - 只在页面可见时判断；隐藏时由调用方停掉检查计时器。
 * - 交易行情与净值披露分别判断：任一行情处于交易时段按 5 分钟，
 *   否则处于国内披露窗口（19:00-24:00）按 10 分钟，其余时间不轮询。
 * - 逾期、源失败或行情过旧只触发一次性补刷，并有冷却时间。
 * - 最近一次刷新时间取服务端数据源运行记录，跨标签页、重新进入页面都不重复请求。
 * - 连续失败按间隔翻倍退避，封顶 30 分钟；连续 3 次后暂停自动刷新，手动刷新或重新可见后恢复。
 *
 * 本文件只依赖类型，便于 node 直接执行 scripts/check-refresh-policy.mjs。
 */
import type { FundsRefreshResponse, FundsResponse } from '@/api/types'

export const INTRADAY_INTERVAL_MS = 5 * 60_000
export const DISCLOSURE_INTERVAL_MS = 10 * 60_000
export const ONE_SHOT_COOLDOWN_MS = 3 * 60_000
export const MAX_BACKOFF_MS = 30 * 60_000
export const MAX_AUTO_FAILURES = 3
export const CHECK_TICK_MS = 30_000

type Monitor = Pick<FundsResponse, 'rows' | 'market_quotes' | 'data_freshness'>

export function pollingInterval(monitor: Monitor | null | undefined): number | null {
  if (!monitor) return null
  const trading = (monitor.market_quotes?.items ?? []).some((item) => item.session_status === 'trading')
  if (trading) return INTRADAY_INTERVAL_MS
  const disclosure = monitor.rows.some((row) => row.in_disclosure_window === true)
  if (disclosure) return DISCLOSURE_INTERVAL_MS
  return null
}

export function needsOneShot(monitor: Monitor | null | undefined): boolean {
  if (!monitor) return false
  return monitor.rows.some((row) => row.auto_refresh_eligible === true)
    || monitor.market_quotes?.refresh_eligible === true
}

export function backoffDelay(base: number, failures: number): number {
  return Math.min(base * 2 ** Math.max(0, failures), MAX_BACKOFF_MS)
}

/** 服务端最近一次刷新完成时间（任一数据源运行记录），毫秒时间戳。 */
export function lastRunAt(monitor: Monitor | null | undefined): number | null {
  const stamps = (monitor?.data_freshness?.sources ?? [])
    .map((source) => (source.completed_at ? Date.parse(source.completed_at) : Number.NaN))
    .filter((value) => Number.isFinite(value))
  return stamps.length ? Math.max(...stamps) : null
}

export interface AutoRefreshInput {
  monitor: Monitor | null | undefined
  now: number
  /** 本页最近一次发起刷新的时间；与服务端记录取较晚者 */
  lastAttemptAt: number | null
  failures: number
}

export function shouldAutoRefresh(input: AutoRefreshInput): boolean {
  const { monitor, now, failures } = input
  if (!monitor || failures >= MAX_AUTO_FAILURES) return false
  const last = Math.max(input.lastAttemptAt ?? -Infinity, lastRunAt(monitor) ?? -Infinity)
  const elapsed = Number.isFinite(last) ? now - last : Number.POSITIVE_INFINITY
  const interval = pollingInterval(monitor)
  if (interval != null && elapsed >= backoffDelay(interval, failures)) return true
  return needsOneShot(monitor) && elapsed >= backoffDelay(ONE_SHOT_COOLDOWN_MS, failures)
}

/**
 * 首次进入或重新进入估值页面时的补拉判断。
 *
 * 仅在初次加载（loadFunds）路径判断一次，不进入常规 30 秒轮询。
 * 复用服务端最近运行时间、3 分钟冷却（ONE_SHOT_COOLDOWN_MS）与失败退避。
 */
export function shouldInitialBackfill(input: AutoRefreshInput): boolean {
  const { monitor, now, failures } = input
  if (!monitor || failures >= MAX_AUTO_FAILURES) return false
  const last = Math.max(input.lastAttemptAt ?? -Infinity, lastRunAt(monitor) ?? -Infinity)
  const elapsed = Number.isFinite(last) ? now - last : Number.POSITIVE_INFINITY
  return elapsed >= backoffDelay(ONE_SHOT_COOLDOWN_MS, failures)
}

/** 请求成功但所有来源都失败时，同样计入连续失败。 */
export function isFailedRun(result: FundsRefreshResponse): boolean {
  return result.has_failure
    && result.success === 0
    && result.estimate_success === 0
    && result.quote_success === 0
}

export interface SourceUpdates {
  estimates: number
  navs: number
  quotes: number
}

/** 对比刷新前后的来源时间，区分「请求完成」与「来源数据实际更新」。 */
export function diffSourceUpdates(
  previous: Monitor | null | undefined,
  next: Monitor,
): SourceUpdates {
  const before = new Map((previous?.rows ?? []).map((row) => [row.code, row]))
  let estimates = 0
  let navs = 0
  for (const row of next.rows) {
    if (row.is_cash_asset) continue
    const old = before.get(row.code)
    const estimateAt = row.overseas_recent?.estimated_at ?? row.estimated_at
    const oldEstimateAt = old?.overseas_recent?.estimated_at ?? old?.estimated_at
    if (estimateAt && estimateAt !== oldEstimateAt) estimates += 1
    if (row.nav_date && row.nav_date !== old?.nav_date) navs += 1
  }
  const oldQuotes = new Map((previous?.market_quotes?.items ?? []).map((item) => [item.symbol, item.quoted_at]))
  const quotes = (next.market_quotes?.items ?? [])
    .filter((item) => item.quoted_at && item.quoted_at !== oldQuotes.get(item.symbol))
    .length
  return { estimates, navs, quotes }
}

export function describeSourceUpdates(updates: SourceUpdates): string {
  const parts: string[] = []
  if (updates.estimates) parts.push(`估值 ${updates.estimates} 只`)
  if (updates.navs) parts.push(`净值 ${updates.navs} 只`)
  if (updates.quotes) parts.push(`行情 ${updates.quotes} 项`)
  return parts.length ? `来源更新：${parts.join('、')}` : '来源暂无新数据'
}
