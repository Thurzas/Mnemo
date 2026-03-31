"""
Phase 6 — GOAP Planner : backward chaining + tri topologique.
Phase 7.3 — Enrichissement dynamique depuis le HP-KG.

Le planner prend un goal (dict de clés WorldState désirées),
un WorldState courant, et un registre d'actions, puis retourne
la séquence minimale ordonnée d'actions à exécuter.

Depuis la Phase 7.3, plan() accepte un db_path optionnel.
Si fourni, les actions sont chargées depuis le KG (préconditions/effets
appris de l'expérience) et fusionnées avec ACTION_REGISTRY.
Les actions système (FetchCalendar, GenerateBriefing…) restent statiques.
Les actions procédurales (web_search, sandbox_write…) viennent du KG.

Pas de A* — l'espace d'actions est petit (~30 actions max).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


class PlanningError(Exception):
    """Levée quand le goal est inatteignable depuis le WorldState courant."""


@dataclass
class Action:
    """
    Une action GOAP.

    Attributs :
        name          : identifiant unique
        preconditions : clés WorldState qui doivent être True avant l'exécution
        effects       : clés WorldState mises à True après l'exécution
        cost          : coût relatif (1=Python pur, 3=IO, 5=LLM)
        resource_lock : ressource exclusive (ex: "memory.db") — None si aucune
    """
    name:          str
    preconditions: dict[str, bool] = field(default_factory=dict)
    effects:       dict[str, bool] = field(default_factory=dict)
    cost:          int = 1
    resource_lock: str | None = None

    def __hash__(self) -> int:
        return hash(self.name)

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Action) and self.name == other.name


# ── Registre d'actions par défaut ─────────────────────────────

ACTION_REGISTRY: list[Action] = [
    Action(
        name          = "FetchCalendar",
        preconditions = {},
        effects       = {"calendar_fetched": True},
        cost          = 1,
    ),
    Action(
        name          = "SyncMemory",
        preconditions = {},
        effects       = {"memory_synced": True},
        cost          = 3,
        resource_lock = "memory.db",
    ),
    Action(
        name          = "AssessMemoryGaps",
        preconditions = {"memory_synced": True},
        effects       = {"memory_gaps_known": True},
        cost          = 5,
    ),
    Action(
        name          = "FillBlockingGaps",
        preconditions = {
            "memory_gaps_known": True,
            "memory_blocking_gaps": True,
            "user_online": True,
        },
        effects       = {"memory_blocking_gaps": False},
        cost          = 5,
        resource_lock = "memory.db",
    ),
    Action(
        name          = "ReconModule",
        preconditions = {},
        effects       = {"knows_module": True},
        cost          = 2,
    ),
    Action(
        name          = "CreatePlan",
        preconditions = {"knows_module": True, "memory_gaps_known": True},
        effects       = {"plan_ready": True},
        cost          = 5,
    ),
    Action(
        name          = "GenerateBriefing",
        preconditions = {
            "calendar_fetched": True,
            "memory_synced": True,
            "memory_gaps_known": True,
        },
        effects       = {"briefing_fresh": True},
        cost          = 5,
    ),
    Action(
        name          = "GenerateWeekly",
        preconditions = {"memory_synced": True},
        effects       = {"weekly_generated": True},
        cost          = 5,
    ),
    Action(
        name          = "SendDeadlineAlert",
        preconditions = {"calendar_fetched": True},
        effects       = {"deadline_alerts_sent": True},
        cost          = 2,
    ),
    # ── Phase N2 ─────────────────────────────────────────────────
    Action(
        name          = "TriggerDream",
        preconditions = {"memory_synced": True},
        effects       = {"memory_consolidated": True},
        cost          = 10,
        resource_lock = "memory.db",
    ),
    Action(
        name          = "ArchiveMemory",
        preconditions = {"memory_consolidated": True},
        effects       = {"old_sessions_archived": True},
        cost          = 5,
        resource_lock = "memory.db",
    ),
    Action(
        name          = "UpdateAssistantConfig",
        preconditions = {},
        effects       = {"assistant_config_fresh": True},
        cost          = 2,
    ),
    Action(
        name          = "FetchWebContext",
        preconditions = {},
        effects       = {"web_context_available": True},
        cost          = 4,
    ),
]


# ── Intégration KG (Phase 7.3) ────────────────────────────────

def _cost_hint(action_label: str) -> int:
    """Heuristique de coût depuis le nom de l'action (fallback si KG muet)."""
    label = action_label.lower()
    if any(k in label for k in ("llm", "crew", "generate", "briefing", "weekly")):
        return 5
    if any(k in label for k in ("shell", "write", "fetch", "commit")):
        return 3
    if any(k in label for k in ("search", "read", "extract")):
        return 2
    return 1


def build_action_from_kg(db_path: Path, action_label: str) -> Action:
    """
    Construit un objet Action depuis le KG.

    Préconditions :
      - (action)-[precondition]->(state) → {state: True}  (requis)
      - (state)-[blocks]->(action)       → {state: False} (doit être absent)
    Effets :
      - (action)-[effect]->(state)       → {state: True}
    Coût : metadata["cost"] dans kg_nodes, ou heuristique.
    """
    from Mnemo.tools.kg_tools import (
        kg_preconditions_for_action,
        kg_effects_for_action,
        kg_blocking_states,
        kg_get_node,
    )
    preconditions: dict[str, bool] = {}
    for state in kg_preconditions_for_action(db_path, action_label):
        preconditions[state] = True
    for state in kg_blocking_states(db_path, action_label):
        preconditions[state] = False

    effects: dict[str, bool] = {}
    for state in kg_effects_for_action(db_path, action_label):
        effects[state] = True

    cost = _cost_hint(action_label)
    node = kg_get_node(db_path, "action", action_label)
    if node:
        try:
            meta = json.loads(node.get("metadata") or "{}")
            cost = int(meta.get("cost", cost))
        except Exception:
            pass

    return Action(name=action_label, preconditions=preconditions,
                  effects=effects, cost=cost)


def load_kg_actions(db_path: Path) -> list[Action]:
    """
    Charge depuis le KG (user + seed) toutes les actions connues.
    Retourne une liste d'Action avec préconditions/effets réels.
    """
    from Mnemo.tools.kg_tools import kg_search_nodes
    nodes = kg_search_nodes(db_path, type_="action")
    actions = []
    for n in nodes:
        try:
            actions.append(build_action_from_kg(db_path, n["label"]))
        except Exception:
            pass
    return actions


def merge_with_registry(kg_actions: list[Action]) -> list[Action]:
    """
    Fusionne les actions KG avec ACTION_REGISTRY.
    Les actions KG remplacent celles du registre de même nom
    (préconditions/effets plus riches). Les actions système sans
    équivalent KG (FetchCalendar…) sont conservées.
    """
    kg_names = {a.name for a in kg_actions}
    system_only = [a for a in ACTION_REGISTRY if a.name not in kg_names]
    return kg_actions + system_only


# ── Planner ───────────────────────────────────────────────────

def _actions_that_produce(key: str, value: bool, actions: list[Action]) -> list[Action]:
    """Retourne les actions dont les effets incluent {key: value}."""
    return [a for a in actions if a.effects.get(key) == value]


def _satisfied(preconditions: dict[str, bool], world_state: dict) -> bool:
    """True si toutes les préconditions sont satisfaites dans world_state."""
    for key, required in preconditions.items():
        if world_state.get(key) != required:
            return False
    return True


def plan(
    goal: dict,
    world_state: dict,
    actions: list[Action] | None = None,
    db_path: Path | None = None,
) -> list[Action]:
    """
    Backward chaining : remonte depuis le goal pour construire
    la séquence minimale d'actions ordonnées.

    Args:
        goal        : dict des clés WorldState désirées, ex: {"briefing_fresh": True}
        world_state : état courant du système (world_state.json)
        actions     : registre d'actions (défaut : ACTION_REGISTRY + KG si db_path fourni)
        db_path     : chemin vers memory.db de l'utilisateur.
                      Si fourni, enrichit le registre avec les actions KG apprises.

    Returns:
        Liste ordonnée d'actions à exécuter (sans doublons).

    Raises:
        PlanningError : si une clé du goal est inatteignable.
    """
    if actions is None:
        if db_path is not None:
            try:
                kg_acts = load_kg_actions(db_path)
                actions = merge_with_registry(kg_acts)
            except Exception:
                actions = ACTION_REGISTRY
        else:
            actions = ACTION_REGISTRY

    # Clés du goal déjà satisfaites dans le WorldState courant
    unsatisfied = {
        k: v for k, v in goal.items()
        if world_state.get(k) != v
    }
    if not unsatisfied:
        return []  # goal déjà atteint

    # Backward chaining : pour chaque clé non satisfaite,
    # trouve l'action qui la produit et résout récursivement ses préconditions.
    needed: list[Action] = []
    visited: set[str]    = set()  # évite les cycles

    def _resolve(keys: dict) -> None:
        for key, required_val in keys.items():
            if world_state.get(key) == required_val:
                continue  # déjà satisfait
            if key in visited:
                continue
            visited.add(key)

            candidates = _actions_that_produce(key, required_val, actions)
            if not candidates and world_state.get(key) != required_val:
                raise PlanningError(
                    f"Impossible d'atteindre '{key}={required_val}' : "
                    f"aucune action ne produit cet effet."
                )

            # Prend le candidat au coût minimal
            best = min(candidates, key=lambda a: a.cost)

            if best not in needed:
                # Résout d'abord les préconditions de cette action
                _resolve(best.preconditions)
                if best not in needed:
                    needed.append(best)

    _resolve(unsatisfied)

    # Tri topologique : une action ne peut apparaître qu'après
    # que toutes ses préconditions soient produites.
    return _topological_sort(needed, world_state)


def _topological_sort(actions: list[Action], initial_state: dict) -> list[Action]:
    """
    Trie les actions pour que les préconditions de chacune soient
    satisfaites soit par initial_state, soit par une action précédente.
    """
    remaining = list(actions)
    ordered:  list[Action] = []
    state = dict(initial_state)
    max_passes = len(remaining) ** 2 + 1  # garde-fou anti-cycle

    passes = 0
    while remaining:
        passes += 1
        if passes > max_passes:
            names = [a.name for a in remaining]
            raise PlanningError(
                f"Cycle détecté dans le graphe d'actions : {names}"
            )
        progress = False
        for action in list(remaining):
            if _satisfied(action.preconditions, state):
                ordered.append(action)
                remaining.remove(action)
                state.update(action.effects)
                progress = True
        if not progress and remaining:
            names = [a.name for a in remaining]
            raise PlanningError(
                f"Préconditions non satisfiables pour : {names}"
            )

    return ordered
