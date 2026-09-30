import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { setImmediate } from 'node:timers/promises'
import { createContext, runInContext } from 'node:vm'
import ts from 'typescript'

import {
  CHECK_TICK_MS,
  DISCLOSURE_INTERVAL_MS,
  INTRADAY_INTERVAL_MS,
  MAX_AUTO_FAILURES,
  MAX_BACKOFF_MS,
  ONE_SHOT_COOLDOWN_MS,
  backoffDelay,
  describeSourceUpdates,
  diffSourceUpdates,
  isFailedRun,
  pollingInterval,
  shouldAutoRefresh,
  shouldInitialBackfill,
} from '../src/shared/refreshPolicy.ts'

const NOW = Date.parse('2026-09-28T02:00:00Z')
const minutesAgo = (minutes) => new Date(NOW - minutes * 60_000).toISOString()

function monitor({ sessions = [], window = false, eligible = false, quoteEligible = false, lastRun = null, rows } = {}) {
  return {
    rows: rows ?? [{
      code: '110011', is_cash_asset: false, in_disclosure_window: window,
      auto_refresh_eligible: eligible, estimated_at: '2026-09-28 10:00', nav_date: '2026-09-25',
      overseas_recent: null,
    }],
    market_quotes: {
      refresh_eligible: quoteEligible,
      items: sessions.map((status, index) => ({ symbol: ['CSI_ALL_SHARE', 'AU9999', 'NDX'][index], session_status: status, quoted_at: null })),
    },
    data_freshness: { sources: lastRun ? [{ completed_at: lastRun }] : [] },
  }
}

// 交易时段与披露窗口分别决定间隔；都不在时不轮询。
assert.equal(pollingInterval(monitor({ sessions: ['trading', 'closed', 'closed'] })), INTRADAY_INTERVAL_MS)
assert.equal(pollingInterval(monitor({ sessions: ['closed', 'closed', 'closed'], window: true })), DISCLOSURE_INTERVAL_MS)
assert.equal(pollingInterval(monitor({ sessions: ['closed', 'break', 'closed'] })), null)
assert.equal(pollingInterval(null), null)
assert.ok(CHECK_TICK_MS < INTRADAY_INTERVAL_MS)

// 服务端最近运行时间参与判断：其他标签页刚刷新过则不重复请求。
const trading = { sessions: ['trading', 'closed', 'closed'] }
assert.equal(shouldAutoRefresh({ monitor: monitor({ ...trading, lastRun: minutesAgo(1) }), now: NOW, lastAttemptAt: null, failures: 0 }), false)
assert.equal(shouldAutoRefresh({ monitor: monitor({ ...trading, lastRun: minutesAgo(6) }), now: NOW, lastAttemptAt: null, failures: 0 }), true)
assert.equal(shouldAutoRefresh({ monitor: monitor({ ...trading, lastRun: minutesAgo(6) }), now: NOW, lastAttemptAt: NOW - 60_000, failures: 0 }), false)
assert.equal(shouldAutoRefresh({ monitor: monitor(trading), now: NOW, lastAttemptAt: null, failures: 0 }), true)

// 休市且无补刷需求时不请求。
assert.equal(shouldAutoRefresh({ monitor: monitor({ sessions: ['closed', 'closed', 'closed'] }), now: NOW, lastAttemptAt: null, failures: 0 }), false)

// 逾期或行情过旧只做带冷却的一次性补刷。
const overdue = monitor({ sessions: ['closed', 'closed', 'closed'], eligible: true, lastRun: minutesAgo(1) })
assert.equal(shouldAutoRefresh({ monitor: overdue, now: NOW, lastAttemptAt: null, failures: 0 }), false)
assert.equal(shouldAutoRefresh({ monitor: { ...overdue, data_freshness: { sources: [{ completed_at: minutesAgo(4) }] } }, now: NOW, lastAttemptAt: null, failures: 0 }), true)
assert.equal(shouldAutoRefresh({ monitor: monitor({ quoteEligible: true, lastRun: minutesAgo(4) }), now: NOW, lastAttemptAt: null, failures: 0 }), true)

// 连续失败退避，封顶；达到上限后暂停。
assert.equal(backoffDelay(INTRADAY_INTERVAL_MS, 1), 2 * INTRADAY_INTERVAL_MS)
assert.equal(backoffDelay(INTRADAY_INTERVAL_MS, 10), MAX_BACKOFF_MS)
assert.equal(backoffDelay(ONE_SHOT_COOLDOWN_MS, 0), ONE_SHOT_COOLDOWN_MS)
assert.equal(shouldAutoRefresh({ monitor: monitor({ ...trading, lastRun: minutesAgo(6) }), now: NOW, lastAttemptAt: null, failures: 1 }), false)
assert.equal(shouldAutoRefresh({ monitor: monitor({ ...trading, lastRun: minutesAgo(11) }), now: NOW, lastAttemptAt: null, failures: 1 }), true)
assert.equal(shouldAutoRefresh({ monitor: monitor(trading), now: NOW, lastAttemptAt: null, failures: MAX_AUTO_FAILURES }), false)

// 请求成功但所有来源失败才计入连续失败。
const run = { has_failure: true, success: 0, estimate_success: 0, quote_success: 0 }
assert.equal(isFailedRun(run), true)
assert.equal(isFailedRun({ ...run, quote_success: 1 }), false)
assert.equal(isFailedRun({ ...run, has_failure: false }), false)

// 区分请求完成与来源数据实际更新。
const before = monitor({ sessions: ['trading'] })
before.market_quotes.items[0].quoted_at = '2026-09-28T01:55:00Z'
const same = diffSourceUpdates(before, before)
assert.deepEqual(same, { estimates: 0, navs: 0, quotes: 0 })
assert.equal(describeSourceUpdates(same), '来源暂无新数据')
const after = structuredClone(before)
after.rows[0].estimated_at = '2026-09-28 10:05'
after.rows[0].nav_date = '2026-09-26'
after.market_quotes.items[0].quoted_at = '2026-09-28T02:00:00Z'
assert.deepEqual(diffSourceUpdates(before, after), { estimates: 1, navs: 1, quotes: 1 })
assert.equal(describeSourceUpdates(diffSourceUpdates(before, after)), '来源更新：估值 1 只、净值 1 只、行情 1 项')

// 海外最近估值按来源时间比较；现金行不计。
const overseasBefore = monitor({ rows: [
  { code: '017731', is_cash_asset: false, estimated_at: '2026-09-26 04:00:00', nav_date: '2026-09-23', overseas_recent: { estimated_at: '2026-09-26 04:00:00' } },
  { code: 'CASH', is_cash_asset: true, estimated_at: null, nav_date: '2026-09-28', overseas_recent: null },
] })
const overseasAfter = structuredClone(overseasBefore)
overseasAfter.rows[1].nav_date = '2026-09-29'
assert.deepEqual(diffSourceUpdates(overseasBefore, overseasAfter), { estimates: 0, navs: 0, quotes: 0 })

// 首次进入页面与重新进入页面时的补拉与 3 分钟冷却。
const closedMarket = { sessions: ['closed', 'closed', 'closed'] }
// 1. 服务端无历史运行记录：允许补拉
assert.equal(shouldInitialBackfill({ monitor: monitor(closedMarket), now: NOW, lastAttemptAt: null, failures: 0 }), true)
// 2. 服务端在 4 分钟前运行过刷新（超过 3 分钟冷却）：允许补拉
assert.equal(shouldInitialBackfill({ monitor: monitor({ ...closedMarket, lastRun: minutesAgo(4) }), now: NOW, lastAttemptAt: null, failures: 0 }), true)
// 3. 服务端在 1 分钟前刚运行过刷新（处于 3 分钟冷却内）：不补拉
assert.equal(shouldInitialBackfill({ monitor: monitor({ ...closedMarket, lastRun: minutesAgo(1) }), now: NOW, lastAttemptAt: null, failures: 0 }), false)
// 4. 重新进入页面（模拟路由切换后再次触发初次加载判断）：若在 3 分钟内冷却生效，不重复补拉
assert.equal(shouldInitialBackfill({ monitor: monitor({ ...closedMarket, lastRun: minutesAgo(2) }), now: NOW, lastAttemptAt: NOW - 120_000, failures: 0 }), false)
// 5. 连续失败达到上限后暂停补拉
assert.equal(shouldInitialBackfill({ monitor: monitor(closedMarket), now: NOW, lastAttemptAt: null, failures: MAX_AUTO_FAILURES }), false)

// 执行组件中的真实恢复路径，避免只测策略函数而漏掉调用顺序。
const view = readFileSync(new URL('../src/views/FundsView.vue', import.meta.url), 'utf8')
const lifecycle = view.slice(view.indexOf('async function maybeInitialBackfill'), view.indexOf('onMounted(() =>'))
for (const scenario of ['recent', 'old', 'hidden', 'unmounted']) {
  const events = []
  const context = createContext({
    pageActive: true, initialBackfillPending: true, refreshInFlight: false,
    funds: { value: monitor({ ...closedMarket, lastRun: minutesAgo(10) }) },
    document: { visibilityState: 'visible' }, autoFailures: { value: 0 },
    lastAttemptAt: null, checkTimer: null, CHECK_TICK_MS,
    setInterval: () => 1, clearInterval: () => {},
    shouldAutoRefresh, shouldInitialBackfill,
    runRefresh: async () => events.push('refresh'),
    maybeAutoRefresh: async () => events.push('regular'),
  })
  context.syncLocalState = async () => {
    events.push('sync')
    context.funds.value = monitor({ ...closedMarket, lastRun: minutesAgo(scenario === 'recent' ? 1 : 10) })
    if (scenario === 'hidden') context.document.visibilityState = 'hidden'
    if (scenario === 'unmounted') context.pageActive = false
  }
  // 策略中的 now 与服务端记录使用同一固定时间。
  context.Date = { now: () => NOW }
  runInContext(ts.transpile(lifecycle), context)
  runInContext('onVisibilityChange()', context)
  await setImmediate()
  assert.deepEqual(events, scenario === 'old' ? ['sync', 'refresh'] : ['sync'], scenario)
  assert.equal(context.initialBackfillPending, scenario === 'hidden' || scenario === 'unmounted', scenario)
  if (scenario === 'recent' || scenario === 'old') {
    events.length = 0
    runInContext('onVisibilityChange()', context)
    await setImmediate()
    assert.deepEqual(events, ['sync', 'regular'], `${scenario}: 首次判断不能重复`)
  }
}

console.log('Refresh policy checks passed')
