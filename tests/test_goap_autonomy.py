"""
Tests Phase 7.4 — Boucle d'autonomie GOAP

Couvre :
  - _is_risky              : classification risky / non-risky
  - _build_project_world_state : world_state minimal d'un projet
  - _push_pending_confirmation : écriture + déduplication
  - _advance_project       : étape non cochée → action risquée vs non-risquée
  - _advance_project       : toutes étapes cochées → projet marqué "done"
  - _advance_project       : préconditions KO → pas d'action
  - _advance_project       : aucune action KG → skip silencieux
  - _goap_autonomy_tick    : scan multi-user / multi-projet

Aucun LLM requis.
"""
import json
import pytest
from pathlib import Path
from unittest.mock import patch

from Mnemo.init_db import init_db
from Mnemo.tools.kg_tools import kg_add_triplet
from Mnemo.scheduler import (
    _is_risky,
    _build_project_world_state,
    _push_pending_confirmation,
    _advance_project,
    _goap_autonomy_tick,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _no_real_seed():
    with patch("Mnemo.tools.kg_tools.SEED_DB_PATH", Path("/nonexistent/kg_seed.db")):
        yield


@pytest.fixture
def data_dir(tmp_path) -> Path:
    """Structure /data/users/<username>/ avec DB et projects/."""
    return tmp_path


@pytest.fixture
def user_dir(data_dir) -> tuple[Path, Path]:
    """Retourne (user_dir, db_path) pour l'utilisateur 'testuser'."""
    udir   = data_dir / "users" / "testuser"
    udir.mkdir(parents=True)
    db     = udir / "memory.db"
    init_db(db_path=db)
    return udir, db


@pytest.fixture
def project_dir(user_dir) -> tuple[Path, Path, dict]:
    """
    Crée un projet sandbox minimal avec plan.md (2 étapes dont 1 cochée).
    Retourne (project_dir, db_path, manifest).
    """
    udir, db = user_dir
    pdir = udir / "projects" / "mon-projet"
    pdir.mkdir(parents=True)
    manifest = {
        "slug": "mon-projet",
        "name": "Mon Projet",
        "goal": "Tester l'autonomie",
        "status": "in_progress",
    }
    (pdir / "project.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (pdir / "plan.md").write_text(
        "# Plan\n\n"
        "- [x] Étape terminée\n"
        "- [ ] recherche initiale\n"
        "- [ ] rédiger la documentation\n",
        encoding="utf-8",
    )
    return pdir, db, manifest


# ══════════════════════════════════════════════════════════════════════════════
# 1. _is_risky
# ══════════════════════════════════════════════════════════════════════════════

class TestIsRisky:

    def test_shell_risque(self):
        assert _is_risky("sandbox_shell: npm install")

    def test_npm_risque(self):
        assert _is_risky("sandbox_shell: npm run build")

    def test_python_risque(self):
        assert _is_risky("sandbox_shell: python script.py")

    def test_write_non_risque(self):
        assert not _is_risky("sandbox_write")

    def test_read_non_risque(self):
        assert not _is_risky("sandbox_read")

    def test_web_search_non_risque(self):
        assert not _is_risky("web_search")

    def test_web_fetch_non_risque(self):
        assert not _is_risky("web_fetch")


# ══════════════════════════════════════════════════════════════════════════════
# 2. _build_project_world_state
# ══════════════════════════════════════════════════════════════════════════════

class TestBuildProjectWorldState:

    def test_sandbox_open(self, tmp_path):
        ws = _build_project_world_state(tmp_path)
        assert ws["sandbox_open"] is True

    def test_sandbox_not_readonly(self, tmp_path):
        ws = _build_project_world_state(tmp_path)
        assert ws["sandbox_readonly"] is False

    def test_web_available(self, tmp_path):
        ws = _build_project_world_state(tmp_path)
        assert ws["web_available"] is True

    def test_retourne_dict(self, tmp_path):
        ws = _build_project_world_state(tmp_path)
        assert isinstance(ws, dict)
        assert len(ws) >= 4


# ══════════════════════════════════════════════════════════════════════════════
# 3. _push_pending_confirmation
# ══════════════════════════════════════════════════════════════════════════════

class TestPushPendingConfirmation:

    def test_ecrit_dans_world_state(self, data_dir, user_dir):
        udir, _ = user_dir
        with patch("Mnemo.scheduler.DATA_PATH", data_dir):
            _push_pending_confirmation(
                "testuser", "mon-projet", "recherche initiale", "web_fetch"
            )
        ws_path = udir / "world_state.json"
        assert ws_path.exists()
        ws = json.loads(ws_path.read_text())
        confs = ws["pending_confirmations"]
        assert len(confs) == 1
        assert confs[0]["action"] == "web_fetch"
        assert confs[0]["project_slug"] == "mon-projet"

    def test_deduplication(self, data_dir, user_dir):
        """Même action + même projet → pas de doublon."""
        with patch("Mnemo.scheduler.DATA_PATH", data_dir):
            _push_pending_confirmation("testuser", "p", "step", "sandbox_shell: npm install")
            _push_pending_confirmation("testuser", "p", "step", "sandbox_shell: npm install")
        udir, _ = user_dir
        ws = json.loads((udir / "world_state.json").read_text())
        assert len(ws["pending_confirmations"]) == 1

    def test_actions_differentes_ajoutees(self, data_dir, user_dir):
        with patch("Mnemo.scheduler.DATA_PATH", data_dir):
            _push_pending_confirmation("testuser", "p", "s", "sandbox_shell: npm install")
            _push_pending_confirmation("testuser", "p", "s", "sandbox_shell: pytest")
        udir, _ = user_dir
        ws = json.loads((udir / "world_state.json").read_text())
        assert len(ws["pending_confirmations"]) == 2

    def test_world_state_existant_preserve(self, data_dir, user_dir):
        udir, _ = user_dir
        ws_path = udir / "world_state.json"
        ws_path.write_text(json.dumps({"existing_key": "value"}))
        with patch("Mnemo.scheduler.DATA_PATH", data_dir):
            _push_pending_confirmation("testuser", "p", "s", "web_fetch")
        ws = json.loads(ws_path.read_text())
        assert ws["existing_key"] == "value"
        assert "pending_confirmations" in ws


# ══════════════════════════════════════════════════════════════════════════════
# 4. _advance_project
# ══════════════════════════════════════════════════════════════════════════════

class TestAdvanceProject:

    def test_action_risquee_pushee(self, data_dir, project_dir):
        pdir, db, manifest = project_dir
        # Ajoute une action risquée pour "recherche initiale"
        kg_add_triplet(db, "step", "recherche initiale", "requires",
                       "action", "sandbox_shell: npm install")
        kg_add_triplet(db, "action", "sandbox_shell: npm install",
                       "precondition", "state", "sandbox_open")

        with patch("Mnemo.scheduler.DATA_PATH", data_dir):
            _advance_project("testuser", db, manifest, pdir)

        udir = data_dir / "users" / "testuser"
        ws_path = udir / "world_state.json"
        assert ws_path.exists()
        ws = json.loads(ws_path.read_text())
        confs = ws.get("pending_confirmations", [])
        assert any(c["action"] == "sandbox_shell: npm install" for c in confs)

    def test_action_non_risquee_loguee(self, data_dir, project_dir, caplog):
        import logging
        pdir, db, manifest = project_dir
        kg_add_triplet(db, "step", "recherche initiale", "requires",
                       "action", "web_search")
        kg_add_triplet(db, "action", "web_search",
                       "precondition", "state", "web_available")

        with patch("Mnemo.scheduler.DATA_PATH", data_dir):
            with caplog.at_level(logging.INFO, logger="mnemo.scheduler"):
                _advance_project("testuser", db, manifest, pdir)

        assert any("web_search" in r.message for r in caplog.records)
        # Pas de pending_confirmation pour une action non risquée
        udir = data_dir / "users" / "testuser"
        ws_path = udir / "world_state.json"
        if ws_path.exists():
            ws = json.loads(ws_path.read_text())
            assert not ws.get("pending_confirmations")

    def test_toutes_etapes_cochees_projet_done(self, data_dir, user_dir):
        udir, db = user_dir
        pdir = udir / "projects" / "done-project"
        pdir.mkdir(parents=True)
        manifest = {"slug": "done-project", "name": "Done", "goal": "X", "status": "in_progress"}
        (pdir / "project.json").write_text(json.dumps(manifest))
        (pdir / "plan.md").write_text("- [x] Étape 1\n- [x] Étape 2\n")

        with patch("Mnemo.scheduler.DATA_PATH", data_dir):
            _advance_project("testuser", db, manifest, pdir)

        updated = json.loads((pdir / "project.json").read_text())
        assert updated["status"] == "done"

    def test_preconditions_ko_pas_action(self, data_dir, project_dir):
        pdir, db, manifest = project_dir
        # Action avec précondition non satisfaite (node_not_available)
        kg_add_triplet(db, "step", "recherche initiale", "requires",
                       "action", "sandbox_shell: npm install")
        kg_add_triplet(db, "action", "sandbox_shell: npm install",
                       "precondition", "state", "impossible_precond")

        with patch("Mnemo.scheduler.DATA_PATH", data_dir):
            _advance_project("testuser", db, manifest, pdir)

        udir = data_dir / "users" / "testuser"
        ws_path = udir / "world_state.json"
        if ws_path.exists():
            ws = json.loads(ws_path.read_text())
            assert not ws.get("pending_confirmations")

    def test_aucune_action_kg_skip_silencieux(self, data_dir, project_dir):
        pdir, db, manifest = project_dir
        # KG vide → pas d'action connue pour "recherche initiale"
        with patch("Mnemo.scheduler.DATA_PATH", data_dir):
            _advance_project("testuser", db, manifest, pdir)  # ne doit pas lever

    def test_plan_sans_etapes_skip(self, data_dir, user_dir):
        udir, db = user_dir
        pdir = udir / "projects" / "empty-plan"
        pdir.mkdir(parents=True)
        manifest = {"slug": "empty-plan", "name": "X", "goal": "Y", "status": "in_progress"}
        (pdir / "project.json").write_text(json.dumps(manifest))
        (pdir / "plan.md").write_text("# Plan\n\nPas d'étapes ici.\n")

        with patch("Mnemo.scheduler.DATA_PATH", data_dir):
            _advance_project("testuser", db, manifest, pdir)  # ne doit pas lever


# ══════════════════════════════════════════════════════════════════════════════
# 5. _goap_autonomy_tick
# ══════════════════════════════════════════════════════════════════════════════

class TestGoApAutonomyTick:

    def test_tick_sans_users_ne_plante_pas(self, tmp_path):
        with patch("Mnemo.scheduler.DATA_PATH", tmp_path):
            _goap_autonomy_tick()  # /data/users/ absent → pas d'erreur

    def test_tick_user_sans_projets(self, data_dir, user_dir):
        with patch("Mnemo.scheduler.DATA_PATH", data_dir):
            _goap_autonomy_tick()  # pas de dossier projects/ → pas d'erreur

    def test_tick_projet_done_ignore(self, data_dir, user_dir):
        udir, db = user_dir
        pdir = udir / "projects" / "finished"
        pdir.mkdir(parents=True)
        manifest = {"slug": "finished", "name": "X", "goal": "Y", "status": "done"}
        (pdir / "project.json").write_text(json.dumps(manifest))
        (pdir / "plan.md").write_text("- [ ] step\n")

        with patch("Mnemo.scheduler.DATA_PATH", data_dir):
            _goap_autonomy_tick()

        # Pas de pending_confirmations créé pour un projet "done"
        ws_path = udir / "world_state.json"
        if ws_path.exists():
            ws = json.loads(ws_path.read_text())
            assert not ws.get("pending_confirmations")

    def test_tick_multi_projets(self, data_dir, user_dir):
        udir, db = user_dir
        for i in range(3):
            pdir = udir / "projects" / f"project-{i}"
            pdir.mkdir(parents=True)
            manifest = {
                "slug": f"project-{i}", "name": f"P{i}",
                "goal": "X", "status": "in_progress"
            }
            (pdir / "project.json").write_text(json.dumps(manifest))
            (pdir / "plan.md").write_text("- [ ] recherche initiale\n")

        with patch("Mnemo.scheduler.DATA_PATH", data_dir):
            _goap_autonomy_tick()  # doit traiter les 3 sans planter

    def test_tick_erreur_projet_corrompu_continue(self, data_dir, user_dir):
        """Un project.json corrompu ne doit pas arrêter le tick."""
        udir, db = user_dir
        pdir = udir / "projects" / "bad-project"
        pdir.mkdir(parents=True)
        (pdir / "project.json").write_text("{ invalid json }")

        with patch("Mnemo.scheduler.DATA_PATH", data_dir):
            _goap_autonomy_tick()  # ne doit pas lever