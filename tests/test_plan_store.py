"""
Tests Phase 6 — PlanStore : création, lecture, écriture des plans persistants.
Niveau 1 — Python pur, aucun LLM.
"""
import pytest
from pathlib import Path


# ── Fixture ───────────────────────────────────────────────────

@pytest.fixture()
def store_env(tmp_path, monkeypatch):
    """Redirige get_data_dir vers tmp_path."""
    import Mnemo.tools.plan_tools as pt
    monkeypatch.setattr(pt, "get_data_dir", lambda: tmp_path)
    return tmp_path


# ── Création ──────────────────────────────────────────────────

class TestCreate:
    def test_retourne_path_existant(self, store_env):
        from Mnemo.tools.plan_tools import PlanStore
        p = PlanStore.create("construire le classifier", ["Étape A", "Étape B"])
        assert p.exists()

    def test_nom_fichier_contient_hash(self, store_env):
        from Mnemo.tools.plan_tools import PlanStore
        p = PlanStore.create("goal unique", ["Étape 1"])
        assert p.name.startswith("plan_")
        assert p.suffix == ".md"

    def test_contenu_contient_goal(self, store_env):
        from Mnemo.tools.plan_tools import PlanStore
        p = PlanStore.create("mon super goal", ["Étape 1"])
        text = p.read_text()
        assert "mon super goal" in text

    def test_etapes_en_todo(self, store_env):
        from Mnemo.tools.plan_tools import PlanStore
        p = PlanStore.create("goal", ["Lire le fichier", "Écrire les tests"])
        text = p.read_text()
        assert "- [ ] Lire le fichier" in text
        assert "- [ ] Écrire les tests" in text

    def test_statut_initial_en_cours(self, store_env):
        from Mnemo.tools.plan_tools import PlanStore, STATUS_IN_PROGRESS
        p = PlanStore.create("goal", ["Étape 1"])
        assert STATUS_IN_PROGRESS in p.read_text()

    def test_contexte_inclus(self, store_env):
        from Mnemo.tools.plan_tools import PlanStore
        p = PlanStore.create("goal", ["Étape 1"], context="contexte de test")
        assert "contexte de test" in p.read_text()

    def test_crew_target_annote(self, store_env):
        from Mnemo.tools.plan_tools import PlanStore
        p = PlanStore.create(
            "goal", ["Étape 1"],
            crew_targets={"Étape 1": "conversation"},
        )
        assert "crew : conversation" in p.read_text()

    def test_deux_plans_differents_deux_fichiers(self, store_env):
        from Mnemo.tools.plan_tools import PlanStore
        p1 = PlanStore.create("goal A", ["Étape 1"])
        p2 = PlanStore.create("goal B", ["Étape 1"])
        assert p1 != p2

    def test_journal_contient_creation(self, store_env):
        from Mnemo.tools.plan_tools import PlanStore
        p = PlanStore.create("goal", ["Étape 1"])
        assert "Plan créé" in p.read_text()


# ── get_active ────────────────────────────────────────────────

class TestGetActive:
    def test_plan_en_cours_retourne(self, store_env):
        from Mnemo.tools.plan_tools import PlanStore
        PlanStore.create("goal actif", ["Étape 1"])
        assert len(PlanStore.get_active()) == 1

    def test_plan_termine_non_retourne(self, store_env):
        from Mnemo.tools.plan_tools import PlanStore
        p = PlanStore.create("goal", ["Étape 1"])
        PlanStore.mark_done(p, "Étape 1")
        assert PlanStore.get_active() == []

    def test_plusieurs_plans_actifs(self, store_env):
        from Mnemo.tools.plan_tools import PlanStore
        PlanStore.create("goal A", ["Étape 1"])
        PlanStore.create("goal B", ["Étape 2"])
        assert len(PlanStore.get_active()) == 2

    def test_aucun_plan(self, store_env):
        from Mnemo.tools.plan_tools import PlanStore
        assert PlanStore.get_active() == []


# ── get_next_step ─────────────────────────────────────────────

class TestGetNextStep:
    def test_retourne_premiere_etape(self, store_env):
        from Mnemo.tools.plan_tools import PlanStore
        p = PlanStore.create("goal", ["Étape A", "Étape B"])
        assert PlanStore.get_next_step(p) == "Étape A"

    def test_retourne_none_si_tout_fait(self, store_env):
        from Mnemo.tools.plan_tools import PlanStore
        p = PlanStore.create("goal", ["Étape A"])
        PlanStore.mark_done(p, "Étape A")
        assert PlanStore.get_next_step(p) is None

    def test_saute_les_etapes_faites(self, store_env):
        from Mnemo.tools.plan_tools import PlanStore
        p = PlanStore.create("goal", ["Étape A", "Étape B", "Étape C"])
        PlanStore.mark_done(p, "Étape A")
        assert PlanStore.get_next_step(p) == "Étape B"


# ── mark_done ─────────────────────────────────────────────────

class TestMarkDone:
    def test_coche_etape(self, store_env):
        from Mnemo.tools.plan_tools import PlanStore
        p = PlanStore.create("goal", ["Faire X"])
        PlanStore.mark_done(p, "Faire X")
        assert "- [x] Faire X" in p.read_text()

    def test_todo_disparait(self, store_env):
        from Mnemo.tools.plan_tools import PlanStore
        p = PlanStore.create("goal", ["Faire X"])
        PlanStore.mark_done(p, "Faire X")
        assert "- [ ] Faire X" not in p.read_text()

    def test_date_ajoutee(self, store_env):
        from Mnemo.tools.plan_tools import PlanStore
        p = PlanStore.create("goal", ["Faire X"])
        PlanStore.mark_done(p, "Faire X")
        assert "✅" in p.read_text()

    def test_statut_termine_si_dernier(self, store_env):
        from Mnemo.tools.plan_tools import PlanStore, STATUS_DONE
        p = PlanStore.create("goal", ["Unique étape"])
        PlanStore.mark_done(p, "Unique étape")
        assert STATUS_DONE in p.read_text()

    def test_statut_reste_en_cours_si_reste(self, store_env):
        from Mnemo.tools.plan_tools import PlanStore, STATUS_IN_PROGRESS
        p = PlanStore.create("goal", ["Étape 1", "Étape 2"])
        PlanStore.mark_done(p, "Étape 1")
        assert STATUS_IN_PROGRESS in p.read_text()

    def test_etape_inconnue_sans_crash(self, store_env):
        from Mnemo.tools.plan_tools import PlanStore
        p = PlanStore.create("goal", ["Étape 1"])
        PlanStore.mark_done(p, "Étape inexistante")  # ne doit pas lever


# ── is_complete ───────────────────────────────────────────────

class TestIsComplete:
    def test_faux_si_etapes_restantes(self, store_env):
        from Mnemo.tools.plan_tools import PlanStore
        p = PlanStore.create("goal", ["Étape 1", "Étape 2"])
        assert not PlanStore.is_complete(p)

    def test_vrai_si_tout_coche(self, store_env):
        from Mnemo.tools.plan_tools import PlanStore
        p = PlanStore.create("goal", ["Étape 1", "Étape 2"])
        PlanStore.mark_done(p, "Étape 1")
        PlanStore.mark_done(p, "Étape 2")
        assert PlanStore.is_complete(p)


# ── add_blocker ───────────────────────────────────────────────

class TestAddBlocker:
    def test_bloquant_ajoute(self, store_env):
        from Mnemo.tools.plan_tools import PlanStore
        p = PlanStore.create("goal", ["Étape 1"])
        PlanStore.add_blocker(p, "lib manquante")
        assert "lib manquante" in p.read_text()

    def test_statut_bloque(self, store_env):
        from Mnemo.tools.plan_tools import PlanStore, STATUS_BLOCKED
        p = PlanStore.create("goal", ["Étape 1"])
        PlanStore.add_blocker(p, "dépendance absente")
        assert STATUS_BLOCKED in p.read_text()

    def test_plusieurs_bloquants(self, store_env):
        from Mnemo.tools.plan_tools import PlanStore
        p = PlanStore.create("goal", ["Étape 1"])
        PlanStore.add_blocker(p, "bloquant A")
        PlanStore.add_blocker(p, "bloquant B")
        text = p.read_text()
        assert "bloquant A" in text
        assert "bloquant B" in text


# ── append_log ────────────────────────────────────────────────

class TestAppendLog:
    def test_entree_ajoutee(self, store_env):
        from Mnemo.tools.plan_tools import PlanStore
        p = PlanStore.create("goal", ["Étape 1"])
        PlanStore.append_log(p, "test d'entrée journal")
        assert "test d'entrée journal" in p.read_text()

    def test_plusieurs_entrees(self, store_env):
        from Mnemo.tools.plan_tools import PlanStore
        p = PlanStore.create("goal", ["Étape 1"])
        PlanStore.append_log(p, "entrée 1")
        PlanStore.append_log(p, "entrée 2")
        text = p.read_text()
        assert "entrée 1" in text
        assert "entrée 2" in text


# ── list_steps ────────────────────────────────────────────────

class TestListSteps:
    def test_retourne_toutes_les_etapes(self, store_env):
        from Mnemo.tools.plan_tools import PlanStore
        p = PlanStore.create("goal", ["A", "B", "C"])
        steps = PlanStore.list_steps(p)
        assert len(steps) == 3

    def test_statut_correct(self, store_env):
        from Mnemo.tools.plan_tools import PlanStore
        p = PlanStore.create("goal", ["A", "B"])
        PlanStore.mark_done(p, "A")
        steps = PlanStore.list_steps(p)
        done  = [s for s in steps if s["done"]]
        todo  = [s for s in steps if not s["done"]]
        assert len(done) == 1
        assert len(todo) == 1
