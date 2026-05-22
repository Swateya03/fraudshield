import { describe, it, expect } from 'vitest'
import {
  fmtAmount,
  decisionClass,
  decisionColor,
  scoreColor,
  psiStatus,
  shortId,
} from './utils.js'

// ── fmtAmount ─────────────────────────────────────────────────────────────────

describe('fmtAmount', () => {
  it('returns — for null', () => {
    expect(fmtAmount(null)).toBe('—')
  })

  it('returns — for undefined', () => {
    expect(fmtAmount(undefined)).toBe('—')
  })

  it('formats INR by default', () => {
    const result = fmtAmount(1500)
    expect(result).toContain('1,500')
  })

  it('formats USD when currency passed', () => {
    const result = fmtAmount(200, 'USD')
    expect(result).toContain('200')
  })

  it('formats zero without dash', () => {
    const result = fmtAmount(0)
    expect(result).not.toBe('—')
    expect(result).toContain('0')
  })
})

// ── decisionClass ─────────────────────────────────────────────────────────────

describe('decisionClass', () => {
  it('maps block → badge-block', () => {
    expect(decisionClass('block')).toBe('badge-block')
  })

  it('maps BLOCK (uppercase) → badge-block', () => {
    expect(decisionClass('BLOCK')).toBe('badge-block')
  })

  it('maps review → badge-review', () => {
    expect(decisionClass('review')).toBe('badge-review')
  })

  it('maps allow → badge-allow', () => {
    expect(decisionClass('allow')).toBe('badge-allow')
  })

  it('returns badge-allow for unknown value', () => {
    expect(decisionClass('unknown')).toBe('badge-allow')
  })
})

// ── decisionColor ─────────────────────────────────────────────────────────────

describe('decisionColor', () => {
  it('block is red', () => {
    expect(decisionColor('block')).toBe('#d84040')
  })

  it('review is amber', () => {
    expect(decisionColor('review')).toBe('#c47f12')
  })

  it('allow is green', () => {
    expect(decisionColor('allow')).toBe('#0d9e75')
  })
})

// ── scoreColor ────────────────────────────────────────────────────────────────

describe('scoreColor', () => {
  it('returns red for score ≥ 0.85', () => {
    expect(scoreColor(0.85)).toBe('#f87171')
    expect(scoreColor(1.0)).toBe('#f87171')
  })

  it('returns amber for score ≥ 0.50', () => {
    expect(scoreColor(0.50)).toBe('#fbbf24')
    expect(scoreColor(0.84)).toBe('#fbbf24')
  })

  it('returns green for score < 0.50', () => {
    expect(scoreColor(0.49)).toBe('#5de4b4')
    expect(scoreColor(0.0)).toBe('#5de4b4')
  })
})

// ── psiStatus ─────────────────────────────────────────────────────────────────

describe('psiStatus', () => {
  it('labels PSI > 0.20 as DRIFT', () => {
    const result = psiStatus(0.25)
    expect(result.label).toBe('DRIFT')
  })

  it('labels PSI between 0.10 and 0.20 as WARNING', () => {
    const result = psiStatus(0.15)
    expect(result.label).toBe('WARNING')
  })

  it('labels PSI < 0.10 as STABLE', () => {
    const result = psiStatus(0.05)
    expect(result.label).toBe('STABLE')
  })

  it('includes a color in the result', () => {
    expect(psiStatus(0.05)).toHaveProperty('color')
  })
})

// ── shortId ───────────────────────────────────────────────────────────────────

describe('shortId', () => {
  it('returns last 8 characters of a long id', () => {
    expect(shortId('txn_abcdef12345678')).toBe('12345678')
  })

  it('returns — for null', () => {
    expect(shortId(null)).toBe('—')
  })

  it('returns — for undefined', () => {
    expect(shortId(undefined)).toBe('—')
  })

  it('returns full string if shorter than 8 chars', () => {
    expect(shortId('abc')).toBe('abc')
  })
})
