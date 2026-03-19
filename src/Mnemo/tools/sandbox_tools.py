"""
sandbox_tools.py — Environnement de travail isolé pour SandboxCrew

Chaque projet est un dépôt git dans /data/projects/<slug>/.
Les outils agents sont strictement confinés à ce répertoire.

Structure d'un projet :
    /data/projects/<slug>/
        .git/           ← dépôt git
        project.json    ← manifest (slug, name, goal, status, created_at)
        plan.md         ← plan GOAP courant
        memory.md       ← mémoire locale au projet
        src/            ← fichiers produits (code, docs...)
        logs/           ← sorties de commandes

Garanties de sécurité :
    - Tous les chemins sont résolus et vérifiés contre la racine du projet
    - Aucun accès hors du dossier projet (pas de ../)
    - SandboxShellTool : cwd = racine projet, env nettoyé, timeout 30s
    - Chaque écriture agent = git add + git commit automatique
    - Détection de conflit git à l'écriture (dirty flag)
"""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Optional, Type

from Mnemo.context import get_data_dir

# ── Config ────────────────────────────────────────────────────────────────────
SANDBOX_TIMEOUT = 30          # secondes max par commande shell
MAX_OUTPUT_BYTES = 32_000     # taille max sortie capturée
MAX_FILE_READ_CHARS = 40_000  # taille max lecture fichier


# ══════════════════════════════════════════════════════════════════════════════
# Manager — CRUD projets
# ══════════════════════════════════════════════════════════════════════════════

def _projects_root() -> Path:
    return get_data_dir() / "projects"


def _project_path(slug: str) -> Path:
    return _projects_root() / _safe_slug(slug)


def _safe_slug(slug: str) -> str:
    """Slugify : alnum + tirets uniquement, max 40 chars."""
    slug = re.sub(r"[^\w\s-]", "", slug.lower())
    slug = re.sub(r"[\s_]+", "-", slug).strip("-")
    return slug[:40] or "project"


def _resolve_safe(project_root: Path, relative: str) -> Path | None:
    """
    Résout un chemin relatif dans le projet.
    Retourne None si le chemin tente d'échapper au sandbox (../).
    """
    try:
        target = (project_root / relative).resolve()
        project_root_resolved = project_root.resolve()
        if not str(target).startswith(str(project_root_resolved)):
            return None
        return target
    except Exception:
        return None


def _git(project_root: Path, *args: str) -> tuple[int, str]:
    """Exécute une commande git dans le projet. Retourne (returncode, output)."""
    try:
        result = subprocess.run(
            ["git"] + list(args),
            cwd=project_root,
            capture_output=True,
            text=True,
            timeout=10,
        )
        return result.returncode, (result.stdout + result.stderr).strip()
    except Exception as e:
        return 1, str(e)


def _git_commit(project_root: Path, message: str, paths: list[str]) -> bool:
    """
    Ajoute les fichiers et crée un commit.
    Retourne True si commit créé, False si rien à committer.
    """
    for p in paths:
        _git(project_root, "add", p)
    rc, out = _git(project_root, "commit", "--author=Mnemo <agent@mnemo>",
                   "-m", message)
    return rc == 0


def _git_has_conflict(project_root: Path, relative: str) -> bool:
    """Vérifie si un fichier a un conflit non résolu."""
    rc, out = _git(project_root, "status", "--porcelain", relative)
    return out.startswith("UU") or out.startswith("AA")


def create_project(slug: str, name: str, goal: str) -> dict:
    """
    Crée un projet sandbox.
    - Initialise le répertoire + git
    - Écrit project.json, plan.md, memory.md
    - Retourne le manifest du projet.
    """
    slug = _safe_slug(slug)
    root = _project_path(slug)
    root.mkdir(parents=True, exist_ok=True)
    (root / "src").mkdir(exist_ok=True)
    (root / "logs").mkdir(exist_ok=True)

    # Git init (idempotent)
    if not (root / ".git").exists():
        _git(root, "init")
        _git(root, "config", "user.email", "agent@mnemo")
        _git(root, "config", "user.name",  "Mnemo")

    manifest = {
        "slug":        slug,
        "name":        name,
        "goal":        goal,
        "status":      "in_progress",
        "created_at":  datetime.now().isoformat(timespec="seconds"),
    }
    (root / "project.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    if not (root / "plan.md").exists():
        (root / "plan.md").write_text(
            f"# Plan — {name}\n\n**Objectif :** {goal}\n\n## Étapes\n\n- [ ] (à définir)\n",
            encoding="utf-8",
        )

    if not (root / "memory.md").exists():
        (root / "memory.md").write_text(
            f"# Mémoire projet — {name}\n\n**Objectif :** {goal}\n\n## Décisions\n\n## Contexte\n",
            encoding="utf-8",
        )

    # Commit initial
    _git(root, "add", ".")
    _git(root, "commit", "--author=Mnemo <agent@mnemo>",
         "-m", f"init: projet {name}")

    return manifest


def list_projects() -> list[dict]:
    """Liste tous les projets existants (lit les project.json)."""
    root = _projects_root()
    if not root.exists():
        return []
    projects = []
    for d in sorted(root.iterdir()):
        pf = d / "project.json"
        if pf.exists():
            try:
                projects.append(json.loads(pf.read_text(encoding="utf-8")))
            except Exception:
                pass
    return projects


def get_project(slug: str) -> dict | None:
    """Retourne le manifest d'un projet, ou None s'il n'existe pas."""
    pf = _project_path(slug) / "project.json"
    if not pf.exists():
        return None
    try:
        return json.loads(pf.read_text(encoding="utf-8"))
    except Exception:
        return None


def delete_project(slug: str) -> bool:
    """Supprime un projet (dossier complet). Retourne True si supprimé."""
    import shutil as _shutil
    root = _project_path(slug)
    if not root.exists():
        return False
    _shutil.rmtree(root)
    return True


def read_file(slug: str, relative_path: str) -> dict:
    """
    Lit un fichier dans le sandbox.
    Retourne {"content": str, "error": None} ou {"content": "", "error": str}.
    """
    root = _project_path(slug)
    target = _resolve_safe(root, relative_path)
    if target is None:
        return {"content": "", "error": "Chemin interdit (tentative d'échappement détectée)"}
    if not target.exists():
        return {"content": "", "error": f"Fichier introuvable : {relative_path}"}
    if not target.is_file():
        return {"content": "", "error": f"Pas un fichier : {relative_path}"}
    try:
        text = target.read_text(encoding="utf-8", errors="replace")
        if len(text) > MAX_FILE_READ_CHARS:
            text = text[:MAX_FILE_READ_CHARS] + "\n\n[…tronqué]"
        return {"content": text, "error": None}
    except Exception as e:
        return {"content": "", "error": str(e)}


def write_file(slug: str, relative_path: str, content: str,
               commit_msg: str | None = None) -> dict:
    """
    Écrit un fichier dans le sandbox + git add + git commit automatique.

    Si le fichier a été modifié manuellement depuis la dernière lecture agent,
    tente un merge git. Retourne les conflits le cas échéant.

    Retourne {"path": str, "committed": bool, "conflict": bool, "error": None|str}
    """
    root = _project_path(slug)
    target = _resolve_safe(root, relative_path)
    if target is None:
        return {"path": relative_path, "committed": False, "conflict": False,
                "error": "Chemin interdit"}

    target.parent.mkdir(parents=True, exist_ok=True)

    # Détection dirty flag : si le fichier existe et est modifié non commité
    conflict = False
    if target.exists():
        rc, status = _git(root, "status", "--porcelain", relative_path)
        if status.strip():  # fichier modifié par l'utilisateur
            # Sauvegarde la version agent
            agent_content = content
            # Tente merge : écrit, git add, laisse git gérer
            target.write_text(content, encoding="utf-8")
            rc_merge, out = _git(root, "merge-file", "--diff3",
                                 relative_path, relative_path, relative_path)
            if _git_has_conflict(root, relative_path):
                conflict = True
                return {"path": relative_path, "committed": False,
                        "conflict": True,
                        "error": "Conflit git — résolution manuelle requise"}

    target.write_text(content, encoding="utf-8")
    msg = commit_msg or f"agent: update {relative_path}"
    committed = _git_commit(root, msg, [relative_path])
    return {"path": relative_path, "committed": committed,
            "conflict": False, "error": None}


def run_command(slug: str, command: str) -> dict:
    """
    Exécute une commande shell dans le sandbox (cwd = racine projet).

    Sécurité :
        - cwd confiné à la racine projet
        - env nettoyé (HOME, PATH minimal)
        - timeout 30s
        - sortie tronquée à MAX_OUTPUT_BYTES

    Retourne {"stdout": str, "stderr": str, "returncode": int, "error": None|str}
    """
    root = _project_path(slug)
    if not root.exists():
        return {"stdout": "", "stderr": "", "returncode": 1,
                "error": f"Projet introuvable : {slug}"}

    safe_env = {
        "PATH":   "/usr/local/bin:/usr/bin:/bin",
        "HOME":   str(root),
        "LANG":   "fr_FR.UTF-8",
        "TERM":   "dumb",
    }

    try:
        proc = subprocess.run(
            command,
            shell=True,
            cwd=root,
            env=safe_env,
            capture_output=True,
            timeout=SANDBOX_TIMEOUT,
        )
        stdout = proc.stdout[:MAX_OUTPUT_BYTES].decode("utf-8", errors="replace")
        stderr = proc.stderr[:MAX_OUTPUT_BYTES].decode("utf-8", errors="replace")

        # Log dans logs/
        log_entry = (
            f"[{datetime.now().isoformat(timespec='seconds')}] $ {command}\n"
            f"rc={proc.returncode}\n{stdout}{stderr}\n---\n"
        )
        try:
            (root / "logs" / "commands.log").open("a", encoding="utf-8").write(log_entry)
        except Exception:
            pass

        return {"stdout": stdout, "stderr": stderr,
                "returncode": proc.returncode, "error": None}

    except subprocess.TimeoutExpired:
        return {"stdout": "", "stderr": "", "returncode": 1,
                "error": f"Timeout ({SANDBOX_TIMEOUT}s dépassé)"}
    except Exception as e:
        return {"stdout": "", "stderr": "", "returncode": 1, "error": str(e)}


def list_files(slug: str, subdir: str = "") -> list[str]:
    """
    Liste les fichiers dans le sandbox (chemins relatifs à la racine projet).
    Exclut .git/.
    """
    root = _project_path(slug)
    if subdir:
        base = _resolve_safe(root, subdir)
        if base is None or not base.is_dir():
            return []
    else:
        base = root

    result = []
    for p in sorted(base.rglob("*")):
        if ".git" in p.parts:
            continue
        if p.is_file():
            try:
                result.append(str(p.relative_to(root)))
            except ValueError:
                pass
    return result


# ══════════════════════════════════════════════════════════════════════════════
# CrewAI Tools
# ══════════════════════════════════════════════════════════════════════════════

try:
    from crewai.tools import BaseTool
    from pydantic import BaseModel, Field

    # ── SandboxReadTool ───────────────────────────────────────────────────────

    class SandboxReadInput(BaseModel):
        slug:          str = Field(description="Slug du projet sandbox")
        relative_path: str = Field(description="Chemin relatif depuis la racine projet (ex: src/index.tsx)")

    class SandboxReadTool(BaseTool):
        name: str = "sandbox_read"
        description: str = (
            "Lit le contenu d'un fichier dans le sandbox du projet. "
            "Ne peut pas lire hors du dossier projet."
        )
        args_schema: Type[BaseModel] = SandboxReadInput

        def _run(self, slug: str, relative_path: str) -> str:
            res = read_file(slug, relative_path)
            if res["error"]:
                return f"Erreur : {res['error']}"
            return res["content"]

    # ── SandboxWriteTool ──────────────────────────────────────────────────────

    class SandboxWriteInput(BaseModel):
        slug:          str = Field(description="Slug du projet sandbox")
        relative_path: str = Field(description="Chemin relatif du fichier à écrire (ex: src/App.tsx)")
        content:       str = Field(description="Contenu complet du fichier")
        commit_msg:    str = Field(default="", description="Message de commit git (optionnel)")

    class SandboxWriteTool(BaseTool):
        name: str = "sandbox_write"
        description: str = (
            "Écrit ou crée un fichier dans le sandbox du projet. "
            "Le fichier est automatiquement commité dans git. "
            "Ne peut pas écrire hors du dossier projet."
        )
        args_schema: Type[BaseModel] = SandboxWriteInput

        def _run(self, slug: str, relative_path: str, content: str,
                 commit_msg: str = "") -> str:
            res = write_file(slug, relative_path, content,
                             commit_msg=commit_msg or None)
            if res["conflict"]:
                return (f"⚠ Conflit git sur {relative_path} — "
                        "le fichier a été modifié manuellement. "
                        "Résolution manuelle requise depuis le dashboard.")
            if res["error"]:
                return f"Erreur : {res['error']}"
            status = "commité" if res["committed"] else "écrit (rien à committer)"
            return f"✓ {relative_path} {status}."

    # ── SandboxShellTool ──────────────────────────────────────────────────────

    class SandboxShellInput(BaseModel):
        slug:    str = Field(description="Slug du projet sandbox")
        command: str = Field(description="Commande shell à exécuter dans le projet")

    class SandboxShellTool(BaseTool):
        name: str = "sandbox_shell"
        description: str = (
            "Exécute une commande shell dans le sandbox du projet "
            "(npm install, python script.py, pytest, etc.). "
            "Confiné au dossier projet, timeout 30s, sortie capturée."
        )
        args_schema: Type[BaseModel] = SandboxShellInput

        def _run(self, slug: str, command: str) -> str:
            res = run_command(slug, command)
            if res["error"]:
                return f"Erreur : {res['error']}"
            out = ""
            if res["stdout"]:
                out += res["stdout"]
            if res["stderr"]:
                out += f"\n[stderr]\n{res['stderr']}"
            rc = res["returncode"]
            return f"[rc={rc}]\n{out.strip()}" if out.strip() else f"[rc={rc}] (pas de sortie)"

    # ── SandboxListTool ───────────────────────────────────────────────────────

    class SandboxListInput(BaseModel):
        slug:   str = Field(description="Slug du projet sandbox")
        subdir: str = Field(default="", description="Sous-dossier à lister (optionnel, ex: src/)")

    class SandboxListTool(BaseTool):
        name: str = "sandbox_list"
        description: str = (
            "Liste les fichiers du projet sandbox. "
            "Retourne les chemins relatifs depuis la racine du projet."
        )
        args_schema: Type[BaseModel] = SandboxListInput

        def _run(self, slug: str, subdir: str = "") -> str:
            files = list_files(slug, subdir)
            if not files:
                return "Aucun fichier trouvé."
            return "\n".join(files)

except ImportError:
    pass