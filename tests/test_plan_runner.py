"""
Tests Phase 6 — PlanRunner : exécution étape par étape, crash recovery, bloquants.
Niveau 1 (logique pure) + Niveau 3 (crews mockés).
"""
import pytest
from unittest.mock import patch, MagicMock
from pathlib import Path


# ── Fixtures ──────────────────────────────────────────────────

@pytest.fixture()
def runner_env(tmp_path, monkeypatch):
    import Mnemo.tools.plan_tools as pt
    monkeypatch.setattr(pt, "get_data_dir", lambda: tmp_path)
    return tmp_path


def _make_plan(tmp_path, steps, crew_targets=None):
    from Mnemo.tools.plan_tools import PlanStore
    targets = crew_targets or {}
    return PlanStore.create("goal test", steps, crew_targets=targets)


# ── _get_crew_target ──────────────────────────────────────────

class TestGetCrewTarget:
    def test_extrait_crew_annote(self):
        from Mnemo.tools.plan_tools import PlanRunner
        assert PlanRunner._get_crew_target("Lire le code — crew : shell") == "shell"

    def test_defaut_conversation_si_absent(self):
        from Mnemo.tools.plan_tools import PlanRunner
        assert PlanRunner._get_crew_target("Lire le code") == "conversation"

    def test_insensible_a_la_casse(self):
        from Mnemo.tools.plan_tools import PlanRunner
        assert PlanRunner._get_crew_target("Étape — crew : Shell") == "shell"


# ── _clean_step ───────────────────────────────────────────────

class TestCleanStep:
    def test_retire_annotation_crew(self):
        from Mnemo.tools.plan_tools import PlanRunner
        assert PlanRunner._clean_step("Étape 1 — crew : conversation") == "Étape 1"

    def test_sans_annotation_inchange(self):
        from Mnemo.tools.plan_tools import PlanRunner
        assert PlanRunner._clean_step("Étape 1") == "Étape 1"


# ── run() — cas nominaux ──────────────────────────────────────

class TestPlanRunnerNominal:
    def test_etape_unique_marquee_done(self, runner_env):
        from Mnemo.tools.plan_tools import PlanRunner, PlanStore
        plan = _make_plan(runner_env, ["Faire X"])
        with patch("Mnemo.crew.ConversationCrew") as MockCrew:
            MockCrew.return_value.crew.return_value.kickoff.return_value = \
                MagicMock(raw="réponse ok")
            PlanRunner().run(plan, session_id="s1")
        assert PlanStore.is_complete(plan)

    def test_toutes_etapes_executees(self, runner_env):
        from Mnemo.tools.plan_tools import PlanRunner, PlanStore
        plan = _make_plan(runner_env, ["Étape A", "Étape B", "Étape C"])
        with patch("Mnemo.crew.ConversationCrew") as MockCrew:
            MockCrew.return_value.crew.return_value.kickoff.return_value = \
                MagicMock(raw="ok")
            PlanRunner().run(plan, session_id="s1")
        assert PlanStore.is_complete(plan)

    def test_retourne_string_terminé(self, runner_env):
        from Mnemo.tools.plan_tools import PlanRunner, STATUS_DONE
        plan = _make_plan(runner_env, ["Étape A"])
        with patch("Mnemo.crew.ConversationCrew") as MockCrew:
            MockCrew.return_value.crew.return_value.kickoff.return_value = \
                MagicMock(raw="ok")
            result = PlanRunner().run(plan, session_id="s1")
        assert "terminé" in result.lower() or STATUS_DONE in result

    def test_crew_shell_appele_pour_target_shell(self, runner_env):
        from Mnemo.tools.plan_tools import PlanRunner
        plan = _make_plan(runner_env,
                          ["Lancer le script — crew : shell"],
                          crew_targets={"Lancer le script — crew : shell": "shell"})
        with patch("Mnemo.crew.ShellCrew") as MockShell:
            MockShell.return_value.run.return_value = "ok shell"
            PlanRunner().run(plan, session_id="s1")
        assert MockShell.called

    def test_plan_vide_retourne_string(self, runner_env):
        from Mnemo.tools.plan_tools import PlanRunner, PlanStore
        plan = _make_plan(runner_env, ["Unique étape"])
        PlanStore.mark_done(plan, "Unique étape")  # déjà terminé
        result = PlanRunner().run(plan, session_id="s1")
        assert isinstance(result, str)


# ── run() — crash recovery ────────────────────────────────────

class TestCrashRecovery:
    def test_reprend_a_la_premiere_etape_non_faite(self, runner_env):
        from Mnemo.tools.plan_tools import PlanRunner, PlanStore
        plan = _make_plan(runner_env, ["Étape 1", "Étape 2", "Étape 3"])
        # Simule un crash après l'étape 1
        PlanStore.mark_done(plan, "Étape 1")
        executed = []

        def fake_conv(step, session_id, inputs):
            executed.append(step)
            return "ok"

        runner = PlanRunner()
        runner._executors["conversation"] = fake_conv
        runner.run(plan, session_id="s1")

        # Étape 1 déjà cochée → on commence par Étape 2
        assert all("Étape 1" not in s for s in executed)
        assert any("Étape 2" in s for s in executed)

    def test_plan_deja_termine_aucune_execution(self, runner_env):
        from Mnemo.tools.plan_tools import PlanRunner, PlanStore
        plan = _make_plan(runner_env, ["A", "B"])
        PlanStore.mark_done(plan, "A")
        PlanStore.mark_done(plan, "B")
        executed = []

        runner = PlanRunner()
        runner._executors["conversation"] = lambda s, sid, i: executed.append(s) or "ok"
        runner.run(plan, session_id="s1")

        assert executed == []


# ── run() — bloquants ─────────────────────────────────────────

class TestPlanRunnerBlocker:
    def test_exception_crew_ajoute_bloquant(self, runner_env):
        from Mnemo.tools.plan_tools import PlanRunner
        plan = _make_plan(runner_env, ["Étape risquée"])

        def failing_executor(step, session_id, inputs):
            raise RuntimeError("module introuvable")

        runner = PlanRunner()
        runner._executors["conversation"] = failing_executor
        runner.run(plan, session_id="s1")

        text = plan.read_text()
        assert "module introuvable" in text

    def test_statut_bloque_apres_erreur(self, runner_env):
        from Mnemo.tools.plan_tools import PlanRunner, STATUS_BLOCKED
        plan = _make_plan(runner_env, ["Étape risquée"])

        runner = PlanRunner()
        runner._executors["conversation"] = lambda s, sid, i: (_ for _ in ()).throw(
            RuntimeError("erreur")
        )
        runner.run(plan, session_id="s1")

        assert STATUS_BLOCKED in plan.read_text()

    def test_arret_au_premier_bloquant(self, runner_env):
        from Mnemo.tools.plan_tools import PlanRunner, PlanStore
        plan = _make_plan(runner_env, ["Étape 1", "Étape 2", "Étape 3"])
        executed = []

        def failing_on_2(step, session_id, inputs):
            executed.append(step)
            if "Étape 2" in step:
                raise RuntimeError("bloqué ici")
            return "ok"

        runner = PlanRunner()
        runner._executors["conversation"] = failing_on_2
        runner.run(plan, session_id="s1")

        # Étape 3 ne doit pas avoir été exécutée
        assert not any("Étape 3" in s for s in executed)

    def test_retourne_message_bloquant(self, runner_env):
        from Mnemo.tools.plan_tools import PlanRunner
        plan = _make_plan(runner_env, ["Étape unique"])

        runner = PlanRunner()
        runner._executors["conversation"] = lambda s, sid, i: (_ for _ in ()).throw(
            RuntimeError("erreur critique")
        )
        result = runner.run(plan, session_id="s1")

        assert "bloquant" in result.lower() or "arrêté" in result.lower()

    def test_reponse_erreur_crew_detectee_comme_bloquant(self, runner_env):
        from Mnemo.tools.plan_tools import PlanRunner, STATUS_BLOCKED
        plan = _make_plan(runner_env, ["Étape 1"])

        runner = PlanRunner()
        runner._executors["conversation"] = lambda s, sid, i: "Erreur : module introuvable"
        runner.run(plan, session_id="s1")

        assert STATUS_BLOCKED in plan.read_text()


# ── check_active_plans ────────────────────────────────────────

class TestCheckActivePlans:
    def test_retourne_liste_vide_si_aucun_plan(self, runner_env):
        from Mnemo.tools.plan_tools import check_active_plans
        assert check_active_plans() == []

    def test_retourne_plan_actif(self, runner_env):
        from Mnemo.tools.plan_tools import check_active_plans, PlanStore
        PlanStore.create("goal", ["étape"])
        assert len(check_active_plans()) == 1

    def test_plan_termine_absent(self, runner_env):
        from Mnemo.tools.plan_tools import check_active_plans, PlanStore
        plan = PlanStore.create("goal", ["étape"])
        PlanStore.mark_done(plan, "étape")
        assert check_active_plans() == []
