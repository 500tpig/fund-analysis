import type { SimulationAccountStatus, SimulationOrderStatus } from '@/api/types'
import type { StatusTone } from '@/shared/dataStatus'

/** 十进制字符串按千分位展示，不转成浮点数，避免金额显示误差。 */
export function formatDecimal(value: string | null | undefined, fallback = '—'): string {
  if (value === null || value === undefined || value === '') return fallback
  const match = value.match(/^(-?)(\d+)(\.\d+)?$/)
  if (!match) return value
  const [, sign, integer, fraction = ''] = match
  const grouped = integer.replace(/\B(?=(\d{3})+(?!\d))/g, ',')
  return `${sign}${grouped}${fraction}`
}

export function formatSignedDecimal(value: string | null | undefined, fallback = '—'): string {
  if (value === null || value === undefined || value === '') return fallback
  const text = formatDecimal(value, fallback)
  return /^-/.test(value) || /^0(\.0+)?$/.test(value) ? text : `+${text}`
}

export function decimalSign(value: string | null | undefined): 'rise' | 'fall' | 'neutral' {
  if (!value || /^-?0(\.0+)?$/.test(value)) return 'neutral'
  return value.startsWith('-') ? 'fall' : 'rise'
}

/** 比例字符串（如 0.150000）转百分比，仅用于展示。 */
export function formatRatio(value: string | null | undefined, options: { signed?: boolean } = {}): string {
  if (value === null || value === undefined) return '—'
  const percent = Number(value) * 100
  if (!Number.isFinite(percent)) return '—'
  const sign = options.signed && percent > 0 ? '+' : ''
  return `${sign}${percent.toFixed(2)}%`
}

const BEIJING = new Intl.DateTimeFormat('zh-CN', {
  timeZone: 'Asia/Shanghai',
  month: '2-digit',
  day: '2-digit',
  hour: '2-digit',
  minute: '2-digit',
  hourCycle: 'h23',
})

/** 服务端时间为 UTC，界面统一换算为北京时间。 */
export function formatBeijing(value: string | null | undefined, fallback = '—'): string {
  if (!value) return fallback
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) return value
  const parts = Object.fromEntries(BEIJING.formatToParts(parsed).map((part) => [part.type, part.value]))
  return `${parts.month}-${parts.day} ${parts.hour}:${parts.minute}`
}

export function formatDay(value: string | null | undefined, fallback = '—'): string {
  if (!value) return fallback
  const match = value.match(/^\d{4}-(\d{2})-(\d{2})$/)
  return match ? `${match[1]}-${match[2]}` : value
}

export const ACCOUNT_STATUS: Record<SimulationAccountStatus, { label: string; tone: StatusTone }> = {
  active: { label: '运行中', tone: 'info' },
  paused: { label: '已暂停买入', tone: 'warning' },
  risk_paused: { label: '回撤锁定', tone: 'error' },
  archived: { label: '已归档', tone: 'muted' },
}

export const ORDER_STATUS: Record<SimulationOrderStatus, { label: string; tone: StatusTone }> = {
  rejected: { label: '已拒绝', tone: 'muted' },
  accepted: { label: '待净值', tone: 'info' },
  awaiting_nav: { label: '待净值', tone: 'info' },
  awaiting_confirmation: { label: '待确认', tone: 'info' },
  awaiting_payment: { label: '待到账', tone: 'warning' },
  settled: { label: '已完成', tone: 'muted' },
  cancelled: { label: '已撤销', tone: 'muted' },
  failed: { label: '失败', tone: 'error' },
}

export const ORDER_FILTERS: Array<{ key: string; label: string; statuses: SimulationOrderStatus[] }> = [
  { key: 'all', label: '全部', statuses: [] },
  { key: 'open', label: '处理中', statuses: ['accepted', 'awaiting_nav', 'awaiting_confirmation', 'awaiting_payment'] },
  { key: 'settled', label: '已完成', statuses: ['settled'] },
  { key: 'closed', label: '未成交', statuses: ['rejected', 'cancelled', 'failed'] },
]

const REASONS: Record<string, string> = {
  account_paused: '账户已暂停新增买入',
  account_archived: '账户已归档',
  rule_incomplete: '基金规则未核验',
  rule_conflict: '基金规则冲突',
  calendar_unknown: '交易日历未覆盖',
  not_open_day: '非基金开放日',
  after_cutoff: '已过当日申请截止',
  below_minimum: '低于最低额',
  daily_limit: '超过当日申购上限',
  insufficient_cash: '可用现金不足',
  insufficient_shares: '可卖份额不足',
  min_remaining: '剩余份额低于最低保留',
  invalid_quantity: '份额精度不符',
  nav_missing: '缺少应披露的官方净值',
  nav_conflict: '同一净值日数值冲突',
  position_limit: '仓位超过上限',
  business_failure: '渠道业务失败',
  zero_shares: '确认份额为零',
  user_cancel: '已撤销',
  manual: '手动暂停',
  drawdown: '回撤达到 15%',
  nav_correction: '已用净值出现更正',
  restored: '备份恢复后暂停',
}

export function reasonLabel(code: string | null | undefined): string {
  if (!code) return ''
  return REASONS[code] ?? code
}
