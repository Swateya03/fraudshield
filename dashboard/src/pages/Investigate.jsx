import { useState, useEffect } from 'react'
import { Briefcase, Search, AlertTriangle, CheckCircle, History } from 'lucide-react'
import { getTransactions, getUser, getUserHistory, updateRiskTier, explainTransaction } from '../api/client'
import { fmtAmount, fmtDateTime, decisionClass, scoreColor, shortId } from '../lib/utils'

const TIERS = ['low', 'medium', 'high', 'blocked']
const TIER_COLOR = {
  low:     'text-allow-text bg-allow-dim border-allow/30',
  medium:  'text-review-text bg-review-dim border-review/30',
  high:    'text-block-text bg-block-dim border-block/30',
  blocked: 'text-block-text bg-block-dim border-block/50 font-bold',
}

function ReasonBar({ code, contribution }) {
  const pct = Math.min(Math.abs(contribution) * 40, 100)
  const pos = contribution > 0
  return (
    <div className="flex items-center gap-3 py-1.5">
      <span className="font-mono text-xs text-slate-400 w-36 truncate">{code}</span>
      <div className="flex-1 h-1.5 bg-muted rounded-full overflow-hidden">
        <div className={`h-full rounded-full ${pos ? 'bg-block' : 'bg-allow'}`} style={{ width: `${pct}%` }} />
      </div>
      <span className={`font-mono text-xs w-14 text-right ${pos ? 'text-block-text' : 'text-allow-text'}`}>
        {contribution > 0 ? '+' : ''}{contribution?.toFixed(3)}
      </span>
    </div>
  )
}

function TxnRow({ txn, selected, onSelect }) {
  return (
    <tr className={`trow cursor-pointer ${selected ? 'bg-surface' : ''}`} onClick={() => onSelect(txn)}>
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

export default function Investigate() {
  const [userQuery,  setUserQuery]  = useState('')
  const [decision,   setDecision]   = useState('all')
  const [txns,       setTxns]       = useState([])
  const [selected,   setSelected]   = useState(null)
  const [txnLoading, setTxnLoading] = useState(false)
  const [nextCursor, setNextCursor] = useState(null)

  const [user,       setUser]       = useState(null)
  const [userError,  setUserError]  = useState(null)
  const [userLoading,setUserLoading]= useState(false)

  const [newTier,    setNewTier]    = useState('')
  const [reason,     setReason]     = useState('')
  const [saved,      setSaved]      = useState(false)
  const [saveError,  setSaveError]  = useState(null)

  const [explainData,    setExplainData]    = useState(null)
  const [explainLoading, setExplainLoading] = useState(false)

  const [tierHistory,    setTierHistory]    = useState([])
  const [historyLoading, setHistoryLoading] = useState(false)

  // Fetch live explain whenever a row is selected
  useEffect(() => {
    if (!selected) { setExplainData(null); return }
    let cancelled = false
    setExplainLoading(true)
    explainTransaction(selected.transaction_id)
      .then(d => { if (!cancelled) setExplainData(d) })
      .catch(() => { if (!cancelled) setExplainData(null) })
      .finally(() => { if (!cancelled) setExplainLoading(false) })
    return () => { cancelled = true }
  }, [selected?.transaction_id])

  // Fetch tier change history whenever a user is loaded
  useEffect(() => {
    if (!user) { setTierHistory([]); return }
    let cancelled = false
    setHistoryLoading(true)
    getUserHistory(user.id)
      .then(d => { if (!cancelled) setTierHistory(d.history || []) })
      .catch(() => { if (!cancelled) setTierHistory([]) })
      .finally(() => { if (!cancelled) setHistoryLoading(false) })
    return () => { cancelled = true }
  }, [user?.id])

  useEffect(() => { loadTxns('', 'all') }, [])

  async function loadTxns(uid, dec, cursor = null) {
    setTxnLoading(true)
    try {
      const params = { limit: 50, order: 'desc' }
      if (uid)           params.user_id  = uid
      if (dec !== 'all') params.decision = dec
      if (cursor)        params.cursor   = cursor
      const data = await getTransactions(params)
      const list = Array.isArray(data) ? data : (data.transactions || data.items || [])
      setTxns(old => cursor ? [...old, ...list] : list)
      setNextCursor(data.next_cursor || null)
    } catch {
      if (!cursor) setTxns([])
      setNextCursor(null)
    } finally {
      setTxnLoading(false)
    }
  }

  async function handleSearch() {
    const uid = userQuery.trim()
    setSelected(null)
    setUserError(null)
    setSaved(false)
    setSaveError(null)
    setNextCursor(null)

    if (uid) {
      setUserLoading(true)
      try {
        const data = await getUser(uid)
        setUser(data)
        setNewTier(data.risk_tier)
      } catch {
        setUser(null)
        setUserError(`User "${uid}" not found`)
      } finally {
        setUserLoading(false)
      }
    } else {
      setUser(null)
    }

    loadTxns(uid, decision)
  }

  function handleDecisionChange(dec) {
    setDecision(dec)
    setNextCursor(null)
    loadTxns(userQuery.trim(), dec)
  }

  function handleClear() {
    setUserQuery('')
    setUser(null)
    setUserError(null)
    setSaved(false)
    setSaveError(null)
    setSelected(null)
    setDecision('all')
    setNextCursor(null)
    loadTxns('', 'all')
  }

  async function saveRiskTier() {
    if (!user || newTier === user.risk_tier) return
    setUserLoading(true)
    setSaveError(null)
    try {
      await updateRiskTier(user.id, newTier, reason)
      setUser(u => ({ ...u, risk_tier: newTier }))
      setSaved(true)
      setTimeout(() => setSaved(false), 3000)
      setReason('')
    } catch (e) {
      setSaveError(e.message)
    } finally {
      setUserLoading(false)
    }
  }

  return (
    <div className="p-6 h-full flex flex-col gap-5">

      {/* Header */}
      <div>
        <h1 className="text-lg font-medium text-slate-100 flex items-center gap-2">
          <Briefcase size={18} className="text-allow-text" />
          Investigate
        </h1>
        <p className="text-xs text-slate-500 mt-0.5">Search users, inspect transactions, and update risk tiers</p>
      </div>

      {/* Search bar */}
      <div className="flex gap-3">
        <div className="flex-1 relative">
          <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-500" />
          <input
            className="w-full bg-surface border border-border rounded-lg pl-9 pr-4 py-2 text-sm font-mono text-slate-200 placeholder:text-slate-600 focus:outline-none focus:border-allow/50"
            placeholder="Enter user ID to filter (e.g. u_0001) or leave blank for all…"
            value={userQuery}
            onChange={e => setUserQuery(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && handleSearch()}
          />
        </div>
        <select
          className="bg-surface border border-border rounded-lg px-3 py-2 text-sm text-slate-300 focus:outline-none focus:border-allow/50"
          value={decision}
          onChange={e => handleDecisionChange(e.target.value)}
        >
          <option value="all">All decisions</option>
          <option value="block">Block</option>
          <option value="review">Review</option>
          <option value="allow">Allow</option>
        </select>
        <button
          onClick={handleSearch}
          disabled={txnLoading || userLoading}
          className="px-4 py-2 bg-allow text-canvas rounded-lg text-sm font-medium hover:bg-allow/90 transition-colors disabled:opacity-50"
        >
          {(txnLoading || userLoading) ? 'Loading…' : 'Search'}
        </button>
        {(userQuery || decision !== 'all') && (
          <button
            onClick={handleClear}
            className="px-4 py-2 border border-border text-slate-400 rounded-lg text-sm hover:text-slate-200 hover:border-muted transition-colors"
          >
            Clear
          </button>
        )}
      </div>

      {/* Main two-panel layout */}
      <div className="flex gap-4 flex-1 min-h-0">

        {/* Left: user profile + tier controls (fixed 280px, only shown when user searched) */}
        {(user || userError || userLoading) && (
          <div className="w-70 flex-shrink-0 space-y-3 overflow-y-auto" style={{ width: '280px' }}>

            {userError && (
              <div className="flex items-center gap-2 text-xs text-block-text bg-block-dim border border-block/30 rounded-lg p-3">
                <AlertTriangle size={13} />
                {userError}
              </div>
            )}

            {saved && (
              <div className="flex items-center gap-2 text-xs text-allow-text bg-allow-dim border border-allow/30 rounded-lg p-3">
                <CheckCircle size={13} />
                Risk tier updated
              </div>
            )}

            {saveError && (
              <div className="flex items-center gap-2 text-xs text-block-text bg-block-dim border border-block/30 rounded-lg p-3">
                <AlertTriangle size={13} />
                {saveError}
              </div>
            )}

            {user && (
              <>
                {/* Profile card */}
                <div className="panel space-y-3">
                  <div className="text-xs text-slate-500 uppercase tracking-widest">User profile</div>
                  <div className="flex items-start justify-between">
                    <div>
                      <div className="font-mono text-sm text-slate-200">{user.id}</div>
                      <div className="text-xs text-slate-500 mt-0.5">{user.email}</div>
                    </div>
                    <span className={`text-xs px-2 py-0.5 rounded border ${TIER_COLOR[user.risk_tier] || 'text-slate-400'}`}>
                      {user.risk_tier?.toUpperCase()}
                    </span>
                  </div>
                  <div className="grid grid-cols-2 gap-2 text-xs">
                    <div>
                      <div className="text-slate-500">KYC</div>
                      <div className="text-slate-300">{user.kyc_status}</div>
                    </div>
                    <div>
                      <div className="text-slate-500">Phone</div>
                      <div className="font-mono text-slate-300">{user.phone || '—'}</div>
                    </div>
                    <div>
                      <div className="text-slate-500">Created</div>
                      <div className="text-slate-300">{fmtDateTime(user.created_at)}</div>
                    </div>
                    <div>
                      <div className="text-slate-500">Updated</div>
                      <div className="text-slate-300">{fmtDateTime(user.updated_at)}</div>
                    </div>
                  </div>
                </div>

                {/* Risk tier controls */}
                <div className="panel space-y-3">
                  <div className="text-xs text-slate-500 uppercase tracking-widest">Update risk tier</div>
                  <div className="grid grid-cols-2 gap-2">
                    {TIERS.map(tier => (
                      <button
                        key={tier}
                        onClick={() => setNewTier(tier)}
                        className={`py-2 px-3 rounded-lg text-xs font-medium border transition-all ${
                          newTier === tier
                            ? TIER_COLOR[tier]
                            : 'border-border text-slate-500 hover:text-slate-300 hover:border-muted'
                        }`}
                      >
                        {tier.toUpperCase()}
                      </button>
                    ))}
                  </div>
                  <textarea
                    className="w-full bg-surface border border-border rounded-lg px-3 py-2 text-xs text-slate-200 placeholder:text-slate-600 focus:outline-none focus:border-allow/50 resize-none"
                    rows={2}
                    placeholder="Reason (optional)"
                    value={reason}
                    onChange={e => setReason(e.target.value)}
                  />
                  <button
                    onClick={saveRiskTier}
                    disabled={userLoading || newTier === user.risk_tier}
                    className="w-full py-2 bg-allow text-canvas rounded-lg text-xs font-medium hover:bg-allow/90 transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
                  >
                    {newTier === user.risk_tier ? 'No changes' : `Save · ${newTier.toUpperCase()}`}
                  </button>
                  {newTier === 'blocked' && (
                    <div className="flex items-start gap-2 text-xs text-block-text bg-block-dim border border-block/30 rounded-lg p-2">
                      <AlertTriangle size={12} className="flex-shrink-0 mt-0.5" />
                      Future transactions will score 1.0 immediately.
                    </div>
                  )}
                </div>

                {/* Tier change audit trail */}
                <div className="panel space-y-2">
                  <div className="text-xs text-slate-500 uppercase tracking-widest flex items-center gap-1.5">
                    <History size={11} />
                    Tier history
                  </div>
                  {historyLoading && (
                    <div className="text-xs text-slate-600 py-1">Loading…</div>
                  )}
                  {!historyLoading && tierHistory.length === 0 && (
                    <div className="text-xs text-slate-600 py-1">No changes recorded</div>
                  )}
                  {!historyLoading && tierHistory.map((h, i) => (
                    <div key={i} className="flex items-start justify-between gap-2 text-xs">
                      <div>
                        <span className={`px-1.5 py-0.5 rounded border text-xs ${TIER_COLOR[h.risk_tier] || 'text-slate-400 border-border'}`}>
                          {h.risk_tier?.toUpperCase()}
                        </span>
                        {h.valid_to === null && (
                          <span className="ml-1 text-allow-text">current</span>
                        )}
                        {h.change_reason && (
                          <div className="text-slate-500 mt-0.5 truncate max-w-[180px]">{h.change_reason}</div>
                        )}
                      </div>
                      <div className="text-slate-600 text-right flex-shrink-0">
                        {fmtDateTime(h.valid_from)}
                      </div>
                    </div>
                  ))}
                </div>
              </>
            )}
          </div>
        )}

        {/* Right: transaction table + detail panel */}
        <div className="flex flex-1 gap-4 min-h-0">
          <div className={`panel p-0 overflow-hidden flex flex-col ${selected ? 'flex-1' : 'w-full'}`}>
            <div className="px-4 py-3 border-b border-border flex-shrink-0">
              <span className="text-xs text-slate-500 uppercase tracking-widest">
                {txns.length} transactions
                {userQuery.trim() && user ? ` · ${user.id}` : ''}
              </span>
            </div>
            <div className="overflow-auto flex-1">
              <table className="w-full text-sm">
                <thead className="sticky top-0 bg-panel z-10">
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
                  {txnLoading && (
                    <tr><td colSpan={7} className="tcell text-center text-slate-600 py-10">Loading…</td></tr>
                  )}
                  {!txnLoading && txns.length === 0 && (
                    <tr><td colSpan={7} className="tcell text-center text-slate-600 py-10">No transactions</td></tr>
                  )}
                  {!txnLoading && txns.map(txn => (
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
            {nextCursor && (
              <div className="px-4 py-2 border-t border-border flex-shrink-0">
                <button
                  onClick={() => loadTxns(userQuery.trim(), decision, nextCursor)}
                  disabled={txnLoading}
                  className="w-full py-2 text-xs text-slate-400 hover:text-slate-200 disabled:opacity-40 transition-colors"
                >
                  {txnLoading ? 'Loading…' : 'Load more'}
                </button>
              </div>
            )}
          </div>

          {/* Detail panel */}
          {selected && (
            <div className="w-72 flex-shrink-0 space-y-3 overflow-y-auto">
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

              <div className="panel space-y-1">
                <div className="text-xs text-slate-500 uppercase tracking-widest mb-2">
                  Why this score
                  {explainData?.strategy_used && (
                    <span className="ml-2 normal-case font-mono text-slate-600">({explainData.strategy_used})</span>
                  )}
                </div>
                {explainLoading && (
                  <div className="text-xs text-slate-600 py-2">Loading reason codes…</div>
                )}
                {!explainLoading && (explainData?.reason_codes ?? selected.reason_codes ?? []).length === 0 && (
                  <div className="text-xs text-slate-600 py-2">No reason codes recorded</div>
                )}
                {!explainLoading && (explainData?.reason_codes ?? selected.reason_codes ?? []).map((rc, i) => (
                  <ReasonBar key={i} {...rc} />
                ))}
              </div>

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
    </div>
  )
}
