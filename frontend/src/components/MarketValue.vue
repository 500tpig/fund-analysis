<script setup lang="ts">
import type { ValueState } from '@/api/types'
import { formatMoney, formatNav, formatPercent, signedMoney, valueStateClass } from '@/shared/formatters'

const props = withDefaults(
  defineProps<{
    value: number | null | undefined
    kind?: 'money' | 'signed-money' | 'percent' | 'nav' | 'plain'
    state?: ValueState | null
    fallback?: string
    signed?: boolean
    toneLevel?: 'strong' | 'muted'
  }>(),
  {
    kind: 'money',
    state: null,
    fallback: '—',
    signed: true,
    toneLevel: 'strong',
  },
)

function display(): string {
  if (props.kind === 'percent') return formatPercent(props.value, { signed: props.signed, fallback: props.fallback })
  if (props.kind === 'signed-money') return signedMoney(props.value, props.fallback)
  if (props.kind === 'nav') return formatNav(props.value, props.fallback)
  if (props.kind === 'plain') return props.value == null ? props.fallback : String(props.value)
  return formatMoney(props.value, props.fallback)
}

function tone(): string {
  if (props.value == null) return 'value-muted'
  if (props.state) return valueStateClass(props.state, props.toneLevel)
  return ''
}
</script>

<template>
  <span
    class="font-numeric tabular-nums"
    :class="tone()"
  >{{ display() }}</span>
</template>
