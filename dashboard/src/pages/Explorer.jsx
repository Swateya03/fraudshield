import { useState } from 'react'
import { Search, Filter, ChevronDown, ChevronUp } from 'lucide-react'
import { getTransactions } from '../api/client'
import { fmtAmount, fmtDateTime, decisionClass, scoreColor, shortId } from '../lib/utils'

function ReasonBar({ code, contribution }) {
  const pct  = Math.min(Math.abs(contribution) * 40, 100)
  const pos  = contribution > 0
  return (
    <div className="flex items-center gap-3 py-1.5">
      <span className="font-mono text-xs text-slate-400 w-36 truncate">{code}</span>
      <div className="flex-1 h-1.5 bg-muted rounded-full overflow-hidden">
        <div
          className={`h-full rounded-full ${pos ? 'bg-block' : 'bg-allow'}`}
          style={{ width: `${pct}%` }}
        />
      </div>
      <span className={`font-mono text-xs w-14 text-right ${pos ? 'text-block-text' : 'text-allow-text'}`}>
        {contribution > 0 ? '+' : ''}{contribution?.toFixed(3)}
      </span>
    </div>
  )
}

function TxnRow({ txn, onSelect, selected }) {
  return (
    <tr
      className={`trow cursor-pointer ${selected ? 'bg-surface' : ''}`}
      onClick={() => onSelect(txn)}
    >
      <td className="tcell font-mono text-xs text-slate-500">{fmtDateTime(txn.scored_at)}</td>
      <td className="tcell font-mono text-xs text-slate-300">{txn.transaction_id}</td>
      <td className="tcell font-mono text-xs text-slate-400">{shortId(txn.user_id)}</td>
      <td className="tcell text-xs text-slate-400">{txn.merchant_id}</td>
      <td className="tcell font-mono text-xs text-right text-slate-200">{fmtAmount(txn.amount, txn.currency)}</td>
      <td className="tcell">
        <span className="font-mono text-xs" style={{ color: scoreColor(txn.fraud_probability) }}>
          {txn.fraud_probability?.toFixed(4)}
        </span>
      </td>
      <td className="tcell">
        <span className={decisionClass(txn.decision)}>{txn.decision?.toUpperCase()}</span>
      </td>
    </tr>
  )
}

export default function Explorer() {
  const [query,    setQuery]    = useState('')
  const [decision, setDecision] = useState('all')
  const [results,  setResults]  = useState([])
  const [selected, setSelected] = useState(null)
  const [loading,  setLoading]  = useState(false)
  const [searched, setSearched] = useState(false)

  async function search() {
    setLoading(true)
    setSearched(true)
    try {
      const params = { limit: 100, order: 'desc' }
      if (query)              params.user_id       = query
      if (decision !== 'all') params.decision      = decision
      const data = await getTransactions(params)
      const txns = Array.isArray(data) ? data : (data.transactions || data.items || [])
      setResults(txns)
    } catch (e) {
      setResults([])
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="p-6 space-y-5">

      {/* Header */}
      <div>
        <h1 className="text-lg font-medium text-slate-100 flex items-center gap-2">
          <Search size={18} className="text-allow-text" />
          Transaction Explorer
        </h1>
        <p className="text-xs text-slate-500 mt-0.5">Search, filter, and inspect scored transactions</p>
      </div>

      {/* Search bar */}
      <div className="flex gap-3">
        <div className="flex-1 relative">
          <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-500" />
          <input
            className="w-full bg-surface border border-border rounded-lg pl-9 pr-4 py-2 text-sm text-slate-200 placeholder:text-slate-600 focus:outline-none focus:border-allow/50"
            placeholder="User ID, transaction ID, or leave blank for all…"
            value={query}
            onChange={e => setQuery(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && search()}
          />
        </div>
        <select
          className="bg-surface border border-border rounded-lg px-3 py-2 text-sm text-slate-300 focus:outline-none focus:border-allow/50"
          value={decision}
          onChange={e => setDecision(e.target.value)}
        >
          <option value="all">All decisions</option>
          <option value="block">Block</option>
          <option value="review">Review</option>
          <option value="allow">Allow</option>
        </select>
        <button
          onClick={search}
          disabled={loading}
          className="px-4 py-2 bg-allow text-canvas rounded-lg text-sm font-medium hover:bg-allow/90 transition-colors disabled:opacity-50"
        >
          {loading ? 'Searching…' : 'Search'}
        </button>
      </div>

      <div className="flex gap-4">
        {/* Results table */}
        <div className={`panel p-0 overflow-hidden ${selected ? 'flex-1' : 'w-full'}`}>
          <div className="px-4 py-3 border-b border-border">
            <span className="text-xs text-slate-500 uppercase tracking-widest">
              {searched ? `${results.length} results` : 'Search to see transactions'}
            </span>
          </div>
          <div className="overflow-x-auto max-h-[calc(100vh-280px)] overflow-y-auto">
            <table className="w-full text-sm">
              <thead className="sticky top-0 bg-panel">
                <tr className="border-b border-border/50 text-xs text-slate-500 uppercase tracking-wider">
                  <th className="tcell text-left font-medium">Time</th>
                  <th className="tcell text-left font-medium">Transaction ID</th>
                  <th className="tcell text-left font-medium">User</th>
                  <th className="tcell text-left font-medium">Merchant</th>
                  <th className="tcell text-right font-medium">Amount</th>
                  <th className="tcell text-left font-medium">Score</th>
                  <th className="tcell text-left font-medium">Decision</th>
                </tr>
              </thead>
              <tbody>
                {results.length === 0 && searched && (
                  <tr><td colSpan={7} className="tcell text-center text-slate-600 py-10">No results</td></tr>
                )}
                {results.map(txn => (
                  <TxnRow
                    key={txn.transaction_id}
                    txn={txn}
                    selected={selected?.transaction_id === txn.transaction_id}
                    onSelect={t => setSelected(prev => prev?.transaction_id === t.transaction_id ? null : t)}
                  />
                ))}
              </tbody>
            </table>
          </div>
        </div>

        {/* Detail panel */}
        {selected && (
          <div className="w-80 flex-shrink-0 space-y-3">
            <div className="panel space-y-3">
              <div className="flex items-start justify-between">
                <div>
                  <div className="text-xs text-slate-500 uppercase tracking-widest">Transaction</div>
                  <div className="font-mono text-xs text-slate-300 mt-0.5">{selected.transaction_id}</div>
                </div>
                <span className={decisionClass(selected.decision)}>{selected.decision?.toUpperCase()}</span>
              </div>

              <div className="grid grid-cols-2 gap-3 text-sm">
                <div>
                  <div className="text-xs text-slate-500">Amount</div>
                  <div className="font-mono text-slate-200">{fmtAmount(selected.amount, selected.currency)}</div>
                </div>
                <div>
                  <div className="text-xs text-slate-500">Score</div>
                  <div className="font-mono" style={{ color: scoreColor(selected.fraud_probability) }}>
                    {selected.fraud_probability?.toFixed(4)}
                  </div>
                </div>
                <div>
                  <div className="text-xs text-slate-500">User</div>
                  <div className="font-mono text-xs text-slate-300">{selected.user_id}</div>
                </div>
                <div>
                  <div className="text-xs text-slate-500">Merchant</div>
                  <div className="text-xs text-slate-300">{selected.merchant_id}</div>
                </div>
                <div>
                  <div className="text-xs text-slate-500">Channel</div>
                  <div className="text-xs text-slate-300">{selected.channel}</div>
                </div>
                <div>
                  <div className="text-xs text-slate-500">Latency</div>
                  <div className="font-mono text-xs text-slate-300">{selected.latency_ms}ms</div>
                </div>
              </div>
            </div>

            {/* Reason codes */}
            {selected.reason_codes?.length > 0 && (
              <div className="panel space-y-1">
                <div className="text-xs text-slate-500 uppercase tracking-widest mb-2">Why this score</div>
                {selected.reason_codes.map((rc, i) => (
                  <ReasonBar key={i} {...rc} />
                ))}
              </div>
            )}

            <button
              onClick={() => setSelected(null)}
              className="w-full py-2 text-xs text-slate-500 hover:text-slate-300 border border-border rounded-lg transition-colors"
            >
              Close detail
            </button>
          </div>
        )}
      </div>
    </div>
  )
}
