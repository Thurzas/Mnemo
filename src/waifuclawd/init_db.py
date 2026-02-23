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