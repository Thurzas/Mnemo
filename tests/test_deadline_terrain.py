#!/usr/bin/env python3
"""
test_deadline_terrain.py — Test de terrain pour la conscience des deadlines.

Simule trois scénarios sans Ollama ni CrewAI :
  S1 — Calendrier non configuré
  S2 — Événements urgents (aujourd'hui / demain / dans 3j)
  S3 — Aucun événement urgent (tous dans > 3j)

Affiche exactement ce que l'agent recevrait dans son contexte temporel,
et ce que l'utilisateur verrait au démarrage.

Usage :
  python test_deadline_terrain.py
  python test_deadline_terrain.py --ics /chemin/vers/agenda.ics  # test sur ton vrai calendrier
"""

import sys
import argparse
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

# Remonte jusqu'à src/ comme conftest.py — fonctionne que le script soit dans
# tests/ ou lancé depuis la racine du projet.
_ROOT = Path(__file__).parent.parent
_SRC  = _ROOT / "src"
sys.path.insert(0, str(_SRC) if _SRC.exists() else str(_ROOT))

# ── Mock uniquement les dépendances EXTERNES qui ne sont pas installées ──────
# Ne pas mocker Mnemo lui-même — c'est le vrai package qu'on veut importer.
import unittest.mock as mock
for mod in ['ollama', 'crewai', 'crewai.tools', 'crewai.project',
            'crewai.tools.base_tool', 'numpy']:
    sys.modules[mod] = mock.MagicMock()

from Mnemo.tools import calendar_tools as ct

# ════════════════════════════════════════════════════════════════════════════
# Helpers
# ════════════════════════════════════════════════════════════════════════════

SEPARATOR = "─" * 60

def section(title: str):
    print(f"\n{'═' * 60}")
    print(f"  {title}")
    print('═' * 60)

def subsection(title: str):
    print(f"\n  {SEPARATOR}")
    print(f"  {title}")
    print(f"  {SEPARATOR}")

def show_startup(events_today: list, events_tomorrow: list, events_soon: list,
                 events_later: list):
    """Simule l'affichage au démarrage de main.py."""
    all_events = events_today + events_tomorrow + events_soon + events_later

    subsection("Ce que l'utilisateur voit au démarrage (bannière CLI)")

    # Bannière format_startup_banner
    banner = ct.format_startup_banner(all_events)
    if banner:
        print(banner)
    else:
        print("  (aucune bannière — pas d'événements dans les 3 prochains jours)")

    # Message proactif (logique de main.py)
    urgent_today    = [e for e in all_events if e["is_today"]]
    urgent_tomorrow = [e for e in all_events if e["is_tomorrow"]]
    urgent_soon     = [e for e in all_events
                       if not e["is_today"] and not e["is_tomorrow"]
                       and e["days_until"] <= 3]

    parts = []
    if urgent_today:
        titles = ", ".join(e["title"] for e in urgent_today)
        parts.append(f"aujourd'hui : {titles}")
    if urgent_tomorrow:
        titles = ", ".join(e["title"] for e in urgent_tomorrow)
        parts.append(f"demain : {titles}")
    if urgent_soon:
        titles = ", ".join(
            f"{e['title']} ({e['label'].lower()})" for e in urgent_soon
        )
        parts.append(titles)

    if parts:
        print(f"\n💬 Mnemo : Au fait, tu as {' | '.join(parts)}.")
        print("   Tu veux qu'on en parle ou on avance sur autre chose ?")
    else:
        print("\n  (pas de message proactif)")


def show_temporal_context(all_events: list):
    """Simule le contexte temporel injecté dans les prompts LLM."""
    subsection("Ce que l'agent reçoit dans son contexte temporel (prompt LLM)")

    with patch.object(ct, 'get_upcoming_events', return_value=all_events):
        ctx = ct.get_temporal_context()
    print(ctx)


def show_deadline_context(all_events: list):
    """Affiche le bloc deadline seul."""
    subsection("Bloc deadline isolé (get_deadline_context)")

    with patch.object(ct, 'get_upcoming_events', return_value=all_events):
        block = ct.get_deadline_context()
    if block:
        print(block)
    else:
        print("  (vide — aucun événement dans les 3 prochains jours)")


# ════════════════════════════════════════════════════════════════════════════
# Fabriques d'événements
# ════════════════════════════════════════════════════════════════════════════

def make_event(days_offset: int, title: str,
               hour: int = None, location: str = None) -> dict:
    today    = date.today()
    ev_date  = today + timedelta(days=days_offset)
    ev_dt    = datetime(ev_date.year, ev_date.month, ev_date.day, hour, 0) \
               if hour is not None else None

    if days_offset == 0:
        label = "Aujourd'hui"
    elif days_offset == 1:
        label = "Demain"
    elif days_offset > 0:
        label = f"Dans {days_offset} jours"
    elif days_offset == -1:
        label = "Hier"
    else:
        label = f"Il y a {abs(days_offset)} jours"

    return {
        "title"      : title,
        "date"       : ev_date,
        "datetime"   : ev_dt,
        "location"   : location,
        "description": None,
        "days_until" : days_offset,
        "is_today"   : days_offset == 0,
        "is_tomorrow": days_offset == 1,
        "label"      : label,
    }


# ════════════════════════════════════════════════════════════════════════════
# Scénarios
# ════════════════════════════════════════════════════════════════════════════

def scenario_no_calendar():
    section("S1 — Calendrier non configuré")
    print("\n  CALENDAR_SOURCE absent ou icalendar non installé.\n")

    with patch.object(ct, 'CALENDAR_SOURCE', ''), \
         patch.object(ct, '_ICALENDAR_AVAILABLE', False):
        subsection("Contexte temporel (prompt LLM)")
        print(ct.get_temporal_context())
        subsection("Bloc deadline")
        block = ct.get_deadline_context()
        print(block if block else "  (vide)")
        subsection("Bannière démarrage")
        print("  (aucune bannière — calendrier non configuré)")


def scenario_urgent_events():
    section("S2 — Événements urgents présents")

    events_today    = [
        make_event(0, "Prospection entreprises", hour=9),
        make_event(0, "Permis de conduire",      hour=11),
        make_event(0, "Cours Wild Code School",  hour=14, location="Remote"),
        make_event(0, "Algo training",           hour=15),
    ]
    events_tomorrow = [
        make_event(1, "Réunion équipe", hour=10, location="Discord"),
    ]
    events_soon     = [
        make_event(3, "Deadline projet Mnemo"),
    ]
    events_later    = [
        make_event(7,  "Conférence IA",       hour=9),
        make_event(10, "Sprint review",       hour=14),
        make_event(14, "Renouvellement contrat"),
    ]
    all_events = events_today + events_tomorrow + events_soon + events_later

    show_startup(events_today, events_tomorrow, events_soon, events_later)
    show_deadline_context(all_events)
    show_temporal_context(all_events)


def scenario_no_urgent():
    section("S3 — Aucun événement urgent (tous > 3 jours)")

    events = [
        make_event(5,  "Sprint review",    hour=14),
        make_event(8,  "Conférence IA",    hour=9, location="Paris"),
        make_event(12, "Deadline rapport"),
    ]

    show_startup([], [], [], events)
    show_deadline_context(events)
    show_temporal_context(events)


def scenario_real_ics(ics_path: str):
    """Test sur ton vrai fichier ICS."""
    section(f"S4 — Calendrier réel : {ics_path}")

    if not ct._ICALENDAR_AVAILABLE:
        print("\n  ⚠️  icalendar non installé. Lance : pip install icalendar")
        return

    path = Path(ics_path)
    if not path.exists():
        print(f"\n  ⚠️  Fichier introuvable : {ics_path}")
        return

    import os
    original_source = ct.CALENDAR_SOURCE
    ct.CALENDAR_SOURCE = str(path)
    ct._cache["data"]       = None
    ct._cache["fetched_at"] = None

    try:
        # Événements des 14 prochains jours
        events = ct.get_upcoming_events(days=14)
        print(f"\n  {len(events)} événement(s) dans les 14 prochains jours\n")

        show_startup(
            [e for e in events if e["is_today"]],
            [e for e in events if e["is_tomorrow"]],
            [e for e in events if not e["is_today"] and not e["is_tomorrow"] and e["days_until"] <= 3],
            [e for e in events if e["days_until"] > 3],
        )
        show_deadline_context(events)
        show_temporal_context(events)

    finally:
        ct.CALENDAR_SOURCE         = original_source
        ct._cache["data"]          = None
        ct._cache["fetched_at"]    = None


# ════════════════════════════════════════════════════════════════════════════
# Main
# ════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="Test de terrain — conscience des deadlines Mnemo"
    )
    parser.add_argument(
        "--ics", metavar="PATH",
        help="Chemin vers un fichier .ics réel pour tester sur ton vrai calendrier"
    )
    parser.add_argument(
        "--scenario", metavar="N", type=int, choices=[1, 2, 3],
        help="Lancer uniquement le scénario N (1, 2 ou 3)"
    )
    args = parser.parse_args()

    print("\n🧪 Test de terrain — Conscience des deadlines Mnemo")
    print(f"   Date du test : {ct.get_current_datetime_str()}")

    if args.ics:
        scenario_real_ics(args.ics)
        return

    if args.scenario == 1 or args.scenario is None:
        scenario_no_calendar()
    if args.scenario == 2 or args.scenario is None:
        scenario_urgent_events()
    if args.scenario == 3 or args.scenario is None:
        scenario_no_urgent()

    print(f"\n{'═' * 60}")
    print("  ✅ Test de terrain terminé")
    print('═' * 60)
    print()


if __name__ == "__main__":
    main()