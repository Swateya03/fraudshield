import { useState } from 'react'
import { Key, Copy, Eye, EyeOff, Plus, Trash2, CheckCircle } from 'lucide-react'

const INITIAL_KEYS = [
  {
    id: 'key_001',
    name: 'Production API',
    key: 'dev_token_fraudshield_local_only',
    created: '2026-01-15',
    lastUsed: 'Just now',
    requests: 1_284,
    active: true,
  },
  {
    id: 'key_002',
    name: 'CI / GitHub Actions',
    key: 'test_token_123',
    created: '2026-02-03',
    lastUsed: '2 hours ago',
    requests: 342,
    active: true,
  },
]

function KeyRow({ apiKey, onDelete }) {
  const [visible, setVisible] = useState(false)
  const [copied,  setCopied]  = useState(false)

  function copy() {
    navigator.clipboard.writeText(apiKey.key)
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }

  const masked = apiKey.key.slice(0, 8) + '••••••••••••••••' + apiKey.key.slice(-4)

  return (
    <div className="panel space-y-3">
      <div className="flex items-start justify-between">
        <div>
          <div className="flex items-center gap-2">
            <span className="text-sm font-medium text-slate-200">{apiKey.name}</span>
            {apiKey.active
              ? <span className="badge-allow">ACTIVE</span>
              : <span className="text-xs text-slate-600 bg-muted px-2 py-0.5 rounded">INACTIVE</span>
            }
          </div>
          <div className="text-xs text-slate-500 mt-0.5">
            Created {apiKey.created} · Last used {apiKey.lastUsed} · {apiKey.requests.toLocaleString()} requests
          </div>
        </div>
        <button
          onClick={() => onDelete(apiKey.id)}
          className="p-1.5 text-slate-600 hover:text-block-text rounded transition-colors"
        >
          <Trash2 size={14} />
        </button>
      </div>

      <div className="flex items-center gap-2 bg-surface rounded-lg px-3 py-2 border border-border">
        <Key size={12} className="text-slate-600 flex-shrink-0" />
        <code className="flex-1 font-mono text-xs text-slate-400 truncate">
          {visible ? apiKey.key : masked}
        </code>
        <button onClick={() => setVisible(v => !v)}
          className="text-slate-600 hover:text-slate-300 transition-colors">
          {visible ? <EyeOff size={13} /> : <Eye size={13} />}
        </button>
        <button onClick={copy}
          className={`transition-colors ${copied ? 'text-allow-text' : 'text-slate-600 hover:text-slate-300'}`}>
          {copied ? <CheckCircle size={13} /> : <Copy size={13} />}
        </button>
      </div>
    </div>
  )
}

export default function ApiKeys() {
  const [keys,    setKeys]    = useState(INITIAL_KEYS)
  const [newName, setNewName] = useState('')
  const [creating, setCreating] = useState(false)
  const [showForm, setShowForm] = useState(false)

  function deleteKey(id) {
    setKeys(k => k.filter(x => x.id !== id))
  }

  function createKey() {
    if (!newName.trim()) return
    setCreating(true)
    setTimeout(() => {
      const fake = `fs_live_${Math.random().toString(36).slice(2, 18)}`
      setKeys(k => [...k, {
        id:       `key_${Date.now()}`,
        name:     newName.trim(),
        key:      fake,
        created:  new Date().toISOString().slice(0, 10),
        lastUsed: 'Never',
        requests: 0,
        active:   true,
      }])
      setNewName('')
      setCreating(false)
      setShowForm(false)
    }, 600)
  }

  return (
    <div className="p-6 space-y-5">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-lg font-medium text-slate-100 flex items-center gap-2">
            <Key size={18} className="text-allow-text" />
            API Keys
          </h1>
          <p className="text-xs text-slate-500 mt-0.5">
            Manage Bearer tokens for the scoring API
          </p>
        </div>
        <button
          onClick={() => setShowForm(s => !s)}
          className="flex items-center gap-1.5 px-3 py-1.5 bg-allow text-canvas rounded-lg text-sm font-medium hover:bg-allow/90 transition-colors"
        >
          <Plus size={14} />
          New key
        </button>
      </div>

      {/* Create form */}
      {showForm && (
        <div className="panel border-allow/30 border animate-slide_in space-y-3">
          <div className="text-xs text-slate-500 uppercase tracking-widest">Create new API key</div>
          <div>
            <label className="block text-xs text-slate-500 mb-1">Key name</label>
            <input
              className="w-full bg-surface border border-border rounded-lg px-3 py-2 text-sm text-slate-200 placeholder:text-slate-600 focus:outline-none focus:border-allow/50"
              placeholder="e.g. Payment gateway integration"
              value={newName}
              onChange={e => setNewName(e.target.value)}
              onKeyDown={e => e.key === 'Enter' && createKey()}
              autoFocus
            />
          </div>
          <div className="flex gap-2">
            <button
              onClick={createKey}
              disabled={creating || !newName.trim()}
              className="px-4 py-2 bg-allow text-canvas rounded-lg text-sm font-medium hover:bg-allow/90 transition-colors disabled:opacity-50"
            >
              {creating ? 'Generating…' : 'Create key'}
            </button>
            <button
              onClick={() => setShowForm(false)}
              className="px-4 py-2 text-slate-400 hover:text-slate-200 text-sm transition-colors"
            >
              Cancel
            </button>
          </div>
        </div>
      )}

      {/* Key list */}
      <div className="space-y-3">
        {keys.map(k => (
          <KeyRow key={k.id} apiKey={k} onDelete={deleteKey} />
        ))}
      </div>

      {/* Usage note */}
      <div className="panel bg-surface/40 border-muted space-y-2">
        <div className="text-xs text-slate-500 uppercase tracking-widest">Usage</div>
        <div className="font-mono text-xs text-slate-400 bg-canvas rounded-lg p-3 space-y-1">
          <div><span className="text-slate-600"># Header format</span></div>
          <div>Authorization: Bearer {'<your-key>'}</div>
          <div className="mt-2"><span className="text-slate-600"># Example curl (use Invoke-WebRequest on Windows)</span></div>
          <div className="text-allow-text">curl -H "Authorization: Bearer dev_token..." \</div>
          <div className="text-allow-text pl-4">http://localhost:8000/v1/transactions/score</div>
        </div>
      </div>
    </div>
  )
}
