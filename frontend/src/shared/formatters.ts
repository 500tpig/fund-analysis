import type { ValueState } from '@/api/types'

export function formatMoney(value: number | null | undefined, fallback = '—'): string {
  if (value === null || value === undefined) return fallback
  return value.toLocaleString('zh-CN', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })
}

export function formatCompactMoney(value: number | null | undefined, fallback = '—'): string {
  if (value === null || value === undefined) return fallback
  if (Math.abs(value) < 100_000_000) return formatMoney(value, fallback)
  return value.toLocaleString('zh-CN', {
    notation: 'compact',
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })
}

export function formatCurrency(value: number | null | undefined, fallback = '—'): string {
  if (value === null || value === undefined) return fallback
  return `¥${formatMoney(value)}`
}

export function formatCompactCurrency(value: number | null | undefined, fallback = '—'): string {
  if (value === null || value === undefined) return fallback
  return `¥${formatCompactMoney(value)}`
}

export function signedMoney(value: number | null | undefined, fallback = '—'): string {
  if (value === null || value === undefined) return fallback
  const abs = formatMoney(Math.abs(value))
  if (value > 0) return `+${abs}`
  if (value < 0) return `-${abs}`
  return abs
}

export function signedCompactMoney(value: number | null | undefined, fallback = '—'): string {
  if (value === null || value === undefined) return fallback
  const abs = formatCompactMoney(Math.abs(value))
  if (value > 0) return `+${abs}`
  if (value < 0) return `-${abs}`
  return abs
}

export function formatPercent(
  value: number | null | undefined,
  options: { signed?: boolean; fallback?: string } = {},
): string {
  if (value === null || value === undefined) return options.fallback ?? '—'
  const sign = (options.signed ?? true) && value > 0 ? '+' : ''
  return `${sign}${value.toFixed(2)}%`
}

export function formatNav(value: number | null | undefined, fallback = '—'): string {
  if (value === null || value === undefined) return fallback
  return value.toLocaleString('zh-CN', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 4,
  })
}

export function formatDateTime(
  value: string | null | undefined,
  fallback = '暂无',
): string {
  if (!value) return fallback
  const stamp = parseStamp(value)
  if (!stamp) return value
  if (stamp.hour != null && stamp.minute != null) {
    return `${stamp.month}-${stamp.day} ${stamp.hour}:${stamp.minute}`
  }
  return `${stamp.month}-${stamp.day}`
}

export function formatDate(value: string | null | undefined, fallback = '暂无'): string {
  return formatMonthDay(value, fallback)
}

export function formatMonthDay(value: string | null | undefined, fallback = '—'): string {
  if (!value) return fallback
  const stamp = parseStamp(value)
  if (!stamp) return fallback
  return `${stamp.month}-${stamp.day}`
}

export function formatClock(value: string | null | undefined, fallback = '—'): string {
  if (!value) return fallback
  const stamp = parseStamp(value)
  if (stamp?.hour != null && stamp.minute != null) {
    return `${stamp.hour}:${stamp.minute}`
  }
  const match = value.match(/(\d{1,2}:\d{2})/)
  return match?.[1] ?? fallback
}

export function valueStateClass(
  state: ValueState | undefined | null,
  level: 'strong' | 'muted' = 'strong',
): string {
  if (state === 'rise') return level === 'muted' ? 'value-rise-muted' : 'value-rise'
  if (state === 'fall') return level === 'muted' ? 'value-fall-muted' : 'value-fall'
  return 'value-muted'
}

export function signedState(value: number | null | undefined): ValueState {
  if (value == null) return 'muted'
  if (value > 0) return 'rise'
  if (value < 0) return 'fall'
  return 'muted'
}

/** 北京时间的日期（YYYY-MM-DD），不依赖浏览器所在时区。 */
export function beijingDateISO(now: Date = new Date()): string {
  return new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Asia/Shanghai',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).format(now)
}

interface DateStamp {
  year: string
  month: string
  day: string
  hour?: string
  minute?: string
}

function hasTimezone(value: string): boolean {
  return /(?:Z|[+-]\d{2}:?\d{2})$/.test(value.trim())
}

function fromDate(parsed: Date): DateStamp {
  return {
    year: String(parsed.getFullYear()),
    month: String(parsed.getMonth() + 1).padStart(2, '0'),
    day: String(parsed.getDate()).padStart(2, '0'),
    hour: String(parsed.getHours()).padStart(2, '0'),
    minute: String(parsed.getMinutes()).padStart(2, '0'),
  }
}

function parseStamp(value: string): DateStamp | null {
  const dateOnly = value.match(/^(\d{4})-(\d{2})-(\d{2})$/)
  if (dateOnly) {
    return {
      year: dateOnly[1],
      month: dateOnly[2],
      day: dateOnly[3],
    }
  }

  const dateTime = value.match(/^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})/)
  if (dateTime && hasTimezone(value)) {
    const parsed = parseDateValue(value)
    if (parsed) return fromDate(parsed)
  }
  if (dateTime) {
    return {
      year: dateTime[1],
      month: dateTime[2],
      day: dateTime[3],
      hour: dateTime[4],
      minute: dateTime[5],
    }
  }

  const parsed = parseDateValue(value)
  if (!parsed) return null
  return fromDate(parsed)
}

function parseDateValue(value: string): Date | null {
  const normalized = value.includes('T') ? value : value.replace(' ', 'T')
  const parsed = new Date(normalized)
  if (Number.isNaN(parsed.getTime())) return null
  return parsed
}
