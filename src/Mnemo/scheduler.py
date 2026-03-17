"""
scheduler.py — Service de planification Mnemo

Service Docker séparé (mnemo-scheduler).
Boucle toutes les minutes, exécute les tâches dues.

Tâches système :
  briefing       → /data/briefing.md   (quotidien, BRIEFING_TIME)
  weekly         → /data/weekly.md     (lundi matin, WEEKLY_TIME)
  deadline_alert → injecté dans briefing.md si J-1 ou J-3 (quotidien 07:00)

Tâches utilisateur (créées via SchedulerCrew) :
  reminder       → injecté dans briefing.md du jour concerné

Usage :
  docker compose up -d mnemo-scheduler
  docker compose run --rm mnemo-scheduler --now briefing
  docker compose run --rm mnemo-scheduler --now weekly
  docker compose run --rm mnemo-scheduler --now deadline
  docker compose run --rm mnemo-scheduler --now all
"""

import os
import sys
import time
import json

# Permissions restrictives dès le démarrage — nouveau fichier 600, répertoire 700
os.umask(0o077)
import re
import logging
from datetime import datetime, date, timedelta
from pathlib import Path

# ── Logging ───────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [scheduler] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("mnemo.scheduler")

# ── Paths & config ────────────────────────────────────────────────
DATA_PATH     = Path(os.getenv("DATA_PATH", "/data")).resolve()
BRIEFING_OUT  = DATA_PATH / "briefing.md"
WEEKLY_OUT    = DATA_PATH / "weekly.md"
SESSIONS_DIR  = DATA_PATH / "sessions"
MARKDOWN_PATH = DATA_PATH / "memory.md"

BRIEFING_TIME = os.getenv("BRIEFING_TIME", "07:30")
WEEKLY_TIME   = os.getenv("WEEKLY_TIME",   "08:00")

_JOURS_FR = ["lundi","mardi","mercredi","jeudi","vendredi","samedi","dimanche"]
_MOIS_FR  = ["janvier","février","mars","avril","mai","juin",
             "juillet","août","septembre","octobre","novembre","décembre"]


# ══════════════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════════════

def _date_fr(dt: datetime) -> str:
    return f"{_JOURS_FR[dt.weekday()]} {dt.day} {_MOIS_FR[dt.month-1]} {dt.year}"


def _strip_fences(text: str) -> str:
    text = re.sub(r'^```[a-zA-Z]*\n', '', text, flags=re.MULTILINE)
    text = re.sub(r'^```\s*$',        '', text, flags=re.MULTILINE)
    text = re.sub(r'\n---\s*\n',      '\n', text)
    return text.strip()


def _get_last_session_summary() -> str:
    if not SESSIONS_DIR.exists():
        return "Aucune session enregistrée."
    done_files = sorted(SESSIONS_DIR.glob("*.done"), reverse=True)
    for done_file in done_files:
        session_id = done_file.stem
        json_path  = SESSIONS_DIR / f"{session_id}.json"
        if not json_path.exists():
            continue
        try:
            session  = json.loads(json_path.read_text(encoding="utf-8"))
            messages = session if isinstance(session, list) else session.get("messages", [])
            lines    = []
            for m in messages:
                role    = m.get("role", "?").upper()
                content = (m.get("content") or "").strip()
                if content:
                    lines.append(f"{role} : {content[:200]}")
            if lines:
                parts      = session_id.split("_")
                date_label = parts[1] if len(parts) > 1 else "date inconnue"
                try:
                    date_label = _date_fr(datetime.strptime(date_label, "%Y%m%d"))
                except Exception:
                    pass
                return f"Session du {date_label} :\n" + "\n".join(lines[:10])
        except Exception as e:
            log.warning(f"Erreur session {session_id}: {e}")
    return "Aucune session récente disponible."


def _get_memory_highlights(session_id: str = "", context_query: str = "") -> str:
    """
    Récupère les highlights mémoire pour le briefing.
    Phase 5.5 : utilise retrieve_all(profile="briefing") + _compress_chunks()
    pour un retrieval traçable dans chunk_usage (données pour l'Axe A).
    Fallback sur lecture directe de memory.md si le pipeline échoue.
    """
    try:
        from Mnemo.tools.memory_tools import (
            retrieve_all, _compress_chunks, _record_retrieved_chunks,
        )
        query  = context_query or "projets décisions préférences identité"
        chunks = retrieve_all(query, top_k_final=4, profile="briefing")
        if session_id and chunks:
            _record_retrieved_chunks(
                session_id, [c["id"] for c in chunks], profile="briefing"
            )
        result = _compress_chunks(chunks, max_tokens=600)
        return result if result else "Mémoire vide ou non structurée."
    except Exception as e:
        log.warning(f"retrieve_all briefing : {e} — fallback lecture directe")

    # Fallback : lecture directe des sections clés (comportement pré-5.5)
    if not MARKDOWN_PATH.exists():
        return "Mémoire non initialisée."
    content = MARKDOWN_PATH.read_text(encoding="utf-8", errors="ignore")
    target  = {"Projets en cours", "Décisions prises", "À ne jamais oublier", "Profil de base"}
    highlights, current_section, current_lines = [], None, []
    for line in content.splitlines():
        if line.startswith("###"):
            if current_section and current_lines:
                highlights.append(f"**{current_section}**\n" + "\n".join(current_lines))
            current_section, current_lines = line.lstrip("#").strip(), []
        elif line.startswith("##"):
            if current_section and current_lines:
                highlights.append(f"**{current_section}**\n" + "\n".join(current_lines))
            current_section, current_lines = None, []
        elif current_section and current_section in target:
            s = line.strip()
            if s and not s.startswith("#"):
                current_lines.append(s)
    if current_section and current_lines and current_section in target:
        highlights.append(f"**{current_section}**\n" + "\n".join(current_lines))
    return "\n\n".join(highlights) if highlights else "Mémoire vide ou non structurée."


def _write_fallback(path: Path, kind: str, error: str) -> None:
    now = datetime.now()
    path.write_text(
        f"# ⚠️ {kind.capitalize()} indisponible — {_date_fr(now)}\n\n"
        f"Erreur : {error}\n\n"
        f"*Tentative : {now.strftime('%Y-%m-%d %H:%M')}*",
        encoding="utf-8"
    )


# ══════════════════════════════════════════════════════════════════
# Actions
# ══════════════════════════════════════════════════════════════════

def action_briefing() -> None:
    log.info("Action : briefing matinal")
    try:
        from Mnemo.crew import BriefingCrew
        from Mnemo.tools.calendar_tools import (
            get_upcoming_events, format_events_for_prompt, get_temporal_context,
        )
    except ImportError as e:
        log.error(f"Import impossible : {e}")
        _write_fallback(BRIEFING_OUT, "briefing", str(e))
        return

    try:
        evts           = get_upcoming_events(days=0)
        calendar_today = format_events_for_prompt(evts) if evts else "Aucun événement aujourd'hui."
    except Exception as e:
        log.warning(f"Calendrier : {e}")
        calendar_today = "Calendrier non disponible."

    now        = datetime.now()
    session_id = f"briefing_{now.strftime('%Y%m%d_%H%M%S')}"
    last_sess  = _get_last_session_summary()
    try:
        result  = BriefingCrew().crew().kickoff(inputs={
            "temporal_context":     get_temporal_context(),
            "calendar_today":       calendar_today,
            "last_session_summary": last_sess,
            "memory_highlights":    _get_memory_highlights(
                session_id=session_id,
                context_query=f"{calendar_today[:200]} {last_sess[:200]}",
            ),
            "date_str":             _date_fr(now),
            "datetime_str":         now.strftime("%Y-%m-%d %H:%M"),
        })
        content = _strip_fences(result.raw.strip())
    except Exception as e:
        log.error(f"BriefingCrew : {e}")
        _write_fallback(BRIEFING_OUT, "briefing", str(e))
        return

    DATA_PATH.mkdir(parents=True, exist_ok=True)
    BRIEFING_OUT.write_text(content, encoding="utf-8")
    log.info(f"Briefing → {BRIEFING_OUT} ({len(content)} car.)")


def action_weekly() -> None:
    log.info("Action : résumé hebdomadaire")
    try:
        from Mnemo.crew import BriefingCrew
        from Mnemo.tools.calendar_tools import (
            get_events_for_date, format_events_for_prompt,
        )
    except ImportError as e:
        log.error(f"Import impossible : {e}")
        _write_fallback(WEEKLY_OUT, "weekly", str(e))
        return

    now        = datetime.now()
    week_start = now - timedelta(days=now.weekday() + 7)
    week_end   = week_start + timedelta(days=6)

    try:
        week_events = []
        for i in range(7):
            d = (week_start + timedelta(days=i)).date()
            week_events.extend(get_events_for_date(d))
        calendar_week = format_events_for_prompt(week_events) if week_events else "Aucun événement."
    except Exception as e:
        log.warning(f"Calendrier semaine : {e}")
        calendar_week = "Calendrier non disponible."

    # Sessions de la semaine passée
    session_lines = []
    if SESSIONS_DIR.exists():
        for done in sorted(SESSIONS_DIR.glob("*.done"), reverse=True):
            parts = done.stem.split("_")
            if len(parts) > 1:
                try:
                    d = datetime.strptime(parts[1], "%Y%m%d").date()
                    if week_start.date() <= d <= week_end.date():
                        session_lines.append(f"Session du {_date_fr(datetime.combine(d, datetime.min.time()))}")
                except Exception:
                    pass
    sessions_summary = "\n".join(session_lines) if session_lines else "Aucune session cette semaine."

    date_str   = f"semaine du {_date_fr(week_start)} au {_date_fr(week_end)}"
    session_id = f"weekly_{now.strftime('%Y%m%d_%H%M%S')}"
    try:
        result  = BriefingCrew().crew().kickoff(inputs={
            "temporal_context":     f"Date actuelle : {now.strftime('%Y-%m-%d %H:%M')}",
            "calendar_today":       calendar_week,
            "last_session_summary": sessions_summary,
            "memory_highlights":    _get_memory_highlights(
                session_id=session_id,
                context_query=f"{sessions_summary[:200]} {calendar_week[:200]}",
            ),
            "date_str":             date_str,
            "datetime_str":         now.strftime("%Y-%m-%d %H:%M"),
        })
        content = _strip_fences(result.raw.strip())
    except Exception as e:
        log.error(f"WeeklyCrew : {e}")
        _write_fallback(WEEKLY_OUT, "weekly", str(e))
        return

    DATA_PATH.mkdir(parents=True, exist_ok=True)
    WEEKLY_OUT.write_text(content, encoding="utf-8")
    log.info(f"Weekly → {WEEKLY_OUT} ({len(content)} car.)")


def action_deadline_alert() -> None:
    log.info("Action : scan deadlines J-1/J-3")
    try:
        from Mnemo.tools.calendar_tools import get_upcoming_events
        events = get_upcoming_events(days=4)
    except Exception as e:
        log.warning(f"Calendrier : {e}")
        return

    alerts = []
    for ev in events:
        days_until = ev.get("days_until", 99)
        if days_until in (1, 3):
            title    = ev.get("title", "Événement")
            dt       = ev.get("datetime")
            time_str = dt.strftime("%H:%M") if dt else ""
            label    = "Demain" if days_until == 1 else "Dans 3 jours"
            alerts.append(
                f"- ⚠️ **{label}** : {title}" + (f" à {time_str}" if time_str else "")
            )

    if not alerts:
        log.info("Aucune deadline J-1/J-3.")
        return

    alert_block = "\n## ⚠️ Alertes deadlines\n" + "\n".join(alerts) + "\n"

    if BRIEFING_OUT.exists():
        current = BRIEFING_OUT.read_text(encoding="utf-8")
        if "## ⚠️ Alertes deadlines" not in current:
            BRIEFING_OUT.write_text(current + alert_block, encoding="utf-8")
            log.info(f"{len(alerts)} alerte(s) injectée(s) dans briefing.md")
        return

    now = datetime.now()
    DATA_PATH.mkdir(parents=True, exist_ok=True)
    BRIEFING_OUT.write_text(
        f"# ⚠️ Alertes — {_date_fr(now)}\n" + alert_block +
        f"\n*Généré par Mnemo — {now.strftime('%Y-%m-%d %H:%M')}*",
        encoding="utf-8"
    )
    log.info(f"Alertes → {BRIEFING_OUT}")


def action_reminder(payload: dict) -> None:
    message = payload.get("message", "Rappel sans message.")
    log.info(f"Action : reminder — {message}")
    now     = datetime.now()
    block   = f"\n## 🔔 Rappel\n{message}\n"
    DATA_PATH.mkdir(parents=True, exist_ok=True)
    if BRIEFING_OUT.exists():
        BRIEFING_OUT.write_text(
            BRIEFING_OUT.read_text(encoding="utf-8") + block, encoding="utf-8"
        )
    else:
        BRIEFING_OUT.write_text(
            f"# 🔔 Rappels — {_date_fr(now)}\n" + block, encoding="utf-8"
        )
    log.info("Rappel injecté dans briefing.md")


# ══════════════════════════════════════════════════════════════════
# Dispatch
# ══════════════════════════════════════════════════════════════════

_ACTION_MAP = {
    "briefing":       lambda p: action_briefing(),
    "weekly":         lambda p: action_weekly(),
    "deadline_alert": lambda p: action_deadline_alert(),
    "reminder":       lambda p: action_reminder(p),
}


def dispatch(task: dict) -> None:
    action  = task.get("action", "")
    payload = {}
    try:
        payload = json.loads(task.get("payload") or "{}")
    except Exception:
        pass
    fn = _ACTION_MAP.get(action)
    if fn:
        fn(payload)
    else:
        log.warning(f"Action inconnue : {action!r}")


# ══════════════════════════════════════════════════════════════════
# Boucle principale
# ══════════════════════════════════════════════════════════════════

def run_scheduler() -> None:
    from Mnemo.tools.scheduler_tasks import (
        bootstrap_system_tasks, get_due_tasks,
        mark_done, mark_error, reschedule,
    )

    log.info(f"Scheduler démarré — DATA_PATH={DATA_PATH}")
    log.info(f"BRIEFING_TIME={BRIEFING_TIME}  WEEKLY_TIME={WEEKLY_TIME}")

    try:
        from Mnemo.init_db import migrate_db
        migrate_db()
    except Exception as e:
        log.warning(f"migrate_db : {e}")

    bootstrap_system_tasks(
        briefing_time=BRIEFING_TIME,
        weekly_time=WEEKLY_TIME,
    )
    log.info("Tâches système bootstrapées — boucle démarrée (tick 60s)")

    while True:
        try:
            for task in get_due_tasks():
                tid   = task["id"]
                ttype = task["type"]
                log.info(f"→ Exécution {tid} [{task['action']}]")
                try:
                    dispatch(task)
                    if ttype == "one_shot":
                        mark_done(tid)
                    else:
                        reschedule(tid, task.get("cron_expr", ""))
                except Exception as e:
                    log.error(f"Tâche {tid} échouée : {e}", exc_info=True)
                    mark_error(tid, str(e))
        except Exception as e:
            log.error(f"Erreur boucle : {e}", exc_info=True)

        time.sleep(60)


# ══════════════════════════════════════════════════════════════════
# Déclenchement manuel
# ══════════════════════════════════════════════════════════════════

def run_now(targets: list) -> None:
    if "all" in targets:
        targets = ["briefing", "weekly", "deadline"]
    for target in targets:
        {"briefing": action_briefing,
         "weekly":   action_weekly,
         "deadline": action_deadline_alert}.get(target, lambda: log.warning(f"Cible inconnue : {target!r}"))()


if __name__ == "__main__":
    args = sys.argv[1:]
    if "--now" in args:
        rest = [a for a in args if a != "--now"] or ["briefing"]
        run_now(rest)
    else:
        run_scheduler()