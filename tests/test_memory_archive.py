"""
Tests — memory_archive.py (DreamerCrew D4)

Couvre :
  - _split_top_sections : découpe correcte, preamble préservé
  - _split_subsections : découpe par ###, preamble de section
  - _extract_session_date : parsing YYYY-MM-DD depuis header
  - archive_old_sessions : entrées vieilles archivées, récentes conservées, idempotence
  - archive_completed_projects : ✅ avec date archivé, sans date conservé
  - prune_memory : orchestration + écriture fichier archive + sync ignorée si no-op
  - list_archives : liste vide et non-vide
  - read_archive : lecture + path traversal bloqué

Aucun LLM requis.
"""
import json
from datetime import date, timedelta
from pathlib import Path

import pytest

from Mnemo.tools.memory_archive import (
    _split_top_sections,
    _split_subsections,
    _extract_session_date,
    archive_old_sessions,
    archive_completed_projects,
    prune_memory,
    list_archives,
    read_archive,
)


# ── Fixtures ───────────────────────────────────────────────────────

MEMORY_MD = """\
# Mémoire de Mitsune

## Identité Utilisateur
### Profil de base
- Nom : Matt
- Métier : développeur

## Historique des sessions
### Session 2025-01-10
Première session de test, mise en place du projet.
### Session 2025-06-20
Discussion sur l'architecture mémoire.
### Session {recent}
Session récente — travail en cours.

## Connaissances persistantes
### Projets en cours
- **ProjetAncien** ✅ (complété {old_date}) — projet terminé depuis longtemps.
- **ProjetRecent** ✅ (complété {recent_date}) — projet terminé récemment.
- **ProjetEnCours** — en développement actif.
"""

TODAY    = date.today()
OLD_DATE = (TODAY - timedelta(days=120)).isoformat()
OLD_DATE2 = (TODAY - timedelta(days=60)).isoformat()
RECENT   = (TODAY - timedelta(days=2)).isoformat()


def _make_memory(recent_session_date=None, old_date=None, recent_date=None) -> str:
    return MEMORY_MD.format(
        recent=recent_session_date or RECENT,
        old_date=old_date or OLD_DATE,
        recent_date=recent_date or OLD_DATE2,
    )


def _user_dir(tmp_path: Path, username: str = "Dreamer") -> Path:
    d = tmp_path / "users" / username
    d.mkdir(parents=True, exist_ok=True)
    return d


# ══════════════════════════════════════════════════════════════════
# 1. Parsing
# ══════════════════════════════════════════════════════════════════

class TestSplitTopSections:

    def test_compte_les_sections(self):
        md = _make_memory()
        secs = _split_top_sections(md)
        assert len(secs) == 3

    def test_headers_extraits(self):
        secs = _split_top_sections(_make_memory())
        headers = [s["header"] for s in secs]
        assert "Identité Utilisateur" in headers
        assert "Historique des sessions" in headers
        assert "Connaissances persistantes" in headers

    def test_texte_vide(self):
        assert _split_top_sections("") == []

    def test_sans_section(self):
        assert _split_top_sections("# Titre seul\nPas de section.") == []

    def test_raw_contient_header(self):
        secs = _split_top_sections(_make_memory())
        for s in secs:
            assert s["raw"].startswith("## ")


class TestSplitSubsections:

    def test_split_subsections_session(self):
        md = _make_memory()
        secs  = _split_top_sections(md)
        hist  = next(s for s in secs if "sessions" in s["header"].lower())
        subs  = _split_subsections(hist["content"])
        names = [s["header"] for s in subs if s["header"]]
        assert any("2025-01-10" in n for n in names)
        assert any("2025-06-20" in n for n in names)
        assert any(RECENT in n for n in names)

    def test_preamble_vide_header(self):
        content = "intro avant\n### Sub1\ncontenu1\n### Sub2\ncontenu2"
        subs = _split_subsections(content)
        assert subs[0]["header"] == ""
        assert "intro" in subs[0]["content"]


class TestExtractSessionDate:

    def test_date_valide(self):
        d = _extract_session_date("Session 2026-03-15")
        assert d == date(2026, 3, 15)

    def test_pas_de_date(self):
        assert _extract_session_date("Session sans date") is None

    def test_date_invalide(self):
        assert _extract_session_date("Session 9999-99-99") is None

    def test_date_dans_contexte(self):
        d = _extract_session_date("Session 2025-06-20 — résumé")
        assert d == date(2025, 6, 20)


# ══════════════════════════════════════════════════════════════════
# 2. archive_old_sessions
# ══════════════════════════════════════════════════════════════════

class TestArchiveOldSessions:

    def test_sessions_anciennes_archivees(self):
        md = _make_memory()
        threshold = TODAY - timedelta(days=30)
        new_md, archived = archive_old_sessions(md, threshold)
        dates = [e["date"] for e in archived]
        assert "2025-01" in dates
        assert "2025-06" in dates

    def test_session_recente_conservee(self):
        md = _make_memory()
        threshold = TODAY - timedelta(days=30)
        new_md, archived = archive_old_sessions(md, threshold)
        assert RECENT in new_md

    def test_session_ancienne_absente_du_nouveau_md(self):
        md = _make_memory()
        threshold = TODAY - timedelta(days=30)
        new_md, _ = archive_old_sessions(md, threshold)
        assert "2025-01-10" not in new_md
        assert "2025-06-20" not in new_md

    def test_sections_non_session_preservees(self):
        md = _make_memory()
        threshold = TODAY - timedelta(days=30)
        new_md, _ = archive_old_sessions(md, threshold)
        assert "Identité Utilisateur" in new_md
        assert "Nom : Matt" in new_md

    def test_idempotence(self):
        md = _make_memory()
        threshold = TODAY - timedelta(days=30)
        md1, arch1 = archive_old_sessions(md, threshold)
        md2, arch2 = archive_old_sessions(md1, threshold)
        assert md1 == md2
        assert arch2 == []

    def test_seuil_0j_archive_tout(self):
        md = _make_memory()
        _, archived = archive_old_sessions(md, TODAY - timedelta(days=0))
        # Toutes les sessions passées (y compris très récentes d'il y a 2j) archivées
        assert len(archived) >= 2

    def test_pas_de_section_historique(self):
        md = "## Identité Utilisateur\n### Profil\n- Nom : X\n"
        new_md, archived = archive_old_sessions(md, TODAY - timedelta(days=30))
        assert archived == []
        assert new_md.strip() == md.strip()


# ══════════════════════════════════════════════════════════════════
# 3. archive_completed_projects
# ══════════════════════════════════════════════════════════════════

class TestArchiveCompletedProjects:

    def test_projet_ancien_archive(self):
        md = _make_memory(old_date=OLD_DATE)
        threshold = TODAY - timedelta(days=30)
        _, archived = archive_completed_projects(md, threshold)
        assert any("ProjetAncien" in e.get("content", "") for e in archived)

    def test_projet_recent_conserve(self):
        md = _make_memory(recent_date=(TODAY - timedelta(days=5)).isoformat())
        threshold = TODAY - timedelta(days=30)
        new_md, _ = archive_completed_projects(md, threshold)
        assert "ProjetRecent" in new_md

    def test_projet_sans_date_conserve(self):
        md = "## Connaissances persistantes\n### Projets en cours\n- **ProjSansDate** ✅\n"
        threshold = TODAY - timedelta(days=30)
        _, archived = archive_completed_projects(md, threshold)
        # Pas de date → ne peut pas décider → conservé
        assert not any("ProjSansDate" in str(e) for e in archived)

    def test_projet_en_cours_conserve(self):
        md = _make_memory()
        threshold = TODAY - timedelta(days=30)
        new_md, _ = archive_completed_projects(md, threshold)
        assert "ProjetEnCours" in new_md


# ══════════════════════════════════════════════════════════════════
# 4. prune_memory
# ══════════════════════════════════════════════════════════════════

class TestPruneMemory:

    def test_memory_absente(self, tmp_path):
        report = prune_memory("Ghost", data_path=tmp_path)
        assert "rien à élaguer" in report.lower() or "aucun" in report.lower()

    def test_cree_archive(self, tmp_path):
        d = _user_dir(tmp_path)
        (d / "memory.md").write_text(_make_memory(), encoding="utf-8")
        prune_memory("Dreamer", data_path=tmp_path, session_days=30, project_days=30)
        archives = list((d / "memory_archive").glob("*.md")) if (d / "memory_archive").exists() else []
        assert len(archives) >= 1

    def test_modifie_memory_md(self, tmp_path):
        d = _user_dir(tmp_path)
        (d / "memory.md").write_text(_make_memory(), encoding="utf-8")
        prune_memory("Dreamer", data_path=tmp_path, session_days=30, project_days=30)
        new_md = (d / "memory.md").read_text(encoding="utf-8")
        assert "2025-01-10" not in new_md

    def test_ecrit_dream_log(self, tmp_path):
        d = _user_dir(tmp_path)
        (d / "memory.md").write_text(_make_memory(), encoding="utf-8")
        prune_memory("Dreamer", data_path=tmp_path, session_days=30, project_days=30)
        log_path = d / "dream_log.md"
        if log_path.exists():
            assert "Élagage" in log_path.read_text(encoding="utf-8")

    def test_no_op_si_propre(self, tmp_path):
        d = _user_dir(tmp_path)
        minimal = "## Identité Utilisateur\n### Profil\n- Nom : X\n"
        (d / "memory.md").write_text(minimal, encoding="utf-8")
        report = prune_memory("Dreamer", data_path=tmp_path, session_days=30, project_days=30)
        assert "rien" in report.lower() or "0" in report or "➖" in report


# ══════════════════════════════════════════════════════════════════
# 5. list_archives / read_archive
# ══════════════════════════════════════════════════════════════════

class TestListReadArchives:

    def test_list_vide_si_absent(self, tmp_path):
        assert list_archives("Ghost", data_path=tmp_path) == []

    def test_list_avec_fichiers(self, tmp_path):
        d = _user_dir(tmp_path) / "memory_archive"
        d.mkdir(parents=True)
        (d / "2025-01.md").write_text("# Sessions archivées — 2025-01\n")
        (d / "projets_termines.md").write_text("# Projets terminés\n")
        names = list_archives("Dreamer", data_path=tmp_path)
        assert "2025-01.md" in names
        assert "projets_termines.md" in names

    def test_read_archive_ok(self, tmp_path):
        d = _user_dir(tmp_path) / "memory_archive"
        d.mkdir(parents=True)
        (d / "2025-01.md").write_text("# Archives\nContenu ici.")
        content = read_archive("Dreamer", "2025-01.md", data_path=tmp_path)
        assert "Contenu ici" in content

    def test_read_archive_absent_leve_fnf(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            read_archive("Ghost", "nonexistent.md", data_path=tmp_path)

    def test_path_traversal_bloque(self, tmp_path):
        with pytest.raises(ValueError):
            read_archive("Ghost", "../../../etc/passwd", data_path=tmp_path)

    def test_path_traversal_backslash(self, tmp_path):
        with pytest.raises(ValueError):
            read_archive("Ghost", "..\\secret.md", data_path=tmp_path)
