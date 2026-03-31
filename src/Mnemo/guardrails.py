"""
Phase N3 — Guardrails & Risk Management

Fournit :
  - RISK_REGISTRY    : taxonomie des risques par route API
  - get_risk_level() : résout le niveau depuis (method, path)
  - is_at_least()    : compare deux niveaux de risque
  - is_system_paused() / set_system_paused() : kill-switch scheduler
  - get_system_state()   : état courant du système
  - log_audit()      : écriture dans audit_log.jsonl utilisateur
  - read_audit()     : lecture des N dernières entrées
  - resolve_username_from_token() : lookup user depuis Bearer token (middleware)
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Literal

RiskLevel = Literal["low", "medium", "high", "critical"]

_RISK_ORDER: dict[str, int] = {"low": 0, "medium": 1, "high": 2, "critical": 3}


# ── Taxonomie des risques ─────────────────────────────────────────────
# (method, path_prefix, risk_level) — premier match gagne

RISK_REGISTRY: list[tuple[str, str, RiskLevel]] = [
    # ── Critical ────────────────────────────────────────────────────
    ("DELETE", "/api/projects/",        "critical"),   # delete project or file
    ("DELETE", "/api/documents/",       "critical"),
    ("DELETE", "/api/calendar/",        "critical"),
    ("POST",   "/api/system/pause",     "critical"),
    # ── High ────────────────────────────────────────────────────────
    ("POST",   "/api/projects/",        "high"),       # create / advance / command
    ("PUT",    "/api/projects/",        "high"),       # write file
    ("POST",   "/api/dream",            "high"),
    ("PUT",    "/api/calendar/",        "high"),
    # ── Medium ──────────────────────────────────────────────────────
    ("POST",   "/api/memory",           "medium"),
    ("POST",   "/api/message",          "medium"),
    ("POST",   "/api/calendar",         "medium"),
    ("POST",   "/api/ingest",           "medium"),
    ("PUT",    "/api/assistant",        "medium"),
    ("POST",   "/api/goap/",            "medium"),
    ("DELETE", "/api/goap/",            "medium"),
    ("POST",   "/api/onboarding",       "medium"),
    ("POST",   "/api/voice/",           "medium"),
    ("POST",   "/api/system/resume",    "medium"),
    ("POST",   "/api/users",            "medium"),
    # ── Low (default) ───────────────────────────────────────────────
]


def get_risk_level(method: str, path: str) -> RiskLevel:
    """Retourne le niveau de risque d'une requête API."""
    m = method.upper()
    for reg_method, prefix, risk in RISK_REGISTRY:
        if m == reg_method and path.startswith(prefix):
            return risk
    return "low"


def is_at_least(level: RiskLevel, minimum: RiskLevel) -> bool:
    """True si level >= minimum dans l'ordre low < medium < high < critical."""
    return _RISK_ORDER[level] >= _RISK_ORDER[minimum]


# ── System state / kill-switch ────────────────────────────────────────

def _system_state_path(data_path: Path) -> Path:
    return data_path / "system_state.json"


def get_system_state(data_path: Path) -> dict:
    path = _system_state_path(data_path)
    if not path.exists():
        return {"paused": False, "paused_at": None, "resumed_at": None}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"paused": False, "paused_at": None, "resumed_at": None}


def is_system_paused(data_path: Path) -> bool:
    return get_system_state(data_path).get("paused", False)


def set_system_paused(data_path: Path, paused: bool) -> None:
    existing = get_system_state(data_path)
    now = datetime.now().isoformat()
    if paused:
        state = {"paused": True,  "paused_at": now,  "resumed_at": existing.get("resumed_at")}
    else:
        state = {"paused": False, "paused_at": existing.get("paused_at"), "resumed_at": now}
    _system_state_path(data_path).write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
    )


# ── Audit log ─────────────────────────────────────────────────────────

def log_audit(
    user_dir: Path,
    *,
    method: str,
    path: str,
    risk: RiskLevel,
    status: int,
    detail: str = "",
) -> None:
    """Ajoute une entrée dans audit_log.jsonl (crée le fichier si absent)."""
    entry = {
        "ts":     datetime.now().isoformat(),
        "method": method,
        "path":   path,
        "risk":   risk,
        "status": status,
        "detail": detail,
    }
    audit_path = user_dir / "audit_log.jsonl"
    try:
        with audit_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass


def read_audit(user_dir: Path, limit: int = 50) -> list[dict]:
    """Retourne les N dernières entrées de audit_log.jsonl, du plus récent au plus ancien."""
    audit_path = user_dir / "audit_log.jsonl"
    if not audit_path.exists():
        return []
    try:
        lines = audit_path.read_text(encoding="utf-8").strip().splitlines()
        entries: list[dict] = []
        for line in reversed(lines):            # most recent first
            try:
                entries.append(json.loads(line))
            except Exception:
                pass
            if len(entries) >= limit:
                break
        return entries
    except Exception:
        return []


# ── Username resolution (pour le middleware) ──────────────────────────

def resolve_username_from_token(token: str, data_path: Path) -> str | None:
    """Résout le username depuis un Bearer token via SHA-256 lookup dans users.json."""
    try:
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        users_file = data_path / "users.json"
        if not users_file.exists():
            return None
        users: dict = json.loads(users_file.read_text(encoding="utf-8"))
        for username, info in users.items():
            if isinstance(info, dict) and info.get("token_hash") == token_hash:
                return username
        return None
    except Exception:
        return None