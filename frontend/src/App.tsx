import { useState, useEffect } from 'react'
import { NavBar } from '@/components/NavBar'
import { ChatPage } from '@/pages/ChatPage'
import { MemoryPage } from '@/pages/MemoryPage'
import { SessionsPage } from '@/pages/SessionsPage'
import { CalendarPage } from '@/pages/CalendarPage'
import { api } from '@/api'
import styles from './App.module.css'

export type TabId = 'chat' | 'memory' | 'sessions' | 'calendar'

export default function App() {
  const [tab, setTab] = useState<TabId>('chat')
  const [connected, setConnected] = useState<'ok' | 'error' | 'connecting'>('connecting')

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

  return (
    <div className={styles.layout}>
      <NavBar tab={tab} onTab={setTab} connected={connected} />
      <main className={styles.main}>
        <div className={tab === 'chat' ? styles.visible : styles.hidden}>
          <ChatPage />
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
      </main>
    </div>
  )
}
