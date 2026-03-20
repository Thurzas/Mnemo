"""
status.py — Émission de messages de statut en temps réel via WebSocket.

Deux usages :
  1. WS live     : emit() pousse les messages dans une queue async
                   consommée en parallèle par _run_and_stream() dans api.py.
  2. Session log : flush_session_log() retourne les messages accumulés
                   pour les persister en session (enrichit la consolidation).

emit() est thread-safe — appelé depuis handle_message() (thread synchrone).
"""

import asyncio
import threading

# {session_id: (asyncio.Queue, asyncio.AbstractEventLoop)}
_ws_sessions: dict[str, tuple] = {}

# {session_id: list[str]}
_session_logs: dict[str, list[str]] = {}

_lock = threading.Lock()


def set_session(
    session_id: str,
    queue: "asyncio.Queue",
    loop: "asyncio.AbstractEventLoop",
) -> None:
    """Enregistre la queue WS pour ce session_id. Appelé depuis api.py (async)."""
    with _lock:
        _ws_sessions[session_id] = (queue, loop)
        _session_logs[session_id] = []


def clear_session(session_id: str) -> None:
    """Désenregistre la queue WS (le log reste jusqu'au flush)."""
    with _lock:
        _ws_sessions.pop(session_id, None)


def flush_session_log(session_id: str) -> list[str]:
    """Retourne et supprime le log accumulé pour ce session_id."""
    with _lock:
        return _session_logs.pop(session_id, [])


def emit(session_id: str, text: str) -> None:
    """
    Thread-safe — émet un message de statut vers le WS et l'ajoute au log session.

    Si aucune queue n'est enregistrée (CLI, tests), le message est ignoré côté WS
    mais toujours ajouté au log session pour la persistance.
    """
    with _lock:
        # Log session (toujours)
        log = _session_logs.get(session_id)
        if log is not None:
            log.append(text)

        # WS live (seulement si une queue est active)
        ws_entry = _ws_sessions.get(session_id)

    if ws_entry is not None:
        queue, loop = ws_entry
        try:
            loop.call_soon_threadsafe(
                queue.put_nowait, {"type": "status", "text": text}
            )
        except Exception:
            pass
