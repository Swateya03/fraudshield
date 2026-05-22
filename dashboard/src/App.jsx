import { lazy, Suspense } from 'react'
import { Routes, Route, Navigate } from 'react-router-dom'
import Layout        from './components/Layout'
import ErrorBoundary from './components/ErrorBoundary'

// Route-level code splitting — each page is its own JS chunk loaded on demand.
// The heavy pages (ModelPerformance, DriftMonitor) are never downloaded by
// analysts who only use LiveFeed.
const LiveFeed         = lazy(() => import('./pages/LiveFeed'))
const Investigate      = lazy(() => import('./pages/Investigate'))
const ScoreExplainer   = lazy(() => import('./pages/ScoreExplainer'))
const ModelPerformance = lazy(() => import('./pages/ModelPerformance'))
const DriftMonitor     = lazy(() => import('./pages/DriftMonitor'))

const PageLoader = () => (
  <div className="flex items-center justify-center h-64 text-slate-400 text-sm">
    Loading…
  </div>
)

const wrap = (Page) => (
  <ErrorBoundary>
    <Suspense fallback={<PageLoader />}>
      <Page />
    </Suspense>
  </ErrorBoundary>
)

export default function App() {
  return (
    <Routes>
      <Route path="/" element={<Layout />}>
        <Route index              element={wrap(LiveFeed)}         />
        <Route path="investigate" element={wrap(Investigate)}      />
        <Route path="explainer"  element={wrap(ScoreExplainer)}   />
        <Route path="model"      element={wrap(ModelPerformance)} />
        <Route path="drift"      element={wrap(DriftMonitor)}     />
        <Route path="*"          element={<Navigate to="/" />}    />
      </Route>
    </Routes>
  )
}
