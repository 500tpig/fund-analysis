export type ValueState = 'rise' | 'fall' | 'muted'

export type FreshnessStatus = 'fresh' | 'stale' | 'missing' | 'partial' | 'provider_failed'

export interface EstimateAccuracy {
  source: string
  sample_count: number
  pending_count?: number | null
  window: number
  mae_pct_points: number | null
  bias_pct_points: number | null
  max_abs_error_pct_points: number | null
}

export interface DataFreshnessSource {
  source_key: string
  job_type: string
  label: string
  freshness_status: FreshnessStatus
  run_status: string | null
  completed_at: string | null
  max_data_date: string | null
  row_count: number | null
  error_message: string | null
}

export interface DataFreshnessResponse {
  schema_version: 'data_freshness.v1'
  overall_status: FreshnessStatus
  sources: DataFreshnessSource[]
}

export type MarketQuoteStatus = 'fresh' | 'stale' | 'failed' | 'missing'
export type MarketSessionStatus = 'trading' | 'break' | 'closed' | 'unknown'

export interface MarketQuoteItem {
  symbol: 'CSI_ALL_SHARE' | 'AU9999' | 'NDX'
  name: string
  instrument_type: 'index' | 'spot'
  market: string
  currency: string
  last_price: number | null
  previous_close: number | null
  change_value: number | null
  change_pct: number | null
  trade_date: string | null
  quoted_at: string | null
  source: string | null
  source_url: string | null
  fetched_at: string | null
  status: MarketQuoteStatus
  session_status: MarketSessionStatus
  session_label: string
  warning: string | null
}

export interface MarketQuotesResponse {
  schema_version: 'market_quotes.v1'
  refresh_eligible: boolean
  items: MarketQuoteItem[]
}

export type TodayProfitKind = 'official_measurement' | 'estimate' | 'unknown'
export type AmountBasisStatus = 'confirmed' | 'unknown' | 'mismatch' | 'unverified_legacy'
export type TodayAccountStatus = 'complete' | 'partial' | 'unavailable'

export interface ProfitMeasurement {
  amount: number | null
  kind: TodayProfitKind
  return_date: string | null
  source: string | null
  basis_amount: number | null
  basis_status: AmountBasisStatus
  included: boolean
  reason_codes: string[]
  return_pct: number | null
}

export interface ImportedDailyProfit {
  amount: number
  dated: false
  label: string
}

export interface CoverageSlice {
  count: number
  total: number
  amount: number | null
  amount_pct: number | null
}

export interface ProfitBucket {
  profit: number | null
  rate_pct: number | null
  count: number
  amount: number | null
}

export interface MonitoringSummary extends ProfitBucket {
  return_date: string
  total: number
  official_count: number
  estimate_count: number
  official: ProfitBucket
  estimate: ProfitBucket
  updated_at: string | null
  excluded_codes: string[]
}

export interface DisplaySession {
  date: string | null
  is_today: boolean
  mode: 'today' | 'latest_official'
  profit: number | null
  rate_pct: number | null
  count: number
  total: number
  amount: number | null
  official_count: number
  estimate_count: number
  updated_at: string | null
}

export interface TodayProfitSummary {
  monitoring: MonitoringSummary
  display_session?: DisplaySession
  return_date: string
  account_status: TodayAccountStatus
  account_message: string
  verifiable: ProfitBucket & { amount_coverage_pct: number | null }
  official_measurement: ProfitBucket
  estimate: ProfitBucket
  // Summary scenarios exclude included measurements; row scenarios retain comparisons.
  official_scenario: ProfitBucket
  // Also excludes funds with an official NAV dated to the target day.
  estimate_scenario: ProfitBucket
  quote_coverage: CoverageSlice
  official_data_coverage: CoverageSlice
  amount_basis_coverage: CoverageSlice
  verifiable_coverage: CoverageSlice
  excluded: Array<{ code: string; amount: number | null; reason_codes: string[] }>
  imported_undated: { count: number; profit: number | null }
  fund_count: number
  fund_amount: number | null
}

export interface SessionProfit {
  amount: number | null
  return_pct: number | null
  return_date: string | null
  kind: 'official_measurement' | 'estimate' | null
  in_session: boolean
}

export interface OverseasRecentEstimate {
  return_pct: number | null
  estimated_nav: number | null
  estimated_at: string | null
  source: string | null
  previous_nav: number | null
  /** 来源给出的涨跌基准净值日，不代表收益覆盖期间 */
  previous_nav_date: string | null
  basis_status: 'consistent' | 'conflict' | 'unknown' | null
  /** 来源估值点是否属于今天（北京时间） */
  is_current: boolean
}

export interface HoldingRow {
  session_profit?: SessionProfit | null
  monitoring_profit: ProfitMeasurement
  overseas_recent: OverseasRecentEstimate | null
  id: number
  code: string
  name: string
  amount: number | null
  shares: string | null
  weight_pct: number | null
  cost: number | null
  amount_nav_date: string | null
  amount_basis_status: AmountBasisStatus
  amount_basis_source: string | null
  daily_return_pct: number | null
  daily_return_state: ValueState
  daily_profit: number | null
  daily_profit_state: ValueState
  today_profit: ProfitMeasurement
  official_scenario: ProfitMeasurement | null
  estimate_scenario: ProfitMeasurement | null
  imported_daily_profit: ImportedDailyProfit | null
  estimated_nav: number | null
  estimated_return_pct: number | null
  estimated_return_state: ValueState
  estimated_daily_profit: number | null
  estimated_daily_profit_state: ValueState
  estimate_basis_status: 'consistent' | 'conflict' | 'unknown' | null
  estimate_basis_reason_codes: string[]
  estimated_at: string | null
  estimated_source: string | null
  estimated_status: string | null
  estimated_warning: string | null
  estimated_unavailable_reason: string | null
  holding_profit: number | null
  holding_profit_state: ValueState
  holding_profit_rate: number | null
  holding_profit_rate_state: ValueState
  holding_profit_amount_source: 'cost_measurement' | 'imported_rate' | null
  holding_profit_rate_source: 'cost_measurement' | 'imported_rate' | null
  holding_profit_conflict: boolean
  holding_profit_conflict_reason_codes: string[]
  cumulative_profit: number | null
  cumulative_profit_state: ValueState
  year_profit: number | null
  year_profit_state: ValueState
  asset_type: string | null
  is_cash_asset: boolean
  is_fund_asset: boolean
  asset_class: string | null
  theme: string | null
  latest_nav: number | null
  nav_date: string | null
  fetched_at: string | null
  market_status: 'fresh' | 'missing' | 'cash'
  accuracy: EstimateAccuracy | null
  nav_update_status?: 'updated' | 'pending' | 'overdue' | 'market_closed' | 'source_failed' | 'unknown_rule' | null
  nav_update_reason?: string | null
  target_nav_date?: string | null
  /** 下一次预计披露官方净值的国内日期（晚间）；逾期或日历未覆盖时为空 */
  next_disclosure_date?: string | null
  in_disclosure_window?: boolean
  auto_refresh_eligible?: boolean
  fund_type?: string | null
  fund_type_label?: string | null
  valuation_market?: string | null
  market_calendar?: string | null
  nav_disclosure_rule?: string | null
  benchmark_name?: string | null
  benchmark_code?: string | null
  source_url?: string | null
  verified_at?: string | null
}

export interface HoldingPayload {
  code: string
  name: string
  amount: number | null
  weight_pct: number | null
  cost: number | null
  asset_type: string | null
  asset_class: string | null
  theme: string | null
  amount_nav_date?: string | null
}

export interface FundSearchHit {
  code: string
  name: string
}

export interface FundSearchResponse {
  query: string
  items: FundSearchHit[]
}

export interface HoldingsSummary {
  holding_count: number
  fund_holding_count: number
  cash_holding_count: number
  total_amount: number | null
  fund_amount: number | null
  cash_amount: number | null
  total_daily_profit: number | null
  total_daily_profit_state: ValueState
  total_estimated_daily_profit: number | null
  total_estimated_daily_profit_state: ValueState
  total_holding_profit: number | null
  total_holding_profit_state: ValueState
  total_cumulative_profit: number | null
  total_cumulative_profit_state: ValueState
  total_year_profit: number | null
  total_year_profit_state: ValueState
  latest_refresh: string | null
  latest_intraday_refresh: string | null
  return_date: string
  today: TodayProfitSummary
}

export interface PortfolioBreakdownItem {
  key: string
  label: string
  amount: number | null
  weight_pct: number | null
  holding_count: number
}

export interface PortfolioBreakdown {
  available: boolean
  source?: string
  scope?: string
  note?: string
  reason?: string
  items: PortfolioBreakdownItem[]
}

export interface PortfolioHoldingWeight {
  code: string
  name: string
  amount: number | null
  weight_pct: number | null
}

export interface PortfolioContributionItem {
  code: string
  name: string
  amount: number | null
  kind?: TodayProfitKind
  estimated_return_pct: number | null
  estimated_profit: number | null
  estimated_profit_state: ValueState
  share_pct: number | null
}

export interface PortfolioAnalysis {
  schema_version: 'portfolio_analysis.v1'
  basis: {
    holdings_scope: string
    weight_basis: string
    look_through: boolean
    has_quarterly_holdings: boolean
    has_transactions: boolean
    contribution_basis: string
  }
  distribution: {
    asset_class: PortfolioBreakdown
    region: PortfolioBreakdown
    currency: PortfolioBreakdown
    industry: PortfolioBreakdown
  }
  concentration: {
    source: string
    max_weight_pct: number | null
    top3_weight_pct: number | null
    top5_weight_pct: number | null
    holdings: PortfolioHoldingWeight[]
  }
  overlap: {
    available: boolean
    reason: string
  }
  contribution: {
    basis: string
    note: string
    total_estimated_profit: number | null
    total_estimated_profit_state: ValueState
    items: PortfolioContributionItem[]
  }
}

export interface PortfolioRiskFund {
  code: string
  name: string
  source: string | null
  start_date: string | null
  end_date: string | null
  sample_count: number
  nav_point_count: number
  volatility_pct: number | null
  max_drawdown_pct: number | null
}

export interface PortfolioRiskPair {
  code_a: string
  code_b: string
  name_a: string
  name_b: string
  overlap_count: number
  correlation: number | null
}

export interface PortfolioRiskResponse {
  schema_version: 'portfolio_risk.v1'
  range: '1m' | '3m' | '6m' | '1y' | '3y'
  basis: {
    series: string
    look_through: boolean
    personal_return: boolean
    advice: boolean
    note: string
  }
  funds: PortfolioRiskFund[]
  pairs: PortfolioRiskPair[]
  failures: Array<{ code: string; error: string }>
}

export interface HoldingsResponse {
  rows: HoldingRow[]
  summary: HoldingsSummary
  data_freshness: DataFreshnessResponse
  portfolio_analysis?: PortfolioAnalysis
  market_quotes: MarketQuotesResponse
}

export type FundsResponse = HoldingsResponse

export interface FundsRefreshResponse {
  success: number
  failure: number
  failed_codes: string[]
  failures: Array<{ code: string; error: string }>
  estimate_success: number
  estimate_failure: number
  estimate_failed_codes: string[]
  estimate_failures: Array<{ code: string; error: string }>
  quote_success: number
  quote_failure: number
  quote_failures: Array<{ code: string; error: string }>
  has_failure: boolean
  message: string
  /** 请求到达时已有刷新在进行，本次复用了那次结果 */
  coalesced?: boolean
  monitor: FundsResponse
}

export interface PerformanceCurvePoint {
  date: string
  raw_value: number
  return_pct: number
}

export type PerformanceBenchmarkCode =
  | '000300'
  | '399006'
  | '000001'
  | '399001'
  | '000012'
  | '000827'

export type SelectedBenchmarkCode = PerformanceBenchmarkCode | ''

export interface PerformanceBenchmarkOption {
  code: SelectedBenchmarkCode
  name: string
}

export const PERFORMANCE_BENCHMARKS: PerformanceBenchmarkOption[] = [
  { code: '000300', name: '沪深300' },
  { code: '399006', name: '创业板指' },
  { code: '000001', name: '上证指数' },
  { code: '399001', name: '深证成指' },
  { code: '000012', name: '国债指数' },
  { code: '000827', name: '中证环保' },
]

export const NO_PERFORMANCE_BENCHMARK: PerformanceBenchmarkOption = {
  code: '',
  name: '暂无跟踪基准',
}

export const PERFORMANCE_BENCHMARK_OPTIONS: PerformanceBenchmarkOption[] = [
  NO_PERFORMANCE_BENCHMARK,
  ...PERFORMANCE_BENCHMARKS,
]

export const DEFAULT_PERFORMANCE_BENCHMARK: PerformanceBenchmarkCode = '000300'

export interface FundPerformanceResponse {
  code: string
  range: '1m' | '3m' | '6m' | '1y' | '3y'
  source: string
  start_date: string
  end_date: string
  points: PerformanceCurvePoint[]
  benchmark: {
    code: string
    name: string
    source: string
    points: PerformanceCurvePoint[]
  } | null
  warnings: string[]
}

export interface IntradayCurveResponse {
  code: string
  date: string
  source: string
  availability: 'available' | 'no_minute_data'
  points: Array<{
    time: string
    datetime?: string
    date?: string
    estimated_nav: number
    estimated_return_pct: number
  }>
  warning: string
}

export interface OfficialDailyProfitItem {
  nav_date: string
  holding_amount: number
  official_nav: number | null
  official_return_pct: number
  profit_amount: number
  cumulative_profit: number
  source: string
  recorded_at: string | null
  basis_status: 'unverified_legacy'
}

export interface OfficialDailyProfitLatest {
  nav_date: string
  profit_amount: number
}

export interface OfficialDailyProfitResponse {
  schema_version: 'fund_official_daily_profit.v1'
  code: string
  record_count: number
  start_date: string | null
  end_date: string | null
  latest: OfficialDailyProfitLatest | null
  cumulative_profit: number
  cumulative_kind: 'recorded_dates_sum'
  observed_dates: string[]
  completeness: 'recorded_dates_only' | 'gapped_recorded_dates'
  basis_note: string
  items: OfficialDailyProfitItem[]
}

export type HoldingsEntryMode = 'merge' | 'replace'
export type HoldingsEntryAction = 'add' | 'update' | 'keep' | 'remove'

export interface HoldingsEntryNameMatch {
  code: string
  name: string
}

export interface HoldingsEntryCandidate {
  source_image_index: number
  code: string | null
  ocr_name: string | null
  name: string | null
  share_class: string | null
  amount: string | null
  shares: string | null
  cost: string | null
  holding_profit_rate: string | null
  daily_profit: string | null
  cumulative_profit: string | null
  issues: string[]
  verification: 'matched' | 'inferred' | 'not_found' | 'unverified'
  confidence: 'high' | 'low'
  name_matches: HoldingsEntryNameMatch[]
}

export interface HoldingsEntryItem {
  code: string
  name: string
  amount: string
  shares?: string | null
  cost?: string | null
  holding_profit_rate?: string | null
  daily_profit?: string | null
  cumulative_profit?: string | null
}

export interface HoldingsEntryRecognizeResponse {
  schema_version: 'holdings_entry_recognize.v1'
  provider: string
  model: string
  image_count: number
  candidates: HoldingsEntryCandidate[]
  warnings: string[]
}

export interface HoldingsEntryPreviewRow extends HoldingsEntryItem {
  shares: string | null
  cost: string | null
  holding_profit_rate: string | null
  daily_profit: string | null
  cumulative_profit: string | null
  action: HoldingsEntryAction
  error: string | null
}

export interface HoldingsEntryPreviewResponse {
  schema_version: 'holdings_entry_preview.v1'
  mode: HoldingsEntryMode
  rows: HoldingsEntryPreviewRow[]
  warnings: string[]
  valid_count: number
  error_count: number
  add_count: number
  update_count: number
  keep_count: number
  remove_count: number
}

export interface HoldingsEntryConfirmResponse {
  schema_version: 'holdings_entry_confirm.v1'
  mode: HoldingsEntryMode
  holdings_written: number
  add_count: number
  update_count: number
  keep_count: number
  remove_count: number
  message: string
  refresh: {
    attempted: boolean
    has_failure: boolean
    message: string
  }
}

// ---- 模拟账户（/api/simulation）：金额、份额、净值均为十进制字符串 ----

export type SimulationAccountStatus = 'active' | 'paused' | 'risk_paused' | 'archived'
export type SimulationOrderStatus =
  | 'rejected'
  | 'accepted'
  | 'awaiting_nav'
  | 'awaiting_confirmation'
  | 'awaiting_payment'
  | 'settled'
  | 'cancelled'
  | 'failed'

export interface SimulationMissingItem {
  code: string
  reason: string
  nav_date?: string
  expected_nav_date?: string
  detail?: string
}

export interface SimulationAccountSummary {
  id: string
  experiment_id: string
  status: SimulationAccountStatus
  created_at: string
  archived_at: string | null
}

export interface SimulationAccountsResponse {
  environment: 'fixture' | 'production'
  items: SimulationAccountSummary[]
}

export interface SimulationAccount {
  id: string
  experiment_id: string
  environment: 'fixture' | 'production'
  status: SimulationAccountStatus
  pause_reason: string | null
  version: number
  created_at: string
  archived_at: string | null
  initial_cash: string
  as_of: string
  balances: {
    available: string
    frozen: string
    receivable: string
    dividend_receivable: string
    holdings_value: string | null
    total: string | null
  }
  profit: string | null
  profit_rate: string | null
  peak: string
  drawdown: string | null
  quality: 'complete' | 'incomplete'
  missing: SimulationMissingItem[]
  nav_date_range: { min: string | null; max: string | null }
  pending_order_count: number
  latest_valuation: { observed_at: string; quality: string } | null
}

export interface SimulationPosition {
  code: string
  name: string
  shares: string
  frozen_shares: string
  available_shares: string
  net_cost: string
  lot_count: number
  nav: string | null
  nav_date: string | null
  value: string | null
  rule_id: string | null
  rule_status: string
  issues: SimulationMissingItem[]
}

export interface SimulationOrder {
  id: string
  account_id: string
  side: 'buy' | 'sell'
  code: string
  name: string | null
  status: SimulationOrderStatus
  reason_code: string | null
  reason_detail: string | null
  blocked_reason: string | null
  version: number
  requested_amount: string | null
  accepted_amount: string | null
  requested_shares: string | null
  confirmed_shares: string | null
  nav_date: string | null
  nav_value: string | null
  fee: string | null
  gross_amount: string | null
  net_amount: string | null
  rounding_delta: string | null
  submitted_at: string
  cancel_deadline_at: string | null
  confirmation_due_date: string | null
  confirmation_due_at: string | null
  confirmed_at: string | null
  payment_due_date: string | null
  payment_due_at: string | null
  paid_at: string | null
  nav_observed_at: string | null
  run_id: string | null
  rule_id: string | null
  created_at: string
  updated_at: string
}

export interface SimulationOrderDetail extends SimulationOrder {
  cancellable: boolean
  events: Array<{
    from_status: string | null
    to_status: string
    occurred_at: string
    effective_date: string | null
    reason: string | null
  }>
  allocations: Array<{ lot_id: string; shares: string; lot_confirmed_date: string }>
  ledger: Array<{
    event_type: string
    cash_available_delta: string
    cash_frozen_delta: string
    receivable_delta: string
    shares_delta: string
    fee: string
    effective_date: string
    recorded_at: string
  }>
  rule: {
    id: string
    environment: string
    status: string
    evidence: { source_url?: string | null; evidence_ref?: string | null; assumptions?: string[] } | null
  } | null
  snapshots: Array<{
    id: string
    kind: string
    code: string | null
    nav_date: string | null
    value: string | null
    source: string
    observed_at: string
  }>
}

export interface SimulationOrdersResponse {
  items: SimulationOrder[]
  next_cursor: string | null
}
