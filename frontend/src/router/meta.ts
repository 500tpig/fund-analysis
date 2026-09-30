import 'vue-router'

export type AppSectionKey = 'valuation' | 'holdings' | 'import'
export type AppRouteName = AppSectionKey
export type AppNavLevel = 'primary'
export type RouteSourceOwner = AppSectionKey
export type AppIconKey = AppSectionKey

export interface AppRouteMeta {
  section: AppSectionKey
  navLevel: AppNavLevel
  label: string
  title: string
  description: string
  order: number
  breadcrumb?: string | false
  sourceOwner: RouteSourceOwner
  iconKey?: AppIconKey
}
declare module 'vue-router' {
  // eslint-disable-next-line @typescript-eslint/no-empty-object-type
  interface RouteMeta extends Partial<AppRouteMeta> {}
}
