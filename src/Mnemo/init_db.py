"""
Lance ce script une seule fois pour initialiser la base SQLite.
    python init_db.py

Peut aussi être appelé programmatiquement avec un chemin explicite :
    from Mnemo.init_db import init_db, migrate_db
    init_db(db_path=Path("/data/users/alice/memory.db"))
"""
import sqlite3
from pathlib import Path

DB_PATH = Path("memory.db")


def init_db(db_path: Path = None):
    if db_path is None:
        db_path = DB_PATH
    db = sqlite3.connect(db_path)
    db.executescript("""
        CREATE TABLE IF NOT EXISTS chunks (
            id               TEXT PRIMARY KEY,
            section          TEXT NOT NULL,
            subsection       TEXT,
            content          TEXT NOT NULL,
            created_at       DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at       DATETIME DEFAULT CURRENT_TIMESTAMP,
            source_line      INTEGER,
            importance_weight REAL DEFAULT 1.0,
            category         TEXT DEFAULT 'connaissance',
            use_count        INTEGER  DEFAULT 0,
            last_used_at     DATETIME
        );

        CREATE TABLE IF NOT EXISTS embeddings (
            chunk_id    TEXT PRIMARY KEY REFERENCES chunks(id) ON DELETE CASCADE,
            model       TEXT NOT NULL,
            vector      BLOB NOT NULL,
            dim         INTEGER NOT NULL
        );

        CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
            chunk_id UNINDEXED,
            content,
            section,
            subsection,
            tokenize = "unicode61"
        );

        CREATE TABLE IF NOT EXISTS sessions (
            id           TEXT PRIMARY KEY,
            date         DATETIME NOT NULL,
            summary      TEXT,
            json_path    TEXT,
            consolidated INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS session_facts (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id  TEXT REFERENCES sessions(id) ON DELETE CASCADE,
            fact        TEXT NOT NULL,
            category    TEXT,
            persisted   INTEGER DEFAULT 0,
            chunk_id    TEXT REFERENCES chunks(id)
        );

        -- Pondération des chunks
        -- importance_weight : poids statique selon la catégorie du fait
        -- category : catégorie sémantique du fait (identité, projet, décision...)
        -- Ces colonnes sont optionnelles sur les chunks parsés manuellement depuis le Markdown
        -- elles sont remplies automatiquement quand le memory_writer écrit depuis une session
        -- Suivi de l'état du fichier memory.md
        -- Permet de détecter les éditions manuelles et les désynchronisations DB
        CREATE TABLE IF NOT EXISTS file_state (
            path        TEXT PRIMARY KEY,
            mtime       REAL NOT NULL,       -- os.stat().st_mtime
            file_hash   TEXT NOT NULL,       -- MD5 du contenu complet du fichier
            synced_at   DATETIME DEFAULT CURRENT_TIMESTAMP
        );

        -- ── Phase 2 : Ingestion de documents externes ─────────────────
        -- Catalogue des fichiers ingérés (PDF, DOCX...)
        -- Permet d'éviter la double ingestion si le fichier n'a pas changé
        CREATE TABLE IF NOT EXISTS documents (
            id          TEXT PRIMARY KEY,    -- MD5 du contenu du fichier
            filename    TEXT NOT NULL,       -- nom original (ex: rapport.pdf)
            path        TEXT NOT NULL,       -- chemin absolu au moment de l'ingestion
            mime_type   TEXT NOT NULL,       -- ex: application/pdf
            page_count  INTEGER,             -- nb de pages (PDF) ou NULL
            chunk_count INTEGER DEFAULT 0,   -- nb de chunks produits
            ingested_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );

        -- Chunks issus des documents (séparés de la mémoire personnelle)
        -- Participent au retrieval hybride comme les chunks normaux
        CREATE TABLE IF NOT EXISTS doc_chunks (
            id               TEXT PRIMARY KEY,
            doc_id           TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
            page             INTEGER,         -- numéro de page source
            chunk_index      INTEGER,         -- position dans le document
            content          TEXT NOT NULL,
            importance_weight REAL DEFAULT 1.0,
            category         TEXT DEFAULT 'connaissance',
            created_at       DATETIME DEFAULT CURRENT_TIMESTAMP
        );

        -- Embeddings des doc_chunks (même structure que pour les chunks mémoire)
        CREATE TABLE IF NOT EXISTS doc_embeddings (
            chunk_id    TEXT PRIMARY KEY REFERENCES doc_chunks(id) ON DELETE CASCADE,
            model       TEXT NOT NULL,
            vector      BLOB NOT NULL,
            dim         INTEGER NOT NULL
        );

        -- Index FTS5 pour la recherche keyword dans les documents
        CREATE VIRTUAL TABLE IF NOT EXISTS doc_chunks_fts USING fts5(
            chunk_id UNINDEXED,
            content,
            filename,
            tokenize = "unicode61"
        );

        -- ── Phase 5.3 : Mémoire procédurale — tracking d'usage des chunks ──
        -- used_score : similarité cosinus réponse/chunk (0.0–1.0)
        -- confirmed  : 1 si used_score > USAGE_THRESHOLD (défaut 0.60)
        CREATE TABLE IF NOT EXISTS chunk_usage (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            chunk_id     TEXT     REFERENCES chunks(id) ON DELETE CASCADE,
            session_id   TEXT,
            retrieved_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            used_score   REAL,
            confirmed    INTEGER  DEFAULT 0,
            profile      TEXT     DEFAULT 'conversation'
        );

        -- ── CuriosityCrew — questions skippées ────────────────────────
        CREATE TABLE IF NOT EXISTS curiosity_skipped (
            id          TEXT PRIMARY KEY,
            question    TEXT NOT NULL,
            skipped_at  DATETIME DEFAULT CURRENT_TIMESTAMP
        );

        -- ── Phase 7 — HP-KG : Graphe de connaissances procédurales ──────
        -- Deux couches : seed (read-only, livré avec l'appli) + user (per-user, writable)
        -- Relations alignées ConceptNet : contains, requires, precondition, effect, causes, enables, blocks
        CREATE TABLE IF NOT EXISTS kg_nodes (
            id       TEXT PRIMARY KEY,        -- SHA1(type || "/" || label)
            type     TEXT NOT NULL,           -- task | step | action | state | concept
            label    TEXT NOT NULL,
            lang     TEXT DEFAULT 'fr',       -- fr | en (import ConceptNet futur)
            source   TEXT DEFAULT 'user',     -- user | seed | conceptnet
            metadata TEXT DEFAULT '{}'        -- JSON libre (coût GOAP, domaine, notes)
        );

        CREATE TABLE IF NOT EXISTS kg_edges (
            id      INTEGER PRIMARY KEY AUTOINCREMENT,
            src     TEXT NOT NULL REFERENCES kg_nodes(id) ON DELETE CASCADE,
            rel     TEXT NOT NULL,            -- contains | requires | precondition | effect | causes | enables | blocks
            dst     TEXT NOT NULL REFERENCES kg_nodes(id) ON DELETE CASCADE,
            weight  REAL DEFAULT 1.0,         -- renforcé à chaque succès
            source  TEXT DEFAULT 'user',      -- user | seed | conceptnet
            UNIQUE(src, rel, dst)
        );

        -- Historique de renforcement pour weight learning
        CREATE TABLE IF NOT EXISTS kg_edge_events (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            edge_src   TEXT NOT NULL,
            edge_rel   TEXT NOT NULL,
            edge_dst   TEXT NOT NULL,
            session_id TEXT,
            outcome    TEXT NOT NULL,         -- success | failure | skipped
            delta      REAL DEFAULT 0.0,      -- variation de weight appliquée
            ts         DATETIME DEFAULT CURRENT_TIMESTAMP
        );

        CREATE INDEX IF NOT EXISTS kg_nodes_type  ON kg_nodes(type);
        CREATE INDEX IF NOT EXISTS kg_nodes_label ON kg_nodes(label);
        CREATE INDEX IF NOT EXISTS kg_edges_src   ON kg_edges(src);
        CREATE INDEX IF NOT EXISTS kg_edges_dst   ON kg_edges(dst);
        CREATE INDEX IF NOT EXISTS kg_edges_rel   ON kg_edges(rel);

        -- ── Scheduler — tâches planifiées ────────────────────────────
        -- one_shot   : exécution unique à trigger_at
        -- recurring  : exécution répétée selon cron_expr
        -- system     : tâches internes (briefing, weekly, deadline_scan)
        CREATE TABLE IF NOT EXISTS scheduled_tasks (
            id          TEXT PRIMARY KEY,         -- hash court
            type        TEXT NOT NULL,            -- one_shot | recurring | system
            action      TEXT NOT NULL,            -- reminder | summary | deadline_alert | weekly | briefing
            payload     TEXT DEFAULT '{}',        -- JSON libre : message, cible, paramètres
            trigger_at  DATETIME,                 -- one_shot : datetime ISO d'exécution
            cron_expr   TEXT,                     -- recurring/system : "lundi 08:00" ou "daily 07:30"
            status      TEXT DEFAULT 'pending',   -- pending | done | cancelled | error
            created_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
            last_run    DATETIME,
            next_run    DATETIME,                 -- précalculé par le scheduler
            error_msg   TEXT                      -- dernier message d'erreur si status=error
        );
    """)
    db.commit()
    db.close()
    print(f"✅ Base initialisée : {db_path}")


def migrate_db(db_path: Path = None):
    """
    Ajoute les colonnes manquantes sur une DB existante.
    Sûr à relancer plusieurs fois — ignore les colonnes déjà présentes.
    """
    if db_path is None:
        db_path = DB_PATH
    db = sqlite3.connect(db_path)
    migrations = [
        "ALTER TABLE chunks ADD COLUMN importance_weight REAL DEFAULT 1.0",
        "ALTER TABLE chunks ADD COLUMN category TEXT DEFAULT 'connaissance'",
        """CREATE TABLE IF NOT EXISTS file_state (
            path        TEXT PRIMARY KEY,
            mtime       REAL NOT NULL,
            file_hash   TEXT NOT NULL,
            synced_at   DATETIME DEFAULT CURRENT_TIMESTAMP
        )""",
        # Phase 2 — tables documents
        """CREATE TABLE IF NOT EXISTS documents (
            id          TEXT PRIMARY KEY,
            filename    TEXT NOT NULL,
            path        TEXT NOT NULL,
            mime_type   TEXT NOT NULL,
            page_count  INTEGER,
            chunk_count INTEGER DEFAULT 0,
            ingested_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )""",
        """CREATE TABLE IF NOT EXISTS doc_chunks (
            id                TEXT PRIMARY KEY,
            doc_id            TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
            page              INTEGER,
            chunk_index       INTEGER,
            content           TEXT NOT NULL,
            importance_weight REAL DEFAULT 1.0,
            category          TEXT DEFAULT 'connaissance',
            created_at        DATETIME DEFAULT CURRENT_TIMESTAMP
        )""",
        """CREATE TABLE IF NOT EXISTS doc_embeddings (
            chunk_id    TEXT PRIMARY KEY REFERENCES doc_chunks(id) ON DELETE CASCADE,
            model       TEXT NOT NULL,
            vector      BLOB NOT NULL,
            dim         INTEGER NOT NULL
        )""",
        """CREATE VIRTUAL TABLE IF NOT EXISTS doc_chunks_fts USING fts5(
            chunk_id UNINDEXED,
            content,
            filename,
            tokenize = 'unicode61'
        )""",
        # CuriosityCrew
        """CREATE TABLE IF NOT EXISTS curiosity_skipped (
            id          TEXT PRIMARY KEY,
            question    TEXT NOT NULL,
            skipped_at  DATETIME DEFAULT CURRENT_TIMESTAMP
        )""",
        # Phase 5.3 — mémoire procédurale
        "ALTER TABLE chunks ADD COLUMN use_count    INTEGER  DEFAULT 0",
        "ALTER TABLE chunks ADD COLUMN last_used_at DATETIME",
        """CREATE TABLE IF NOT EXISTS chunk_usage (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            chunk_id     TEXT     REFERENCES chunks(id) ON DELETE CASCADE,
            session_id   TEXT,
            retrieved_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            used_score   REAL,
            confirmed    INTEGER  DEFAULT 0,
            profile      TEXT     DEFAULT 'conversation'
        )""",
        # Phase 5.5 — profil par retrieval
        "ALTER TABLE chunk_usage ADD COLUMN profile TEXT DEFAULT 'conversation'",
        # Phase 7 — HP-KG
        """CREATE TABLE IF NOT EXISTS kg_nodes (
            id       TEXT PRIMARY KEY,
            type     TEXT NOT NULL,
            label    TEXT NOT NULL,
            lang     TEXT DEFAULT 'fr',
            source   TEXT DEFAULT 'user',
            metadata TEXT DEFAULT '{}'
        )""",
        """CREATE TABLE IF NOT EXISTS kg_edges (
            id      INTEGER PRIMARY KEY AUTOINCREMENT,
            src     TEXT NOT NULL REFERENCES kg_nodes(id) ON DELETE CASCADE,
            rel     TEXT NOT NULL,
            dst     TEXT NOT NULL REFERENCES kg_nodes(id) ON DELETE CASCADE,
            weight  REAL DEFAULT 1.0,
            source  TEXT DEFAULT 'user',
            UNIQUE(src, rel, dst)
        )""",
        """CREATE TABLE IF NOT EXISTS kg_edge_events (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            edge_src   TEXT NOT NULL,
            edge_rel   TEXT NOT NULL,
            edge_dst   TEXT NOT NULL,
            session_id TEXT,
            outcome    TEXT NOT NULL,
            delta      REAL DEFAULT 0.0,
            ts         DATETIME DEFAULT CURRENT_TIMESTAMP
        )""",
        "CREATE INDEX IF NOT EXISTS kg_nodes_type  ON kg_nodes(type)",
        "CREATE INDEX IF NOT EXISTS kg_nodes_label ON kg_nodes(label)",
        "CREATE INDEX IF NOT EXISTS kg_edges_src   ON kg_edges(src)",
        "CREATE INDEX IF NOT EXISTS kg_edges_dst   ON kg_edges(dst)",
        "CREATE INDEX IF NOT EXISTS kg_edges_rel   ON kg_edges(rel)",
        # Scheduler
        """CREATE TABLE IF NOT EXISTS scheduled_tasks (
            id          TEXT PRIMARY KEY,
            type        TEXT NOT NULL,
            action      TEXT NOT NULL,
            payload     TEXT DEFAULT '{}',
            trigger_at  DATETIME,
            cron_expr   TEXT,
            status      TEXT DEFAULT 'pending',
            created_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
            last_run    DATETIME,
            next_run    DATETIME,
            error_msg   TEXT
        )""",
    ]
    for sql in migrations:
        try:
            db.execute(sql)
        except sqlite3.OperationalError:
            pass  # Colonne ou table déjà existante — on ignore
    db.commit()
    db.close()
    print("✅ Migration terminée.")


def init_kg_db(db_path: Path) -> None:
    """
    Initialise une DB minimale contenant uniquement les tables HP-KG.
    Utilisé pour kg_seed.db — pas besoin du schéma complet (chunks, sessions...).
    """
    db = sqlite3.connect(db_path)
    db.executescript("""
        CREATE TABLE IF NOT EXISTS kg_nodes (
            id       TEXT PRIMARY KEY,
            type     TEXT NOT NULL,
            label    TEXT NOT NULL,
            lang     TEXT DEFAULT 'fr',
            source   TEXT DEFAULT 'user',
            metadata TEXT DEFAULT '{}'
        );

        CREATE TABLE IF NOT EXISTS kg_edges (
            id      INTEGER PRIMARY KEY AUTOINCREMENT,
            src     TEXT NOT NULL REFERENCES kg_nodes(id) ON DELETE CASCADE,
            rel     TEXT NOT NULL,
            dst     TEXT NOT NULL REFERENCES kg_nodes(id) ON DELETE CASCADE,
            weight  REAL DEFAULT 1.0,
            source  TEXT DEFAULT 'user',
            UNIQUE(src, rel, dst)
        );

        CREATE TABLE IF NOT EXISTS kg_edge_events (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            edge_src   TEXT NOT NULL,
            edge_rel   TEXT NOT NULL,
            edge_dst   TEXT NOT NULL,
            session_id TEXT,
            outcome    TEXT NOT NULL,
            delta      REAL DEFAULT 0.0,
            ts         DATETIME DEFAULT CURRENT_TIMESTAMP
        );

        CREATE INDEX IF NOT EXISTS kg_nodes_type  ON kg_nodes(type);
        CREATE INDEX IF NOT EXISTS kg_nodes_label ON kg_nodes(label);
        CREATE INDEX IF NOT EXISTS kg_edges_src   ON kg_edges(src);
        CREATE INDEX IF NOT EXISTS kg_edges_dst   ON kg_edges(dst);
        CREATE INDEX IF NOT EXISTS kg_edges_rel   ON kg_edges(rel);
    """)
    db.commit()
    db.close()


if __name__ == "__main__":
    init_db()
    migrate_db()