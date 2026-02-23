import json
import math
import sqlite3
import numpy as np
import ollama
import hashlib
import re
from datetime import datetime

from pathlib import Path
from crewai.tools import BaseTool
from pydantic import BaseModel, Field
from typing import Type

# ── Config paths ──────────────────────────────────────────────
DB_PATH      = Path("memory.db")
MARKDOWN_PATH = Path("memory.md")
SESSIONS_DIR = Path("sessions")
SESSIONS_DIR.mkdir(exist_ok=True)

EMBED_MODEL   = "nomic-embed-text"
TOP_K_SEARCH  = 10
TOP_K_FINAL   = 5
HALF_LIFE_DAYS = 30.0  # Fraîcheur : un chunk vieux de 30j a un score de ~0.37

# Poids statiques par catégorie — ajustables selon tes préférences
CATEGORY_WEIGHTS: dict[str, float] = {
    "identité":           1.5,  # nom, métier, préférences fondamentales
    "décision":           1.3,  # choix techniques ou architecturaux
    "projet":             1.2,  # état, stack, objectifs en cours
    "préférence":         1.1,  # habitudes, style de communication
    "connaissance":       1.0,  # faits appris en session (défaut)
    "historique_session": 0.7,  # résumés de sessions passées
}


# ══════════════════════════════════════════════════════════════
# Sanitization
# ══════════════════════════════════════════════════════════════

def sanitize_str(text: str) -> str:
    """Nettoie les caractères surrogates invalides produits par certains modèles Ollama.
    Ces caractères (ex: \udcc3) crashent json.dumps avec ensure_ascii=False."""
    return text.encode("utf-8", errors="ignore").decode("utf-8")
TOP_K_FINAL  = 5


# ══════════════════════════════════════════════════════════════
# Helpers bas niveau
# ══════════════════════════════════════════════════════════════

def get_db() -> sqlite3.Connection:
    return sqlite3.connect(DB_PATH)


def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b)))


def embed(text: str, prefix: str = "search_document") -> np.ndarray:
    response = ollama.embeddings(model=EMBED_MODEL, prompt=f"{prefix}: {text}")
    return np.array(response["embedding"], dtype=np.float32)


def compute_hash(content: str) -> str:
    return hashlib.md5(content.encode()).hexdigest()


def build_chunk_text(section: str, subsection: str, content: str) -> str:
    return f"Section : {section}\nSous-section : {subsection}\n\n{content}".strip()


# ══════════════════════════════════════════════════════════════
# Pondération — fraîcheur & importance
# ══════════════════════════════════════════════════════════════

def freshness_score(updated_at: str, half_life_days: float = HALF_LIFE_DAYS) -> float:
    """
    Score de fraîcheur entre 0 et 1, décroissance exponentielle.
    half_life_days=30 → chunk vieux de 30j = 0.37, 60j = 0.14, 90j = 0.05.
    Configurable via HALF_LIFE_DAYS en tête de fichier.
    """
    try:
        updated = datetime.fromisoformat(updated_at)
    except (ValueError, TypeError):
        return 1.0  # Pas de date → on ne pénalise pas
    age_days = max(0, (datetime.now() - updated).days)
    return math.exp(-age_days / half_life_days)


def importance_score(category: str, weight: float | None = None) -> float:
    """
    Retourne le poids d'importance d'un chunk.
    Priorité : weight explicite stocké en DB > catégorie > défaut 1.0
    """
    if weight is not None:
        return weight
    return CATEGORY_WEIGHTS.get(category or "connaissance", 1.0)


# ══════════════════════════════════════════════════════════════
# Retrieval hybride
# ══════════════════════════════════════════════════════════════

def search_keyword(db: sqlite3.Connection, query: str, top_k: int = TOP_K_SEARCH) -> list[dict]:
    rows = db.execute("""
        SELECT c.id, c.section, c.subsection, c.content,
               bm25(chunks_fts) as score,
               c.updated_at, c.importance_weight, c.category
        FROM chunks_fts
        JOIN chunks c ON chunks_fts.chunk_id = c.id
        WHERE chunks_fts MATCH ?
        ORDER BY score
        LIMIT ?
    """, (query, top_k)).fetchall()
    return [{
        "id": r[0], "section": r[1], "subsection": r[2], "content": r[3],
        "score_fts": abs(r[4]),
        "updated_at": r[5], "importance_weight": r[6], "category": r[7]
    } for r in rows]


def search_vector(db: sqlite3.Connection, query: str, top_k: int = TOP_K_SEARCH) -> list[dict]:
    query_vec = embed(query, prefix="search_query")
    rows = db.execute("""
        SELECT c.id, c.section, c.subsection, c.content, e.vector,
               c.updated_at, c.importance_weight, c.category
        FROM embeddings e
        JOIN chunks c ON e.chunk_id = c.id
    """).fetchall()
    results = []
    for r in rows:
        vec = np.frombuffer(r[4], dtype=np.float32)
        results.append({
            "id": r[0], "section": r[1], "subsection": r[2], "content": r[3],
            "score_vector": cosine_similarity(query_vec, vec),
            "updated_at": r[5], "importance_weight": r[6], "category": r[7]
        })
    results.sort(key=lambda x: x["score_vector"], reverse=True)
    return results[:top_k]


def adaptive_weights(query: str) -> tuple[float, float]:
    return (0.6, 0.4) if len(query.split()) <= 2 else (0.3, 0.7)


def reciprocal_rank_fusion(kw: list[dict], vec: list[dict], k: int = 60) -> list[dict]:
    w_fts, w_vec = adaptive_weights(" ".join(c["content"] for c in kw[:1]))
    scores: dict[str, float] = {}
    all_chunks: dict[str, dict] = {}

    for rank, chunk in enumerate(kw):
        cid = chunk["id"]
        scores[cid] = scores.get(cid, 0) + w_fts * (1 / (k + rank + 1))
        all_chunks[cid] = chunk
    for rank, chunk in enumerate(vec):
        cid = chunk["id"]
        scores[cid] = scores.get(cid, 0) + w_vec * (1 / (k + rank + 1))
        all_chunks[cid] = chunk

    results = []
    for cid, rrf_score in scores.items():
        chunk      = all_chunks[cid]
        importance = importance_score(chunk.get("category"), chunk.get("importance_weight"))
        freshness  = freshness_score(chunk.get("updated_at", datetime.now().isoformat()))
        results.append({
            **chunk,
            "score_rrf":       rrf_score,
            "score_importance": importance,
            "score_freshness":  freshness,
            "score_final":      rrf_score * importance * freshness,
        })

    results.sort(key=lambda x: x["score_final"], reverse=True)
    return results


def retrieve(query: str, top_k_final: int = TOP_K_FINAL) -> list[dict]:
    db = get_db()
    kw  = search_keyword(db, query)
    vec = search_vector(db, query)
    merged = reciprocal_rank_fusion(kw, vec)
    db.close()
    return merged[:top_k_final]


def format_chunks_for_prompt(chunks: list[dict]) -> str:
    parts = [f"[{c['section']} > {c['subsection']}]\n{c['content']}" for c in chunks]
    return "\n\n---\n\n".join(parts) if parts else "Aucun souvenir pertinent trouvé."


# ══════════════════════════════════════════════════════════════
# Markdown helpers
# ══════════════════════════════════════════════════════════════

# Mapping section → catégorie pour l'inférence lors du parsing Markdown
SECTION_CATEGORY_MAP: dict[str, str] = {
    "identité utilisateur":   "identité",
    "identité agent":         "identité",
    "connaissances":          "connaissance",
    "projets":                "projet",
    "décisions":              "décision",
    "préférences":            "préférence",
    "historique des sessions": "historique_session",
    "à ne jamais oublier":    "décision",
}

def infer_category_from_section(section: str) -> str:
    """Infère la catégorie d'un chunk depuis le nom de sa section parente."""
    section_lower = section.lower().strip()
    # Supprime les emojis et caractères non-alpha en début de chaîne
    section_clean = section_lower.lstrip("🧑🤖📚🔁⚠️ ").strip()
    for key, cat in SECTION_CATEGORY_MAP.items():
        if key in section_clean:
            return cat
    return "connaissance"  # Défaut


def parse_markdown_chunks(md_path: Path) -> list[dict]:
    lines = md_path.read_text(encoding="utf-8").splitlines()
    chunks, current_section, current_subsection, current_content, current_line = [], "", "", [], 0
    for i, line in enumerate(lines):
        if line.startswith("## "):
            current_section = line.lstrip("# ").strip()
            current_subsection = ""
        elif line.startswith("### "):
            if current_content and current_subsection:
                chunks.append({
                    "section":    current_section,
                    "subsection": current_subsection,
                    "content":    "\n".join(current_content).strip(),
                    "source_line": current_line,
                    "category":   infer_category_from_section(current_section),
                })
            current_subsection = line.lstrip("# ").strip()
            current_content = []
            current_line = i
        elif current_subsection:
            current_content.append(line.rstrip())
    if current_content and current_subsection:
        chunks.append({
            "section":    current_section,
            "subsection": current_subsection,
            "content":    "\n".join(current_content).strip(),
            "source_line": current_line,
            "category":   infer_category_from_section(current_section),
        })
    return [c for c in chunks if len(c["content"]) > 50]


def upsert_chunk(
    db: sqlite3.Connection,
    section: str,
    subsection: str,
    content: str,
    source_line: int,
    category: str = "connaissance",
    importance_weight: float | None = None,
):
    chunk_text = build_chunk_text(section, subsection, content)
    chunk_id   = compute_hash(chunk_text)
    if db.execute("SELECT 1 FROM chunks WHERE id = ?", (chunk_id,)).fetchone():
        return

    # Poids : explicite > catégorie > défaut
    weight = importance_weight if importance_weight is not None else CATEGORY_WEIGHTS.get(category, 1.0)

    db.execute("""
        INSERT OR REPLACE INTO chunks
            (id, section, subsection, content, updated_at, source_line, category, importance_weight)
        VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP, ?, ?, ?)
    """, (chunk_id, section, subsection, content, source_line, category, weight))
    vector = embed(chunk_text)
    db.execute("""
        INSERT OR REPLACE INTO embeddings (chunk_id, model, vector, dim)
        VALUES (?, ?, ?, ?)
    """, (chunk_id, EMBED_MODEL, vector.tobytes(), len(vector)))
    db.execute("""
        INSERT INTO chunks_fts (chunk_id, content, section, subsection) VALUES (?, ?, ?, ?)
    """, (chunk_id, content, section, subsection))
    db.commit()


def sync_markdown_to_db(md_path: Path = MARKDOWN_PATH):
    db = get_db()
    chunks = parse_markdown_chunks(md_path)
    expected_ids = set()
    for c in chunks:
        chunk_text = build_chunk_text(c["section"], c["subsection"], c["content"])
        expected_ids.add(compute_hash(chunk_text))
        upsert_chunk(
            db,
            c["section"],
            c["subsection"],
            c["content"],
            c["source_line"],
            category=c.get("category", "connaissance"),
        )
    existing_ids = {r[0] for r in db.execute("SELECT id FROM chunks").fetchall()}
    for chunk_id in existing_ids - expected_ids:
        db.execute("DELETE FROM chunks WHERE id = ?", (chunk_id,))
        db.execute("DELETE FROM chunks_fts WHERE chunk_id = ?", (chunk_id,))
    db.commit()
    db.close()

    # Met à jour l'état du fichier après chaque sync réussie
    update_file_state(md_path)


def update_markdown_section(section: str, subsection: str, content: str, md_path: Path = MARKDOWN_PATH, category: str = "connaissance"):
    """Upsert propre d'une sous-section dans le Markdown."""
    # Sanitize — le memory_writer peut recevoir du texte corrompu du modèle
    section    = sanitize_str(section)
    subsection = sanitize_str(subsection)
    content    = sanitize_str(content)
    text = md_path.read_text(encoding="utf-8") if md_path.exists() else ""
    lines = text.splitlines()

    in_target_section    = False
    in_target_subsection = False
    section_start        = -1
    subsection_start     = -1
    subsection_end       = len(lines)

    for i, line in enumerate(lines):
        if line.startswith("## ") and line.lstrip("# ").strip() == section:
            in_target_section = True
            section_start = i
        elif line.startswith("## ") and in_target_section:
            in_target_section = False
        if in_target_section and line.startswith("### ") and line.lstrip("# ").strip() == subsection:
            in_target_subsection = True
            subsection_start = i
        elif in_target_subsection and (line.startswith("## ") or line.startswith("### ")):
            subsection_end = i
            break

    new_block = [f"### {subsection}", content, ""]

    if in_target_subsection and subsection_start != -1:
        # Remplace le bloc existant
        lines[subsection_start:subsection_end] = new_block
    elif section_start != -1:
        # Insère dans la section existante
        lines.insert(subsection_end, "\n".join(new_block))
    else:
        # Crée la section et sous-section
        lines += ["", f"## {section}", ""] + new_block

    md_path.write_text("\n".join(lines), encoding="utf-8")


# ══════════════════════════════════════════════════════════════
# Session helpers
# ══════════════════════════════════════════════════════════════

def load_session_json(session_id: str) -> dict:
    path = SESSIONS_DIR / f"{session_id}.json"
    if not path.exists():
        return {}
    try:
        raw = path.read_text(encoding="utf-8", errors="ignore").strip()
        if not raw:
            return {}
        return json.loads(raw)
    except json.JSONDecodeError:
        # Fichier corrompu — on tente de le renommer pour archivage et on repart propre
        broken_path = path.with_suffix(".broken.json")
        path.rename(broken_path)
        print(f"   ⚠️  Session corrompue archivée : {broken_path.name}")
        return {}


def update_session_memory(session_id: str, user_message: str, agent_response: str):
    session = load_session_json(session_id)
    session.setdefault("session_id", session_id)
    session.setdefault("messages", [])
    session.setdefault("facts_extracted", [])
    session.setdefault("entities_mentioned", [])
    session.setdefault("to_persist", [])
    # Sanitize avant écriture — certains modèles Ollama retournent des surrogates invalides
    session["messages"].append({"role": "user", "content": sanitize_str(user_message)})
    session["messages"].append({"role": "agent", "content": sanitize_str(agent_response)})
    path = SESSIONS_DIR / f"{session_id}.json"
    path.write_text(json.dumps(session, ensure_ascii=False, indent=2), encoding="utf-8")


# ══════════════════════════════════════════════════════════════
# Détection de modification du Markdown + cohérence DB
# ══════════════════════════════════════════════════════════════

def get_file_hash(path: Path) -> str:
    """Hash MD5 du contenu complet du fichier."""
    return hashlib.md5(path.read_bytes()).hexdigest()


def is_markdown_stale(md_path: Path = MARKDOWN_PATH) -> bool:
    """
    Retourne True si memory.md a été modifié depuis la dernière sync.
    Vérifie à la fois le mtime (rapide) et le hash (fiable).
    Retourne True aussi si le fichier n'a jamais été indexé.
    """
    if not md_path.exists():
        return False  # Rien à syncer

    db = get_db()
    row = db.execute(
        "SELECT mtime, file_hash FROM file_state WHERE path = ?",
        (str(md_path),)
    ).fetchone()
    db.close()

    if not row:
        return True  # Jamais indexé → sync obligatoire

    stored_mtime, stored_hash = row
    current_mtime = md_path.stat().st_mtime

    # Optimisation : si mtime identique, pas besoin de hasher
    if current_mtime == stored_mtime:
        return False

    # mtime a changé → on vérifie le hash pour confirmer
    # (évite les faux positifs dus aux métadonnées système)
    return get_file_hash(md_path) != stored_hash


def update_file_state(md_path: Path = MARKDOWN_PATH):
    """Met à jour file_state après une sync réussie."""
    if not md_path.exists():
        return
    db = get_db()
    db.execute("""
        INSERT OR REPLACE INTO file_state (path, mtime, file_hash, synced_at)
        VALUES (?, ?, ?, CURRENT_TIMESTAMP)
    """, (str(md_path), md_path.stat().st_mtime, get_file_hash(md_path)))
    db.commit()
    db.close()


def check_and_sync(md_path: Path = MARKDOWN_PATH) -> bool:
    """
    Vérifie si memory.md est désynchronisé et re-sync si nécessaire.
    Appelé au démarrage de run().
    Retourne True si une sync a été effectuée, False sinon.
    """
    if not is_markdown_stale(md_path):
        return False

    print("🔄 memory.md modifié depuis la dernière sync — re-indexation en cours...")
    sync_markdown_to_db(md_path)  # update_file_state() appelé en interne
    print("✅ Re-indexation terminée.")
    return True


# ══════════════════════════════════════════════════════════════
# TOOLS — Classe BaseTool (format CrewAI moderne)
# ══════════════════════════════════════════════════════════════

class RetrieveMemoryInput(BaseModel):
    query: str = Field(..., description="La query optimisée pour rechercher dans la mémoire long terme.")

class RetrieveMemoryTool(BaseTool):
    name: str = "retrieve_memory"
    description: str = (
        "Recherche dans la mémoire long terme de l'agent via une recherche hybride "
        "(keyword FTS5 + similarité vectorielle). Retourne les chunks les plus pertinents "
        "formatés pour injection dans un prompt."
    )
    args_schema: Type[BaseModel] = RetrieveMemoryInput

    def _run(self, query: str) -> str:
        chunks = retrieve(query)
        return format_chunks_for_prompt(chunks)


# ──────────────────────────────────────────────────────────────

class GetSessionMemoryInput(BaseModel):
    session_id: str = Field(..., description="L'identifiant unique de la session courante.")

class GetSessionMemoryTool(BaseTool):
    name: str = "get_session_memory"
    description: str = (
        "Récupère la mémoire de la session courante au format JSON. "
        "Contient l'historique des messages, les entités mentionnées et les faits extraits."
    )
    args_schema: Type[BaseModel] = GetSessionMemoryInput

    def _run(self, session_id: str) -> str:
        session = load_session_json(session_id)
        return json.dumps(session, ensure_ascii=False, indent=2) if session else "{}"


# ──────────────────────────────────────────────────────────────

class UpdateMarkdownInput(BaseModel):
    section:    str = Field(..., description="La section ## cible dans le Markdown (ex: 'Connaissances persistantes').")
    subsection: str = Field(..., description="La sous-section ### cible (ex: 'Projets en cours > MonProjet').")
    content:    str = Field(..., description="Le contenu Markdown à écrire dans cette sous-section.")
    category:   str = Field(
        default="connaissance",
        description=(
            "Catégorie sémantique du fait. Détermine son importance dans le retrieval. "
            "Valeurs : identité | décision | projet | préférence | connaissance | historique_session"
        )
    )

class UpdateMarkdownTool(BaseTool):
    name: str = "update_markdown_memory"
    description: str = (
        "Écrit ou met à jour une entrée dans le fichier memory.md. "
        "Effectue un upsert propre : crée la section si elle n'existe pas, "
        "met à jour si elle existe déjà. Ne duplique jamais. "
        "La catégorie détermine le poids du fait dans les recherches futures : "
        "identité (1.5) > décision (1.3) > projet (1.2) > préférence (1.1) > connaissance (1.0) > historique_session (0.7)."
    )
    args_schema: Type[BaseModel] = UpdateMarkdownInput

    def _run(self, section: str, subsection: str, content: str, category: str = "connaissance") -> str:
        update_markdown_section(section, subsection, content, category=category)
        weight = CATEGORY_WEIGHTS.get(category, 1.0)
        return f"✓ [{section} > {subsection}] mis à jour (catégorie: {category}, poids: {weight})"


# ──────────────────────────────────────────────────────────────

class SyncMemoryDbInput(BaseModel):
    reason: str = Field(default="post-write sync", description="Raison de la synchronisation (pour le log).")

class SyncMemoryDbTool(BaseTool):
    name: str = "sync_memory_db"
    description: str = (
        "Re-synchronise la base SQLite (chunks + embeddings + FTS5) "
        "après une modification du fichier memory.md. "
        "Détecte les chunks modifiés via hash MD5 et re-vectorise uniquement ceux qui ont changé."
    )
    args_schema: Type[BaseModel] = SyncMemoryDbInput

    def _run(self, reason: str = "post-write sync") -> str:
        sync_markdown_to_db()
        return f"✓ Synchronisation SQLite terminée ({reason})"