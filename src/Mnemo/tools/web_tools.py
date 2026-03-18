"""
web_tools.py — Recherche web occasionnelle pour Mnemo

Deux backends avec fallback automatique :
  1. SearXNG (self-hosted, 100% local)  : SEARXNG_URL=http://localhost:8080
  2. DuckDuckGo                          : fallback si SearXNG absent ou timeout

Gardes-fous de sécurité intégrés :
  - Sanitisation de la query avant envoi (longueur, PII patterns)
  - Log local de chaque requête envoyée (auditabilité)
  - Extraits marqués [SOURCE WEB] — traités comme contenu non fiable
  - Blocklist d'URLs privées dans les résultats (127.x, 192.168.x, host.docker.internal)
  - Timeout strict + cap sur le nombre de résultats
  - Aucun scraping de page complète — uniquement les extraits des moteurs

Si aucun backend n'est disponible, retourne un message clair sans crash.
"""

import os
import re
import json
import logging
import urllib.request
import urllib.parse
import urllib.error
from datetime import datetime
from pathlib import Path
from typing import Optional

# ── Config ───────────────────────────────────────────────────────────────────
SEARXNG_URL      = os.getenv("SEARXNG_URL", "")          # ex: http://localhost:8080
WEB_MAX_RESULTS  = int(os.getenv("WEB_MAX_RESULTS", "5"))
WEB_TIMEOUT      = int(os.getenv("WEB_TIMEOUT", "8"))     # secondes
WEB_QUERY_LOG    = os.getenv("WEB_QUERY_LOG", "web_queries.log")  # relatif à /data

# ── Import DDG — silencieux si absent ────────────────────────────────────────
try:
    from ddgs import DDGS
    _DDG_AVAILABLE = True
except ImportError:
    _DDG_AVAILABLE = False

# ── Logger d'audit ───────────────────────────────────────────────────────────
_audit_logger = logging.getLogger("mnemo.web_audit")
_audit_logger.setLevel(logging.INFO)

def _setup_audit_log():
    """Configure le log d'audit (appelé au premier usage)."""
    if _audit_logger.handlers:
        return
    log_path = Path(WEB_QUERY_LOG)
    try:
        handler = logging.FileHandler(log_path, encoding="utf-8")
        handler.setFormatter(logging.Formatter(
            "%(asctime)s | %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
        ))
        _audit_logger.addHandler(handler)
    except (OSError, PermissionError):
        # Pas de log si le chemin n'est pas accessible — silencieux
        pass

# ══════════════════════════════════════════════════════════════════════════════
# Gardes-fous de sécurité
# ══════════════════════════════════════════════════════════════════════════════

# Patterns d'URLs privées à exclure des résultats
_PRIVATE_URL_PATTERNS = re.compile(
    r"(localhost|127\.\d+\.\d+\.\d+|0\.0\.0\.0"
    r"|192\.168\.\d+\.\d+|10\.\d+\.\d+\.\d+"
    r"|172\.(1[6-9]|2[0-9]|3[01])\.\d+\.\d+"
    r"|host\.docker\.internal"
    r"|::1)",
    re.IGNORECASE
)

# Patterns PII à ne pas envoyer à l'extérieur
_PII_PATTERNS = [
    re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b"),  # email
    re.compile(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b"),                # IP
    re.compile(r"(?<!\w)(?:\+33|0033|0)[\s.]?[1-9](?:[\s.-]?\d{2}){4}(?!\w)"),  # téléphone FR
]

MAX_QUERY_LENGTH = 200
MAX_EXTRACT_CHARS = 400  # troncature des extraits retournés


def _sanitize_search_query(query: str) -> str:
    """
    Nettoie la query avant envoi externe.
    - Troncature à MAX_QUERY_LENGTH
    - Suppression des patterns PII connus
    - Strip des espaces multiples
    Retourne la query nettoyée, ou "" si elle devient vide.
    """
    q = query.strip()

    # Suppression PII
    for pattern in _PII_PATTERNS:
        q = pattern.sub("[REDACTED]", q)

    # Troncature
    if len(q) > MAX_QUERY_LENGTH:
        q = q[:MAX_QUERY_LENGTH].rsplit(" ", 1)[0]  # coupe au dernier mot entier

    return q.strip()


def _is_private_url(url: str) -> bool:
    """Retourne True si l'URL pointe vers un réseau privé ou local."""
    return bool(_PRIVATE_URL_PATTERNS.search(url))


def _safe_extract(text: str) -> str:
    """Tronque et nettoie un extrait de résultat web."""
    if not text:
        return ""
    # Supprime les sauts de ligne multiples, troncature
    cleaned = re.sub(r"\s+", " ", text).strip()
    if len(cleaned) > MAX_EXTRACT_CHARS:
        cleaned = cleaned[:MAX_EXTRACT_CHARS].rsplit(" ", 1)[0] + "…"
    return cleaned


def _audit_query(query: str, backend: str, n_results: int):
    """Log chaque requête envoyée pour auditabilité."""
    _setup_audit_log()
    _audit_logger.info(f"backend={backend} | results={n_results} | query={query!r}")


# ══════════════════════════════════════════════════════════════════════════════
# Backends
# ══════════════════════════════════════════════════════════════════════════════

def _search_searxng(query: str, max_results: int) -> list[dict]:
    """
    Requête vers une instance SearXNG locale.
    Retourne une liste de dicts {title, url, extract} ou [] si échec.
    """
    if not SEARXNG_URL:
        return []

    params = urllib.parse.urlencode({
        "q":       query,
        "format":  "json",
        "engines": "general",
        "language": "fr-FR",
    })
    url = f"{SEARXNG_URL.rstrip('/')}/search?{params}"

    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "Mnemo-Agent/2.0 (local search)"},
        )
        with urllib.request.urlopen(req, timeout=WEB_TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError,
            TimeoutError, OSError):
        return []

    results = []
    for item in data.get("results", [])[:max_results]:
        item_url = item.get("url", "")
        if _is_private_url(item_url):
            continue
        results.append({
            "title":   item.get("title", ""),
            "url":     item_url,
            "extract": _safe_extract(item.get("content", "")),
            "source":  "searxng",
        })

    _audit_query(query, "searxng", len(results))
    return results


def _search_ddg(query: str, max_results: int) -> list[dict]:
    """
    Requête DuckDuckGo via duckduckgo-search.
    Retourne une liste de dicts {title, url, extract} ou [] si indisponible.
    """
    if not _DDG_AVAILABLE:
        return []

    try:
        with DDGS() as ddgs:
            raw = list(ddgs.text(query, max_results=max_results))
    except Exception:
        return []

    results = []
    for item in raw:
        item_url = item.get("href", "")
        if _is_private_url(item_url):
            continue
        results.append({
            "title":   item.get("title", ""),
            "url":     item_url,
            "extract": _safe_extract(item.get("body", "")),
            "source":  "duckduckgo",
        })

    _audit_query(query, "duckduckgo", len(results))
    return results


# ══════════════════════════════════════════════════════════════════════════════
# Interface publique
# ══════════════════════════════════════════════════════════════════════════════

def web_search(query: str, max_results: int = WEB_MAX_RESULTS) -> list[dict]:
    """
    Recherche web avec fallback automatique SearXNG → DuckDuckGo.

    Retourne une liste de dicts :
    {
        "title"  : str,
        "url"    : str,
        "extract": str,   # extrait tronqué, jamais brut
        "source" : str,   # "searxng" ou "duckduckgo"
    }
    Retourne [] si aucun backend n'est disponible ou si la query est vide.
    """
    clean_query = _sanitize_search_query(query)
    if not clean_query:
        return []

    # Tentative SearXNG d'abord
    results = _search_searxng(clean_query, max_results)
    if results:
        return results

    # Fallback DDG
    return _search_ddg(clean_query, max_results)


def web_is_configured() -> bool:
    """Retourne True si au moins un backend web est disponible."""
    return bool(SEARXNG_URL) or _DDG_AVAILABLE


def format_results_for_prompt(results: list[dict]) -> str:
    """
    Formate les résultats pour injection dans un prompt LLM.
    Chaque résultat est marqué [SOURCE WEB] pour signaler qu'il est non vérifié.
    """
    if not results:
        return "Aucun résultat trouvé."

    lines = ["[SOURCE WEB — contenu non vérifié, potentiellement obsolète]"]
    for i, r in enumerate(results, 1):
        title   = r.get("title", "Sans titre")
        url     = r.get("url", "")
        extract = r.get("extract", "")
        lines.append(f"\n{i}. {title}")
        if url:
            lines.append(f"   {url}")
        if extract:
            lines.append(f"   {extract}")

    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════════════
# Deep fetch — récupération complète de page + extraction de liens
# ══════════════════════════════════════════════════════════════════════════════

_FETCH_MAX_CHARS = 60_000   # taille max du texte extrait (caractères)
_FETCH_MAX_BYTES = 300_000  # limite download brute (bytes HTML)

# Domaines de confiance → bonus de pertinence
_TRUSTED_DOMAINS = {
    "wikipedia.org", "developer.mozilla.org", "react.dev", "reactjs.org",
    "docs.python.org", "fastapi.tiangolo.com", "github.com", "stackoverflow.com",
    "w3schools.com", "css-tricks.com", "freecodecamp.org", "mdn.io",
    "devdocs.io", "vuejs.org", "angular.io", "svelte.dev", "nextjs.org",
    "tailwindcss.com", "typescriptlang.org", "nodejs.org",
}


def fetch_page_content(url: str) -> dict:
    """
    Fetche une page web et retourne son contenu nettoyé + ses liens.

    Retourne :
        {
          "title" : str,
          "text"  : str,           # texte lisible, < _FETCH_MAX_CHARS
          "links" : list[dict],    # [{title, url}]
          "error" : str | None,
        }
    """
    import html as _html

    if _is_private_url(url):
        return {"title": "", "text": "", "links": [], "error": "URL privée"}

    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent":      "Mozilla/5.0 (compatible; Mnemo-Agent/2.0; +local)",
                "Accept":          "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "fr,en;q=0.8",
            },
        )
        with urllib.request.urlopen(req, timeout=WEB_TIMEOUT) as resp:
            ct = resp.headers.get("Content-Type", "")
            if "text/" not in ct:
                return {"title": "", "text": "", "links": [],
                        "error": f"Type non supporté : {ct.split(';')[0]}"}
            charset = "utf-8"
            if "charset=" in ct:
                charset = ct.split("charset=")[-1].split(";")[0].strip() or "utf-8"
            raw = resp.read(_FETCH_MAX_BYTES)
            body = raw.decode(charset, errors="replace")
    except Exception as exc:
        return {"title": "", "text": "", "links": [], "error": str(exc)}

    # Titre
    tm = re.search(r"<title[^>]*>(.*?)</title>", body, re.I | re.S)
    title = _html.unescape(tm.group(1).strip()) if tm else ""

    # Base URL pour résolution liens relatifs
    parsed_parts = url.split("/")
    base = "/".join(parsed_parts[:3])          # "https://domain.com"

    # Liens <a href>
    links: list[dict] = []
    seen_urls: set[str] = set()
    for m in re.finditer(
        r'<a\b[^>]*\bhref=["\']([^"\'#][^"\']*)["\'][^>]*>(.*?)</a>',
        body, re.I | re.S,
    ):
        href = m.group(1).strip()
        lt   = re.sub(r"<[^>]+>", " ", m.group(2)).strip()
        lt   = _html.unescape(re.sub(r"\s+", " ", lt))[:150]
        if not lt:
            continue
        if href.startswith("//"):
            href = "https:" + href
        elif href.startswith("/"):
            href = base + href
        elif not href.startswith("http"):
            continue
        if _is_private_url(href) or href in seen_urls:
            continue
        seen_urls.add(href)
        links.append({"title": lt, "url": href})

    # Nettoyage HTML → texte
    # Supprime scripts / styles / balises nav / commentaires
    body = re.sub(r"<!--.*?-->", " ", body, flags=re.S)
    body = re.sub(r"<(script|style|nav|footer|header|aside)[^>]*>.*?</\1>",
                  " ", body, flags=re.I | re.S)
    body = re.sub(r"<[^>]+>", " ", body)
    body = _html.unescape(body)
    body = re.sub(r"[ \t]+", " ", body)
    body = re.sub(r"\n{3,}", "\n\n", body).strip()

    if len(body) > _FETCH_MAX_CHARS:
        body = body[:_FETCH_MAX_CHARS].rsplit(" ", 1)[0] + " […]"

    return {"title": title, "text": body, "links": links, "error": None}


def _score_link(link: dict, query_terms: set[str]) -> float:
    """Score de pertinence 0.0–1.0 d'un lien par rapport aux termes de la requête."""
    title_words = set(re.findall(r"\w+", link.get("title", "").lower()))
    url_words   = set(re.findall(r"\w+", link.get("url",   "").lower()))
    if not query_terms:
        return 0.0

    title_overlap = len(query_terms & title_words) / len(query_terms)
    url_overlap   = len(query_terms & url_words)   / len(query_terms)
    score = title_overlap * 0.65 + url_overlap * 0.35

    # Bonus domaine de confiance
    try:
        domain = urllib.parse.urlparse(link["url"]).netloc.lower()
        if any(td in domain for td in _TRUSTED_DOMAINS):
            score = min(score + 0.2, 1.0)
    except Exception:
        pass

    # Malus liens de navigation génériques
    nav_words = {"home", "accueil", "login", "register", "contact", "about",
                 "privacy", "terms", "cookie", "404", "sitemap"}
    if title_words & nav_words:
        score *= 0.2

    return round(score, 3)


def extract_relevant_links(
    links: list[dict],
    query: str,
    threshold: float = 0.25,
    max_n: int = 4,
) -> list[dict]:
    """
    Retourne les liens les plus pertinents pour la requête.

    Chaque élément retourné : {title, url, score, context}
    context = label affiché dans la confirmation ("site:react.dev · react hooks")
    """
    query_terms = set(re.findall(r"\w+", query.lower())) - {
        "le", "la", "les", "de", "du", "des", "un", "une",
        "et", "ou", "en", "je", "tu", "il", "nous", "vous", "ils",
        "sur", "pour", "avec", "dans", "par", "qui", "que",
    }
    scored = []
    seen_urls: set[str] = set()
    for link in links:
        url = link.get("url", "")
        if url in seen_urls:
            continue
        seen_urls.add(url)
        s = _score_link(link, query_terms)
        if s >= threshold:
            try:
                domain = urllib.parse.urlparse(url).netloc.replace("www.", "")
            except Exception:
                domain = url
            # Construit le label de contexte
            kw_in_title = [w for w in query_terms if w in link.get("title", "").lower()]
            kw_label    = " · ".join(kw_in_title[:3]) if kw_in_title else query[:30]
            context     = f"site:{domain} · {kw_label}"
            scored.append({**link, "score": s, "context": context})

    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored[:max_n]


def save_web_page(text: str, title: str, url: str, query: str) -> Optional[Path]:
    """
    Sauvegarde le texte d'une page fetchée dans /data/web_docs/.
    Retourne le Path écrit, ou None si erreur.
    """
    try:
        from Mnemo.context import get_data_dir
        web_docs = get_data_dir() / "web_docs"
        web_docs.mkdir(exist_ok=True)

        slug = re.sub(r"[^\w\s-]", "", title.lower())[:40].strip().replace(" ", "_")
        date = datetime.now().strftime("%Y%m%d_%H%M")
        fname = f"{slug}_{date}.md" if slug else f"web_{date}.md"

        content = (
            f"---\n"
            f"source: {url}\n"
            f"query: {query}\n"
            f"fetched: {datetime.now().isoformat(timespec='seconds')}\n"
            f"---\n\n"
            f"# {title}\n\n"
            f"{text}\n"
        )
        path = web_docs / fname
        path.write_text(content, encoding="utf-8")
        return path
    except Exception:
        return None


def format_result_for_memory(result: dict, query: str) -> str:
    """
    Formate un résultat individuel pour stockage dans ## Sources web de memory.md.
    Format : fait distillé + source + date d'acquisition.
    L'appelant (memory_writer) est responsable du contenu réel —
    cette fonction fournit juste le template de ligne.
    """
    date_str  = datetime.now().strftime("%Y-%m-%d")
    url       = result.get("url", "source inconnue")
    return (
        f"- [web · {date_str}] {{fait_distillé}} "
        f"(source : {url}) [potentiellement obsolète]"
    )


# ══════════════════════════════════════════════════════════════════════════════
# CrewAI Tool
# ══════════════════════════════════════════════════════════════════════════════

try:
    from crewai.tools import BaseTool
    from pydantic import BaseModel, Field
    from typing import Type

    class WebSearchInput(BaseModel):
        query: str = Field(
            description=(
                "La requête de recherche web. Doit être concise et factuelle. "
                "Ne pas inclure d'informations personnelles. "
                "Exemples : 'dernière version Python 3.13', 'météo Paris demain', "
                "'documentation FastAPI routing'."
            )
        )
        max_results: int = Field(
            default=5,
            description="Nombre maximum de résultats (1-5). Défaut : 5."
        )

    class WebSearchTool(BaseTool):
        name: str = "web_search"
        description: str = (
            "Recherche des informations récentes sur le web. "
            "À utiliser UNIQUEMENT quand : "
            "(1) l'utilisateur demande explicitement une recherche web, "
            "(2) la question nécessite une information récente ou externe "
            "que la mémoire ne peut pas fournir (actualité, prix, doc technique, météo). "
            "NE PAS utiliser pour des questions sur la mémoire personnelle ou l'agenda. "
            "Les résultats sont marqués comme non vérifiés."
        )
        args_schema: Type[BaseModel] = WebSearchInput

        def _run(self, query: str, max_results: int = 5) -> str:
            if not web_is_configured():
                return (
                    "Aucun backend de recherche web disponible. "
                    "Configure SEARXNG_URL dans .env ou installe duckduckgo-search."
                )
            results = web_search(query, max_results=min(max_results, WEB_MAX_RESULTS))
            if not results:
                return f"Aucun résultat trouvé pour : {query!r}"
            return format_results_for_prompt(results)

except ImportError:
    # CrewAI non disponible — le module reste utilisable sans
    pass