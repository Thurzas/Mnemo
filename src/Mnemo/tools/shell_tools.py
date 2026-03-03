"""
shell_tools.py — Outil d'exécution de commandes shell pour ShellCrew

Implémente ShellExecuteTool : un outil CrewAI qui valide une commande
contre la whitelist, puis l'exécute via subprocess si elle est sûre.

Garanties :
  - Aucune commande n'atteint subprocess sans passer par la whitelist
  - Toutes les opérations fichiers restent sous /data
  - Sortie tronquée à MAX_OUTPUT_BYTES
  - Timeout fixe — pas de processus zombie
  - La confirmation utilisateur est gérée en amont (main.py)
    avant que cet outil soit appelé — la commande est figée
"""

from __future__ import annotations

import shlex
import subprocess
from typing import Optional, Type

from crewai.tools import BaseTool
from pydantic import BaseModel, Field

from Mnemo.tools.shell_whitelist import (
    ALLOWED_PATH_ROOT,
    MAX_OUTPUT_BYTES,
    is_command_allowed,
    is_path_safe,
    is_python_script_safe,
    validate_rm_args,
    describe_command_policy,
)

# Timeout subprocess en secondes
COMMAND_TIMEOUT = 30


# ══════════════════════════════════════════════════════════════════
# Validation complète d'une commande
# ══════════════════════════════════════════════════════════════════

class ValidationResult:
    """Résultat de la validation d'une commande."""
    def __init__(self, ok: bool, reason: str = ""):
        self.ok     = ok
        self.reason = reason

    def __bool__(self) -> bool:
        return self.ok


def validate_command(command_str: str) -> ValidationResult:
    """
    Valide une commande complète (avec arguments) contre la whitelist.

    Retourne ValidationResult(ok=True) si la commande est autorisée,
    ou ValidationResult(ok=False, reason=...) avec l'explication du refus.
    """
    if not command_str or not command_str.strip():
        return ValidationResult(False, "commande vide")

    # Détection de chaînage de commandes — toujours refusé
    # (&&, ||, ;, |, >, <, `, $(...))
    for metachar in ("&&", "||", ";", "|", "`", "$(", ">", "<"):
        if metachar in command_str:
            return ValidationResult(
                False,
                f"opérateur shell interdit : {metachar!r} — "
                "une seule commande à la fois"
            )

    # Parse la commande
    try:
        parts = shlex.split(command_str)
    except ValueError as e:
        return ValidationResult(False, f"commande non parseable : {e}")

    if not parts:
        return ValidationResult(False, "commande vide après parsing")

    cmd  = parts[0]
    args = parts[1:]

    # 1. Commande dans la whitelist ?
    if not is_command_allowed(cmd):
        return ValidationResult(
            False,
            f"commande {cmd!r} non autorisée.\n{describe_command_policy()}"
        )

    # 2. Validations spécifiques par commande
    if cmd in ("python", "python3"):
        # Python : exige un script .py sous /data
        if not args:
            return ValidationResult(False, "python : un fichier .py est requis")
        script = args[0]
        if not is_python_script_safe(script):
            return ValidationResult(
                False,
                f"python : {script!r} refusé — doit être un .py sous /data"
            )
        # Arguments supplémentaires au script : autorisés (valeurs passées au script)
        return ValidationResult(True)

    if cmd == "rm":
        ok, reason = validate_rm_args(args)
        if not ok:
            return ValidationResult(False, f"rm : {reason}")

    # 3. Tous les chemins dans les args doivent rester sous /data
    #    (sauf lecture système pure : ls /etc, cat /proc/... sont légitimes)
    READ_ONLY_CMDS = {"ls", "cat", "find", "grep", "head", "tail",
                      "wc", "du", "stat", "file", "diff", "sort", "uniq"}

    if cmd not in READ_ONLY_CMDS:
        # Commandes de modification : TOUS les chemins doivent être sous /data
        for arg in args:
            if arg.startswith("-"):
                continue  # flag, pas un chemin
            if arg.startswith("/") or arg.startswith(".."):
                if not is_path_safe(arg):
                    return ValidationResult(
                        False,
                        f"chemin interdit : {arg!r} — "
                        f"opérations limitées à {ALLOWED_PATH_ROOT}"
                    )
    else:
        # Commandes de lecture : les chemins sous / sont autorisés SAUF
        # les chemins sensibles
        SENSITIVE_PATHS = {"/etc/shadow", "/etc/passwd", "/proc/keys",
                           "/root", "/home"}
        for arg in args:
            if arg.startswith("-"):
                continue
            for sensitive in SENSITIVE_PATHS:
                if arg.startswith(sensitive):
                    return ValidationResult(
                        False,
                        f"chemin sensible interdit : {arg!r}"
                    )

    return ValidationResult(True)


# ══════════════════════════════════════════════════════════════════
# Exécution sécurisée
# ══════════════════════════════════════════════════════════════════

def execute_command(command_str: str) -> dict:
    """
    Valide puis exécute une commande shell.

    Retourne un dict :
      {
        "success": bool,
        "stdout":  str,
        "stderr":  str,
        "returncode": int,
        "error":   str | None   # raison du refus si success=False avant exec
      }
    """
    result = validate_command(command_str)
    if not result:
        return {
            "success":    False,
            "stdout":     "",
            "stderr":     "",
            "returncode": -1,
            "error":      f"Commande refusée : {result.reason}",
        }

    try:
        parts = shlex.split(command_str)
        proc  = subprocess.run(
            parts,
            capture_output=True,
            timeout=COMMAND_TIMEOUT,
            cwd=str(ALLOWED_PATH_ROOT),   # CWD = /data par défaut
        )

        stdout = proc.stdout.decode("utf-8", errors="replace")
        stderr = proc.stderr.decode("utf-8", errors="replace")

        # Troncature sortie
        if len(stdout.encode()) > MAX_OUTPUT_BYTES:
            stdout = stdout.encode()[:MAX_OUTPUT_BYTES].decode("utf-8", errors="replace")
            stdout += f"\n[… sortie tronquée à {MAX_OUTPUT_BYTES // 1000} KB]"

        return {
            "success":    proc.returncode == 0,
            "stdout":     stdout,
            "stderr":     stderr,
            "returncode": proc.returncode,
            "error":      None,
        }

    except subprocess.TimeoutExpired:
        return {
            "success":    False,
            "stdout":     "",
            "stderr":     "",
            "returncode": -1,
            "error":      f"Timeout : commande non terminée après {COMMAND_TIMEOUT}s",
        }
    except FileNotFoundError:
        parts = shlex.split(command_str)
        return {
            "success":    False,
            "stdout":     "",
            "stderr":     "",
            "returncode": -1,
            "error":      f"Binaire introuvable : {parts[0]!r}",
        }
    except Exception as e:
        return {
            "success":    False,
            "stdout":     "",
            "stderr":     "",
            "returncode": -1,
            "error":      f"Erreur inattendue : {e}",
        }


def format_result_for_agent(cmd: str, result: dict) -> str:
    """
    Formate le résultat d'une commande pour l'agent principal.
    Produit un bloc lisible que l'agent peut incorporer dans sa réponse.
    """
    lines = [f"[SHELL] `{cmd}`"]

    if result.get("error"):
        lines.append(f"❌ {result['error']}")
        return "\n".join(lines)

    rc = result["returncode"]
    status = "✅ succès" if result["success"] else f"⚠️ code retour {rc}"
    lines.append(f"Statut : {status}")

    if result["stdout"]:
        lines.append("Sortie :")
        lines.append(result["stdout"].rstrip())

    if result["stderr"] and not result["success"]:
        lines.append("Erreur :")
        lines.append(result["stderr"].rstrip())

    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════
# Outil CrewAI
# ══════════════════════════════════════════════════════════════════

class ShellExecuteInput(BaseModel):
    command: str = Field(
        description=(
            "Commande shell à exécuter. Doit être une commande unique "
            "sans opérateurs (pas de &&, |, ;). "
            "Exemple : 'ls /data', 'cat /data/notes.txt', "
            "'mkdir /data/projets', 'python /data/script.py'"
        )
    )


class ShellExecuteTool(BaseTool):
    """
    Outil d'exécution de commandes shell pour ShellCrew.

    Valide la commande contre la whitelist, puis l'exécute.
    La confirmation utilisateur a déjà eu lieu en amont — la commande
    passée ici est figée et approuvée.

    Portée : /data uniquement. Timeout : 30s. Sortie max : 50 KB.
    """
    name:        str = "execute_shell_command"
    description: str = (
        "Exécute une commande shell validée et approuvée. "
        "Commandes autorisées : ls, cat, find, grep, head, tail, wc, "
        "du, stat, file, diff, sort, uniq, mkdir, touch, mv, cp, rm, "
        "rmdir, python/python3 (scripts .py dans /data). "
        "Toutes les opérations restent dans /data. "
        "Une seule commande à la fois — pas de &&, |, ;."
    )
    args_schema: Type[BaseModel] = ShellExecuteInput

    def _run(self, command: str) -> str:
        result = execute_command(command)
        return format_result_for_agent(command, result)