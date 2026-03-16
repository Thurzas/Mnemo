"""
Tests unitaires Niveau 3 — Cycle de session complet
Aucun LLM requis — les crews CrewAI sont mockés.
Teste la logique de session, consolidation et rattrapage des orphelins.

Lance avec : uv run pytest tests/test_session_cycle.py -v
"""
import json
import pytest

from pathlib import Path
from unittest.mock import MagicMock, patch


# ══════════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════════

def make_crew_result(text: str) -> MagicMock:
    """Simule le résultat d'un crew.kickoff()."""
    result = MagicMock()
    result.raw = text
    return result


# ══════════════════════════════════════════════════════════════
# Fixtures
# ══════════════════════════════════════════════════════════════

@pytest.fixture(autouse=True)
def patch_sessions_dir(tmp_path, monkeypatch):
    """
    Redirige _sessions_dir() vers un dossier temporaire pour tous les tests.
    Patche get_data_dir dans memory_tools — _sessions_dir() et _markdown_path()
    utilisent toutes les deux cette fonction, donc un seul patch suffit.
    autouse=True : appliqué automatiquement à chaque test du module.
    """
    monkeypatch.setattr("Mnemo.tools.memory_tools.get_data_dir", lambda: tmp_path)
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    return sessions


@pytest.fixture
def sessions_dir(tmp_path) -> Path:
    """Accès direct au dossier sessions temporaire."""
    return tmp_path / "sessions"


# ══════════════════════════════════════════════════════════════
# new_session_id
# ══════════════════════════════════════════════════════════════

class TestNewSessionId:

    def test_format_attendu(self):
        from Mnemo.main import new_session_id
        sid = new_session_id()
        assert sid.startswith("session_")

    def test_deux_ids_sont_differents(self):
        from Mnemo.main import new_session_id
        assert new_session_id() != new_session_id()

    def test_contient_date(self):
        from Mnemo.main import new_session_id
        from datetime import datetime
        sid = new_session_id()
        today = datetime.now().strftime("%Y%m%d")
        assert today in sid


# ══════════════════════════════════════════════════════════════
# handle_message — crew mocké
# ══════════════════════════════════════════════════════════════

class TestHandleMessage:

    @pytest.fixture(autouse=True)
    def mock_conversation_crew(self):
        """
        Mock le routing et ConversationCrew pour ne pas appeler de LLM.
        build_router → retourne directement "conversation" (bypass KeywordHandler/ML/LLM).
        run_confirmation_middleware → passthrough.
        dispatch → retourne la réponse du ConversationCrew mocké.
        """
        from Mnemo.routing.context import RouterResult
        from Mnemo.routing.confirmation import ConfirmationResult

        mock_router = MagicMock()
        mock_router.handle.return_value = RouterResult("conversation", 1.0, "keyword")

        def fake_confirmation(result, user_message, temporal_ctx):
            return ConfirmationResult(result=result, user_message=user_message)

        def fake_dispatch(result, **kwargs):
            return "Bonjour Matt, je me souviens de toi !"

        with patch("Mnemo.routing.build_router", return_value=mock_router), \
             patch("Mnemo.routing.confirmation.run_confirmation_middleware", side_effect=fake_confirmation), \
             patch("Mnemo.routing.dispatch", side_effect=fake_dispatch):
            yield

    def test_retourne_la_reponse_du_crew(self):
        from Mnemo.main import handle_message
        response = handle_message("Salut", "sess_test_001")
        assert response == "Bonjour Matt, je me souviens de toi !"

    def test_ecrit_la_session_sur_disque(self, sessions_dir):
        from Mnemo.main import handle_message
        handle_message("Message test", "sess_test_002")
        assert (sessions_dir / "sess_test_002.json").exists()

    def test_accumule_les_messages_sur_plusieurs_appels(self, sessions_dir):
        from Mnemo.main import handle_message
        handle_message("Message 1", "sess_test_003")
        handle_message("Message 2", "sess_test_003")
        handle_message("Message 3", "sess_test_003")
        data = json.loads((sessions_dir / "sess_test_003.json").read_text())
        # 3 user + 3 agent = 6 messages
        assert len(data["messages"]) == 6

    def test_messages_en_ordre_chronologique(self, sessions_dir):
        from Mnemo.main import handle_message
        handle_message("Premier", "sess_test_004")
        handle_message("Deuxième", "sess_test_004")
        data = json.loads((sessions_dir / "sess_test_004.json").read_text())
        assert data["messages"][0]["content"] == "Premier"
        assert data["messages"][2]["content"] == "Deuxième"

    def test_session_id_correct_dans_le_fichier(self, sessions_dir):
        from Mnemo.main import handle_message
        handle_message("Test", "sess_mon_id")
        data = json.loads((sessions_dir / "sess_mon_id.json").read_text())
        assert data["session_id"] == "sess_mon_id"


# ══════════════════════════════════════════════════════════════
# end_session — crew mocké
# ══════════════════════════════════════════════════════════════

class TestEndSession:

    @pytest.fixture(autouse=True)
    def mock_consolidation_crew(self):
        """Mock ConsolidationCrew pour ne pas appeler de LLM."""
        with patch("Mnemo.main.ConsolidationCrew") as mock_cls:
            mock_instance = MagicMock()
            mock_instance.crew.return_value.kickoff.return_value = make_crew_result(
                '{"worth_persisting": true, "facts": [], "session_summary": "Session de test."}'
            )
            mock_cls.return_value = mock_instance
            yield mock_cls

    def test_session_vide_retourne_message_sans_consolider(self):
        """end_session sur une session inexistante ne doit pas appeler le crew."""
        from Mnemo.main import end_session, ConsolidationCrew
        result = end_session("session_qui_nexiste_pas")
        assert result[0] == "Session vide, rien à consolider."
        ConsolidationCrew.assert_not_called()

    def test_session_avec_contenu_appelle_le_crew(self, sessions_dir):
        """end_session sur une session réelle doit appeler ConsolidationCrew."""
        from Mnemo.main import end_session, ConsolidationCrew
        # Crée une session avec du contenu
        session_data = {
            "session_id": "sess_consolidation",
            "messages": [
                {"role": "user", "content": "Je m'appelle Matt"},
                {"role": "agent", "content": "Bonjour Matt !"},
            ]
        }
        (sessions_dir / "sess_consolidation.json").write_text(
            json.dumps(session_data), encoding="utf-8"
        )
        end_session("sess_consolidation")
        ConsolidationCrew.assert_called_once()

    def test_cree_fichier_done_apres_consolidation(self, sessions_dir):
        """Un fichier .done doit être créé après une consolidation réussie."""
        from Mnemo.main import end_session
        session_data = {"session_id": "sess_done_test", "messages": [{"role": "user", "content": "Test"}]}
        (sessions_dir / "sess_done_test.json").write_text(
            json.dumps(session_data), encoding="utf-8"
        )
        end_session("sess_done_test")
        assert (sessions_dir / "sess_done_test.done").exists()

    def test_session_json_passe_au_crew(self, sessions_dir):
        """Le contenu JSON de la session doit être passé au crew en entrée."""
        from Mnemo.main import end_session, ConsolidationCrew
        session_data = {
            "session_id": "sess_content_check",
            "messages": [{"role": "user", "content": "Contenu important"}]
        }
        (sessions_dir / "sess_content_check.json").write_text(
            json.dumps(session_data), encoding="utf-8"
        )
        end_session("sess_content_check")
        # Vérifie que kickoff a bien reçu le session_json
        call_inputs = ConsolidationCrew.return_value.crew.return_value.kickoff.call_args[1]["inputs"]
        assert "session_json" in call_inputs
        assert "Contenu important" in call_inputs["session_json"]

    def test_retourne_le_raw_du_crew(self, sessions_dir):
        from Mnemo.main import end_session
        session_data = {"session_id": "sess_raw", "messages": [{"role": "user", "content": "Test"}]}
        (sessions_dir / "sess_raw.json").write_text(
            json.dumps(session_data), encoding="utf-8"
        )
        result = end_session("sess_raw")
        assert "Session de test" in result[0]


# ══════════════════════════════════════════════════════════════
# consolidate_orphan_sessions — crew mocké
# ══════════════════════════════════════════════════════════════

class TestConsolidateOrphanSessions:

    @pytest.fixture(autouse=True)
    def mock_end_session(self):
        """Mock end_session pour ne pas appeler de crew."""
        with patch("Mnemo.main.end_session") as mock_fn:
            mock_fn.return_value = "Consolidation OK"
            yield mock_fn

    def test_aucun_orphelin_ne_fait_rien(self, sessions_dir):
        from Mnemo.main import consolidate_orphan_sessions, end_session
        consolidate_orphan_sessions()
        end_session.assert_not_called()

    def test_session_json_sans_done_est_traitee(self, sessions_dir):
        from Mnemo.main import consolidate_orphan_sessions, end_session
        session_data = {"session_id": "orphan_001", "messages": [{"role": "user", "content": "Test"}]}
        (sessions_dir / "orphan_001.json").write_text(
            json.dumps(session_data), encoding="utf-8"
        )
        consolidate_orphan_sessions()
        end_session.assert_called_once_with("orphan_001")

    def test_session_avec_done_est_ignoree(self, sessions_dir):
        from Mnemo.main import consolidate_orphan_sessions, end_session
        session_data = {"session_id": "done_001", "messages": []}
        (sessions_dir / "done_001.json").write_text(json.dumps(session_data), encoding="utf-8")
        (sessions_dir / "done_001.done").touch()  # déjà traitée
        consolidate_orphan_sessions()
        end_session.assert_not_called()

    def test_plusieurs_orphelins_tous_traites(self, sessions_dir):
        from Mnemo.main import consolidate_orphan_sessions, end_session
        for i in range(3):
            session_data = {"session_id": f"orphan_{i:03d}", "messages": [{"role": "user", "content": "Test"}]}
            (sessions_dir / f"orphan_{i:03d}.json").write_text(
                json.dumps(session_data), encoding="utf-8"
            )
        consolidate_orphan_sessions()
        assert end_session.call_count == 3

    def test_session_vide_marquee_done_sans_appeler_crew(self, sessions_dir):
        """Une session JSON vide doit être marquée .done sans appeler end_session."""
        from Mnemo.main import consolidate_orphan_sessions, end_session
        (sessions_dir / "empty_orphan.json").write_text("", encoding="utf-8")
        consolidate_orphan_sessions()
        end_session.assert_not_called()
        assert (sessions_dir / "empty_orphan.done").exists()

    def test_session_broken_ignoree(self, sessions_dir):
        """Les fichiers .broken.json ne doivent pas être traités."""
        from Mnemo.main import consolidate_orphan_sessions, end_session
        (sessions_dir / "corrupted.broken.json").write_text(
            '{"session_id": "corrupted"}', encoding="utf-8"
        )
        consolidate_orphan_sessions()
        end_session.assert_not_called()

    def test_echec_consolidation_marque_quand_meme_done(self, sessions_dir):
        """Si end_session lève une exception, la session doit quand même être marquée .done."""
        from Mnemo.main import consolidate_orphan_sessions, end_session
        end_session.side_effect = Exception("Crew indisponible")
        session_data = {"session_id": "failing_orphan", "messages": [{"role": "user", "content": "Test"}]}
        (sessions_dir / "failing_orphan.json").write_text(
            json.dumps(session_data), encoding="utf-8"
        )
        # Ne doit pas lever d'exception vers l'appelant
        consolidate_orphan_sessions()
        assert (sessions_dir / "failing_orphan.done").exists()

    def test_mix_orphelins_et_done(self, sessions_dir):
        """Seules les sessions sans .done doivent être traitées."""
        from Mnemo.main import consolidate_orphan_sessions, end_session
        session_data = {"session_id": "s", "messages": [{"role": "user", "content": "Test"}]}
        # 2 orphelins
        for i in ["a", "b"]:
            (sessions_dir / f"sess_{i}.json").write_text(
                json.dumps({**session_data, "session_id": f"sess_{i}"}), encoding="utf-8"
            )
        # 1 déjà traitée
        (sessions_dir / "sess_c.json").write_text(
            json.dumps({**session_data, "session_id": "sess_c"}), encoding="utf-8"
        )
        (sessions_dir / "sess_c.done").touch()

        consolidate_orphan_sessions()
        assert end_session.call_count == 2


# ══════════════════════════════════════════════════════════════
# Scénarios bout en bout (sans LLM)
# ══════════════════════════════════════════════════════════════

class TestSessionScenarios:
    """
    Scénarios complets qui simulent un cycle conversation → consolidation.
    Tous les appels LLM sont mockés.
    """

    @pytest.fixture(autouse=True)
    def mock_all_crews(self):
        from Mnemo.routing.context import RouterResult
        from Mnemo.routing.confirmation import ConfirmationResult

        mock_router = MagicMock()
        mock_router.handle.return_value = RouterResult("conversation", 1.0, "keyword")

        def fake_confirmation(result, user_message, temporal_ctx):
            return ConfirmationResult(result=result, user_message=user_message)

        def fake_dispatch(result, **kwargs):
            return "Réponse de l'agent."

        with patch("Mnemo.routing.build_router", return_value=mock_router), \
             patch("Mnemo.routing.confirmation.run_confirmation_middleware", side_effect=fake_confirmation), \
             patch("Mnemo.routing.dispatch", side_effect=fake_dispatch), \
             patch("Mnemo.main.ConsolidationCrew") as mock_consol:
            mock_consol.return_value.crew.return_value.kickoff.return_value = \
                make_crew_result('{"worth_persisting": true, "facts": [], "session_summary": "OK"}')
            yield

    def test_scenario_session_normale(self, sessions_dir):
        """Scénario A : 3 messages → session JSON correcte → consolidation → .done créé."""
        from Mnemo.main import handle_message, end_session

        sid = "scenario_normale"
        handle_message("Bonjour", sid)
        handle_message("Je m'appelle Matt", sid)
        handle_message("Je travaille sur Mnemo", sid)

        # Vérifie la session
        data = json.loads((sessions_dir / f"{sid}.json").read_text())
        assert len(data["messages"]) == 6
        assert data["messages"][2]["content"] == "Je m'appelle Matt"

        # Consolide
        result = end_session(sid)
        assert (sessions_dir / f"{sid}.done").exists()
        assert isinstance(result[0], str)

    def test_scenario_session_vide_exit_immediat(self, sessions_dir):
        """Scénario B : l'utilisateur quitte sans envoyer de message → rien à consolider."""
        from Mnemo.main import end_session

        sid = "scenario_vide"
        # Pas de handle_message → pas de fichier JSON
        result = end_session(sid)
        assert result[0] == "Session vide, rien à consolider."
        # Pas de fichier .done non plus (session vide = session inexistante)
        assert not (sessions_dir / f"{sid}.done").exists()

    def test_scenario_rattrapage_au_demarrage(self, sessions_dir):
        """Scénario C : session orpheline du run précédent → rattrapée au démarrage."""
        from Mnemo.main import handle_message, consolidate_orphan_sessions

        # Simule une session interrompue (pas de .done)
        sid = "scenario_orphan"
        handle_message("Message avant CTRL+C", sid)
        assert not (sessions_dir / f"{sid}.done").exists()

        # Au prochain démarrage
        with patch("Mnemo.main.end_session", return_value="OK") as mock_end:
            consolidate_orphan_sessions()
            mock_end.assert_called_once_with(sid)

    def test_scenario_deux_sessions_independantes(self, sessions_dir):
        """Scénario D : deux sessions distinctes ne doivent pas se mélanger."""
        from Mnemo.main import handle_message

        handle_message("Session 1 — message A", "sess_user1")
        handle_message("Session 2 — message B", "sess_user2")

        data1 = json.loads((sessions_dir / "sess_user1.json").read_text())
        data2 = json.loads((sessions_dir / "sess_user2.json").read_text())

        assert len(data1["messages"]) == 2
        assert len(data2["messages"]) == 2
        assert "Session 1" in data1["messages"][0]["content"]
        assert "Session 2" in data2["messages"][0]["content"]