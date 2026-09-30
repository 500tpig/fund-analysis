import type { RouteLocationNormalized, RouteLocationNormalizedLoaded, RouteRecordRaw } from 'vue-router'

import type { AppIconKey, AppRouteMeta, AppRouteName } from './meta'
import { appShellChildren } from './routes'

export const APP_LAYOUT_TITLE = '基金估值'
export const APP_LAYOUT_DESCRIPTION = '本地基金估值与持仓管理'

export interface AppNavItem {
  key: AppRouteName
  label: string
  description: string
  to: { name: AppRouteName }
  iconKey?: AppIconKey
}
function meta(record: { meta?: unknown }): Partial<AppRouteMeta> {
  return (record.meta ?? {}) as Partial<AppRouteMeta>
}

const primaryRecords = appShellChildren
  .filter((record) => meta(record).navLevel === 'primary' && typeof record.name === 'string')
  .sort((left, right) => (meta(left).order ?? 0) - (meta(right).order ?? 0))

export function getPrimaryNavItems(): AppNavItem[] {
  return primaryRecords.map((record) => ({
    key: record.name as AppRouteName,
    label: meta(record).label ?? '',
    description: meta(record).description ?? APP_LAYOUT_DESCRIPTION,
    to: { name: record.name as AppRouteName },
    iconKey: meta(record).iconKey,
  }))
}

export function getActivePrimaryRouteName(route: RouteLocationNormalizedLoaded): AppRouteName | null {
  const record = [...route.matched].reverse().find((item) => meta(item).navLevel === 'primary')
  return typeof record?.name === 'string' ? record.name as AppRouteName : null
}

export function getPageTitle(route: RouteLocationNormalizedLoaded | RouteLocationNormalized): string {
  const record = [...route.matched].reverse().find((item) => meta(item).title)
  return meta(record ?? {}).title ?? APP_LAYOUT_TITLE
}

export function getPageDescription(route: RouteLocationNormalizedLoaded): string {
  const record = [...route.matched].reverse().find((item) => meta(item).description)
  return meta(record ?? {}).description ?? APP_LAYOUT_DESCRIPTION
}

export function buildBreadcrumbs(route: RouteLocationNormalizedLoaded): Array<{ key: string; label: string }> {
  const record = [...route.matched].reverse().find((item) => meta(item).navLevel === 'primary')
  if (!record) return []
  return [{ key: String(record.name ?? record.path), label: meta(record).breadcrumb as string || meta(record).label || '' }]
}

export function isPrimaryRecord(record: RouteRecordRaw): boolean {
  return meta(record).navLevel === 'primary'
}
