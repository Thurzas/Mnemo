"""
test_router.py — Tests unitaires du router pattern (Phase 3)

Ce qui est testé :
  1. _parse_eval_json      — extraction JSON robuste depuis réponse LLM brute
  2. _route_message        — dispatch selon route, fallback, web_context injecté
  3. Stubs Phase 3         — ShellCrew, CalendarWriteCrew, SchedulerCrew
  4. handle_message        — pipeline complet mocké bout en bout

Zéro appel LLM, zéro réseau, zéro fichier.
"""

import json
import pytest
from unittest.mock import patch, MagicMock, call

# ── Import du module à tester ─────────────────────────────────────
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from Mnemo import main as mn
from Mnemo.crew import ShellCrew, CalendarWriteCrew, SchedulerCrew


# ══════════════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════════════

def _eval_json(route="conversation", needs_web=False, web_query=None,
               needs_clarification=False, clarification_reason=None, **kwargs) -> dict:
    """Construit un eval_json minimal valide."""
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


# ══════════════════════════════════════════════════════════════════
# 1. _parse_eval_json
# ══════════════════════════════════════════════════════════════════

class TestParseEvalJson:

    def test_clean_json_parsed(self):
        raw = '{"route": "conversation", "needs_web": false}'
        result = mn._parse_eval_json(raw)
        assert result["route"] == "conversation"

    def test_json_with_preamble_extracted(self):
        raw = 'Voici mon analyse : {"route": "shell", "intent": "demande_action"}'
        result = mn._parse_eval_json(raw)
        assert result["route"] == "shell"

    def test_json_with_trailing_text_extracted(self):
        raw = '{"route": "calendar"} Voilà.'
        result = mn._parse_eval_json(raw)
        assert result["route"] == "calendar"

    def test_invalid_json_returns_empty_dict(self):
        assert mn._parse_eval_json("pas du JSON") == {}

    def test_empty_string_returns_empty_dict(self):
        assert mn._parse_eval_json("") == {}

    def test_nested_json_parsed(self):
        data = _eval_json(route="conversation")
        result = mn._parse_eval_json(json.dumps(data))
        assert result["route"] == "conversation"
        assert result["needs_memory"] is True

    def test_returns_dict(self):
        assert isinstance(mn._parse_eval_json('{"a": 1}'), dict)


# ══════════════════════════════════════════════════════════════════
# 2. _route_message
# ══════════════════════════════════════════════════════════════════

class TestRouteMessage:

    BASE = dict(user_message="test", session_id="s1",
                temporal_ctx="2026-03-01", web_context="")

    def _call(self, route, web_context="", **kwargs):
        eval_json = _eval_json(route=route, **kwargs)
        with patch.object(mn, 'ConversationCrew') as mock_conv, \
             patch.object(mn, 'ShellCrew') as mock_shell, \
             patch.object(mn, 'CalendarWriteCrew') as mock_cal, \
             patch.object(mn, 'SchedulerCrew') as mock_sched:
            mock_conv.return_value.crew.return_value.kickoff.return_value = _mock_crew_result("réponse conv")
            mock_shell.return_value.run.return_value = "réponse shell"
            mock_cal.return_value.run.return_value = "réponse calendar"
            mock_sched.return_value.run.return_value = "réponse scheduler"
            result = mn._route_message(eval_json, "test", "s1", "ctx", web_context)
            return result, mock_conv, mock_shell, mock_cal, mock_sched

    def test_conversation_route_uses_conversation_crew(self):
        result, mock_conv, mock_shell, *_ = self._call("conversation")
        assert mock_conv.called
        assert not mock_shell.called

    def test_shell_route_uses_shell_crew(self):
        result, mock_conv, mock_shell, *_ = self._call("shell")
        assert mock_shell.called
        assert not mock_conv.called

    def test_calendar_route_uses_calendar_crew(self):
        result, mock_conv, mock_shell, mock_cal, _ = self._call("calendar")
        assert mock_cal.called
        assert not mock_conv.called

    def test_scheduler_route_uses_scheduler_crew(self):
        result, mock_conv, _, __, mock_sched = self._call("scheduler")
        assert mock_sched.called
        assert not mock_conv.called

    def test_unknown_route_falls_back_to_conversation(self):
        result, mock_conv, mock_shell, *_ = self._call("route_inexistante")
        assert mock_conv.called
        assert not mock_shell.called

    def test_absent_route_falls_back_to_conversation(self):
        eval_json = _eval_json()
        del eval_json["route"]
        with patch.object(mn, 'ConversationCrew') as mock_conv, \
             patch.object(mn, 'ShellCrew') as mock_shell:
            mock_conv.return_value.crew.return_value.kickoff.return_value = _mock_crew_result("ok")
            mn._route_message(eval_json, "msg", "s1", "ctx", "")
        assert mock_conv.called
        assert not mock_shell.called

    def test_web_context_injected_in_conversation(self):
        eval_json = _eval_json(route="conversation")
        with patch.object(mn, 'ConversationCrew') as mock_conv:
            mock_conv.return_value.crew.return_value.kickoff.return_value = _mock_crew_result("ok")
            mn._route_message(eval_json, "msg", "s1", "ctx", "WEB RESULTS")
        inputs = mock_conv.return_value.crew.return_value.kickoff.call_args[1]["inputs"]
        assert inputs["web_context"] == "WEB RESULTS"

    def test_web_context_injected_in_shell(self):
        eval_json = _eval_json(route="shell")
        with patch.object(mn, 'ShellCrew') as mock_shell:
            mock_shell.return_value.run.return_value = "ok"
            mn._route_message(eval_json, "msg", "s1", "ctx", "WEB RESULTS")
        inputs = mock_shell.return_value.run.call_args[0][0]
        assert inputs["web_context"] == "WEB RESULTS"

    def test_conversation_returns_raw_string(self):
        result, *_ = self._call("conversation")
        assert result == "réponse conv"

    def test_shell_returns_string(self):
        result, *_ = self._call("shell")
        assert result == "réponse shell"

    def test_evaluation_result_injected_in_all_routes(self):
        for route in ["conversation", "shell", "calendar", "scheduler"]:
            eval_json = _eval_json(route=route)
            eval_raw = json.dumps(eval_json)
            with patch.object(mn, 'ConversationCrew') as mc, \
                 patch.object(mn, 'ShellCrew') as ms, \
                 patch.object(mn, 'CalendarWriteCrew') as mca, \
                 patch.object(mn, 'SchedulerCrew') as msc:
                mc.return_value.crew.return_value.kickoff.return_value = _mock_crew_result("ok")
                ms.return_value.run.return_value = "ok"
                mca.return_value.run.return_value = "ok"
                msc.return_value.run.return_value = "ok"
                mn._route_message(eval_json, "msg", "s1", "ctx", "")


# ══════════════════════════════════════════════════════════════════
# 3. Stubs Phase 3
# ══════════════════════════════════════════════════════════════════

class TestPhase3Stubs:

    def test_shell_crew_stub_returns_string(self):
        result = ShellCrew().run({"user_message": "crée un fichier"})
        assert isinstance(result, str)

    def test_shell_crew_stub_mentions_not_implemented(self):
        result = ShellCrew().run({})
        assert "non encore implémenté" in result.lower() or "ShellCrew" in result

    def test_calendar_write_crew_stub_returns_string(self):
        result = CalendarWriteCrew().run({"user_message": "crée un événement"})
        assert isinstance(result, str)

    def test_calendar_write_crew_stub_mentions_not_implemented(self):
        result = CalendarWriteCrew().run({})
        assert "non encore implémenté" in result.lower() or "CalendarWriteCrew" in result

    def test_scheduler_crew_stub_returns_string(self):
        result = SchedulerCrew().run({"user_message": "rappelle-moi demain"})
        assert isinstance(result, str)

    def test_scheduler_crew_stub_mentions_not_implemented(self):
        result = SchedulerCrew().run({})
        assert "non encore implémenté" in result.lower() or "SchedulerCrew" in result

    def test_stubs_accept_empty_inputs(self):
        """Les stubs ne doivent pas crasher si inputs est vide."""
        for cls in [ShellCrew, CalendarWriteCrew, SchedulerCrew]:
            try:
                cls().run({})
            except Exception as e:
                pytest.fail(f"{cls.__name__}.run({{}}) a levé : {e}")

    def test_stubs_accept_full_inputs(self):
        """Les stubs acceptent tous les inputs du router."""
        inputs = {
            "user_message": "test",
            "evaluation_result": "{}",
            "temporal_context": "2026-03-01",
            "web_context": "résultats web",
        }
        for cls in [ShellCrew, CalendarWriteCrew, SchedulerCrew]:
            result = cls().run(inputs)
            assert isinstance(result, str)


# ══════════════════════════════════════════════════════════════════
# 4. handle_message — pipeline complet
# ══════════════════════════════════════════════════════════════════

class TestHandleMessage:

    def _mock_eval(self, route="conversation", needs_web=False, web_query=None,
                   needs_clarification=False):
        """Retourne un MagicMock simulant EvaluationCrew().crew().kickoff()."""
        eval_json = _eval_json(route=route, needs_web=needs_web,
                               web_query=web_query,
                               needs_clarification=needs_clarification)
        mock = MagicMock()
        mock.raw = json.dumps(eval_json)
        return mock

    def test_conversation_pipeline_end_to_end(self):
        eval_mock = self._mock_eval(route="conversation")
        conv_mock = _mock_crew_result("Bonjour !")
        with patch.object(mn, 'EvaluationCrew') as mock_eval_cls, \
             patch.object(mn, 'ConversationCrew') as mock_conv_cls, \
             patch.object(mn, 'get_temporal_context', return_value="ctx"), \
             patch.object(mn, 'update_session_memory'):
            mock_eval_cls.return_value.crew.return_value.kickoff.return_value = eval_mock
            mock_conv_cls.return_value.crew.return_value.kickoff.return_value = conv_mock
            result = mn.handle_message("salut", "session_1")
        assert result == "Bonjour !"

    def test_shell_route_dispatches_to_shell_crew(self):
        eval_mock = self._mock_eval(route="shell")
        with patch.object(mn, 'EvaluationCrew') as mock_eval_cls, \
             patch.object(mn, 'ShellCrew') as mock_shell_cls, \
             patch.object(mn, 'ConversationCrew') as mock_conv_cls, \
             patch.object(mn, 'get_temporal_context', return_value="ctx"), \
             patch.object(mn, 'update_session_memory'):
            mock_eval_cls.return_value.crew.return_value.kickoff.return_value = eval_mock
            mock_shell_cls.return_value.run.return_value = "commande exécutée"
            result = mn.handle_message("crée un fichier test.txt", "session_1")
        assert mock_shell_cls.called
        assert not mock_conv_cls.called

    def test_web_confirmation_cancelled_uses_conversation(self):
        """Si l'utilisateur refuse le web, on continue en conversation."""
        eval_mock = self._mock_eval(route="conversation", needs_web=True,
                                    web_query="python latest version")
        conv_mock = _mock_crew_result("réponse sans web")
        with patch.object(mn, 'EvaluationCrew') as mock_eval_cls, \
             patch.object(mn, 'ConversationCrew') as mock_conv_cls, \
             patch.object(mn, 'get_temporal_context', return_value="ctx"), \
             patch.object(mn, 'update_session_memory'), \
             patch.object(mn, '_confirm_web_search', return_value=False):
            mock_eval_cls.return_value.crew.return_value.kickoff.return_value = eval_mock
            mock_conv_cls.return_value.crew.return_value.kickoff.return_value = conv_mock
            result = mn.handle_message("quelle est la dernière version de python ?", "s1")
        assert result == "réponse sans web"

    def test_web_confirmed_context_injected(self):
        """Si l'utilisateur confirme, web_context non vide dans les inputs."""
        eval_mock = self._mock_eval(route="conversation", needs_web=True,
                                    web_query="python latest version")
        conv_mock = _mock_crew_result("réponse avec web")
        with patch.object(mn, 'EvaluationCrew') as mock_eval_cls, \
             patch.object(mn, 'ConversationCrew') as mock_conv_cls, \
             patch.object(mn, 'get_temporal_context', return_value="ctx"), \
             patch.object(mn, 'update_session_memory'), \
             patch.object(mn, '_confirm_web_search', return_value=True), \
             patch('Mnemo.tools.web_tools.web_search', return_value=[
                 {"title": "Python 3.13", "url": "https://python.org", "extract": "Latest.", "source": "ddg"}
             ]):
            mock_eval_cls.return_value.crew.return_value.kickoff.return_value = eval_mock
            mock_conv_cls.return_value.crew.return_value.kickoff.return_value = conv_mock
            result = mn.handle_message("quelle est la dernière version de python ?", "s1")
        inputs = mock_conv_cls.return_value.crew.return_value.kickoff.call_args[1]["inputs"]
        assert "Python 3.13" in inputs["web_context"]

    def test_session_memory_updated_after_response(self):
        eval_mock = self._mock_eval()
        conv_mock = _mock_crew_result("réponse")
        with patch.object(mn, 'EvaluationCrew') as mock_eval_cls, \
             patch.object(mn, 'ConversationCrew') as mock_conv_cls, \
             patch.object(mn, 'get_temporal_context', return_value="ctx"), \
             patch.object(mn, 'update_session_memory') as mock_update:
            mock_eval_cls.return_value.crew.return_value.kickoff.return_value = eval_mock
            mock_conv_cls.return_value.crew.return_value.kickoff.return_value = conv_mock
            mn.handle_message("test", "session_42")
        mock_update.assert_called_once_with("session_42", "test", "réponse")

    def test_unknown_route_silently_falls_back(self):
        """Route inconnue → conversation, sans exception."""
        bad_json = _eval_json(route="route_imaginaire")
        eval_mock = MagicMock()
        eval_mock.raw = json.dumps(bad_json)
        conv_mock = _mock_crew_result("fallback ok")
        with patch.object(mn, 'EvaluationCrew') as mock_eval_cls, \
             patch.object(mn, 'ConversationCrew') as mock_conv_cls, \
             patch.object(mn, 'get_temporal_context', return_value="ctx"), \
             patch.object(mn, 'update_session_memory'):
            mock_eval_cls.return_value.crew.return_value.kickoff.return_value = eval_mock
            mock_conv_cls.return_value.crew.return_value.kickoff.return_value = conv_mock
            result = mn.handle_message("test", "s1")
        assert result == "fallback ok"
        assert mock_conv_cls.called