"""
dreamer_tools.py — Outils Python purs pour DreamerCrew.

D1 : scan_sessions(), resolve_dates()
D2 : detect_duplicates()

Aucun appel LLM ici — tout est déterministe.
Le LLM intervient uniquement dans les agents DreamerCrew (D3).
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any


# ══════════════════════════════════════════════════════════════════
# D1 — Scan des sessions
# ══════════════════════════════════════════════════════════════════

def scan_sessions(
    username: str,
    since_ts: datetime | None = None,
    data_path: Path | None = None,
) -> list[dict]:
    """
    Lit toutes les sessions terminées d'un utilisateur et retourne leur contenu structuré.

    Paramètres :
        username  : nom de l'utilisateur
        since_ts  : ne retourner que les sessions plus récentes que ce timestamp
                    (typiquement = last_dream_ts depuis world_state).
                    None = toutes les sessions.
        data_path : chemin DATA_PATH (défaut : /data ou via get_data_dir())

    Retourne une liste triée chronologiquement de :
    {
        "session_id"      : str,
        "date_iso"        : str (ISO 8601 — mtime du .done),
        "messages"        : [{"role": str, "content": str}, ...],
        "facts_extracted" : list,        # faits déjà consolidés par ConsolidationCrew
        "entities"        : list,        # entités mentionnées
    }
    """
    if data_path is None:
        try:
            from Mnemo.context import get_data_dir
            data_path = get_data_dir()
        except Exception:
            data_path = Path("/data")

    sessions_dir = data_path / "users" / username / "sessions"
    if not sessions_dir.exists():
        return []

    results: list[dict] = []

    for done_file in sessions_dir.glob("*.done"):
        session_id = done_file.stem
        json_file  = done_file.with_suffix(".json")
        if not json_file.exists():
            continue

        # Timestamp = mtime du .done (moment de fin de session)
        mtime = datetime.fromtimestamp(done_file.stat().st_mtime)
        if since_ts and mtime <= since_ts:
            continue

        try:
            raw  = json.loads(json_file.read_text(encoding="utf-8"))
        except Exception:
            continue

        # Normalise le format : dict avec "messages" ou liste directe
        if isinstance(raw, list):
            messages        = raw
            facts_extracted = []
            entities        = []
        else:
            messages        = raw.get("messages", [])
            facts_extracted = raw.get("facts_extracted", [])
            entities        = raw.get("entities_mentioned", [])

        # Filtre les messages vides ou trop courts
        messages = [
            m for m in messages
            if isinstance(m, dict) and m.get("content", "").strip()
        ]

        results.append({
            "session_id":      session_id,
            "date_iso":        mtime.isoformat(),
            "messages":        messages,
            "facts_extracted": facts_extracted,
            "entities":        entities,
        })

    results.sort(key=lambda s: s["date_iso"])
    return results


def extract_text_from_sessions(sessions: list[dict], roles: list[str] | None = None) -> list[dict]:
    """
    Extrait les segments de texte de sessions avec leur contexte temporel.

    Utile pour passer uniquement le texte pertinent au LLM de consolidation.

    Retourne : [{"date_iso": str, "role": str, "content": str}, ...]
    """
    roles_filter = set(roles) if roles else None
    segments: list[dict] = []
    for session in sessions:
        for msg in session["messages"]:
            role    = msg.get("role", "")
            content = msg.get("content", "").strip()
            if not content:
                continue
            if roles_filter and role not in roles_filter:
                continue
            segments.append({
                "date_iso": session["date_iso"],
                "role":     role,
                "content":  content,
            })
    return segments


# ══════════════════════════════════════════════════════════════════
# D1 — Résolution des dates relatives
# ══════════════════════════════════════════════════════════════════

_JOURS_FR = {
    "lundi": 0, "mardi": 1, "mercredi": 2, "jeudi": 3,
    "vendredi": 4, "samedi": 5, "dimanche": 6,
}

# Patterns de date relative en français, du plus spécifique au plus général
_DATE_PATTERNS: list[tuple[str, Any]] = [
    # Avant-hier / après-demain
    (r"\bavant[- ]hier\b",                lambda ref: ref - timedelta(days=2)),
    (r"\bapr[eè]s[- ]demain\b",           lambda ref: ref + timedelta(days=2)),
    # Hier / demain / aujourd'hui
    (r"\bhier\b",                          lambda ref: ref - timedelta(days=1)),
    (r"\bdemain\b",                        lambda ref: ref + timedelta(days=1)),
    (r"\baujourd['\u2019]hui\b",           lambda ref: ref),
    (r"\bce (matin|soir|midi|soir)\b",    lambda ref: ref),
    # Il y a N jours/semaines
    (r"\bil y a (\d+) jour[s]?\b",
     lambda ref, n: ref - timedelta(days=int(n))),
    (r"\bil y a (\d+) semaine[s]?\b",
     lambda ref, n: ref - timedelta(weeks=int(n))),
    (r"\bil y a (\d+) mois\b",
     lambda ref, n: ref - timedelta(days=int(n) * 30)),
    # Dans N jours/semaines
    (r"\bdans (\d+) jour[s]?\b",
     lambda ref, n: ref + timedelta(days=int(n))),
    (r"\bdans (\d+) semaine[s]?\b",
     lambda ref, n: ref + timedelta(weeks=int(n))),
    (r"\bdans (\d+) mois\b",
     lambda ref, n: ref + timedelta(days=int(n) * 30)),
    # La semaine dernière / prochaine
    (r"\bla semaine derni[eè]re\b",        lambda ref: ref - timedelta(weeks=1)),
    (r"\bla semaine prochaine\b",          lambda ref: ref + timedelta(weeks=1)),
    (r"\bcette semaine\b",                 lambda ref: ref),
    # Le mois dernier / prochain
    (r"\ble mois dernier\b",               lambda ref: ref - timedelta(days=30)),
    (r"\ble mois prochain\b",              lambda ref: ref + timedelta(days=30)),
    # Lundi/mardi/... dernier ou prochain
    *[
        (rf"\b{jour} dernier\b",
         lambda ref, j=jour_idx: _nearest_weekday(ref, j, direction=-1))
        for jour, jour_idx in _JOURS_FR.items()
    ],
    *[
        (rf"\b{jour} prochain\b",
         lambda ref, j=jour_idx: _nearest_weekday(ref, j, direction=+1))
        for jour, jour_idx in _JOURS_FR.items()
    ],
]


def _nearest_weekday(ref: datetime, weekday: int, direction: int) -> datetime:
    """
    Retourne la date du jour de semaine le plus proche dans la direction donnée.
    direction = -1 : passé / +1 : futur
    """
    delta = (ref.weekday() - weekday) % 7
    if delta == 0:
        delta = 7  # Si c'est aujourd'hui, prend la semaine d'avant/après
    if direction > 0:
        delta = (weekday - ref.weekday()) % 7 or 7
    return ref + timedelta(days=direction * delta)


def resolve_dates(text: str, session_ts: datetime) -> str:
    """
    Résout les expressions de date relative en dates absolues dans un texte.

    Utilise session_ts comme point de référence temporel.
    Remplace l'expression par sa date ISO (ex: "hier" → "2026-03-27 [hier]").
    Le texte original est préservé entre crochets pour l'auditabilité.

    Exemple :
        resolve_dates("hier on a fixé le bug", datetime(2026, 3, 28))
        → "2026-03-27 [hier] on a fixé le bug"
    """
    result = text

    for pattern, resolver in _DATE_PATTERNS:
        def _replacer(m: re.Match, res=resolver, ref=session_ts) -> str:
            try:
                groups = m.groups()
                if groups:
                    resolved_dt: datetime = res(ref, *groups)
                else:
                    resolved_dt = res(ref)
                date_str = resolved_dt.strftime("%Y-%m-%d")
                return f"{date_str} [{m.group(0)}]"
            except Exception:
                return m.group(0)  # En cas d'erreur, ne pas modifier

        result = re.sub(pattern, _replacer, result, flags=re.IGNORECASE)

    return result


def resolve_sessions_dates(sessions: list[dict]) -> list[dict]:
    """
    Applique resolve_dates sur tous les messages de toutes les sessions.
    Modifie les sessions in-place et retourne la liste.
    """
    for session in sessions:
        ref = datetime.fromisoformat(session["date_iso"])
        for msg in session["messages"]:
            content = msg.get("content", "")
            if content:
                msg["content"] = resolve_dates(content, ref)
    return sessions


# ══════════════════════════════════════════════════════════════════
# D2 — Détection de doublons
# ══════════════════════════════════════════════════════════════════

def _line_hash(line: str) -> str:
    """Hash MD5 d'une ligne normalisée (minuscules, espaces condensés)."""
    normalized = re.sub(r"\s+", " ", line.strip().lower())
    return hashlib.md5(normalized.encode("utf-8")).hexdigest()


def detect_exact_duplicates(memory_md: str) -> list[dict]:
    """
    Détecte les lignes exactement dupliquées dans memory.md (après normalisation).

    Retourne une liste de groupes :
    [{"hash": str, "content": str, "occurrences": [int, ...]}, ...]
    où occurrences est la liste des numéros de ligne (1-based).
    """
    seen: dict[str, list[int]] = {}
    for i, line in enumerate(memory_md.splitlines(), start=1):
        stripped = line.strip()
        if len(stripped) < 8:  # ignore les lignes trop courtes (séparateurs, titres vides)
            continue
        if stripped.startswith("#"):  # ignore les titres de section
            continue
        h = _line_hash(stripped)
        seen.setdefault(h, []).append(i)

    return [
        {"hash": h, "content": memory_md.splitlines()[lines[0] - 1].strip(), "occurrences": lines}
        for h, lines in seen.items()
        if len(lines) > 1
    ]


def detect_dead_references(memory_md: str, existing_paths: set[str]) -> list[dict]:
    """
    Détecte les références à des fichiers/fonctions qui n'existent plus.

    existing_paths : ensemble de chemins relatifs réels (depuis project_index ou list_files).

    Retourne une liste de :
    [{"line_no": int, "content": str, "dead_ref": str}, ...]
    """
    dead: list[dict] = []
    # Patterns de référence à du code : `src/foo.py`, `tools/bar.py`, `def baz`
    ref_patterns = [
        re.compile(r"`(src/[\w/.\-]+\.\w+)`"),
        re.compile(r"`(tools/[\w/.\-]+\.\w+)`"),
        re.compile(r"`(Mnemo/[\w/.\-]+\.\w+)`"),
    ]
    for i, line in enumerate(memory_md.splitlines(), start=1):
        for pat in ref_patterns:
            for m in pat.finditer(line):
                ref = m.group(1)
                # Vérifier si ce chemin existe dans les paths connus
                if not any(ref in p or p.endswith(ref) for p in existing_paths):
                    dead.append({"line_no": i, "content": line.strip(), "dead_ref": ref})
    return dead


def build_dedup_report(memory_md: str, existing_paths: set[str] | None = None) -> dict:
    """
    Construit un rapport complet : doublons exacts + références mortes.

    Retourne :
    {
        "exact_duplicates": [...],
        "dead_references":  [...],
        "total_lines":      int,
        "duplicate_count":  int,
        "dead_ref_count":   int,
    }
    """
    exact = detect_exact_duplicates(memory_md)
    dead  = detect_dead_references(memory_md, existing_paths or set())
    lines = memory_md.splitlines()
    return {
        "exact_duplicates": exact,
        "dead_references":  dead,
        "total_lines":      len(lines),
        "duplicate_count":  sum(len(g["occurrences"]) - 1 for g in exact),
        "dead_ref_count":   len(dead),
    }