"""
Tests Phase 7 — HP-KG : schéma SQLite des tables kg_nodes / kg_edges / kg_edge_events

Couvre :
  - init_db  : crée les 3 tables + index sur DB vierge
  - migrate_db : ajoute les tables sur DB existante (idempotent)
  - Contrainte UNIQUE(src, rel, dst) sur kg_edges
  - CASCADE ON DELETE sur kg_nodes → kg_edges
  - Insert / query basiques
  - kg_edge_events : enregistrement outcome + delta

Aucun LLM requis.
"""
import hashlib
import json
import sqlite3
import pytest
from pathlib import Path

from Mnemo.init_db import init_db, migrate_db


# ── Helpers ───────────────────────────────────────────────────────────────────

def _node_id(type_: str, label: str) -> str:
    return hashlib.sha1(f"{type_}/{label}".encode()).hexdigest()


def _fresh_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "test_kg.db"
    init_db(db_path=db_path)
    return db_path


def _conn(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


# ══════════════════════════════════════════════════════════════════════════════
# 1. init_db — tables et index créés
# ══════════════════════════════════════════════════════════════════════════════

class TestInitDbKgTables:

    def test_kg_nodes_existe(self, tmp_path):
        db = _fresh_db(tmp_path)
        conn = _conn(db)
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='kg_nodes'"
        ).fetchall()
        assert rows, "table kg_nodes absente"

    def test_kg_edges_existe(self, tmp_path):
        db = _fresh_db(tmp_path)
        conn = _conn(db)
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='kg_edges'"
        ).fetchall()
        assert rows, "table kg_edges absente"

    def test_kg_edge_events_existe(self, tmp_path):
        db = _fresh_db(tmp_path)
        conn = _conn(db)
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='kg_edge_events'"
        ).fetchall()
        assert rows, "table kg_edge_events absente"

    def test_index_kg_nodes_type(self, tmp_path):
        db = _fresh_db(tmp_path)
        conn = _conn(db)
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name='kg_nodes_type'"
        ).fetchall()
        assert rows, "index kg_nodes_type absent"

    def test_index_kg_edges_src(self, tmp_path):
        db = _fresh_db(tmp_path)
        conn = _conn(db)
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name='kg_edges_src'"
        ).fetchall()
        assert rows, "index kg_edges_src absent"

    def test_index_kg_edges_rel(self, tmp_path):
        db = _fresh_db(tmp_path)
        conn = _conn(db)
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name='kg_edges_rel'"
        ).fetchall()
        assert rows, "index kg_edges_rel absent"


# ══════════════════════════════════════════════════════════════════════════════
# 2. migrate_db — idempotent sur DB existante
# ══════════════════════════════════════════════════════════════════════════════

class TestMigrateDbKg:

    def test_migrate_sur_db_vierge(self, tmp_path):
        """migrate_db sur DB fraîche ne plante pas."""
        db = tmp_path / "fresh.db"
        init_db(db_path=db)
        migrate_db(db_path=db)   # ne doit pas lever
        conn = _conn(db)
        assert conn.execute("SELECT COUNT(*) FROM kg_nodes").fetchone()[0] == 0

    def test_migrate_idempotent(self, tmp_path):
        """Deux appels successifs à migrate_db → pas d'erreur."""
        db = tmp_path / "idem.db"
        init_db(db_path=db)
        migrate_db(db_path=db)
        migrate_db(db_path=db)   # second appel doit être silencieux

    def test_migrate_sur_db_ancienne(self, tmp_path):
        """DB sans tables KG → migrate_db les crée."""
        db = tmp_path / "old.db"
        # Crée une DB avec seulement la table chunks (simulation DB pré-Phase 7)
        conn = sqlite3.connect(db)
        conn.execute("""
            CREATE TABLE chunks (
                id TEXT PRIMARY KEY,
                section TEXT,
                content TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.commit()
        conn.close()

        migrate_db(db_path=db)

        conn = _conn(db)
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        assert "kg_nodes" in tables
        assert "kg_edges" in tables
        assert "kg_edge_events" in tables
        # La table existante n'a pas été détruite
        assert "chunks" in tables


# ══════════════════════════════════════════════════════════════════════════════
# 3. Opérations basiques sur kg_nodes
# ══════════════════════════════════════════════════════════════════════════════

class TestKgNodes:

    def test_insert_node(self, tmp_path):
        db = _fresh_db(tmp_path)
        conn = _conn(db)
        nid = _node_id("task", "documenter React")
        conn.execute(
            "INSERT INTO kg_nodes(id, type, label, source) VALUES (?,?,?,?)",
            (nid, "task", "documenter React", "seed"),
        )
        conn.commit()
        row = conn.execute("SELECT type, label, source FROM kg_nodes WHERE id=?", (nid,)).fetchone()
        assert row == ("task", "documenter React", "seed")

    def test_insert_node_metadata_json(self, tmp_path):
        db = _fresh_db(tmp_path)
        conn = _conn(db)
        meta = json.dumps({"cost": 3, "domain": "web"})
        nid = _node_id("action", "web_search")
        conn.execute(
            "INSERT INTO kg_nodes(id, type, label, metadata) VALUES (?,?,?,?)",
            (nid, "action", "web_search", meta),
        )
        conn.commit()
        raw = conn.execute("SELECT metadata FROM kg_nodes WHERE id=?", (nid,)).fetchone()[0]
        assert json.loads(raw)["cost"] == 3

    def test_node_id_unique(self, tmp_path):
        db = _fresh_db(tmp_path)
        conn = _conn(db)
        nid = _node_id("task", "duplicate")
        conn.execute("INSERT INTO kg_nodes(id, type, label) VALUES (?,?,?)", (nid, "task", "duplicate"))
        conn.commit()
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO kg_nodes(id, type, label) VALUES (?,?,?)", (nid, "task", "duplicate"))
            conn.commit()


# ══════════════════════════════════════════════════════════════════════════════
# 4. Opérations basiques sur kg_edges
# ══════════════════════════════════════════════════════════════════════════════

class TestKgEdges:

    def _insert_two_nodes(self, conn):
        src_id = _node_id("task", "créer projet web")
        dst_id = _node_id("step", "initialiser environnement")
        conn.execute("INSERT INTO kg_nodes(id, type, label) VALUES (?,?,?)",
                     (src_id, "task", "créer projet web"))
        conn.execute("INSERT INTO kg_nodes(id, type, label) VALUES (?,?,?)",
                     (dst_id, "step", "initialiser environnement"))
        conn.commit()
        return src_id, dst_id

    def test_insert_edge(self, tmp_path):
        db = _fresh_db(tmp_path)
        conn = _conn(db)
        src, dst = self._insert_two_nodes(conn)
        conn.execute(
            "INSERT INTO kg_edges(src, rel, dst, weight) VALUES (?,?,?,?)",
            (src, "contains", dst, 1.0),
        )
        conn.commit()
        row = conn.execute(
            "SELECT rel, weight FROM kg_edges WHERE src=? AND dst=?", (src, dst)
        ).fetchone()
        assert row == ("contains", 1.0)

    def test_edge_unique_constraint(self, tmp_path):
        """Même triplet (src, rel, dst) → IntegrityError."""
        db = _fresh_db(tmp_path)
        conn = _conn(db)
        src, dst = self._insert_two_nodes(conn)
        conn.execute("INSERT INTO kg_edges(src, rel, dst) VALUES (?,?,?)", (src, "contains", dst))
        conn.commit()
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO kg_edges(src, rel, dst) VALUES (?,?,?)", (src, "contains", dst))
            conn.commit()

    def test_edge_upsert_weight(self, tmp_path):
        """INSERT OR REPLACE met à jour le weight."""
        db = _fresh_db(tmp_path)
        conn = _conn(db)
        src, dst = self._insert_two_nodes(conn)
        conn.execute("INSERT INTO kg_edges(src, rel, dst, weight) VALUES (?,?,?,?)",
                     (src, "contains", dst, 1.0))
        conn.commit()
        conn.execute("""
            INSERT INTO kg_edges(src, rel, dst, weight) VALUES (?,?,?,?)
            ON CONFLICT(src, rel, dst) DO UPDATE SET weight = excluded.weight
        """, (src, "contains", dst, 2.5))
        conn.commit()
        w = conn.execute("SELECT weight FROM kg_edges WHERE src=? AND dst=?", (src, dst)).fetchone()[0]
        assert w == pytest.approx(2.5)

    def test_cascade_delete_node_removes_edges(self, tmp_path):
        """Suppression d'un nœud → ses arêtes sont supprimées."""
        db = _fresh_db(tmp_path)
        conn = _conn(db)
        conn.execute("PRAGMA foreign_keys = ON")
        src, dst = self._insert_two_nodes(conn)
        conn.execute("INSERT INTO kg_edges(src, rel, dst) VALUES (?,?,?)", (src, "contains", dst))
        conn.commit()
        conn.execute("DELETE FROM kg_nodes WHERE id=?", (src,))
        conn.commit()
        edges = conn.execute("SELECT COUNT(*) FROM kg_edges WHERE src=?", (src,)).fetchone()[0]
        assert edges == 0

    def test_relations_valides(self, tmp_path):
        """Les 7 relations du schéma sont insérables."""
        db = _fresh_db(tmp_path)
        conn = _conn(db)
        relations = ["contains", "requires", "precondition", "effect", "causes", "enables", "blocks"]
        nodes = []
        for i, rel in enumerate(relations):
            nid = _node_id(f"concept", f"node_{i}")
            conn.execute("INSERT INTO kg_nodes(id, type, label) VALUES (?,?,?)", (nid, "concept", f"node_{i}"))
            nodes.append(nid)
        conn.commit()
        for i, rel in enumerate(relations[:-1]):
            conn.execute("INSERT INTO kg_edges(src, rel, dst) VALUES (?,?,?)",
                         (nodes[i], rel, nodes[i + 1]))
        conn.commit()
        count = conn.execute("SELECT COUNT(*) FROM kg_edges").fetchone()[0]
        assert count == len(relations) - 1


# ══════════════════════════════════════════════════════════════════════════════
# 5. kg_edge_events — renforcement
# ══════════════════════════════════════════════════════════════════════════════

class TestKgEdgeEvents:

    def test_insert_event_success(self, tmp_path):
        db = _fresh_db(tmp_path)
        conn = _conn(db)
        conn.execute("""
            INSERT INTO kg_edge_events(edge_src, edge_rel, edge_dst, session_id, outcome, delta)
            VALUES (?,?,?,?,?,?)
        """, ("src1", "requires", "dst1", "sess_abc", "success", 0.1))
        conn.commit()
        row = conn.execute(
            "SELECT outcome, delta FROM kg_edge_events WHERE session_id='sess_abc'"
        ).fetchone()
        assert row == ("success", pytest.approx(0.1))

    def test_insert_event_failure(self, tmp_path):
        db = _fresh_db(tmp_path)
        conn = _conn(db)
        conn.execute("""
            INSERT INTO kg_edge_events(edge_src, edge_rel, edge_dst, session_id, outcome, delta)
            VALUES (?,?,?,?,?,?)
        """, ("src1", "requires", "dst1", "sess_xyz", "failure", -0.05))
        conn.commit()
        row = conn.execute(
            "SELECT outcome, delta FROM kg_edge_events WHERE session_id='sess_xyz'"
        ).fetchone()
        assert row[0] == "failure"
        assert row[1] == pytest.approx(-0.05)

    def test_multiple_events_same_edge(self, tmp_path):
        """Plusieurs events sur la même arête sont autorisés."""
        db = _fresh_db(tmp_path)
        conn = _conn(db)
        for i in range(5):
            conn.execute("""
                INSERT INTO kg_edge_events(edge_src, edge_rel, edge_dst, outcome, delta)
                VALUES (?,?,?,?,?)
            """, ("src", "contains", "dst", "success", 0.1))
        conn.commit()
        count = conn.execute(
            "SELECT COUNT(*) FROM kg_edge_events WHERE edge_src='src'"
        ).fetchone()[0]
        assert count == 5

    def test_weight_accumulation_query(self, tmp_path):
        """Requête de weight cumulé depuis les events."""
        db = _fresh_db(tmp_path)
        conn = _conn(db)
        events = [("success", 0.1), ("success", 0.1), ("failure", -0.05), ("skipped", 0.0)]
        for outcome, delta in events:
            conn.execute("""
                INSERT INTO kg_edge_events(edge_src, edge_rel, edge_dst, outcome, delta)
                VALUES (?,?,?,?,?)
            """, ("src", "requires", "dst", outcome, delta))
        conn.commit()
        total = conn.execute(
            "SELECT SUM(delta) FROM kg_edge_events WHERE edge_src='src' AND edge_rel='requires'"
        ).fetchone()[0]
        assert total == pytest.approx(0.15)
