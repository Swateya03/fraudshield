import { Routes, Route, Navigate } from 'react-router-dom'
import Layout          from './components/Layout'
import LiveFeed        from './pages/LiveFeed'
import Explorer        from './pages/Explorer'
import ScoreExplainer  from './pages/ScoreExplainer'
import UserRiskManager from './pages/UserRiskManager'
import ModelPerformance from './pages/ModelPerformance'
import DriftMonitor    from './pages/DriftMonitor'
export default function App() {
  return (
    <Routes>
      <Route path="/" element={<Layout />}>
        <Route index              element={<LiveFeed />}         />
        <Route path="explorer"   element={<Explorer />}         />
        <Route path="explainer"  element={<ScoreExplainer />}   />
        <Route path="users"      element={<UserRiskManager />}  />
        <Route path="model"      element={<ModelPerformance />} />
        <Route path="drift"      element={<DriftMonitor />}     />
        <Route path="*"          element={<Navigate to="/" />}  />
      </Route>
    </Routes>
  )
}
