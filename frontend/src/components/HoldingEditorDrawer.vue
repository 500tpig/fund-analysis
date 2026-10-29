<script setup lang="ts">
import { nextTick, onBeforeUnmount, ref, watch } from 'vue'

import { createHolding, searchFunds, updateHolding } from '@/api/client'
import type { HoldingPayload, HoldingRow } from '@/api/types'
import Button from '@/components/ui/Button.vue'
import Drawer from '@/components/ui/Drawer.vue'
import Input from '@/components/ui/Input.vue'
import { useMessage } from '@/shared/toast'

type HoldingDraft = Omit<HoldingPayload, 'amount' | 'weight_pct' | 'cost' | 'amount_nav_date'> & {
  amount: string | number | null
  weight_pct: string | number | null
  cost: string | number | null
  amount_nav_date: string | null
}

type NameLookupStatus = 'idle' | 'loading' | 'matched' | 'not_found' | 'failed' | 'skipped'

const props = withDefaults(defineProps<{
  show: boolean
  holding?: HoldingRow | null
}>(), {
  holding: null,
})

const emit = defineEmits<{
  'update:show': [value: boolean]
  saved: []
}>()

const saving = ref(false)
const draft = ref<HoldingDraft>(emptyDraft())
const nameLookupStatus = ref<NameLookupStatus>('idle')
const nameLookupMessage = ref('')
const lastLookedUpCode = ref('')
const lastOfficialName = ref('')
const originalCode = ref('')
let ignoreCodeWatch = false
let nameLookupTimer: ReturnType<typeof setTimeout> | null = null
let nameLookupSeq = 0

const message = useMessage()

function emptyDraft(): HoldingDraft {
  return {
    code: '',
    name: '',
    amount: null,
    weight_pct: null,
    cost: null,
    asset_type: null,
    asset_class: null,
    theme: null,
    amount_nav_date: null,
  }
}

function rowToDraft(row: HoldingRow): HoldingDraft {
  return {
    code: row.code,
    name: row.name,
    amount: row.amount,
    weight_pct: row.weight_pct,
    cost: row.cost,
    asset_type: row.asset_type,
    asset_class: row.asset_class,
    theme: row.theme,
    amount_nav_date: row.amount_nav_date,
  }
}

function toNullableNumber(value: string | number | null, label: string): number | null {
  if (value === null || value === '') return null
  const number = Number(value)
  if (!Number.isFinite(number)) throw new Error(`${label}必须是有限数字`)
  return number
}

function isCashDraft(): boolean {
  const code = draft.value.code.trim().toUpperCase()
  const assetType = (draft.value.asset_type || '').trim().toLowerCase()
  return assetType === 'cash' || code === 'CASH' || code.startsWith('CASH:')
}

function isFundCode(code: string): boolean {
  return /^\d{6}$/.test(code.trim())
}

function resetNameLookup(): void {
  nameLookupSeq += 1
  if (nameLookupTimer) {
    clearTimeout(nameLookupTimer)
    nameLookupTimer = null
  }
  nameLookupStatus.value = 'idle'
  nameLookupMessage.value = ''
  lastLookedUpCode.value = ''
  lastOfficialName.value = ''
}

async function lookupOfficialName(code: string): Promise<void> {
  const seq = ++nameLookupSeq
  nameLookupStatus.value = 'loading'
  nameLookupMessage.value = '正在核对正式名称'
  try {
    const result = await searchFunds(code, 8)
    if (seq !== nameLookupSeq) return
    const match = result.items.find((item) => item.code === code)
    lastLookedUpCode.value = code
    if (!match) {
      if (lastOfficialName.value && draft.value.name === lastOfficialName.value) {
        draft.value.name = ''
      }
      lastOfficialName.value = ''
      nameLookupStatus.value = 'not_found'
      nameLookupMessage.value = '未找到该基金的正式名称，请确认代码或手动填写'
      return
    }
    draft.value.name = match.name
    lastOfficialName.value = match.name
    nameLookupStatus.value = 'matched'
    nameLookupMessage.value = '已核对正式名称'
  } catch (caught) {
    if (seq !== nameLookupSeq) return
    lastLookedUpCode.value = code
    if (lastOfficialName.value && draft.value.name === lastOfficialName.value) {
      draft.value.name = ''
    }
    lastOfficialName.value = ''
    nameLookupStatus.value = 'failed'
    nameLookupMessage.value = caught instanceof Error ? caught.message : '正式名称查询失败'
  }
}

function scheduleNameLookup(code: string): void {
  if (nameLookupTimer) clearTimeout(nameLookupTimer)
  nameLookupTimer = setTimeout(() => {
    nameLookupTimer = null
    void lookupOfficialName(code)
  }, 250)
}

function initializeEditor(): void {
  ignoreCodeWatch = true
  draft.value = props.holding ? rowToDraft(props.holding) : emptyDraft()
  originalCode.value = props.holding?.code ?? ''
  resetNameLookup()
  if (props.holding?.is_cash_asset || isCashDraft()) nameLookupStatus.value = 'skipped'
  void nextTick(() => {
    ignoreCodeWatch = false
  })
}

function close(): void {
  emit('update:show', false)
}

async function save(): Promise<void> {
  const code = draft.value.code.trim()
  if (!code) {
    message.warning('基金代码和名称不能为空')
    return
  }
  const editingId = props.holding?.id ?? null
  const shouldVerifyName = isFundCode(code)
    && !isCashDraft()
    && (editingId === null || code !== originalCode.value)
  if (shouldVerifyName) {
    if (
      nameLookupStatus.value === 'loading'
      || nameLookupStatus.value === 'failed'
      || lastLookedUpCode.value !== code
    ) {
      if (nameLookupTimer) {
        clearTimeout(nameLookupTimer)
        nameLookupTimer = null
      }
      await lookupOfficialName(code)
    }
    if (nameLookupStatus.value === 'failed') {
      message.error(nameLookupMessage.value || '正式名称查询失败')
      return
    }
    if (nameLookupStatus.value === 'not_found' && !draft.value.name.trim()) {
      message.warning(nameLookupMessage.value || '未找到该基金的正式名称')
      return
    }
    if (nameLookupStatus.value === 'matched' && lastOfficialName.value) {
      draft.value.name = lastOfficialName.value
    }
  }
  if (!draft.value.name.trim()) {
    message.warning('基金代码和名称不能为空')
    return
  }

  saving.value = true
  try {
    const payload: HoldingPayload = {
      ...draft.value,
      code,
      name: draft.value.name.trim(),
      amount: toNullableNumber(draft.value.amount, '持仓金额'),
      cost: toNullableNumber(draft.value.cost, '建仓成本'),
      weight_pct: toNullableNumber(draft.value.weight_pct, '持仓占比'),
      theme: draft.value.theme?.trim() || null,
      asset_type: draft.value.asset_type?.trim() || null,
      asset_class: draft.value.asset_class?.trim() || null,
      amount_nav_date: draft.value.amount_nav_date?.trim() || null,
    }

    if (editingId === null) {
      await createHolding(payload)
      message.success('持仓已新增')
    } else {
      await updateHolding(editingId, payload)
      message.success('持仓已更新')
    }
    close()
    emit('saved')
  } catch (caught) {
    message.error(caught instanceof Error ? caught.message : '保存失败')
  } finally {
    saving.value = false
  }
}

watch(
  () => [props.show, props.holding?.id] as const,
  ([show]) => {
    if (show) initializeEditor()
    else resetNameLookup()
  },
  { immediate: true },
)

watch(
  () => draft.value.amount,
  (amount) => {
    if (!props.show || !props.holding) return
    const original = props.holding.amount
    const next = amount === null || amount === '' ? null : Number(amount)
    const originalNumber = original == null ? null : Number(original)
    const changed = originalNumber !== next && !(originalNumber == null && next == null)
    if (changed) draft.value.amount_nav_date = null
  },
)

watch(
  () => [draft.value.code, draft.value.asset_type, props.show] as const,
  ([code]) => {
    if (!props.show || ignoreCodeWatch) return
    const trimmed = String(code ?? '').trim()
    if (isCashDraft()) {
      resetNameLookup()
      nameLookupStatus.value = 'skipped'
      return
    }
    if (!isFundCode(trimmed)) {
      resetNameLookup()
      return
    }
    if (props.holding && trimmed === originalCode.value) {
      resetNameLookup()
      nameLookupStatus.value = 'skipped'
      return
    }
    if (lastLookedUpCode.value === trimmed && nameLookupStatus.value !== 'idle') return
    scheduleNameLookup(trimmed)
  },
)

onBeforeUnmount(resetNameLookup)
</script>

<template>
  <Drawer
    :show="show"
    :title="holding ? '编辑持仓' : '新增持仓'"
    :width="520"
    @update:show="emit('update:show', $event)"
  >
    <form
      class="holding-editor"
      @submit.prevent="save"
    >
      <div class="space-y-1.5">
        <label
          for="holding-code"
          class="text-[12px] text-[var(--text-secondary)]"
        >基金代码 *</label>
        <Input
          id="holding-code"
          v-model="draft.code"
          placeholder="如 005827 或 110011"
          autocomplete="off"
        />
      </div>

      <div class="space-y-1.5">
        <label
          for="holding-name"
          class="text-[12px] text-[var(--text-secondary)]"
        >基金全称 *</label>
        <Input
          id="holding-name"
          v-model="draft.name"
          placeholder="输入 6 位代码后自动核对"
          autocomplete="off"
          aria-describedby="holding-name-lookup"
        />
        <p
          v-if="nameLookupMessage"
          id="holding-name-lookup"
          class="text-[12px]"
          :class="{
            'text-[var(--text-secondary)]': nameLookupStatus === 'matched',
            'text-[var(--text-tertiary)]': nameLookupStatus === 'loading',
            'text-[var(--status-warning)]': nameLookupStatus === 'not_found',
            'text-[var(--status-error)]': nameLookupStatus === 'failed',
          }"
        >
          {{ nameLookupMessage }}
        </p>
      </div>

      <div class="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <div class="space-y-1.5">
          <label
            for="holding-amount"
            class="text-[12px] text-[var(--text-secondary)]"
          >持仓金额 (元)</label>
          <Input
            id="holding-amount"
            v-model="draft.amount"
            type="number"
            placeholder="0.00"
          />
        </div>
        <div class="space-y-1.5">
          <label
            for="holding-cost"
            class="text-[12px] text-[var(--text-secondary)]"
          >建仓成本 (元)</label>
          <Input
            id="holding-cost"
            v-model="draft.cost"
            type="number"
            placeholder="0.00"
          />
        </div>
      </div>

      <div class="space-y-1.5">
        <label
          for="holding-amount-nav-date"
          class="text-[12px] text-[var(--text-secondary)]"
        >金额基准日期</label>
        <Input
          id="holding-amount-nav-date"
          v-model="draft.amount_nav_date"
          type="date"
          placeholder="YYYY-MM-DD"
        />
        <p class="text-[12px] text-[var(--text-tertiary)]">
          持仓金额对应的净值日期。可留空；改金额后需重新确认，不会自动填今天。日期存在也不等于已满足实际收益计算的全部条件。
        </p>
      </div>

      <div class="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <div class="space-y-1.5">
          <label
            for="holding-weight"
            class="text-[12px] text-[var(--text-secondary)]"
          >持仓占比 (%)</label>
          <Input
            id="holding-weight"
            v-model="draft.weight_pct"
            type="number"
            placeholder="0.00"
          />
        </div>
        <div class="space-y-1.5">
          <label
            for="holding-theme"
            class="text-[12px] text-[var(--text-secondary)]"
          >板块 / 分类</label>
          <Input
            id="holding-theme"
            v-model="draft.theme"
            placeholder="如 消费 / 科技 / 港股"
          />
        </div>
      </div>

      <div class="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <div class="space-y-1.5">
          <label
            for="holding-type"
            class="text-[12px] text-[var(--text-secondary)]"
          >资产类别</label>
          <Input
            id="holding-type"
            v-model="draft.asset_type"
            placeholder="fund / cash"
          />
        </div>
        <div class="space-y-1.5">
          <label
            for="holding-class"
            class="text-[12px] text-[var(--text-secondary)]"
          >细分分类</label>
          <Input
            id="holding-class"
            v-model="draft.asset_class"
            placeholder="如 股票型 / 混合型"
          />
        </div>
      </div>
    </form>

    <template #footer>
      <Button
        variant="ghost"
        @click="close"
      >
        取消
      </Button>
      <Button
        variant="primary"
        :loading="saving"
        @click="save"
      >
        保存持仓
      </Button>
    </template>
  </Drawer>
</template>

<style scoped>
.holding-editor {
  display: flex;
  flex-direction: column;
  gap: var(--space-module);
  font-size: 13px;
  line-height: 20px;
}

.holding-editor label {
  display: block;
  margin-bottom: 6px;
  font-size: 13px;
}

.holding-editor p {
  margin-top: 6px;
}

.holding-editor :deep(input),
.ui-press {
  min-width: 44px;
  min-height: 44px;
}

@media (max-width: 768px) {
  .holding-editor > .grid {
    grid-template-columns: minmax(0, 1fr);
    gap: 20px;
  }

  .holding-editor :deep(input) {
    font-size: 15px;
    line-height: 22px;
  }
}
</style>
