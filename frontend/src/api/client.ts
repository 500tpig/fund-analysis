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
  SimulationAccount,
  SimulationAccountsResponse,
  SimulationMetrics,
  SimulationOrderDetail,
  SimulationOrdersResponse,
  SimulationPosition,
  SimulationRunDetail,
  SimulationRunsResponse,
} from './types'

async function requestJson<T>(input: RequestInfo | URL, init?: RequestInit): Promise<T> {
  const response = await fetch(input, init)
  const payload = await response.json().catch(() => null) as unknown
  if (!response.ok) {
    throw new Error(errorDetail(payload) ?? `请求失败（${response.status}）`)
  }
  return payload as T
}

function errorDetail(payload: unknown): string | null {
  if (!payload || typeof payload !== 'object' || !('detail' in payload)) return null
  const detail = (payload as { detail: unknown }).detail
  // 模拟接口返回 {code, message, fields}；其余接口仍是字符串。
  if (detail && typeof detail === 'object' && 'message' in detail) {
    return String((detail as { message: unknown }).message)
  }
  return String(detail)
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

export function fetchSimulationAccounts(): Promise<SimulationAccountsResponse> {
  return requestJson('/api/simulation/accounts')
}

export function createSimulationAccount(idempotencyKey: string): Promise<SimulationAccount> {
  return requestJson('/api/simulation/accounts', {
    method: 'POST',
    headers: { 'Idempotency-Key': idempotencyKey },
  })
}

export function fetchSimulationAccount(id: string): Promise<SimulationAccount> {
  return requestJson(`/api/simulation/accounts/${encodeURIComponent(id)}`)
}

export function fetchSimulationPositions(id: string): Promise<{ items: SimulationPosition[] }> {
  return requestJson(`/api/simulation/accounts/${encodeURIComponent(id)}/positions`)
}

export function fetchSimulationOrders(
  id: string,
  options: { cursor?: string | null; limit?: number; status?: string[] } = {},
): Promise<SimulationOrdersResponse> {
  const params = new URLSearchParams({ limit: String(options.limit ?? 20) })
  if (options.cursor) params.set('cursor', options.cursor)
  if (options.status?.length) params.set('status', options.status.join(','))
  return requestJson(`/api/simulation/accounts/${encodeURIComponent(id)}/orders?${params.toString()}`)
}

export function fetchSimulationOrder(id: string): Promise<SimulationOrderDetail> {
  return requestJson(`/api/simulation/orders/${encodeURIComponent(id)}`)
}

export function fetchSimulationRuns(
  id: string,
  options: { cursor?: string | null; limit?: number } = {},
): Promise<SimulationRunsResponse> {
  const params = new URLSearchParams({ limit: String(options.limit ?? 20) })
  if (options.cursor) params.set('cursor', options.cursor)
  return requestJson(`/api/simulation/accounts/${encodeURIComponent(id)}/runs?${params.toString()}`)
}

export function fetchSimulationRun(runId: string): Promise<SimulationRunDetail> {
  return requestJson(`/api/simulation/runs/${encodeURIComponent(runId)}`)
}

export function fetchSimulationMetrics(id: string): Promise<SimulationMetrics> {
  return requestJson(`/api/simulation/accounts/${encodeURIComponent(id)}/metrics`)
}

/** 暂停/恢复只改本账户状态，不触发下单；服务端按 expected_version 校验并发。 */
export function setSimulationPause(
  id: string,
  action: 'pause' | 'resume',
  expectedVersion: number,
  idempotencyKey: string,
): Promise<SimulationAccount> {
  return requestJson(`/api/simulation/accounts/${encodeURIComponent(id)}/${action}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'Idempotency-Key': idempotencyKey },
    body: JSON.stringify({ expected_version: expectedVersion }),
  })
}
