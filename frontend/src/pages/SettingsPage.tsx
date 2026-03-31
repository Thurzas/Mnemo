import { useState, useEffect, useCallback } from 'react'
import { api } from '@/api'
import type { AssistantConfig, AssistantUpdate, AuditEntry, SystemState } from '@/api'
import styles from './SettingsPage.module.css'

interface Props {
  active: boolean
}

function _timeAgo(isoTs: string): string {
  const diff = (Date.now() - new Date(isoTs).getTime()) / 1000
  if (diff < 60)      return 'à l\'instant'
  if (diff < 3600)    return `il y a ${Math.floor(diff / 60)} min`
  if (diff < 86400)   return `il y a ${Math.floor(diff / 3600)} h`
  return `il y a ${Math.floor(diff / 86400)} j`
}

// ── Section DreamerCrew ───────────────────────────────────────────

function DreamerSection() {
  const [lastDream, setLastDream]   = useState<string | null>(null)
  const [running, setRunning]       = useState(false)
  const [triggering, setTriggering] = useState(false)
  const [dreamError, setDreamError] = useState<string | null>(null)

  const fetchStatus = useCallback(async () => {
    try {
      const { last_dream_ts, dreamer_running } = await api.getDreamLog()
      setLastDream(last_dream_ts)
      setRunning(dreamer_running)
    } catch {
      // silence — backend optionnel
    }
  }, [])

  useEffect(() => {
    fetchStatus()
    const id = setInterval(fetchStatus, 15_000)
    return () => clearInterval(id)
  }, [fetchStatus])

  const handleTrigger = async () => {
    setTriggering(true)
    setDreamError(null)
    try {
      const { started, already_running } = await api.triggerDream()
      if (already_running) {
        setDreamError('Une consolidation est déjà en cours.')
      } else if (started) {
        setRunning(true)
        setTimeout(fetchStatus, 3000)
      }
    } catch (e: unknown) {
      setDreamError(e instanceof Error ? e.message : 'Erreur inconnue')
    } finally {
      setTriggering(false)
    }
  }

  return (
    <div className={styles.section}>
      <h3 className={styles.sectionTitle}>Consolidation mémoire</h3>

      <div className={styles.dreamerStatus}>
        <span className={`${styles.dreamerDot} ${running ? styles.dreamerActive : ''}`}>💤</span>
        <span className={styles.dreamerLabel}>
          {running
            ? 'Consolidation en cours…'
            : lastDream
              ? `Dernier rêve : ${_timeAgo(lastDream)}`
              : 'Aucune consolidation enregistrée'
          }
        </span>
      </div>

      {dreamError && <p className={styles.error}>{dreamError}</p>}

      <div className={styles.actions}>
        <button
          className={styles.saveBtn}
          onClick={handleTrigger}
          disabled={triggering || running}
        >
          {running ? 'En cours…' : 'Lancer maintenant'}
        </button>
      </div>
    </div>
  )
}

// ── Section Guardrails ────────────────────────────────────────────

const RISK_COLOR: Record<string, string> = {
  low:      '#64748b',
  medium:   '#d97706',
  high:     '#ef4444',
  critical: '#7c3aed',
}

const METHOD_COLOR: Record<string, string> = {
  GET:    '#22c55e',
  POST:   '#3b82f6',
  PUT:    '#f59e0b',
  DELETE: '#ef4444',
}

function GuardrailsSection() {
  const [sysState, setSysState] = useState<SystemState | null>(null)
  const [audit, setAudit]       = useState<AuditEntry[]>([])
  const [toggling, setToggling] = useState(false)
  const [sysError, setSysError] = useState<string | null>(null)

  const fetchAll = useCallback(async () => {
    try {
      const [sys, aud] = await Promise.all([
        api.getSystemState(),
        api.getAudit(20),
      ])
      setSysState(sys)
      setAudit(aud.entries)
    } catch { /* silence */ }
  }, [])

  useEffect(() => {
    fetchAll()
    const id = setInterval(fetchAll, 15_000)
    return () => clearInterval(id)
  }, [fetchAll])

  const handleToggle = async () => {
    if (!sysState) return
    setToggling(true)
    setSysError(null)
    try {
      if (sysState.paused) await api.resumeSystem()
      else                 await api.pauseSystem()
      await fetchAll()
    } catch (e: unknown) {
      setSysError(e instanceof Error ? e.message : 'Erreur inconnue')
    } finally {
      setToggling(false)
    }
  }

  return (
    <div className={styles.section}>
      <h3 className={styles.sectionTitle}>Guardrails</h3>

      {sysState && (
        <div className={styles.guardrailsRow}>
          <span className={`${styles.guardrailsDot} ${sysState.paused ? styles.guardrailsPaused : styles.guardrailsActive}`} />
          <span className={styles.guardrailsLabel}>
            {sysState.paused
              ? `Système en pause${sysState.paused_at ? ` — ${_timeAgo(sysState.paused_at)}` : ''}`
              : 'Système actif'}
          </span>
          <button
            className={sysState.paused ? styles.resumeBtn : styles.pauseBtn}
            onClick={handleToggle}
            disabled={toggling}
          >
            {toggling ? '…' : sysState.paused ? 'Reprendre' : 'Mettre en pause'}
          </button>
        </div>
      )}

      {sysError && <p className={styles.error}>{sysError}</p>}

      {sysState?.paused && (
        <p className={styles.pauseNotice}>
          Le scheduler autonome (briefing, rêve, avancement projet) est suspendu.
          Les actions HIGH+ sont bloquées par le middleware.
        </p>
      )}

      <div className={styles.auditList}>
        <p className={styles.auditTitle}>Journal d'actions récentes</p>
        {audit.length === 0 && (
          <p className={styles.auditEmpty}>Aucune action enregistrée.</p>
        )}
        {audit.map((e, i) => (
          <div key={i} className={styles.auditEntry}>
            <span
              className={styles.auditMethod}
              style={{ color: METHOD_COLOR[e.method] ?? '#94a3b8' }}
            >{e.method}</span>
            <span className={styles.auditPath}>{e.path}</span>
            <span
              className={styles.auditRisk}
              style={{ color: RISK_COLOR[e.risk] ?? '#64748b' }}
            >{e.risk}</span>
            <span
              className={styles.auditStatus}
              style={{ color: e.status < 400 ? '#22c55e' : '#ef4444' }}
            >{e.status}</span>
            <span className={styles.auditTs}>{_timeAgo(e.ts)}</span>
          </div>
        ))}
      </div>
    </div>
  )
}

// ── Page principale ───────────────────────────────────────────────

export function SettingsPage({ active }: Props) {
  const [config, setConfig] = useState<AssistantConfig | null>(null)
  const [form, setForm]     = useState<AssistantUpdate>({})
  const [saving, setSaving] = useState(false)
  const [saved, setSaved]   = useState(false)
  const [error, setError]   = useState<string | null>(null)

  useEffect(() => {
    if (!active) return
    api.getAssistant()
      .then(cfg => {
        setConfig(cfg)
        setForm({
          name:           cfg.name,
          persona_short:  cfg.persona_short,
          persona_full:   cfg.persona_full,
          language_style: cfg.language_style,
          pronouns:       cfg.pronouns,
        })
      })
      .catch(e => setError(e.message))
  }, [active])

  const handleChange = (field: keyof AssistantUpdate) => (
    e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>
  ) => {
    setForm(f => ({ ...f, [field]: e.target.value }))
    setSaved(false)
  }

  const handleSave = async () => {
    setSaving(true)
    setError(null)
    try {
      const updated = await api.updateAssistant(form)
      setConfig(updated)
      setSaved(true)
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Erreur inconnue')
    } finally {
      setSaving(false)
    }
  }

  const isDirty = config && (
    form.name           !== config.name           ||
    form.persona_short  !== config.persona_short  ||
    form.persona_full   !== config.persona_full   ||
    form.language_style !== config.language_style ||
    form.pronouns       !== config.pronouns
  )

  if (!config) return (
    <div className={styles.page}>
      {error
        ? <p className={styles.error}>{error}</p>
        : <p className={styles.loading}>Chargement…</p>
      }
    </div>
  )

  return (
    <div className={styles.page}>
      <h2 className={styles.pageTitle}>Paramètres</h2>

      <div className={styles.section}>
        <h3 className={styles.sectionTitle}>Identité de l'assistant</h3>

        <div className={styles.field}>
          <label className={styles.label}>Nom</label>
          <input
            className={styles.input}
            value={form.name ?? ''}
            onChange={handleChange('name')}
            placeholder="Mnemo"
          />
        </div>

        <div className={styles.field}>
          <label className={styles.label}>Pronoms</label>
          <input
            className={styles.input}
            value={form.pronouns ?? ''}
            onChange={handleChange('pronouns')}
            placeholder="il/lui, elle/la, …"
          />
        </div>

        <div className={styles.field}>
          <label className={styles.label}>Persona (résumé)</label>
          <input
            className={styles.input}
            value={form.persona_short ?? ''}
            onChange={handleChange('persona_short')}
            placeholder="Description courte du personnage"
          />
        </div>

        <div className={styles.field}>
          <label className={styles.label}>Persona complet</label>
          <textarea
            className={styles.textarea}
            rows={8}
            value={form.persona_full ?? ''}
            onChange={handleChange('persona_full')}
            placeholder="Description détaillée injectée dans les crews…"
          />
        </div>

        <div className={styles.field}>
          <label className={styles.label}>Style de communication</label>
          <textarea
            className={styles.textarea}
            rows={3}
            value={form.language_style ?? ''}
            onChange={handleChange('language_style')}
            placeholder="Ex: Direct, concis, quelques expressions japonaises…"
          />
        </div>

        {error && <p className={styles.error}>{error}</p>}

        <div className={styles.actions}>
          {saved && !isDirty && <span className={styles.savedBadge}>Sauvegardé</span>}
          <button
            className={styles.saveBtn}
            onClick={handleSave}
            disabled={saving || !isDirty}
          >
            {saving ? 'Sauvegarde…' : 'Sauvegarder'}
          </button>
        </div>
      </div>

      <DreamerSection />
      <GuardrailsSection />
    </div>
  )
}
