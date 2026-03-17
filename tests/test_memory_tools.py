"""
Tests unitaires Niveau 1 — Briques bas niveau
Aucun appel LLM ou Ollama requis.
Lance avec : uv run pytest tests/ -v
"""
import json
import math
import sqlite3
import pytest

from datetime import datetime, timedelta
from pathlib import Path

# ── Import des fonctions à tester ─────────────────────────────
from Mnemo.tools.memory_tools import (
    sanitize_str,
    compute_hash,
    build_chunk_text,
    freshness_score,
    importance_score,
    infer_category_from_section,
    parse_markdown_chunks,
    update_markdown_section,
    load_session_json,
    update_session_memory,
    get_file_hash,
    CATEGORY_WEIGHTS,
    HALF_LIFE_BY_CATEGORY,
)


# ══════════════════════════════════════════════════════════════
# Fixtures
# ══════════════════════════════════════════════════════════════

@pytest.fixture
def tmp_md(tmp_path: Path) -> Path:
    """Fichier Markdown temporaire pour les tests."""
    md = tmp_path / "memory.md"
    md.write_text("""# Memory

## 🧑 Identité Utilisateur

### Profil de base
- Nom : Matt
- Métier : Développeur full-stack, spécialisé en IA et systèmes multi-agents
- Localisation : France

### Préférences & style
- Aime les réponses concises et techniques
- Préfère qu'on lui propose des alternatives

## 📚 Connaissances persistantes

### Projets en cours
Le projet Mnemo est un assistant personnel avec mémoire hybride.
Stack : CrewAI, SQLite, nomic-embed-text, Ollama en Docker.
""", encoding="utf-8")
    return md


@pytest.fixture
def tmp_session_dir(tmp_path: Path, monkeypatch) -> Path:
    """Dossier sessions temporaire — patch get_data_dir pour pointer sur tmp_path."""
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    monkeypatch.setattr("Mnemo.tools.memory_tools.get_data_dir", lambda: tmp_path)
    return sessions


# ══════════════════════════════════════════════════════════════
# sanitize_str
# ══════════════════════════════════════════════════════════════

class TestSanitizeStr:

    def test_string_normale_inchangee(self):
        assert sanitize_str("Bonjour Matt !") == "Bonjour Matt !"

    def test_accents_preserves(self):
        assert sanitize_str("éàüç préférence") == "éàüç préférence"

    def test_surrogate_supprime(self):
        """Le bug Ollama — surrogate invalide doit être supprimé sans crash."""
        with_surrogate = "texte\udcc3corrompu"
        result = sanitize_str(with_surrogate)
        assert "\udcc3" not in result
        assert "texte" in result
        assert "corrompu" in result

    def test_string_vide(self):
        assert sanitize_str("") == ""

    def test_emojis_preserves(self):
        assert sanitize_str("🧠 Agent 🐠") == "🧠 Agent 🐠"


# ══════════════════════════════════════════════════════════════
# compute_hash
# ══════════════════════════════════════════════════════════════

class TestComputeHash:

    def test_meme_contenu_meme_hash(self):
        assert compute_hash("hello world") == compute_hash("hello world")

    def test_contenu_different_hash_different(self):
        assert compute_hash("hello") != compute_hash("world")

    def test_hash_est_string(self):
        assert isinstance(compute_hash("test"), str)

    def test_hash_longueur_md5(self):
        """MD5 produit toujours 32 caractères hexadécimaux."""
        assert len(compute_hash("n'importe quoi")) == 32

    def test_sensible_a_la_casse(self):
        assert compute_hash("Hello") != compute_hash("hello")

    def test_sensible_aux_espaces(self):
        assert compute_hash("hello world") != compute_hash("helloworld")

    def test_string_vide(self):
        """Hash d'une string vide est déterministe."""
        assert compute_hash("") == compute_hash("")


# ══════════════════════════════════════════════════════════════
# build_chunk_text
# ══════════════════════════════════════════════════════════════

class TestBuildChunkText:

    def test_contient_section(self):
        result = build_chunk_text("Ma Section", "Ma Sous-section", "Contenu")
        assert "Ma Section" in result

    def test_contient_subsection(self):
        result = build_chunk_text("Ma Section", "Ma Sous-section", "Contenu")
        assert "Ma Sous-section" in result

    def test_contient_content(self):
        result = build_chunk_text("Section", "Sub", "Contenu important")
        assert "Contenu important" in result

    def test_format_attendu(self):
        result = build_chunk_text("Section", "Sub", "Contenu")
        assert result.startswith("Section : Section")
        assert "Sous-section : Sub" in result

    def test_pas_de_whitespace_en_debut_fin(self):
        result = build_chunk_text("  Section  ", "  Sub  ", "  Contenu  ")
        assert result == result.strip()


# ══════════════════════════════════════════════════════════════
# freshness_score
# ══════════════════════════════════════════════════════════════

class TestFreshnessScore:

    def test_score_aujourdhui_proche_de_1(self):
        now = datetime.now().isoformat()
        score = freshness_score(now)
        assert score > 0.99

    def test_score_demi_vie_environ_0_37(self):
        """À t = half_life(connaissance), le score doit être ≈ e^-1 ≈ 0.368."""
        half_life = HALF_LIFE_BY_CATEGORY["connaissance"]
        past = (datetime.now() - timedelta(days=half_life)).isoformat()
        score = freshness_score(past, category="connaissance")
        assert abs(score - math.exp(-1)) < 0.01

    def test_score_double_demi_vie(self):
        """À t = 2 × half_life(connaissance), score ≈ e^-2 ≈ 0.135."""
        half_life = HALF_LIFE_BY_CATEGORY["connaissance"]
        past = (datetime.now() - timedelta(days=half_life * 2)).isoformat()
        score = freshness_score(past, category="connaissance")
        assert abs(score - math.exp(-2)) < 0.01

    def test_score_entre_0_et_1(self):
        for days in [0, 1, 7, 30, 90, 365]:
            past = (datetime.now() - timedelta(days=days)).isoformat()
            score = freshness_score(past)
            assert 0 <= score <= 1, f"Score hors bornes pour {days} jours"

    def test_score_decroit_avec_le_temps(self):
        recent = (datetime.now() - timedelta(days=5)).isoformat()
        old    = (datetime.now() - timedelta(days=60)).isoformat()
        assert freshness_score(recent) > freshness_score(old)

    def test_date_invalide_retourne_1(self):
        """Date invalide → on ne pénalise pas le chunk."""
        assert freshness_score("pas-une-date") == 1.0

    def test_date_none_retourne_1(self):
        assert freshness_score(None) == 1.0

    def test_half_life_multiplier_personnalise(self):
        """half_life_multiplier < 1 accélère le decay → score plus bas."""
        past = (datetime.now() - timedelta(days=10)).isoformat()
        score_normal = freshness_score(past, category="connaissance", half_life_multiplier=1.0)
        score_accel  = freshness_score(past, category="connaissance", half_life_multiplier=0.25)
        # Demi-vie réduite à 15j → le chunk de 10j est plus « périmé »
        assert score_normal > score_accel


# ══════════════════════════════════════════════════════════════
# importance_score
# ══════════════════════════════════════════════════════════════

class TestImportanceScore:

    def test_poids_par_categorie(self):
        for cat, expected_weight in CATEGORY_WEIGHTS.items():
            assert importance_score(cat) == expected_weight

    def test_categorie_inconnue_retourne_1(self):
        assert importance_score("categorie_inexistante") == 1.0

    def test_categorie_none_retourne_1(self):
        assert importance_score(None) == 1.0

    def test_weight_explicite_prioritaire(self):
        """Un weight explicite doit écraser la catégorie."""
        assert importance_score("identité", weight=2.0) == 2.0

    def test_identite_est_le_plus_haut(self):
        scores = [importance_score(cat) for cat in CATEGORY_WEIGHTS]
        assert importance_score("identité") == max(scores)

    def test_historique_est_le_plus_bas(self):
        scores = [importance_score(cat) for cat in CATEGORY_WEIGHTS]
        assert importance_score("historique_session") == min(scores)


# ══════════════════════════════════════════════════════════════
# infer_category_from_section
# ══════════════════════════════════════════════════════════════

class TestInferCategoryFromSection:

    def test_identite_utilisateur(self):
        assert infer_category_from_section("🧑 Identité Utilisateur") == "identité"

    def test_identite_agent(self):
        assert infer_category_from_section("🤖 Identité Agent") == "identité"

    def test_connaissances(self):
        assert infer_category_from_section("📚 Connaissances persistantes") == "connaissance"

    def test_historique_sessions(self):
        assert infer_category_from_section("🔁 Historique des sessions") == "historique_session"

    def test_a_ne_jamais_oublier(self):
        assert infer_category_from_section("⚠️ À ne jamais oublier") == "décision"

    def test_section_inconnue_retourne_connaissance(self):
        assert infer_category_from_section("Section inconnue") == "connaissance"

    def test_insensible_a_la_casse(self):
        assert infer_category_from_section("IDENTITÉ UTILISATEUR") == "identité"


# ══════════════════════════════════════════════════════════════
# parse_markdown_chunks
# ══════════════════════════════════════════════════════════════

class TestParseMarkdownChunks:

    def test_retourne_liste(self, tmp_md):
        chunks = parse_markdown_chunks(tmp_md)
        assert isinstance(chunks, list)

    def test_nombre_de_chunks(self, tmp_md):
        """Le fixture a 3 sous-sections avec contenu suffisant."""
        chunks = parse_markdown_chunks(tmp_md)
        assert len(chunks) == 3

    def test_structure_chunk(self, tmp_md):
        chunk = parse_markdown_chunks(tmp_md)[0]
        assert "section" in chunk
        assert "subsection" in chunk
        assert "content" in chunk
        assert "source_line" in chunk
        assert "category" in chunk

    def test_section_correcte(self, tmp_md):
        chunks = parse_markdown_chunks(tmp_md)
        sections = {c["section"] for c in chunks}
        assert any("Identité" in s for s in sections)

    def test_chunks_pas_trop_courts(self, tmp_md):
        """Aucun chunk ne doit avoir moins de 50 caractères."""
        chunks = parse_markdown_chunks(tmp_md)
        for c in chunks:
            assert len(c["content"]) > 50

    def test_categorie_inferee(self, tmp_md):
        chunks = parse_markdown_chunks(tmp_md)
        identity_chunks = [c for c in chunks if "Identité" in c["section"]]
        assert all(c["category"] == "identité" for c in identity_chunks)

    def test_chunk_sans_contenu_suffisant_ignore(self, tmp_path):
        """Un ### avec moins de 50 chars de contenu ne doit pas être indexé."""
        md = tmp_path / "short.md"
        md.write_text("""## Section

### Sous-section courte
Court.
""", encoding="utf-8")
        chunks = parse_markdown_chunks(md)
        assert len(chunks) == 0

    def test_fichier_sans_subsection(self, tmp_path):
        """Un fichier sans ### ne doit pas crasher."""
        md = tmp_path / "no_sub.md"
        md.write_text("## Section\nContenu sans sous-section.\n", encoding="utf-8")
        chunks = parse_markdown_chunks(md)
        assert chunks == []


# ══════════════════════════════════════════════════════════════
# update_markdown_section
# ══════════════════════════════════════════════════════════════

class TestUpdateMarkdownSection:

    def test_cree_section_inexistante(self, tmp_path):
        md = tmp_path / "memory.md"
        md.write_text("", encoding="utf-8")
        update_markdown_section("Nouvelle Section", "Nouveau Sujet",
                                 "Contenu du nouveau sujet.", md_path=md)
        text = md.read_text(encoding="utf-8")
        assert "Nouvelle Section" in text
        assert "Nouveau Sujet" in text
        assert "Contenu du nouveau sujet." in text

    def test_met_a_jour_section_existante(self, tmp_md):
        update_markdown_section(
            "🧑 Identité Utilisateur", "Profil de base",
            "- Nom : Matt\n- Métier : Développeur senior",
            md_path=tmp_md
        )
        text = tmp_md.read_text(encoding="utf-8")
        assert "Développeur senior" in text

    def test_pas_de_duplication(self, tmp_md):
        update_markdown_section(
            "🧑 Identité Utilisateur", "Profil de base",
            "Contenu mis à jour.",
            md_path=tmp_md
        )
        update_markdown_section(
            "🧑 Identité Utilisateur", "Profil de base",
            "Contenu mis à jour encore.",
            md_path=tmp_md
        )
        text = tmp_md.read_text(encoding="utf-8")
        # Le titre ### ne doit apparaître qu'une seule fois
        assert text.count("### Profil de base") == 1

    def test_section_voisine_intacte(self, tmp_md):
        """Modifier une sous-section ne doit pas altérer les autres."""
        update_markdown_section(
            "🧑 Identité Utilisateur", "Profil de base",
            "Nouveau contenu.",
            md_path=tmp_md
        )
        text = tmp_md.read_text(encoding="utf-8")
        assert "Préférences & style" in text

    def test_sanitize_applique(self, tmp_path):
        """Les surrogates dans le contenu ne doivent pas crasher l'écriture."""
        md = tmp_path / "memory.md"
        md.write_text("", encoding="utf-8")
        update_markdown_section(
            "Section", "Sub",
            "texte\udcc3corrompu",
            md_path=md
        )
        text = md.read_text(encoding="utf-8")
        assert "\udcc3" not in text


# ══════════════════════════════════════════════════════════════
# load_session_json
# ══════════════════════════════════════════════════════════════

class TestLoadSessionJson:

    def test_session_inexistante_retourne_dict_vide(self, tmp_session_dir):
        result = load_session_json("session_inexistante")
        assert result == {}

    def test_charge_session_valide(self, tmp_session_dir):
        data = {"session_id": "test_123", "messages": []}
        (tmp_session_dir / "test_123.json").write_text(
            json.dumps(data), encoding="utf-8"
        )
        result = load_session_json("test_123")
        assert result["session_id"] == "test_123"

    def test_fichier_vide_retourne_dict_vide(self, tmp_session_dir):
        (tmp_session_dir / "empty.json").write_text("", encoding="utf-8")
        result = load_session_json("empty")
        assert result == {}

    def test_json_corrompu_retourne_dict_vide(self, tmp_session_dir):
        (tmp_session_dir / "broken.json").write_text(
            "{ceci n'est pas du json valide", encoding="utf-8"
        )
        result = load_session_json("broken")
        assert result == {}

    def test_json_corrompu_archive_en_broken(self, tmp_session_dir):
        """Un fichier JSON corrompu doit être renommé en .broken.json."""
        (tmp_session_dir / "broken2.json").write_text(
            "{{invalide", encoding="utf-8"
        )
        load_session_json("broken2")
        assert (tmp_session_dir / "broken2.broken.json").exists()
        assert not (tmp_session_dir / "broken2.json").exists()


# ══════════════════════════════════════════════════════════════
# update_session_memory
# ══════════════════════════════════════════════════════════════

class TestUpdateSessionMemory:

    def test_cree_fichier_session(self, tmp_session_dir):
        update_session_memory("sess_001", "Bonjour", "Salut !")
        assert (tmp_session_dir / "sess_001.json").exists()

    def test_accumule_les_messages(self, tmp_session_dir):
        update_session_memory("sess_002", "Message 1", "Réponse 1")
        update_session_memory("sess_002", "Message 2", "Réponse 2")
        data = load_session_json("sess_002")
        assert len(data["messages"]) == 4  # 2 user + 2 agent

    def test_roles_corrects(self, tmp_session_dir):
        update_session_memory("sess_003", "Question", "Réponse")
        data = load_session_json("sess_003")
        roles = [m["role"] for m in data["messages"]]
        assert roles == ["user", "agent"]

    def test_contenu_correct(self, tmp_session_dir):
        update_session_memory("sess_004", "Ma question", "Ma réponse")
        data = load_session_json("sess_004")
        assert data["messages"][0]["content"] == "Ma question"
        assert data["messages"][1]["content"] == "Ma réponse"

    def test_structure_par_defaut(self, tmp_session_dir):
        """Les clés par défaut doivent être présentes dès la première écriture."""
        update_session_memory("sess_005", "Hello", "Hi")
        data = load_session_json("sess_005")
        assert "session_id" in data
        assert "messages" in data
        assert "facts_extracted" in data
        assert "entities_mentioned" in data
        assert "to_persist" in data

    def test_sanitize_surrogate_dans_message(self, tmp_session_dir):
        """Les surrogates dans la réponse du modèle ne doivent pas crasher."""
        update_session_memory("sess_006", "Question", "Réponse\udcc3corrompue")
        data = load_session_json("sess_006")
        assert "\udcc3" not in data["messages"][1]["content"]


# ══════════════════════════════════════════════════════════════
# get_file_hash
# ══════════════════════════════════════════════════════════════

class TestGetFileHash:

    def test_meme_contenu_meme_hash(self, tmp_path):
        f1 = tmp_path / "a.txt"
        f2 = tmp_path / "b.txt"
        f1.write_bytes(b"contenu identique")
        f2.write_bytes(b"contenu identique")
        assert get_file_hash(f1) == get_file_hash(f2)

    def test_contenu_different_hash_different(self, tmp_path):
        f1 = tmp_path / "a.txt"
        f2 = tmp_path / "b.txt"
        f1.write_bytes(b"contenu A")
        f2.write_bytes(b"contenu B")
        assert get_file_hash(f1) != get_file_hash(f2)

    def test_hash_est_string_de_32_chars(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_bytes(b"test")
        h = get_file_hash(f)
        assert isinstance(h, str)
        assert len(h) == 32

    def test_modification_change_le_hash(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_bytes(b"version 1")
        hash1 = get_file_hash(f)
        f.write_bytes(b"version 2")
        hash2 = get_file_hash(f)
        assert hash1 != hash2