"""
Tests Phase 7.3 — GOAP enrichi par le KG

Couvre :
  - _cost_hint         : heuristique de coût
  - build_action_from_kg : préconditions / effets / blockers / coût metadata
  - load_kg_actions    : chargement de toutes les actions KG
  - merge_with_registry: fusion KG + ACTION_REGISTRY (priorité KG, système conservé)
  - plan() + db_path   : planification enrichie depuis le KG
  - plan() sans db_path: régression — comportement identique à avant la Phase 7.3

Aucun LLM requis.
"""
import pytest
from pathlib import Path
from unittest.mock import patch

from Mnemo.init_db import init_db, init_kg_db
from Mnemo.tools.kg_tools import kg_add_triplet, kg_add_node
from Mnemo.goap.planner import (
    Action, ACTION_REGISTRY, PlanningError,
    _cost_hint, build_action_from_kg, load_kg_actions,
    merge_with_registry, plan,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _no_real_seed():
    with patch("Mnemo.tools.kg_tools.SEED_DB_PATH", Path("/nonexistent/kg_seed.db")):
        yield


@pytest.fixture
def db(tmp_path) -> Path:
    p = tmp_path / "memory.db"
    init_db(db_path=p)
    return p


@pytest.fixture
def db_with_actions(db) -> Path:
    """DB peuplée avec un graphe d'actions complet pour les tests planner."""
    # web_search : précondition web_available, effet web_results_ready
    kg_add_triplet(db, "action", "web_search", "precondition", "state", "web_available")
    kg_add_triplet(db, "action", "web_search", "effect",       "state", "web_results_ready")

    # web_fetch : précondition web_results_ready, effet page_content_ready
    kg_add_triplet(db, "action", "web_fetch",  "precondition", "state", "web_results_ready")
    kg_add_triplet(db, "action", "web_fetch",  "effect",       "state", "page_content_ready")

    # sandbox_write : précondition sandbox_open, effet file_created
    # + bloqué par sandbox_readonly
    kg_add_triplet(db, "action", "sandbox_write", "precondition", "state", "sandbox_open")
    kg_add_triplet(db, "action", "sandbox_write", "effect",       "state", "file_created")
    kg_add_triplet(db, "state",  "sandbox_readonly", "blocks",    "action","sandbox_write")

    return db


# ══════════════════════════════════════════════════════════════════════════════
# 1. _cost_hint
# ══════════════════════════════════════════════════════════════════════════════

class TestCostHint:

    def test_shell_cout_3(self):
        assert _cost_hint("sandbox_shell: npm install") == 3

    def test_write_cout_3(self):
        assert _cost_hint("sandbox_write") == 3

    def test_search_cout_2(self):
        assert _cost_hint("web_search") == 2

    def test_read_cout_2(self):
        assert _cost_hint("sandbox_read") == 2

    def test_generate_cout_5(self):
        assert _cost_hint("GenerateBriefing") == 5

    def test_inconnu_cout_1(self):
        assert _cost_hint("action_inconnue") == 1


# ══════════════════════════════════════════════════════════════════════════════
# 2. build_action_from_kg
# ══════════════════════════════════════════════════════════════════════════════

class TestBuildActionFromKg:

    def test_preconditions_chargees(self, db_with_actions):
        action = build_action_from_kg(db_with_actions, "web_search")
        assert action.preconditions.get("web_available") is True

    def test_effets_charges(self, db_with_actions):
        action = build_action_from_kg(db_with_actions, "web_search")
        assert action.effects.get("web_results_ready") is True

    def test_blocker_en_precondition_false(self, db_with_actions):
        action = build_action_from_kg(db_with_actions, "sandbox_write")
        assert action.preconditions.get("sandbox_readonly") is False

    def test_cout_heuristique_web_search(self, db_with_actions):
        action = build_action_from_kg(db_with_actions, "web_search")
        assert action.cost == 2

    def test_cout_depuis_metadata(self, db):
        import json
        kg_add_node(db, "action", "custom_action", metadata={"cost": 4})
        action = build_action_from_kg(db, "custom_action")
        assert action.cost == 4

    def test_action_inconnue_retourne_action_vide(self, db):
        action = build_action_from_kg(db, "action_fantôme")
        assert action.name == "action_fantôme"
        assert action.preconditions == {}
        assert action.effects == {}

    def test_chaine_preconditions(self, db_with_actions):
        """web_fetch nécessite web_results_ready."""
        action = build_action_from_kg(db_with_actions, "web_fetch")
        assert action.preconditions.get("web_results_ready") is True
        assert action.effects.get("page_content_ready") is True


# ══════════════════════════════════════════════════════════════════════════════
# 3. load_kg_actions
# ══════════════════════════════════════════════════════════════════════════════

class TestLoadKgActions:

    def test_charge_toutes_les_actions(self, db_with_actions):
        actions = load_kg_actions(db_with_actions)
        names = {a.name for a in actions}
        assert "web_search" in names
        assert "web_fetch" in names
        assert "sandbox_write" in names

    def test_ne_charge_pas_les_states(self, db_with_actions):
        actions = load_kg_actions(db_with_actions)
        names = {a.name for a in actions}
        assert "web_available" not in names
        assert "sandbox_open" not in names

    def test_db_vide_retourne_liste_vide(self, db):
        assert load_kg_actions(db) == []

    def test_retourne_des_action_objects(self, db_with_actions):
        actions = load_kg_actions(db_with_actions)
        assert all(isinstance(a, Action) for a in actions)


# ══════════════════════════════════════════════════════════════════════════════
# 4. merge_with_registry
# ══════════════════════════════════════════════════════════════════════════════

class TestMergeWithRegistry:

    def test_actions_systeme_conservees(self):
        kg_acts = [Action("web_search", effects={"web_results_ready": True})]
        merged = merge_with_registry(kg_acts)
        names = {a.name for a in merged}
        assert "FetchCalendar" in names
        assert "GenerateBriefing" in names
        assert "web_search" in names

    def test_kg_remplace_registre_meme_nom(self):
        """Si KG et registre ont la même action, la version KG est utilisée."""
        kg_version = Action(
            "SyncMemory",
            preconditions={"extra_precond": True},
            effects={"memory_synced": True},
        )
        merged = merge_with_registry([kg_version])
        sync_actions = [a for a in merged if a.name == "SyncMemory"]
        assert len(sync_actions) == 1
        assert "extra_precond" in sync_actions[0].preconditions

    def test_pas_de_doublons(self, db_with_actions):
        kg_acts = load_kg_actions(db_with_actions)
        merged = merge_with_registry(kg_acts)
        names = [a.name for a in merged]
        assert len(names) == len(set(names))

    def test_registre_vide_kg_retourne_kg(self):
        kg_acts = [Action("test_action")]
        with patch("Mnemo.goap.planner.ACTION_REGISTRY", []):
            merged = merge_with_registry(kg_acts)
        assert merged == kg_acts


# ══════════════════════════════════════════════════════════════════════════════
# 5. plan() avec db_path
# ══════════════════════════════════════════════════════════════════════════════

class TestPlanWithKg:

    def test_plan_web_search_puis_fetch(self, db_with_actions):
        """
        Goal: page_content_ready=True
        World: web_available=True, sandbox_open=True
        KG doit permettre : web_search → web_fetch
        """
        goal        = {"page_content_ready": True}
        world_state = {"web_available": True, "sandbox_open": True}
        result = plan(goal, world_state, db_path=db_with_actions)
        names = [a.name for a in result]
        assert "web_search" in names
        assert "web_fetch" in names
        assert names.index("web_search") < names.index("web_fetch")

    def test_plan_sandbox_write(self, db_with_actions):
        goal        = {"file_created": True}
        world_state = {"sandbox_open": True, "sandbox_readonly": False}
        result = plan(goal, world_state, db_path=db_with_actions)
        assert any(a.name == "sandbox_write" for a in result)

    def test_plan_bloque_par_readonly(self, db_with_actions):
        """sandbox_readonly=True bloque sandbox_write → PlanningError."""
        goal        = {"file_created": True}
        world_state = {"sandbox_open": True, "sandbox_readonly": True}
        with pytest.raises(PlanningError):
            plan(goal, world_state, db_path=db_with_actions)

    def test_goal_deja_satisfait(self, db_with_actions):
        goal        = {"web_results_ready": True}
        world_state = {"web_results_ready": True}
        result = plan(goal, world_state, db_path=db_with_actions)
        assert result == []

    def test_plan_sans_db_path_utilise_registre(self):
        """Sans db_path, plan() se comporte exactement comme avant la Phase 7.3."""
        goal        = {"memory_synced": True}
        world_state = {}
        result = plan(goal, world_state)
        assert any(a.name == "SyncMemory" for a in result)

    def test_plan_systeme_toujours_disponible_avec_kg(self, db_with_actions):
        """Les actions système restent planifiables même avec db_path."""
        goal        = {"memory_synced": True}
        world_state = {}
        result = plan(goal, world_state, db_path=db_with_actions)
        assert any(a.name == "SyncMemory" for a in result)

    def test_db_path_invalide_fallback_registre(self, tmp_path):
        """db_path inexistant → pas de crash, fallback sur ACTION_REGISTRY."""
        absent = tmp_path / "nonexistent.db"
        goal        = {"memory_synced": True}
        world_state = {}
        result = plan(goal, world_state, db_path=absent)
        assert any(a.name == "SyncMemory" for a in result)