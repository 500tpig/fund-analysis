import type {
  DisplaySession,
  HoldingRow,
  ProfitMeasurement,
  SessionProfit,
  TodayProfitKind,
  TodayProfitSummary,
  ValueState,
} from '@/api/types'

export interface TodayDisplay {
  amount: number | null
  kind: TodayProfitKind
  label: string
  confirmed: boolean
  returnDate: string | null
}

export interface SessionProfitView {
  amount: number | null
  returnPct: number | null
  returnDate: string | null
  kind: 'official_measurement' | 'estimate' | null
  inSession: boolean
  badges: string[]
  isCash: boolean
}

export type RowSessionProfit = SessionProfitView

export interface TodayHeroCopyResult {
  label: string
  amount: number | null
  ratePct: number | null
  hint?: string
  mode?: 'today' | 'latest_official'
  displayDate?: string | null
  count?: number
  total?: number
  officialCount?: number
  estimateCount?: number
  infoDetails: string[]
}

const REASON_LABELS: Record<string, string> = {
  amount_basis_unknown: '金额基准日未确认',
  amount_basis_unverified: '金额日期未能与涨跌前净值基准对应',
  amount_basis_mismatch: '金额基准日与收益日不匹配',
  amount_missing: '缺少持仓金额',
  official_nav_not_target_date: '官方净值不属于该收益日',
  official_nav_missing: '缺少当日官方净值',
  official_requires_amount_basis: '官方净值已披露，但金额基准未确认',
  estimate_not_target_date: '盘中估值不属于该收益日',
  estimate_incomplete: '盘中估值不完整',
  estimate_basis_unverified: '估值涨跌基准待核验',
  estimate_basis_conflict: '净值比与来源涨跌幅不一致',
  estimate_return_date_unverified: '海外收益归属日未确认',
  sina_pre_date_not_return_date: '未将估值点日期当作海外收益日',
  previous_nav_not_pre_value: '估值前值日期必须早于估值日',
  imported_undated: '历史导入收益没有日期',
  cash_asset: '现金资产不计算基金当日收益',
  holding_profit_source_conflict: '持有收益金额与导入收益率口径不一致',
}

function parseMMDD(dateStr: string | null | undefined): string {
  if (!dateStr) return ''
  const trimmed = dateStr.trim()
  if (trimmed.length >= 10 && trimmed[4] === '-') {
    return trimmed.slice(5, 10)
  }
  return trimmed
}

function formatSimpleDateTime(isoStr: string | null | undefined): string {
  if (!isoStr) return ''
  try {
    const d = new Date(isoStr)
    if (isNaN(d.getTime())) return isoStr
    const m = String(d.getMonth() + 1).padStart(2, '0')
    const day = String(d.getDate()).padStart(2, '0')
    const h = String(d.getHours()).padStart(2, '0')
    const min = String(d.getMinutes()).padStart(2, '0')
    return `${m}-${day} ${h}:${min}`
  } catch {
    return isoStr
  }
}

export function reasonLabel(code: string): string {
  return REASON_LABELS[code] || code
}

export function kindLabel(kind: TodayProfitKind | null | undefined, confirmed = false): string {
  if (kind === 'official_measurement') return confirmed ? '已披露净值测算' : '官方情景'
  if (kind === 'estimate') return confirmed ? '未披露预估' : '预估情景'
  return '无法确认'
}

export function rowTodayDisplay(row: HoldingRow): TodayDisplay {
  const value = row.monitoring_profit
  return {
    amount: value?.amount ?? null,
    kind: value?.kind ?? 'unknown',
    label: value?.amount != null
      ? (value.kind === 'official_measurement' ? '官方测算' : '盘中预估')
      : (row.overseas_recent ? '海外日期待确认' : '暂无'),
    confirmed: false,
    returnDate: value?.return_date ?? null,
  }
}

export function rowSessionProfitView(
  row: HoldingRow,
  displaySession?: DisplaySession | null,
): SessionProfitView {
  if (row.is_cash_asset) {
    return {
      amount: null,
      returnPct: null,
      returnDate: null,
      kind: null,
      inSession: false,
      badges: [],
      isCash: true,
    }
  }

  const sp: SessionProfit | null | undefined = row.session_profit
  const amount = sp?.amount ?? null
  const returnPct = sp?.return_pct ?? null
  const returnDate = sp?.return_date ?? null
  const kind = sp?.kind ?? null
  const inSession = sp?.in_session ?? false

  const badges: string[] = []
  const displayDate = displaySession?.date ?? null
  const mode = displaySession?.mode ?? 'today'

  // 1. return_date 早于展示日 → 「MM-DD」
  const isEarlierDate = Boolean(returnDate && displayDate && returnDate < displayDate)
  if (isEarlierDate) {
    const mmdd = parseMMDD(returnDate)
    if (mmdd) {
      badges.push(mmdd)
    }
  }

  // 2. 仅在 mode === 'today' 时才显示「延迟」「无估值」
  // 若已是落后日期（如 QDII 回退），按规范统一处理为 MM-DD 标记，不重复打「无估值」
  if (mode === 'today' && !isEarlierDate) {
    if (row.estimated_status === 'stale') {
      badges.push('延迟')
    } else {
      const hasEstimate = row.estimated_status === 'available'
        || Boolean(row.estimated_nav)
        || Boolean(row.overseas_recent)
        || kind === 'estimate'
      if (!hasEstimate && kind !== 'official_measurement') {
        badges.push('无估值')
      }
    }
  }

  // 3. 展示日合计同时含官方和预估时的预估行 → 「估」
  const hasMixedSession = (displaySession?.official_count ?? 0) > 0 && (displaySession?.estimate_count ?? 0) > 0
  if (hasMixedSession && kind === 'estimate' && inSession) {
    badges.push('估')
  }

  return {
    amount,
    returnPct,
    returnDate,
    kind,
    inSession,
    badges: badges.slice(0, 2),
    isCash: false,
  }
}

export function todayHeroCopy(summary: TodayProfitSummary | null | undefined): TodayHeroCopyResult {
  const ds = summary?.display_session
  if (ds) {
    const isToday = ds.mode === 'today'
    const count = ds.count ?? 0
    const total = ds.total ?? 0
    const isPartial = total > 0 && count < total
    const dateMMDD = parseMMDD(ds.date)

    let baseTitle = ''
    if (isToday) {
      baseTitle = (ds.estimate_count ?? 0) > 0 ? '今日预估' : '今日收益'
    } else {
      baseTitle = dateMMDD ? `${dateMMDD} 当日收益` : '当日收益'
    }

    const label = isPartial ? `${baseTitle} · 已更新 ${count}/${total}` : baseTitle

    const infoDetails: string[] = []
    infoDetails.push('按当前持仓金额情景测算，非实际结算收益。')

    if (ds.official_count > 0 || ds.estimate_count > 0) {
      infoDetails.push(`计入合计：官方净值 ${ds.official_count} 只，盘中预估 ${ds.estimate_count} 只（共 ${count}/${total} 只）。`)
    }

    if (!isToday && isPartial) {
      infoDetails.push(`未计入的 ${total - count} 只基金可能因净值披露较晚（例如海外 QDII 基金）或缺少当日净值。`)
    }

    const hint = ds.updated_at ? `更新于 ${formatSimpleDateTime(ds.updated_at)}` : undefined

    return {
      label,
      amount: ds.profit ?? null,
      ratePct: ds.rate_pct ?? null,
      hint,
      mode: ds.mode,
      displayDate: ds.date,
      count,
      total,
      officialCount: ds.official_count,
      estimateCount: ds.estimate_count,
      infoDetails,
    }
  }

  const monitor = summary?.monitoring
  const hasOfficial = (monitor?.official_count ?? 0) > 0
  const partial = monitor != null && monitor.count < monitor.total
  return {
    label: hasOfficial ? '今日更新合计' : '今日盘中预估',
    amount: monitor?.profit ?? null,
    ratePct: null,
    hint: monitor
      ? `${monitor.return_date} · ${partial ? '部分预估 · ' : ''}覆盖 ${monitor.count}/${monitor.total} 只 · 官方 ${monitor.official_count}＋预估 ${monitor.estimate_count}`
      : '等待行情',
    mode: 'today',
    displayDate: monitor?.return_date ?? null,
    count: monitor?.count ?? 0,
    total: monitor?.total ?? 0,
    officialCount: monitor?.official_count ?? 0,
    estimateCount: monitor?.estimate_count ?? 0,
    infoDetails: ['按当前持仓金额情景测算，非实际结算收益。'],
  }
}

export function measurementReasons(item: ProfitMeasurement | null | undefined): string[] {
  return (item?.reason_codes ?? []).map(reasonLabel)
}

export interface EstimateView {
  nav: number | null
  pct: number | null
  state: ValueState
  at: string | null
  source: string | null
  /** 海外最近估值：只展示，不计入今日合计 */
  overseas: boolean
  basisDate: string | null
  basisNav: number | null
  conflict: boolean
}

/**
 * 行内与详情共用的估值视图。海外基金展示最近一次来源估值（可能不是今天），
 * 其余基金只展示当日估值。
 */
export function rowEstimateView(row: HoldingRow): EstimateView {
  const recent = row.overseas_recent
  if (recent) {
    return {
      nav: recent.estimated_nav,
      pct: recent.return_pct,
      state: signedState(recent.return_pct),
      at: recent.estimated_at,
      source: recent.source,
      overseas: true,
      basisDate: recent.previous_nav_date,
      basisNav: recent.previous_nav,
      conflict: recent.basis_status === 'conflict',
    }
  }
  return {
    nav: row.estimated_nav,
    pct: row.estimated_return_pct,
    state: row.estimated_return_state,
    at: row.estimated_at,
    source: row.estimated_source,
    overseas: false,
    basisDate: null,
    basisNav: null,
    conflict: false,
  }
}

// 本文件由 scripts/check-today-profit.mjs 单独转译执行，保持无运行时依赖。
function signedState(value: number | null | undefined): ValueState {
  if (value == null || value === 0) return 'muted'
  return value > 0 ? 'rise' : 'fall'
}
