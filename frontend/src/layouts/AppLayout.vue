<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  Activity,
  Wallet,
  ScanLine,
  ChevronLeft,
} from '@lucide/vue'

import type { AppIconKey, AppRouteName } from '@/router/meta'
import { getActivePrimaryRouteName, getPageTitle, getPrimaryNavItems } from '@/router/navigation'
import { isEditableTarget } from '@/shared/hotkeys'

const route = useRoute()
const router = useRouter()
const collapsed = ref(false)

function collapseIfNarrow() {
  if (window.innerWidth <= 860) collapsed.value = true
}

onMounted(() => {
  collapseIfNarrow()
  window.addEventListener('resize', collapseIfNarrow)
  window.addEventListener('keydown', onHotkey)
})

onUnmounted(() => {
  window.removeEventListener('resize', collapseIfNarrow)
  window.removeEventListener('keydown', onHotkey)
})

const iconMap = {
  valuation: Activity,
  holdings: Wallet,
  import: ScanLine,
} satisfies Record<AppIconKey, unknown>

const navItems = getPrimaryNavItems()
const activeKey = computed(() => getActivePrimaryRouteName(route))

watch(
  () => route.fullPath,
  () => {
    document.title = `${getPageTitle(route)} · FundPilot`
  },
  { immediate: true },
)

function prefersReducedMotion(): boolean {
  return window.matchMedia('(prefers-reduced-motion: reduce)').matches
}

function scrollMainToTop() {
  window.scrollTo({ top: 0, behavior: prefersReducedMotion() ? 'auto' : 'smooth' })
}

function navigate(name: AppRouteName) {
  if (activeKey.value === name) {
    scrollMainToTop()
    return
  }
  void router.push({ name })
}

function onHotkey(event: KeyboardEvent) {
  if (event.isComposing || isEditableTarget(event.target)) return
  if (event.metaKey || event.ctrlKey || event.altKey) return
  if (event.key === '[') {
    collapsed.value = !collapsed.value
    return
  }
  const index = Number(event.key)
  const item = navItems[index - 1]
  if (item) {
    event.preventDefault()
    navigate(item.key)
  }
}
</script>

<template>
  <div class="stage stage--desk flex min-h-screen w-full text-[var(--text-primary)]">
    <a
      class="skip-link"
      href="#workbench-main"
    >跳到内容</a>

    <aside
      class="rail"
      :class="collapsed ? 'w-[72px]' : 'w-[216px]'"
    >
      <div
        class="flex h-16 items-center gap-3 overflow-hidden px-4"
        :class="collapsed ? 'justify-center px-0' : ''"
      >
        <div
          class="brand-mark shrink-0"
          aria-hidden="true"
        >
          <span /><span /><span />
        </div>
        <div
          v-if="!collapsed"
          class="min-w-0 leading-none"
        >
          <div class="truncate text-[15px] font-medium tracking-tight text-[var(--text-primary)]">
            FundPilot
          </div>
          <div class="mt-1 text-[11px] text-[var(--text-secondary)]">
            资产工作台
          </div>
        </div>
      </div>

      <nav
        class="flex-1 space-y-1 px-3 py-2"
        aria-label="主导航"
      >
        <button
          v-for="item in navItems"
          :key="item.key"
          type="button"
          :class="['nav-item', activeKey === item.key ? 'is-active' : '', collapsed ? 'justify-center px-0' : '']"
          :aria-current="activeKey === item.key ? 'page' : undefined"
          :title="collapsed ? item.label : undefined"
          @click="navigate(item.key)"
        >
          <component
            :is="iconMap[item.iconKey || 'valuation']"
            class="h-[18px] w-[18px] shrink-0"
            :stroke-width="1.75"
          />
          <span
            v-if="!collapsed"
            class="truncate"
          >{{ item.label }}</span>
        </button>
      </nav>

      <div class="border-t border-[var(--border-subtle)] px-3 py-3">
        <button
          type="button"
          class="nav-item justify-center text-[12px]"
          :class="collapsed ? '' : 'justify-start'"
          :aria-label="collapsed ? '展开导航' : '收起导航'"
          @click="collapsed = !collapsed"
        >
          <ChevronLeft
            class="h-4 w-4 transition-transform duration-200"
            :class="collapsed ? 'rotate-180' : ''"
            :stroke-width="1.75"
          />
          <span v-if="!collapsed">收起</span>
        </button>
      </div>
    </aside>

    <div class="stage__content relative z-[1] flex min-w-0 flex-1 flex-col">
      <main
        id="workbench-main"
        class="main-pad mx-auto w-full max-w-[1680px] flex-1 px-8 py-5 lg:px-10"
        tabindex="-1"
      >
        <router-view v-slot="{ Component, route: viewRoute }">
          <transition
            name="page"
            mode="out-in"
          >
            <component
              :is="Component"
              :key="String(viewRoute.name)"
            />
          </transition>
        </router-view>
      </main>
    </div>

    <nav
      class="mobile-nav"
      aria-label="移动端主导航"
    >
      <button
        v-for="item in navItems"
        :key="item.key"
        type="button"
        class="mobile-nav__item"
        :class="activeKey === item.key ? 'is-active' : ''"
        :aria-current="activeKey === item.key ? 'page' : undefined"
        @click="navigate(item.key)"
      >
        <component
          :is="iconMap[item.iconKey || 'valuation']"
          class="h-5 w-5"
          :stroke-width="1.75"
        />
        <span>{{ item.label }}</span>
      </button>
    </nav>
  </div>
</template>
