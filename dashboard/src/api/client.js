/**
 * api/client.js
 * All calls to the FraudShield FastAPI backend.
 * Base URL proxied through Vite → /api → localhost:8000
 */

const BASE  = '/api'
const TOKEN = import.meta.env.VITE_API_TOKEN
if (!TOKEN) {
  throw new Error(
    '[FraudShield] VITE_API_TOKEN is not set. ' +
    'Copy .env.example to .env and set API_TOKEN before starting the dev server.'
  )
}
const HEADERS = {
  'Content-Type':  'application/json',
  'Authorization': `Bearer ${TOKEN}`,
}

async function get(path) {
  const res = await fetch(`${BASE}${path}`, { headers: HEADERS })
  if (!res.ok) throw new Error(`GET ${path} → ${res.status}`)
  return res.json()
}

async function post(path, body) {
  const res = await fetch(`${BASE}${path}`, {
    method:  'POST',
    headers: HEADERS,
    body:    JSON.stringify(body),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err?.detail?.error?.message || `POST ${path} → ${res.status}`)
  }
  return res.json()
}

async function patch(path, body) {
  const res = await fetch(`${BASE}${path}`, {
    method:  'PATCH',
    headers: HEADERS,
    body:    JSON.stringify(body),
  })
  if (!res.ok) throw new Error(`PATCH ${path} → ${res.status}`)
  return res.json()
}

// ── Health ────────────────────────────────────────────────────────────────
export const getHealth = () => get('/health')

// ── Dashboard overview ────────────────────────────────────────────────────
export const getDashboardStats = () => get('/v1/dashboard/stats')

// ── Transactions ──────────────────────────────────────────────────────────
export const getTransactions = (params = {}) => {
  const qs = new URLSearchParams(params).toString()
  return get(`/v1/transactions?${qs}`)
}
export const getTransaction        = (id)   => get(`/v1/transactions/${id}`)
export const explainTransaction    = (id)   => get(`/v1/transactions/${id}/explain`)
export const scoreTransaction      = (body) => post('/v1/transactions/score', body)
export const scoreAndExplain       = (body) => post('/v1/transactions/score/explain', body)
export const scoreBatch            = (transactions) => post('/v1/transactions/score/batch', { transactions })

// ── Users ─────────────────────────────────────────────────────────────────
export const getUser        = (id)   => get(`/v1/users/${id}`)
export const getUserHistory = (id)   => get(`/v1/users/${id}/history`)
export const updateRiskTier = (id, risk_tier, reason) =>
  patch(`/v1/users/${id}/risk-tier`, { risk_tier, reason })

// ── Model ─────────────────────────────────────────────────────────────────
export const getModelInfo       = ()        => get('/v1/model/info')
export const getModelCard       = ()        => get('/v1/model/card')
export const getModelVersions   = ()        => get('/v1/model/versions')
export const getModelFeatures   = ()        => get('/v1/model/features')
export const promoteModel       = (version) => post(`/v1/model/promote/${version}`, {})
export const triggerRetrain     = (reason)  => post('/v1/model/retrain', { reason })
export const getABRouting       = ()                                => get('/v1/model/ab')
export const setABRouting       = (challenger_version, traffic_pct) =>
  post('/v1/model/ab', { challenger_version, traffic_pct })

// ── Drift ─────────────────────────────────────────────────────────────────
export const getDriftReport = () => get('/v1/drift/report')

// ── Labels ────────────────────────────────────────────────────────────────
export const submitLabel = (transaction_id, is_fraud, notes = '') =>
  post('/v1/labels', { transaction_id, is_fraud, notes })

// ── SSE ───────────────────────────────────────────────────────────────────
// Exchange the permanent API token for a short-lived (60 s) SSE URL token so
// the permanent secret never appears in browser history or server access logs.
export const getSSEToken = () => post('/v1/stream/token', {})
