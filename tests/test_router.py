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
from Mnemo.crew import ShellCrew, CalendarWriteCrew, SchedulerCrew, NoteWriterCrew
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