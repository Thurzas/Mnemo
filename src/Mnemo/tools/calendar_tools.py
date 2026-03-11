"""
calendar_tools.py — Temporal awareness pour Mnemo

Supporte deux sources ICS :
  - Fichier local    : get_calendar_source()=/home/matt/agenda.ics
  - URL Google Cal   : get_calendar_source()=https://calendar.google.com/calendar/ical/xxx/basic.ics
  - URL Nextcloud    : get_calendar_source()=https://nextcloud.local/remote.php/dav/calendars/...

Si get_calendar_source() est absent ou invalide, tout est silencieux — le système
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
from Mnemo.context import get_calendar_source   # source per-user via ContextVar

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
    if not get_calendar_source() or not _ICALENDAR_AVAILABLE:
        return None

    src = get_calendar_source().strip()

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
    """Retourne l'objet Calendar en cache ou recharge si TTL expiré ou fichier modifié."""
    global _cache
    now = datetime.now()

    # Vérifie si le fichier local a été modifié depuis le dernier chargement
    # (pour détecter les écritures faites par un autre processus, ex. CLI → FastAPI)
    file_modified = False
    src = get_calendar_source().strip() if get_calendar_source() else ""
    if src and not src.startswith("http") and _cache["fetched_at"] is not None:
        try:
            mtime = datetime.fromtimestamp(Path(src).stat().st_mtime)
            if mtime > _cache["fetched_at"]:
                file_modified = True
        except OSError:
            pass

    if (
        not file_modified
        and _cache["data"] is not None
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

    # Durée en minutes (pour la vue semaine du dashboard).
    # On calcule DTEND − DTSTART depuis le composant original :
    # cela donne la durée intrinsèque de l'événement, correcte pour
    # les occurrences récurrentes comme pour les événements simples.
    duration_minutes = 60
    if ev_datetime:
        dtend_raw = component.get("DTEND")
        if dtend_raw and dtstart_raw:
            orig_start = _to_datetime(dtstart_raw.dt)
            orig_end   = _to_datetime(dtend_raw.dt)
            if orig_start and orig_end and orig_end > orig_start:
                duration_minutes = max(15, int((orig_end - orig_start).total_seconds() / 60))

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
        "title"            : summary,
        "date"             : ev_date,
        "datetime"         : ev_datetime,
        "duration_minutes" : duration_minutes,
        "location"         : location,
        "description"      : description,
        "days_until"       : days_until,
        "is_today"         : days_until == 0,
        "is_tomorrow"      : days_until == 1,
        "label"            : label,
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
    Format : [mardi 10 mars - Demain] HH:MM Titre - Lieu
    Le nom du jour et la date sont inclus pour eviter que le LLM
    ait a recalculer "dans 2 jours = quel jour ?".
    """
    if not events:
        return "Aucun événement à venir dans les prochains jours."

    _jours = ["lundi","mardi","mercredi","jeudi","vendredi","samedi","dimanche"]
    _mois  = ["janvier","février","mars","avril","mai","juin",
              "juillet","août","septembre","octobre","novembre","décembre"]

    lines = []
    for ev in events:
        d        = ev["date"]
        day_name = f"{_jours[d.weekday()]} {d.day} {_mois[d.month - 1]}"
        time_str = f" à {ev['datetime'].strftime('%H:%M')}" if ev["datetime"] else ""
        loc_str  = f" — {ev['location']}" if ev["location"] else ""
        label    = ev["label"]
        lines.append(f"- [{day_name} - {label}]{time_str} {ev['title']}{loc_str}")

    return "\n".join(lines)

def format_startup_banner(events: list[dict]) -> str:
    """
    Formate un résumé compact pour l'affichage au démarrage CLI.
    Affiche les événements dans les 3 prochains jours, avec icônes colorées.
    """
    urgent = [e for e in events if 0 <= e["days_until"] <= 3]
    if not urgent:
        return ""

    lines = ["📅 Événements à venir :"]
    for ev in urgent:
        days = ev["days_until"]
        if days == 0:
            icon = "🔴"
        elif days == 1:
            icon = "🟡"
        else:
            icon = "🟢"
        time_str = f" {ev['datetime'].strftime('%H:%M')}" if ev.get("datetime") else ""
        lines.append(f"  {icon}{time_str} — {ev['title']}")
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


def get_week_dates_for_prompt() -> str:
    """
    Retourne un mapping explicite nom-de-jour -> date ISO pour les 14 prochains jours.
    Injecte comme variable dedicee dans CalendarWriteCrew pour une resolution de date fiable.
    """
    _jours = ["lundi","mardi","mercredi","jeudi","vendredi","samedi","dimanche"]
    today  = date.today()
    monday = today - timedelta(days=today.weekday())
    parts  = []
    for i in range(14):
        d     = monday + timedelta(days=i)
        label = _jours[i % 7] + (" prochain" if i >= 7 else "")
        suffix = " <- aujourd'hui" if d == today else ""
        parts.append(f"{label} = {d.isoformat()}{suffix}")
    return ", ".join(parts)


def get_temporal_context() -> str:
    """
    Construit le bloc temporel complet à injecter dans les prompts.

    Structure claire :
      - Date et heure actuelles + mapping semaine (lundi 9 → dimanche 15)
      - Hier (pour résoudre les requêtes "qu'est-ce que j'ai fait hier")
      - Deadlines urgentes (<=3j)
      - Agenda de la semaine courante (7 jours) — le LLM utilise get_calendar_events
        pour toute requête au-delà de cette fenêtre
    """
    _jours = ["lundi","mardi","mercredi","jeudi","vendredi","samedi","dimanche"]
    _mois  = ["janvier","février","mars","avril","mai","juin",
              "juillet","août","septembre","octobre","novembre","décembre"]

    today   = date.today()
    monday  = today - timedelta(days=today.weekday())
    week_parts = []
    for i in range(7):
        d = monday + timedelta(days=i)
        week_parts.append(f"{_jours[i]} {d.day} {_mois[d.month - 1]}")

    lines = [
        f"Date et heure actuelles : {get_current_datetime_str()}",
        f"Semaine en cours : {' · '.join(week_parts)}",
        f"Hier : {get_yesterday_date_str()}",
    ]

    # Deadlines urgentes (<=3j) — section dédiée, visible en premier
    deadline_block = get_deadline_context()
    if deadline_block:
        lines.append("")
        lines.append(deadline_block)

    # Agenda 7 jours (semaine courante) — fenêtre réduite pour limiter le bruit.
    # Pour les requêtes au-delà, le LLM dispose de get_calendar_events(reference_date=...).
    events = get_upcoming_events(days=7)
    if events:
        lines.append("")
        lines.append("Agenda - 7 prochains jours :")
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
    return bool(get_calendar_source()) and _ICALENDAR_AVAILABLE


# ══════════════════════════════════════════════════════════════════
# Écriture ICS (fichiers locaux uniquement)
# ══════════════════════════════════════════════════════════════════

def calendar_is_writable() -> bool:
    """True si CALENDAR_SOURCE est un fichier local (pas une URL)."""
    if not get_calendar_source() or not _ICALENDAR_AVAILABLE:
        return False
    src = get_calendar_source().strip()
    return not (src.startswith("http://") or src.startswith("https://"))


def _save_calendar(cal: "Calendar") -> None:
    """Écrit le calendrier modifié dans le fichier ICS et invalide le cache."""
    global _cache
    path = Path(get_calendar_source().strip())
    path.write_bytes(cal.to_ical())
    _cache["data"] = None
    _cache["fetched_at"] = None


def _load_writable_calendar() -> "Calendar":
    """
    Charge le calendrier pour modification.
    Crée un calendrier vide si le fichier n'existe pas encore.
    Lève ValueError si non writable.
    """
    if not calendar_is_writable():
        raise ValueError(
            "Le calendrier est en lecture seule (URL distante) ou non configuré. "
            "Configurez une source ICS locale dans votre profil utilisateur."
        )
    raw = _fetch_ics_raw()
    if raw:
        return Calendar.from_ical(raw)
    cal = Calendar()
    cal.add("PRODID", "-//Mnemo//Mnemo Calendar//FR")
    cal.add("VERSION", "2.0")
    return cal


def get_events_with_uid(days: int = 30, from_date: date | None = None) -> list[dict]:
    """
    Comme get_upcoming_events() mais inclut le champ 'uid' dans chaque événement.
    Utilisé par CalendarWriteCrew pour cibler un événement par UID.
    from_date permet de démarrer avant aujourd'hui (ex: début de semaine courante).
    """
    cal = _get_calendar()
    if cal is None:
        return []

    today = date.today()
    start = from_date if from_date is not None else today
    end   = today + timedelta(days=days)
    seen  = set()
    events: list[dict] = []

    for component in cal.walk():
        if component.name != "VEVENT":
            continue
        uid = str(component.get("UID", ""))

        if component.get("RRULE"):
            for occ_date in _expand_rrule(component, start, end):
                key = (uid, occ_date)
                if key not in seen:
                    seen.add(key)
                    ev = _make_event_dict(component, occ_date, today)
                    ev["uid"] = uid
                    events.append(ev)
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
                ev = _make_event_dict(component, ev_date, today)
                ev["uid"] = uid
                events.append(ev)

    events.sort(key=lambda e: (e["date"], e["datetime"] or datetime.min))
    return events


def format_events_with_uid(events: list[dict]) -> str:
    """
    Formate les événements avec un index numérique (#N) pour CalendarWriteCrew.
    Format : - [#N] [label] HH:MM Titre — Lieu
    L'index est résolu en UID complet par CalendarWriteCrew.run() avant toute opération.
    Les UIDs bruts (parfois 60+ caractères pour Google Calendar) ne sont jamais exposés au LLM.
    """
    if not events:
        return "Aucun événement à venir dans les 30 prochains jours."
    lines = []
    for i, ev in enumerate(events):
        time_str = f" à {ev['datetime'].strftime('%H:%M')}" if ev.get("datetime") else ""
        loc_str  = f" — {ev['location']}" if ev.get("location") else ""
        lines.append(f"- [#{i}] [{ev['label']}]{time_str} {ev['title']}{loc_str}")
    return "\n".join(lines)


def add_event(
    title: str,
    date_iso: str,
    time_str: Optional[str] = None,
    duration_minutes: int = 60,
    location: Optional[str] = None,
    description: Optional[str] = None,
) -> str:
    """
    Ajoute un VEVENT dans le fichier ICS local.
    Retourne l'UID du nouvel événement.
    Lève ValueError si le calendrier n'est pas writable.
    """
    import uuid as _uuid
    from icalendar import Event as _Event

    cal = _load_writable_calendar()
    uid = str(_uuid.uuid4())

    ev = _Event()
    ev.add("UID",     uid)
    ev.add("SUMMARY", title)
    ev.add("DTSTAMP", datetime.now())

    try:
        ev_date = date.fromisoformat(date_iso)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"Date invalide : {date_iso!r} (format attendu : YYYY-MM-DD)") from exc

    if time_str:
        try:
            h, m    = map(int, time_str.split(":"))
            dt_start = datetime(ev_date.year, ev_date.month, ev_date.day, h, m)
            dt_end   = dt_start + timedelta(minutes=duration_minutes)
            ev.add("DTSTART", dt_start)
            ev.add("DTEND",   dt_end)
        except Exception:
            ev.add("DTSTART", ev_date)
    else:
        ev.add("DTSTART", ev_date)

    if location:
        ev.add("LOCATION", location)
    if description:
        ev.add("DESCRIPTION", description)

    cal.add_component(ev)
    _save_calendar(cal)
    return uid


def _resolve_uid(components: list, uid: str) -> Optional[str]:
    """
    Résout un UID partiel vers l'UID complet d'un VEVENT.
    Essaie d'abord une correspondance exacte, puis par préfixe (min 8 chars).
    Retourne l'UID complet trouvé, ou None.
    """
    vevents = [c for c in components if getattr(c, "name", "") == "VEVENT"]
    # Correspondance exacte
    for c in vevents:
        if str(c.get("UID", "")) == uid:
            return uid
    # Correspondance par préfixe (LLM peut tronquer les longs UIDs)
    if len(uid) >= 8:
        for c in vevents:
            full = str(c.get("UID", ""))
            if full.startswith(uid):
                return full
    return None


def delete_event(uid: str) -> bool:
    """
    Supprime un VEVENT par UID dans le fichier ICS local.
    Accepte un UID partiel (préfixe) si le LLM l'a tronqué.
    Retourne True si trouvé et supprimé, False sinon.
    """
    cal = _load_writable_calendar()
    resolved = _resolve_uid(cal.subcomponents, uid)
    if resolved is None:
        return False
    before = len(cal.subcomponents)
    cal.subcomponents = [
        c for c in cal.subcomponents
        if not (getattr(c, "name", "") == "VEVENT" and str(c.get("UID", "")) == resolved)
    ]
    if len(cal.subcomponents) == before:
        return False
    _save_calendar(cal)
    return True


def update_event(uid: str, **fields) -> bool:
    """
    Modifie un VEVENT existant par UID.
    fields accepte : title, date, time, duration_minutes, location, description.
    Retourne True si trouvé et modifié, False sinon.
    """
    cal = _load_writable_calendar()

    resolved = _resolve_uid(cal.subcomponents, uid)
    if resolved is None:
        return False

    target = None
    for c in cal.subcomponents:
        if getattr(c, "name", "") == "VEVENT" and str(c.get("UID", "")) == resolved:
            target = c
            break

    if target is None:
        return False

    # SUMMARY
    if fields.get("title"):
        if "SUMMARY" in target:
            del target["SUMMARY"]
        target.add("SUMMARY", fields["title"])

    # DTSTART / DTEND
    new_date_str = fields.get("date")
    new_time_str = fields.get("time")
    dur          = int(fields.get("duration_minutes") or 60)

    if new_date_str or new_time_str:
        dtstart_raw = target.get("DTSTART")
        if new_date_str:
            ev_date = date.fromisoformat(new_date_str)
        else:
            ev_date = _to_date(dtstart_raw.dt) if dtstart_raw else date.today()

        orig_is_dt = dtstart_raw and isinstance(dtstart_raw.dt, datetime)

        if new_time_str or orig_is_dt:
            if new_time_str:
                h, m = map(int, new_time_str.split(":"))
            else:
                orig_dt = _to_datetime(dtstart_raw.dt)
                h, m    = (orig_dt.hour, orig_dt.minute) if orig_dt else (0, 0)
            dt_start = datetime(ev_date.year, ev_date.month, ev_date.day, h, m)
            dt_end   = dt_start + timedelta(minutes=dur)
            for key in ("DTSTART", "DTEND"):
                if key in target:
                    del target[key]
            target.add("DTSTART", dt_start)
            target.add("DTEND",   dt_end)
        else:
            if "DTSTART" in target:
                del target["DTSTART"]
            target.add("DTSTART", ev_date)

    # LOCATION
    if "location" in fields:
        if "LOCATION" in target:
            del target["LOCATION"]
        if fields["location"]:
            target.add("LOCATION", fields["location"])

    # DESCRIPTION
    if "description" in fields:
        if "DESCRIPTION" in target:
            del target["DESCRIPTION"]
        if fields["description"]:
            target.add("DESCRIPTION", fields["description"])

    _save_calendar(cal)
    return True


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
                    return "Aucun calendrier configuré (CALENDAR_SOURCE ou profil utilisateur absent)."
                return f"Aucun événement dans les {days} prochains jours."
            return format_events_for_prompt(events)

except ImportError:
    # CrewAI non disponible (tests unitaires) — pas de Tool, juste les helpers
    pass