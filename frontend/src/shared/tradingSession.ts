/**
 * Trading session utilities for Chinese financial markets (Beijing time / Asia/Shanghai).
 *
 * Core principles:
 * 1. China A-share has 240 trading minutes:
 *    - Morning: 09:30 - 11:30 (120 minutes)
 *    - Afternoon: 13:00 - 15:00 (120 minutes)
 * 2. Lunch break (11:30 - 13:00) is compressed with a fixed visual gap (16-20px),
 *    never linearly interpolated across the 90-minute break.
 * 3. Beijing time is strictly enforced, never relying on browser local timezone.
 */

export type TradingSessionType = 'cn-a' | 'continuous' | 'raw'

export interface TradingMinuteInfo {
  session: 'morning' | 'afternoon' | 'closed' | 'out_of_range'
  sessionMinute: number // Morning: 0..120, Afternoon: 0..120
  tradingMinute: number // 0..240 across full day
  isBreak: boolean
}

export interface TradingAxisLayout {
  left: number
  right: number
  plotWidth: number
  morningWidth: number
  afternoonWidth: number
  breakGap: number
  morningStartX: number
  morningEndX: number
  afternoonStartX: number
  afternoonEndX: number
  breakCenterX: number
}

export interface IntradayPoint {
  x: string
  y: number
  nav?: number | null
}

export interface SplitSessionsResult<T> {
  morning: T[]
  afternoon: T[]
}

/**
 * Normalizes an intraday time string (e.g. "13:48" or "2026-09-02 13:48:00" or ISO)
 * into a Beijing time [hours, minutes, seconds] tuple.
 */
export function parseBeijingTime(timeInput: string): [number, number, number] | null {
  if (!timeInput || typeof timeInput !== 'string') return null
  const trimmed = timeInput.trim()

  // Match ISO or full datetime like "2026-09-02T13:48:00+08:00" or "2026-09-02 13:48:00"
  const isoMatch = trimmed.match(/(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})(?::(\d{2}))?/)
  if (isoMatch) {
    const hasTz = /(?:Z|[+-]\d{2}:?\d{2})$/.test(trimmed)
    if (hasTz) {
      const d = new Date(trimmed)
      if (!Number.isNaN(d.getTime())) {
        // Convert to Beijing time (UTC+8)
        const utc = d.getTime() + d.getTimezoneOffset() * 60000
        const bjDate = new Date(utc + 8 * 3600000)
        return [bjDate.getHours(), bjDate.getMinutes(), bjDate.getSeconds()]
      }
    }
    // Datetime without timezone offset is considered Beijing local time by convention
    const hours = Number(isoMatch[4])
    const minutes = Number(isoMatch[5])
    const seconds = Number(isoMatch[6] || 0)
    return [hours, minutes, seconds]
  }

  // Pure time string "HH:mm" or "HH:mm:ss"
  const timeMatch = trimmed.match(/^(\d{1,2}):(\d{2})(?::(\d{2}))?$/)
  if (timeMatch) {
    const hours = Number(timeMatch[1])
    const minutes = Number(timeMatch[2])
    const seconds = Number(timeMatch[3] || 0)
    if (hours >= 0 && hours < 24 && minutes >= 0 && minutes < 60) {
      return [hours, minutes, seconds]
    }
  }

  return null
}

/**
 * Maps a Beijing time to China A-share trading minutes.
 */
export function toTradingMinute(timeStr: string): TradingMinuteInfo {
  const bj = parseBeijingTime(timeStr)
  if (!bj) {
    return {
      session: 'out_of_range',
      sessionMinute: -1,
      tradingMinute: -1,
      isBreak: false,
    }
  }

  const [h, m] = bj
  const totalMinutes = h * 60 + m

  const morningStart = 9 * 60 + 30 // 09:30 (570)
  const morningEnd = 11 * 60 + 30 // 11:30 (690)
  const afternoonStart = 13 * 60 // 13:00 (780)
  const afternoonEnd = 15 * 60 // 15:00 (900)

  if (totalMinutes < morningStart) {
    return {
      session: 'out_of_range',
      sessionMinute: 0,
      tradingMinute: 0,
      isBreak: false,
    }
  }

  if (totalMinutes <= morningEnd) {
    const min = totalMinutes - morningStart
    return {
      session: 'morning',
      sessionMinute: min,
      tradingMinute: min,
      isBreak: false,
    }
  }

  if (totalMinutes < afternoonStart) {
    return {
      session: 'closed',
      sessionMinute: 120,
      tradingMinute: 120,
      isBreak: true,
    }
  }

  if (totalMinutes <= afternoonEnd) {
    const min = totalMinutes - afternoonStart
    return {
      session: 'afternoon',
      sessionMinute: min,
      tradingMinute: 120 + min,
      isBreak: false,
    }
  }

  return {
    session: 'closed',
    sessionMinute: 120,
    tradingMinute: 240,
    isBreak: false,
  }
}

/**
 * Computes horizontal layout coordinates for A-share trading chart
 * with a dedicated lunch break gap (default 36px for visual breathing room).
 */
export function computeTradingAxisLayout(
  totalWidth: number,
  plotLeft: number,
  plotRight: number,
  breakGap = 36,
): TradingAxisLayout {
  const plotWidth = totalWidth - plotLeft - plotRight
  const effectiveWidth = Math.max(plotWidth - breakGap, 100)
  const morningWidth = effectiveWidth / 2
  const afternoonWidth = effectiveWidth / 2

  const morningStartX = plotLeft
  const morningEndX = plotLeft + morningWidth
  const afternoonStartX = morningEndX + breakGap
  const afternoonEndX = afternoonStartX + afternoonWidth
  const breakCenterX = morningEndX + breakGap / 2

  return {
    left: plotLeft,
    right: plotRight,
    plotWidth,
    morningWidth,
    afternoonWidth,
    breakGap,
    morningStartX,
    morningEndX,
    afternoonStartX,
    afternoonEndX,
    breakCenterX,
  }
}

/**
 * Maps a trading minute (0..240) to pixel X coordinate using layout.
 */
export function mapTradingMinuteToX(
  tradingMinuteInfo: TradingMinuteInfo,
  layout: TradingAxisLayout,
): number {
  if (tradingMinuteInfo.session === 'morning') {
    const fraction = Math.min(Math.max(tradingMinuteInfo.sessionMinute / 120, 0), 1)
    return layout.morningStartX + fraction * layout.morningWidth
  }

  if (tradingMinuteInfo.session === 'afternoon') {
    const fraction = Math.min(Math.max(tradingMinuteInfo.sessionMinute / 120, 0), 1)
    return layout.afternoonStartX + fraction * layout.afternoonWidth
  }

  if (tradingMinuteInfo.isBreak) {
    // Falls in the break gap: snap to morning end
    return layout.morningEndX
  }

  if (tradingMinuteInfo.tradingMinute >= 240) {
    return layout.afternoonEndX
  }

  return layout.morningStartX
}

/**
 * Splits intraday points into morning and afternoon sessions.
 * Points inside the lunch break (11:31 - 12:59) are filtered out,
 * ensuring no artificial connection line across the break.
 */
export function splitTradingSessions<T extends { x: string }>(
  points: T[],
): SplitSessionsResult<T> {
  const morning: T[] = []
  const afternoon: T[] = []

  for (const point of points) {
    const info = toTradingMinute(point.x)
    if (info.session === 'morning') {
      morning.push(point)
    } else if (info.session === 'afternoon') {
      afternoon.push(point)
    }
  }

  return { morning, afternoon }
}

/**
 * Finds the latest valid point in an array of points.
 */
export function getLatestValidPoint<T extends { x: string; y: number }>(
  points: T[],
): T | null {
  if (!points || !points.length) return null
  for (let i = points.length - 1; i >= 0; i--) {
    const p = points[i]
    if (p && typeof p.y === 'number' && !Number.isNaN(p.y)) {
      return p
    }
  }
  return null
}
