"""
crew_graph.py — Générateur dynamique du graphe crew/agent/tool (Phase N1+)

Sources de données :
  - TRIGGER_DEFS  : nœuds déclencheurs (statiques)
  - CREW_DEFS     : crews avec leurs agents (référencés par fichier YAML + clé)
                    et leurs outils (liste de noms)
  - EDGE_DEFS     : arêtes encodant le flux d'exécution
  - YAML config/  : rôle & description des agents (lu dynamiquement)

Le layout est calculé automatiquement en couches (layer 0→3), centré
horizontalement. Ajouter un crew = l'ajouter à CREW_DEFS + EDGE_DEFS.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

CONFIG_DIR = Path(__file__).parent / "config"

# ── Constantes de layout ──────────────────────────────────────────────
_NODE_W  = 180   # largeur d'un nœud (px)
_H_GAP   = 36    # espacement horizontal entre nœuds
_LAYER_H = 220   # hauteur entre couches


# ── Triggers ──────────────────────────────────────────────────────────

TRIGGER_DEFS: list[dict] = [
    {
        "id":          "trig_user_message",
        "label":       "Message utilisateur",
        "description": "Déclenché à chaque message envoyé via /api/message ou WebSocket.",
    },
    {
        "id":          "trig_scheduler",
        "label":       "Scheduler (cron)",
        "description": "Boucle toutes les 60 s — briefing, deadline scan, autonomie GOAP.",
    },
    {
        "id":          "trig_inactivity",
        "label":       "Inactivité (30 min)",
        "description": "Déclenche DreamerCrew après 30 min d'inactivité (min 24 h entre rêves).",
    },
    {
        "id":          "trig_plan_step",
        "label":       "Avancement plan",
        "description": "Scheduler avance l'étape courante d'un projet actif dans world_state.",
    },
]


# ── Crews ─────────────────────────────────────────────────────────────
# layer : 1 = evaluation, 2 = route-targets, 3 = background/autonome
# agents: list of {id, yaml_file, yaml_key}
# tools : list of tool class names

CREW_DEFS: list[dict] = [
    # ── layer 1 — evaluation ─────────────────────────────────────────
    {
        "id":          "crew_evaluation",
        "label":       "EvaluationCrew",
        "description": "Analyse l'intent du message et produit un JSON de routing.",
        "layer":       1,
        "agents": [
            {"id": "agent_evaluator",   "yaml_file": "conversation", "yaml_key": "evaluator"},
        ],
        "tools": [],
    },

    # ── layer 2 — route targets ───────────────────────────────────────
    {
        "id":          "crew_conversation",
        "label":       "ConversationCrew",
        "description": "Récupère la mémoire pertinente (FTS5 + vecteurs) et génère la réponse.",
        "layer":       2,
        "agents": [
            {"id": "agent_memory_retriever", "yaml_file": "conversation", "yaml_key": "memory_retriever"},
            {"id": "agent_main_agent",       "yaml_file": "conversation", "yaml_key": "main_agent"},
        ],
        "tools": [
            "RetrieveMemoryTool", "GetSessionMemoryTool",
            "ListDocumentsTool", "GetCalendarTool", "WebSearchTool",
        ],
    },
    {
        "id":          "crew_shell",
        "label":       "ShellCrew",
        "description": "Exécute des commandes shell après validation par la whitelist.",
        "layer":       2,
        "agents": [
            {"id": "agent_shell_executor", "yaml_file": "shell", "yaml_key": "shell_executor"},
        ],
        "tools": ["ShellExecuteTool", "ReadPdfTool", "FileWriterTool"],
    },
    {
        "id":          "crew_notewriter",
        "label":       "NoteWriterCrew",
        "description": "Écrit une note structurée directement dans memory.md.",
        "layer":       2,
        "agents": [
            {"id": "agent_note_writer", "yaml_file": "note", "yaml_key": "note_writer"},
        ],
        "tools": ["UpdateMarkdownTool", "SyncMemoryDbTool"],
    },
    {
        "id":          "crew_scheduler_crew",
        "label":       "SchedulerCrew",
        "description": "Traduit une demande en langage naturel en tâche planifiée (DB).",
        "layer":       2,
        "agents": [
            {"id": "agent_scheduler_agent", "yaml_file": "scheduler", "yaml_key": "scheduler_agent"},
        ],
        "tools": [],
    },
    {
        "id":          "crew_calendar",
        "label":       "CalendarWriteCrew",
        "description": "Gestion des événements calendrier ICS (create/update/delete).",
        "layer":       2,
        "agents": [
            {"id": "agent_calendar_writer", "yaml_file": "calendar_write", "yaml_key": "calendar_writer_agent"},
        ],
        "tools": [],
    },
    {
        "id":          "crew_reconnaissance",
        "label":       "ReconnaissanceCrew",
        "description": "Scanne le filesystem d'un projet et synthétise le contexte technique.",
        "layer":       2,
        "agents": [
            {"id": "agent_recon_agent", "yaml_file": "recon", "yaml_key": "recon_agent"},
        ],
        "tools": [],
    },
    {
        "id":          "crew_planner",
        "label":       "PlannerCrew",
        "description": "Décompose un objectif en plan d'étapes annotées (HTN, max_depth=2).",
        "layer":       2,
        "agents": [
            {"id": "agent_planner_agent", "yaml_file": "planner", "yaml_key": "planner_agent"},
        ],
        "tools": [],
    },

    # ── layer 3 — background / autonome ──────────────────────────────
    {
        "id":          "crew_consolidation",
        "label":       "ConsolidationCrew",
        "description": "Fin de session : extrait les faits et les écrit dans memory.md.",
        "layer":       3,
        "agents": [
            {"id": "agent_session_consolidator", "yaml_file": "consolidation", "yaml_key": "session_consolidator"},
            {"id": "agent_memory_writer",         "yaml_file": "consolidation", "yaml_key": "memory_writer"},
        ],
        "tools": ["UpdateMarkdownTool", "SyncMemoryDbTool"],
    },
    {
        "id":          "crew_curiosity",
        "label":       "CuriosityCrew",
        "description": "Post-consolidation : détecte les lacunes mémoire et formule des questions.",
        "layer":       3,
        "agents": [
            {"id": "agent_gap_detector",       "yaml_file": "curiosity", "yaml_key": "gap_detector"},
            {"id": "agent_questionnaire_agent", "yaml_file": "curiosity", "yaml_key": "questionnaire_agent"},
        ],
        "tools": ["UpdateMarkdownTool", "SyncMemoryDbTool"],
    },
    {
        "id":          "crew_briefing",
        "label":       "BriefingCrew",
        "description": "Génère briefing.md (quotidien) et weekly.md (hebdomadaire).",
        "layer":       3,
        "agents": [
            {"id": "agent_briefing_agent", "yaml_file": "briefing", "yaml_key": "briefing_agent"},
        ],
        "tools": [],
    },
    {
        "id":          "crew_sandbox",
        "label":       "SandboxCrew",
        "description": "Travail isolé sur un projet sandbox : lecture/écriture/shell confinés.",
        "layer":       3,
        "agents": [
            {"id": "agent_sandbox_agent", "yaml_file": "sandbox", "yaml_key": "sandbox_agent"},
        ],
        "tools": ["SandboxReadTool", "SandboxWriteTool", "SandboxShellTool", "SandboxListTool"],
    },
    {
        "id":          "crew_dreamer",
        "label":       "DreamerCrew",
        "description": "Consolidation mémoire long terme : détecte doublons, applique patches, archive.",
        "layer":       3,
        "agents": [
            {"id": "agent_memory_analyst", "yaml_file": "dreamer", "yaml_key": "memory_analyst"},
            {"id": "agent_memory_patcher", "yaml_file": "dreamer", "yaml_key": "memory_patcher"},
        ],
        "tools": ["ApplyDreamPatchesTool"],
    },
]


# ── Arêtes ────────────────────────────────────────────────────────────

EDGE_DEFS: list[dict] = [
    # Triggers → EvaluationCrew
    {"id": "e_msg_eval",    "source": "trig_user_message", "target": "crew_evaluation",     "label": ""},
    # Scheduler → BriefingCrew
    {"id": "e_sched_brief", "source": "trig_scheduler",    "target": "crew_briefing",       "label": "daily/weekly"},
    # Inactivity → DreamerCrew
    {"id": "e_idle_dream",  "source": "trig_inactivity",   "target": "crew_dreamer",        "label": "idle 30 min"},
    # Plan step → Planner / Recon / Sandbox
    {"id": "e_plan_plan",   "source": "trig_plan_step",    "target": "crew_planner",        "label": "new project"},
    {"id": "e_plan_recon",  "source": "trig_plan_step",    "target": "crew_reconnaissance", "label": "recon step"},
    {"id": "e_plan_sand",   "source": "trig_plan_step",    "target": "crew_sandbox",        "label": "shell/note step"},
    # EvaluationCrew routing
    {"id": "e_eval_conv",   "source": "crew_evaluation",   "target": "crew_conversation",   "label": "conversation"},
    {"id": "e_eval_shell",  "source": "crew_evaluation",   "target": "crew_shell",          "label": "shell"},
    {"id": "e_eval_note",   "source": "crew_evaluation",   "target": "crew_notewriter",     "label": "note"},
    {"id": "e_eval_sched",  "source": "crew_evaluation",   "target": "crew_scheduler_crew", "label": "scheduler"},
    {"id": "e_eval_cal",    "source": "crew_evaluation",   "target": "crew_calendar",       "label": "calendar"},
    {"id": "e_eval_plan",   "source": "crew_evaluation",   "target": "crew_planner",        "label": "plan"},
    # Session lifecycle
    {"id": "e_conv_consol", "source": "crew_conversation", "target": "crew_consolidation",  "label": "fin de session"},
    {"id": "e_consol_cur",  "source": "crew_consolidation","target": "crew_curiosity",      "label": "post-consolidation"},
    # Planner → Recon → Sandbox
    {"id": "e_plan_rec2",   "source": "crew_planner",      "target": "crew_reconnaissance", "label": "needs_recon"},
    {"id": "e_plan_sand2",  "source": "crew_planner",      "target": "crew_sandbox",        "label": "exécution"},
]


# ── Lecture YAML ──────────────────────────────────────────────────────

_yaml_cache: dict[str, dict] = {}


def _load_yaml(prefix: str) -> dict:
    """Charge config/{prefix}_agents.yaml avec cache en mémoire."""
    if prefix in _yaml_cache:
        return _yaml_cache[prefix]
    path = CONFIG_DIR / f"{prefix}_agents.yaml"
    if not path.exists():
        _yaml_cache[prefix] = {}
        return {}
    try:
        import yaml  # PyYAML — disponible via crewai
        with path.open(encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        _yaml_cache[prefix] = data
        return data
    except Exception:
        _yaml_cache[prefix] = {}
        return {}


def _agent_description(yaml_file: str, yaml_key: str) -> str:
    """Retourne le `role` YAML de l'agent comme description courte."""
    data = _load_yaml(yaml_file)
    agent = data.get(yaml_key, {})
    if not isinstance(agent, dict):
        return ""
    role = agent.get("role", "")
    # Le champ role peut contenir des templates {assistant_name} — on les garde tels quels
    return str(role).strip()


# ── Calcul de layout en couches ───────────────────────────────────────

def _compute_positions(all_nodes: list[dict]) -> dict[str, dict[str, int]]:
    """
    Calcule les positions x/y pour chaque nœud en les répartissant par couche.
    Couche 0 = triggers, 1 = evaluation, 2 = route-targets, 3 = background.
    Chaque couche est centrée horizontalement par rapport à la couche la plus large.
    """
    layers: dict[int, list[str]] = {}
    for node in all_nodes:
        layer = node["_layer"]
        layers.setdefault(layer, []).append(node["id"])

    # Largeur totale de la couche la plus large
    max_w = max(
        len(ids) * _NODE_W + max(0, len(ids) - 1) * _H_GAP
        for ids in layers.values()
    )

    positions: dict[str, dict[str, int]] = {}
    for layer_idx in sorted(layers.keys()):
        ids   = layers[layer_idx]
        row_w = len(ids) * _NODE_W + max(0, len(ids) - 1) * _H_GAP
        start = (max_w - row_w) // 2          # centrage
        y     = layer_idx * _LAYER_H
        for i, nid in enumerate(ids):
            positions[nid] = {"x": start + i * (_NODE_W + _H_GAP), "y": y}

    return positions


# ── Build graph ───────────────────────────────────────────────────────

def build_graph(world_state: dict | None = None) -> dict:
    """
    Construit le graphe complet (nœuds + arêtes + live status).

    world_state : dict lu depuis world_state.json utilisateur (optionnel).
                  Utilisé pour déterminer quels crews sont "running".
    """
    ws = world_state or {}

    # ── Live status ───────────────────────────────────────────────────
    running_set: set[str] = set()
    if ws.get("dreamer_running"):
        running_set.add("crew_dreamer")

    active_project_raw = ws.get("active_project")
    if isinstance(active_project_raw, dict):
        active_project: str | None = active_project_raw.get("slug")
    elif isinstance(active_project_raw, str):
        active_project = active_project_raw
    else:
        active_project = None

    if active_project:
        running_set.update({"crew_sandbox", "crew_planner"})

    # ── Construire la liste plate de tous les nœuds (pour le layout) ─
    flat: list[dict[str, Any]] = []

    for t in TRIGGER_DEFS:
        flat.append({**t, "_layer": 0, "_type": "trigger"})

    for c in CREW_DEFS:
        flat.append({**c, "_layer": c["layer"], "_type": "crew"})

    # ── Positions ─────────────────────────────────────────────────────
    positions = _compute_positions(flat)

    # ── Nœuds finaux ──────────────────────────────────────────────────
    nodes: list[dict] = []

    for t in TRIGGER_DEFS:
        nodes.append({
            "id":          t["id"],
            "type":        "trigger",
            "label":       t["label"],
            "description": t["description"],
            "position":    positions[t["id"]],
            "status":      "idle",
            "agents":      [],
            "tools":       [],
        })

    for c in CREW_DEFS:
        # Agents avec description lue depuis YAML
        agents = []
        for a in c["agents"]:
            desc = _agent_description(a["yaml_file"], a["yaml_key"])
            agents.append({"id": a["id"], "label": a["yaml_key"], "description": desc})

        nodes.append({
            "id":          c["id"],
            "type":        "crew",
            "label":       c["label"],
            "description": c["description"],
            "position":    positions[c["id"]],
            "status":      "running" if c["id"] in running_set else "idle",
            "agents":      agents,
            "tools":       c.get("tools", []),
        })

    return {
        "nodes": nodes,
        "edges": EDGE_DEFS,
        "live":  {
            "running_crews": list(running_set),
            "active_project": active_project,
            "dreamer_running": bool(ws.get("dreamer_running", False)),
        },
    }