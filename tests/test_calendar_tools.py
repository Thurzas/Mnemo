"""
test_calendar_tools.py — Tests unitaires pour calendar_tools.py

Trois familles :
  1. Fonctions pures     — aucune dépendance externe (datetime, formatage)
  2. Fetch / Cache       — urllib et Path mockés, icalendar mockable
  3. Intégration ICS     — parsing réel avec un .ics synthétique
                           (skipés automatiquement si icalendar absent)

Zéro appel réseau, zéro Ollama.
"""

import os
import sys
import pytest
from datetime import datetime, date, timedelta, timezone
from pathlib import Path
from textwrap import dedent
from unittest.mock import patch, MagicMock, mock_open

# ── path géré par conftest.py ─────────────────────────────────────


# ── Import du module à tester ────────────────────────────────────
from Mnemo.tools import calendar_tools as ct

# Raccourcis — tous préfixés ct. explicitement
_to_date                 = ct._to_date
_to_datetime             = ct._to_datetime
_clean_text              = ct._clean_text
get_current_datetime_str = ct.get_current_datetime_str
format_events_for_prompt = ct.format_events_for_prompt
format_startup_banner    = ct.format_startup_banner
get_temporal_context     = ct.get_temporal_context
get_upcoming_events      = ct.get_upcoming_events
calendar_is_configured   = ct.calendar_is_configured

# Marqueur pour les tests qui nécessitent icalendar réellement installé
icalendar_required = pytest.mark.skipif(
    not ct._ICALENDAR_AVAILABLE,
    reason="icalendar non installé — pip install icalendar"
)


# ══════════════════════════════════════════════════════════════════
# Fixtures partagées
# ══════════════════════════════════════════════════════════════════

@pytest.fixture(autouse=True)
def reset_cache():
    """Remet le cache à zéro avant chaque test."""
    ct._cache["data"]       = None
    ct._cache["fetched_at"] = None
    yield
    ct._cache["data"]       = None
    ct._cache["fetched_at"] = None


def _make_event(days_offset: int, title: str,
                with_time: bool = False, location: str = None,
                description: str = None) -> dict:
    """Helper : construit un dict événement comme retourné par get_upcoming_events."""
    today    = date.today()
    ev_date  = today + timedelta(days=days_offset)
    ev_dt    = datetime(ev_date.year, ev_date.month, ev_date.day, 14, 0) if with_time else None
    label    = "Aujourd'hui" if days_offset == 0 else ("Demain" if days_offset == 1 else f"Dans {days_offset} jours")
    return {
        "title"      : title,
        "date"       : ev_date,
        "datetime"   : ev_dt,
        "location"   : location,
        "description": description,
        "days_until" : days_offset,
        "is_today"   : days_offset == 0,
        "is_tomorrow": days_offset == 1,
        "label"      : label,
    }


# ══════════════════════════════════════════════════════════════════
# _to_date
# ══════════════════════════════════════════════════════════════════

class TestToDate:

    def test_naive_datetime_returns_date(self):
        dt = datetime(2026, 3, 15, 10, 30)
        assert _to_date(dt) == date(2026, 3, 15)

    def test_aware_datetime_strips_timezone(self):
        dt = datetime(2026, 3, 15, 10, 30, tzinfo=timezone.utc)
        result = _to_date(dt)
        assert isinstance(result, date)
        assert result.year == 2026 and result.month == 3

    def test_date_returns_same(self):
        d = date(2026, 6, 21)
        assert _to_date(d) == d

    def test_none_returns_none(self):
        assert _to_date(None) is None

    def test_unknown_type_returns_none(self):
        assert _to_date("2026-03-15") is None
        assert _to_date(12345) is None


# ══════════════════════════════════════════════════════════════════
# _to_datetime
# ══════════════════════════════════════════════════════════════════

class TestToDatetime:

    def test_naive_datetime_unchanged(self):
        dt = datetime(2026, 3, 15, 10, 30)
        result = _to_datetime(dt)
        assert result == dt
        assert result.tzinfo is None

    def test_aware_datetime_becomes_naive(self):
        dt = datetime(2026, 3, 15, 10, 30, tzinfo=timezone.utc)
        result = _to_datetime(dt)
        assert result.tzinfo is None

    def test_date_becomes_midnight_datetime(self):
        d = date(2026, 6, 21)
        result = _to_datetime(d)
        assert isinstance(result, datetime)
        assert result.hour == 0 and result.minute == 0
        assert result.date() == d

    def test_none_returns_none(self):
        assert _to_datetime(None) is None

    def test_unknown_type_returns_none(self):
        assert _to_datetime("nope") is None


# ══════════════════════════════════════════════════════════════════
# _clean_text
# ══════════════════════════════════════════════════════════════════

class TestCleanText:

    def test_empty_string(self):
        assert _clean_text("") == ""

    def test_none_like_empty(self):
        # None converti en str("None") puis nettoyé
        # La fonction protège avec `if not text: return ""`
        assert _clean_text("") == ""

    def test_backslash_n_replaced(self):
        assert _clean_text("Réunion\\navec équipe") == "Réunion avec équipe"

    def test_backslash_comma_replaced(self):
        assert _clean_text("Paris\\, Lyon") == "Paris, Lyon"

    def test_backslash_semicolon_replaced(self):
        assert _clean_text("Note\\;importante") == "Note;importante"

    def test_strips_whitespace(self):
        assert _clean_text("  Réunion  ") == "Réunion"

    def test_normal_text_unchanged(self):
        assert _clean_text("Démo projet Mnemo") == "Démo projet Mnemo"

    def test_multiple_escapes_combined(self):
        result = _clean_text("Lundi\\, 14h\\nSalle B")
        assert result == "Lundi, 14h Salle B"


# ══════════════════════════════════════════════════════════════════
# get_current_datetime_str
# ══════════════════════════════════════════════════════════════════

class TestGetCurrentDatetimeStr:

    JOURS = ["lundi","mardi","mercredi","jeudi","vendredi","samedi","dimanche"]
    MOIS  = ["janvier","février","mars","avril","mai","juin",
             "juillet","août","septembre","octobre","novembre","décembre"]

    def test_contains_french_day(self):
        result = get_current_datetime_str()
        assert any(j in result for j in self.JOURS), f"Aucun jour français trouvé dans : {result}"

    def test_contains_french_month(self):
        result = get_current_datetime_str()
        assert any(m in result for m in self.MOIS), f"Aucun mois français trouvé dans : {result}"

    def test_contains_current_year(self):
        result = get_current_datetime_str()
        assert str(datetime.now().year) in result

    def test_contains_time_hhmm(self):
        result = get_current_datetime_str()
        import re
        assert re.search(r"\d{2}:\d{2}", result), f"Pas d'heure HH:MM dans : {result}"

    def test_format_structure(self):
        """Format attendu : '<jour> <numéro> <mois> <année>, <HH:MM>'"""
        result = get_current_datetime_str()
        assert "," in result, "La virgule séparant la date de l'heure est absente"

    def test_mocked_date_monday_january(self):
        """Vérifie que lundi 5 janvier 2026 est correctement formaté."""
        fake_now = datetime(2026, 1, 5, 9, 30)  # lundi
        with patch("Mnemo.tools.calendar_tools.datetime") as mock_dt:
            mock_dt.now.return_value = fake_now
            mock_dt.min = datetime.min
            result = get_current_datetime_str()
        assert "lundi" in result
        assert "janvier" in result
        assert "2026" in result
        assert "09:30" in result

    def test_mocked_date_friday_february(self):
        """Vérifie vendredi 27 février 2026."""
        fake_now = datetime(2026, 2, 27, 14, 45)  # vendredi
        with patch("Mnemo.tools.calendar_tools.datetime") as mock_dt:
            mock_dt.now.return_value = fake_now
            mock_dt.min = datetime.min
            result = get_current_datetime_str()
        assert "vendredi" in result
        assert "février" in result
        assert "14:45" in result

    def test_all_days_covered(self):
        """Chaque jour de la semaine doit être correctement nommé."""
        # lundi = weekday 0, dimanche = 6
        day_names = self.JOURS
        for i, expected in enumerate(day_names):
            # Trouve une date connue avec ce weekday
            base = datetime(2026, 3, 2)  # lundi 2 mars 2026
            fake = base + timedelta(days=i)
            with patch("Mnemo.tools.calendar_tools.datetime") as mock_dt:
                mock_dt.now.return_value = fake
                mock_dt.min = datetime.min
                result = get_current_datetime_str()
            assert expected in result, f"Jour {expected} (weekday {i}) non trouvé dans '{result}'"

    def test_all_months_covered(self):
        """Chaque mois doit être correctement nommé."""
        month_names = self.MOIS
        for i, expected in enumerate(month_names, start=1):
            fake = datetime(2026, i, 15, 12, 0)
            with patch("Mnemo.tools.calendar_tools.datetime") as mock_dt:
                mock_dt.now.return_value = fake
                mock_dt.min = datetime.min
                result = get_current_datetime_str()
            assert expected in result, f"Mois {expected} (mois {i}) non trouvé dans '{result}'"


# ══════════════════════════════════════════════════════════════════
# format_events_for_prompt
# ══════════════════════════════════════════════════════════════════

class TestFormatEventsForPrompt:

    def test_empty_list_returns_message(self):
        result = format_events_for_prompt([])
        assert result == "Aucun événement à venir dans les prochains jours."

    def test_event_without_time(self):
        events = [_make_event(0, "Démo projet")]
        result = format_events_for_prompt(events)
        assert "Démo projet" in result
        assert "Aujourd'hui" in result
        # Pas d'heure "à HH:MM"
        assert " à " not in result

    def test_event_with_time(self):
        events = [_make_event(1, "Réunion équipe", with_time=True)]
        result = format_events_for_prompt(events)
        assert "Réunion équipe" in result
        assert "à 14:00" in result

    def test_event_with_location(self):
        events = [_make_event(2, "Conférence", location="Salle B")]
        result = format_events_for_prompt(events)
        assert "Conférence" in result
        assert "Salle B" in result
        assert " — Salle B" in result

    def test_event_without_location_no_dash(self):
        events = [_make_event(0, "Café solo")]
        result = format_events_for_prompt(events)
        assert " — " not in result

    def test_multiple_events_all_present(self):
        events = [
            _make_event(0, "Démo"),
            _make_event(1, "Réunion"),
            _make_event(5, "Atelier"),
        ]
        result = format_events_for_prompt(events)
        assert "Démo" in result
        assert "Réunion" in result
        assert "Atelier" in result

    def test_label_format(self):
        events = [_make_event(3, "Sprint review")]
        result = format_events_for_prompt(events)
        assert "[Dans 3 jours]" in result

    def test_today_label_format(self):
        events = [_make_event(0, "Standup")]
        result = format_events_for_prompt(events)
        assert "[Aujourd'hui]" in result

    def test_tomorrow_label_format(self):
        events = [_make_event(1, "Deploy")]
        result = format_events_for_prompt(events)
        assert "[Demain]" in result


# ══════════════════════════════════════════════════════════════════
# format_startup_banner
# ══════════════════════════════════════════════════════════════════

class TestFormatStartupBanner:

    def test_empty_list_returns_empty_string(self):
        assert format_startup_banner([]) == ""

    def test_only_distant_events_no_banner(self):
        """Événements dans 4+ jours → pas de bannière."""
        events = [_make_event(4, "Futur lointain"), _make_event(10, "Encore plus loin")]
        assert format_startup_banner(events) == ""

    def test_today_event_shows_red_icon(self):
        events = [_make_event(0, "Démo aujourd'hui")]
        result = format_startup_banner(events)
        assert "🔴" in result
        assert "Démo aujourd'hui" in result

    def test_tomorrow_event_shows_yellow_icon(self):
        events = [_make_event(1, "Réunion demain")]
        result = format_startup_banner(events)
        assert "🟡" in result
        assert "Réunion demain" in result

    def test_in_2_days_shows_green_icon(self):
        events = [_make_event(2, "Sprint dans 2j")]
        result = format_startup_banner(events)
        assert "🟢" in result
        assert "Sprint dans 2j" in result

    def test_in_3_days_still_urgent(self):
        events = [_make_event(3, "Deadline J-3")]
        result = format_startup_banner(events)
        assert result != "", "J-3 doit apparaître dans la bannière"
        assert "🟢" in result

    def test_in_4_days_not_urgent(self):
        events = [_make_event(4, "Pas urgent")]
        result = format_startup_banner(events)
        assert result == ""

    def test_event_with_time_shows_time(self):
        events = [_make_event(0, "Stand-up", with_time=True)]
        result = format_startup_banner(events)
        assert "14:00" in result

    def test_event_without_time_no_time(self):
        events = [_make_event(0, "Journée off")]
        result = format_startup_banner(events)
        # Le titre doit être là mais pas d'heure "HH:MM" dans la bannière
        assert "Journée off" in result
        import re
        # Vérifie qu'il n'y a pas de pattern HH:MM
        assert not re.search(r"\b\d{2}:\d{2}\b", result)

    def test_banner_header_present(self):
        events = [_make_event(0, "Event")]
        result = format_startup_banner(events)
        assert "📅" in result
        assert "Événements à venir" in result

    def test_mixed_urgent_and_distant(self):
        """Seuls les events ≤ 3j doivent apparaître."""
        events = [
            _make_event(1, "Urgent"),
            _make_event(7, "Pas urgent"),
        ]
        result = format_startup_banner(events)
        assert "Urgent" in result
        assert "Pas urgent" not in result

    def test_multiple_urgent_all_shown(self):
        events = [
            _make_event(0, "Aujourd'hui"),
            _make_event(1, "Demain"),
            _make_event(2, "Après-demain"),
        ]
        result = format_startup_banner(events)
        assert "Aujourd'hui" in result
        assert "Demain" in result
        assert "Après-demain" in result
        assert result.count("🔴") == 1
        assert result.count("🟡") == 1
        assert result.count("🟢") == 1


# ══════════════════════════════════════════════════════════════════
# get_temporal_context
# ══════════════════════════════════════════════════════════════════

class TestGetTemporalContext:

    def test_contains_date_line(self):
        result = get_temporal_context()
        assert "Date et heure actuelles" in result

    def test_without_calendar_says_no_events(self):
        with patch.object(ct, "get_upcoming_events", return_value=[]):
            result = get_temporal_context()
        assert "Aucun evenement calendrier disponible" in result

    def test_with_events_lists_them(self):
        fake_events = [_make_event(0, "Démo"), _make_event(1, "Réunion")]
        with patch.object(ct, "get_upcoming_events", return_value=fake_events):
            result = get_temporal_context()
        assert "Démo" in result
        assert "Réunion" in result
        assert "Agenda complet" in result

    def test_with_events_shows_lookahead_days(self):
        fake_events = [_make_event(0, "Event")]
        with patch.object(ct, "get_upcoming_events", return_value=fake_events):
            result = get_temporal_context()
        assert str(ct.LOOKAHEAD_DAYS) in result


# ══════════════════════════════════════════════════════════════════
# calendar_is_configured
# ══════════════════════════════════════════════════════════════════

class TestCalendarIsConfigured:

    def test_no_source_returns_false(self, monkeypatch):
        monkeypatch.setattr(ct, "CALENDAR_SOURCE", "")
        assert calendar_is_configured() is False

    def test_source_without_icalendar_returns_false(self, monkeypatch):
        monkeypatch.setattr(ct, "CALENDAR_SOURCE", "/path/to/agenda.ics")
        monkeypatch.setattr(ct, "_ICALENDAR_AVAILABLE", False)
        assert calendar_is_configured() is False

    def test_source_with_icalendar_returns_true(self, monkeypatch):
        monkeypatch.setattr(ct, "CALENDAR_SOURCE", "/path/to/agenda.ics")
        monkeypatch.setattr(ct, "_ICALENDAR_AVAILABLE", True)
        assert calendar_is_configured() is True

    def test_url_source_with_icalendar_returns_true(self, monkeypatch):
        monkeypatch.setattr(ct, "CALENDAR_SOURCE", "https://calendar.google.com/ical/xxx/basic.ics")
        monkeypatch.setattr(ct, "_ICALENDAR_AVAILABLE", True)
        assert calendar_is_configured() is True


# ══════════════════════════════════════════════════════════════════
# _fetch_ics_raw
# ══════════════════════════════════════════════════════════════════

class TestFetchIcsRaw:

    def test_no_source_returns_none(self, monkeypatch):
        monkeypatch.setattr(ct, "CALENDAR_SOURCE", "")
        assert ct._fetch_ics_raw() is None

    def test_icalendar_unavailable_returns_none(self, monkeypatch):
        monkeypatch.setattr(ct, "CALENDAR_SOURCE", "/agenda.ics")
        monkeypatch.setattr(ct, "_ICALENDAR_AVAILABLE", False)
        assert ct._fetch_ics_raw() is None

    def test_local_file_exists_returns_bytes(self, tmp_path, monkeypatch):
        ics_file = tmp_path / "agenda.ics"
        ics_file.write_bytes(b"BEGIN:VCALENDAR\nEND:VCALENDAR")
        monkeypatch.setattr(ct, "CALENDAR_SOURCE", str(ics_file))
        monkeypatch.setattr(ct, "_ICALENDAR_AVAILABLE", True)
        result = ct._fetch_ics_raw()
        assert result == b"BEGIN:VCALENDAR\nEND:VCALENDAR"

    def test_local_file_missing_returns_none(self, monkeypatch):
        monkeypatch.setattr(ct, "CALENDAR_SOURCE", "/nonexistent/agenda.ics")
        monkeypatch.setattr(ct, "_ICALENDAR_AVAILABLE", True)
        assert ct._fetch_ics_raw() is None

    def test_url_source_returns_bytes(self, monkeypatch):
        monkeypatch.setattr(ct, "CALENDAR_SOURCE", "https://example.com/cal.ics")
        monkeypatch.setattr(ct, "_ICALENDAR_AVAILABLE", True)
        fake_response = MagicMock()
        fake_response.read.return_value = b"BEGIN:VCALENDAR\nEND:VCALENDAR"
        fake_response.__enter__ = lambda s: fake_response
        fake_response.__exit__  = MagicMock(return_value=False)
        with patch("urllib.request.urlopen", return_value=fake_response):
            result = ct._fetch_ics_raw()
        assert result == b"BEGIN:VCALENDAR\nEND:VCALENDAR"

    def test_url_network_error_returns_none(self, monkeypatch):
        monkeypatch.setattr(ct, "CALENDAR_SOURCE", "https://example.com/cal.ics")
        monkeypatch.setattr(ct, "_ICALENDAR_AVAILABLE", True)
        with patch("urllib.request.urlopen", side_effect=OSError("Network error")):
            result = ct._fetch_ics_raw()
        assert result is None

    def test_url_timeout_returns_none(self, monkeypatch):
        monkeypatch.setattr(ct, "CALENDAR_SOURCE", "https://example.com/cal.ics")
        monkeypatch.setattr(ct, "_ICALENDAR_AVAILABLE", True)
        import urllib.error
        with patch("urllib.request.urlopen", side_effect=TimeoutError("timeout")):
            result = ct._fetch_ics_raw()
        assert result is None


# ══════════════════════════════════════════════════════════════════
# _get_calendar — cache
# ══════════════════════════════════════════════════════════════════

class TestGetCalendarCache:

    def test_cache_miss_calls_fetch(self, monkeypatch):
        monkeypatch.setattr(ct, "_ICALENDAR_AVAILABLE", True)
        fake_cal = MagicMock()
        with patch.object(ct, "_fetch_ics_raw", return_value=b"raw") as mock_fetch:
            with patch("Mnemo.tools.calendar_tools.Calendar") as mock_cal_cls:
                mock_cal_cls.from_ical.return_value = fake_cal
                result = ct._get_calendar()
        mock_fetch.assert_called_once()
        assert result is fake_cal

    def test_cache_hit_skips_fetch(self, monkeypatch):
        """Cache valide → _fetch_ics_raw ne doit pas être rappelé."""
        monkeypatch.setattr(ct, "_ICALENDAR_AVAILABLE", True)
        fake_cal = MagicMock()
        ct._cache["data"]       = fake_cal
        ct._cache["fetched_at"] = datetime.now()

        with patch.object(ct, "_fetch_ics_raw") as mock_fetch:
            result = ct._get_calendar()

        mock_fetch.assert_not_called()
        assert result is fake_cal

    def test_cache_expired_refetches(self, monkeypatch):
        """Cache expiré → doit relancer le fetch."""
        monkeypatch.setattr(ct, "_ICALENDAR_AVAILABLE", True)
        monkeypatch.setattr(ct, "CACHE_TTL_SECONDS", 0)  # TTL à 0 → toujours expiré
        fake_cal = MagicMock()
        ct._cache["data"]       = MagicMock()  # ancien cache
        ct._cache["fetched_at"] = datetime(2000, 1, 1)  # très vieux

        with patch.object(ct, "_fetch_ics_raw", return_value=b"raw") as mock_fetch:
            with patch("Mnemo.tools.calendar_tools.Calendar") as mock_cal_cls:
                mock_cal_cls.from_ical.return_value = fake_cal
                result = ct._get_calendar()

        mock_fetch.assert_called_once()
        assert result is fake_cal

    def test_fetch_returns_none_returns_none(self, monkeypatch):
        with patch.object(ct, "_fetch_ics_raw", return_value=None):
            assert ct._get_calendar() is None

    def test_parse_error_returns_none(self, monkeypatch):
        monkeypatch.setattr(ct, "_ICALENDAR_AVAILABLE", True)
        with patch.object(ct, "_fetch_ics_raw", return_value=b"invalid ics"):
            with patch("Mnemo.tools.calendar_tools.Calendar") as mock_cal_cls:
                mock_cal_cls.from_ical.side_effect = Exception("parse error")
                result = ct._get_calendar()
        assert result is None

    def test_successful_fetch_updates_cache(self, monkeypatch):
        monkeypatch.setattr(ct, "_ICALENDAR_AVAILABLE", True)
        fake_cal = MagicMock()
        with patch.object(ct, "_fetch_ics_raw", return_value=b"raw"):
            with patch("Mnemo.tools.calendar_tools.Calendar") as mock_cal_cls:
                mock_cal_cls.from_ical.return_value = fake_cal
                ct._get_calendar()
        assert ct._cache["data"] is fake_cal
        assert ct._cache["fetched_at"] is not None


# ══════════════════════════════════════════════════════════════════
# get_upcoming_events — mock Calendar
# ══════════════════════════════════════════════════════════════════

class TestGetUpcomingEvents:

    def _make_vevent(self, dt_val, summary="Event", location="", description=""):
        """Construit un faux composant VEVENT pour _get_calendar mocké."""
        component = MagicMock()
        component.name = "VEVENT"

        dtstart = MagicMock()
        dtstart.dt = dt_val
        component.get = lambda key, default=None: {
            "DTSTART"    : dtstart,
            "SUMMARY"    : summary,
            "LOCATION"   : location,
            "DESCRIPTION": description,
        }.get(key, default)
        return component

    def _make_calendar(self, components):
        """Construit un faux Calendar qui walk() les composants donnés."""
        cal = MagicMock()
        # walk() retourne d'abord le composant racine (VCALENDAR), puis les VEVENTs
        vcal = MagicMock()
        vcal.name = "VCALENDAR"
        cal.walk.return_value = [vcal] + components
        return cal

    def test_no_calendar_returns_empty(self):
        with patch.object(ct, "_get_calendar", return_value=None):
            assert get_upcoming_events() == []

    def test_event_today_included(self):
        today_dt = datetime.combine(date.today(), datetime.min.time()).replace(hour=10)
        vevent = self._make_vevent(today_dt, "Stand-up")
        cal = self._make_calendar([vevent])
        with patch.object(ct, "_get_calendar", return_value=cal):
            events = get_upcoming_events(days=7)
        assert len(events) == 1
        assert events[0]["title"] == "Stand-up"
        assert events[0]["is_today"] is True

    def test_event_tomorrow_included(self):
        tomorrow = datetime.combine(date.today() + timedelta(days=1), datetime.min.time())
        vevent = self._make_vevent(tomorrow, "Réunion")
        cal = self._make_calendar([vevent])
        with patch.object(ct, "_get_calendar", return_value=cal):
            events = get_upcoming_events(days=7)
        assert len(events) == 1
        assert events[0]["is_tomorrow"] is True

    def test_event_past_excluded(self):
        yesterday = datetime.combine(date.today() - timedelta(days=1), datetime.min.time())
        vevent = self._make_vevent(yesterday, "Passé")
        cal = self._make_calendar([vevent])
        with patch.object(ct, "_get_calendar", return_value=cal):
            events = get_upcoming_events(days=7)
        assert events == []

    def test_event_beyond_window_excluded(self):
        far_future = datetime.combine(date.today() + timedelta(days=30), datetime.min.time())
        vevent = self._make_vevent(far_future, "Lointain")
        cal = self._make_calendar([vevent])
        with patch.object(ct, "_get_calendar", return_value=cal):
            events = get_upcoming_events(days=7)
        assert events == []

    def test_all_day_event_has_no_datetime(self):
        """Événement jour entier (date, pas datetime) → ev['datetime'] doit être None."""
        today_date = date.today()  # type date, pas datetime
        vevent = self._make_vevent(today_date, "Journée off")
        cal = self._make_calendar([vevent])
        with patch.object(ct, "_get_calendar", return_value=cal):
            events = get_upcoming_events(days=7)
        assert len(events) == 1
        assert events[0]["datetime"] is None

    def test_timed_event_has_datetime(self):
        today_dt = datetime(date.today().year, date.today().month, date.today().day, 14, 30)
        vevent = self._make_vevent(today_dt, "Call")
        cal = self._make_calendar([vevent])
        with patch.object(ct, "_get_calendar", return_value=cal):
            events = get_upcoming_events(days=7)
        assert events[0]["datetime"] is not None
        assert events[0]["datetime"].hour == 14

    def test_events_sorted_by_date(self):
        base = date.today()
        ev1 = self._make_vevent(datetime.combine(base + timedelta(days=2), datetime.min.time()), "B")
        ev2 = self._make_vevent(datetime.combine(base + timedelta(days=1), datetime.min.time()), "A")
        ev3 = self._make_vevent(datetime.combine(base, datetime.min.time()), "C")
        cal = self._make_calendar([ev1, ev2, ev3])
        with patch.object(ct, "_get_calendar", return_value=cal):
            events = get_upcoming_events(days=7)
        titles = [e["title"] for e in events]
        assert titles == ["C", "A", "B"]

    def test_event_with_location(self):
        today_dt = datetime.combine(date.today(), datetime.min.time())
        vevent = self._make_vevent(today_dt, "Conf", location="Salle A")
        cal = self._make_calendar([vevent])
        with patch.object(ct, "_get_calendar", return_value=cal):
            events = get_upcoming_events(days=7)
        assert events[0]["location"] == "Salle A"

    def test_event_empty_location_is_none(self):
        today_dt = datetime.combine(date.today(), datetime.min.time())
        vevent = self._make_vevent(today_dt, "Solo", location="")
        cal = self._make_calendar([vevent])
        with patch.object(ct, "_get_calendar", return_value=cal):
            events = get_upcoming_events(days=7)
        assert events[0]["location"] is None

    def test_days_until_correct(self):
        in_3 = datetime.combine(date.today() + timedelta(days=3), datetime.min.time())
        vevent = self._make_vevent(in_3, "Event J+3")
        cal = self._make_calendar([vevent])
        with patch.object(ct, "_get_calendar", return_value=cal):
            events = get_upcoming_events(days=7)
        assert events[0]["days_until"] == 3
        assert events[0]["label"] == "Dans 3 jours"

    def test_non_vevent_components_ignored(self):
        """Les composants VTIMEZONE, VALARM etc. doivent être ignorés."""
        vtimezone = MagicMock()
        vtimezone.name = "VTIMEZONE"
        today_dt = datetime.combine(date.today(), datetime.min.time())
        vevent = self._make_vevent(today_dt, "Real event")
        cal = self._make_calendar([vtimezone, vevent])
        with patch.object(ct, "_get_calendar", return_value=cal):
            events = get_upcoming_events(days=7)
        assert len(events) == 1
        assert events[0]["title"] == "Real event"

    def test_event_missing_dtstart_ignored(self):
        component = MagicMock()
        component.name = "VEVENT"
        component.get = lambda key, default=None: None  # pas de DTSTART
        cal = self._make_calendar([component])
        with patch.object(ct, "_get_calendar", return_value=cal):
            events = get_upcoming_events(days=7)
        assert events == []


# ══════════════════════════════════════════════════════════════════
# Intégration ICS réelle (skipée si icalendar absent)
# ══════════════════════════════════════════════════════════════════

ICS_SAMPLE = dedent("""\
    BEGIN:VCALENDAR
    VERSION:2.0
    PRODID:-//Test//Test//FR
    BEGIN:VEVENT
    DTSTART:{today}T100000
    DTEND:{today}T110000
    SUMMARY:Stand-up quotidien
    LOCATION:Remote
    DESCRIPTION:Point journalier
    END:VEVENT
    BEGIN:VEVENT
    DTSTART:{tomorrow}
    DTEND:{tomorrow}
    SUMMARY:Journée off
    END:VEVENT
    BEGIN:VEVENT
    DTSTART:{past}T090000
    DTEND:{past}T100000
    SUMMARY:Réunion passée
    END:VEVENT
    BEGIN:VEVENT
    DTSTART:{far}T150000
    DTEND:{far}T160000
    SUMMARY:Événement lointain
    END:VEVENT
    END:VCALENDAR
""")


@icalendar_required
class TestICSIntegration:
    """Tests sur parsing ICS réel — nécessite pip install icalendar."""

    @pytest.fixture
    def ics_file(self, tmp_path):
        today    = date.today()
        tomorrow = today + timedelta(days=1)
        past     = today - timedelta(days=2)
        far      = today + timedelta(days=20)
        content  = ICS_SAMPLE.format(
            today    = today.strftime("%Y%m%d"),
            tomorrow = tomorrow.strftime("%Y%m%d"),
            past     = past.strftime("%Y%m%d"),
            far      = far.strftime("%Y%m%d"),
        )
        f = tmp_path / "test.ics"
        f.write_text(content, encoding="utf-8")
        return f

    def test_parses_event_today(self, ics_file, monkeypatch):
        monkeypatch.setattr(ct, "CALENDAR_SOURCE", str(ics_file))
        monkeypatch.setattr(ct, "_ICALENDAR_AVAILABLE", True)
        events = get_upcoming_events(days=7)
        titles = [e["title"] for e in events]
        assert "Stand-up quotidien" in titles

    def test_parses_all_day_event(self, ics_file, monkeypatch):
        monkeypatch.setattr(ct, "CALENDAR_SOURCE", str(ics_file))
        monkeypatch.setattr(ct, "_ICALENDAR_AVAILABLE", True)
        events = get_upcoming_events(days=7)
        all_day = [e for e in events if e["title"] == "Journée off"]
        assert len(all_day) == 1
        assert all_day[0]["datetime"] is None

    def test_excludes_past_event(self, ics_file, monkeypatch):
        monkeypatch.setattr(ct, "CALENDAR_SOURCE", str(ics_file))
        monkeypatch.setattr(ct, "_ICALENDAR_AVAILABLE", True)
        events = get_upcoming_events(days=7)
        titles = [e["title"] for e in events]
        assert "Réunion passée" not in titles

    def test_excludes_far_future_event(self, ics_file, monkeypatch):
        monkeypatch.setattr(ct, "CALENDAR_SOURCE", str(ics_file))
        monkeypatch.setattr(ct, "_ICALENDAR_AVAILABLE", True)
        events = get_upcoming_events(days=7)
        titles = [e["title"] for e in events]
        assert "Événement lointain" not in titles

    def test_location_parsed(self, ics_file, monkeypatch):
        monkeypatch.setattr(ct, "CALENDAR_SOURCE", str(ics_file))
        monkeypatch.setattr(ct, "_ICALENDAR_AVAILABLE", True)
        events = get_upcoming_events(days=7)
        standup = next(e for e in events if e["title"] == "Stand-up quotidien")
        assert standup["location"] == "Remote"

    def test_full_prompt_output(self, ics_file, monkeypatch):
        monkeypatch.setattr(ct, "CALENDAR_SOURCE", str(ics_file))
        monkeypatch.setattr(ct, "_ICALENDAR_AVAILABLE", True)
        events = get_upcoming_events(days=7)
        prompt = format_events_for_prompt(events)
        assert "Stand-up quotidien" in prompt
        assert "Remote" in prompt
        assert "à 10:00" in prompt

    def test_file_local_path_works(self, ics_file, monkeypatch):
        monkeypatch.setattr(ct, "CALENDAR_SOURCE", str(ics_file))
        monkeypatch.setattr(ct, "_ICALENDAR_AVAILABLE", True)
        raw = ct._fetch_ics_raw()
        assert raw is not None
        assert b"VCALENDAR" in raw


# ══════════════════════════════════════════════════════════════════
# get_deadline_context
# ══════════════════════════════════════════════════════════════════

class TestGetDeadlineContext:

    def _ev(self, days_offset, title, hour=None, location=None):
        today   = date.today()
        ev_date = today + timedelta(days=days_offset)
        ev_dt   = datetime(ev_date.year, ev_date.month, ev_date.day, hour, 0) \
                  if hour is not None else None
        if days_offset == 0:   label = "Aujourd'hui"
        elif days_offset == 1: label = "Demain"
        else:                  label = f"Dans {days_offset} jours"
        return {
            "title": title, "date": ev_date, "datetime": ev_dt,
            "location": location, "description": None,
            "days_until": days_offset,
            "is_today": days_offset == 0,
            "is_tomorrow": days_offset == 1,
            "label": label,
        }

    def test_empty_when_no_events(self):
        with patch.object(ct, "get_upcoming_events", return_value=[]):
            assert ct.get_deadline_context() == ""

    def test_empty_when_all_events_beyond_3_days(self):
        events = [self._ev(4, "Lointain"), self._ev(7, "Encore plus loin")]
        with patch.object(ct, "get_upcoming_events", return_value=events):
            assert ct.get_deadline_context() == ""

    def test_today_section_present(self):
        events = [self._ev(0, "Standup")]
        with patch.object(ct, "get_upcoming_events", return_value=events):
            result = ct.get_deadline_context()
        assert "AUJOURD'HUI" in result
        assert "Standup" in result

    def test_tomorrow_section_present(self):
        events = [self._ev(1, "Réunion")]
        with patch.object(ct, "get_upcoming_events", return_value=events):
            result = ct.get_deadline_context()
        assert "DEMAIN" in result
        assert "Réunion" in result

    def test_soon_section_present(self):
        events = [self._ev(2, "Sprint"), self._ev(3, "Demo")]
        with patch.object(ct, "get_upcoming_events", return_value=events):
            result = ct.get_deadline_context()
        assert "DANS 2-3 JOURS" in result
        assert "Sprint" in result
        assert "Demo" in result

    def test_three_levels_all_present(self):
        events = [
            self._ev(0, "Aujourd'hui"),
            self._ev(1, "Demain"),
            self._ev(3, "Bientot"),
        ]
        with patch.object(ct, "get_upcoming_events", return_value=events):
            result = ct.get_deadline_context()
        assert "AUJOURD'HUI" in result
        assert "DEMAIN" in result
        assert "DANS 2-3 JOURS" in result

    def test_only_sections_with_events_shown(self):
        """Si aucun événement demain, la section DEMAIN ne doit pas apparaître."""
        events = [self._ev(0, "Standup"), self._ev(3, "Deadline")]
        with patch.object(ct, "get_upcoming_events", return_value=events):
            result = ct.get_deadline_context()
        assert "AUJOURD'HUI" in result
        assert "DANS 2-3 JOURS" in result
        assert "DEMAIN" not in result

    def test_time_shown_when_present(self):
        events = [self._ev(0, "Cours", hour=14)]
        with patch.object(ct, "get_upcoming_events", return_value=events):
            result = ct.get_deadline_context()
        assert "14:00" in result

    def test_no_time_for_all_day_event(self):
        events = [self._ev(0, "Journée off", hour=None)]
        with patch.object(ct, "get_upcoming_events", return_value=events):
            result = ct.get_deadline_context()
        import re
        assert not re.search(r"\b\d{2}:\d{2}\b", result)

    def test_location_shown_when_present(self):
        events = [self._ev(1, "Réunion", hour=10, location="Discord")]
        with patch.object(ct, "get_upcoming_events", return_value=events):
            result = ct.get_deadline_context()
        assert "Discord" in result

    def test_no_location_shown_when_absent(self):
        events = [self._ev(0, "Solo")]
        with patch.object(ct, "get_upcoming_events", return_value=events):
            result = ct.get_deadline_context()
        assert "()" not in result

    def test_multiple_events_same_day_all_listed(self):
        events = [
            self._ev(0, "Standup",    hour=9),
            self._ev(0, "Permis",     hour=11),
            self._ev(0, "Cours WCS",  hour=14),
        ]
        with patch.object(ct, "get_upcoming_events", return_value=events):
            result = ct.get_deadline_context()
        assert "Standup" in result
        assert "Permis" in result
        assert "Cours WCS" in result
        assert result.count("AUJOURD'HUI") == 1   # un seul header

    def test_event_at_exactly_3_days_included(self):
        """J+3 est la limite — doit être inclus."""
        events = [self._ev(3, "Limite")]
        with patch.object(ct, "get_upcoming_events", return_value=events):
            assert "Limite" in ct.get_deadline_context()

    def test_event_at_4_days_excluded(self):
        """J+4 est hors fenêtre — ne doit pas apparaître."""
        events = [self._ev(4, "Trop loin")]
        with patch.object(ct, "get_upcoming_events", return_value=events):
            assert ct.get_deadline_context() == ""

    def test_returns_string(self):
        with patch.object(ct, "get_upcoming_events", return_value=[]):
            assert isinstance(ct.get_deadline_context(), str)


# ══════════════════════════════════════════════════════════════════
# get_temporal_context — avec deadlines
# ══════════════════════════════════════════════════════════════════

class TestGetTemporalContextWithDeadlines:

    def _ev(self, days_offset, title):
        today   = date.today()
        ev_date = today + timedelta(days=days_offset)
        label   = "Aujourd'hui" if days_offset == 0 else \
                  "Demain"      if days_offset == 1 else \
                  f"Dans {days_offset} jours"
        return {
            "title": title, "date": ev_date, "datetime": None,
            "location": None, "description": None,
            "days_until": days_offset,
            "is_today": days_offset == 0, "is_tomorrow": days_offset == 1,
            "label": label,
        }

    def test_deadline_block_appears_before_agenda(self):
        """Le bloc deadline doit précéder l'agenda complet."""
        events = [self._ev(0, "Urgent"), self._ev(7, "Lointain")]
        with patch.object(ct, "get_upcoming_events", return_value=events):
            result = ct.get_temporal_context()
        idx_deadline = result.index("Deadlines")
        idx_agenda   = result.index("Agenda complet")
        assert idx_deadline < idx_agenda

    def test_no_deadline_block_when_no_urgent(self):
        """Sans événement urgent, pas de section Deadlines dans le contexte."""
        events = [self._ev(5, "Pas urgent"), self._ev(10, "Encore moins")]
        with patch.object(ct, "get_upcoming_events", return_value=events):
            result = ct.get_temporal_context()
        assert "Deadlines" not in result
        assert "Agenda complet" in result

    def test_agenda_complet_label_present(self):
        """Le nouveau label 'Agenda complet' remplace l'ancien."""
        events = [self._ev(2, "Event")]
        with patch.object(ct, "get_upcoming_events", return_value=events):
            result = ct.get_temporal_context()
        assert "Agenda complet" in result

    def test_no_agenda_when_no_events_no_deadlines(self):
        """Sans aucun événement, ni deadline ni agenda dans le contexte."""
        with patch.object(ct, "get_upcoming_events", return_value=[]):
            result = ct.get_temporal_context()
        assert "Deadlines" not in result
        assert "Agenda complet" not in result
        assert "Aucun evenement" in result or "Aucun événement" in result or \
               "calendrier" in result.lower()

    def test_hier_always_present(self):
        """La ligne 'Hier :' doit toujours être présente dans le contexte."""
        with patch.object(ct, "get_upcoming_events", return_value=[]):
            result = ct.get_temporal_context()
        assert "Hier :" in result

    def test_date_always_present(self):
        with patch.object(ct, "get_upcoming_events", return_value=[]):
            result = ct.get_temporal_context()
        assert "Date et heure actuelles" in result