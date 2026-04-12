import { useState } from 'react'
import { Users, Search, AlertTriangle, CheckCircle } from 'lucide-react'
import { getUser, updateRiskTier } from '../api/client'
import { fmtDateTime } from '../lib/utils'

const TIERS = ['low', 'medium', 'high', 'blocked']
const tierColor = {
  low:     'text-allow-text bg-allow-dim border-allow/30',
  medium:  'text-review-text bg-review-dim border-review/30',
  high:    'text-block-text bg-block-dim border-block/30',
  blocked: 'text-block-text bg-block-dim border-block/50 font-bold',
}

export default function UserRiskManager() {
  const [userId,  setUserId]  = useState('')
  const [user,    setUser]    = useState(null)
  const [loading, setLoading] = useState(false)
  const [error,   setError]   = useState(null)
  const [newTier, setNewTier] = useState('')
  const [reason,  setReason]  = useState('')
  const [saved,   setSaved]   = useState(false)

  async function lookupUser() {
    if (!userId.trim()) return
    setLoading(true); setError(null); setUser(null); setSaved(false)
    try {
      const data = await getUser(userId.trim())
      setUser(data)
      setNewTier(data.risk_tier)
    } catch (e) {
      setError(`User "${userId}" not found`)
    } finally {
      setLoading(false)
    }
  }

  async function saveRiskTier() {
    if (!user || newTier === user.risk_tier) return
    setLoading(true)
    try {
      await updateRiskTier(user.id, newTier, reason)
      setUser(u => ({ ...u, risk_tier: newTier }))
      setSaved(true)
      setTimeout(() => setSaved(false), 3000)
      setReason('')
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="p-6 space-y-5">
      <div>
        <h1 className="text-lg font-medium text-slate-100 flex items-center gap-2">
          <Users size={18} className="text-allow-text" />
          User Risk Manager
        </h1>
        <p className="text-xs text-slate-500 mt-0.5">Look up users and update their risk tier</p>
      </div>

      {/* Search */}
      <div className="flex gap-3">
        <div className="flex-1 relative">
          <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-500" />
          <input
            className="w-full bg-surface border border-border rounded-lg pl-9 pr-4 py-2 text-sm font-mono text-slate-200 placeholder:text-slate-600 focus:outline-none focus:border-allow/50"
            placeholder="Enter user ID (e.g. u_0001)…"
            value={userId}
            onChange={e => setUserId(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && lookupUser()}
          />
        </div>
        <button
          onClick={lookupUser}
          disabled={loading}
          className="px-4 py-2 bg-allow text-canvas rounded-lg text-sm font-medium hover:bg-allow/90 transition-colors disabled:opacity-50"
        >
          {loading ? 'Looking up…' : 'Look up'}
        </button>
      </div>

      {error && (
        <div className="flex items-center gap-2 text-sm text-block-text bg-block-dim border border-block/30 rounded-lg p-3">
          <AlertTriangle size={14} />
          {error}
        </div>
      )}

      {saved && (
        <div className="flex items-center gap-2 text-sm text-allow-text bg-allow-dim border border-allow/30 rounded-lg p-3 animate-slide_in">
          <CheckCircle size={14} />
          Risk tier updated successfully
        </div>
      )}

      {user && (
        <div className="grid grid-cols-2 gap-5">
          {/* User profile */}
          <div className="panel space-y-4">
            <div className="text-xs text-slate-500 uppercase tracking-widest">User profile</div>

            <div className="flex items-start justify-between">
              <div>
                <div className="font-mono text-sm text-slate-200">{user.id}</div>
                <div className="text-xs text-slate-500 mt-0.5">{user.email}</div>
              </div>
              <span className={`text-xs px-2 py-0.5 rounded border ${tierColor[user.risk_tier] || 'text-slate-400'}`}>
                {user.risk_tier?.toUpperCase()}
              </span>
            </div>

            <div className="grid grid-cols-2 gap-3 text-sm">
              <div>
                <div className="text-xs text-slate-500">KYC Status</div>
                <div className="text-slate-300">{user.kyc_status}</div>
              </div>
              <div>
                <div className="text-xs text-slate-500">Phone</div>
                <div className="font-mono text-xs text-slate-300">{user.phone || '—'}</div>
              </div>
              <div>
                <div className="text-xs text-slate-500">Created</div>
                <div className="text-xs text-slate-300">{fmtDateTime(user.created_at)}</div>
              </div>
              <div>
                <div className="text-xs text-slate-500">Updated</div>
                <div className="text-xs text-slate-300">{fmtDateTime(user.updated_at)}</div>
              </div>
            </div>
          </div>

          {/* Risk tier update */}
          <div className="panel space-y-4">
            <div className="text-xs text-slate-500 uppercase tracking-widest">Update risk tier</div>

            <div>
              <label className="block text-xs text-slate-500 mb-2">New risk tier</label>
              <div className="grid grid-cols-2 gap-2">
                {TIERS.map(tier => (
                  <button
                    key={tier}
                    onClick={() => setNewTier(tier)}
                    className={`py-2 px-3 rounded-lg text-xs font-medium border transition-all ${
                      newTier === tier
                        ? tierColor[tier]
                        : 'border-border text-slate-500 hover:text-slate-300 hover:border-muted'
                    }`}
                  >
                    {tier.toUpperCase()}
                  </button>
                ))}
              </div>
            </div>

            <div>
              <label className="block text-xs text-slate-500 mb-1">Reason (optional)</label>
              <textarea
                className="w-full bg-surface border border-border rounded-lg px-3 py-2 text-sm text-slate-200 placeholder:text-slate-600 focus:outline-none focus:border-allow/50 resize-none"
                rows={3}
                placeholder="Manual review, chargeback confirmed, etc."
                value={reason}
                onChange={e => setReason(e.target.value)}
              />
            </div>

            <button
              onClick={saveRiskTier}
              disabled={loading || newTier === user.risk_tier}
              className="w-full py-2.5 bg-allow text-canvas rounded-lg text-sm font-medium hover:bg-allow/90 transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
            >
              {newTier === user.risk_tier ? 'No changes' : `Save: set to ${newTier.toUpperCase()}`}
            </button>

            {newTier === 'blocked' && (
              <div className="flex items-start gap-2 text-xs text-block-text bg-block-dim border border-block/30 rounded-lg p-2">
                <AlertTriangle size={12} className="flex-shrink-0 mt-0.5" />
                Blocking this user will cause all future transactions to return score=1.0 immediately, bypassing the ML model.
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
