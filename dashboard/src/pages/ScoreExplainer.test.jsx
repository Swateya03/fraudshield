import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

vi.mock('../api/client', () => ({
  scoreAndExplain: vi.fn(),
}))

import ScoreExplainer from './ScoreExplainer'
import { scoreAndExplain } from '../api/client'

const BLOCK_RESULT = {
  transaction_id:    'txn_ui_1',
  fraud_probability: 0.953,
  decision:          'block',
  reason_codes:      [
    { code: 'high_risk_merchant',  contribution: 0.62 },
    { code: 'known_fraud_ip',      contribution: 0.41 },
  ],
  model_version: 'v1.0.0',
  latency_ms:    28,
  request_id:    'req_test',
}

describe('ScoreExplainer', () => {
  beforeEach(() => {
    scoreAndExplain.mockResolvedValue(BLOCK_RESULT)
  })
  afterEach(() => { vi.clearAllMocks() })

  it('renders the page heading', () => {
    render(<ScoreExplainer />)
    expect(screen.getByText('Score Explainer')).toBeInTheDocument()
  })

  it('renders all three preset buttons', () => {
    render(<ScoreExplainer />)
    expect(screen.getByText('Classic fraud')).toBeInTheDocument()
    expect(screen.getByText('Normal purchase')).toBeInTheDocument()
    expect(screen.getByText('Drift-pattern fraud')).toBeInTheDocument()
  })

  it('renders the Score Transaction submit button', () => {
    render(<ScoreExplainer />)
    expect(screen.getByText('Score Transaction')).toBeInTheDocument()
  })

  it('pre-fills form with the classic fraud preset by default', () => {
    render(<ScoreExplainer />)
    // Default amount is 45000
    expect(screen.getByDisplayValue('45000')).toBeInTheDocument()
  })

  it('applies Normal purchase preset when clicked', async () => {
    const user = userEvent.setup()
    render(<ScoreExplainer />)
    await user.click(screen.getByText('Normal purchase'))
    // Normal purchase amount = 450
    expect(screen.getByDisplayValue('450')).toBeInTheDocument()
  })

  it('applies Drift-pattern fraud preset when clicked', async () => {
    const user = userEvent.setup()
    render(<ScoreExplainer />)
    await user.click(screen.getByText('Drift-pattern fraud'))
    expect(screen.getByDisplayValue('1800')).toBeInTheDocument()
  })

  it('calls scoreAndExplain with dry_run=true on submit', async () => {
    const user = userEvent.setup()
    render(<ScoreExplainer />)
    await user.click(screen.getByText('Score Transaction'))
    await waitFor(() => expect(scoreAndExplain).toHaveBeenCalledOnce())
    const arg = scoreAndExplain.mock.calls[0][0]
    expect(arg.dry_run).toBe(true)
  })

  it('shows fraud probability after successful score', async () => {
    const user = userEvent.setup()
    render(<ScoreExplainer />)
    await user.click(screen.getByText('Score Transaction'))
    await waitFor(() =>
      expect(screen.getByText(/fraud probability/i)).toBeInTheDocument()
    )
    // 0.953 should appear somewhere in the result panel
    expect(screen.getByText(/0\.953|95\.3/)).toBeInTheDocument()
  })

  it('renders reason code bars after scoring', async () => {
    const user = userEvent.setup()
    render(<ScoreExplainer />)
    await user.click(screen.getByText('Score Transaction'))
    await waitFor(() =>
      expect(screen.getByText('high_risk_merchant')).toBeInTheDocument()
    )
    expect(screen.getByText('known_fraud_ip')).toBeInTheDocument()
  })

  it('shows error message when API throws', async () => {
    scoreAndExplain.mockRejectedValue(new Error('Service unavailable'))
    const user = userEvent.setup()
    render(<ScoreExplainer />)
    await user.click(screen.getByText('Score Transaction'))
    await waitFor(() =>
      expect(screen.getByText('Service unavailable')).toBeInTheDocument()
    )
  })

  it('shows Scoring… while the request is in flight', async () => {
    let resolve
    scoreAndExplain.mockReturnValue(new Promise(r => { resolve = r }))
    const user = userEvent.setup()
    render(<ScoreExplainer />)
    await user.click(screen.getByText('Score Transaction'))
    expect(screen.getByText('Scoring…')).toBeInTheDocument()
    resolve(BLOCK_RESULT)
  })
})
