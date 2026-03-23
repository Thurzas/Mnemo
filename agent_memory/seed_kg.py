"""
seed_kg.py — Peuplement du KG seed (HP-KG base)

Génère src/Mnemo/assets/kg_seed.db avec les patterns procéduraux génériques.
Ce fichier est bundlé avec l'application et sert de fallback pour tous les
utilisateurs (double-lookup : user KG d'abord, seed en fallback).

Usage :
    python agent_memory/seed_kg.py          # génère le seed
    python agent_memory/seed_kg.py --stats  # affiche les stats sans regénérer

Le seed est READ-ONLY à l'exécution. Pour le mettre à jour, relancer ce script
et rebuilder l'image Docker.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

# Chemin racine du projet
PROJECT_ROOT = Path(__file__).parent.parent
SEED_PATH    = PROJECT_ROOT / "src" / "Mnemo" / "assets" / "kg_seed.db"

sys.path.insert(0, str(PROJECT_ROOT / "src"))

from Mnemo.init_db import init_kg_db
from Mnemo.tools.kg_tools import kg_add_triplet, kg_add_node


# ══════════════════════════════════════════════════════════════════════════════
# Définition des patterns seed
# ══════════════════════════════════════════════════════════════════════════════

def _triplets() -> list[tuple]:
    """
    Retourne la liste de tous les triplets seed sous forme de tuples :
    (src_type, src_label, rel, dst_type, dst_label)
    """
    return [

        # ── Actions primitives — préconditions et effets ──────────────────────
        # web_search
        ("action", "web_search",        "precondition", "state", "web_available"),
        ("action", "web_search",        "effect",       "state", "web_results_ready"),
        ("action", "web_search",        "causes",       "action","web_fetch"),

        # web_fetch
        ("action", "web_fetch",         "precondition", "state", "web_available"),
        ("action", "web_fetch",         "precondition", "state", "web_results_ready"),
        ("action", "web_fetch",         "effect",       "state", "page_content_ready"),
        ("action", "web_fetch",         "causes",       "action","extract_links"),

        # extract_links
        ("action", "extract_links",     "precondition", "state", "page_content_ready"),
        ("action", "extract_links",     "effect",       "state", "links_extracted"),

        # sandbox_read
        ("action", "sandbox_read",      "precondition", "state", "sandbox_open"),
        ("action", "sandbox_read",      "effect",       "state", "file_content_ready"),

        # sandbox_write
        ("action", "sandbox_write",     "precondition", "state", "sandbox_open"),
        ("action", "sandbox_write",     "effect",       "state", "file_created"),
        ("state",  "sandbox_readonly",  "blocks",       "action","sandbox_write"),

        # sandbox_shell (générique)
        ("action", "sandbox_shell",     "precondition", "state", "sandbox_open"),
        ("action", "sandbox_shell",     "effect",       "state", "command_executed"),

        # npm
        ("action", "sandbox_shell: npm init",    "precondition", "state", "node_available"),
        ("action", "sandbox_shell: npm init",    "effect",       "state", "npm_project_initialized"),
        ("action", "sandbox_shell: npm install", "precondition", "state", "npm_project_initialized"),
        ("action", "sandbox_shell: npm install", "effect",       "state", "dependencies_installed"),
        ("action", "sandbox_shell: npm run build","precondition","state", "dependencies_installed"),
        ("action", "sandbox_shell: npm run build","effect",      "state", "project_built"),
        ("action", "sandbox_shell: npm test",    "precondition", "state", "dependencies_installed"),
        ("action", "sandbox_shell: npm test",    "effect",       "state", "tests_validated"),

        # python
        ("action", "sandbox_shell: pytest",      "precondition", "state", "python_available"),
        ("action", "sandbox_shell: pytest",      "precondition", "state", "sandbox_open"),
        ("action", "sandbox_shell: pytest",      "effect",       "state", "tests_validated"),
        ("action", "sandbox_shell: python",      "precondition", "state", "python_available"),
        ("action", "sandbox_shell: python",      "effect",       "state", "command_executed"),

        # git
        ("action", "sandbox_shell: git commit",  "precondition", "state", "sandbox_open"),
        ("action", "sandbox_shell: git commit",  "effect",       "state", "changes_committed"),

        # ── Domaine : Documenter une technologie ─────────────────────────────
        ("task", "documenter une technologie", "contains", "step", "recherche initiale"),
        ("task", "documenter une technologie", "contains", "step", "organiser les informations"),
        ("task", "documenter une technologie", "contains", "step", "rédiger la documentation"),
        ("task", "documenter une technologie", "contains", "step", "valider la documentation"),

        ("step", "recherche initiale",          "requires", "action", "web_search"),
        ("step", "recherche initiale",          "requires", "action", "web_fetch"),
        ("step", "organiser les informations",  "requires", "action", "sandbox_write"),
        ("step", "rédiger la documentation",    "requires", "action", "sandbox_write"),
        ("step", "valider la documentation",    "requires", "action", "sandbox_read"),

        # ── Domaine : Créer projet web (React / JS) ───────────────────────────
        ("task", "créer projet web",  "contains", "step", "initialiser environnement"),
        ("task", "créer projet web",  "contains", "step", "configurer les dépendances"),
        ("task", "créer projet web",  "contains", "step", "créer la structure de base"),
        ("task", "créer projet web",  "contains", "step", "tester le projet"),
        ("task", "créer projet web",  "contains", "step", "builder le projet"),

        ("step", "initialiser environnement",    "requires", "action", "sandbox_shell: npm init"),
        ("step", "configurer les dépendances",   "requires", "action", "sandbox_shell: npm install"),
        ("step", "créer la structure de base",   "requires", "action", "sandbox_write"),
        ("step", "tester le projet",             "requires", "action", "sandbox_shell: npm test"),
        ("step", "builder le projet",            "requires", "action", "sandbox_shell: npm run build"),

        # ── Domaine : Implémenter module Python ───────────────────────────────
        ("task", "implémenter module Python", "contains", "step", "écrire le code"),
        ("task", "implémenter module Python", "contains", "step", "écrire les tests"),
        ("task", "implémenter module Python", "contains", "step", "valider les tests"),
        ("task", "implémenter module Python", "contains", "step", "intégrer au projet"),

        ("step", "écrire le code",      "requires", "action", "sandbox_write"),
        ("step", "écrire les tests",    "requires", "action", "sandbox_write"),
        ("step", "valider les tests",   "requires", "action", "sandbox_shell: pytest"),
        ("step", "intégrer au projet",  "requires", "action", "sandbox_shell: git commit"),

        # ── Domaine : Déboguer ────────────────────────────────────────────────
        ("task", "déboguer", "contains", "step", "reproduire le bug"),
        ("task", "déboguer", "contains", "step", "isoler la cause"),
        ("task", "déboguer", "contains", "step", "corriger le bug"),
        ("task", "déboguer", "contains", "step", "vérifier la correction"),

        ("step", "reproduire le bug",     "requires", "action", "sandbox_shell: pytest"),
        ("step", "isoler la cause",       "requires", "action", "sandbox_read"),
        ("step", "corriger le bug",       "requires", "action", "sandbox_write"),
        ("step", "vérifier la correction","requires", "action", "sandbox_shell: pytest"),

        # ── Domaine : Refactoriser un module ──────────────────────────────────
        ("task", "refactoriser module", "contains", "step", "documenter l'architecture cible"),
        ("task", "refactoriser module", "contains", "step", "créer le package vide"),
        ("task", "refactoriser module", "contains", "step", "extraire les composants"),
        ("task", "refactoriser module", "contains", "step", "mettre à jour les tests"),
        ("task", "refactoriser module", "contains", "step", "mettre à jour les consommateurs"),

        ("step", "documenter l'architecture cible", "requires", "action", "sandbox_write"),
        ("step", "créer le package vide",            "requires", "action", "sandbox_write"),
        ("step", "extraire les composants",          "requires", "action", "sandbox_write"),
        ("step", "mettre à jour les tests",          "requires", "action", "sandbox_write"),
        ("step", "mettre à jour les consommateurs",  "requires", "action", "sandbox_write"),

        # ── Domaine : Recherche et synthèse ───────────────────────────────────
        ("task", "rechercher et synthétiser", "contains", "step", "définir les termes de recherche"),
        ("task", "rechercher et synthétiser", "contains", "step", "collecter les sources"),
        ("task", "rechercher et synthétiser", "contains", "step", "évaluer la pertinence"),
        ("task", "rechercher et synthétiser", "contains", "step", "rédiger la synthèse"),

        ("step", "définir les termes de recherche", "requires", "action", "sandbox_write"),
        ("step", "collecter les sources",            "requires", "action", "web_search"),
        ("step", "collecter les sources",            "requires", "action", "web_fetch"),
        ("step", "évaluer la pertinence",            "requires", "action", "sandbox_read"),
        ("step", "rédiger la synthèse",              "requires", "action", "sandbox_write"),

        # ── Actions PlanRunner (crew executors) ───────────────────────────────
        # write_markdown_file (crew : shell)
        ("action", "write_markdown_file",       "precondition", "state", "sandbox_open"),
        ("action", "write_markdown_file",       "effect",       "state", "file_created"),
        ("action", "write_markdown_file",       "effect",       "state", "chapter_written"),

        # analyse_et_note (crew : note)
        ("action", "analyse_et_note",           "precondition", "state", "sandbox_open"),
        ("action", "analyse_et_note",           "effect",       "state", "analysis_written"),
        ("action", "analyse_et_note",           "effect",       "state", "memory_updated"),

        # generate_response (crew : conversation)
        ("action", "generate_response",         "precondition", "state", "sandbox_open"),
        ("action", "generate_response",         "effect",       "state", "response_generated"),

        # create_structured_content (crew : scheduler)
        ("action", "create_structured_content", "precondition", "state", "sandbox_open"),
        ("action", "create_structured_content", "effect",       "state", "structured_plan_written"),

        # spawn_sub_plan (crew : planner — HTN)
        ("action", "spawn_sub_plan",            "precondition", "state", "sandbox_open"),
        ("action", "spawn_sub_plan",            "effect",       "state", "sub_plan_created"),
        ("action", "spawn_sub_plan",            "effect",       "state", "sub_plan_executed"),
        ("action", "spawn_sub_plan",            "causes",       "action","write_markdown_file"),

        # ── Patterns plan executor — étapes génériques ───────────────────────
        # Patterns "écrire/rédiger X"
        ("step", "rédiger introduction",         "requires", "action", "write_markdown_file"),
        ("step", "rédiger conclusion",           "requires", "action", "write_markdown_file"),
        ("step", "rédiger un chapitre",          "requires", "action", "write_markdown_file"),
        ("step", "écrire la documentation",      "requires", "action", "write_markdown_file"),
        ("step", "écrire un résumé",             "requires", "action", "write_markdown_file"),
        ("step", "créer le fichier README",      "requires", "action", "write_markdown_file"),

        # Patterns "analyser/identifier X"
        ("step", "analyser les besoins",         "requires", "action", "analyse_et_note"),
        ("step", "identifier les concepts clés", "requires", "action", "analyse_et_note"),
        ("step", "analyser le contenu",          "requires", "action", "analyse_et_note"),
        ("step", "identifier les lacunes",       "requires", "action", "analyse_et_note"),
        ("step", "évaluer la qualité",           "requires", "action", "analyse_et_note"),

        # Patterns "réviser/corriger X"
        ("step", "révision et correction",       "requires", "action", "spawn_sub_plan"),
        ("step", "réviser le contenu",           "requires", "action", "analyse_et_note"),
        ("step", "corriger les erreurs",         "requires", "action", "write_markdown_file"),
        ("step", "relire et améliorer",          "requires", "action", "spawn_sub_plan"),

        # Patterns "planifier/organiser X"
        ("step", "planifier les étapes",         "requires", "action", "spawn_sub_plan"),
        ("step", "organiser le travail",         "requires", "action", "create_structured_content"),
        ("step", "créer le plan détaillé",       "requires", "action", "create_structured_content"),

        # Patterns "rechercher X"
        ("step", "rechercher des informations",  "requires", "action", "web_search"),
        ("step", "rechercher des sources",       "requires", "action", "web_search"),
        ("step", "rechercher sur le web",        "requires", "action", "web_search"),

        # Patterns conversation/réponse
        ("step", "répondre à la question",       "requires", "action", "generate_response"),
        ("step", "expliquer le concept",         "requires", "action", "generate_response"),
        ("step", "synthétiser les résultats",    "requires", "action", "generate_response"),
    ]


# ══════════════════════════════════════════════════════════════════════════════
# Génération
# ══════════════════════════════════════════════════════════════════════════════

def generate_seed(path: Path = SEED_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    if path.exists():
        path.unlink()
        print(f"⟳  Seed existant supprimé : {path}")

    init_kg_db(path)
    print(f"✅ DB KG initialisée : {path}")

    triplets = _triplets()
    ok = 0
    for src_type, src_label, rel, dst_type, dst_label in triplets:
        try:
            kg_add_triplet(path, src_type, src_label, rel, dst_type, dst_label, source="seed")
            ok += 1
        except Exception as e:
            print(f"  ⚠  Erreur sur ({src_label!r} -{rel}→ {dst_label!r}) : {e}")

    print(f"✅ {ok}/{len(triplets)} triplets insérés.")
    _print_stats(path)


def _print_stats(path: Path) -> None:
    conn = sqlite3.connect(path)
    n_nodes = conn.execute("SELECT COUNT(*) FROM kg_nodes").fetchone()[0]
    n_edges = conn.execute("SELECT COUNT(*) FROM kg_edges").fetchone()[0]
    by_type = conn.execute(
        "SELECT type, COUNT(*) FROM kg_nodes GROUP BY type ORDER BY COUNT(*) DESC"
    ).fetchall()
    by_rel  = conn.execute(
        "SELECT rel, COUNT(*) FROM kg_edges GROUP BY rel ORDER BY COUNT(*) DESC"
    ).fetchall()
    conn.close()

    print(f"\n── Statistiques seed ──────────────────────────")
    print(f"   Nœuds  : {n_nodes}")
    for t, c in by_type:
        print(f"     {t:<12} {c}")
    print(f"   Relations : {n_edges}")
    for r, c in by_rel:
        print(f"     {r:<15} {c}")
    print(f"───────────────────────────────────────────────")


# ══════════════════════════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Génère le KG seed de Mnemo.")
    parser.add_argument("--stats", action="store_true",
                        help="Affiche les stats du seed existant sans le regénérer.")
    parser.add_argument("--output", type=Path, default=SEED_PATH,
                        help=f"Chemin de sortie (défaut : {SEED_PATH})")
    args = parser.parse_args()

    if args.stats:
        if not args.output.exists():
            print(f"⚠  Seed absent : {args.output}")
            sys.exit(1)
        _print_stats(args.output)
    else:
        generate_seed(args.output)