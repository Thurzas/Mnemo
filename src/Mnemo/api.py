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
import logging
import os
import re
import secrets
import uuid
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, File, Header, HTTPException, Response, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# Import anticipé de main.py — son code module-level appelle _set_data_dir("/data")
# ce qui est inoffensif ici (contexte startup = root context, isolé des contextes
# utilisateurs). Sans ça, l'import se déclencherait à l'intérieur de
# user_context.run(...) et écraserait le user_dir avec /data.
import Mnemo.main as _mnemo_main  # noqa: F401

DATA_PATH  = Path(os.getenv("DATA_PATH", "/data")).resolve()
USERS_FILE = DATA_PATH / "users.json"
USERS_DIR  = DATA_PATH / "users"
STATIC_DIR = Path(__file__).parent / "static"

# ── Restrictions de permissions ────────────────────────────────────
# Nouveau fichier → 600 (rw-------), nouveau répertoire → 700 (rwx------)
# Protège les données utilisateurs contre les autres comptes OS sur l'hôte.
os.umask(0o077)

log = logging.getLogger(__name__)

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


# ── Pipeline web (sans confirmation interactive) ─��─────────────────

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
    Utilise la même CoR que le CLI (build_router) avec des adaptations web :
    - route=shell          → refus explicite (sécurité, pas de confirmation possible)
    - needs_clarification  → ignoré
    - needs_web=True       → retourne {"__web_confirm__": True, "web_query": ...}
                             si web_confirmed is None (premier appel).
                             Si web_confirmed=True  → lance la recherche.
                             Si web_confirmed=False → skip la recherche.
    """
    from Mnemo.routing import build_router, RouterContext, dispatch
    from Mnemo.routing.handlers.keyword import _detect_shell_intent
    from Mnemo.routing.context import RouterResult
    from Mnemo.tools.memory_tools import update_session_memory
    from Mnemo.tools.calendar_tools import get_temporal_context

    temporal_ctx = get_temporal_context()

    # Sécurité rapide : keywords shell → refus immédiat sans passer par la CoR
    if _detect_shell_intent(user_message):
        return _SHELL_BLOCKED

    # Routing via la chaîne de responsabilité
    ctx    = RouterContext(message=user_message, session_id=session_id, temporal_context=temporal_ctx)
    router = build_router()
    result = router.handle(ctx)

    if result is None:
        result = RouterResult("conversation", 0.0, "fallback")

    # API : shell toujours bloqué, même si le ML/LLM l'a choisi
    if result.route == "shell":
        return _SHELL_BLOCKED

    meta = result.metadata

    # Coercion : web_query non-null implique needs_web = True
    if meta.get("web_query") and not meta.get("needs_web"):
        meta["needs_web"] = True

    # Gestion de la confirmation web interactive (remplace stdin)
    web_context = ""
    if meta.get("needs_web") and meta.get("web_query"):
        if web_confirmed is None:
            return {"__web_confirm__": True, "web_query": meta["web_query"]}
        elif web_confirmed:
            from Mnemo.tools.web_tools import (
                web_search, format_results_for_prompt,
                fetch_page_content, extract_relevant_links, save_web_page,
            )
            import Mnemo.status as _status_mod_web
            query = confirmed_web_query or meta["web_query"]

            _status_mod_web.emit(session_id, f"Recherche : {query}…")
            results = web_search(query)
            web_context = format_results_for_prompt(results) if results else ""

            # Deep fetch — récupère le contenu complet des résultats + liens
            suggestions: list[dict] = []
            for r in results[:2]:   # max 2 pages fetchées pour limiter le temps
                page_url = r.get("url", "")
                if not page_url:
                    continue
                _status_mod_web.emit(session_id, f"Lecture de la page : {r.get('title', page_url)[:50]}…")
                page = fetch_page_content(page_url)
                if page.get("error") or not page.get("text"):
                    continue
                saved = save_web_page(page["text"], page["title"] or r["title"], page_url, query)
                if saved:
                    _status_mod_web.emit(session_id, f"Sauvegardé : {saved.name}")
                # Liens pertinents
                links = page.get("links", [])
                page_suggestions = extract_relevant_links(links, query, threshold=0.25, max_n=3)
                suggestions.extend(page_suggestions)

            # Déduplique + trie les suggestions globales
            seen: set[str] = set()
            deduped: list[dict] = []
            for s in sorted(suggestions, key=lambda x: x["score"], reverse=True):
                if s["url"] not in seen:
                    seen.add(s["url"])
                    deduped.append(s)
            suggestions = deduped[:4]

            # Stocke les suggestions dans world_state pour le WS handler
            if suggestions:
                from Mnemo.tools.memory_tools import load_world_state
                from Mnemo.context import get_data_dir as _gdd
                ws = load_world_state()
                ws["pending_web_suggestions"] = suggestions
                (_gdd() / "world_state.json").write_text(
                    json.dumps(ws, ensure_ascii=False, indent=2), encoding="utf-8"
                )
        else:
            meta["needs_web"] = False
            meta["web_query"] = None

    meta["needs_clarification"] = False
    meta["_web_mode"] = True   # CalendarWriteCrew : auto-confirme sans stdin

    import Mnemo.status as _status_mod

    _status_mod.emit(session_id, "Analyse de la demande...")

    print(f"[EVAL] ({result.handler}) {json.dumps({'route': result.route, 'conf': round(result.confidence, 2), **meta}, ensure_ascii=False)}")

    conf_pct = round(result.confidence * 100)
    _status_mod.emit(session_id, f"Route : {result.route} · {result.handler} ({conf_pct}%)")

    response = dispatch(result, user_message=user_message, session_id=session_id,
                        temporal_ctx=temporal_ctx, web_context=web_context)
    update_session_memory(session_id, user_message, response)

    # Persiste le log pipeline en session pour enrichir la consolidation
    logs = _status_mod.flush_session_log(session_id)
    if logs:
        from Mnemo.tools.memory_tools import append_session_message
        append_session_message(session_id, {
            "role": "system",
            "content": "[pipeline] " + " · ".join(logs),
        })

    # Déclenchement immédiat du plan — PlannerCrew a écrit pending_plan_step (et
    # éventuellement pending_web_search) dans world_state.
    if result.route == "plan":
        try:
            from Mnemo.tools.memory_tools import load_world_state
            from Mnemo.context import get_data_dir
            ws = load_world_state()
            pending     = ws.pop("pending_plan_step", None)
            pending_web = ws.pop("pending_web_search", None)
            if pending or pending_web:
                path = get_data_dir() / "world_state.json"
                path.write_text(
                    json.dumps(ws, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                if pending_web:
                    # Phase 6.5 — étape 1 est une recherche web : demande confirmation
                    return {
                        "__web_confirm__": True,
                        "web_query":       pending_web["query"],
                        "_plan_web":       pending_web,
                        "response":        response,   # texte du plan (affiché avant le modal)
                    }
                return {"__plan_created__": True, "response": response, "plan_step": pending}
        except Exception:
            pass

    # Suggestions de liens (deep fetch après search confirmée)
    try:
        from Mnemo.tools.memory_tools import load_world_state
        from Mnemo.context import get_data_dir
        ws = load_world_state()
        suggestions = ws.pop("pending_web_suggestions", None)
        if suggestions:
            (get_data_dir() / "world_state.json").write_text(
                json.dumps(ws, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            return {"__web_suggestions__": True, "response": response, "suggestions": suggestions}
    except Exception:
        pass

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


@app.post("/api/calendar/import", status_code=200)
async def calendar_import(file: UploadFile, _: Auth):
    """
    Importe un fichier ICS : fusionne les VEVENTs dans le calendrier local.
    Les événements dont l'UID existe déjà sont ignorés (pas de doublon).
    Retourne {"imported": N, "skipped": M}.
    """
    try:
        from Mnemo.tools.calendar_tools import (
            calendar_is_writable, _load_writable_calendar, _save_calendar
        )
        from icalendar import Calendar as _Calendar
    except ImportError as e:
        raise HTTPException(status_code=500, detail=f"icalendar non disponible : {e}")

    if not calendar_is_writable():
        raise HTTPException(status_code=403, detail="Calendrier en lecture seule (URL distante).")

    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="Fichier vide.")

    try:
        imported_cal = _Calendar.from_ical(raw)
    except Exception:
        raise HTTPException(status_code=400, detail="Fichier ICS invalide.")

    local_cal = _load_writable_calendar()

    # Collecte les UIDs déjà présents
    existing_uids: set[str] = {
        str(c.get("UID", ""))
        for c in local_cal.walk()
        if c.name == "VEVENT"
    }

    imported = 0
    skipped = 0
    for component in imported_cal.walk():
        if component.name != "VEVENT":
            continue
        uid = str(component.get("UID", ""))
        if uid and uid in existing_uids:
            skipped += 1
            continue
        local_cal.add_component(component)
        existing_uids.add(uid)
        imported += 1

    if imported > 0:
        _save_calendar(local_cal)

    return {"imported": imported, "skipped": skipped}


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


_ONBOARDING_QUESTIONS = [
    {"id": "name",     "question": "Comment tu t'appelles ?",
     "section": "🧑 Identité Utilisateur", "subsection": "Profil de base",       "label": "Nom/Pseudo"},
    {"id": "job",      "question": "Quelle est ta profession ou ton domaine d'activité ?",
     "section": "🧑 Identité Utilisateur", "subsection": "Profil de base",       "label": "Métier"},
    {"id": "location", "question": "Dans quelle ville ou pays tu te trouves ?",
     "section": "🧑 Identité Utilisateur", "subsection": "Profil de base",       "label": "Localisation"},
    {"id": "style",    "question": "Tu préfères des réponses courtes et directes, ou détaillées ?",
     "section": "🧑 Identité Utilisateur", "subsection": "Préférences & style",  "label": "Style de communication"},
    {"id": "agent",    "question": "Comment tu veux appeler l'agent ? Il a un nom ?",
     "section": "🧑 Identité Agent",       "subsection": "Rôle & personnalité définis", "label": "Nom de l'agent"},
]


@app.get("/api/onboarding/status")
async def onboarding_status(_: Auth):
    """
    Première connexion = memory.md absent ou vide.
    Dans ce cas, retourne les 5 questions d'initialisation.
    """
    from Mnemo.context import get_data_dir

    memory_file = get_data_dir() / "memory.md"
    is_empty    = (
        not memory_file.exists()
        or not memory_file.read_text(encoding="utf-8", errors="ignore").strip()
    )

    return {
        "needed":    is_empty,
        "questions": _ONBOARDING_QUESTIONS if is_empty else [],
    }


class OnboardingAnswerItem(BaseModel):
    id: str
    answer: str
    section: str
    subsection: str
    label: str


class OnboardingSubmitRequest(BaseModel):
    answers: list[OnboardingAnswerItem]


@app.post("/api/onboarding")
def onboarding_submit(req: OnboardingSubmitRequest, _: Auth):
    """
    Écrit les réponses non vides dans memory.md et synchronise la DB.
    """
    from Mnemo.tools.memory_tools import update_markdown_section, sync_markdown_to_db

    written = 0
    for ans in req.answers:
        text = ans.answer.strip()
        if not text:
            continue
        content = f"- **{ans.label}** : {text}" if ans.label else text
        update_markdown_section(
            section    = ans.section,
            subsection = ans.subsection,
            content    = content,
            category   = "identité",
        )
        written += 1

    if written:
        sync_markdown_to_db()

    return {"ok": True, "written": written}


@app.get("/api/auth/whoami")
async def whoami(username: Auth):
    users = _load_users()
    info = users.get(username, {})
    return {
        "username": username,
        "calendar_source": info.get("calendar_source", ""),
        "created_at": info.get("created_at"),
    }


# ── STT / TTS ─────────────────────────────────────────────────────

@app.post("/api/stt")
async def speech_to_text(
    username: Auth,
    file: UploadFile = File(...),
):
    """
    Transcrit un fichier audio (webm, wav, mp3…) en texte.
    Nécessite faster-whisper installé (sinon 503).
    """
    try:
        from Mnemo.tools.audio_tools import transcribe_audio  # noqa: PLC0415
    except ImportError:
        raise HTTPException(503, "Module STT non disponible — installez faster-whisper")

    audio_bytes = await file.read()
    loop = asyncio.get_running_loop()
    try:
        text = await loop.run_in_executor(None, lambda: transcribe_audio(audio_bytes))
    except Exception as exc:
        raise HTTPException(500, f"Erreur STT : {exc}") from exc
    return {"text": text}


class TTSRequest(BaseModel):
    text: str


@app.post("/api/tts")
async def text_to_speech(
    req: TTSRequest,
    username: Auth,
):
    """
    Synthétise `text` en audio WAV.
    Nécessite kokoro installé (sinon 503).
    Retourne audio/wav (bytes).
    """
    try:
        from Mnemo.tools.audio_tools import synthesize_speech  # noqa: PLC0415
    except Exception as exc:
        raise HTTPException(503, f"Module TTS non disponible : {exc}")

    loop = asyncio.get_running_loop()
    try:
        wav_bytes = await loop.run_in_executor(None, lambda: synthesize_speech(req.text))
    except Exception as exc:
        import logging as _log
        _log.getLogger(__name__).error("TTS synthesis failed: %s", exc, exc_info=True)
        raise HTTPException(500, f"Erreur TTS : {exc}") from exc
    if not wav_bytes:
        raise HTTPException(500, "TTS a retourné des bytes vides")
    return Response(content=wav_bytes, media_type="audio/wav")


# ── Voix — paramétrage UI ──────────────────────────────────────────

_VOICE_SETTINGS_FILE = DATA_PATH / "voice_settings.json"
_RVC_MODELS_DIR = DATA_PATH / "models" / "rvc"


def _list_rvc_models() -> list[dict]:
    """Liste les modèles .pth disponibles dans DATA_PATH/models/rvc/."""
    if not _RVC_MODELS_DIR.exists():
        return []
    models = []
    for pth in sorted(_RVC_MODELS_DIR.glob("*.pth")):
        stem = pth.stem
        index_file = _RVC_MODELS_DIR / f"{stem}.index"
        models.append({
            "name":  stem,
            "pth":   pth.name,
            "index": index_file.name if index_file.exists() else None,
        })
    return models


def _load_voice_settings_on_startup() -> None:
    """Charge voice_settings.json au démarrage de l'API (si présent)."""
    import json
    try:
        from Mnemo.tools.audio_tools import apply_voice_settings
        if _VOICE_SETTINGS_FILE.exists():
            apply_voice_settings(json.loads(_VOICE_SETTINGS_FILE.read_text()))
            log.info("Paramètres voix chargés depuis %s", _VOICE_SETTINGS_FILE)
    except Exception as exc:
        log.warning("Impossible de charger voice_settings.json : %s", exc)


# Charge les settings au démarrage du module (uvicorn import)
_load_voice_settings_on_startup()


class VoiceSettingsRequest(BaseModel):
    rvc_enabled:       bool  | None = None
    kokoro_voice_fr:   str   | None = None
    kokoro_voice_ja:   str   | None = None
    kokoro_speed:      float | None = None
    rvc_f0_method:     str   | None = None
    rvc_f0_up_key:     int   | None = None
    rvc_index_rate:    float | None = None
    rvc_filter_radius: int   | None = None
    rvc_rms_mix_rate:  float | None = None
    rvc_protect:       float | None = None


@app.get("/api/voice/settings")
async def voice_settings_get(_: Auth):
    """Retourne les paramètres voix actifs + les voix disponibles."""
    from Mnemo.tools.audio_tools import (  # noqa: PLC0415
        get_voice_settings, KOKORO_VOICES_FR, KOKORO_VOICES_JA,
    )
    s = get_voice_settings()
    s["available_voices_fr"] = KOKORO_VOICES_FR
    s["available_voices_ja"] = KOKORO_VOICES_JA
    s["rvc_service_url"]     = os.getenv("RVC_SERVICE_URL") or None
    s["available_models"]    = _list_rvc_models()
    return s


@app.post("/api/voice/settings")
async def voice_settings_post(req: VoiceSettingsRequest, _: Auth):
    """Met à jour les paramètres voix et les persiste dans voice_settings.json."""
    import json
    from Mnemo.tools.audio_tools import (  # noqa: PLC0415
        get_voice_settings, apply_voice_settings, KOKORO_VOICES_FR, KOKORO_VOICES_JA,
    )
    update = {k: v for k, v in req.model_dump().items() if v is not None}
    apply_voice_settings(update)
    current = get_voice_settings()
    try:
        _VOICE_SETTINGS_FILE.write_text(json.dumps(current, indent=2))
    except Exception as exc:
        log.warning("Impossible de persister voice_settings.json : %s", exc)
    current["available_voices_fr"] = KOKORO_VOICES_FR
    current["available_voices_ja"] = KOKORO_VOICES_JA
    current["rvc_service_url"]     = os.getenv("RVC_SERVICE_URL") or None
    current["available_models"]    = _list_rvc_models()
    return current


class VoiceTestRequest(BaseModel):
    text:              str   | None = None
    # Paramètres optionnels — appliqués au runtime sans persistance
    rvc_enabled:       bool  | None = None
    kokoro_voice_fr:   str   | None = None
    kokoro_voice_ja:   str   | None = None
    kokoro_speed:      float | None = None
    rvc_f0_method:     str   | None = None
    rvc_f0_up_key:     int   | None = None
    rvc_index_rate:    float | None = None
    rvc_filter_radius: int   | None = None
    rvc_rms_mix_rate:  float | None = None
    rvc_protect:       float | None = None
    rvc_active_model:  str   | None = None


@app.post("/api/voice/test")
async def voice_test(req: VoiceTestRequest, _: Auth):
    """
    Synthétise une phrase de test.
    Si des paramètres sont fournis dans le body, ils sont appliqués au runtime
    avant la synthèse (sans persistance sur disque) — permet de tester sans sauvegarder.
    """
    try:
        from Mnemo.tools.audio_tools import apply_voice_settings, synthesize_speech  # noqa: PLC0415
    except Exception as exc:
        raise HTTPException(503, f"Module TTS non disponible : {exc}")

    # Applique les settings du form au runtime (profil intermédiaire, non persisté)
    patch = {k: v for k, v in req.model_dump().items() if k != "text" and v is not None}
    if patch:
        apply_voice_settings(patch)

    phrase = req.text or "Bonjour, je suis ta voix personnalisée."
    loop = asyncio.get_running_loop()
    try:
        wav = await loop.run_in_executor(None, lambda: synthesize_speech(phrase))
    except Exception as exc:
        raise HTTPException(500, f"Erreur test voix : {exc}") from exc
    if not wav:
        raise HTTPException(500, "Test voix retourné vide")
    return Response(content=wav, media_type="audio/wav")


@app.post("/api/voice/model", status_code=201)
def voice_model_upload(
    pth_file:   UploadFile,
    _: Auth,
    index_file: UploadFile | None = None,
):
    """
    Upload un modèle RVC (.pth requis, .index optionnel).
    Sauvegarde dans DATA_PATH/models/rvc/.
    Retourne {"name": stem, "pth": filename, "index": filename|null}.
    """
    if not pth_file.filename or not pth_file.filename.endswith(".pth"):
        raise HTTPException(400, "Fichier .pth requis")
    if index_file and index_file.filename and not index_file.filename.endswith(".index"):
        raise HTTPException(400, "Le fichier index doit avoir l'extension .index")

    _RVC_MODELS_DIR.mkdir(parents=True, exist_ok=True)

    # Sécuriser les noms de fichier (pas de path traversal)
    import re as _re
    safe_pth   = _re.sub(r"[^\w.\-]", "_", pth_file.filename)
    stem       = safe_pth[:-4]  # sans ".pth"

    pth_dest = _RVC_MODELS_DIR / safe_pth
    pth_data = pth_file.file.read()
    if not pth_data:
        raise HTTPException(400, "Fichier .pth vide")
    pth_dest.write_bytes(pth_data)

    index_dest = None
    if index_file and index_file.filename:
        safe_idx   = _re.sub(r"[^\w.\-]", "_", index_file.filename)
        index_dest = _RVC_MODELS_DIR / safe_idx
        idx_data   = index_file.file.read()
        if idx_data:
            index_dest.write_bytes(idx_data)
        else:
            index_dest = None

    log.info("Modèle RVC uploadé : %s (index=%s)", safe_pth, index_dest)
    return {
        "name":  stem,
        "pth":   safe_pth,
        "index": index_dest.name if index_dest else None,
    }


@app.post("/api/voice/model/{model_name}/activate")
async def voice_model_activate(model_name: str, _: Auth):
    """
    Active un modèle RVC uploadé en appelant /reload sur le service RVC.
    Persiste le modèle actif dans voice_settings.json.
    """
    import json

    # Validation nom
    import re as _re
    if not _re.match(r"^[\w.\-]+$", model_name):
        raise HTTPException(400, "Nom de modèle invalide")

    pth_path   = _RVC_MODELS_DIR / f"{model_name}.pth"
    index_path = _RVC_MODELS_DIR / f"{model_name}.index"
    if not pth_path.exists():
        raise HTTPException(404, f"Modèle '{model_name}.pth' introuvable")

    rvc_url = os.getenv("RVC_SERVICE_URL", "").rstrip("/")
    if rvc_url:
        # Les chemins dans le container RVC sont sous /models/
        rvc_model_path = f"/models/{pth_path.name}"
        rvc_index_path = f"/models/{index_path.name}" if index_path.exists() else ""
        qs = f"?model_path={urllib.parse.quote(rvc_model_path)}"
        if rvc_index_path:
            qs += f"&index_path={urllib.parse.quote(rvc_index_path)}"
        req = urllib.request.Request(
            f"{rvc_url}/reload{qs}",
            data=b"",
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                result = json.loads(resp.read())
        except Exception as exc:
            raise HTTPException(500, f"Erreur reload RVC : {exc}") from exc

    # Persiste le modèle actif
    from Mnemo.tools.audio_tools import apply_voice_settings  # noqa: PLC0415
    apply_voice_settings({"rvc_active_model": model_name})
    try:
        current_settings = json.loads(_VOICE_SETTINGS_FILE.read_text()) if _VOICE_SETTINGS_FILE.exists() else {}
        current_settings["rvc_active_model"] = model_name
        _VOICE_SETTINGS_FILE.write_text(json.dumps(current_settings, indent=2))
    except Exception as exc:
        log.warning("Impossible de persister rvc_active_model : %s", exc)

    return {"ok": True, "active_model": model_name}


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

    # Consolide les sessions orphelines de la connexion précédente (pas de .done)
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(
        None,
        lambda: user_context.run(_mnemo_main.consolidate_orphan_sessions)
    )

    await websocket.send_json({"type": "auth_ok", "username": username})

    async def _safe_send(payload: dict) -> bool:
        """Envoie un message JSON — retourne False si la connexion est fermée."""
        try:
            await websocket.send_json(payload)
            return True
        except Exception:
            return False

    async def _run_and_stream(
        text: str,
        sid: str,
        web_confirmed: bool | None = None,
        web_query: str | None = None,
    ) -> None:
        import Mnemo.status as _status_mod

        # Enregistre la queue avant de lancer le thread
        status_queue: asyncio.Queue = asyncio.Queue()
        _status_mod.set_session(sid, status_queue, loop)

        if not await _safe_send({"type": "thinking"}):
            return

        # Lance handle_message dans le thread pool (non-bloquant)
        fut = loop.run_in_executor(
            None,
            lambda: user_context.run(
                _handle_message_web, text, sid, web_confirmed, web_query
            ),
        )

        # Draine la queue de statut en parallèle du thread
        while not fut.done():
            try:
                event = status_queue.get_nowait()
                if not await _safe_send(event):
                    return
            except asyncio.QueueEmpty:
                await asyncio.sleep(0.03)

        # Draine les événements émis juste avant que fut.done() devienne True
        while not status_queue.empty():
            if not await _safe_send(status_queue.get_nowait()):
                return

        _status_mod.clear_session(sid)

        try:
            result = fut.result()
        except Exception as e:
            await _safe_send({"type": "error", "detail": str(e)})
            return

        if isinstance(result, dict) and result.get("__web_confirm__"):
            # Si un texte de plan est présent (Phase 6.5), on le stream d'abord
            plan_text = result.get("response", "")
            plan_web  = result.get("_plan_web")
            if plan_text:
                words = plan_text.split(" ")
                for i, word in enumerate(words):
                    chunk = word if i == len(words) - 1 else word + " "
                    if not await _safe_send({"type": "token", "text": chunk}):
                        return
                    await asyncio.sleep(0.012)
                if not await _safe_send({"type": "done", "session_id": sid}):
                    return
            await _safe_send({
                "type":             "web_confirm",
                "web_query":        result["web_query"],
                "session_id":       sid,
                "original_message": text,
                **({"plan_web": plan_web} if plan_web else {}),
            })
            return

        # Déclenchement immédiat du plan — extrait avant streaming
        plan_step    = None
        web_suggests = None
        if isinstance(result, dict) and result.get("__plan_created__"):
            plan_step = result.get("plan_step")
            result    = result["response"]
        elif isinstance(result, dict) and result.get("__web_suggestions__"):
            web_suggests = result.get("suggestions")
            result       = result["response"]

        words = (result or "").split(" ")
        for i, word in enumerate(words):
            chunk = word if i == len(words) - 1 else word + " "
            if not await _safe_send({"type": "token", "text": chunk}):
                return
            await asyncio.sleep(0.012)

        if not await _safe_send({"type": "done", "session_id": sid}):
            return

        # Émis après done — le frontend peut ouvrir le modal sans interrompre le streaming
        if plan_step:
            await _safe_send({"type": "plan_step_ready", **plan_step})
        if web_suggests:
            await websocket.send_json({
                "type":        "web_suggest",
                "session_id":  sid,
                "suggestions": web_suggests,
            })

    async def _execute_plan_web_search(
        sid: str,
        plan_web: dict,
        confirmed: bool,
        web_query: str | None,
    ) -> None:
        """
        Phase 6.5 — Exécute la recherche web pour l'étape 1 d'un plan (après confirmation).
        Marque l'étape done, émet les résultats, et émet plan_step_ready pour l'étape 2.
        """
        if not await _safe_send({"type": "thinking"}):
            return

        if not confirmed:
            await _safe_send({"type": "done", "session_id": sid})
            return

        import Mnemo.status as _status_mod
        status_queue: asyncio.Queue = asyncio.Queue()
        _status_mod.set_session(sid, status_queue, loop)

        def _do_plan_web() -> str:
            from Mnemo.tools.web_tools import web_search, format_results_for_prompt
            from Mnemo.tools.plan_tools import PlanStore
            from pathlib import Path
            _status_mod.emit(sid, f"Recherche web : {web_query}...")
            results     = web_search(web_query or plan_web.get("query", ""))
            web_context = format_results_for_prompt(results) if results else "(aucun résultat)"
            plan_path   = Path(plan_web["plan_path"])
            step_label  = plan_web["step_label"]
            try:
                PlanStore.mark_done(plan_path, step_label)
                PlanStore.append_log(plan_path, f"Web search : {web_query} — {len(results)} résultats")
            except Exception:
                pass
            reply = f"**Résultats — Étape 1 : {step_label}**\n\n{web_context}"
            if plan_web.get("step2_data"):
                reply += "\n\n→ **Étape 2 prête.** Confirme pour continuer."
            return reply

        fut = loop.run_in_executor(None, lambda: user_context.run(_do_plan_web))
        while not fut.done():
            try:
                event = status_queue.get_nowait()
                if not await _safe_send(event):
                    return
            except asyncio.QueueEmpty:
                await asyncio.sleep(0.03)
        while not status_queue.empty():
            if not await _safe_send(status_queue.get_nowait()):
                return
        _status_mod.clear_session(sid)

        try:
            result = fut.result()
        except Exception as e:
            await _safe_send({"type": "error", "detail": str(e)})
            return

        words = (result or "").split(" ")
        for i, word in enumerate(words):
            chunk = word if i == len(words) - 1 else word + " "
            if not await _safe_send({"type": "token", "text": chunk}):
                return
            await asyncio.sleep(0.012)

        if not await _safe_send({"type": "done", "session_id": sid}):
            return

        step2 = plan_web.get("step2_data")
        if step2:
            await _safe_send({"type": "plan_step_ready", **step2})

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
                plan_web  = msg.get("plan_web")
                if plan_web:
                    await _execute_plan_web_search(sid, plan_web, confirmed, wq)
                else:
                    await _run_and_stream(original, sid, confirmed, wq)

            elif msg_type == "web_link_explore":
                # Exploration d'un lien suggéré (Phase 6.5 deep fetch)
                link_url   = msg.get("url", "")
                link_title = msg.get("title", link_url)
                link_query = msg.get("original_query", link_title)
                sid        = msg.get("session_id") or f"web_{uuid.uuid4().hex[:12]}"

                if not await _safe_send({"type": "thinking"}):
                    return

                import Mnemo.status as _status_mod_lnk
                sq: asyncio.Queue = asyncio.Queue()
                _status_mod_lnk.set_session(sid, sq, loop)

                def _do_link_explore() -> str:
                    from Mnemo.tools.web_tools import (
                        fetch_page_content, extract_relevant_links,
                        save_web_page, format_results_for_prompt,
                    )
                    _status_mod_lnk.emit(sid, f"Lecture : {link_title[:60]}…")
                    page = fetch_page_content(link_url)
                    if page.get("error") or not page.get("text"):
                        return f"Impossible de lire la page : {page.get('error', 'contenu vide')}"
                    saved = save_web_page(page["text"], page["title"] or link_title, link_url, link_query)
                    if saved:
                        _status_mod_lnk.emit(sid, f"Sauvegardé : {saved.name}")
                    excerpt = page["text"][:2000].strip()
                    n_links = len(page.get("links", []))
                    reply = (
                        f"**{page['title'] or link_title}**\n"
                        f"Source : {link_url}\n\n"
                        f"{excerpt}{'…' if len(page['text']) > 2000 else ''}\n\n"
                        f"_{n_links} liens trouvés sur la page._"
                    )
                    if saved:
                        reply += f"\n\n📄 Contenu sauvegardé dans `{saved.name}`."
                    return reply

                fut2 = loop.run_in_executor(None, lambda: user_context.run(_do_link_explore))
                while not fut2.done():
                    try:
                        ev = sq.get_nowait()
                        if not await _safe_send(ev):
                            return
                    except asyncio.QueueEmpty:
                        await asyncio.sleep(0.03)
                while not sq.empty():
                    if not await _safe_send(sq.get_nowait()):
                        return
                _status_mod_lnk.clear_session(sid)

                try:
                    link_result = fut2.result()
                except Exception as e:
                    await _safe_send({"type": "error", "detail": str(e)})
                    continue

                words = (link_result or "").split(" ")
                for i, word in enumerate(words):
                    chunk = word if i == len(words) - 1 else word + " "
                    if not await _safe_send({"type": "token", "text": chunk}):
                        return
                    await asyncio.sleep(0.012)
                await _safe_send({"type": "done", "session_id": sid})

    except Exception:
        # Couvre WebSocketDisconnect, ConnectionClosedError, ConnectionClosedOK,
        # RuntimeError("WebSocket is not connected") — toutes les déconnexions normales.
        pass


# ── Base de connaissances (ingestion de fichiers) ──────────────────

@app.get("/api/documents")
def documents_list(_: Auth):
    """Liste tous les documents ingérés avec leurs métadonnées."""
    try:
        from Mnemo.context import get_data_dir
        from Mnemo.tools.ingest_tools import list_ingested_documents
        docs = list_ingested_documents(db_path=get_data_dir() / "memory.db")
        return {"documents": docs}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# Formats acceptés pour l'ingestion (sync avec ingest_file dispatcher)
_INGEST_EXTENSIONS = {
    ".pdf", ".docx", ".txt", ".md",
    ".py", ".js", ".ts", ".c", ".cpp", ".h",
    ".cs", ".java", ".sh", ".bash", ".ps1",
}


@app.post("/api/ingest", status_code=200)
def ingest_upload(file: UploadFile, _: Auth):
    """
    Ingère un fichier uploadé dans la base de connaissances.
    Le fichier est sauvegardé temporairement, ingéré, puis supprimé.
    Retourne {status, filename, pages, chunks, doc_id}.
    """
    import tempfile
    from pathlib import Path as _Path
    from Mnemo.context import get_data_dir
    from Mnemo.tools.ingest_tools import ingest_file

    filename = file.filename or "upload"
    ext = _Path(filename).suffix.lower()
    if ext not in _INGEST_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Format non supporté : {ext}. Acceptés : {', '.join(sorted(_INGEST_EXTENSIONS))}",
        )

    try:
        raw = file.file.read()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Lecture du fichier échouée : {e}")

    if not raw:
        raise HTTPException(status_code=400, detail="Fichier vide.")

    # Sauvegarde temporaire avec l'extension correcte (ingest_file en a besoin)
    try:
        with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
            tmp.write(raw)
            tmp_path = _Path(tmp.name)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erreur temporaire : {e}")

    try:
        result = ingest_file(tmp_path, db_path=get_data_dir() / "memory.db")
        result["filename"] = filename   # remplace le nom tmp par le nom original
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass


@app.delete("/api/documents/{doc_id}", status_code=200)
def document_delete(doc_id: str, _: Auth):
    """Supprime un document ingéré et tous ses chunks de la base."""
    if not doc_id or len(doc_id) > 64 or any(c in doc_id for c in ("/", "\\", "..")):
        raise HTTPException(status_code=400, detail="doc_id invalide.")
    try:
        from Mnemo.context import get_data_dir
        from Mnemo.tools.ingest_tools import delete_document
        ok = delete_document(doc_id, db_path=get_data_dir() / "memory.db")
        if not ok:
            raise HTTPException(status_code=404, detail="Document introuvable.")
        return {"ok": True}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Confirmations GOAP (Phase 7.6) ────────────────────────────────


@app.get("/api/confirmations")
async def confirmations_list(_: Auth):
    """Retourne les actions en attente de confirmation (world_state.json)."""
    from Mnemo.context import get_data_dir
    ws_path = get_data_dir() / "world_state.json"
    if not ws_path.exists():
        return {"confirmations": []}
    try:
        ws = json.loads(ws_path.read_text(encoding="utf-8"))
        return {"confirmations": ws.get("pending_confirmations", [])}
    except Exception:
        return {"confirmations": []}


class ConfirmActionRequest(BaseModel):
    approved: bool


@app.post("/api/confirmations/{confirmation_id}")
def confirm_action(confirmation_id: str, body: ConfirmActionRequest, _: Auth):
    """
    Approuve ou rejette une action en attente.
    Si approved=True et action=sandbox_shell:..., exécute la commande.
    Dans les deux cas, retire la confirmation de la liste.
    """
    from Mnemo.context import get_data_dir
    ws_path = get_data_dir() / "world_state.json"
    if not ws_path.exists():
        raise HTTPException(status_code=404, detail="Aucune confirmation en attente.")

    try:
        ws = json.loads(ws_path.read_text(encoding="utf-8"))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    confirmations: list = ws.get("pending_confirmations", [])
    target = next((c for c in confirmations if c.get("id") == confirmation_id), None)
    if target is None:
        raise HTTPException(status_code=404, detail="Confirmation introuvable.")

    # Retirer de la liste (approuvée ou rejetée)
    ws["pending_confirmations"] = [c for c in confirmations if c.get("id") != confirmation_id]
    ws_path.write_text(json.dumps(ws, ensure_ascii=False, indent=2), encoding="utf-8")

    if not body.approved:
        return {"ok": True, "executed": False, "stdout": "", "stderr": "", "returncode": None}

    action = target.get("action", "")
    slug   = target.get("project_slug", "")

    if action.startswith("sandbox_shell:"):
        command = action[len("sandbox_shell:"):].strip()
        from Mnemo.tools.sandbox_tools import run_command
        result = run_command(slug, command)
        return {
            "ok":         result["returncode"] == 0,
            "executed":   True,
            "stdout":     result.get("stdout", ""),
            "stderr":     result.get("stderr", ""),
            "returncode": result["returncode"],
        }

    # Action non-shell (sandbox_write, etc.) — pas d'exécution automatisée ici
    return {"ok": True, "executed": False, "stdout": "",
            "stderr": f"Action non exécutable depuis l'API : {action}", "returncode": None}


# ── Projets sandbox (Phase 7) ──────────────────────────────────────


@app.get("/api/projects")
def projects_list(_: Auth):
    from Mnemo.tools.sandbox_tools import list_projects
    return {"projects": list_projects()}


class ProjectCreate(BaseModel):
    name: str
    goal: str
    slug: str = ""


@app.post("/api/projects", status_code=201)
def project_create(body: ProjectCreate, _: Auth):
    from Mnemo.tools.sandbox_tools import create_project
    manifest = create_project(body.slug or body.name, body.name, body.goal)
    return manifest


@app.get("/api/projects/{slug}")
def project_get(slug: str, _: Auth):
    from Mnemo.tools.sandbox_tools import get_project, list_files
    manifest = get_project(slug)
    if manifest is None:
        raise HTTPException(status_code=404, detail="Projet introuvable.")
    return {**manifest, "files": list_files(slug)}


@app.post("/api/projects/{slug}/advance")
def project_advance(slug: str, _: Auth):
    """Exécute la prochaine étape non cochée du plan (1 seule étape)."""
    from Mnemo.tools.sandbox_tools import _project_path
    from Mnemo.tools.plan_tools import PlanRunner, PlanStore

    project_dir = _project_path(slug)
    plan_path   = project_dir / "plan.md"
    if not plan_path.exists():
        raise HTTPException(status_code=404, detail="plan.md introuvable")

    next_step = PlanStore.get_next_step(plan_path)
    if next_step is None:
        return {"done": True, "message": "Toutes les étapes sont terminées."}

    from Mnemo.tools.sandbox_tools import get_manifest
    manifest = get_manifest(slug) or {}
    runner  = PlanRunner()
    summary = runner.run(plan_path, max_steps=1, base_inputs={
        "project_dir": str(project_dir),
        "slug":        slug,
        "goal":        manifest.get("goal", ""),
    })
    return {"done": PlanStore.is_complete(plan_path), "message": summary}


@app.get("/api/projects/{slug}/log")
def project_log_read(slug: str, _: Auth):
    """Retourne logs/commands.log — toujours 200, vide si pas encore créé."""
    from Mnemo.tools.sandbox_tools import read_file
    res = read_file(slug, "logs/commands.log")
    return {"content": res["content"], "path": "logs/commands.log"}


@app.get("/api/projects/{slug}/file")
def project_file_read(slug: str, path: str, _: Auth):
    from Mnemo.tools.sandbox_tools import read_file
    res = read_file(slug, path)
    if res["error"]:
        # 400 pour chemin interdit, 404 pour fichier absent (polling normal)
        code = 400 if "interdit" in res["error"] or "échappement" in res["error"] else 404
        raise HTTPException(status_code=code, detail=res["error"])
    return {"content": res["content"], "path": path}


class FileWriteRequest(BaseModel):
    path: str
    content: str
    commit_msg: str = ""


@app.post("/api/projects/{slug}/file")
def project_file_write(slug: str, body: FileWriteRequest, _: Auth):
    from Mnemo.tools.sandbox_tools import write_file
    res = write_file(slug, body.path, body.content,
                     commit_msg=body.commit_msg or None)
    if res["conflict"]:
        raise HTTPException(status_code=409,
                            detail="Conflit git — résolution manuelle requise.")
    if res["error"]:
        raise HTTPException(status_code=400, detail=res["error"])
    return res


@app.delete("/api/projects/{slug}", status_code=200)
def project_delete(slug: str, _: Auth):
    from Mnemo.tools.sandbox_tools import delete_project
    if not delete_project(slug):
        raise HTTPException(status_code=404, detail="Projet introuvable.")
    return {"ok": True}


@app.get("/api/projects/{slug}/git")
def project_git_log(slug: str, _: Auth):
    from Mnemo.tools.sandbox_tools import _project_path, _git
    root = _project_path(slug)
    if not root.exists():
        raise HTTPException(status_code=404, detail="Projet introuvable.")
    _, out = _git(root, "log", "--oneline", "-20")
    return {"log": out}


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
