"""
assistant_tools.py — Gestion de l'identité persistante de l'assistant.

Chaque utilisateur peut nommer et personnaliser son assistant.
La config vit dans data/users/<username>/assistant.json.
Elle est injectée dans les crews via get_assistant_context().

Séparation des couches :
  assistant.json          ← config permanente (source de vérité)
  memory.md > Identité Agent  ← journal vécu (enrichi par les sessions)
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path


_DEFAULT_NAME = "Mnemo"

_DEFAULT_PERSONA_FULL = (
    "Tu es {name}, un assistant personnel IA avec une mémoire long terme persistante. "
    "Tu utilises cette mémoire pour personnaliser tes réponses et maintenir la continuité "
    "des échanges au fil du temps. Tu es précis, utile et naturel dans ta façon de communiquer."
)

_DEFAULT_LANGUAGE_STYLE = (
    "Clair et direct. Réponses concises sauf si le contexte demande plus de détail."
)

_DEFAULT_CONFIG: dict = {
    "name":           _DEFAULT_NAME,
    "pronouns":       "il/elle",
    "persona_short":  "Assistant personnel IA avec mémoire long terme",
    "persona_full":   _DEFAULT_PERSONA_FULL,
    "language_style": _DEFAULT_LANGUAGE_STYLE,
    "default_name":   _DEFAULT_NAME,
}


def _config_path(username: str, data_path: Path | None = None) -> Path:
    if data_path is None:
        try:
            from Mnemo.context import get_data_dir
            data_path = get_data_dir()
        except Exception:
            data_path = Path("/data")
    return data_path / "users" / username / "assistant.json"


def get_assistant_config(username: str, data_path: Path | None = None) -> dict:
    """
    Lit assistant.json pour cet utilisateur.
    Retourne les valeurs par défaut (name=Mnemo) si le fichier n'existe pas.
    """
    path = _config_path(username, data_path)
    if not path.exists():
        config = dict(_DEFAULT_CONFIG)
        config["persona_full"] = _DEFAULT_PERSONA_FULL.format(name=_DEFAULT_NAME)
        return config
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        config = dict(_DEFAULT_CONFIG)
        config["persona_full"] = _DEFAULT_PERSONA_FULL.format(name=_DEFAULT_NAME)
        return config


def ensure_assistant_config(username: str, data_path: Path | None = None) -> dict:
    """
    Crée assistant.json avec les valeurs par défaut s'il n'existe pas.
    Retourne la config (existante ou nouvellement créée).
    """
    path = _config_path(username, data_path)
    if path.exists():
        return get_assistant_config(username, data_path)

    config = dict(_DEFAULT_CONFIG)
    config["persona_full"]   = _DEFAULT_PERSONA_FULL.format(name=_DEFAULT_NAME)
    config["created_at"]     = datetime.now().isoformat()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    return config


def set_assistant_config(username: str, data_path: Path | None = None, **kwargs) -> dict:
    """
    Met à jour assistant.json avec les champs fournis.
    Retourne la config mise à jour.

    Exemple :
        set_assistant_config("Matt", name="Mitsune", persona_short="tsundere...")
    """
    config = get_assistant_config(username, data_path)
    for key, value in kwargs.items():
        if value is not None:
            config[key] = value
    config["updated_at"] = datetime.now().isoformat()
    path = _config_path(username, data_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    return config


def get_assistant_context(username: str, data_path: Path | None = None) -> str:
    """
    Retourne le bloc de contexte identité formaté pour injection dans les crew inputs.

    Format :
      ## Ton identité
      Tu es **Mitsune**. [persona_full]
      Style : [language_style]

    Ce texte est injecté dans assistant_persona (variable YAML des agents).
    """
    cfg  = get_assistant_config(username, data_path)
    name = cfg.get("name", _DEFAULT_NAME)

    persona = cfg.get("persona_full", "").strip()
    # Remplacer les placeholders résiduels si le persona_full contient {name}
    persona = persona.replace("{name}", name)

    style = cfg.get("language_style", "").strip()

    parts = [f"## Ton identité\nTu es **{name}**."]
    if persona:
        parts.append(persona)
    if style:
        parts.append(f"Style de communication : {style}")

    return "\n".join(parts)


def get_assistant_name(username: str, data_path: Path | None = None) -> str:
    """Raccourci — retourne juste le nom de l'assistant."""
    return get_assistant_config(username, data_path).get("name", _DEFAULT_NAME)