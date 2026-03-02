"""
shell_whitelist.py — Whitelist de commandes autorisées pour le ShellCrew

Ce fichier définit les seules commandes qu'un agent shell peut exécuter.
Toute commande non présente dans cette liste est rejetée AVANT confirmation
utilisateur — elle n'atteint jamais le subprocess.

Principes :
  - Portée limitée à /data (pas d'accès au système de fichiers hôte)
  - Trois familles autorisées : fichiers /data, lecture système, scripts Python
  - Aucune commande réseau (curl, wget, nc, ssh...)
  - Aucune commande d'élévation de droits (sudo, su, chmod, chown...)
  - Aucun interpréteur arbitraire (bash -c, sh -c, eval...)

Utilisé par : ShellCrew (phase 3.2)
"""

from pathlib import Path

# ══════════════════════════════════════════════════════════════════
# Famille 1 — Gestion de fichiers dans /data
# ══════════════════════════════════════════════════════════════════

FILE_COMMANDS = {
    "mkdir",    # créer un répertoire
    "touch",    # créer un fichier vide / mettre à jour mtime
    "mv",       # déplacer / renommer
    "cp",       # copier
    "rm",       # supprimer (args vérifiés séparément)
    "rmdir",    # supprimer répertoire vide
}

# ══════════════════════════════════════════════════════════════════
# Famille 2 — Lecture système (lecture seule, pas d'écriture)
# ══════════════════════════════════════════════════════════════════

READ_COMMANDS = {
    "ls",       # lister un répertoire
    "cat",      # afficher le contenu d'un fichier
    "find",     # rechercher des fichiers
    "grep",     # rechercher dans des fichiers
    "head",     # premières lignes
    "tail",     # dernières lignes
    "wc",       # compter lignes/mots/chars
    "du",       # taille disque
    "stat",     # métadonnées fichier
    "file",     # type de fichier
    "diff",     # comparer deux fichiers
    "sort",     # trier
    "uniq",     # dédupliquer
}

# ══════════════════════════════════════════════════════════════════
# Famille 3 — Scripts Python dans /data
# Seul `python` est autorisé, et uniquement pour des fichiers .py
# situés dans /data — pas d'arguments arbitraires.
# ══════════════════════════════════════════════════════════════════

PYTHON_COMMANDS = {
    "python",
    "python3",
}

# ══════════════════════════════════════════════════════════════════
# Whitelist complète — union des trois familles
# ══════════════════════════════════════════════════════════════════

ALLOWED_COMMANDS: set[str] = FILE_COMMANDS | READ_COMMANDS | PYTHON_COMMANDS

# ══════════════════════════════════════════════════════════════════
# Blacklist explicite — commandes interdites même si le binaire existe
# Sert de filet de sécurité supplémentaire.
# ══════════════════════════════════════════════════════════════════

FORBIDDEN_COMMANDS: set[str] = {
    # Shells et interpréteurs arbitraires
    "bash", "sh", "zsh", "fish", "dash", "ksh", "tcsh",
    "perl", "ruby", "node", "nodejs", "lua", "php",
    # Élévation de droits
    "sudo", "su", "doas", "pkexec", "newgrp",
    # Modification de permissions
    "chmod", "chown", "chgrp", "setcap",
    # Réseau
    "curl", "wget", "nc", "netcat", "ncat", "ssh", "scp", "rsync",
    "ping", "nmap", "traceroute", "dig", "nslookup",
    # Dangereux
    "eval", "exec", "xargs",
    "dd", "mkfs", "mount", "umount",
    "kill", "killall", "pkill",
    "crontab", "at", "systemctl", "service",
    "apt", "apt-get", "dpkg", "pip", "pip3",
}

# ══════════════════════════════════════════════════════════════════
# Contraintes sur les arguments
# ══════════════════════════════════════════════════════════════════

# Chemin racine autorisé — toutes les opérations sur fichiers
# doivent rester sous /data
ALLOWED_PATH_ROOT = Path("/data")

# Extensions autorisées pour les scripts Python
ALLOWED_PYTHON_EXTENSIONS = {".py"}

# Flags rm interdits — rm -rf sans restriction est trop dangereux
RM_FORBIDDEN_FLAGS = {"-rf", "-fr", "--force", "-r", "--recursive"}

# Taille maximale de la sortie retournée (en bytes)
# Evite qu'un `cat gros_fichier` sature la mémoire
MAX_OUTPUT_BYTES = 50_000  # 50 KB


# ══════════════════════════════════════════════════════════════════
# Fonctions de validation (utilisées par ShellCrew)
# ══════════════════════════════════════════════════════════════════

def is_command_allowed(cmd: str) -> bool:
    """Retourne True si la commande de base est dans la whitelist."""
    return cmd in ALLOWED_COMMANDS and cmd not in FORBIDDEN_COMMANDS


def is_path_safe(path_str: str) -> bool:
    """
    Retourne True si le chemin résolu est sous ALLOWED_PATH_ROOT.
    Protège contre les path traversal (../../etc/passwd).
    """
    try:
        resolved = Path(path_str).resolve()
        return resolved == ALLOWED_PATH_ROOT or \
               ALLOWED_PATH_ROOT in resolved.parents
    except (ValueError, OSError):
        return False


def is_python_script_safe(script_path: str) -> bool:
    """Retourne True si le script est un .py sous /data."""
    p = Path(script_path)
    return (
        p.suffix in ALLOWED_PYTHON_EXTENSIONS
        and is_path_safe(script_path)
    )


def validate_rm_args(args: list[str]) -> tuple[bool, str]:
    """
    Valide les arguments de `rm`.
    Retourne (ok, raison_si_refus).
    rm -rf est interdit — rm fichier.txt est autorisé.
    """
    for arg in args:
        if arg.startswith("-"):
            # Décompose les flags combinés ex: -rf → {-r, -f}
            flags = {f"-{c}" for c in arg.lstrip("-")} | {arg}
            forbidden = flags & RM_FORBIDDEN_FLAGS
            if forbidden:
                return False, f"flag interdit : {', '.join(forbidden)}"
    return True, ""


def describe_command_policy() -> str:
    """Retourne une description lisible de la politique pour l'agent LLM."""
    return (
        "Commandes autorisées :\n"
        f"  Fichiers /data : {', '.join(sorted(FILE_COMMANDS))}\n"
        f"  Lecture        : {', '.join(sorted(READ_COMMANDS))}\n"
        f"  Python         : scripts .py dans /data uniquement\n"
        "\nContraintes :\n"
        "  - Toutes les opérations fichiers restent sous /data\n"
        "  - rm : sans flag -r/-f (fichiers individuels uniquement)\n"
        "  - python : fichiers .py dans /data uniquement\n"
        "  - Sortie tronquée à 50 KB\n"
        "  - Aucune commande réseau\n"
        "  - Aucun shell arbitraire (bash, sh...)\n"
    )