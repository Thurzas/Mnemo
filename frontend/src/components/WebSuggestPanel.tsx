import styles from './WebSuggestPanel.module.css'

export interface WebSuggestion {
  title:   string
  url:     string
  context: string   // "site:react.dev · hooks"
  score:   number
}

interface Props {
  suggestions:   WebSuggestion[]
  originalQuery?: string
  sessionId:     string
  onExplore: (suggestion: WebSuggestion) => void
  onDismiss: () => void
}

export function WebSuggestPanel({ suggestions, onExplore, onDismiss }: Props) {
  return (
    <div className={styles.panel}>
      <div className={styles.header}>
        <span className={styles.icon}>🔗</span>
        <span className={styles.title}>
          Liens pertinents trouvés ({suggestions.length})
        </span>
        <button className={styles.dismissBtn} onClick={onDismiss} title="Ignorer">✕</button>
      </div>
      <ul className={styles.list}>
        {suggestions.map((s, i) => (
          <li key={i} className={styles.item}>
            <div className={styles.itemInfo}>
              <span className={styles.itemTitle}>{s.title}</span>
              <span className={styles.itemCtx}>{s.context}</span>
            </div>
            <button
              className={styles.exploreBtn}
              onClick={() => onExplore(s)}
              title={s.url}
            >
              Explorer
            </button>
          </li>
        ))}
      </ul>
    </div>
  )
}