import { useState, useEffect } from 'react'
import { toast, ToastContainer } from 'react-toastify'
import 'react-toastify/dist/ReactToastify.css'
import { NavBar } from '@/components/NavBar'
import { ChatPage } from '@/pages/ChatPage'
import { MemoryPage } from '@/pages/MemoryPage'
import { SessionsPage } from '@/pages/SessionsPage'
import { CalendarPage } from '@/pages/CalendarPage'
import { KnowledgePage } from '@/pages/KnowledgePage'
import { VoicePage } from '@/pages/VoicePage'
import { ProjectsPage } from '@/pages/ProjectsPage'
import { LoginPage } from '@/pages/LoginPage'
import { OnboardingModal } from '@/pages/OnboardingModal'
import { api, auth } from '@/api'
import type { OnboardingQuestion } from '@/api'
import styles from './App.module.css'

export type TabId = 'chat' | 'memory' | 'sessions' | 'calendar' | 'knowledge' | 'voice' | 'projects'

export default function App() {
  const [tab, setTab] = useState<TabId>('chat')
  const [connected, setConnected] = useState<'ok' | 'error' | 'connecting'>('connecting')
  const [username, setUsername] = useState<string | null>(null)
  const [authChecked, setAuthChecked] = useState(false)
  const [onboardingQuestions, setOnboardingQuestions] = useState<OnboardingQuestion[] | null>(null)
  const [targetProjectSlug, setTargetProjectSlug] = useState<string | null>(null)

  const openProject = (slug: string) => {
    setTargetProjectSlug(slug)
    setTab('projects')
  }

  const checkOnboarding = async () => {
    try {
      const { questions } = await api.onboardingStatus()
      setOnboardingQuestions(questions)
    } catch {
      setOnboardingQuestions([])
    }
  }

  // Verify stored token on mount
  useEffect(() => {
    if (!auth.getToken()) {
      setAuthChecked(true)
      return
    }
    api.whoami()
      .then(({ username: u }) => { setUsername(u); checkOnboarding() })
      .catch(() => { /* 401 already handled (+ auth.clear) inside request() — don't clear on network errors */ })
      .finally(() => setAuthChecked(true))
  }, [])

  useEffect(() => {
    const check = async () => {
      try {
        await api.health()
        setConnected('ok')
      } catch {
        setConnected('error')
      }
    }
    check()
    const id = setInterval(check, 30_000)
    return () => clearInterval(id)
  }, [])

  // Polling rappels — vérifie briefing.md toutes les 60s, toast pour chaque nouveau rappel
  useEffect(() => {
    const today = new Date().toISOString().slice(0, 10)
    const storageKey = `mnemo_reminders_${today}`

    // Nettoie les entrées des jours précédents
    for (const key of Object.keys(localStorage)) {
      if (key.startsWith('mnemo_reminders_') && key !== storageKey) {
        localStorage.removeItem(key)
      }
    }

    const poll = async () => {
      try {
        const { reminders } = await api.getReminders()
        const seen: string[] = JSON.parse(localStorage.getItem(storageKey) ?? '[]')
        for (const r of reminders) {
          if (!seen.includes(r.id)) {
            toast(`🔔 ${r.message}`, { autoClose: false, closeOnClick: false })
            seen.push(r.id)
          }
        }
        localStorage.setItem(storageKey, JSON.stringify(seen))
      } catch {
        // silence — backend peut ne pas encore être prêt
      }
    }

    poll()
    const id = setInterval(poll, 60_000)
    return () => clearInterval(id)
  }, [])

  if (!authChecked) return null

  if (!username) {
    return (
      <>
        <ToastContainer position="top-right" theme="dark" />
        <LoginPage onLogin={u => { setUsername(u); checkOnboarding() }} />
      </>
    )
  }

  return (
    <div className={styles.layout}>
      <ToastContainer position="top-right" theme="dark" />
      <NavBar tab={tab} onTab={setTab} connected={connected} username={username} />
      <main className={styles.main}>
        <div className={tab === 'chat' ? styles.visible : styles.hidden}>
          <ChatPage onOpenProject={openProject} />
        </div>
        <div className={tab === 'memory' ? styles.visible : styles.hidden}>
          <MemoryPage active={tab === 'memory'} />
        </div>
        <div className={tab === 'sessions' ? styles.visible : styles.hidden}>
          <SessionsPage active={tab === 'sessions'} />
        </div>
        <div className={tab === 'calendar' ? styles.visible : styles.hidden}>
          <CalendarPage active={tab === 'calendar'} />
        </div>
        <div className={tab === 'knowledge' ? styles.visible : styles.hidden}>
          <KnowledgePage active={tab === 'knowledge'} />
        </div>
        <div className={tab === 'voice' ? styles.visible : styles.hidden}>
          <VoicePage active={tab === 'voice'} />
        </div>
        <div className={tab === 'projects' ? styles.visible : styles.hidden}>
          <ProjectsPage active={tab === 'projects'} targetSlug={targetProjectSlug} />
        </div>
      </main>
      {onboardingQuestions?.length ? (
        <OnboardingModal
          username={username}
          questions={onboardingQuestions}
          onDone={() => setOnboardingQuestions([])}
        />
      ) : null}
    </div>
  )
}
