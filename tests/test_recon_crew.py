"""
Tests Phase 6 — ReconnaissanceCrew : résolution hints, lecture fichiers, synthèse LLM.
Niveau 1 (Python pur) + Niveau 3 (LLM mocké au niveau crew.kickoff()).
"""
import json
import pytest
from unittest.mock import patch, MagicMock
from pathlib import Path


# ── Fixture ───────────────────────────────────────────────────

@pytest.fixture()
def recon_env(tmp_path, monkeypatch):
    import Mnemo.tools.memory_tools as mt
    import Mnemo.context as ctx
    monkeypatch.setattr(mt, "get_data_dir", lambda: tmp_path)
    monkeypatch.setattr(ctx, "get_data_dir", lambda: tmp_path)
    return tmp_path


def _mock_kickoff(raw: str) -> MagicMock:
    m = MagicMock()
    m.raw = raw
    return m


def _recon_json(**kwargs) -> str:
    base = {
        "files_read":     ["src/module.py"],
        "symbols_found":  {"ma_fonction": "ligne 42"},
        "existing_tests": ["tests/test_module.py"],
        "key_imports":    ["os", "json"],
        "entry_points":   ["ma_fonction()"],
        "todos_stubs":    [],
        "summary":        "Le module existe et contient ma_fonction.",
    }
    base.update(kwargs)
    return json.dumps(base)


# ── _resolve_hints (Python pur) ───────────────────────────────

class TestResolveHints:
    def test_chemin_direct_existant(self, tmp_path):
        from Mnemo.crew import ReconnaissanceCrew
        f = tmp_path / "mon_module.py"
        f.write_text("# contenu")
        result = ReconnaissanceCrew._resolve_hints([str(f)])
        assert str(f) in result

    def test_chemin_inexistant_ignore(self, tmp_path):
        from Mnemo.crew import ReconnaissanceCrew
        result = ReconnaissanceCrew._resolve_hints([str(tmp_path / "absent.py")])
        assert result == []

    def test_hints_vides(self):
        from Mnemo.crew import ReconnaissanceCrew
        assert ReconnaissanceCrew._resolve_hints([]) == []

    def test_deduplication(self, tmp_path):
        from Mnemo.crew import ReconnaissanceCrew
        f = tmp_path / "mod.py"
        f.write_text("x = 1")
        result = ReconnaissanceCrew._resolve_hints([str(f), str(f)])
        assert result.count(str(f)) == 1


# ── _load_files (Python pur) ──────────────────────────────────

class TestLoadFiles:
    def test_lit_fichier_existant(self, tmp_path):
        from Mnemo.crew import ReconnaissanceCrew
        f = tmp_path / "code.py"
        f.write_text("def foo(): pass")
        contents = ReconnaissanceCrew._load_files([str(f)], max_chars=500)
        assert "def foo(): pass" in contents[str(f)]

    def test_tronque_fichier_long(self, tmp_path):
        from Mnemo.crew import ReconnaissanceCrew
        f = tmp_path / "long.py"
        f.write_text("x" * 5000)
        contents = ReconnaissanceCrew._load_files([str(f)], max_chars=100)
        assert "tronqué" in contents[str(f)]
        assert len(contents[str(f)]) < 5000

    def test_fichier_absent_message_erreur(self, tmp_path):
        from Mnemo.crew import ReconnaissanceCrew
        path = str(tmp_path / "absent.py")
        contents = ReconnaissanceCrew._load_files([path], max_chars=500)
        assert "impossible" in contents[path].lower()

    def test_retourne_dict_vide_si_aucun_fichier(self):
        from Mnemo.crew import ReconnaissanceCrew
        assert ReconnaissanceCrew._load_files([], max_chars=500) == {}


# ── run() — intégration ───────────────────────────────────────

class TestReconCrewRun:
    def test_retourne_dict(self, recon_env):
        from Mnemo.crew import ReconnaissanceCrew
        with patch("Mnemo.crew.ReconnaissanceCrew.crew") as mock_crew:
            mock_crew.return_value.kickoff.return_value = _mock_kickoff(_recon_json())
            result = ReconnaissanceCrew().run({"goal": "implémenter X", "hints": []})
        assert isinstance(result, dict)

    def test_summary_present(self, recon_env):
        from Mnemo.crew import ReconnaissanceCrew
        with patch("Mnemo.crew.ReconnaissanceCrew.crew") as mock_crew:
            mock_crew.return_value.kickoff.return_value = _mock_kickoff(_recon_json())
            result = ReconnaissanceCrew().run({"goal": "goal", "hints": []})
        assert "summary" in result

    def test_persiste_dans_world_state(self, recon_env):
        from Mnemo.crew import ReconnaissanceCrew
        with patch("Mnemo.crew.ReconnaissanceCrew.crew") as mock_crew:
            mock_crew.return_value.kickoff.return_value = _mock_kickoff(_recon_json())
            ReconnaissanceCrew().run({"goal": "goal", "hints": []})
        ws_path = recon_env / "world_state.json"
        assert ws_path.exists()
        ws = json.loads(ws_path.read_text())
        assert "recon_context" in ws

    def test_knows_module_false_si_aucun_fichier(self, recon_env):
        from Mnemo.crew import ReconnaissanceCrew
        with patch("Mnemo.crew.ReconnaissanceCrew.crew") as mock_crew:
            mock_crew.return_value.kickoff.return_value = _mock_kickoff(_recon_json())
            ReconnaissanceCrew().run({"goal": "goal", "hints": []})
        ws = json.loads((recon_env / "world_state.json").read_text())
        assert ws["knows_module"] is False

    def test_knows_module_true_si_fichier_trouve(self, recon_env):
        from Mnemo.crew import ReconnaissanceCrew
        f = recon_env / "module.py"
        f.write_text("def foo(): pass")
        with patch("Mnemo.crew.ReconnaissanceCrew.crew") as mock_crew:
            mock_crew.return_value.kickoff.return_value = _mock_kickoff(_recon_json())
            ReconnaissanceCrew().run({"goal": "goal", "hints": [str(f)]})
        ws = json.loads((recon_env / "world_state.json").read_text())
        assert ws["knows_module"] is True

    def test_hints_string_splitte_par_virgule(self, recon_env):
        from Mnemo.crew import ReconnaissanceCrew
        captured = {}
        def fake_kickoff(inputs):
            captured.update(inputs)
            return _mock_kickoff(_recon_json())
        with patch("Mnemo.crew.ReconnaissanceCrew.crew") as mock_crew:
            mock_crew.return_value.kickoff.side_effect = fake_kickoff
            ReconnaissanceCrew().run({"goal": "goal", "hints": "fichier_a.py, fichier_b.py"})
        assert "fichier_a.py" in captured.get("hints", "")

    def test_fallback_si_llm_retourne_json_invalide(self, recon_env):
        from Mnemo.crew import ReconnaissanceCrew
        with patch("Mnemo.crew.ReconnaissanceCrew.crew") as mock_crew:
            mock_crew.return_value.kickoff.return_value = _mock_kickoff("PAS DU JSON")
            result = ReconnaissanceCrew().run({"goal": "goal", "hints": []})
        assert "summary" in result
        assert "erreur" in result["summary"].lower()

    def test_contenu_fichier_transmis_au_llm(self, recon_env):
        from Mnemo.crew import ReconnaissanceCrew
        f = recon_env / "cible.py"
        f.write_text("def fonctionCible(): return 42")
        captured = {}
        def fake_kickoff(inputs):
            captured.update(inputs)
            return _mock_kickoff(_recon_json())
        with patch("Mnemo.crew.ReconnaissanceCrew.crew") as mock_crew:
            mock_crew.return_value.kickoff.side_effect = fake_kickoff
            ReconnaissanceCrew().run({"goal": "goal", "hints": [str(f)]})
        assert "fonctionCible" in captured.get("file_contents", "")
