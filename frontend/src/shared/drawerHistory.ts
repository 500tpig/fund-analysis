/**
 * 抽屉与浏览器返回栈的协调。
 *
 * 同一时刻所有抽屉共用一条历史记录：任何抽屉打开时只压入一条，最后一条关闭时消费它，
 * 返回手势每次关闭最上层抽屉。这样下层抽屉先关闭时不会留下无人认领的记录。
 *
 * 两处需要额外保护：
 * - 同一帧内关闭一个抽屉并打开另一个（模拟页「运行详情 → 订单详情」）时，记录直接交接给
 *   新抽屉，不做回退，因此关闭决定延后一个微任务再执行。
 * - 主动关闭需要回退时，回退事件是异步到达的；期间可能已有新抽屉接管记录，所以标记这次
 *   回退是自己发起的，到达时不再关闭界面。
 *
 * 路由跳转或组件卸载导致记录无法消费时，记录会残留在历史中；下一次返回到达该记录时
 * 会再回退一次把它跳过，用户不需要多按一次返回。卸载时不能无条件回退，否则会撤销
 * 正在进行的路由跳转。
 */

export const DRAWER_HISTORY_MARKER = '__fundDrawer'

export interface DrawerHistoryEnv {
  /** 当前历史项的状态 */
  currentState: () => Record<string, unknown> | null
  /** 压入一条保护记录 */
  pushState: (state: Record<string, unknown>) => void
  /** 回退一条记录 */
  back: () => void
  /** 订阅返回事件，同一环境只订阅一次 */
  listen: (handler: (state: unknown) => void) => void
}

export interface DrawerHandle {
  close: () => void
}

export interface DrawerHistory {
  register: (close: () => void) => DrawerHandle
  unregister: (handle: DrawerHandle) => void
}

function hasMarker(state: unknown): boolean {
  return Boolean(state && typeof state === 'object' && DRAWER_HISTORY_MARKER in (state as Record<string, unknown>))
}

export function createDrawerHistory(env: DrawerHistoryEnv): DrawerHistory {
  const open: DrawerHandle[] = []
  let armed = false
  let orphaned = false
  let subscribed = false
  let ownPopPending = false

  function arm(): void {
    if (armed) return
    armed = true
    if (!subscribed) {
      subscribed = true
      env.listen(onPopState)
    }
    // 当前记录已是保护记录时直接沿用，避免交接时压入重复记录。
    if (hasMarker(env.currentState())) return
    env.pushState({ ...(env.currentState() ?? {}), [DRAWER_HISTORY_MARKER]: true })
  }

  function release(): void {
    if (!armed) return
    armed = false
    if (!hasMarker(env.currentState())) {
      // 当前记录已被路由跳转占走：保护记录埋在下面，等下次返回到达时跳过。
      orphaned = true
      return
    }
    // 同一帧内可能已有新抽屉接管这条记录，等本轮界面状态更新结束再决定。
    void Promise.resolve().then(() => {
      if (open.length > 0) {
        armed = true
        return
      }
      if (!hasMarker(env.currentState())) return
      ownPopPending = true
      env.back()
    })
  }

  function onPopState(state: unknown): void {
    if (ownPopPending) {
      // 自己发起的回退，界面已经关好了。
      ownPopPending = false
      return
    }
    // 返回事件已经消费掉一条记录。
    armed = false
    const top = open[open.length - 1]
    if (!top) {
      if (orphaned && hasMarker(state)) {
        orphaned = false
        ownPopPending = true
        env.back()
      }
      return
    }
    top.close()
    // top 仍在数组中：界面要到下一次刷新才卸载，长度大于 1 表示还有下层抽屉需要保护。
    if (open.length > 1) arm()
  }

  return {
    register(close: () => void): DrawerHandle {
      const handle: DrawerHandle = { close }
      open.push(handle)
      arm()
      return handle
    },
    unregister(handle: DrawerHandle): void {
      const index = open.indexOf(handle)
      if (index >= 0) open.splice(index, 1)
      if (open.length === 0) release()
    },
  }
}

let browserHistory: DrawerHistory | null = null

/** 浏览器环境下的单例；模块导入本身不触碰 window，便于在 node 中直接测试工厂。 */
export function useDrawerHistory(): DrawerHistory {
  if (!browserHistory) {
    browserHistory = createDrawerHistory({
      currentState: () => (window.history.state ?? null) as Record<string, unknown> | null,
      pushState: (state) => window.history.pushState(state, ''),
      back: () => window.history.back(),
      listen: (handler) => {
        window.addEventListener('popstate', (event) => handler(event.state))
      },
    })
  }
  return browserHistory
}
