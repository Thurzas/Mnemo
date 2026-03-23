"""
Phase E.1 — Project Index

Maintient project_index.json dans chaque projet sandbox.
Sert de "carte du territoire" pour l'agent avant d'exécuter une étape.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

_EXCLUDE = {".git", "__pycache__", "node_modules", ".venv"}
_TEXT_SUFFIXES = {
    ".md", ".txt", ".py", ".js", ".ts", ".json", ".yaml", ".yml",
    ".html", ".css", ".sh", ".toml", ".ini", ".cfg",
}


def _project_root(slug: str) -> Path:
    from Mnemo.tools.sandbox_tools import _project_path
    return _project_path(slug)


def _safe_preview(path: Path, max_chars: int = 200) -> str:
    """Lit les premiers max_chars d'un fichier texte."""
    if path.suffix not in _TEXT_SUFFIXES:
        return "(binaire)"
    try:
        text = path.read_text(encoding="utf-8", errors="replace").strip()
        if len(text) > max_chars:
            text = text[:max_chars] + "…"
        return text
    except Exception:
        return ""


def _file_entry(root: Path, path: Path) -> dict:
    rel = path.relative_to(root).as_posix()
    try:
        stat  = path.stat()
        size  = stat.st_size
        mtime = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    except Exception:
        size, mtime = 0, ""
    return {
        "path":     rel,
        "size":     size,
        "modified": mtime,
        "preview":  _safe_preview(path),
    }


def index_project(slug: str) -> dict:
    """
    Scanne le projet, construit et sauvegarde project_index.json.
    Retourne le dict de l'index.
    """
    root = _project_root(slug)
    if not root.exists():
        return {"slug": slug, "files": [], "updated_at": ""}

    files = []
    for path in sorted(root.rglob("*")):
        parts = path.relative_to(root).parts
        if any(p in _EXCLUDE for p in parts):
            continue
        if path.name == "project_index.json":
            continue
        if path.is_file():
            files.append(_file_entry(root, path))

    index = {
        "slug":       slug,
        "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"),
        "files":      files,
    }
    try:
        (root / "project_index.json").write_text(
            json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception:
        pass
    return index


def update_index(slug: str, changed_path: str) -> None:
    """
    Met à jour l'entrée d'un fichier dans l'index existant.
    Si l'index n'existe pas, reconstruit tout.
    Appelé automatiquement après chaque sandbox_tools.write_file().
    """
    root       = _project_root(slug)
    index_path = root / "project_index.json"

    if not index_path.exists():
        index_project(slug)
        return

    try:
        index = json.loads(index_path.read_text(encoding="utf-8"))
    except Exception:
        index_project(slug)
        return

    rel    = changed_path.replace("\\", "/")
    target = root / changed_path

    # Supprimer l'ancienne entrée
    index["files"] = [f for f in index.get("files", []) if f["path"] != rel]

    # Ajouter la nouvelle entrée si le fichier existe
    if target.exists() and target.is_file():
        index["files"].append(_file_entry(root, target))
        index["files"].sort(key=lambda f: f["path"])

    index["updated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    try:
        index_path.write_text(
            json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception:
        pass


def glob_project(slug: str, pattern: str) -> list[dict]:
    """
    Retourne les fichiers correspondant au pattern glob (style Unix).
    Ex: glob_project("doc-react", "src/**/*.md")
    """
    root = _project_root(slug)
    if not root.exists():
        return []
    results = []
    for path in sorted(root.glob(pattern)):
        parts = path.relative_to(root).parts
        if any(p in _EXCLUDE for p in parts) or not path.is_file():
            continue
        results.append(_file_entry(root, path))
    return results


def format_project_context(slug: str, max_chars: int = 800) -> str:
    """
    Construit le bloc de contexte projet pour injection dans un prompt.
    Priorise src/ et research/, tronque si nécessaire.
    Retourne "" si le projet n'a pas encore de fichiers significatifs.
    """
    root = _project_root(slug)
    if not root.exists():
        return ""

    index_path = root / "project_index.json"
    if index_path.exists():
        try:
            index = json.loads(index_path.read_text(encoding="utf-8"))
        except Exception:
            index = index_project(slug)
    else:
        index = index_project(slug)

    files = [f for f in index.get("files", []) if f["path"] != "project_index.json"]
    if not files:
        return ""

    # src/ et research/ en priorité
    def _priority(f: dict) -> int:
        p = f["path"]
        if p.startswith("src/"):      return 0
        if p.startswith("research/"): return 1
        return 2

    files = sorted(files, key=_priority)

    lines = ["## Fichiers existants dans le projet\n"]
    total = len(lines[0])
    for f in files:
        size_kb = f["size"] / 1024
        preview = f["preview"].replace("\n", " ").strip()
        if len(preview) > 100:
            preview = preview[:100] + "…"
        line = f'- {f["path"]} ({size_kb:.1f} KB) — "{preview}"\n'
        if total + len(line) > max_chars:
            lines.append("- … (autres fichiers tronqués)\n")
            break
        lines.append(line)
        total += len(line)

    return "".join(lines).strip()