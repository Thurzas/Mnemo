"""
conftest.py — Configuration pytest pour les tests Mnemo

Ajoute automatiquement src/ au sys.path pour que `import Mnemo` fonctionne
sans avoir à installer le package en editable mode.

Structure attendue :
  waifuclawd/
  ├── src/
  │   └── Mnemo/
  │       ├── main.py
  │       ├── crew.py
  │       └── tools/
  └── tests/
      ├── conftest.py     ← ce fichier
      └── test_curiosity.py
"""

import sys
from pathlib import Path

# Remonte jusqu'à la racine du projet (parent de tests/) puis entre dans src/
_ROOT = Path(__file__).parent.parent
_SRC  = _ROOT / "src"

if _SRC.exists():
    sys.path.insert(0, str(_SRC))
else:
    # Fallback si les tests sont dans src/Mnemo/tests/ directement
    sys.path.insert(0, str(_ROOT))

# test_deadline_terrain.py est un script standalone (python test_deadline_terrain.py).
# Il mock numpy/crewai/ollama au niveau module, ce qui polluerait sys.modules pour
# tous les autres tests si pytest l'importait. On l'exclut de la collection.
collect_ignore = ["test_deadline_terrain.py"]