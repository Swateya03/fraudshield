import { NavLink, Outlet } from 'react-router-dom'
import {
  Activity, Search, Zap, Users, BarChart2,
  TrendingUp, Key, Shield, Circle,
} from 'lucide-react'
import { usePolling } from '../hooks/usePolling'
import { getHealth }  from '../api/client'

const NAV = [
  { to: '/',          icon: Activity,   label: 'Live Feed'        },
  { to: '/explorer',  icon: Search,     label: 'Explorer'         },
  { to: '/explainer', icon: Zap,        label: 'Score Explainer'  },
  { to: '/users',     icon: Users,      label: 'Risk Manager'     },
  { to: '/model',     icon: BarChart2,  label: 'Model Performance'},
  { to: '/drift',     icon: TrendingUp, label: 'Drift Monitor'    },
  { to: '/keys',      icon: Key,        label: 'API Keys'         },
]

export default function Layout() {
  const { data: health } = usePolling(getHealth, 5000)
  const online = !!health

  return (
    <div className="flex h-screen overflow-hidden bg-canvas">

      {/* ── Sidebar ──────────────────────────────────────────────── */}
      <aside className="w-56 flex-shrink-0 flex flex-col border-r border-border bg-surface">

        {/* Logo */}
        <div className="flex items-center gap-2.5 px-5 py-4 border-b border-border">
          <Shield size={20} className="text-allow-text" />
          <span className="font-sans font-semibold text-slate-100 tracking-tight">
            FraudShield
          </span>
        </div>

        {/* Nav */}
        <nav className="flex-1 py-3 space-y-0.5 px-2 overflow-y-auto">
          {NAV.map(({ to, icon: Icon, label }) => (
            <NavLink
              key={to}
              to={to}
              end={to === '/'}
              className={({ isActive }) =>
                `flex items-center gap-2.5 px-3 py-2 rounded-lg text-sm transition-colors duration-100 ${
                  isActive
                    ? 'bg-allow-dim text-allow-text font-medium'
                    : 'text-slate-400 hover:text-slate-200 hover:bg-surface'
                }`
              }
            >
              <Icon size={15} />
              {label}
            </NavLink>
          ))}
        </nav>

        {/* Status footer */}
        <div className="px-4 py-3 border-t border-border">
          <div className="flex items-center gap-2 text-xs">
            <Circle
              size={7}
              className={online ? 'fill-allow text-allow animate-pulse_soft' : 'fill-slate-600 text-slate-600'}
            />
            <span className={online ? 'text-allow-text' : 'text-slate-500'}>
              {online ? `API · ${health?.strategy?.replace('_',' ')}` : 'API offline'}
            </span>
          </div>
        </div>
      </aside>

      {/* ── Main content ─────────────────────────────────────────── */}
      <main className="flex-1 overflow-y-auto">
        <Outlet />
      </main>
    </div>
  )
}
