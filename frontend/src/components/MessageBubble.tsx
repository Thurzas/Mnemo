import styles from './MessageBubble.module.css'

interface Props {
  role: 'user' | 'mnemo'
  content: string
  loading?: boolean
  streaming?: boolean
}

function formatMsg(text: string): string {
  let out = text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')

  // Code blocks
  out = out.replace(/```[\w]*\n?([\s\S]*?)```/g, (_, c) => `<pre>${c.trim()}</pre>`)
  // Inline code
  out = out.replace(/`([^`]+)`/g, '<code>$1</code>')
  // Bold
  out = out.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
  // Newlines
  out = out.replace(/\n/g, '<br />')

  return out
}

export function MessageBubble({ role, content, loading, streaming }: Props) {
  const avatar = role === 'user' ? 'T' : 'M'

  return (
    <div className={`${styles.msg} ${styles[role]}`}>
      <div className={styles.avatar}>{avatar}</div>
      <div className={styles.bubble}>
        {loading ? (
          <span className={styles.loadingDots}>
            <span /><span /><span />
          </span>
        ) : (
          <>
            <span dangerouslySetInnerHTML={{ __html: formatMsg(content) }} />
            {streaming && <span className={styles.cursor} />}
          </>
        )}
      </div>
    </div>
  )
}