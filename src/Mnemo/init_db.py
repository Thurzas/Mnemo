"""
Lance ce script une seule fois pour initialiser la base SQLite.
    python init_db.py
"""
import sqlite3
from pathlib import Path

DB_PATH = Path("memory.db")


def init_db():
    db = sqlite3.connect(DB_PATH)
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
            category         TEXT DEFAULT 'connaissance'
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

        -- ── CuriosityCrew — questions skippées ────────────────────────
        CREATE TABLE IF NOT EXISTS curiosity_skipped (
            id          TEXT PRIMARY KEY,
            question    TEXT NOT NULL,
            skipped_at  DATETIME DEFAULT CURRENT_TIMESTAMP
        );

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
    print(f"✅ Base initialisée : {DB_PATH}")


def migrate_db():
    """
    Ajoute les colonnes manquantes sur une DB existante.
    Sûr à relancer plusieurs fois — ignore les colonnes déjà présentes.
    """
    db = sqlite3.connect(DB_PATH)
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


if __name__ == "__main__":
    init_db()
    migrate_db()