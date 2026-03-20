"""
Tests Phase 6 — GOAP Planner : backward chaining + tri topologique.
Niveau 1 — aucun LLM, états simulés.
"""
import pytest
from Mnemo.goap.planner import Action, PlanningError, plan, _topological_sort


# ── Fixtures d'actions ────────────────────────────────────────

@pytest.fixture()
def simple_actions():
    """Graphe simple : A → B → C (chaîne linéaire)."""
    return [
        Action("A", preconditions={},            effects={"a_done": True}, cost=1),
        Action("B", preconditions={"a_done": True}, effects={"b_done": True}, cost=1),
        Action("C", preconditions={"b_done": True}, effects={"c_done": True}, cost=1),
    ]


@pytest.fixture()
def diamond_actions():
    """
    Graphe en diamant :
      Root → Left → Goal
      Root → Right → Goal
    """
    return [
        Action("Root",  preconditions={},                effects={"root": True},  cost=1),
        Action("Left",  preconditions={"root": True},    effects={"left": True},  cost=3),
        Action("Right", preconditions={"root": True},    effects={"right": True}, cost=1),
        Action("Goal",  preconditions={"left": True, "right": True}, effects={"goal": True}, cost=1),
    ]


# ── Goal déjà atteint ─────────────────────────────────────────

class TestGoalAlreadySatisfied:
    def test_retourne_liste_vide(self, simple_actions):
        result = plan({"a_done": True}, {"a_done": True}, simple_actions)
        assert result == []

    def test_goal_partiel_satisfait(self, simple_actions):
        # a_done déjà True → seul b_done à résoudre
        result = plan({"a_done": True, "b_done": True},
                      {"a_done": True, "b_done": True}, simple_actions)
        assert result == []


# ── Chaîne linéaire ───────────────────────────────────────────

class TestLinearChain:
    def test_goal_c_depuis_etat_vide(self, simple_actions):
        result = plan({"c_done": True}, {}, simple_actions)
        names  = [a.name for a in result]
        assert names == ["A", "B", "C"]

    def test_goal_b_depuis_etat_vide(self, simple_actions):
        result = plan({"b_done": True}, {}, simple_actions)
        names  = [a.name for a in result]
        assert names == ["A", "B"]

    def test_goal_c_depuis_a_fait(self, simple_actions):
        result = plan({"c_done": True}, {"a_done": True}, simple_actions)
        names  = [a.name for a in result]
        assert names == ["B", "C"]

    def test_pas_de_doublons(self, simple_actions):
        result = plan({"c_done": True}, {}, simple_actions)
        assert len(result) == len(set(a.name for a in result))


# ── Graphe en diamant ─────────────────────────────────────────

class TestDiamondGraph:
    def test_toutes_les_actions_incluses(self, diamond_actions):
        result = plan({"goal": True}, {}, diamond_actions)
        names  = {a.name for a in result}
        assert names == {"Root", "Left", "Right", "Goal"}

    def test_root_en_premier(self, diamond_actions):
        result = plan({"goal": True}, {}, diamond_actions)
        assert result[0].name == "Root"

    def test_goal_en_dernier(self, diamond_actions):
        result = plan({"goal": True}, {}, diamond_actions)
        assert result[-1].name == "Goal"


# ── Sélection par coût minimal ────────────────────────────────

class TestCostSelection:
    def test_prend_candidat_moins_cher(self):
        actions = [
            Action("Cheap",     preconditions={}, effects={"x": True}, cost=1),
            Action("Expensive", preconditions={}, effects={"x": True}, cost=5),
        ]
        result = plan({"x": True}, {}, actions)
        assert result[0].name == "Cheap"


# ── Erreurs ───────────────────────────────────────────────────

class TestPlanningErrors:
    def test_goal_inatteignable(self, simple_actions):
        with pytest.raises(PlanningError):
            plan({"inexistant": True}, {}, simple_actions)

    def test_precondition_non_satisfiable(self):
        actions = [
            Action("B", preconditions={"a": True}, effects={"b": True}, cost=1),
            # "a" n'est produit par aucune action
        ]
        with pytest.raises(PlanningError):
            plan({"b": True}, {}, actions)


# ── Registre par défaut ───────────────────────────────────────

class TestActionRegistry:
    def test_briefing_depuis_etat_vide(self):
        from Mnemo.goap.planner import ACTION_REGISTRY
        result = plan({"briefing_fresh": True}, {}, ACTION_REGISTRY)
        names  = [a.name for a in result]
        assert "FetchCalendar"    in names
        assert "SyncMemory"       in names
        assert "AssessMemoryGaps" in names
        assert "GenerateBriefing" in names

    def test_ordre_briefing_correct(self):
        from Mnemo.goap.planner import ACTION_REGISTRY
        result = plan({"briefing_fresh": True}, {}, ACTION_REGISTRY)
        names  = [a.name for a in result]
        assert names.index("SyncMemory")       < names.index("AssessMemoryGaps")
        assert names.index("AssessMemoryGaps") < names.index("GenerateBriefing")
        assert names.index("FetchCalendar")    < names.index("GenerateBriefing")

    def test_deadline_alert_depuis_etat_vide(self):
        from Mnemo.goap.planner import ACTION_REGISTRY
        result = plan({"deadline_alerts_sent": True}, {}, ACTION_REGISTRY)
        names  = [a.name for a in result]
        assert "FetchCalendar"    in names
        assert "SendDeadlineAlert" in names

    def test_create_plan_depuis_etat_vide(self):
        from Mnemo.goap.planner import ACTION_REGISTRY
        result = plan({"plan_ready": True}, {}, ACTION_REGISTRY)
        names  = [a.name for a in result]
        assert "ReconModule"      in names
        assert "SyncMemory"       in names
        assert "AssessMemoryGaps" in names
        assert "CreatePlan"       in names

    def test_goal_deja_satisfait_dans_world_state(self):
        from Mnemo.goap.planner import ACTION_REGISTRY
        ws = {"briefing_fresh": True}
        result = plan({"briefing_fresh": True}, ws, ACTION_REGISTRY)
        assert result == []

    def test_goal_partiel_depuis_world_state_partiel(self):
        from Mnemo.goap.planner import ACTION_REGISTRY
        # Calendar déjà fetché → FetchCalendar ne doit pas apparaître
        ws = {"calendar_fetched": True}
        result = plan({"briefing_fresh": True}, ws, ACTION_REGISTRY)
        names  = [a.name for a in result]
        assert "FetchCalendar" not in names
        assert "GenerateBriefing" in names

    def test_fill_blocking_gaps_si_user_online(self):
        from Mnemo.goap.planner import ACTION_REGISTRY
        ws = {
            "memory_synced":        True,
            "memory_gaps_known":    True,
            "memory_blocking_gaps": True,
            "user_online":          True,
        }
        result = plan({"memory_blocking_gaps": False}, ws, ACTION_REGISTRY)
        names  = [a.name for a in result]
        assert "FillBlockingGaps" in names

    def test_fill_blocking_gaps_impossible_si_user_offline(self):
        from Mnemo.goap.planner import ACTION_REGISTRY
        ws = {
            "memory_synced":        True,
            "memory_gaps_known":    True,
            "memory_blocking_gaps": True,
            "user_online":          False,
        }
        with pytest.raises(PlanningError):
            plan({"memory_blocking_gaps": False}, ws, ACTION_REGISTRY)

    def test_multi_goal(self):
        from Mnemo.goap.planner import ACTION_REGISTRY
        result = plan(
            {"briefing_fresh": True, "deadline_alerts_sent": True},
            {},
            ACTION_REGISTRY,
        )
        names = [a.name for a in result]
        assert "GenerateBriefing"  in names
        assert "SendDeadlineAlert" in names
        # FetchCalendar partagé — pas de doublon
        assert names.count("FetchCalendar") == 1


# ── Tri topologique ───────────────────────────────────────────

class TestTopologicalSort:
    def test_ordre_respecte_preconditions(self):
        actions = [
            Action("B", preconditions={"a": True}, effects={"b": True}),
            Action("A", preconditions={},           effects={"a": True}),
        ]
        result = _topological_sort(actions, {})
        assert result[0].name == "A"
        assert result[1].name == "B"

    def test_action_sans_preconditions_en_premier(self):
        actions = [
            Action("Dep",  preconditions={"x": True}, effects={"y": True}),
            Action("Root", preconditions={},            effects={"x": True}),
        ]
        result = _topological_sort(actions, {})
        assert result[0].name == "Root"
