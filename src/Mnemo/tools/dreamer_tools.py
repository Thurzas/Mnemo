"""
dreamer_tools.py — Outils Python purs pour DreamerCrew.

D1 : scan_sessions(), resolve_dates()
D2 : detect_duplicates()
D3 : apply_patches(), run_dream_cycle(), ApplyDreamPatchesTool, prepare_dream_inputs()

Aucun appel LLM ici — tout est déterministe.
Le LLM intervient uniquement dans les agents DreamerCrew (D3).
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Type

from crewai.tools import BaseTool
from pydantic import BaseModel, Field


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
        "facts_extracted" : list,
        "entities"        : list,
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

        mtime = datetime.fromtimestamp(done_file.stat().st_mtime)
        if since_ts and mtime <= since_ts:
            continue

        try:
            raw = json.loads(json_file.read_text(encoding="utf-8"))
        except Exception:
            continue

        if isinstance(raw, list):
            messages        = raw
            facts_extracted = []
            entities        = []
        else:
            messages        = raw.get("messages", [])
            facts_extracted = raw.get("facts_extracted", [])
            entities        = raw.get("entities_mentioned", [])

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

_DATE_PATTERNS: list[tuple[str, Any]] = [
    (r"\bavant[- ]hier\b",              lambda ref: ref - timedelta(days=2)),
    (r"\bapr[eè]s[- ]demain\b",         lambda ref: ref + timedelta(days=2)),
    (r"\bhier\b",                        lambda ref: ref - timedelta(days=1)),
    (r"\bdemain\b",                      lambda ref: ref + timedelta(days=1)),
    (r"\baujourd['\u2019]hui\b",         lambda ref: ref),
    (r"\bce (matin|soir|midi)\b",        lambda ref: ref),
    (r"\bil y a (\d+) jour[s]?\b",       lambda ref, n: ref - timedelta(days=int(n))),
    (r"\bil y a (\d+) semaine[s]?\b",    lambda ref, n: ref - timedelta(weeks=int(n))),
    (r"\bil y a (\d+) mois\b",           lambda ref, n: ref - timedelta(days=int(n) * 30)),
    (r"\bdans (\d+) jour[s]?\b",         lambda ref, n: ref + timedelta(days=int(n))),
    (r"\bdans (\d+) semaine[s]?\b",      lambda ref, n: ref + timedelta(weeks=int(n))),
    (r"\bdans (\d+) mois\b",             lambda ref, n: ref + timedelta(days=int(n) * 30)),
    (r"\bla semaine derni[eè]re\b",      lambda ref: ref - timedelta(weeks=1)),
    (r"\bla semaine prochaine\b",        lambda ref: ref + timedelta(weeks=1)),
    (r"\bcette semaine\b",               lambda ref: ref),
    (r"\ble mois dernier\b",             lambda ref: ref - timedelta(days=30)),
    (r"\ble mois prochain\b",            lambda ref: ref + timedelta(days=30)),
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
    delta = (ref.weekday() - weekday) % 7
    if delta == 0:
        delta = 7
    if direction > 0:
        delta = (weekday - ref.weekday()) % 7 or 7
    return ref + timedelta(days=direction * delta)


def resolve_dates(text: str, session_ts: datetime) -> str:
    """
    Résout les expressions de date relative en dates absolues ISO.
    Utilise session_ts comme référence. Préserve l'original entre crochets.

    Ex: resolve_dates("hier on a fixé le bug", datetime(2026, 3, 28))
        → "2026-03-27 [hier] on a fixé le bug"
    """
    result = text
    for pattern, resolver in _DATE_PATTERNS:
        def _replacer(m: re.Match, res=resolver, ref=session_ts) -> str:
            try:
                groups = m.groups()
                resolved_dt: datetime = res(ref, *groups) if groups else res(ref)
                return f"{resolved_dt.strftime('%Y-%m-%d')} [{m.group(0)}]"
            except Exception:
                return m.group(0)
        result = re.sub(pattern, _replacer, result, flags=re.IGNORECASE)
    return result


def resolve_sessions_dates(sessions: list[dict]) -> list[dict]:
    """Applique resolve_dates sur tous les messages de toutes les sessions (in-place)."""
    for session in sessions:
        ref = datetime.fromisoformat(session["date_iso"])
        for msg in session["messages"]:
            if msg.get("content"):
                msg["content"] = resolve_dates(msg["content"], ref)
    return sessions


# ══════════════════════════════════════════════════════════════════
# D2 — Détection de doublons et références mortes
# ══════════════════════════════════════════════════════════════════

def _line_hash(line: str) -> str:
    normalized = re.sub(r"\s+", " ", line.strip().lower())
    return hashlib.md5(normalized.encode("utf-8")).hexdigest()


def detect_exact_duplicates(memory_md: str) -> list[dict]:
    """
    Détecte les lignes exactement dupliquées (après normalisation).
    Retourne [{hash, content, occurrences: [line_no, ...]}, ...].
    """
    seen: dict[str, list[int]] = {}
    for i, line in enumerate(memory_md.splitlines(), start=1):
        stripped = line.strip()
        if len(stripped) < 8 or stripped.startswith("#"):
            continue
        h = _line_hash(stripped)
        seen.setdefault(h, []).append(i)

    all_lines = memory_md.splitlines()
    return [
        {"hash": h, "content": all_lines[lines[0] - 1].strip(), "occurrences": lines}
        for h, lines in seen.items()
        if len(lines) > 1
    ]


def detect_dead_references(memory_md: str, existing_paths: set[str]) -> list[dict]:
    """
    Détecte les références à des fichiers qui n'existent plus dans le codebase.
    Retourne [{line_no, content, dead_ref}, ...].
    """
    dead: list[dict] = []
    ref_patterns = [
        re.compile(r"`(src/[\w/.\-]+\.\w+)`"),
        re.compile(r"`(tools/[\w/.\-]+\.\w+)`"),
        re.compile(r"`(Mnemo/[\w/.\-]+\.\w+)`"),
    ]
    for i, line in enumerate(memory_md.splitlines(), start=1):
        for pat in ref_patterns:
            for m in pat.finditer(line):
                ref = m.group(1)
                if not any(ref in p or p.endswith(ref) for p in existing_paths):
                    dead.append({"line_no": i, "content": line.strip(), "dead_ref": ref})
    return dead


def build_dedup_report(memory_md: str, existing_paths: set[str] | None = None) -> dict:
    """Rapport complet : doublons exacts + références mortes."""
    exact = detect_exact_duplicates(memory_md)
    dead  = detect_dead_references(memory_md, existing_paths or set())
    return {
        "exact_duplicates": exact,
        "dead_references":  dead,
        "total_lines":      len(memory_md.splitlines()),
        "duplicate_count":  sum(len(g["occurrences"]) - 1 for g in exact),
        "dead_ref_count":   len(dead),
    }


# ══════════════════════════════════════════════════════════════════
# D3 — Application des patches + outil CrewAI
# ══════════════════════════════════════════════════════════════════

def apply_patches(memory_md: str, patches: list[dict]) -> tuple[str, list[str]]:
    """
    Applique une liste de patches sur le contenu de memory.md.

    Formats supportés :
      {"action": "delete",         "line": "texte exact"}
      {"action": "replace",        "old": "...", "new": "..."}
      {"action": "update_section", "section": "...", "subsection": "...",
                                   "content": "...", "category": "..."}

    Les patches "update_section" sont délégués à update_markdown_section().
    Retourne (nouveau_contenu, change_log).
    """
    lines      = memory_md.splitlines(keepends=True)
    change_log: list[str] = []
    deferred:   list[dict] = []

    for patch in patches:
        action = patch.get("action", "")

        if action == "delete":
            target = patch.get("line", "").strip()
            if not target:
                continue
            before = len(lines)
            lines = [l for l in lines if l.strip() != target]
            if len(lines) < before:
                change_log.append(f"DELETED : {target[:80]}")
            else:
                change_log.append(f"NOT FOUND (delete) : {target[:80]}")

        elif action == "replace":
            old = patch.get("old", "").strip()
            new = patch.get("new", "").strip()
            if not old or not new or old == new:
                continue
            found = False
            for i, line in enumerate(lines):
                if line.strip() == old:
                    indent = len(line) - len(line.lstrip())
                    lines[i] = " " * indent + new + "\n"
                    found = True
                    change_log.append(f"REPLACED : {old[:60]} → {new[:60]}")
                    break
            if not found:
                change_log.append(f"NOT FOUND (replace) : {old[:80]}")

        elif action == "update_section":
            deferred.append(patch)
            change_log.append(
                f"SECTION : [{patch.get('section')} > {patch.get('subsection')}]"
            )

    new_content = "".join(lines)

    if deferred:
        try:
            from Mnemo.tools.memory_tools import update_markdown_section
            for p in deferred:
                update_markdown_section(
                    p["section"],
                    p["subsection"],
                    p["content"],
                    category=p.get("category", "connaissance"),
                )
        except Exception as e:
            change_log.append(f"SECTION ERROR : {e}")

    return new_content, change_log


def run_dream_cycle(
    username: str,
    patches: list[dict],
    summary: str,
    data_path: Path | None = None,
) -> str:
    """
    Applique les patches, écrit memory.md, logue dans dream_log.md, sync DB.
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

    if not memory_path.exists():
        return f"Aucun memory.md trouvé pour {username}"

    original    = memory_path.read_text(encoding="utf-8")
    new_content, change_log = apply_patches(original, patches)

    changed = new_content != original
    if changed:
        memory_path.write_text(new_content, encoding="utf-8")

    try:
        from Mnemo.tools.memory_tools import sync_markdown_to_db
        sync_markdown_to_db()
    except Exception as e:
        change_log.append(f"SYNC ERROR : {e}")

    now_str   = datetime.now().strftime("%Y-%m-%d %H:%M")
    log_entry = (
        f"\n## Rêve du {now_str}\n"
        f"**Résumé** : {summary}\n\n"
        + "\n".join(f"- {c}" for c in change_log)
        + "\n"
    )
    try:
        existing = dream_log_path.read_text(encoding="utf-8") if dream_log_path.exists() else ""
        dream_log_path.write_text(existing + log_entry, encoding="utf-8")
    except Exception:
        pass

    n_applied = len([c for c in change_log if not c.startswith("NOT FOUND")])
    return (
        f"{'✅' if changed else '➖'} Rêve terminé — "
        f"{n_applied} modification(s) appliquées sur {len(patches)} patch(es).\n"
        + "\n".join(f"  {c}" for c in change_log)
    )


# ── CrewAI Tool ──────────────────────────────────────────────────

class ApplyDreamPatchesInput(BaseModel):
    patches_json: str = Field(
        description=(
            'JSON string : {"patches": [...], "summary": "..."}. '
            'Chaque patch : {"action": "delete|replace|update_section", ...}.'
        )
    )


class ApplyDreamPatchesTool(BaseTool):
    name: str = "apply_dream_patches"
    description: str = (
        "Applique les patches de consolidation sur memory.md de l'utilisateur. "
        "Supprime les doublons, fusionne les contradictions, met à jour les sections. "
        "Logue chaque changement dans dream_log.md et synchronise la base SQLite. "
        "Appelle cet outil UNE SEULE FOIS avec TOUS les patches en un seul JSON."
    )
    args_schema: Type[BaseModel] = ApplyDreamPatchesInput
    username: str = ""

    def _run(self, patches_json: str) -> str:
        try:
            data    = json.loads(patches_json)
            patches = data.get("patches", [])
            summary = data.get("summary", "Consolidation automatique")
        except Exception as e:
            return f"JSON invalide : {e}"

        if not patches:
            return "Aucun patch nécessaire — mémoire déjà propre."

        return run_dream_cycle(self.username, patches, summary)


# ── Préparation des inputs pour le crew ─────────────────────────

def prepare_dream_inputs(
    username: str,
    data_path: Path | None = None,
    max_memory_chars: int = 3000,
    max_sessions_chars: int = 2000,
) -> dict:
    """
    Prépare le dict d'inputs pour DreamerCrew.crew().kickoff().
    Lit memory.md, construit le rapport dedup, scanne les sessions récentes.
    """
    if data_path is None:
        try:
            from Mnemo.context import get_data_dir
            data_path = get_data_dir()
        except Exception:
            data_path = Path("/data")

    user_dir    = data_path / "users" / username
    memory_path = user_dir / "memory.md"

    # memory.md (cappé)
    memory_content = ""
    if memory_path.exists():
        raw = memory_path.read_text(encoding="utf-8")
        memory_content = raw if len(raw) <= max_memory_chars else raw[:max_memory_chars] + "\n…[tronqué]"

    # Rapport doublons
    dedup_report = json.dumps(build_dedup_report(memory_content), ensure_ascii=False, indent=2)

    # Sessions depuis le dernier rêve
    ws: dict = {}
    ws_path = user_dir / "world_state.json"
    if ws_path.exists():
        try:
            ws = json.loads(ws_path.read_text(encoding="utf-8"))
        except Exception:
            pass

    since_ts = None
    if ws.get("last_dream_ts"):
        try:
            since_ts = datetime.fromisoformat(ws["last_dream_ts"])
        except Exception:
            pass

    sessions      = resolve_sessions_dates(scan_sessions(username, since_ts=since_ts, data_path=data_path))
    segments      = extract_text_from_sessions(sessions, roles=["user", "assistant"])
    sessions_text = ""
    for seg in segments:
        line = f"[{seg['date_iso'][:10]}] {seg['role'].upper()}: {seg['content'][:200]}\n"
        if len(sessions_text) + len(line) > max_sessions_chars:
            break
        sessions_text += line

    # Identité de l'assistant
    assistant_name    = "Mnemo"
    assistant_persona = ""
    try:
        from Mnemo.tools.assistant_tools import get_assistant_config, get_assistant_context
        cfg               = get_assistant_config(username, data_path)
        assistant_name    = cfg.get("name", "Mnemo")
        assistant_persona = get_assistant_context(username, data_path)
    except Exception:
        pass

    return {
        "username":          username,
        "assistant_name":    assistant_name,
        "assistant_persona": assistant_persona,
        "memory_content":    memory_content,
        "dedup_report":      dedup_report,
        "sessions_summary":  sessions_text or "Aucune session récente.",
    }