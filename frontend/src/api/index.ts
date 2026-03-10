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
}

export interface MessageResponse {
  response: string
  session_id: string
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

// ── Fetch helper ─────────────────────────────────────────────────

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    headers: { 'Content-Type': 'application/json', ...init?.headers },
    ...init,
  })
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

  getReminders: () =>
    request<RemindersResponse>('/api/reminders'),
}