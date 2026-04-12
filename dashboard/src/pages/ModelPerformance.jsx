import { BarChart2, TrendingUp, Award } from 'lucide-react'
import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, RadarChart, PolarGrid,
  PolarAngleAxis, Radar, Legend,
} from 'recharts'
import { usePolling }    from '../hooks/usePolling'
import { getModelVersions, getModelInfo } from '../api/client'

function MetricGauge({ label, value, color }) {
  const pct = value * 100
  return (
    <div className="panel text-center space-y-2">
      <div className="text-xs text-slate-500 uppercase tracking-widest">{label}</div>
      <div className="relative w-24 h-24 mx-auto">
        <svg viewBox="0 0 100 100" className="rotate-[-90deg]">
          <circle cx="50" cy="50" r="40" fill="none" stroke="#1e2a38" strokeWidth="10"/>
          <circle
            cx="50" cy="50" r="40" fill="none"
            stroke={color} strokeWidth="10"
            strokeDasharray={`${pct * 2.51} 251`}
            strokeLinecap="round"
          />
        </svg>
        <div className="absolute inset-0 flex items-center justify-center">
          <span className="font-mono text-base font-medium" style={{ color }}>
            {(value * 100).toFixed(1)}%
          </span>
        </div>
      </div>
    </div>
  )
}

const CustomTooltip = ({ active, payload, label }) => {
  if (!active || !payload?.length) return null
  return (
    <div className="bg-surface border border-border rounded-lg p-3 text-xs space-y-1">
      <div className="text-slate-400 font-mono">{label}</div>
      {payload.map(p => (
        <div key={p.name} style={{ color: p.color }}>
          {p.name}: {(p.value * 100).toFixed(2)}%
        </div>
      ))}
    </div>
  )
}

export default function ModelPerformance() {
  const { data: versions, loading: vLoading } = usePolling(getModelVersions, 30000)
  const { data: current,  loading: cLoading } = usePolling(getModelInfo, 30000)

  const versionList = Array.isArray(versions) ? versions : (versions?.versions || [])
  const champion    = versionList.find(v => v.is_champion) || current

  const radarData = champion ? [
    { metric: 'AUC-ROC',   value: champion.auc_roc   || 0 },
    { metric: 'Precision', value: champion.precision  || 0 },
    { metric: 'Recall',    value: champion.recall     || 0 },
    { metric: 'F1',        value: champion.f1_score   || 0 },
  ] : []

  const barData = versionList.slice(-6).map(v => ({
    version:   v.version?.slice(-8) || v.version,
    auc:       v.auc_roc,
    precision: v.precision,
    recall:    v.recall,
    champion:  v.is_champion,
  }))

  const loading = vLoading && cLoading

  return (
    <div className="p-6 space-y-5">
      <div>
        <h1 className="text-lg font-medium text-slate-100 flex items-center gap-2">
          <BarChart2 size={18} className="text-allow-text" />
          Model Performance
        </h1>
        <p className="text-xs text-slate-500 mt-0.5">Champion model metrics and version history</p>
      </div>

      {loading && (
        <div className="text-sm text-slate-500">Loading model data…</div>
      )}

      {champion && (
        <>
          {/* Champion banner */}
          <div className="panel border-allow/30 border bg-allow-dim/20 flex items-center gap-3">
            <Award size={18} className="text-allow-text flex-shrink-0" />
            <div>
              <div className="text-sm font-medium text-allow-text">
                Champion: {champion.version}
              </div>
              <div className="text-xs text-slate-500 mt-0.5">
                Trained on {champion.training_rows?.toLocaleString()} rows ·
                {' '}Threshold: {champion.threshold} ·
                {' '}Fraud rate: {((champion.fraud_rate || 0)*100).toFixed(1)}%
              </div>
            </div>
          </div>

          {/* Metric gauges */}
          <div className="grid grid-cols-4 gap-3">
            <MetricGauge label="AUC-ROC"   value={champion.auc_roc   || 0} color="#5de4b4" />
            <MetricGauge label="Precision" value={champion.precision  || 0} color="#7bb8f8" />
            <MetricGauge label="Recall"    value={champion.recall     || 0} color="#fbbf24" />
            <MetricGauge label="F1 Score"  value={champion.f1_score   || 0} color="#c084fc" />
          </div>
        </>
      )}

      <div className="grid grid-cols-2 gap-4">
        {/* Radar chart */}
        {radarData.length > 0 && (
          <div className="panel">
            <div className="text-xs text-slate-500 uppercase tracking-widest mb-4">
              Champion metrics
            </div>
            <ResponsiveContainer width="100%" height={200}>
              <RadarChart data={radarData}>
                <PolarGrid stroke="#1e2a38" />
                <PolarAngleAxis
                  dataKey="metric"
                  tick={{ fill: '#64748b', fontSize: 11, fontFamily: 'DM Sans' }}
                />
                <Radar
                  name="Score"
                  dataKey="value"
                  stroke="#0d9e75"
                  fill="#0d9e75"
                  fillOpacity={0.2}
                />
              </RadarChart>
            </ResponsiveContainer>
          </div>
        )}

        {/* Version comparison bar chart */}
        {barData.length > 0 && (
          <div className="panel">
            <div className="text-xs text-slate-500 uppercase tracking-widest mb-4">
              Version history
            </div>
            <ResponsiveContainer width="100%" height={200}>
              <BarChart data={barData} barSize={16}>
                <CartesianGrid strokeDasharray="3 3" stroke="#1e2a38" vertical={false} />
                <XAxis
                  dataKey="version"
                  tick={{ fill: '#64748b', fontSize: 10, fontFamily: 'JetBrains Mono' }}
                  axisLine={false} tickLine={false}
                />
                <YAxis
                  domain={[0.9, 1]}
                  tick={{ fill: '#64748b', fontSize: 10 }}
                  axisLine={false} tickLine={false}
                  tickFormatter={v => `${(v*100).toFixed(0)}%`}
                />
                <Tooltip content={<CustomTooltip />} cursor={{ fill: '#ffffff05' }} />
                <Bar dataKey="auc"       name="AUC"       fill="#5de4b4" radius={[2,2,0,0]} />
                <Bar dataKey="precision" name="Precision" fill="#7bb8f8" radius={[2,2,0,0]} />
                <Bar dataKey="recall"    name="Recall"    fill="#fbbf24" radius={[2,2,0,0]} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        )}
      </div>

      {/* Version table */}
      {versionList.length > 0 && (
        <div className="panel p-0 overflow-hidden">
          <div className="px-4 py-3 border-b border-border">
            <span className="text-xs text-slate-500 uppercase tracking-widest">All versions</span>
          </div>
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border/50 text-xs text-slate-500 uppercase tracking-wider">
                <th className="tcell text-left font-medium">Version</th>
                <th className="tcell text-right font-medium">AUC</th>
                <th className="tcell text-right font-medium">Precision</th>
                <th className="tcell text-right font-medium">Recall</th>
                <th className="tcell text-right font-medium">F1</th>
                <th className="tcell text-right font-medium">Threshold</th>
                <th className="tcell text-left font-medium">Status</th>
              </tr>
            </thead>
            <tbody>
              {versionList.map(v => (
                <tr key={v.version} className="trow">
                  <td className="tcell font-mono text-xs text-slate-300">{v.version}</td>
                  <td className="tcell font-mono text-xs text-right text-slate-200">{(v.auc_roc*100).toFixed(2)}%</td>
                  <td className="tcell font-mono text-xs text-right text-slate-200">{(v.precision*100).toFixed(2)}%</td>
                  <td className="tcell font-mono text-xs text-right text-slate-200">{(v.recall*100).toFixed(2)}%</td>
                  <td className="tcell font-mono text-xs text-right text-slate-200">{(v.f1_score*100).toFixed(2)}%</td>
                  <td className="tcell font-mono text-xs text-right text-slate-300">{v.threshold}</td>
                  <td className="tcell">
                    {v.is_champion
                      ? <span className="badge-allow">CHAMPION</span>
                      : <span className="text-xs text-slate-600">—</span>
                    }
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {!loading && versionList.length === 0 && (
        <div className="panel text-center py-10 text-slate-600">
          <BarChart2 size={32} className="mx-auto mb-2 opacity-30" />
          <div className="text-sm">No model versions found — run python scripts/train_model.py first</div>
        </div>
      )}
    </div>
  )
}
