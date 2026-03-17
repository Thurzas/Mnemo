import { useState, useRef, useEffect, useCallback } from 'react'
import { MessageBubble } from '@/components/MessageBubble'
import { ConfirmModal } from '@/components/ConfirmModal'
import { auth, api } from '@/api'
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

// ── TTS helpers ────────────────────────────────────────────────────

/** Supprime les éléments markdown qui rendraient la synthèse bizarre. */
function cleanForTts(text: string): string {
  return text
    .replace(/```[\s\S]*?```/g, ' ')                      // blocs de code
    .replace(/`[^`]*`/g, '')                              // code inline
    .replace(/#{1,6}\s*/g, '')                            // titres
    .replace(/\*{1,3}([^*\n]+)\*{1,3}/g, '$1')           // gras / italique
    .replace(/_{1,3}([^_\n]+)_{1,3}/g, '$1')             // soulignement
    .replace(/\[([^\]]+)\]\([^)]*\)/g, '$1')             // liens → texte seul
    .replace(/!\[[^\]]*\]\([^)]*\)/g, '')                 // images
    .trim()
}

/**
 * Découpe un texte en phrases jouables.
 * Chaque phrase doit faire ≥ 4 caractères pour éviter les artefacts Piper.
 */
function splitSentences(text: string): string[] {
  const cleaned = cleanForTts(text)
  // Coupe après . ! ? suivi d'un espace, ou sur double saut de ligne
  const parts = cleaned.split(/(?<=[.!?…])\s+|\n{2,}/)
  return parts.map(s => s.trim()).filter(s => s.length >= 4)
}

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

  // ── Audio state ───────────────────────────────────────────────────
  const [recording, setRecording]     = useState(false)
  const [ttsEnabled, setTtsEnabled]   = useState(false)
  const [sttError, setSttError]       = useState<string | null>(null)
  const mediaRecorderRef = useRef<MediaRecorder | null>(null)
  const audioChunksRef   = useRef<Blob[]>([])
  const ttsEnabledRef    = useRef(false)
  const ttsAbortRef      = useRef<AbortController | null>(null)
  const currentAudioRef  = useRef<HTMLAudioElement | null>(null)

  // Keep ref in sync with state (readable inside WS callbacks without closure stale issue)
  useEffect(() => { ttsEnabledRef.current = ttsEnabled }, [ttsEnabled])

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
          if (ttsEnabledRef.current && finalContent.trim()) {
            playTts(finalContent)
          }
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

  // ── TTS ──────────────────────────────────────────────────────────

  const playTts = useCallback(async (text: string) => {
    // Interrompt toute lecture en cours (message précédent)
    ttsAbortRef.current?.abort()
    if (currentAudioRef.current) {
      currentAudioRef.current.pause()
      currentAudioRef.current.src = ''
      currentAudioRef.current = null
    }

    const abort = new AbortController()
    ttsAbortRef.current = abort

    const sentences = splitSentences(text)
    if (sentences.length === 0) return

    // Pool de concurrence : prefetch 1 phrase en avance seulement.
    // Pendant que la phrase N est en cours de lecture, la phrase N+1 se
    // télécharge en arrière-plan. On n'envoie JAMAIS plus de 2 requêtes
    // simultanées au serveur — évite la race condition sur le singleton Kokoro.
    let nextFetch: Promise<Blob | null> = api.tts(sentences[0]).catch(() => null)

    for (let i = 0; i < sentences.length; i++) {
      if (abort.signal.aborted) break

      const fetchPromise = nextFetch
      // Lance le prefetch de la phrase suivante dès que la requête courante est en vol
      nextFetch = i + 1 < sentences.length
        ? api.tts(sentences[i + 1]).catch(() => null)
        : Promise.resolve(null)

      let blob: Blob | null
      try { blob = await fetchPromise } catch { blob = null }
      if (!blob || abort.signal.aborted) continue

      const url = URL.createObjectURL(blob)
      await new Promise<void>(resolve => {
        const audio = new Audio(url)
        currentAudioRef.current = audio
        const cleanup = () => {
          URL.revokeObjectURL(url)
          if (currentAudioRef.current === audio) currentAudioRef.current = null
          resolve()
        }
        audio.onended = cleanup
        audio.onerror = cleanup
        abort.signal.addEventListener('abort', () => {
          audio.pause()
          audio.src = ''
          cleanup()
        }, { once: true })
        audio.play().catch(cleanup)
      })
    }
  }, [])

  // ── STT ──────────────────────────────────────────────────────────

  const startRecording = useCallback(async () => {
    setSttError(null)

    // getUserMedia nécessite un contexte sécurisé (HTTPS ou localhost)
    if (!navigator.mediaDevices?.getUserMedia) {
      setSttError('Microphone non disponible (HTTPS ou localhost requis)')
      return
    }

    let stream: MediaStream
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true })
    } catch (e) {
      setSttError(e instanceof Error ? e.message : 'Microphone inaccessible')
      return
    }

    // Choisir le mimeType supporté par ce navigateur
    const mimeType = ['audio/webm;codecs=opus', 'audio/webm', 'audio/ogg;codecs=opus', '']
      .find(m => !m || MediaRecorder.isTypeSupported(m)) ?? ''

    let mr: MediaRecorder
    try {
      mr = new MediaRecorder(stream, mimeType ? { mimeType } : undefined)
    } catch (e) {
      stream.getTracks().forEach(t => t.stop())
      setSttError(e instanceof Error ? e.message : 'MediaRecorder non supporté')
      return
    }
    mediaRecorderRef.current = mr
    audioChunksRef.current = []

    const recordedMime = mr.mimeType || mimeType || 'audio/webm'

    mr.ondataavailable = (e) => {
      if (e.data.size > 0) audioChunksRef.current.push(e.data)
    }

    mr.onstop = async () => {
      stream.getTracks().forEach(t => t.stop())
      const blob = new Blob(audioChunksRef.current, { type: recordedMime })
      if (blob.size < 1000) {
        setSttError('Enregistrement trop court ou silencieux')
        return
      }
      try {
        const { text } = await api.stt(blob)
        if (text) {
          setInput(text)
          // auto-resize textarea
          if (textareaRef.current) {
            textareaRef.current.style.height = 'auto'
            textareaRef.current.style.height =
              Math.min(textareaRef.current.scrollHeight, 110) + 'px'
          }
          textareaRef.current?.focus()
        }
      } catch (e) {
        setSttError(e instanceof Error ? e.message : 'Erreur STT')
      }
    }

    mr.start(250)   // chunk toutes les 250ms — garantit des données même pour les courtes durées
    setRecording(true)
  }, [])

  const stopRecording = useCallback(() => {
    mediaRecorderRef.current?.stop()
    setRecording(false)
  }, [])

  const toggleMic = useCallback(() => {
    if (recording) stopRecording()
    else startRecording()
  }, [recording, startRecording, stopRecording])

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

      {sttError && (
        <div className={styles.sttError}>{sttError}</div>
      )}

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
          className={`${styles.micBtn} ${recording ? styles.micBtnActive : ''}`}
          onClick={toggleMic}
          disabled={loading}
          title={recording ? 'Arrêter l\'enregistrement' : 'Dicter un message'}
          aria-label={recording ? 'Arrêter' : 'Micro'}
        >
          {recording ? '⏹' : '🎙'}
        </button>
        <button
          className={`${styles.ttsBtn} ${ttsEnabled ? styles.ttsBtnActive : ''}`}
          onClick={() => setTtsEnabled(v => !v)}
          title={ttsEnabled ? 'Désactiver la synthèse vocale' : 'Activer la synthèse vocale'}
          aria-label="Synthèse vocale"
        >
          {ttsEnabled ? '🔊' : '🔇'}
        </button>
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