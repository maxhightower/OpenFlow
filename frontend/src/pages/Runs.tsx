import { useQuery } from '@tanstack/react-query'
import { api } from '../api'

export default function RunsPage() {
  const { data: runs, isLoading } = useQuery({ queryKey: ['runs-all'], queryFn: () => api.runs.list(50) })

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold text-white">Run History</h1>
      {isLoading && <p className="text-gray-500">Loading…</p>}
      {runs?.length === 0 && <p className="text-gray-500">No runs yet.</p>}
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-gray-800 text-gray-400 text-left">
              <th className="pb-2 pr-4">Task</th>
              <th className="pb-2 pr-4">Status</th>
              <th className="pb-2 pr-4">Tokens</th>
              <th className="pb-2 pr-4">Started</th>
              <th className="pb-2">Finished</th>
            </tr>
          </thead>
          <tbody>
            {runs?.map(r => (
              <tr key={r.run_id} className="border-b border-gray-800/50 text-gray-300">
                <td className="py-3 pr-4 font-medium">{r.task_name ?? r.task_id}</td>
                <td className="py-3 pr-4">
                  <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${
                    r.status === 'done' ? 'bg-green-900 text-green-300' :
                    r.status === 'failed' ? 'bg-red-900 text-red-300' :
                    'bg-yellow-900 text-yellow-300'
                  }`}>{r.status}</span>
                </td>
                <td className="py-3 pr-4 text-gray-400">{r.tokens_used.toLocaleString()}</td>
                <td className="py-3 pr-4 text-gray-400">{new Date(r.started_at).toLocaleString()}</td>
                <td className="py-3 text-gray-400">{r.finished_at ? new Date(r.finished_at).toLocaleString() : '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
