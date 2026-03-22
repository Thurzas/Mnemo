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

// ── Tree helpers ──────────────────────────────────────────────────

interface TreeNode {
  name: string
  path: string
  isDir: boolean
  children: TreeNode[]
}

function buildTree(files: string[]): TreeNode[] {
  const root: TreeNode[] = []
  const dirMap = new Map<string, TreeNode>()

  // Sort: dirs first, then files, both alphabetically
  const sorted = [...files].sort((a, b) => {
    const aDir = a.endsWith('/')
    const bDir = b.endsWith('/')
    if (aDir !== bDir) return aDir ? -1 : 1
    return a.localeCompare(b)
  })

  for (const rawPath of sorted) {
    const isDir = rawPath.endsWith('/')
    const cleanPath = isDir ? rawPath.slice(0, -1) : rawPath
    const parts = cleanPath.split('/')
    const name = parts[parts.length - 1]
    const parentPath = parts.slice(0, -1).join('/')

    const node: TreeNode = { name, path: cleanPath, isDir, children: [] }
    if (isDir) dirMap.set(cleanPath, node)

    if (parentPath === '') {
      root.push(node)
    } else {
      const parent = dirMap.get(parentPath)
      if (parent) parent.children.push(node)
      else root.push(node) // fallback: orphaned node
    }
  }
  return root
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

  // ── Tree state ────────────────────────────────────────────────
  const [expandedDirs, setExpandedDirs] = useState<Set<string>>(new Set(['']))
  const [ctxMenu, setCtxMenu] = useState<{ x: number; y: number; path: string; isDir: boolean } | null>(null)
  const [newItemParent, setNewItemParent] = useState<string | null>(null)
  const [newItemType, setNewItemType] = useState<'file' | 'dir'>('file')
  const [newItemName, setNewItemName] = useState('')

  // ── Terminal command input ────────────────────────────────────
  const [cmdInput, setCmdInput] = useState('')
  const [cmdRunning, setCmdRunning] = useState(false)

  const editorRef    = useRef<editor.IStandaloneCodeEditor | null>(null)
  const logPollRef   = useRef<ReturnType<typeof setInterval> | null>(null)
  const confPollRef  = useRef<ReturnType<typeof setInterval> | null>(null)
  const filesPollRef = useRef<ReturnType<typeof setInterval> | null>(null)

  // ── Close context menu on outside click ─────────────────────
  useEffect(() => {
    if (!ctxMenu) return
    const handler = () => setCtxMenu(null)
    document.addEventListener('click', handler)
    return () => document.removeEventListener('click', handler)
  }, [ctxMenu])

  // ── Charger la liste des projets ────────────────────────────────
  const loadProjects = useCallback(async () => {
    try {
      const { projects: p } = await api.listProjects()
      setProjects(p)
    } catch { /* silence */ }
  }, [])

  useEffect(() => {
    if (!active) return
    loadProjects()
    const interval = setInterval(loadProjects, 60_000)
    return () => clearInterval(interval)
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
      // Expand all top-level dirs by default
      const topDirs = (p.files ?? [])
        .filter(f => f.endsWith('/'))
        .map(f => f.slice(0, -1))
      setExpandedDirs(new Set(['', ...topDirs]))
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

  // ── Polling fichiers toutes les 5s (sans reset éditeur) ─────────
  useEffect(() => {
    if (filesPollRef.current) clearInterval(filesPollRef.current)
    if (!slug) return
    const refresh = async () => {
      try {
        const p = await api.getProject(slug)
        const newFiles = p.files ?? []
        setFiles(prev => {
          // Mise à jour silencieuse : ne touche pas à l'éditeur
          if (JSON.stringify(prev) === JSON.stringify(newFiles)) return prev
          return newFiles
        })
        // Si plan.md a changé et qu'il est ouvert, le recharger aussi
        if (newFiles.includes('plan.md')) {
          try {
            const r = await api.readProjectFile(slug, 'plan.md')
            setPlanSteps(parsePlan(r.content))
          } catch { /* silence */ }
        }
      } catch { /* silence */ }
    }
    filesPollRef.current = setInterval(refresh, 5_000)
    return () => { if (filesPollRef.current) clearInterval(filesPollRef.current) }
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
    if (slug && files.some(f => f === 'plan.md')) handleOpenFile('plan.md')
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
  const handleCreate = async (e: React.FormEvent<HTMLFormElement>) => {
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
      setOpenFile(null)
      setFileContent('')
      setPlanSteps([])
      setTerminalLog('')
      setGitLog('')
      await loadProjects()
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Erreur suppression')
    }
  }

  // ── Tree: toggle directory ────────────────────────────────────
  const toggleDir = (path: string) => {
    setExpandedDirs(prev => {
      const next = new Set(prev)
      if (next.has(path)) next.delete(path)
      else next.add(path)
      return next
    })
  }

  // ── Tree: context menu ────────────────────────────────────────
  const handleContextMenu = (e: React.MouseEvent, path: string, isDir: boolean) => {
    e.preventDefault()
    e.stopPropagation()
    setCtxMenu({ x: e.clientX, y: e.clientY, path, isDir })
    setNewItemParent(null)
    setNewItemName('')
  }

  // ── Tree: new file/dir confirmed ──────────────────────────────
  const handleNewItemSubmit = async (e: React.SyntheticEvent<HTMLFormElement>) => {
    e.preventDefault()
    if (!slug || newItemParent === null || !newItemName.trim()) return
    const name = newItemName.trim()
    const fullPath = newItemParent ? `${newItemParent}/${name}` : name
    setError(null)
    try {
      if (newItemType === 'dir') {
        await api.mkdir(slug, fullPath)
      } else {
        await api.writeProjectFile(slug, { path: fullPath, content: '', commit_msg: `user: new file ${fullPath}` })
      }
      await loadProject(slug)
      setNewItemParent(null)
      setNewItemName('')
      if (newItemType === 'file') {
        await handleOpenFile(fullPath)
      }
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Erreur création')
    }
  }

  // ── Tree: delete file ─────────────────────────────────────────
  const handleDeleteFile = async (path: string) => {
    if (!slug) return
    if (!window.confirm(`Supprimer « ${path} » ?`)) return
    setError(null)
    try {
      await api.deleteFile(slug, path)
      if (openFile === path) {
        setOpenFile(null)
        setFileContent('')
      }
      await loadProject(slug)
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Erreur suppression fichier')
    }
  }

  // ── Tree: delete directory (recursively via multiple deletes) ─
  const handleDeleteDir = async (dirPath: string) => {
    if (!slug) return
    if (!window.confirm(`Supprimer le dossier « ${dirPath} » et tout son contenu ?`)) return
    setError(null)
    try {
      // Delete all files under this directory
      const filesToDelete = files.filter(f => !f.endsWith('/') && f.startsWith(dirPath + '/'))
      for (const f of filesToDelete) {
        await api.deleteFile(slug, f)
      }
      // Also try to delete the .gitkeep if it exists
      try { await api.deleteFile(slug, `${dirPath}/.gitkeep`) } catch { /* ignore */ }
      if (openFile?.startsWith(dirPath + '/')) {
        setOpenFile(null)
        setFileContent('')
      }
      await loadProject(slug)
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Erreur suppression dossier')
    }
  }

  // ── Terminal: run command ─────────────────────────────────────
  const handleRunCommand = async (e: React.SyntheticEvent<HTMLFormElement>) => {
    e.preventDefault()
    if (!slug || !cmdInput.trim()) return
    setCmdRunning(true)
    try {
      await api.runProjectCommand(slug, cmdInput.trim())
      setCmdInput('')
      // terminal log will auto-refresh via poll
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Erreur commande')
    } finally {
      setCmdRunning(false)
    }
  }

  // ── Tree render ───────────────────────────────────────────────
  const renderTree = (nodes: TreeNode[], depth = 0): React.ReactNode => {
    return nodes.map(node => {
      const isExpanded = expandedDirs.has(node.path)
      const indent = depth * 12

      return (
        <div key={node.path}>
          <div
            className={`${styles.treeNode} ${!node.isDir && openFile === node.path ? styles.treeNodeActive : ''}`}
            style={{ paddingLeft: `${0.5 + indent / 16}rem` }}
            onContextMenu={e => handleContextMenu(e, node.path, node.isDir)}
          >
            {node.isDir ? (
              <button
                className={styles.treeDir}
                onClick={() => toggleDir(node.path)}
                title={node.path}
              >
                <span className={styles.treeToggle}>{isExpanded ? '▼' : '▶'}</span>
                {node.name}/
              </button>
            ) : (
              <button
                className={styles.treeFile}
                onClick={() => handleOpenFile(node.path)}
                title={node.path}
              >
                {node.name}
              </button>
            )}
          </div>

          {/* Inline new item form */}
          {newItemParent === node.path && (
            <form
              className={styles.newItemForm}
              style={{ paddingLeft: `${0.5 + (indent + 12) / 16}rem` }}
              onSubmit={handleNewItemSubmit}
            >
              <input
                className={styles.newItemInput}
                autoFocus
                placeholder={newItemType === 'dir' ? 'nom-dossier' : 'fichier.md'}
                value={newItemName}
                onChange={e => setNewItemName(e.target.value)}
                onKeyDown={e => { if (e.key === 'Escape') { setNewItemParent(null); setNewItemName('') } }}
              />
              <button className={styles.newItemOk} type='submit'>+</button>
            </form>
          )}

          {/* Children */}
          {node.isDir && isExpanded && node.children.length > 0 && (
            <div className={styles.treeChildren}>
              {renderTree(node.children, depth + 1)}
            </div>
          )}
        </div>
      )
    })
  }

  const currentProject = projects.find(p => p.slug === slug)
  const tree = buildTree(files)

  // New item form at root level (parent = '')
  const rootNewItemForm = newItemParent === '' && (
    <form
      className={styles.newItemForm}
      style={{ paddingLeft: '0.5rem' }}
      onSubmit={handleNewItemSubmit}
    >
      <input
        className={styles.newItemInput}
        autoFocus
        placeholder={newItemType === 'dir' ? 'nom-dossier' : 'fichier.md'}
        value={newItemName}
        onChange={e => setNewItemName(e.target.value)}
        onKeyDown={e => { if (e.key === 'Escape') { setNewItemParent(null); setNewItemName('') } }}
      />
      <button className={styles.newItemOk} type='submit'>+</button>
    </form>
  )

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
            Actions en attente de confirmation ({confirmations.length})
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
        <div
          className={styles.tree}
          onContextMenu={e => {
            // Right-click on empty tree area → context for root
            if (e.target === e.currentTarget) {
              handleContextMenu(e, '', true)
            }
          }}
        >
          <div className={styles.treeTitle}>Fichiers</div>
          {files.length === 0 && <div className={styles.treeEmpty}>Aucun fichier</div>}
          {rootNewItemForm}
          {renderTree(tree)}
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
        {/* ── Command input ── */}
        <form className={styles.cmdForm} onSubmit={handleRunCommand}>
          <span className={styles.cmdPrompt}>$</span>
          <input
            className={styles.cmdInput}
            value={cmdInput}
            onChange={e => setCmdInput(e.target.value)}
            placeholder={slug ? 'commande shell...' : ''}
            disabled={!slug || cmdRunning}
          />
          <button className={styles.cmdRun} type='submit' disabled={!slug || cmdRunning || !cmdInput.trim()}>
            {cmdRunning ? '⏳' : '▶'}
          </button>
        </form>
      </div>

      {/* ── Context menu ── */}
      {ctxMenu && (
        <div
          className={styles.ctxMenu}
          style={{ top: ctxMenu.y, left: ctxMenu.x }}
          onClick={e => e.stopPropagation()}
        >
          {ctxMenu.isDir ? (
            <>
              <div
                className={styles.ctxMenuItem}
                onClick={() => {
                  setNewItemParent(ctxMenu.path)
                  setNewItemType('file')
                  setNewItemName('')
                  // Ensure the dir is expanded
                  if (ctxMenu.path) setExpandedDirs(prev => new Set(prev).add(ctxMenu.path))
                  setCtxMenu(null)
                }}
              >
                Nouveau fichier
              </div>
              <div
                className={styles.ctxMenuItem}
                onClick={() => {
                  setNewItemParent(ctxMenu.path)
                  setNewItemType('dir')
                  setNewItemName('')
                  if (ctxMenu.path) setExpandedDirs(prev => new Set(prev).add(ctxMenu.path))
                  setCtxMenu(null)
                }}
              >
                Nouveau dossier
              </div>
              {ctxMenu.path !== '' && (
                <div
                  className={`${styles.ctxMenuItem} ${styles.ctxMenuItemDanger}`}
                  onClick={() => {
                    handleDeleteDir(ctxMenu.path)
                    setCtxMenu(null)
                  }}
                >
                  Supprimer dossier
                </div>
              )}
            </>
          ) : (
            <div
              className={`${styles.ctxMenuItem} ${styles.ctxMenuItemDanger}`}
              onClick={() => {
                handleDeleteFile(ctxMenu.path)
                setCtxMenu(null)
              }}
            >
              Supprimer
            </div>
          )}
        </div>
      )}
    </div>
  )
}