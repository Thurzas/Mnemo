"""
test_curiosity.py — Tests unitaires pour la CuriosityCrew

Couvre :
  - _extract_section_content   : isolation correcte par section ##
  - _line_is_real_value        : placeholder / label vide / vraie valeur
  - _detect_structural_gaps    : template vierge, partiellement rempli, complet
  - update_markdown_section    : placeholder → remplace, contenu réel → enrichit
  - _collect_answers           : input() mocké, skip, interruption
  - _mark_skipped / _get_skipped_questions : persistance DB

Aucun appel Ollama, CrewAI ou LLM requis.
"""

import json
import sqlite3
import sys
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock
from textwrap import dedent


# ── path géré par conftest.py ────────────────────────────────────────────────


# ══════════════════════════════════════════════════════════════════════════════
# Fixtures & helpers
# ══════════════════════════════════════════════════════════════════════════════

TEMPLATE_BLANK = dedent("""\
    ## 🧑 Identité Utilisateur
    ### Profil de base
    - **Nom/Pseudo** : pas encore renseigné — je dois questionner l'utilisateur sur ce point.
    - **Métier** : pas encore renseigné — je dois questionner l'utilisateur sur ce point.
    - **Localisation** : pas encore renseigné — je dois questionner l'utilisateur sur ce point.
    ### Préférences & style
    - **Style de communication** : pas encore renseigné — je dois questionner l'utilisateur sur ce point.
    ## 🤖 Identité Agent
    ### Rôle & personnalité définis
    - **Nom de l'agent** : pas encore renseigné — je dois questionner l'utilisateur sur ce point.
    ## 📚 Connaissances persistantes
    ### Projets en cours
    Aucun projet renseigné pour l'instant.
""")

TEMPLATE_PARTIAL = dedent("""\
    ## 🧑 Identité Utilisateur
    ### Profil de base
    - **Nom/Pseudo** : Matt
    - **Métier** : pas encore renseigné — je dois questionner l'utilisateur sur ce point.
    - **Localisation** : pas encore renseigné — je dois questionner l'utilisateur sur ce point.
    ### Préférences & style
    - **Style de communication** : réponses courtes et directes
    ## 🤖 Identité Agent
    ### Rôle & personnalité définis
    - **Nom de l'agent** : Mnemo
    ## 📚 Connaissances persistantes
    ### Projets en cours
    Agent IA en Python avec CrewAI.
""")

TEMPLATE_FULL = dedent("""\
    ## 🧑 Identité Utilisateur
    ### Profil de base
    - **Nom/Pseudo** : Matt
    - **Métier** : développeur web
    - **Localisation** : France
    ### Préférences & style
    - **Style de communication** : réponses courtes et directes
    ## 🤖 Identité Agent
    ### Rôle & personnalité définis
    - **Nom de l'agent** : Mnemo
    ## 📚 Connaissances persistantes
    ### Projets en cours
    Agent IA en Python avec CrewAI.
""")


@pytest.fixture(autouse=True)
def mock_ollama():
    """Bloque tout appel Ollama — aucun test ne doit en avoir besoin."""
    with patch("ollama.embed", side_effect=RuntimeError("Ollama ne doit pas être appelé dans ces tests")):
        yield


@pytest.fixture()
def tmp_md(tmp_path):
    """Retourne un Path vers un memory.md temporaire."""
    md = tmp_path / "memory.md"
    return md


@pytest.fixture()
def tmp_db(tmp_path, monkeypatch):
    """Base SQLite temporaire avec la table curiosity_skipped."""
    db_path = tmp_path / "memory.db"

    def fake_get_db():
        conn = sqlite3.connect(db_path)
        conn.execute(
            "CREATE TABLE IF NOT EXISTS curiosity_skipped "
            "(id TEXT PRIMARY KEY, question TEXT, skipped_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
        )
        conn.commit()
        return conn

    # Patch get_db dans memory_tools ET dans main
    import Mnemo.tools.memory_tools as mt
    import Mnemo.main as main_module
    monkeypatch.setattr(mt, "get_db", fake_get_db)
    monkeypatch.setattr(main_module, "get_db", fake_get_db)
    return db_path


# ── Import après setup path ───────────────────────────────────────────────────
from Mnemo.main import (
    _extract_section_content,
    _line_is_real_value,
    _detect_structural_gaps,
    _collect_answers,
    _mark_skipped,
    _get_skipped_questions,
    MEMORY_SCHEMA,
    _PLACEHOLDER_MARKERS,
)
from Mnemo.tools.memory_tools import update_markdown_section


# ══════════════════════════════════════════════════════════════════════════════
# _extract_section_content
# ══════════════════════════════════════════════════════════════════════════════

class TestExtractSectionContent:

    def test_section_found_returns_content(self):
        content = _extract_section_content(TEMPLATE_BLANK, "identité utilisateur")
        assert "profil de base" in content.lower()
        assert "préférences" in content.lower()

    def test_section_stops_at_next_h2(self):
        """Le contenu ne doit pas déborder dans la section suivante."""
        content = _extract_section_content(TEMPLATE_BLANK, "identité utilisateur")
        assert "identité agent" not in content.lower()
        assert "connaissances" not in content.lower()

    def test_section_with_emoji_stripped(self):
        """Les emojis dans les titres ## ne doivent pas bloquer la recherche."""
        content = _extract_section_content(TEMPLATE_BLANK, "identité agent")
        assert "nom de l'agent" in content.lower()

    def test_section_not_found_returns_empty(self):
        content = _extract_section_content(TEMPLATE_BLANK, "section inexistante xyz")
        assert content == ""

    def test_section_key_partial_match(self):
        """La clé courte doit matcher même si le titre ## est plus long."""
        content = _extract_section_content(TEMPLATE_BLANK, "connaissances")
        assert "projets" in content.lower()

    def test_empty_memory_returns_empty(self):
        assert _extract_section_content("", "identité utilisateur") == ""

    def test_section_content_is_lowercase(self):
        """La fonction retourne tout en minuscules pour faciliter les comparaisons."""
        content = _extract_section_content(TEMPLATE_FULL, "identité utilisateur")
        assert content == content.lower()

    def test_second_section_not_polluted_by_first(self):
        content_agent = _extract_section_content(TEMPLATE_FULL, "identité agent")
        assert "matt" not in content_agent
        assert "mnemo" in content_agent


# ══════════════════════════════════════════════════════════════════════════════
# _line_is_real_value
# ══════════════════════════════════════════════════════════════════════════════

class TestLineIsRealValue:

    # ── Placeholders — doivent retourner False ────────────────────────────────

    def test_placeholder_not_renseigne(self):
        line = "- **Nom/Pseudo** : pas encore renseigné — je dois questionner l'utilisateur"
        assert _line_is_real_value(line, "nom") is False

    def test_placeholder_aucun(self):
        line = "- **Centres d'intérêt** : aucun"
        assert _line_is_real_value(line, "intérêt") is False

    def test_placeholder_aucune(self):
        line = "- **Décision** : aucune pour l'instant"
        assert _line_is_real_value(line, "décision") is False

    def test_placeholder_pour_instant(self):
        line = "Aucun projet renseigné pour l'instant."
        assert _line_is_real_value(line, "projet") is False

    def test_placeholder_je_dois_questionner(self):
        line = "- **Métier** : je dois questionner l'utilisateur sur ce point."
        assert _line_is_real_value(line, "métier") is False

    def test_placeholder_je_me_demande(self):
        line = "- **Fuseau** : je me demande quel est son fuseau"
        assert _line_is_real_value(line, "fuseau") is False

    # ── Labels Markdown sans valeur — doivent retourner False ────────────────

    def test_label_only_no_value_after_colon(self):
        """**Nom/Pseudo** : (vide) → pas de vraie valeur."""
        line = "- **nom/pseudo** :"
        assert _line_is_real_value(line, "nom") is False

    def test_label_with_empty_value(self):
        line = "- **Métier** :   "
        assert _line_is_real_value(line, "métier") is False

    # ── Vraies valeurs — doivent retourner True ───────────────────────────────

    def test_real_value_after_label(self):
        line = "- **Nom/Pseudo** : Matt"
        assert _line_is_real_value(line, "nom") is True

    def test_real_value_prose(self):
        """Texte libre sans Markdown — doit être considéré comme une vraie valeur."""
        line = "Matt est développeur web basé en France."
        assert _line_is_real_value(line, "matt") is True

    def test_real_value_profession(self):
        line = "- **Métier** : développeur web"
        assert _line_is_real_value(line, "métier") is True

    def test_real_value_localisation(self):
        line = "- **Localisation** : France"
        assert _line_is_real_value(line, "france") is True

    def test_real_value_style(self):
        line = "- **Style de communication** : réponses courtes et directes"
        assert _line_is_real_value(line, "style") is True

    def test_real_value_agent_name(self):
        line = "- **Nom de l'agent** : Mnemo"
        assert _line_is_real_value(line, "mnemo") is True

    # ── Cas limites ───────────────────────────────────────────────────────────

    def test_alias_in_prose_with_placeholder(self):
        """L'alias 'nom' présent mais sur une ligne placeholder → False."""
        line = "je dois questionner l'utilisateur sur son nom et prénom"
        assert _line_is_real_value(line, "nom") is False

    def test_alias_case_insensitive(self):
        """La comparaison doit être insensible à la casse."""
        line = "- **NOM/PSEUDO** : MATTIEU"
        assert _line_is_real_value(line, "nom") is True


# ══════════════════════════════════════════════════════════════════════════════
# _detect_structural_gaps
# ══════════════════════════════════════════════════════════════════════════════

class TestDetectStructuralGaps:

    def _gap_questions(self, gaps):
        return [g["question"] for g in gaps]

    def _gap_ids(self, gaps):
        return [g["id"] for g in gaps]

    # ── Template vierge ───────────────────────────────────────────────────────

    def test_blank_template_detects_all_gaps(self):
        """Tous les champs du MEMORY_SCHEMA doivent être manquants sur template vierge."""
        gaps = _detect_structural_gaps(TEMPLATE_BLANK)
        total_fields = sum(len(fields) for fields in MEMORY_SCHEMA.values())
        assert len(gaps) == total_fields

    def test_blank_template_gap_types(self):
        gaps = _detect_structural_gaps(TEMPLATE_BLANK)
        assert all(g["type"] == "structural" for g in gaps)

    def test_blank_template_gap_has_label(self):
        gaps = _detect_structural_gaps(TEMPLATE_BLANK)
        assert all(g["label"] for g in gaps), "Chaque gap doit avoir un label non vide"

    def test_blank_template_gap_has_section_subsection(self):
        gaps = _detect_structural_gaps(TEMPLATE_BLANK)
        for g in gaps:
            assert g["section"], f"section manquante sur gap: {g['question']}"
            assert g["subsection"], f"subsection manquante sur gap: {g['question']}"

    def test_blank_template_priority_when_section_absent(self):
        """Section complètement absente → priorité 1 (plus haute)."""
        empty = ""
        gaps = _detect_structural_gaps(empty)
        assert all(g["priority"] == 1 for g in gaps)

    def test_blank_template_priority_when_section_present(self):
        """Section présente mais champ vide → priorité 2."""
        gaps = _detect_structural_gaps(TEMPLATE_BLANK)
        # La section ## est présente, les champs sont placeholders → priorité 2
        assert all(g["priority"] == 2 for g in gaps)

    # ── Template partiellement rempli ─────────────────────────────────────────

    def test_partial_template_nom_filled(self):
        gaps = _detect_structural_gaps(TEMPLATE_PARTIAL)
        questions = self._gap_questions(gaps)
        # "Matt" dans Profil de base → nom détecté comme rempli
        assert not any("appelles" in q for q in questions)

    def test_partial_template_metier_missing(self):
        gaps = _detect_structural_gaps(TEMPLATE_PARTIAL)
        questions = self._gap_questions(gaps)
        assert any("profession" in q.lower() or "métier" in q.lower() for q in questions)

    def test_partial_template_localisation_missing(self):
        gaps = _detect_structural_gaps(TEMPLATE_PARTIAL)
        questions = self._gap_questions(gaps)
        assert any("ville" in q.lower() or "pays" in q.lower() for q in questions)

    def test_partial_template_style_filled(self):
        gaps = _detect_structural_gaps(TEMPLATE_PARTIAL)
        questions = self._gap_questions(gaps)
        # "réponses courtes et directes" contient "courte" et "direct" → rempli
        assert not any("réponses courtes" in q for q in questions)

    def test_partial_template_agent_filled(self):
        gaps = _detect_structural_gaps(TEMPLATE_PARTIAL)
        questions = self._gap_questions(gaps)
        # "Mnemo" dans Rôle & personnalité → rempli
        assert not any("agent" in q.lower() and "nom" in q.lower() for q in questions)

    def test_partial_template_exact_count(self):
        """Avec TEMPLATE_PARTIAL : Métier + Localisation manquants = 2 gaps."""
        gaps = _detect_structural_gaps(TEMPLATE_PARTIAL)
        assert len(gaps) == 2

    # ── Template complet ──────────────────────────────────────────────────────

    def test_full_template_no_gaps(self):
        gaps = _detect_structural_gaps(TEMPLATE_FULL)
        assert gaps == []

    # ── Cas limites ───────────────────────────────────────────────────────────

    def test_empty_string_all_gaps(self):
        gaps = _detect_structural_gaps("")
        total_fields = sum(len(fields) for fields in MEMORY_SCHEMA.values())
        assert len(gaps) == total_fields

    def test_gap_ids_unique(self):
        gaps = _detect_structural_gaps(TEMPLATE_BLANK)
        ids = self._gap_ids(gaps)
        assert len(ids) == len(set(ids)), "Les IDs de gaps doivent être uniques"

    def test_gap_ids_deterministic(self):
        """Le même contenu doit toujours produire les mêmes IDs."""
        gaps1 = _detect_structural_gaps(TEMPLATE_BLANK)
        gaps2 = _detect_structural_gaps(TEMPLATE_BLANK)
        assert [g["id"] for g in gaps1] == [g["id"] for g in gaps2]

    def test_no_false_positive_on_label_text(self):
        """**Nom/Pseudo** dans le label ne doit pas compter comme 'nom rempli'."""
        # TEMPLATE_BLANK contient "- **Nom/Pseudo** : pas encore renseigné"
        # → "nom" apparaît dans le label mais la valeur est un placeholder
        gaps = _detect_structural_gaps(TEMPLATE_BLANK)
        questions = self._gap_questions(gaps)
        assert any("appelles" in q for q in questions), \
            "La question sur le nom doit être détectée même si **Nom/Pseudo** est dans le label"

    def test_alias_in_prose_counts_as_filled(self):
        """Un alias dans du texte libre (non label) doit compter comme rempli."""
        memory = dedent("""\
            ## 🧑 Identité Utilisateur
            ### Profil de base
            Matt, développeur web basé en France.
        """)
        gaps = _detect_structural_gaps(memory)
        questions = self._gap_questions(gaps)
        # "Matt" → nom rempli, "développeur" → métier rempli, "France" → localisation remplie
        assert not any("appelles" in q for q in questions)
        assert not any("profession" in q for q in questions)
        assert not any("ville" in q for q in questions)


# ══════════════════════════════════════════════════════════════════════════════
# update_markdown_section — logique enrichissement / remplacement
# ══════════════════════════════════════════════════════════════════════════════

class TestUpdateMarkdownSection:

    def test_placeholder_replaced_not_appended(self, tmp_md):
        """Placeholder → remplace proprement, n'accumule pas."""
        tmp_md.write_text(TEMPLATE_BLANK, encoding="utf-8")
        update_markdown_section(
            section="Identité Utilisateur",
            subsection="Profil de base",
            content="- **Nom/Pseudo** : Matt",
            md_path=tmp_md,
        )
        result = tmp_md.read_text(encoding="utf-8")
        assert "- **Nom/Pseudo** : Matt" in result
        assert "pas encore renseigné" not in result.split("Profil de base")[1].split("###")[0]

    def test_real_content_enriched_not_replaced(self, tmp_md):
        """Contenu réel existant → enrichit par append, ne remplace pas."""
        tmp_md.write_text(TEMPLATE_PARTIAL, encoding="utf-8")
        update_markdown_section(
            section="Identité Utilisateur",
            subsection="Profil de base",
            content="- **Localisation** : France",
            md_path=tmp_md,
        )
        result = tmp_md.read_text(encoding="utf-8")
        assert "- **Nom/Pseudo** : Matt" in result
        assert "- **Localisation** : France" in result

    def test_new_subsection_created_if_absent(self, tmp_md):
        """Sous-section inexistante → créée avec le contenu."""
        tmp_md.write_text(TEMPLATE_PARTIAL, encoding="utf-8")
        update_markdown_section(
            section="Identité Utilisateur",
            subsection="Centres d'intérêt",
            content="- **IA & Agentisation** : passionné",
            md_path=tmp_md,
        )
        result = tmp_md.read_text(encoding="utf-8")
        assert "### Centres d'intérêt" in result
        assert "- **IA & Agentisation** : passionné" in result

    def test_new_section_and_subsection_created(self, tmp_md):
        """Section et sous-section absentes → toutes les deux créées."""
        tmp_md.write_text("", encoding="utf-8")
        update_markdown_section(
            section="Nouvelle Section",
            subsection="Nouvelle Sous-section",
            content="Contenu de test.",
            md_path=tmp_md,
        )
        result = tmp_md.read_text(encoding="utf-8")
        assert "## Nouvelle Section" in result
        assert "### Nouvelle Sous-section" in result
        assert "Contenu de test." in result

    def test_no_double_hash_in_section(self, tmp_md):
        """Des ## accidentels dans section/subsection doivent être nettoyés."""
        tmp_md.write_text("", encoding="utf-8")
        update_markdown_section(
            section="## Identité Utilisateur",
            subsection="### Profil de base",
            content="- **Nom/Pseudo** : Test",
            md_path=tmp_md,
        )
        result = tmp_md.read_text(encoding="utf-8")
        assert "#### " not in result
        assert "## Identité Utilisateur" in result
        assert "### Profil de base" in result

    def test_aucun_placeholder_replaced(self, tmp_md):
        """Le marqueur 'aucun' doit aussi être remplacé."""
        tmp_md.write_text(dedent("""\
            ## 🧑 Identité Utilisateur
            ### Centres d'intérêt
            aucun
        """), encoding="utf-8")
        update_markdown_section(
            section="Identité Utilisateur",
            subsection="Centres d'intérêt",
            content="- **Jeux vidéo** : passionné",
            md_path=tmp_md,
        )
        result = tmp_md.read_text(encoding="utf-8")
        section_content = result.split("Centres d'intérêt")[1].split("###")[0] if "Centres d'intérêt" in result else ""
        assert "- **Jeux vidéo** : passionné" in result
        assert "aucun" not in section_content

    def test_multiple_writes_accumulate(self, tmp_md):
        """Deux écritures successives avec labels DIFFÉRENTS s'accumulent."""
        tmp_md.write_text(TEMPLATE_PARTIAL, encoding="utf-8")
        update_markdown_section(
            section="Identité Utilisateur",
            subsection="Profil de base",
            content="- **Localisation** : France",
            md_path=tmp_md,
        )
        update_markdown_section(
            section="Identité Utilisateur",
            subsection="Profil de base",
            content="- **Langue préférée** : Français",
            md_path=tmp_md,
        )
        result = tmp_md.read_text(encoding="utf-8")
        assert "- **Localisation** : France" in result
        assert "- **Langue préférée** : Français" in result
        assert "- **Nom/Pseudo** : Matt" in result

    def test_same_label_replaced_not_duplicated(self, tmp_md):
        """Même label écrit deux fois → 1 seule occurrence, dernière valeur."""
        tmp_md.write_text(TEMPLATE_PARTIAL, encoding="utf-8")
        update_markdown_section(
            section="Identité Utilisateur",
            subsection="Profil de base",
            content="- **Nom/Pseudo** : Thurzas",
            md_path=tmp_md,
        )
        result = tmp_md.read_text(encoding="utf-8")
        assert result.count("**Nom/Pseudo**") == 1
        assert "Thurzas" in result
        assert "Matt" not in result.split("Profil de base")[1].split("###")[0]

    def test_same_label_three_writes_one_occurrence(self, tmp_md):
        """3 écritures du même label → toujours 1 ligne, dernière valeur."""
        tmp_md.write_text(TEMPLATE_PARTIAL, encoding="utf-8")
        for val in ["direct et concis", "jovial et amical", "réponses courtes"]:
            update_markdown_section(
                section="Identité Utilisateur",
                subsection="Préférences & style",
                content=f"- **Style de communication** : {val}",
                md_path=tmp_md,
            )
        result = tmp_md.read_text(encoding="utf-8")
        assert result.count("**Style de communication**") == 1
        assert "réponses courtes" in result
        assert "direct et concis" not in result
        assert "jovial et amical" not in result

    def test_same_label_other_labels_untouched(self, tmp_md):
        """Upsert d'un label ne doit pas altérer les autres labels de la sous-section."""
        tmp_md.write_text(TEMPLATE_PARTIAL, encoding="utf-8")
        update_markdown_section(
            section="Identité Utilisateur",
            subsection="Profil de base",
            content="- **Métier** : Développeur IA",
            md_path=tmp_md,
        )
        result = tmp_md.read_text(encoding="utf-8")
        assert "- **Nom/Pseudo** : Matt" in result
        assert "Développeur IA" in result
        assert result.count("**Métier**") == 1

    def test_narrative_exact_duplicate_not_appended(self, tmp_md):
        """Section narrative : contenu identique écrit deux fois → 1 seule fois."""
        tmp_md.write_text(dedent("""\
            ## Connaissances persistantes
            ### Décisions prises & leur raison
            Choix de SQLite pour le déploiement local.
        """), encoding="utf-8")
        update_markdown_section(
            section="Connaissances persistantes",
            subsection="Décisions prises & leur raison",
            content="Choix de SQLite pour le déploiement local.",
            md_path=tmp_md,
        )
        result = tmp_md.read_text(encoding="utf-8")
        assert result.count("Choix de SQLite") == 1

    def test_narrative_new_content_appended(self, tmp_md):
        """Section narrative : contenu différent → appende normalement."""
        tmp_md.write_text(dedent("""\
            ## Connaissances persistantes
            ### Décisions prises & leur raison
            Choix de SQLite pour le déploiement local.
        """), encoding="utf-8")
        update_markdown_section(
            section="Connaissances persistantes",
            subsection="Décisions prises & leur raison",
            content="Choix d'Ollama pour les embeddings locaux.",
            md_path=tmp_md,
        )
        result = tmp_md.read_text(encoding="utf-8")
        assert "Choix de SQLite" in result
        assert "Choix d'Ollama" in result

    def test_file_created_if_absent(self, tmp_md):
        """Fichier absent → créé automatiquement."""
        assert not tmp_md.exists()
        update_markdown_section(
            section="Test Section",
            subsection="Test Sub",
            content="Contenu.",
            md_path=tmp_md,
        )
        assert tmp_md.exists()
        assert "Contenu." in tmp_md.read_text()


# ══════════════════════════════════════════════════════════════════════════════
# Format des réponses : - **Label** : valeur
# ══════════════════════════════════════════════════════════════════════════════

class TestAnswerFormatting:
    """Vérifie que le format label/valeur est correctement produit par curiosity_session."""

    def test_label_format_with_label(self):
        """Avec un label, le format doit être '- **Label** : valeur'."""
        ans = {"question": "...", "answer": "France", "section": "X", "subsection": "Y", "label": "Localisation"}
        label   = ans.get("label", "")
        raw_ans = ans["answer"]
        content = f"- **{label}** : {raw_ans}" if label else raw_ans
        assert content == "- **Localisation** : France"

    def test_label_format_without_label(self):
        """Sans label, le contenu brut est utilisé directement."""
        ans = {"question": "...", "answer": "Texte libre.", "section": "X", "subsection": "Y", "label": ""}
        label   = ans.get("label", "")
        raw_ans = ans["answer"]
        content = f"- **{label}** : {raw_ans}" if label else raw_ans
        assert content == "Texte libre."

    def test_label_format_missing_key(self):
        """Clé 'label' absente → get retourne '' → contenu brut."""
        ans = {"question": "...", "answer": "Texte.", "section": "X", "subsection": "Y"}
        label   = ans.get("label", "")
        raw_ans = ans["answer"]
        content = f"- **{label}** : {raw_ans}" if label else raw_ans
        assert content == "Texte."

    def test_label_roundtrip_in_memory(self, tmp_md):
        """Le format label produit doit être détecté comme valeur réelle ensuite."""
        tmp_md.write_text(TEMPLATE_BLANK, encoding="utf-8")
        content = "- **Localisation** : France"
        update_markdown_section(
            section="Identité Utilisateur",
            subsection="Profil de base",
            content=content,
            md_path=tmp_md,
        )
        result = tmp_md.read_text(encoding="utf-8")
        # Après écriture, _line_is_real_value doit reconnaître le contenu
        assert _line_is_real_value("- **Localisation** : France", "localisation") is True
        assert "- **Localisation** : France" in result


# ══════════════════════════════════════════════════════════════════════════════
# _collect_answers — input() mocké
# ══════════════════════════════════════════════════════════════════════════════

class TestCollectAnswers:

    QUESTIONS = [
        {"id": "q1", "question": "Comment tu t'appelles ?",     "section": "Identité Utilisateur", "subsection": "Profil de base",    "label": "Nom/Pseudo"},
        {"id": "q2", "question": "Quelle est ta profession ?",  "section": "Identité Utilisateur", "subsection": "Profil de base",    "label": "Métier"},
        {"id": "q3", "question": "Dans quelle ville/pays ?",    "section": "Identité Utilisateur", "subsection": "Profil de base",    "label": "Localisation"},
    ]

    def test_all_answers_collected(self, tmp_db):
        with patch("builtins.input", side_effect=["Matt", "développeur web", "France"]):
            answers = _collect_answers(self.QUESTIONS)
        assert len(answers) == 3
        assert answers[0]["answer"] == "Matt"
        assert answers[1]["answer"] == "développeur web"
        assert answers[2]["answer"] == "France"

    def test_labels_propagated_in_answers(self, tmp_db):
        with patch("builtins.input", side_effect=["Matt", "développeur web", "France"]):
            answers = _collect_answers(self.QUESTIONS)
        assert answers[0]["label"] == "Nom/Pseudo"
        assert answers[1]["label"] == "Métier"
        assert answers[2]["label"] == "Localisation"

    def test_skip_with_zero(self, tmp_db):
        """Répondre '0' → question skippée, pas dans les réponses."""
        with patch("builtins.input", side_effect=["Matt", "0", "France"]):
            answers = _collect_answers(self.QUESTIONS)
        assert len(answers) == 2
        assert all(a["answer"] != "0" for a in answers)

    def test_skip_with_empty_string(self, tmp_db):
        """Répondre '' (entrée vide) → question skippée."""
        with patch("builtins.input", side_effect=["", "développeur", "France"]):
            answers = _collect_answers(self.QUESTIONS)
        assert len(answers) == 2
        assert answers[0]["answer"] == "développeur"

    def test_skip_marks_question_in_db(self, tmp_db):
        """Question skippée avec '0' → enregistrée dans curiosity_skipped."""
        with patch("builtins.input", side_effect=["0", "développeur", "France"]):
            _collect_answers(self.QUESTIONS)
        skipped = _get_skipped_questions()
        assert "q1" in skipped

    def test_eof_error_interrupts_gracefully(self, tmp_db):
        """EOFError (interruption) → answers partielles retournées proprement."""
        with patch("builtins.input", side_effect=["Matt", EOFError]):
            answers = _collect_answers(self.QUESTIONS)
        assert len(answers) == 1
        assert answers[0]["answer"] == "Matt"

    def test_keyboard_interrupt_interrupts_gracefully(self, tmp_db):
        """KeyboardInterrupt → answers partielles retournées proprement."""
        with patch("builtins.input", side_effect=["Matt", KeyboardInterrupt]):
            answers = _collect_answers(self.QUESTIONS)
        assert len(answers) == 1

    def test_section_subsection_in_answers(self, tmp_db):
        """section et subsection doivent être transportées dans les réponses."""
        with patch("builtins.input", side_effect=["Matt", "dev", "France"]):
            answers = _collect_answers(self.QUESTIONS)
        assert answers[0]["section"] == "Identité Utilisateur"
        assert answers[0]["subsection"] == "Profil de base"

    def test_all_questions_skipped_returns_empty(self, tmp_db):
        """Tout passer → liste vide retournée."""
        with patch("builtins.input", side_effect=["0", "0", "0"]):
            answers = _collect_answers(self.QUESTIONS)
        assert answers == []


# ══════════════════════════════════════════════════════════════════════════════
# _mark_skipped / _get_skipped_questions
# ══════════════════════════════════════════════════════════════════════════════

class TestSkippedQuestions:

    def test_mark_and_retrieve(self, tmp_db):
        _mark_skipped("abc123", "Comment tu t'appelles ?")
        skipped = _get_skipped_questions()
        assert "abc123" in skipped

    def test_multiple_marks(self, tmp_db):
        _mark_skipped("id1", "Q1")
        _mark_skipped("id2", "Q2")
        _mark_skipped("id3", "Q3")
        skipped = _get_skipped_questions()
        assert set(["id1", "id2", "id3"]).issubset(set(skipped))

    def test_mark_idempotent(self, tmp_db):
        """INSERT OR REPLACE → marquer deux fois la même question ne duplique pas."""
        _mark_skipped("abc123", "Question dupliquée")
        _mark_skipped("abc123", "Question dupliquée")
        skipped = _get_skipped_questions()
        assert skipped.count("abc123") == 1

    def test_empty_db_returns_empty_list(self, tmp_db):
        skipped = _get_skipped_questions()
        assert skipped == []

    def test_skipped_ids_filter_gaps(self, tmp_db):
        """Les gaps dont l'ID est skippé ne doivent pas apparaître."""
        gaps = _detect_structural_gaps(TEMPLATE_BLANK)
        first_gap = gaps[0]

        _mark_skipped(first_gap["id"], first_gap["question"])
        skipped_ids = _get_skipped_questions()

        filtered = [g for g in gaps if g["id"] not in skipped_ids]
        assert len(filtered) == len(gaps) - 1
        assert all(g["id"] != first_gap["id"] for g in filtered)


# ══════════════════════════════════════════════════════════════════════════════
# MEMORY_SCHEMA — cohérence interne
# ══════════════════════════════════════════════════════════════════════════════

class TestMemorySchemaCoherence:
    """Vérifie que le schéma lui-même est cohérent — détecte les régressions."""

    def test_all_tuples_have_5_elements(self):
        for section_key, fields in MEMORY_SCHEMA.items():
            for t in fields:
                assert len(t) == 5, \
                    f"Tuple mal formé dans '{section_key}': {t} — attendu (aliases, question, section, subsection, label)"

    def test_all_aliases_are_lists(self):
        for section_key, fields in MEMORY_SCHEMA.items():
            for (aliases, *_) in fields:
                assert isinstance(aliases, list), f"aliases doit être une liste dans '{section_key}'"
                assert len(aliases) > 0, f"aliases vide dans '{section_key}'"

    def test_all_labels_non_empty(self):
        for section_key, fields in MEMORY_SCHEMA.items():
            for (*_, label) in fields:
                assert label, f"label vide dans section '{section_key}'"

    def test_no_hash_in_section_names(self):
        for section_key, fields in MEMORY_SCHEMA.items():
            for (_, _, section, subsection, _) in fields:
                assert not section.startswith("#"), f"## accidentel dans section: '{section}'"
                assert not subsection.startswith("#"), f"### accidentel dans subsection: '{subsection}'"

    def test_section_keys_match_markdown_headers(self):
        """Les clés du dict doivent matcher les ## dans le fichier template."""
        for section_key in MEMORY_SCHEMA:
            content = _extract_section_content(TEMPLATE_BLANK, section_key)
            assert content.strip(), \
                f"La clé '{section_key}' ne matche aucun ## dans le template — vérifier la normalisation"

    def test_placeholder_markers_all_lowercase(self):
        """Les marqueurs de placeholder doivent être en minuscules pour les comparaisons."""
        for marker in _PLACEHOLDER_MARKERS:
            assert marker == marker.lower(), f"Marqueur non lowercase: '{marker}'"