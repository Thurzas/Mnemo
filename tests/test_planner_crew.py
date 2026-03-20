"""
Tests Phase 6 — PlannerCrew : décomposition goal → plan.md
Niveau 3 — LLM mocké au niveau crew.kickoff(), filesystem via tmp_path.
"""
import json
import pytest
from unittest.mock import patch, MagicMock


# ── Fixtures ──────────────────────────────────────────────────

@pytest.fixture()
def planner_env(tmp_path, monkeypatch):
    """Isole le filesystem (plans/ + world_state.json) dans tmp_path."""
    import Mnemo.tools.plan_tools as pt
    import Mnemo.tools.memory_tools as mt
    monkeypatch.setattr(pt, "get_data_dir", lambda: tmp_path)
    monkeypatch.setattr(mt, "get_data_dir", lambda: tmp_path)
    return tmp_path


def _llm_plan_response(
    title="Construire le classifier",
    steps=None,
    crew_targets=None,
    context_summary="Contexte de test.",
) -> str:
    if steps is None:
        steps = ["Étape 1 — lire le code", "Étape 2 — écrire les tests"]
    if crew_targets is None:
        crew_targets = {s: "conversation" for s in steps}
    return json.dumps({
        "title":          title,
        "steps":          steps,
        "crew_targets":   crew_targets,
        "context_summary": context_summary,
    })


def _mock_kickoff(raw: str) -> MagicMock:
    m = MagicMock()
    m.raw = raw
    return m


# ── Tests ─────────────────────────────────────────────────────

class TestPlannerCrewRun:
    def test_cree_fichier_plan(self, planner_env):
        from Mnemo.crew import PlannerCrew
        with patch.object(
            PlannerCrew().crew().__class__, "kickoff",
            return_value=_mock_kickoff(_llm_plan_response()),
        ):
            with patch("Mnemo.crew.PlannerCrew.crew") as mock_crew:
                mock_crew.return_value.kickoff.return_value = \
                    _mock_kickoff(_llm_plan_response())
                PlannerCrew().run({"user_message": "construis le classifier"})

        plans = list((planner_env / "plans").glob("plan_*.md"))
        assert len(plans) == 1

    def test_retourne_string_avec_etapes(self, planner_env):
        from Mnemo.crew import PlannerCrew
        with patch("Mnemo.crew.PlannerCrew.crew") as mock_crew:
            mock_crew.return_value.kickoff.return_value = \
                _mock_kickoff(_llm_plan_response(
                    steps=["Lire le fichier", "Écrire les tests"],
                ))
            result = PlannerCrew().run({"user_message": "mon goal"})

        assert "Lire le fichier" in result
        assert "Écrire les tests" in result

    def test_retourne_nom_fichier_plan(self, planner_env):
        from Mnemo.crew import PlannerCrew
        with patch("Mnemo.crew.PlannerCrew.crew") as mock_crew:
            mock_crew.return_value.kickoff.return_value = \
                _mock_kickoff(_llm_plan_response())
            result = PlannerCrew().run({"user_message": "mon goal"})

        assert "plan_" in result
        assert ".md" in result

    def test_crew_target_annote_dans_reponse(self, planner_env):
        from Mnemo.crew import PlannerCrew
        steps = ["Lire les fichiers", "Exécuter le script"]
        with patch("Mnemo.crew.PlannerCrew.crew") as mock_crew:
            mock_crew.return_value.kickoff.return_value = _mock_kickoff(
                _llm_plan_response(
                    steps=steps,
                    crew_targets={
                        "Lire les fichiers":   "conversation",
                        "Exécuter le script":  "shell",
                    },
                )
            )
            result = PlannerCrew().run({"user_message": "goal"})

        assert "shell" in result

    def test_plan_md_contient_etapes_todo(self, planner_env):
        from Mnemo.crew import PlannerCrew
        steps = ["Étape A", "Étape B"]
        with patch("Mnemo.crew.PlannerCrew.crew") as mock_crew:
            mock_crew.return_value.kickoff.return_value = \
                _mock_kickoff(_llm_plan_response(steps=steps))
            PlannerCrew().run({"user_message": "goal"})

        plan_file = next((planner_env / "plans").glob("plan_*.md"))
        text = plan_file.read_text()
        assert "- [ ] Étape A" in text
        assert "- [ ] Étape B" in text

    def test_steps_vides_retourne_message_erreur(self, planner_env):
        from Mnemo.crew import PlannerCrew
        with patch("Mnemo.crew.PlannerCrew.crew") as mock_crew:
            mock_crew.return_value.kickoff.return_value = \
                _mock_kickoff(_llm_plan_response(steps=[]))
            result = PlannerCrew().run({"user_message": "goal vague"})

        assert "reformuler" in result.lower()

    def test_json_invalide_retourne_erreur(self, planner_env):
        from Mnemo.crew import PlannerCrew
        with patch("Mnemo.crew.PlannerCrew.crew") as mock_crew:
            mock_crew.return_value.kickoff.return_value = _mock_kickoff("RÉPONSE INVALIDE")
            result = PlannerCrew().run({"user_message": "goal"})

        assert "erreur" in result.lower()

    def test_lacunes_bloquantes_dans_inputs(self, planner_env):
        """Si world_state contient des blocking_gaps, ils sont transmis au LLM."""
        import json as _json
        ws_path = planner_env / "world_state.json"
        ws_path.write_text(_json.dumps({
            "memory_blocking_gaps": True,
            "last_gap_report": {
                "blocking_gaps": [
                    {"description": "Projets en cours vide", "section": "S", "subsection": "SS"}
                ]
            }
        }))
        captured = {}

        def fake_kickoff(inputs):
            captured.update(inputs)
            return _mock_kickoff(_llm_plan_response())

        from Mnemo.crew import PlannerCrew
        with patch("Mnemo.crew.PlannerCrew.crew") as mock_crew:
            mock_crew.return_value.kickoff.side_effect = fake_kickoff
            PlannerCrew().run({"user_message": "goal"})

        assert "Projets en cours vide" in captured.get("memory_gap_summary", "")

    def test_recon_context_transmis_au_llm(self, planner_env):
        """recon_context fourni dans inputs → transmis au kickoff."""
        captured = {}

        def fake_kickoff(inputs):
            captured.update(inputs)
            return _mock_kickoff(_llm_plan_response())

        from Mnemo.crew import PlannerCrew
        with patch("Mnemo.crew.PlannerCrew.crew") as mock_crew:
            mock_crew.return_value.kickoff.side_effect = fake_kickoff
            PlannerCrew().run({
                "user_message":  "goal",
                "recon_context": "Module memory_tools.py — fonctions clés : retrieve_all",
            })

        assert "retrieve_all" in captured.get("recon_context", "")
