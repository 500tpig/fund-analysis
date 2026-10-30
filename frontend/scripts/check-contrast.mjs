#!/usr/bin/env node
/* global process */
/**
 * 浅／深双主题对比度检查。
 *
 * 从 frontend/src/assets/main.css 解析颜色 token，按「用途 → 最小比值」的声明计算
 * WCAG 对比度并打印表格；任一项不达标则退出码为 1。
 * 用法：node scripts/check-contrast.mjs [--json] [--self-test]
 */
import { readFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import assert from 'node:assert/strict'
import postcss from 'postcss'

const here = dirname(fileURLToPath(import.meta.url))
const css = readFileSync(resolve(here, '../src/assets/main.css'), 'utf8')

function readThemes(source) {
  const sheet = postcss.parse(source)
  const root = sheet.nodes.find((node) => node.type === 'rule' && node.selector === ':root')
  const lightMedia = sheet.nodes.find((node) => node.type === 'atrule'
    && node.name === 'media' && node.params === '(prefers-color-scheme: light)')
  const lightRoot = lightMedia?.nodes.find((node) => node.type === 'rule' && node.selector === ':root')
  assert.ok(root && lightRoot, '缺少浅色或深色 :root token 块')
  const declarations = (rule) => Object.fromEntries(rule.nodes
    .filter((node) => node.type === 'decl' && node.prop.startsWith('--'))
    .map((node) => [node.prop.slice(2), node.value]))
  const dark = declarations(root)
  return { dark, light: { ...dark, ...declarations(lightRoot) } }
}

/** 先应用主题覆盖，再解析引用；否则浅色别名会沿用深色值。 */
function resolveToken(name, tokens, visited = []) {
  assert.ok(tokens[name], `缺少 token --${name}`)
  assert.ok(!visited.includes(name), `token 循环引用：${[...visited, name].join(' → ')}`)
  const reference = tokens[name].match(/^var\(--([a-z0-9-]+)\)$/)
  return reference ? resolveToken(reference[1], tokens, [...visited, name]) : tokens[name]
}

/** 解析 #rgb / #rrggbb / #rrggbbaa / rgb() / rgba()。 */
function parseColor(value) {
  if (value.startsWith('#')) {
    const hex = value.slice(1)
    assert.match(hex, /^(?:[\da-f]{3,4}|[\da-f]{6}|[\da-f]{8})$/i, `不支持的色值：${value}`)
    const full = hex.length <= 4 ? [...hex].map((c) => c + c).join('') : hex
    const int = Number.parseInt(full.slice(0, 6), 16)
    return { r: (int >> 16) & 255, g: (int >> 8) & 255, b: int & 255,
      a: full.length === 8 ? Number.parseInt(full.slice(6), 16) / 255 : 1 }
  }
  assert.match(value, /^rgba?\([\d.,\s]+\)$/, `不支持的色值：${value}`)
  const numbers = value.slice(value.indexOf('(') + 1, -1).split(',').map(Number)
  assert.ok((numbers.length === 3 || numbers.length === 4)
    && numbers.slice(0, 3).every((channel) => channel >= 0 && channel <= 255)
    && (numbers[3] === undefined || (numbers[3] >= 0 && numbers[3] <= 1)), `无效色值：${value}`)
  return { r: numbers[0], g: numbers[1], b: numbers[2], a: numbers[3] ?? 1 }
}

function composite(foreground, background) {
  assert.equal(background.a, 1, '透明底色必须提供不透明的实际背景')
  return { r: foreground.r * foreground.a + background.r * (1 - foreground.a),
    g: foreground.g * foreground.a + background.g * (1 - foreground.a),
    b: foreground.b * foreground.a + background.b * (1 - foreground.a), a: 1 }
}

function relativeLuminance({ r, g, b }) {
  const channel = (raw) => {
    const v = raw / 255
    return v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4
  }
  return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)
}

function contrast(foreground, background, underlay) {
  let actualBackground = parseColor(background)
  if (actualBackground.a < 1) {
    assert.ok(underlay, `透明底色 ${background} 缺少实际背景`)
    actualBackground = composite(actualBackground, parseColor(underlay))
  }
  const a = relativeLuminance(composite(parseColor(foreground), actualBackground))
  const b = relativeLuminance(actualBackground)
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
  ['status-live', 'bg-surface', 4.5, '状态文字 / 面板'],
  ['status-warning', 'bg-surface', 4.5, '警告文字 / 面板'],
  ['status-error', 'bg-surface', 4.5, '错误文字 / 面板'],
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
  ['action-primary-text', 'action-primary-hover', 4.5, '主按钮文字 / 悬停底色'],
  ['status-info', 'status-info-bg', 4.5, '信息文字 / 信息底色'],
  ['status-warning', 'status-warning-bg', 4.5, '警告文字 / 警告底色'],
  ['status-error', 'status-error-bg', 4.5, '错误文字 / 错误底色'],
  ['status-error', 'status-error-hover', 4.5, '危险按钮文字 / 悬停底色'],
  ['text-tertiary', 'status-muted-bg', 4.5, '三级文字 / 中性标记'],
  ['text-primary', 'selection-bg', 4.5, '选区文字 / 选区底色'],
]

for (const surface of ['bg-canvas', 'bg-surface', 'bg-elevated', 'bg-surface-hover', 'bg-elevated-hover']) {
  PAIRS.push(['brand-accent', surface, 3, `焦点环 / ${surface}`])
  for (const tone of ['market-up-strong', 'market-down-strong', 'market-up-muted', 'market-down-muted']) {
    // 弱涨跌只用于页面指标与持仓列表，没有浮层调用方。
    if (tone.endsWith('-muted') && surface.startsWith('bg-elevated')) continue
    if (!PAIRS.some(([fg, bg]) => fg === tone && bg === surface)) {
      PAIRS.push([tone, surface, 4.5, `${tone} / ${surface}`])
    }
  }
  for (const tone of ['status-info', 'status-warning', 'status-error']) {
    if (!PAIRS.some(([fg, bg]) => fg === tone && bg === surface)) {
      PAIRS.push([tone, surface, 4.5, `${tone} / ${surface}`])
    }
  }
}

for (const surface of ['bg-canvas', 'bg-surface']) {
  for (const tone of ['chart-line-primary', 'chart-benchmark', 'chart-crosshair']) {
    if (!PAIRS.some(([fg, bg]) => fg === tone && bg === surface)) {
      PAIRS.push([tone, surface, 3, `${tone} / ${surface}`])
    }
    PAIRS.push([tone, 'chart-break-fill', 3, `${tone} / 休市带 (${surface})`, surface])
  }
}

/**
 * 表面色阶必须两两可分辨（下沉 < 画布 < 面板 < 面板悬停 < 浮层 < 浮层悬停）。
 * 这里不是 WCAG 文字对比，只防止相邻两档取成同色；1.04 约等于暗色下肉眼可辨的最小亮度差。
 */
const DARK_LAYERS = [
  ['bg-sunken', 'bg-canvas'],
  ['bg-canvas', 'bg-surface'],
  ['bg-surface', 'bg-surface-hover'],
  ['bg-surface-hover', 'bg-elevated'],
  ['bg-elevated', 'bg-elevated-hover'],
]
const LIGHT_LAYERS = [
  ['bg-canvas', 'bg-surface'],
  ['bg-sidebar', 'bg-canvas'],
  ['bg-sunken', 'bg-canvas'],
  ['bg-surface-hover', 'bg-surface'],
  ['bg-elevated-hover', 'bg-elevated'],
]
const LAYER_MIN = 1.04

function checkTheme(tokens, theme) {
  const color = (name) => resolveToken(name, tokens)
  const rows = PAIRS.map(([fgName, bgName, min, label, underlay]) => {
    const fg = color(fgName)
    const bg = color(bgName)
    const ratio = contrast(fg, bg, underlay ? color(underlay) : undefined)
    const status = ratio < min ? 'FAIL' : ratio < min + 0.5 ? 'THIN' : 'OK'
    return { label, fg: `${fgName} ${fg}`, bg: `${bgName} ${bg}`, value: Number(ratio.toFixed(2)), min, status }
  })
  for (const [lowName, highName] of theme === 'dark' ? DARK_LAYERS : LIGHT_LAYERS) {
    const low = color(lowName)
    const high = color(highName)
    const brighter = relativeLuminance(parseColor(high)) > relativeLuminance(parseColor(low))
    const ratio = contrast(high, low)
    rows.push({ label: `层级 ${lowName} < ${highName}`, fg: `${highName} ${high}`, bg: `${lowName} ${low}`,
      value: Number(ratio.toFixed(2)), min: LAYER_MIN, status: brighter && ratio >= LAYER_MIN ? 'OK' : 'FAIL' })
  }
  return { rows, failed: rows.filter((row) => row.status === 'FAIL').length }
}

const themes = readThemes(css)
if (process.argv.includes('--self-test')) {
  const sample = readThemes('@theme { --a: #f00; } :root { --a: #000; --b: var(--a); }'
    + '@media (prefers-color-scheme: light) { :root { --a: #fff; } } .ignored { --a: #0f0; }')
  assert.equal(resolveToken('b', sample.dark), '#000')
  assert.equal(resolveToken('b', sample.light), '#fff')
  assert.equal(contrast('#000', '#fff'), 21)
  assert.equal(contrast('#0000', '#fff'), 1)
  assert.equal(contrast('#00000000', '#fff'), 1)
  assert.equal(contrast('rgba(0, 0, 0, 0)', '#fff'), 1)
  assert.equal(contrast('#000', 'rgba(255, 255, 255, 0)', '#fff'), 21)
  assert.equal(contrast('rgba(0, 0, 0, 0.5)', '#fff'), contrast('rgb(127.5, 127.5, 127.5)', '#fff'))
  assert.throws(() => contrast('#fff', 'rgba(0, 0, 0, 0.5)'), /缺少实际背景/)
  assert.throws(() => parseColor('not-a-color'), /不支持的色值/)
  assert.throws(() => resolveToken('missing', themes.light), /缺少 token/)
  assert.throws(() => resolveToken('a', { a: 'var(--b)', b: 'var(--a)' }), /循环引用/)
  assert.equal(resolveToken('color-rise', themes.light), themes.light['market-up-strong'])
  const badLight = { ...themes.light, 'text-primary': 'var(--bg-surface)' }
  assert.ok(checkTheme(badLight, 'light').failed > 0, '浅色不达标必须失败')
}

const results = Object.fromEntries(Object.entries(themes).map(([theme, tokens]) => [theme, checkTheme(tokens, theme)]))
const failed = Object.values(results).reduce((sum, result) => sum + result.failed, 0)
if (process.argv.includes('--json')) {
  console.log(JSON.stringify({ themes: results, failed }, null, 2))
} else {
  for (const [theme, { rows }] of Object.entries(results)) {
    console.log(`\n${theme === 'dark' ? '深色' : '浅色'}主题`)
    const width = Math.max(...rows.map((row) => row.label.length))
    for (const row of rows) {
      console.log(`${row.label.padEnd(width)}  ${row.value.toFixed(2).padStart(5)} / ${String(row.min).padStart(4)}  ${row.status}  ${row.fg} on ${row.bg}`)
    }
  }
  console.log(failed === 0 ? '\n全部达标（THIN 表示余量小于 0.5，建议提高）' : `\n${failed} 项未达标`)
}

process.exit(failed === 0 ? 0 : 1)
