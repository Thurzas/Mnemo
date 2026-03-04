import os
#!/usr/bin/env python3
"""
retrain_router.py
Fusionne training_data.jsonl + uncertain_cases.jsonl et re-entraine le modele.
A lancer periodiquement quand uncertain_cases.jsonl a accumule assez de cas.

Usage:
    python retrain_router.py [--min-new 20]
"""
import argparse, json, sys
from pathlib import Path
from collections import Counter

# Tous les fichiers de données dans /data (WORKDIR Docker)
# → pas dans /app qui est read-only en production
_DATA = Path(os.getenv("DATA_PATH", ".")).resolve()
BASE       = Path(__file__).parent
TRAIN_DATA = _DATA / "training_data.jsonl"
UNCERTAIN  = _DATA / "uncertain_cases.jsonl"
OUTPUT     = _DATA / "router_model.joblib"
ROUTES     = ["conversation", "shell", "calendar", "scheduler"]


def load_jsonl(path):
    items = []
    if not path.exists():
        return items
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if line:
            items.append(json.loads(line))
    return items


def dedup(items):
    seen, out = set(), []
    for item in items:
        k = item["text"].lower().strip()
        if k not in seen:
            seen.add(k)
            out.append(item)
    return out


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--min-new", type=int, default=20,
                   help="Nombre minimum de nouveaux cas pour declencher le re-train (defaut: 20)")
    p.add_argument("--force",   action="store_true",
                   help="Re-entraine meme si --min-new non atteint")
    args = p.parse_args()

    train    = load_jsonl(TRAIN_DATA)
    uncertain = load_jsonl(UNCERTAIN)

    if not uncertain:
        print("Aucun cas incertain collecte — rien a faire.")
        print(f"Les cas sont collectes automatiquement dans : {UNCERTAIN}")
        sys.exit(0)

    print(f"Donnees actuelles    : {len(train)} exemples dans {TRAIN_DATA.name}")
    print(f"Cas incertains       : {len(uncertain)} dans {UNCERTAIN.name}")

    # Stats des nouveaux cas par route
    counter = Counter(d["route"] for d in uncertain)
    for r in ROUTES:
        print(f"  {r:15s}: {counter.get(r, 0):4d} nouveaux cas")

    if len(uncertain) < args.min_new and not args.force:
        print(f"\nSeulement {len(uncertain)} nouveaux cas (min: {args.min_new}).")
        print("Attends d'en accumuler plus, ou utilise --force.")
        sys.exit(0)

    # Fusion + dedup (uncertain en dernier = les labels LLM ont priorite sur doublons)
    merged = dedup(train + uncertain)
    print(f"\nDataset fusionne     : {len(merged)} exemples uniques")
    for r in ROUTES:
        n = sum(1 for d in merged if d["route"] == r)
        print(f"  {r:15s}: {n:4d}")

    # Sauvegarde le dataset fusionne
    with open(TRAIN_DATA, "w", encoding="utf-8") as f:
        for item in merged:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")
    print(f"\nDataset mis a jour : {TRAIN_DATA}")

    # Lance train_router
    import subprocess
    result = subprocess.run(
        [sys.executable, str(BASE / "train_router.py"),
         "--data", str(TRAIN_DATA),
         "--output", str(OUTPUT)],
        check=False,
    )

    if result.returncode == 0:
        # Archive + vide uncertain_cases.jsonl
        archive = BASE / "uncertain_cases.jsonl.bak"
        UNCERTAIN.rename(archive)
        UNCERTAIN.touch()
        print(f"\nCas incertains archives dans {archive.name}")
        print("Re-train termine. Relance le container pour charger le nouveau modele.")
    else:
        print("\nErreur pendant le re-train — uncertain_cases.jsonl conserve.")
        sys.exit(1)


if __name__ == "__main__":
    main()