CREATE TABLE chunks (
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
CREATE TABLE embeddings (
            chunk_id    TEXT PRIMARY KEY REFERENCES chunks(id) ON DELETE CASCADE,
            model       TEXT NOT NULL,
            vector      BLOB NOT NULL,
            dim         INTEGER NOT NULL
        );
CREATE VIRTUAL TABLE chunks_fts USING fts5(
            chunk_id UNINDEXED,
            content,
            section,
            subsection,
            tokenize = "unicode61"
        )
/* chunks_fts(chunk_id,content,section,subsection) */;
CREATE TABLE IF NOT EXISTS 'chunks_fts_data'(id INTEGER PRIMARY KEY, block BLOB);
CREATE TABLE IF NOT EXISTS 'chunks_fts_idx'(segid, term, pgno, PRIMARY KEY(segid, term)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS 'chunks_fts_content'(id INTEGER PRIMARY KEY, c0, c1, c2, c3);
CREATE TABLE IF NOT EXISTS 'chunks_fts_docsize'(id INTEGER PRIMARY KEY, sz BLOB);
CREATE TABLE IF NOT EXISTS 'chunks_fts_config'(k PRIMARY KEY, v) WITHOUT ROWID;
CREATE TABLE sessions (
            id           TEXT PRIMARY KEY,
            date         DATETIME NOT NULL,
            summary      TEXT,
            json_path    TEXT,
            consolidated INTEGER DEFAULT 0
        );
CREATE TABLE session_facts (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id  TEXT REFERENCES sessions(id) ON DELETE CASCADE,
            fact        TEXT NOT NULL,
            category    TEXT,
            persisted   INTEGER DEFAULT 0,
            chunk_id    TEXT REFERENCES chunks(id)
        );
CREATE TABLE sqlite_sequence(name,seq);
CREATE TABLE file_state (
            path        TEXT PRIMARY KEY,
            mtime       REAL NOT NULL,       -- os.stat().st_mtime
            file_hash   TEXT NOT NULL,       -- MD5 du contenu complet du fichier
            synced_at   DATETIME DEFAULT CURRENT_TIMESTAMP
        );
CREATE TABLE documents (
            id          TEXT PRIMARY KEY,    -- MD5 du contenu du fichier
            filename    TEXT NOT NULL,       -- nom original (ex: rapport.pdf)
            path        TEXT NOT NULL,       -- chemin absolu au moment de l'ingestion
            mime_type   TEXT NOT NULL,       -- ex: application/pdf
            page_count  INTEGER,             -- nb de pages (PDF) ou NULL
            chunk_count INTEGER DEFAULT 0,   -- nb de chunks produits
            ingested_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );
CREATE TABLE doc_chunks (
            id               TEXT PRIMARY KEY,
            doc_id           TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
            page             INTEGER,         -- numéro de page source
            chunk_index      INTEGER,         -- position dans le document
            content          TEXT NOT NULL,
            importance_weight REAL DEFAULT 1.0,
            category         TEXT DEFAULT 'connaissance',
            created_at       DATETIME DEFAULT CURRENT_TIMESTAMP
        );
CREATE TABLE doc_embeddings (
            chunk_id    TEXT PRIMARY KEY REFERENCES doc_chunks(id) ON DELETE CASCADE,
            model       TEXT NOT NULL,
            vector      BLOB NOT NULL,
            dim         INTEGER NOT NULL
        );
CREATE VIRTUAL TABLE doc_chunks_fts USING fts5(
            chunk_id UNINDEXED,
            content,
            filename,
            tokenize = "unicode61"
        )
/* doc_chunks_fts(chunk_id,content,filename) */;
CREATE TABLE IF NOT EXISTS 'doc_chunks_fts_data'(id INTEGER PRIMARY KEY, block BLOB);
CREATE TABLE IF NOT EXISTS 'doc_chunks_fts_idx'(segid, term, pgno, PRIMARY KEY(segid, term)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS 'doc_chunks_fts_content'(id INTEGER PRIMARY KEY, c0, c1, c2);
CREATE TABLE IF NOT EXISTS 'doc_chunks_fts_docsize'(id INTEGER PRIMARY KEY, sz BLOB);
CREATE TABLE IF NOT EXISTS 'doc_chunks_fts_config'(k PRIMARY KEY, v) WITHOUT ROWID;
CREATE TABLE curiosity_skipped (
            id          TEXT PRIMARY KEY,   -- hash MD5 de la question normalisée
            question    TEXT NOT NULL,      -- texte original de la question
            skipped_at  DATETIME DEFAULT CURRENT_TIMESTAMP
        );
CREATE TABLE scheduled_tasks (
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
        );
