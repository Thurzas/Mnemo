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

export interface IngestedDocument {
  filename: string
  pages: number
  chunks: number
  ingested_at: string
  doc_id?: string   // présent si on l'expose plus tard via la route list
}

export interface DocumentsResponse {
  documents: IngestedDocument[]
}

export interface IngestResult {
  status: 'ingested' | 'already_ingested' | 'empty'
  doc_id: string
  filename: string
  pages: number
  chunks: number
}

export interface PendingConfirmation {
  id:           string
  action:       string
  project_slug: string
  step_label:   string
  description:  string
  ts:           string
}

export interface ConfirmationsResponse {
  confirmations: PendingConfirmation[]
}

export interface ConfirmActionResult {
  ok:         boolean
  executed:   boolean
  stdout:     string
  stderr:     string
  returncode: number | null
}

export interface ProjectManifest {
  slug:       string
  name:       string
  goal:       string
  status:     string
  created_at: string
  files?:     string[]
}

export interface ProjectFile {
  content: string
  path:    string
}

export interface FileWriteResult {
  path:      string
  committed: boolean
  conflict:  boolean
  error:     string | null
}

export interface OnboardingAnswerItem {
  id: string
  answer: string
  section: string
  subsection: string
  label: string
}

export interface RvcModel {
  name: string
  pth: string
  index: string | null
}

export interface VoiceSettings {
  rvc_enabled: boolean
  kokoro_voice_fr: string
  kokoro_voice_ja: string
  kokoro_speed: number
  rvc_f0_method: string
  rvc_f0_up_key: number
  rvc_index_rate: number
  rvc_filter_radius: number
  rvc_rms_mix_rate: number
  rvc_protect: number
  rvc_active_model: string
}

export interface VoiceSettingsResponse extends VoiceSettings {
  available_voices_fr: string[]
  available_voices_ja: string[]
  rvc_service_url: string | null
  available_models: RvcModel[]
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

  getDocuments: () =>
    request<DocumentsResponse>('/api/documents'),

  deleteDocument: (docId: string) =>
    request<{ ok: boolean }>(`/api/documents/${encodeURIComponent(docId)}`, {
      method: 'DELETE',
    }),

  ingestFile: async (file: File): Promise<IngestResult> => {
    const token = auth.getToken()
    const headers: Record<string, string> = {}
    if (token) headers['Authorization'] = `Bearer ${token}`
    const body = new FormData()
    body.append('file', file)
    const res = await fetch('/api/ingest', { method: 'POST', headers, body })
    if (res.status === 401) { auth.clear(); throw new Error('Non authentifié') }
    if (!res.ok) {
      const detail = await res.json().catch(() => ({ detail: res.statusText }))
      throw new Error(detail?.detail ?? res.statusText)
    }
    return res.json()
  },

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

  getVoiceSettings: () =>
    request<VoiceSettingsResponse>('/api/voice/settings'),

  updateVoiceSettings: (settings: Partial<VoiceSettings>) =>
    request<VoiceSettings>('/api/voice/settings', {
      method: 'POST',
      body: JSON.stringify(settings),
    }),

  getVoiceModels: () =>
    request<{ models: RvcModel[] }>('/api/voice/models'),

  uploadVoiceModel: async (pthFile: File, indexFile?: File): Promise<RvcModel> => {
    const token = auth.getToken()
    const headers: Record<string, string> = {}
    if (token) headers['Authorization'] = `Bearer ${token}`
    const body = new FormData()
    body.append('pth_file', pthFile)
    if (indexFile) body.append('index_file', indexFile)
    const res = await fetch('/api/voice/model', { method: 'POST', headers, body })
    if (res.status === 401) { auth.clear(); throw new Error('Non authentifié') }
    if (!res.ok) {
      const detail = await res.json().catch(() => ({ detail: res.statusText }))
      throw new Error(detail?.detail ?? res.statusText)
    }
    return res.json()
  },

  activateVoiceModel: (name: string) =>
    request<{ ok: boolean; active_model: string }>(`/api/voice/model/${encodeURIComponent(name)}/activate`, {
      method: 'POST',
    }),

  listProjects: () =>
    request<{ projects: ProjectManifest[] }>('/api/projects'),

  createProject: (body: { name: string; goal: string; slug?: string }) =>
    request<ProjectManifest>('/api/projects', {
      method: 'POST',
      body: JSON.stringify(body),
    }),

  getProject: (slug: string) =>
    request<ProjectManifest & { files: string[] }>(`/api/projects/${encodeURIComponent(slug)}`),

  readProjectFile: (slug: string, path: string) =>
    request<ProjectFile>(`/api/projects/${encodeURIComponent(slug)}/file?path=${encodeURIComponent(path)}`),

  readProjectLog: (slug: string) =>
    request<ProjectFile>(`/api/projects/${encodeURIComponent(slug)}/log`),

  advanceProject: (slug: string) =>
    request<{ done: boolean; message: string }>(`/api/projects/${encodeURIComponent(slug)}/advance`, {
      method: 'POST',
    }),

  writeProjectFile: (slug: string, body: { path: string; content: string; commit_msg?: string }) =>
    request<FileWriteResult>(`/api/projects/${encodeURIComponent(slug)}/file`, {
      method: 'POST',
      body: JSON.stringify(body),
    }),

  deleteProject: (slug: string) =>
    request<{ ok: boolean }>(`/api/projects/${encodeURIComponent(slug)}`, {
      method: 'DELETE',
    }),

  getProjectGitLog: (slug: string) =>
    request<{ log: string }>(`/api/projects/${encodeURIComponent(slug)}/git`),

  getConfirmations: () =>
    request<ConfirmationsResponse>('/api/confirmations'),

  confirmAction: (id: string, approved: boolean) =>
    request<ConfirmActionResult>(`/api/confirmations/${encodeURIComponent(id)}`, {
      method: 'POST',
      body: JSON.stringify({ approved }),
    }),

  testVoice: async (settings?: Partial<VoiceSettings>, text?: string): Promise<Blob> => {
    const token = auth.getToken()
    const headers: Record<string, string> = { 'Content-Type': 'application/json' }
    if (token) headers['Authorization'] = `Bearer ${token}`
    const res = await fetch('/api/voice/test', {
      method: 'POST',
      headers,
      body: JSON.stringify({ text: text ?? null, ...settings }),
    })
    if (res.status === 401) { auth.clear(); throw new Error('Non authentifié') }
    if (!res.ok) {
      const detail = await res.json().catch(() => ({ detail: res.statusText }))
      throw new Error(detail?.detail ?? res.statusText)
    }
    return res.blob()
  },
}