import { useState, useEffect } from 'react'
import { api, type MemorySection } from '@/api'
import styles from './MemoryPage.module.css'

interface Props { active: boolean }

export function MemoryPage({ active }: Props) {
  const [sections, setSections] = useState<MemorySection[]>([])
  const [selected, setSelected] = useState<number | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loaded, setLoaded] = useState(false)

  useEffect(() => {
    if (active && !loaded) {
      setLoaded(true)
      api.getMemory()
        .then(d => setSections(d.sections))
        .catch(e => setError(e instanceof Error ? e.message : 'Erreur'))
    }
  }, [active, loaded])

  const current = selected !== null ? sections[selected] : null

  return (
    <div className={styles.page}>
      <aside className={styles.sidebar}>
        <div className={styles.sidebarLabel}>Sections</div>
        {error && <div className={styles.error}>{error}</div>}
        {!error && sections.length === 0 && loaded && (
          <div className={styles.empty}>Mémoire vide</div>
        )}
        {sections.map((s, i) => (
          <button
            key={i}
            className={`${styles.sectionItem} ${selected === i ? styles.active : ''}`}
            onClick={() => setSelected(i)}
          >
            {s.title}
          </button>
        ))}
      </aside>

      <div className={styles.content}>
        {current ? (
          <>
            <div className={styles.sectionTitle}>{current.title}</div>
            <pre className={styles.sectionBody}>{current.content}</pre>
          </>
        ) : (
          <div className={styles.placeholder}>Sélectionne une section</div>
        )}
      </div>
    </div>
  )
}