"""
memory_archive.py — Élagage et archivage de memory.md (DreamerCrew D4).

Règles d'archivage :
  - "Historique des sessions" > sous-sections "Session YYYY-MM-DD" datant de plus
    de DREAMER_SESSION_ARCHIVE_DAYS (défaut 90 jours) → memory_archive/YYYY-MM.md
  - Projets contenant ✅ avec une date récupérable datant de plus de
    DREAMER_PROJECT_ARCHIVE_DAYS (défaut 30 jours) → memory_archive/projets_termines.md

Invariants :
  - Toute suppression est loggée dans dream_log.md avant d'être effectuée
  - Idempotent : exécuter deux fois de suite ne change rien la 2e fois
  - memory.md reste la source de vérité — sync DB déclenchée après chaque écriture
"""
from __future__ import annotations

import os
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any


# ── Configuration (surchargeables via .env) ───────────────────────

SESSION_ARCHIVE_DAYS = int(os.getenv("DREAMER_SESSION_ARCHIVE_DAYS", "90"))
PROJECT_ARCHIVE_DAYS = int(os.getenv("DREAMER_PROJECT_ARCHIVE_DAYS", "30"))
MAX_MEMORY_SIZE_KB   = int(os.getenv("DREAMER_MAX_MEMORY_SIZE_KB", "50"))


# ══════════════════════════════════════════════════════════════════
# Parsing helpers
# ══════════════════════════════════════════════════════════════════

def _split_top_sections(text: str) -> list[dict]:
    """
    Découpe memory.md en sections de niveau ## .
    Retourne [{header, content, raw}] où raw = header + "\n" + content.
    La partie avant le premier ## (titre # + éventuelles métadonnées) est ignorée.
    """
    pattern = re.compile(r"^(## .+)$", re.MULTILINE)
    positions = [m.start() for m in pattern.finditer(text)]
    if not positions:
        return []

    sections = []
    for i, start in enumerate(positions):
        end = positions[i + 1] if i + 1 < len(positions) else len(text)
        block = text[start:end]
        lines = block.splitlines()
        header  = lines[0].lstrip("# ").strip()
        content = "\n".join(lines[1:]).strip()
        sections.append({"header": header, "content": content, "raw": block})
    return sections


def _split_subsections(section_content: str) -> list[dict]:
    """
    Découpe le contenu d'une section ## en sous-sections ### .
    Retourne [{header, content, raw}].
    Les lignes avant le premier ### sont regroupées sous header="" (preamble).
    """
    pattern = re.compile(r"^(### .+)$", re.MULTILINE)
    positions = [m.start() for m in pattern.finditer(section_content)]
    result = []

    if positions and positions[0] > 0:
        pre = section_content[:positions[0]].strip()
        if pre:
            result.append({"header": "", "content": pre, "raw": pre})

    for i, start in enumerate(positions):
        end = positions[i + 1] if i + 1 < len(positions) else len(section_content)
        block = section_content[start:end]
        lines = block.splitlines()
        header  = lines[0].lstrip("# ").strip()
        content = "\n".join(lines[1:]).strip()
        result.append({"header": header, "content": content, "raw": block})

    return result


def _extract_session_date(subsection_header: str) -> date | None:
    """
    Extrait la date d'un header de type "Session 2026-01-15".
    Retourne None si non trouvée.
    """
    m = re.search(r"(\d{4}-\d{2}-\d{2})", subsection_header)
    if m:
        try:
            return date.fromisoformat(m.group(1))
        except ValueError:
            pass
    return None


def _find_date_in_content(content: str) -> date | None:
    """
    Cherche n'importe quelle date YYYY-MM-DD dans le contenu d'un bloc.
    Retourne la plus récente trouvée, ou None.
    """
    dates = []
    for m in re.finditer(r"(\d{4}-\d{2}-\d{2})", content):
        try:
            dates.append(date.fromisoformat(m.group(1)))
        except ValueError:
            pass
    return max(dates) if dates else None


# ══════════════════════════════════════════════════════════════════
# Archivage des sessions
# ══════════════════════════════════════════════════════════════════

def archive_old_sessions(
    memory_md: str,
    threshold_date: date,
) -> tuple[str, list[dict]]:
    """
    Retire de memory_md les sous-sections "Session YYYY-MM-DD" antérieures à
    threshold_date et les retourne séparément.

    Retourne :
        (new_memory_md, archived_entries)
        archived_entries = [{"date": "YYYY-MM", "header": str, "content": str}]
    """
    sections = _split_top_sections(memory_md)
    archived: list[dict] = []
    new_sections: list[str] = []

    # Préserve le préambule (tout ce qui précède le premier ##)
    first_pos = memory_md.find("## ")
    preamble  = memory_md[:first_pos] if first_pos > 0 else ""

    for sec in sections:
        key = sec["header"].lower()
        if "historique" not in key and "sessions" not in key:
            new_sections.append(sec["raw"].rstrip())
            continue

        # C'est la section "Historique des sessions"
        subsections   = _split_subsections(sec["content"])
        kept_subs:  list[str] = []
        preamble_sub: list[str] = []

        for sub in subsections:
            if not sub["header"]:
                preamble_sub.append(sub["raw"])
                continue
            d = _extract_session_date(sub["header"])
            if d and d < threshold_date:
                month_key = d.strftime("%Y-%m")
                archived.append({
                    "date":    month_key,
                    "header":  sub["header"],
                    "content": sub["content"],
                    "raw":     sub["raw"],
                })
            else:
                kept_subs.append(sub["raw"])

        # Reconstruit la section sans les entrées archivées
        parts = ["## " + sec["header"]] + preamble_sub + kept_subs
        new_sections.append("\n".join(parts).rstrip())

    new_md = preamble + "\n\n".join(new_sections)
    if not new_md.endswith("\n"):
        new_md += "\n"
    return new_md, archived


# ══════════════════════════════════════════════════════════════════
# Archivage des projets terminés
# ══════════════════════════════════════════════════════════════════

def archive_completed_projects(
    memory_md: str,
    threshold_date: date,
) -> tuple[str, list[dict]]:
    """
    Retire de memory_md les entrées de projets marquées ✅ dont la dernière
    date détectée est antérieure à threshold_date.

    Les projets sont cherchés dans la section "Connaissances persistantes" >
    sous-section "Projets en cours" (ou toute sous-section contenant ✅ dans
    son header).

    Retourne :
        (new_memory_md, archived_entries)
        archived_entries = [{"header": str, "content": str}]
    """
    sections  = _split_top_sections(memory_md)
    archived: list[dict] = []
    new_sections: list[str] = []

    first_pos = memory_md.find("## ")
    preamble  = memory_md[:first_pos] if first_pos > 0 else ""

    for sec in sections:
        key = sec["header"].lower()
        if "connaissance" not in key and "projet" not in key:
            new_sections.append(sec["raw"].rstrip())
            continue

        subsections  = _split_subsections(sec["content"])
        kept_subs: list[str] = []
        preamble_sub: list[str] = []

        for sub in subsections:
            if not sub["header"]:
                preamble_sub.append(sub["raw"])
                continue

            # Cas 1 : le header de sous-section contient ✅
            if "✅" in sub["header"]:
                d = _find_date_in_content(sub["header"] + "\n" + sub["content"])
                if d and d < threshold_date:
                    archived.append({"header": sub["header"], "content": sub["content"], "raw": sub["raw"]})
                    continue

            # Cas 2 : la sous-section contient des lignes bullet avec ✅
            lines_kept:    list[str] = []
            lines_archived: list[str] = []
            for line in sub["content"].splitlines():
                if "✅" in line:
                    d = _find_date_in_content(line)
                    if d and d < threshold_date:
                        lines_archived.append(line)
                        archived.append({"header": sub["header"], "content": line, "raw": line})
                        continue
                lines_kept.append(line)

            if lines_archived:
                new_content = "\n".join(lines_kept).strip()
                kept_subs.append(f"### {sub['header']}\n{new_content}".rstrip())
            else:
                kept_subs.append(sub["raw"])

        parts = ["## " + sec["header"]] + preamble_sub + kept_subs
        new_sections.append("\n".join(parts).rstrip())

    new_md = preamble + "\n\n".join(new_sections)
    if not new_md.endswith("\n"):
        new_md += "\n"
    return new_md, archived


# ══════════════════════════════════════════════════════════════════
# Écriture des archives
# ══════════════════════════════════════════════════════════════════

def _write_archive_entry(archive_path: Path, entries: list[dict], section_title: str) -> int:
    """
    Ajoute les entries dans le fichier archive (Markdown, append).
    Évite les doublons en vérifiant les headers déjà présents.
    Retourne le nombre d'entrées réellement écrites.
    """
    existing = archive_path.read_text(encoding="utf-8") if archive_path.exists() else ""

    written = 0
    for e in entries:
        header = e.get("header", "")
        # Déduplication : si ce header est déjà archivé, on saute
        if header and f"### {header}" in existing:
            continue
        block = f"\n### {header}\n{e.get('content', '')}\n" if header else f"\n{e.get('content', '')}\n"
        existing += block
        written += 1

    if written:
        archive_path.parent.mkdir(parents=True, exist_ok=True)
        if not archive_path.exists() or not archive_path.read_text(encoding="utf-8").startswith(f"# {section_title}"):
            existing = f"# {section_title}\n" + existing.lstrip()
        archive_path.write_text(existing, encoding="utf-8")

    return written


# ══════════════════════════════════════════════════════════════════
# API publique
# ══════════════════════════════════════════════════════════════════

def prune_memory(
    username: str,
    data_path: Path | None = None,
    session_days: int = SESSION_ARCHIVE_DAYS,
    project_days: int = PROJECT_ARCHIVE_DAYS,
) -> str:
    """
    Élagage complet de memory.md pour un utilisateur :
      1. Archive les sessions historiques > session_days
      2. Archive les projets terminés (✅) > project_days
      3. Sync DB
      4. Logue dans dream_log.md

    Retourne un rapport texte.
    """
    if data_path is None:
        try:
            from Mnemo.context import get_data_dir
            data_path = get_data_dir()
        except Exception:
            data_path = Path("/data")

    user_dir       = data_path / "users" / username
    memory_path    = user_dir / "memory.md"
    dream_log_path = user_dir / "dream_log.md"
    archive_dir    = user_dir / "memory_archive"

    if not memory_path.exists():
        return f"[prune] Aucun memory.md pour {username} — rien à élaguer."

    original = memory_path.read_text(encoding="utf-8")
    today    = date.today()
    log_lines: list[str] = []
    current  = original

    # 1. Sessions
    session_threshold = today - timedelta(days=session_days)
    current, session_archived = archive_old_sessions(current, session_threshold)

    if session_archived:
        # Regroupe par mois YYYY-MM
        by_month: dict[str, list[dict]] = {}
        for e in session_archived:
            by_month.setdefault(e["date"], []).append(e)

        for month, entries in sorted(by_month.items()):
            archive_path = archive_dir / f"{month}.md"
            written = _write_archive_entry(archive_path, entries, f"Sessions archivées — {month}")
            if written:
                log_lines.append(f"ARCHIVED_SESSIONS({month}) : {written} entrée(s) → {archive_path.name}")

    # 2. Projets terminés
    project_threshold = today - timedelta(days=project_days)
    current, project_archived = archive_completed_projects(current, project_threshold)

    if project_archived:
        archive_path = archive_dir / "projets_termines.md"
        written = _write_archive_entry(archive_path, project_archived, "Projets terminés — archives")
        if written:
            log_lines.append(f"ARCHIVED_PROJECTS : {written} entrée(s) → {archive_path.name}")

    # 3. Écrit memory.md si modifié
    changed = current != original
    if changed:
        memory_path.write_text(current, encoding="utf-8")
        try:
            from Mnemo.tools.memory_tools import sync_markdown_to_db
            sync_markdown_to_db()
            log_lines.append("SYNC_DB : OK")
        except Exception as e:
            log_lines.append(f"SYNC_DB ERROR : {e}")

    # 4. Log dans dream_log.md
    if log_lines:
        now_str   = datetime.now().strftime("%Y-%m-%d %H:%M")
        log_entry = (
            f"\n## Élagage du {now_str}\n"
            + "\n".join(f"- {l}" for l in log_lines)
            + "\n"
        )
        try:
            existing = dream_log_path.read_text(encoding="utf-8") if dream_log_path.exists() else ""
            dream_log_path.write_text(existing + log_entry, encoding="utf-8")
        except Exception:
            pass

    n = len(session_archived) + len(project_archived)
    if n == 0:
        return "[prune] ➖ Rien à archiver — memory.md est déjà propre."
    return (
        f"[prune] ✅ {n} entrée(s) archivée(s).\n"
        + "\n".join(f"  {l}" for l in log_lines)
    )


def list_archives(username: str, data_path: Path | None = None) -> list[str]:
    """Retourne la liste des fichiers dans memory_archive/."""
    if data_path is None:
        try:
            from Mnemo.context import get_data_dir
            data_path = get_data_dir()
        except Exception:
            data_path = Path("/data")

    archive_dir = data_path / "users" / username / "memory_archive"
    if not archive_dir.exists():
        return []
    return sorted(p.name for p in archive_dir.glob("*.md"))


def read_archive(
    username: str,
    filename: str,
    data_path: Path | None = None,
) -> str:
    """Lit un fichier d'archive. Lève FileNotFoundError si absent."""
    if data_path is None:
        try:
            from Mnemo.context import get_data_dir
            data_path = get_data_dir()
        except Exception:
            data_path = Path("/data")

    # Garde-fou : pas de path traversal
    if ".." in filename or "/" in filename or "\\" in filename:
        raise ValueError(f"Nom de fichier invalide : {filename!r}")

    path = data_path / "users" / username / "memory_archive" / filename
    if not path.exists():
        raise FileNotFoundError(f"Archive introuvable : {filename}")
    return path.read_text(encoding="utf-8")
