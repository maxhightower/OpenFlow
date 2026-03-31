import { useQuery } from '@tanstack/react-query'
import { api } from '../api'
import { Link } from 'react-router-dom'

function StatCard({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="bg-gray-900 rounded-xl p-5 border border-gray-800">
      <p className="text-sm text-gray-400">{label}</p>
      <p className="text-2xl font-bold text-white mt-1">{value}</p>
      {sub && <p className="text-xs text-gray-500 mt-1">{sub}</p>}
    </div>
  )
}

export default function Dashboard() {
  const { data: budget } = useQuery({ queryKey: ['budget'], queryFn: api.budget.status, refetchInterval: 30_000 })
  const { data: burn } = useQuery({ queryKey: ['burn-rate'], queryFn: api.analytics.burnRate, refetchInterval: 30_000 })
  const { data: projects } = useQuery({ queryKey: ['projects'], queryFn: api.projects.list })
  const { data: runs } = useQuery({ queryKey: ['runs'], queryFn: () => api.runs.list(5) })

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold text-white">Dashboard</h1>

      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <StatCard
          label="Tokens Used"
          value={budget?.tokens_used.toLocaleString() ?? '—'}
          sub={`of ${budget?.token_budget.toLocaleString() ?? '—'} budget`}
        />
        <StatCard
          label="Tokens Remaining"
          value={budget?.tokens_remaining.toLocaleString() ?? '—'}
          sub={budget?.hours_remaining ? `~${budget.hours_remaining.toFixed(1)}h left` : undefined}
        />
        <StatCard
          label="Burn Rate"
          value={burn ? `${burn.tokens_per_hour.toLocaleString()}/hr` : '—'}
          sub={burn ? `$${burn.cost_per_hour.toFixed(4)}/hr` : undefined}
        />
        <StatCard
          label="Projects"
          value={projects?.length.toString() ?? '—'}
          sub="active"
        />
      </div>

      <div className="grid md:grid-cols-2 gap-6">
        <div className="bg-gray-900 rounded-xl border border-gray-800 p-5">
          <div className="flex items-center justify-between mb-4">
            <h2 className="font-semibold text-white">Recent Projects</h2>
            <Link to="/projects" className="text-sky-400 text-sm hover:text-sky-300">View all →</Link>
          </div>
          {projects?.length === 0 && <p className="text-gray-500 text-sm">No projects yet.</p>}
          <ul className="space-y-2">
            {projects?.slice(0, 5).map(p => (
              <li key={p.project_id}>
                <Link
                  to={`/projects/${p.project_id}`}
                  className="flex items-center justify-between rounded-lg px-3 py-2 hover:bg-gray-800 transition-colors"
                >
                  <span className="text-sm text-gray-200">{p.name}</span>
                  <span className="text-xs text-gray-500">{p.dag.tasks?.length ?? 0} tasks</span>
                </Link>
              </li>
            ))}
          </ul>
        </div>

        <div className="bg-gray-900 rounded-xl border border-gray-800 p-5">
          <div className="flex items-center justify-between mb-4">
            <h2 className="font-semibold text-white">Recent Runs</h2>
            <Link to="/runs" className="text-sky-400 text-sm hover:text-sky-300">View all →</Link>
          </div>
          {runs?.length === 0 && <p className="text-gray-500 text-sm">No runs yet.</p>}
          <ul className="space-y-2">
            {runs?.map(r => (
              <li key={r.run_id} className="flex items-center justify-between rounded-lg px-3 py-2 bg-gray-800/50">
                <div>
                  <p className="text-sm text-gray-200">{r.task_name ?? r.task_id}</p>
                  <p className="text-xs text-gray-500">{new Date(r.started_at).toLocaleString()}</p>
                </div>
                <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${
                  r.status === 'done' ? 'bg-green-900 text-green-300' :
                  r.status === 'failed' ? 'bg-red-900 text-red-300' :
                  'bg-yellow-900 text-yellow-300'
                }`}>{r.status}</span>
              </li>
            ))}
          </ul>
        </div>
      </div>
    </div>
  )
}
