"""
api.py — Dashboard web pour Mnemo (FastAPI)

Routes :
  GET    /                       → dashboard HTML
  GET    /api/health             → {"status": "ok"}
  POST   /api/message            → {message, session_id?} → {response, session_id}
  GET    /api/memory             → contenu de memory.md + sections parsées
  POST   /api/memory             → écriture memory.md + sync DB
  GET    /api/sessions           → liste des sessions avec métadonnées
  GET    /api/sessions/{id}      → messages d'une session
  GET    /api/calendar           → événements à venir (14 jours) avec uid
  POST   /api/calendar           → créer un événement ICS
  PUT    /api/calendar/{uid}     → modifier un événement ICS
  DELETE /api/calendar/{uid}     → supprimer un événement ICS
  GET    /api/reminders          → rappels depuis briefing.md
  POST   /api/users              → créer un utilisateur (MNEMO_ADMIN_TOKEN requis)
  GET    /api/auth/whoami        → infos de l'utilisateur courant
  WS     /ws/message             → streaming token par token (auth + message loop)

Authentification :
  - Toutes les routes /api/* (sauf health) requièrent : Authorization: Bearer <token>
  - Les tokens sont stockés dans DATA_PATH/users.json (hash SHA-256)
  - Chaque utilisateur a son répertoire DATA_PATH/users/{username}/

Sécurité :
  - Route shell bloquée (pas de subprocess depuis le web)
  - needs_clarification ignoré
  - Validation des session_id contre le path traversal
  - Écriture calendrier : lecture seule si CALENDAR_SOURCE est une URL
"""

from __future__ import annotations

import asyncio
import contextvars
import hashlib
import json
import os
import re
import secrets
import uuid
from datetime import datetime
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

DATA_PATH  = Path(os.getenv("DATA_PATH", "/data")).resolve()
USERS_FILE = DATA_PATH / "users.json"
USERS_DIR  = DATA_PATH / "users"
STATIC_DIR = Path(__file__).parent / "static"

# ── Restrictions de permissions ────────────────────────────────────
# Nouveau fichier → 600 (rw-------), nouveau répertoire → 700 (rwx------)
# Protège les données utilisateurs contre les autres comptes OS sur l'hôte.
os.umask(0o077)

app = FastAPI(title="Mnemo Dashboard", docs_url=None, redoc_url=None)

# Serve React build — mounted last so /api/* routes take priority
if STATIC_DIR.exists():
    app.mount("/assets", StaticFiles(directory=str(STATIC_DIR / "assets")), name="assets")


# ── Gestion des utilisateurs ───────────────────────────────────────

def _load_users() -> dict:
    """Charge users.json. Retourne {} si absent."""
    if not USERS_FILE.exists():
        return {}
    try:
        return json.loads(USERS_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _save_users(users: dict) -> None:
    USERS_FILE.parent.mkdir(parents=True, exist_ok=True)
    USERS_FILE.write_text(json.dumps(users, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        USERS_FILE.chmod(0o600)         # rw------- : hash des tokens, sensible
        USERS_FILE.parent.chmod(0o700)  # rwx------ : répertoire /data
    except OSError:
        pass


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _init_user_dir(username: str, user_info: dict) -> Path:
    """Crée le répertoire utilisateur et initialise sa DB si nécessaire."""
    from Mnemo.init_db import init_db, migrate_db
    user_dir = USERS_DIR / username
    user_dir.mkdir(parents=True, exist_ok=True)
    sessions_dir = user_dir / "sessions"
    sessions_dir.mkdir(exist_ok=True)
    # chmod explicite — couvre les répertoires créés avant l'ajout de umask(077)
    try:
        USERS_DIR.chmod(0o700)
        user_dir.chmod(0o700)
        sessions_dir.chmod(0o700)
    except OSError:
        pass
    db_path = user_dir / "memory.db"
    if not db_path.exists():
        init_db(db_path=db_path)
        migrate_db(db_path=db_path)
        try:
            db_path.chmod(0o600)
        except OSError:
            pass
    return user_dir


# ── Dépendance d'authentification ─────────────────────────────────

async def get_current_user(authorization: Annotated[str | None, Header()] = None) -> str:
    """
    Valide le token Bearer, configure le contexte utilisateur (data dir + calendar source)
    et retourne le username. Appelé en dépendance async avant les handlers sync —
    anyio copie le ContextVar dans le thread pool, donc les tools CrewAI voient le bon chemin.
    """
    from Mnemo.context import set_data_dir, set_calendar_source

    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Token manquant (Authorization: Bearer <token>)")

    token = authorization[7:].strip()
    token_hash = _hash_token(token)

    users = _load_users()
    for username, info in users.items():
        if info.get("token_hash") == token_hash:
            user_dir = _init_user_dir(username, info)
            set_data_dir(user_dir)
            if info.get("calendar_source"):
                set_calendar_source(info["calendar_source"])
            return username

    raise HTTPException(status_code=401, detail="Token invalide")


Auth = Annotated[str, Depends(get_current_user)]


# ── Modèles ────────────────────────────────────────────────────────

class MessageRequest(BaseModel):
    message: str
    session_id: str | None = None
    web_confirmed: bool | None = None   # None=premier appel, True=confirmé, False=refusé
    web_query: str | None = None        # query confirmée (renvoyée par le client)


class MessageResponse(BaseModel):
    response: str
    session_id: str
    needs_web_confirm: bool = False
    web_query: str | None = None


# ── Pipeline web (sans confirmation interactive) ───────────────────

_SHELL_BLOCKED = (
    "Les commandes shell ne sont pas disponibles depuis l'interface web. "
    "Utilise le terminal CLI pour les opérations système."
)


def _handle_message_web(
    user_message: str,
    session_id: str,
    web_confirmed: bool | None = None,
    confirmed_web_query: str | None = None,
) -> str | dict:
    """
    Variante de handle_message sans stdin — destinée à l'API web.

    Différences vs handle_message() :
    - route=shell          → refus explicite (sécurité)
    - needs_clarification  → ignoré (pas d'input utilisateur possible)
    - needs_web=True       → retourne {"__web_confirm__": True, "web_query": ...}
                             si web_confirmed is None (premier appel).
                             Si web_confirmed=True  → lance la recherche.
                             Si web_confirmed=False → skip la recherche.
    """
    from Mnemo.main import (
        _parse_eval_json,
        _route_message,
        _detect_shell_intent,
        _detect_calendar_write_intent,
        _detect_note_intent,
        _detect_scheduler_intent,
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

    # Pre-check note : keywords forts → bypass LLM
    if _detect_note_intent(user_message):
        eval_json = {"route": "note", "needs_memory": False, "needs_web": False,
                     "needs_clarification": False, "_web_mode": True}
        print(f"[EVAL] (bypass kw) {json.dumps(eval_json, ensure_ascii=False)}")
        response = _route_message(eval_json, user_message, session_id, temporal_ctx, "")
        update_session_memory(session_id, user_message, response)
        return response

    # Pre-check scheduler : keywords forts → bypass LLM
    kw_scheduler_strong, kw_scheduler_weak = _detect_scheduler_intent(user_message)
    if kw_scheduler_strong:
        eval_json = {"route": "scheduler", "needs_memory": False, "needs_web": False,
                     "needs_clarification": False, "_web_mode": True}
        print(f"[EVAL] (bypass kw) {json.dumps(eval_json, ensure_ascii=False)}")
        response = _route_message(eval_json, user_message, session_id, temporal_ctx, "")
        update_session_memory(session_id, user_message, response)
        return response

    # Pre-check calendar : keywords → force route=calendar sans passer par le LLM
    kw_calendar = _detect_calendar_write_intent(user_message)
    ml_calendar  = ml_route == "calendar" and ml_conf >= 0.92

    if kw_calendar or ml_calendar:
        eval_json = {"route": "calendar", "needs_memory": False, "needs_web": False,
                     "needs_clarification": False, "_web_mode": True}
        print(f"[EVAL] (bypass kw) {json.dumps(eval_json, ensure_ascii=False)}")
        response = _route_message(eval_json, user_message, session_id, temporal_ctx, "")
        update_session_memory(session_id, user_message, response)
        return response

    # EvaluationCrew (LLM) — analyse sémantique complète
    eval_result = EvaluationCrew().crew().kickoff(inputs={
        "user_message": user_message,
        "temporal_context": temporal_ctx,
    })
    eval_json = _parse_eval_json(eval_result.raw.strip())

    # Dernier filet : si le LLM a quand même routé vers shell
    if eval_json.get("route") == "shell":
        return _SHELL_BLOCKED

    # Coercion : web_query non-null implique needs_web = True
    if eval_json.get("web_query") and not eval_json.get("needs_web"):
        eval_json["needs_web"] = True

    # Gestion de la recherche web interactive
    web_context = ""
    if eval_json.get("needs_web") and eval_json.get("web_query"):
        if web_confirmed is None:
            # Premier appel : demander confirmation au client
            return {"__web_confirm__": True, "web_query": eval_json["web_query"]}
        elif web_confirmed:
            # Confirmé : lancer la recherche
            from Mnemo.tools.web_tools import web_search, format_results_for_prompt
            results = web_search(confirmed_web_query or eval_json["web_query"])
            web_context = format_results_for_prompt(results) if results else ""
        else:
            # Refusé : désactiver la recherche
            eval_json["needs_web"] = False
            eval_json["web_query"] = None

    eval_json["needs_clarification"] = False
    eval_json["_web_mode"] = True   # CalendarWriteCrew : auto-confirme sans stdin

    print(f"[EVAL] (LLM) {json.dumps(eval_json, ensure_ascii=False)}")
    response = _route_message(eval_json, user_message, session_id, temporal_ctx, web_context)
    update_session_memory(session_id, user_message, response)
    return response


# ── Routes ─────────────────────────────────────────────────────────

@app.get("/api/health")
async def health():
    return {"status": "ok", "data_path": str(DATA_PATH)}


@app.post("/api/message", response_model=MessageResponse)
def message(req: MessageRequest, _: Auth):
    """
    Envoie un message à Mnemo — exécuté en thread pool (def, pas async def)
    pour ne pas bloquer la boucle d'événements pendant l'appel LLM.
    """
    sid = req.session_id or f"web_{uuid.uuid4().hex[:12]}"
    try:
        result = _handle_message_web(req.message, sid, req.web_confirmed, req.web_query)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    if isinstance(result, dict) and result.get("__web_confirm__"):
        return {"response": "", "session_id": sid,
                "needs_web_confirm": True, "web_query": result["web_query"]}
    return {"response": result, "session_id": sid}


@app.get("/api/memory")
async def memory(_: Auth):
    from Mnemo.context import get_data_dir
    memory_file = get_data_dir() / "memory.md"
    if not memory_file.exists():
        return {"content": "", "sections": [], "preamble": ""}

    content = memory_file.read_text(encoding="utf-8")

    sections: list[dict] = []
    preamble_lines: list[str] = []
    preamble_done = False
    current: dict | None = None

    for line in content.splitlines():
        if line.startswith("## "):
            preamble_done = True
            if current is not None:
                sections.append(current)
            current = {"title": line[3:].strip(), "content": ""}
        elif current is not None:
            current["content"] += line + "\n"
        elif not preamble_done:
            preamble_lines.append(line)

    if current is not None:
        sections.append(current)

    # Trim trailing newlines from section content for cleaner editing
    for s in sections:
        s["content"] = s["content"].rstrip("\n")

    preamble = "\n".join(preamble_lines).rstrip("\n")
    return {"content": content, "sections": sections, "preamble": preamble}


class MemoryWriteRequest(BaseModel):
    content: str


@app.post("/api/memory")
def memory_write(req: MemoryWriteRequest, _: Auth):
    """
    Écrit le contenu dans memory.md puis synchronise la DB SQLite.
    Exécuté en thread pool (def, pas async) car sync_markdown_to_db peut être lent.
    """
    try:
        from Mnemo.context import get_data_dir
        from Mnemo.tools.memory_tools import sync_markdown_to_db
        (get_data_dir() / "memory.md").write_text(req.content, encoding="utf-8")
        sync_markdown_to_db()
        return {"ok": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/sessions")
async def sessions(_: Auth):
    from Mnemo.context import get_data_dir
    sessions_dir = get_data_dir() / "sessions"
    if not sessions_dir.exists():
        return {"sessions": []}

    result = []
    for f in sorted(
        sessions_dir.glob("*.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    ):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            messages = data.get("messages", [])
            result.append({
                "id": f.stem,
                "message_count": len(messages),
                "done": (sessions_dir / f"{f.stem}.done").exists(),
                "modified": f.stat().st_mtime,
                "preview": (
                    next(
                        (m.get("content", "")[:100] for m in messages if m.get("role") == "user"),
                        ""
                    )
                    if messages else ""
                ),
            })
        except (json.JSONDecodeError, OSError):
            continue

    return {"sessions": result}


@app.get("/api/sessions/{session_id}")
async def session_detail(session_id: str, _: Auth):
    from Mnemo.context import get_data_dir
    if any(c in session_id for c in ("/", "\\", "..")):
        raise HTTPException(status_code=400, detail="Invalid session ID")

    path = get_data_dir() / "sessions" / f"{session_id}.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Session not found")

    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        raise HTTPException(status_code=500, detail="Corrupted session file")


def _serialize_events(events: list) -> list:
    """Sérialise les objets date/datetime Python en ISO string."""
    result = []
    for e in events:
        s = dict(e)
        s["date"]     = e["date"].isoformat()     if e.get("date")     else None
        s["datetime"] = e["datetime"].isoformat() if e.get("datetime") else None
        result.append(s)
    return result


@app.get("/api/calendar")
async def calendar_list(_: Auth):
    try:
        from datetime import date, timedelta
        from Mnemo.tools.calendar_tools import get_events_with_uid, calendar_is_writable
        today = date.today()
        week_start = today - timedelta(days=today.weekday())  # lundi de la semaine courante
        events = get_events_with_uid(days=60, from_date=week_start)
        return {
            "events": _serialize_events(events),
            "writable": calendar_is_writable(),
        }
    except Exception:
        return {"events": [], "writable": False}


class EventCreateRequest(BaseModel):
    title: str
    date: str                    # YYYY-MM-DD
    time: str | None = None      # HH:MM
    duration_minutes: int = 60
    location: str | None = None
    description: str | None = None


class EventUpdateRequest(BaseModel):
    title: str | None = None
    date: str | None = None
    time: str | None = None
    duration_minutes: int | None = None
    location: str | None = None
    description: str | None = None


@app.post("/api/calendar", status_code=201)
def calendar_create(req: EventCreateRequest, _: Auth):
    try:
        from Mnemo.tools.calendar_tools import add_event, calendar_is_writable
        if not calendar_is_writable():
            raise HTTPException(status_code=403, detail="Calendrier en lecture seule (URL distante).")
        uid = add_event(
            title            = req.title,
            date_iso         = req.date,
            time_str         = req.time,
            duration_minutes = req.duration_minutes,
            location         = req.location,
            description      = req.description,
        )
        return {"uid": uid}
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.put("/api/calendar/{event_uid}")
def calendar_update(event_uid: str, req: EventUpdateRequest, _: Auth):
    if any(c in event_uid for c in ("/", "\\", "..")):
        raise HTTPException(status_code=400, detail="UID invalide.")
    try:
        from Mnemo.tools.calendar_tools import update_event, calendar_is_writable
        if not calendar_is_writable():
            raise HTTPException(status_code=403, detail="Calendrier en lecture seule (URL distante).")
        fields = {k: v for k, v in req.model_dump().items() if v is not None}
        ok = update_event(event_uid, **fields)
        if not ok:
            raise HTTPException(status_code=404, detail="Événement introuvable.")
        return {"ok": True}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/calendar/{event_uid}")
def calendar_delete(event_uid: str, _: Auth):
    if any(c in event_uid for c in ("/", "\\", "..")):
        raise HTTPException(status_code=400, detail="UID invalide.")
    try:
        from Mnemo.tools.calendar_tools import delete_event, calendar_is_writable
        if not calendar_is_writable():
            raise HTTPException(status_code=403, detail="Calendrier en lecture seule (URL distante).")
        ok = delete_event(event_uid)
        if not ok:
            raise HTTPException(status_code=404, detail="Événement introuvable.")
        return {"ok": True}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/reminders")
async def reminders(_: Auth):
    """
    Retourne les rappels présents dans briefing.md (sections ## 🔔 Rappel).
    Chaque rappel a un `id` (MD5 du message) pour dédupliquer côté client.
    """
    from Mnemo.context import get_data_dir
    briefing_file = get_data_dir() / "briefing.md"
    if not briefing_file.exists():
        return {"reminders": []}

    content = briefing_file.read_text(encoding="utf-8")
    items: list[dict] = []

    # Découpe sur chaque en-tête ## 🔔 Rappel
    parts = re.split(r"^## 🔔 Rappel\s*$", content, flags=re.MULTILINE)
    for part in parts[1:]:
        # Prend le texte jusqu'au prochain ## ou fin de fichier
        body = re.split(r"^##", part, maxsplit=1, flags=re.MULTILINE)[0].strip()
        if body:
            rid = hashlib.md5(body.encode()).hexdigest()[:12]
            items.append({"id": rid, "message": body})

    return {"reminders": items}


# ── Gestion des utilisateurs (admin) ──────────────────────────────

class UserCreateRequest(BaseModel):
    username: str
    calendar_source: str = ""


@app.post("/api/users", status_code=201)
async def create_user(
    req: UserCreateRequest,
    authorization: Annotated[str | None, Header()] = None,
):
    """
    Crée un utilisateur. Protégé par MNEMO_ADMIN_TOKEN (pas par le token utilisateur).
    Retourne le token en clair une seule fois — non récupérable ensuite.
    """
    admin_token = os.getenv("MNEMO_ADMIN_TOKEN", "")
    if not admin_token or authorization != f"Bearer {admin_token}":
        raise HTTPException(status_code=403, detail="MNEMO_ADMIN_TOKEN requis")

    username = req.username.strip()
    if not username or not re.match(r"^[a-zA-Z0-9_-]{1,64}$", username):
        raise HTTPException(status_code=400, detail="Nom invalide (alphanumérique, 1-64 caractères)")

    users = _load_users()
    if username in users:
        raise HTTPException(status_code=409, detail="Utilisateur déjà existant")

    token = f"mnemo_{secrets.token_hex(32)}"
    users[username] = {
        "token_hash": _hash_token(token),
        "calendar_source": req.calendar_source,
        "created_at": datetime.now().isoformat(),
    }
    _save_users(users)
    _init_user_dir(username, users[username])
    return {"username": username, "token": token}


@app.get("/api/auth/whoami")
async def whoami(username: Auth):
    users = _load_users()
    info = users.get(username, {})
    return {
        "username": username,
        "calendar_source": info.get("calendar_source", ""),
        "created_at": info.get("created_at"),
    }


# ── WebSocket streaming ────────────────────────────────────────────

@app.websocket("/ws/message")
async def ws_message(websocket: WebSocket):
    """
    WebSocket endpoint pour le streaming de réponses token par token.

    Protocole client → serveur :
      {"type": "auth",       "token": "mnemo_..."}
      {"type": "message",    "message": "...", "session_id": "..."}
      {"type": "web_answer", "confirmed": bool, "web_query": "...",
                             "session_id": "...", "original_message": "..."}

    Protocole serveur → client :
      {"type": "auth_ok",    "username": "..."}
      {"type": "thinking"}
      {"type": "token",      "text": "word "}
      {"type": "done",       "session_id": "..."}
      {"type": "web_confirm","web_query": "...", "session_id": "...", "original_message": "..."}
      {"type": "error",      "detail": "..."}
    """
    from Mnemo.context import set_data_dir, set_calendar_source

    await websocket.accept()

    # Auth handshake
    try:
        auth_msg = await asyncio.wait_for(websocket.receive_json(), timeout=10.0)
    except Exception:
        await websocket.close(code=4001)
        return

    if auth_msg.get("type") != "auth":
        await websocket.send_json({"type": "error", "detail": "Authentification requise"})
        await websocket.close(code=4001)
        return

    token = auth_msg.get("token", "")
    token_hash = _hash_token(token)
    users = _load_users()
    username: str | None = None
    user_info: dict | None = None
    for u, info in users.items():
        if info.get("token_hash") == token_hash:
            username = u
            user_info = info
            break

    if not username or user_info is None:
        await websocket.send_json({"type": "error", "detail": "Token invalide"})
        await websocket.close(code=4003)
        return

    user_dir = _init_user_dir(username, user_info)
    set_data_dir(user_dir)
    if user_info.get("calendar_source"):
        set_calendar_source(user_info["calendar_source"])

    # Capture context après set_data_dir — propagé dans run_in_executor
    user_context = contextvars.copy_context()

    await websocket.send_json({"type": "auth_ok", "username": username})

    loop = asyncio.get_event_loop()

    async def _run_and_stream(
        text: str,
        sid: str,
        web_confirmed: bool | None = None,
        web_query: str | None = None,
    ) -> None:
        await websocket.send_json({"type": "thinking"})
        try:
            result = await loop.run_in_executor(
                None,
                lambda: user_context.run(
                    _handle_message_web, text, sid, web_confirmed, web_query
                ),
            )
        except Exception as e:
            await websocket.send_json({"type": "error", "detail": str(e)})
            return

        if isinstance(result, dict) and result.get("__web_confirm__"):
            await websocket.send_json({
                "type": "web_confirm",
                "web_query": result["web_query"],
                "session_id": sid,
                "original_message": text,
            })
            return

        words = (result or "").split(" ")
        for i, word in enumerate(words):
            chunk = word if i == len(words) - 1 else word + " "
            await websocket.send_json({"type": "token", "text": chunk})
            await asyncio.sleep(0.012)

        await websocket.send_json({"type": "done", "session_id": sid})

    try:
        while True:
            msg = await websocket.receive_json()
            msg_type = msg.get("type")

            if msg_type == "message":
                text = msg.get("message", "")
                sid  = msg.get("session_id") or f"web_{uuid.uuid4().hex[:12]}"
                await _run_and_stream(text, sid)

            elif msg_type == "web_answer":
                original  = msg.get("original_message", "")
                sid       = msg.get("session_id") or f"web_{uuid.uuid4().hex[:12]}"
                confirmed = bool(msg.get("confirmed", False))
                wq        = msg.get("web_query") if confirmed else None
                await _run_and_stream(original, sid, confirmed, wq)

    except WebSocketDisconnect:
        pass


# ── SPA catch-all — DOIT être en dernier pour ne pas écraser /api/* ──
# FastAPI matche les routes dans l'ordre de définition.
# Ce catch-all doit être après toutes les routes /api/*.

@app.get("/", response_class=HTMLResponse)
@app.get("/{full_path:path}", response_class=HTMLResponse)
async def spa(full_path: str = ""):
    """Serve React SPA — catch-all for client-side routing."""
    html_file = STATIC_DIR / "index.html"
    if html_file.exists():
        return HTMLResponse(html_file.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>Dashboard unavailable — run: cd frontend && npm run build</h1>", status_code=503)


# ── Point d'entrée local ───────────────────────────────────────────

def run():
    """Lance le serveur de dashboard en mode développement."""
    import uvicorn
    uvicorn.run("Mnemo.api:app", host="0.0.0.0", port=8000, reload=True)
