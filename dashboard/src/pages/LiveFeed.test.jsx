import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

// Must be hoisted before the component import so the module boundary is mocked
vi.mock('../api/client', () => ({
  getTransactions:    vi.fn(),
  getDashboardStats:  vi.fn(),
  getSSEToken:        vi.fn(),
}))

// jsdom has no EventSource — stub it so the SSE effect doesn't throw
class _MockEventSource {
  constructor() { this.onopen = null; this.onmessage = null; this.onerror = null }
  close() {}
}
global.EventSource = _MockEventSource

import LiveFeed from './LiveFeed'
import { getTransactions, getDashboardStats, getSSEToken } from '../api/client'

const STATS = { total_24h: 142, blocked_24h: 8, review_24h: 14, allowed_24h: 120 }
const FEED  = {
  items: [
    {
      transaction_id:    'txn_abc123',
      user_id:           'u_1',
      merchant_id:       'm_grocery',
      amount:            450,
      currency:          'INR',
      channel:           'online',
      ip_address:        '1.2.3.4',
      fraud_probability: 0.05,
      decision:          'allow',
      reason_codes:      [],
      latency_ms:        38,
      scored_at:         '2026-01-01T10:00:00',
    },
  ],
  count: 1,
}

describe('LiveFeed', () => {
  beforeEach(() => {
    getTransactions.mockResolvedValue(FEED)
    getDashboardStats.mockResolvedValue(STATS)
    getSSEToken.mockResolvedValue({ token: 'tok_test' })
  })
  afterEach(() => { vi.clearAllMocks() })

  it('renders the page heading', () => {
    render(<LiveFeed />)
    expect(screen.getByText('Live Transaction Feed')).toBeInTheDocument()
  })

  it('renders Pause button on mount', () => {
    render(<LiveFeed />)
    expect(screen.getByText(/pause/i)).toBeInTheDocument()
  })

  it('toggles to Resume when Pause is clicked', async () => {
    const user = userEvent.setup()
    render(<LiveFeed />)
    await user.click(screen.getByText(/pause/i))
    expect(screen.getByText(/resume/i)).toBeInTheDocument()
  })

  it('requests a fresh SSE token on mount', async () => {
    render(<LiveFeed />)
    await waitFor(() => expect(getSSEToken).toHaveBeenCalledOnce())
  })

  it('fetches recent transactions on mount', async () => {
    render(<LiveFeed />)
    await waitFor(() =>
      expect(getTransactions).toHaveBeenCalledWith({ limit: 50, order: 'desc' })
    )
  })

  it('displays a row for each loaded transaction', async () => {
    render(<LiveFeed />)
    await waitFor(() =>
      expect(screen.getByText('txn_abc123')).toBeInTheDocument()
    )
  })

  it('shows stats from getDashboardStats', async () => {
    render(<LiveFeed />)
    await waitFor(() =>
      expect(screen.getByText('142')).toBeInTheDocument()
    )
    expect(screen.getByText('8')).toBeInTheDocument()
  })

  it('polls dashboard stats with an interval', async () => {
    vi.useFakeTimers()
    render(<LiveFeed />)
    await waitFor(() => expect(getDashboardStats).toHaveBeenCalledTimes(1))
    vi.advanceTimersByTime(10_000)
    await waitFor(() => expect(getDashboardStats).toHaveBeenCalledTimes(2))
    vi.useRealTimers()
  })
})
