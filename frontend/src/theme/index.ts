/**
 * FundPilot visual tokens — Quiet Financial Desk.
 *
 * Warm gold is brand / focus / fund curve only.
 * A-share rise is red, fall is green. System status never reuses fall green.
 */

export const chartColors: Record<'fund' | 'benchmark' | 'rise' | 'fall', string> = {
  fund: 'var(--chart-line-primary)',
  benchmark: 'var(--chart-benchmark)',
  rise: 'var(--market-up-strong)',
  fall: 'var(--market-down-strong)',
}

export const themeTokens = {
  colors: {
    canvas: 'var(--bg-canvas)',
    surface: 'var(--bg-surface)',
    accent: 'var(--brand-accent)',
    rise: 'var(--market-up-strong)',
    fall: 'var(--market-down-strong)',
  },
}
