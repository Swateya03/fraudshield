import { TrendingUp, AlertTriangle, CheckCircle, RefreshCw } from 'lucide-react'
import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, ReferenceLine,
} from 'recharts'
import { usePolling }  from '../hooks/usePolling'
import { getDriftReport } from '../api/client'
import { psiStatus, fmtDateTime } from '../lib/utils'

const CustomTooltip = ({ active, payload, label }) => {
  if (!active || !payload?.length) return null
  const psi = payload[0]?.value
  const st  = psiStatus(psi)
  return (
    <div className="bg-surface border border-border rounded-lg p-3 text-xs space-y-1">
      <div className="text-slate-300 font-medium">{label}</div>
      <div className="font-mono">PSI: {psi?.toFixed(4)}</div>
      <div className={st.color}>{st.label}</div>
    </div>
  )
}

function PSIBar({ feature, psi }) {
  const st  = psiStatus(psi ?? 0)
  const pct = Math.min((psi ?? 0) / 0.5 * 100, 100)
  const barColor = psi >= 0.25 ? '#d84040' : psi >= 0.10 ? '#c47f12' : '#0d9e75'

  return (
    <div className="flex items-center gap-4 py-2.5 border-b border-border/40 last:border-0">
      <span className="font-mono text-xs text-slate-400 w-40 flex-shrink-0">{feature}</span>
      <div className="flex-1 h-2 bg-muted rounded-full overflow-hidden">
        <div
          className="h-full rounded-full transition-all duration-700"
          style={{ width: `${pct}%`, background: barColor }}
        />
      </div>
      <span className="font-mono text-xs w-16 text-right" style={{ color: barColor }}>
        {psi?.toFixed(4) ?? 'N/A'}
      </span>
      <span className={`text-xs w-16 text-right ${st.color}`}>{st.label}</span>
    </div>
  )
}

export default function DriftMonitor() {
  const { data, loading, error, refetch } = usePolling(getDriftReport, 30000)

  const psiMap     = data?.psi_by_feature || {}
  const features   = Object.entries(psiMap)
  const maxPsi     = data?.max_psi ?? 0
  const rec        = data?.recommendation ?? 'UNKNOWN'
  const threshold  = data?.psi_threshold ?? 0.25

  const recColor = rec === 'RETRAIN_REQUIRED'
    ? 'text-block-text bg-block-dim border-block/30'
    : rec === 'MONITOR'
    ? 'text-review-text bg-review-dim border-review/30'
    : 'text-allow-text bg-allow-dim border-allow/30'

  const barData = features.map(([f, v]) => ({ feature: f.replace('_', ' '), psi: v ?? 0 }))

  return (
    <div className="p-6 space-y-5">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-lg font-medium text-slate-100 flex items-center gap-2">
            <TrendingUp size={18} className="text-allow-text" />
            Drift Monitor
          </h1>
          <p className="text-xs text-slate-500 mt-0.5">
            PSI (Population Stability Index) per feature ·
            {data ? ` computed ${fmtDateTime(data.computed_at)}` : ''}
          </p>
        </div>
        <button
          onClick={refetch}
          className="flex items-center gap-1.5 px-3 py-1.5 bg-surface border border-border rounded-lg text-xs text-slate-400 hover:text-slate-200 transition-colors"
        >
          <RefreshCw size={12} />
          Refresh
        </button>
      </div>

      {loading && <div className="text-sm text-slate-500">Computing drift report…</div>}
      {error   && (
        <div className="flex items-center gap-2 text-sm text-block-text bg-block-dim border border-block/30 rounded-lg p-3">
          <AlertTriangle size={14} />
          {error} — make sure fraud_api/main.py is running
        </div>
      )}

      {data && (
        <>
          {/* Recommendation banner */}
          <div className={`panel border flex items-start gap-3 ${recColor}`}>
            {rec === 'RETRAIN_REQUIRED'
              ? <AlertTriangle size={18} className="flex-shrink-0 mt-0.5" />
              : <CheckCircle  size={18} className="flex-shrink-0 mt-0.5" />
            }
            <div>
              <div className="font-medium text-sm">{rec.replace('_', ' ')}</div>
              <div className="text-xs mt-0.5 opacity-80">
                Max PSI: {maxPsi.toFixed(4)} · threshold: {threshold} ·
                {' '}{data.baseline_window} vs {data.current_window}
                {data.segment ? ` · segment: ${data.segment}` : ''}
              </div>
              {rec === 'RETRAIN_REQUIRED' && (
                <div className="text-xs mt-1 font-mono opacity-80">
                  Run: python scripts/train_model.py
                </div>
              )}
            </div>
          </div>

          {/* Windows */}
          <div className="grid grid-cols-2 gap-3">
            <div className="panel text-center">
              <div className="stat-num">{data.baseline_rows?.toLocaleString()}</div>
              <div className="stat-lbl">Baseline transactions</div>
              <div className="text-xs text-slate-600 mt-1">{data.baseline_window}</div>
            </div>
            <div className="panel text-center">
              <div className="stat-num">{data.current_rows?.toLocaleString()}</div>
              <div className="stat-lbl">Current transactions</div>
              <div className="text-xs text-slate-600 mt-1">{data.current_window}</div>
            </div>
          </div>

          {/* Bar chart */}
          {barData.length > 0 && (
            <div className="panel">
              <div className="text-xs text-slate-500 uppercase tracking-widest mb-4">PSI by feature</div>
              <ResponsiveContainer width="100%" height={200}>
                <BarChart data={barData} barSize={28} margin={{ left: -10 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#1e2a38" vertical={false} />
                  <XAxis
                    dataKey="feature"
                    tick={{ fill: '#64748b', fontSize: 10, fontFamily: 'JetBrains Mono' }}
                    axisLine={false} tickLine={false}
                  />
                  <YAxis
                    tick={{ fill: '#64748b', fontSize: 10 }}
                    axisLine={false} tickLine={false}
                  />
                  <Tooltip content={<CustomTooltip />} cursor={{ fill: '#ffffff05' }} />
                  <ReferenceLine y={threshold} stroke="#d84040" strokeDasharray="4 2"
                    label={{ value: 'threshold', fill: '#f87171', fontSize: 10 }} />
                  <Bar
                    dataKey="psi"
                    radius={[3, 3, 0, 0]}
                    fill="#0d9e75"
                  />
                </BarChart>
              </ResponsiveContainer>
            </div>
          )}

          {/* Feature detail */}
          <div className="panel">
            <div className="text-xs text-slate-500 uppercase tracking-widest mb-3">Feature detail</div>
            {features.map(([feat, psi]) => (
              <PSIBar key={feat} feature={feat} psi={psi} />
            ))}
          </div>

          {/* Explanation */}
          <div className="panel bg-surface/50 border-muted">
            <div className="text-xs text-slate-500 uppercase tracking-widest mb-2">PSI interpretation</div>
            <div className="space-y-1 text-xs text-slate-500">
              <div className="flex gap-3">
                <span className="text-allow-text font-mono w-16">{'< 0.10'}</span>
                <span>Stable — no action needed</span>
              </div>
              <div className="flex gap-3">
                <span className="text-review-text font-mono w-16">0.10–0.25</span>
                <span>Warning — monitor closely, slight distribution shift</span>
              </div>
              <div className="flex gap-3">
                <span className="text-block-text font-mono w-16">{'> 0.25'}</span>
                <span>Drift detected — retrain model on recent data</span>
              </div>
            </div>
          </div>
        </>
      )}
    </div>
  )
}
