import type {
  FundsRefreshResponse,
  FundsResponse,
  FundPerformanceResponse,
  FundSearchResponse,
  HoldingPayload,
  HoldingsResponse,
  HoldingsEntryConfirmResponse,
  HoldingsEntryItem,
  HoldingsEntryMode,
  HoldingsEntryPreviewResponse,
  HoldingsEntryRecognizeResponse,
  IntradayCurveResponse,
  OfficialDailyProfitResponse,
  PortfolioRiskResponse,
} from './types'

async function requestJson<T>(input: RequestInfo | URL, init?: RequestInit): Promise<T> {
  const response = await fetch(input, init)
  const payload = await response.json().catch(() => null) as unknown
  if (!response.ok) {
    const detail = payload && typeof payload === 'object' && 'detail' in payload
      ? String((payload as { detail: unknown }).detail)
      : `请求失败（${response.status}）`
    throw new Error(detail)
  }
  return payload as T
}

export function fetchHoldings(): Promise<HoldingsResponse> {
  return requestJson('/api/holdings')
}

export function createHolding(payload: HoldingPayload): Promise<{ id: number }> {
  return requestJson('/api/holdings', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
}

export function updateHolding(id: number, payload: HoldingPayload): Promise<{ id: number }> {
  return requestJson(`/api/holdings/${id}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
}

export function deleteHolding(id: number): Promise<{ id: number; deleted: boolean }> {
  return requestJson(`/api/holdings/${id}`, { method: 'DELETE' })
}

export function fetchFunds(): Promise<FundsResponse> {
  return requestJson('/api/funds')
}

export function refreshFunds(): Promise<FundsRefreshResponse> {
  return requestJson('/api/funds/refresh', { method: 'POST' })
}

export function fetchFundPerformance(
  code: string,
  range: FundPerformanceResponse['range'],
  benchmark: string = '',
): Promise<FundPerformanceResponse> {
  const params = new URLSearchParams({ range })
  if (benchmark) params.set('benchmark', benchmark)
  return requestJson(
    `/api/funds/${encodeURIComponent(code)}/performance?${params.toString()}`,
  )
}

export function fetchFundIntradayCurve(code: string): Promise<IntradayCurveResponse> {
  return requestJson(`/api/funds/${encodeURIComponent(code)}/intraday-curve`)
}

export function fetchFundOfficialProfit(code: string): Promise<OfficialDailyProfitResponse> {
  return requestJson(`/api/funds/${encodeURIComponent(code)}/official-profit`)
}

export function fetchPortfolioRisk(
  range: FundPerformanceResponse['range'] = '1y',
): Promise<PortfolioRiskResponse> {
  const params = new URLSearchParams({ range })
  return requestJson(`/api/funds/portfolio-risk?${params.toString()}`)
}

export function searchFunds(query: string, limit = 8): Promise<FundSearchResponse> {
  const params = new URLSearchParams({ q: query, limit: String(limit) })
  return requestJson(`/api/funds/search?${params.toString()}`)
}

export function recognizeHoldings(
  images: File[],
  signal?: AbortSignal,
): Promise<HoldingsEntryRecognizeResponse> {
  const form = new FormData()
  for (const image of images) form.append('images', image)
  return requestJson('/api/holdings/entry/recognize', { method: 'POST', body: form, signal })
}

export function previewHoldingsEntry(
  mode: HoldingsEntryMode,
  items: HoldingsEntryItem[],
): Promise<HoldingsEntryPreviewResponse> {
  return requestJson('/api/holdings/entry/preview', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ mode, items }),
  })
}

export function confirmHoldingsEntry(payload: {
  mode: HoldingsEntryMode
  items: HoldingsEntryItem[]
  confirm_remove: boolean
  remove_codes: string[]
}): Promise<HoldingsEntryConfirmResponse> {
  return requestJson('/api/holdings/entry/confirm', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
}
