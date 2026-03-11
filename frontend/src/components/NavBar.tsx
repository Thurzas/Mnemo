import type { TabId } from '@/App'
import { auth } from '@/api'
import styles from './NavBar.module.css'

interface Props {
  tab: TabId
  onTab: (t: TabId) => void
  connected: 'ok' | 'error' | 'connecting'
  username?: string
}

const TABS: { id: TabId; label: string }[] = [
  { id: 'chat',     label: 'Chat' },
  { id: 'memory',   label: 'Mémoire' },
  { id: 'sessions', label: 'Sessions' },
  { id: 'calendar', label: 'Calendrier' },
]

const STATUS_LABEL = { ok: 'connecté', error: 'hors ligne', connecting: 'connexion…' }

export function NavBar({ tab, onTab, connected, username }: Props) {
  const handleLogout = () => {
    auth.clear()
    window.location.reload()
  }

  return (
    <nav className={styles.nav}>
      <span className={styles.logo}>Mnemo</span>
      <div className={styles.tabs}>
        {TABS.map(t => (
          <button
            key={t.id}
            className={`${styles.tab} ${tab === t.id ? styles.active : ''}`}
            onClick={() => onTab(t.id)}
          >
            {t.label}
          </button>
        ))}
      </div>
      <div className={styles.status}>
        <span className={`${styles.dot} ${styles[connected]}`} />
        <span className={styles.statusText}>{STATUS_LABEL[connected]}</span>
        {username && (
          <>
            <span className={styles.separator}>·</span>
            <span className={styles.username}>{username}</span>
            <button className={styles.logoutBtn} onClick={handleLogout} title="Se déconnecter">
              ⏻
            </button>
          </>
        )}
      </div>
    </nav>
  )
}