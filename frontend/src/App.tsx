import { Routes, Route, NavLink } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { api } from './api'
import Dashboard from './pages/Dashboard'
import ProjectsPage from './pages/Projects'
import ProjectDetail from './pages/ProjectDetail'
import AnalyticsPage from './pages/Analytics'
import RunsPage from './pages/Runs'

function NavItem({ to, label }: { to: string; label: string }) {
  return (
    <NavLink
      to={to}
      end={to === '/'}
      className={({ isActive }) =>
        `px-3 py-2 rounded-md text-sm font-medium transition-colors ${
          isActive
            ? 'bg-sky-700 text-white'
            : 'text-sky-100 hover:bg-sky-700/60'
        }`
      }
    >
      {label}
    </NavLink>
  )
}

export default function App() {
  const { data: budget } = useQuery({ queryKey: ['budget'], queryFn: api.budget.status, refetchInterval: 30_000 })
  const pct = budget?.percent_used ?? 0
  const barColor = pct > 80 ? 'bg-red-400' : pct > 60 ? 'bg-yellow-400' : 'bg-green-400'

  return (
    <div className="min-h-screen bg-gray-950 text-gray-100 flex flex-col">
      <nav className="bg-sky-800 shadow-lg">
        <div className="max-w-7xl mx-auto px-4 py-3 flex items-center gap-6">
          <span className="text-white font-bold text-lg tracking-tight">Chloe</span>
          <div className="flex gap-1">
            <NavItem to="/" label="Dashboard" />
            <NavItem to="/projects" label="Projects" />
            <NavItem to="/analytics" label="Analytics" />
            <NavItem to="/runs" label="Runs" />
          </div>
          {budget && (
            <div className="ml-auto flex items-center gap-3 text-sm text-sky-100">
              <span>{budget.tokens_remaining.toLocaleString()} tokens left</span>
              <div className="w-32 h-2 bg-sky-900 rounded-full overflow-hidden">
                <div className={`h-full rounded-full transition-all ${barColor}`} style={{ width: `${Math.min(pct, 100)}%` }} />
              </div>
              <span>{pct.toFixed(0)}%</span>
            </div>
          )}
        </div>
      </nav>
      <main className="flex-1 max-w-7xl w-full mx-auto px-4 py-6">
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/projects" element={<ProjectsPage />} />
          <Route path="/projects/:id" element={<ProjectDetail />} />
          <Route path="/analytics" element={<AnalyticsPage />} />
          <Route path="/runs" element={<RunsPage />} />
        </Routes>
      </main>
    </div>
  )
}
