"""
test_router.py — Tests unitaires du router (Chaîne de Responsabilité)

Ce qui est testé :
  1. _parse_eval_json      — extraction JSON robuste (routing.handlers.llm)
  2. dispatch()            — dispatch selon route, fallback, web_context injecté
  3. Stubs Phase 3         — ShellCrew, CalendarWriteCrew, SchedulerCrew
  4. handle_message        — pipeline complet mocké bout en bout

Zéro appel LLM, zéro réseau, zéro fichier.
"""

import json
import pytest
from unittest.mock import patch, MagicMock

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from Mnemo import main as mn
from Mnemo.crew import ShellCrew, CalendarWriteCrew, SchedulerCrew, NoteWriterCrew, SandboxCrew
from Mnemo.routing.handlers.llm import _parse_eval_json
from Mnemo.routing.dispatch import dispatch
from Mnemo.routing.context import RouterResult
from Mnemo.routing.confirmation import ConfirmationResult


# ══════════════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════════════

def _eval_json(route="conversation", needs_web=False, web_query=None,
               needs_clarification=False, clarification_reason=None, **kwargs) -> dict:
    """Construit un eval_json (metadata RouterResult) minimal valide."""
    return {
        "intent": "question",
        "entities": [],
        "topics": [],
        "needs_memory": True,
        "needs_clarification": needs_clarification,
        "clarification_reason": clarification_reason,
        "complexity": "simple",
        "memory_query": "test query",
        "needs_calendar": False,
        "needs_web": needs_web,
        "web_query": web_query,
        "temporal_reference": "none",
        "reference_date": None,
        "route": route,
        **kwargs,
    }


def _mock_crew_result(text: str) -> MagicMock:
    """Simule le résultat d'un crew.kickoff()."""
    result = MagicMock()
    result.raw = text
    return result


def _router_result(route="conversation", **kwargs) -> RouterResult:
    """Construit un RouterResult minimal."""
    return RouterResult(
        route=route,
        confidence=1.0,
        handler="keyword",
        metadata=_eval_json(route=route, **kwargs),
    )


# ══════════════════════════════════════════════════════════════════
# 1. _parse_eval_json
# ══════════════════════════════════════════════════════════════════

class TestParseEvalJson:

    def test_clean_json_parsed(self):
        raw = '{"route": "conversation", "needs_web": false}'
        result = _parse_eval_json(raw)
        assert result["route"] == "conversation"

    def test_json_with_preamble_extracted(self):
        raw = 'Voici mon analyse : {"route": "shell", "intent": "demande_action"}'
        result = _parse_eval_json(raw)
        assert result["route"] == "shell"

    def test_json_with_trailing_text_extracted(self):
        raw = '{"route": "calendar"} Voilà.'
        result = _parse_eval_json(raw)
        assert result["route"] == "calendar"

    def test_invalid_json_returns_empty_dict(self):
        assert _parse_eval_json("pas du JSON") == {}

    def test_empty_string_returns_empty_dict(self):
        assert _parse_eval_json("") == {}

    def test_nested_json_parsed(self):
        data = _eval_json(route="conversation")
        result = _parse_eval_json(json.dumps(data))
        assert result["route"] == "conversation"
        assert result["needs_memory"] is True

    def test_returns_dict(self):
        assert isinstance(_parse_eval_json('{"a": 1}'), dict)


# ══════════════════════════════════════════════════════════════════
# 2. dispatch()
# ══════════════════════════════════════════════════════════════════

class TestDispatch:
    """Teste dispatch() — anciennement _route_message()."""

    def _call(self, route, web_context="", shell_command="", **kwargs):
        result = _router_result(route=route, **kwargs)
        with patch("Mnemo.crew.ConversationCrew") as mock_conv, \
             patch("Mnemo.crew.ShellCrew") as mock_shell, \
             patch("Mnemo.crew.CalendarWriteCrew") as mock_cal, \
             patch("Mnemo.crew.SchedulerCrew") as mock_sched, \
             patch("Mnemo.crew.NoteWriterCrew") as mock_note:
            mock_conv.return_value.crew.return_value.kickoff.return_value = _mock_crew_result("réponse conv")
            mock_shell.return_value.run.return_value = "réponse shell"
            mock_cal.return_value.run.return_value = "réponse calendar"
            mock_sched.return_value.run.return_value = "réponse scheduler"
            mock_note.return_value.run.return_value = "réponse note"
            res = dispatch(result, user_message="test", session_id="s1",
                           temporal_ctx="ctx", web_context=web_context,
                           shell_command=shell_command)
            return res, mock_conv, mock_shell, mock_cal, mock_sched, mock_note

    def test_conversation_route_uses_conversation_crew(self):
        result, mock_conv, mock_shell, *_ = self._call("conversation")
        assert mock_conv.called
        assert not mock_shell.called

    def test_shell_route_uses_shell_crew(self):
        result, mock_conv, mock_shell, *_ = self._call("shell")
        assert mock_shell.called
        assert not mock_conv.called

    def test_calendar_route_uses_calendar_crew(self):
        result, mock_conv, mock_shell, mock_cal, *_ = self._call("calendar")
        assert mock_cal.called
        assert not mock_conv.called

    def test_scheduler_route_uses_scheduler_crew(self):
        result, mock_conv, _, __, mock_sched, ___ = self._call("scheduler")
        assert mock_sched.called
        assert not mock_conv.called

    def test_unknown_route_falls_back_to_conversation(self):
        result, mock_conv, mock_shell, *_ = self._call("route_inexistante")
        assert mock_conv.called
        assert not mock_shell.called

    def test_web_context_injected_in_conversation(self):
        router_result = _router_result(route="conversation")
        with patch("Mnemo.crew.ConversationCrew") as mock_conv:
            mock_conv.return_value.crew.return_value.kickoff.return_value = _mock_crew_result("ok")
            dispatch(router_result, user_message="msg", session_id="s1",
                     temporal_ctx="ctx", web_context="WEB RESULTS")
        inputs = mock_conv.return_value.crew.return_value.kickoff.call_args[1]["inputs"]
        assert inputs["web_context"] == "WEB RESULTS"

    def test_web_context_injected_in_shell(self):
        router_result = _router_result(route="shell")
        with patch("Mnemo.crew.ShellCrew") as mock_shell:
            mock_shell.return_value.run.return_value = "ok"
            dispatch(router_result, user_message="msg", session_id="s1",
                     temporal_ctx="ctx", web_context="WEB RESULTS")
        inputs = mock_shell.return_value.run.call_args[0][0]
        assert inputs["web_context"] == "WEB RESULTS"

    def test_conversation_returns_raw_string(self):
        result, *_ = self._call("conversation")
        assert result == "réponse conv"

    def test_shell_returns_string(self):
        result, *_ = self._call("shell")
        assert result == "réponse shell"


# ══════════════════════════════════════════════════════════════════
# 2b. Route "plan" — keyword + dispatch + needs_recon
# ══════════════════════════════════════════════════════════════════

class TestPlanRoute:
    """Tests de la route 'plan' : détection keywords, dispatch, needs_recon."""

    def test_plan_keyword_strong_routes_to_plan(self):
        from Mnemo.routing.handlers.keyword import _detect_plan_intent
        strong, _ = _detect_plan_intent("construis-moi la feature X")
        assert strong

    def test_plan_keyword_weak_no_strong(self):
        from Mnemo.routing.handlers.keyword import _detect_plan_intent
        strong, weak = _detect_plan_intent("comment implémenter le truc ?")
        assert not strong
        assert weak

    def test_plan_keyword_no_match(self):
        from Mnemo.routing.handlers.keyword import _detect_plan_intent
        strong, weak = _detect_plan_intent("quel temps fait-il ?")
        assert not strong
        assert not weak

    def test_keyword_handler_returns_plan_on_strong(self):
        from Mnemo.routing.handlers.keyword import KeywordHandler
        from Mnemo.routing.context import RouterContext
        ctx = RouterContext(message="prépare un plan pour la feature Y", session_id="s1")
        result = KeywordHandler().handle(ctx)
        assert result is not None
        assert result.route == "plan"
        assert result.confidence == 1.0

    def test_dispatch_plan_appelle_planner_crew(self, tmp_path, monkeypatch):
        """Route plan → PlannerCrew.run() est appelé."""
        import Mnemo.tools.plan_tools as pt
        import Mnemo.tools.memory_tools as mt
        monkeypatch.setattr(pt, "get_data_dir", lambda: tmp_path)
        monkeypatch.setattr(mt, "get_data_dir", lambda: tmp_path)
        result = _router_result(route="plan")
        with patch("Mnemo.crew.PlannerCrew") as MockPlanner, \
             patch("Mnemo.crew.ReconnaissanceCrew"):
            MockPlanner.return_value.run.return_value = "Plan créé."
            res = dispatch(result, user_message="construis le classifier",
                           session_id="s1", temporal_ctx="", web_context="")
        assert MockPlanner.called
        assert res == "Plan créé."

    def test_dispatch_plan_avec_recon_appelle_recon_crew(self, tmp_path, monkeypatch):
        """needs_recon=True → ReconnaissanceCrew appelé avant PlannerCrew."""
        import Mnemo.tools.plan_tools as pt
        import Mnemo.tools.memory_tools as mt
        monkeypatch.setattr(pt, "get_data_dir", lambda: tmp_path)
        monkeypatch.setattr(mt, "get_data_dir", lambda: tmp_path)
        result = _router_result(route="plan", needs_recon=True)
        with patch("Mnemo.crew.PlannerCrew") as MockPlanner, \
             patch("Mnemo.crew.ReconnaissanceCrew") as MockRecon:
            MockRecon.return_value.run.return_value = {"summary": "module X trouvé"}
            MockPlanner.return_value.run.return_value = "Plan créé."
            dispatch(result, user_message="construis memory_tools",
                     session_id="s1", temporal_ctx="", web_context="")
        assert MockRecon.called
        assert MockPlanner.called

    def test_dispatch_plan_sans_recon_skip_recon_crew(self, tmp_path, monkeypatch):
        """needs_recon=False → ReconnaissanceCrew NON appelé."""
        import Mnemo.tools.plan_tools as pt
        import Mnemo.tools.memory_tools as mt
        monkeypatch.setattr(pt, "get_data_dir", lambda: tmp_path)
        monkeypatch.setattr(mt, "get_data_dir", lambda: tmp_path)
        result = _router_result(route="plan", needs_recon=False)
        with patch("Mnemo.crew.PlannerCrew") as MockPlanner, \
             patch("Mnemo.crew.ReconnaissanceCrew") as MockRecon:
            MockPlanner.return_value.run.return_value = "Plan créé."
            dispatch(result, user_message="construis le classifier",
                     session_id="s1", temporal_ctx="", web_context="")
        assert not MockRecon.called

    def test_needs_recon_set_on_complex_plan(self):
        """LLMHandler doit setter needs_recon=True si route=plan et complexity=complex."""
        from Mnemo.routing.handlers.llm import LLMHandler
        from Mnemo.routing.context import RouterContext
        ctx = RouterContext(message="construis le module X", session_id="s1")
        ctx._hints["ml_route"] = "plan"
        ctx._hints["ml_conf"]  = 0.9
        eval_json = _eval_json(route="plan", complexity="complex")
        with patch("Mnemo.crew.EvaluationCrew") as MockEval:
            MockEval.return_value.crew.return_value.kickoff.return_value = \
                _mock_crew_result(json.dumps(eval_json))
            result = LLMHandler().handle(ctx)
        assert result.metadata.get("needs_recon") is True

    def test_needs_recon_not_set_on_simple_plan(self):
        """needs_recon reste False si complexity != complex."""
        from Mnemo.routing.handlers.llm import LLMHandler
        from Mnemo.routing.context import RouterContext
        ctx = RouterContext(message="construis le module X", session_id="s1")
        ctx._hints["ml_route"] = "plan"
        ctx._hints["ml_conf"]  = 0.9
        eval_json = _eval_json(route="plan", complexity="simple")
        with patch("Mnemo.crew.EvaluationCrew") as MockEval:
            MockEval.return_value.crew.return_value.kickoff.return_value = \
                _mock_crew_result(json.dumps(eval_json))
            result = LLMHandler().handle(ctx)
        assert not result.metadata.get("needs_recon")


# ══════════════════════════════════════════════════════════════════
# 3. Stubs Phase 3
# ══════════════════════════════════════════════════════════════════

class TestPhase3Stubs:
    """
    CalendarWriteCrew — crew réel mais CalDAV non implémenté.
    Sans calendrier local writable, run() retourne un message d'erreur sans appeler le LLM.
    """

    def test_calendar_write_crew_readonly_returns_string(self):
        with patch("Mnemo.tools.calendar_tools.calendar_is_writable", return_value=False):
            result = CalendarWriteCrew().run({"user_message": "crée un événement"})
        assert isinstance(result, str)

    def test_calendar_write_crew_readonly_message(self):
        with patch("Mnemo.tools.calendar_tools.calendar_is_writable", return_value=False):
            result = CalendarWriteCrew().run({})
        assert "lecture seule" in result.lower() or "non configuré" in result.lower()

    def test_calendar_write_crew_readonly_no_crash_empty_inputs(self):
        """CalendarWriteCrew ne doit pas crasher si inputs est vide (calendar non writable)."""
        with patch("Mnemo.tools.calendar_tools.calendar_is_writable", return_value=False):
            try:
                CalendarWriteCrew().run({})
            except Exception as e:
                pytest.fail(f"CalendarWriteCrew.run({{}}) a levé : {e}")

    def test_calendar_write_crew_accepts_full_inputs(self):
        inputs = {
            "user_message": "test",
            "evaluation_result": "{}",
            "temporal_context": "2026-03-01",
            "web_context": "résultats web",
        }
        result = CalendarWriteCrew().run(inputs)
        assert isinstance(result, str)


# ══════════════════════════════════════════════════════════════════
# 4. handle_message — pipeline complet
# ══════════════════════════════════════════════════════════════════

class TestHandleMessage:
    """
    Teste handle_message() en mockant la chaîne de routing au complet.

    Stratégie : mock build_router() pour retourner un router fictif,
    mock run_confirmation_middleware() pour contrôler les confirmations,
    et mock dispatch() pour vérifier quel crew est appelé.
    """

    def _mock_router(self, route="conversation", **kwargs) -> MagicMock:
        """Retourne un mock de RouterHandler qui produit un RouterResult fixe."""
        result = _router_result(route=route, **kwargs)
        router = MagicMock()
        router.handle.return_value = result
        return router

    def _confirmed(self, result: RouterResult, user_message="test",
                   web_context="", shell_command="") -> ConfirmationResult:
        return ConfirmationResult(
            result=result,
            user_message=user_message,
            web_context=web_context,
            shell_command=shell_command,
        )

    def test_conversation_pipeline_end_to_end(self):
        rr = _router_result("conversation")
        with patch("Mnemo.routing.build_router", return_value=self._mock_router("conversation")), \
             patch("Mnemo.routing.confirmation.run_confirmation_middleware",
                   return_value=self._confirmed(rr, "salut")), \
             patch("Mnemo.routing.dispatch", return_value="Bonjour !") as mock_dispatch, \
             patch.object(mn, "get_temporal_context", return_value="ctx"), \
             patch.object(mn, "update_session_memory"):
            result = mn.handle_message("salut", "session_1")
        assert result == "Bonjour !"

    def test_shell_route_dispatches_correctly(self):
        rr = _router_result("shell")
        confirmed = self._confirmed(rr, "crée un fichier", shell_command="touch /data/test.txt")
        with patch("Mnemo.routing.build_router", return_value=self._mock_router("shell")), \
             patch("Mnemo.routing.confirmation.run_confirmation_middleware",
                   return_value=confirmed), \
             patch("Mnemo.routing.dispatch", return_value="commande exécutée") as mock_dispatch, \
             patch.object(mn, "get_temporal_context", return_value="ctx"), \
             patch.object(mn, "update_session_memory"):
            result = mn.handle_message("crée un fichier test.txt", "session_1")
        mock_dispatch.assert_called_once()
        call_kwargs = mock_dispatch.call_args
        assert call_kwargs[1]["shell_command"] == "touch /data/test.txt"

    def test_web_context_passed_to_dispatch(self):
        rr = _router_result("conversation", needs_web=True, web_query="python version")
        confirmed = self._confirmed(rr, "quelle version ?", web_context="Python 3.13 is latest")
        with patch("Mnemo.routing.build_router", return_value=self._mock_router("conversation")), \
             patch("Mnemo.routing.confirmation.run_confirmation_middleware",
                   return_value=confirmed), \
             patch("Mnemo.routing.dispatch", return_value="réponse avec web") as mock_dispatch, \
             patch.object(mn, "get_temporal_context", return_value="ctx"), \
             patch.object(mn, "update_session_memory"):
            result = mn.handle_message("quelle version de python ?", "s1")
        assert mock_dispatch.call_args[1]["web_context"] == "Python 3.13 is latest"

    def test_session_memory_updated_after_response(self):
        rr = _router_result("conversation")
        with patch("Mnemo.routing.build_router", return_value=self._mock_router()), \
             patch("Mnemo.routing.confirmation.run_confirmation_middleware",
                   return_value=self._confirmed(rr, "test")), \
             patch("Mnemo.routing.dispatch", return_value="réponse"), \
             patch.object(mn, "get_temporal_context", return_value="ctx"), \
             patch.object(mn, "update_session_memory") as mock_update:
            mn.handle_message("test", "session_42")
        mock_update.assert_called_once_with("session_42", "test", "réponse",
                                             retrieved_chunk_ids=None)

    def test_unknown_route_silently_handled(self):
        """Route inconnue → dispatch gère le fallback, pas d'exception."""
        rr = _router_result("route_imaginaire")
        with patch("Mnemo.routing.build_router", return_value=self._mock_router("route_imaginaire")), \
             patch("Mnemo.routing.confirmation.run_confirmation_middleware",
                   return_value=self._confirmed(rr)), \
             patch("Mnemo.routing.dispatch", return_value="fallback ok"), \
             patch.object(mn, "get_temporal_context", return_value="ctx"), \
             patch.object(mn, "update_session_memory"):
            result = mn.handle_message("test", "s1")
        assert result == "fallback ok"


# ══════════════════════════════════════════════════════════════════
# 5. KeywordHandler — gate longueur de message
# ══════════════════════════════════════════════════════════════════

class TestKeywordLengthGate:
    """
    Le bypass keyword ne doit pas s'activer pour les messages longs.
    Seuil : _KEYWORD_BYPASS_MAX_WORDS = 12 mots.
    Shell et Note restent actifs quelle que soit la longueur.
    """

    def test_scheduler_strong_court_bypass(self):
        """Message court avec keyword scheduler fort → bypass."""
        from Mnemo.routing.handlers.keyword import KeywordHandler
        from Mnemo.routing.context import RouterContext
        ctx = RouterContext(message="rappelle-moi de prendre mes médicaments", session_id="s1")
        result = KeywordHandler().handle(ctx)
        assert result is not None
        assert result.route == "scheduler"

    def test_scheduler_strong_long_no_bypass(self):
        """Message long contenant un keyword scheduler fort → pas de bypass."""
        from Mnemo.routing.handlers.keyword import KeywordHandler
        from Mnemo.routing.context import RouterContext
        # 20 mots — dépasse le seuil de 12
        msg = ("hello on va préparer un projet de landing page avec react js "
               "on va planifier ce projet en étapes pour s organiser")
        ctx = RouterContext(message=msg, session_id="s1")
        result = KeywordHandler().handle(ctx)
        # Doit passer au handler suivant, pas retourner scheduler
        assert result is None or result.route != "scheduler"

    def test_plan_strong_court_bypass(self):
        """Message court avec keyword plan fort → bypass plan."""
        from Mnemo.routing.handlers.keyword import KeywordHandler
        from Mnemo.routing.context import RouterContext
        ctx = RouterContext(message="prépare un plan pour cette feature", session_id="s1")
        result = KeywordHandler().handle(ctx)
        assert result is not None
        assert result.route == "plan"

    def test_plan_strong_long_no_bypass(self):
        """Message long contenant un keyword plan fort → pas de bypass direct."""
        from Mnemo.routing.handlers.keyword import KeywordHandler
        from Mnemo.routing.context import RouterContext
        msg = ("je voudrais qu on construise ensemble une stratégie complète "
               "pour prépare un plan et organiser toutes les étapes du projet")
        ctx = RouterContext(message=msg, session_id="s1")
        result = KeywordHandler().handle(ctx)
        assert result is None or result.route != "plan"

    def test_note_long_toujours_bypass(self):
        """Note : pas de limite de longueur — bypass même sur message long."""
        from Mnemo.routing.handlers.keyword import KeywordHandler
        from Mnemo.routing.context import RouterContext
        msg = ("note que j ai décidé d utiliser FastAPI pour le backend "
               "de ce projet car c est plus simple et plus performant")
        ctx = RouterContext(message=msg, session_id="s1")
        result = KeywordHandler().handle(ctx)
        assert result is not None
        assert result.route == "note"

    def test_hint_kw_plan_weak_set_sur_message_long(self):
        """Message long avec plan_weak keyword → hint kw_plan_weak déposé pour ML."""
        from Mnemo.routing.handlers.keyword import KeywordHandler
        from Mnemo.routing.context import RouterContext
        msg = ("hello on va préparer un projet de landing page avec react js "
               "on va planifier ce projet en étapes pour s organiser")
        ctx = RouterContext(message=msg, session_id="s1")
        KeywordHandler().handle(ctx)
        assert ctx._hints.get("kw_plan_weak") is True

    def test_hint_kw_sched_weak_non_set_sans_weak_kw(self):
        """Message sans weak scheduler keyword → kw_sched_weak = False."""
        from Mnemo.routing.handlers.keyword import KeywordHandler
        from Mnemo.routing.context import RouterContext
        ctx = RouterContext(message="comment vas-tu aujourd hui ?", session_id="s1")
        KeywordHandler().handle(ctx)
        assert ctx._hints.get("kw_sched_weak") is False


# ══════════════════════════════════════════════════════════════════
# 6. Plan weak keywords — nouveaux patterns projet
# ══════════════════════════════════════════════════════════════════

class TestPlanWeakKeywords:
    """
    Les nouveaux keywords plan_weak doivent être détectés pour orienter
    le ML/LLM vers la route 'plan' plutôt que 'scheduler'.
    """

    def _weak(self, msg: str) -> bool:
        from Mnemo.routing.handlers.keyword import _detect_plan_intent
        _, weak = _detect_plan_intent(msg)
        return weak

    def test_planifier_ce_projet(self):
        assert self._weak("on va planifier ce projet en étapes")

    def test_planifier_le_projet(self):
        assert self._weak("je veux planifier le projet correctement")

    def test_planifier_en_etapes(self):
        assert self._weak("il faudrait planifier en étapes tout ça")

    def test_organiser_ce_projet(self):
        assert self._weak("on va organiser ce projet ensemble")

    def test_preparer_un_projet(self):
        assert self._weak("on va préparer un projet de landing page")

    def test_decoupe_en_etapes(self):
        assert self._weak("on va découper en étapes le développement")

    def test_phrase_non_plan(self):
        """Phrase neutre → pas de plan_weak."""
        assert not self._weak("salut comment tu vas aujourd hui")

    def test_rappel_scheduler_pas_plan_weak(self):
        """Rappel scheduler → pas de plan_weak."""
        assert not self._weak("rappelle-moi demain de prendre mes médicaments")


# ══════════════════════════════════════════════════════════════════
# 7. Route "sandbox" — keyword + dispatch
# ══════════════════════════════════════════════════════════════════

class TestSandboxRoute:

    def test_sandbox_keyword_strong_court(self):
        """Keyword sandbox fort sur message court → bypass sandbox."""
        from Mnemo.routing.handlers.keyword import KeywordHandler
        from Mnemo.routing.context import RouterContext
        ctx = RouterContext(message="ouvre le projet landing-page", session_id="s1")
        result = KeywordHandler().handle(ctx)
        assert result is not None
        assert result.route == "sandbox"

    def test_sandbox_keyword_strong_continue(self):
        from Mnemo.routing.handlers.keyword import KeywordHandler
        from Mnemo.routing.context import RouterContext
        ctx = RouterContext(message="continue le projet react-doc", session_id="s1")
        result = KeywordHandler().handle(ctx)
        assert result is not None
        assert result.route == "sandbox"

    def test_sandbox_keyword_no_match(self):
        """Message sans keyword sandbox → pas de bypass sandbox."""
        from Mnemo.routing.handlers.keyword import _detect_sandbox_intent
        strong, weak = _detect_sandbox_intent("planifie un rappel demain")
        assert not strong
        assert not weak

    def test_sandbox_keyword_weak_hint_depose(self):
        """Keyword sandbox faible → hint kw_sandbox_weak déposé."""
        from Mnemo.routing.handlers.keyword import KeywordHandler
        from Mnemo.routing.context import RouterContext
        ctx = RouterContext(message="je veux travailler dans le sandbox", session_id="s1")
        KeywordHandler().handle(ctx)
        assert ctx._hints.get("kw_sandbox_weak") is True

    def test_sandbox_long_message_no_bypass(self):
        """Message long avec keyword sandbox fort → pas de bypass (gate longueur)."""
        from Mnemo.routing.handlers.keyword import KeywordHandler
        from Mnemo.routing.context import RouterContext
        msg = ("alors aujourd hui on va vraiment ouvrir le projet landing-page "
               "et avancer sur toutes les étapes du plan qu on avait préparé")
        ctx = RouterContext(message=msg, session_id="s1")
        result = KeywordHandler().handle(ctx)
        assert result is None or result.route != "sandbox"

    def test_dispatch_sandbox_appelle_sandbox_crew(self, tmp_path, monkeypatch):
        """Route sandbox → SandboxCrew.run() est appelé."""
        import Mnemo.tools.sandbox_tools as st
        monkeypatch.setattr(st, "get_data_dir", lambda: tmp_path)
        result = _router_result(route="sandbox")
        with patch("Mnemo.crew.SandboxCrew") as MockSandbox:
            MockSandbox.return_value.run.return_value = "Étape 1 terminée."
            res = dispatch(result, user_message="ouvre le projet react-doc",
                           session_id="s1", temporal_ctx="", web_context="")
        assert MockSandbox.called
        assert res == "Étape 1 terminée."

    def test_dispatch_sandbox_pas_conversation(self, tmp_path, monkeypatch):
        """Route sandbox → ConversationCrew NON appelé."""
        import Mnemo.tools.sandbox_tools as st
        monkeypatch.setattr(st, "get_data_dir", lambda: tmp_path)
        result = _router_result(route="sandbox")
        with patch("Mnemo.crew.SandboxCrew") as MockSandbox, \
             patch("Mnemo.crew.ConversationCrew") as MockConv:
            MockSandbox.return_value.run.return_value = "ok"
            dispatch(result, user_message="ouvre le projet",
                     session_id="s1", temporal_ctx="", web_context="")
        assert not MockConv.called