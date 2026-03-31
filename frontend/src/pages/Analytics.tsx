import { useQuery } from '@tanstack/react-query'
import { api } from '../api'
import { AreaChart, Area, BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, PieChart, Pie, Cell } from 'recharts'

const COLORS = ['#0ea5e9', '#8b5cf6', '#10b981', '#f59e0b', '#ef4444', '#ec4899']

export default function AnalyticsPage() {
  const { data: daily } = useQuery({ queryKey: ['daily'], queryFn: () => api.analytics.daily(30) })
  const { data: hourly } = useQuery({ queryKey: ['hourly'], queryFn: api.analytics.hourly })
  const { data: byProject } = useQuery({ queryKey: ['by-project'], queryFn: api.analytics.byProject })
  const { data: byModel } = useQuery({ queryKey: ['by-model'], queryFn: api.analytics.byModel })
  const { data: burn } = useQuery({ queryKey: ['burn-rate'], queryFn: api.analytics.burnRate })

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold text-white">Analytics</h1>

      {burn && (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          {[
            { label: 'Tokens/hr', value: burn.tokens_per_hour.toLocaleString() },
            { label: 'Cost/hr', value: `$${burn.cost_per_hour.toFixed(4)}` },
            { label: 'Total Tokens', value: (burn.total_input_tokens + burn.total_output_tokens).toLocaleString() },
            { label: 'Sessions', value: burn.session_count.toLocaleString() },
          ].map(s => (
            <div key={s.label} className="bg-gray-900 border border-gray-800 rounded-xl p-4">
              <p className="text-xs text-gray-400">{s.label}</p>
              <p className="text-xl font-bold text-white mt-1">{s.value}</p>
            </div>
          ))}
        </div>
      )}

      <div className="grid md:grid-cols-2 gap-6">
        <div className="bg-gray-900 border border-gray-800 rounded-xl p-5">
          <h2 className="font-semibold text-white mb-4">Daily Token Usage (30d)</h2>
          <ResponsiveContainer width="100%" height={200}>
            <AreaChart data={daily ?? []}>
              <XAxis dataKey="period" tick={{ fill: '#6b7280', fontSize: 11 }} tickFormatter={(v: string) => v.slice(5)} />
              <YAxis tick={{ fill: '#6b7280', fontSize: 11 }} />
              <Tooltip contentStyle={{ background: '#111827', border: '1px solid #374151', color: '#fff' }} />
              <Area type="monotone" dataKey="tokens" stroke="#0ea5e9" fill="#0ea5e933" />
            </AreaChart>
          </ResponsiveContainer>
        </div>

        <div className="bg-gray-900 border border-gray-800 rounded-xl p-5">
          <h2 className="font-semibold text-white mb-4">Hourly Usage Pattern</h2>
          <ResponsiveContainer width="100%" height={200}>
            <BarChart data={hourly ?? []}>
              <XAxis dataKey="period" tick={{ fill: '#6b7280', fontSize: 11 }} />
              <YAxis tick={{ fill: '#6b7280', fontSize: 11 }} />
              <Tooltip contentStyle={{ background: '#111827', border: '1px solid #374151', color: '#fff' }} />
              <Bar dataKey="tokens" fill="#8b5cf6" radius={[4, 4, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </div>

        <div className="bg-gray-900 border border-gray-800 rounded-xl p-5">
          <h2 className="font-semibold text-white mb-4">Cost by Project</h2>
          {(byProject?.length ?? 0) === 0 ? (
            <p className="text-gray-500 text-sm">No project data yet.</p>
          ) : (
            <ResponsiveContainer width="100%" height={200}>
              <PieChart>
                <Pie data={byProject} dataKey="cost" nameKey="project" cx="50%" cy="50%" outerRadius={80} label={(entry: any) => entry.project}>
                  {byProject?.map((_, i) => <Cell key={i} fill={COLORS[i % COLORS.length]} />)}
                </Pie>
                <Tooltip contentStyle={{ background: '#111827', border: '1px solid #374151', color: '#fff' }} formatter={(v: any) => `$${Number(v).toFixed(6)}`} />
              </PieChart>
            </ResponsiveContainer>
          )}
        </div>

        <div className="bg-gray-900 border border-gray-800 rounded-xl p-5">
          <h2 className="font-semibold text-white mb-4">Cost by Model</h2>
          {(byModel?.length ?? 0) === 0 ? (
            <p className="text-gray-500 text-sm">No model data yet.</p>
          ) : (
            <ResponsiveContainer width="100%" height={200}>
              <BarChart data={byModel} layout="vertical">
                <XAxis type="number" tick={{ fill: '#6b7280', fontSize: 11 }} />
                <YAxis dataKey="model" type="category" tick={{ fill: '#6b7280', fontSize: 11 }} width={120} />
                <Tooltip contentStyle={{ background: '#111827', border: '1px solid #374151', color: '#fff' }} formatter={(v: any) => `$${Number(v).toFixed(6)}`} />
                <Bar dataKey="cost" fill="#10b981" radius={[0, 4, 4, 0]} />
              </BarChart>
            </ResponsiveContainer>
          )}
        </div>
      </div>
    </div>
  )
}
