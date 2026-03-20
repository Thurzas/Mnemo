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
                  session_prefix: str = "sess", profile: str = "conversation"):
    """Insère n_retrieved lignes dans chunk_usage (n_confirmed avec confirmed=1)."""
    db = sqlite3.connect(str(db_path))
    chunk_id = f"chunk_{category}_{session_prefix}"
    db.execute(
        "INSERT OR IGNORE INTO chunks (id, section, content, category) VALUES (?,?,?,?)",
        (chunk_id, "Test", "contenu", category),
    )
    for i in range(n_retrieved):
        confirmed = 1 if i < n_confirmed else 0
        db.execute(
            "INSERT INTO chunk_usage (chunk_id, session_id, used_score, confirmed, profile)"
            " VALUES (?, ?, ?, ?, ?)",
            (chunk_id, f"{session_prefix}_{i}", 0.8 if confirmed else 0.3, confirmed, profile),
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


# ══════════════════════════════════════════════════════════════
# Phase 5.5 — Données par profil
# ══════════════════════════════════════════════════════════════

class TestComputeCategoryStatsByProfile:

    def test_filtre_par_profil(self, mem_db):
        from Mnemo.tools.memory_tools import compute_category_stats
        import sqlite3
        _insert_usage(mem_db, "identité",    n_retrieved=15, n_confirmed=12, profile="conversation")
        _insert_usage(mem_db, "connaissance", n_retrieved=15, n_confirmed=3,  profile="briefing")
        db = sqlite3.connect(str(mem_db))
        stats_conv = compute_category_stats(db, profile="conversation")
        db.close()
        assert "identité" in stats_conv
        assert "connaissance" not in stats_conv

    def test_profile_none_retourne_tout(self, mem_db):
        from Mnemo.tools.memory_tools import compute_category_stats
        import sqlite3
        _insert_usage(mem_db, "identité",    n_retrieved=15, n_confirmed=10, profile="conversation")
        _insert_usage(mem_db, "connaissance", n_retrieved=15, n_confirmed=5,  profile="briefing")
        db = sqlite3.connect(str(mem_db))
        stats_all = compute_category_stats(db, profile=None)
        db.close()
        assert "identité"    in stats_all
        assert "connaissance" in stats_all

    def test_profil_inexistant_retourne_vide(self, mem_db):
        from Mnemo.tools.memory_tools import compute_category_stats
        import sqlite3
        _insert_usage(mem_db, "identité", n_retrieved=15, n_confirmed=10, profile="conversation")
        db = sqlite3.connect(str(mem_db))
        stats = compute_category_stats(db, profile="briefing")
        db.close()
        assert stats == {}


# ══════════════════════════════════════════════════════════════
# Phase 5.5 — Régression vers la moyenne (Axe B)
# ══════════════════════════════════════════════════════════════

class TestRegressionTowardMean:

    def test_regression_rapproche_du_poids_initial(self):
        from Mnemo.tools.memory_tools import suggest_weight_adjustments, REGRESSION_RATE
        # Catégorie fortement sur-représentée → raw ajusté à la hausse
        # La régression doit ramener vers CATEGORY_WEIGHTS["identité"] = 1.5
        stats = {
            "identité": {"retrieved": 20, "confirmed": 20, "utility": 1.0},
            "projet":   {"retrieved": 20, "confirmed": 4,  "utility": 0.2},
        }
        base = {"identité": 2.5, "projet": 0.5}  # 2.5*1.1=2.75 < WEIGHT_MAX, laisse de la marge
        result_no_reg = suggest_weight_adjustments(stats, base, regression_rate=0.0)
        result_reg    = suggest_weight_adjustments(stats, base, regression_rate=REGRESSION_RATE)
        # Avec régression : identité doit être plus proche de 1.5 (initial) que sans
        assert abs(result_reg["identité"] - 1.5) < abs(result_no_reg["identité"] - 1.5)

    def test_regression_zero_identique_sans_regression(self):
        from Mnemo.tools.memory_tools import suggest_weight_adjustments
        stats = {
            "identité": {"retrieved": 20, "confirmed": 15, "utility": 0.75},
            "projet":   {"retrieved": 20, "confirmed": 5,  "utility": 0.25},
        }
        base = {"identité": 1.5, "projet": 1.2}
        r0   = suggest_weight_adjustments(stats, base, regression_rate=0.0)
        r_no = suggest_weight_adjustments(stats, base)  # défaut = 0.0
        assert r0 == r_no

    def test_regression_ne_sort_pas_des_clamps(self):
        from Mnemo.tools.memory_tools import suggest_weight_adjustments, WEIGHT_MIN, WEIGHT_MAX, REGRESSION_RATE
        stats = {"connaissance": {"retrieved": 50, "confirmed": 0, "utility": 0.0}}
        base  = {"connaissance": 1.0}
        result = suggest_weight_adjustments(stats, base, regression_rate=REGRESSION_RATE)
        assert result["connaissance"] >= WEIGHT_MIN
        assert result["connaissance"] <= WEIGHT_MAX


# ══════════════════════════════════════════════════════════════
# Phase 5.5 — Persistance par profil (Axe A)
# ══════════════════════════════════════════════════════════════

class TestLoadLearnedWeightsByProfile:

    def test_global_charge_learned_weights_json(self, tmp_path, monkeypatch):
        monkeypatch.setattr("Mnemo.tools.memory_tools.get_data_dir", lambda: tmp_path)
        (tmp_path / "learned_weights.json").write_text(
            json.dumps({"weights": {"identité": 1.8}}), encoding="utf-8"
        )
        from Mnemo.tools.memory_tools import _load_learned_weights
        result = _load_learned_weights("global")
        assert result["identité"] == 1.8

    def test_profil_charge_fichier_specifique(self, tmp_path, monkeypatch):
        monkeypatch.setattr("Mnemo.tools.memory_tools.get_data_dir", lambda: tmp_path)
        (tmp_path / "learned_weights_briefing.json").write_text(
            json.dumps({"weights": {"projet": 2.1}}), encoding="utf-8"
        )
        from Mnemo.tools.memory_tools import _load_learned_weights
        result = _load_learned_weights("briefing")
        assert result["projet"] == 2.1

    def test_profil_absent_retourne_vide(self, tmp_path, monkeypatch):
        monkeypatch.setattr("Mnemo.tools.memory_tools.get_data_dir", lambda: tmp_path)
        from Mnemo.tools.memory_tools import _load_learned_weights
        assert _load_learned_weights("curiosity") == {}

    def test_profil_isole_du_global(self, tmp_path, monkeypatch):
        monkeypatch.setattr("Mnemo.tools.memory_tools.get_data_dir", lambda: tmp_path)
        (tmp_path / "learned_weights.json").write_text(
            json.dumps({"weights": {"identité": 0.5}}), encoding="utf-8"
        )
        from Mnemo.tools.memory_tools import _load_learned_weights
        # Le fichier profil-spécifique n'existe pas → retourne {}
        assert _load_learned_weights("briefing") == {}


class TestAdaptWeightsIfReadyByProfile:

    def test_profil_seuil_min_sessions_per_profile(self, mem_db):
        from Mnemo.tools.memory_tools import adapt_weights_if_ready, MIN_SESSIONS_PER_PROFILE
        _insert_usage(mem_db, "identité", n_retrieved=MIN_SESSIONS_PER_PROFILE - 1,
                      n_confirmed=5, session_prefix="b", profile="briefing")
        assert adapt_weights_if_ready("briefing") is False

    def test_profil_suffisant_retourne_true(self, mem_db, tmp_path):
        from Mnemo.tools.memory_tools import adapt_weights_if_ready, MIN_SESSIONS_PER_PROFILE
        _insert_usage(mem_db, "identité",    n_retrieved=MIN_SESSIONS_PER_PROFILE,
                      n_confirmed=8, session_prefix="b", profile="briefing")
        _insert_usage(mem_db, "connaissance", n_retrieved=MIN_SESSIONS_PER_PROFILE,
                      n_confirmed=2, session_prefix="b2", profile="briefing")
        assert adapt_weights_if_ready("briefing") is True

    def test_fichier_profil_cree(self, mem_db, tmp_path):
        from Mnemo.tools.memory_tools import adapt_weights_if_ready, MIN_SESSIONS_PER_PROFILE
        _insert_usage(mem_db, "identité",    n_retrieved=MIN_SESSIONS_PER_PROFILE,
                      n_confirmed=8, session_prefix="c", profile="curiosity")
        _insert_usage(mem_db, "connaissance", n_retrieved=MIN_SESSIONS_PER_PROFILE,
                      n_confirmed=2, session_prefix="c2", profile="curiosity")
        adapt_weights_if_ready("curiosity")
        assert (tmp_path / "learned_weights_curiosity.json").exists()

    def test_fichier_profil_isole_du_global(self, mem_db, tmp_path):
        """L'adaptation d'un profil ne doit pas créer learned_weights.json."""
        from Mnemo.tools.memory_tools import adapt_weights_if_ready, MIN_SESSIONS_PER_PROFILE
        _insert_usage(mem_db, "identité", n_retrieved=MIN_SESSIONS_PER_PROFILE,
                      n_confirmed=8, session_prefix="b3", profile="briefing")
        _insert_usage(mem_db, "projet",   n_retrieved=MIN_SESSIONS_PER_PROFILE,
                      n_confirmed=3, session_prefix="b4", profile="briefing")
        adapt_weights_if_ready("briefing")
        assert not (tmp_path / "learned_weights.json").exists()

    def test_fichier_contient_champ_profile(self, mem_db, tmp_path):
        from Mnemo.tools.memory_tools import adapt_weights_if_ready, MIN_SESSIONS_PER_PROFILE
        _insert_usage(mem_db, "identité", n_retrieved=MIN_SESSIONS_PER_PROFILE,
                      n_confirmed=8, session_prefix="b5", profile="briefing")
        _insert_usage(mem_db, "projet",   n_retrieved=MIN_SESSIONS_PER_PROFILE,
                      n_confirmed=3, session_prefix="b6", profile="briefing")
        adapt_weights_if_ready("briefing")
        data = json.loads((tmp_path / "learned_weights_briefing.json").read_text())
        assert data["profile"] == "briefing"


# ══════════════════════════════════════════════════════════════
# Phase 5.5 — Audit trail (Axe C)
# ══════════════════════════════════════════════════════════════

class TestAppendWeightsHistory:

    def test_fichier_cree_apres_adaptation(self, mem_db, tmp_path):
        from Mnemo.tools.memory_tools import adapt_weights_if_ready, MIN_SESSIONS
        _insert_usage(mem_db, "identité",    n_retrieved=MIN_SESSIONS, n_confirmed=15)
        _insert_usage(mem_db, "connaissance", n_retrieved=MIN_SESSIONS, n_confirmed=5, session_prefix="h")
        adapt_weights_if_ready("global")
        assert (tmp_path / "learned_weights_history.jsonl").exists()

    def test_ligne_jsonl_valide(self, mem_db, tmp_path):
        from Mnemo.tools.memory_tools import adapt_weights_if_ready, MIN_SESSIONS
        _insert_usage(mem_db, "identité",    n_retrieved=MIN_SESSIONS, n_confirmed=15)
        _insert_usage(mem_db, "connaissance", n_retrieved=MIN_SESSIONS, n_confirmed=5, session_prefix="h")
        adapt_weights_if_ready("global")
        lines = (tmp_path / "learned_weights_history.jsonl").read_text().strip().splitlines()
        assert len(lines) == 1
        entry = json.loads(lines[0])
        assert "timestamp"         in entry
        assert "profile"           in entry
        assert "sessions_analyzed" in entry
        assert "weights"           in entry
        assert "regression_applied" in entry

    def test_plusieurs_adaptations_plusieurs_lignes(self, mem_db, tmp_path):
        from Mnemo.tools.memory_tools import adapt_weights_if_ready, MIN_SESSIONS
        _insert_usage(mem_db, "identité",    n_retrieved=MIN_SESSIONS, n_confirmed=15)
        _insert_usage(mem_db, "connaissance", n_retrieved=MIN_SESSIONS, n_confirmed=5, session_prefix="h")
        adapt_weights_if_ready("global")
        adapt_weights_if_ready("global")
        lines = [l for l in (tmp_path / "learned_weights_history.jsonl").read_text().strip().splitlines() if l]
        assert len(lines) == 2

    def test_purge_entrees_anciennes(self, tmp_path, monkeypatch):
        from Mnemo.tools.memory_tools import _append_weights_history
        monkeypatch.setattr("Mnemo.tools.memory_tools.get_data_dir", lambda: tmp_path)
        from datetime import timedelta, datetime as dt
        old_ts  = (dt.now() - timedelta(days=100)).isoformat()
        new_ts  = dt.now().isoformat()
        # Écriture manuelle d'une entrée ancienne
        history = tmp_path / "learned_weights_history.jsonl"
        history.write_text(
            json.dumps({"timestamp": old_ts, "profile": "global",
                        "sessions_analyzed": 20, "weights": {}, "regression_applied": False}) + "\n",
            encoding="utf-8",
        )
        _append_weights_history("global", 25, {"identité": 1.5}, False, new_ts)
        lines = [l for l in history.read_text().strip().splitlines() if l]
        # L'entrée ancienne doit avoir été purgée
        assert len(lines) == 1
        assert json.loads(lines[0])["sessions_analyzed"] == 25

    def test_regression_applied_false_pour_global(self, mem_db, tmp_path):
        from Mnemo.tools.memory_tools import adapt_weights_if_ready, MIN_SESSIONS
        _insert_usage(mem_db, "identité",    n_retrieved=MIN_SESSIONS, n_confirmed=15)
        _insert_usage(mem_db, "connaissance", n_retrieved=MIN_SESSIONS, n_confirmed=5, session_prefix="h")
        adapt_weights_if_ready("global")
        entry = json.loads(
            (tmp_path / "learned_weights_history.jsonl").read_text().strip().splitlines()[0]
        )
        assert entry["regression_applied"] is False

    def test_regression_applied_true_pour_profil(self, mem_db, tmp_path):
        from Mnemo.tools.memory_tools import adapt_weights_if_ready, MIN_SESSIONS_PER_PROFILE
        _insert_usage(mem_db, "identité", n_retrieved=MIN_SESSIONS_PER_PROFILE,
                      n_confirmed=8, session_prefix="p", profile="briefing")
        _insert_usage(mem_db, "projet",   n_retrieved=MIN_SESSIONS_PER_PROFILE,
                      n_confirmed=3, session_prefix="p2", profile="briefing")
        adapt_weights_if_ready("briefing")
        entry = json.loads(
            (tmp_path / "learned_weights_history.jsonl").read_text().strip().splitlines()[0]
        )
        assert entry["regression_applied"] is True


# ══════════════════════════════════════════════════════════════
# Phase 5.5 — 4 niveaux de priorité dans reciprocal_rank_fusion
# ══════════════════════════════════════════════════════════════

class TestFourLevelWeightPriority:

    def _chunk(self, cid, category):
        return {"id": cid, "category": category, "content": "x",
                "importance_weight": None, "updated_at": None}

    def test_learned_profil_surcharge_learned_global(self, tmp_path, monkeypatch):
        """learned_weights_briefing.json > learned_weights.json pour le profil briefing."""
        from Mnemo.tools.memory_tools import reciprocal_rank_fusion, PROFILES
        monkeypatch.setattr("Mnemo.tools.memory_tools.get_data_dir", lambda: tmp_path)
        # Global : identité = 0.4 (réduit)
        (tmp_path / "learned_weights.json").write_text(
            json.dumps({"weights": {"identité": 0.4}}), encoding="utf-8"
        )
        # Profil briefing : identité = 2.0 (booste)
        (tmp_path / "learned_weights_briefing.json").write_text(
            json.dumps({"weights": {"identité": 2.0}}), encoding="utf-8"
        )
        chunk = self._chunk("a", "identité")
        result = reciprocal_rank_fusion([chunk], [], query="test", profile=PROFILES["briefing"])
        # Le poids effectif doit être dominé par le fichier profil (2.0), pas le global (0.4)
        # Mais le profil override de briefing n'a pas "identité" → poids effectif = 2.0
        # Sans fichiers : identité = 1.5 (statique)
        result_no_files = reciprocal_rank_fusion(
            [self._chunk("b", "identité")], [], query="test", profile=PROFILES["conversation"]
        )
        assert result[0]["score_importance"] > result_no_files[0]["score_importance"]

    def test_override_profil_prime_sur_learned_profil(self, tmp_path, monkeypatch):
        """category_overrides du profil priment toujours, même avec learned profil."""
        from Mnemo.tools.memory_tools import reciprocal_rank_fusion, PROFILES
        monkeypatch.setattr("Mnemo.tools.memory_tools.get_data_dir", lambda: tmp_path)
        # curiosity override : identité = 2.5 × base
        # learned curiosity : identité = 0.3 (tente d'écraser)
        (tmp_path / "learned_weights_curiosity.json").write_text(
            json.dumps({"weights": {"identité": 0.3}}), encoding="utf-8"
        )
        chunk = self._chunk("a", "identité")
        result_curious = reciprocal_rank_fusion([chunk], [], query="test",
                                                profile=PROFILES["curiosity"])
        result_conv    = reciprocal_rank_fusion(
            [self._chunk("b", "identité")], [], query="test", profile=PROFILES["conversation"]
        )
        # curiosity override (2.5) doit dominer learned_curiosity (0.3)
        assert result_curious[0]["score_importance"] > result_conv[0]["score_importance"]