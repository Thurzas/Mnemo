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

# ── DreamerCrew — détection d'inactivité ──────────────────────────
DREAMER_ENABLED        = os.getenv("DREAMER_ENABLED", "true").lower() == "true"
DREAMER_IDLE_THRESHOLD = int(os.getenv("DREAMER_IDLE_THRESHOLD", "1800"))  # 30 min
DREAMER_MIN_INTERVAL   = int(os.getenv("DREAMER_MIN_INTERVAL",   "86400")) # 24h

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
# GOAP — WorldState + orchestration
# ══════════════════════════════════════════════════════════════════

def _build_scheduler_world_state() -> dict:
    """
    Construit le WorldState courant pour le scheduler.

    Sources :
    - world_state.json (persisté par CuriosityCrew, AssessMemoryGaps, etc.)
    - Vérifications légères en Python (fichiers présents, DB accessible)

    user_online est toujours False dans le contexte scheduler —
    les actions qui le requièrent (FillBlockingGaps) ne seront jamais planifiées.
    """
    from Mnemo.tools.memory_tools import load_world_state

    ws_persisted = load_world_state()
    now          = datetime.now()
    today        = now.date()

    # briefing_fresh : briefing.md existe et date d'aujourd'hui
    briefing_fresh = False
    if BRIEFING_OUT.exists():
        mtime = datetime.fromtimestamp(BRIEFING_OUT.stat().st_mtime).date()
        briefing_fresh = (mtime == today)

    # weekly_generated : weekly.md existe et date de cette semaine
    weekly_generated = False
    if WEEKLY_OUT.exists():
        mtime = datetime.fromtimestamp(WEEKLY_OUT.stat().st_mtime).date()
        # Même semaine ISO
        weekly_generated = (mtime.isocalendar()[:2] == today.isocalendar()[:2])

    # memory_synced : memory.db accessible
    memory_synced = (DATA_PATH / "memory.db").exists()

    # calendar_fetched : calendrier configuré
    calendar_fetched = False
    try:
        from Mnemo.tools.calendar_tools import calendar_is_configured
        calendar_fetched = calendar_is_configured()
    except Exception:
        pass

    return {
        "calendar_fetched":      calendar_fetched,
        "memory_synced":         memory_synced,
        "memory_gaps_known":     ws_persisted.get("memory_gaps_known", False),
        "memory_blocking_gaps":  ws_persisted.get("memory_blocking_gaps", False),
        "user_online":           False,   # scheduler = pas d'utilisateur présent
        "briefing_fresh":        briefing_fresh,
        "weekly_generated":      weekly_generated,
        "deadline_alerts_sent":  ws_persisted.get("deadline_alerts_sent", False),
        "knows_module":          ws_persisted.get("knows_module", False),
    }


def _update_world_state(updates: dict) -> None:
    """Applique des mises à jour partielles sur world_state.json (avec timestamps TTL)."""
    from Mnemo.tools.memory_tools import load_world_state, WORLD_STATE_TTL
    ws = load_world_state()
    now_ts = time.time()
    for key, value in updates.items():
        ws[key] = value
        if key in WORLD_STATE_TTL:
            ws[f"_ts_{key}"] = now_ts
    path = DATA_PATH / "world_state.json"
    path.write_text(
        json.dumps(ws, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _scheduler_fetch_calendar() -> None:
    """Précondition légère : vérifie que le calendrier est accessible."""
    from Mnemo.tools.calendar_tools import get_upcoming_events
    get_upcoming_events(days=7)  # lève une exception si indisponible
    _update_world_state({"calendar_fetched": True})


def _scheduler_sync_memory() -> None:
    """Précondition légère : vérifie que memory.db est accessible."""
    from Mnemo.tools.memory_tools import get_db
    db = get_db()
    db.close()
    _update_world_state({"memory_synced": True})


def _scheduler_assess_gaps() -> None:
    """
    AssessMemoryGaps dans le contexte scheduler (sans utilisateur).
    Lit le world_state.json existant — ne relance pas de LLM si déjà frais.
    """
    from Mnemo.tools.memory_tools import load_world_state
    ws = load_world_state()
    if not ws.get("memory_gaps_known"):
        log.info("memory_gaps_known=False — rapport de gaps absent ou obsolète")
    _update_world_state({"memory_gaps_known": True})


# Registre d'exécuteurs GOAP pour le scheduler
# Chaque entrée : Action.name → callable sans argument
_SCHEDULER_EXECUTORS: dict = {}  # initialisé après la définition des actions


def goap_dispatch(goal: dict) -> None:
    """
    Orchestre l'exécution d'un goal via le planner GOAP.

    1. Construit le WorldState courant
    2. Demande au planner la séquence minimale d'actions
    3. Exécute chaque action dans l'ordre

    Les actions dont l'exécuteur est absent sont loggées et ignorées.
    """
    from Mnemo.goap.planner import plan as goap_plan, PlanningError

    ws = _build_scheduler_world_state()
    log.debug(f"WorldState : {ws}")

    try:
        actions = goap_plan(goal, ws)
    except PlanningError as e:
        log.warning(f"GOAP inatteignable pour {goal} : {e}")
        return

    if not actions:
        log.info(f"Goal {goal} déjà atteint — aucune action nécessaire")
        return

    log.info(f"Plan GOAP : {[a.name for a in actions]}")
    for action in actions:
        executor = _SCHEDULER_EXECUTORS.get(action.name)
        if executor is None:
            log.warning(f"Pas d'exécuteur pour l'action GOAP : {action.name!r}")
            continue
        try:
            log.info(f"→ {action.name}")
            executor()
        except Exception as e:
            log.error(f"Action GOAP {action.name!r} échouée : {e}", exc_info=True)
            raise  # arrêt — les actions suivantes dépendent de celle-ci


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
    _update_world_state({"briefing_fresh": True})


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
    _update_world_state({"weekly_generated": True})


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
            _update_world_state({"deadline_alerts_sent": True})
        return

    now = datetime.now()
    DATA_PATH.mkdir(parents=True, exist_ok=True)
    BRIEFING_OUT.write_text(
        f"# ⚠️ Alertes — {_date_fr(now)}\n" + alert_block +
        f"\n*Généré par Mnemo — {now.strftime('%Y-%m-%d %H:%M')}*",
        encoding="utf-8"
    )
    log.info(f"Alertes → {BRIEFING_OUT}")
    _update_world_state({"deadline_alerts_sent": True})


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
# Phase 7.4 — Boucle d'autonomie GOAP (sandbox projects)
# ══════════════════════════════════════════════════════════════════

# Actions considérées comme risquées → confirmation utilisateur requise
_RISKY_KEYWORDS = ("shell", "npm", "pip", "node", "python", "docker", "install", "build", "run")


def _is_risky(action_label: str) -> bool:
    label = action_label.lower()
    return any(k in label for k in _RISKY_KEYWORDS)


def _check_command_available(cmd: str) -> bool:
    import subprocess
    try:
        subprocess.run(cmd.split(), capture_output=True, timeout=3)
        return True
    except Exception:
        return False


def _build_project_world_state(project_dir: Path) -> dict:
    """WorldState minimal pour évaluer les préconditions d'un projet sandbox."""
    return {
        "sandbox_open":      True,
        "sandbox_readonly":  False,
        "web_available":     True,
        "node_available":    _check_command_available("node --version"),
        "python_available":  _check_command_available("python3 --version"),
    }


def _push_pending_confirmation(
    username: str, slug: str, step_label: str, action_label: str
) -> bool:
    """
    Ajoute une action risquée dans pending_confirmations du world_state utilisateur.
    Évite les doublons (même action + même projet).
    Retourne True si une nouvelle confirmation a été ajoutée, False si déjà présente.
    """
    import uuid
    ws_path = DATA_PATH / "users" / username / "world_state.json"
    ws: dict = {}
    if ws_path.exists():
        try:
            ws = json.loads(ws_path.read_text(encoding="utf-8"))
        except Exception:
            pass

    confirmations: list = ws.get("pending_confirmations", [])
    # Dédoublonnage — même step + même projet = déjà en attente
    if any(c.get("step_label") == step_label and c.get("project_slug") == slug
           for c in confirmations):
        return False

    confirmations.append({
        "id":           uuid.uuid4().hex[:12],
        "project_slug": slug,
        "username":     username,
        "step_label":   step_label,
        "action":       action_label,
        "description":  (f"Exécuter '{action_label}' "
                         f"pour l'étape '{step_label}' (projet {slug})"),
        "ts":           datetime.now().isoformat(timespec="seconds"),
    })
    ws["pending_confirmations"] = confirmations
    ws_path.write_text(
        json.dumps(ws, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    log.info(f"[autonomy] Confirmation en attente : {action_label!r} → {slug} ({username})")
    return True


def _advance_project(
    username: str, db_path: Path, manifest: dict, project_dir: Path
) -> None:
    """
    Avance d'une étape sur un projet sandbox actif en déléguant à PlanRunner.

    PlanRunner est la source de vérité pour l'exécution des étapes :
      - Lit plan.md via PlanStore (pas de duplication de logique)
      - Consulte le KG, exécute avec les executeurs crew-aware
      - Met à jour memory.md, KG feedback, outputs

    Les actions shell risquées passent toujours par pending_confirmations
    (vérification préalable avant de déléguer à PlanRunner).
    """
    from Mnemo.context import set_data_dir
    from Mnemo.tools.plan_tools import PlanRunner, PlanStore

    # Positionne le data_dir sur le répertoire utilisateur pour que get_data_dir()
    # retourne le bon chemin dans tous les tools (sandbox, KG, memory, etc.)
    set_data_dir(DATA_PATH / "users" / username)

    slug      = manifest["slug"]
    plan_path = project_dir / "plan.md"
    if not plan_path.exists():
        return

    # Projet déjà terminé ?
    if PlanStore.is_complete(plan_path):
        manifest["status"] = "done"
        (project_dir / "project.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        log.info(f"[autonomy] Projet {slug!r} terminé pour {username!r}")
        return

    # Lire auto_approve depuis le world_state propre à l'utilisateur
    user_ws_path = DATA_PATH / "users" / username / "world_state.json"
    try:
        user_ws = json.loads(user_ws_path.read_text(encoding="utf-8")) if user_ws_path.exists() else {}
    except Exception:
        user_ws = {}
    auto_approve = user_ws.get("auto_approve_confirmations", False)

    # Vérification risque avant exécution : si l'étape suivante est shell,
    # queue confirmation — sauf si auto_approve est activé.
    next_step = PlanStore.get_next_step(plan_path)
    if next_step and not auto_approve:
        import re as _re
        crew_m = _re.search(r"—\s*crew\s*:\s*(\w+)", next_step, _re.IGNORECASE)
        crew_t = crew_m.group(1).lower() if crew_m else "conversation"
        if crew_t == "shell":
            step_clean = _re.sub(r"—\s*crew\s*:\S+", "", next_step).strip(" —")
            added = _push_pending_confirmation(username, slug, step_clean, "sandbox_shell")
            if added:
                log.info(f"[autonomy] Confirmation requise pour '{step_clean}' ({slug})")
            return

    # Exécuter une étape via PlanRunner (source de vérité unique)
    runner  = PlanRunner()
    summary = runner.run(
        plan_path,
        session_id  = f"scheduler_{username}",
        base_inputs = {
            "slug":        slug,
            "project_dir": str(project_dir),
            "goal":        manifest.get("goal", slug),
        },
        max_steps = 1,
    )
    log.info(f"[autonomy] {slug!r} ({username}) — {summary}")

    # Fix 3 — marquer le projet actif dans world_state pour que ConversationCrew
    # ait conscience du travail en cours si l'utilisateur revient dans le chat.
    try:
        next_s = PlanStore.get_next_step(plan_path)
        active: dict = {
            "slug": slug,
            "goal": manifest.get("goal", slug),
            "step": next_s or "terminé",
        }
        ws_now: dict = {}
        if user_ws_path.exists():
            ws_now = json.loads(user_ws_path.read_text(encoding="utf-8"))
        ws_now["active_project"] = active
        user_ws_path.write_text(json.dumps(ws_now, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


# ══════════════════════════════════════════════════════════════════
# DreamerCrew — détection d'inactivité + déclenchement
# ══════════════════════════════════════════════════════════════════

def _user_sessions_dir(username: str) -> Path:
    """Retourne le dossier sessions de l'utilisateur (par user, pas global)."""
    return DATA_PATH / "users" / username / "sessions"


def _last_session_ts(username: str) -> datetime | None:
    """
    Retourne le timestamp de fin de la session la plus récente pour cet utilisateur.
    Se base sur le mtime du fichier .done le plus récent dans users/<username>/sessions/.
    Retourne None si aucune session terminée n'existe.
    """
    sessions_dir = _user_sessions_dir(username)
    if not sessions_dir.exists():
        return None
    done_files = sorted(sessions_dir.glob("*.done"), reverse=True)
    if not done_files:
        return None
    return datetime.fromtimestamp(done_files[0].stat().st_mtime)


def _has_active_session(username: str) -> bool:
    """
    Retourne True si une session est ouverte (json sans .done correspondant).
    Ignore les orphelins de plus de 4h (crash probable).
    """
    sessions_dir = _user_sessions_dir(username)
    if not sessions_dir.exists():
        return False
    for json_file in sessions_dir.glob("*.json"):
        if not json_file.with_suffix(".done").exists():
            age = (datetime.now() - datetime.fromtimestamp(json_file.stat().st_mtime)).total_seconds()
            if age < 14400:  # < 4h → session probablement active
                return True
    return False


def _should_dream(username: str) -> bool:
    """
    Retourne True si toutes les conditions sont réunies pour lancer DreamerCrew :
    1. DREAMER_ENABLED = true
    2. Aucune session active (utilisateur absent)
    3. Inactivité >= DREAMER_IDLE_THRESHOLD depuis la dernière session
    4. Intervalle >= DREAMER_MIN_INTERVAL depuis le dernier rêve
    5. Pas de rêve déjà en cours (dreamer_running dans world_state)
    """
    if not DREAMER_ENABLED:
        return False

    if _has_active_session(username):
        return False

    last_ts = _last_session_ts(username)
    if last_ts is None:
        return False
    if (datetime.now() - last_ts).total_seconds() < DREAMER_IDLE_THRESHOLD:
        return False

    user_ws_path = DATA_PATH / "users" / username / "world_state.json"
    ws: dict = {}
    if user_ws_path.exists():
        try:
            ws = json.loads(user_ws_path.read_text(encoding="utf-8"))
        except Exception:
            pass

    if ws.get("dreamer_running"):
        return False

    last_dream_str = ws.get("last_dream_ts")
    if last_dream_str:
        try:
            since = (datetime.now() - datetime.fromisoformat(last_dream_str)).total_seconds()
            if since < DREAMER_MIN_INTERVAL:
                return False
        except Exception:
            pass

    return True


def _set_dreamer_state(username: str, running: bool) -> None:
    """Met à jour dreamer_running (+ last_dream_ts si fin) dans world_state utilisateur."""
    user_ws_path = DATA_PATH / "users" / username / "world_state.json"
    ws: dict = {}
    if user_ws_path.exists():
        try:
            ws = json.loads(user_ws_path.read_text(encoding="utf-8"))
        except Exception:
            pass
    ws["dreamer_running"] = running
    if not running:
        ws["last_dream_ts"] = datetime.now().isoformat()
    try:
        user_ws_path.write_text(json.dumps(ws, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        log.warning(f"[dreamer] Impossible d'écrire world_state pour {username}: {e}")


def _run_dreamer(username: str) -> None:
    """
    Lance DreamerCrew pour un utilisateur dans un thread séparé.
    Configure le contexte utilisateur (set_data_dir) avant le lancement.
    Enchaîne avec prune_memory (D4) après la consolidation LLM.
    """
    log.info(f"[dreamer] 💤 Début consolidation mémoire — {username}")
    _set_dreamer_state(username, running=True)
    try:
        from Mnemo.context import set_data_dir
        set_data_dir(DATA_PATH / "users" / username)

        from Mnemo.crew import DreamerCrew
        report = DreamerCrew().run(username=username)
        log.info(f"[dreamer] ✅ {username} — {report[:200]}")

        # D4 — élagage après consolidation LLM
        try:
            from Mnemo.tools.memory_archive import prune_memory
            prune_report = prune_memory(username, data_path=DATA_PATH)
            log.info(f"[dreamer] {prune_report[:200]}")
        except Exception as e:
            log.warning(f"[dreamer] Élagage échoué pour {username}: {e}")

    except Exception as e:
        log.error(f"[dreamer] Erreur pour {username}: {e}", exc_info=True)
    finally:
        _set_dreamer_state(username, running=False)


def _dream_tick() -> None:
    """
    Tick du détecteur d'inactivité DreamerCrew.
    Appelé à chaque tour de boucle scheduler.
    Lance _run_dreamer() dans un thread daemon si les conditions sont réunies.
    """
    import threading

    users_dir = DATA_PATH / "users"
    if not users_dir.exists():
        return

    for user_dir in sorted(users_dir.iterdir()):
        if not user_dir.is_dir():
            continue
        username = user_dir.name
        if _should_dream(username):
            log.info(f"[dreamer] Inactivité ≥ {DREAMER_IDLE_THRESHOLD}s détectée → lancement")
            threading.Thread(
                target=_run_dreamer,
                args=(username,),
                daemon=True,
                name=f"dreamer-{username}",
            ).start()


def _goap_autonomy_tick() -> None:
    """
    Tick de la boucle d'autonomie Phase 7.4.
    Scanne tous les projets sandbox actifs de tous les utilisateurs.
    Appelé toutes les 10s depuis run_scheduler().
    """
    users_dir = DATA_PATH / "users"
    if not users_dir.exists():
        return

    for user_dir in sorted(users_dir.iterdir()):
        if not user_dir.is_dir():
            continue
        username = user_dir.name
        db_path  = user_dir / "memory.db"
        if not db_path.exists():
            continue

        projects_dir = user_dir / "projects"
        if not projects_dir.exists():
            continue

        for project_dir in sorted(projects_dir.iterdir()):
            manifest_file = project_dir / "project.json"
            if not manifest_file.exists():
                continue
            try:
                manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
                if manifest.get("status") != "in_progress":
                    continue
                _advance_project(username, db_path, manifest, project_dir)
            except Exception as e:
                log.warning(
                    f"[autonomy] Erreur sur {username}/{project_dir.name} : {e}",
                    exc_info=True,
                )


# ══════════════════════════════════════════════════════════════════
# Dispatch
# ══════════════════════════════════════════════════════════════════

# Initialisation du registre GOAP après les définitions d'actions
_SCHEDULER_EXECUTORS.update({
    "FetchCalendar":    _scheduler_fetch_calendar,
    "SyncMemory":       _scheduler_sync_memory,
    "AssessMemoryGaps": _scheduler_assess_gaps,
    "GenerateBriefing": action_briefing,
    "GenerateWeekly":   action_weekly,
    "SendDeadlineAlert": action_deadline_alert,
})

# Goals GOAP par type de tâche système
_SYSTEM_GOALS = {
    "briefing":       {"briefing_fresh": True},
    "weekly":         {"weekly_generated": True},
    "deadline_alert": {"deadline_alerts_sent": True},
}

# _ACTION_MAP conservé pour les tâches utilisateur simples (reminder)
_ACTION_MAP = {
    "reminder": lambda p: action_reminder(p),
}


def dispatch(task: dict) -> None:
    """
    Dispatche une tâche vers le bon exécuteur.

    Tâches système (briefing, weekly, deadline_alert) :
      → GOAP planner résout le plan, exécute dans l'ordre
    Tâches utilisateur (reminder) :
      → _ACTION_MAP direct (pas de GOAP nécessaire)
    """
    action  = task.get("action", "")
    payload = {}
    try:
        payload = json.loads(task.get("payload") or "{}")
    except Exception:
        pass

    # Tâches système → GOAP
    if action in _SYSTEM_GOALS:
        goap_dispatch(_SYSTEM_GOALS[action])
        return

    # Tâches utilisateur → direct
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

        # Phase 7.4 — Boucle d'autonomie GOAP
        try:
            _goap_autonomy_tick()
        except Exception as e:
            log.error(f"[autonomy] Erreur tick : {e}", exc_info=True)

        # DreamerCrew — consolidation mémoire sur inactivité
        try:
            _dream_tick()
        except Exception as e:
            log.error(f"[dreamer] Erreur tick : {e}", exc_info=True)

        time.sleep(5)


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