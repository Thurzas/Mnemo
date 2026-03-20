"""
Tests Phase 7 — kg_tools.py

Couvre :
  - kg_node_id       : déterminisme + unicité
  - kg_add_node      : idempotence, retour ID
  - kg_add_edge      : idempotence, relation invalide
  - kg_add_triplet   : raccourci end-to-end
  - kg_reinforce_edge: delta +/-, floor 0.01
  - kg_record_event  : insertion sans modifier weight
  - kg_query         : filtres src/rel/dst
  - kg_steps_for_task, kg_actions_for_step, kg_preconditions, kg_effects
  - kg_blocking_states, kg_causes
  - kg_get_node, kg_search_nodes
  - Double-lookup seed : seed en fallback, pas de doublons

Aucun LLM requis.
"""
import hashlib
import pytest
from pathlib import Path
from unittest.mock import patch

from Mnemo.init_db import init_db, init_kg_db
from Mnemo.tools.kg_tools import (
    kg_node_id, kg_add_node, kg_add_edge, kg_add_triplet,
    kg_reinforce_edge, kg_record_event,
    kg_query, kg_steps_for_task, kg_actions_for_step,
    kg_preconditions_for_action, kg_effects_for_action,
    kg_blocking_states, kg_causes,
    kg_get_node, kg_search_nodes,
    VALID_RELATIONS,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _no_real_seed():
    """
    Isole tous les tests du seed réel sur disque.
    Les tests double-lookup surchargent SEED_DB_PATH avec leur propre fixture.
    """
    with patch("Mnemo.tools.kg_tools.SEED_DB_PATH", Path("/nonexistent/kg_seed.db")):
        yield


@pytest.fixture
def db(tmp_path) -> Path:
    p = tmp_path / "memory.db"
    init_db(db_path=p)
    return p


@pytest.fixture
def seed_db(tmp_path) -> Path:
    """DB seed minimale pour tester le double-lookup."""
    p = tmp_path / "kg_seed.db"
    init_kg_db(p)
    # Peuplement seed : (task: projet web) -[contains]-> (step: init env)
    kg_add_triplet(p, "task", "projet web", "contains", "step", "init env", source="seed")
    kg_add_triplet(p, "step", "init env", "requires", "action", "sandbox_shell: npm init",
                   source="seed")
    kg_add_triplet(p, "action", "sandbox_shell: npm init", "precondition", "state",
                   "node_available", source="seed")
    kg_add_triplet(p, "action", "sandbox_shell: npm init", "effect", "state",
                   "npm_project_initialized", source="seed")
    return p


# ══════════════════════════════════════════════════════════════════════════════
# 1. kg_node_id
# ══════════════════════════════════════════════════════════════════════════════

class TestKgNodeId:

    def test_deterministe(self):
        assert kg_node_id("task", "foo") == kg_node_id("task", "foo")

    def test_type_different_id_different(self):
        assert kg_node_id("task", "foo") != kg_node_id("step", "foo")

    def test_label_different_id_different(self):
        assert kg_node_id("task", "foo") != kg_node_id("task", "bar")

    def test_format_sha1(self):
        nid = kg_node_id("task", "foo")
        assert len(nid) == 40
        int(nid, 16)  # valide hexadécimal


# ══════════════════════════════════════════════════════════════════════════════
# 2. kg_add_node
# ══════════════════════════════════════════════════════════════════════════════

class TestKgAddNode:

    def test_retourne_id(self, db):
        nid = kg_add_node(db, "task", "documenter React")
        assert nid == kg_node_id("task", "documenter React")

    def test_idempotent(self, db):
        id1 = kg_add_node(db, "task", "foo")
        id2 = kg_add_node(db, "task", "foo")
        assert id1 == id2

    def test_metadata_stockee(self, db):
        import sqlite3, json
        kg_add_node(db, "action", "web_search", metadata={"cost": 2})
        conn = sqlite3.connect(db)
        row = conn.execute("SELECT metadata FROM kg_nodes WHERE label='web_search'").fetchone()
        assert json.loads(row[0])["cost"] == 2

    def test_source_seed(self, db):
        import sqlite3
        kg_add_node(db, "task", "seed_task", source="seed")
        conn = sqlite3.connect(db)
        row = conn.execute("SELECT source FROM kg_nodes WHERE label='seed_task'").fetchone()
        assert row[0] == "seed"


# ══════════════════════════════════════════════════════════════════════════════
# 3. kg_add_edge
# ══════════════════════════════════════════════════════════════════════════════

class TestKgAddEdge:

    def _two_nodes(self, db) -> tuple[str, str]:
        src = kg_add_node(db, "task", "créer projet")
        dst = kg_add_node(db, "step", "initialiser")
        return src, dst

    def test_insert_edge(self, db):
        import sqlite3
        src, dst = self._two_nodes(db)
        kg_add_edge(db, src, "contains", dst)
        conn = sqlite3.connect(db)
        row = conn.execute("SELECT rel FROM kg_edges WHERE src=? AND dst=?", (src, dst)).fetchone()
        assert row[0] == "contains"

    def test_idempotent(self, db):
        import sqlite3
        src, dst = self._two_nodes(db)
        kg_add_edge(db, src, "contains", dst)
        kg_add_edge(db, src, "contains", dst)
        conn = sqlite3.connect(db)
        count = conn.execute("SELECT COUNT(*) FROM kg_edges").fetchone()[0]
        assert count == 1

    def test_relation_invalide_leve_exception(self, db):
        src = kg_add_node(db, "task", "t")
        dst = kg_add_node(db, "step", "s")
        with pytest.raises(ValueError, match="Relation inconnue"):
            kg_add_edge(db, src, "invented_rel", dst)

    def test_toutes_relations_valides(self, db):
        for i, rel in enumerate(sorted(VALID_RELATIONS)):
            src = kg_add_node(db, "concept", f"src_{i}")
            dst = kg_add_node(db, "concept", f"dst_{i}")
            kg_add_edge(db, src, rel, dst)  # ne doit pas lever


# ══════════════════════════════════════════════════════════════════════════════
# 4. kg_add_triplet
# ══════════════════════════════════════════════════════════════════════════════

class TestKgAddTriplet:

    def test_cree_noeuds_et_relation(self, db):
        import sqlite3
        src_id, dst_id = kg_add_triplet(db, "task", "T", "contains", "step", "S")
        conn = sqlite3.connect(db)
        assert conn.execute("SELECT COUNT(*) FROM kg_nodes").fetchone()[0] == 2
        assert conn.execute("SELECT COUNT(*) FROM kg_edges").fetchone()[0] == 1

    def test_retourne_ids_corrects(self, db):
        src_id, dst_id = kg_add_triplet(db, "task", "T", "contains", "step", "S")
        assert src_id == kg_node_id("task", "T")
        assert dst_id == kg_node_id("step", "S")

    def test_idempotent(self, db):
        import sqlite3
        kg_add_triplet(db, "task", "T", "contains", "step", "S")
        kg_add_triplet(db, "task", "T", "contains", "step", "S")
        conn = sqlite3.connect(db)
        assert conn.execute("SELECT COUNT(*) FROM kg_edges").fetchone()[0] == 1


# ══════════════════════════════════════════════════════════════════════════════
# 5. kg_reinforce_edge
# ══════════════════════════════════════════════════════════════════════════════

class TestKgReinforceEdge:

    def _setup_edge(self, db) -> tuple[str, str]:
        src, dst = kg_add_triplet(db, "step", "écrire docs", "requires", "action", "web_search")
        return src, dst

    def test_renforce_weight(self, db):
        import sqlite3
        src, dst = self._setup_edge(db)
        kg_reinforce_edge(db, src, "requires", dst, delta=0.5)
        conn = sqlite3.connect(db)
        w = conn.execute("SELECT weight FROM kg_edges WHERE src=? AND dst=?", (src, dst)).fetchone()[0]
        assert w == pytest.approx(1.5)

    def test_affaiblit_weight(self, db):
        import sqlite3
        src, dst = self._setup_edge(db)
        kg_reinforce_edge(db, src, "requires", dst, delta=-0.5)
        conn = sqlite3.connect(db)
        w = conn.execute("SELECT weight FROM kg_edges WHERE src=? AND dst=?", (src, dst)).fetchone()[0]
        assert w == pytest.approx(0.5)

    def test_floor_001(self, db):
        import sqlite3
        src, dst = self._setup_edge(db)
        kg_reinforce_edge(db, src, "requires", dst, delta=-999.0)
        conn = sqlite3.connect(db)
        w = conn.execute("SELECT weight FROM kg_edges WHERE src=? AND dst=?", (src, dst)).fetchone()[0]
        assert w == pytest.approx(0.01)

    def test_enregistre_event(self, db):
        import sqlite3
        src, dst = self._setup_edge(db)
        kg_reinforce_edge(db, src, "requires", dst, delta=0.1,
                          session_id="sess_1", outcome="success")
        conn = sqlite3.connect(db)
        row = conn.execute(
            "SELECT outcome, delta FROM kg_edge_events WHERE session_id='sess_1'"
        ).fetchone()
        assert row[0] == "success"
        assert row[1] == pytest.approx(0.1)


# ══════════════════════════════════════════════════════════════════════════════
# 6. kg_record_event
# ══════════════════════════════════════════════════════════════════════════════

class TestKgRecordEvent:

    def test_event_sans_modifier_weight(self, db):
        import sqlite3
        src, dst = kg_add_triplet(db, "step", "s", "requires", "action", "a")
        kg_record_event(db, src, "requires", dst, outcome="skipped", session_id="sess_x")
        conn = sqlite3.connect(db)
        # Weight intact
        w = conn.execute("SELECT weight FROM kg_edges WHERE src=? AND dst=?", (src, dst)).fetchone()[0]
        assert w == pytest.approx(1.0)
        # Event présent
        row = conn.execute(
            "SELECT outcome FROM kg_edge_events WHERE session_id='sess_x'"
        ).fetchone()
        assert row[0] == "skipped"


# ══════════════════════════════════════════════════════════════════════════════
# 7. Requêtes de haut niveau
# ══════════════════════════════════════════════════════════════════════════════

class TestKgQueries:

    @pytest.fixture(autouse=True)
    def _populate(self, db):
        """
        Peuple un graphe de test :
          (task: doc React) -[contains]→ (step: recherche)
          (task: doc React) -[contains]→ (step: écriture)
          (step: recherche) -[requires]→ (action: web_search)
          (action: web_search) -[precondition]→ (state: web_available)
          (action: web_search) -[effect]→ (state: web_results_ready)
          (action: web_search) -[causes]→ (action: sandbox_write)
          (state: sandbox_readonly) -[blocks]→ (action: sandbox_write)
        """
        self.db = db
        kg_add_triplet(db, "task",   "doc React",        "contains",     "step",   "recherche")
        kg_add_triplet(db, "task",   "doc React",        "contains",     "step",   "écriture")
        kg_add_triplet(db, "step",   "recherche",        "requires",     "action", "web_search")
        kg_add_triplet(db, "action", "web_search",       "precondition", "state",  "web_available")
        kg_add_triplet(db, "action", "web_search",       "effect",       "state",  "web_results_ready")
        kg_add_triplet(db, "action", "web_search",       "causes",       "action", "sandbox_write")
        kg_add_triplet(db, "state",  "sandbox_readonly", "blocks",       "action", "sandbox_write")

    def test_kg_query_par_rel(self):
        rows = kg_query(self.db, rel="contains")
        assert len(rows) == 2
        labels = {r["dst_label"] for r in rows}
        assert labels == {"recherche", "écriture"}

    def test_steps_for_task(self):
        steps = kg_steps_for_task(self.db, "doc React")
        assert len(steps) == 2
        assert {s["dst_label"] for s in steps} == {"recherche", "écriture"}

    def test_actions_for_step(self):
        actions = kg_actions_for_step(self.db, "recherche")
        assert len(actions) == 1
        assert actions[0]["dst_label"] == "web_search"

    def test_preconditions_for_action(self):
        preconds = kg_preconditions_for_action(self.db, "web_search")
        assert "web_available" in preconds

    def test_effects_for_action(self):
        effects = kg_effects_for_action(self.db, "web_search")
        assert "web_results_ready" in effects

    def test_causes(self):
        caused = kg_causes(self.db, "web_search")
        assert "sandbox_write" in caused

    def test_blocking_states(self):
        blockers = kg_blocking_states(self.db, "sandbox_write")
        assert "sandbox_readonly" in blockers

    def test_task_inexistante_retourne_vide(self):
        assert kg_steps_for_task(self.db, "tâche inconnue") == []

    def test_action_sans_preconditions(self):
        kg_add_node(self.db, "action", "action_nue")
        assert kg_preconditions_for_action(self.db, "action_nue") == []


# ══════════════════════════════════════════════════════════════════════════════
# 8. kg_get_node et kg_search_nodes
# ══════════════════════════════════════════════════════════════════════════════

class TestKgGetSearch:

    def test_get_node_existant(self, db):
        kg_add_node(db, "task", "mon projet")
        node = kg_get_node(db, "task", "mon projet")
        assert node is not None
        assert node["label"] == "mon projet"
        assert node["type"] == "task"

    def test_get_node_inexistant(self, db):
        assert kg_get_node(db, "task", "fantôme") is None

    def test_search_par_type(self, db):
        kg_add_node(db, "task", "tâche 1")
        kg_add_node(db, "task", "tâche 2")
        kg_add_node(db, "step", "étape 1")
        results = kg_search_nodes(db, type_="task")
        assert len(results) == 2
        assert all(r["type"] == "task" for r in results)

    def test_search_par_label_fragment(self, db):
        kg_add_node(db, "action", "web_search")
        kg_add_node(db, "action", "web_fetch")
        kg_add_node(db, "action", "sandbox_write")
        results = kg_search_nodes(db, label_contains="web")
        assert len(results) == 2

    def test_search_sans_filtre_retourne_tout(self, db):
        kg_add_node(db, "task", "T")
        kg_add_node(db, "step", "S")
        results = kg_search_nodes(db)
        assert len(results) == 2


# ══════════════════════════════════════════════════════════════════════════════
# 9. Double-lookup seed
# ══════════════════════════════════════════════════════════════════════════════

class TestDoubleLookupSeed:

    def test_seed_en_fallback(self, db, seed_db):
        """KG user vide → résultats du seed retournés."""
        with patch("Mnemo.tools.kg_tools.SEED_DB_PATH", seed_db):
            steps = kg_steps_for_task(db, "projet web")
        assert any(s["dst_label"] == "init env" for s in steps)

    def test_user_prioritaire_sur_seed(self, db, seed_db):
        """
        Si user a (projet web)-[contains]->(init env) avec weight=5.0,
        le résultat user doit apparaître et pas le doublon seed.
        """
        kg_add_triplet(db, "task", "projet web", "contains", "step", "init env")
        import sqlite3
        conn = sqlite3.connect(db)
        conn.execute("""
            UPDATE kg_edges SET weight=5.0
            WHERE src=? AND rel='contains' AND dst=?
        """, (kg_node_id("task", "projet web"), kg_node_id("step", "init env")))
        conn.commit()
        conn.close()

        with patch("Mnemo.tools.kg_tools.SEED_DB_PATH", seed_db):
            steps = kg_steps_for_task(db, "projet web")

        # Un seul résultat (pas de doublon)
        init_env_steps = [s for s in steps if s["dst_label"] == "init env"]
        assert len(init_env_steps) == 1
        assert init_env_steps[0]["weight"] == pytest.approx(5.0)

    def test_seed_absent_ne_plante_pas(self, db):
        """Seed inexistant → pas d'exception, juste les résultats user."""
        absent = Path("/nonexistent/kg_seed.db")
        with patch("Mnemo.tools.kg_tools.SEED_DB_PATH", absent):
            results = kg_steps_for_task(db, "quelque chose")
        assert results == []

    def test_seed_actions_accessibles(self, db, seed_db):
        """Actions du seed visibles depuis un user KG vide."""
        with patch("Mnemo.tools.kg_tools.SEED_DB_PATH", seed_db):
            actions = kg_actions_for_step(db, "init env")
        assert any(a["dst_label"] == "sandbox_shell: npm init" for a in actions)

    def test_seed_preconditions_accessibles(self, db, seed_db):
        with patch("Mnemo.tools.kg_tools.SEED_DB_PATH", seed_db):
            preconds = kg_preconditions_for_action(db, "sandbox_shell: npm init")
        assert "node_available" in preconds