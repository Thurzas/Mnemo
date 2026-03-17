"""
Tests unitaires Phase 5.5 — Retrieval budget

Couvre :
  - _build_memory_overview : carte structurelle de memory.md
  - _compress_chunks : compression en texte injectable
  - _record_retrieved_chunks : enregistrement offline dans chunk_usage
  - Schéma DB : colonne chunk_usage.profile
  - Intégration curiosity_session : memory_overview + memory_recent injectés

Aucun LLM requis.

Lance avec :
    pytest tests/test_retrieval_budget.py -v
"""
import json
import sqlite3
import pytest

from pathlib import Path
from unittest.mock import patch, MagicMock


# ══════════════════════════════════════════════════════════════
# Fixtures
# ══════════════════════════════════════════════════════════════

@pytest.fixture
def mem_db(tmp_path, monkeypatch):
    from Mnemo.init_db import init_db
    monkeypatch.setattr("Mnemo.tools.memory_tools.get_data_dir", lambda: tmp_path)
    db_path = tmp_path / "memory.db"
    init_db(db_path)
    return db_path


SAMPLE_MEMORY_MD = """\
## Identité Utilisateur
### Profil de base
- **Nom** : Alice
- **Métier** : Développeuse
### Préférences & style
### Centres d'intérêt

## Connaissances persistantes
### Projets en cours
- Projet Mnemo en cours de développement
### Décisions prises
- Utiliser SQLite pour la persistance
- Préférer les modèles locaux
### À ne jamais oublier

## Historique des sessions
"""


# ══════════════════════════════════════════════════════════════
# Schéma DB — colonne profile dans chunk_usage
# ══════════════════════════════════════════════════════════════

class TestDBSchema:

    def test_chunk_usage_a_colonne_profile(self, mem_db):
        db   = sqlite3.connect(str(mem_db))
        cols = {r[1] for r in db.execute("PRAGMA table_info(chunk_usage)")}
        db.close()
        assert "profile" in cols

    def test_profile_default_conversation(self, mem_db):
        db = sqlite3.connect(str(mem_db))
        db.execute(
            "INSERT INTO chunks (id, section, content, category) VALUES (?,?,?,?)",
            ("c1", "Test", "contenu", "connaissance"),
        )
        db.execute(
            "INSERT INTO chunk_usage (chunk_id, session_id) VALUES (?, ?)",
            ("c1", "sess_1"),
        )
        db.commit()
        row = db.execute("SELECT profile FROM chunk_usage WHERE chunk_id='c1'").fetchone()
        db.close()
        assert row[0] == "conversation"


# ══════════════════════════════════════════════════════════════
# _build_memory_overview
# ══════════════════════════════════════════════════════════════

class TestBuildMemoryOverview:

    def test_retourne_structure_sections(self, mem_db, tmp_path):
        (tmp_path / "memory.md").write_text(SAMPLE_MEMORY_MD, encoding="utf-8")
        from Mnemo.tools.memory_tools import _build_memory_overview
        result = _build_memory_overview()
        assert "## Identité Utilisateur" in result
        assert "### Profil de base" in result

    def test_section_vide_marquee_vide(self, mem_db, tmp_path):
        (tmp_path / "memory.md").write_text(SAMPLE_MEMORY_MD, encoding="utf-8")
        from Mnemo.tools.memory_tools import _build_memory_overview
        result = _build_memory_overview()
        assert "VIDE" in result  # Préférences & style est vide

    def test_section_avec_contenu_affiche_nb_lignes(self, mem_db, tmp_path):
        (tmp_path / "memory.md").write_text(SAMPLE_MEMORY_MD, encoding="utf-8")
        from Mnemo.tools.memory_tools import _build_memory_overview
        result = _build_memory_overview()
        # Profil de base a 2 lignes de contenu
        assert "lignes" in result

    def test_fichier_absent_retourne_message(self, mem_db, tmp_path):
        from Mnemo.tools.memory_tools import _build_memory_overview
        result = _build_memory_overview()
        assert "absent" in result.lower() or "non initialisée" in result.lower()

    def test_budget_raisonnable(self, mem_db, tmp_path):
        # Même avec un memory.md bien rempli, l'overview reste compact
        big_md = SAMPLE_MEMORY_MD + ("- ligne de contenu\n" * 200)
        (tmp_path / "memory.md").write_text(big_md, encoding="utf-8")
        from Mnemo.tools.memory_tools import _build_memory_overview
        result = _build_memory_overview()
        # Estimation : overview ≤ 300 tokens ≈ 1200 chars
        assert len(result) <= 1500

    def test_memory_vide_retourne_message(self, mem_db, tmp_path):
        (tmp_path / "memory.md").write_text("", encoding="utf-8")
        from Mnemo.tools.memory_tools import _build_memory_overview
        result = _build_memory_overview()
        assert result  # ne lève pas d'exception


# ══════════════════════════════════════════════════════════════
# _compress_chunks
# ══════════════════════════════════════════════════════════════

class TestCompressChunks:

    def _make_chunk(self, chunk_id: str, cat: str, content: str, score: float = 0.8):
        return {
            "id": chunk_id, "category": cat,
            "section": "Section", "subsection": "Sous",
            "content": content, "score": score,
        }

    def test_liste_vide_retourne_chaine_vide(self, mem_db):
        from Mnemo.tools.memory_tools import _compress_chunks
        assert _compress_chunks([]) == ""

    def test_un_chunk_produit_une_ligne(self, mem_db):
        from Mnemo.tools.memory_tools import _compress_chunks
        chunks = [self._make_chunk("c1", "projet", "Projet Mnemo en développement")]
        result = _compress_chunks(chunks)
        assert result.count("\n") == 0  # une seule ligne
        assert "[projet]" in result

    def test_plusieurs_chunks_plusieurs_lignes(self, mem_db):
        from Mnemo.tools.memory_tools import _compress_chunks
        chunks = [
            self._make_chunk("c1", "projet", "Projet Alpha", score=0.9),
            self._make_chunk("c2", "décision", "Utiliser SQLite", score=0.7),
        ]
        result = _compress_chunks(chunks)
        lines = result.strip().split("\n")
        assert len(lines) == 2

    def test_trie_par_score_descendant(self, mem_db):
        from Mnemo.tools.memory_tools import _compress_chunks
        chunks = [
            self._make_chunk("c1", "connaissance", "Score bas", score=0.3),
            self._make_chunk("c2", "projet", "Score haut", score=0.9),
        ]
        result = _compress_chunks(chunks)
        lines = result.strip().split("\n")
        assert "Score haut" in lines[0]
        assert "Score bas" in lines[1]

    def test_respecte_budget_tokens(self, mem_db):
        from Mnemo.tools.memory_tools import _compress_chunks
        # 50 chunks × ~150 chars = ~7500 chars >> budget 600 tokens × 4 = 2400 chars
        chunks = [
            self._make_chunk(f"c{i}", "connaissance", "x" * 120, score=1.0 - i * 0.01)
            for i in range(50)
        ]
        result = _compress_chunks(chunks, max_tokens=600)
        assert len(result) <= 600 * 4 + 10  # tolérance pour les sauts de ligne

    def test_contenu_tronque_a_120_chars(self, mem_db):
        from Mnemo.tools.memory_tools import _compress_chunks
        long_content = "A" * 300
        chunks = [self._make_chunk("c1", "connaissance", long_content)]
        result = _compress_chunks(chunks)
        # Le contenu tronqué ne doit pas dépasser 120 chars dans la ligne
        line = result.strip()
        content_part = line.split(":", 1)[-1].strip()
        assert len(content_part) <= 120

    def test_contient_categorie_et_localisation(self, mem_db):
        from Mnemo.tools.memory_tools import _compress_chunks
        chunks = [self._make_chunk("c1", "identité", "Alice est développeuse")]
        result = _compress_chunks(chunks)
        assert "[identité]" in result
        assert "Section" in result


# ══════════════════════════════════════════════════════════════
# _record_retrieved_chunks
# ══════════════════════════════════════════════════════════════

class TestRecordRetrievedChunks:

    def _insert_chunk(self, db_path, chunk_id):
        db = sqlite3.connect(str(db_path))
        db.execute(
            "INSERT OR IGNORE INTO chunks (id, section, content, category) VALUES (?,?,?,?)",
            (chunk_id, "Test", "contenu", "connaissance"),
        )
        db.commit()
        db.close()

    def test_insere_lignes_dans_chunk_usage(self, mem_db):
        from Mnemo.tools.memory_tools import _record_retrieved_chunks
        self._insert_chunk(mem_db, "c1")
        self._insert_chunk(mem_db, "c2")
        _record_retrieved_chunks("sess_1", ["c1", "c2"], profile="briefing")
        db  = sqlite3.connect(str(mem_db))
        rows = db.execute("SELECT chunk_id, profile, confirmed FROM chunk_usage").fetchall()
        db.close()
        assert len(rows) == 2
        assert all(r[1] == "briefing" for r in rows)
        assert all(r[2] == 0 for r in rows)  # confirmed=0 (pas encore scoré)

    def test_profil_curiosity(self, mem_db):
        from Mnemo.tools.memory_tools import _record_retrieved_chunks
        self._insert_chunk(mem_db, "cx1")
        _record_retrieved_chunks("sess_curiosity", ["cx1"], profile="curiosity")
        db  = sqlite3.connect(str(mem_db))
        row = db.execute("SELECT profile FROM chunk_usage WHERE chunk_id='cx1'").fetchone()
        db.close()
        assert row[0] == "curiosity"

    def test_liste_vide_ninsere_rien(self, mem_db):
        from Mnemo.tools.memory_tools import _record_retrieved_chunks
        _record_retrieved_chunks("sess_1", [], profile="briefing")
        db   = sqlite3.connect(str(mem_db))
        count = db.execute("SELECT COUNT(*) FROM chunk_usage").fetchone()[0]
        db.close()
        assert count == 0

    def test_session_id_enregistre(self, mem_db):
        from Mnemo.tools.memory_tools import _record_retrieved_chunks
        self._insert_chunk(mem_db, "c1")
        _record_retrieved_chunks("ma_session_42", ["c1"], profile="briefing")
        db  = sqlite3.connect(str(mem_db))
        row = db.execute("SELECT session_id FROM chunk_usage WHERE chunk_id='c1'").fetchone()
        db.close()
        assert row[0] == "ma_session_42"

    def test_plusieurs_chunks_meme_session(self, mem_db):
        from Mnemo.tools.memory_tools import _record_retrieved_chunks
        for i in range(4):
            self._insert_chunk(mem_db, f"c{i}")
        _record_retrieved_chunks("sess_multi", [f"c{i}" for i in range(4)], profile="briefing")
        db    = sqlite3.connect(str(mem_db))
        count = db.execute(
            "SELECT COUNT(*) FROM chunk_usage WHERE session_id='sess_multi'"
        ).fetchone()[0]
        db.close()
        assert count == 4


# ══════════════════════════════════════════════════════════════
# Intégration curiosity_session — injection hybride
# ══════════════════════════════════════════════════════════════

class TestCuriositySessionInjection:

    @pytest.fixture
    def curiosity_env(self, mem_db, tmp_path, monkeypatch):
        """Environnement complet pour tester curiosity_session."""
        monkeypatch.setattr("Mnemo.main._markdown_path", lambda: tmp_path / "memory.md")
        monkeypatch.setattr("Mnemo.main.get_db", lambda: sqlite3.connect(str(mem_db)))
        (tmp_path / "memory.md").write_text(SAMPLE_MEMORY_MD, encoding="utf-8")
        return tmp_path

    def test_overview_injecte_dans_kickoff(self, curiosity_env, mem_db, monkeypatch):
        """memory_overview doit être présent dans les inputs du kickoff CuriosityCrew."""
        monkeypatch.setattr("Mnemo.tools.memory_tools.get_data_dir", lambda: curiosity_env)

        captured = {}

        def fake_kickoff(inputs):
            captured.update(inputs)
            result = MagicMock()
            result.raw = '{"memory_completeness": 0.5, "blocking_gaps": [], "enriching_gaps": []}'
            return result

        with patch("Mnemo.main.CuriosityCrew") as MockCrew, \
             patch("Mnemo.tools.memory_tools.retrieve_all", return_value=[]), \
             patch("Mnemo.tools.memory_tools.embed", return_value=__import__("numpy").zeros(768)), \
             patch("Mnemo.main._collect_answers", return_value={}):
            mock_instance = MockCrew.return_value
            mock_instance.crew.return_value.kickoff.side_effect = fake_kickoff

            from Mnemo.main import curiosity_session
            curiosity_session("Résumé de session de test.", session_id="test_sess")

        assert "memory_overview" in captured
        assert "memory_recent" in captured
        assert "memory_content" not in captured  # l'ancien champ ne doit plus apparaître

    def test_memory_content_absent_des_inputs(self, curiosity_env, monkeypatch):
        """L'ancien champ memory_content ne doit plus être transmis au LLM."""
        monkeypatch.setattr("Mnemo.tools.memory_tools.get_data_dir", lambda: curiosity_env)

        captured = {}

        def fake_kickoff(inputs):
            captured.update(inputs)
            result = MagicMock()
            result.raw = '{"memory_completeness": 0.5, "blocking_gaps": [], "enriching_gaps": []}'
            return result

        with patch("Mnemo.main.CuriosityCrew") as MockCrew, \
             patch("Mnemo.tools.memory_tools.retrieve_all", return_value=[]), \
             patch("Mnemo.tools.memory_tools.embed", return_value=__import__("numpy").zeros(768)), \
             patch("Mnemo.main._collect_answers", return_value={}):
            mock_instance = MockCrew.return_value
            mock_instance.crew.return_value.kickoff.side_effect = fake_kickoff

            from Mnemo.main import curiosity_session
            curiosity_session("Test session.", session_id="test_sess_2")

        assert "memory_content" not in captured

    def test_session_id_transmis_a_record(self, curiosity_env, monkeypatch):
        """_record_retrieved_chunks doit recevoir le session_id."""
        monkeypatch.setattr("Mnemo.tools.memory_tools.get_data_dir", lambda: curiosity_env)

        chunk_stub = [{"id": "c1", "score": 0.8, "category": "projet",
                       "section": "S", "subsection": "SS", "content": "Projet X"}]
        recorded = []

        def fake_record(session_id, chunk_ids, profile):
            recorded.append((session_id, chunk_ids, profile))

        with patch("Mnemo.main.CuriosityCrew") as MockCrew, \
             patch("Mnemo.tools.memory_tools.retrieve_all", return_value=chunk_stub), \
             patch("Mnemo.tools.memory_tools._record_retrieved_chunks", fake_record), \
             patch("Mnemo.tools.memory_tools.embed", return_value=__import__("numpy").zeros(768)), \
             patch("Mnemo.main._collect_answers", return_value={}):
            mock_instance = MockCrew.return_value
            mock_instance.crew.return_value.kickoff.return_value = MagicMock(
                raw='{"memory_completeness": 0.5, "blocking_gaps": [], "enriching_gaps": []}'
            )

            from Mnemo.main import curiosity_session
            curiosity_session("Résumé test.", session_id="ma_session_xyz")

        assert any(r[0] == "ma_session_xyz" and r[2] == "curiosity" for r in recorded)

    def test_fallback_si_retrieve_echoue(self, curiosity_env, monkeypatch):
        """Si retrieve_all lève une exception, le kickoff doit quand même se faire."""
        monkeypatch.setattr("Mnemo.tools.memory_tools.get_data_dir", lambda: curiosity_env)

        called = []

        def fake_kickoff(inputs):
            called.append(inputs)
            result = MagicMock()
            result.raw = '{"memory_completeness": 0.5, "blocking_gaps": [], "enriching_gaps": []}'
            return result

        with patch("Mnemo.main.CuriosityCrew") as MockCrew, \
             patch("Mnemo.tools.memory_tools.retrieve_all", side_effect=RuntimeError("Ollama indisponible")), \
             patch("Mnemo.main._collect_answers", return_value={}):
            mock_instance = MockCrew.return_value
            mock_instance.crew.return_value.kickoff.side_effect = fake_kickoff

            from Mnemo.main import curiosity_session
            curiosity_session("Résumé test.", session_id="sess_fallback")

        # Le kickoff doit avoir été appelé malgré l'erreur retrieve
        assert len(called) == 1
        # Le fallback doit utiliser memory_overview (overview depuis fichier ou contenu[:1500])
        assert "memory_overview" in called[0]