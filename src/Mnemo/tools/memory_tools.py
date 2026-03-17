import json
import math
import sqlite3
import numpy as np
import ollama
import os
from dataclasses import dataclass, field
# Client Ollama explicite — lit OLLAMA_HOST ou API_BASE depuis l'env.
# Sans ça, le client Python se connecte toujours à localhost:11434
# ce qui échoue dans un conteneur Docker où Ollama est sur l'hôte.
_OLLAMA_HOST = (
    os.getenv("OLLAMA_HOST")
    or os.getenv("API_BASE", "http://localhost:11434").replace("/v1", "")
)
_ollama_client = ollama.Client(host=_OLLAMA_HOST)
import hashlib
import re
from datetime import datetime

from pathlib import Path
from crewai.tools import BaseTool
from pydantic import BaseModel, Field
from typing import Type

# ── Config paths (dynamiques — résolus via ContextVar par requête) ────
from Mnemo.context import get_data_dir


def _db_path() -> Path:
    """Chemin vers memory.db de l'utilisateur courant."""
    return get_data_dir() / "memory.db"


def _markdown_path() -> Path:
    """Chemin vers memory.md de l'utilisateur courant."""
    return get_data_dir() / "memory.md"


def _sessions_dir() -> Path:
    """Répertoire sessions/ de l'utilisateur courant (créé si absent)."""
    d = get_data_dir() / "sessions"
    try:
        d.mkdir(exist_ok=True, parents=True)
    except OSError:
        pass
    return d

EMBED_MODEL   = "nomic-embed-text"
TOP_K_SEARCH  = 10
TOP_K_FINAL   = 5

# Demi-vie par catégorie (en jours) — remplace l'ancien HALF_LIFE_DAYS unique de 30j
HALF_LIFE_BY_CATEGORY: dict[str, float] = {
    "identité":           365.0,  # change rarement — stable sur des années
    "décision":           180.0,
    "projet":              90.0,
    "préférence":          90.0,
    "connaissance":        60.0,
    "historique_session":  14.0,  # périme vite
}

# Un chunk ne descend jamais sous ce score de fraîcheur (évite l'écrasement multiplicatif)
FRESHNESS_FLOOR = 0.15

# Poids statiques par catégorie — ajustables selon tes préférences
CATEGORY_WEIGHTS: dict[str, float] = {
    "identité":           1.5,  # nom, métier, préférences fondamentales
    "décision":           1.3,  # choix techniques ou architecturaux
    "projet":             1.2,  # état, stack, objectifs en cours
    "préférence":         1.1,  # habitudes, style de communication
    "connaissance":       1.0,  # faits appris en session (défaut)
    "historique_session": 0.7,  # résumés de sessions passées
}


# ── Phase 5.3 — Buffer de retrieval du tour courant ──────────────
# Vidé par handle_message() avant chaque kickoff ConversationCrew.
# Rempli par RetrieveMemoryTool._run() pendant l'exécution de l'agent.
# Lu par handle_message() après le kickoff pour persister les IDs dans le session JSON.
_retrieved_this_turn: list[dict] = []


@dataclass
class WeightProfile:
    """Profil de pondération contextuel — passé à retrieve_all() selon le crew appelant."""
    category_overrides:   dict[str, float] = field(default_factory=dict)
    half_life_multiplier: float = 1.0       # < 1 → decay plus rapide, > 1 → plus lent
    freshness_floor:      float = FRESHNESS_FLOOR
    top_k_override:       int | None = None  # None → utilise top_k_final du caller


PROFILES: dict[str, WeightProfile] = {
    "conversation": WeightProfile(),
    "briefing": WeightProfile(
        category_overrides   = {"historique_session": 2.0, "projet": 1.5, "décision": 1.3},
        half_life_multiplier = 0.25,   # fraîcheur très privilégiée
        freshness_floor      = 0.30,   # les vieux chunks sont vraiment écartés
        top_k_override       = 8,      # le briefing a besoin de plus de contexte
    ),
    "curiosity": WeightProfile(
        category_overrides = {"identité": 2.5, "préférence": 2.0},
    ),
    "scheduler": WeightProfile(
        category_overrides = {"décision": 1.8, "projet": 1.6},
    ),
    "shell": WeightProfile(
        category_overrides = {"projet": 1.4, "décision": 1.2},
    ),
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
    return sqlite3.connect(_db_path())


def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b)))


def embed(text: str, prefix: str = "search_document") -> np.ndarray:
    response = _ollama_client.embeddings(model=EMBED_MODEL, prompt=f"{prefix}: {text}")
    return np.array(response["embedding"], dtype=np.float32)


def compute_hash(content: str) -> str:
    return hashlib.md5(content.encode()).hexdigest()


def build_chunk_text(section: str, subsection: str, content: str) -> str:
    return f"Section : {section}\nSous-section : {subsection}\n\n{content}".strip()


# ══════════════════════════════════════════════════════════════
# Pondération — fraîcheur & importance
# ══════════════════════════════════════════════════════════════

def freshness_score(updated_at: str, category: str = "connaissance",
                    half_life_multiplier: float = 1.0) -> float:
    """
    Score de fraîcheur entre 0 et 1, décroissance exponentielle par catégorie.
    Demi-vies : identité=365j · connaissance=60j · historique_session=14j.
    half_life_multiplier < 1 accélère le decay (ex: profil briefing = 0.25).
    """
    half_life = HALF_LIFE_BY_CATEGORY.get(category, HALF_LIFE_BY_CATEGORY["connaissance"])
    half_life *= half_life_multiplier
    try:
        updated = datetime.fromisoformat(updated_at)
    except (ValueError, TypeError):
        return 1.0  # Pas de date → on ne pénalise pas
    age_days = max(0, (datetime.now() - updated).days)
    return math.exp(-age_days / half_life)


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

def _sanitize_fts_query(query: str) -> str:
    """
    Nettoie une query pour FTS5 SQLite.
    FTS5 interprète certains caractères comme opérateurs ou syntaxe :
      ?  → paramètre de binding (OperationalError)
      '  → string SQL (syntax error near "'")
      "  → colonne ou phrase (peut planter si mal balancé)
      *  → wildcard (acceptable mais peut surprendre)
      :  → filtre de colonne
      ( ) → groupement
      -  → exclusion
    Stratégie : retire la ponctuation, garde les mots et chiffres.
    """
    import re
    # Garde uniquement lettres, chiffres, espaces et tirets entre mots
    cleaned = re.sub(r"[^\w\s\-]", " ", query, flags=re.UNICODE)
    # Collapse les espaces multiples
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def search_keyword(db: sqlite3.Connection, query: str, top_k: int = TOP_K_SEARCH) -> list[dict]:
    # FTS5 plante sur une query vide ou composée uniquement d'espaces
    if not query or not query.strip():
        return []
    fts_query = _sanitize_fts_query(query)
    if not fts_query:
        return []
    rows = db.execute("""
        SELECT c.id, c.section, c.subsection, c.content,
               bm25(chunks_fts) as score,
               c.updated_at, c.importance_weight, c.category
        FROM chunks_fts
        JOIN chunks c ON chunks_fts.chunk_id = c.id
        WHERE chunks_fts MATCH ?
        ORDER BY score
        LIMIT ?
    """, (fts_query, top_k)).fetchall()
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
    """
    Poids FTS/vector selon la longueur de la query.
    Interpolation linéaire : 1 mot → (0.70, 0.30) · 10+ mots → (0.25, 0.75).
    """
    n   = len(query.split())
    t   = min(n / 10.0, 1.0)
    w_kw = round(0.70 - 0.45 * t, 3)
    return (w_kw, round(1.0 - w_kw, 3))


def reciprocal_rank_fusion(kw: list[dict], vec: list[dict],
                           query: str = "", k: int = 60,
                           profile: WeightProfile | None = None) -> list[dict]:
    p = profile or PROFILES["conversation"]
    w_fts, w_vec = adaptive_weights(query)
    # Priorité : profil override > poids appris > poids statiques
    effective_cat_weights = {**CATEGORY_WEIGHTS, **_load_learned_weights(), **p.category_overrides}

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
        chunk = all_chunks[cid]
        cat   = chunk.get("category") or "connaissance"

        # Importance : poids explicite DB > catégorie effective (base + overrides profil)
        explicit = chunk.get("importance_weight")
        imp = explicit if explicit is not None else effective_cat_weights.get(cat, 1.0)

        # Fraîcheur : demi-vie par catégorie × multiplicateur profil, plancher garanti
        freshness = max(
            freshness_score(chunk.get("updated_at", datetime.now().isoformat()),
                            category=cat,
                            half_life_multiplier=p.half_life_multiplier),
            p.freshness_floor,
        )

        results.append({
            **chunk,
            "score_rrf":        rrf_score,
            "score_importance": imp,
            "score_freshness":  freshness,
            "score_final":      rrf_score * imp * freshness,
        })

    results.sort(key=lambda x: x["score_final"], reverse=True)
    return results


def retrieve(query: str, top_k_final: int = TOP_K_FINAL) -> list[dict]:
    """Recherche uniquement dans la mémoire personnelle (chunks de memory.md)."""
    db = get_db()
    kw  = search_keyword(db, query)
    vec = search_vector(db, query)
    merged = reciprocal_rank_fusion(kw, vec, query=query)
    db.close()
    return merged[:top_k_final]


def retrieve_all(query: str, top_k_final: int = TOP_K_FINAL,
                 profile: str = "conversation") -> list[dict]:
    """
    Recherche hybride globale : mémoire personnelle + documents ingérés.
    Fusionne les deux sources via RRF avant de retourner le top_k_final.

    profile : clé dans PROFILES — ajuste les poids catégorie, demi-vies et top_k.
    Les chunks mémoire et les doc_chunks sont normalisés dans le même format
    avant la fusion — le champ 'source_type' permet de les distinguer dans le prompt.
    """
    # Import ici pour éviter la dépendance circulaire (ingest_tools importe memory_tools)
    from Mnemo.tools.ingest_tools import search_docs_keyword, search_docs_vector

    p = PROFILES.get(profile, PROFILES["conversation"])
    effective_top_k = p.top_k_override or top_k_final

    db = get_db()

    # ── Mémoire personnelle ────────────────────────────────────
    kw_mem  = search_keyword(db, query)
    vec_mem = search_vector(db, query)
    for c in kw_mem + vec_mem:
        c.setdefault("source_type", "memory")
        c.setdefault("source", f"{c.get('section', '')} > {c.get('subsection', '')}")

    # ── Documents ingérés ──────────────────────────────────────
    kw_doc  = search_docs_keyword(db, query)
    vec_doc = search_docs_vector(db, query)
    for c in kw_doc + vec_doc:
        c.setdefault("source_type", "document")

    db.close()

    # ── Fusion RRF globale ─────────────────────────────────────
    merged_mem = reciprocal_rank_fusion(kw_mem, vec_mem, query=query, profile=p)
    merged_doc = reciprocal_rank_fusion(kw_doc, vec_doc, query=query, profile=p)

    all_results = merged_mem + merged_doc
    all_results.sort(key=lambda x: x["score_final"], reverse=True)

    return all_results[:effective_top_k]


def format_chunks_for_prompt(chunks: list[dict]) -> str:
    """
    Formate les chunks pour injection dans le prompt.
    Distingue la source (mémoire personnelle vs document) dans le header.
    """
    parts = []
    for c in chunks:
        if c.get("source_type") == "document":
            header = f"[📄 {c.get('source', 'document')} — p.{c.get('page', '?')}]"
        else:
            header = f"[🧠 {c.get('section', '')} > {c.get('subsection', '')}]"
        parts.append(f"{header}\n{c['content']}")
    return "\n\n---\n\n".join(parts) if parts else "Aucun souvenir pertinent trouvé."


# ══════════════════════════════════════════════════════════════
# Phase 5.3 — Scoring d'usage des chunks (mémoire procédurale)
# ══════════════════════════════════════════════════════════════

USAGE_THRESHOLD = 0.60  # similarité cosinus min pour considérer un chunk "utilisé"


def score_and_record_chunk_usage(session: dict, session_id: str) -> None:
    """
    Appelé en fin de session (end_session), après ConsolidationCrew.
    Pour chaque tour agent avec retrieved_chunk_ids :
      1. Embed la réponse agent
      2. Calcule cosine_similarity(response_vec, chunk_vec) pour chaque chunk récupéré
      3. Insère dans chunk_usage
      4. Met à jour chunks.use_count + last_used_at pour les chunks confirmés
    Silencieux sur les tours sans retrieved_chunk_ids (sessions pré-5.3 ou non-conversation).
    """
    messages = session.get("messages", [])
    if not messages:
        return

    db   = get_db()
    now  = datetime.now().isoformat()

    try:
        for msg in messages:
            if msg.get("role") != "agent":
                continue
            chunk_ids = msg.get("retrieved_chunk_ids")
            if not chunk_ids:
                continue

            response_text = msg.get("content", "").strip()
            if not response_text:
                continue

            # Embed la réponse — peut échouer si Ollama est offline (fin de session)
            try:
                response_vec = embed(response_text, prefix="search_document")
            except Exception:
                continue

            for chunk_id in chunk_ids:
                # Récupère le vecteur du chunk depuis la DB
                row = db.execute(
                    "SELECT vector FROM embeddings WHERE chunk_id = ?", (chunk_id,)
                ).fetchone()
                if row is None:
                    continue

                chunk_vec  = np.frombuffer(row[0], dtype=np.float32)
                score      = float(cosine_similarity(response_vec, chunk_vec))
                confirmed  = 1 if score >= USAGE_THRESHOLD else 0

                db.execute(
                    "INSERT INTO chunk_usage (chunk_id, session_id, retrieved_at, used_score, confirmed)"
                    " VALUES (?, ?, ?, ?, ?)",
                    (chunk_id, session_id, now, score, confirmed),
                )

                if confirmed:
                    db.execute(
                        "UPDATE chunks SET use_count = use_count + 1, last_used_at = ?"
                        " WHERE id = ?",
                        (now, chunk_id),
                    )

        db.commit()
    finally:
        db.close()


# ══════════════════════════════════════════════════════════════
# Phase 5.4 — Active learning sur les poids
# ══════════════════════════════════════════════════════════════

MIN_SESSIONS  = 20    # sessions minimum avant toute adaptation
LEARNING_RATE = 0.15  # amortissement — max 15% de changement par cycle
WEIGHT_MIN    = 0.30  # plancher absolu
WEIGHT_MAX    = 3.00  # plafond absolu
MIN_RETRIEVED = 10    # nb minimum de retrievals pour qu'une catégorie soit ajustée


def compute_category_stats(db: sqlite3.Connection) -> dict[str, dict]:
    """
    Lit chunk_usage JOIN chunks et retourne par catégorie :
      retrieved : nb de fois récupéré
      confirmed : nb de fois utilisé (confirmed=1)
      utility   : confirmed / retrieved  (0.0 si retrieved == 0)
    """
    rows = db.execute("""
        SELECT c.category,
               COUNT(*)                                    AS retrieved,
               SUM(cu.confirmed)                           AS confirmed
        FROM chunk_usage cu
        JOIN chunks c ON cu.chunk_id = c.id
        GROUP BY c.category
    """).fetchall()

    stats: dict[str, dict] = {}
    for category, retrieved, confirmed in rows:
        cat = category or "connaissance"
        stats[cat] = {
            "retrieved": retrieved,
            "confirmed": int(confirmed or 0),
            "utility":   (confirmed or 0) / retrieved if retrieved else 0.0,
        }
    return stats


def suggest_weight_adjustments(stats: dict[str, dict],
                                current_weights: dict[str, float]) -> dict[str, float]:
    """
    Calcule les nouveaux poids par catégorie.
    Principe : nudge proportionnel à l'écart entre l'utilité observée et la baseline.
      baseline  = utilité moyenne toutes catégories avec retrieved >= MIN_RETRIEVED
      gap       = utility(cat) / baseline
      new_weight = old_weight × (1 + LEARNING_RATE × (gap - 1))
    Clampé entre WEIGHT_MIN et WEIGHT_MAX.
    Catégories avec retrieved < MIN_RETRIEVED : inchangées.
    """
    eligible = {
        cat: s for cat, s in stats.items()
        if s["retrieved"] >= MIN_RETRIEVED
    }
    if not eligible:
        return dict(current_weights)

    baseline = sum(s["utility"] for s in eligible.values()) / len(eligible)
    if baseline == 0:
        return dict(current_weights)

    new_weights = dict(current_weights)
    for cat, s in eligible.items():
        old = current_weights.get(cat, 1.0)
        gap = s["utility"] / baseline
        adjusted = old * (1.0 + LEARNING_RATE * (gap - 1.0))
        new_weights[cat] = round(max(WEIGHT_MIN, min(WEIGHT_MAX, adjusted)), 4)

    return new_weights


def _load_learned_weights() -> dict[str, float]:
    """
    Charge learned_weights.json depuis data_dir si présent.
    Retourne {} en cas d'absence ou d'erreur (fallback silencieux sur CATEGORY_WEIGHTS).
    """
    path = get_data_dir() / "learned_weights.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return {k: float(v) for k, v in data.get("weights", {}).items()}
    except Exception:
        return {}


def adapt_weights_if_ready() -> bool:
    """
    Vérifie si on a suffisamment de données (MIN_SESSIONS sessions avec chunk_usage),
    calcule les nouveaux poids et écrit learned_weights.json.
    Retourne True si une adaptation a été effectuée, False sinon.
    """
    db = get_db()
    try:
        session_count = db.execute(
            "SELECT COUNT(DISTINCT session_id) FROM chunk_usage"
        ).fetchone()[0]

        if session_count < MIN_SESSIONS:
            return False

        stats       = compute_category_stats(db)
        new_weights = suggest_weight_adjustments(stats, CATEGORY_WEIGHTS)

        path = get_data_dir() / "learned_weights.json"
        path.write_text(
            json.dumps({
                "updated_at":        datetime.now().isoformat(),
                "sessions_analyzed": session_count,
                "weights":           new_weights,
            }, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return True
    finally:
        db.close()


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
            # Sauvegarde le chunk en cours avant de changer de section
            # BUG CORRIGÉ : sans ça, le dernier ### de chaque ## était perdu
            if current_content and current_subsection:
                chunks.append({
                    "section":     current_section,
                    "subsection":  current_subsection,
                    "content":     "\n".join(current_content).strip(),
                    "source_line": current_line,
                    "category":    infer_category_from_section(current_section),
                })
            current_section = line.lstrip("# ").strip()
            current_subsection = ""
            current_content = []
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


def sync_markdown_to_db(md_path: Path = None):
    if md_path is None:
        md_path = _markdown_path()
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


def _normalize_section(title: str) -> str:
    """
    Normalise un titre de section pour la comparaison :
    supprime les emojis, espaces et # en tête, met en minuscules.
    Ex: "🧑 Identité Utilisateur" → "identité utilisateur"
    """
    import re
    # Retire les # de début
    title = title.lstrip("# ").strip()
    # Retire les emojis et caractères non-alphanumérique en début de chaîne
    title = re.sub(r'^[\U00010000-\U0010ffff\u2600-\u26FF\u2700-\u27BF\s]+', '', title)
    return title.strip().lower()


def update_markdown_section(section: str, subsection: str, content: str, md_path: Path = None, category: str = "connaissance"):
    """Upsert propre d'une sous-section dans le Markdown."""
    if md_path is None:
        md_path = _markdown_path()
    section    = sanitize_str(section).lstrip("#").strip()  # retire les ## accidentels
    subsection = sanitize_str(subsection).lstrip("#").strip()
    content    = sanitize_str(content)

    # Sous-section vide → fallback sur le nom de section pour éviter ### vide
    if not subsection:
        subsection = section.split(">")[-1].strip() or "Général"

    text  = md_path.read_text(encoding="utf-8") if md_path.exists() else ""
    lines = text.splitlines()

    section_norm = _normalize_section(section)

    in_target_section    = False
    in_target_subsection = False
    section_start        = -1
    subsection_start     = -1
    subsection_end       = len(lines)

    for i, line in enumerate(lines):
        if line.startswith("## "):
            if _normalize_section(line) == section_norm:
                in_target_section = True
                section_start = i
            elif in_target_section:
                in_target_section = False
        if in_target_section and line.startswith("### "):
            sub_norm = line.lstrip("# ").strip().lower()
            if sub_norm == subsection.strip().lower():
                in_target_subsection = True
                subsection_start = i
            elif in_target_subsection:
                subsection_end = i
                break

    # Marqueurs de placeholder — contenu vide sémantiquement, à remplacer
    PLACEHOLDERS = (
        "pas encore renseigné",
        "aucun",
        "aucune",
        "pour l'instant",
        "je dois questionner",
        "je me demande",
    )

    if in_target_subsection and subsection_start != -1:
        # Récupère les lignes existantes (entre ### et la prochaine section)
        existing_lines = lines[subsection_start + 1 : subsection_end]

        # Garde uniquement les lignes qui ont une vraie valeur (pas placeholder, pas vides seules)
        real_lines = [
            l for l in existing_lines
            if l.strip() and not any(p in l.lower() for p in PLACEHOLDERS)
        ]

        # ── Upsert par label pour les lignes atomiques ──
        # Si content est de la forme "- **Label** : valeur",
        # on remplace la ligne existante avec le même label plutôt qu'appender.
        import re as _re
        label_match = _re.match(r"^-\s*\*\*(.+?)\*\*\s*:", content)
        if label_match:
            label_key = label_match.group(1).strip().lower()
            replaced  = False
            new_real  = []
            for existing_line in real_lines:
                ex_match = _re.match(r"^-\s*\*\*(.+?)\*\*\s*:", existing_line)
                if ex_match and ex_match.group(1).strip().lower() == label_key:
                    # Même label → remplace par la nouvelle valeur
                    new_real.append(content)
                    replaced = True
                else:
                    new_real.append(existing_line)
            if not replaced:
                new_real.append(content)
            real_lines = new_real
        else:
            # Contenu narratif — déduplique les lignes exactes, appende si nouveau
            content_stripped = content.strip()
            if content_stripped not in "\n".join(real_lines):
                real_lines = real_lines + [content]
            # Sinon : contenu déjà présent → on n'écrit rien

        merged    = "\n".join(real_lines) if real_lines else content
        new_block = [f"### {subsection}", merged, ""]
        lines[subsection_start:subsection_end] = new_block

    elif section_start != -1:
        # Insère dans la section existante
        new_block = [f"### {subsection}", content, ""]
        lines.insert(subsection_end, "\n".join(new_block))
    else:
        # Crée la section et sous-section
        new_block = [f"### {subsection}", content, ""]
        lines += ["", f"## {section}", ""] + new_block

    md_path.write_text("\n".join(lines), encoding="utf-8")


# ══════════════════════════════════════════════════════════════
# Session helpers
# ══════════════════════════════════════════════════════════════

def load_session_json(session_id: str) -> dict:
    path = _sessions_dir() / f"{session_id}.json"
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


def update_session_memory(session_id: str, user_message: str, agent_response: str,
                          retrieved_chunk_ids: list[str] | None = None):
    session = load_session_json(session_id)
    session.setdefault("session_id", session_id)
    session.setdefault("messages", [])
    session.setdefault("facts_extracted", [])
    session.setdefault("entities_mentioned", [])
    session.setdefault("to_persist", [])
    # Sanitize avant écriture — certains modèles Ollama retournent des surrogates invalides
    session["messages"].append({"role": "user", "content": sanitize_str(user_message)})
    agent_msg: dict = {"role": "agent", "content": sanitize_str(agent_response)}
    if retrieved_chunk_ids:
        agent_msg["retrieved_chunk_ids"] = retrieved_chunk_ids
    session["messages"].append(agent_msg)
    path = _sessions_dir() / f"{session_id}.json"
    path.write_text(json.dumps(session, ensure_ascii=False, indent=2), encoding="utf-8")


# ══════════════════════════════════════════════════════════════
# Détection de modification du Markdown + cohérence DB
# ══════════════════════════════════════════════════════════════

def get_file_hash(path: Path) -> str:
    """Hash MD5 du contenu complet du fichier."""
    return hashlib.md5(path.read_bytes()).hexdigest()


def is_markdown_stale(md_path: Path = None) -> bool:
    """
    Retourne True si memory.md a été modifié depuis la dernière sync.
    Vérifie à la fois le mtime (rapide) et le hash (fiable).
    Retourne True aussi si le fichier n'a jamais été indexé.
    """
    if md_path is None:
        md_path = _markdown_path()
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


def update_file_state(md_path: Path = None):
    """Met à jour file_state après une sync réussie."""
    if md_path is None:
        md_path = _markdown_path()
    if not md_path.exists():
        return
    db = get_db()
    db.execute("""
        INSERT OR REPLACE INTO file_state (path, mtime, file_hash, synced_at)
        VALUES (?, ?, ?, CURRENT_TIMESTAMP)
    """, (str(md_path), md_path.stat().st_mtime, get_file_hash(md_path)))
    db.commit()
    db.close()


def check_and_sync(md_path: Path = None) -> bool:
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
        "(keyword FTS5 + similarité vectorielle). "
        "Cherche à la fois dans la mémoire personnelle (faits mémorisés sur l'utilisateur) "
        "ET dans les documents ingérés (PDF, etc.). "
        "Retourne les chunks les plus pertinents formatés pour injection dans un prompt. "
        "Les résultats indiquent leur source : 🧠 = mémoire personnelle, 📄 = document."
    )
    args_schema: Type[BaseModel] = RetrieveMemoryInput
    profile: str = "conversation"  # profil de pondération contextuel

    def _run(self, query: str) -> str:
        global _retrieved_this_turn
        chunks = retrieve_all(query, profile=self.profile)
        _retrieved_this_turn.extend(chunks)
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


class ListDocumentsInput(BaseModel):
    dummy: str = Field(default="", description="Paramètre non utilisé — appelle sans argument.")

class ListDocumentsTool(BaseTool):
    name: str = "list_documents"
    description: str = (
        "Liste tous les documents ingérés dans la base de connaissances "
        "(PDF, DOCX, TXT, Markdown, code source). "
        "À utiliser quand l'utilisateur demande quels documents, livres ou fichiers "
        "sont disponibles dans la documentation. "
        "Retourne le titre, le nombre de pages et la date d'ingestion de chaque document."
    )
    args_schema: Type[BaseModel] = ListDocumentsInput

    def _run(self, dummy: str = "") -> str:
        from Mnemo.tools.ingest_tools import list_ingested_documents
        docs = list_ingested_documents()
        if not docs:
            return "Aucun document ingéré pour le moment."
        lines = ["Documents disponibles dans la base de connaissances :\n"]
        for d in docs:
            lines.append(
                f"- {d['filename']}  "
                f"({d['pages']} pages · {d['chunks']} chunks · ingéré le {d['ingested_at'][:10]})"
            )
        return "\n".join(lines)


class GetSkippedQuestionsInput(BaseModel):
    dummy: str = Field(default="", description="Paramètre non utilisé.")

class GetSkippedQuestionsTool(BaseTool):
    name: str = "get_skipped_questions"
    description: str = (
        "Retourne la liste des questions que l'utilisateur a déjà refusé de répondre. "
        "À utiliser avant de générer de nouvelles questions pour éviter de reproposer "
        "les mêmes."
    )
    args_schema: Type[BaseModel] = GetSkippedQuestionsInput

    def _run(self, dummy: str = "") -> str:
        db   = get_db()
        rows = db.execute(
            "SELECT question FROM curiosity_skipped ORDER BY skipped_at DESC"
        ).fetchall()
        db.close()
        if not rows:
            return "[]"
        return "\n".join(f"- {r[0]}" for r in rows)


class MarkQuestionSkippedInput(BaseModel):
    question_id: str = Field(..., description="ID de la question (hash) à marquer comme skippée.")
    question:    str = Field(..., description="Texte complet de la question.")

class MarkQuestionSkippedTool(BaseTool):
    name: str = "mark_question_skipped"
    description: str = (
        "Marque une question comme refusée par l'utilisateur. "
        "Cette question ne sera plus proposée dans les futures sessions."
    )
    args_schema: Type[BaseModel] = MarkQuestionSkippedInput

    def _run(self, question_id: str, question: str) -> str:
        db = get_db()
        db.execute(
            "INSERT OR REPLACE INTO curiosity_skipped (id, question) VALUES (?, ?)",
            (question_id, question)
        )
        db.commit()
        db.close()
        return f"✓ Question marquée comme skippée : {question_id}"