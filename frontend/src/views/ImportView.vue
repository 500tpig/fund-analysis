<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import {
  AlertCircle,
  AlertTriangle,
  Camera,
  CheckCircle2,
  ClipboardPaste,
  Images,
  ScanLine,
  Trash2,
} from '@lucide/vue'

import {
  confirmHoldingsEntry,
  previewHoldingsEntry,
  recognizeHoldings,
} from '@/api/client'
import type {
  HoldingsEntryAction,
  HoldingsEntryCandidate,
  HoldingsEntryConfirmResponse,
  HoldingsEntryItem,
  HoldingsEntryMode,
  HoldingsEntryPreviewResponse,
} from '@/api/types'
import WorkbenchPageHeader from '@/components/WorkbenchPageHeader.vue'
import Button from '@/components/ui/Button.vue'
import Input from '@/components/ui/Input.vue'

type Phase = 'idle' | 'recognizing' | 'recognize_failed' | 'candidates' | 'previewing' | 'preview' | 'writing' | 'done'

const router = useRouter()
const albumInput = ref<HTMLInputElement | null>(null)
const cameraInput = ref<HTMLInputElement | null>(null)
const phase = ref<Phase>('idle')
const selectedImages = ref<File[]>([])
const candidates = ref<HoldingsEntryCandidate[]>([])
const warnings = ref<string[]>([])
const error = ref<string | null>(null)
const mode = ref<HoldingsEntryMode>('merge')
const preview = ref<HoldingsEntryPreviewResponse | null>(null)
const result = ref<HoldingsEntryConfirmResponse | null>(null)
const replaceConfirmed = ref(false)
let recognizeController: AbortController | null = null
const originalCodes = ref<Array<string | null>>([])

const blockingIssues = new Set([
  'code_missing',
  'name_missing',
  'amount_missing',
  'amount_conflict',
  'share_class_mismatch',
  'share_class_ambiguous',
  'skipped_non_fund',
  'profit_conflict',
])

const issueLabels: Record<string, string> = {
  profit_conflict: '多图收益不一致，请核对收益字段',
  code_missing: '缺少基金代码',
  name_missing: '缺少基金名称',
  amount_missing: '缺少持仓金额',
  amount_conflict: '多图金额不一致',
  shares_unclear: '份额不清晰，已留空',
  shares_conflict: '多图份额不一致，已留空',
  name_mismatch: '已采用核验后的正式名称',
  share_class_mismatch: '基金类别与代码不一致',
  share_class_ambiguous: '需要选择 A/C 类别',
  unverified: '基金信息暂未核验',
  not_found: '未检索到该基金代码',
  skipped_non_fund: '不是可确认的 6 位基金代码',
}

const step = computed(() => {
  if (phase.value === 'done') return 4
  if (phase.value === 'preview' || phase.value === 'previewing' || phase.value === 'writing') return 3
  if (phase.value === 'candidates') return 2
  return 1
})

const canPreview = computed(() => candidates.value.length > 0 && candidates.value.every((candidate) => {
  const hasRequired = /^\d{6}$/.test(candidate.code ?? '')
    && Boolean(candidate.name?.trim())
    && isNonNegativeDecimal(candidate.amount)
  return hasRequired && !candidate.issues.some((issue) => blockingIssues.has(issue))
}))

const removeRows = computed(() => preview.value?.rows.filter((row) => row.action === 'remove') ?? [])
const canConfirm = computed(() => {
  if (!preview.value || phase.value === 'writing') return false
  return mode.value !== 'replace' || preview.value.remove_count === 0 || replaceConfirmed.value
})

function actionLabel(action: HoldingsEntryAction): string {
  if (action === 'add') return '新增'
  if (action === 'update') return '更新'
  if (action === 'keep') return '保留'
  return '删除'
}

function isNonNegativeDecimal(value: string | null): boolean {
  if (!value?.trim()) return false
  return /^\d+(?:\.\d+)?$/.test(value.trim())
}

function clearIssues(candidate: HoldingsEntryCandidate, ...issues: string[]) {
  candidate.issues = candidate.issues.filter((issue) => !issues.includes(issue))
}

function updateCode(candidate: HoldingsEntryCandidate, value: string, index: number) {
  candidate.code = value.replace(/\D/g, '').slice(0, 6)
  if (/^\d{6}$/.test(candidate.code)) clearIssues(candidate, 'code_missing')
  if (/^\d{6}$/.test(candidate.code) && candidate.code !== originalCodes.value[index]) {
    clearIssues(candidate, 'share_class_mismatch', 'share_class_ambiguous', 'skipped_non_fund')
  }
  candidate.verification = 'unverified'
  candidate.confidence = 'low'
}

function updateName(candidate: HoldingsEntryCandidate, value: string) {
  candidate.name = value
  if (value.trim()) clearIssues(candidate, 'name_missing')
}

function updateAmount(candidate: HoldingsEntryCandidate, value: string) {
  candidate.amount = value
  if (isNonNegativeDecimal(value)) clearIssues(candidate, 'amount_missing', 'amount_conflict')
}

function updateShares(candidate: HoldingsEntryCandidate, value: string) {
  candidate.shares = value || null
  clearIssues(candidate, 'shares_unclear', 'shares_conflict')
}

function chooseNameMatch(candidate: HoldingsEntryCandidate, code: string, name: string) {
  candidate.code = code
  candidate.name = name
  candidate.share_class = name.match(/([ABCEH])$/i)?.[1]?.toUpperCase() ?? null
  candidate.verification = 'matched'
  clearIssues(candidate, 'code_missing', 'share_class_ambiguous', 'share_class_mismatch', 'skipped_non_fund')
}

function removeCandidate(index: number) {
  candidates.value.splice(index, 1)
  originalCodes.value.splice(index, 1)
}

function entryItems(): HoldingsEntryItem[] {
  return candidates.value.map((candidate) => {
    const item: HoldingsEntryItem = {
      code: candidate.code?.trim() ?? '',
      name: candidate.name?.trim() ?? '',
      amount: candidate.amount?.trim() ?? '',
    }
    for (const field of ['shares', 'cost', 'holding_profit_rate', 'daily_profit', 'cumulative_profit', 'imported_holding_profit', 'profit_snapshot_date', 'profit_source', 'daily_profit_date'] as const) {
      const value = candidate[field]?.trim()
      if (value) item[field] = value
    }
    return item
  })
}

async function startRecognition(files: File[]) {
  if (files.length === 0) {
    error.value = '请选择 JPG、PNG 或 WebP 截图'
    phase.value = 'recognize_failed'
    return
  }
  if (files.length > 5) {
    error.value = '一次最多识别 5 张截图'
    phase.value = 'recognize_failed'
    return
  }
  const images = files
  selectedImages.value = images
  candidates.value = []
  warnings.value = []
  preview.value = null
  result.value = null
  error.value = null
  phase.value = 'recognizing'
  recognizeController = new AbortController()
  try {
    const response = await recognizeHoldings(images, recognizeController.signal)
    const drafts = response.candidates.map((candidate) => ({
      ...candidate,
      issues: [...candidate.issues],
      name_matches: [...candidate.name_matches],
    }))
    originalCodes.value = drafts.map((draft) => draft.code)
    candidates.value = drafts
    warnings.value = response.warnings
    phase.value = 'candidates'
  } catch (caught) {
    if (caught instanceof DOMException && caught.name === 'AbortError') return
    error.value = caught instanceof Error ? caught.message : '持仓识别失败'
    phase.value = 'recognize_failed'
  } finally {
    recognizeController = null
  }
}

function onAlbumSelect(event: Event) {
  const input = event.target as HTMLInputElement
  const files = Array.from(input.files ?? [])
  if (files.length) void startRecognition(files)
  input.value = ''
}

function onCameraSelect(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  if (file) void startRecognition([file])
  input.value = ''
}

function onPaste(event: ClipboardEvent) {
  if (phase.value === 'recognizing' || phase.value === 'writing') return
  const files = Array.from(event.clipboardData?.files ?? []).filter((file) => file.type.startsWith('image/'))
  if (files.length) {
    event.preventDefault()
    void startRecognition(files)
  }
}

function cancelRecognition() {
  recognizeController?.abort()
  reset()
}

function reset() {
  phase.value = 'idle'
  selectedImages.value = []
  candidates.value = []
  originalCodes.value = []
  warnings.value = []
  error.value = null
  preview.value = null
  result.value = null
  mode.value = 'merge'
  replaceConfirmed.value = false
}

async function requestPreview(nextMode: HoldingsEntryMode = mode.value) {
  if (!canPreview.value) {
    error.value = '请先处理候选项中的必填字段和类别问题'
    return
  }
  mode.value = nextMode
  error.value = null
  preview.value = null
  replaceConfirmed.value = false
  phase.value = 'previewing'
  try {
    preview.value = await previewHoldingsEntry(nextMode, entryItems())
    phase.value = 'preview'
  } catch (caught) {
    error.value = caught instanceof Error ? caught.message : '无法预览持仓变更'
    phase.value = 'candidates'
  }
}

async function changeMode(nextMode: HoldingsEntryMode) {
  if (nextMode === mode.value || phase.value === 'previewing') return
  await requestPreview(nextMode)
}

async function confirmEntry() {
  if (!preview.value || !canConfirm.value) return
  error.value = null
  phase.value = 'writing'
  try {
    result.value = await confirmHoldingsEntry({
      mode: mode.value,
      items: entryItems(),
      confirm_remove: mode.value === 'replace' && replaceConfirmed.value,
      remove_codes: removeRows.value.map((row) => row.code),
    })
    phase.value = 'done'
  } catch (caught) {
    error.value = caught instanceof Error ? caught.message : '持仓写入失败'
    phase.value = 'preview'
  }
}

onMounted(() => window.addEventListener('paste', onPaste))
onUnmounted(() => {
  window.removeEventListener('paste', onPaste)
  recognizeController?.abort()
})
</script>

<template>
  <div class="workbench-container flex flex-col gap-5">
    <WorkbenchPageHeader
      title="持仓录入"
      description="从持仓截图提取候选，人工核对后再批量写入。"
    />

    <nav
      class="import-stepper shrink-0"
      aria-label="录入步骤"
    >
      <template
        v-for="(label, index) in ['选择截图', '核对候选', '确认变更', '完成']"
        :key="label"
      >
        <span
          v-if="index > 0"
          class="import-stepper__rule"
        />
        <div :class="['import-step', step === index + 1 ? 'is-active' : step > index + 1 ? 'is-done' : '']">
          <span class="import-step__index">{{ index + 1 }}</span>{{ label }}
        </div>
      </template>
    </nav>

    <div
      v-if="error"
      class="alert-banner alert-banner--error"
      role="alert"
    >
      <AlertCircle
        class="h-4 w-4 shrink-0"
        :stroke-width="1.75"
      />
      <span>{{ error }}</span>
    </div>

    <section
      v-if="phase === 'idle' || phase === 'recognizing' || phase === 'recognize_failed'"
      class="entry-source-panel"
    >
      <div
        class="drop-stage"
        @dragover.prevent
        @drop.prevent="startRecognition(Array.from($event.dataTransfer?.files ?? []))"
      >
        <ScanLine
          class="h-7 w-7 text-[var(--text-tertiary)]"
          :stroke-width="1.5"
        />
        <div class="drop-stage__title">
          选择持仓截图
        </div>
        <p class="max-w-md text-[13px] leading-5 text-[var(--text-tertiary)]">
          最多 5 张 JPG、PNG 或 WebP。桌面端也可拖入或粘贴图片。
        </p>
        <div class="entry-source-actions">
          <Button
            variant="primary"
            :disabled="phase === 'recognizing'"
            @click="albumInput?.click()"
          >
            <template #icon>
              <Images
                class="h-4 w-4"
                :stroke-width="1.75"
              />
            </template>从相册选择
          </Button>
          <Button
            variant="outline"
            :disabled="phase === 'recognizing'"
            @click="cameraInput?.click()"
          >
            <template #icon>
              <Camera
                class="h-4 w-4"
                :stroke-width="1.75"
              />
            </template>拍照
          </Button>
        </div>
        <div class="entry-paste-hint">
          <ClipboardPaste
            class="h-4 w-4"
            :stroke-width="1.75"
          />可直接粘贴剪贴板图片
        </div>
        <input
          ref="albumInput"
          class="hidden"
          type="file"
          accept="image/jpeg,image/png,image/webp"
          multiple
          @change="onAlbumSelect"
        >
        <input
          ref="cameraInput"
          class="hidden"
          type="file"
          accept="image/jpeg,image/png,image/webp"
          capture="environment"
          @change="onCameraSelect"
        >
      </div>
      <p class="entry-privacy-copy">
        识别会把当次截图发送到已配置的识图服务，图片不会保存。
      </p>
      <div
        v-if="phase === 'recognizing'"
        class="entry-progress"
        aria-live="polite"
      >
        <span>正在识别 {{ selectedImages.length }} 张截图</span>
        <Button
          variant="ghost"
          size="sm"
          @click="cancelRecognition"
        >
          取消
        </Button>
      </div>
    </section>

    <section
      v-else-if="phase === 'candidates'"
      class="space-y-5"
    >
      <div class="entry-section-heading">
        <div><h2>核对候选持仓</h2><p>{{ candidates.length }} 条候选 · 低可信字段需要人工核对</p></div>
        <Button
          variant="ghost"
          size="sm"
          @click="reset"
        >
          重新选择
        </Button>
      </div>

      <div
        v-for="warning in warnings"
        :key="warning"
        class="alert-banner alert-banner--warn"
      >
        <AlertTriangle
          class="h-4 w-4 shrink-0"
          :stroke-width="1.75"
        /><span>{{ warning }}</span>
      </div>

      <div class="entry-candidate-list">
        <article
          v-for="(candidate, index) in candidates"
          :key="`${candidate.source_image_index}-${index}`"
          class="entry-candidate-card"
        >
          <header>
            <div class="flex min-w-0 items-center gap-2">
              <span class="entry-candidate-index">{{ index + 1 }}</span>
              <strong class="truncate">{{ candidate.name || candidate.ocr_name || '未命名候选' }}</strong>
              <span
                v-if="candidate.confidence === 'low' || candidate.issues.length"
                class="entry-review-badge"
              >需核对</span>
            </div>
            <Button
              variant="ghost"
              size="icon-sm"
              :aria-label="`删除候选 ${index + 1}`"
              @click="removeCandidate(index)"
            >
              <Trash2
                class="h-4 w-4"
                :stroke-width="1.75"
              />
            </Button>
          </header>

          <div
            v-if="candidate.issues.length"
            class="entry-issue-list"
          >
            <span
              v-for="issue in candidate.issues"
              :key="issue"
            >{{ issueLabels[issue] ?? issue }}</span>
          </div>

          <div
            v-if="candidate.name_matches.length"
            class="entry-name-matches"
          >
            <button
              v-for="match in candidate.name_matches"
              :key="match.code"
              type="button"
              @click="chooseNameMatch(candidate, match.code, match.name)"
            >
              <span>{{ match.name }}</span><code>{{ match.code }}</code>
            </button>
          </div>

          <div class="entry-field-grid">
            <label><span>基金代码</span><Input
              :model-value="candidate.code"
              inputmode="numeric"
              maxlength="6"
              placeholder="6 位代码"
              @update:model-value="updateCode(candidate, $event, index)"
            /></label>
            <label class="entry-field--wide"><span>基金名称</span><Input
              :model-value="candidate.name"
              placeholder="基金名称"
              @update:model-value="updateName(candidate, $event)"
            /></label>
            <label><span>持仓金额</span><Input
              :model-value="candidate.amount"
              inputmode="decimal"
              placeholder="0.00"
              @update:model-value="updateAmount(candidate, $event)"
            /></label>
            <label><span>持有份额（选填）</span><Input
              :model-value="candidate.shares"
              inputmode="decimal"
              placeholder="留空不反推"
              @update:model-value="updateShares(candidate, $event)"
            /></label>
            <label><span>持仓成本（选填）</span><Input
              v-model="candidate.cost"
              inputmode="decimal"
              placeholder="0.00"
            /></label>
          </div>

          <details class="entry-optional-fields">
            <summary>其他识别字段</summary>
            <div class="entry-field-grid mt-3">
              <label><span>截图持有收益</span><Input
                v-model="candidate.imported_holding_profit"
                inputmode="decimal"
              /></label>
              <label><span>截图日期</span><Input
                v-model="candidate.profit_snapshot_date"
                type="date"
              /></label>
              <label><span>收益来源</span><Input
                v-model="candidate.profit_source"
                maxlength="80"
              /></label>
              <label><span>日收益日期</span><Input
                v-model="candidate.daily_profit_date"
                type="date"
              /></label>
              <label><span>持有收益率</span><Input
                v-model="candidate.holding_profit_rate"
                inputmode="decimal"
              /></label>
              <label><span>当日收益</span><Input
                v-model="candidate.daily_profit"
                inputmode="decimal"
              /></label>
              <label><span>累计收益</span><Input
                v-model="candidate.cumulative_profit"
                inputmode="decimal"
              /></label>
              <Button
                v-if="candidate.issues.includes('profit_conflict')"
                variant="secondary"
                @click="candidate.issues = candidate.issues.filter((issue) => issue !== 'profit_conflict')"
              >
                已核对收益字段
              </Button>
            </div>
          </details>
        </article>
      </div>

      <div class="entry-sticky-actions">
        <span
          v-if="!canPreview"
          class="text-[12px] text-[var(--status-warning)]"
        >处理必填字段和类别问题后可继续</span>
        <Button
          variant="primary"
          :disabled="!canPreview"
          @click="requestPreview()"
        >
          预览变更
        </Button>
      </div>
    </section>

    <section
      v-else-if="phase === 'preview' || phase === 'previewing' || phase === 'writing'"
      class="space-y-5"
    >
      <div class="entry-section-heading">
        <div><h2>确认持仓变更</h2><p>默认合并更新，不删除未出现在截图中的持仓。</p></div>
        <Button
          variant="ghost"
          size="sm"
          :disabled="phase === 'writing'"
          @click="phase = 'candidates'"
        >
          返回修正
        </Button>
      </div>

      <div
        class="entry-mode-switch"
        role="group"
        aria-label="写入方式"
      >
        <button
          type="button"
          :class="mode === 'merge' ? 'is-active' : ''"
          :disabled="phase !== 'preview'"
          @click="changeMode('merge')"
        >
          合并更新
        </button>
        <button
          type="button"
          :class="mode === 'replace' ? 'is-active' : ''"
          :disabled="phase !== 'preview'"
          @click="changeMode('replace')"
        >
          替换全部
        </button>
      </div>

      <div
        v-if="phase === 'previewing'"
        class="entry-progress"
      >
        正在计算变更…
      </div>
      <template v-else-if="preview">
        <div class="entry-counts">
          <span>新增 <strong>{{ preview.add_count }}</strong></span><span>更新 <strong>{{ preview.update_count }}</strong></span><span>保留 <strong>{{ preview.keep_count }}</strong></span><span v-if="mode === 'replace'">删除 <strong>{{ preview.remove_count }}</strong></span>
        </div>

        <div class="entry-preview-list">
          <article
            v-for="row in preview.rows.filter((item) => item.action !== 'remove')"
            :key="`${row.action}-${row.code}`"
          >
            <div>
              <strong>{{ row.name }}</strong><code>{{ row.code }}</code>
              <div
                v-if="row.imported_holding_profit != null"
                class="text-xs text-(--text-secondary)"
              >
                {{ row.profit_source }} · {{ row.profit_snapshot_date }} · 持有收益 {{ row.imported_holding_profit }}
              </div>
              <div
                v-if="row.daily_profit_date"
                class="text-xs text-(--text-secondary)"
              >
                {{ row.daily_profit_date }} 收益 {{ row.daily_profit }}
              </div>
            </div><span class="font-numeric">¥{{ row.amount }}</span><span :class="`entry-action entry-action--${row.action}`">{{ actionLabel(row.action) }}</span>
          </article>
        </div>

        <div
          v-if="mode === 'replace' && removeRows.length"
          class="entry-remove-panel"
        >
          <div class="flex items-center gap-2 font-medium">
            <AlertTriangle
              class="h-4 w-4"
              :stroke-width="1.75"
            />待删除 {{ removeRows.length }} 只持仓
          </div>
          <ul>
            <li
              v-for="row in removeRows"
              :key="row.code"
            >
              <span><strong>{{ row.name }}</strong><code>{{ row.code }}</code></span><span class="font-numeric">¥{{ row.amount }}</span>
            </li>
          </ul>
          <label class="entry-remove-confirm"><input
            v-model="replaceConfirmed"
            type="checkbox"
          ><span>确认删除以上持仓，并用当前候选替换全部持仓</span></label>
        </div>

        <div class="entry-sticky-actions">
          <Button
            variant="primary"
            :disabled="!canConfirm"
            :loading="phase === 'writing'"
            @click="confirmEntry"
          >
            {{ mode === 'replace' && preview.remove_count > 0 ? `确认删除 ${preview.remove_count} 只并替换持仓` : '确认合并' }}
          </Button>
        </div>
      </template>
    </section>

    <section
      v-else-if="phase === 'done' && result"
      class="entry-done-panel"
    >
      <CheckCircle2
        class="h-8 w-8 text-[var(--status-info)]"
        :stroke-width="1.5"
      />
      <div>
        <h2>{{ result.message }}</h2><p>{{ result.refresh.message }}</p><p
          v-if="result.refresh.has_failure"
          class="status-warning"
        >
          持仓已保存，部分估值刷新失败。
        </p>
      </div>
      <div class="flex flex-wrap gap-3">
        <Button
          variant="primary"
          @click="router.push({ name: 'valuation' })"
        >
          查看估值
        </Button><Button
          variant="outline"
          @click="reset"
        >
          继续录入
        </Button>
      </div>
    </section>
  </div>
</template>

<style scoped>
/* 桌面端全局 .workbench-container 是定高 + overflow:hidden 的容器（见 main.css ≥769px 规则），
   会覆盖根节点原来的 overflow-auto，把较长的候选或预览列表裁掉且无法滚动。
   录入页没有内部滚动区域，这里让容器自身纵向滚动。 */
.workbench-container {
  overflow-y: auto;
  overflow-x: hidden;
  overscroll-behavior: contain;
}

@media (min-width: 769px) {
  /* 底部操作栏吸附在滚动容器底部，长列表滚动时保持可见。 */
  .entry-sticky-actions {
    position: sticky;
    bottom: 0;
    z-index: 20;
    padding-bottom: 12px;
    background: var(--bg-canvas);
  }
}
</style>
