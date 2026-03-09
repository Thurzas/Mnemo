# Installation de Mnemo

> Agent mémoire personnel — 100% local, 100% privé.

---

## Prérequis

| Outil | Version | Installation |
|---|---|---|
| Docker | ≥ 24 | [docs.docker.com](https://docs.docker.com/get-docker/) |
| Docker Compose | ≥ 2 | inclus dans Docker Desktop |
| Ollama | ≥ 0.4 | [ollama.com](https://ollama.com) |

> **WSL2** : Docker Desktop avec intégration WSL2 activée est recommandé sous Windows.

---

## Installation en 3 étapes

### 1. Clone & prépare

```bash
git clone https://github.com/thurzas/mnemo.git
cd mnemo
chmod +x mnemo.sh install.sh
```

### 2. Lance l'installeur

```bash
./mnemo.sh setup
```

L'installeur (`install.sh`) fait tout automatiquement :

- Vérifie que Docker et Ollama sont installés
- Crée `.env` depuis `.env.template` (si absent)
- Crée `data/` avec `memory.md`, `docs/`, `sessions/`
- Tire les modèles Ollama (`mistral` + `nomic-embed-text`)
- Build les images Docker (`mnemo:latest` + `mnemo-scheduler:latest`)
- Initialise la base SQLite (`data/memory.db`)

### 3. Lance Mnemo

```bash
./mnemo.sh
```

---

## Configuration

Toute la configuration est dans `.env` (créé automatiquement depuis `.env.template`) :

```bash
# Modèle LLM (ollama/<nom>)
MODEL=ollama/mistral

# URL Ollama — pointe vers ta machine hôte
API_BASE=http://host.docker.internal:11434

# Dossier des données (memory.db, memory.md, sessions/)
DATA_DIR=./data

# Calendrier ICS (optionnel)
CALENDAR_SOURCE=/data/agenda.ics
```

Les autres options (scheduler, SearXNG, limites ressources) sont documentées dans `.env.template`.

---

## Commandes disponibles

```bash
./mnemo.sh                   # Session de conversation
./mnemo.sh setup             # Installation initiale / réinstallation
./mnemo.sh services          # Démarre scheduler + API (daemon)
./mnemo.sh scheduler         # Démarre le scheduler seul (daemon)
./mnemo.sh api               # Démarre l'API seule (daemon)
./mnemo.sh stop              # Arrête les services background
./mnemo.sh rebuild           # Stop → build → redémarre les services
./mnemo.sh briefing          # Génère le briefing maintenant
./mnemo.sh weekly            # Génère le résumé hebdomadaire
./mnemo.sh logs              # Logs scheduler en temps réel
./mnemo.sh logs-api          # Logs API en temps réel
./mnemo.sh ingest <fichier>  # Ingère un fichier en mémoire
./mnemo.sh status            # État des containers
./mnemo.sh help              # Aide
```

Commandes directes Docker (si besoin) :

```bash
# Session de conversation
docker compose run --rm mnemo

# Ingérer un document
docker compose run --rm mnemo ingest /data/docs/rapport.pdf

# Lister les documents ingérés
docker compose run --rm mnemo docs

# Forcer le questionnaire d'initialisation
docker compose run --rm mnemo curiosity

# Initialiser / migrer la base SQLite
docker compose run --rm mnemo init_db
```

---

## Structure des données

```
data/                   ← monté dans le conteneur comme /data
├── memory.md           ← mémoire long terme (éditable à la main)
├── memory.db           ← index SQLite (FTS5 + vecteurs)
├── sessions/           ← sessions JSON en attente de consolidation
│   └── session_*.json
├── docs/               ← documents à ingérer (PDF, DOCX, TXT...)
│   └── rapport.pdf
├── briefing.md         ← briefing matinal (généré par le scheduler)
├── weekly.md           ← résumé hebdomadaire (généré par le scheduler)
└── tasks.md            ← tâches planifiées (généré par SchedulerCrew)
```

> `memory.md` et `memory.db` ne vivent **jamais** dans l'image Docker.
> Tes données restent sur ta machine.

---

## Calendrier ICS

Mnemo peut lire ton agenda pour contextualiser les réponses et gérer les deadlines.

**Fichier local** : place ton `.ics` dans `data/` et configure :
```
CALENDAR_SOURCE=/data/agenda.ics
```

**Google Calendar** : dans Google Agenda → Paramètres → *Lien secret au format iCal*
```
CALENDAR_SOURCE=https://calendar.google.com/calendar/ical/xxx/basic.ics
```

**Nextcloud / Radicale** :
```
CALENDAR_SOURCE=https://nextcloud.local/remote.php/dav/calendars/user/default/
```

---

## Mise à jour

```bash
git pull
./mnemo.sh rebuild       # rebuild les images Docker avec le nouveau code
```

Si la DB a évolué :
```bash
docker compose run --rm mnemo init_db   # applique les migrations (idempotent)
```

---

## Ré-entraîner le modèle de routing (optionnel)

Le routeur ML est pré-entraîné (`router_model.joblib`).
Si tu modifies `training_data.jsonl`, relance l'entraînement :

```bash
# Depuis l'hôte (avec Python + scikit-learn installé)
python agent_memory/train_router.py

# Le modèle est rechargé automatiquement au prochain lancement de Mnemo
```

---

## Troubleshooting

Consulte [TROUBLESHOOTING.MD](TROUBLESHOOTING.MD) pour les problèmes courants.

**`Connection refused` sur Ollama**
Vérifie qu'Ollama tourne : `ollama list`
Sur Linux, `host.docker.internal` nécessite Docker ≥ 20.10.

**`ModuleNotFoundError`**
Rebuild l'image : `docker compose build --no-cache`

**Mémoire réinitialisée à chaque session**
`DATA_DIR` dans `.env` doit pointer vers un dossier persistant sur ta machine.

**Le modèle ML n'est pas chargé**
Vérifie que `data/router_model.joblib` existe. Si absent : `cp router_model.joblib data/`
