import os
#!/usr/bin/env python3
"""
generate_training_data.py
Genere un dataset JSONL pour le classifieur de routing Mnemo.
Usage: python generate_training_data.py [--n 100] [--seed-only]
"""
import argparse, json, os, re, sys, time
from pathlib import Path
import requests

OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")
MODEL = os.getenv("MODEL", "ollama/mistral").replace("ollama/", "")
_DATA  = Path(os.getenv("DATA_PATH", ".")).resolve()
OUTPUT = _DATA / "training_data.jsonl"
ROUTES      = ["conversation", "shell", "calendar", "scheduler", "note", "plan", "sandbox"]
BATCH_SIZE  = 20

ROUTE_PROMPTS = {
    "conversation": (
        "Tu es un utilisateur parlant a Mnemo, un assistant IA personnel.\n"
        "Genere {n} messages DIFFERENTS de type conversation :\n"
        "- Questions generales, explications, definitions\n"
        "- Questions sur la memoire personnelle\n"
        "- Mises a jour d informations personnelles\n"
        "- Bavardage, salutations\n"
        "- Recherches web explicites\n"
        "- LECTURE de l agenda (pas modification)\n"
        "Reponds UNIQUEMENT avec JSON : {{\"messages\": [\"msg1\", \"msg2\", ...]}}\n"
        "Genere exactement {n} messages varies."
    ),
    "shell": (
        "Tu es un utilisateur parlant a Mnemo, un assistant IA personnel.\n"
        "Genere {n} messages DIFFERENTS de type shell (operations filesystem) :\n"
        "- Lister fichiers/dossiers dans data/\n"
        "- Lire/afficher fichiers texte ou PDF\n"
        "- Chercher fichiers par nom ou extension\n"
        "- Creer/supprimer/deplacer fichiers\n"
        "- Lancer scripts Python\n"
        "Varie : direct (ls /data), naturel (montre mes PDF), contextuel.\n"
        "Reponds UNIQUEMENT avec JSON : {{\"messages\": [\"msg1\", \"msg2\", ...]}}\n"
        "Genere exactement {n} messages varies."
    ),
    "calendar": (
        "Tu es un utilisateur parlant a Mnemo, un assistant IA personnel.\n"
        "Genere {n} messages DIFFERENTS de type calendar (MODIFICATION agenda) :\n"
        "- Creer evenement/RDV\n"
        "- Modifier/deplacer evenement\n"
        "- Supprimer/annuler evenement\n"
        "- Bloquer du temps\n"
        "IMPORTANT : lecture agenda = conversation, pas calendar.\n"
        "Reponds UNIQUEMENT avec JSON : {{\"messages\": [\"msg1\", \"msg2\", ...]}}\n"
        "Genere exactement {n} messages varies."
    ),
    "scheduler": (
        "Tu es un utilisateur parlant a Mnemo, un assistant IA personnel.\n"
        "Genere {n} messages DIFFERENTS de type scheduler (taches differees/recurrentes) :\n"
        "- Rappels dans X min/h/jours\n"
        "- Taches recurrentes automatiques\n"
        "- Notifications differees\n"
        "- Routines automatisees\n"
        "Reponds UNIQUEMENT avec JSON : {{\"messages\": [\"msg1\", \"msg2\", ...]}}\n"
        "Genere exactement {n} messages varies."
    ),
    "sandbox": (
        "Tu es un utilisateur parlant a Mnemo, un assistant IA personnel.\n"
        "Genere {n} messages DIFFERENTS de type sandbox (travail actif dans un projet existant) :\n"
        "- Reprendre/continuer un projet en cours (landing page, app, module...)\n"
        "- Ouvrir ou retourner sur un projet specifique\n"
        "- Travailler sur une feature dans un projet existant\n"
        "- Continuer le developpement d une feature commencee\n"
        "- Executer du code, des tests dans un projet\n"
        "IMPORTANT : sandbox = travailler DANS un projet existant, pas planifier.\n"
        "Exemples : ouvre le projet X, continue le projet Y, reprends le projet Z.\n"
        "Reponds UNIQUEMENT avec JSON : {\"messages\": [\"msg1\", ...]}\n"
        "Genere exactement {n} messages varies."
    ),
    "plan": (
        "Tu es un utilisateur parlant a Mnemo, un assistant IA personnel.\n"
        "Genere {n} messages DIFFERENTS de type plan (decomposition de projet en etapes) :\n"
        "- Demandes de planification de projet ou feature (pas un rappel, pas un RDV)\n"
        "- Decomposition d une tache complexe en etapes\n"
        "- Organisation d un developpement logiciel\n"
        "- Preparation d un projet (documentation, landing page, refactoring...)\n"
        "- Demandes avec 'etapes', 'plan de travail', 'organiser le projet'\n"
        "IMPORTANT : un plan c est une sequence d etapes, pas un rappel ni un evenement agenda.\n"
        "Ne genere PAS de rappels ('rappelle-moi'), ni d evenements agenda, ni de commandes shell.\n"
        "Reponds UNIQUEMENT avec JSON : {{\"messages\": [\"msg1\", \"msg2\", ...]}}\n"
        "Genere exactement {n} messages varies."
    ),
    "note": (
        "Tu es un utilisateur parlant a Mnemo, un assistant IA personnel.\n"
        "Genere {n} messages DIFFERENTS de type note (ecriture immediate en memoire longue duree) :\n"
        "- L utilisateur veut que Mnemo retienne un fait maintenant, pas en fin de session\n"
        "- Preferences (outil prefere, style de travail, alimentation...)\n"
        "- Informations personnelles (ville, metier, projet en cours...)\n"
        "- Decisions prises (choix d un stack, cap professionnel...)\n"
        "- Connaissances importantes a garder\n"
        "Varie les formulations : 'note que', 'retiens que', 'memorise', 'n oublie pas',\n"
        "'souviens-toi', 'garde en memoire', 'enregistre', 'je veux que tu saches'.\n"
        "IMPORTANT : ces messages n attendent PAS de reponse, ils veulent juste etre notes.\n"
        "Ne genere PAS de questions (ex: 'tu te souviens de X ?' = conversation, pas note).\n"
        "Reponds UNIQUEMENT avec JSON : {{\"messages\": [\"msg1\", \"msg2\", ...]}}\n"
        "Genere exactement {n} messages varies."
    ),
}

SEED_DATA = [
    {"text": "salut", "route": "conversation"},
    {"text": "comment tu vas ?", "route": "conversation"},
    {"text": "c est quoi le TDD ?", "route": "conversation"},
    {"text": "explique-moi les transformers", "route": "conversation"},
    {"text": "qu est-ce que j ai prevu demain ?", "route": "conversation"},
    {"text": "resume ce que tu sais sur moi", "route": "conversation"},
    {"text": "cherche la doc FastAPI routing", "route": "conversation"},
    {"text": "j ai fini le module 3 du cours Coursera", "route": "conversation"},
    {"text": "quelle difference entre Docker et Podman ?", "route": "conversation"},
    {"text": "mon agenda cette semaine ?", "route": "conversation"},
    {"text": "qu est-ce que j avais prevu lundi ?", "route": "conversation"},
    {"text": "je t ai dit que j avais change de job", "route": "conversation"},
    {"text": "cherche les news Python 3.14", "route": "conversation"},
    {"text": "quel est mon niveau en Python ?", "route": "conversation"},
    {"text": "explique les embeddings", "route": "conversation"},
    {"text": "tu te souviens de mon projet ?", "route": "conversation"},
    {"text": "difference entre RAM et VRAM ?", "route": "conversation"},
    {"text": "resume ma semaine", "route": "conversation"},
    {"text": "qu est-ce que j apprends en ce moment ?", "route": "conversation"},
    {"text": "explique-moi le machine learning", "route": "conversation"},
    {"text": "liste les fichiers dans docs", "route": "shell"},
    {"text": "liste les PDF dans data/docs", "route": "shell"},
    {"text": "qu est-ce qu il y a dans le dossier data ?", "route": "shell"},
    {"text": "affiche le contenu de notes.txt", "route": "shell"},
    {"text": "trouve tous les fichiers .py dans data", "route": "shell"},
    {"text": "ls /data/docs", "route": "shell"},
    {"text": "lis le fichier config.json", "route": "shell"},
    {"text": "cree un dossier projets dans data", "route": "shell"},
    {"text": "lance le script analyse.py", "route": "shell"},
    {"text": "supprime le fichier temp.txt dans data", "route": "shell"},
    {"text": "montre-moi les fichiers dans scripts", "route": "shell"},
    {"text": "va fouiller dans data/docs", "route": "shell"},
    {"text": "commande shell : liste le dossier docs", "route": "shell"},
    {"text": "t aurais pas mes PDF de cours dans data ?", "route": "shell"},
    {"text": "donne-moi la premiere page du rapport.pdf", "route": "shell"},
    {"text": "liste les dossiers dans data", "route": "shell"},
    {"text": "cherche les fichiers .txt dans data", "route": "shell"},
    {"text": "affiche les 10 premieres lignes de log.txt", "route": "shell"},
    {"text": "combien de fichiers dans data/docs ?", "route": "shell"},
    {"text": "lit moi le fichier README.md", "route": "shell"},
    {"text": "cree un RDV dentiste vendredi 15h", "route": "calendar"},
    {"text": "ajoute une reunion lundi matin 10h", "route": "calendar"},
    {"text": "decale le cours de yoga de jeudi a vendredi", "route": "calendar"},
    {"text": "annule le rendez-vous de demain", "route": "calendar"},
    {"text": "bloque-moi 2h mercredi pour coder", "route": "calendar"},
    {"text": "supprime la reunion Projects du calendrier", "route": "calendar"},
    {"text": "ajoute demo produit mardi 14h", "route": "calendar"},
    {"text": "modifie le RDV medecin a 11h", "route": "calendar"},
    {"text": "mets appel client jeudi 16h dans mon agenda", "route": "calendar"},
    {"text": "ajoute vacances 15-22 juillet dans le calendrier", "route": "calendar"},
    {"text": "change l heure de la reunion de demain a 9h", "route": "calendar"},
    {"text": "programme session sport samedi 8h", "route": "calendar"},
    {"text": "inscris formation Python 3-5 mars dans agenda", "route": "calendar"},
    {"text": "nouveau RDV ophtalmo mercredi 11h", "route": "calendar"},
    {"text": "efface la reunion du lundi de mon calendrier", "route": "calendar"},
    {"text": "rappelle-moi dans 3h de prendre mes medicaments", "route": "scheduler"},
    {"text": "envoie-moi un resume tous les soirs a 20h", "route": "scheduler"},
    {"text": "dis-moi dans 30 min de rappeler le plombier", "route": "scheduler"},
    {"text": "tous les lundis matin genere un rapport", "route": "scheduler"},
    {"text": "rappelle-moi tous les dimanches de planifier ma semaine", "route": "scheduler"},
    {"text": "chaque jour a 8h envoie-moi la meteo", "route": "scheduler"},
    {"text": "dans 2 jours rappelle-moi de relire ce contrat", "route": "scheduler"},
    {"text": "programme une notification dans 1h", "route": "scheduler"},
    {"text": "bilan hebdomadaire chaque vendredi soir", "route": "scheduler"},
    {"text": "chaque matin rappelle-moi d arroser les plantes", "route": "scheduler"},
    {"text": "dans 20 minutes previens-moi de sortir le poulet", "route": "scheduler"},
    {"text": "chaque mercredi soir rappelle compta", "route": "scheduler"},
    {"text": "alerte dans 45 min fin de reunion", "route": "scheduler"},
    {"text": "tous les 1er du mois rappel backup", "route": "scheduler"},
    {"text": "notifie dans 2h d aller chercher les enfants", "route": "scheduler"},
    # ── sandbox ───────────────────────────────────────────────────────
    {"text": "ouvre le projet landing-page", "route": "sandbox"},
    {"text": "continue le projet react-doc", "route": "sandbox"},
    {"text": "reprends le projet de landing page", "route": "sandbox"},
    {"text": "travaille sur le projet notifications", "route": "sandbox"},
    {"text": "retourne sur le projet mnemo-frontend", "route": "sandbox"},
    {"text": "ouvre ce projet et continue l etape en cours", "route": "sandbox"},
    {"text": "reprend le projet documentation react js", "route": "sandbox"},
    {"text": "continue le developpement du projet landing page", "route": "sandbox"},
    {"text": "travaille sur le projet auth et avance sur l etape 2", "route": "sandbox"},
    {"text": "ouvre le projet scheduler et execute les tests", "route": "sandbox"},
    {"text": "retourne dans le projet et ecris le composant Header", "route": "sandbox"},
    {"text": "reprends le projet et installe les dependances npm", "route": "sandbox"},
    {"text": "continue le projet et lance pytest", "route": "sandbox"},
    {"text": "ouvre le projet web-scraper et corrige le bug", "route": "sandbox"},
    {"text": "travaille sur le projet api-rest et ecris les tests", "route": "sandbox"},
    # ── plan ─────────────────────────────────────────────────────────
    {"text": "construis-moi un plan pour developper la feature auth", "route": "plan"},
    {"text": "prepare un plan pour refactoriser le module memoire", "route": "plan"},
    {"text": "fais-moi un plan pour creer une landing page React", "route": "plan"},
    {"text": "on va planifier ce projet en etapes", "route": "plan"},
    {"text": "organise les etapes pour implementer le scheduler", "route": "plan"},
    {"text": "je veux planifier le developpement du dashboard", "route": "plan"},
    {"text": "decompose la tache migration base de donnees", "route": "plan"},
    {"text": "cree un plan de travail pour documenter l API", "route": "plan"},
    {"text": "planifier le projet en plusieurs phases", "route": "plan"},
    {"text": "comment organiser le developpement de cette feature ?", "route": "plan"},
    {"text": "redige un plan pour implementer le systeme de cache", "route": "plan"},
    {"text": "j ai besoin d un plan pour refaire le frontend", "route": "plan"},
    {"text": "planifie le projet de documentation React JS", "route": "plan"},
    {"text": "prepare la documentation en etapes pour ce projet", "route": "plan"},
    {"text": "on commence par preparer la documentation puis planifie les etapes", "route": "plan"},
    # ── note ─────────────────────────────────────────────────────────
    {"text": "note que je prefere vim a vscode", "route": "note"},
    {"text": "retiens que je suis vegetarien", "route": "note"},
    {"text": "memorise que mon langage prefere c est Python", "route": "note"},
    {"text": "n oublie pas que je travaille en freelance", "route": "note"},
    {"text": "souviens-toi que j habite a Lyon", "route": "note"},
    {"text": "garde en memoire que je prefere les reponses courtes", "route": "note"},
    {"text": "enregistre que mon projet principal s appelle Mnemo", "route": "note"},
    {"text": "je veux que tu saches que j ai change de boulot", "route": "note"},
    {"text": "note que j ai decide d utiliser FastAPI pour le backend", "route": "note"},
    {"text": "retiens que je me leve tous les jours a 7h", "route": "note"},
    {"text": "memorise : allergique aux arachides", "route": "note"},
    {"text": "important a noter : je n utilise plus Windows", "route": "note"},
    {"text": "ajoute a ma memoire que je parle anglais et espagnol", "route": "note"},
    {"text": "garde ca en memoire : objectif terminer le cours d ici mars", "route": "note"},
    {"text": "note que j ai commence a apprendre le Rust", "route": "note"},
    {"text": "enregistre ceci : j ai adopte la methode pomodoro", "route": "note"},
    {"text": "retiens bien que je prefere Docker a Podman pour ce projet", "route": "note"},
    {"text": "souviens-toi que ma deadline projet c est le 15 mars", "route": "note"},
    {"text": "je veux que tu retiennes que j utilise arch linux", "route": "note"},
    {"text": "a noter : j ai decide de passer a neovim", "route": "note"},
]





def resolve_model(model_name):
    """
    Verifie que model_name existe dans Ollama.
    Si non, cherche un modele avec un nom proche et le retourne.
    """
    try:
        r = requests.get(f"{OLLAMA_HOST}/api/tags", timeout=10)
        r.raise_for_status()
        available = [m["name"] for m in r.json().get("models", [])]
    except Exception:
        return model_name  # on ne peut pas verifier, on essaie quand meme

    if model_name in available:
        return model_name

    # Cherche un nom approchant (ignore le tag :latest)
    base = model_name.split(":")[0]
    for name in available:
        if base in name or name.split(":")[0] == base:
            print(f"  [INFO] Modele {model_name!r} introuvable -> utilise {name!r}")
            return name

    # Rien ne matche — affiche ce qui est dispo
    print(f"  [ERREUR] Modele {model_name!r} introuvable dans Ollama.")
    print(f"  Modeles disponibles : {available}")
    sys.exit(1)


def call_ollama(prompt):
    """
    Appelle Ollama. Essaie /v1/chat/completions puis /api/chat.
    """
    endpoints = [
        ("/v1/chat/completions", "openai"),
        ("/api/chat",           "ollama"),
        ("/api/generate",       "generate"),
    ]
    last_error = ""
    for path, fmt in endpoints:
        url = f"{OLLAMA_HOST}{path}"
        try:
            if fmt == "openai":
                payload = {
                    "model": MODEL,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.85,
                    "max_tokens": 2000,
                    # Pas de response_format — incompatible avec certaines versions Ollama
                }
            elif fmt == "ollama":
                payload = {
                    "model": MODEL,
                    "messages": [{"role": "user", "content": prompt}],
                    "stream": False,
                    "options": {"temperature": 0.85, "num_predict": 2000},
                    "format": "json",
                }
            else:
                payload = {
                    "model": MODEL,
                    "prompt": prompt + "\nReponds uniquement en JSON valide.",
                    "stream": False,
                    "options": {"temperature": 0.85, "num_predict": 2000},
                    "format": "json",
                }

            r = requests.post(url, json=payload, timeout=120)

            # Debug : affiche le code HTTP si pas 200
            if r.status_code != 200:
                last_error = f"{path} -> HTTP {r.status_code}: {r.text[:200]}"
                continue

            data = r.json()
            if fmt == "openai":
                return data["choices"][0]["message"]["content"]
            elif fmt == "ollama":
                return data["message"]["content"]
            else:
                return data.get("response", "")

        except requests.exceptions.ConnectionError as e:
            print(f"\nERREUR connexion {OLLAMA_HOST} : {e}")
            print("Ollama est-il demarre ? Lance : ollama serve")
            sys.exit(1)
        except Exception as e:
            last_error = f"{path} -> {type(e).__name__}: {e}"
            continue

    print(f"\n[DEBUG] Tous les endpoints ont echoue sur {OLLAMA_HOST}")
    print(f"[DEBUG] Derniere erreur : {last_error}")
    print(f"[DEBUG] Modele utilise : {MODEL!r}")
    print("[DEBUG] Verifie avec : curl http://localhost:11434/api/tags")
    return ""

def parse_messages(raw):
    try:
        data = json.loads(raw)
        msgs = data.get("messages", [])
        if isinstance(msgs, list):
            return [str(m).strip() for m in msgs if len(str(m).strip()) >= 8]
    except json.JSONDecodeError:
        return [m.strip() for m in re.findall(r'"([^"]{10,200})"', raw)]
    return []


def generate_for_route(route, total):
    template = ROUTE_PROMPTS[route]
    results, seen = [], set()
    batches = (total + BATCH_SIZE - 1) // BATCH_SIZE
    print(f"\n  Route '{route}' — {batches} batch(es)")
    for b in range(batches):
        n = min(BATCH_SIZE, total - len(results))
        if n <= 0:
            break
        print(f"    batch {b+1}/{batches}...", end=" ", flush=True)
        msgs = parse_messages(call_ollama(template.format(n=n)))
        added = sum(1 for m in msgs if m not in seen and not seen.add(m)
                    and results.append({"text": m, "route": route}) is None)
        print(f"{added} OK (total: {len(results)})")
        if b < batches - 1:
            time.sleep(0.3)
    return results


def main():
    global MODEL
    MODEL = resolve_model(MODEL)
    p = argparse.ArgumentParser()
    p.add_argument("--n",         type=int,  default=120)
    p.add_argument("--output",    type=Path, default=OUTPUT)
    p.add_argument("--seed-only", action="store_true")
    args = p.parse_args()

    print(f"Dataset routing | modele: {MODEL} @ {OLLAMA_HOST}")
    data = list(SEED_DATA)
    if not args.seed_only:
        for route in ROUTES:
            data.extend(generate_for_route(route, args.n))
    else:
        print("Mode seed-only — pas d appels LLM")

    seen, unique = set(), []
    for item in data:
        k = item["text"].lower().strip()
        if k not in seen:
            seen.add(k)
            unique.append(item)

    print(f"\nDataset : {len(unique)} exemples uniques")
    for r in ROUTES:
        count = sum(1 for d in unique if d['route'] == r)
        bar   = "█" * (count // 5)
        print(f"  {r:15s}: {count:4d}  {bar}")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as f:
        for item in unique:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")
    print(f"\nSauvegarde : {args.output}")
    print("Etape suivante : python train_router.py")


if __name__ == "__main__":
    main()