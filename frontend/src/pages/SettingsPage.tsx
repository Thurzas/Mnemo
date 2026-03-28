import { useState, useEffect } from 'react'
import { api } from '@/api'
import type { AssistantConfig, AssistantUpdate } from '@/api'
import styles from './SettingsPage.module.css'

interface Props {
  active: boolean
}

export function SettingsPage({ active }: Props) {
  const [config, setConfig] = useState<AssistantConfig | null>(null)
  const [form, setForm] = useState<AssistantUpdate>({})
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!active) return
    api.getAssistant()
      .then(cfg => {
        setConfig(cfg)
        setForm({
          name: cfg.name,
          persona_short: cfg.persona_short,
          persona_full: cfg.persona_full,
          language_style: cfg.language_style,
          pronouns: cfg.pronouns,
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
    form.name !== config.name ||
    form.persona_short !== config.persona_short ||
    form.persona_full !== config.persona_full ||
    form.language_style !== config.language_style ||
    form.pronouns !== config.pronouns
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
    </div>
  )
}
