import assert from 'node:assert/strict'
import { Buffer } from 'node:buffer'
import { readFileSync } from 'node:fs'
import ts from 'typescript'

// Exercise the same presentation functions imported by the list and drawer.
const source = readFileSync(new URL('../src/shared/todayProfit.ts', import.meta.url), 'utf8')
const { outputText } = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext } })
const { rowEstimateView, rowSessionProfitView, rowTodayDisplay, todayHeroCopy } = await import(`data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`)
const summary = (changes = {}) => ({ monitoring: {
  profit: 0, return_date: '2026-09-09', count: 1, total: 2,
  official_count: 0, estimate_count: 1, ...changes,
} })
assert.equal(todayHeroCopy(summary()).amount, 0)
assert.equal(todayHeroCopy(summary()).label, '今日盘中预估')
assert.match(todayHeroCopy(summary()).hint, /部分预估.*1\/2/)
assert.equal(todayHeroCopy(summary({ official_count: 1 })).label, '今日更新合计')
assert.equal(todayHeroCopy(summary({ profit: null, count: 0 })).amount, null)
assert.equal(todayHeroCopy(undefined).amount, null)

// display_session 模式测试
const dsToday = {
  display_session: {
    date: '2026-09-24', is_today: true, mode: 'today', profit: -123.45, rate_pct: -0.5,
    count: 17, total: 19, official_count: 15, estimate_count: 2, amount: 100000, updated_at: null,
  },
}
assert.equal(todayHeroCopy(dsToday).label, '今日预估 · 已更新 17/19')
assert.equal(todayHeroCopy(dsToday).amount, -123.45)
assert.equal(todayHeroCopy(dsToday).ratePct, -0.5)

const dsTodayFull = {
  display_session: {
    date: '2026-09-24', is_today: true, mode: 'today', profit: 100, rate_pct: 1.0,
    count: 19, total: 19, official_count: 19, estimate_count: 0, amount: 100000, updated_at: null,
  },
}
assert.equal(todayHeroCopy(dsTodayFull).label, '今日收益')

const dsOfficialPartial = {
  display_session: {
    date: '2026-09-24', is_today: false, mode: 'latest_official', profit: -2042.3, rate_pct: -1.26,
    count: 17, total: 19, official_count: 17, estimate_count: 0, amount: 158000, updated_at: null,
  },
}
assert.equal(todayHeroCopy(dsOfficialPartial).label, '09-24 当日收益 · 已更新 17/19')
assert.equal(todayHeroCopy(dsOfficialPartial).amount, -2042.3)

const dsOfficialFull = {
  display_session: {
    date: '2026-09-24', is_today: false, mode: 'latest_official', profit: 500, rate_pct: 0.8,
    count: 19, total: 19, official_count: 19, estimate_count: 0, amount: 158000, updated_at: null,
  },
}
assert.equal(todayHeroCopy(dsOfficialFull).label, '09-24 当日收益')

const base = { today_profit: { amount: null }, daily_profit: 999, estimate_scenario: { amount: 500 } }
assert.equal(rowTodayDisplay(base).amount, null)
assert.equal(rowTodayDisplay({ ...base, overseas_recent: {} }).label, '海外日期待确认')
assert.equal(rowTodayDisplay({ ...base, monitoring_profit: { amount: 0, kind: 'estimate' } }).amount, 0)
assert.equal(rowTodayDisplay({ ...base, monitoring_profit: { amount: 100, kind: 'official_measurement' } }).label, '官方测算')

// 行内标记测试
const rowQDII = {
  ...base,
  session_profit: { amount: -50, return_pct: -1.2, return_date: '2026-09-23', kind: 'official_measurement', in_session: false },
  estimated_status: 'none',
}
// 在 latest_official 模式下，不显示「无估值」或「延迟」，只显示 MM-DD（09-23）
const viewQdiiOfficial = rowSessionProfitView(rowQDII, dsOfficialPartial.display_session)
assert.deepEqual(viewQdiiOfficial.badges, ['09-23'])
assert.equal(viewQdiiOfficial.amount, -50)

// 在 today 模式下，QDII 日期早于展示日显示 MM-DD
const viewQdiiToday = rowSessionProfitView(rowQDII, dsToday.display_session)
assert.deepEqual(viewQdiiToday.badges, ['09-23'])

// 估值行在混合展示日下带「估」
const rowEstimate = {
  ...base,
  session_profit: { amount: 30, return_pct: 0.6, return_date: '2026-09-24', kind: 'estimate', in_session: true },
  estimated_status: 'available',
}
const viewEstimateToday = rowSessionProfitView(rowEstimate, dsToday.display_session)
assert.deepEqual(viewEstimateToday.badges, ['估'])

// 延迟行在 today 模式下显示「延迟」
const rowStale = {
  ...base,
  session_profit: { amount: 10, return_pct: 0.2, return_date: '2026-09-24', kind: 'estimate', in_session: true },
  estimated_status: 'stale',
}
const viewStaleToday = rowSessionProfitView(rowStale, dsToday.display_session)
assert.deepEqual(viewStaleToday.badges, ['延迟', '估'])

// 但在 latest_official 模式下不显示「延迟」
const viewStaleOfficial = rowSessionProfitView(rowStale, dsOfficialPartial.display_session)
assert.deepEqual(viewStaleOfficial.badges, [])

// 现金行不显示
const rowCash = { is_cash_asset: true }
assert.equal(rowSessionProfitView(rowCash, dsToday.display_session).isCash, true)
assert.deepEqual(rowSessionProfitView(rowCash, dsToday.display_session).badges, [])

// 海外行展示最近来源估值（可能非今日），不借用当日字段；境内行只用当日估值。
const overseasRow = {
  ...base,
  estimated_nav: null, estimated_return_pct: null, estimated_return_state: 'muted',
  estimated_at: '2026-09-26 04:00:00', estimated_source: 'sina_fund_estimate',
  overseas_recent: {
    return_pct: -0.8758, estimated_nav: 4.1736, estimated_at: '2026-09-26 04:00:00',
    source: 'sina_fund_estimate', previous_nav: 4.2105, previous_nav_date: '2026-09-23',
    basis_status: 'consistent', is_current: false,
  },
}
const overseasView = rowEstimateView(overseasRow)
assert.equal(overseasView.overseas, true)
assert.equal(overseasView.pct, -0.8758)
assert.equal(overseasView.state, 'fall')
assert.equal(overseasView.basisDate, '2026-09-23')
assert.equal(overseasView.conflict, false)
assert.equal(rowEstimateView({ ...overseasRow, overseas_recent: { ...overseasRow.overseas_recent, basis_status: 'conflict' } }).conflict, true)
const domesticView = rowEstimateView({ ...overseasRow, overseas_recent: null, estimated_return_pct: 0.5, estimated_return_state: 'rise' })
assert.equal(domesticView.overseas, false)
assert.equal(domesticView.pct, 0.5)
assert.equal(domesticView.basisDate, null)
console.log('Today profit presentation checks passed')
