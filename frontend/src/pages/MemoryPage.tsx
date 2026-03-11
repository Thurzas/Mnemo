import { useState, useEffect, useCallback, useRef } from 'react'
import { toast } from 'react-toastify'
import { api, type MemorySection } from '@/api'
import { ConfirmModal } from '@/components/ConfirmModal'
import styles from './MemoryPage.module.css'

interface Props { active: boolean }

type ConfirmState = {
  message: string
  confirmLabel: string
  danger: boolean
} | null

export function MemoryPage({ active }: Props) {
  const [sections, setSections]   = useState<MemorySection[]>([])
  const [preamble, setPreamble]   = useState('')
  const [selected, setSelected]   = useState<number | null>(null)
  const [editTitle, setEditTitle] = useState('')
  const [editBody, setEditBody]   = useState('')
  const [isDirty, setIsDirty]     = useState(false)
  const [saving, setSaving]       = useState(false)
  const [saveMsg, setSaveMsg]     = useState<string | null>(null)
  const [loaded, setLoaded]       = useState(false)
  const [confirm, setConfirm]     = useState<ConfirmState>(null)
  const confirmResolve            = useRef<((v: boolean) => void) | null>(null)

  const load = useCallback(async () => {
    try {
      const d = await api.getMemory()
      setSections(d.sections)
      setPreamble(d.preamble)
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Erreur de chargement')
    }
  }, [])

  useEffect(() => {
    if (active && !loaded) {
      setLoaded(true)
      load()
    }
  }, [active, loaded, load])

  useEffect(() => {
    if (selected !== null && sections[selected]) {
      setEditTitle(sections[selected].title)
      setEditBody(sections[selected].content)
      setIsDirty(false)
    }
  }, [selected]) // eslint-disable-line react-hooks/exhaustive-deps

  const askConfirm = (message: string, confirmLabel: string, danger: boolean): Promise<boolean> =>
    new Promise(resolve => {
      confirmResolve.current = resolve
      setConfirm({ message, confirmLabel, danger })
    })

  const handleConfirmYes = () => {
    setConfirm(null)
    confirmResolve.current?.(true)
  }

  const handleConfirmNo = () => {
    setConfirm(null)
    confirmResolve.current?.(false)
  }

  const selectSection = async (i: number) => {
    if (isDirty) {
      const ok = await askConfirm('Modifications non enregistrées. Continuer sans sauvegarder ?', 'Continuer', false)
      if (!ok) return
    }
    setSelected(i)
  }

  const handleTitleChange = (v: string) => { setEditTitle(v); setIsDirty(true) }
  const handleBodyChange  = (v: string) => { setEditBody(v);  setIsDirty(true) }

  const buildMarkdown = (secs: MemorySection[]): string => {
    const parts = secs.map(s => `## ${s.title}\n${s.content}`)
    const joined = parts.join('\n\n')
    return preamble ? `${preamble}\n\n${joined}\n` : `${joined}\n`
  }

  const save = async () => {
    if (selected === null) return
    setSaving(true)
    const updated = sections.map((s, i) =>
      i === selected ? { title: editTitle, content: editBody } : s
    )
    try {
      await api.postMemory(buildMarkdown(updated))
      setSections(updated)
      setIsDirty(false)
      setSaveMsg('Enregistré ✓')
      setTimeout(() => setSaveMsg(null), 2500)
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Erreur lors de la sauvegarde')
    } finally {
      setSaving(false)
    }
  }

  const addSection = async () => {
    if (isDirty) {
      const ok = await askConfirm('Modifications non enregistrées. Continuer sans sauvegarder ?', 'Continuer', false)
      if (!ok) return
    }
    const updated = [...sections, { title: 'Nouvelle section', content: '' }]
    setSections(updated)
    setSelected(updated.length - 1)
    setIsDirty(false)
  }

  const deleteSection = async () => {
    if (selected === null) return
    const ok = await askConfirm(
      `Supprimer la section "${sections[selected].title}" ? Cette action est irréversible.`,
      'Supprimer',
      true
    )
    if (!ok) return
    const updated = sections.filter((_, i) => i !== selected)
    setSaving(true)
    try {
      await api.postMemory(buildMarkdown(updated))
      setSections(updated)
      setSelected(updated.length > 0 ? Math.min(selected, updated.length - 1) : null)
      setIsDirty(false)
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Erreur lors de la suppression')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className={styles.page}>
      <aside className={styles.sidebar}>
        <div className={styles.sidebarLabel}>Sections</div>
        {sections.length === 0 && loaded && (
          <div className={styles.empty}>Mémoire vide</div>
        )}
        {sections.map((s, i) => (
          <button
            key={i}
            className={`${styles.sectionItem} ${selected === i ? styles.active : ''}`}
            onClick={() => selectSection(i)}
          >
            {s.title}
          </button>
        ))}
        <button className={styles.addBtn} onClick={addSection}>+ Ajouter</button>
      </aside>

      <div className={styles.content}>
        {selected !== null && sections[selected] ? (
          <div className={styles.editor}>
            <div className={styles.editorHeader}>
              <input
                className={styles.titleInput}
                value={editTitle}
                onChange={e => handleTitleChange(e.target.value)}
                placeholder="Titre de la section"
              />
              <div className={styles.editorActions}>
                {saveMsg && <span className={styles.saveMsg}>{saveMsg}</span>}
                <button
                  className={styles.btnDelete}
                  onClick={deleteSection}
                  disabled={saving}
                >Supprimer</button>
                <button
                  className={`${styles.btnSave} ${isDirty ? styles.dirty : ''}`}
                  onClick={save}
                  disabled={saving || !isDirty}
                >{saving ? 'Enregistrement…' : 'Enregistrer'}</button>
              </div>
            </div>
            <textarea
              className={styles.bodyTextarea}
              value={editBody}
              onChange={e => handleBodyChange(e.target.value)}
              placeholder="Contenu de la section (Markdown, ### sous-sections…)"
              spellCheck={false}
            />
          </div>
        ) : (
          <div className={styles.placeholder}>Sélectionne une section</div>
        )}
      </div>

      {confirm && (
        <ConfirmModal
          message={confirm.message}
          confirmLabel={confirm.confirmLabel}
          danger={confirm.danger}
          onConfirm={handleConfirmYes}
          onCancel={handleConfirmNo}
        />
      )}
    </div>
  )
}