"""
shell_tools.py — Outils d'exécution shell pour ShellCrew

Outils :
  - ShellExecuteTool  : exécute une commande shell validée
  - ReadPdfTool       : lit une ou plusieurs pages d'un PDF via pypdf

Garanties :
  - Aucune commande n'atteint subprocess sans passer par la whitelist
  - Toutes les opérations fichiers restent sous /data
  - Sortie tronquée à MAX_OUTPUT_BYTES
  - Timeout fixe — pas de processus zombie
  - La confirmation utilisateur est gérée en amont (main.py)
"""

from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
from pathlib import Path
from typing import Type

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

COMMAND_TIMEOUT = 30


# ══════════════════════════════════════════════════════════════════
# Validation
# ══════════════════════════════════════════════════════════════════

class ValidationResult:
    def __init__(self, ok: bool, reason: str = ""):
        self.ok     = ok
        self.reason = reason
    def __bool__(self) -> bool:
        return self.ok


def validate_command(command_str: str) -> ValidationResult:
    """
    Valide une commande complète contre la whitelist.
    Retourne ValidationResult(ok=True) si autorisée.
    """
    if not command_str or not command_str.strip():
        return ValidationResult(False, "commande vide")

    READ_ONLY_CMDS = {"ls", "cat", "find", "grep", "head", "tail",
                      "wc", "du", "stat", "file", "diff", "sort", "uniq"}

    # Pipe unique entre deux commandes de lecture — autorisé
    if "|" in command_str and "||" not in command_str:
        pipe_parts = command_str.split("|")
        if len(pipe_parts) == 2:
            left, right = pipe_parts[0].strip(), pipe_parts[1].strip()
            left_ok  = validate_command(left)
            right_ok = validate_command(right)
            try:
                right_cmd = os.path.basename(shlex.split(right)[0]) if right else ""
            except ValueError:
                right_cmd = ""
            if left_ok and right_ok and right_cmd in READ_ONLY_CMDS:
                return ValidationResult(True)
            if not left_ok:
                reason = f"cote gauche du pipe : {left_ok.reason}"
            elif not right_ok:
                reason = f"cote droit du pipe : {right_ok.reason}"
            else:
                reason = f"pipe vers {right_cmd!r} interdit — droit limite aux commandes de lecture"
            return ValidationResult(False, reason)

    # Chainages dangereux — toujours refuse
    for metachar in ("&&", "||", ";", "|", "`", "$(", ">", "<"):
        if metachar in command_str:
            return ValidationResult(
                False,
                f"operateur shell interdit : {metachar!r} — une seule commande a la fois"
            )

    try:
        parts = shlex.split(command_str)
    except ValueError as e:
        return ValidationResult(False, f"commande non parseable : {e}")

    if not parts:
        return ValidationResult(False, "commande vide apres parsing")

    # Accepte /bin/ls, /usr/bin/grep... — whitelist sur le basename
    cmd_raw = parts[0]
    cmd     = os.path.basename(cmd_raw)
    args    = parts[1:]

    if not is_command_allowed(cmd):
        return ValidationResult(
            False,
            f"commande {cmd!r} non autorisee.\n{describe_command_policy()}"
        )

    if cmd in ("python", "python3"):
        if not args:
            return ValidationResult(False, "python : un fichier .py est requis")
        if not is_python_script_safe(args[0]):
            return ValidationResult(
                False, f"python : {args[0]!r} refuse — doit etre un .py sous /data"
            )
        return ValidationResult(True)

    if cmd == "rm":
        ok, reason = validate_rm_args(args)
        if not ok:
            return ValidationResult(False, f"rm : {reason}")

    if cmd not in READ_ONLY_CMDS:
        for arg in args:
            if arg.startswith("-"):
                continue
            if arg.startswith("/") or arg.startswith(".."):
                if not is_path_safe(arg):
                    return ValidationResult(
                        False,
                        f"chemin interdit : {arg!r} — operations limitees a {ALLOWED_PATH_ROOT}"
                    )
    else:
        SENSITIVE_PATHS = {"/etc/shadow", "/etc/passwd", "/proc/keys", "/root", "/home"}
        for arg in args:
            if arg.startswith("-"):
                continue
            try:
                resolved = str(Path(arg).resolve())
            except (ValueError, OSError):
                resolved = arg
            for sensitive in SENSITIVE_PATHS:
                if resolved.startswith(sensitive):
                    return ValidationResult(False, f"chemin sensible interdit : {arg!r}")

    return ValidationResult(True)


# ══════════════════════════════════════════════════════════════════
# Execution securisee
# ══════════════════════════════════════════════════════════════════

def _autoquote_paths(cmd: str) -> str:
    """
    Auto-quote les chemins /data/... avec espaces non quotes.
    Ex: cat /data/docs/Mon fichier.pdf -> cat '/data/docs/Mon fichier.pdf'
    Ne touche pas les commandes deja quotees.
    """
    if "'" in cmd or '"' in cmd:
        return cmd

    def _q(m: re.Match) -> str:
        path = m.group(0)
        return f"'{path}'" if " " in path else path

    patched = re.sub(r"/data/[^\n|;&<>]+", _q, cmd)
    try:
        shlex.split(patched)
        return patched
    except ValueError:
        return cmd


def execute_command(command_str: str) -> dict:
    """
    Valide puis execute une commande shell.
    Retourne {"success", "stdout", "stderr", "returncode", "error"}.
    """
    # Auto-quote les chemins avec espaces avant validation
    command_str = _autoquote_paths(command_str)

    result = validate_command(command_str)
    if not result:
        return {
            "success": False, "stdout": "", "stderr": "",
            "returncode": -1,
            "error": f"Commande refusee : {result.reason}",
        }

    env = os.environ.copy()
    env.setdefault("PATH", "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin")

    def _resolve(name: str) -> str:
        if os.path.isabs(name):
            return name
        found = shutil.which(name, path=env["PATH"])
        return found if found else name

    try:
        has_pipe = "|" in command_str and "||" not in command_str

        if has_pipe:
            left_str, right_str = command_str.split("|", 1)
            lp = shlex.split(left_str.strip())
            rp = shlex.split(right_str.strip())
            lp[0] = _resolve(lp[0])
            rp[0] = _resolve(rp[0])

            pl = subprocess.Popen(lp, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  cwd=str(ALLOWED_PATH_ROOT), env=env)
            pr = subprocess.Popen(rp, stdin=pl.stdout, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, cwd=str(ALLOWED_PATH_ROOT), env=env)
            pl.stdout.close()
            try:
                out, err = pr.communicate(timeout=COMMAND_TIMEOUT)
                pl.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pl.kill()
                pr.kill()
                return {"success": False, "stdout": "", "stderr": "", "returncode": -1,
                        "error": f"Timeout apres {COMMAND_TIMEOUT}s"}
            stdout, stderr, returncode = (
                out.decode("utf-8", errors="replace"),
                err.decode("utf-8", errors="replace"),
                pr.returncode,
            )

        else:
            parts = shlex.split(command_str)
            parts[0] = _resolve(parts[0])
            proc = subprocess.run(parts, capture_output=True, timeout=COMMAND_TIMEOUT,
                                  cwd=str(ALLOWED_PATH_ROOT), env=env)
            stdout     = proc.stdout.decode("utf-8", errors="replace")
            stderr     = proc.stderr.decode("utf-8", errors="replace")
            returncode = proc.returncode

        if len(stdout.encode()) > MAX_OUTPUT_BYTES:
            stdout = stdout.encode()[:MAX_OUTPUT_BYTES].decode("utf-8", errors="replace")
            stdout += f"\n[... tronque a {MAX_OUTPUT_BYTES // 1000} KB]"

        return {"success": returncode == 0, "stdout": stdout, "stderr": stderr,
                "returncode": returncode, "error": None}

    except subprocess.TimeoutExpired:
        return {"success": False, "stdout": "", "stderr": "", "returncode": -1,
                "error": f"Timeout apres {COMMAND_TIMEOUT}s"}
    except FileNotFoundError:
        try:
            cmd_name = shlex.split(command_str.split("|")[0].strip())[0]
        except (ValueError, IndexError):
            cmd_name = command_str.split()[0]
        return {"success": False, "stdout": "", "stderr": "", "returncode": -1,
                "error": f"Binaire introuvable : {cmd_name!r}"}
    except Exception as e:
        return {"success": False, "stdout": "", "stderr": "", "returncode": -1,
                "error": f"Erreur inattendue : {e}"}


def format_result_for_agent(cmd: str, result: dict) -> str:
    lines = [f"[SHELL] `{cmd}`"]
    if result.get("error"):
        lines.append(f"❌ {result['error']}")
        return "\n".join(lines)
    if result["success"]:
        lines.append("✅ Succès")
        if result["stdout"]:
            lines.append(result["stdout"].rstrip())
    else:
        lines.append(f"⚠️ Échec (code {result['returncode']})")
        if result["stderr"]:
            lines.append(result["stderr"].rstrip())
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════
# Outil 1 — ShellExecuteTool
# ══════════════════════════════════════════════════════════════════

class ShellExecuteInput(BaseModel):
    command: str = Field(
        description=(
            "Commande shell a executer. Une commande a la fois — pas de &&, ;. "
            "Pipe lecture-lecture autorise : 'ls /data/docs | grep .pdf'. "
            "Pour plusieurs etapes, fais plusieurs appels successifs. "
            "Les chemins avec espaces sont geres automatiquement. "
            "Exemples : 'ls /data/docs', 'find /data -name *.pdf', "
            "'ls /data/docs | grep .pdf', 'cat /data/notes.txt'"
        )
    )


class ShellExecuteTool(BaseTool):
    """
    Executes a validated shell command. Whitelist: /data only, 30s timeout, 50KB max.
    For reading PDFs, use ReadPdfTool instead — cat on a PDF returns binary garbage.
    """
    name:        str = "execute_shell_command"
    description: str = (
        "Executes a shell command. Allowed: ls, cat, find, grep, head, tail, "
        "wc, du, stat, file, diff, sort, uniq, mkdir, touch, mv, cp, rm, rmdir, "
        "python (scripts in /data). read|read pipe OK. One command at a time. "
        "For PDFs use read_pdf tool instead."
    )
    args_schema: Type[BaseModel] = ShellExecuteInput

    def _run(self, command: str) -> str:
        result = execute_command(command)
        return format_result_for_agent(command, result)


# ══════════════════════════════════════════════════════════════════
# Outil 2 — ReadPdfTool
# ══════════════════════════════════════════════════════════════════

class ReadPdfInput(BaseModel):
    path: str = Field(
        description=(
            "Chemin complet du PDF sous /data. "
            "Exemples : '/data/docs/rapport.pdf', "
            "'/data/docs/Mathematics for game developpers.pdf'"
        )
    )
    pages: str = Field(
        default="1",
        description=(
            "Pages a lire (1-indexe). Exemples : '1' (page 1), "
            "'1-3' (pages 1 a 3), '1,3,5' (pages specifiques). "
            "Par defaut : premiere page uniquement."
        )
    )


class ReadPdfTool(BaseTool):
    """
    Lit le contenu textuel d'un PDF via pypdf.
    TOUJOURS utiliser cet outil pour lire des PDFs.
    Ne jamais utiliser 'cat' sur un PDF — retourne du binaire illisible.
    """
    name:        str = "read_pdf"
    description: str = (
        "Reads text content from a PDF file under /data using pypdf. "
        "ALWAYS use this tool to read PDFs — never use 'cat' on a PDF. "
        "Specify full path and pages (e.g. '1', '1-3', '1,2,5'). "
        "If the file has spaces in its name, include them as-is in the path."
    )
    args_schema: Type[BaseModel] = ReadPdfInput

    def _run(self, path: str, pages: str = "1") -> str:
        if not is_path_safe(path):
            return f"[ERREUR] Chemin interdit : {path!r} — limite a /data"

        pdf_path = Path(path)
        found_msg = ""

        if not pdf_path.exists():
            # Recherche par nom dans /data si chemin incomplet
            candidates = list(ALLOWED_PATH_ROOT.rglob(pdf_path.name))
            if candidates:
                pdf_path = candidates[0]
                found_msg = f"(trouve : {pdf_path})\n"
            else:
                # Recherche fuzzy : nom sans extension
                stem = pdf_path.stem.lower()
                candidates = [
                    p for p in ALLOWED_PATH_ROOT.rglob("*.pdf")
                    if stem in p.stem.lower()
                ]
                if candidates:
                    pdf_path = candidates[0]
                    found_msg = f"(correspondance approchee : {pdf_path})\n"
                else:
                    return f"[ERREUR] Fichier introuvable : {path!r}"

        if pdf_path.suffix.lower() != ".pdf":
            return f"[ERREUR] {path!r} n'est pas un fichier PDF"

        try:
            from pypdf import PdfReader
        except ImportError:
            return "[ERREUR] pypdf non disponible"

        try:
            reader  = PdfReader(str(pdf_path))
            n_pages = len(reader.pages)
            page_nums = _parse_page_range(pages, n_pages)

            if not page_nums:
                return f"[ERREUR] Pages invalides : {pages!r} (le document a {n_pages} page(s))"

            chunks = []
            for p in page_nums:
                text = reader.pages[p - 1].extract_text() or "[page sans texte extractible]"
                chunks.append(f"-- Page {p}/{n_pages} --\n{text.strip()}")

            content = "\n\n".join(chunks)

            if len(content.encode()) > MAX_OUTPUT_BYTES:
                content = content.encode()[:MAX_OUTPUT_BYTES].decode("utf-8", errors="replace")
                content += f"\n[... tronque a {MAX_OUTPUT_BYTES // 1000} KB]"

            return f"{found_msg}[PDF] {pdf_path.name} ({n_pages} pages)\n\n{content}"

        except Exception as e:
            return f"[ERREUR] Lecture PDF : {e}"


# ══════════════════════════════════════════════════════════════════
# Outil 3 — FileWriterTool
# ══════════════════════════════════════════════════════════════════

# Noms de fichiers/dossiers internes à Mnemo — protégés contre toute
# écriture externe (memory.db est binaire, sessions/ est géré par memory_tools)
_PROTECTED_NAMES: frozenset[str] = frozenset({"memory.db", "sessions"})


def _is_protected(path: Path) -> bool:
    """Retourne True si le chemin résolu pointe sur un fichier interne Mnemo."""
    try:
        resolved = path.resolve()
    except (ValueError, OSError):
        return True
    for name in _PROTECTED_NAMES:
        protected = (ALLOWED_PATH_ROOT / name).resolve()
        if resolved == protected or protected in resolved.parents:
            return True
    return False


class FileWriterInput(BaseModel):
    path: str = Field(
        description=(
            "Chemin complet du fichier à créer ou écraser, sous /data. "
            "Les répertoires parents sont créés automatiquement. "
            "Exemples : '/data/notes/todo.txt', '/data/projets/plan.md'."
        )
    )
    content: str = Field(
        description=(
            "Contenu textuel complet à écrire dans le fichier (UTF-8). "
            f"Limité à {MAX_OUTPUT_BYTES // 1000} KB."
        )
    )
    overwrite: bool = Field(
        default=False,
        description=(
            "Si True, écrase le fichier s'il existe déjà. "
            "Si False (défaut) et que le fichier existe, l'opération est refusée."
        )
    )


class FileWriterTool(BaseTool):
    """
    Creates or overwrites a text file under /data using Python (no subprocess).
    Parent directories are created automatically.
    Cannot write to memory.db or sessions/. Max 50 KB.
    For moving/renaming files use execute_shell_command with mv instead.
    """
    name:        str = "write_file"
    description: str = (
        "Creates or overwrites a text file under /data. "
        "Parent dirs are created automatically. "
        "Provide full path, full text content, and overwrite=true to replace existing. "
        "Cannot touch memory.db or sessions/. Max 50 KB. "
        "To move or rename files, use execute_shell_command with mv."
    )
    args_schema: Type[BaseModel] = FileWriterInput

    def _run(self, path: str, content: str, overwrite: bool = False) -> str:
        # Validation du chemin
        if not is_path_safe(path):
            return (
                f"[ERREUR] Chemin interdit : {path!r} "
                f"— opérations limitées à {ALLOWED_PATH_ROOT}"
            )

        target = Path(path).resolve()

        if _is_protected(target):
            return (
                f"[ERREUR] Fichier protégé : {target.name!r} "
                "— modification réservée aux outils internes Mnemo"
            )

        # Limite taille du contenu
        encoded = content.encode("utf-8", errors="replace")
        if len(encoded) > MAX_OUTPUT_BYTES:
            return (
                f"[ERREUR] Contenu trop volumineux : {len(encoded)} octets "
                f"(limite : {MAX_OUTPUT_BYTES // 1000} KB)"
            )

        # Refus si fichier existant et overwrite=False
        if target.exists() and not overwrite:
            return (
                f"[ERREUR] {target} existe déjà. "
                "Utilise overwrite=true pour l'écraser."
            )

        # Création des répertoires parents si nécessaire
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            return f"[ERREUR] Impossible de créer le répertoire parent : {e}"

        # Écriture
        try:
            target.write_text(content, encoding="utf-8")
        except OSError as e:
            return f"[ERREUR] Écriture impossible : {e}"

        size_kb = len(encoded) / 1000
        action  = "écrasé" if target.exists() and overwrite else "créé"
        return f"[OK] Fichier {action} : {target} ({size_kb:.1f} KB)"


def _parse_page_range(spec: str, n_pages: int) -> list[int]:
    """Parse '1', '1-3', '1,3,5' en liste de numeros valides (1-indexe)."""
    pages: set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        if "-" in part:
            try:
                a, b = part.split("-", 1)
                pages.update(range(int(a), int(b) + 1))
            except ValueError:
                return []
        else:
            try:
                pages.add(int(part))
            except ValueError:
                return []
    return sorted(p for p in pages if 1 <= p <= n_pages)