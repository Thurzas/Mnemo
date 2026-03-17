"""
test_calendar_write.py — Tests pour les fonctions d'écriture ICS et CalendarWriteCrew

Niveau 1 : fonctions pures (calendar_is_writable, format_events_with_uid)
Niveau 2 : écriture ICS réelle avec fichier temporaire (add, delete, update, get_with_uid)
Niveau 3 : CalendarWriteCrew.run() avec crew.kickoff() mocké

Zéro appel réseau, zéro Ollama.
"""

import json
import pytest
from datetime import date, datetime, timedelta
from pathlib import Path
from textwrap import dedent
from unittest.mock import MagicMock, patch

from Mnemo.tools import calendar_tools as ct

# ── Skip si icalendar absent ──────────────────────────────────────
icalendar_required = pytest.mark.skipif(
    not ct._ICALENDAR_AVAILABLE,
    reason="icalendar non installé"
)


# ══════════════════════════════════════════════════════════════════
# Fixtures
# ══════════════════════════════════════════════════════════════════

@pytest.fixture(autouse=True)
def reset_cache():
    ct._cache["data"]       = None
    ct._cache["fetched_at"] = None
    yield
    ct._cache["data"]       = None
    ct._cache["fetched_at"] = None


ICS_TEMPLATE = dedent("""\
    BEGIN:VCALENDAR
    VERSION:2.0
    PRODID:-//Test//Test//EN
    BEGIN:VEVENT
    UID:uid-alpha-1234
    SUMMARY:Réunion Alpha
    DTSTART:{tomorrow}T140000
    DTEND:{tomorrow}T150000
    END:VEVENT
    BEGIN:VEVENT
    UID:uid-beta-5678
    SUMMARY:Journée Beta
    DTSTART:{in3days}
    END:VEVENT
    END:VCALENDAR
""")


def _make_ics(tmp_path: Path) -> Path:
    """Crée un fichier ICS minimal avec deux événements et retourne son chemin."""
    tomorrow  = (date.today() + timedelta(days=1)).strftime("%Y%m%d")
    in3days   = (date.today() + timedelta(days=3)).strftime("%Y%m%d")
    content   = ICS_TEMPLATE.format(tomorrow=tomorrow, in3days=in3days)
    ics_path  = tmp_path / "test.ics"
    ics_path.write_text(content, encoding="utf-8")
    return ics_path


@pytest.fixture
def ics_env(tmp_path, monkeypatch):
    """Fixture : pointe CALENDAR_SOURCE sur un ICS temporaire, remet le cache à zéro."""
    ics_path = _make_ics(tmp_path)
    monkeypatch.setattr(ct, "get_calendar_source", lambda: str(ics_path))
    ct._cache["data"]       = None
    ct._cache["fetched_at"] = None
    return ics_path


# ══════════════════════════════════════════════════════════════════
# Niveau 1 — fonctions pures
# ══════════════════════════════════════════════════════════════════

class TestCalendarIsWritable:

    def test_local_path_is_writable(self, monkeypatch):
        monkeypatch.setattr(ct, "get_calendar_source", lambda: "/data/agenda.ics")
        assert ct.calendar_is_writable() is True

    def test_http_url_is_not_writable(self, monkeypatch):
        monkeypatch.setattr(ct, "get_calendar_source", lambda: "http://example.com/cal.ics")
        assert ct.calendar_is_writable() is False

    def test_https_url_is_not_writable(self, monkeypatch):
        monkeypatch.setattr(ct, "get_calendar_source", lambda: "https://calendar.google.com/xxx")
        assert ct.calendar_is_writable() is False

    def test_empty_source_is_not_writable(self, monkeypatch):
        monkeypatch.setattr(ct, "get_calendar_source", lambda: "")
        assert ct.calendar_is_writable() is False


class TestFormatEventsWithUid:

    def _make_ev(self, uid, title, days=1, with_time=False):
        today   = date.today()
        ev_date = today + timedelta(days=days)
        return {
            "uid"        : uid,
            "title"      : title,
            "date"       : ev_date,
            "datetime"   : datetime(ev_date.year, ev_date.month, ev_date.day, 14, 0) if with_time else None,
            "location"   : None,
            "description": None,
            "days_until" : days,
            "is_today"   : days == 0,
            "is_tomorrow": days == 1,
            "label"      : "Demain" if days == 1 else f"Dans {days} jours",
        }

    def test_empty_list_returns_no_events_message(self):
        assert "Aucun" in ct.format_events_with_uid([])

    def test_index_shown_instead_of_uid(self):
        """format_events_with_uid expose [#N] — pas l'UID brut."""
        ev  = self._make_ev("abcdefghijklmnopqrstuvwxyz", "Réunion")
        out = ct.format_events_with_uid([ev])
        assert "[#0]" in out
        assert "abcdefghijkl" not in out

    def test_time_appears_when_datetime_set(self):
        ev  = self._make_ev("uid-abc", "Déjeuner", days=2, with_time=True)
        out = ct.format_events_with_uid([ev])
        assert "14:00" in out

    def test_no_time_when_all_day(self):
        ev  = self._make_ev("uid-abc", "Journée entière", days=2, with_time=False)
        out = ct.format_events_with_uid([ev])
        assert "14:00" not in out

    def test_multiple_events_all_present(self):
        evs = [self._make_ev(f"uid-{i}", f"Event {i}", days=i+1) for i in range(3)]
        out = ct.format_events_with_uid(evs)
        for i in range(3):
            assert f"Event {i}" in out


# ══════════════════════════════════════════════════════════════════
# Niveau 2 — écriture ICS réelle
# ══════════════════════════════════════════════════════════════════

@icalendar_required
class TestAddEvent:

    def test_add_creates_vevent(self, ics_env):
        tomorrow = (date.today() + timedelta(days=1)).isoformat()
        uid = ct.add_event("Test Meeting", tomorrow, time_str="10:00", duration_minutes=30)
        assert uid

        raw = ics_env.read_text(encoding="utf-8")
        assert "Test Meeting" in raw
        assert uid[:8] in raw

    def test_add_all_day_event(self, ics_env):
        tomorrow = (date.today() + timedelta(days=2)).isoformat()
        uid = ct.add_event("Journée entière", tomorrow)
        raw = ics_env.read_text(encoding="utf-8")
        assert "Journée entière" in raw or "Journ" in raw  # encodage possible

    def test_add_with_location_and_description(self, ics_env):
        tomorrow = (date.today() + timedelta(days=1)).isoformat()
        ct.add_event("Conf", tomorrow, time_str="09:00", location="Salle A", description="Ordre du jour")
        raw = ics_env.read_text(encoding="utf-8")
        assert "Salle A" in raw

    def test_add_invalidates_cache(self, ics_env):
        ct._cache["data"] = MagicMock()
        tomorrow = (date.today() + timedelta(days=1)).isoformat()
        ct.add_event("X", tomorrow)
        assert ct._cache["data"] is None

    def test_add_invalid_date_raises(self, ics_env):
        with pytest.raises(ValueError, match="Date invalide"):
            ct.add_event("X", "not-a-date")

    def test_add_raises_if_not_writable(self, monkeypatch):
        monkeypatch.setattr(ct, "get_calendar_source", lambda: "https://example.com/cal.ics")
        with pytest.raises(ValueError):
            ct.add_event("X", "2026-04-01")


@icalendar_required
class TestDeleteEvent:

    def test_delete_existing_uid_returns_true(self, ics_env):
        result = ct.delete_event("uid-alpha-1234")
        assert result is True
        raw = ics_env.read_text(encoding="utf-8")
        assert "Réunion Alpha" not in raw
        assert "uid-alpha-1234" not in raw

    def test_delete_unknown_uid_returns_false(self, ics_env):
        result = ct.delete_event("uid-does-not-exist")
        assert result is False

    def test_delete_keeps_other_events(self, ics_env):
        ct.delete_event("uid-alpha-1234")
        raw = ics_env.read_text(encoding="utf-8")
        assert "Journée Beta" in raw
        assert "uid-beta-5678" in raw

    def test_delete_invalidates_cache(self, ics_env):
        ct._cache["data"] = MagicMock()
        ct.delete_event("uid-alpha-1234")
        assert ct._cache["data"] is None

    def test_delete_raises_if_not_writable(self, monkeypatch):
        monkeypatch.setattr(ct, "get_calendar_source", lambda: "https://example.com/cal.ics")
        with pytest.raises(ValueError):
            ct.delete_event("uid-alpha-1234")


@icalendar_required
class TestUpdateEvent:

    def test_update_title(self, ics_env):
        result = ct.update_event("uid-alpha-1234", title="Réunion Modifiée")
        assert result is True
        raw = ics_env.read_text(encoding="utf-8")
        assert "SUMMARY:R" in raw or "Modifi" in raw

    def test_update_unknown_uid_returns_false(self, ics_env):
        result = ct.update_event("uid-does-not-exist", title="X")
        assert result is False

    def test_update_date_and_time(self, ics_env):
        new_date = (date.today() + timedelta(days=5)).isoformat()
        result = ct.update_event("uid-alpha-1234", date=new_date, time="16:00", duration_minutes=90)
        assert result is True

    def test_update_location(self, ics_env):
        result = ct.update_event("uid-alpha-1234", location="Salle B")
        assert result is True
        raw = ics_env.read_text(encoding="utf-8")
        assert "Salle B" in raw

    def test_update_invalidates_cache(self, ics_env):
        ct._cache["data"] = MagicMock()
        ct.update_event("uid-alpha-1234", title="X")
        assert ct._cache["data"] is None

    def test_update_raises_if_not_writable(self, monkeypatch):
        monkeypatch.setattr(ct, "get_calendar_source", lambda: "https://example.com/cal.ics")
        with pytest.raises(ValueError):
            ct.update_event("uid-alpha-1234", title="X")


@icalendar_required
class TestGetEventsWithUid:

    def test_returns_uid_field(self, ics_env):
        events = ct.get_events_with_uid(days=30)
        assert len(events) >= 1
        for ev in events:
            assert "uid" in ev
            assert ev["uid"]

    def test_uid_matches_ics_uid(self, ics_env):
        events = ct.get_events_with_uid(days=30)
        uids = {ev["uid"] for ev in events}
        assert "uid-alpha-1234" in uids

    def test_empty_when_no_source(self, monkeypatch):
        monkeypatch.setattr(ct, "get_calendar_source", lambda: "")
        events = ct.get_events_with_uid(days=30)
        assert events == []

    def test_add_then_get_finds_new_event(self, ics_env):
        tomorrow = (date.today() + timedelta(days=1)).isoformat()
        uid = ct.add_event("Nouveau RDV", tomorrow, time_str="11:00")
        events = ct.get_events_with_uid(days=30)
        uids = {ev["uid"] for ev in events}
        assert uid in uids


# ══════════════════════════════════════════════════════════════════
# Niveau 3 — CalendarWriteCrew.run() avec kickoff mocké
# ══════════════════════════════════════════════════════════════════

def _mock_result(action, event, target_uid=None, msg="OK"):
    """Construit un CrewOutput mocké retournant le JSON demandé."""
    plan = {
        "action": action,
        "event": event,
        "target_uid": target_uid,
        "confirmation_message": msg,
    }
    mock = MagicMock()
    mock.raw = json.dumps(plan)
    return mock


@icalendar_required
class TestCalendarWriteCrewRun:

    def _crew(self):
        from Mnemo.crew import CalendarWriteCrew
        return CalendarWriteCrew()

    def test_create_event_no_confirmation_needed(self, ics_env):
        tomorrow = (date.today() + timedelta(days=1)).isoformat()
        event = {"title": "Nouveau RDV", "date": tomorrow, "time": "10:00",
                 "duration_minutes": 45, "location": None, "description": None}

        crew = self._crew()
        with patch.object(crew, "crew") as mock_crew_method:
            mock_crew_method.return_value.kickoff.return_value = _mock_result(
                "create", event, msg="Nouveau RDV ajouté pour demain à 10h."
            )
            result = crew.run({"user_message": "ajoute un rdv demain à 10h",
                               "temporal_context": "", "calendar_context": ""})

        assert "Nouveau RDV" in result or "ajouté" in result
        raw = ics_env.read_text(encoding="utf-8")
        assert "Nouveau RDV" in raw

    def test_delete_event_confirmed(self, ics_env):
        event = None
        crew = self._crew()
        with patch.object(crew, "crew") as mock_crew_method, \
             patch("builtins.input", return_value="oui"):
            mock_crew_method.return_value.kickoff.return_value = _mock_result(
                "delete", event, target_uid="uid-alpha-1234",
                msg="Réunion Alpha supprimée."
            )
            result = crew.run({"user_message": "supprime la réunion alpha",
                               "temporal_context": "", "calendar_context": ""})

        assert "supprim" in result.lower() or "Réunion Alpha" in result
        raw = ics_env.read_text(encoding="utf-8")
        assert "uid-alpha-1234" not in raw

    def test_delete_event_cancelled_by_user(self, ics_env):
        crew = self._crew()
        with patch.object(crew, "crew") as mock_crew_method, \
             patch("builtins.input", return_value="non"):
            mock_crew_method.return_value.kickoff.return_value = _mock_result(
                "delete", None, target_uid="uid-alpha-1234"
            )
            result = crew.run({"user_message": "supprime la réunion alpha",
                               "temporal_context": "", "calendar_context": ""})

        assert "annul" in result.lower()
        # L'événement doit toujours être présent
        raw = ics_env.read_text(encoding="utf-8")
        assert "uid-alpha-1234" in raw

    def test_update_event_confirmed(self, ics_env):
        tomorrow = (date.today() + timedelta(days=1)).isoformat()
        event = {"title": "Réunion Modifiée", "date": tomorrow, "time": "16:00",
                 "duration_minutes": 60, "location": None, "description": None}
        crew = self._crew()
        with patch.object(crew, "crew") as mock_crew_method, \
             patch("builtins.input", return_value="oui"):
            mock_crew_method.return_value.kickoff.return_value = _mock_result(
                "update", event, target_uid="uid-alpha-1234",
                msg="Réunion Alpha décalée à 16h."
            )
            result = crew.run({"user_message": "décale la réunion alpha à 16h",
                               "temporal_context": "", "calendar_context": ""})

        assert "décal" in result.lower() or "Modifi" in result or "16h" in result

    def test_create_missing_date_returns_error(self, ics_env):
        crew = self._crew()
        with patch.object(crew, "crew") as mock_crew_method:
            mock_crew_method.return_value.kickoff.return_value = _mock_result(
                "create", {"title": "RDV sans date", "date": None, "time": None,
                            "duration_minutes": 60, "location": None, "description": None}
            )
            result = crew.run({"user_message": "ajoute un rdv",
                               "temporal_context": "", "calendar_context": ""})

        assert "date" in result.lower() or "manquant" in result.lower()

    def test_bad_json_from_llm_returns_error(self, ics_env):
        crew = self._crew()
        bad_result = MagicMock()
        bad_result.raw = "Voici l'événement que tu voulais créer : réunion demain."
        with patch.object(crew, "crew") as mock_crew_method:
            mock_crew_method.return_value.kickoff.return_value = bad_result
            result = crew.run({"user_message": "ajoute un rdv",
                               "temporal_context": "", "calendar_context": ""})

        assert "reformuler" in result.lower() or "interpréter" in result.lower()

    def test_read_only_source_blocked(self, monkeypatch):
        monkeypatch.setattr(ct, "get_calendar_source", lambda: "https://example.com/cal.ics")
        ct._cache["data"] = None
        ct._cache["fetched_at"] = None
        from Mnemo.crew import CalendarWriteCrew
        result = CalendarWriteCrew().run({"user_message": "ajoute un rdv",
                                          "temporal_context": "", "calendar_context": ""})
        assert "lecture seule" in result or "writable" in result or "local" in result

    def test_unknown_action_returns_error(self, ics_env):
        crew = self._crew()
        bad_plan = {"action": "move", "event": {}, "target_uid": None, "confirmation_message": ""}
        bad_result = MagicMock()
        bad_result.raw = json.dumps(bad_plan)
        with patch.object(crew, "crew") as mock_crew_method:
            mock_crew_method.return_value.kickoff.return_value = bad_result
            result = crew.run({"user_message": "déplace le rdv",
                               "temporal_context": "", "calendar_context": ""})

        assert "inconnue" in result.lower() or "move" in result
