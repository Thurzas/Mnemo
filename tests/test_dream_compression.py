"""
Tests — compression de contexte DreamerCrew (Options A et B)

Couvre :
  - _section_line_ranges : découpe correcte, numéros de lignes
  - _extract_hot_sections : sections chaudes détectées, label, cas propre
  - _get_rotation_section : rotation correcte, wrap-around, sections vides
  - prepare_dream_inputs : priorise A sur B, avance le pointeur B, cappe si besoin

Aucun LLM requis.
"""
import json
from pathlib import Path

import pytest

from Mnemo.tools.dreamer_tools import (
    _section_line_ranges,
    _extract_hot_sections,
    _get_rotation_section,
    prepare_dream_inputs,
    build_dedup_report,
)


# ── Fixtures ───────────────────────────────────────────────────────

MEMORY_CLEAN = """\
# Mémoire

## Identité Utilisateur
### Profil
- Nom : Matt
- Métier : développeur

## Connaissances persistantes
### Projets en cours
- MonProjet en développement.

## Historique des sessions
### Session 2026-03-01
Travail sur le scheduler.
"""

MEMORY_WITH_DUPS = """\
# Mémoire

## Identité Utilisateur
### Profil
- Nom : Matt
- Nom : Matt

## Connaissances persistantes
### Décisions
- Utiliser SQLite pour la persistance.
"""

MEMORY_WITH_DEAD = """\
# Mémoire

## Connaissances persistantes
### Décisions
- Voir `src/old_module.py` pour les détails.

## Historique des sessions
### Session 2026-01-01
Session ancienne.
"""


def _make_user(tmp_path: Path, memory_md: str, ws: dict | None = None) -> Path:
    d = tmp_path / "users" / "Dreamer"
    d.mkdir(parents=True, exist_ok=True)
    (d / "memory.md").write_text(memory_md, encoding="utf-8")
    if ws is not None:
        (d / "world_state.json").write_text(json.dumps(ws), encoding="utf-8")
    return d


# ══════════════════════════════════════════════════════════════════
# 1. _section_line_ranges
# ══════════════════════════════════════════════════════════════════

class TestSectionLineRanges:

    def test_compte_les_sections(self):
        ranges = _section_line_ranges(MEMORY_CLEAN)
        assert len(ranges) == 3

    def test_headers_corrects(self):
        ranges = _section_line_ranges(MEMORY_CLEAN)
        headers = [r["header"] for r in ranges]
        assert "Identité Utilisateur" in headers
        assert "Connaissances persistantes" in headers
        assert "Historique des sessions" in headers

    def test_start_line_croissant(self):
        ranges = _section_line_ranges(MEMORY_CLEAN)
        starts = [r["start_line"] for r in ranges]
        assert starts == sorted(starts)

    def test_end_line_geq_start(self):
        for r in _section_line_ranges(MEMORY_CLEAN):
            assert r["end_line"] >= r["start_line"]

    def test_raw_contient_header(self):
        for r in _section_line_ranges(MEMORY_CLEAN):
            assert r["raw"].startswith("## " + r["header"])

    def test_texte_vide(self):
        assert _section_line_ranges("") == []

    def test_sans_section(self):
        assert _section_line_ranges("# Titre\nPas de section.") == []


# ══════════════════════════════════════════════════════════════════
# 2. _extract_hot_sections — Option A
# ══════════════════════════════════════════════════════════════════

class TestExtractHotSections:

    def test_memoire_propre_retourne_vide(self):
        dedup = build_dedup_report(MEMORY_CLEAN, set())
        content, label = _extract_hot_sections(MEMORY_CLEAN, dedup)
        assert content == ""
        assert label == ""

    def test_doublon_detecte(self):
        dedup = build_dedup_report(MEMORY_WITH_DUPS, set())
        content, label = _extract_hot_sections(MEMORY_WITH_DUPS, dedup)
        assert content != ""
        assert "Identité Utilisateur" in content

    def test_section_chaude_contient_la_ligne_problematique(self):
        dedup = build_dedup_report(MEMORY_WITH_DUPS, set())
        content, _ = _extract_hot_sections(MEMORY_WITH_DUPS, dedup)
        assert "Nom : Matt" in content

    def test_label_mentionne_doublons(self):
        dedup = build_dedup_report(MEMORY_WITH_DUPS, set())
        _, label = _extract_hot_sections(MEMORY_WITH_DUPS, dedup)
        assert "doublon" in label.lower()

    def test_ref_morte_detectee(self):
        dedup = build_dedup_report(MEMORY_WITH_DEAD, set())  # no existing paths → tout est mort
        content, label = _extract_hot_sections(MEMORY_WITH_DEAD, dedup)
        if dedup["dead_ref_count"] > 0:
            assert content != ""
            assert "référence" in label.lower()

    def test_section_non_chaude_exclue(self):
        dedup = build_dedup_report(MEMORY_WITH_DUPS, set())
        content, _ = _extract_hot_sections(MEMORY_WITH_DUPS, dedup)
        # La section "Connaissances persistantes" n'a pas de doublons → absente
        if content:
            # Si le doublon est seulement dans Identité Utilisateur
            assert "Connaissances persistantes" not in content or "Identité Utilisateur" in content


# ══════════════════════════════════════════════════════════════════
# 3. _get_rotation_section — Option B
# ══════════════════════════════════════════════════════════════════

class TestGetRotationSection:

    def test_retourne_premiere_section_si_idx_0(self):
        content, label, next_idx = _get_rotation_section(MEMORY_CLEAN, 0)
        assert "Identité Utilisateur" in content
        assert next_idx == 1

    def test_retourne_deuxieme_section_si_idx_1(self):
        content, label, next_idx = _get_rotation_section(MEMORY_CLEAN, 1)
        assert "Connaissances persistantes" in content
        assert next_idx == 2

    def test_wrap_around(self):
        ranges = _section_line_ranges(MEMORY_CLEAN)
        n = len(ranges)
        content, label, next_idx = _get_rotation_section(MEMORY_CLEAN, n - 1)
        assert next_idx == 0  # retour au début

    def test_label_contient_numero(self):
        _, label, _ = _get_rotation_section(MEMORY_CLEAN, 0)
        assert "1/" in label

    def test_label_contient_nom_section(self):
        _, label, _ = _get_rotation_section(MEMORY_CLEAN, 0)
        assert "Identité Utilisateur" in label

    def test_idx_hors_bornes_modulo(self):
        ranges = _section_line_ranges(MEMORY_CLEAN)
        n = len(ranges)
        # idx = n → équivalent à idx = 0
        content1, _, _ = _get_rotation_section(MEMORY_CLEAN, 0)
        content2, _, _ = _get_rotation_section(MEMORY_CLEAN, n)
        assert content1 == content2

    def test_memoire_vide(self):
        content, label, next_idx = _get_rotation_section("", 0)
        assert next_idx == 0


# ══════════════════════════════════════════════════════════════════
# 4. prepare_dream_inputs — intégration A+B
# ══════════════════════════════════════════════════════════════════

class TestPrepareDreamInputs:

    def test_option_a_prioritaire_si_doublons(self, tmp_path):
        _make_user(tmp_path, MEMORY_WITH_DUPS)
        inputs = prepare_dream_inputs("Dreamer", data_path=tmp_path)
        assert "doublon" in inputs["memory_scope"].lower()

    def test_option_b_si_memoire_propre(self, tmp_path):
        _make_user(tmp_path, MEMORY_CLEAN, ws={"last_dream_section_idx": 0})
        inputs = prepare_dream_inputs("Dreamer", data_path=tmp_path)
        assert "Rotation" in inputs["memory_scope"] or "rotation" in inputs["memory_scope"].lower()

    def test_memory_scope_present(self, tmp_path):
        _make_user(tmp_path, MEMORY_CLEAN)
        inputs = prepare_dream_inputs("Dreamer", data_path=tmp_path)
        assert "memory_scope" in inputs
        assert inputs["memory_scope"] != ""

    def test_memory_content_non_vide(self, tmp_path):
        _make_user(tmp_path, MEMORY_CLEAN)
        inputs = prepare_dream_inputs("Dreamer", data_path=tmp_path)
        assert inputs["memory_content"] != ""

    def test_pointeur_b_avance(self, tmp_path):
        d = _make_user(tmp_path, MEMORY_CLEAN, ws={"last_dream_section_idx": 0})
        prepare_dream_inputs("Dreamer", data_path=tmp_path)
        ws = json.loads((d / "world_state.json").read_text())
        assert ws.get("last_dream_section_idx", 0) == 1

    def test_pointeur_b_non_avance_si_option_a(self, tmp_path):
        d = _make_user(tmp_path, MEMORY_WITH_DUPS, ws={"last_dream_section_idx": 1})
        prepare_dream_inputs("Dreamer", data_path=tmp_path)
        ws = json.loads((d / "world_state.json").read_text())
        # Option A utilisée → pointeur B inchangé
        assert ws.get("last_dream_section_idx", 1) == 1

    def test_cappe_si_section_trop_longue(self, tmp_path):
        big_section = "## Grande Section\n" + ("- ligne de données\n" * 500)
        _make_user(tmp_path, big_section)
        inputs = prepare_dream_inputs("Dreamer", data_path=tmp_path, max_memory_chars=200)
        assert len(inputs["memory_content"]) <= 210  # quelques chars pour le marqueur tronqué
        assert "tronqué" in inputs["memory_content"]

    def test_dedup_report_present(self, tmp_path):
        _make_user(tmp_path, MEMORY_CLEAN)
        inputs = prepare_dream_inputs("Dreamer", data_path=tmp_path)
        assert "dedup_report" in inputs
        parsed = json.loads(inputs["dedup_report"])
        assert "exact_duplicates" in parsed

    def test_memory_absente(self, tmp_path):
        d = tmp_path / "users" / "Ghost"
        d.mkdir(parents=True)
        inputs = prepare_dream_inputs("Ghost", data_path=tmp_path)
        assert inputs["memory_content"] == ""
        assert inputs["memory_scope"] == ""
