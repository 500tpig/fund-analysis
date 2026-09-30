import { createRouter, createWebHistory } from 'vue-router'

import { getPageTitle, APP_LAYOUT_TITLE } from './navigation'
import { appRoutes } from './routes'

const router = createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes: appRoutes,
})

router.afterEach((to) => {
  const title = getPageTitle(to)
  document.title = title === APP_LAYOUT_TITLE ? APP_LAYOUT_TITLE : `${title} — ${APP_LAYOUT_TITLE}`
})

export default router
