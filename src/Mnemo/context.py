"""
context.py — Contexte par requête pour le support multi-utilisateurs

Utilise contextvars.ContextVar pour stocker les chemins de données et la
source calendrier de l'utilisateur courant. Thread-safe : anyio/asyncio copie
le contexte lors de la soumission aux thread pools, donc les tools CrewAI
(qui tournent dans le même thread que le handler FastAPI) voient les bonnes valeurs.

Usage dans l'API :
    set_data_dir(Path("/data/users/alice"))
    set_calendar_source("https://...")

Usage dans les tools :
    db = sqlite3.connect(_db_path())     # via get_data_dir()
    md = _markdown_path()
"""

from __future__ import annotations

import os
from contextvars import ContextVar
from pathlib import Path

# ── ContextVars ────────────────────────────────────────────────
_user_data_dir: ContextVar[Path | None]   = ContextVar("user_data_dir",    default=None)
_calendar_src:  ContextVar[str | None]    = ContextVar("calendar_source",  default=None)


# ── Data dir ───────────────────────────────────────────────────

def get_data_dir() -> Path:
    """
    Retourne le répertoire de données de l'utilisateur courant.
    - Si un ContextVar est positionné (requête API authentifiée) : utilise ce chemin.
    - Sinon : fallback sur DATA_PATH env var (CLI, scheduler, tests).
    """
    d = _user_data_dir.get()
    if d is not None:
        return d
    return Path(os.getenv("DATA_PATH", "/data")).resolve()


def set_data_dir(path: Path) -> None:
    """Positionne le répertoire de données pour la requête/tâche courante."""
    _user_data_dir.set(path)


# ── Calendar source ────────────────────────────────────────────

def get_calendar_source() -> str:
    """
    Retourne la source calendrier de l'utilisateur courant.
    - Si un ContextVar est positionné : utilise cette valeur (profil utilisateur).
    - Sinon : fallback sur CALENDAR_SOURCE env var (CLI, scheduler).
    """
    override = _calendar_src.get()
    if override is not None:
        return override
    return os.getenv("CALENDAR_SOURCE", "")


def set_calendar_source(source: str) -> None:
    """Positionne la source calendrier pour la requête/tâche courante."""
    _calendar_src.set(source)