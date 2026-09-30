import type { RouteRecordRaw } from 'vue-router'

export const appShellChildren = [
  { path: '', redirect: { name: 'valuation' } },
  {
    path: 'valuation',
    name: 'valuation',
    component: () => import('@/views/FundsView.vue'),
    meta: {
      section: 'valuation',
      navLevel: 'primary',
      label: '估值',
      title: '基金估值',
      description: '查看盘中估值、官方净值、持仓收益与组合分析',
      order: 10,
      breadcrumb: '估值',
      sourceOwner: 'valuation',
      iconKey: 'valuation',
    },
  },
  {
    path: 'holdings',
    name: 'holdings',
    component: () => import('@/views/HoldingsView.vue'),
    meta: {
      section: 'holdings',
      navLevel: 'primary',
      label: '持仓',
      title: '持仓管理',
      description: '新增、修改或删除基金持仓',
      order: 20,
      breadcrumb: '持仓',
      sourceOwner: 'holdings',
      iconKey: 'holdings',
    },
  },
  {
    path: 'import',
    name: 'import',
    component: () => import('@/views/ImportView.vue'),
    meta: {
      section: 'import',
      navLevel: 'primary',
      label: '录入',
      title: '持仓录入',
      description: '识别截图并核对持仓变更',
      order: 30,
      breadcrumb: '录入',
      sourceOwner: 'import',
      iconKey: 'import',
    },
  },
  { path: ':pathMatch(.*)*', redirect: { name: 'valuation' } },
] satisfies RouteRecordRaw[]
export const appRoutes = [
  {
    path: '/',
    component: () => import('@/layouts/AppLayout.vue'),
    children: appShellChildren,
  },
] satisfies RouteRecordRaw[]
