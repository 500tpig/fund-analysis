<script setup lang="ts">
import type { HoldingRow } from '@/api/types'
import Drawer from '@/components/ui/Drawer.vue'
import FundDetailPanel from '@/components/FundDetailPanel.vue'
import FundIdentityHeader from '@/components/FundIdentityHeader.vue'

interface Props {
  show: boolean
  fund: HoldingRow | null
}

const props = withDefaults(defineProps<Props>(), {
  show: false,
  fund: null,
})

const emit = defineEmits<{
  'update:show': [value: boolean]
  close: []
  'copy-code': [code: string]
}>()
</script>

<template>
  <Drawer
    :show="show"
    :title="props.fund ? props.fund.name : '基金明细'"
    width="clamp(620px, 42vw, 760px)"
    @update:show="emit('update:show', $event)"
    @close="emit('close')"
  >
    <template
      v-if="fund"
      #header
    >
      <div class="min-w-0 pr-4">
        <FundIdentityHeader
          :fund="fund"
          @copy-code="emit('copy-code', $event)"
        />
      </div>
    </template>

    <FundDetailPanel
      v-if="fund"
      :fund="fund"
    />
  </Drawer>
</template>
