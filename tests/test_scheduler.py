"""
test_scheduler.py — Tests du système de scheduling Mnemo

Niveau 1 : Unitaires purs — compute_next_run, _fmt_task_line, _strip_fences
Niveau 2 : Intégration DB — CRUD scheduler_tasks, tasks.md (SQLite temporaire)
Niveau 3 : Intégration crew — SchedulerCrew.run() avec kickoff() mocké

Zéro appel LLM, zéro réseau.
Lance avec : uv run pytest tests/test_scheduler.py -v
"""

import json
import sqlite3
import pytest

from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch


# ══════════════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════════════

def make_crew_result(text: str) -> MagicMock:
    """Simule le résultat d'un crew.kickoff()."""
    result = MagicMock()
    result.raw = text
    return result


# ══════════════════════════════════════════════════════════════════
# Fixtures
# ══════════════════════════════════════════════════════════════════

@pytest.fixture
def db_env(tmp_path, monkeypatch):
    """
    Crée une DB SQLite temporaire avec la table scheduled_tasks,
    et redirige DB_PATH + TASKS_MD de scheduler_tasks vers tmp_path.
    """
    db_path  = tmp_path / "memory.db"
    tasks_md = tmp_path / "tasks.md"

    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE scheduled_tasks (
            id          TEXT PRIMARY KEY,
            type        TEXT NOT NULL,
            action      TEXT NOT NULL,
            payload     TEXT DEFAULT '{}',
            trigger_at  TEXT,
            cron_expr   TEXT,
            status      TEXT DEFAULT 'pending',
            created_at  TEXT,
            last_run    TEXT,
            next_run    TEXT,
            error_msg   TEXT
        )
    """)
    conn.commit()
    conn.close()

    import Mnemo.tools.scheduler_tasks as st
    monkeypatch.setattr(st, "DB_PATH",  db_path)
    monkeypatch.setattr(st, "TASKS_MD", tasks_md)

    return {"db": db_path, "md": tasks_md, "tmp": tmp_path}


# ══════════════════════════════════════════════════════════════════
# Niveau 1 — compute_next_run
# ══════════════════════════════════════════════════════════════════

class TestComputeNextRun:

    # ── one_shot ──────────────────────────────────────────────────

    def test_one_shot_retourne_trigger_at(self):
        from Mnemo.tools.scheduler_tasks import compute_next_run
        result = compute_next_run("one_shot", None, "2026-03-10T09:00:00")
        assert result == datetime(2026, 3, 10, 9, 0, 0)

    def test_one_shot_sans_trigger_at_retourne_none(self):
        from Mnemo.tools.scheduler_tasks import compute_next_run
        assert compute_next_run("one_shot", None, None) is None

    def test_one_shot_trigger_at_invalide_retourne_none(self):
        from Mnemo.tools.scheduler_tasks import compute_next_run
        assert compute_next_run("one_shot", None, "pas-une-date") is None

    # ── daily ────────────────────────────────────────────────────

    def test_daily_heure_future(self):
        from Mnemo.tools.scheduler_tasks import compute_next_run
        now    = datetime(2026, 3, 6, 6, 0, 0)   # 06:00, heure cible = 07:30
        result = compute_next_run("system", "daily 07:30", None, from_dt=now)
        assert result == datetime(2026, 3, 6, 7, 30, 0)  # aujourd'hui

    def test_daily_heure_passee(self):
        from Mnemo.tools.scheduler_tasks import compute_next_run
        now    = datetime(2026, 3, 6, 8, 0, 0)   # 08:00, heure cible = 07:30 passée
        result = compute_next_run("system", "daily 07:30", None, from_dt=now)
        assert result == datetime(2026, 3, 7, 7, 30, 0)  # demain

    # ── weekly ───────────────────────────────────────────────────
    # 2026-03-06 est un vendredi (weekday=4)

    def test_weekly_jour_futur_cette_semaine(self):
        from Mnemo.tools.scheduler_tasks import compute_next_run
        now    = datetime(2026, 3, 6, 10, 0, 0)  # vendredi
        result = compute_next_run("recurring", "weekly dimanche 09:00", None, from_dt=now)
        assert result == datetime(2026, 3, 8, 9, 0, 0)  # dimanche +2j

    def test_weekly_jour_courant_heure_future(self):
        from Mnemo.tools.scheduler_tasks import compute_next_run
        now    = datetime(2026, 3, 6, 8, 0, 0)   # vendredi 08:00, cible 09:00
        result = compute_next_run("recurring", "weekly vendredi 09:00", None, from_dt=now)
        assert result == datetime(2026, 3, 6, 9, 0, 0)  # aujourd'hui

    def test_weekly_jour_courant_heure_passee(self):
        from Mnemo.tools.scheduler_tasks import compute_next_run
        now    = datetime(2026, 3, 6, 10, 0, 0)  # vendredi 10:00, cible 09:00 passée
        result = compute_next_run("recurring", "weekly vendredi 09:00", None, from_dt=now)
        assert result == datetime(2026, 3, 13, 9, 0, 0)  # vendredi suivant

    def test_weekly_jour_deja_passe_dans_la_semaine(self):
        from Mnemo.tools.scheduler_tasks import compute_next_run
        now    = datetime(2026, 3, 6, 10, 0, 0)  # vendredi, lundi déjà passé
        result = compute_next_run("recurring", "weekly lundi 08:00", None, from_dt=now)
        assert result == datetime(2026, 3, 9, 8, 0, 0)  # lundi prochain

    # ── cas invalides ─────────────────────────────────────────────

    def test_cron_format_inconnu_retourne_none(self):
        from Mnemo.tools.scheduler_tasks import compute_next_run
        assert compute_next_run("system", "monthly 08:00", None) is None

    def test_sans_cron_expr_retourne_none(self):
        from Mnemo.tools.scheduler_tasks import compute_next_run
        assert compute_next_run("recurring", None, None) is None


# ══════════════════════════════════════════════════════════════════
# Niveau 1 — _fmt_task_line
# ══════════════════════════════════════════════════════════════════

class TestFmtTaskLine:

    def _task(self, **kwargs):
        base = {
            "type": "one_shot", "status": "pending", "action": "reminder",
            "next_run": "2026-03-07T09:00:00", "trigger_at": None,
            "payload": '{"message": "Mon rappel"}',
        }
        base.update(kwargs)
        return base

    def test_one_shot_pending_affiche_case_vide(self):
        from Mnemo.tools.scheduler_tasks import _fmt_task_line
        line = _fmt_task_line(self._task())
        assert "[ ]" in line

    def test_one_shot_done_affiche_croix(self):
        from Mnemo.tools.scheduler_tasks import _fmt_task_line
        line = _fmt_task_line(self._task(status="done"))
        assert "[x]" in line

    def test_one_shot_error_affiche_point_exclamation(self):
        from Mnemo.tools.scheduler_tasks import _fmt_task_line
        line = _fmt_task_line(self._task(status="error"))
        assert "[!]" in line

    def test_system_affiche_icone_recurrence(self):
        from Mnemo.tools.scheduler_tasks import _fmt_task_line
        line = _fmt_task_line(self._task(
            type="system",
            payload='{"message": "Morning briefing"}',
        ))
        assert "[↻]" in line

    def test_recurring_affiche_icone_recurrence(self):
        from Mnemo.tools.scheduler_tasks import _fmt_task_line
        line = _fmt_task_line(self._task(type="recurring"))
        assert "[↻]" in line

    def test_contient_message_payload(self):
        from Mnemo.tools.scheduler_tasks import _fmt_task_line
        line = _fmt_task_line(self._task(payload='{"message": "Acheter du café"}'))
        assert "Acheter du café" in line

    def test_contient_date_formatee(self):
        from Mnemo.tools.scheduler_tasks import _fmt_task_line
        line = _fmt_task_line(self._task(next_run="2026-03-07T09:00:00"))
        assert "2026-03-07 09:00" in line


# ══════════════════════════════════════════════════════════════════
# Niveau 1 — _strip_fences (scheduler.py)
# ══════════════════════════════════════════════════════════════════

class TestStripFences:

    def test_enleve_fence_markdown(self):
        from Mnemo.scheduler import _strip_fences
        text   = "```markdown\n# Briefing\n\nContenu\n```"
        result = _strip_fences(text)
        assert "```" not in result
        assert "# Briefing" in result

    def test_texte_sans_fence_inchange(self):
        from Mnemo.scheduler import _strip_fences
        text = "# Briefing\n\nContenu normal"
        assert _strip_fences(text) == text

    def test_enleve_separateurs_triple_tiret(self):
        from Mnemo.scheduler import _strip_fences
        text   = "Section A\n\n---\n\nSection B"
        result = _strip_fences(text)
        assert "---" not in result

    def test_fence_sans_langage(self):
        from Mnemo.scheduler import _strip_fences
        text = "```\ncontenu\n```"
        assert "```" not in _strip_fences(text)


# ══════════════════════════════════════════════════════════════════
# Niveau 2 — CRUD scheduler_tasks
# ══════════════════════════════════════════════════════════════════

class TestCreateTask:

    def test_cree_tache_one_shot(self, db_env):
        from Mnemo.tools.scheduler_tasks import create_task, list_tasks
        result = create_task(
            task_id="t001", task_type="one_shot", action="reminder",
            payload={"message": "Test one-shot"}, trigger_at="2026-03-10T09:00:00",
        )
        assert result["id"] == "t001"
        assert result["next_run"] is not None
        assert any(t["id"] == "t001" for t in list_tasks())

    def test_cree_tache_recurring(self, db_env):
        from Mnemo.tools.scheduler_tasks import create_task, list_tasks
        create_task(
            task_id="t002", task_type="recurring", action="weekly",
            payload={}, cron_expr="weekly lundi 08:00",
        )
        t = next(t for t in list_tasks() if t["id"] == "t002")
        assert t["cron_expr"] == "weekly lundi 08:00"
        assert t["next_run"] is not None

    def test_insert_or_replace_idempotent(self, db_env):
        from Mnemo.tools.scheduler_tasks import create_task, list_tasks
        create_task("t003", "one_shot", "reminder", {"message": "v1"},
                    trigger_at="2026-03-10T09:00:00")
        create_task("t003", "one_shot", "reminder", {"message": "v2"},
                    trigger_at="2026-03-10T10:00:00")
        tasks = [t for t in list_tasks() if t["id"] == "t003"]
        assert len(tasks) == 1
        assert json.loads(tasks[0]["payload"])["message"] == "v2"

    def test_payload_serialise_en_json(self, db_env):
        from Mnemo.tools.scheduler_tasks import create_task, list_tasks
        create_task("t004", "one_shot", "reminder",
                    {"message": "Vérifier les emails"},
                    trigger_at="2026-03-10T09:00:00")
        t = next(t for t in list_tasks() if t["id"] == "t004")
        payload = json.loads(t["payload"])
        assert payload["message"] == "Vérifier les emails"


class TestGetDueTasks:

    def test_retourne_taches_echeues(self, db_env):
        from Mnemo.tools.scheduler_tasks import create_task, get_due_tasks
        past = (datetime.now() - timedelta(hours=1)).isoformat()
        create_task("due_001", "one_shot", "reminder", {}, trigger_at=past)
        assert any(t["id"] == "due_001" for t in get_due_tasks())

    def test_ne_retourne_pas_taches_futures(self, db_env):
        from Mnemo.tools.scheduler_tasks import create_task, get_due_tasks
        future = (datetime.now() + timedelta(hours=2)).isoformat()
        create_task("fut_001", "one_shot", "reminder", {}, trigger_at=future)
        assert not any(t["id"] == "fut_001" for t in get_due_tasks())

    def test_ne_retourne_pas_taches_done(self, db_env):
        from Mnemo.tools.scheduler_tasks import create_task, get_due_tasks, mark_done
        past = (datetime.now() - timedelta(hours=1)).isoformat()
        create_task("done_001", "one_shot", "reminder", {}, trigger_at=past)
        mark_done("done_001")
        assert not any(t["id"] == "done_001" for t in get_due_tasks())

    def test_ordre_chronologique(self, db_env):
        from Mnemo.tools.scheduler_tasks import create_task, get_due_tasks
        t1 = (datetime.now() - timedelta(hours=3)).isoformat()
        t2 = (datetime.now() - timedelta(hours=1)).isoformat()
        create_task("ord_b", "one_shot", "reminder", {}, trigger_at=t2)
        create_task("ord_a", "one_shot", "reminder", {}, trigger_at=t1)
        ids = [t["id"] for t in get_due_tasks()]
        assert ids.index("ord_a") < ids.index("ord_b")


class TestStatusTransitions:

    def test_mark_done(self, db_env):
        from Mnemo.tools.scheduler_tasks import create_task, mark_done, list_tasks
        past = (datetime.now() - timedelta(hours=1)).isoformat()
        create_task("td", "one_shot", "reminder", {}, trigger_at=past)
        mark_done("td")
        t = next(t for t in list_tasks() if t["id"] == "td")
        assert t["status"] == "done"
        assert t["last_run"] is not None

    def test_mark_error(self, db_env):
        from Mnemo.tools.scheduler_tasks import create_task, mark_error, list_tasks
        past = (datetime.now() - timedelta(hours=1)).isoformat()
        create_task("te", "one_shot", "reminder", {}, trigger_at=past)
        mark_error("te", "Ollama indisponible")
        t = next(t for t in list_tasks() if t["id"] == "te")
        assert t["status"] == "error"
        assert "Ollama" in t["error_msg"]

    def test_mark_error_tronque_message_long(self, db_env):
        from Mnemo.tools.scheduler_tasks import create_task, mark_error, list_tasks
        past = (datetime.now() - timedelta(hours=1)).isoformat()
        create_task("te2", "one_shot", "reminder", {}, trigger_at=past)
        mark_error("te2", "x" * 600)
        t = next(t for t in list_tasks() if t["id"] == "te2")
        assert len(t["error_msg"]) <= 500

    def test_reschedule_repasse_en_pending(self, db_env):
        from Mnemo.tools.scheduler_tasks import create_task, reschedule, mark_error, list_tasks
        past = (datetime.now() - timedelta(hours=1)).isoformat()
        create_task("tr", "recurring", "briefing", {}, cron_expr="daily 07:30")
        mark_error("tr", "erreur test")
        reschedule("tr", "daily 07:30")
        t = next(t for t in list_tasks() if t["id"] == "tr")
        assert t["status"] == "pending"
        assert t["next_run"] is not None

    def test_cancel_task_retourne_true(self, db_env):
        from Mnemo.tools.scheduler_tasks import create_task, cancel_task, list_tasks
        future = (datetime.now() + timedelta(hours=2)).isoformat()
        create_task("tc", "one_shot", "reminder", {}, trigger_at=future)
        assert cancel_task("tc") is True
        t = next(t for t in list_tasks() if t["id"] == "tc")
        assert t["status"] == "cancelled"

    def test_cancel_task_inexistant_retourne_false(self, db_env):
        from Mnemo.tools.scheduler_tasks import cancel_task
        assert cancel_task("inexistant_xyz") is False

    def test_cancel_tache_deja_done_retourne_false(self, db_env):
        from Mnemo.tools.scheduler_tasks import create_task, mark_done, cancel_task
        past = (datetime.now() - timedelta(hours=1)).isoformat()
        create_task("tc2", "one_shot", "reminder", {}, trigger_at=past)
        mark_done("tc2")
        assert cancel_task("tc2") is False


class TestBootstrapSystemTasks:

    def test_cree_trois_taches_systeme(self, db_env):
        from Mnemo.tools.scheduler_tasks import bootstrap_system_tasks, list_tasks
        bootstrap_system_tasks()
        ids = {t["id"] for t in list_tasks()}
        assert {"sys_briefing", "sys_weekly", "sys_deadline_scan"}.issubset(ids)

    def test_toutes_les_taches_sont_system(self, db_env):
        from Mnemo.tools.scheduler_tasks import bootstrap_system_tasks, list_tasks
        bootstrap_system_tasks()
        for t in list_tasks():
            assert t["type"] == "system"

    def test_idempotent_double_appel(self, db_env):
        from Mnemo.tools.scheduler_tasks import bootstrap_system_tasks, list_tasks
        bootstrap_system_tasks()
        bootstrap_system_tasks()
        assert len([t for t in list_tasks() if t["type"] == "system"]) == 3

    def test_briefing_time_custom(self, db_env):
        from Mnemo.tools.scheduler_tasks import bootstrap_system_tasks, list_tasks
        bootstrap_system_tasks(briefing_time="06:00")
        t = next(t for t in list_tasks() if t["id"] == "sys_briefing")
        assert "06:00" in t["cron_expr"]

    def test_weekly_time_custom(self, db_env):
        from Mnemo.tools.scheduler_tasks import bootstrap_system_tasks, list_tasks
        bootstrap_system_tasks(weekly_time="09:30")
        t = next(t for t in list_tasks() if t["id"] == "sys_weekly")
        assert "09:30" in t["cron_expr"]


class TestSyncTasksMd:

    def test_cree_le_fichier(self, db_env):
        from Mnemo.tools.scheduler_tasks import bootstrap_system_tasks
        bootstrap_system_tasks()
        assert db_env["md"].exists()

    def test_contient_trois_sections(self, db_env):
        from Mnemo.tools.scheduler_tasks import bootstrap_system_tasks
        bootstrap_system_tasks()
        content = db_env["md"].read_text(encoding="utf-8")
        assert "## Système" in content
        assert "## Récurrentes" in content
        assert "## One-shot" in content

    def test_tache_one_shot_visible_dans_section(self, db_env):
        from Mnemo.tools.scheduler_tasks import create_task
        future = (datetime.now() + timedelta(hours=2)).isoformat()
        create_task("md_t", "one_shot", "reminder",
                    {"message": "Acheter du café"}, trigger_at=future)
        content = db_env["md"].read_text(encoding="utf-8")
        assert "Acheter du café" in content

    def test_tache_done_absente_de_one_shot(self, db_env):
        from Mnemo.tools.scheduler_tasks import create_task, mark_done
        past = (datetime.now() - timedelta(hours=1)).isoformat()
        create_task("md_done", "one_shot", "reminder",
                    {"message": "Tâche terminée"}, trigger_at=past)
        mark_done("md_done")
        content = db_env["md"].read_text(encoding="utf-8")
        # Les tâches done ne doivent pas apparaître dans ## One-shot
        lines    = content.splitlines()
        in_oneshot = False
        for line in lines:
            if "## One-shot" in line:
                in_oneshot = True
            if in_oneshot and "Tâche terminée" in line:
                pytest.fail("Tâche done trouvée dans la section One-shot")

    def test_tache_cancelled_absente(self, db_env):
        from Mnemo.tools.scheduler_tasks import create_task, cancel_task
        future = (datetime.now() + timedelta(hours=2)).isoformat()
        create_task("md_cancel", "one_shot", "reminder",
                    {"message": "A annuler"}, trigger_at=future)
        cancel_task("md_cancel")
        content = db_env["md"].read_text(encoding="utf-8")
        assert "A annuler" not in content


# ══════════════════════════════════════════════════════════════════
# Niveau 3 — SchedulerCrew.run() (kickoff mocké)
# ══════════════════════════════════════════════════════════════════

class TestSchedulerCrewRun:

    @pytest.fixture(autouse=True)
    def setup(self, db_env):
        """Isole la DB pour chaque test du groupe."""
        self._db_env = db_env

    def _run(self, json_payload: dict) -> str:
        from Mnemo.crew import SchedulerCrew
        crew_instance = SchedulerCrew()
        mock_result   = make_crew_result(json.dumps(json_payload))
        with patch("Mnemo.init_db.migrate_db"):
            with patch.object(crew_instance, "crew") as mock_crew:
                mock_crew.return_value.kickoff.return_value = mock_result
                return crew_instance.run({
                    "user_message":      "test",
                    "temporal_context":  "2026-03-06",
                    "evaluation_result": "{}",
                })

    # ── Création one_shot ─────────────────────────────────────────

    def test_creation_one_shot_insere_en_db(self):
        from Mnemo.tools.scheduler_tasks import list_tasks
        future = (datetime.now() + timedelta(hours=2)).isoformat()
        result = self._run({
            "tasks": [{
                "action":      "create",
                "task_type":   "one_shot",
                "task_action": "reminder",
                "trigger_at":  future,
                "cron_expr":   None,
                "payload":     {"message": "Appeler le médecin"},
            }],
            "confirmation_message": "Rappel créé.",
        })
        assert result == "Rappel créé."
        assert any(
            json.loads(t["payload"]).get("message") == "Appeler le médecin"
            for t in list_tasks()
        )

    # ── Création recurring ────────────────────────────────────────

    def test_creation_recurring_insere_en_db(self):
        from Mnemo.tools.scheduler_tasks import list_tasks
        result = self._run({
            "tasks": [{
                "action":      "create",
                "task_type":   "recurring",
                "task_action": "weekly",
                "trigger_at":  None,
                "cron_expr":   "weekly lundi 08:00",
                "payload":     {"message": "Résumé hebdo"},
            }],
            "confirmation_message": "Résumé planifié chaque lundi à 8h.",
        })
        assert "lundi" in result
        tasks = list_tasks()
        assert any(
            t["action"] == "weekly" and t["cron_expr"] == "weekly lundi 08:00"
            for t in tasks
        )

    # ── Création multi-tâches ─────────────────────────────────────

    def test_creation_multi_taches(self):
        from Mnemo.tools.scheduler_tasks import list_tasks
        self._run({
            "tasks": [
                {
                    "action":      "create",
                    "task_type":   "recurring",
                    "task_action": "briefing",
                    "trigger_at":  None,
                    "cron_expr":   "daily 07:00",
                    "payload":     {},
                },
                {
                    "action":      "create",
                    "task_type":   "recurring",
                    "task_action": "reminder",
                    "trigger_at":  None,
                    "cron_expr":   "daily 07:00",
                    "payload":     {"message": "Boire de l'eau"},
                },
            ],
            "confirmation_message": "Briefing + rappel planifiés chaque jour à 7h.",
        })
        actions = {t["action"] for t in list_tasks()}
        assert "briefing" in actions
        assert "reminder" in actions

    # ── Annulation ───────────────────────────────────────────────

    def test_annulation_tache_existante(self):
        from Mnemo.tools.scheduler_tasks import create_task, list_tasks
        future = (datetime.now() + timedelta(hours=2)).isoformat()
        create_task("usr_aaa", "one_shot", "reminder",
                    {"message": "A annuler"}, trigger_at=future)
        result = self._run({
            "tasks": [{
                "action":             "cancel",
                "task_id_to_cancel":  "usr_aaa",
            }],
            "confirmation_message": "Rappel annulé.",
        })
        assert result == "Rappel annulé."
        t = next(t for t in list_tasks() if t["id"] == "usr_aaa")
        assert t["status"] == "cancelled"

    def test_annulation_id_inexistant_signale_erreur(self):
        result = self._run({
            "tasks": [{
                "action":            "cancel",
                "task_id_to_cancel": "usr_inexistant",
            }],
            "confirmation_message": "Annulé.",
        })
        assert "introuvable" in result or "Erreurs" in result

    def test_annulation_sans_id_signale_erreur(self):
        result = self._run({
            "tasks": [{"action": "cancel"}],
            "confirmation_message": "Annulé.",
        })
        assert "identifiant" in result or "Erreurs" in result

    # ── Cas dégradés ─────────────────────────────────────────────

    def test_json_malformed_retourne_message_erreur(self):
        from Mnemo.crew import SchedulerCrew
        crew_instance = SchedulerCrew()
        mock_result   = make_crew_result("pas du JSON valide {{{")
        with patch("Mnemo.init_db.migrate_db"):
            with patch.object(crew_instance, "crew") as mock_crew:
                mock_crew.return_value.kickoff.return_value = mock_result
                result = crew_instance.run({
                    "user_message":      "test",
                    "temporal_context":  "2026-03-06",
                    "evaluation_result": "{}",
                })
        assert "reformuler" in result.lower() or "interpréter" in result.lower()

    def test_liste_vide_retourne_message(self):
        result = self._run({"tasks": [], "confirmation_message": ""})
        assert "Aucune tâche" in result

    def test_fences_markdown_dans_output_llm(self):
        """Le LLM peut wrapper le JSON dans des fences — le parser doit les ignorer."""
        from Mnemo.tools.scheduler_tasks import list_tasks
        future  = (datetime.now() + timedelta(hours=2)).isoformat()
        payload = {
            "tasks": [{
                "action":      "create",
                "task_type":   "one_shot",
                "task_action": "reminder",
                "trigger_at":  future,
                "cron_expr":   None,
                "payload":     {"message": "Test fences"},
            }],
            "confirmation_message": "OK.",
        }
        from Mnemo.crew import SchedulerCrew
        crew_instance = SchedulerCrew()
        # Simule un LLM qui wrappe le JSON dans des fences
        raw_with_fences = f"```json\n{json.dumps(payload)}\n```"
        mock_result     = make_crew_result(raw_with_fences)
        with patch("Mnemo.init_db.migrate_db"):
            with patch.object(crew_instance, "crew") as mock_crew:
                mock_crew.return_value.kickoff.return_value = mock_result
                result = crew_instance.run({
                    "user_message":      "test",
                    "temporal_context":  "2026-03-06",
                    "evaluation_result": "{}",
                })
        assert result == "OK."
        assert any(
            json.loads(t["payload"]).get("message") == "Test fences"
            for t in list_tasks()
        )


# ══════════════════════════════════════════════════════════════════
# Fixtures — couche exécution scheduler.py
# ══════════════════════════════════════════════════════════════════

@pytest.fixture
def sched_env(tmp_path, monkeypatch):
    """
    Redirige les paths module-level de scheduler.py vers tmp_path.
    Crée les dossiers nécessaires.
    """
    import Mnemo.scheduler as sched

    data_path    = tmp_path / "data"
    sessions_dir = data_path / "sessions"
    data_path.mkdir()
    sessions_dir.mkdir()

    briefing_out  = data_path / "briefing.md"
    weekly_out    = data_path / "weekly.md"
    markdown_path = data_path / "memory.md"

    monkeypatch.setattr(sched, "DATA_PATH",     data_path)
    monkeypatch.setattr(sched, "BRIEFING_OUT",  briefing_out)
    monkeypatch.setattr(sched, "WEEKLY_OUT",    weekly_out)
    monkeypatch.setattr(sched, "SESSIONS_DIR",  sessions_dir)
    monkeypatch.setattr(sched, "MARKDOWN_PATH", markdown_path)

    return {
        "data":     data_path,
        "sessions": sessions_dir,
        "briefing": briefing_out,
        "weekly":   weekly_out,
        "memory":   markdown_path,
    }


# ══════════════════════════════════════════════════════════════════
# Niveau 2 — action_reminder()
# ══════════════════════════════════════════════════════════════════

class TestActionReminder:

    def test_cree_briefing_md_si_absent(self, sched_env):
        from Mnemo.scheduler import action_reminder
        action_reminder({"message": "Boire de l'eau"})
        assert sched_env["briefing"].exists()
        assert "Boire de l'eau" in sched_env["briefing"].read_text(encoding="utf-8")

    def test_appende_a_briefing_existant(self, sched_env):
        from Mnemo.scheduler import action_reminder
        sched_env["briefing"].write_text("# Briefing du jour\n", encoding="utf-8")
        action_reminder({"message": "Point projets"})
        content = sched_env["briefing"].read_text(encoding="utf-8")
        assert "# Briefing du jour" in content
        assert "Point projets" in content

    def test_bloc_rappel_dans_output(self, sched_env):
        from Mnemo.scheduler import action_reminder
        action_reminder({"message": "Appeler le dentiste"})
        content = sched_env["briefing"].read_text(encoding="utf-8")
        assert "Rappel" in content
        assert "Appeler le dentiste" in content

    def test_payload_vide_message_fallback(self, sched_env):
        from Mnemo.scheduler import action_reminder
        action_reminder({})  # pas de clé "message"
        content = sched_env["briefing"].read_text(encoding="utf-8")
        assert "Rappel sans message" in content


# ══════════════════════════════════════════════════════════════════
# Niveau 2 — action_deadline_alert()
# ══════════════════════════════════════════════════════════════════

class TestActionDeadlineAlert:

    def _make_event(self, title: str, days_until: int, has_time: bool = True):
        from datetime import datetime
        dt = datetime.now() if has_time else None
        return {"title": title, "days_until": days_until, "datetime": dt}

    def test_injecte_alertes_dans_briefing_existant(self, sched_env):
        from Mnemo.scheduler import action_deadline_alert
        sched_env["briefing"].write_text("# Briefing\n", encoding="utf-8")
        events = [self._make_event("Réunion client", 1)]
        with patch("Mnemo.tools.calendar_tools.get_upcoming_events", return_value=events):
            action_deadline_alert()
        content = sched_env["briefing"].read_text(encoding="utf-8")
        assert "Réunion client" in content
        assert "Alertes" in content

    def test_cree_briefing_si_absent(self, sched_env):
        from Mnemo.scheduler import action_deadline_alert
        events = [self._make_event("Deadline projet", 3)]
        with patch("Mnemo.tools.calendar_tools.get_upcoming_events", return_value=events):
            action_deadline_alert()
        assert sched_env["briefing"].exists()
        assert "Deadline projet" in sched_env["briefing"].read_text(encoding="utf-8")

    def test_alerte_j1_libelle_demain(self, sched_env):
        from Mnemo.scheduler import action_deadline_alert
        events = [self._make_event("Rendez-vous médecin", 1)]
        with patch("Mnemo.tools.calendar_tools.get_upcoming_events", return_value=events):
            action_deadline_alert()
        content = sched_env["briefing"].read_text(encoding="utf-8")
        assert "Demain" in content

    def test_alerte_j3_libelle_dans_3_jours(self, sched_env):
        from Mnemo.scheduler import action_deadline_alert
        events = [self._make_event("Livraison sprint", 3)]
        with patch("Mnemo.tools.calendar_tools.get_upcoming_events", return_value=events):
            action_deadline_alert()
        content = sched_env["briefing"].read_text(encoding="utf-8")
        assert "3 jours" in content

    def test_ignore_evenements_autres_jours(self, sched_env):
        from Mnemo.scheduler import action_deadline_alert
        events = [
            self._make_event("Dans 2 jours", 2),
            self._make_event("Dans 4 jours", 4),
        ]
        with patch("Mnemo.tools.calendar_tools.get_upcoming_events", return_value=events):
            action_deadline_alert()
        assert not sched_env["briefing"].exists()

    def test_pas_de_doublon_si_alertes_deja_presentes(self, sched_env):
        from Mnemo.scheduler import action_deadline_alert
        sched_env["briefing"].write_text(
            "# Briefing\n\n## ⚠️ Alertes deadlines\n- déjà là\n",
            encoding="utf-8"
        )
        events = [self._make_event("Autre deadline", 1)]
        with patch("Mnemo.tools.calendar_tools.get_upcoming_events", return_value=events):
            action_deadline_alert()
        content = sched_env["briefing"].read_text(encoding="utf-8")
        # La section ne doit apparaître qu'une seule fois
        assert content.count("## ⚠️ Alertes deadlines") == 1

    def test_erreur_calendrier_ne_plante_pas(self, sched_env):
        from Mnemo.scheduler import action_deadline_alert
        with patch("Mnemo.tools.calendar_tools.get_upcoming_events",
                   side_effect=Exception("Calendrier indisponible")):
            action_deadline_alert()  # ne doit pas lever d'exception


# ══════════════════════════════════════════════════════════════════
# Niveau 3 — action_briefing() (BriefingCrew mocké)
# ══════════════════════════════════════════════════════════════════

class TestActionBriefing:

    def test_ecrit_briefing_md(self, sched_env):
        from Mnemo.scheduler import action_briefing
        mock_result = make_crew_result("# Briefing\n\nContenu du briefing.")
        with patch("Mnemo.crew.BriefingCrew") as MockCrew, \
             patch("Mnemo.tools.calendar_tools.get_upcoming_events", return_value=[]), \
             patch("Mnemo.tools.calendar_tools.format_events_for_prompt", return_value=""), \
             patch("Mnemo.tools.calendar_tools.get_temporal_context", return_value="2026-03-06"):
            MockCrew.return_value.crew.return_value.kickoff.return_value = mock_result
            action_briefing()
        assert sched_env["briefing"].exists()
        content = sched_env["briefing"].read_text(encoding="utf-8")
        assert "Contenu du briefing" in content

    def test_strip_fences_dans_output_llm(self, sched_env):
        from Mnemo.scheduler import action_briefing
        mock_result = make_crew_result("```markdown\n# Briefing\n\nContenu.\n```")
        with patch("Mnemo.crew.BriefingCrew") as MockCrew, \
             patch("Mnemo.tools.calendar_tools.get_upcoming_events", return_value=[]), \
             patch("Mnemo.tools.calendar_tools.format_events_for_prompt", return_value=""), \
             patch("Mnemo.tools.calendar_tools.get_temporal_context", return_value="2026-03-06"):
            MockCrew.return_value.crew.return_value.kickoff.return_value = mock_result
            action_briefing()
        content = sched_env["briefing"].read_text(encoding="utf-8")
        assert "```" not in content

    def test_fallback_si_crew_crash(self, sched_env):
        from Mnemo.scheduler import action_briefing
        with patch("Mnemo.crew.BriefingCrew") as MockCrew, \
             patch("Mnemo.tools.calendar_tools.get_upcoming_events", return_value=[]), \
             patch("Mnemo.tools.calendar_tools.format_events_for_prompt", return_value=""), \
             patch("Mnemo.tools.calendar_tools.get_temporal_context", return_value="2026-03-06"):
            MockCrew.return_value.crew.return_value.kickoff.side_effect = Exception("Ollama down")
            action_briefing()
        assert sched_env["briefing"].exists()
        content = sched_env["briefing"].read_text(encoding="utf-8")
        assert "indisponible" in content.lower() or "Erreur" in content

    def test_erreur_calendrier_ne_bloque_pas(self, sched_env):
        from Mnemo.scheduler import action_briefing
        mock_result = make_crew_result("# Briefing OK")
        with patch("Mnemo.crew.BriefingCrew") as MockCrew, \
             patch("Mnemo.tools.calendar_tools.get_upcoming_events",
                   side_effect=Exception("ICS indisponible")), \
             patch("Mnemo.tools.calendar_tools.get_temporal_context", return_value="2026-03-06"):
            MockCrew.return_value.crew.return_value.kickoff.return_value = mock_result
            action_briefing()
        assert sched_env["briefing"].exists()


# ══════════════════════════════════════════════════════════════════
# Niveau 3 — action_weekly() (BriefingCrew mocké)
# ══════════════════════════════════════════════════════════════════

class TestActionWeekly:

    def test_ecrit_weekly_md(self, sched_env):
        from Mnemo.scheduler import action_weekly
        mock_result = make_crew_result("# Weekly\n\nRésumé de la semaine.")
        with patch("Mnemo.crew.BriefingCrew") as MockCrew, \
             patch("Mnemo.tools.calendar_tools.get_events_for_date", return_value=[]), \
             patch("Mnemo.tools.calendar_tools.format_events_for_prompt", return_value=""):
            MockCrew.return_value.crew.return_value.kickoff.return_value = mock_result
            action_weekly()
        assert sched_env["weekly"].exists()
        content = sched_env["weekly"].read_text(encoding="utf-8")
        assert "Résumé de la semaine" in content

    def test_fallback_si_crew_crash(self, sched_env):
        from Mnemo.scheduler import action_weekly
        with patch("Mnemo.crew.BriefingCrew") as MockCrew, \
             patch("Mnemo.tools.calendar_tools.get_events_for_date", return_value=[]), \
             patch("Mnemo.tools.calendar_tools.format_events_for_prompt", return_value=""):
            MockCrew.return_value.crew.return_value.kickoff.side_effect = Exception("Crash")
            action_weekly()
        assert sched_env["weekly"].exists()
        content = sched_env["weekly"].read_text(encoding="utf-8")
        assert "indisponible" in content.lower() or "Erreur" in content


# ══════════════════════════════════════════════════════════════════
# Niveau 2 — dispatch()
# ══════════════════════════════════════════════════════════════════

class TestDispatch:

    def test_dispatch_reminder(self, sched_env):
        from Mnemo.scheduler import dispatch
        task = {"action": "reminder", "payload": '{"message": "Test dispatch"}'}
        with patch("Mnemo.scheduler.action_reminder") as mock_fn:
            dispatch(task)
            mock_fn.assert_called_once()

    def test_dispatch_briefing(self, sched_env):
        from Mnemo.scheduler import dispatch
        task = {"action": "briefing", "payload": "{}"}
        with patch("Mnemo.scheduler.action_briefing") as mock_fn:
            dispatch(task)
            mock_fn.assert_called_once()

    def test_dispatch_weekly(self, sched_env):
        from Mnemo.scheduler import dispatch
        task = {"action": "weekly", "payload": "{}"}
        with patch("Mnemo.scheduler.action_weekly") as mock_fn:
            dispatch(task)
            mock_fn.assert_called_once()

    def test_dispatch_deadline_alert(self, sched_env):
        from Mnemo.scheduler import dispatch
        task = {"action": "deadline_alert", "payload": "{}"}
        with patch("Mnemo.scheduler.action_deadline_alert") as mock_fn:
            dispatch(task)
            mock_fn.assert_called_once()

    def test_dispatch_action_inconnue_ne_plante_pas(self, sched_env):
        from Mnemo.scheduler import dispatch
        dispatch({"action": "action_inexistante", "payload": "{}"})  # ne doit pas lever

    def test_dispatch_payload_malformed_ne_plante_pas(self, sched_env):
        from Mnemo.scheduler import dispatch
        task = {"action": "reminder", "payload": "pas du json {{{"}
        with patch("Mnemo.scheduler.action_reminder"):
            dispatch(task)  # ne doit pas lever


# ══════════════════════════════════════════════════════════════════
# Niveau 2 — run_now()
# ══════════════════════════════════════════════════════════════════

class TestRunNow:

    def test_run_now_briefing(self, sched_env):
        from Mnemo.scheduler import run_now
        with patch("Mnemo.scheduler.action_briefing") as mock_b, \
             patch("Mnemo.scheduler.action_weekly") as mock_w, \
             patch("Mnemo.scheduler.action_deadline_alert") as mock_d:
            run_now(["briefing"])
            mock_b.assert_called_once()
            mock_w.assert_not_called()
            mock_d.assert_not_called()

    def test_run_now_weekly(self, sched_env):
        from Mnemo.scheduler import run_now
        with patch("Mnemo.scheduler.action_briefing") as mock_b, \
             patch("Mnemo.scheduler.action_weekly") as mock_w, \
             patch("Mnemo.scheduler.action_deadline_alert") as mock_d:
            run_now(["weekly"])
            mock_w.assert_called_once()
            mock_b.assert_not_called()
            mock_d.assert_not_called()

    def test_run_now_all_appelle_les_trois(self, sched_env):
        from Mnemo.scheduler import run_now
        with patch("Mnemo.scheduler.action_briefing") as mock_b, \
             patch("Mnemo.scheduler.action_weekly") as mock_w, \
             patch("Mnemo.scheduler.action_deadline_alert") as mock_d:
            run_now(["all"])
            mock_b.assert_called_once()
            mock_w.assert_called_once()
            mock_d.assert_called_once()

    def test_run_now_cible_inconnue_ne_plante_pas(self, sched_env):
        from Mnemo.scheduler import run_now
        run_now(["cible_inexistante"])  # ne doit pas lever
