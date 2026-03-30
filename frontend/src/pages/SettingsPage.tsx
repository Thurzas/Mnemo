import { useState, useEffect, useCallback } from 'react'
import { api } from '@/api'
import type { AssistantConfig, AssistantUpdate } from '@/api'
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
    </div>
  )
}
