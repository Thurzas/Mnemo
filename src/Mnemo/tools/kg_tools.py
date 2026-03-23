"""
kg_tools.py — Graphe de connaissances procédurales hiérarchiques (HP-KG)

Stratégie double-lookup :
  1. KG personnel  : users/<username>/memory.db  (source='user', writable)
  2. KG seed       : src/Mnemo/assets/kg_seed.db  (source='seed', read-only)

Les fonctions de lecture interrogent d'abord le KG personnel, complètent
avec le seed en fallback (pas de doublons). Les fonctions d'écriture
n'écrivent que dans le KG personnel.

Relations du schéma (alignées ConceptNet) :
  contains      (Task)   → (Step)
  requires      (Step)   → (Action)
  precondition  (Action) → (State)   requis avant exécution
  effect        (Action) → (State)   produit après exécution
  causes        (Action) → (Action)  déclenche souvent
  enables       (State)  → (Action)  rend possible
  blocks        (State)  → (Action)  empêche
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Optional

# ── Chemin du seed bundlé avec l'appli ───────────────────────────────────────
SEED_DB_PATH: Path = Path(__file__).parent.parent / "assets" / "kg_seed.db"

# Relations valides du schéma
VALID_RELATIONS = frozenset(
    ["contains", "requires", "precondition", "effect", "causes", "enables", "blocks"]
)


# ══════════════════════════════════════════════════════════════════════════════
# Utilitaires internes
# ══════════════════════════════════════════════════════════════════════════════

def kg_node_id(type_: str, label: str) -> str:
    """Génère un ID déterministe SHA1 pour un nœud (type, label)."""
    return hashlib.sha1(f"{type_}/{label}".encode()).hexdigest()


def _conn(db_path: Path, readonly: bool = False) -> sqlite3.Connection:
    if readonly:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    else:
        conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.row_factory = sqlite3.Row
    return conn


def _seed_conn() -> sqlite3.Connection | None:
    """Ouvre le seed en lecture seule. Retourne None si absent."""
    if not SEED_DB_PATH.exists():
        return None
    try:
        return _conn(SEED_DB_PATH, readonly=True)
    except Exception:
        return None


def _rows_to_dicts(rows) -> list[dict]:
    return [dict(r) for r in rows]


# ══════════════════════════════════════════════════════════════════════════════
# Écriture — KG personnel uniquement
# ══════════════════════════════════════════════════════════════════════════════

def kg_add_node(
    db_path: Path,
    type_: str,
    label: str,
    source: str = "user",
    metadata: dict | None = None,
    lang: str = "fr",
) -> str:
    """
    Insère un nœud dans le KG personnel (idempotent).
    Retourne l'ID du nœud.
    """
    nid = kg_node_id(type_, label)
    meta_str = json.dumps(metadata or {}, ensure_ascii=False)
    conn = _conn(db_path)
    _ensure_kg_tables(conn)
    conn.execute("""
        INSERT INTO kg_nodes(id, type, label, lang, source, metadata)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO NOTHING
    """, (nid, type_, label, lang, source, meta_str))
    conn.commit()
    conn.close()
    return nid


def kg_add_edge(
    db_path: Path,
    src_id: str,
    rel: str,
    dst_id: str,
    weight: float = 1.0,
    source: str = "user",
) -> None:
    """
    Insère une relation dans le KG personnel (idempotent).
    Si elle existe déjà, ne touche pas au weight existant.
    """
    if rel not in VALID_RELATIONS:
        raise ValueError(f"Relation inconnue : {rel!r}. Valides : {sorted(VALID_RELATIONS)}")
    conn = _conn(db_path)
    _ensure_kg_tables(conn)
    conn.execute("""
        INSERT INTO kg_edges(src, rel, dst, weight, source)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(src, rel, dst) DO NOTHING
    """, (src_id, rel, dst_id, weight, source))
    conn.commit()
    conn.close()


def kg_add_triplet(
    db_path: Path,
    src_type: str,
    src_label: str,
    rel: str,
    dst_type: str,
    dst_label: str,
    source: str = "user",
    src_metadata: dict | None = None,
    dst_metadata: dict | None = None,
) -> tuple[str, str]:
    """
    Raccourci : crée les deux nœuds + la relation en une seule opération.
    Retourne (src_id, dst_id).
    """
    src_id = kg_add_node(db_path, src_type, src_label, source=source, metadata=src_metadata)
    dst_id = kg_add_node(db_path, dst_type, dst_label, source=source, metadata=dst_metadata)
    kg_add_edge(db_path, src_id, rel, dst_id, source=source)
    return src_id, dst_id


def kg_reinforce_edge(
    db_path: Path,
    src_id: str,
    rel: str,
    dst_id: str,
    delta: float,
    session_id: str = "",
    outcome: str = "success",
) -> None:
    """
    Renforce (delta > 0) ou affaiblit (delta < 0) le weight d'une relation.
    Enregistre l'événement dans kg_edge_events.
    Le weight ne descend pas en dessous de 0.01.
    """
    conn = _conn(db_path)
    conn.execute("""
        UPDATE kg_edges
        SET weight = MAX(0.01, weight + ?)
        WHERE src=? AND rel=? AND dst=?
    """, (delta, src_id, rel, dst_id))
    conn.execute("""
        INSERT INTO kg_edge_events(edge_src, edge_rel, edge_dst, session_id, outcome, delta)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (src_id, rel, dst_id, session_id, outcome, delta))
    conn.commit()
    conn.close()


def kg_record_event(
    db_path: Path,
    src_id: str,
    rel: str,
    dst_id: str,
    outcome: str,
    session_id: str = "",
    delta: float = 0.0,
) -> None:
    """
    Enregistre un événement sans modifier le weight.
    Utile pour les outcomes 'skipped'.
    """
    conn = _conn(db_path)
    conn.execute("""
        INSERT INTO kg_edge_events(edge_src, edge_rel, edge_dst, session_id, outcome, delta)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (src_id, rel, dst_id, session_id, outcome, delta))
    conn.commit()
    conn.close()


# ══════════════════════════════════════════════════════════════════════════════
# Lecture — double-lookup (user d'abord, seed en fallback)
# ══════════════════════════════════════════════════════════════════════════════

_KG_DDL = """
CREATE TABLE IF NOT EXISTS kg_nodes (
    id       TEXT PRIMARY KEY,
    type     TEXT NOT NULL,
    label    TEXT NOT NULL,
    lang     TEXT NOT NULL DEFAULT 'fr',
    source   TEXT NOT NULL DEFAULT 'user',
    metadata TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS kg_edges (
    src     TEXT NOT NULL REFERENCES kg_nodes(id) ON DELETE CASCADE,
    rel     TEXT NOT NULL,
    dst     TEXT NOT NULL REFERENCES kg_nodes(id) ON DELETE CASCADE,
    weight  REAL NOT NULL DEFAULT 1.0,
    source  TEXT NOT NULL DEFAULT 'user',
    PRIMARY KEY (src, rel, dst)
);
CREATE INDEX IF NOT EXISTS kg_nodes_type  ON kg_nodes(type);
CREATE INDEX IF NOT EXISTS kg_nodes_label ON kg_nodes(label);
CREATE INDEX IF NOT EXISTS kg_edges_src   ON kg_edges(src);
CREATE INDEX IF NOT EXISTS kg_edges_dst   ON kg_edges(dst);
CREATE INDEX IF NOT EXISTS kg_edges_rel   ON kg_edges(rel);
"""


def _ensure_kg_tables(conn: sqlite3.Connection) -> None:
    """Crée les tables KG si elles n'existent pas (lazy migration idempotente)."""
    conn.executescript(_KG_DDL)
    conn.commit()


def _query_edges(
    conn: sqlite3.Connection,
    src_id: str | None = None,
    rel: str | None = None,
    dst_id: str | None = None,
) -> list[dict]:
    """
    Requête générique sur kg_edges + kg_nodes (src + dst).
    Filtre optionnel sur src_id, rel, dst_id.
    Crée les tables si elles sont absentes (migration transparente).
    """
    where = []
    params = []
    if src_id:
        where.append("e.src = ?")
        params.append(src_id)
    if rel:
        where.append("e.rel = ?")
        params.append(rel)
    if dst_id:
        where.append("e.dst = ?")
        params.append(dst_id)

    where_clause = ("WHERE " + " AND ".join(where)) if where else ""
    sql = f"""
        SELECT
            e.src, e.rel, e.dst, e.weight, e.source,
            ns.type  AS src_type,  ns.label AS src_label,
            nd.type  AS dst_type,  nd.label AS dst_label
        FROM kg_edges e
        JOIN kg_nodes ns ON ns.id = e.src
        JOIN kg_nodes nd ON nd.id = e.dst
        {where_clause}
        ORDER BY e.weight DESC
    """
    try:
        return _rows_to_dicts(conn.execute(sql, params).fetchall())
    except sqlite3.OperationalError as e:
        if "no such table" in str(e):
            _ensure_kg_tables(conn)
            return _rows_to_dicts(conn.execute(sql, params).fetchall())
        raise


def _merge_results(user_rows: list[dict], seed_rows: list[dict]) -> list[dict]:
    """
    Fusionne user + seed sans doublons.
    Un doublon = même (src_label, rel, dst_label).
    Les entrées user ont priorité (weight personnel).
    """
    seen = {(r["src_label"], r["rel"], r["dst_label"]) for r in user_rows}
    extra = [r for r in seed_rows if (r["src_label"], r["rel"], r["dst_label"]) not in seen]
    return user_rows + extra


def kg_query(
    db_path: Path,
    src_id: str | None = None,
    rel: str | None = None,
    dst_id: str | None = None,
) -> list[dict]:
    """
    Requête générique double-lookup.
    Retourne les relations matchant les filtres, user d'abord puis seed.
    """
    conn = _conn(db_path)
    user_rows = _query_edges(conn, src_id=src_id, rel=rel, dst_id=dst_id)
    conn.close()

    seed = _seed_conn()
    if seed is None:
        return user_rows
    seed_rows = _query_edges(seed, src_id=src_id, rel=rel, dst_id=dst_id)
    seed.close()

    return _merge_results(user_rows, seed_rows)


def kg_steps_for_task(db_path: Path, task_label: str) -> list[dict]:
    """
    Retourne les étapes d'une tâche : (Task)-[contains]->(Step).
    Double-lookup user + seed.
    """
    task_id = kg_node_id("task", task_label)
    return kg_query(db_path, src_id=task_id, rel="contains")


def kg_actions_for_step(db_path: Path, step_label: str) -> list[dict]:
    """
    Retourne les actions requises par un step : (Step)-[requires]->(Action).
    Double-lookup user + seed.
    """
    step_id = kg_node_id("step", step_label)
    return kg_query(db_path, src_id=step_id, rel="requires")


def kg_preconditions_for_action(db_path: Path, action_label: str) -> list[str]:
    """
    Retourne les états requis avant une action : (Action)-[precondition]->(State).
    Retourne les labels des états (clés world_state).
    """
    action_id = kg_node_id("action", action_label)
    rows = kg_query(db_path, src_id=action_id, rel="precondition")
    return [r["dst_label"] for r in rows]


def kg_effects_for_action(db_path: Path, action_label: str) -> list[str]:
    """
    Retourne les états produits par une action : (Action)-[effect]->(State).
    Retourne les labels des états (clés world_state).
    """
    action_id = kg_node_id("action", action_label)
    rows = kg_query(db_path, src_id=action_id, rel="effect")
    return [r["dst_label"] for r in rows]


def kg_blocking_states(db_path: Path, action_label: str) -> list[str]:
    """
    Retourne les états qui bloquent une action : (State)-[blocks]->(Action).
    """
    action_id = kg_node_id("action", action_label)
    rows = kg_query(db_path, dst_id=action_id, rel="blocks")
    return [r["src_label"] for r in rows]


def kg_causes(db_path: Path, action_label: str) -> list[str]:
    """
    Retourne les actions souvent déclenchées après : (Action)-[causes]->(Action).
    """
    action_id = kg_node_id("action", action_label)
    rows = kg_query(db_path, src_id=action_id, rel="causes")
    return [r["dst_label"] for r in rows]


def kg_get_node(db_path: Path, type_: str, label: str) -> dict | None:
    """
    Retourne un nœud par (type, label). Double-lookup user + seed.
    """
    nid = kg_node_id(type_, label)

    conn = _conn(db_path)
    row = conn.execute("SELECT * FROM kg_nodes WHERE id=?", (nid,)).fetchone()
    conn.close()
    if row:
        return dict(row)

    seed = _seed_conn()
    if seed is None:
        return None
    row = seed.execute("SELECT * FROM kg_nodes WHERE id=?", (nid,)).fetchone()
    seed.close()
    return dict(row) if row else None


def kg_search_nodes(
    db_path: Path,
    type_: str | None = None,
    label_contains: str | None = None,
) -> list[dict]:
    """
    Recherche des nœuds par type et/ou fragment de label.
    Double-lookup user + seed.
    """
    def _search(conn: sqlite3.Connection) -> list[dict]:
        where, params = [], []
        if type_:
            where.append("type = ?")
            params.append(type_)
        if label_contains:
            where.append("label LIKE ?")
            params.append(f"%{label_contains}%")
        clause = ("WHERE " + " AND ".join(where)) if where else ""
        return _rows_to_dicts(conn.execute(f"SELECT * FROM kg_nodes {clause}", params).fetchall())

    conn = _conn(db_path)
    user_rows = _search(conn)
    conn.close()

    seed = _seed_conn()
    if seed is None:
        return user_rows
    seed_rows = _search(seed)
    seed.close()

    seen = {r["id"] for r in user_rows}
    return user_rows + [r for r in seed_rows if r["id"] not in seen]