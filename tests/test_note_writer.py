"""
test_note_writer.py — Tests de NoteWriterCrew et _detect_note_intent (Phase 3)

Ce qui est testé :
  1. _detect_note_intent  — détection par keywords (vrais positifs / négatifs)
  2. NoteWriterCrew.run() — kickoff mocké, N3
  3. _route_message       — dispatch route=note vers NoteWriterCrew

Zéro appel LLM, zéro réseau, zéro fichier.
"""

import json
import pytest
from unittest.mock import patch, MagicMock

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from Mnemo import main as mn
from Mnemo.crew import NoteWriterCrew
from Mnemo.routing.handlers.keyword import _detect_note_intent
from Mnemo.routing.context import RouterResult
from Mnemo.routing.dispatch import dispatch
from Mnemo.routing.confirmation import ConfirmationResult


# ══════════════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════════════

def _mock_crew_result(text: str) -> MagicMock:
    result = MagicMock()
    result.raw = text
    return result


def _eval_json_note(**kwargs) -> dict:
    base = {
        "intent": "mise_a_jour_info",
        "entities": [],
        "topics": [],
        "needs_memory": False,
        "needs_clarification": False,
        "clarification_reason": None,
        "complexity": "simple",
        "memory_query": "",
        "needs_calendar": False,
        "needs_web": False,
        "web_query": None,
        "temporal_reference": "none",
        "reference_date": None,
        "route": "note",
        "shell_command": None,
    }
    base.update(kwargs)
    return base


# ══════════════════════════════════════════════════════════════════
# 1. _detect_note_intent — vrais positifs
# ══════════════════════════════════════════════════════════════════

class TestDetectNoteIntentPositives:

    def test_note_que(self):
        assert _detect_note_intent("note que je préfère vim") is True

    def test_notes_que(self):
        assert _detect_note_intent("notes que j'habite à Bordeaux") is True

    def test_retiens_que(self):
        assert _detect_note_intent("retiens que mon projet s'appelle Mnemo") is True

    def test_retiens_bien_que(self):
        assert _detect_note_intent("retiens bien que je suis allergique au gluten") is True

    def test_memorise_que(self):
        assert _detect_note_intent("mémorise que j'utilise FastAPI") is True

    def test_memorise_ca(self):
        assert _detect_note_intent("mémorise ça") is True

    def test_memorise_ceci(self):
        assert _detect_note_intent("mémorise ceci : je préfère Python") is True

    def test_noublie_pas_que(self):
        assert _detect_note_intent("n'oublie pas que je travaille en remote") is True

    def test_noublie_pas_ca(self):
        assert _detect_note_intent("n'oublie pas ça") is True

    def test_souviens_toi_que(self):
        assert _detect_note_intent("souviens-toi que j'ai un chat") is True

    def test_souviens_toi_que_sans_tiret(self):
        assert _detect_note_intent("souviens toi que je suis développeur") is True

    def test_garde_en_memoire(self):
        assert _detect_note_intent("garde en mémoire que je préfère le thé") is True

    def test_garde_ca_en_memoire(self):
        assert _detect_note_intent("garde ça en mémoire") is True

    def test_enregistre_que(self):
        assert _detect_note_intent("enregistre que mon budget est 500€") is True

    def test_enregistre_ceci(self):
        assert _detect_note_intent("enregistre ceci") is True

    def test_enregistre_ca(self):
        assert _detect_note_intent("enregistre ça") is True

    def test_ajoute_a_ma_memoire(self):
        assert _detect_note_intent("ajoute à ma mémoire que je déteste les réunions") is True

    def test_ecris_dans_ma_memoire_sans_accent(self):
        assert _detect_note_intent("ecris dans ma memoire que j'aime le vélo") is True

    def test_ecris_dans_ma_memoire_avec_accent(self):
        assert _detect_note_intent("écris dans ma mémoire ma décision") is True

    def test_ajoute_a_mes_notes(self):
        assert _detect_note_intent("ajoute à mes notes que j'utilise uv") is True

    def test_ajoute_dans_mes_notes(self):
        assert _detect_note_intent("ajoute dans mes notes : deadline vendredi") is True

    def test_important_a_noter_avec_accent(self):
        assert _detect_note_intent("important à noter : réunion annulée") is True

    def test_important_a_noter_sans_accent(self):
        assert _detect_note_intent("important a noter que le client a changé") is True

    def test_a_noter_avec_accent(self):
        assert _detect_note_intent("à noter : livraison demain") is True

    def test_a_noter_sans_accent(self):
        assert _detect_note_intent("a noter : nouvelle adresse mail") is True

    def test_case_insensitive(self):
        assert _detect_note_intent("NOTE QUE je préfère le café") is True

    def test_mixed_case(self):
        assert _detect_note_intent("Retiens Que mon pseudo c'est Mathi") is True

    def test_keyword_in_middle_of_sentence(self):
        assert _detect_note_intent("s'il te plaît note que je suis gaucher") is True


# ══════════════════════════════════════════════════════════════════
# 1b. _detect_note_intent — vrais négatifs
# ══════════════════════════════════════════════════════════════════

class TestDetectNoteIntentNegatives:

    def test_simple_question(self):
        assert _detect_note_intent("comment tu vas ?") is False

    def test_conversation_greeting(self):
        assert _detect_note_intent("bonjour Mnemo") is False

    def test_memory_query(self):
        assert _detect_note_intent("qu'est-ce que je t'ai dit sur mon projet ?") is False

    def test_calendar_query(self):
        assert _detect_note_intent("qu'est-ce que j'ai prévu demain ?") is False

    def test_shell_command(self):
        assert _detect_note_intent("liste les fichiers dans docs") is False

    def test_scheduler_request(self):
        assert _detect_note_intent("rappelle-moi dans 3h de prendre mes médicaments") is False

    def test_philosophical_note(self):
        # "note" seul en milieu de phrase technique ne doit pas déclencher
        assert _detect_note_intent("prends note mentalement") is False

    def test_technical_context_note(self):
        # "note" dans contexte de musique ne doit pas déclencher
        assert _detect_note_intent("quelle est la note de ce morceau ?") is False

    def test_empty_string(self):
        assert _detect_note_intent("") is False

    def test_merci(self):
        assert _detect_note_intent("merci !") is False

    def test_souvenir_without_keyword(self):
        # "souviens" seul sans "toi que" ne doit pas déclencher
        assert _detect_note_intent("tu t'en souviens ?") is False


# ══════════════════════════════════════════════════════════════════
# 2. NoteWriterCrew.run() — N3, kickoff mocké
# ══════════════════════════════════════════════════════════════════

class TestNoteWriterCrewRun:

    def _run_with_mock(self, user_message: str, response: str = "Note enregistrée.") -> tuple:
        """Lance NoteWriterCrew.run() avec kickoff mocké. Retourne (result, mock_kickoff)."""
        mock_kickoff_result = _mock_crew_result(response)
        with patch.object(NoteWriterCrew, 'crew') as mock_crew_method:
            mock_crew_obj = MagicMock()
            mock_crew_method.return_value = mock_crew_obj
            mock_crew_obj.kickoff.return_value = mock_kickoff_result
            result = NoteWriterCrew().run({"user_message": user_message})
        return result, mock_crew_obj.kickoff

    def test_run_returns_string(self):
        result, _ = self._run_with_mock("note que je préfère vim")
        assert isinstance(result, str)

    def test_run_returns_crew_response(self):
        result, _ = self._run_with_mock("note que je préfère vim", "Note enregistrée dans Préférences.")
        assert result == "Note enregistrée dans Préférences."

    def test_run_strips_whitespace(self):
        result, _ = self._run_with_mock("retiens que j'habite à Bordeaux", "  Ok.  ")
        assert result == "Ok."

    def test_run_calls_kickoff_with_user_message(self):
        msg = "mémorise que mon projet c'est Mnemo"
        _, mock_kickoff = self._run_with_mock(msg)
        mock_kickoff.assert_called_once()
        call_kwargs = mock_kickoff.call_args
        inputs = call_kwargs[1]["inputs"] if call_kwargs[1] else call_kwargs[0][0]
        assert inputs["user_message"] == msg

    def test_run_kickoff_called_exactly_once(self):
        _, mock_kickoff = self._run_with_mock("enregistre que je préfère le thé")
        assert mock_kickoff.call_count == 1

    def test_run_with_empty_response(self):
        result, _ = self._run_with_mock("note que ...", "")
        assert result == ""

    def test_run_with_multiline_response(self):
        response = "Note enregistrée.\nSection : Préférences."
        result, _ = self._run_with_mock("retiens que j'utilise FastAPI", response)
        assert "Note enregistrée" in result


# ══════════════════════════════════════════════════════════════════
# 3. dispatch — route=note
# ══════════════════════════════════════════════════════════════════

class TestRouteNote:

    def _rr(self, route="note", **meta):
        return RouterResult(route=route, confidence=1.0, handler="test", metadata=meta)

    def _call_route_note(self, note_response="Note sauvegardée."):
        rr = self._rr("note")
        with patch("Mnemo.crew.NoteWriterCrew") as mock_note_cls, \
             patch("Mnemo.crew.ConversationCrew") as mock_conv_cls:
            mock_note_cls.return_value.run.return_value = note_response
            result = dispatch(rr, "note que je préfère vim", "s1", "ctx", "")
        return result, mock_note_cls, mock_conv_cls

    def test_note_route_calls_note_writer_crew(self):
        _, mock_note, _ = self._call_route_note()
        assert mock_note.called

    def test_note_route_does_not_call_conversation_crew(self):
        _, _, mock_conv = self._call_route_note()
        assert not mock_conv.called

    def test_note_route_returns_crew_response(self):
        result, _, _ = self._call_route_note("Préférence Vim enregistrée.")
        assert result == "Préférence Vim enregistrée."

    def test_note_route_passes_user_message(self):
        rr = self._rr("note")
        user_msg = "note que j'habite à Bordeaux"
        with patch("Mnemo.crew.NoteWriterCrew") as mock_note_cls:
            mock_note_cls.return_value.run.return_value = "ok"
            dispatch(rr, user_msg, "s1", "ctx", "")
        inputs = mock_note_cls.return_value.run.call_args[0][0]
        assert inputs["user_message"] == user_msg

    def test_note_route_distinct_from_conversation(self):
        """route=note et route=conversation ne déclenchent pas le même crew."""
        rr_conv = self._rr("conversation")
        with patch("Mnemo.crew.NoteWriterCrew") as mock_note, \
             patch("Mnemo.crew.ConversationCrew") as mock_conv:
            mock_conv.return_value.crew.return_value.kickoff.return_value = _mock_crew_result("réponse conv")
            dispatch(rr_conv, "test", "s1", "ctx", "")
        assert not mock_note.called
        assert mock_conv.called


# ══════════════════════════════════════════════════════════════════
# 4. handle_message — kw_note bypass LLM
# ══════════════════════════════════════════════════════════════════

class TestHandleMessageNoteBypass:

    def _mock_router(self, route="note"):
        rr = RouterResult(route=route, confidence=1.0, handler="keyword", metadata={})
        mock_handler = MagicMock()
        mock_handler.handle.return_value = rr
        return mock_handler

    def _confirmed(self, rr, user_msg="note que je préfère vim"):
        return ConfirmationResult(result=rr, user_message=user_msg,
                                  web_context="", shell_command="")

    def test_note_keyword_bypasses_evaluation_crew(self):
        """Quand route=note, EvaluationCrew ne doit pas être appelé."""
        rr = RouterResult(route="note", confidence=1.0, handler="keyword", metadata={})
        with patch("Mnemo.routing.build_router", return_value=self._mock_router("note")), \
             patch("Mnemo.routing.confirmation.run_confirmation_middleware",
                   return_value=self._confirmed(rr, "note que je préfère vim")), \
             patch("Mnemo.routing.dispatch", return_value="Note enregistrée.") as mock_dispatch, \
             patch.object(mn, 'EvaluationCrew') as mock_eval, \
             patch.object(mn, 'get_temporal_context', return_value="ctx"), \
             patch.object(mn, 'update_session_memory'):
            result = mn.handle_message("note que je préfère vim", "session_1")
        assert not mock_eval.called
        assert mock_dispatch.called
        dispatched_route = mock_dispatch.call_args[0][0].route
        assert dispatched_route == "note"

    def test_note_keyword_returns_note_crew_response(self):
        rr = RouterResult(route="note", confidence=1.0, handler="keyword", metadata={})
        with patch("Mnemo.routing.build_router", return_value=self._mock_router("note")), \
             patch("Mnemo.routing.confirmation.run_confirmation_middleware",
                   return_value=self._confirmed(rr, "note que j'habite à Bordeaux")), \
             patch("Mnemo.routing.dispatch", return_value="Bordeaux enregistré."), \
             patch.object(mn, 'get_temporal_context', return_value="ctx"), \
             patch.object(mn, 'update_session_memory'):
            result = mn.handle_message("note que j'habite à Bordeaux", "session_1")
        assert result == "Bordeaux enregistré."

    def test_non_note_message_does_not_dispatch_to_note(self):
        """Un message sans keyword note ne doit pas router vers note."""
        rr = RouterResult(route="conversation", confidence=1.0, handler="keyword", metadata={})
        with patch("Mnemo.routing.build_router", return_value=self._mock_router("conversation")), \
             patch("Mnemo.routing.confirmation.run_confirmation_middleware",
                   return_value=self._confirmed(rr, "comment tu vas ?")), \
             patch("Mnemo.routing.dispatch", return_value="réponse") as mock_dispatch, \
             patch.object(mn, 'get_temporal_context', return_value="ctx"), \
             patch.object(mn, 'update_session_memory'):
            mn.handle_message("comment tu vas ?", "session_1")
        dispatched_route = mock_dispatch.call_args[0][0].route
        assert dispatched_route != "note"

    def test_note_route_in_dispatch(self):
        """route=note dans dispatch retourne la réponse du NoteWriterCrew."""
        rr = RouterResult(route="note", confidence=1.0, handler="keyword", metadata={})
        with patch("Mnemo.crew.NoteWriterCrew") as mock_note:
            mock_note.return_value.run.return_value = "ok"
            result = dispatch(rr, "msg", "s1", "ctx", "")
        assert result == "ok"
