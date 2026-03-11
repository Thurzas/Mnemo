import { useState, FormEvent } from 'react'
import { api, auth } from '@/api'
import styles from './LoginPage.module.css'

interface Props {
  onLogin: (username: string) => void
}

export function LoginPage({ onLogin }: Props) {
  const [token, setToken] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault()
    const t = token.trim()
    if (!t) return

    setLoading(true)
    setError(null)
    try {
      auth.setToken(t)
      const { username } = await api.whoami()
      onLogin(username)
    } catch {
      auth.clear()
      setError('Token invalide. Vérifie ta clé et réessaie.')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className={styles.page}>
      <div className={styles.card}>
        <div className={styles.logo}>🧠</div>
        <h1 className={styles.title}>Mnemo</h1>
        <p className={styles.subtitle}>Assistant personnel local</p>
        <form onSubmit={handleSubmit}>
          <label className={styles.label} htmlFor="token">
            Token d'accès
          </label>
          <input
            id="token"
            className={styles.input}
            type="password"
            placeholder="mnemo_…"
            value={token}
            onChange={e => setToken(e.target.value)}
            autoFocus
            autoComplete="current-password"
          />
          <button className={styles.btn} type="submit" disabled={loading || !token.trim()}>
            {loading ? 'Vérification…' : 'Se connecter'}
          </button>
        </form>
        {error && <p className={styles.error}>{error}</p>}
      </div>
    </div>
  )
}