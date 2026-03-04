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

# ── Expansion RRULE ──────────────────────────────────────────────
# On tente d'utiliser recurring_ical_events si dispo,
# sinon on fait une expansion manuelle légère des FREQ=WEEKLY/DAILY.
try:
    import recurring_ical_events as _rie
    _HAS_RIE = True
except ImportError:
    _HAS_RIE = False

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


def get_events_for_date(target_date: date) -> list[dict]:
    """
    Retourne tous les événements d'un jour précis (passé ou futur).
    Point d'entrée principal pour les questions temporelles ("que s'est-il passé mardi ?").
    """
    return get_upcoming_events(days=0, from_date=target_date, to_date=target_date)


def _expand_rrule(component, start: date, end: date) -> list[date]:
    """
    Expand une RRULE FREQ=WEEKLY ou DAILY dans [start, end].
    Retourne la liste des dates d'occurrence dans la fenêtre.
    Gère BYDAY, UNTIL, COUNT, EXDATE.
    """
    rrule_prop = component.get("RRULE")
    if not rrule_prop:
        return []

    dtstart_raw = component.get("DTSTART")
    if dtstart_raw is None:
        return []
    base_dt = dtstart_raw.dt
    base_date = _to_date(base_dt)
    if base_date is None:
        return []

    rrule = rrule_prop
    freq  = str(rrule.get("FREQ", [""])[0]).upper()
    if freq not in ("WEEKLY", "DAILY"):
        return []

    # BYDAY → liste de noms de jours (MO, TU, WE, TH, FR, SA, SU)
    byday   = [str(d) for d in rrule.get("BYDAY", [])]
    day_map = {"MO": 0, "TU": 1, "WE": 2, "TH": 3, "FR": 4, "SA": 5, "SU": 6}

    # UNTIL
    until_list = rrule.get("UNTIL", [])
    until_date = None
    if until_list:
        u = until_list[0]
        until_date = _to_date(u) if hasattr(u, "year") else None

    # COUNT
    count_list = rrule.get("COUNT", [])
    max_count  = int(count_list[0]) if count_list else None

    # EXDATE — dates à exclure
    exdate_prop = component.get("EXDATE")
    excluded = set()
    if exdate_prop:
        items = exdate_prop if isinstance(exdate_prop, list) else [exdate_prop]
        for item in items:
            vals = item.dts if hasattr(item, "dts") else [item]
            for v in vals:
                d = _to_date(v.dt if hasattr(v, "dt") else v)
                if d:
                    excluded.add(d)

    # Génération
    results = []
    cursor  = base_date
    count   = 0
    step    = timedelta(weeks=1) if freq == "WEEKLY" else timedelta(days=1)

    # Pour WEEKLY+BYDAY, on avance jour par jour dans la semaine
    if freq == "WEEKLY" and byday:
        # On part du lundi de la semaine de base_date
        week_start = base_date - timedelta(days=base_date.weekday())
        cursor = week_start
        while True:
            for day_name, day_idx in day_map.items():
                if day_name not in byday:
                    continue
                d = cursor + timedelta(days=day_idx)
                if d < base_date:
                    continue
                if until_date and d > until_date:
                    return results
                if max_count and count >= max_count:
                    return results
                if d > end + timedelta(days=1):
                    return results
                if d not in excluded and start <= d <= end:
                    results.append(d)
                count += 1
            cursor += timedelta(weeks=1)
            if cursor > end + timedelta(weeks=1):
                break
    else:
        while cursor <= end:
            if until_date and cursor > until_date:
                break
            if max_count and count >= max_count:
                break
            if cursor >= base_date and cursor not in excluded:
                if start <= cursor <= end:
                    results.append(cursor)
            count += 1
            cursor += step

    return results


def _make_event_dict(component, ev_date: date, today: date) -> dict:
    """Construit un dict événement depuis un composant VEVENT et une date d'occurrence."""
    dtstart_raw = component.get("DTSTART")
    dt_val      = dtstart_raw.dt if dtstart_raw else None
    all_day     = isinstance(dt_val, date) and not isinstance(dt_val, datetime)

    if all_day or dt_val is None:
        ev_datetime = None
    else:
        # Reconstruit l'heure sur la nouvelle date
        base_dt = _to_datetime(dt_val)
        if base_dt:
            ev_datetime = datetime(ev_date.year, ev_date.month, ev_date.day,
                                   base_dt.hour, base_dt.minute, base_dt.second)
        else:
            ev_datetime = None

    summary     = _clean_text(str(component.get("SUMMARY", "")))
    location    = _clean_text(str(component.get("LOCATION", ""))) or None
    description = _clean_text(str(component.get("DESCRIPTION", ""))) or None

    days_until = (ev_date - today).days
    if days_until == 0:
        label = "Aujourd'hui"
    elif days_until == 1:
        label = "Demain"
    elif days_until > 0:
        label = f"Dans {days_until} jours"
    elif days_until == -1:
        label = "Hier"
    else:
        label = f"Il y a {abs(days_until)} jours"

    return {
        "title"      : summary,
        "date"       : ev_date,
        "datetime"   : ev_datetime,
        "location"   : location,
        "description": description,
        "days_until" : days_until,
        "is_today"   : days_until == 0,
        "is_tomorrow": days_until == 1,
        "label"      : label,
    }


def get_upcoming_events(
    days: int = LOOKAHEAD_DAYS,
    from_date: Optional[date] = None,
    to_date:   Optional[date] = None,
) -> list[dict]:
    """
    Retourne les événements du calendrier dans une fenêtre de dates.
    Gère les événements récurrents (RRULE WEEKLY/DAILY + BYDAY + EXDATE).

    Modes d'utilisation :
      get_upcoming_events()                        → today .. today+14j
      get_upcoming_events(days=7)                  → today .. today+7j
      get_upcoming_events(from_date=d, to_date=d)  → jour exact (passé ou futur)
    """
    cal = _get_calendar()
    if cal is None:
        return []

    today = date.today()
    start = from_date if from_date is not None else today
    end   = to_date   if to_date   is not None else (today + timedelta(days=days))

    # Utilise recurring_ical_events si disponible (gère tous les cas edge)
    if _HAS_RIE:
        try:
            start_dt = datetime(start.year, start.month, start.day, 0, 0, 0)
            end_dt   = datetime(end.year,   end.month,   end.day,   23, 59, 59)
            raw_events = _rie.of(cal).between(start_dt, end_dt)
            events = []
            for component in raw_events:
                dtstart = component.get("DTSTART")
                if dtstart is None:
                    continue
                ev_date = _to_date(dtstart.dt)
                if ev_date is None:
                    continue
                events.append(_make_event_dict(component, ev_date, today))
            events.sort(key=lambda e: (e["date"], e["datetime"] or datetime.min))
            return events
        except Exception:
            pass  # fallback sur notre expansion manuelle

    # Expansion manuelle — gère VEVENT simples + RRULE WEEKLY/DAILY
    seen   = set()   # (uid, date) pour éviter les doublons
    events = []

    for component in cal.walk():
        if component.name != "VEVENT":
            continue

        uid = str(component.get("UID", ""))

        # Cas 1 : événement récurrent → expand les occurrences dans la fenêtre
        if component.get("RRULE"):
            occurrence_dates = _expand_rrule(component, start, end)
            for occ_date in occurrence_dates:
                key = (uid, occ_date)
                if key not in seen:
                    seen.add(key)
                    events.append(_make_event_dict(component, occ_date, today))

        # Cas 2 : événement simple (ou RECURRENCE-ID = exception d'une récurrence)
        else:
            dtstart = component.get("DTSTART")
            if dtstart is None:
                continue
            ev_date = _to_date(dtstart.dt)
            if ev_date is None or not (start <= ev_date <= end):
                continue
            key = (uid, ev_date)
            if key not in seen:
                seen.add(key)
                events.append(_make_event_dict(component, ev_date, today))

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
    N'affiche que les événements d'aujourd'hui.
    """
    today_events = [e for e in events if e["days_until"] == 0]
    if not today_events:
        return ""

    lines = ["\n📅 Aujourd'hui :"]
    for ev in today_events:
        time_str = f" {ev['datetime'].strftime('%H:%M')}" if ev["datetime"] else ""
        lines.append(f"  🔴{time_str} — {ev['title']}")
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════
# Helpers date/heure pour injection systématique dans les prompts
# ══════════════════════════════════════════════════════════════════

def _fmt_date(dt) -> str:
    """Formate une datetime en français : 'vendredi 28 février 2026, 09:14'."""
    jours = ["lundi","mardi","mercredi","jeudi","vendredi","samedi","dimanche"]
    mois  = ["janvier","février","mars","avril","mai","juin",
             "juillet","août","septembre","octobre","novembre","décembre"]
    return f"{jours[dt.weekday()]} {dt.day} {mois[dt.month-1]} {dt.year}, {dt.strftime('%H:%M')}"


def get_current_datetime_str() -> str:
    """
    Retourne la date et heure actuelles formatées pour injection dans les prompts.
    Format : "vendredi 28 février 2026, 09:14"
    """
    return _fmt_date(datetime.now())


def get_yesterday_date_str() -> str:
    """
    Retourne la date d'hier au format ISO et en français.
    Exemple : "jeudi 26 février 2026 (2026-02-26)"
    Utilisé pour que l'agent sache quelle date chercher dans l'historique des sessions.
    """
    yesterday = datetime.now() - timedelta(days=1)
    jours = ["lundi","mardi","mercredi","jeudi","vendredi","samedi","dimanche"]
    mois  = ["janvier","février","mars","avril","mai","juin",
             "juillet","août","septembre","octobre","novembre","décembre"]
    iso   = yesterday.strftime("%Y-%m-%d")
    human = f"{jours[yesterday.weekday()]} {yesterday.day} {mois[yesterday.month-1]} {yesterday.year}"
    return f"{human} ({iso})"


def get_temporal_context() -> str:
    """
    Construit le bloc temporel complet à injecter dans les prompts.

    Structure claire :
      - Date et heure actuelles
      - Hier (pour résoudre les requêtes "qu'est-ce que j'ai fait hier")
      - Événements calendrier : AUJOURD'HUI ET FUTUR UNIQUEMENT
    """
    lines = [
        f"Date et heure actuelles : {get_current_datetime_str()}",
        f"Hier : {get_yesterday_date_str()}",
    ]

    # Deadlines urgentes (<=3j) — section dédiée, visible en premier
    deadline_block = get_deadline_context()
    if deadline_block:
        lines.append("")
        lines.append(deadline_block)

    # Agenda complet dans la fenetre lookahead
    events = get_upcoming_events(days=LOOKAHEAD_DAYS)
    if events:
        lines.append("")
        lines.append(f"Agenda complet - aujourd'hui et {LOOKAHEAD_DAYS} prochains jours :")
        lines.append(format_events_for_prompt(events))
    elif not deadline_block:
        lines.append("Aucun evenement calendrier disponible.")

    return "\n".join(lines)


def get_deadline_context() -> str:
    """
    Retourne un bloc texte structuré par niveau d'urgence, pour injection dans les prompts.

    Niveaux :
      AUJOURD'HUI  — days_until == 0
      DEMAIN       — days_until == 1
      DANS 2-3J    — days_until in [2, 3]

    Retourne "" si aucun événement urgent ou calendrier non configuré.
    """
    events = get_upcoming_events(days=3)
    urgent = [e for e in events if 0 <= e["days_until"] <= 3]
    if not urgent:
        return ""

    today_items, tomorrow_items, soon_items = [], [], []
    for ev in urgent:
        time_str = f" a {ev['datetime'].strftime('%H:%M')}" if ev["datetime"] else ""
        loc_str  = f" ({ev['location']})" if ev["location"] else ""
        line     = f"- {ev['title']}{time_str}{loc_str}"
        if ev["is_today"]:
            today_items.append(line)
        elif ev["is_tomorrow"]:
            tomorrow_items.append(line)
        else:
            soon_items.append(line)

    lines = ["Deadlines et evenements proches :"]
    if today_items:
        lines.append("  AUJOURD'HUI")
        lines.extend(f"    {item}" for item in today_items)
    if tomorrow_items:
        lines.append("  DEMAIN")
        lines.extend(f"    {item}" for item in tomorrow_items)
    if soon_items:
        lines.append("  DANS 2-3 JOURS")
        lines.extend(f"    {item}" for item in soon_items)
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
        reference_date: Optional[str] = Field(
            default=None,
            description=(
                "Date ISO (YYYY-MM-DD) pour consulter un jour précis, passé ou futur. "
                "Ex: '2026-02-24' pour voir le programme du mardi. "
                "Si fournie, le paramètre days est ignoré."
            )
        )

    class GetCalendarTool(BaseTool):
        name: str = "get_calendar_events"
        description: str = (
            "Récupère les événements du calendrier personnel. "
            "Utiliser reference_date (format YYYY-MM-DD) pour un jour précis passé ou futur "
            "('quel était mon programme mardi ?', 'qu'est-ce que j'avais hier ?', etc.). "
            "Utiliser days pour une fenêtre glissante depuis aujourd'hui. "
            "Source principale pour toutes les questions de planning ou d'agenda."
        )
        args_schema: Type[BaseModel] = GetCalendarInput

        def _run(self, days: int = 14, reference_date: Optional[str] = None) -> str:
            if reference_date:
                try:
                    from datetime import date as _date
                    target = _date.fromisoformat(reference_date)
                    events = get_events_for_date(target)
                    if not events:
                        return f"Aucun événement trouvé pour le {reference_date}."
                    return format_events_for_prompt(events)
                except ValueError:
                    return f"Date invalide : {reference_date!r} (format attendu : YYYY-MM-DD)"
            events = get_upcoming_events(days=days)
            if not events:
                if not calendar_is_configured():
                    return "Aucun calendrier configuré (variable CALENDAR_SOURCE absente)."
                return f"Aucun événement dans les {days} prochains jours."
            return format_events_for_prompt(events)

except ImportError:
    # CrewAI non disponible (tests unitaires) — pas de Tool, juste les helpers
    pass