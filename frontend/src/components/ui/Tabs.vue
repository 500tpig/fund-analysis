<script setup lang="ts">
interface TabOption {
  key: string
  label: string
  count?: number
}

interface Props {
  modelValue: string
  options: TabOption[]
  size?: 'sm' | 'md'
}

withDefaults(defineProps<Props>(), {
  size: 'md',
})

const emit = defineEmits<{
  'update:modelValue': [val: string]
  change: [val: string]
}>()

function selectTab(key: string) {
  emit('update:modelValue', key)
  emit('change', key)
}
</script>

<template>
  <div
    class="segmented-control"
    role="tablist"
  >
    <button
      v-for="item in options"
      :key="item.key"
      type="button"
      role="tab"
      :aria-selected="modelValue === item.key ? 'true' : 'false'"
      :class="[
        'segmented-item',
        size === 'sm' ? 'h-8 px-2.5 text-[12px]' : '',
        modelValue === item.key ? 'segmented-item--active' : '',
      ]"
      @click="selectTab(item.key)"
    >
      <span>{{ item.label }}</span>
      <span
        v-if="item.count !== undefined"
        class="ml-1 font-numeric text-[11px] opacity-70"
      >
        {{ item.count }}
      </span>
    </button>
  </div>
</template>
