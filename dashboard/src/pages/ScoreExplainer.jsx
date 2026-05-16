import { useState } from 'react'
import { Zap, Send } from 'lucide-react'
import { scoreTransaction } from '../api/client'
import { fmtAmount, scoreColor, decisionClass } from '../lib/utils'

const PRESETS = [
  {
    label: 'Classic fraud',
    color: 'border-block/40 hover:bg-block-dim',
    body: { transaction_id: '', user_id: 'u_test', merchant_id: 'm_crypto',
            amount: 45000, currency: 'INR', channel: 'online', ip_address: '185.220.101.5' },
  },
  {
    label: 'Normal purchase',
    color: 'border-allow/40 hover:bg-allow-dim',
    body: { transaction_id: '', user_id: 'u_test', merchant_id: 'm_grocery',
            amount: 450, currency: 'INR', channel: 'online', ip_address: '203.112.45.67' },
  },
  {
    label: 'Drift-pattern fraud',
    color: 'border-review/40 hover:bg-review-dim',
    body: { transaction_id: '', user_id: 'u_test', merchant_id: 'm_crypto',
            amount: 1800, currency: 'INR', channel: 'online', ip_address: '45.142.212.100' },
  },
]

let _counter = 0
function nextTxnId() { return `txn_ui_${++_counter}_${Date.now()}` }

function ContributionBar({ code, contribution }) {
  const pct = Math.min(Math.abs(contribution) * 50, 100)
  const pos = contribution > 0
  return (
    <div className="flex items-center gap-3">
      <span className="font-mono text-xs text-slate-400 w-44 truncate">{code}</span>
      <div className="flex-1 flex items-center gap-1">
        {!pos && <div className="flex-1 flex justify-end"><div className={`h-4 rounded-sm bg-allow`} style={{ width: `${pct}%` }} /></div>}
        <div className="w-px h-4 bg-border" />
        {pos && <div className={`h-4 rounded-sm bg-block`} style={{ width: `${pct}%` }} />}
        {!pos && <div className="flex-1" />}
      </div>
      <span className={`font-mono text-xs w-16 text-right ${pos ? 'text-block-text' : 'text-allow-text'}`}>
        {contribution > 0 ? '+' : ''}{contribution?.toFixed(3)}
      </span>
    </div>
  )
}

export default function ScoreExplainer() {
  const [form, setForm] = useState({
    user_id: 'u_test', merchant_id: 'm_crypto', amount: '45000',
    currency: 'INR', channel: 'online', ip_address: '185.220.101.5',
  })
  const [result,  setResult]  = useState(null)
  const [loading, setLoading] = useState(false)
  const [error,   setError]   = useState(null)

  function applyPreset(preset) {
    setForm({
      user_id:     preset.body.user_id,
      merchant_id: preset.body.merchant_id,
      amount:      String(preset.body.amount),
      currency:    preset.body.currency,
      channel:     preset.body.channel,
      ip_address:  preset.body.ip_address,
    })
    setResult(null); setError(null)
  }

  async function score() {
    setLoading(true); setError(null)
    try {
      const res = await scoreTransaction({
        transaction_id: nextTxnId(),
        user_id:     form.user_id,
        merchant_id: form.merchant_id,
        amount:      parseFloat(form.amount),
        currency:    form.currency,
        channel:     form.channel,
        ip_address:  form.ip_address || undefined,
        dry_run:     true,
      })
      setResult(res)
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }

  const scoreVal = result?.fraud_probability ?? 0

  return (
    <div className="p-6 space-y-5">
      <div>
        <h1 className="text-lg font-medium text-slate-100 flex items-center gap-2">
          <Zap size={18} className="text-allow-text" />
          Score Explainer
        </h1>
        <p className="text-xs text-slate-500 mt-0.5">
          Submit a transaction and see exactly why the model scored it
        </p>
      </div>

      <div className="grid grid-cols-3 gap-3">
        {PRESETS.map(p => (
          <button
            key={p.label}
            onClick={() => applyPreset(p)}
            className={`panel border text-left p-3 transition-colors cursor-pointer ${p.color}`}
          >
            <div className="text-xs font-medium text-slate-300">{p.label}</div>
            <div className="font-mono text-xs text-slate-500 mt-0.5">
              {fmtAmount(p.body.amount)} · {p.body.merchant_id}
            </div>
          </button>
        ))}
      </div>

      <div className="grid grid-cols-2 gap-5">
        {/* Form */}
        <div className="panel space-y-3">
          <div className="text-xs text-slate-500 uppercase tracking-widest">Transaction details</div>
          {[
            { key: 'user_id',     label: 'User ID' },
            { key: 'merchant_id', label: 'Merchant ID' },
            { key: 'amount',      label: `Amount (${form.currency})`, type: 'number' },
            { key: 'ip_address',  label: 'IP Address' },
          ].map(({ key, label, type = 'text' }) => (
            <div key={key}>
              <label className="block text-xs text-slate-500 mb-1">{label}</label>
              <input
                type={type}
                className="w-full bg-surface border border-border rounded-lg px-3 py-2 text-sm font-mono text-slate-200 focus:outline-none focus:border-allow/50"
                value={form[key]}
                onChange={e => setForm(f => ({ ...f, [key]: e.target.value }))}
              />
            </div>
          ))}
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-xs text-slate-500 mb-1">Currency</label>
              <select
                className="w-full bg-surface border border-border rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none"
                value={form.currency}
                onChange={e => setForm(f => ({ ...f, currency: e.target.value }))}
              >
                {['INR','USD','EUR'].map(c => <option key={c}>{c}</option>)}
              </select>
            </div>
            <div>
              <label className="block text-xs text-slate-500 mb-1">Channel</label>
              <select
                className="w-full bg-surface border border-border rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none"
                value={form.channel}
                onChange={e => setForm(f => ({ ...f, channel: e.target.value }))}
              >
                {['online','pos','upi','atm','nfc'].map(c => <option key={c}>{c}</option>)}
              </select>
            </div>
          </div>
          <button
            onClick={score}
            disabled={loading}
            className="w-full flex items-center justify-center gap-2 py-2.5 bg-allow text-canvas rounded-lg font-medium text-sm hover:bg-allow/90 transition-colors disabled:opacity-50"
          >
            <Send size={14} />
            {loading ? 'Scoring…' : 'Score Transaction'}
          </button>
          {error && <div className="text-xs text-block-text bg-block-dim border border-block/30 rounded-lg p-2">{error}</div>}
        </div>

        {/* Result */}
        <div className="space-y-3">
          {result ? (
            <>
              {/* Score gauge */}
              <div className="panel text-center space-y-2">
                <div className="text-xs text-slate-500 uppercase tracking-widest">Fraud probability</div>
                <div className="font-mono text-5xl font-medium" style={{ color: scoreColor(scoreVal) }}>
                  {(scoreVal * 100).toFixed(1)}%
                </div>
                <div className="w-full h-2 bg-muted rounded-full overflow-hidden">
                  <div
                    className="h-full rounded-full transition-all duration-700"
                    style={{
                      width: `${scoreVal * 100}%`,
                      background: scoreColor(scoreVal),
                    }}
                  />
                </div>
                <span className={decisionClass(result.decision)}>
                  {result.decision?.toUpperCase()}
                </span>
                <div className="text-xs text-slate-500">
                  Scored in {result.latency_ms}ms · {result.model_version}
                </div>
              </div>

              {/* Reason codes */}
              {result.reason_codes?.length > 0 && (
                <div className="panel space-y-2">
                  <div className="text-xs text-slate-500 uppercase tracking-widest mb-3">
                    Feature contributions
                  </div>
                  <div className="text-xs text-slate-600 flex justify-between mb-1">
                    <span>← pushes toward allow</span>
                    <span>pushes toward block →</span>
                  </div>
                  <div className="space-y-1">
                    {result.reason_codes.map((rc, i) => (
                      <ContributionBar key={i} {...rc} />
                    ))}
                  </div>
                </div>
              )}
            </>
          ) : (
            <div className="panel h-64 flex items-center justify-center">
              <div className="text-center text-slate-600">
                <Zap size={32} className="mx-auto mb-2 opacity-30" />
                <div className="text-sm">Submit a transaction to see the score and explanation</div>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
