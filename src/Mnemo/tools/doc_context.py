"""
Phase E.2 — Doc Context

Interroge doc_chunks (documents ingérés) pour trouver des passages pertinents
à une étape du plan. Retourne les passages avec citations [Source : X, p.Y]
prêts à être injectés dans les prompts.
"""
from __future__ import annotations


def search_ingested_docs(query: str, top_k: int = 4) -> list[dict]:
    """
    Recherche dans les documents ingérés (doc_chunks) via RRF (FTS5 + cosine).

    Retourne une liste de dicts :
      {content: str, source: str, page: int|None, score: float}

    Retourne [] si aucun document ingéré, DB indisponible, ou query vide.
    """
    if not query or not query.strip():
        return []
    try:
        from Mnemo.tools.memory_tools import get_db, reciprocal_rank_fusion
        from Mnemo.tools.ingest_tools import search_docs_keyword, search_docs_vector

        db  = get_db()
        kw  = search_docs_keyword(db, query, top_k=top_k * 2)
        vec = search_docs_vector(db, query, top_k=top_k * 2)
        db.close()

        if not kw and not vec:
            return []

        merged = reciprocal_rank_fusion(kw, vec, query=query)
        return [
            {
                "content": r.get("content", ""),
                "source":  r.get("source", "document"),
                "page":    r.get("page"),
                "score":   round(r.get("score_final", 0.0), 3),
            }
            for r in merged[:top_k]
        ]
    except Exception:
        return []


def format_doc_context(results: list[dict], max_chars: int = 1200) -> str:
    """
    Formate les passages pour injection dans un prompt.
    Inclut les citations [Source : X, p.Y].
    Retourne "" si aucun résultat.
    """
    if not results:
        return ""

    parts = ["## Sources disponibles (documents ingérés)\n"]
    total = len(parts[0])

    for r in results:
        source   = r.get("source", "document")
        page     = r.get("page")
        page_str = f", p.{page}" if page else ""
        citation = f"[{source}{page_str}]"
        snippet  = r.get("content", "").strip()
        if len(snippet) > 400:
            snippet = snippet[:400] + "…"
        block = f"\n{citation}\n> {snippet}\n"
        if total + len(block) > max_chars:
            break
        parts.append(block)
        total += len(block)

    return "".join(parts).strip()