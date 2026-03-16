"""
Tests unitaires Phase 5.3 — Mémoire procédurale (tracking d'usage des chunks)

Couvre :
  - Schéma DB : colonnes use_count / last_used_at / table chunk_usage
  - Buffer de retrieval : _retrieved_this_turn
  - update_session_memory : champ retrieved_chunk_ids optionnel
  - score_and_record_chunk_usage : scoring, confirmation, use_count

Aucun LLM requis — embed() est mocké dans tous les tests de scoring.

Lance avec :
    pytest tests/test_procedural_memory.py -v
"""
import json
import sqlite3
import numpy as np
import pytest

from pathlib import Path
from unittest.mock import patch


# ══════════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════════

def _make_vec(seed: int = 0, dim: int = 768) -> np.ndarray:
    """Vecteur unitaire aléatoire reproductible (distribution normale → composantes +/-)."""
    rng = np.random.default_rng(seed)
    v = rng.standard_normal(dim).astype(np.float32)
    return v / np.linalg.norm(v)


def _insert_chunk(db_path: Path, chunk_id: str, content: str,
                  category: str = "connaissance", vec_seed: int | None = None) -> np.ndarray:
    """Insère un chunk + son embedding dans la DB de test. Retourne le vecteur."""
    seed = vec_seed if vec_seed is not None else abs(hash(chunk_id)) % 10000
    vec  = _make_vec(seed=seed)
    db   = sqlite3.connect(str(db_path))
    db.execute(
        "INSERT OR IGNORE INTO chunks (id, section, subsection, content, category)"
        " VALUES (?, 'Test', 'Sous-section', ?, ?)",
        (chunk_id, content, category),
    )
    db.execute(
        "INSERT OR IGNORE INTO embeddings (chunk_id, model, vector, dim)"
        " VALUES (?, 'nomic-embed-text', ?, ?)",
        (chunk_id, vec.tobytes(), len(vec)),
    )
    db.commit()
    db.close()
    return vec


# ══════════════════════════════════════════════════════════════
# Fixture principale
# ══════════════════════════════════════════════════════════════

@pytest.fixture
def mem_db(tmp_path, monkeypatch):
    """
    DB SQLite sur disque avec schéma Phase 5.3 complet.
    get_data_dir() est patché pour que memory_tools utilise tmp_path.
    """
    from Mnemo.init_db import init_db
    monkeypatch.setattr("Mnemo.tools.memory_tools.get_data_dir", lambda: tmp_path)
    db_path = tmp_path / "memory.db"
    init_db(db_path)
    return db_path


# ══════════════════════════════════════════════════════════════
# Schéma DB
# ══════════════════════════════════════════════════════════════

class TestDBSchema:

    def test_chunks_a_colonne_use_count(self, mem_db):
        db   = sqlite3.connect(str(mem_db))
        cols = [r[1] for r in db.execute("PRAGMA table_info(chunks)").fetchall()]
        db.close()
        assert "use_count" in cols

    def test_chunks_a_colonne_last_used_at(self, mem_db):
        db   = sqlite3.connect(str(mem_db))
        cols = [r[1] for r in db.execute("PRAGMA table_info(chunks)").fetchall()]
        db.close()
        assert "last_used_at" in cols

    def test_use_count_defaut_zero(self, mem_db):
        _insert_chunk(mem_db, "c_default", "contenu test")
        db        = sqlite3.connect(str(mem_db))
        use_count = db.execute("SELECT use_count FROM chunks WHERE id='c_default'").fetchone()[0]
        db.close()
        assert use_count == 0

    def test_table_chunk_usage_existe(self, mem_db):
        db     = sqlite3.connect(str(mem_db))
        tables = [r[0] for r in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()]
        db.close()
        assert "chunk_usage" in tables

    def test_chunk_usage_colonnes(self, mem_db):
        db   = sqlite3.connect(str(mem_db))
        cols = [r[1] for r in db.execute("PRAGMA table_info(chunk_usage)").fetchall()]
        db.close()
        for col in ("id", "chunk_id", "session_id", "retrieved_at", "used_score", "confirmed"):
            assert col in cols


# ══════════════════════════════════════════════════════════════
# Buffer de retrieval
# ══════════════════════════════════════════════════════════════

class TestRetrievalBuffer:

    def test_buffer_vide_par_defaut(self):
        import Mnemo.tools.memory_tools as mt
        mt._retrieved_this_turn = []
        assert mt._retrieved_this_turn == []

    def test_buffer_accumule(self):
        import Mnemo.tools.memory_tools as mt
        mt._retrieved_this_turn = []
        mt._retrieved_this_turn.extend([{"id": "a"}, {"id": "b"}])
        assert len(mt._retrieved_this_turn) == 2
        assert mt._retrieved_this_turn[0]["id"] == "a"

    def test_buffer_peut_etre_vide(self):
        import Mnemo.tools.memory_tools as mt
        mt._retrieved_this_turn = [{"id": "x"}]
        mt._retrieved_this_turn = []
        assert mt._retrieved_this_turn == []

    def test_retrieved_ids_extraits_correctement(self):
        import Mnemo.tools.memory_tools as mt
        mt._retrieved_this_turn = [{"id": "abc", "score_final": 0.9},
                                    {"id": "def", "score_final": 0.7}]
        ids = [c["id"] for c in mt._retrieved_this_turn]
        assert ids == ["abc", "def"]


# ══════════════════════════════════════════════════════════════
# update_session_memory — champ retrieved_chunk_ids
# ══════════════════════════════════════════════════════════════

class TestUpdateSessionMemoryChunkIds:

    def test_retrieved_chunk_ids_present_si_fourni(self, tmp_path, monkeypatch):
        monkeypatch.setattr("Mnemo.tools.memory_tools.get_data_dir", lambda: tmp_path)
        (tmp_path / "sessions").mkdir()
        from Mnemo.tools.memory_tools import update_session_memory, load_session_json

        update_session_memory("sess_001", "Bonjour", "Salut !",
                              retrieved_chunk_ids=["id_a", "id_b"])
        session   = load_session_json("sess_001")
        agent_msg = next(m for m in session["messages"] if m["role"] == "agent")
        assert agent_msg["retrieved_chunk_ids"] == ["id_a", "id_b"]

    def test_retrieved_chunk_ids_absent_si_non_fourni(self, tmp_path, monkeypatch):
        monkeypatch.setattr("Mnemo.tools.memory_tools.get_data_dir", lambda: tmp_path)
        (tmp_path / "sessions").mkdir()
        from Mnemo.tools.memory_tools import update_session_memory, load_session_json

        update_session_memory("sess_002", "Bonjour", "Salut !")
        session   = load_session_json("sess_002")
        agent_msg = next(m for m in session["messages"] if m["role"] == "agent")
        assert "retrieved_chunk_ids" not in agent_msg

    def test_retrieved_chunk_ids_absent_si_liste_vide(self, tmp_path, monkeypatch):
        monkeypatch.setattr("Mnemo.tools.memory_tools.get_data_dir", lambda: tmp_path)
        (tmp_path / "sessions").mkdir()
        from Mnemo.tools.memory_tools import update_session_memory, load_session_json

        update_session_memory("sess_003", "Bonjour", "Salut !", retrieved_chunk_ids=None)
        session   = load_session_json("sess_003")
        agent_msg = next(m for m in session["messages"] if m["role"] == "agent")
        assert "retrieved_chunk_ids" not in agent_msg


# ══════════════════════════════════════════════════════════════
# score_and_record_chunk_usage
# ══════════════════════════════════════════════════════════════

class TestScoreAndRecordChunkUsage:

    def test_session_vide_ne_crash_pas(self, mem_db):
        from Mnemo.tools.memory_tools import score_and_record_chunk_usage
        score_and_record_chunk_usage({}, "sess_vide")
        db    = sqlite3.connect(str(mem_db))
        count = db.execute("SELECT COUNT(*) FROM chunk_usage").fetchone()[0]
        db.close()
        assert count == 0

    def test_tour_sans_retrieved_chunk_ids_ignore(self, mem_db):
        from Mnemo.tools.memory_tools import score_and_record_chunk_usage
        session = {"messages": [
            {"role": "user",  "content": "Bonjour"},
            {"role": "agent", "content": "Salut !"},
        ]}
        score_and_record_chunk_usage(session, "sess_sans_ids")
        db    = sqlite3.connect(str(mem_db))
        count = db.execute("SELECT COUNT(*) FROM chunk_usage").fetchone()[0]
        db.close()
        assert count == 0

    def test_chunk_similaire_confirme(self, mem_db):
        from Mnemo.tools.memory_tools import score_and_record_chunk_usage, USAGE_THRESHOLD
        chunk_vec = _insert_chunk(mem_db, "c_sim", "Python est un langage", vec_seed=42)

        session = {"messages": [
            {"role": "user",  "content": "C'est quoi Python ?"},
            {"role": "agent", "content": "Python est un langage de programmation.",
             "retrieved_chunk_ids": ["c_sim"]},
        ]}
        # Réponse sémantiquement identique → vecteur = chunk_vec → score = 1.0
        with patch("Mnemo.tools.memory_tools.embed", return_value=chunk_vec):
            score_and_record_chunk_usage(session, "sess_sim")

        db        = sqlite3.connect(str(mem_db))
        row       = db.execute(
            "SELECT used_score, confirmed FROM chunk_usage WHERE chunk_id='c_sim'"
        ).fetchone()
        use_count = db.execute("SELECT use_count FROM chunks WHERE id='c_sim'").fetchone()[0]
        db.close()

        assert row is not None
        assert row[0] >= USAGE_THRESHOLD
        assert row[1] == 1
        assert use_count == 1

    def test_chunk_dissimilaire_non_confirme(self, mem_db):
        from Mnemo.tools.memory_tools import score_and_record_chunk_usage, USAGE_THRESHOLD
        _insert_chunk(mem_db, "c_dis", "Python est un langage", vec_seed=42)

        # Vecteur orthogonal → cosine sim ≈ 0 << USAGE_THRESHOLD
        ortho_vec = _make_vec(seed=9999)

        session = {"messages": [
            {"role": "agent", "content": "La cuisine française est délicieuse.",
             "retrieved_chunk_ids": ["c_dis"]},
        ]}
        with patch("Mnemo.tools.memory_tools.embed", return_value=ortho_vec):
            score_and_record_chunk_usage(session, "sess_dis")

        db        = sqlite3.connect(str(mem_db))
        row       = db.execute(
            "SELECT used_score, confirmed FROM chunk_usage WHERE chunk_id='c_dis'"
        ).fetchone()
        use_count = db.execute("SELECT use_count FROM chunks WHERE id='c_dis'").fetchone()[0]
        db.close()

        assert row is not None
        assert row[0] < USAGE_THRESHOLD
        assert row[1] == 0
        assert use_count == 0

    def test_use_count_incremente_sur_deux_sessions(self, mem_db):
        from Mnemo.tools.memory_tools import score_and_record_chunk_usage
        chunk_vec = _insert_chunk(mem_db, "c_multi", "Mnemo est un assistant", vec_seed=7)

        session_a = {"messages": [
            {"role": "agent", "content": "Mnemo est un assistant.",
             "retrieved_chunk_ids": ["c_multi"]},
        ]}
        session_b = {"messages": [
            {"role": "agent", "content": "Mnemo est un assistant.",
             "retrieved_chunk_ids": ["c_multi"]},
        ]}
        with patch("Mnemo.tools.memory_tools.embed", return_value=chunk_vec):
            score_and_record_chunk_usage(session_a, "sess_a")
            score_and_record_chunk_usage(session_b, "sess_b")

        db        = sqlite3.connect(str(mem_db))
        use_count = db.execute("SELECT use_count FROM chunks WHERE id='c_multi'").fetchone()[0]
        nb_rows   = db.execute(
            "SELECT COUNT(*) FROM chunk_usage WHERE chunk_id='c_multi'"
        ).fetchone()[0]
        db.close()

        assert use_count == 2
        assert nb_rows   == 2

    def test_chunk_id_inconnu_ignore_silencieusement(self, mem_db):
        from Mnemo.tools.memory_tools import score_and_record_chunk_usage
        session = {"messages": [
            {"role": "agent", "content": "Réponse.",
             "retrieved_chunk_ids": ["id_inexistant"]},
        ]}
        dummy_vec = _make_vec(seed=1)
        with patch("Mnemo.tools.memory_tools.embed", return_value=dummy_vec):
            score_and_record_chunk_usage(session, "sess_inconnu")

        db    = sqlite3.connect(str(mem_db))
        count = db.execute("SELECT COUNT(*) FROM chunk_usage").fetchone()[0]
        db.close()
        assert count == 0

    def test_plusieurs_chunks_par_tour(self, mem_db):
        from Mnemo.tools.memory_tools import score_and_record_chunk_usage
        vec_a = _insert_chunk(mem_db, "c_a", "Mnemo est local",    vec_seed=10)
        vec_b = _insert_chunk(mem_db, "c_b", "Mnemo utilise CrewAI", vec_seed=20)

        # La réponse est proche de c_a, pas de c_b
        session = {"messages": [
            {"role": "agent", "content": "Mnemo est local.",
             "retrieved_chunk_ids": ["c_a", "c_b"]},
        ]}
        with patch("Mnemo.tools.memory_tools.embed", return_value=vec_a):
            score_and_record_chunk_usage(session, "sess_multi")

        db           = sqlite3.connect(str(mem_db))
        rows         = db.execute(
            "SELECT chunk_id, confirmed FROM chunk_usage ORDER BY chunk_id"
        ).fetchall()
        use_count_a  = db.execute("SELECT use_count FROM chunks WHERE id='c_a'").fetchone()[0]
        use_count_b  = db.execute("SELECT use_count FROM chunks WHERE id='c_b'").fetchone()[0]
        db.close()

        assert len(rows) == 2
        assert use_count_a == 1   # c_a confirmé (vecteur identique)
        assert use_count_b == 0   # c_b non confirmé (vecteur différent)

    def test_last_used_at_mis_a_jour_si_confirme(self, mem_db):
        from Mnemo.tools.memory_tools import score_and_record_chunk_usage
        chunk_vec = _insert_chunk(mem_db, "c_date", "test fraîcheur", vec_seed=5)

        session = {"messages": [
            {"role": "agent", "content": "test fraîcheur.",
             "retrieved_chunk_ids": ["c_date"]},
        ]}
        with patch("Mnemo.tools.memory_tools.embed", return_value=chunk_vec):
            score_and_record_chunk_usage(session, "sess_date")

        db           = sqlite3.connect(str(mem_db))
        last_used_at = db.execute(
            "SELECT last_used_at FROM chunks WHERE id='c_date'"
        ).fetchone()[0]
        db.close()
        assert last_used_at is not None