import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { api } from '../api'

export default function ProjectsPage() {
  const qc = useQueryClient()
  const [name, setName] = useState('')
  const [desc, setDesc] = useState('')
  const [showForm, setShowForm] = useState(false)

  const { data: projects, isLoading } = useQuery({ queryKey: ['projects'], queryFn: api.projects.list })

  const create = useMutation({
    mutationFn: () => api.projects.create({ name, description: desc }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['projects'] }); setName(''); setDesc(''); setShowForm(false) },
  })

  const del = useMutation({
    mutationFn: (id: string) => api.projects.delete(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['projects'] }),
  })

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold text-white">Projects</h1>
        <button
          onClick={() => setShowForm(v => !v)}
          className="bg-sky-600 hover:bg-sky-500 text-white text-sm px-4 py-2 rounded-lg transition-colors"
        >
          + New Project
        </button>
      </div>

      {showForm && (
        <div className="bg-gray-900 border border-gray-700 rounded-xl p-5 space-y-3">
          <h2 className="font-semibold text-white">Create Project</h2>
          <input
            className="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-sky-500"
            placeholder="Project name"
            value={name}
            onChange={e => setName(e.target.value)}
          />
          <input
            className="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-sky-500"
            placeholder="Description (optional)"
            value={desc}
            onChange={e => setDesc(e.target.value)}
          />
          <div className="flex gap-2">
            <button
              onClick={() => create.mutate()}
              disabled={!name || create.isPending}
              className="bg-sky-600 hover:bg-sky-500 disabled:opacity-50 text-white text-sm px-4 py-2 rounded-lg transition-colors"
            >
              {create.isPending ? 'Creating…' : 'Create'}
            </button>
            <button onClick={() => setShowForm(false)} className="text-gray-400 hover:text-white text-sm px-4 py-2">Cancel</button>
          </div>
        </div>
      )}

      {isLoading && <p className="text-gray-500">Loading…</p>}

      <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
        {projects?.map(p => (
          <div key={p.project_id} className="bg-gray-900 border border-gray-800 rounded-xl p-5 hover:border-gray-600 transition-colors">
            <Link to={`/projects/${p.project_id}`}>
              <h3 className="font-semibold text-white hover:text-sky-400 transition-colors">{p.name}</h3>
              {p.description && <p className="text-sm text-gray-400 mt-1">{p.description}</p>}
              <div className="flex gap-3 mt-3 text-xs text-gray-500">
                <span>{p.dag.tasks?.length ?? 0} tasks</span>
                <span>Updated {new Date(p.updated_at).toLocaleDateString()}</span>
              </div>
            </Link>
            <button
              onClick={() => { if (confirm(`Delete "${p.name}"?`)) del.mutate(p.project_id) }}
              className="mt-3 text-xs text-red-500 hover:text-red-400 transition-colors"
            >
              Delete
            </button>
          </div>
        ))}
      </div>
    </div>
  )
}
