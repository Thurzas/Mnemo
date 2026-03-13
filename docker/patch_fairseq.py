"""
patch_fairseq.py — Corrige les mutable defaults dans fairseq/hydra.

N'importe PAS fairseq — accès filesystem uniquement pour éviter
de déclencher l'erreur qu'on cherche justement à corriger.

Pattern ciblé : `    name: TypeName = TypeName()`
Remplacement  : `    name: TypeName = field(default_factory=TypeName)`
"""
import re
import shutil
import sys
from pathlib import Path

# ── Trouver site-packages / dist-packages ─────────────────────────
# Ordre : /opt/conda (pytorch base image) → /usr/local/lib site-packages
#       → /usr/local/lib dist-packages (Ubuntu 20.04 + pip apt-installé)
site = (
    next(Path("/opt/conda/lib").glob("*/site-packages"), None)
    or next(Path("/usr/local/lib").glob("*/site-packages"), None)
    or next(Path("/usr/local/lib").glob("*/dist-packages"), None)
)
if not site:
    print("ERROR: site-packages introuvable")
    sys.exit(1)

print(f"site-packages : {site}")

# Pattern : début de ligne, indentation optionnelle, `field: Type = Type()`
# Backreference \3 garantit que le type de l'annotation == le constructeur appelé
MUTABLE_RE = re.compile(
    r"^( *)(\w+): (\w+) = \3\(\)\s*$",
    re.MULTILINE,
)

patched = 0

for pkg_name in ("fairseq", "hydra"):
    pkg_dir = site / pkg_name
    if not pkg_dir.is_dir():
        print(f"[skip] {pkg_name} non trouvé dans {site}")
        continue

    for py_file in sorted(pkg_dir.rglob("*.py")):
        try:
            txt = py_file.read_text(encoding="utf-8")
        except OSError:
            continue

        if not MUTABLE_RE.search(txt):
            continue

        # Ajouter `field` à l'import dataclasses existant (ou en créer un)
        if "from dataclasses import" in txt:
            txt = re.sub(
                r"(from dataclasses import )([^\n]+)",
                lambda m: (
                    m.group(1) + m.group(2).rstrip() + ", field"
                    if "field" not in m.group(2).split(",")
                    else m.group(0)
                ),
                txt,
                count=1,
            )
        else:
            txt = "from dataclasses import field\n" + txt

        # Remplacer les mutable defaults
        txt = MUTABLE_RE.sub(
            lambda m: f"{m.group(1)}{m.group(2)}: {m.group(3)} = field(default_factory={m.group(3)})",
            txt,
        )

        py_file.write_text(txt, encoding="utf-8")

        # Supprimer le bytecode compilé pour forcer la relecture du .py patché
        pycache = py_file.parent / "__pycache__"
        if pycache.exists():
            shutil.rmtree(pycache)

        print(f"  patched: {py_file.relative_to(site)}")
        patched += 1

# ── Patch fairseq/dataclass/initialize.py ────────────────────────
# Bug omegaconf 2.0.6 + torch CUDA : _MISSING_TYPE sur FairseqConfig.
# initialize.py capture l'exception, la log (format "{key} - {err}"),
# puis la RE-RAISE. Pour l'inférence RVC on n'a pas besoin de l'enregistrement
# Hydra → on supprime le re-raise.
init_file = site / "fairseq/dataclass/initialize.py"
if init_file.exists():
    txt = init_file.read_text(encoding="utf-8")

    # Pattern A : logger.error(...) suivi d'un raise sur la ligne suivante
    patched_txt = re.sub(
        r"(logger\.error\([^\n]+\))\n(\s+)(raise\b)",
        r"\1\n\2pass  # patched: suppressed re-raise (omegaconf 2.0.6 compat)",
        txt,
    )

    # Pattern B (fallback) : cs.store(...) en une ligne, on wrappe en try-except
    if patched_txt == txt:
        patched_txt = re.sub(
            r"^(\s+)(cs\.store\([^)]*\))",
            r"\1try:\n\1    \2\n\1except Exception:\n\1    pass  # patched",
            txt,
            flags=re.MULTILINE,
        )

    if patched_txt != txt:
        init_file.write_text(patched_txt, encoding="utf-8")
        pycache = init_file.parent / "__pycache__"
        if pycache.exists():
            shutil.rmtree(pycache)
        print(f"  patched: fairseq/dataclass/initialize.py")
        patched += 1
    else:
        # Diagnostique : affiche le contenu pour écrire le bon pattern au prochain cycle
        print(f"  [warn] initialize.py : aucun pattern trouvé — contenu :")
        print(txt[:800])
else:
    print(f"  [skip] fairseq/dataclass/initialize.py non trouvé dans {site}")

print(f"\n{patched} fichier(s) patché(s).")