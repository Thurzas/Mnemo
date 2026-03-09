import { useState, useEffect } from 'react'
import { api, type SessionMeta, type SessionMessage } from '@/api'
import { MessageBubble } from '@/components/MessageBubble'
import styles from './SessionsPage.module.css'

interface Props { active: boolean }

function fmtDate(ts: number): string {
  return new Date(ts * 1000).toLocaleString('fr-FR', {
    day: '2-digit', month: '2-digit', year: '2-digit',
    hour: '2-digit', minute: '2-digit',
  })
}

export function SessionsPage({ active }: Props) {
  const [sessions, setSessions] = useState<SessionMeta[]>([])
  const [selected, setSelected] = useState<string | null>(null)
  const [messages, setMessages] = useState<SessionMessage[]>([])
  const [loadingList, setLoadingList] = useState(false)
  const [loadingDetail, setLoadingDetail] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!active) return
    setLoadingList(true)
    api.getSessions()
      .then(d => setSessions(d.sessions))
      .catch((e: unknown) => setError(e instanceof Error ? e.message : 'Erreur'))
      .finally(() => setLoadingList(false))
  }, [active])

  const loadDetail = async (id: string) => {
    setSelected(id)
    setLoadingDetail(true)
    setMessages([])
    try {
      const d = await api.getSession(id)
      setMessages(d.messages ?? [])
    } catch {
      setMessages([])
    } finally {
      setLoadingDetail(false)
    }
  }

  return (
    <div className={styles.page}>
      <aside className={styles.sidebar}>
        <div className={styles.sidebarLabel}>Historique</div>
        {error && <div className={styles.error}>{error}</div>}
        {loadingList && <div className={styles.empty}>Chargement…</div>}
        {!loadingList && sessions.length === 0 && (
          <div className={styles.empty}>Aucune session</div>
        )}
        {sessions.map(s => (
          <button
            key={s.id}
            className={`${styles.sessionItem} ${selected === s.id ? styles.active : ''}`}
            onClick={() => loadDetail(s.id)}
          >
            <div className={styles.sessionTop}>
              <span className={styles.sessionId}>{s.id.slice(-12)}</span>
              {s.done && <span className={styles.doneBadge}>✓</span>}
            </div>
            <div className={styles.sessionPreview}>{s.preview || '—'}</div>
            <div className={styles.sessionMeta}>
              {s.message_count} msg · {fmtDate(s.modified)}
            </div>
          </button>
        ))}
      </aside>

      <div className={styles.detail}>
        {!selected && (
          <div className={styles.placeholder}>Sélectionne une session</div>
        )}
        {selected && loadingDetail && (
          <div className={styles.placeholder}>Chargement…</div>
        )}
        {selected && !loadingDetail && messages.length === 0 && (
          <div className={styles.placeholder}>Session vide</div>
        )}
        {/* Each entry has user_message + response */}
        {messages.flatMap((m, i) => {
          const bubbles = []
          if (m.user_message) {
            bubbles.push(
              <MessageBubble key={`u-${i}`} role="user" content={m.user_message} />
            )
          }
          if (m.response) {
            bubbles.push(
              <MessageBubble key={`r-${i}`} role="mnemo" content={m.response} />
            )
          }
          return bubbles
        })}
      </div>
    </div>
  )
}