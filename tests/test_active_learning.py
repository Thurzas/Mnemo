"""
Tests unitaires Phase 5.4 — Active learning sur les poids

Couvre :
  - compute_category_stats : agrégation chunk_usage par catégorie
  - suggest_weight_adjustments : nudge, clamp, catégories sous-représentées
  - _load_learned_weights : chargement JSON, fallback silencieux
  - adapt_weights_if_ready : seuil MIN_SESSIONS, écriture fichier
  - reciprocal_rank_fusion : poids appris pris en compte

Aucun LLM requis.

Lance avec :
    pytest tests/test_active_learning.py -v
"""
import json
import sqlite3
import pytest

from pathlib import Path
from unittest.mock import patch


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


def _insert_usage(db_path: Path, category: str, n_retrieved: int, n_confirmed: int,
                  session_prefix: str = "sess"):
    """Insère n_retrieved lignes dans chunk_usage (n_confirmed avec confirmed=1)."""
    db = sqlite3.connect(str(db_path))
    # chunk fictif
    chunk_id = f"chunk_{category}_{session_prefix}"
    db.execute(
        "INSERT OR IGNORE INTO chunks (id, section, content, category) VALUES (?,?,?,?)",
        (chunk_id, "Test", "contenu", category),
    )
    for i in range(n_retrieved):
        confirmed = 1 if i < n_confirmed else 0
        db.execute(
            "INSERT INTO chunk_usage (chunk_id, session_id, used_score, confirmed)"
            " VALUES (?, ?, ?, ?)",
            (chunk_id, f"{session_prefix}_{i}", 0.8 if confirmed else 0.3, confirmed),
        )
    db.commit()
    db.close()


# ══════════════════════════════════════════════════════════════
# compute_category_stats
# ══════════════════════════════════════════════════════════════

class TestComputeCategoryStats:

    def test_db_vide_retourne_dict_vide(self, mem_db):
        from Mnemo.tools.memory_tools import compute_category_stats
        db    = sqlite3.connect(str(mem_db))
        stats = compute_category_stats(db)
        db.close()
        assert stats == {}

    def test_une_categorie(self, mem_db):
        from Mnemo.tools.memory_tools import compute_category_stats
        _insert_usage(mem_db, "identité", n_retrieved=20, n_confirmed=16)
        db    = sqlite3.connect(str(mem_db))
        stats = compute_category_stats(db)
        db.close()
        assert "identité" in stats
        assert stats["identité"]["retrieved"] == 20
        assert stats["identité"]["confirmed"] == 16
        assert abs(stats["identité"]["utility"] - 0.8) < 0.01

    def test_plusieurs_categories(self, mem_db):
        from Mnemo.tools.memory_tools import compute_category_stats
        _insert_usage(mem_db, "projet",             n_retrieved=30, n_confirmed=24)
        _insert_usage(mem_db, "historique_session", n_retrieved=20, n_confirmed=4)
        db    = sqlite3.connect(str(mem_db))
        stats = compute_category_stats(db)
        db.close()
        assert abs(stats["projet"]["utility"] - 0.8)  < 0.01
        assert abs(stats["historique_session"]["utility"] - 0.2) < 0.01

    def test_aucun_confirmed_utility_zero(self, mem_db):
        from Mnemo.tools.memory_tools import compute_category_stats
        _insert_usage(mem_db, "connaissance", n_retrieved=15, n_confirmed=0)
        db    = sqlite3.connect(str(mem_db))
        stats = compute_category_stats(db)
        db.close()
        assert stats["connaissance"]["utility"] == 0.0


# ══════════════════════════════════════════════════════════════
# suggest_weight_adjustments
# ══════════════════════════════════════════════════════════════

class TestSuggestWeightAdjustments:

    def test_categorie_sous_representee_inchangee(self):
        from Mnemo.tools.memory_tools import suggest_weight_adjustments, MIN_RETRIEVED
        stats = {"identité": {"retrieved": MIN_RETRIEVED - 1, "confirmed": 8, "utility": 0.8}}
        base  = {"identité": 1.5}
        result = suggest_weight_adjustments(stats, base)
        assert result["identité"] == 1.5   # inchangé

    def test_utilite_superieure_baseline_augmente_poids(self):
        from Mnemo.tools.memory_tools import suggest_weight_adjustments
        # identité très utile, historique peu utile → baseline = 0.5
        stats = {
            "identité":           {"retrieved": 20, "confirmed": 18, "utility": 0.9},
            "historique_session": {"retrieved": 20, "confirmed":  2, "utility": 0.1},
        }
        base = {"identité": 1.5, "historique_session": 0.7}
        result = suggest_weight_adjustments(stats, base)
        assert result["identité"] > 1.5
        assert result["historique_session"] < 0.7

    def test_clamp_minimum_respecte(self):
        from Mnemo.tools.memory_tools import suggest_weight_adjustments, WEIGHT_MIN
        # utilité = 0 → pousse vers le bas sans limite
        stats = {"connaissance": {"retrieved": 50, "confirmed": 0, "utility": 0.0}}
        base  = {"connaissance": 1.0}
        result = suggest_weight_adjustments(stats, base)
        assert result["connaissance"] >= WEIGHT_MIN

    def test_clamp_maximum_respecte(self):
        from Mnemo.tools.memory_tools import suggest_weight_adjustments, WEIGHT_MAX
        # identité seule, utilité = 1.0, baseline = 1.0, gap = 1.0 → poids inchangé
        # Forçons un gap élevé : identité utility=1.0, autre utility=0.0
        stats = {
            "identité":   {"retrieved": 20, "confirmed": 20, "utility": 1.0},
            "connaissance": {"retrieved": 20, "confirmed": 0,  "utility": 0.0},
        }
        # Poids de départ élevé pour tenter de dépasser WEIGHT_MAX
        base = {"identité": 2.8, "connaissance": 1.0}
        result = suggest_weight_adjustments(stats, base)
        assert result["identité"] <= WEIGHT_MAX

    def test_stats_vides_retourne_poids_inchanges(self):
        from Mnemo.tools.memory_tools import suggest_weight_adjustments
        from Mnemo.tools.memory_tools import CATEGORY_WEIGHTS
        result = suggest_weight_adjustments({}, CATEGORY_WEIGHTS)
        assert result == CATEGORY_WEIGHTS

    def test_toutes_utilites_egales_poids_quasi_inchanges(self):
        from Mnemo.tools.memory_tools import suggest_weight_adjustments
        # gap = 1.0 pour toutes → adjustment ≈ 0
        stats = {
            "identité": {"retrieved": 20, "confirmed": 10, "utility": 0.5},
            "projet":   {"retrieved": 20, "confirmed": 10, "utility": 0.5},
        }
        base = {"identité": 1.5, "projet": 1.2}
        result = suggest_weight_adjustments(stats, base)
        assert abs(result["identité"] - 1.5) < 0.01
        assert abs(result["projet"]   - 1.2) < 0.01


# ══════════════════════════════════════════════════════════════
# _load_learned_weights
# ══════════════════════════════════════════════════════════════

class TestLoadLearnedWeights:

    def test_fichier_absent_retourne_dict_vide(self, tmp_path, monkeypatch):
        monkeypatch.setattr("Mnemo.tools.memory_tools.get_data_dir", lambda: tmp_path)
        from Mnemo.tools.memory_tools import _load_learned_weights
        assert _load_learned_weights() == {}

    def test_fichier_valide_retourne_poids(self, tmp_path, monkeypatch):
        monkeypatch.setattr("Mnemo.tools.memory_tools.get_data_dir", lambda: tmp_path)
        (tmp_path / "learned_weights.json").write_text(json.dumps({
            "updated_at": "2026-03-17T12:00:00",
            "sessions_analyzed": 25,
            "weights": {"identité": 1.63, "projet": 1.28},
        }), encoding="utf-8")
        from Mnemo.tools.memory_tools import _load_learned_weights
        result = _load_learned_weights()
        assert result["identité"] == 1.63
        assert result["projet"]   == 1.28

    def test_fichier_corrompu_retourne_dict_vide(self, tmp_path, monkeypatch):
        monkeypatch.setattr("Mnemo.tools.memory_tools.get_data_dir", lambda: tmp_path)
        (tmp_path / "learned_weights.json").write_text("pas du json{{{", encoding="utf-8")
        from Mnemo.tools.memory_tools import _load_learned_weights
        assert _load_learned_weights() == {}

    def test_fichier_sans_weights_retourne_dict_vide(self, tmp_path, monkeypatch):
        monkeypatch.setattr("Mnemo.tools.memory_tools.get_data_dir", lambda: tmp_path)
        (tmp_path / "learned_weights.json").write_text(
            json.dumps({"updated_at": "2026-03-17"}), encoding="utf-8"
        )
        from Mnemo.tools.memory_tools import _load_learned_weights
        assert _load_learned_weights() == {}


# ══════════════════════════════════════════════════════════════
# adapt_weights_if_ready
# ══════════════════════════════════════════════════════════════

class TestAdaptWeightsIfReady:

    def test_pas_assez_de_sessions_retourne_false(self, mem_db):
        from Mnemo.tools.memory_tools import adapt_weights_if_ready, MIN_SESSIONS
        # Insère MIN_SESSIONS - 1 sessions distinctes
        _insert_usage(mem_db, "identité", n_retrieved=MIN_SESSIONS - 1,
                      n_confirmed=10, session_prefix="s")
        result = adapt_weights_if_ready()
        assert result is False

    def test_suffisamment_de_sessions_retourne_true(self, mem_db, tmp_path):
        from Mnemo.tools.memory_tools import adapt_weights_if_ready, MIN_SESSIONS
        # Insère MIN_SESSIONS sessions distinctes avec données variées
        _insert_usage(mem_db, "identité",           n_retrieved=MIN_SESSIONS,
                      n_confirmed=16, session_prefix="s")
        _insert_usage(mem_db, "historique_session", n_retrieved=MIN_SESSIONS,
                      n_confirmed=4, session_prefix="h")
        result = adapt_weights_if_ready()
        assert result is True

    def test_fichier_cree_si_adaptation(self, mem_db, tmp_path):
        from Mnemo.tools.memory_tools import adapt_weights_if_ready, MIN_SESSIONS
        _insert_usage(mem_db, "identité",           n_retrieved=MIN_SESSIONS,
                      n_confirmed=16, session_prefix="s")
        _insert_usage(mem_db, "historique_session", n_retrieved=MIN_SESSIONS,
                      n_confirmed=4, session_prefix="h")
        adapt_weights_if_ready()
        assert (tmp_path / "learned_weights.json").exists()

    def test_fichier_contient_champs_attendus(self, mem_db, tmp_path):
        from Mnemo.tools.memory_tools import adapt_weights_if_ready, MIN_SESSIONS
        _insert_usage(mem_db, "identité",           n_retrieved=MIN_SESSIONS,
                      n_confirmed=16, session_prefix="s")
        _insert_usage(mem_db, "historique_session", n_retrieved=MIN_SESSIONS,
                      n_confirmed=4, session_prefix="h")
        adapt_weights_if_ready()
        data = json.loads((tmp_path / "learned_weights.json").read_text())
        assert "updated_at"        in data
        assert "sessions_analyzed" in data
        assert "weights"           in data
        assert data["sessions_analyzed"] >= MIN_SESSIONS

    def test_fichier_non_cree_si_pas_assez_de_sessions(self, mem_db, tmp_path):
        from Mnemo.tools.memory_tools import adapt_weights_if_ready, MIN_SESSIONS
        _insert_usage(mem_db, "identité", n_retrieved=5, n_confirmed=3, session_prefix="s")
        adapt_weights_if_ready()
        assert not (tmp_path / "learned_weights.json").exists()


# ══════════════════════════════════════════════════════════════
# Intégration : poids appris dans reciprocal_rank_fusion
# ══════════════════════════════════════════════════════════════

class TestLearnedWeightsInRetrieval:

    def test_poids_appris_surcharge_statiques(self, tmp_path, monkeypatch):
        """Un chunk avec poids appris > statique doit avoir un score_final plus élevé."""
        import numpy as np
        from Mnemo.tools.memory_tools import reciprocal_rank_fusion

        monkeypatch.setattr("Mnemo.tools.memory_tools.get_data_dir", lambda: tmp_path)

        chunk_a = {"id": "a", "category": "identité",   "content": "x",
                   "importance_weight": None, "updated_at": None}
        chunk_b = {"id": "b", "category": "connaissance", "content": "y",
                   "importance_weight": None, "updated_at": None}

        # Sans learned_weights : identité (1.5) > connaissance (1.0) → a > b
        result_base = reciprocal_rank_fusion([chunk_a], [chunk_b], query="test")
        scores_base = {r["id"]: r["score_importance"] for r in result_base}
        assert scores_base["a"] > scores_base["b"]

        # Avec learned_weights qui inverse : connaissance = 2.0 > identité = 0.5
        (tmp_path / "learned_weights.json").write_text(json.dumps({
            "weights": {"identité": 0.5, "connaissance": 2.0}
        }), encoding="utf-8")

        result_learned = reciprocal_rank_fusion([chunk_a], [chunk_b], query="test")
        scores_learned = {r["id"]: r["score_importance"] for r in result_learned}
        assert scores_learned["b"] > scores_learned["a"]

    def test_profil_override_prime_sur_learned(self, tmp_path, monkeypatch):
        """Le profil override doit toujours primer sur les poids appris."""
        from Mnemo.tools.memory_tools import reciprocal_rank_fusion, PROFILES

        monkeypatch.setattr("Mnemo.tools.memory_tools.get_data_dir", lambda: tmp_path)

        # learned_weights écrase identité à 0.5
        (tmp_path / "learned_weights.json").write_text(json.dumps({
            "weights": {"identité": 0.5}
        }), encoding="utf-8")

        # Profil curiosity booste identité à 2.5 × base
        chunk = {"id": "a", "category": "identité", "content": "x",
                 "importance_weight": None, "updated_at": None}

        result_conv    = reciprocal_rank_fusion([chunk], [], query="test",
                                                profile=PROFILES["conversation"])
        result_curious = reciprocal_rank_fusion([chunk], [], query="test",
                                                profile=PROFILES["curiosity"])

        # curiosity override (2.5) > learned (0.5) → curiosity doit donner un score plus élevé
        assert result_curious[0]["score_importance"] > result_conv[0]["score_importance"]