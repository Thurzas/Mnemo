"""
Tests Phase 5.6 — MemoryGapReport, MemoryGap, save/load world_state.json
"""
import json
import pytest


class TestMemoryGap:
    def test_defaults(self):
        from Mnemo.tools.memory_tools import MemoryGap
        g = MemoryGap(section="S", subsection="Sub", description="desc")
        assert g.affects == []
        assert g.priority == 3
        assert g.label == ""
        assert g.question == ""

    def test_full_init(self):
        from Mnemo.tools.memory_tools import MemoryGap
        g = MemoryGap(
            section="Connaissances persistantes",
            subsection="Projets en cours",
            description="Section vide",
            affects=["briefing", "plan"],
            priority=1,
            label="Projet actif",
            question="Quels sont tes projets actifs ?",
        )
        assert g.affects == ["briefing", "plan"]
        assert g.priority == 1


class TestMemoryGapReport:
    def _make_report(self):
        from Mnemo.tools.memory_tools import MemoryGapReport, MemoryGap
        return MemoryGapReport(
            assessed_at="2026-03-17T14:00:00",
            memory_completeness=0.62,
            blocking_gaps=[
                MemoryGap(
                    section="Connaissances persistantes",
                    subsection="Projets en cours",
                    description="Vide",
                    affects=["briefing"],
                    priority=1,
                )
            ],
            enriching_gaps=[
                MemoryGap(
                    section="Identité Utilisateur",
                    subsection="Préférences & style",
                    description="Style non renseigné",
                    affects=["conversation"],
                    priority=3,
                )
            ],
            questions_ready=[{"id": "q1", "question": "Quel est ton style préféré ?"}],
        )

    def test_defaults(self):
        from Mnemo.tools.memory_tools import MemoryGapReport
        r = MemoryGapReport()
        assert r.blocking_gaps == []
        assert r.enriching_gaps == []
        assert r.questions_ready == []
        assert r.memory_completeness == 0.0

    def test_to_world_state_with_blocking(self):
        r = self._make_report()
        ws = r.to_world_state()
        assert ws["memory_gaps_known"] is True
        assert ws["memory_blocking_gaps"] is True
        assert ws["memory_completeness"] == 0.62

    def test_to_world_state_no_blocking(self):
        from Mnemo.tools.memory_tools import MemoryGapReport, MemoryGap
        r = MemoryGapReport(
            assessed_at="2026-03-17T14:00:00",
            memory_completeness=0.9,
            blocking_gaps=[],
            enriching_gaps=[MemoryGap(section="S", subsection="Sub", description="d")],
        )
        ws = r.to_world_state()
        assert ws["memory_blocking_gaps"] is False
        assert ws["memory_completeness"] == 0.9

    def test_to_json_roundtrip(self):
        r = self._make_report()
        raw = r.to_json()
        data = json.loads(raw)
        assert data["memory_completeness"] == 0.62
        assert len(data["blocking_gaps"]) == 1
        assert data["blocking_gaps"][0]["section"] == "Connaissances persistantes"
        assert len(data["enriching_gaps"]) == 1
        assert len(data["questions_ready"]) == 1

    def test_from_json_string(self):
        from Mnemo.tools.memory_tools import MemoryGapReport
        r = self._make_report()
        raw = r.to_json()
        r2 = MemoryGapReport.from_json(raw)
        assert r2.assessed_at == "2026-03-17T14:00:00"
        assert r2.memory_completeness == 0.62
        assert len(r2.blocking_gaps) == 1
        assert r2.blocking_gaps[0].affects == ["briefing"]
        assert len(r2.enriching_gaps) == 1
        assert r2.questions_ready[0]["id"] == "q1"

    def test_from_json_dict(self):
        from Mnemo.tools.memory_tools import MemoryGapReport
        r = self._make_report()
        d = json.loads(r.to_json())
        r2 = MemoryGapReport.from_json(d)
        assert r2.memory_completeness == 0.62

    def test_from_json_empty(self):
        from Mnemo.tools.memory_tools import MemoryGapReport
        r = MemoryGapReport.from_json("{}")
        assert r.blocking_gaps == []
        assert r.memory_completeness == 0.0


class TestSaveLoadWorldState:
    def test_save_creates_file(self, tmp_path, monkeypatch):
        import Mnemo.tools.memory_tools as mt
        monkeypatch.setattr(mt, "get_data_dir", lambda: tmp_path)
        from Mnemo.tools.memory_tools import save_memory_gap_report, MemoryGapReport, MemoryGap
        r = MemoryGapReport(
            assessed_at="2026-03-17T14:00:00",
            memory_completeness=0.5,
            blocking_gaps=[MemoryGap(section="S", subsection="Sub", description="d")],
        )
        save_memory_gap_report(r)
        ws_path = tmp_path / "world_state.json"
        assert ws_path.exists()
        data = json.loads(ws_path.read_text())
        assert data["memory_gaps_known"] is True
        assert data["memory_blocking_gaps"] is True
        assert data["memory_completeness"] == 0.5

    def test_save_merges_existing(self, tmp_path, monkeypatch):
        import Mnemo.tools.memory_tools as mt
        monkeypatch.setattr(mt, "get_data_dir", lambda: tmp_path)
        from Mnemo.tools.memory_tools import save_memory_gap_report, MemoryGapReport
        ws_path = tmp_path / "world_state.json"
        ws_path.write_text(json.dumps({"calendar_fetched": True}))
        r = MemoryGapReport(assessed_at="2026-03-17T14:00:00", memory_completeness=0.8)
        save_memory_gap_report(r)
        data = json.loads(ws_path.read_text())
        assert data["calendar_fetched"] is True   # préservé
        assert data["memory_gaps_known"] is True  # ajouté

    def test_save_overwrites_flags(self, tmp_path, monkeypatch):
        import Mnemo.tools.memory_tools as mt
        monkeypatch.setattr(mt, "get_data_dir", lambda: tmp_path)
        from Mnemo.tools.memory_tools import save_memory_gap_report, MemoryGapReport
        ws_path = tmp_path / "world_state.json"
        ws_path.write_text(json.dumps({"memory_blocking_gaps": True}))
        r = MemoryGapReport(assessed_at="2026-03-17T14:00:00", memory_completeness=0.9)
        save_memory_gap_report(r)
        data = json.loads(ws_path.read_text())
        assert data["memory_blocking_gaps"] is False  # mis à jour

    def test_save_includes_last_gap_report(self, tmp_path, monkeypatch):
        import Mnemo.tools.memory_tools as mt
        monkeypatch.setattr(mt, "get_data_dir", lambda: tmp_path)
        from Mnemo.tools.memory_tools import save_memory_gap_report, MemoryGapReport
        r = MemoryGapReport(assessed_at="2026-03-17T14:00:00", memory_completeness=0.7)
        save_memory_gap_report(r)
        data = json.loads((tmp_path / "world_state.json").read_text())
        assert "last_gap_report" in data
        assert data["last_gap_report"]["assessed_at"] == "2026-03-17T14:00:00"

    def test_load_absent(self, tmp_path, monkeypatch):
        import Mnemo.tools.memory_tools as mt
        monkeypatch.setattr(mt, "get_data_dir", lambda: tmp_path)
        from Mnemo.tools.memory_tools import load_world_state
        assert load_world_state() == {}

    def test_load_corrupt(self, tmp_path, monkeypatch):
        import Mnemo.tools.memory_tools as mt
        monkeypatch.setattr(mt, "get_data_dir", lambda: tmp_path)
        from Mnemo.tools.memory_tools import load_world_state
        (tmp_path / "world_state.json").write_text("NOT JSON")
        assert load_world_state() == {}

    def test_load_roundtrip(self, tmp_path, monkeypatch):
        import Mnemo.tools.memory_tools as mt
        monkeypatch.setattr(mt, "get_data_dir", lambda: tmp_path)
        from Mnemo.tools.memory_tools import (
            save_memory_gap_report, load_world_state, MemoryGapReport
        )
        r = MemoryGapReport(assessed_at="2026-03-17T14:00:00", memory_completeness=0.65)
        save_memory_gap_report(r)
        ws = load_world_state()
        assert ws["memory_completeness"] == 0.65
        assert ws["memory_gaps_known"] is True
