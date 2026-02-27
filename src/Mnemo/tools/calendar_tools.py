"""
calendar_tools.py — Temporal awareness pour Mnemo

Supporte deux sources ICS :
  - Fichier local    : CALENDAR_SOURCE=/home/matt/agenda.ics
  - URL Google Cal   : CALENDAR_SOURCE=https://calendar.google.com/calendar/ical/xxx/basic.ics
  - URL Nextcloud    : CALENDAR_SOURCE=https://nextcloud.local/remote.php/dav/calendars/...

Si CALENDAR_SOURCE est absent ou invalide, tout est silencieux — le système
fonctionne normalement sans calendrier.

Dépendance : icalendar (pip install icalendar)
"""

import os
import re
import urllib.request
from datetime import datetime, date, timedelta, timezone
from pathlib import Path
from typing import Optional

# ── Tentative d'import icalendar — silencieux si absent ──────────
try:
    from icalendar import Calendar, Event
    _ICALENDAR_AVAILABLE = True
except ImportError:
    _ICALENDAR_AVAILABLE = False

# ── Config ───────────────────────────────────────────────────────
CALENDAR_SOURCE   = os.getenv("CALENDAR_SOURCE", "")
LOOKAHEAD_DAYS    = int(os.getenv("CALENDAR_LOOKAHEAD_DAYS", "14"))
CACHE_TTL_SECONDS = int(os.getenv("CALENDAR_CACHE_TTL", "300"))  # 5 min

# Cache mémoire simple pour éviter de retélécharger à chaque message
_cache: dict = {"data": None, "fetched_at": None}


# ══════════════════════════════════════════════════════════════════
# Fetch — local ou URL
# ══════════════════════════════════════════════════════════════════

def _fetch_ics_raw() -> Optional[bytes]:
    """
    Récupère le contenu brut du fichier ICS depuis la source configurée.
    Retourne None si source absente, inaccessible ou icalendar non installé.
    """
    if not CALENDAR_SOURCE or not _ICALENDAR_AVAILABLE:
        return None

    src = CALENDAR_SOURCE.strip()

    # ── URL (Google Calendar, Nextcloud, etc.) ──
    if src.startswith("http://") or src.startswith("https://"):
        try:
            with urllib.request.urlopen(src, timeout=5) as resp:
                return resp.read()
        except Exception as e:
            print(f"  ⚠️  Calendrier inaccessible ({src[:60]}...) : {e}")
            return None

    # ── Fichier local ──
    path = Path(src)
    if path.exists():
        return path.read_bytes()

    print(f"  ⚠️  Fichier calendrier introuvable : {src}")
    return None


def _get_calendar() -> Optional["Calendar"]:
    """Retourne l'objet Calendar en cache ou recharge si TTL expiré."""
    global _cache
    now = datetime.now()

    if (
        _cache["data"] is not None
        and _cache["fetched_at"] is not None
        and (now - _cache["fetched_at"]).seconds < CACHE_TTL_SECONDS
    ):
        return _cache["data"]

    raw = _fetch_ics_raw()
    if raw is None:
        return None

    try:
        cal = Calendar.from_ical(raw)
        _cache["data"]       = cal
        _cache["fetched_at"] = now
        return cal
    except Exception as e:
        print(f"  ⚠️  Erreur parsing ICS : {e}")
        return None


# ══════════════════════════════════════════════════════════════════
# Parsing des événements
# ══════════════════════════════════════════════════════════════════

def _to_date(dt_val) -> Optional[date]:
    """Normalise DTSTART (date ou datetime, naive ou aware) en date."""
    if dt_val is None:
        return None
    if isinstance(dt_val, datetime):
        # Convertit en heure locale naïve
        if dt_val.tzinfo is not None:
            dt_val = dt_val.astimezone().replace(tzinfo=None)
        return dt_val.date()
    if isinstance(dt_val, date):
        return dt_val
    return None


def _to_datetime(dt_val) -> Optional[datetime]:
    """Normalise en datetime local naïf."""
    if dt_val is None:
        return None
    if isinstance(dt_val, datetime):
        if dt_val.tzinfo is not None:
            return dt_val.astimezone().replace(tzinfo=None)
        return dt_val
    if isinstance(dt_val, date):
        return datetime(dt_val.year, dt_val.month, dt_val.day, 0, 0)
    return None


def _clean_text(text: str) -> str:
    """Nettoie les caractères d'échappement ICS (backslash, \\n, etc.)."""
    if not text:
        return ""
    text = str(text)
    text = text.replace("\\n", " ").replace("\\,", ",").replace("\\;", ";")
    return text.strip()


def get_upcoming_events(days: int = LOOKAHEAD_DAYS) -> list[dict]:
    """
    Retourne les événements du calendrier dans les `days` prochains jours.
    Chaque événement est un dict :
    {
        "title"      : str,
        "date"       : date,
        "datetime"   : datetime | None,   # None si événement jour entier
        "location"   : str | None,
        "description": str | None,
        "days_until" : int,               # 0 = aujourd'hui, 1 = demain, etc.
        "is_today"   : bool,
        "is_tomorrow": bool,
        "label"      : str,               # "Aujourd'hui", "Demain", "Dans 3 jours", etc.
    }
    Trié par date croissante.
    """
    cal = _get_calendar()
    if cal is None:
        return []

    today     = date.today()
    cutoff    = today + timedelta(days=days)
    events    = []

    for component in cal.walk():
        if component.name != "VEVENT":
            continue

        dtstart  = component.get("DTSTART")
        if dtstart is None:
            continue
        dt_val   = dtstart.dt
        ev_date  = _to_date(dt_val)
        if ev_date is None:
            continue

        # Filtre : événements entre aujourd'hui et la fenêtre
        if not (today <= ev_date <= cutoff):
            continue

        # Événement jour entier vs événement horaire
        all_day     = isinstance(dt_val, date) and not isinstance(dt_val, datetime)
        ev_datetime = None if all_day else _to_datetime(dt_val)

        summary     = _clean_text(str(component.get("SUMMARY", "")))
        location    = _clean_text(str(component.get("LOCATION", ""))) or None
        description = _clean_text(str(component.get("DESCRIPTION", ""))) or None

        days_until  = (ev_date - today).days
        if days_until == 0:
            label = "Aujourd'hui"
        elif days_until == 1:
            label = "Demain"
        else:
            label = f"Dans {days_until} jours"

        events.append({
            "title"      : summary,
            "date"       : ev_date,
            "datetime"   : ev_datetime,
            "location"   : location,
            "description": description,
            "days_until" : days_until,
            "is_today"   : days_until == 0,
            "is_tomorrow": days_until == 1,
            "label"      : label,
        })

    events.sort(key=lambda e: (e["date"], e["datetime"] or datetime.min))
    return events


# ══════════════════════════════════════════════════════════════════
# Formatage pour injection dans les prompts
# ══════════════════════════════════════════════════════════════════

def format_events_for_prompt(events: list[dict]) -> str:
    """
    Formate les événements pour injection dans un prompt LLM.
    Format compact lisible, avec indication temporelle claire.
    """
    if not events:
        return "Aucun événement à venir dans les prochains jours."

    lines = []
    for ev in events:
        time_str = ""
        if ev["datetime"]:
            time_str = f" à {ev['datetime'].strftime('%H:%M')}"
        loc_str = f" — {ev['location']}" if ev["location"] else ""
        lines.append(f"- [{ev['label']}]{time_str} {ev['title']}{loc_str}")

    return "\n".join(lines)


def format_startup_banner(events: list[dict]) -> str:
    """
    Formate un résumé compact pour l'affichage au démarrage CLI.
    N'affiche que les événements urgents (≤ 3 jours).
    """
    urgent = [e for e in events if e["days_until"] <= 3]
    if not urgent:
        return ""

    lines = ["\n📅 Événements à venir :"]
    for ev in urgent:
        time_str = ""
        if ev["datetime"]:
            time_str = f" {ev['datetime'].strftime('%H:%M')}"
        icon = "🔴" if ev["is_today"] else ("🟡" if ev["is_tomorrow"] else "🟢")
        lines.append(f"  {icon} {ev['label']}{time_str} — {ev['title']}")
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════
# Helpers date/heure pour injection systématique dans les prompts
# ══════════════════════════════════════════════════════════════════

def get_current_datetime_str() -> str:
    """
    Retourne la date et heure actuelles formatées pour injection dans les prompts.
    Format : "vendredi 28 février 2026, 09:14"
    """
    now = datetime.now()
    # Jours et mois en français
    jours   = ["lundi","mardi","mercredi","jeudi","vendredi","samedi","dimanche"]
    mois    = ["janvier","février","mars","avril","mai","juin",
               "juillet","août","septembre","octobre","novembre","décembre"]
    jour_semaine = jours[now.weekday()]
    return f"{jour_semaine} {now.day} {mois[now.month-1]} {now.year}, {now.strftime('%H:%M')}"


def get_temporal_context() -> str:
    """
    Construit le bloc temporel complet à injecter dans les prompts :
    date + heure + événements à venir (si calendrier disponible).
    """
    lines = [f"Date et heure actuelles : {get_current_datetime_str()}"]

    events = get_upcoming_events(days=LOOKAHEAD_DAYS)
    if events:
        lines.append(f"\nÉvénements à venir ({LOOKAHEAD_DAYS} prochains jours) :")
        lines.append(format_events_for_prompt(events))
    else:
        lines.append("Aucun événement calendrier disponible.")

    return "\n".join(lines)


def calendar_is_configured() -> bool:
    """Retourne True si une source calendrier est configurée ET accessible."""
    return bool(CALENDAR_SOURCE) and _ICALENDAR_AVAILABLE


# ══════════════════════════════════════════════════════════════════
# CrewAI Tool
# ══════════════════════════════════════════════════════════════════

try:
    from crewai.tools import BaseTool
    from pydantic import BaseModel, Field
    from typing import Type

    class GetCalendarInput(BaseModel):
        days: int = Field(
            default=14,
            description="Nombre de jours à regarder en avant (défaut : 14)."
        )

    class GetCalendarTool(BaseTool):
        name: str = "get_calendar_events"
        description: str = (
            "Récupère les événements du calendrier personnel dans les N prochains jours. "
            "À utiliser quand l'utilisateur mentionne des deadlines, rendez-vous, "
            "événements à venir, ou demande 'qu'est-ce que j'ai de prévu'. "
            "Retourne une liste formatée des événements avec leur date et heure."
        )
        args_schema: Type[BaseModel] = GetCalendarInput

        def _run(self, days: int = 14) -> str:
            events = get_upcoming_events(days=days)
            if not events:
                if not calendar_is_configured():
                    return "Aucun calendrier configuré (variable CALENDAR_SOURCE absente)."
                return f"Aucun événement dans les {days} prochains jours."
            return format_events_for_prompt(events)

except ImportError:
    # CrewAI non disponible (tests unitaires) — pas de Tool, juste les helpers
    pass