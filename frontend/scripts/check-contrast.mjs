#!/usr/bin/env node
/* global process */
/**
 * 深色主题对比度检查。
 *
 * 从 frontend/src/assets/main.css 解析颜色 token，按「用途 → 最小比值」的声明计算
 * WCAG 对比度并打印表格；任一项不达标则退出码为 1。
 * 用法：node scripts/check-contrast.mjs [--json]
 */
import { readFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const css = readFileSync(resolve(here, '../src/assets/main.css'), 'utf8')

/** 收集 `--token: #hex|rgba()`，再解析一层到多层的 `var(--token)` 引用。 */
function readTokens(source) {
  const tokens = {}
  const literal = /--([a-z0-9-]+):\s*(#[0-9a-fA-F]{3,8}|rgba?\([^)]*\))\s*;/g
  const reference = /--([a-z0-9-]+):\s*var\(--([a-z0-9-]+)\)\s*;/g
  for (const [, name, value] of source.matchAll(literal)) {
    if (tokens[name] === undefined) tokens[name] = value
  }
  for (let pass = 0; pass < 4; pass += 1) {
    let changed = false
    for (const [, name, target] of source.matchAll(reference)) {
      if (tokens[name] === undefined && tokens[target] !== undefined) {
        tokens[name] = tokens[target]
        changed = true
      }
    }
    if (!changed) break
  }
  return tokens
}

/** 解析 #rgb / #rrggbb / #rrggbbaa / rgb() / rgba()。 */
function parseColor(value) {
  if (value.startsWith('#')) {
    const hex = value.slice(1)
    const full = hex.length <= 4 ? [...hex].map((c) => c + c).join('') : hex
    const int = Number.parseInt(full.slice(0, 6), 16)
    return { r: (int >> 16) & 255, g: (int >> 8) & 255, b: int & 255 }
  }
  const numbers = value.match(/[\d.]+/g)?.map(Number) ?? []
  return { r: numbers[0] ?? 0, g: numbers[1] ?? 0, b: numbers[2] ?? 0 }
}

function relativeLuminance({ r, g, b }) {
  const channel = (raw) => {
    const v = raw / 255
    return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4
  }
  return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)
}

function contrast(foreground, background) {
  const a = relativeLuminance(parseColor(foreground))
  const b = relativeLuminance(parseColor(background))
  const [high, low] = a > b ? [a, b] : [b, a]
  return (high + 0.05) / (low + 0.05)
}

/** [前景, 背景, 最小比值, 说明]；正文与数据 4.5，非文本图形 3。 */
const PAIRS = [
  ['text-primary', 'bg-surface', 4.5, '主文字 / 面板'],
  ['text-primary', 'bg-canvas', 4.5, '主文字 / 画布'],
  ['text-primary', 'bg-elevated', 4.5, '主文字 / 浮层'],
  ['text-secondary', 'bg-surface', 4.5, '次要文字 / 面板'],
  ['text-secondary', 'bg-canvas', 4.5, '次要文字 / 画布'],
  ['text-secondary', 'bg-elevated', 4.5, '次要文字 / 浮层'],
  ['text-tertiary', 'bg-surface', 4.5, '三级文字 / 面板'],
  ['text-tertiary', 'bg-canvas', 4.5, '三级文字 / 画布'],
  ['market-up-strong', 'bg-surface', 4.5, '涨 / 面板'],
  ['market-down-strong', 'bg-surface', 4.5, '跌 / 面板'],
  ['brand-accent', 'bg-surface', 4.5, '品牌金 / 面板'],
  ['status-live', 'bg-surface', 3, '状态色 / 面板'],
  ['status-warning', 'bg-surface', 3, '警告色 / 面板'],
  ['status-error', 'bg-surface', 3, '错误色 / 面板'],
  ['chart-line-primary', 'bg-surface', 3, '曲线主色 / 面板'],
  ['action-primary-text', 'action-primary-bg', 4.5, '主按钮文字 / 底色'],
  ['text-primary', 'bg-surface-hover', 4.5, '主文字 / 悬停面'],
  ['text-secondary', 'bg-surface-hover', 4.5, '次要文字 / 悬停面'],
  ['text-tertiary', 'bg-elevated', 4.5, '三级文字 / 浮层'],
  ['text-tertiary', 'bg-surface-hover', 4.5, '三级文字 / 悬停面'],
  ['text-primary', 'bg-elevated-hover', 4.5, '主文字 / 浮层悬停'],
  ['text-secondary', 'bg-elevated-hover', 4.5, '次要文字 / 浮层悬停'],
  ['text-tertiary', 'bg-elevated-hover', 4.5, '三级文字 / 浮层悬停'],
  ['market-up-strong', 'bg-surface-hover', 4.5, '涨 / 悬停面'],
  ['market-down-strong', 'bg-surface-hover', 4.5, '跌 / 悬停面'],
]

/**
 * 表面色阶必须两两可分辨（下沉 < 画布 < 面板 < 面板悬停 < 浮层 < 浮层悬停）。
 * 这里不是 WCAG 文字对比，只防止相邻两档取成同色；1.04 约等于暗色下肉眼可辨的最小亮度差。
 */
const LAYERS = [
  ['bg-sunken', 'bg-canvas'],
  ['bg-canvas', 'bg-surface'],
  ['bg-surface', 'bg-surface-hover'],
  ['bg-surface-hover', 'bg-elevated'],
  ['bg-elevated', 'bg-elevated-hover'],
]
const LAYER_MIN = 1.04

const tokens = readTokens(css)
const rows = []
let failed = 0

for (const [fgName, bgName, min, label] of PAIRS) {
  const fg = tokens[fgName]
  const bg = tokens[bgName]
  if (!fg || !bg) {
    rows.push({ label, fg: fgName, bg: bgName, value: null, min, status: 'MISSING' })
    failed += 1
    continue
  }
  const ratio = contrast(fg, bg)
  const status = ratio < min ? 'FAIL' : ratio < min + 0.5 ? 'THIN' : 'OK'
  if (status === 'FAIL') failed += 1
  rows.push({ label, fg: `${fgName} ${fg}`, bg: `${bgName} ${bg}`, value: Number(ratio.toFixed(2)), min, status })
}

for (const [lowName, highName] of LAYERS) {
  const label = `层级 ${lowName} < ${highName}`
  const low = tokens[lowName]
  const high = tokens[highName]
  if (!low || !high) {
    rows.push({ label, fg: highName, bg: lowName, value: null, min: LAYER_MIN, status: 'MISSING' })
    failed += 1
    continue
  }
  const brighter = relativeLuminance(parseColor(high)) > relativeLuminance(parseColor(low))
  const ratio = contrast(high, low)
  const status = !brighter || ratio < LAYER_MIN ? 'FAIL' : 'OK'
  if (status === 'FAIL') failed += 1
  rows.push({ label, fg: `${highName} ${high}`, bg: `${lowName} ${low}`, value: Number(ratio.toFixed(2)), min: LAYER_MIN, status })
}

if (process.argv.includes('--json')) {
  console.log(JSON.stringify({ rows, failed }, null, 2))
} else {
  const width = Math.max(...rows.map((row) => row.label.length))
  for (const row of rows) {
    const value = row.value === null ? '—' : row.value.toFixed(2)
    console.log(`${row.label.padEnd(width)}  ${value.padStart(5)} / ${String(row.min).padStart(4)}  ${row.status}  ${row.fg} on ${row.bg}`)
  }
  console.log(failed === 0 ? '\n全部达标（THIN 表示余量小于 0.5，建议提高）' : `\n${failed} 项未达标`)
}

process.exit(failed === 0 ? 0 : 1)
