import { clsx } from 'clsx'
import { twMerge } from 'tailwind-merge'

export function cn(...inputs) {
  return twMerge(clsx(inputs))
}

export function fmtAmount(n, currency = 'INR') {
  if (n == null) return '—'
  const locale = currency === 'INR' ? 'en-IN' : 'en-US'
  return new Intl.NumberFormat(locale, {
    style: 'currency', currency, maximumFractionDigits: 0,
  }).format(n)
}

export function fmtTime(iso) {
  if (!iso) return '—'
  return new Date(iso).toLocaleTimeString('en-IN', {
    hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false,
  })
}

export function fmtDate(iso) {
  if (!iso) return '—'
  return new Date(iso).toLocaleDateString('en-IN', {
    day: '2-digit', month: 'short', year: 'numeric',
  })
}

export function fmtDateTime(iso) {
  if (!iso) return '—'
  return `${fmtDate(iso)} ${fmtTime(iso)}`
}

export function decisionClass(decision) {
  if (decision === 'block')  return 'badge-block'
  if (decision === 'review') return 'badge-review'
  return 'badge-allow'
}

export function decisionColor(decision) {
  if (decision === 'block')  return '#d84040'
  if (decision === 'review') return '#c47f12'
  return '#0d9e75'
}

export function scoreColor(score) {
  if (score >= 0.85) return '#f87171'
  if (score >= 0.50) return '#fbbf24'
  return '#5de4b4'
}

export function psiStatus(psi) {
  if (psi >= 0.25) return { label: 'DRIFT', color: 'text-block-text' }
  if (psi >= 0.10) return { label: 'WARNING', color: 'text-review-text' }
  return { label: 'STABLE', color: 'text-allow-text' }
}

export function shortId(id) {
  return id ? id.slice(-8) : '—'
}
