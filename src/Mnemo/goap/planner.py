"""
Phase 6 — GOAP Planner : backward chaining + tri topologique.

Le planner prend un goal (dict de clés WorldState désirées),
un WorldState courant, et un registre d'actions, puis retourne
la séquence minimale ordonnée d'actions à exécuter.

Pas de A* pour l'instant — l'espace d'actions est petit (~15 actions).
Le backward chaining suffit et est O(actions²) au pire.
"""
from __future__ import annotations

from dataclasses import dataclass, field


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
]


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
) -> list[Action]:
    """
    Backward chaining : remonte depuis le goal pour construire
    la séquence minimale d'actions ordonnées.

    Args:
        goal        : dict des clés WorldState désirées, ex: {"briefing_fresh": True}
        world_state : état courant du système (world_state.json)
        actions     : registre d'actions (défaut : ACTION_REGISTRY)

    Returns:
        Liste ordonnée d'actions à exécuter (sans doublons).

    Raises:
        PlanningError : si une clé du goal est inatteignable.
    """
    if actions is None:
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
