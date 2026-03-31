import { useParams } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useState, useMemo } from 'react'
import { ReactFlow, Background, Controls, MiniMap } from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import { api } from '../api'
import type { Task } from '../api'

const STATUS_COLORS: Record<string, string> = {
  done: '#16a34a',
  'in-progress': '#0284c7',
  pending: '#6b7280',
  blocked: '#dc2626',
}

function TaskNode({ data }: { data: any }) {
  const color = STATUS_COLORS[data.status] ?? '#6b7280'
  return (
    <div className="bg-gray-800 border-2 rounded-lg p-3 min-w-[160px] shadow-lg" style={{ borderColor: color }}>
      <p className="text-white text-xs font-semibold">{data.name}</p>
      <div className="flex items-center justify-between mt-1">
        <span className="text-xs text-gray-400">{data.task_type}</span>
        <span className="text-xs px-1.5 py-0.5 rounded-full" style={{ background: color + '33', color }}>
          {data.status}
        </span>
      </div>
      <p className="text-xs text-gray-500 mt-1">{data.estimated_hours}h est.</p>
      {data.status !== 'done' && (
        <button
          onClick={data.onMarkDone}
          className="mt-2 w-full text-xs bg-green-800 hover:bg-green-700 text-green-300 rounded px-2 py-1 transition-colors"
        >
          Mark done
        </button>
      )}
    </div>
  )
}

const nodeTypes = { task: TaskNode }

function dagToFlow(tasks: Task[], deps: [string, string][] = [], onMarkDone: (id: string) => void) {
  const nodes = tasks.map((t, i) => ({
    id: t.id,
    type: 'task',
    position: { x: (i % 4) * 220, y: Math.floor(i / 4) * 180 },
    data: { ...t, onMarkDone: () => onMarkDone(t.id) },
  }))
  const edges = deps.map(([src, tgt]) => ({
    id: `${src}-${tgt}`,
    source: src,
    target: tgt,
    animated: true,
    style: { stroke: '#0ea5e9' },
  }))
  return { nodes, edges }
}

export default function ProjectDetail() {
  const { id } = useParams<{ id: string }>()
  const qc = useQueryClient()
  const [tab, setTab] = useState<'dag' | 'schedule' | 'tasks'>('dag')
  const [newTaskName, setNewTaskName] = useState('')
  const [newTaskType, setNewTaskType] = useState('feature')

  const { data: project } = useQuery({ queryKey: ['project', id], queryFn: () => api.projects.get(id!) })
  const { data: analysis } = useQuery({ queryKey: ['dag-analysis', id], queryFn: () => api.dag.analysis(id!) })
  const { data: schedule } = useQuery({ queryKey: ['schedule', id], queryFn: () => api.scheduler.schedule(id!), enabled: tab === 'schedule' })

  const markDone = useMutation({
    mutationFn: (taskId: string) => api.scheduler.markDone(id!, taskId),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['project', id] }),
  })

  const createTask = useMutation({
    mutationFn: () => api.dag.createTask(id!, { name: newTaskName, task_type: newTaskType }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['project', id] }); setNewTaskName('') },
  })

  const optimize = useMutation({
    mutationFn: () => api.scheduler.optimize(id!),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['schedule', id] }),
  })

  const { nodes, edges } = useMemo(() => {
    if (!project) return { nodes: [], edges: [] }
    return dagToFlow(
      project.dag.tasks ?? [],
      project.dag.dependencies ?? [],
      (taskId) => markDone.mutate(taskId)
    )
  }, [project, markDone])

  const tabClass = (t: string) =>
    `px-4 py-2 text-sm font-medium rounded-t-lg transition-colors ${tab === t ? 'bg-gray-900 text-white' : 'text-gray-400 hover:text-gray-200'}`

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-2xl font-bold text-white">{project?.name ?? '…'}</h1>
        {project?.description && <p className="text-gray-400 text-sm mt-1">{project.description}</p>}
      </div>

      {analysis && (
        <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
          {[
            { label: 'Total', value: analysis.task_count },
            { label: 'Done', value: analysis.done_count },
            { label: 'In Progress', value: analysis.in_progress_count },
            { label: 'Pending', value: analysis.pending_count },
            { label: 'Blocked', value: analysis.blocked_count },
          ].map(s => (
            <div key={s.label} className="bg-gray-900 border border-gray-800 rounded-lg p-3 text-center">
              <p className="text-xl font-bold text-white">{s.value}</p>
              <p className="text-xs text-gray-400">{s.label}</p>
            </div>
          ))}
        </div>
      )}
      {analysis && (
        <div className="bg-gray-900 border border-gray-800 rounded-lg p-3">
          <div className="flex items-center gap-3">
            <span className="text-sm text-gray-400">Progress</span>
            <div className="flex-1 h-2 bg-gray-700 rounded-full overflow-hidden">
              <div className="h-full bg-sky-500 rounded-full" style={{ width: `${analysis.progress_pct}%` }} />
            </div>
            <span className="text-sm text-white">{analysis.progress_pct.toFixed(0)}%</span>
          </div>
        </div>
      )}

      <div className="flex gap-1 border-b border-gray-800">
        <button className={tabClass('dag')} onClick={() => setTab('dag')}>DAG View</button>
        <button className={tabClass('tasks')} onClick={() => setTab('tasks')}>Tasks</button>
        <button className={tabClass('schedule')} onClick={() => setTab('schedule')}>Schedule</button>
      </div>

      {tab === 'dag' && (
        <div className="bg-gray-900 border border-gray-800 rounded-xl overflow-hidden" style={{ height: 500 }}>
          <ReactFlow nodes={nodes} edges={edges} nodeTypes={nodeTypes} fitView>
            <Background color="#374151" gap={16} />
            <Controls />
            <MiniMap nodeColor={(n: any) => STATUS_COLORS[n.data?.status] ?? '#6b7280'} />
          </ReactFlow>
        </div>
      )}

      {tab === 'tasks' && (
        <div className="space-y-4">
          <div className="bg-gray-900 border border-gray-700 rounded-xl p-4 flex gap-2 flex-wrap">
            <input
              className="flex-1 min-w-[200px] bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-sky-500"
              placeholder="New task name"
              value={newTaskName}
              onChange={e => setNewTaskName(e.target.value)}
            />
            <select
              className="bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-sky-500"
              value={newTaskType}
              onChange={e => setNewTaskType(e.target.value)}
            >
              {['feature', 'bug-fix', 'refactor', 'test', 'docs', 'release'].map(t => (
                <option key={t} value={t}>{t}</option>
              ))}
            </select>
            <button
              onClick={() => createTask.mutate()}
              disabled={!newTaskName || createTask.isPending}
              className="bg-sky-600 hover:bg-sky-500 disabled:opacity-50 text-white text-sm px-4 py-2 rounded-lg transition-colors"
            >
              Add Task
            </button>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-gray-800 text-gray-400 text-left">
                  <th className="pb-2 pr-4">Name</th>
                  <th className="pb-2 pr-4">Type</th>
                  <th className="pb-2 pr-4">Status</th>
                  <th className="pb-2 pr-4">Hours</th>
                  <th className="pb-2">Actions</th>
                </tr>
              </thead>
              <tbody>
                {project?.dag.tasks?.map(t => (
                  <tr key={t.id} className="border-b border-gray-800/50 text-gray-300">
                    <td className="py-2 pr-4 font-medium">{t.name}</td>
                    <td className="py-2 pr-4 text-gray-400">{t.task_type}</td>
                    <td className="py-2 pr-4">
                      <span className="px-2 py-0.5 rounded-full text-xs" style={{
                        background: (STATUS_COLORS[t.status] ?? '#6b7280') + '33',
                        color: STATUS_COLORS[t.status] ?? '#9ca3af'
                      }}>{t.status}</span>
                    </td>
                    <td className="py-2 pr-4">{t.estimated_hours}h</td>
                    <td className="py-2">
                      {t.status !== 'done' && (
                        <button
                          onClick={() => markDone.mutate(t.id)}
                          className="text-xs text-green-500 hover:text-green-400 transition-colors mr-2"
                        >Mark done</button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {tab === 'schedule' && (
        <div className="space-y-4">
          <div className="flex justify-end">
            <button
              onClick={() => optimize.mutate()}
              disabled={optimize.isPending}
              className="bg-sky-600 hover:bg-sky-500 disabled:opacity-50 text-white text-sm px-4 py-2 rounded-lg transition-colors"
            >
              {optimize.isPending ? 'Optimizing…' : 'Optimize Schedule'}
            </button>
          </div>
          {schedule?.length === 0 && <p className="text-gray-500 text-sm">No schedule yet. Click Optimize to generate one.</p>}
          <div className="space-y-2">
            {schedule?.map(s => (
              <div key={s.task_id} className="bg-gray-900 border border-gray-800 rounded-lg p-4 flex items-center gap-4">
                <div className="flex-1">
                  <p className="text-white text-sm font-medium">{s.task_name}</p>
                  <p className="text-xs text-gray-400">Slot {s.start_slot} → {s.start_slot + s.duration_slots} · Worker {s.assigned_worker}</p>
                </div>
                <div className="text-xs text-sky-400 font-medium">{s.duration_slots} slots</div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
