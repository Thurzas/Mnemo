import { useState, useRef, useEffect, useCallback } from 'react'
import { MessageBubble } from '@/components/MessageBubble'
import { ConfirmModal } from '@/components/ConfirmModal'
import { api } from '@/api'
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

const SID_KEY = 'mnemo_sid'

export function ChatPage() {
  const [messages, setMessages] = useState<Message[]>([
    { role: 'mnemo', content: 'Bonjour. Comment puis-je t\'aider ?' },
  ])
  const [input, setInput] = useState('')
  const [loading, setLoading] = useState(false)
  const [sessionId, setSessionId] = useState<string | undefined>(
    sessionStorage.getItem(SID_KEY) ?? undefined
  )
  const [webConfirm, setWebConfirm] = useState<WebConfirmState | null>(null)
  const bottomRef = useRef<HTMLDivElement>(null)
  const textareaRef = useRef<HTMLTextAreaElement>(null)

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, loading])

  const handleWebResult = useCallback(async (
    originalMessage: string,
    sid: string,
    confirmed: boolean,
    query: string,
  ) => {
    setWebConfirm(null)
    setLoading(true)
    try {
      const res = await api.sendMessage({
        message: originalMessage,
        session_id: sid,
        web_confirmed: confirmed,
        web_query: confirmed ? query : undefined,
      })
      setSessionId(res.session_id)
      sessionStorage.setItem(SID_KEY, res.session_id)
      setMessages(prev => [...prev, { role: 'mnemo', content: res.response }])
    } catch (e) {
      const msg = e instanceof Error ? e.message : 'Erreur inconnue'
      setMessages(prev => [...prev, { role: 'mnemo', content: `⚠ ${msg}` }])
    } finally {
      setLoading(false)
      textareaRef.current?.focus()
    }
  }, [])

  const send = useCallback(async () => {
    const text = input.trim()
    if (!text || loading) return

    setInput('')
    setLoading(true)
    setMessages(prev => [...prev, { role: 'user', content: text }])

    try {
      const res = await api.sendMessage({ message: text, session_id: sessionId })
      setSessionId(res.session_id)
      sessionStorage.setItem(SID_KEY, res.session_id)
      if (res.needs_web_confirm && res.web_query) {
        setWebConfirm({ query: res.web_query, sessionId: res.session_id, originalMessage: text })
        setLoading(false)
        return
      }
      setMessages(prev => [...prev, { role: 'mnemo', content: res.response }])
    } catch (e) {
      const msg = e instanceof Error ? e.message : 'Erreur inconnue'
      setMessages(prev => [...prev, { role: 'mnemo', content: `⚠ ${msg}` }])
    } finally {
      setLoading(false)
      textareaRef.current?.focus()
    }
  }, [input, loading, sessionId])

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

  return (
    <div className={styles.page}>
      <div className={styles.messages}>
        {messages.map((m, i) => (
          <MessageBubble key={i} role={m.role} content={m.content} />
        ))}
        {loading && <MessageBubble role="mnemo" content="" loading />}
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
          disabled={loading}
        />
        <button
          className={styles.sendBtn}
          onClick={send}
          disabled={loading || !input.trim()}
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