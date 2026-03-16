import { useState, useEffect, useRef } from 'react'
import { api } from '@/api'
import type { VoiceSettings, VoiceSettingsResponse } from '@/api'
import styles from './VoicePage.module.css'

const F0_METHODS = ['harvest', 'pm', 'rmvpe']

function Slider({
  label, hint, value, min, max, step, onChange,
}: {
  label: string; hint?: string; value: number; min: number; max: number; step: number
  onChange: (v: number) => void
}) {
  return (
    <div className={styles.sliderRow}>
      <div className={styles.sliderMeta}>
        <span className={styles.sliderLabel}>{label}</span>
        {hint && <span className={styles.sliderHint}>{hint}</span>}
      </div>
      <div className={styles.sliderControl}>
        <input
          type="range"
          min={min} max={max} step={step}
          value={value}
          onChange={e => onChange(parseFloat(e.target.value))}
          className={styles.range}
        />
        <span className={styles.sliderValue}>{value}</span>
      </div>
    </div>
  )
}

export function VoicePage({ active }: { active: boolean }) {
  const [data, setData] = useState<VoiceSettingsResponse | null>(null)
  const [form, setForm] = useState<VoiceSettings | null>(null)
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [testing, setTesting] = useState(false)
  const [saveMsg, setSaveMsg] = useState<string | null>(null)
  const audioRef = useRef<HTMLAudioElement | null>(null)

  useEffect(() => {
    if (!active || data) return
    setLoading(true)
    api.getVoiceSettings()
      .then(d => { setData(d); setForm({ ...d }) })
      .catch(() => {/* silent */})
      .finally(() => setLoading(false))
  }, [active])

  if (!active) return null

  const set = (patch: Partial<VoiceSettings>) =>
    setForm(f => f ? { ...f, ...patch } : f)

  const handleSave = async () => {
    if (!form) return
    setSaving(true)
    setSaveMsg(null)
    try {
      const updated = await api.updateVoiceSettings(form)
      setForm({ ...updated })
      setSaveMsg('Paramètres sauvegardés.')
    } catch (e: unknown) {
      setSaveMsg(`Erreur : ${e instanceof Error ? e.message : String(e)}`)
    } finally {
      setSaving(false)
    }
  }

  const handleTest = async () => {
    if (testing) return
    setTesting(true)
    try {
      const blob = await api.testVoice()
      const url = URL.createObjectURL(blob)
      if (audioRef.current) {
        audioRef.current.pause()
        URL.revokeObjectURL(audioRef.current.src)
      }
      const audio = new Audio(url)
      audioRef.current = audio
      audio.play()
      audio.onended = () => URL.revokeObjectURL(url)
    } catch {
      /* silent */
    } finally {
      setTesting(false)
    }
  }

  if (loading) return <div className={styles.page}><p className={styles.empty}>Chargement…</p></div>
  if (!form || !data) return <div className={styles.page}><p className={styles.empty}>Impossible de charger les paramètres.</p></div>

  const rvcAvailable = !!data.rvc_service_url

  return (
    <div className={styles.page}>
      <h2 className={styles.pageTitle}>Voix</h2>

      {/* ── Kokoro ─────────────────────────────────────────── */}
      <section className={styles.section}>
        <h3 className={styles.sectionTitle}>Synthèse vocale (Kokoro-82M)</h3>

        <div className={styles.row}>
          <label className={styles.label}>Voix française</label>
          <select
            className={styles.select}
            value={form.kokoro_voice_fr}
            onChange={e => set({ kokoro_voice_fr: e.target.value })}
          >
            {data.available_voices_fr.map(v => (
              <option key={v} value={v}>{v}</option>
            ))}
          </select>
        </div>

        <div className={styles.row}>
          <label className={styles.label}>Voix japonaise</label>
          <select
            className={styles.select}
            value={form.kokoro_voice_ja}
            onChange={e => set({ kokoro_voice_ja: e.target.value })}
          >
            {data.available_voices_ja.map(v => (
              <option key={v} value={v}>{v}</option>
            ))}
          </select>
        </div>

        <Slider
          label="Vitesse"
          hint="0.5 – 2.0"
          value={form.kokoro_speed}
          min={0.5} max={2.0} step={0.05}
          onChange={v => set({ kokoro_speed: v })}
        />
      </section>

      {/* ── RVC ────────────────────────────────────────────── */}
      <section className={styles.section}>
        <h3 className={styles.sectionTitle}>
          Conversion de voix (RVC)
          {!rvcAvailable && (
            <span className={styles.badge}>service non connecté</span>
          )}
        </h3>

        <div className={styles.row}>
          <label className={styles.label}>Activer RVC</label>
          <label className={styles.toggle}>
            <input
              type="checkbox"
              checked={form.rvc_enabled}
              disabled={!rvcAvailable}
              onChange={e => set({ rvc_enabled: e.target.checked })}
            />
            <span className={styles.toggleTrack} />
          </label>
        </div>

        <div className={`${styles.rvcParams} ${!form.rvc_enabled || !rvcAvailable ? styles.disabled : ''}`}>
          <div className={styles.row}>
            <label className={styles.label}>Méthode F0</label>
            <select
              className={styles.select}
              value={form.rvc_f0_method}
              disabled={!form.rvc_enabled || !rvcAvailable}
              onChange={e => set({ rvc_f0_method: e.target.value })}
            >
              {F0_METHODS.map(m => (
                <option key={m} value={m}>{m}</option>
              ))}
            </select>
          </div>

          <Slider
            label="Transposition (demi-tons)"
            hint="-12 – +12"
            value={form.rvc_f0_up_key}
            min={-12} max={12} step={1}
            onChange={v => set({ rvc_f0_up_key: v })}
          />

          <Slider
            label="Index rate"
            hint="0.0 – 1.0"
            value={form.rvc_index_rate}
            min={0} max={1} step={0.05}
            onChange={v => set({ rvc_index_rate: v })}
          />

          <Slider
            label="Filter radius"
            hint="0 – 7"
            value={form.rvc_filter_radius}
            min={0} max={7} step={1}
            onChange={v => set({ rvc_filter_radius: v })}
          />

          <Slider
            label="RMS mix rate"
            hint="0.0 – 1.0"
            value={form.rvc_rms_mix_rate}
            min={0} max={1} step={0.05}
            onChange={v => set({ rvc_rms_mix_rate: v })}
          />

          <Slider
            label="Protect"
            hint="0.0 – 0.5"
            value={form.rvc_protect}
            min={0} max={0.5} step={0.01}
            onChange={v => set({ rvc_protect: v })}
          />
        </div>
      </section>

      {/* ── Actions ────────────────────────────────────────── */}
      <div className={styles.actions}>
        <button className={styles.testBtn} onClick={handleTest} disabled={testing}>
          {testing ? 'Lecture…' : 'Tester'}
        </button>
        <button className={styles.saveBtn} onClick={handleSave} disabled={saving}>
          {saving ? 'Sauvegarde…' : 'Sauvegarder'}
        </button>
        {saveMsg && <span className={styles.saveMsg}>{saveMsg}</span>}
      </div>
    </div>
  )
}