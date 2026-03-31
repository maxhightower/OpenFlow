const BASE = '/api'

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { 'Content-Type': 'application/json', ...init?.headers },
    ...init,
  })
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)
  if (res.status === 204) return undefined as T
  return res.json()
}

// --- types ---
export interface Project {
  project_id: string
  name: string
  description: string
  dag: DagData
  created_at: string
  updated_at: string
  is_archived: boolean
}

export interface DagData {
  name: string
  tasks: Task[]
  dependencies?: [string, string][]
}

export interface Task {
  id: string
  name: string
  task_type: string
  estimated_hours: number
  priority: number
  estimated_tokens: number
  status: string
  depends_on?: string[]
}

export interface BudgetStatus {
  window_id?: string
  tokens_used: number
  token_budget: number
  percent_used: number
  tokens_remaining: number
  hours_remaining?: number
  burn_rate_per_hour?: number
}

export interface BurnRate {
  tokens_per_hour: number
  cost_per_hour: number
  total_input_tokens: number
  total_output_tokens: number
  total_cost: number
  session_count: number
}

export interface UsagePoint {
  period: string
  tokens: number
  cost: number
  sessions?: number
}

export interface Run {
  run_id: string
  task_id: string
  task_name?: string
  project_id?: string
  status: string
  tokens_used: number
  started_at: string
  finished_at?: string
}

export interface ScheduleEntry {
  task_id: string
  task_name: string
  start_slot: number
  duration_slots: number
  assigned_worker: number
}

export interface DagAnalysis {
  task_count: number
  done_count: number
  in_progress_count: number
  pending_count: number
  blocked_count: number
  critical_path: string[]
  progress_pct: number
}

// --- projects ---
export const api = {
  projects: {
    list: () => request<Project[]>('/projects'),
    get: (id: string) => request<Project>(`/projects/${id}`),
    create: (body: { name: string; description?: string }) =>
      request<Project>('/projects', { method: 'POST', body: JSON.stringify(body) }),
    update: (id: string, body: { name?: string; description?: string }) =>
      request<Project>(`/projects/${id}`, { method: 'PUT', body: JSON.stringify(body) }),
    delete: (id: string) => request<void>(`/projects/${id}`, { method: 'DELETE' }),
  },
  dag: {
    get: (projectId: string) => request<DagData>(`/projects/${projectId}/dag`),
    analysis: (projectId: string) => request<DagAnalysis>(`/projects/${projectId}/dag/analysis`),
    createTask: (projectId: string, task: Partial<Task> & { name: string }) =>
      request<Task>(`/projects/${projectId}/dag/tasks`, { method: 'POST', body: JSON.stringify(task) }),
    updateTask: (projectId: string, taskId: string, updates: Partial<Task>) =>
      request<Task>(`/projects/${projectId}/dag/tasks/${taskId}`, { method: 'PUT', body: JSON.stringify(updates) }),
    deleteTask: (projectId: string, taskId: string) =>
      request<void>(`/projects/${projectId}/dag/tasks/${taskId}`, { method: 'DELETE' }),
    createEdge: (projectId: string, from_id: string, to_id: string) =>
      request<void>(`/projects/${projectId}/dag/edges`, { method: 'POST', body: JSON.stringify({ from_id, to_id }) }),
    deleteEdge: (projectId: string, from_id: string, to_id: string) =>
      request<void>(`/projects/${projectId}/dag/edges`, { method: 'DELETE', body: JSON.stringify({ from_id, to_id }) }),
  },
  scheduler: {
    schedule: (projectId: string) => request<ScheduleEntry[]>(`/projects/${projectId}/schedule`),
    optimize: (projectId: string) => request<ScheduleEntry[]>(`/projects/${projectId}/schedule/optimize`, { method: 'POST' }),
    nextTask: (projectId: string) => request<Task>(`/projects/${projectId}/next-task`),
    markDone: (projectId: string, taskId: string, actual_tokens = 0) =>
      request<void>(`/projects/${projectId}/tasks/${taskId}/done`, { method: 'POST', body: JSON.stringify({ actual_tokens }) }),
  },
  budget: {
    status: () => request<BudgetStatus>('/budget'),
    history: () => request<BudgetStatus[]>('/budget/history'),
  },
  analytics: {
    burnRate: () => request<BurnRate>('/analytics/burn-rate'),
    daily: (limit = 30) => request<UsagePoint[]>(`/analytics/usage/daily?limit=${limit}`),
    hourly: () => request<UsagePoint[]>('/analytics/usage/hourly'),
    byProject: () => request<{ project: string; cost: number; tokens: number }[]>('/analytics/projects'),
    byModel: () => request<{ model: string; cost: number; tokens: number }[]>('/analytics/models'),
  },
  runs: {
    list: (limit = 20) =>
      request<{ runs: Run[]; count: number }>(`/runs?limit=${limit}`).then(d => d.runs),
    get: (id: string) => request<Run>(`/runs/${id}`),
  },
}
