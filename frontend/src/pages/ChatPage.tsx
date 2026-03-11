import { useState, useRef, useEffect, useCallback } from 'react'
import { MessageBubble } from '@/components/MessageBubble'
import { ConfirmModal } from '@/components/ConfirmModal'
import { auth } from '@/api'
import styles from './ChatPage.module.css'

interface Message {
  role: 'user' | 'mnemo'
  content: string
}

interface WebConfirmState {
  query: string
  sessionId: string
  originalMessage: string
}

type WsStatus = 'connecting' | 'ready' | 'error'

const SID_KEY = 'mnemo_sid'
const WS_URL  = `${window.location.protocol === 'https:' ? 'wss:' : 'ws:'}//${window.location.host}/ws/message`

export function ChatPage() {
  const [messages, setMessages]       = useState<Message[]>([
    { role: 'mnemo', content: 'Bonjour. Comment puis-je t\'aider ?' },
  ])
  const [input, setInput]             = useState('')
  const [loading, setLoading]         = useState(false)
  const [streamBuffer, setStreamBuffer] = useState('')
  const [sessionId, setSessionId]     = useState<string | undefined>(
    sessionStorage.getItem(SID_KEY) ?? undefined
  )
  const [webConfirm, setWebConfirm]   = useState<WebConfirmState | null>(null)
  const [wsStatus, setWsStatus]       = useState<WsStatus>('connecting')

  const bottomRef    = useRef<HTMLDivElement>(null)
  const textareaRef  = useRef<HTMLTextAreaElement>(null)
  const wsRef        = useRef<WebSocket | null>(null)
  const streamBufRef = useRef('')
  const reconnectRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, loading, streamBuffer])

  // ── WebSocket lifecycle ──────────────────────────────────────────

  const connect = useCallback(() => {
    if (wsRef.current?.readyState === WebSocket.OPEN) return

    const token = auth.getToken()
    if (!token) return

    setWsStatus('connecting')
    const ws = new WebSocket(WS_URL)
    wsRef.current = ws

    ws.onopen = () => {
      ws.send(JSON.stringify({ type: 'auth', token }))
    }

    ws.onmessage = (ev) => {
      let data: Record<string, unknown>
      try { data = JSON.parse(ev.data) } catch { return }

      switch (data.type) {
        case 'auth_ok':
          setWsStatus('ready')
          break

        case 'thinking':
          setLoading(true)
          streamBufRef.current = ''
          setStreamBuffer('')
          break

        case 'token': {
          const chunk = String(data.text ?? '')
          streamBufRef.current += chunk
          setStreamBuffer(streamBufRef.current)
          break
        }

        case 'done': {
          const sid = String(data.session_id ?? '')
          const finalContent = streamBufRef.current
          streamBufRef.current = ''
          setMessages(prev => [...prev, { role: 'mnemo', content: finalContent }])
          setStreamBuffer('')
          setLoading(false)
          setSessionId(sid)
          sessionStorage.setItem(SID_KEY, sid)
          textareaRef.current?.focus()
          break
        }

        case 'web_confirm':
          setWebConfirm({
            query: String(data.web_query ?? ''),
            sessionId: String(data.session_id ?? ''),
            originalMessage: String(data.original_message ?? ''),
          })
          setLoading(false)
          streamBufRef.current = ''
          setStreamBuffer('')
          break

        case 'error':
          setMessages(prev => [
            ...prev, { role: 'mnemo', content: `⚠ ${data.detail ?? 'Erreur inconnue'}` },
          ])
          setLoading(false)
          streamBufRef.current = ''
          setStreamBuffer('')
          break
      }
    }

    ws.onerror = () => {
      setWsStatus('error')
    }

    ws.onclose = () => {
      setWsStatus('error')
      // auto-reconnect after 2 s
      reconnectRef.current = setTimeout(connect, 2000)
    }
  }, [])

  useEffect(() => {
    connect()
    return () => {
      reconnectRef.current && clearTimeout(reconnectRef.current)
      wsRef.current?.close()
    }
  }, [connect])

  // ── Send helpers ─────────────────────────────────────────────────

  const sendWs = (payload: Record<string, unknown>) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify(payload))
    }
  }

  const send = useCallback(() => {
    const text = input.trim()
    if (!text || loading || wsStatus !== 'ready') return

    setInput('')
    setMessages(prev => [...prev, { role: 'user', content: text }])
    sendWs({ type: 'message', message: text, session_id: sessionId })
  }, [input, loading, sessionId, wsStatus])

  const handleWebResult = useCallback((
    originalMessage: string,
    sid: string,
    confirmed: boolean,
    query: string,
  ) => {
    setWebConfirm(null)
    sendWs({
      type: 'web_answer',
      confirmed,
      web_query: query,
      session_id: sid,
      original_message: originalMessage,
    })
  }, [])

  const onKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      send()
    }
  }

  const autoResize = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    setInput(e.target.value)
    const el = e.target
    el.style.height = 'auto'
    el.style.height = Math.min(el.scrollHeight, 110) + 'px'
  }

  const isDisabled = loading || wsStatus !== 'ready'

  return (
    <div className={styles.page}>
      <div className={styles.messages}>
        {messages.map((m, i) => (
          <MessageBubble key={i} role={m.role} content={m.content} />
        ))}
        {loading && !streamBuffer && <MessageBubble role="mnemo" content="" loading />}
        {streamBuffer && <MessageBubble role="mnemo" content={streamBuffer} streaming />}
        <div ref={bottomRef} />
      </div>

      <div className={styles.inputBar}>
        <span className={styles.sessionPill}>
          {sessionId ? `sid: ${sessionId.slice(-8)}` : 'nouvelle session'}
        </span>
        <textarea
          ref={textareaRef}
          className={styles.textarea}
          value={input}
          onChange={autoResize}
          onKeyDown={onKeyDown}
          placeholder="Envoie un message… (Entrée pour envoyer, Maj+Entrée pour sauter une ligne)"
          rows={1}
          disabled={isDisabled}
        />
        <button
          className={styles.sendBtn}
          onClick={send}
          disabled={isDisabled || !input.trim()}
        >
          {loading ? '…' : '↑'}
        </button>
      </div>

      {webConfirm && (
        <ConfirmModal
          message={`Lancer une recherche web pour :\n« ${webConfirm.query} » ?`}
          confirmLabel="Rechercher"
          onConfirm={() => handleWebResult(webConfirm.originalMessage, webConfirm.sessionId, true, webConfirm.query)}
          onCancel={() => handleWebResult(webConfirm.originalMessage, webConfirm.sessionId, false, webConfirm.query)}
        />
      )}
    </div>
  )
}