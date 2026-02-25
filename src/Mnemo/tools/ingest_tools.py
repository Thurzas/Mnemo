"""
Phase 2 — Ingestion de documents externes.
Gère l'extraction, le chunking et l'indexation de fichiers PDF dans SQLite.

Séparation volontaire de memory_tools.py :
  - memory_tools → mémoire personnelle (memory.md + chunks)
  - ingest_tools  → documents de référence (doc_chunks)

Les doc_chunks participent au retrieval hybride global via retrieve_all().
"""
import hashlib
import sqlite3
import re

from pathlib import Path
from typing import Iterator

import numpy as np

# ── Dépendances optionnelles — installées via uv add pypdf ──
try:
    from pypdf import PdfReader
    HAS_PYPDF = True
except ImportError:
    HAS_PYPDF = False

from Mnemo.tools.memory_tools import (
    DB_PATH,
    EMBED_MODEL,
    CATEGORY_WEIGHTS,
    embed,
    cosine_similarity,
    compute_hash,
    sanitize_str,
)

# ── Config ────────────────────────────────────────────────────
CHUNK_SIZE     = 400   # Mots par chunk (PDF → texte brut)
CHUNK_OVERLAP  = 50    # Mots de chevauchement entre chunks
MIN_CHUNK_LEN  = 80    # Caractères minimum pour qu'un chunk soit indexé


# ══════════════════════════════════════════════════════════════
# Extraction PDF
# ══════════════════════════════════════════════════════════════

def extract_pdf_pages(path: Path) -> list[dict]:
    """
    Extrait le texte de chaque page d'un PDF.
    Retourne une liste de dicts {page: int, text: str}.
    Lève ImportError si pypdf n'est pas installé.
    Lève ValueError si le fichier n'est pas un PDF lisible.
    """
    if not HAS_PYPDF:
        raise ImportError(
            "pypdf n'est pas installé. Lance : uv add pypdf"
        )
    if not path.exists():
        raise FileNotFoundError(f"Fichier introuvable : {path}")
    if path.suffix.lower() != ".pdf":
        raise ValueError(f"Format non supporté : {path.suffix} (attendu : .pdf)")

    reader = PdfReader(str(path))
    pages  = []
    for i, page in enumerate(reader.pages, start=1):
        text = page.extract_text() or ""
        text = sanitize_str(text.strip())
        if text:
            pages.append({"page": i, "text": text})
    return pages


def get_pdf_page_count(path: Path) -> int:
    """Retourne le nombre de pages d'un PDF sans extraire le texte."""
    if not HAS_PYPDF:
        raise ImportError("pypdf n'est pas installé. Lance : uv add pypdf")
    return len(PdfReader(str(path)).pages)


# ══════════════════════════════════════════════════════════════
# Chunking
# ══════════════════════════════════════════════════════════════

def clean_text(text: str) -> str:
    """
    Nettoie le texte extrait d'un PDF :
    - Supprime les lignes vides multiples
    - Normalise les espaces
    - Supprime les artefacts courants (numéros de page seuls, tirets de césure)
    """
    # Joins les mots coupés en fin de ligne (trait d'union + saut de ligne)
    text = re.sub(r"-\n", "", text)
    # Normalise les sauts de ligne multiples
    text = re.sub(r"\n{3,}", "\n\n", text)
    # Normalise les espaces multiples
    text = re.sub(r" {2,}", " ", text)
    return text.strip()


def chunk_text(text: str, chunk_size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> Iterator[str]:
    """
    Découpe un texte en chunks de `chunk_size` mots avec `overlap` mots
    de chevauchement entre chunks consécutifs.
    Préserve les frontières de phrases quand c'est possible.
    """
    words = text.split()
    if not words:
        return

    start = 0
    while start < len(words):
        end   = min(start + chunk_size, len(words))
        chunk = " ".join(words[start:end])
        if len(chunk) >= MIN_CHUNK_LEN:
            yield chunk
        start += chunk_size - overlap
        if start >= len(words):
            break


def chunk_pages(pages: list[dict]) -> list[dict]:
    """
    Découpe les pages extraites en chunks indexables.
    Retourne une liste de dicts :
    {page: int, chunk_index: int, content: str}
    """
    chunks      = []
    chunk_index = 0
    for page_data in pages:
        text = clean_text(page_data["text"])
        for chunk in chunk_text(text):
            chunks.append({
                "page":        page_data["page"],
                "chunk_index": chunk_index,
                "content":     chunk,
            })
            chunk_index += 1
    return chunks


# ══════════════════════════════════════════════════════════════
# Gestion des documents en DB
# ══════════════════════════════════════════════════════════════

def file_hash(path: Path) -> str:
    """MD5 du contenu du fichier — sert d'ID unique pour le document."""
    return hashlib.md5(path.read_bytes()).hexdigest()


def is_already_ingested(db: sqlite3.Connection, doc_id: str) -> bool:
    """Retourne True si ce document (même hash) a déjà été ingéré."""
    return db.execute(
        "SELECT 1 FROM documents WHERE id = ?", (doc_id,)
    ).fetchone() is not None


def register_document(
    db: sqlite3.Connection,
    doc_id: str,
    path: Path,
    page_count: int,
    chunk_count: int,
) -> None:
    """Enregistre le document dans la table documents."""
    db.execute("""
        INSERT INTO documents (id, filename, path, mime_type, page_count, chunk_count)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (doc_id, path.name, str(path.resolve()), "application/pdf", page_count, chunk_count))


def upsert_doc_chunk(
    db: sqlite3.Connection,
    doc_id: str,
    chunk: dict,
    filename: str,
) -> None:
    """
    Insère un chunk de document dans doc_chunks + doc_embeddings + doc_chunks_fts.
    Le chunk_id est le MD5 de (doc_id + page + chunk_index + contenu) —
    garantit l'unicité même si deux passages du document ont le même texte.
    """
    # BUG CORRIGÉ : hash sur contenu seul → collision si même texte apparaît
    # deux fois dans le document (ex: headers répétés, formules identiques)
    chunk_id = compute_hash(f"{doc_id}:{chunk['page']}:{chunk['chunk_index']}:{chunk['content']}")

    # Idempotence — on ne réinsère pas un chunk identique
    if db.execute("SELECT 1 FROM doc_chunks WHERE id = ?", (chunk_id,)).fetchone():
        return

    db.execute("""
        INSERT INTO doc_chunks
            (id, doc_id, page, chunk_index, content, importance_weight, category)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (
        chunk_id, doc_id,
        chunk["page"], chunk["chunk_index"],
        chunk["content"],
        CATEGORY_WEIGHTS.get("connaissance", 1.0),
        "connaissance",
    ))

    # Embedding
    vector = embed(chunk["content"])
    db.execute("""
        INSERT OR REPLACE INTO doc_embeddings (chunk_id, model, vector, dim)
        VALUES (?, ?, ?, ?)
    """, (chunk_id, EMBED_MODEL, vector.tobytes(), len(vector)))

    # Index FTS5
    db.execute("""
        INSERT INTO doc_chunks_fts (chunk_id, content, filename)
        VALUES (?, ?, ?)
    """, (chunk_id, chunk["content"], filename))


# ══════════════════════════════════════════════════════════════
# Pipeline principal
# ══════════════════════════════════════════════════════════════

def ingest_pdf(path: Path, db_path: Path = DB_PATH) -> dict:
    """
    Pipeline complet d'ingestion d'un PDF :
    1. Vérifie que le fichier n'est pas déjà ingéré (par hash)
    2. Extrait le texte page par page
    3. Découpe en chunks
    4. Génère les embeddings et indexe en DB

    Retourne un dict de résumé :
    {
        "status":      "ingested" | "already_ingested" | "empty",
        "doc_id":      str,
        "filename":    str,
        "pages":       int,
        "chunks":      int,
    }
    """
    db     = sqlite3.connect(str(db_path))
    doc_id = file_hash(path)

    # ── Déjà ingéré ? ─────────────────────────────────────────
    if is_already_ingested(db, doc_id):
        db.close()
        return {
            "status":   "already_ingested",
            "doc_id":   doc_id,
            "filename": path.name,
            "pages":    0,
            "chunks":   0,
        }

    # ── Extraction ────────────────────────────────────────────
    pages      = extract_pdf_pages(path)
    page_count = get_pdf_page_count(path)

    if not pages:
        db.close()
        return {
            "status":   "empty",
            "doc_id":   doc_id,
            "filename": path.name,
            "pages":    page_count,
            "chunks":   0,
        }

    # ── Chunking ──────────────────────────────────────────────
    chunks = chunk_pages(pages)

    # ── Indexation ────────────────────────────────────────────
    total = len(chunks)
    for i, chunk in enumerate(chunks, start=1):
        upsert_doc_chunk(db, doc_id, chunk, path.name)
        # Progression toutes les 10 chunks ou sur le dernier
        if i % 10 == 0 or i == total:
            pct = int(i / total * 100)
            bar = "█" * (pct // 5) + "░" * (20 - pct // 5)
            print(f"\r   [{bar}] {pct:3d}%  chunk {i}/{total}", end="", flush=True)

    print()  # Saut de ligne final

    register_document(db, doc_id, path, page_count, len(chunks))
    db.commit()
    db.close()

    return {
        "status":   "ingested",
        "doc_id":   doc_id,
        "filename": path.name,
        "pages":    page_count,
        "chunks":   len(chunks),
    }


# ══════════════════════════════════════════════════════════════
# Retrieval dans les documents (utilisé par retrieve_all)
# ══════════════════════════════════════════════════════════════

def search_docs_keyword(db: sqlite3.Connection, query: str, top_k: int = 10) -> list[dict]:
    """Recherche FTS5 dans les doc_chunks."""
    if not query or not query.strip():
        return []
    rows = db.execute("""
        SELECT dc.id, dc.content, dc.page, dc.chunk_index,
               d.filename, bm25(doc_chunks_fts) as score,
               dc.importance_weight, dc.category
        FROM doc_chunks_fts
        JOIN doc_chunks dc  ON doc_chunks_fts.chunk_id = dc.id
        JOIN documents  d   ON dc.doc_id = d.id
        WHERE doc_chunks_fts MATCH ?
        ORDER BY score
        LIMIT ?
    """, (query.strip(), top_k)).fetchall()
    return [{
        "id":               r[0],
        "content":          r[1],
        "page":             r[2],
        "chunk_index":      r[3],
        "source":           r[4],   # filename — affiché dans le prompt
        "score_fts":        abs(r[5]),
        "importance_weight": r[6],
        "category":         r[7],
        "type":             "document",
    } for r in rows]


def search_docs_vector(db: sqlite3.Connection, query: str, top_k: int = 10) -> list[dict]:
    """Recherche vectorielle dans les doc_chunks."""
    query_vec = embed(query, prefix="search_query")
    rows = db.execute("""
        SELECT dc.id, dc.content, dc.page, dc.chunk_index,
               d.filename, de.vector,
               dc.importance_weight, dc.category
        FROM doc_embeddings de
        JOIN doc_chunks  dc ON de.chunk_id = dc.id
        JOIN documents    d ON dc.doc_id   = d.id
    """).fetchall()
    results = []
    for r in rows:
        vec = np.frombuffer(r[5], dtype=np.float32)
        results.append({
            "id":               r[0],
            "content":          r[1],
            "page":             r[2],
            "chunk_index":      r[3],
            "source":           r[4],
            "score_vector":     cosine_similarity(query_vec, vec),
            "importance_weight": r[6],
            "category":         r[7],
            "type":             "document",
        })
    results.sort(key=lambda x: x["score_vector"], reverse=True)
    return results[:top_k]


# ══════════════════════════════════════════════════════════════
# Utilitaires CLI
# ══════════════════════════════════════════════════════════════

def list_ingested_documents(db_path: Path = DB_PATH) -> list[dict]:
    """Retourne la liste des documents ingérés avec leurs métadonnées."""
    db   = sqlite3.connect(str(db_path))
    rows = db.execute("""
        SELECT filename, page_count, chunk_count, ingested_at
        FROM documents
        ORDER BY ingested_at DESC
    """).fetchall()
    db.close()
    return [
        {
            "filename":    r[0],
            "pages":       r[1],
            "chunks":      r[2],
            "ingested_at": r[3],
        }
        for r in rows
    ]