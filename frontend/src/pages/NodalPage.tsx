import { useCallback, useEffect, useRef, useState, useMemo } from 'react'
import {
  ReactFlow,
  Background,
  Controls,
  MiniMap,
  useNodesState,
  useEdgesState,
  Handle,
  Position,
  type Node,
  type Edge,
  type NodeProps,
  MarkerType,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import { api } from '@/api'
import type { GraphNode, GraphNodeAgent, GraphResponse, GoapStateResponse, GoapAction } from '@/api'
import styles from './NodalPage.module.css'

// ── Node type colours ─────────────────────────────────────────────

const TYPE_COLOR: Record<string, string> = {
  crew:    '#2563eb',
  trigger: '#ea580c',
  goap:    '#dc2626',
  agent:   '#7c3aed',
  tool:    '#16a34a',
}

const TYPE_LABEL: Record<string, string> = {
  crew:    'Crew',
  trigger: 'Trigger',
  goap:    'GOAP',
  agent:   'Agent',
  tool:    'Outil',
}

// ── Custom node component ─────────────────────────────────────────

interface NodeData {
  label: string
  nodeType: string
  status: 'idle' | 'running' | 'blocked'
  description: string
  agents: GraphNodeAgent[]
  tools: string[]
  onClick: (node: GraphNode) => void
  originalNode: GraphNode
  [key: string]: unknown
}

function MnemoNode({ data }: NodeProps) {
  const d = data as NodeData
  const color = TYPE_COLOR[d.nodeType] ?? '#6b7280'
  const isRunning = d.status === 'running'

  return (
    <div
      className={`${styles.node} ${isRunning ? styles.nodeRunning : ''}`}
      style={{ borderColor: color }}
      onClick={() => d.onClick(d.originalNode)}
    >
      <Handle type="target" position={Position.Top} style={{ background: color }} />
      <div className={styles.nodeType} style={{ background: color }}>
        {TYPE_LABEL[d.nodeType] ?? d.nodeType}
      </div>
      <div className={styles.nodeLabel}>{d.label}</div>
      {isRunning && <span className={styles.runningDot} title="En cours" />}
      <Handle type="source" position={Position.Bottom} style={{ background: color }} />
    </div>
  )
}

const NODE_TYPES = { mnemoNode: MnemoNode }

// ── Helpers ───────────────────────────────────────────────────────

function toFlowNodes(graphNodes: GraphNode[], onSelect: (n: GraphNode) => void): Node[] {
  return graphNodes.map(n => ({
    id: n.id,
    type: 'mnemoNode',
    position: n.position,
    data: {
      label: n.label,
      nodeType: n.type,
      status: n.status,
      description: n.description,
      agents: n.agents,
      tools: n.tools,
      onClick: onSelect,
      originalNode: n,
    },
  }))
}

function toFlowEdges(graphEdges: GraphResponse['edges']): Edge[] {
  return graphEdges.map(e => ({
    id: e.id,
    source: e.source,
    target: e.target,
    label: e.label || undefined,
    animated: false,
    markerEnd: { type: MarkerType.ArrowClosed, color: '#94a3b8' },
    style: { stroke: '#94a3b8', strokeWidth: 1.5 },
    labelStyle: { fill: '#94a3b8', fontSize: 10 },
    labelBgStyle: { fill: '#1e293b', fillOpacity: 0.85 },
  }))
}

// ── Detail panel ──────────────────────────────────────────────────

function DetailPanel({ node, onClose }: { node: GraphNode; onClose: () => void }) {
  const color = TYPE_COLOR[node.type] ?? '#6b7280'
  return (
    <div className={styles.panel}>
      <div className={styles.panelHeader} style={{ borderLeftColor: color }}>
        <span className={styles.panelTypeTag} style={{ background: color }}>
          {TYPE_LABEL[node.type] ?? node.type}
        </span>
        <span className={styles.panelTitle}>{node.label}</span>
        <button className={styles.panelClose} onClick={onClose}>✕</button>
      </div>

      <div className={styles.panelStatus}>
        <span
          className={`${styles.statusDot} ${node.status === 'running' ? styles.statusRunning : ''}`}
        />
        {node.status === 'running' ? 'En cours' : 'Inactif'}
      </div>

      <p className={styles.panelDesc}>{node.description}</p>

      {node.agents.length > 0 && (
        <section className={styles.panelSection}>
          <h4>Agents</h4>
          {node.agents.map(a => (
            <div key={a.id} className={styles.panelItem}>
              <span className={styles.panelItemName} style={{ color: TYPE_COLOR.agent }}>
                {a.label}
              </span>
              <span className={styles.panelItemDesc}>{a.description}</span>
            </div>
          ))}
        </section>
      )}

      {node.tools.length > 0 && (
        <section className={styles.panelSection}>
          <h4>Outils</h4>
          <div className={styles.toolList}>
            {node.tools.map(t => (
              <span key={t} className={styles.toolTag}>{t}</span>
            ))}
          </div>
        </section>
      )}
    </div>
  )
}

// ── GOAP Panel ────────────────────────────────────────────────────

const PRESET_GOALS: { label: string; goal: Record<string, boolean> }[] = [
  { label: 'Générer le briefing',          goal: { briefing_fresh: true } },
  { label: 'Synchroniser la mémoire',      goal: { memory_synced: true } },
  { label: 'Consolider la mémoire (rêve)', goal: { memory_consolidated: true } },
  { label: 'Archiver les anciennes sessions', goal: { old_sessions_archived: true } },
  { label: 'Récupérer le contexte web',    goal: { web_context_available: true } },
  { label: 'Configurer l\'assistant',      goal: { assistant_config_fresh: true } },
]

function WorldStateChip({ k, v }: { k: string; v: unknown }) {
  const isTrue  = v === true
  const isFalse = v === false
  const label = k.replace(/_/g, ' ')
  return (
    <span
      className={styles.wsChip}
      style={{ borderColor: isTrue ? '#16a34a' : isFalse ? '#dc2626' : '#475569' }}
      title={JSON.stringify(v)}
    >
      <span
        className={styles.wsChipDot}
        style={{ background: isTrue ? '#16a34a' : isFalse ? '#dc2626' : '#64748b' }}
      />
      {label}
    </span>
  )
}

function ActionCard({ action, expanded, onToggle }: {
  action: GoapAction
  expanded: boolean
  onToggle: () => void
}) {
  return (
    <div className={styles.actionCard} onClick={onToggle}>
      <div className={styles.actionCardHeader}>
        <span className={styles.actionName}>{action.name}</span>
        <span className={styles.actionCost}>coût {action.cost}</span>
        {action.resource_lock && (
          <span className={styles.actionLock} title={`Verrou : ${action.resource_lock}`}>🔒</span>
        )}
        <span className={styles.actionChevron}>{expanded ? '▴' : '▾'}</span>
      </div>
      {expanded && (
        <div className={styles.actionCardBody}>
          {Object.keys(action.preconditions).length > 0 && (
            <div className={styles.actionSection}>
              <span className={styles.actionSectionLabel}>Prérequis</span>
              <div className={styles.actionConditions}>
                {Object.entries(action.preconditions).map(([k, v]) => (
                  <span key={k} className={styles.conditionTag} style={{ color: v ? '#86efac' : '#fca5a5' }}>
                    {k.replace(/_/g, ' ')} = {String(v)}
                  </span>
                ))}
              </div>
            </div>
          )}
          <div className={styles.actionSection}>
            <span className={styles.actionSectionLabel}>Effets</span>
            <div className={styles.actionConditions}>
              {Object.entries(action.effects).map(([k, v]) => (
                <span key={k} className={styles.conditionTag} style={{ color: '#67e8f9' }}>
                  {k.replace(/_/g, ' ')} = {String(v)}
                </span>
              ))}
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

function GoapPanel({ active }: { active: boolean }) {
  const [state, setState] = useState<GoapStateResponse | null>(null)
  const [selectedPreset, setSelectedPreset] = useState(0)
  const [submitting, setSubmitting] = useState(false)
  const [expandedAction, setExpandedAction] = useState<string | null>(null)
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null)

  const fetchState = useCallback(async () => {
    try {
      const data = await api.getGoapState()
      setState(data)
    } catch { /* silence */ }
  }, [])

  useEffect(() => {
    if (!active) return
    fetchState()
    intervalRef.current = setInterval(fetchState, 15_000)
    return () => { if (intervalRef.current) clearInterval(intervalRef.current) }
  }, [active, fetchState])

  const handleSetGoal = async () => {
    const goal = PRESET_GOALS[selectedPreset]?.goal
    if (!goal) return
    setSubmitting(true)
    try {
      await api.setGoapGoal(goal)
      await fetchState()
    } finally {
      setSubmitting(false)
    }
  }

  const handleClearGoal = async () => {
    setSubmitting(true)
    try {
      await api.clearGoapGoal()
      await fetchState()
    } finally {
      setSubmitting(false)
    }
  }

  const wsEntries = useMemo(
    () => Object.entries(state?.world_state ?? {}),
    [state?.world_state],
  )

  if (!state) return <div className={styles.goapLoading}>Chargement…</div>

  return (
    <div className={styles.goapPanel}>
      {/* World state */}
      <section className={styles.goapSection}>
        <h3 className={styles.goapSectionTitle}>État du monde</h3>
        <div className={styles.wsGrid}>
          {wsEntries.map(([k, v]) => <WorldStateChip key={k} k={k} v={v} />)}
          {wsEntries.length === 0 && <span className={styles.goapEmpty}>Vide</span>}
        </div>
      </section>

      {/* Active plan */}
      <section className={styles.goapSection}>
        <h3 className={styles.goapSectionTitle}>
          Plan actif
          {state.pending_goal && (
            <span className={styles.goapGoalBadge}>
              goal : {Object.entries(state.pending_goal).map(([k, v]) => `${k}=${v}`).join(', ')}
            </span>
          )}
        </h3>
        {state.plan_error && (
          <p className={styles.goapPlanError}>{state.plan_error}</p>
        )}
        {state.active_plan.length > 0 ? (
          <ol className={styles.planSteps}>
            {state.active_plan.map((step, i) => (
              <li key={i} className={styles.planStep}>
                <span className={styles.planStepNum}>{i + 1}</span>
                <span className={styles.planStepName}>{step.name}</span>
                <span className={styles.planStepCost}>coût {step.cost}</span>
              </li>
            ))}
          </ol>
        ) : !state.pending_goal ? (
          <p className={styles.goapEmpty}>Aucun goal en attente</p>
        ) : (
          <p className={styles.goapEmpty}>Goal déjà atteint</p>
        )}

        {state.pending_goal && (
          <button
            className={styles.clearGoalBtn}
            onClick={handleClearGoal}
            disabled={submitting}
          >
            Annuler le goal
          </button>
        )}
      </section>

      {/* Goal injection */}
      <section className={styles.goapSection}>
        <h3 className={styles.goapSectionTitle}>Injecter un goal</h3>
        <div className={styles.goalForm}>
          <select
            className={styles.goalSelect}
            value={selectedPreset}
            onChange={e => setSelectedPreset(Number(e.target.value))}
          >
            {PRESET_GOALS.map((p, i) => (
              <option key={i} value={i}>{p.label}</option>
            ))}
          </select>
          <button
            className={styles.goalSubmitBtn}
            onClick={handleSetGoal}
            disabled={submitting || !!state.pending_goal}
          >
            {submitting ? '…' : 'Planifier'}
          </button>
        </div>
        {state.pending_goal && (
          <p className={styles.goapEmpty} style={{ marginTop: '0.35rem' }}>
            Un goal est déjà en attente.
          </p>
        )}
      </section>

      {/* Actions catalogue */}
      <section className={styles.goapSection}>
        <h3 className={styles.goapSectionTitle}>Actions disponibles ({state.actions.length})</h3>
        {state.actions.map(a => (
          <ActionCard
            key={a.name}
            action={a}
            expanded={expandedAction === a.name}
            onToggle={() => setExpandedAction(prev => prev === a.name ? null : a.name)}
          />
        ))}
      </section>
    </div>
  )
}

// ── Main page ─────────────────────────────────────────────────────

interface Props {
  active: boolean
}

export function NodalPage({ active }: Props) {
  const [view, setView] = useState<'arch' | 'goap'>('arch')
  const [nodes, setNodes, onNodesChange] = useNodesState<Node>([])
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([])
  const [selected, setSelected] = useState<GraphNode | null>(null)
  type LiveState = { running_crews: string[]; active_project: string | null; dreamer_running: boolean }
  const [live, setLive] = useState<LiveState | null>(null)
  const [error, setError] = useState<string | null>(null)
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null)

  const handleSelect = useCallback((node: GraphNode) => {
    setSelected(prev => prev?.id === node.id ? null : node)
  }, [])

  const fetchGraph = useCallback(async () => {
    try {
      const data = await api.getGraph()
      // Normalise active_project : le backend peut retourner un objet {slug, goal, step}
      const rawProject = data.live.active_project
      let activeProject: string | null = null
      if (typeof rawProject === 'string') {
        activeProject = rawProject
      } else if (rawProject != null && typeof rawProject === 'object') {
        activeProject = (rawProject as { slug?: string }).slug ?? null
      }
      setLive({ ...data.live, active_project: activeProject })
      setNodes(toFlowNodes(data.nodes, handleSelect))
      setEdges(toFlowEdges(data.edges))
      setError(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Erreur réseau')
    }
  }, [handleSelect, setNodes, setEdges])

  useEffect(() => {
    if (!active) return
    fetchGraph()
    intervalRef.current = setInterval(fetchGraph, 30_000)
    return () => {
      if (intervalRef.current) clearInterval(intervalRef.current)
    }
  }, [active, fetchGraph])

  // Sync selected node status when live data refreshes
  useEffect(() => {
    if (!selected || !live) return
    const isRunning = live.running_crews.includes(selected.id)
    const newStatus = isRunning ? 'running' : 'idle'
    if (selected.status !== newStatus) {
      setSelected(prev => prev ? { ...prev, status: newStatus } : null)
    }
  }, [live, selected])

  if (error) {
    return (
      <div className={styles.errorState}>
        Impossible de charger le graphe : {error}
      </div>
    )
  }

  return (
    <div className={styles.root}>
      <div className={styles.liveBar}>
        {/* View toggle */}
        <div className={styles.viewToggle}>
          <button
            className={`${styles.viewBtn} ${view === 'arch' ? styles.viewBtnActive : ''}`}
            onClick={() => setView('arch')}
          >Architecture</button>
          <button
            className={`${styles.viewBtn} ${view === 'goap' ? styles.viewBtnActive : ''}`}
            onClick={() => setView('goap')}
          >GOAP</button>
        </div>

        {view === 'arch' && live && (
          <>
            {live.running_crews.length > 0 ? (
              <>
                <span className={styles.liveRunningDot} />
                <span>En cours : {live.running_crews.map(id => id.replace('crew_', '')).join(', ')}</span>
              </>
            ) : (
              <span className={styles.liveIdle}>Tous les crews inactifs</span>
            )}
            {live.active_project && (
              <span className={styles.liveProject}>Projet actif : <strong>{live.active_project}</strong></span>
            )}
          </>
        )}
      </div>

      {view === 'arch' ? (
        <>
          <div className={styles.canvas}>
            <ReactFlow
              nodes={nodes}
              edges={edges}
              onNodesChange={onNodesChange}
              onEdgesChange={onEdgesChange}
              nodeTypes={NODE_TYPES}
              fitView
              fitViewOptions={{ padding: 0.1 }}
              minZoom={0.2}
              maxZoom={2}
              proOptions={{ hideAttribution: true }}
            >
              <Background color="#334155" gap={20} />
              <Controls position="bottom-left" />
              <MiniMap
                nodeColor={n => TYPE_COLOR[(n.data as NodeData).nodeType] ?? '#6b7280'}
                maskColor="rgba(15,23,42,0.7)"
                style={{ background: '#1e293b', border: '1px solid #334155' }}
              />
            </ReactFlow>
          </div>
          {selected && (
            <DetailPanel node={selected} onClose={() => setSelected(null)} />
          )}
        </>
      ) : (
        <GoapPanel active={active && view === 'goap'} />
      )}
    </div>
  )
}