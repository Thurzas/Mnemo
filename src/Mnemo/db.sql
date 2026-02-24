-- Source de vérité : les chunks extraits du Markdown
CREATE TABLE chunks (
    id          TEXT PRIMARY KEY,  -- hash MD5/SHA du contenu, sert à détecter les changements
    section     TEXT NOT NULL,     -- ex: "Connaissances persistantes"
    subsection  TEXT,              -- ex: "Projets en cours > Système d'agentisation"
    content     TEXT NOT NULL,     -- le texte brut du chunk
    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
    source_line INTEGER            -- numéro de ligne dans le Markdown, utile pour re-sync
);

-- Index vectoriel : un vecteur par chunk
CREATE TABLE embeddings (
    chunk_id    TEXT PRIMARY KEY REFERENCES chunks(id) ON DELETE CASCADE,
    model       TEXT NOT NULL,     -- ex: "text-embedding-3-small", pour pouvoir re-vectoriser si tu changes de modèle
    vector      BLOB NOT NULL,     -- vecteur sérialisé en bytes (numpy → bytes)
    dim         INTEGER NOT NULL   -- dimension du vecteur, ex: 1536
);

-- Index keyword : mots clés extraits par chunk (FTS ou table simple)
CREATE VIRTUAL TABLE chunks_fts USING fts5(
    chunk_id UNINDEXED,
    content,
    section,
    subsection,
    tokenize = "unicode61"  -- gère les accents
);

CREATE TABLE IF NOT EXISTS 'chunks_fts_data'(id INTEGER PRIMARY KEY, block BLOB);
CREATE TABLE IF NOT EXISTS 'chunks_fts_idx'(segid, term, pgno, PRIMARY KEY(segid, term)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS 'chunks_fts_content'(id INTEGER PRIMARY KEY, c0, c1, c2, c3);
CREATE TABLE IF NOT EXISTS 'chunks_fts_docsize'(id INTEGER PRIMARY KEY, sz BLOB);
CREATE TABLE IF NOT EXISTS 'chunks_fts_config'(k PRIMARY KEY, v) WITHOUT ROWID;
-- Sessions : lien entre JSON de session et faits persistés
CREATE TABLE sessions (
    id           TEXT PRIMARY KEY,  -- session_id du JSON
    date         DATETIME NOT NULL,
    summary      TEXT,
    json_path    TEXT,              -- chemin vers le fichier JSON de session
    consolidated INTEGER DEFAULT 0  -- 0 = pas encore fusionné dans le Markdown, 1 = fait
);

-- Faits extraits d'une session, avant consolidation dans le Markdown
CREATE TABLE session_facts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT REFERENCES sessions(id) ON DELETE CASCADE,
    fact        TEXT NOT NULL,
    category    TEXT,               -- "projet", "préférence", "décision", "identité"...
    persisted   INTEGER DEFAULT 0,  -- 0 = en attente, 1 = écrit dans le Markdown
    chunk_id    TEXT REFERENCES chunks(id)  -- rempli après consolidation
);
CREATE TABLE sqlite_sequence(name,seq);
CREATE TABLE file_state (
            path        TEXT PRIMARY KEY,
            mtime       REAL NOT NULL,       -- os.stat().st_mtime
            file_hash   TEXT NOT NULL,       -- MD5 du contenu complet du fichier
            synced_at   DATETIME DEFAULT CURRENT_TIMESTAMP
        );

--**La logique derrière :**
--**`chunks.id` = hash du contenu** — c'est le mécanisme de sync. Au re-scan du Markdown, si le hash d'un `###` n'a pas changé, tu ne re-vectorises pas. Ça évite des appels API inutiles.
--**`embeddings.model`** — quand tu changeras de modèle d'embedding dans 6 mois, tu sais exactement quels vecteurs sont obsolètes et tu peux re-vectoriser sélectivement.
--**`chunks_fts`** — SQLite FTS5 est très puissant nativement, pas besoin d'Elasticsearch. Le `tokenize = unicode61` gère les accents français correctement.
--**`sessions` + `session_facts`** — c'est le pipeline de consolidation. Les faits d'une session attendent dans `session_facts` avec `persisted = 0` jusqu'à ce que l'agent de consolidation les écrive dans le Markdown et mette à jour les chunks correspondants.
---
--**Le flow de recherche hybride ensuite sera :**
--```
--query utilisateur
--    ├── FTS5 sur chunks_fts → résultats keyword
--    ├── vectorisation query → similarité cosine sur embeddings → résultats sémantiques
--    └── merge + rerank des deux listes → top K chunks injectés dans le prompt