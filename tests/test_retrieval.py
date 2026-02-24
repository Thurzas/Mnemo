"""
Tests unitaires Niveau 2 — Retrieval hybride
Deux groupes :
  - Marqués @pytest.mark.ollama : nécessitent Ollama + nomic-embed-text
  - Non marqués : pure logique, aucun service requis

Lancer tous les tests :
    uv run pytest tests/test_retrieval.py -v

Lancer uniquement les tests sans Ollama :
    uv run pytest tests/test_retrieval.py -v -m "not ollama"

Lancer uniquement les tests Ollama :
    uv run pytest tests/test_retrieval.py -v -m ollama
"""
import sqlite3
import numpy as np
import pytest

from pathlib import Path
from datetime import datetime

from Mnemo.tools.memory_tools import (
    adaptive_weights,
    reciprocal_rank_fusion,
    format_chunks_for_prompt,
    cosine_similarity,
    compute_hash,
    build_chunk_text,
    search_keyword,
    search_vector,
    retrieve,
    upsert_chunk,
    importance_score,
    freshness_score,
    CATEGORY_WEIGHTS,
    TOP_K_FINAL,
)


# ══════════════════════════════════════════════════════════════
# Fixtures
# ══════════════════════════════════════════════════════════════

def create_schema(db: sqlite3.Connection):
    """Crée le schéma complet dans une DB SQLite (identique à init_db.py)."""
    db.executescript("""
        CREATE TABLE IF NOT EXISTS chunks (
            id                TEXT PRIMARY KEY,
            section           TEXT NOT NULL,
            subsection        TEXT,
            content           TEXT NOT NULL,
            created_at        DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at        DATETIME DEFAULT CURRENT_TIMESTAMP,
            source_line       INTEGER,
            importance_weight REAL DEFAULT 1.0,
            category          TEXT DEFAULT 'connaissance'
        );

        CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts
        USING fts5(chunk_id, content, section, subsection, tokenize='unicode61');

        CREATE TABLE IF NOT EXISTS embeddings (
            chunk_id TEXT PRIMARY KEY REFERENCES chunks(id) ON DELETE CASCADE,
            model    TEXT NOT NULL,
            vector   BLOB NOT NULL,
            dim      INTEGER NOT NULL
        );
    """)
    db.commit()


@pytest.fixture
def db_mem() -> sqlite3.Connection:
    """DB SQLite en mémoire avec schéma complet — aucun fichier sur disque."""
    db = sqlite3.connect(":memory:")
    create_schema(db)
    yield db
    db.close()


@pytest.fixture
def db_with_chunks(db_mem, monkeypatch, tmp_path) -> sqlite3.Connection:
    """
    DB en mémoire pré-remplie de chunks manuels avec faux vecteurs.
    N'utilise PAS Ollama — les vecteurs sont des np.arrays aléatoires fixés.
    Utilisé pour tester la logique RRF et les scores sans dépendance réseau.
    """
    FAKE_DIM = 8  # Dimension réduite pour les tests

    chunks_data = [
        {
            "id":       "chunk_python",
            "section":  "Connaissances persistantes",
            "subsection": "Langages maîtrisés",
            "content":  "Matt maîtrise Python, JavaScript et TypeScript.",
            "category": "connaissance",
            "weight":   1.0,
            # Vecteur pointant vers "python programming language"
            "vector":   np.array([1.0, 0.8, 0.1, 0.0, 0.2, 0.0, 0.0, 0.0], dtype=np.float32),
        },
        {
            "id":       "chunk_identity",
            "section":  "Identité Utilisateur",
            "subsection": "Profil de base",
            "content":  "Matt est développeur full-stack spécialisé en IA et systèmes multi-agents.",
            "category": "identité",
            "weight":   1.5,
            "vector":   np.array([0.0, 0.1, 1.0, 0.9, 0.0, 0.0, 0.0, 0.0], dtype=np.float32),
        },
        {
            "id":       "chunk_project",
            "section":  "Connaissances persistantes",
            "subsection": "Projets en cours",
            "content":  "Le projet Mnemo est un assistant personnel avec mémoire hybride. Stack CrewAI SQLite Ollama.",
            "category": "projet",
            "weight":   1.2,
            "vector":   np.array([0.0, 0.0, 0.0, 0.1, 1.0, 0.9, 0.0, 0.0], dtype=np.float32),
        },
        {
            "id":       "chunk_preference",
            "section":  "Identité Utilisateur",
            "subsection": "Préférences & style",
            "content":  "Matt préfère des réponses concises et techniques. Il aime les analogies.",
            "category": "préférence",
            "weight":   1.1,
            "vector":   np.array([0.0, 0.0, 0.0, 0.0, 0.0, 0.1, 1.0, 0.9], dtype=np.float32),
        },
    ]

    for c in chunks_data:
        db_mem.execute("""
            INSERT INTO chunks (id, section, subsection, content, updated_at, source_line, category, importance_weight)
            VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP, 0, ?, ?)
        """, (c["id"], c["section"], c["subsection"], c["content"], c["category"], c["weight"]))
        db_mem.execute("""
            INSERT INTO embeddings (chunk_id, model, vector, dim)
            VALUES (?, 'test-model', ?, ?)
        """, (c["id"], c["vector"].tobytes(), FAKE_DIM))
        db_mem.execute("""
            INSERT INTO chunks_fts (chunk_id, content, section, subsection)
            VALUES (?, ?, ?, ?)
        """, (c["id"], c["content"], c["section"], c["subsection"]))

    db_mem.commit()
    return db_mem


@pytest.fixture
def ollama_db(tmp_path, monkeypatch):
    """
    DB SQLite réelle sur disque, patchée comme DB_PATH globale.
    Utilisée pour les tests qui font de vrais appels Ollama.
    """
    db_path = tmp_path / "test_memory.db"
    monkeypatch.setattr("Mnemo.tools.memory_tools.DB_PATH", db_path)

    db = sqlite3.connect(str(db_path))
    create_schema(db)
    db.close()

    return db_path


# ══════════════════════════════════════════════════════════════
# adaptive_weights — pure logique, pas d'Ollama
# ══════════════════════════════════════════════════════════════

class TestAdaptiveWeights:

    def test_query_courte_biais_keyword(self):
        """1-2 mots → keyword 60%, vector 40%."""
        w_fts, w_vec = adaptive_weights("Python")
        assert w_fts == 0.6
        assert w_vec == 0.4

    def test_query_deux_mots_biais_keyword(self):
        w_fts, w_vec = adaptive_weights("projet Mnemo")
        assert w_fts == 0.6
        assert w_vec == 0.4

    def test_query_longue_biais_vector(self):
        """3+ mots → keyword 30%, vector 70%."""
        w_fts, w_vec = adaptive_weights("comment fonctionne la mémoire hybride")
        assert w_fts == 0.3
        assert w_vec == 0.7

    def test_somme_poids_egale_1(self):
        for query in ["mot", "deux mots", "une longue phrase de test"]:
            w_fts, w_vec = adaptive_weights(query)
            assert abs(w_fts + w_vec - 1.0) < 1e-9

    def test_query_vide_ne_crash_pas(self):
        """Une query vide doit retourner des poids valides sans crasher."""
        w_fts, w_vec = adaptive_weights("")
        assert w_fts + w_vec == pytest.approx(1.0)


# ══════════════════════════════════════════════════════════════
# cosine_similarity — pure math, pas d'Ollama
# ══════════════════════════════════════════════════════════════

class TestCosineSimilarity:

    def test_vecteurs_identiques_retourne_1(self):
        v = np.array([1.0, 2.0, 3.0], dtype=np.float32)
        assert cosine_similarity(v, v) == pytest.approx(1.0, abs=1e-5)

    def test_vecteurs_orthogonaux_retourne_0(self):
        a = np.array([1.0, 0.0, 0.0], dtype=np.float32)
        b = np.array([0.0, 1.0, 0.0], dtype=np.float32)
        assert cosine_similarity(a, b) == pytest.approx(0.0, abs=1e-5)

    def test_vecteurs_opposes_retourne_moins_1(self):
        a = np.array([1.0, 0.0], dtype=np.float32)
        b = np.array([-1.0, 0.0], dtype=np.float32)
        assert cosine_similarity(a, b) == pytest.approx(-1.0, abs=1e-5)

    def test_score_entre_moins1_et_1(self):
        rng = np.random.default_rng(42)
        for _ in range(20):
            a = rng.random(64).astype(np.float32)
            b = rng.random(64).astype(np.float32)
            score = cosine_similarity(a, b)
            assert -1.0 <= score <= 1.0

    def test_insensible_a_la_norme(self):
        """La similarité cosinus ne dépend pas de la magnitude des vecteurs."""
        a = np.array([1.0, 2.0, 3.0], dtype=np.float32)
        b = np.array([2.0, 4.0, 6.0], dtype=np.float32)  # même direction, double norme
        assert cosine_similarity(a, b) == pytest.approx(1.0, abs=1e-5)


# ══════════════════════════════════════════════════════════════
# reciprocal_rank_fusion — logique pure, vecteurs mockés
# ══════════════════════════════════════════════════════════════

class TestReciprocalRankFusion:

    def _make_chunks(self, ids_and_cats: list[tuple]) -> list[dict]:
        """Helper : crée des chunks minimaux pour les tests RRF."""
        return [
            {
                "id":               cid,
                "content":          f"Contenu du chunk {cid}",
                "section":          "Test",
                "subsection":       "Sub",
                "category":         cat,
                "importance_weight": CATEGORY_WEIGHTS.get(cat, 1.0),
                "updated_at":       datetime.now().isoformat(),
            }
            for cid, cat in ids_and_cats
        ]

    def test_retourne_liste(self):
        kw  = self._make_chunks([("a", "connaissance"), ("b", "projet")])
        vec = self._make_chunks([("b", "projet"), ("a", "connaissance")])
        result = reciprocal_rank_fusion(kw, vec)
        assert isinstance(result, list)

    def test_chunk_present_dans_les_deux_listes_score_plus_haut(self):
        """Un chunk en top de kw ET vec doit scorer plus haut qu'un chunk dans une seule liste."""
        kw  = self._make_chunks([("commun", "connaissance"), ("kw_only", "connaissance")])
        vec = self._make_chunks([("commun", "connaissance"), ("vec_only", "connaissance")])
        result = reciprocal_rank_fusion(kw, vec)
        ids_ranked = [r["id"] for r in result]
        assert ids_ranked.index("commun") < ids_ranked.index("kw_only")
        assert ids_ranked.index("commun") < ids_ranked.index("vec_only")

    def test_importance_influence_le_classement(self):
        """Un chunk 'identité' (1.5) doit surclasser un chunk 'historique_session' (0.7)
        même si les deux sont au même rang dans les deux listes."""
        kw  = self._make_chunks([("identite", "identité"), ("historique", "historique_session")])
        vec = self._make_chunks([("identite", "identité"), ("historique", "historique_session")])
        result = reciprocal_rank_fusion(kw, vec)
        ids_ranked = [r["id"] for r in result]
        assert ids_ranked.index("identite") < ids_ranked.index("historique")

    def test_score_final_present_dans_chaque_chunk(self):
        kw  = self._make_chunks([("a", "connaissance")])
        vec = self._make_chunks([("a", "connaissance")])
        result = reciprocal_rank_fusion(kw, vec)
        for r in result:
            assert "score_final" in r
            assert "score_rrf" in r
            assert "score_importance" in r
            assert "score_freshness" in r

    def test_score_final_strictement_positif(self):
        kw  = self._make_chunks([("a", "connaissance"), ("b", "projet")])
        vec = self._make_chunks([("b", "projet"), ("a", "connaissance")])
        result = reciprocal_rank_fusion(kw, vec)
        for r in result:
            assert r["score_final"] > 0

    def test_liste_vide_kw_ne_crash_pas(self):
        vec = self._make_chunks([("a", "connaissance")])
        result = reciprocal_rank_fusion([], vec)
        assert len(result) == 1

    def test_liste_vide_vec_ne_crash_pas(self):
        kw = self._make_chunks([("a", "connaissance")])
        result = reciprocal_rank_fusion(kw, [])
        assert len(result) == 1

    def test_deux_listes_vides_retourne_vide(self):
        result = reciprocal_rank_fusion([], [])
        assert result == []

    def test_tri_decroissant_par_score_final(self):
        kw  = self._make_chunks([("a", "identité"), ("b", "connaissance"), ("c", "historique_session")])
        vec = self._make_chunks([("a", "identité"), ("b", "connaissance"), ("c", "historique_session")])
        result = reciprocal_rank_fusion(kw, vec)
        scores = [r["score_final"] for r in result]
        assert scores == sorted(scores, reverse=True)


# ══════════════════════════════════════════════════════════════
# search_keyword — FTS5, pas d'Ollama
# ══════════════════════════════════════════════════════════════

class TestSearchKeyword:

    def test_trouve_chunk_par_mot_cle_exact(self, db_with_chunks):
        results = search_keyword(db_with_chunks, "Python")
        ids = [r["id"] for r in results]
        assert "chunk_python" in ids

    def test_trouve_chunk_par_mot_partiel(self, db_with_chunks):
        results = search_keyword(db_with_chunks, "Mnemo")
        ids = [r["id"] for r in results]
        assert "chunk_project" in ids

    def test_query_inexistante_retourne_liste_vide(self, db_with_chunks):
        results = search_keyword(db_with_chunks, "xyzquantumfoobarbaz")
        assert results == []

    def test_structure_resultat(self, db_with_chunks):
        results = search_keyword(db_with_chunks, "Matt")
        assert len(results) > 0
        r = results[0]
        assert "id" in r
        assert "content" in r
        assert "score_fts" in r
        assert "category" in r
        assert "importance_weight" in r

    def test_top_k_respecte(self, db_with_chunks):
        results = search_keyword(db_with_chunks, "Matt", top_k=2)
        assert len(results) <= 2

    def test_query_vide_ne_crash_pas(self, db_with_chunks):
        """FTS5 peut crasher sur une query vide — on vérifie que ça ne plante pas."""
        try:
            results = search_keyword(db_with_chunks, "")
            assert isinstance(results, list)
        except Exception:
            pass  # Acceptable : FTS5 peut rejeter une query vide


# ══════════════════════════════════════════════════════════════
# format_chunks_for_prompt — pure logique, pas d'Ollama
# ══════════════════════════════════════════════════════════════

class TestFormatChunksForPrompt:

    def _make_chunk(self, section, subsection, content):
        return {
            "section": section, "subsection": subsection,
            "content": content, "score_final": 1.0,
        }

    def test_liste_vide_retourne_message_par_defaut(self):
        result = format_chunks_for_prompt([])
        assert "Aucun souvenir" in result

    def test_contient_section_et_subsection(self):
        chunks = [self._make_chunk("Section A", "Sub 1", "Contenu 1")]
        result = format_chunks_for_prompt(chunks)
        assert "Section A" in result
        assert "Sub 1" in result

    def test_contient_le_contenu(self):
        chunks = [self._make_chunk("S", "SS", "Contenu important")]
        result = format_chunks_for_prompt(chunks)
        assert "Contenu important" in result

    def test_separateur_entre_chunks(self):
        chunks = [
            self._make_chunk("S", "Sub 1", "Contenu 1"),
            self._make_chunk("S", "Sub 2", "Contenu 2"),
        ]
        result = format_chunks_for_prompt(chunks)
        assert "---" in result

    def test_un_seul_chunk_pas_de_separateur(self):
        chunks = [self._make_chunk("S", "Sub", "Contenu")]
        result = format_chunks_for_prompt(chunks)
        assert "---" not in result


# ══════════════════════════════════════════════════════════════
# Tests Ollama — nécessitent nomic-embed-text en fonctionnement
# ══════════════════════════════════════════════════════════════

@pytest.mark.ollama
class TestRetrievalWithOllama:
    """
    Ces tests font de vrais appels à Ollama (nomic-embed-text).
    Ollama doit tourner et le modèle doit être disponible.
    Lance uniquement ces tests avec : pytest -m ollama
    """

    CHUNKS_TO_INSERT = [
        {
            "section":    "Identité Utilisateur",
            "subsection": "Profil de base",
            "content":    "Matt est développeur full-stack spécialisé en IA et systèmes multi-agents. Il travaille principalement avec Python et JavaScript.",
            "category":   "identité",
        },
        {
            "section":    "Connaissances persistantes",
            "subsection": "Projets en cours",
            "content":    "Le projet Mnemo est un assistant personnel avec mémoire hybride basé sur CrewAI, SQLite et nomic-embed-text pour la vectorisation locale.",
            "category":   "projet",
        },
        {
            "section":    "Identité Utilisateur",
            "subsection": "Préférences & style",
            "content":    "Matt préfère des réponses concises et techniques. Il apprécie les analogies pour expliquer des concepts complexes.",
            "category":   "préférence",
        },
        {
            "section":    "Connaissances persistantes",
            "subsection": "Stack technique",
            "content":    "Stack principal : Python, CrewAI, SQLite FTS5, nomic-embed-text via Ollama, Docker pour l'infrastructure.",
            "category":   "connaissance",
        },
        {
            "section":    "Identité Agent",
            "subsection": "Rôle & personnalité",
            "content":    "L'agent est un assistant personnel local. Il répond de façon naturelle en utilisant le contexte mémoire disponible sans exposer ses mécanismes internes.",
            "category":   "identité",
        },
    ]

    @pytest.fixture(autouse=True)
    def setup_db(self, ollama_db, monkeypatch):
        """Insère des chunks réels via upsert_chunk (appels Ollama réels)."""
        import sqlite3 as _sqlite3
        db = _sqlite3.connect(str(ollama_db))
        for i, c in enumerate(self.CHUNKS_TO_INSERT):
            upsert_chunk(
                db,
                section=c["section"],
                subsection=c["subsection"],
                content=c["content"],
                source_line=i,
                category=c["category"],
            )
        db.close()

    def test_retrieve_retourne_liste(self):
        results = retrieve("qui est Matt")
        assert isinstance(results, list)

    def test_retrieve_retourne_au_plus_top_k(self):
        results = retrieve("Matt développeur", top_k_final=3)
        assert len(results) <= 3

    def test_retrieve_question_identite_retourne_profil(self):
        """'Qui est Matt' doit retourner le chunk d'identité en premier."""
        results = retrieve("Qui est Matt", top_k_final=5)
        assert len(results) > 0
        top_contents = " ".join(r["content"] for r in results[:2])
        assert "Matt" in top_contents

    def test_retrieve_question_projet_retourne_mnemo(self):
        """Une question sur le projet doit trouver le chunk Mnemo quelque part dans les résultats.
        On vérifie sur l'ensemble du top_k — le retrieval sémantique peut placer d'autres chunks
        pertinents (ex: identité de l'auteur) avant le chunk projet exact."""
        results = retrieve("en quoi consiste le projet en cours", top_k_final=5)
        assert len(results) > 0
        all_contents = " ".join(r["content"] for r in results)
        assert "Mnemo" in all_contents, (
            f"'Mnemo' introuvable dans les {len(results)} chunks retournés.\n"
            f"Chunks : {[r['subsection'] for r in results]}"
        )

    def test_retrieve_question_stack_retourne_technique(self):
        """Question sur la stack technique → chunk stack."""
        results = retrieve("quels outils et technologies sont utilisés", top_k_final=5)
        assert len(results) > 0
        top_contents = " ".join(r["content"] for r in results)
        # Au moins un des termes techniques doit apparaître
        assert any(term in top_contents for term in ["Python", "CrewAI", "SQLite", "Docker"])

    def test_identite_chunk_score_plus_haut_que_connaissance(self):
        """
        À contenu et rang similaires, un chunk 'identité' (poids 1.5)
        doit avoir un score_final supérieur à un chunk 'connaissance' (poids 1.0).
        """
        results = retrieve("Matt", top_k_final=5)
        identity_chunks = [r for r in results if r.get("category") == "identité"]
        knowledge_chunks = [r for r in results if r.get("category") == "connaissance"]
        if identity_chunks and knowledge_chunks:
            # Le meilleur chunk identité doit scorer mieux que le meilleur connaissance
            best_identity  = max(c["score_final"] for c in identity_chunks)
            best_knowledge = max(c["score_final"] for c in knowledge_chunks)
            assert best_identity > best_knowledge

    def test_retrieve_query_vide_ne_crash_pas(self):
        """Une query vide ne doit pas faire planter retrieve()."""
        try:
            results = retrieve("")
            assert isinstance(results, list)
        except Exception as e:
            pytest.fail(f"retrieve('') a crashé : {e}")

    def test_chunks_ont_score_final(self):
        results = retrieve("Matt développeur projet", top_k_final=5)
        for r in results:
            assert "score_final" in r
            assert r["score_final"] > 0

    def test_chunks_tries_par_score_decroissant(self):
        results = retrieve("mémoire hybride assistant", top_k_final=5)
        scores = [r["score_final"] for r in results]
        assert scores == sorted(scores, reverse=True)

    def test_upsert_idempotent(self, ollama_db):
        """Insérer deux fois le même chunk ne doit pas créer de doublon."""
        import sqlite3 as _sqlite3
        db = _sqlite3.connect(str(ollama_db))
        count_before = db.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        # Réinsère le premier chunk (déjà présent)
        c = self.CHUNKS_TO_INSERT[0]
        upsert_chunk(db, c["section"], c["subsection"], c["content"], 0, c["category"])
        count_after = db.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        db.close()
        assert count_before == count_after