// ── Types ────────────────────────────────────────────────────────

export interface HealthResponse {
  status: string
  data_path: string
  memory_exists: boolean
  sessions_dir: string
}

export interface MessageRequest {
  message: string
  session_id?: string
  web_confirmed?: boolean
  web_query?: string
}

export interface MessageResponse {
  response: string
  session_id: string
  needs_web_confirm?: boolean
  web_query?: string
}

export interface MemorySection {
  title: string
  content: string
}

export interface MemoryResponse {
  content: string
  sections: MemorySection[]
  preamble: string
}

export interface SessionMeta {
  id: string
  message_count: number
  done: boolean
  modified: number
  preview: string
}

export interface SessionsResponse {
  sessions: SessionMeta[]
}

export interface SessionMessage {
  role: 'user' | 'agent'
  content: string
}

export interface SessionDetail {
  messages: SessionMessage[]
  [key: string]: unknown
}

export interface CalendarEvent {
  uid: string
  title: string
  date: string | null
  datetime: string | null
  duration_minutes: number
  location?: string
  description?: string
}

export interface CalendarResponse {
  events: CalendarEvent[]
  writable: boolean
}

export interface EventCreateRequest {
  title: string
  date: string
  time?: string
  duration_minutes?: number
  location?: string
  description?: string
}

export interface EventUpdateRequest {
  title?: string
  date?: string
  time?: string
  duration_minutes?: number
  location?: string
  description?: string
}

export interface ReminderItem {
  id: string
  message: string
}

export interface RemindersResponse {
  reminders: ReminderItem[]
}

export interface OnboardingQuestion {
  id: string
  question: string
  section: string
  subsection: string
  label: string
}

export interface OnboardingStatusResponse {
  needed: boolean
  questions: OnboardingQuestion[]
}

export interface OnboardingAnswerItem {
  id: string
  answer: string
  section: string
  subsection: string
  label: string
}

// ── Auth token ───────────────────────────────────────────────────

const TOKEN_KEY = 'mnemo_token'

export const auth = {
  getToken: () => localStorage.getItem(TOKEN_KEY),
  setToken: (t: string) => localStorage.setItem(TOKEN_KEY, t),
  clear:    () => localStorage.removeItem(TOKEN_KEY),
}

// ── Fetch helper ─────────────────────────────────────────────────

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const token = auth.getToken()
  const headers: Record<string, string> = { 'Content-Type': 'application/json' }
  if (token) headers['Authorization'] = `Bearer ${token}`
  if (init?.headers) {
    for (const [k, v] of Object.entries(init.headers as Record<string, string>)) {
      headers[k] = v
    }
  }
  const res = await fetch(path, { ...init, headers })
  if (res.status === 401) {
    auth.clear()
    throw new Error('Non authentifié')
  }
  if (!res.ok) {
    const detail = await res.json().catch(() => ({ detail: res.statusText }))
    throw new Error(detail?.detail ?? res.statusText)
  }
  return res.json() as Promise<T>
}

// ── API calls ────────────────────────────────────────────────────

export const api = {
  health: () =>
    request<HealthResponse>('/api/health'),

  sendMessage: (body: MessageRequest) =>
    request<MessageResponse>('/api/message', {
      method: 'POST',
      body: JSON.stringify(body),
    }),

  getMemory: () =>
    request<MemoryResponse>('/api/memory'),

  postMemory: (content: string) =>
    request<{ ok: boolean }>('/api/memory', {
      method: 'POST',
      body: JSON.stringify({ content }),
    }),

  getSessions: () =>
    request<SessionsResponse>('/api/sessions'),

  getSession: (id: string) =>
    request<SessionDetail>(`/api/sessions/${encodeURIComponent(id)}`),

  getCalendar: () =>
    request<CalendarResponse>('/api/calendar'),

  createEvent: (body: EventCreateRequest) =>
    request<{ uid: string }>('/api/calendar', {
      method: 'POST',
      body: JSON.stringify(body),
    }),

  updateEvent: (uid: string, body: EventUpdateRequest) =>
    request<{ ok: boolean }>(`/api/calendar/${encodeURIComponent(uid)}`, {
      method: 'PUT',
      body: JSON.stringify(body),
    }),

  deleteEvent: (uid: string) =>
    request<{ ok: boolean }>(`/api/calendar/${encodeURIComponent(uid)}`, {
      method: 'DELETE',
    }),

  importCalendar: async (file: File): Promise<{ imported: number; skipped: number }> => {
    const token = auth.getToken()
    const headers: Record<string, string> = {}
    if (token) headers['Authorization'] = `Bearer ${token}`
    const body = new FormData()
    body.append('file', file)
    const res = await fetch('/api/calendar/import', { method: 'POST', headers, body })
    if (res.status === 401) { auth.clear(); throw new Error('Non authentifié') }
    if (!res.ok) {
      const detail = await res.json().catch(() => ({ detail: res.statusText }))
      throw new Error(detail?.detail ?? res.statusText)
    }
    return res.json()
  },

  getReminders: () =>
    request<RemindersResponse>('/api/reminders'),

  whoami: () =>
    request<{ username: string; calendar_source: string; created_at: string | null }>('/api/auth/whoami'),

  onboardingStatus: () =>
    request<OnboardingStatusResponse>('/api/onboarding/status'),

  onboardingSubmit: (answers: OnboardingAnswerItem[]) =>
    request<{ ok: boolean; written: number }>('/api/onboarding', {
      method: 'POST',
      body: JSON.stringify({ answers }),
    }),

  stt: async (blob: Blob): Promise<{ text: string }> => {
    const token = auth.getToken()
    const headers: Record<string, string> = {}
    if (token) headers['Authorization'] = `Bearer ${token}`
    const body = new FormData()
    body.append('file', blob, 'audio.webm')
    const res = await fetch('/api/stt', { method: 'POST', headers, body })
    if (res.status === 401) { auth.clear(); throw new Error('Non authentifié') }
    if (!res.ok) {
      const detail = await res.json().catch(() => ({ detail: res.statusText }))
      throw new Error(detail?.detail ?? res.statusText)
    }
    return res.json()
  },

  tts: async (text: string): Promise<Blob> => {
    const token = auth.getToken()
    const headers: Record<string, string> = { 'Content-Type': 'application/json' }
    if (token) headers['Authorization'] = `Bearer ${token}`
    const res = await fetch('/api/tts', {
      method: 'POST',
      headers,
      body: JSON.stringify({ text }),
    })
    if (res.status === 401) { auth.clear(); throw new Error('Non authentifié') }
    if (!res.ok) {
      const detail = await res.json().catch(() => ({ detail: res.statusText }))
      throw new Error(detail?.detail ?? res.statusText)
    }
    return res.blob()
  },
}