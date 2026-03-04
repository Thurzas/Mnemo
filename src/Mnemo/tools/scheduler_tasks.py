"""
scheduler_tasks.py — Gestion des tâches planifiées Mnemo

Responsabilités :
  - CRUD sur la table scheduled_tasks (SQLite)
  - Calcul de next_run pour les tâches récurrentes
  - Mirror tasks.md — miroir humain lisible
  - Helpers pour le scheduler.py

Structure cron_expr (format simplifié, pas POSIX) :
  Tâches système  : "daily HH:MM"           → ex: "daily 07:30"
  Récurrentes     : "weekly WEEKDAY HH:MM"   → ex: "weekly lundi 08:00"
  One-shot        : None (trigger_at est utilisé)
"""

import json
import sqlite3
from datetime import datetime, timedelta, date
from pathlib import Path
from typing import Optional
import os

# ── Paths ─────────────────────────────────────────────────────────
DATA_PATH  = Path(os.getenv("DATA_PATH", "/data")).resolve()
DB_PATH    = DATA_PATH / "memory.db"
TASKS_MD   = DATA_PATH / "tasks.md"

_JOURS_FR = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
_JOURS_EN = {j: i for i, j in enumerate(_JOURS_FR)}


# ══════════════════════════════════════════════════════════════════
# Connexion DB
# ══════════════════════════════════════════════════════════════════

def _get_db() -> sqlite3.Connection:
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    return db


# ══════════════════════════════════════════════════════════════════
# Calcul next_run
# ══════════════════════════════════════════════════════════════════

def compute_next_run(
    task_type: str,
    cron_expr: Optional[str],
    trigger_at: Optional[str],
    from_dt: Optional[datetime] = None,
) -> Optional[datetime]:
    """
    Calcule le prochain datetime d'exécution.

    one_shot   : retourne trigger_at (converti en datetime)
    recurring  : "weekly lundi 08:00" → prochain lundi à 08:00
    system     : "daily 07:30" → demain à 07:30 (ou aujourd'hui si pas encore passé)
    """
    now = from_dt or datetime.now()

    if task_type == "one_shot":
        if not trigger_at:
            return None
        try:
            return datetime.fromisoformat(trigger_at)
        except ValueError:
            return None

    if not cron_expr:
        return None

    parts = cron_expr.strip().split()

    # "daily HH:MM"
    if parts[0] == "daily" and len(parts) == 2:
        try:
            h, m = map(int, parts[1].split(":"))
        except ValueError:
            return None
        target = now.replace(hour=h, minute=m, second=0, microsecond=0)
        if now >= target:
            target += timedelta(days=1)
        return target

    # "weekly WEEKDAY HH:MM"
    if parts[0] == "weekly" and len(parts) == 3:
        weekday_name = parts[1].lower()
        if weekday_name not in _JOURS_EN:
            return None
        try:
            h, m = map(int, parts[2].split(":"))
        except ValueError:
            return None
        target_wd = _JOURS_EN[weekday_name]
        days_ahead = (target_wd - now.weekday()) % 7
        if days_ahead == 0:
            # Aujourd'hui — vérifier si l'heure est passée
            target = now.replace(hour=h, minute=m, second=0, microsecond=0)
            if now >= target:
                days_ahead = 7
            else:
                return target
        target = (now + timedelta(days=days_ahead)).replace(
            hour=h, minute=m, second=0, microsecond=0
        )
        return target

    return None


# ══════════════════════════════════════════════════════════════════
# CRUD
# ══════════════════════════════════════════════════════════════════

def create_task(
    task_id: str,
    task_type: str,          # one_shot | recurring | system
    action: str,             # reminder | summary | deadline_alert | weekly | briefing
    payload: dict,
    trigger_at: Optional[str] = None,   # ISO datetime pour one_shot
    cron_expr: Optional[str]  = None,   # "daily HH:MM" ou "weekly lundi 08:00"
) -> dict:
    """Crée ou remplace une tâche planifiée."""
    now      = datetime.now().isoformat()
    next_run = compute_next_run(task_type, cron_expr, trigger_at)
    next_run_str = next_run.isoformat() if next_run else None

    db = _get_db()
    db.execute("""
        INSERT OR REPLACE INTO scheduled_tasks
            (id, type, action, payload, trigger_at, cron_expr,
             status, created_at, next_run)
        VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?)
    """, (
        task_id, task_type, action,
        json.dumps(payload, ensure_ascii=False),
        trigger_at, cron_expr, now, next_run_str,
    ))
    db.commit()
    db.close()
    _sync_tasks_md()
    return {"id": task_id, "next_run": next_run_str}


def get_due_tasks(now: Optional[datetime] = None) -> list[dict]:
    """Retourne les tâches pending dont next_run ≤ now."""
    now = now or datetime.now()
    db  = _get_db()
    rows = db.execute("""
        SELECT * FROM scheduled_tasks
        WHERE status = 'pending'
          AND next_run IS NOT NULL
          AND next_run <= ?
        ORDER BY next_run ASC
    """, (now.isoformat(),)).fetchall()
    db.close()
    return [dict(r) for r in rows]


def mark_done(task_id: str) -> None:
    """Marque un one_shot comme done."""
    db = _get_db()
    db.execute("""
        UPDATE scheduled_tasks
        SET status='done', last_run=?
        WHERE id=?
    """, (datetime.now().isoformat(), task_id))
    db.commit()
    db.close()
    _sync_tasks_md()


def mark_error(task_id: str, error_msg: str) -> None:
    db = _get_db()
    db.execute("""
        UPDATE scheduled_tasks
        SET status='error', last_run=?, error_msg=?
        WHERE id=?
    """, (datetime.now().isoformat(), error_msg[:500], task_id))
    db.commit()
    db.close()
    _sync_tasks_md()


def reschedule(task_id: str, cron_expr: str) -> None:
    """Recalcule next_run pour une tâche récurrente/système après exécution."""
    next_run = compute_next_run("recurring", cron_expr, None)
    next_run_str = next_run.isoformat() if next_run else None
    db = _get_db()
    db.execute("""
        UPDATE scheduled_tasks
        SET last_run=?, next_run=?, status='pending'
        WHERE id=?
    """, (datetime.now().isoformat(), next_run_str, task_id))
    db.commit()
    db.close()
    _sync_tasks_md()


def cancel_task(task_id: str) -> bool:
    db = _get_db()
    cur = db.execute(
        "UPDATE scheduled_tasks SET status='cancelled' WHERE id=? AND status='pending'",
        (task_id,)
    )
    db.commit()
    db.close()
    _sync_tasks_md()
    return cur.rowcount > 0


def list_tasks(status: Optional[str] = None) -> list[dict]:
    db = _get_db()
    if status:
        rows = db.execute(
            "SELECT * FROM scheduled_tasks WHERE status=? ORDER BY next_run ASC",
            (status,)
        ).fetchall()
    else:
        rows = db.execute(
            "SELECT * FROM scheduled_tasks ORDER BY next_run ASC"
        ).fetchall()
    db.close()
    return [dict(r) for r in rows]


# ══════════════════════════════════════════════════════════════════
# Mirror tasks.md
# ══════════════════════════════════════════════════════════════════

def _fmt_task_line(task: dict) -> str:
    """Formate une tâche en ligne Markdown."""
    payload = {}
    try:
        payload = json.loads(task.get("payload") or "{}")
    except Exception:
        pass

    status_icon = {
        "pending":   "[ ]",
        "done":      "[x]",
        "cancelled": "[~]",
        "error":     "[!]",
    }.get(task["status"], "[ ]")

    recur_icon = "[↻]" if task["type"] in ("recurring", "system") else status_icon

    next_run = task.get("next_run") or task.get("trigger_at") or "?"
    try:
        dt = datetime.fromisoformat(next_run)
        next_run = dt.strftime("%Y-%m-%d %H:%M")
    except Exception:
        pass

    message = payload.get("message", task["action"])
    return f"- {recur_icon} {next_run} — {message}"


def _sync_tasks_md() -> None:
    """Réécrit tasks.md depuis la DB."""
    tasks = list_tasks()

    system    = [t for t in tasks if t["type"] == "system"    and t["status"] not in ("cancelled",)]
    recurring = [t for t in tasks if t["type"] == "recurring" and t["status"] not in ("cancelled",)]
    one_shot  = [t for t in tasks if t["type"] == "one_shot"  and t["status"] not in ("done", "cancelled")]

    lines = ["# 📋 Tâches planifiées\n"]

    lines.append("## Système")
    if system:
        lines.extend(_fmt_task_line(t) for t in system)
    else:
        lines.append("*(aucune tâche système active)*")

    lines.append("\n## Récurrentes")
    if recurring:
        lines.extend(_fmt_task_line(t) for t in recurring)
    else:
        lines.append("*(aucune tâche récurrente)*")

    lines.append("\n## One-shot")
    if one_shot:
        lines.extend(_fmt_task_line(t) for t in one_shot)
    else:
        lines.append("*(aucun rappel en attente)*")

    lines.append(f"\n---\n*Mis à jour : {datetime.now().strftime('%Y-%m-%d %H:%M')}*")

    try:
        TASKS_MD.write_text("\n".join(lines), encoding="utf-8")
    except Exception:
        pass  # Silencieux si /data non accessible


# ══════════════════════════════════════════════════════════════════
# Bootstrap tâches système
# ══════════════════════════════════════════════════════════════════

def bootstrap_system_tasks(briefing_time: str = "07:30", weekly_time: str = "08:00") -> None:
    """
    Crée les tâches système si elles n'existent pas encore.
    Idempotent — INSERT OR REPLACE.
    """
    create_task(
        task_id   = "sys_briefing",
        task_type = "system",
        action    = "briefing",
        payload   = {"message": "Morning briefing quotidien"},
        cron_expr = f"daily {briefing_time}",
    )
    create_task(
        task_id   = "sys_weekly",
        task_type = "system",
        action    = "weekly",
        payload   = {"message": "Résumé hebdomadaire (weekly.md)"},
        cron_expr = f"weekly lundi {weekly_time}",
    )
    create_task(
        task_id   = "sys_deadline_scan",
        task_type = "system",
        action    = "deadline_alert",
        payload   = {"message": "Scan deadlines calendrier J-1/J-3"},
        cron_expr = "daily 07:00",
    )