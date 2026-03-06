import argparse, json, sys, os, subprocess
from pathlib import Path
from collections import Counter

# Configuration des chemins
_DATA = Path(os.getenv("DATA_PATH", ".")).resolve()
BASE = Path(__file__).parent
TRAIN_DATA = _DATA / "training_data.jsonl"
UNCERTAIN = _DATA / "uncertain_cases.jsonl"
OUTPUT = _DATA / "router_model.joblib"
ROUTES = ["conversation", "shell", "calendar", "scheduler"]

def clean_text(text):
    """Normalisation pour le dédoublonnement et la comparaison."""
    return text.lower().strip().replace("  ", " ")

def load_jsonl(path):
    items = []
    if not path.exists(): return items
    with open(path, encoding="utf-8") as f:
        for line in f:
            if line.strip(): items.append(json.loads(line))
    return items

def consolidate_dataset(train_items, new_items):
    """
    Fusionne en donnant la priorité aux nouveaux cas (correction LLM) 
    tout en supprimant les bruits évidents.
    """
    # On indexe par texte nettoyé
    data_map = {}
    
    # 1. Charger l'existant
    for item in train_items:
        data_map[clean_text(item["text"])] = item["route"]
        
    # 2. Ecraser avec les nouveaux cas (le LLM/Humain a rectifié le tir)
    # [Source: 65, 66 montrent des cas où le ML s'est trompé]
    for item in new_items:
        txt = clean_text(item["text"])
        # On ne prend le nouveau cas que s'il a une route valide
        if item["route"] in ROUTES:
            data_map[txt] = item["route"]

    # 3. Reconstruction et Statistiques
    final_list = [{"text": t, "route": r} for t, r in data_map.items()]
    
    # 4. Équilibrage (Optionnel mais recommandé) : 
    # Si une classe est trop faible, on pourrait dupliquer certains exemples, 
    # mais ici on va juste logger l'état du balancement.
    return final_list

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--min-new", type=int, default=10) # Réduit pour tester plus vite 
    p.add_argument("--force", action="store_true")
    args = p.parse_args()

    train = load_jsonl(TRAIN_DATA)
    uncertain = load_jsonl(UNCERTAIN)

    if not uncertain and not args.force:
        print("Rien à traiter.")
        sys.exit(0)

    print(f"Fusion de {len(train)} anciens + {len(uncertain)} nouveaux cas...")
    
    merged = consolidate_dataset(train, uncertain)
    
    # Stats de santé du dataset
    counts = Counter(d["route"] for d in merged)
    print("\nRépartition après consolidation :")
    for r in ROUTES:
        print(f"  {r:15s}: {counts[r]:4d}")

    # Sauvegarde propre
    with open(TRAIN_DATA, "w", encoding="utf-8") as f:
        for item in merged:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")

    # Entraînement effectif
    print("\n--- Lancement de l'entraînement ---")
    cmd = [sys.executable, str(BASE / "train_router.py"), "--data", str(TRAIN_DATA)]
    res = subprocess.run(cmd)

    if res.returncode == 0:
        # Rotation des logs uncertain
        if UNCERTAIN.exists():
            bak = UNCERTAIN.with_suffix(".jsonl.bak")
            UNCERTAIN.replace(bak)
            UNCERTAIN.touch()
        print("\nSuccès : Modèle mis à jour et dataset nettoyé.")
    else:
        print("\nÉchec de l'entraînement.")

if __name__ == "__main__":
    main()