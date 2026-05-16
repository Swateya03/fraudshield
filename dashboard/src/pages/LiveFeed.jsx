import { useState, useEffect, useRef } from 'react'
import { Activity } from 'lucide-react'
import { getTransactions, getDashboardStats } from '../api/client'
import { fmtAmount, fmtTime, decisionClass, scoreColor, shortId } from '../lib/utils'

const SSE_TOKEN = import.meta.env.VITE_API_TOKEN || 'dev_token_fraudshield_local_only'

const MAX_FEED = 50

function StatCard({ label, value, sub, color = 'text-slate-100' }) {
  return (
    <div className="panel flex flex-col gap-1">
      <div className={`stat-num ${color}`}>{value ?? '—'}</div>
      <div className="stat-lbl">{label}</div>
      {sub && <div className="text-xs text-slate-500 mt-0.5">{sub}</div>}
    </div>
  )
}

const CHANNEL_STYLES = {
  online: 'bg-blue-900/40 text-blue-300 border-blue-700/40',
  upi:    'bg-purple-900/40 text-purple-300 border-purple-700/40',
  pos:    'bg-emerald-900/40 text-emerald-300 border-emerald-700/40',
  atm:    'bg-yellow-900/40 text-yellow-300 border-yellow-700/40',
  nfc:    'bg-slate-800 text-slate-400 border-slate-600',
}

function ChannelBadge({ channel }) {
  const cls = CHANNEL_STYLES[channel] ?? 'bg-slate-800 text-slate-400 border-slate-600'
  return (
    <span className={`px-1.5 py-0.5 rounded text-xs font-mono border ${cls}`}>
      {channel || '—'}
    </span>
  )
}

function ScoreBar({ score }) {
  const color = score >= 0.85 ? 'bg-block' : score >= 0.5 ? 'bg-review' : 'bg-allow'
  return (
    <div className="flex items-center gap-2">
      <div className="w-20 h-1.5 bg-muted rounded-full overflow-hidden">
        <div className={`h-full ${color} rounded-full`} style={{ width: `${score * 100}%` }} />
      </div>
      <span className="font-mono text-xs" style={{ color: scoreColor(score) }}>
        {score?.toFixed(3)}
      </span>
    </div>
  )
}

export default function LiveFeed() {
  const [feed,   setFeed]   = useState([])
  const [stats,  setStats]  = useState(null)
  const [paused, setPaused] = useState(false)
  const pausedRef = useRef(false)

  useEffect(() => { pausedRef.current = paused }, [paused])

  // Load recent history on mount so the table isn't empty when you first open the page
  useEffect(() => {
    getTransactions({ limit: 50, order: 'desc' })
      .then(data => {
        const txns = Array.isArray(data) ? data : (data.transactions || data.items || [])
        setFeed(txns.slice(0, MAX_FEED))
      })
      .catch(() => {})
  }, [])

  // SSE — push new transactions instantly as they are scored
  const [connected, setConnected] = useState(false)
  useEffect(() => {
    const es = new EventSource(`/api/v1/stream?token=${SSE_TOKEN}`)

    es.onopen = () => setConnected(true)

    es.onmessage = (e) => {
      if (e.data.startsWith(':')) return   // keepalive comment
      if (pausedRef.current) return
      try {
        const txn = JSON.parse(e.data)
        setFeed(old => {
          const seen = new Set(old.map(t => t.transaction_id || t.id))
          if (seen.has(txn.transaction_id)) return old
          return [txn, ...old].slice(0, MAX_FEED)
        })
      } catch (_) {}
    }

    es.onerror = () => setConnected(false)

    return () => { es.close(); setConnected(false) }
  }, [])

  // Stats still polled — no need for SSE on aggregate numbers
  useEffect(() => {
    const fetchStats = () => getDashboardStats().then(setStats).catch(() => {})
    fetchStats()
    const t = setInterval(fetchStats, 10000)
    return () => clearInterval(t)
  }, [])

  const blocked = feed.filter(t => t.decision === 'block').length
  const reviews = feed.filter(t => t.decision === 'review').length
  const allowed = feed.filter(t => t.decision === 'allow').length

  return (
    <div className="p-6 space-y-5">

      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-lg font-medium text-slate-100 flex items-center gap-2">
            <Activity size={18} className="text-allow-text" />
            Live Transaction Feed
          </h1>
          <p className="text-xs text-slate-500 mt-0.5">
            <span className={connected ? 'text-allow-text' : 'text-slate-600'}>
              {connected ? '● Live' : '○ Connecting…'}
            </span>
            {' '}· {feed.length} transactions
          </p>
        </div>
        <button
          onClick={() => setPaused(p => !p)}
          className={`px-3 py-1.5 rounded-lg text-xs font-medium border transition-colors ${
            paused
              ? 'bg-review-dim border-review/40 text-review-text'
              : 'bg-surface border-border text-slate-400 hover:text-slate-200'
          }`}
        >
          {paused ? '▶ Resume' : '⏸ Pause'}
        </button>
      </div>

      {/* Stat cards */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <StatCard
          label="Total scored (24h)"
          value={stats?.total_24h?.toLocaleString() ?? feed.length}
          color="text-slate-100"
        />
        <StatCard
          label="Blocked"
          value={stats?.blocked_24h ?? blocked}
          sub={stats ? `${((stats.blocked_24h / Math.max(stats.total_24h,1))*100).toFixed(1)}% block rate` : null}
          color="text-block-text"
        />
        <StatCard
          label="In review"
          value={stats?.review_24h ?? reviews}
          color="text-review-text"
        />
        <StatCard
          label="Allowed"
          value={stats?.allowed_24h ?? allowed}
          color="text-allow-text"
        />
      </div>

      {/* Feed table */}
      <div className="panel p-0 overflow-hidden">
        <div className="flex items-center justify-between px-4 py-3 border-b border-border">
          <span className="text-xs font-medium text-slate-400 uppercase tracking-widest">
            Transaction stream
          </span>
          {paused && (
            <span className="text-xs text-review-text animate-pulse_soft">● Paused</span>
          )}
        </div>

        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border/50 text-xs text-slate-500 uppercase tracking-wider">
                <th className="tcell text-left font-medium">Time</th>
                <th className="tcell text-left font-medium">Txn ID</th>
                <th className="tcell text-left font-medium">User</th>
                <th className="tcell text-left font-medium">Merchant</th>
                <th className="tcell text-left font-medium">Channel</th>
                <th className="tcell text-left font-medium">IP</th>
                <th className="tcell text-right font-medium">Amount</th>
                <th className="tcell text-left font-medium">Score</th>
                <th className="tcell text-left font-medium">Decision</th>
              </tr>
            </thead>
            <tbody>
              {feed.length === 0 && (
                <tr>
                  <td colSpan={9} className="tcell text-center text-slate-600 py-12">
                    No transactions yet — run scripts/simulate_live.py to start the feed
                  </td>
                </tr>
              )}
              {feed.map((txn, i) => (
                <tr key={txn.transaction_id || txn.id || i}
                    className={`trow ${i === 0 && !paused ? 'animate-slide_in' : ''}`}>
                  <td className="tcell font-mono text-xs text-slate-500">
                    {fmtTime(txn.scored_at || txn.created_at)}
                  </td>
                  <td className="tcell font-mono text-xs text-slate-400">
                    {shortId(txn.transaction_id || txn.id)}
                  </td>
                  <td className="tcell font-mono text-xs text-slate-300">
                    {shortId(txn.user_id)}
                  </td>
                  <td className="tcell text-xs text-slate-400">
                    {txn.merchant_id}
                  </td>
                  <td className="tcell">
                    <ChannelBadge channel={txn.channel} />
                  </td>
                  <td className="tcell font-mono text-xs text-slate-500">
                    {txn.ip_address || '—'}
                  </td>
                  <td className="tcell font-mono text-xs text-right text-slate-200">
                    {fmtAmount(txn.amount, txn.currency)}
                  </td>
                  <td className="tcell">
                    <ScoreBar score={txn.fraud_probability ?? txn.score ?? 0} />
                  </td>
                  <td className="tcell">
                    <span className={decisionClass(txn.decision)}>
                      {txn.decision?.toUpperCase()}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}
