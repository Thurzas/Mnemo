"""
patch_crewai_tracing.py — Désactive le prompt interactif de tracing CrewAI

CrewAI >=0.130 affiche "Would you like to view your execution traces?"
même avec CREWAI_DISABLE_EXECUTION_TRACE_VIEWER=true.
Ce script patche directement les fichiers source de crewai installés.

Exécuté une seule fois au build Docker.
"""
import glob
import os
import site
import sys


MARKERS = [
    "Would you like to view your execution traces",
    "View execution traces",
    "view_execution_traces",
    "Tracing Preference Saved",
    "CREWAI_DISABLE_EXECUTION_TRACE_VIEWER",
]

PATCHES = [
    # Prompt interactif → message silencieux
    (
        "Would you like to view your execution traces?",
        "Execution traces disabled.",
    ),
    # input() avec timeout → False directement
    (
        'get_user_input("Would you like to view',
        'False  # patched — was: get_user_input("Would you like to view',
    ),
]

patched_files = []

search_paths = site.getsitepackages()
# Ajoute aussi les paths non-standard
search_paths += ["/usr/local/lib/python3.12/site-packages"]

for sp in search_paths:
    pattern = os.path.join(sp, "crewai", "**", "*.py")
    for filepath in glob.glob(pattern, recursive=True):
        try:
            original = open(filepath, encoding="utf-8").read()
        except (OSError, UnicodeDecodeError):
            continue

        # Ce fichier contient-il un des marqueurs ?
        if not any(m in original for m in MARKERS):
            continue

        patched = original

        # Applique les remplacements textuels
        for old, new in PATCHES:
            patched = patched.replace(old, new)

        # Patch plus agressif : toute fonction dont le nom contient
        # "trace_viewer" ou "execution_trace" → return immédiat
        import re
        def noop_trace_fn(m):
            sig = m.group(0)
            indent = "    "
            return sig + f"\n{indent}return  # patched — trace viewer disabled\n{indent}original_body_follows = ("
        
        patched = re.sub(
            r"def \w*(trace_viewer|execution_trace|show_trace)\w*\s*\([^)]*\)\s*(?:->.*?)?:",
            noop_trace_fn,
            patched,
        )

        if patched != original:
            open(filepath, "w", encoding="utf-8").write(patched)
            patched_files.append(filepath)
            print(f"  ✓ patché : {os.path.relpath(filepath, sp)}")

if patched_files:
    print(f"\n✅ {len(patched_files)} fichier(s) patchés")
else:
    print("ℹ️  Aucun fichier patché (marqueurs non trouvés — version incompatible ou déjà patchée)")
    sys.exit(0)