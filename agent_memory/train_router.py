import os
#!/usr/bin/env python3
"""train_router.py - Entraine TF-IDF+LR sur training_data.jsonl"""
import argparse, json, sys
from pathlib import Path

try:
    import joblib, numpy as np
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import classification_report, confusion_matrix
    from sklearn.model_selection import StratifiedKFold, cross_val_score
    from sklearn.pipeline import Pipeline
except ImportError:
    print("pip install scikit-learn joblib --break-system-packages")
    sys.exit(1)

_DATA          = Path(os.getenv("DATA_PATH", ".")).resolve()
DEFAULT_DATA   = _DATA / "training_data.jsonl"
DEFAULT_OUTPUT = _DATA / "router_model.joblib"
ROUTES = ["conversation", "shell", "calendar", "scheduler"]


def load_data(path):
    texts, labels = [], []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line: continue
            item = json.loads(line)
            texts.append(item["text"])
            labels.append(item["route"])
    return texts, labels


def build_pipeline():
    return Pipeline([
        ("tfidf", TfidfVectorizer(
            analyzer="char_wb", ngram_range=(2, 4),
            min_df=1, max_features=20000, sublinear_tf=True,
        )),
        ("clf", LogisticRegression(
            C=5.0, max_iter=1000, class_weight="balanced",
            solver="lbfgs",
        )),
    ])


TESTS = [
    ("salut comment tu vas", "conversation"),
    ("liste les fichiers dans docs", "shell"),
    ("cree un RDV dentiste vendredi 15h", "calendar"),
    ("rappelle-moi dans 3h de prendre mes medicaments", "scheduler"),
    ("qu est-ce que j ai prevu demain", "conversation"),
    ("ls /data/docs", "shell"),
    ("annule la reunion de lundi", "calendar"),
    ("tous les soirs a 20h envoie un resume", "scheduler"),
    ("t aurais pas mes PDF dans data", "shell"),
    ("dans 20 minutes previens-moi de sortir le poulet", "scheduler"),
]


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--data",   type=Path, default=DEFAULT_DATA)
    p.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    p.add_argument("--cv",     type=int,  default=5)
    args = p.parse_args()

    if not args.data.exists():
        print(f"ERREUR : {args.data} introuvable. Lance generate_training_data.py d'abord.")
        sys.exit(1)

    texts, labels = load_data(args.data)
    print(f"{len(texts)} exemples")
    for r in ROUTES:
        print(f"  {r:15s}: {labels.count(r):4d}")

    pipeline = build_pipeline()

    if args.cv > 1 and len(texts) >= args.cv * 4:
        scores = cross_val_score(
            pipeline, texts, labels,
            cv=StratifiedKFold(args.cv, shuffle=True, random_state=42),
            scoring="f1_weighted", n_jobs=-1,
        )
        print(f"\nCV F1 : {scores.mean():.3f} +/- {scores.std():.3f}")
        if scores.mean() < 0.70:
            print("ATTENTION : F1 < 0.70 — relance generate_training_data.py --n 200")

    pipeline.fit(texts, labels)
    print("\n" + classification_report(labels, pipeline.predict(texts),
                                       target_names=ROUTES, zero_division=0))

    print("Tests rapides :")
    ok = 0
    for text, expected in TESTS:
        pred = pipeline.predict([text])[0]
        conf = max(pipeline.predict_proba([text])[0])
        tag  = "OK" if pred == expected else "FAIL"
        if pred == expected:
            ok += 1
        print(f"  [{tag}] {conf:.2f} | {text[:40]:<40} -> {pred}")
    print(f"  {ok}/{len(TESTS)} tests\n")

    joblib.dump({"pipeline": pipeline, "routes": ROUTES, "n_train": len(texts)}, args.output)
    print(f"Modele sauvegarde : {args.output} ({args.output.stat().st_size // 1024} KB)")
    print("Etape suivante : monte router_model.joblib dans Docker et relance.")


if __name__ == "__main__":
    main()