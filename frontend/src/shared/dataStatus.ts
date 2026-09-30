import type { DataFreshnessResponse, FreshnessStatus, HoldingRow, HoldingsSummary } from '@/api/types'

import { beijingDateISO, formatClock, formatDateTime, formatMonthDay } from './formatters'

export type StatusTone = 'info' | 'warning' | 'error' | 'muted' | 'neutral'

export type IntradayFilter = 'all' | 'available' | 'delayed' | 'none'

export interface StatusDescriptor {
  key: 'cash' | 'intraday' | 'closed' | 'fallback' | 'stale' | 'none' | 'overseas' | 'official' | 'official-pending'
  label: string
  compactLabel: string
  detail?: string
  tone: StatusTone
  exceptional: boolean
}

function isFallbackSource(source: string | null | undefined): boolean {
  if (!source) return false
  return source.includes('sina')
}

function clockMinutes(clock: string): number | null {
  const match = clock.match(/^(\d{1,2}):(\d{2})$/)
  if (!match) return null
  return Number(match[1]) * 60 + Number(match[2])
}

function isCloseSnapshot(clock: string): boolean {
  const minutes = clockMinutes(clock)
  if (minutes == null) return false
  return minutes >= 15 * 60 && minutes < 16 * 60
}

export function freshnessStatusLabel(status: string): string {
  if (status === 'fresh') return '新鲜'
  if (status === 'stale') return '已过期'
  if (status === 'partial') return '部分成功'
  if (status === 'provider_failed') return '数据源失败'
  if (status === 'missing') return '未记录'
  if (status === 'estimate_only') return '仅估算'
  if (status === 'disclosure_lagged') return '披露滞后'
  return status
}

export function humanizeUnavailableReason(reason: string): string {
  if (reason.includes('未返回该基金') || reason.includes('均无可用')) return '盘中估值暂不可用'
  if (reason.includes('不是今日')) return '今日暂无盘中数据'
  return '暂无盘中数据'
}

export function resolveIntradayStatus(row: HoldingRow): StatusDescriptor {
  if (row.is_cash_asset) {
    return {
      key: 'cash',
      label: '现金资产',
      compactLabel: '现金',
      tone: 'neutral',
      exceptional: false,
    }
  }

  const clock = formatClock(row.estimated_at, '')
  const warning = row.estimated_warning?.trim() || undefined
  const unavailable = row.estimated_unavailable_reason?.trim() || undefined

  const recent = row.overseas_recent
  if (recent) {
    // 海外估值常跨午夜、跨休市日，来源时间始终带日期。
    const stamp = formatDateTime(recent.estimated_at, '时间未知')
    const conflict = recent.basis_status === 'conflict'
    const basis = recent.previous_nav_date
      ? `涨跌基准为 ${formatMonthDay(recent.previous_nav_date)} 官方净值`
      : '涨跌基准日未知'
    return {
      key: 'overseas',
      label: conflict ? `海外估值 · 基准冲突 · ${stamp}` : `海外估值 · ${stamp}`,
      compactLabel: conflict ? `基准冲突 · ${stamp}` : `海外 · ${stamp}`,
      detail: [
        basis,
        conflict ? '估值净值与来源涨跌幅不一致' : undefined,
        '收益归属日未确认，不计入今日预估',
      ].filter(Boolean).join('；'),
      tone: conflict ? 'warning' : 'neutral',
      exceptional: conflict,
    }
  }

  if (row.estimated_status === 'stale') {
    const stamp = formatDateTime(row.estimated_at, '')
    return {
      key: 'stale',
      label: stamp ? `数据延迟 · ${stamp}` : '数据延迟',
      compactLabel: stamp ? `延迟 · ${stamp}` : '数据延迟',
      detail: warning,
      tone: 'warning',
      exceptional: true,
    }
  }

  if (row.estimated_status === 'available' || (row.estimated_nav != null && row.estimated_at)) {
    if (isFallbackSource(row.estimated_source)) {
      return {
        key: 'fallback',
        label: clock ? `备用数据源 · ${clock}` : '备用数据源',
        compactLabel: clock ? `备用数据源 · ${clock}` : '备用数据源',
        detail: warning,
        tone: 'warning',
        exceptional: true,
      }
    }
    if (clock && isCloseSnapshot(clock)) {
      return {
        key: 'closed',
        label: `已收盘 · ${clock}`,
        compactLabel: `已收盘 · ${clock}`,
        detail: warning,
        tone: 'neutral',
        exceptional: false,
      }
    }
    return {
      key: 'intraday',
      label: clock ? `盘中估值 · ${clock}` : '盘中估值',
      compactLabel: clock ? `盘中估值 · ${clock}` : '盘中估值',
      detail: warning,
      tone: 'info',
      exceptional: Boolean(warning),
    }
  }

  const short = unavailable ? humanizeUnavailableReason(unavailable) : '暂无盘中数据'
  return {
    key: 'none',
    label: short,
    compactLabel: short,
    detail: unavailable || warning,
    tone: 'muted',
    exceptional: true,
  }
}

export function resolveOfficialStatus(row: HoldingRow, now: Date = new Date()): StatusDescriptor {
  if (row.is_cash_asset) {
    return {
      key: 'official',
      label: '现金',
      compactLabel: '现金',
      tone: 'neutral',
      exceptional: false,
    }
  }

  // 官方净值日期始终出现在行内标签里；披露状态只作为后缀。
  const dateLabel = row.nav_date ? formatMonthDay(row.nav_date) : null
  const navText = dateLabel ? `净值 ${dateLabel}` : '净值未记录'
  const status = row.nav_update_status
  const reason = row.nav_update_reason?.trim() || undefined
  const next = row.next_disclosure_date || null
  const describe = (
    key: StatusDescriptor['key'],
    suffix: string,
    tone: StatusTone,
    exceptional: boolean,
    detail?: string,
  ): StatusDescriptor => {
    const compactLabel = suffix ? `${navText} · ${suffix}` : navText
    return { key, label: `官方${compactLabel}`, compactLabel, detail, tone, exceptional }
  }

  if (status === 'source_failed') {
    return describe('official-pending', '源失败', 'error', true, reason || '官方净值数据源刷新失败')
  }

  if (status === 'market_closed') {
    return describe('official', '休市', 'muted', false, reason || '国内市场今日休市，无需等待当日更新')
  }

  if (status === 'overdue') {
    return describe('official-pending', '已滞后', 'error', true, reason || '已超过正常披露窗口仍未更新')
  }

  if (status === 'pending') {
    let suffix = '待更新'
    if (row.in_disclosure_window) suffix = '披露中'
    else if (next && next === beijingDateISO(now)) suffix = '今晚更新'
    else if (next) suffix = `待 ${formatMonthDay(next)}`
    const pendingDetail = reason
      || (row.in_disclosure_window
        ? '处于正常披露窗口，等待基金公司更新'
        : '尚未进入披露窗口')
    return describe('official-pending', suffix, 'warning', true, pendingDetail)
  }

  if (status === 'updated') {
    return describe(
      'official',
      '',
      'neutral',
      false,
      reason || (dateLabel ? `官方净值（${dateLabel}）已披露` : undefined),
    )
  }

  if (status === 'unknown_rule') {
    return describe('official-pending', '规则待核验', 'muted', true, reason || '缺少可靠净值披露规则')
  }

  // 降级兜底
  if (dateLabel) return describe('official', '', 'neutral', false)
  return describe('official-pending', '待更新', 'warning', true)
}

export function matchesIntradayFilter(row: HoldingRow, filter: IntradayFilter): boolean {
  if (filter === 'all') return true
  const key = resolveIntradayStatus(row).key
  if (filter === 'available') return isAvailableKey(row, key)
  if (filter === 'delayed') return key === 'stale' || (key === 'overseas' && !row.overseas_recent?.is_current)
  if (filter === 'none') return key === 'none'
  return true
}

function isAvailableKey(row: HoldingRow, key: StatusDescriptor['key']): boolean {
  if (key === 'overseas') return row.overseas_recent?.is_current === true
  return key === 'intraday' || key === 'fallback' || key === 'closed'
}

export function hasAutoRefreshEligible(rows: HoldingRow[]): boolean {
  return rows.some((row) => row.auto_refresh_eligible === true)
}

export function coverageCount(rows: HoldingRow[]): { available: number; total: number; text: string } {
  const funds = rows.filter((row) => !row.is_cash_asset)
  const available = funds.filter((row) => isAvailableKey(row, resolveIntradayStatus(row).key)).length
  return {
    available,
    total: funds.length,
    text: `${available} / ${funds.length}`,
  }
}

export function inventoryLabel(rows: HoldingRow[], displayed: HoldingRow[] = rows): string {
  const fundTotal = rows.filter((row) => !row.is_cash_asset).length
  const cashTotal = rows.filter((row) => row.is_cash_asset).length
  const fundShown = displayed.filter((row) => !row.is_cash_asset).length
  const cashShown = displayed.filter((row) => row.is_cash_asset).length

  if (displayed.length !== rows.length) {
    if (cashShown > 0) return `显示 ${displayed.length} 项 · ${fundShown} 只基金`
    return `显示 ${fundShown} 只基金`
  }
  if (cashTotal > 0) return `${rows.length} 项持仓 · ${fundTotal} 只基金`
  return `共 ${fundTotal} 只基金`
}

export function latestNavDate(
  rows: HoldingRow[],
  freshness?: DataFreshnessResponse | null,
): string | null {
  const dates = rows
    .map((row) => row.nav_date?.slice(0, 10))
    .filter((value): value is string => Boolean(value))
    .sort()
  if (dates.length) return dates[dates.length - 1] ?? null
  const official = freshness?.sources.find((item) => item.job_type === 'fund_market_snapshot')
  const fromJob = official?.max_data_date?.slice(0, 10)
  return fromJob || null
}

export interface SummaryStatusItem {
  key: string
  label: string
  value: string
  tone: StatusTone
  detail?: string
}

function sourceTone(status: FreshnessStatus | undefined, fallback: StatusTone): StatusTone {
  if (!status || status === 'fresh') return fallback
  if (status === 'provider_failed') return 'error'
  return 'warning'
}

function sourceValue(
  base: string,
  status: FreshnessStatus | undefined,
): string {
  if (!status || status === 'fresh') return base
  return `${base} · ${freshnessStatusLabel(status)}`
}

export function resolveSummaryStatus(
  summary: HoldingsSummary | null | undefined,
  rows: HoldingRow[],
  freshness?: DataFreshnessResponse | null,
): SummaryStatusItem[] {
  const estimateSource = freshness?.sources.find((item) => item.job_type === 'fund_intraday_estimate')
  const officialSource = freshness?.sources.find((item) => item.job_type === 'fund_market_snapshot')
  const estimateAt = formatDateTime(summary?.latest_intraday_refresh, '')
  const navDate = formatMonthDay(latestNavDate(rows, freshness), '')
  const syncedAt = formatDateTime(
    summary?.latest_refresh || officialSource?.completed_at,
    '',
  )

  return [
    {
      key: 'intraday',
      label: '盘中估值时间',
      value: sourceValue(estimateAt || '未刷新', estimateSource?.freshness_status),
      tone: sourceTone(estimateSource?.freshness_status, estimateAt ? 'info' : 'muted'),
      detail: estimateSource?.error_message
        || (estimateSource?.max_data_date ? `${estimateSource.label} ${estimateSource.max_data_date}` : undefined),
    },
    {
      key: 'official',
      label: '最新官方净值日',
      value: navDate || '待更新',
      tone: navDate ? 'neutral' : 'warning',
      detail: officialSource?.error_message
        || (officialSource?.max_data_date
          ? `${officialSource.label} ${officialSource.max_data_date}（显示当前持仓中最新的官方净值日期，各基金实际净值日以表格行内数据为准）`
          : '显示当前持仓中最新的官方净值日期，各基金实际净值日以表格行内数据为准。'),
    },
    {
      key: 'sync',
      label: '数据同步时间',
      value: sourceValue(syncedAt || '未记录', officialSource?.freshness_status),
      tone: sourceTone(officialSource?.freshness_status, syncedAt ? 'neutral' : 'muted'),
      detail: officialSource?.error_message
        || (officialSource?.completed_at ? `抓取完成 ${formatDateTime(officialSource.completed_at)}` : undefined),
    },
  ]
}

export function sourceDisplayName(
  source: string | null | undefined,
  short = false,
): string {
  if (!source) return short ? '未记录' : '实时推算'
  if (source === 'eastmoney_fund_core_diy' || source.includes('eastmoney')) {
    return short ? '东方财富' : '东方财富推算'
  }
  if (source.includes('sina')) return short ? '新浪财经' : '新浪财经推算'
  if (source.includes('tiantian')) return short ? '天天基金' : '天天基金推算'
  return source
}
