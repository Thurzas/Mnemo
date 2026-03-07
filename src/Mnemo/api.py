"""
api.py — Dashboard web pour Mnemo (FastAPI)

Routes :
  GET  /                     → dashboard HTML
  GET  /api/health           → {"status": "ok"}
  POST /api/message          → {message, session_id?} → {response, session_id}
  GET  /api/memory           → contenu de memory.md + sections parsées
  GET  /api/sessions         → liste des sessions avec métadonnées
  GET  /api/sessions/{id}    → messages d'une session
  GET  /api/calendar         → événements à venir (14 jours)

Sécurité :
  - Route shell bloquée (pas de subprocess depuis le web)
  - needs_web auto-refusé (pas de confirmation interactive)
  - needs_clarification ignoré
  - Validation des session_id contre le path traversal
"""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

DATA_PATH    = Path(os.getenv("DATA_PATH", "/data")).resolve()
MEMORY_FILE  = DATA_PATH / "memory.md"
SESSIONS_DIR = DATA_PATH / "sessions"
STATIC_DIR   = Path(__file__).parent / "static"

app = FastAPI(title="Mnemo Dashboard", docs_url=None, redoc_url=None)


# ── Modèles ────────────────────────────────────────────────────────

class MessageRequest(BaseModel):
    message: str
    session_id: str | None = None


class MessageResponse(BaseModel):
    response: str
    session_id: str


# ── Pipeline web (sans confirmation interactive) ───────────────────

_SHELL_BLOCKED = (
    "Les commandes shell ne sont pas disponibles depuis l'interface web. "
    "Utilise le terminal CLI pour les opérations système."
)


def _handle_message_web(user_message: str, session_id: str) -> str:
    """
    Variante de handle_message sans stdin — destinée à l'API web.

    Différences vs handle_message() :
    - route=shell → refus explicite (sécurité)
    - needs_web   → auto-refusé (pas de confirmation interactive)
    - needs_clarification → ignoré (pas d'input utilisateur possible)
    - conversation / note / scheduler / calendar : pipeline normal
    """
    from Mnemo.main import (
        _parse_eval_json,
        _route_message,
        _detect_shell_intent,
        _ml_detect_intent,
    )
    from Mnemo.crew import EvaluationCrew
    from Mnemo.tools.memory_tools import update_session_memory
    from Mnemo.tools.calendar_tools import get_temporal_context

    temporal_ctx = get_temporal_context()

    # Sécurité rapide : keywords shell → refus immédiat sans LLM
    if _detect_shell_intent(user_message):
        return _SHELL_BLOCKED

    # ML pre-check : shell à haute confiance → refus
    ml_route, ml_conf = _ml_detect_intent(user_message)
    if ml_route == "shell" and ml_conf >= 0.80:
        return _SHELL_BLOCKED

    # EvaluationCrew (LLM) — analyse sémantique complète
    eval_result = EvaluationCrew().crew().kickoff(inputs={
        "user_message": user_message,
        "temporal_context": temporal_ctx,
    })
    eval_json = _parse_eval_json(eval_result.raw.strip())

    # Dernier filet : si le LLM a quand même routé vers shell
    if eval_json.get("route") == "shell":
        return _SHELL_BLOCKED

    # Désactiver les flux interactifs non disponibles en mode web
    eval_json["needs_web"] = False
    eval_json["needs_clarification"] = False

    response = _route_message(eval_json, user_message, session_id, temporal_ctx, "")
    update_session_memory(session_id, user_message, response)
    return response


# ── Routes ─────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def dashboard():
    html_file = STATIC_DIR / "index.html"
    if html_file.exists():
        return HTMLResponse(html_file.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>Dashboard unavailable</h1>", status_code=404)


@app.get("/api/health")
async def health():
    return {
        "status": "ok",
        "data_path": str(DATA_PATH),
        "memory_exists": MEMORY_FILE.exists(),
        "sessions_dir": str(SESSIONS_DIR),
    }


@app.post("/api/message", response_model=MessageResponse)
def message(req: MessageRequest):
    """
    Envoie un message à Mnemo — exécuté en thread pool (def, pas async def)
    pour ne pas bloquer la boucle d'événements pendant l'appel LLM.
    """
    sid = req.session_id or f"web_{uuid.uuid4().hex[:12]}"
    try:
        response = _handle_message_web(req.message, sid)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    return {"response": response, "session_id": sid}


@app.get("/api/memory")
async def memory():
    if not MEMORY_FILE.exists():
        return {"content": "", "sections": []}

    content = MEMORY_FILE.read_text(encoding="utf-8")

    sections: list[dict] = []
    current: dict | None = None
    for line in content.splitlines():
        if line.startswith("## "):
            if current is not None:
                sections.append(current)
            current = {"title": line[3:].strip(), "content": ""}
        elif current is not None:
            current["content"] += line + "\n"
    if current is not None:
        sections.append(current)

    return {"content": content, "sections": sections}


@app.get("/api/sessions")
async def sessions():
    if not SESSIONS_DIR.exists():
        return {"sessions": []}

    result = []
    for f in sorted(
        SESSIONS_DIR.glob("*.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    ):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            messages = data.get("messages", [])
            result.append({
                "id": f.stem,
                "message_count": len(messages),
                "done": (SESSIONS_DIR / f"{f.stem}.done").exists(),
                "modified": f.stat().st_mtime,
                "preview": (
                    messages[0].get("user_message", "")[:100]
                    if messages else ""
                ),
            })
        except (json.JSONDecodeError, OSError):
            continue

    return {"sessions": result}


@app.get("/api/sessions/{session_id}")
async def session_detail(session_id: str):
    if any(c in session_id for c in ("/", "\\", "..")):
        raise HTTPException(status_code=400, detail="Invalid session ID")

    path = SESSIONS_DIR / f"{session_id}.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Session not found")

    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        raise HTTPException(status_code=500, detail="Corrupted session file")


@app.get("/api/calendar")
async def calendar():
    try:
        from Mnemo.tools.calendar_tools import get_upcoming_events
        events = get_upcoming_events(days=14)
        # Sérialisation des objets date/datetime Python → ISO string
        serialized = []
        for e in events:
            s = dict(e)
            s["date"]     = e["date"].isoformat()     if e.get("date")     else None
            s["datetime"] = e["datetime"].isoformat() if e.get("datetime") else None
            serialized.append(s)
        return {"events": serialized}
    except Exception:
        return {"events": []}


# ── Point d'entrée local ───────────────────────────────────────────

def run():
    """Lance le serveur de dashboard en mode développement."""
    import uvicorn
    uvicorn.run("Mnemo.api:app", host="0.0.0.0", port=8000, reload=True)
