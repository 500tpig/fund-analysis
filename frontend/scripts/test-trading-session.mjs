import assert from 'node:assert/strict'
import {
  parseBeijingTime,
  toTradingMinute,
  computeTradingAxisLayout,
  mapTradingMinuteToX,
  splitTradingSessions,
  getLatestValidPoint,
} from '../src/shared/tradingSession.ts'

console.log('--- Testing tradingSession.ts pure functions ---')

// 1. Time parsing (Beijing Time vs ISO vs Local)
assert.deepEqual(parseBeijingTime('09:30'), [9, 30, 0])
assert.deepEqual(parseBeijingTime('11:30:00'), [11, 30, 0])
assert.deepEqual(parseBeijingTime('13:48'), [13, 48, 0])
assert.deepEqual(parseBeijingTime('15:00:00'), [15, 0, 0])

// UTC timestamp: 2026-09-02T05:48:00Z -> Beijing time (UTC+8) is 13:48:00
assert.deepEqual(parseBeijingTime('2026-09-02T05:48:00Z'), [13, 48, 0])
// Datetime string without TZ
assert.deepEqual(parseBeijingTime('2026-09-02 13:48:00'), [13, 48, 0])

// 2. Trading minute mappings
// 09:30 is morning start (minute 0)
const m0930 = toTradingMinute('09:30')
assert.equal(m0930.session, 'morning')
assert.equal(m0930.sessionMinute, 0)
assert.equal(m0930.tradingMinute, 0)
assert.equal(m0930.isBreak, false)

// 11:30 is morning end (minute 120)
const m1130 = toTradingMinute('11:30')
assert.equal(m1130.session, 'morning')
assert.equal(m1130.sessionMinute, 120)
assert.equal(m1130.tradingMinute, 120)
assert.equal(m1130.isBreak, false)

// 11:31 is lunch break
const m1131 = toTradingMinute('11:31')
assert.equal(m1131.isBreak, true)
assert.equal(m1131.session, 'closed')

// 12:59 is lunch break
const m1259 = toTradingMinute('12:59')
assert.equal(m1259.isBreak, true)
assert.equal(m1259.session, 'closed')

// 13:00 is afternoon start (minute 0 of afternoon, trading minute 120)
const m1300 = toTradingMinute('13:00')
assert.equal(m1300.session, 'afternoon')
assert.equal(m1300.sessionMinute, 0)
assert.equal(m1300.tradingMinute, 120)
assert.equal(m1300.isBreak, false)

// 13:48 is minute 48 of afternoon, trading minute 168
const m1348 = toTradingMinute('13:48')
assert.equal(m1348.session, 'afternoon')
assert.equal(m1348.sessionMinute, 48)
assert.equal(m1348.tradingMinute, 168)
assert.equal(m1348.isBreak, false)

// 15:00 is afternoon end (minute 120 of afternoon, trading minute 240)
const m1500 = toTradingMinute('15:00')
assert.equal(m1500.session, 'afternoon')
assert.equal(m1500.sessionMinute, 120)
assert.equal(m1500.tradingMinute, 240)
assert.equal(m1500.isBreak, false)

// 3. Layout with lunch break gap (36px by default)
const layout = computeTradingAxisLayout(720, 62, 18, 36)
assert.equal(layout.left, 62)
assert.equal(layout.right, 18)
assert.equal(layout.plotWidth, 640) // 720 - 62 - 18
assert.equal(layout.breakGap, 36)
assert.equal(layout.morningWidth, (640 - 36) / 2) // 302
assert.equal(layout.afternoonWidth, 302)
assert.equal(layout.morningStartX, 62)
assert.equal(layout.morningEndX, 62 + 302) // 364
assert.equal(layout.afternoonStartX, 364 + 36) // 400
assert.equal(layout.afternoonEndX, 400 + 302) // 702
assert.equal(layout.breakCenterX, 364 + 18) // 382

// 11:30 must map to morningEndX (364)
const x1130 = mapTradingMinuteToX(m1130, layout)
assert.equal(x1130, 364)

// 13:00 must map to afternoonStartX (400)
const x1300 = mapTradingMinuteToX(m1300, layout)
assert.equal(x1300, 400)

// 11:30 and 13:00 are separated by exactly 36px break gap!
assert.equal(x1300 - x1130, 36)

// 4. Session split filters lunch break
const testPoints = [
  { x: '09:30', y: 0.1 },
  { x: '10:00', y: 0.2 },
  { x: '11:30', y: 0.3 },
  { x: '11:45', y: 0.35 }, // break - should be skipped
  { x: '12:30', y: 0.36 }, // break - should be skipped
  { x: '13:00', y: 0.4 },
  { x: '13:48', y: -0.18 },
]
const split = splitTradingSessions(testPoints)
assert.equal(split.morning.length, 3)
assert.equal(split.morning[0].x, '09:30')
assert.equal(split.morning[2].x, '11:30')

assert.equal(split.afternoon.length, 2)
assert.equal(split.afternoon[0].x, '13:00')
assert.equal(split.afternoon[1].x, '13:48')

// 5. getLatestValidPoint
assert.equal(getLatestValidPoint(testPoints)?.x, '13:48')
assert.equal(getLatestValidPoint(testPoints)?.y, -0.18)
assert.equal(getLatestValidPoint([]), null)

console.log('✓ All tradingSession tests passed successfully!')
