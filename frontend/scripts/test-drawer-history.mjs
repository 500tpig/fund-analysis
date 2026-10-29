/**
 * 抽屉返回栈回归：覆盖叠放抽屉切换、同帧交接与路由离开三条路径。
 *
 * 用假的历史栈模拟浏览器与 vue-router：push 截断前进历史，back 与返回手势只移动游标
 * （真实浏览器同样把已消费的记录留在前进历史里），因此判断残留看的是「用户返回路径上
 * 还有没有已关闭抽屉的记录」。
 */
import assert from 'node:assert/strict'

import { createDrawerHistory, DRAWER_HISTORY_MARKER } from '../src/shared/drawerHistory.ts'

const hasMarker = (state) => Boolean(state && typeof state === 'object' && DRAWER_HISTORY_MARKER in state)

/** 用户返回路径上是否还有未消费的抽屉记录 */
function reachableDrawerRecord(browser) {
  return browser.stack.slice(0, browser.index + 1).some(hasMarker)
}

/** 关闭抽屉时的回退决定延后一个微任务，断言前先让微任务队列排空 */
async function settle() {
  for (let i = 0; i < 3; i += 1) await Promise.resolve()
}

function createFakeBrowser(path = '/holdings') {
  const stack = [{ current: path, position: 0, tag: 'page' }]
  let index = 0
  let handler = null
  const fire = () => handler?.(stack[index])

  return {
    stack,
    get index() {
      return index
    },
    current: () => stack[index],
    env: {
      currentState: () => stack[index],
      pushState: (state) => {
        stack.length = index + 1
        stack.push(state)
        index += 1
      },
      back: () => {
        if (index === 0) return
        index -= 1
        fire()
      },
      listen: (value) => {
        handler = value
      },
    },
    /** 用户按下返回键或侧滑手势 */
    pressBack: () => {
      if (index === 0) return
      index -= 1
      fire()
    },
    /** vue-router push：新增一条记录 */
    pushRoute: (next) => {
      stack.length = index + 1
      stack.push({ current: next, position: index + 1, tag: 'route' })
      index += 1
    },
    /** vue-router replace：改写当前记录，不新增 */
    replaceRoute: (next) => {
      stack[index] = { current: next, position: index, tag: 'route' }
    },
  }
}

console.log('--- Testing drawerHistory.ts ---')

// 1. 叠放抽屉只压一条记录；下层先关闭不残留，全部关闭后回到进入前的记录
{
  const browser = createFakeBrowser()
  const history = createDrawerHistory(browser.env)
  const page = browser.current()
  const runDrawer = history.register(() => {})
  const orderDrawer = history.register(() => {})
  assert.equal(browser.stack.length, 2, '两个抽屉只应压入一条保护记录')

  history.unregister(runDrawer)
  assert.equal(browser.index, 1, '上层抽屉仍打开时不得回退记录')
  assert.equal(hasMarker(browser.current()), true, '保护记录必须保留给仍在打开的抽屉')

  history.unregister(orderDrawer)
  await settle()
  assert.equal(browser.current(), page, '全部关闭后应回到进入前的记录')
  assert.equal(reachableDrawerRecord(browser), false, '全部关闭后返回路径上不得残留记录')
}

// 2. 返回手势逐个关闭叠放抽屉，界面在下一帧才卸载
{
  const browser = createFakeBrowser()
  const history = createDrawerHistory(browser.env)
  const page = browser.current()
  const closed = []
  const lower = history.register(() => closed.push('lower'))
  const upper = history.register(() => closed.push('upper'))

  browser.pressBack()
  assert.deepEqual(closed, ['upper'], '返回手势只关闭最上层抽屉')
  history.unregister(upper)
  assert.equal(hasMarker(browser.current()), true, '下层抽屉仍受保护')
  assert.equal(browser.stack.length, 2)

  browser.pressBack()
  assert.deepEqual(closed, ['upper', 'lower'])
  history.unregister(lower)
  assert.equal(browser.current(), page)
  assert.equal(reachableDrawerRecord(browser), false, '全部关闭后返回路径上不得残留记录')
}

// 3. 同一帧内交接：收起一个抽屉并打开另一个，只保留一条记录且不触发回退
{
  const browser = createFakeBrowser()
  const history = createDrawerHistory(browser.env)
  const page = browser.current()
  const runDrawer = history.register(() => {})

  history.unregister(runDrawer)
  const orderDrawer = history.register(() => {})
  assert.equal(browser.stack.length, 2, '交接后仍只应有一条保护记录')
  assert.equal(hasMarker(browser.current()), true, '保护记录必须交给新抽屉')

  await settle()
  assert.equal(browser.index, 1, '交接不得触发回退')

  history.unregister(orderDrawer)
  await settle()
  assert.equal(browser.current(), page, '交接后的抽屉关闭时要回到进入前的记录')
  assert.equal(reachableDrawerRecord(browser), false)
}

// 4. 单个抽屉：主动关闭消费自己那条记录
{
  const browser = createFakeBrowser()
  const history = createDrawerHistory(browser.env)
  const page = browser.current()
  const handle = history.register(() => {})
  history.unregister(handle)
  await settle()
  assert.equal(browser.current(), page, '主动关闭应消费自己的保护记录')
  assert.equal(reachableDrawerRecord(browser), false)
}

// 5. 返回手势关闭单个抽屉后不得重新压入记录
{
  const browser = createFakeBrowser()
  const history = createDrawerHistory(browser.env)
  let closed = false
  const handle = history.register(() => {
    closed = true
  })
  browser.pressBack()
  assert.equal(closed, true)
  history.unregister(handle)
  await settle()
  assert.equal(browser.index, 0, '返回手势消费后仍停在进入前的记录')
  assert.equal(reachableDrawerRecord(browser), false, '返回手势消费后不得重新压入记录')
}

// 6. 抽屉打开时离开页面：卸载不得撤销跳转，下次返回要跳过残留记录
{
  const browser = createFakeBrowser()
  const history = createDrawerHistory(browser.env)
  const page = browser.current()
  const handle = history.register(() => {})

  browser.pushRoute('/valuation')
  history.unregister(handle)
  await settle()
  assert.equal(browser.current().current, '/valuation', '卸载不得撤销正在进行的跳转')

  browser.pressBack()
  assert.equal(browser.current(), page, '一次返回就回到持仓页，不需要多按一次')
  assert.equal(browser.index, 0)
  assert.equal(reachableDrawerRecord(browser), false)
}

// 7. 记录被 replace 改写时不回退，避免撤销导航
{
  const browser = createFakeBrowser()
  const history = createDrawerHistory(browser.env)
  const handle = history.register(() => {})
  browser.replaceRoute('/valuation')
  history.unregister(handle)
  await settle()
  assert.equal(browser.current().current, '/valuation', '记录被改写时不得回退')

  browser.pressBack()
  assert.equal(browser.current().tag, 'page')
}

console.log('✓ All drawer history tests passed successfully!')
