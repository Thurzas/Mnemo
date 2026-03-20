import { useState, useEffect, useCallback, useRef } from 'react'
import Editor from '@monaco-editor/react'
import type { OnMount } from '@monaco-editor/react'
import type { editor } from 'monaco-editor'
import { api } from '@/api'
import type { ProjectManifest, PendingConfirmation } from '@/api'
import styles from './ProjectsPage.module.css'

// ── Helpers ───────────────────────────────────────────────────────

function detectLanguage(path: string): string {
  const ext = path.split('.').pop()?.toLowerCase() ?? ''
  const map: Record<string, string> = {
    ts: 'typescript', tsx: 'typescript',
    js: 'javascript', jsx: 'javascript',
    py: 'python', json: 'json',
    md: 'markdown', yaml: 'yaml', yml: 'yaml',
    css: 'css', html: 'html', sh: 'shell',
  }
  return map[ext] ?? 'plaintext'
}

interface PlanStep {
  done: boolean
  label: string
}

function parsePlan(md: string): PlanStep[] {
  return md
    .split('\n')
    .filter(l => /^\s*-\s*\[[ xX]\]/.test(l))
    .map(l => ({
      done:  /\[x\]/i.test(l),
      label: l.replace(/^\s*-\s*\[[ xX]\]\s*/, '').trim(),
    }))
}

// ── Component ─────────────────────────────────────────────────────

interface Props {
  active: boolean
  targetSlug?: string | null
}

export function ProjectsPage({ active, targetSlug }: Props) {
  const [projects,     setProjects]     = useState<ProjectManifest[]>([])
  const [slug,         setSlug]         = useState<string | null>(null)
  const [files,        setFiles]        = useState<string[]>([])
  const [openFile,     setOpenFile]     = useState<string | null>(null)
  const [fileContent,  setFileContent]  = useState('')
  const [planSteps,    setPlanSteps]    = useState<PlanStep[]>([])
  const [terminalLog,  setTerminalLog]  = useState('')
  const [gitLog,       setGitLog]       = useState('')
  const [saving,         setSaving]         = useState(false)
  const [advancing,      setAdvancing]      = useState(false)
  const [creating,       setCreating]       = useState(false)
  const [newName,        setNewName]        = useState('')
  const [newGoal,        setNewGoal]        = useState('')
  const [error,          setError]          = useState<string | null>(null)
  const [confirmations,  setConfirmations]  = useState<PendingConfirmation[]>([])
  const [confirmLoading, setConfirmLoading] = useState<Set<string>>(new Set())
  const editorRef = useRef<editor.IStandaloneCodeEditor | null>(null)
  const logPollRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const confPollRef = useRef<ReturnType<typeof setInterval> | null>(null)

  // ── Charger la liste des projets ────────────────────────────────
  const loadProjects = useCallback(async () => {
    try {
      const { projects: p } = await api.listProjects()
      setProjects(p)
    } catch { /* silence */ }
  }, [])

  useEffect(() => {
    if (active) loadProjects()
  }, [active, loadProjects])

  // ── Navigation vers un projet ciblé (depuis ChatPage) ───────────
  useEffect(() => {
    if (targetSlug && active) setSlug(targetSlug)
  }, [targetSlug, active])

  // ── Charger les fichiers d'un projet ────────────────────────────
  const loadProject = useCallback(async (s: string) => {
    try {
      const p = await api.getProject(s)
      setFiles(p.files ?? [])
      setOpenFile(null)
      setFileContent('')
      setTerminalLog('')
      setGitLog('')
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Erreur')
    }
  }, [])

  useEffect(() => {
    if (slug) loadProject(slug)
  }, [slug, loadProject])

  // ── Polling pending_confirmations toutes les 30s ────────────────
  const loadConfirmations = useCallback(async () => {
    try {
      const { confirmations: c } = await api.getConfirmations()
      setConfirmations(c)
    } catch { /* silence */ }
  }, [])

  useEffect(() => {
    if (!active) return
    loadConfirmations()
    confPollRef.current = setInterval(loadConfirmations, 30_000)
    return () => { if (confPollRef.current) clearInterval(confPollRef.current) }
  }, [active, loadConfirmations])

  // ── Approuver / Rejeter une confirmation ─────────────────────────
  const handleConfirm = async (id: string, approved: boolean) => {
    setConfirmLoading(prev => new Set(prev).add(id))
    try {
      const result = await api.confirmAction(id, approved)
      setConfirmations(prev => prev.filter(c => c.id !== id))
      if (result.executed) {
        const out = [
          approved ? `✓ Commande exécutée (rc=${result.returncode})` : '✗ Rejeté',
          result.stdout,
          result.stderr,
        ].filter(Boolean).join('\n')
        setTerminalLog(prev => prev + '\n' + out)
      }
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Erreur confirmation')
    } finally {
      setConfirmLoading(prev => { const s = new Set(prev); s.delete(id); return s })
    }
  }

  // ── Polling logs/commands.log toutes les 3s ─────────────────────
  useEffect(() => {
    if (logPollRef.current) clearInterval(logPollRef.current)
    if (!slug) return
    const poll = async () => {
      try {
        const res = await api.readProjectLog(slug)
        setTerminalLog(res.content)
      } catch { /* ignore */ }
    }
    poll()
    logPollRef.current = setInterval(poll, 3_000)
    return () => { if (logPollRef.current) clearInterval(logPollRef.current) }
  }, [slug])

  // ── Charger un fichier dans l'éditeur ────────────────────────────
  const handleOpenFile = useCallback(async (path: string) => {
    if (!slug) return
    try {
      const res = await api.readProjectFile(slug, path)
      setOpenFile(path)
      setFileContent(res.content)
      if (path === 'plan.md') setPlanSteps(parsePlan(res.content))
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Erreur lecture')
    }
  }, [slug])

  // Charger plan.md automatiquement quand le projet change
  useEffect(() => {
    if (slug && files.includes('plan.md')) handleOpenFile('plan.md')
  }, [slug, files, handleOpenFile])

  // ── Avancer d'une étape ──────────────────────────────────────────
  const handleAdvance = useCallback(async () => {
    if (!slug) return
    setAdvancing(true)
    setError(null)
    try {
      const res = await api.advanceProject(slug)
      // Rafraîchit plan + git log après exécution
      const planRes = await api.readProjectFile(slug, 'plan.md')
      setFileContent(planRes.content)
      setPlanSteps(parsePlan(planRes.content))
      const { log } = await api.getProjectGitLog(slug)
      setGitLog(log)
      if (res.done) setError(null)
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Erreur avancement')
    } finally {
      setAdvancing(false)
    }
  }, [slug])

  // ── Sauvegarder ─────────────────────────────────────────────────
  const handleSave = useCallback(async () => {
    if (!slug || !openFile) return
    setSaving(true)
    setError(null)
    try {
      const content = editorRef.current?.getValue() ?? fileContent
      await api.writeProjectFile(slug, {
        path:    openFile,
        content,
        commit_msg: `user: edit ${openFile}`,
      })
      setFileContent(content)
      if (openFile === 'plan.md') setPlanSteps(parsePlan(content))
      // Refresh git log
      const { log } = await api.getProjectGitLog(slug)
      setGitLog(log)
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Erreur sauvegarde')
    } finally {
      setSaving(false)
    }
  }, [slug, openFile, fileContent])

  // ── Keybinding Ctrl+S ───────────────────────────────────────────
  const handleEditorMount: OnMount = (editorInstance, monacoInstance) => {
    editorRef.current = editorInstance
    editorInstance.addCommand(
      monacoInstance.KeyMod.CtrlCmd | monacoInstance.KeyCode.KeyS,
      () => handleSave(),
    )
  }

  // ── Créer un projet ─────────────────────────────────────────────
  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!newName.trim()) return
    setError(null)
    try {
      const manifest = await api.createProject({ name: newName.trim(), goal: newGoal.trim() })
      await loadProjects()
      setSlug(manifest.slug)
      setCreating(false)
      setNewName('')
      setNewGoal('')
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Erreur création')
    }
  }

  // ── Supprimer un projet ─────────────────────────────────────────
  const handleDelete = async () => {
    if (!slug) return
    if (!window.confirm(`Supprimer le projet « ${slug} » ? Cette action est irréversible.`)) return
    try {
      await api.deleteProject(slug)
      setSlug(null)
      setFiles([])
      await loadProjects()
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Erreur suppression')
    }
  }

  const currentProject = projects.find(p => p.slug === slug)

  return (
    <div className={styles.root}>

      {/* ── Header ── */}
      <div className={styles.header}>
        <div className={styles.headerLeft}>
          <select
            className={styles.projectSelect}
            value={slug ?? ''}
            onChange={e => { setSlug(e.target.value || null) }}
          >
            <option value=''>— Sélectionner un projet —</option>
            {projects.map(p => (
              <option key={p.slug} value={p.slug}>{p.name}</option>
            ))}
          </select>

          {currentProject && (
            <span className={styles.projectGoal} title={currentProject.goal}>
              {currentProject.goal.length > 60
                ? currentProject.goal.slice(0, 57) + '…'
                : currentProject.goal}
            </span>
          )}
        </div>

        <div className={styles.headerRight}>
          {slug && (
            <button className={styles.btnDanger} onClick={handleDelete}>
              Supprimer
            </button>
          )}
          <button className={styles.btnPrimary} onClick={() => setCreating(c => !c)}>
            {creating ? 'Annuler' : '+ Nouveau projet'}
          </button>
        </div>
      </div>

      {/* ── Formulaire nouveau projet ── */}
      {creating && (
        <form className={styles.createForm} onSubmit={handleCreate}>
          <input
            className={styles.input}
            placeholder='Nom du projet'
            value={newName}
            onChange={e => setNewName(e.target.value)}
            required
          />
          <input
            className={styles.input}
            placeholder='Objectif (décris ce que tu veux construire)'
            value={newGoal}
            onChange={e => setNewGoal(e.target.value)}
          />
          <button className={styles.btnPrimary} type='submit'>Créer</button>
        </form>
      )}

      {error && <div className={styles.errorBar}>{error}</div>}

      {/* ── Confirmations en attente (GOAP) ── */}
      {confirmations.length > 0 && (
        <div className={styles.confirmPanel}>
          <div className={styles.confirmTitle}>
            ⚡ Actions en attente de confirmation ({confirmations.length})
          </div>
          {confirmations.map(c => (
            <div key={c.id} className={styles.confirmCard}>
              <div className={styles.confirmDesc}>{c.description}</div>
              <div className={styles.confirmMeta}>
                <span className={styles.confirmBadge}>{c.action}</span>
                <span className={styles.confirmTs}>{c.ts}</span>
              </div>
              <div className={styles.confirmActions}>
                <button
                  className={styles.btnApprove}
                  disabled={confirmLoading.has(c.id)}
                  onClick={() => handleConfirm(c.id, true)}
                >
                  {confirmLoading.has(c.id) ? '…' : 'Approuver'}
                </button>
                <button
                  className={styles.btnReject}
                  disabled={confirmLoading.has(c.id)}
                  onClick={() => handleConfirm(c.id, false)}
                >
                  Rejeter
                </button>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* ── Corps — 3 colonnes ── */}
      <div className={styles.body}>

        {/* FileTree */}
        <div className={styles.tree}>
          <div className={styles.treeTitle}>Fichiers</div>
          {files.length === 0 && <div className={styles.treeEmpty}>Aucun fichier</div>}
          {files.map(f => (
            <button
              key={f}
              className={`${styles.treeItem} ${openFile === f ? styles.treeItemActive : ''}`}
              onClick={() => handleOpenFile(f)}
              title={f}
            >
              {f}
            </button>
          ))}
        </div>

        {/* Monaco Editor */}
        <div className={styles.editorPanel}>
          {openFile ? (
            <>
              <div className={styles.editorHeader}>
                <span className={styles.openFileName}>{openFile}</span>
                <button
                  className={styles.btnSave}
                  onClick={handleSave}
                  disabled={saving}
                >
                  {saving ? 'Sauvegarde…' : 'Sauvegarder'}
                </button>
              </div>
              <div className={styles.monacoWrapper}>
                <Editor
                  height='100%'
                  theme='vs-dark'
                  language={detectLanguage(openFile)}
                  value={fileContent}
                  onMount={handleEditorMount}
                  options={{
                    fontSize:         13,
                    minimap:          { enabled: false },
                    scrollBeyondLastLine: false,
                    wordWrap:         'on',
                    automaticLayout:  true,
                  }}
                />
              </div>
            </>
          ) : (
            <div className={styles.editorEmpty}>
              {slug ? 'Sélectionne un fichier' : 'Sélectionne ou crée un projet'}
            </div>
          )}
        </div>

        {/* Plan panel */}
        <div className={styles.planPanel}>
          <div className={styles.planTitle}>
            Plan
            {slug && (
              <button
                className={styles.btnAdvance}
                onClick={handleAdvance}
                disabled={advancing}
                title="Exécuter la prochaine étape"
              >
                {advancing ? '⏳' : '▶ Continuer'}
              </button>
            )}
          </div>
          {planSteps.length === 0 ? (
            <div className={styles.planEmpty}>
              {slug ? 'plan.md vide ou non trouvé' : '—'}
            </div>
          ) : (
            <ul className={styles.planList}>
              {planSteps.map((step, i) => (
                <li
                  key={i}
                  className={`${styles.planStep} ${step.done ? styles.planStepDone : ''}`}
                >
                  <span className={styles.planCheck}>{step.done ? '✓' : '○'}</span>
                  {step.label}
                </li>
              ))}
            </ul>
          )}

          {gitLog && (
            <>
              <div className={styles.planTitle} style={{ marginTop: '1rem' }}>Git log</div>
              <pre className={styles.gitLog}>{gitLog}</pre>
            </>
          )}
        </div>
      </div>

      {/* ── Terminal ── */}
      <div className={styles.terminal}>
        <div className={styles.terminalTitle}>Terminal — logs/commands.log</div>
        <pre className={styles.terminalContent}>
          {terminalLog || (slug ? '(aucune commande exécutée)' : '—')}
        </pre>
      </div>
    </div>
  )
}