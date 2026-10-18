<script setup lang="ts">
import { Copy } from '@lucide/vue'

import type { HoldingRow } from '@/api/types'
import DataStatus from '@/components/DataStatus.vue'
import Button from '@/components/ui/Button.vue'

defineProps<{
  fund: HoldingRow
}>()

const emit = defineEmits<{
  'copy-code': [code: string]
}>()
</script>

<template>
  <div class="min-w-0">
    <div class="flex items-start gap-3">
      <h2
        class="min-w-0 truncate text-[20px] font-medium leading-7"
        :title="fund.name"
      >
        {{ fund.name }}
      </h2>
      <Button
        v-if="!fund.is_cash_asset"
        variant="ghost"
        size="icon"
        aria-label="复制基金代码"
        @click="emit('copy-code', fund.code)"
      >
        <Copy
          class="h-3.5 w-3.5"
          :stroke-width="1.75"
        />
      </Button>
    </div>
    <DataStatus
      class="mt-1"
      :row="fund"
    />
  </div>
</template>
